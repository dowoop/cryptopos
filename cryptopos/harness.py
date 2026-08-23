"""End-to-end harness for the vertical slice.

Runs the whole path against the real testnet4 chain: charge, watch, bind,
settle, book. It asserts rather than prints-and-hopes, because a harness
that reports PASS while checking a fraction of what it claims is the exact
failure this project keeps finding.

  bench --site erp.localhost execute cryptopos.harness.run

Each check names the rule it is defending, so a failure says which promise
broke rather than which line threw.
"""

import json

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from cryptopos import api as api_module
from cryptopos import charge as charge_module
from cryptopos import settle as settle_module
from cryptopos import watch as watch_module
from cryptopos.cryptopos.doctype.crypto_sale.crypto_sale import IllegalTransition

# The rail this harness charges on. Ethereum Sepolia, because it is one of
# the rails this terminal actually offers -- `btc` is seeded switched off,
# and section 9 is where that is asserted rather than assumed. See
# DECISIONS.md D5.
RAIL = "eth"

# A real Sepolia address. Nothing here signs or spends: the terminal is
# watch-only and so is this harness, which is also why no section asserts
# that a payment arrived. Nobody sends one.
WATCHED_ADDRESS = "0x25C5f1f6EFf347D0E0c49021B157759331325019"

# A transaction id shaped like the chain's own, for the sections that need a
# settled sale in order to test BOOKING rather than observation. It is
# labelled in every event it produces, and `_cleanup` removes every sale and
# invoice this harness creates -- the site once held 44 abandoned harness
# sales, and a test that leaves ledger-shaped residue behind is a test that
# will eventually be mistaken for revenue.
HARNESS_TX_ID = "0x" + "ha12e5" * 10 + "abcd"

PASS = []
FAIL = []


def check(rule, condition, detail=""):
	(PASS if condition else FAIL).append(f"{rule}{(' -- ' + detail) if detail else ''}")


def _refuses(action):
	"""True if `action` raised. A refusal is a result, and is asserted as one."""
	try:
		action()
	except Exception:
		return True
	return False


def _ensure_prerequisites():
	company = frappe.db.get_value("Company", {}, "name")

	if not frappe.db.exists("Customer", "CryptoPoS Walk-in"):
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": "CryptoPoS Walk-in",
				"customer_type": "Individual",
			}
		).insert(ignore_permissions=True)

	if not frappe.db.exists("Item", "CRYPTOPOS-SALE"):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "CRYPTOPOS-SALE",
				"item_name": "Counter Sale",
				"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
				"stock_uom": "Nos",
				"is_stock_item": 0,
			}
		).insert(ignore_permissions=True)

	settings = frappe.get_single("CryptoPoS Settings")
	settings.merchant_name = "CryptoPoS Terminal"
	settings.mode = "testnet"
	settings.chain_reference = 1
	settings.customer = "CryptoPoS Walk-in"
	settings.item_code = "CRYPTOPOS-SALE"
	settings.company = company
	settings.save(ignore_permissions=True)

	# The receiving address lives on the rail now. Borrowed and given back
	# by `_snapshot_settings` / `_restore_settings`, same as the rest.
	frappe.db.set_value(
		"Crypto Rail", RAIL, "testnet_recipient", WATCHED_ADDRESS, update_modified=False
	)
	frappe.db.commit()
	return company


_CREATED = []


def _charge(usd_cents, rail_key=None):
	"""Charge, and remember the sale so `_cleanup` can take it away again."""
	sale = charge_module.charge(usd_cents, rail_key or RAIL)
	_CREATED.append(sale.name)
	return sale


def _settle_by_hand(sale, credited_native, tx_id=HARNESS_TX_ID):
	"""Drive a sale to settled without a payment, to test what comes after.

	**This is not an observation and never claims to be.** Nothing signs or
	sends here, so no payment ever arrives, and the sections that test
	BOOKING would otherwise have nothing to book. The source on every event
	it writes says `harness`, which is exactly how the 44 abandoned sales
	this file used to leave behind were identified later.
	"""
	sale.db_set("provenance", "REAL", update_modified=False)
	sale.db_set("tx_id", tx_id, update_modified=False)
	sale.db_set("credited_native", str(credited_native), update_modified=False)
	sale.reload()
	sale.transition_to(
		"confirmed",
		source="harness",
		detail="settled by the harness; no payment was observed",
		end_kind="over" if credited_native > int(sale.invoiced_native) else "clean",
		settled_at=now_datetime(),
	)
	sale.save(ignore_permissions=True)
	return sale


