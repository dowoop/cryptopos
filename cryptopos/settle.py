"""Booking — where a settled sale becomes an ERPNext Sales Invoice.

This is the seam between the two halves, and it only opens one way. The
Crypto Sale owns uncertainty; ERPNext owns the ledger. A sale that is
settled, bound and real produces an invoice. Every other sale -- expired,
part-paid, sighted-but-unidentified, could-not-verify -- produces nothing,
and that silence is the design rather than a gap in it.

The alternative was mapping the sale's eight states onto a Sales Invoice's
three docstatus values, which would have required deciding whether
"I cannot tell" is a submit or a cancel. It is neither, so it does not go
into a document that can only say those two things.
"""

import frappe
from frappe import _
from frappe.utils import strip_html

# One name for the mode of payment, shared with the code that creates it, so
# the booking cannot come to name something install never made.
from cryptopos.install import RECEIPT_MODE_OF_PAYMENT


def book(sale):
	"""Emit a Sales Invoice for a settled sale. Idempotent."""
	if sale.sales_invoice:
		return sale.sales_invoice

	# IDEMPOTENT WAS A CHECK-THEN-ACT, AND TWO CALLERS REALLY DO ARRIVE.
	#
	# The browser's auto-poll calls `api.poll` every ten seconds and the
	# scheduler's `heartbeat` polls every sale in flight every minute; both
	# land in `poll` -> `book` for the same sale. The line above is a read
	# with no lock, so both could pass it, both build an invoice, and both
	# submit one. Nothing in this function stopped the second.
	#
	# It has not happened here -- 61 sales, 61 distinct invoices, checked --
	# and the reason is luck rather than design: ERPNext touches `tabCompany`
	# while submitting, the two transactions deadlocked there, and MariaDB
	# rolled the loser back INCLUDING its invoice. A deadlock is what stood
	# between this instance and two invoices for one payment, which is not a
	# thing to leave standing.
	#
	# So take the row. The second caller blocks here until the first commits,
	# then reads the invoice it wrote and returns it. This is the same shape
	# `claim_covers` already uses to hand a cover out exactly once, and the
	# lock lives no longer than one sale's poll -- `heartbeat` commits per
	# sale.
	already = frappe.db.get_value("Crypto Sale", sale.name, "sales_invoice", for_update=True)
	if already:
		sale.reload()
		return already

	ok, reason = sale.may_book()
	if not ok:
		sale._append_event(sale.state, sale.state, "book", _("not booked: {0}").format(reason))
		sale.save(ignore_permissions=True)
		return None

	settings = frappe.get_single("CryptoPoS Settings")
	missing = [
		label
		for label, value in (
			("Default Customer", settings.customer),
			("Default Item", settings.item_code),
			("Company", settings.company),
			# THE RECEIPT ACCOUNT IS AS LOAD-BEARING AS THE OTHER THREE, and
			# leaving it out of this list is what produced the defect below:
			# without it the invoice still books, so nothing refuses and
			# nothing complains, and the ledger quietly gains a receivable
			# for money that is already in the merchant's wallet.
			("Crypto Receipt Account", settings.get("receipt_account")),
		)
		if not value
	]
	if missing:
		# The sale is settled and stays settled. What failed is the booking,
		# and saying so on the sale is better than throwing: the money did
		# arrive, and a configuration gap must not rewrite that fact.
		sale._append_event(
			sale.state,
			sale.state,
			"book",
			_("cannot book, CryptoPoS Settings missing: {0}").format(", ".join(missing)),
		)
		sale.save(ignore_permissions=True)
		return None

	invoice = frappe.new_doc("Sales Invoice")
	invoice.customer = settings.customer
	invoice.company = settings.company
	invoice.currency = "USD"
	# A SETTLED SALE IS A PAID SALE, AND THE INVOICE HAS TO SAY SO.
	#
	# This booked the revenue and the receivable and stopped, which left every
	# settled sale sitting in ERPNext as money a customer still owes. On this
	# instance that was 57 invoices reading Unpaid then Overdue, $839.81 of
	# receivable against a walk-in who had paid every cent of it on chain, and
	# no asset account holding the crypto that arrived. The books disagreed
	# with the chain, in the one direction an operator would never think to
	# check, and `health.sh` could not see it: its booking check asks whether a
	# settled sale HAS an invoice, never whether the invoice was ever paid.
	#
	# `is_pos` with a payment row rather than a separate Payment Entry, for one
	# reason that decides it: this document is the whole booking, so the
	# savepoint below covers the payment too. A Payment Entry made after the
	# submit is a second document with its own failure, and a failure between
	# the two lands exactly on today's bug -- a submitted invoice nothing ever
	# paid -- which is the state this is here to end. Probed against this
	# site's chart before it was written: submits Paid, outstanding 0.00, and
	# needs no POS Profile.
	invoice.is_pos = 1
	invoice.append(
		"payments",
		{
			"mode_of_payment": RECEIPT_MODE_OF_PAYMENT,
			"account": settings.get("receipt_account"),
			# The invoice's own total, not a second conversion of the sale.
			# `usd_cents` already priced this once and a rounding difference
			# between the two would leave a few cents outstanding forever --
			# which is the same defect in miniature.
			"amount": sale.usd_cents / 100.0,
		},
	)
	invoice.append(
		"items",
		{
			"item_code": settings.item_code,
			"qty": 1,
			# A float, and it stays one: Frappe runs every Currency value
			# through `flt()`, so handing it a Decimal or a string here
			# produces the identical stored number. The integer cents on the
			# sale remain the authoritative figure; this is the derived copy
			# that ERPNext owns. `harness` asserts the two still agree.
			"rate": sale.usd_cents / 100.0,
			"cost_center": settings.cost_center or None,
		},
	)
	# The crypto reference travels with the ledger entry, so a dispute months
	# later can get from the accounting record back to the chain.
	invoice.remarks = _(
		"CryptoPoS {invoice_id} ({ref})\n"
		"rail: {rail}  mode: {mode}  provenance: {provenance}\n"
		"paid: {credited} {unit} (invoiced {invoiced})\n"
		"rate: {rate} microcents/coin from {source}\n"
		"txid: {tx}"
	).format(
		invoice_id=sale.invoice_id,
		ref=sale.invoice_ref,
		rail=sale.rail_key,
		mode=sale.mode,
		provenance=sale.provenance,
		credited=sale.credited_native,
		unit=frappe.db.get_value("Crypto Rail", sale.rail_key, "unit_name"),
		invoiced=sale.invoiced_native,
		rate=sale.rate_microcents,
		source=sale.rate_source,
		tx=sale.tx_id or _("not recorded"),
	)
	# Booking is the one act here that runs someone else's validation. A
	# renamed Item, a closed fiscal year, an accounting dimension made
	# mandatory this morning -- each raises from inside ERPNext, and each is
	# a configuration fact about the ledger rather than anything wrong with
	# the sale. So the failure is caught, written onto the sale where an
	# operator can read it, and left for `sweep_unbooked` to retry. It must
	# not escape: the caller is the watcher, mid-heartbeat, and the money has
	# already arrived.
	savepoint = "cryptopos_book"
	frappe.db.savepoint(savepoint)
	try:
		invoice.insert(ignore_permissions=True)
		invoice.submit()
	except Exception as exception:
		# THE ROLLBACK CAN ITSELF RAISE, AND THEN IT ESCAPES THIS HANDLER.
		#
		# A deadlock or a lock-wait timeout makes MariaDB roll the whole
		# transaction back on its own, and that DISCARDS every savepoint in
		# it. The rollback below then fails with `SAVEPOINT cryptopos_book
		# does not exist` -- raised from inside an `except` block, where
		# nothing catches it -- so the exception leaves `book()` and defeats
		# the paragraph above word for word.
		#
		# Measured live on 2026-09-02: four consecutive sales each logged
		# "cryptopos heartbeat failed", with the real cause (a `tabCompany`
		# deadlock between the browser's auto-poll and the scheduler's
		# heartbeat, both reaching `book` for the same sale) two frames up
		# and the savepoint error on top of it. Every one of those sales was
		# in fact booked correctly by whichever caller won, so the escape
		# produced pure noise -- noise shaped exactly like a sale failing to
		# book, which is the worst thing for it to be shaped like.
		#
		# A savepoint that is gone is the state the rollback was asking for,
		# so its absence is success and not a second failure.
		try:
			frappe.db.rollback(save_point=savepoint)
		except Exception:
			pass
		# Everything from here is recording, not deciding, and the money has
		# already arrived. A transaction the database has just rolled back
		# underneath us can refuse these too, and a failure to write a note
		# must not become the thing the caller sees.
		try:
			frappe.log_error(
				title=f"cryptopos could not book {sale.name}",
				message=frappe.get_traceback(),
			)
			sale.reload()
			sale._append_event(
				sale.state,
				sale.state,
				"book",
				_("booking failed, will retry: {0}").format(
					strip_html(str(exception)).strip().replace("\n", " ")[:200]
				),
			)
			sale.save(ignore_permissions=True)
		except Exception:
			# `sweep_unbooked` is the retry and it reads the sale's state, not
			# this note, so a lost note costs a line of explanation and never
			# the booking itself.
			pass
		return None
	frappe.db.release_savepoint(savepoint)

	sale.db_set("sales_invoice", invoice.name, update_modified=False)
	sale.reload()
	sale._append_event(sale.state, sale.state, "book", _("booked as {0}").format(invoice.name))
	sale.save(ignore_permissions=True)
	return invoice.name


