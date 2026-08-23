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


def book(sale):
	"""Emit a Sales Invoice for a settled sale. Idempotent."""
	if sale.sales_invoice:
		return sale.sales_invoice

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
		frappe.db.rollback(save_point=savepoint)
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
