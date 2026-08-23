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
from cryptopos import catalog as catalog_module
from cryptopos import charge as charge_module
from cryptopos import settle as settle_module
from cryptopos import watch as watch_module
from cryptopos.cryptopos.doctype.crypto_sale.crypto_sale import IllegalTransition
from cryptopos.cryptopos.report.crypto_takings import crypto_takings as takings_report
from cryptopos_core import hd

# The rail this harness charges on. Ethereum Sepolia, because it is one of
# the rails this terminal actually offers -- `btc` is seeded switched off,
# and section 9 is where that is asserted rather than assumed. See
# DECISIONS.md D5.
RAIL = "eth"

# A real Sepolia address. Nothing here signs or spends: the terminal is
# watch-only and so is this harness, which is also why no section asserts
# that a payment arrived. Nobody sends one.
WATCHED_ADDRESS = "0x25C5f1f6EFf347D0E0c49021B157759331325019"

# BIP-84's published account zpub and first two external receiving keys:
# https://github.com/bitcoin/bips/blob/master/bip-0084.mediawiki#test-vectors
BIP84_ACCOUNT_ZPUB = "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"

# `testnet_xpub` deliberately refuses that zpub's mainnet version bytes. This
# vpub is the same published 78-byte account-key payload with only its SLIP-132
# version changed from zpub to vpub and its Base58Check checksum recomputed.
# No child key was generated to make the expected answers agree with the code.
BIP84_ACCOUNT_VPUB = "vpub5YvMuJNjRSYon44z9QmCfdf8SqJRVNvz6m55Qy5iVjZQxDfUgtiQjnc7CC1fAbED2tAGCZRERUfvtn2DstZGU6HMns6dXXH2wujSc2wfi2x"

# An account key whose addresses have never been used.
#
# The published vector above cannot do this job: it is one of the most widely
# known keys in existence, its addresses carry real testnet history, and the
# bitcoin adapter refuses a recipient with any -- which is the whole point of
# DECISIONS.md D5. So the two keys do two different jobs. The published one
# proves the DERIVATION is right, against numbers a BIP published. This one
# proves the terminal hands out a FRESH address, which needs addresses nobody
# has touched. Its 0/0 and 0/1 were confirmed to have zero chain and mempool
# transactions on 2026-08-23.
#
# Nothing is ever expected to arrive at these addresses: every sale the harness
# charges on this rail is left to expire. Who could hold the corresponding
# private key is therefore not a property this fixture needs.
HARNESS_ACCOUNT_VPUB = "vpub5Z7wNKS2FP2pFiomoXojA6b3wxqq4ubAT3mdSYumHhqvFRB2BuZQHRrCn7FXmtR38pozTcnigp1qxRfKs44SFFv767WBjGDKaLZJGgbzyxs"

# The BIP publishes these two witness programs as bc1 addresses. These fixed
# tb1 encodings change only the human-readable part and checksum for testnet.
BIP84_TESTNET_RECEIVING = (
	"tb1qcr8te4kr609gcawutmrza0j4xv80jy8zmfp6l0",
	"tb1qnjg0jd8228aq7egyzacy8cys3knf9xvrn9d67m",
)

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


def _refusal_message(action):
	"""The precise words from a refusal, or empty text if it did not refuse."""
	try:
		action()
	except Exception as exception:
		return str(exception)
	return ""


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

_BORROWED_RAIL_FIELDS = ("testnet_recipient", "testnet_xpub", "next_address_index")


# The scheduler runs `settle.sweep_unbooked` every five minutes, and this
# harness deliberately creates sales that satisfy all five booking terms with a
# FABRICATED transaction id. Those two facts must never be true at the same
# moment: a sweep firing mid-run would book fiction into the ledger, and if a
# run then died before `_cleanup` the invoice would simply stay there. Borrowed
# and given back exactly like the settings, for the same reason.
_SWEEP_JOB = "cryptopos.settle.sweep_unbooked"


