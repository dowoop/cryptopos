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
import time
import urllib.request

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from cryptopos import api as api_module
from cryptopos import catalog as catalog_module
from cryptopos import charge as charge_module
from cryptopos import reconcile as reconcile_module
from cryptopos import settle as settle_module
from cryptopos import watch as watch_module
from cryptopos.cryptopos.doctype.crypto_sale.crypto_sale import IllegalTransition
from cryptopos.cryptopos.report.crypto_takings import crypto_takings as takings_report
from cryptopos_core import addresses as core_addresses
from cryptopos_core import hd
from cryptopos_core.plugin import NOT_UNCONDITIONAL, binding_category_for

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

# BIP-32 test vector 1's published depth-three xpub at m/0'/1/2'. It is
# public derivation material only; this harness uses it to exercise EVM address
# allocation and never offers the resulting addresses in a sale.
# https://github.com/bitcoin/bips/blob/master/bip-0032.mediawiki#test-vectors
BIP32_DEPTH_THREE_XPUB = "xpub6D4BDPcP2GT577Vvch3R8wDkScZWzQzMMUm3PWbmWvVJrZwQY4VUNgqFJPMM3No2dFDFGTsxxpG5uJh7n7epu4trkrX7x7DogT5Uv6fcLW5"

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
# A real testnet4 address, used only to prove the refusal below. Nothing is
# ever charged to it.
WATCHED_ADDRESS_BTC = "tb1quyndcxh5sfqv6rm73h47p9vgenlhphq28dc9ga"

# A block holding one of that address's confirmed payments. STALENESS HEDGE:
# true when last checked, 2026-08-23. If testnet4 resets, the reconciler check
# stops finding money; read a current height off the endpoint rather than
# loosening the assertion.
#
# THE HEDGE ANTICIPATED THE WRONG DRIFT, AND THE OTHER ONE HAS HAPPENED.
# Measured 2026-08-24: these four reconciler checks now FAIL, and not because
# testnet4 reset. `tb1quyndc…` is an actively funded address -- 590
# transactions, ~1,000,000 sat arriving repeatedly, most recently at height
# 149693. Thirteen transactions now sit at or above the pinned height,
# totalling 13,000,000 sat, where the fixture was written expecting the
# arithmetic of one. The sale it charges no longer ends unpaid; it settles,
# with "payment exceeds the invoice", so the reconciler has nothing late to
# find and the three checks after it fall with the first.
#
# This is the defect class the vault names: a check must not derive its
# expectation from the thing that can break. This one derives it from the live
# contents of a third-party address that is still receiving money, so it was
# always going to drift -- and the hedge above only guarded the direction that
# did not happen.
#
# FIXED the same day, and the diagnosis was sharper than the paragraph above.
# The fixture did not merely depend on the address's balance -- it depended on
# every payment above the pinned height having been CLAIMED by an earlier
# harness sale. Verified in the data: 30 sales have pointed at this address and
# claimed 16 tx_ids, in pairs, one settling "over" and the next finding nothing.
# Thirteen unclaimed payments accumulated between runs and the second sale of
# the pair settled instead of ending unpaid.
#
# The fixture below is now two phases: the baseline is the LIVE tip while the
# sale must end unpaid, and is rewound to this height only afterwards, which is
# what makes the money late. Neither phase depends on what has accumulated.
# This constant is therefore only used by phase 2, where "a payment exists at
# or above it" is all that is required of it.
WATCHED_PAYMENT_HEIGHT = 149613