def _cleanup():
	"""Remove every sale and invoice this run created.

	A harness that leaves settled-looking sales behind is a harness whose
	residue is indistinguishable from revenue. This site carried 44 of them
	for eight days.
	"""
	for name in _CREATED:
		if not frappe.db.exists("Crypto Sale", name):
			continue
		invoice = frappe.db.get_value("Crypto Sale", name, "sales_invoice")
		frappe.db.set_value("Crypto Sale", name, "sales_invoice", None, update_modified=False)
		if invoice and frappe.db.exists("Sales Invoice", invoice):
			document = frappe.get_doc("Sales Invoice", invoice)
			if document.docstatus == 1:
				document.cancel()
			frappe.delete_doc("Sales Invoice", invoice, force=True, ignore_permissions=True)
		frappe.delete_doc("Crypto Sale", name, force=True, ignore_permissions=True)
	frappe.db.commit()
	_CREATED.clear()


# Everything _ensure_prerequisites writes over. The harness has to aim the
# terminal at an address whose history it already knows, but that field is
# where the operator's money goes: borrowing it for a test run and keeping it
# leaves a merchant watching an address they do not hold the keys to, with
# nothing on any screen to say so. It is given back.
#
# The receiving address is on the rail now, not in settings -- one address
# per rail, because an address is a fact about a chain -- so the borrowing is
# in two parts and both are given back together.
_BORROWED_SETTINGS = (
	"merchant_name",
	"mode",
	"chain_reference",
	"customer",
	"item_code",
	"company",
)

_BORROWED_RAIL_FIELDS = ("testnet_recipient",)


def _snapshot_settings():
	settings = frappe.get_single("CryptoPoS Settings")
	borrowed = {field: settings.get(field) for field in _BORROWED_SETTINGS}
	rails = {}
	for name in frappe.get_all("Crypto Rail", pluck="name"):
		rail = frappe.get_doc("Crypto Rail", name)
		rails[name] = {field: rail.get(field) for field in _BORROWED_RAIL_FIELDS}
	return {"settings": borrowed, "rails": rails}


def _restore_settings(snapshot):
	settings = frappe.get_single("CryptoPoS Settings")
	for field, value in snapshot["settings"].items():
		settings.set(field, value)
	settings.save(ignore_permissions=True)
	for name, fields in snapshot["rails"].items():
		if not frappe.db.exists("Crypto Rail", name):
			continue
		for field, value in fields.items():
			frappe.db.set_value("Crypto Rail", name, field, value, update_modified=False)
	frappe.db.commit()


def run():
	"""Run the suite, and hand the operator's settings back when it ends.

	The restore is in a finally because a harness that fails partway through
	is exactly when the terminal must not be left pointed somewhere else.
	"""
	borrowed = _snapshot_settings()
	try:
		return _run_checks()
	finally:
		_cleanup()
		_restore_settings(borrowed)