def unbooked(limit=100):
	"""Settled sales that ought to have an invoice and do not.

	The operator-facing half of the same question `sweep_unbooked` answers
	for the scheduler: what money has this terminal taken that the ledger has
	not been told about? Each row carries the reason, so a sale that is
	waiting on configuration reads differently from one that is waiting on a
	retry.
	"""
	rows = []
	for name in frappe.get_all(
		"Crypto Sale",
		filters={"state": "confirmed", "sales_invoice": ("is", "not set")},
		pluck="name",
		order_by="creation asc",
		limit=limit,
	):
		sale = frappe.get_doc("Crypto Sale", name)
		ok, reason = sale.may_book()
		rows.append(
			{
				"sale": name,
				"usd_cents": sale.usd_cents,
				"rail_key": sale.rail_key,
				"tx_id": sale.tx_id,
				"settled_at": sale.settled_at,
				"bookable": ok,
				"reason": reason or _("bookable; awaiting the next sweep"),
			}
		)
	return rows


def sweep_unbooked(limit=50):
	"""Book settled sales the first attempt missed. Scheduler entry point.

	`book` is called once, from the watcher, at the instant a sale settles.
	Nothing called it a second time -- `confirmed` is terminal, `poll`
	returns immediately for it, and `heartbeat` only selects sales still in
	flight. So a booking that failed stayed failed, in silence, with the
	money already received. This is the retry that was missing.

	It is deliberately narrow. A sale is booked only if `may_book` says so
	NOW, which is what keeps this safe: at the time this was written the
	site held 44 settled sales with no invoice and no transaction id, and a
	sweep that trusted the old four-term equation would have written
	$1,101,650 of fiction into the ledger in one pass.
	"""
	considered = booked = 0
	for name in frappe.get_all(
		"Crypto Sale",
		filters={"state": "confirmed", "sales_invoice": ("is", "not set")},
		pluck="name",
		order_by="creation asc",
		limit=limit,
	):
		sale = frappe.get_doc("Crypto Sale", name)
		ok, _reason = sale.may_book()
		if not ok:
			continue
		considered += 1
		try:
			if book(sale):
				booked += 1
		except Exception:
			# `book` catches ERPNext's refusals itself; anything reaching
			# here is unexpected, and one bad sale must not end the sweep.
			frappe.log_error(
				title=f"cryptopos sweep failed for {name}",
				message=frappe.get_traceback(),
			)
		frappe.db.commit()
	return {"considered": considered, "booked": booked}