def _pause_sweep():
	"""Stop the booking sweep for the duration of the run. Returns what to restore."""
	name = frappe.db.get_value("Scheduled Job Type", {"method": _SWEEP_JOB}, "name")
	if not name:
		return None
	was_stopped = frappe.db.get_value("Scheduled Job Type", name, "stopped")
	frappe.db.set_value("Scheduled Job Type", name, "stopped", 1, update_modified=False)
	frappe.db.commit()
	return (name, was_stopped)


def _resume_sweep(borrowed):
	if not borrowed:
		return
	name, was_stopped = borrowed
	if frappe.db.exists("Scheduled Job Type", name):
		frappe.db.set_value("Scheduled Job Type", name, "stopped", was_stopped, update_modified=False)
		frappe.db.commit()


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
	sweep = _pause_sweep()
	try:
		return _run_checks()
	finally:
		_cleanup()
		_restore_settings(borrowed)
		_resume_sweep(sweep)


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
	check("a configured recipient records the shared binding", sale.binding == "shared", sale.binding)
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
	# 9. Bitcoin allocates one published-vector address per sale.
	# ---------------------------------------------------------------
	btc = frappe.get_doc("Crypto Rail", "btc")
	btc.testnet_recipient = ""
	btc.testnet_xpub = BIP84_ACCOUNT_ZPUB
	mainnet_refusal = _refusal_message(lambda: btc.save(ignore_permissions=True))
	check(
		"a mainnet account xpub is refused by the testnet field",
		bool(mainnet_refusal) and "mainnet" in mainnet_refusal.lower(),
		mainnet_refusal or "not refused",
	)

	btc.reload()
	btc.testnet_xpub = BIP84_ACCOUNT_VPUB
	btc.testnet_recipient = BIP84_TESTNET_RECEIVING[0]
	both_refusal = _refusal_message(lambda: btc.save(ignore_permissions=True))
	check(
		"a rail refuses both per-sale and shared receiving material",
		bool(both_refusal) and "one or the other" in both_refusal.lower(),
		both_refusal or "not refused",
	)

	btc.reload()
	btc.testnet_recipient = ""
	btc.testnet_xpub = HARNESS_ACCOUNT_VPUB
	btc.next_address_index = 0
	btc.save(ignore_permissions=True)
	frappe.db.commit()

	# Derivation is checked against what a BIP published, with no network and
	# no sale involved. This is the half the published key is right for; the
	# half below needs addresses nobody has ever used, which that key by its
	# nature cannot provide.
	published = hd.parse_extended_key(BIP84_ACCOUNT_VPUB)
	derived = tuple(
		hd.p2wpkh_address(hd.derive_path(published, f"0/{index}"), "tb") for index in (0, 1)
	)
	check(
		"derivation reproduces BIP-84's published receiving keys",
		derived == BIP84_TESTNET_RECEIVING,
		str(derived),
	)

	expected = tuple(
		hd.p2wpkh_address(hd.derive_path(hd.parse_extended_key(HARNESS_ACCOUNT_VPUB), f"0/{i}"), "tb")
		for i in (0, 1)
	)
	used_before = set(
		frappe.get_all(
			"Crypto Sale",
			filters={"identity_address": ("in", list(expected))},
			pluck="identity_address",
		)
	)
	btc_first = _charge(5000, "btc")
	btc_second = _charge(7000, "btc")
	addresses = (btc_first.identity_address, btc_second.identity_address)
	check("two btc charges receive two different addresses", len(set(addresses)) == 2, str(addresses))
	check(
		"they are the next two addresses under the account key",
		addresses == expected,
		f"{addresses} vs {expected}",
	)
	check(
		"neither allocated address belonged to an earlier sale",
		not used_before,
		str(sorted(used_before)),
	)
	check(
		"the btc address index advances exactly twice",
		int(frappe.db.get_value("Crypto Rail", "btc", "next_address_index") or 0) == 2,
		str(frappe.db.get_value("Crypto Rail", "btc", "next_address_index")),
	)
	check(
		"a derived address records the per-sale binding",
		btc_first.binding == btc_second.binding == "per-sale",
		f"{btc_first.binding!r}, {btc_second.binding!r}",
	)

	# End both unpaid sales so the latest terminal run is exactly two. This
	# number is a warning for the operator, never a refusal in charge().
	for btc_sale in (btc_first, btc_second):
		btc_sale.db_set("rate_lock_end", add_to_date(now_datetime(), seconds=-1), update_modified=False)
		watch_module.poll(btc_sale.name)
		btc_sale.reload()
	check(
		"two latest unpaid btc endings make a gap run of two",
		catalog_module.gap_run_for(btc) == 2,
		str(catalog_module.gap_run_for(btc)),
	)
	btc_row = next((row for row in api_module.rails() if row["name"] == "btc"), {})
	check(
		"api rails reports the btc gap run",
		btc_row.get("gap_run") == 2,
		str(btc_row),
	)

	# ---------------------------------------------------------------
	# 10. Offered rails name the adapter that drives them.
	# ---------------------------------------------------------------
	check(
		"the rails an operator is offered are the ones that work",
		all(
			frappe.db.get_value("Crypto Rail", row["name"], "catalog_key")
			for row in api_module.rails()
		),
		str([row["name"] for row in api_module.rails()]),
	)
	# The gap limit is a ceiling, and a ceiling ships on the surface that
	# offers the feature. A rail deriving a fresh address per sale leaves an
	# unused address behind every time a sale goes unpaid, and a wallet
	# restored from the account key stops scanning after 20 of them.
	offered = {row["name"]: row for row in api_module.rails()}
	check(
		"every offered rail says which binding it uses",
		all(row["binding"] in ("per-sale", "shared") for row in offered.values()),
		str({name: row["binding"] for name, row in offered.items()}),
	)
	check(
		"the booking sweep is paused while the harness fabricates settlements",
		bool(frappe.db.get_value("Scheduled Job Type", {"method": _SWEEP_JOB}, "stopped")),
		str(frappe.db.get_value("Scheduled Job Type", {"method": _SWEEP_JOB}, "stopped")),
	)
	check(
		"a deriving rail reports its unused-address run and the limit",
		all(
			isinstance(row["gap_run"], int) and row["gap_limit"] == catalog_module.GAP_LIMIT
			for row in offered.values()
		),
		str({name: (row["gap_run"], row["gap_limit"]) for name, row in offered.items()}),
	)

	# ---------------------------------------------------------------
	# 11. Rail health is a deliberate network operation.
	# ---------------------------------------------------------------
	# The terminal calls api.rails() on every page load. Guard the default
	# path with a replacement that fails if readiness -- one network call per
	# rail -- is touched at all.
	real_readiness_for = catalog_module.readiness_for

	def unexpected_readiness(*_args, **_kwargs):
		raise AssertionError("default api.rails() asked the network for readiness")

	catalog_module.readiness_for = unexpected_readiness
	default_rails = None
	default_error = ""
	try:
		default_rails = api_module.rails()
	except Exception as exception:
		default_error = str(exception)
	finally:
		catalog_module.readiness_for = real_readiness_for
	check(
		"api rails makes no readiness call by default",
		default_rails is not None and not default_error,
		default_error,
	)
	check(
		"default rail rows omit readiness",
		default_rails is not None and all("readiness" not in row for row in default_rails),
		str(default_rails),
	)

	readiness_rows = api_module.rails(with_readiness=1)
	check(
		"readiness is returned for every enabled rail when asked",
		len(readiness_rows) == frappe.db.count("Crypto Rail", {"enabled": 1})
		and all(
			row.get("readiness", {}).get("rail_key")
			and isinstance(row["readiness"].get("ready"), list)
			for row in readiness_rows
		),
		str(readiness_rows),
	)

	# ---------------------------------------------------------------
	# 12. Takings stay per rail, and booking's gap stays visible.
	# ---------------------------------------------------------------
	report_date = get_datetime(now_datetime()).date()
	report_filters = {"from_date": report_date, "to_date": report_date}
	columns, before_rows, _message, before_chart = takings_report.execute(report_filters)
	column_names = [column["fieldname"] for column in columns]
	check(
		"the takings report returns exactly its seven columns and rows",
		column_names
		== [
			"date",
			"rail",
			"sales",
			"booked_usd",
			"unbooked_usd",
			"credited_native",
			"unit",
		]
		and isinstance(before_rows, list),
		str(column_names),
	)

	def report_row(rows, rail_key):
		return next(
			(
				row
				for row in rows
				if get_datetime(row["date"]).date() == report_date and row["rail"] == rail_key
			),
			{
				"booked_usd": 0,
				"unbooked_usd": 0,
				"credited_native": "0",
			},
		)

	before_eth = report_row(before_rows, RAIL)
	report_booked = _charge(1234)
	_settle_by_hand(report_booked, int(report_booked.invoiced_native))
	settle_module.book(report_booked)
	report_booked.reload()
	_, booked_rows, _message, booked_chart = takings_report.execute(report_filters)
	booked_eth = report_row(booked_rows, RAIL)
	check(
		"a booked settled sale increases booked USD only",
		round((booked_eth["booked_usd"] - before_eth["booked_usd"]) * 100) == 1234
		and booked_eth["unbooked_usd"] == before_eth["unbooked_usd"],
		f"before={before_eth} after={booked_eth}",
	)
	check(
		"the booked chart increases from booked USD only",
		round(
			(booked_chart["data"]["datasets"][0]["values"][0]
			- before_chart["data"]["datasets"][0]["values"][0])
			* 100
		)
		== 1234,
		f"before={before_chart} after={booked_chart}",
	)

	report_unbooked = _charge(2345)
	_settle_by_hand(report_unbooked, int(report_unbooked.invoiced_native))
	_, unbooked_rows, _message, unbooked_chart = takings_report.execute(report_filters)
	unbooked_eth = report_row(unbooked_rows, RAIL)
	check(
		"an unbooked settled sale increases unbooked USD only",
		round((unbooked_eth["unbooked_usd"] - booked_eth["unbooked_usd"]) * 100) == 2345
		and unbooked_eth["booked_usd"] == booked_eth["booked_usd"],
		f"before={booked_eth} after={unbooked_eth}",
	)
	check(
		"unbooked USD never enters the booked chart",
		unbooked_chart == booked_chart,
		f"before={booked_chart} after={unbooked_chart}",
	)

	report_btc = _charge(3456, "btc")
	_settle_by_hand(report_btc, int(report_btc.invoiced_native))
	_, final_rows, _message, _chart = takings_report.execute(report_filters)
	today_rows = [row for row in final_rows if get_datetime(row["date"]).date() == report_date]
	check(
		"credited native is text in every report row",
		all(isinstance(row["credited_native"], str) for row in final_rows),
		str(final_rows),
	)
	check(
		"report rows never combine rails",
		len({(str(row["date"]), row["rail"]) for row in final_rows}) == len(final_rows)
		and {RAIL, "btc"} <= {row["rail"] for row in today_rows},
		str(today_rows),
	)

	unbooked_summary = api_module.unbooked()
	count_card = api_module.settled_not_in_ledger_count(filters=[])
	value_card = api_module.settled_not_in_ledger_usd(filters=[])
	check(
		"the settled-not-in-ledger count card matches the oversight endpoint",
		count_card["value"] == unbooked_summary["count"]
		and count_card["fieldtype"] == "Int",
		f"card={count_card} endpoint={unbooked_summary}",
	)
	check(
		"the settled-not-in-ledger value card uses charged USD cents",
		round(value_card["value"] * 100) == unbooked_summary["usd_cents"]
		and value_card["fieldtype"] == "Currency"
		and value_card["currency"] == "USD",
		f"card={value_card} endpoint={unbooked_summary}",
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