def _run_checks():
	PASS.clear()
	FAIL.clear()
	company = _ensure_prerequisites()

	# ---------------------------------------------------------------
	# 1. Charge snapshots everything, and claims nothing yet.
	# ---------------------------------------------------------------
	sale = _charge(5000)
	check("charge produces a sale in awaiting", sale.state == "awaiting", sale.state)
	check("charge snapshots the mode onto the sale", sale.mode == "testnet", sale.mode)
	check(
		"provenance is unset before any transport answers",
		not sale.provenance,
		f"got {sale.provenance!r}",
	)
	check("the amount is an exact integer of native units", int(sale.invoiced_native) > 0)
	check("the rate carries a named source", bool(sale.rate_source), sale.rate_source)
	check("the rate carries the time it was read", bool(sale.rate_at))
	check("the URI encodes the address", WATCHED_ADDRESS in (sale.uri or ""), sale.uri)
	check("identity source is recorded", sale.identity_source == "config", sale.identity_source)
	check("an unsettled sale has booked nothing", not sale.sales_invoice)

	bookable, reason = sale.may_book()
	check("an unsettled sale refuses to book", not bookable, reason)

	# ---------------------------------------------------------------
	# 2. The state machine refuses illegal moves.
	# ---------------------------------------------------------------
	# A sale cannot be un-charged. Going backwards would let a surface
	# rewrite a snapshot that charge() is supposed to have written once.
	try:
		sale.transition_to("idle", source="harness-attack")
		check("awaiting -> idle is refused", False, "the sale was un-charged")
	except IllegalTransition:
		check("awaiting -> idle is refused", True)

	# ---------------------------------------------------------------
	# 3. The watcher reaches the real chain, and claims nothing it did
	#    not see.
	# ---------------------------------------------------------------
	# Nobody pays this sale -- the terminal is watch-only and so is this
	# harness -- so the entire assertion is that a real network answered and
	# that answering produced no claim of payment. A watcher that settled
	# here would be inventing money, which is the failure this section
	# exists to catch.
	before = sale.state
	watch_module.poll(sale.name)
	sale.reload()

	check(
		"a real endpoint answering stamps provenance REAL",
		sale.provenance == "REAL",
		f"got {sale.provenance!r}",
	)
	check(
		"the look records the chain position it reached",
		isinstance(sale.scratch().get("tip"), int) and sale.scratch()["tip"] > 0,
		str(sale.scratch()),
	)
	check(
		"observation starts from the baseline captured at charge time",
		sale.scratch().get("baseline_tip") == sale.extras()["intent"]["baseline"]["tip"],
		f"{sale.scratch().get('baseline_tip')} vs {sale.extras()['intent']['baseline']['tip']}",
	)
	check(
		"an unpaid sale is not settled by being looked at",
		sale.state == before and not sale.tx_id,
		f"state={sale.state} tx_id={sale.tx_id!r}",
	)
	check("nothing was credited", int(sale.credited_native or 0) == 0, sale.credited_native)

	# ---------------------------------------------------------------
	# 4. Booking, and only then.
	# ---------------------------------------------------------------
	# Settled by hand, because nothing here can make a payment. What is
	# under test below is what happens AFTER settlement, and that half is
	# real.
	_settle_by_hand(sale, int(sale.invoiced_native) + 1)
	sale.reload()
	check(
		"a settled sale records when the money arrived",
		bool(sale.settled_at),
		f"settled_at={sale.settled_at!r}",
	)
	check("an overpayment is named as one", sale.end_kind == "over", f"end_kind={sale.end_kind}")

	from cryptopos import settle as _settle

	_settle.book(sale)
	sale.reload()
	check(
		"a settled bound real sale books an invoice",
		bool(sale.sales_invoice),
		f"sales_invoice={sale.sales_invoice!r}",
	)
	if sale.sales_invoice:
		invoice = frappe.get_doc("Sales Invoice", sale.sales_invoice)
		check("the invoice is submitted", invoice.docstatus == 1, str(invoice.docstatus))
		check(
			"the invoice carries the chain reference back to the sale",
			sale.tx_id in (invoice.remarks or ""),
		)
		check(
			"the invoice total matches the sale",
			round(invoice.grand_total * 100) == sale.usd_cents,
			f"{invoice.grand_total} vs {sale.usd_cents}c",
		)

	# A settled sale is an ending. Nothing reopens it -- a correction is a
	# new record, never an edit to the one that already told the customer
	# something.
	try:
		sale.transition_to("failed", source="harness-attack", end_kind="clean")
		check("a terminal sale refuses to move", False, "confirmed -> failed was allowed")
	except IllegalTransition:
		check("a terminal sale refuses to move", True)

	# ---------------------------------------------------------------
	# 5. The audit trail exists and is attributable.
	# ---------------------------------------------------------------
	sources = {event.source for event in sale.events}
	check("every transition is attributed to a source", "" not in sources, str(sources))
	# Transitions are what the event trail records, so a heartbeat that
	# reached the chain and found nothing appears nowhere in it. Which
	# provider answered, and when, is recorded on the sale's scratchpad
	# instead -- and that distinction is the point: "looked, saw nothing"
	# and "never looked" must not read the same.
	check(
		"the sale records which provider answered",
		bool(sale.scratch().get("provider")),
		str(sale.scratch()),
	)
	check(
		"and which rail answered for it",
		sale.scratch().get("rail") == sale.extras().get("catalog_key"),
		f"{sale.scratch().get('rail')!r} vs {sale.extras().get('catalog_key')!r}",
	)

	# ---------------------------------------------------------------
	# 6. An unpaid sale ends as expired, not as failed.
	# ---------------------------------------------------------------
	unpaid = _charge(7000)
	unpaid.db_set("rate_lock_end", add_to_date(now_datetime(), seconds=-1), update_modified=False)
	unpaid.reload()
	watch_module.poll(unpaid.name)
	unpaid.reload()
	check(
		"an unpaid sale past its lock expires",
		unpaid.state == "expired",
		f"state={unpaid.state}",
	)
	check("an expired sale says which ending it was", unpaid.end_kind == "clean", unpaid.end_kind)
	check("an expired sale books nothing", not unpaid.sales_invoice)

	# ---------------------------------------------------------------
	# 7. An unreachable chain does not become a claim about the world.
	# ---------------------------------------------------------------
	blind = _charge(9000)
	extras = blind.extras()
	extras["endpoint"] = "https://127.0.0.1:9/does-not-exist"
	blind.db_set("identity_extras", json.dumps(extras), update_modified=False)
	blind.db_set("rate_lock_end", add_to_date(now_datetime(), seconds=-1), update_modified=False)
	blind.reload()
	watch_module.poll(blind.name)
	blind.reload()
	check(
		"a final look that never reached the chain parks for review",
		blind.state == "needs_review",
		f"state={blind.state}",
	)
	check(
		"it says could-not-verify rather than unpaid",
		blind.end_kind == "unverified",
		f"end_kind={blind.end_kind}",
	)
	check("a parked sale carries a reason", bool(blind.review_reason))
	check("a parked sale books nothing", not blind.sales_invoice)

	# ---------------------------------------------------------------
	# 8. A booking that fails is retried, and one that cannot be traced
	#    is never written at all.
	# ---------------------------------------------------------------
	# Booking runs ERPNext's validation, which fails for reasons that have
	# nothing to do with the sale. `confirmed` is terminal and the heartbeat
	# does not poll it, so before the sweep existed such a sale stayed
	# unbooked forever, in silence, with the money already received.
	retried = _charge(5000)

	settings = frappe.get_single("CryptoPoS Settings")
	settings.db_set("item_code", "CRYPTOPOS-NO-SUCH-ITEM", update_modified=False)
	frappe.clear_document_cache("CryptoPoS Settings", "CryptoPoS Settings")

	_settle_by_hand(retried, int(retried.invoiced_native))
	settle_module.book(retried)
	retried.reload()
	check(
		"a sale settles even when the ledger refuses it",
		retried.state == "confirmed",
		f"state={retried.state}",
	)
	check("a failed booking books nothing", not retried.sales_invoice)
	check(
		"a failed booking says so on the sale",
		any(
			event.source == "book" and "booking failed" in (event.detail or "")
			for event in retried.events
		),
		str([event.detail for event in retried.events if event.source == "book"]),
	)
	check(
		"an unbooked settled sale is visible to an operator",
		any(row["sale"] == retried.name for row in settle_module.unbooked()),
	)

	settings = frappe.get_single("CryptoPoS Settings")
	settings.db_set("item_code", "CRYPTOPOS-SALE", update_modified=False)
	frappe.clear_document_cache("CryptoPoS Settings", "CryptoPoS Settings")

	swept = settle_module.sweep_unbooked()
	retried.reload()
	check(
		"the sweep books it once the ledger will accept it",
		bool(retried.sales_invoice),
		f"swept={swept} invoice={retried.sales_invoice!r}",
	)
	check(
		"and the invoice still matches the sale exactly",
		bool(retried.sales_invoice)
		and round(frappe.db.get_value("Sales Invoice", retried.sales_invoice, "grand_total") * 100)
		== retried.usd_cents,
	)

	# The fifth term of the booking equation. A sale nothing can trace back
	# to a transaction is not evidence of revenue, and must never book --
	# this site held 44 such sales when the sweep was written, worth
	# $1,101,650 of fiction had the sweep trusted the old four terms.
	untraceable = _charge(5000)
	_settle_by_hand(untraceable, int(untraceable.invoiced_native))
	untraceable.db_set("sales_invoice", None, update_modified=False)
	untraceable.db_set("tx_id", "", update_modified=False)
	untraceable.reload()
	bookable, reason = untraceable.may_book()
	check("a sale with no transaction id refuses to book", not bookable, reason)
	check(
		"and the sweep leaves it alone",
		settle_module.sweep_unbooked()["booked"] == 0
		or not frappe.db.get_value("Crypto Sale", untraceable.name, "sales_invoice"),
	)

	# ---------------------------------------------------------------
	# 9. A rail that cannot bind safely is described and not offered.
	# ---------------------------------------------------------------
	# `btc` is seeded switched off because its adapter refuses a receiving
	# address that has any history, and this terminal has no per-sale
	# address source. Asserted rather than assumed: a later change that
	# quietly switched it back on would take money it cannot attribute.
	# DECISIONS.md D5 has the seven sequences that settled this.
	check(
		"btc is present, and is not on offer",
		frappe.db.exists("Crypto Rail", "btc")
		and not frappe.db.get_value("Crypto Rail", "btc", "enabled"),
		f"enabled={frappe.db.get_value('Crypto Rail', 'btc', 'enabled')!r}",
	)
	check(
		"charging a rail that is off is refused",
		_refuses(lambda: charge_module.charge(5000, "btc")),
	)
	check(
		"the rails an operator is offered are the ones that work",
		all(
			frappe.db.get_value("Crypto Rail", row["name"], "catalog_key")
			for row in api_module.rails()
		),
		str([row["name"] for row in api_module.rails()]),
	)

	frappe.db.commit()

	print("")
	for line in PASS:
		print(f"  PASS  {line}")
	for line in FAIL:
		print(f"  FAIL  {line}")
	print("")
	print(f"  {len(PASS)} passed, {len(FAIL)} failed")
	print(f"  settled sale: {sale.name}  invoice: {sale.sales_invoice}")
	print(f"  company: {company}")
	if FAIL:
		raise SystemExit(1)
	return {"passed": len(PASS), "failed": len(FAIL)}