def _testnet4_tip():
	"""The current testnet4 height, read rather than pinned.

	The reconciler fixture below needs a baseline with NOTHING above it, and
	the only honest way to get one at a live address is to ask where the chain
	is now. Pinning a height cannot do it: the address keeps receiving.
	"""
	request = urllib.request.Request(
		"https://mempool.space/testnet4/api/blocks/tip/height",
		headers={"User-Agent": "cryptopos-harness/1.0"},
	)
	with urllib.request.urlopen(request, timeout=25) as response:
		return int(response.read().decode("ascii").strip())

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
	frappe.db.set_value("Crypto Rail", RAIL, "testnet_xpub", "", update_modified=False)
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
	# AND THE COVER QUEUE, because the payer that drains it is NOT a Frappe
	# job and cannot be stopped the way the sweep can. Without this the
	# host-side payer claims the harness's own test requests and pays them:
	# measured on 2026-09-02, 500,000,000 uT of real testnet money spent on a
	# $25.00 test sale that this file then deleted. A test that can spend
	# money is not a test.
	api_module.pause_covers()
	try:
		return _run_checks()
	finally:
		_cleanup()
		_restore_settings(borrowed)
		_resume_sweep(sweep)
		api_module.resume_covers()


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

	# The check that was missing, and whose absence let a nine-hour error hide
	# behind seventy-six green ones. See DECISIONS.md D19.
	#
	# Every fixture in this file that exercises settlement points at payments
	# that are genuinely DAYS old, and a days-old block time compares fine
	# against an expiry that is nine hours in the past. So the suites proved the
	# parts and never the whole: no live sale could settle, and nothing said so.
	#
	# The intent's clock is the thing to check, because both adapters credit a
	# transfer only when `block_time_epoch <= expires_at_epoch`. If that epoch
	# does not agree with the real one, every payment made NOW is "after
	# expiry", on every rail.
	intent_now = sale.extras()["intent"]["created_at_epoch"]
	real_now = int(time.time())
	check(
		"a charge stamps the intent with the real epoch, not a mistimed one",
		abs(real_now - intent_now) <= 120,
		f"intent says {intent_now}, the clock says {real_now}"
		f" ({(real_now - intent_now) / 3600:+.2f} h apart)",
	)
	check(
		"and its expiry is a rate lock into the future, not the past",
		0 < sale.extras()["intent"]["expires_at_epoch"] - real_now <= charge_module.RATE_LOCK_SECONDS,
		f"expires in {sale.extras()['intent']['expires_at_epoch'] - real_now} s",
	)

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

	# THE HANDLER THAT COULD NOT HANDLE.
	#
	# `book` rolls back to a savepoint when the ledger refuses, and that
	# rollback can itself raise. A deadlock or lock-wait timeout makes MariaDB
	# roll the whole transaction back on its own, which DISCARDS every
	# savepoint in it, so the rollback fails with "SAVEPOINT cryptopos_book
	# does not exist" -- raised from inside an `except` block, where nothing
	# catches it. It escapes `book` and the watcher logs "cryptopos heartbeat
	# failed" about a sale whose money has already arrived.
	#
	# Seen on four consecutive live sales on 2026-09-02, each of them in fact
	# booked correctly by whichever caller won the race, so the only thing the
	# escape produced was an error log shaped exactly like a failure to book.
	# The item_code is still wrong here, so the ledger refuses and the handler
	# is the code under test.
	escaping = _charge(5000)
	_settle_by_hand(escaping, int(escaping.invoiced_native))
	original_rollback = frappe.db.rollback

	def _rollback_that_lost_its_savepoint(*args, **kwargs):
		if kwargs.get("save_point") or args:
			raise Exception("SAVEPOINT cryptopos_book does not exist")
		return original_rollback(*args, **kwargs)

	frappe.db.rollback = _rollback_that_lost_its_savepoint
	escaped = None
	try:
		settle_module.book(escaping)
	except Exception as exception:
		escaped = exception
	finally:
		frappe.db.rollback = original_rollback
	check(
		"a rollback that cannot find its savepoint does not escape book()",
		escaped is None,
		f"escaped {type(escaped).__name__}: {escaped}" if escaped else "nothing escaped",
	)
	escaping.reload()
	check(
		"...and the sale is still left unbooked for the sweep to retry",
		not escaping.sales_invoice,
		f"sales_invoice={escaping.sales_invoice}",
	)

	settings = frappe.get_single("CryptoPoS Settings")
	settings.db_set("item_code", "CRYPTOPOS-SALE", update_modified=False)
	frappe.clear_document_cache("CryptoPoS Settings", "CryptoPoS Settings")

	# ONE PAYMENT, ONE INVOICE, however many callers ask for it.
	#
	# `book` opens by returning early if the sale already names an invoice,
	# and that read used to take no lock -- so the browser's ten-second
	# auto-poll and the scheduler's per-minute heartbeat could both pass it
	# for the same sale and both submit. What actually prevented two invoices
	# on this instance was a `tabCompany` deadlock rolling the loser back,
	# which is luck, not design.
	#
	# THE SECOND CALLER MUST HOLD A STALE DOC, or this proves nothing.
	#
	# `book` opens with `if sale.sales_invoice: return`, which is an in-memory
	# read. Reloading the document before calling again would satisfy THAT and
	# never reach the lock -- the check would pass with the lock removed, which
	# is the "green for the wrong reason" failure this file exists to avoid.
	#
	# The racing caller is one that loaded the sale BEFORE the winner booked
	# it, so its copy still says unbooked. `stale` is that caller. Only the
	# locked re-read against the database can catch it.
	twice = _charge(1500)
	_settle_by_hand(twice, int(twice.invoiced_native))
	stale = frappe.get_doc("Crypto Sale", twice.name)
	first = settle_module.book(twice)
	invoices_before = frappe.db.count("Sales Invoice")
	check("a sale books an invoice", bool(first), f"first={first}")
	check(
		"the racing caller's copy still says unbooked -- otherwise this proves nothing",
		not stale.sales_invoice,
		f"stale.sales_invoice={stale.sales_invoice!r}",
	)
	second = settle_module.book(stale)
	check(
		"a caller holding a stale sale gets the invoice that exists",
		second == first,
		f"first={first} second={second}",
	)
	check(
		"...and writes no second invoice for one payment",
		frappe.db.count("Sales Invoice") == invoices_before,
		f"{frappe.db.count('Sales Invoice')} vs {invoices_before}",
	)

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
	gap_before = catalog_module.gap_run_for(frappe.get_doc("Crypto Rail", "btc"))
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
	# The run counts the rail's whole recent history, so what this run can
	# assert is the CHANGE it caused. An absolute would depend on whatever
	# else the site happens to hold -- which is how this check first broke.
	gap_after = catalog_module.gap_run_for(frappe.get_doc("Crypto Rail", "btc"))
	check(
		"two unpaid btc endings lengthen the unused-address run by two",
		gap_after == gap_before + 2,
		f"{gap_before} -> {gap_after}",
	)
	btc_row = next((row for row in api_module.rails() if row["name"] == "btc"), {})
	check(
		"api rails reports the same run the catalog counts",
		btc_row.get("gap_run") == gap_after,
		f"{btc_row.get('gap_run')} vs {gap_after}",
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
		"Solana reports its reference-bound payments as per-sale",
		offered.get("sol", {}).get("binding") == "per-sale",
		str(offered.get("sol", {})),
	)
	check(
		"an EVM rail without an xpub reports its address as shared",
		not (frappe.db.get_value("Crypto Rail", RAIL, "testnet_xpub") or "").strip()
		and offered.get(RAIL, {}).get("binding") == "shared",
		str(offered.get(RAIL, {})),
	)

	# `binding_category` was added after the first plugin wheel was published.
	# Requiring it removed that installed Solana adapter from discovery in all
	# four process environments until the host learned the pessimistic default.
	# Exercise the app's actual discovery path with that published shape: all
	# original fields and operations, deliberately no new declaration.
	sol_key = frappe.db.get_value("Crypto Rail", "sol", "catalog_key")
	installed_sol = catalog_module.plugins().get(sol_key)

	class PublishedPlugin:
		pass

	published = PublishedPlugin()
	for field in (
		"key",
		"capabilities",
		"validate_recipient",
		"readiness",
		"capture_baseline",
		"create_request",
		"observe",
		"settle",
	):
		setattr(published, field, getattr(installed_sol, field))
	real_entry_point_rails = catalog_module._entry_point_rails
	legacy_driveable = False
	legacy_category = ""
	legacy_refused = {}

	def published_entry_point_rails():
		return [("published 0.1.0", published)], {}

	try:
		catalog_module._entry_point_rails = published_entry_point_rails
		catalog_module._forget_plugins()
		legacy_plugins = catalog_module.plugins()
		legacy_refused = catalog_module.refused_plugins()
		legacy_driveable = legacy_plugins.get(sol_key) is published
		legacy_category = binding_category_for(published)
	finally:
		catalog_module._entry_point_rails = real_entry_point_rails
		catalog_module._forget_plugins()
		catalog_module.plugins()
	check(
		"a published plugin without binding_category stays driveable and defaults pessimistically",
		not hasattr(published, "binding_category")
		and legacy_driveable
		and legacy_category == NOT_UNCONDITIONAL
		and not legacy_refused,
		f"driveable={legacy_driveable} category={legacy_category!r} refused={legacy_refused}",
	)
	# A single address on a fresh-address rail is virgin until its first
	# payment: it charges perfectly, takes one payment, and then refuses every
	# sale afterwards. Refused at configuration time and again at charge time,
	# because a rail configured before this rule existed is still in the
	# database -- which is exactly how it was found.
	trap = frappe.get_doc("Crypto Rail", "btc")
	trap.testnet_xpub = ""
	trap.testnet_recipient = WATCHED_ADDRESS_BTC
	trap_refusal = _refusal_message(lambda: trap.save(ignore_permissions=True))
	check(
		"a single address is refused on a rail that derives its own",
		bool(trap_refusal) and "fresh receiving address" in trap_refusal,
		trap_refusal or "not refused",
	)
	trap.reload()
	frappe.db.set_value("Crypto Rail", "btc", "testnet_xpub", "", update_modified=False)
	frappe.db.set_value(
		"Crypto Rail", "btc", "testnet_recipient", WATCHED_ADDRESS_BTC, update_modified=False
	)
	check(
		"and charging one is refused even if it reached the database",
		_refuses(lambda: charge_module.charge(5000, "btc")),
	)
	frappe.db.set_value("Crypto Rail", "btc", "testnet_recipient", "", update_modified=False)
	frappe.db.set_value(
		"Crypto Rail", "btc", "testnet_xpub", HARNESS_ACCOUNT_VPUB, update_modified=False
	)
	frappe.db.commit()

	# Two rails sharing one account key both start at index zero and hand out
	# the SAME address. That is D5's collision again, across assets, and it
	# reproduced on this site before the rule below existed.
	# Only one deriving rail exists, so the collision needs a second one made
	# for the purpose. It is removed again whatever happens.
	twin_name = "btc-harness-twin"
	if frappe.db.exists("Crypto Rail", twin_name):
		frappe.delete_doc("Crypto Rail", twin_name, force=True, ignore_permissions=True)
	twin = frappe.new_doc("Crypto Rail")
	twin.update(
		{
			"rail_key": twin_name,
			"label": "Harness twin",
			"chain": "Bitcoin",
			"asset": "BTC",
			"family": "bitcoin",
			"unit_name": "satoshi",
			"native_decimals": 8,
			"display_decimals": 8,
			"gate_text": "harness fixture; never enabled",
			"enabled": 0,
			"testnet_xpub": HARNESS_ACCOUNT_VPUB,
		}
	)
	try:
		twin_refusal = _refusal_message(lambda: twin.insert(ignore_permissions=True))
		check(
			"two rails may not share one account key",
			bool(twin_refusal) and "already uses this extended public key" in twin_refusal,
			twin_refusal or "not refused",
		)
	finally:
		if frappe.db.exists("Crypto Rail", twin_name):
			frappe.delete_doc("Crypto Rail", twin_name, force=True, ignore_permissions=True)
		frappe.db.commit()

	# EVM uses ordinary xpub version bytes on testnets too. Bitcoin's vpub is a
	# P2WPKH declaration from SLIP-132, so accepting it here would derive a
	# valid-looking address for material explicitly exported for another family.
	evm = frappe.get_doc("Crypto Rail", RAIL)
	evm.testnet_recipient = ""
	evm.testnet_xpub = BIP84_ACCOUNT_VPUB
	wrong_family_refusal = _refusal_message(lambda: evm.save(ignore_permissions=True))
	check(
		"an EVM rail refuses Bitcoin vpub receiving material",
		bool(wrong_family_refusal) and "EVM" in wrong_family_refusal and "vpub" in wrong_family_refusal,
		wrong_family_refusal or "not refused",
	)

	evm.reload()
	evm.testnet_recipient = WATCHED_ADDRESS
	evm.testnet_xpub = BIP32_DEPTH_THREE_XPUB
	two_bindings_refusal = _refusal_message(lambda: evm.save(ignore_permissions=True))
	check(
		"an EVM rail refuses a derived key and a fixed recipient together",
		bool(two_bindings_refusal) and "different payment bindings" in two_bindings_refusal,
		two_bindings_refusal or "not refused",
	)

	evm.reload()
	evm.testnet_recipient = ""
	evm.testnet_xpub = BIP32_DEPTH_THREE_XPUB
	evm.next_address_index = 0
	evm.save(ignore_permissions=True)
	frappe.db.commit()
	evm_first = catalog_module.recipient_for(evm, "testnet")
	evm_second = catalog_module.recipient_for(evm, "testnet")
	check(
		"two EVM allocations receive two different addresses",
		evm_first != evm_second,
		f"{evm_first}, {evm_second}",
	)
	check(
		"every allocated EVM address verifies through the library validator",
		all(
			core_addresses.validate(RAIL, address, "testnet") == (core_addresses.OK, "")
			for address in (evm_first, evm_second)
		),
		f"{evm_first}, {evm_second}",
	)
	check(
		"the EVM address index advances exactly twice",
		int(frappe.db.get_value("Crypto Rail", RAIL, "next_address_index") or 0) == 2,
		str(frappe.db.get_value("Crypto Rail", RAIL, "next_address_index")),
	)

	# ---------------------------------------------------------------
	#     Money that arrived after the terminal stopped looking.
	# ---------------------------------------------------------------
	# A payment confirming after its sale's lock ran out is invisible: `poll`
	# returns immediately for a terminal state, the heartbeat selects only
	# in-flight sales, and on a per-sale address nothing looks again. Named in
	# D9 as a consequence of D7 and closed here.
	#
	# Testing it needs money that really arrived, and nothing here can send
	# any. So the fixture points an ended sale at a REAL testnet4 address with
	# real confirmed payments, and rewinds its baseline below them -- the
	# observation the reconciler performs is genuine even though the arrival
	# was not late in wall-clock terms.
	# TWO PHASES, and they are separated on purpose -- see the note by
	# WATCHED_PAYMENT_HEIGHT for what happens when they are not.
	#
	# PHASE 1: the sale must end having credited nothing. That is only
	# deterministic if NOTHING is above its baseline, so the baseline is the
	# live tip. The previous version rewound below a pinned payment here and
	# relied on every payment above it already being claimed by an earlier
	# harness sale -- true while runs kept pace with the address, false the
	# moment thirteen payments arrived between runs, which is what broke it.
	late = _charge(5000, "btc")
	extras = late.extras()
	extras["intent"]["recipient"] = WATCHED_ADDRESS_BTC
	extras["intent"]["baseline"]["recipient"] = WATCHED_ADDRESS_BTC
	extras["intent"]["baseline"]["tip"] = _testnet4_tip()
	late.db_set("identity_extras", json.dumps(extras), update_modified=False)
	late.db_set("identity_address", WATCHED_ADDRESS_BTC, update_modified=False)
	late.db_set("rate_lock_end", add_to_date(now_datetime(), seconds=-1), update_modified=False)
	late.reload()
	watch_module.poll(late.name)
	late.reload()
	check(
		"the fixture sale ended without crediting anything",
		late.state in ("expired", "needs_review") and int(late.credited_native or 0) == 0,
		f"state={late.state} credited={late.credited_native}",
	)

	# PHASE 2: now the money is "late" -- the sale has ended, and the baseline
	# is rewound below a payment that really exists, so the observation the
	# reconciler performs is genuine even though the arrival was not late in
	# wall-clock terms.
	extras["intent"]["baseline"]["tip"] = WATCHED_PAYMENT_HEIGHT - 1
	late.db_set("identity_extras", json.dumps(extras), update_modified=False)
	late.reload()

	swept_late = reconcile_module.sweep_late_payments()
	late.reload()
	check(
		"the reconciler finds money that arrived at an ended sale's address",
		any(row["sale"] == late.name for row in swept_late["found"]),
		str(swept_late),
	)
	check(
		"it records the finding on the sale rather than reopening it",
		late.state in ("expired", "needs_review")
		and any(
			event.source == "reconcile" and "late payment" in (event.detail or "")
			for event in late.events
		),
		f"state={late.state} events={[(e.source, e.detail) for e in late.events]}",
	)
	check(
		"an operator can see it",
		any(row["sale"] == late.name for row in api_module.late_payments()["rows"]),
	)
	check(
		"and a second sweep does not record it twice",
		not any(
			row["sale"] == late.name
			for row in reconcile_module.sweep_late_payments()["found"]
		),
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

	# ---------------------------------------------------------------
	# The LAST look is asked twice, and only the last one.
	#
	# WHAT THIS COST BEFORE IT EXISTED. On 2026-09-02 this deployment's
	# review queue held 26 sales and **18 of them were one sentence** --
	# "the rate lock ran out and the last look never reached the chain" --
	# every one with nothing ever sighted. They were unpaid sales whose
	# final read happened to time out, and each one demanded a human.
	#
	# A failure mid-window is free: the sale stays and the next heartbeat
	# asks again. The last look has no next heartbeat, so one refused read
	# ended the sale in `needs_review` permanently. It is asked twice now.
	#
	# The retry must NOT weaken "unknown is not unpaid" -- two failures
	# still end in review -- and must NOT double the cost of an ordinary
	# beat. All three are checked, because a retry seen working only in the
	# rescuing direction is a retry of unknown direction.
	# ---------------------------------------------------------------
	class _Flaky:
		"""The real adapter with `observe` made to fail a fixed number of times."""

		def __init__(self, inner, failures):
			self._inner, self._left = inner, failures
			self.calls = 0

		def __getattr__(self, name):
			return getattr(self._inner, name)

		def observe(self, *arguments, **keywords):
			self.calls += 1
			if self._left > 0:
				self._left -= 1
				raise RuntimeError("harness: simulated unreachable endpoint")
			return self._inner.observe(*arguments, **keywords)

	def _poll_with_failures(failures, expire_lock):
		flaky_sale = _charge(2500)
		if expire_lock:
			flaky_sale.db_set(
				"rate_lock_end",
				add_to_date(now_datetime(), minutes=-1),
				update_modified=False,
			)
			frappe.db.commit()
		inner = catalog_module.plugin_for(frappe.get_doc("Crypto Rail", flaky_sale.rail_key))
		flaky = _Flaky(inner, failures)
		real_plugin_for = catalog_module.plugin_for
		catalog_module.plugin_for = lambda rail: flaky
		watch_module.catalog.plugin_for = lambda rail: flaky
		try:
			watch_module.poll(flaky_sale.name)
		finally:
			catalog_module.plugin_for = real_plugin_for
			watch_module.catalog.plugin_for = real_plugin_for
		return frappe.get_doc("Crypto Sale", flaky_sale.name), flaky.calls

	# ---------------------------------------------------------------
	# The cover queue: intent here, signing on the host.
	#
	# The container cannot pay -- the Ootle key is on the host -- so the
	# button records a request and `demo_payer.py` claims it. What must hold
	# here is that a request is never handed over twice, and that a sale which
	# ENDED before the house got to it is resolved rather than left saying
	# "paying" on a visitor's screen forever.
	# ---------------------------------------------------------------
	cover_eth = _charge(2500)
	check(
		"a cover is refused on a rail the house cannot pay",
		_refuses(lambda: api_module.request_cover(cover_eth.name)),
		"only xtr has a payer that is not the customer's own wallet",
	)

	# ANY REQUEST ALREADY IN THE QUEUE IS SOMEBODY ELSE'S. Claiming marks a
	# sale `paying`, so a harness run must not swallow a live visitor's
	# request and leave it marked for a payer that will never see it.
	foreign = set(
		frappe.get_all("Crypto Sale", filters={"demo_cover_state": "requested"}, pluck="name")
	)

	cover_sale = _charge(2500, "xtr")
	queued = api_module.request_cover(cover_sale.name)
	check("a cover request is queued", queued["queued"] is True, str(queued))
	check("...as `requested`", queued["demo_cover_state"] == "requested",
	      str(queued["demo_cover_state"]))
	check(
		"asking twice queues once",
		api_module.request_cover(cover_sale.name)["queued"] is False,
		"a second press must not create a second payment",
	)

	api_module.claim_covers(limit=25, peek=1)
	check(
		"a peek does not claim",
		frappe.db.get_value("Crypto Sale", cover_sale.name, "demo_cover_state") == "requested",
		"a dry run that consumed the queue would strand real requests",
	)

	ended = _charge(2500, "xtr")
	api_module.request_cover(ended.name)
	ended.reload()
	ended.transition_to("expired", source="harness",
	                    detail="ended before the house could pay it", end_kind="clean")
	ended.save(ignore_permissions=True)

	# THE PAUSE IS PROVED BEFORE IT IS BYPASSED. If this returned rows the
	# protection would be decorative, and the next harness run would spend
	# real money again.
	check(
		"a paused cover queue hands the payer nothing",
		api_module.claim_covers(limit=25) == [],
		"this is what keeps a harness run from spending real money",
	)
	check("...and the pause reads as on", api_module.covers_paused() is True,
	      str(api_module.covers_paused()))

	# `ignore_pause` is the harness's own key to the queue it locked. The
	# pause stays up throughout, so the live payer never sees these requests.
	claimed = api_module.claim_covers(limit=25, ignore_pause=1)
	handed = {row["name"] for row in claimed}
	for name in foreign & handed:
		# Put somebody else's request back exactly as it was found.
		frappe.db.set_value("Crypto Sale", name, "demo_cover_state", "requested",
		                    update_modified=False)

	check("a requested cover is handed to the payer", cover_sale.name in handed)
	cover_after_claim = frappe.db.get_value("Crypto Sale", cover_sale.name, "demo_cover_state")
	check(
		"...and marked `paying` so it cannot be handed over twice",
		cover_after_claim == "paying",
		str(cover_after_claim),
	)
	check(
		"a cover on a sale that ENDED is not handed over",
		ended.name not in handed,
		"paying it would put money into a window that has already shut",
	)
	ended_state = frappe.db.get_value("Crypto Sale", ended.name, "demo_cover_state")
	check(
		"...and is resolved rather than left saying `paying` forever",
		ended_state == "refused",
		str(ended_state),
	)
	check(
		"...with a reason the visitor can read",
		bool(frappe.db.get_value("Crypto Sale", ended.name, "demo_cover_note")),
		frappe.db.get_value("Crypto Sale", ended.name, "demo_cover_note") or "(empty)",
	)

	api_module.report_cover(cover_sale.name, "refused", "harness: nothing was paid")
	reported = frappe.db.get_value("Crypto Sale", cover_sale.name, "demo_cover_state")
	check("the host can report back what it did", reported == "refused", str(reported))
	reported_note = frappe.db.get_value("Crypto Sale", cover_sale.name, "demo_cover_note")
	check(
		"...and the reason lands on the sale",
		reported_note == "harness: nothing was paid",
		str(reported_note),
	)

	# A REFUSAL MUST NOT UN-PAY A SALE THE HOUSE ALREADY PAID.
	#
	# `claim_covers` marks a sale `paying` in an ordinary transaction, and a
	# deadlock with the watcher writing the same row rolls that mark back to
	# `requested`. The payer then claims it a second time and `pay_sale`
	# correctly refuses to pay twice -- and that refusal used to be written
	# straight over `covered`. Measured live on CPS-2026-00772: paid, settled
	# on that transaction, booked, and recorded as not covered.
	api_module.report_cover(cover_sale.name, "covered", "harness: the house paid it")
	outcome = api_module.report_cover(
		cover_sale.name, "refused", "harness: a duplicate attempt was suppressed"
	)
	check(
		"a later refusal does not overwrite a cover that was paid",
		frappe.db.get_value("Crypto Sale", cover_sale.name, "demo_cover_state") == "covered",
		str(frappe.db.get_value("Crypto Sale", cover_sale.name, "demo_cover_state")),
	)
	check(
		"...and the refusal's reason does not replace the payment's either",
		frappe.db.get_value("Crypto Sale", cover_sale.name, "demo_cover_note")
		== "harness: the house paid it",
		str(frappe.db.get_value("Crypto Sale", cover_sale.name, "demo_cover_note")),
	)
	check(
		"...and the caller is told it was not recorded rather than lied to",
		outcome.get("recorded") is False and bool(outcome.get("why")),
		str(outcome),
	)

	rescued, rescued_looks = _poll_with_failures(1, expire_lock=True)
	check(
		"one failed final look is retried, not turned into a review item",
		rescued.state != "needs_review",
		f"state {rescued.state} after {rescued_looks} look(s)",
	)
	check("...and the retry is a second look, not a re-read of the first",
	      rescued_looks == 2, f"{rescued_looks} look(s)")

	stranded, stranded_looks = _poll_with_failures(2, expire_lock=True)
	check(
		"two failed final looks still end in review -- unknown is not unpaid",
		stranded.state == "needs_review",
		f"state {stranded.state} after {stranded_looks} look(s)",
	)
	check("...saying both looks failed, which is what was earned",
	      "two looks" in (stranded.review_reason or ""),
	      stranded.review_reason or "")

	beat, beat_looks = _poll_with_failures(1, expire_lock=False)
	check(
		"a failure with lock time left is ONE look and no transition",
		beat_looks == 1 and beat.state == "awaiting",
		f"state {beat.state} after {beat_looks} look(s)",
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
