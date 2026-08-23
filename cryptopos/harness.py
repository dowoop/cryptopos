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

from cryptopos import charge as charge_module
from cryptopos import settle as settle_module
from cryptopos import watch as watch_module
from cryptopos.cryptopos.doctype.crypto_sale.crypto_sale import IllegalTransition

# A real testnet4 address carrying a real confirmed payment. Nothing here
# signs or spends -- the terminal is watch-only and so is this harness.
WATCHED_ADDRESS = "tb1quyndcxh5sfqv6rm73h47p9vgenlhphq28dc9ga"

PASS = []
FAIL = []


def check(rule, condition, detail=""):
	(PASS if condition else FAIL).append(f"{rule}{(' -- ' + detail) if detail else ''}")


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
	settings.btc_testnet_address = WATCHED_ADDRESS
	settings.customer = "CryptoPoS Walk-in"
	settings.item_code = "CRYPTOPOS-SALE"
	settings.company = company
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	return company


# Everything _ensure_prerequisites writes over. The harness has to aim the
# terminal at an address whose history it already knows, but that field is
# where the operator's money goes: borrowing it for a test run and keeping it
# leaves a merchant watching an address they do not hold the keys to, with
# nothing on any screen to say so. It is given back.
_BORROWED_SETTINGS = (
	"merchant_name",
	"mode",
	"chain_reference",
	"btc_testnet_address",
	"customer",
	"item_code",
	"company",
)


def _snapshot_settings():
	settings = frappe.get_single("CryptoPoS Settings")
	return {field: settings.get(field) for field in _BORROWED_SETTINGS}


def _restore_settings(snapshot):
	settings = frappe.get_single("CryptoPoS Settings")
	for field, value in snapshot.items():
		settings.set(field, value)
	settings.save(ignore_permissions=True)
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
		_restore_settings(borrowed)


def _run_checks():
	PASS.clear()
	FAIL.clear()
	company = _ensure_prerequisites()

	# ---------------------------------------------------------------
	# 1. Charge snapshots everything, and claims nothing yet.
	# ---------------------------------------------------------------
	sale = charge_module.charge(5000, "btc")
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
	# 3. The watcher reaches the real chain.
	# ---------------------------------------------------------------
	# The confirmed payment on this address predates the charge, and the
	# watcher correctly refuses to treat an older transaction as payment for
	# a newer sale. Backdating the charge is what makes it eligible -- it is
	# the sale that moves, never the rule.
	sale.db_set("charged_at", add_to_date(now_datetime(), days=-30), update_modified=False)
	sale.reload()

	watch_module.poll(sale.name)
	sale.reload()

	check(
		"a real endpoint answering stamps provenance REAL",
		sale.provenance == "REAL",
		f"got {sale.provenance!r}",
	)
	check(
		"the watcher bound a real transaction",
		bool(sale.tx_id),
		f"tx_id={sale.tx_id!r} state={sale.state}",
	)
	check(
		"bound money is recorded as credited",
		int(sale.credited_native or 0) > 0,
		sale.credited_native,
	)
	check(
		"a payment past the gate settles",
		sale.state == "confirmed",
		f"state={sale.state} end_kind={sale.end_kind}",
	)
	check(
		"an overpayment is named as one",
		sale.end_kind == "over",
		f"end_kind={sale.end_kind}",
	)
	check("a settled sale records when the money arrived", bool(sale.settled_at))

	# ---------------------------------------------------------------
	# 4. Booking, and only then.
	# ---------------------------------------------------------------
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
	check("the chain transport appears in the trail", "esplora-rest" in sources, str(sources))

	# ---------------------------------------------------------------
	# 6. An unpaid sale ends as expired, not as failed.
	# ---------------------------------------------------------------
	unpaid = charge_module.charge(7000, "btc")
	unpaid.db_set("identity_address", "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx", update_modified=False)
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
	blind = charge_module.charge(9000, "btc")
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
	retried = charge_module.charge(5000, "btc")
	retried.db_set("charged_at", add_to_date(now_datetime(), days=-30), update_modified=False)
	retried.reload()

	settings = frappe.get_single("CryptoPoS Settings")
	settings.db_set("item_code", "CRYPTOPOS-NO-SUCH-ITEM", update_modified=False)
	frappe.clear_document_cache("CryptoPoS Settings", "CryptoPoS Settings")

	watch_module.poll(retried.name)
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
	untraceable = charge_module.charge(5000, "btc")
	untraceable.db_set("charged_at", add_to_date(now_datetime(), days=-30), update_modified=False)
	untraceable.reload()
	watch_module.poll(untraceable.name)
	untraceable.reload()
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
