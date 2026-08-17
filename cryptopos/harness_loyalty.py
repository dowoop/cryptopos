"""End-to-end harness for the policy tier.

    bench --site erp.localhost execute cryptopos.harness_loyalty.run

Reads the real deployed contract on esmeralda. Writes nothing to the chain --
the write path is the host drainer, and a harness that mints would spend a
fee and create points nothing can burn.

The checks are grouped by the rule each one defends, because a harness that
reports PASS while checking a fraction of what it claims is the failure this
project keeps finding.
"""

import json

import frappe
from frappe.utils import now_datetime

from cryptopos import loyalty, ootle

PASS = []
FAIL = []

# Verified on-chain against the K1 component; all four resource slots match
# ootle-testnet/ADDRESSES.md, which is what fixes the positional mapping.
EXPECTED_RATE = 100
EXPECTED_PER_ISSUE = 1_000_000
EXPECTED_PER_EPOCH = 10_000_000

CUSTOMER_ACCOUNT = "component_" + "a1" * 32


def check(rule, condition, detail=""):
	(PASS if condition else FAIL).append(f"{rule}{(' -- ' + detail) if detail else ''}")


def _settled_sale(usd_cents=5000, account="", earn_rate=2):
	"""A sale already past its ending. The award path only ever sees these."""
	sale = frappe.new_doc("Crypto Sale")
	sale.update(
		{
			"state": "idle",
			"mode": "testnet",
			"provenance": "REAL",
			"charged_at": now_datetime(),
			"rail_key": "btc",
			"usd_cents": usd_cents,
			"invoiced_native": "1000",
			"credited_native": "1000",
			"rate_microcents": 6_400_000_000,
			"rate_source": "harness",
			"rate_at": now_datetime(),
			"rate_lock_end": now_datetime(),
			"identity_address": "tb1qharness",
			"identity_source": "config",
			"uri": "bitcoin:tb1qharness",
			"invoice_id": f"INV-HARNESS-{frappe.generate_hash(length=6)}",
			"invoice_ref": frappe.generate_hash(length=12).upper(),
			"loyalty_earn_rate": earn_rate,
			"loyalty_account": account,
		}
	)
	sale.insert(ignore_permissions=True)
	sale.transition_to("awaiting", source="harness")
	sale.transition_to("confirmed", source="harness", end_kind="clean", settled_at=now_datetime())
	sale.save(ignore_permissions=True)
	return sale


def run():
	PASS.clear()
	FAIL.clear()

	# -----------------------------------------------------------------
	# 1. The contract can be read, free and keyless.
	# -----------------------------------------------------------------
	reachable, detail = ootle.available()
	check("the indexer is reachable without a key or a fee", reachable, str(detail))

	facts, why = ootle.promise()
	check("the deployed contract can be read", facts is not None, str(why))

	if facts:
		check(
			"the redemption rate reads as deployed",
			facts["redemption_rate"] == EXPECTED_RATE,
			f"{facts['redemption_rate']} points/cent",
		)
		check(
			"the per-award ceiling reads as deployed",
			facts["per_issue_ceiling"] == EXPECTED_PER_ISSUE,
			f"{facts['per_issue_ceiling']:,}",
		)
		check(
			"the per-epoch ceiling reads as deployed",
			facts["per_epoch_ceiling"] == EXPECTED_PER_EPOCH,
			f"{facts['per_epoch_ceiling']:,}",
		)
		check(
			"the component has no owner, so no upgrade path exists",
			facts["owner_rule"] == "None",
			str(facts["owner_rule"]),
		)
		check(
			"the points resource matches the one configured",
			facts["points_resource"].startswith("resource_73c42829"),
			facts["points_resource"],
		)

	# -----------------------------------------------------------------
	# 2. A shape this build does not recognise is a refusal, not a guess.
	# -----------------------------------------------------------------
	settings = frappe.get_single("CryptoPoS Settings")
	original_resource = settings.loyalty_points_resource
	settings.db_set("loyalty_points_resource", "resource_" + "ff" * 32, update_modified=False)
	frappe.clear_cache(doctype="CryptoPoS Settings")
	wrong, wrong_why = ootle.promise()
	check(
		"a points resource that the component does not name is refused",
		wrong is None and "refusing" in (wrong_why or ""),
		str(wrong_why)[:80],
	)
	settings.db_set("loyalty_points_resource", original_resource, update_modified=False)
	frappe.clear_cache(doctype="CryptoPoS Settings")

	original_indexer = settings.ootle_indexer
	settings.db_set("ootle_indexer", "https://127.0.0.1:9", update_modified=False)
	frappe.clear_cache(doctype="CryptoPoS Settings")
	down, down_why = ootle.promise()
	check("an unreachable indexer returns a reason, never an exception", down is None and bool(down_why))
	settings.db_set("ootle_indexer", original_indexer, update_modified=False)
	frappe.clear_cache(doctype="CryptoPoS Settings")

	# -----------------------------------------------------------------
	# 3. Nothing about loyalty may fail a sale.
	# -----------------------------------------------------------------
	no_account = _settled_sale(account="")
	award_name = loyalty.request_award(no_account)
	no_account.reload()
	check(
		"a customer who presents nothing still gets their sale",
		no_account.state == "confirmed",
		no_account.state,
	)
	award = frappe.get_doc("Crypto Loyalty Award", award_name)
	check(
		"presenting no account is not_offered, not an error",
		award.state == "not_offered",
		award.state,
	)
	check("that refusal carries a reason a cashier can read", bool(award.reason), award.reason)
	check("nothing is claimed for it", not award.claims_points())

	# With the policy layer pointed at nothing, a settled sale must survive.
	settings.db_set("ootle_indexer", "https://127.0.0.1:9", update_modified=False)
	frappe.clear_cache(doctype="CryptoPoS Settings")
	blind_sale = _settled_sale(account=CUSTOMER_ACCOUNT)
	blind_award = frappe.get_doc("Crypto Loyalty Award", loyalty.request_award(blind_sale))
	blind_sale.reload()
	check(
		"a settled sale survives the policy layer being down",
		blind_sale.state == "confirmed",
		blind_sale.state,
	)
	check(
		"an unreadable contract refuses the award rather than guessing",
		blind_award.state == "refused",
		blind_award.state,
	)
	settings.db_set("ootle_indexer", original_indexer, update_modified=False)
	frappe.clear_cache(doctype="CryptoPoS Settings")

	# -----------------------------------------------------------------
	# 4. The ordinary path: a sale with an account queues an award.
	# -----------------------------------------------------------------
	good = _settled_sale(usd_cents=5000, account=CUSTOMER_ACCOUNT, earn_rate=2)
	good_award = frappe.get_doc("Crypto Loyalty Award", loyalty.request_award(good))
	check("a sale with an account queues an award", good_award.state == "pending", good_award.state)
	check(
		"points are the snapshotted earn rate times the sale",
		int(good_award.points) == 2 * 5000,
		str(good_award.points),
	)
	check(
		"the on-chain reference is the unguessable ref, not the sequential id",
		good_award.sale_ref == good.invoice_ref and good_award.sale_ref != good.invoice_id,
		good_award.sale_ref,
	)
	check(
		"the constitutional rate is recorded beside the operational one",
		int(good_award.redemption_rate_at_award) == EXPECTED_RATE
		and int(good_award.earn_rate_snapshot) == 2,
		f"redemption={good_award.redemption_rate_at_award} earn={good_award.earn_rate_snapshot}",
	)
	check("a pending award claims nothing yet", not good_award.claims_points())
	check(
		"its wording is the degraded one until the network says otherwise",
		"NOT ISSUED" in good_award.wording(),
		good_award.wording()[:60],
	)

	# One sale, one award.
	again = loyalty.request_award(good)
	check("a second request for the same sale is refused", again is None)

	# -----------------------------------------------------------------
	# 5. The ceiling is enforced before a fee is spent.
	# -----------------------------------------------------------------
	over = _settled_sale(usd_cents=10_000_000, account=CUSTOMER_ACCOUNT, earn_rate=2)
	over_award = frappe.get_doc("Crypto Loyalty Award", loyalty.request_award(over))
	check(
		"an award above the per-award ceiling is refused locally",
		over_award.state == "refused",
		over_award.state,
	)
	check(
		"the refusal names the ceiling and says it cannot be raised",
		"ceiling" in (over_award.reason or "") and "never raised" in (over_award.reason or ""),
		(over_award.reason or "")[:70],
	)

	# -----------------------------------------------------------------
	# 6. THE REFUSAL THAT MATTERS: ERPNext's loyalty tables are untouched.
	#
	# ERPNext values a point from a live, editable conversion_factor, so
	# using it as the ledger would restore the devaluation this contract
	# makes impossible -- and its redemption UI would offer to spend points
	# that cannot be spent at all.
	# -----------------------------------------------------------------
	entries = frappe.db.count("Loyalty Point Entry")
	programs = frappe.db.count("Loyalty Program")
	check("no Loyalty Point Entry is ever created", entries == 0, str(entries))
	check("no Loyalty Program is created", programs == 0, str(programs))
	invoices = frappe.get_all("Sales Invoice", fields=["name", "loyalty_program", "loyalty_points"])
	check(
		"no emitted Sales Invoice carries a loyalty programme",
		all(not inv.loyalty_program for inv in invoices),
		f"{len(invoices)} invoices checked",
	)
	check(
		"no emitted Sales Invoice offers redeemable points",
		all(not inv.loyalty_points for inv in invoices),
	)

	# -----------------------------------------------------------------
	# 7. Every ceiling ships on the surface that offers the feature.
	# -----------------------------------------------------------------
	if facts:
		wording = ootle.ceilings_wording(facts)
		joined = " ".join(head + " " + body for head, body in wording)
		check("the locked rate is stated", "can never change" in joined)
		check(
			"the rate promise is distinguished from the price promise",
			"never that your points keep their value" in joined,
		)
		check("soulboundness is stated", "cannot be sold or transferred" in joined)
		check("both ceilings are stated with their direction", "tighten" in joined.lower())
		check("the remainder rule is stated", "remainder stays yours" in joined)
		check("what earning publishes is stated", "purchase history" in joined)
		check("the absence of an upgrade path is stated", "no upgrade path" in joined)

	notice = ootle.earning_only_notice()
	check("EARNING ONLY is stated", "EARNING ONLY" in notice)
	check(
		"the operator is told not to claim spending",
		"Do not tell a customer they can spend these" in notice,
	)

	if facts:
		urls = ootle.check_it_yourself(facts, CUSTOMER_ACCOUNT)
		check("the customer is handed real URLs to check", len(urls) == 3, str(len(urls)))
		check(
			"those URLs point at the indexer that was actually read",
			all(url.startswith(ootle.indexer()) for _label, url in urls),
		)

	# -----------------------------------------------------------------
	# 8. The queue survives being looked at.
	#
	# Regression: a --dry-run once claimed the queue, marking real awards
	# attempted and stranding them pending forever with nothing ever written.
	# Peeking must not consume.
	# -----------------------------------------------------------------
	from cryptopos import api

	pending_before = frappe.get_all(
		"Crypto Loyalty Award", filters={"state": "pending", "attempts": 0}, pluck="name"
	)
	if pending_before:
		api.claim_awards(limit=5, peek=1)
		still_unclaimed = frappe.get_all(
			"Crypto Loyalty Award", filters={"state": "pending", "attempts": 0}, pluck="name"
		)
		check(
			"peeking at the queue does not consume it",
			set(still_unclaimed) == set(pending_before),
			f"{len(pending_before)} before, {len(still_unclaimed)} after",
		)

		api.claim_awards(limit=1, peek=0)
		after_claim = frappe.get_all(
			"Crypto Loyalty Award", filters={"state": "pending", "attempts": 0}, pluck="name"
		)
		check(
			"claiming the queue does consume it",
			len(after_claim) == len(pending_before) - 1,
			f"{len(pending_before)} -> {len(after_claim)}",
		)
		check(
			"a claimed award is never handed out twice",
			not any(name in after_claim for name in [n for n in pending_before if n not in after_claim]),
		)

	# -----------------------------------------------------------------
	# 9. An award record cannot claim without evidence.
	# -----------------------------------------------------------------
	try:
		bogus = frappe.new_doc("Crypto Loyalty Award")
		bogus.update(
			{"sale": good.name, "state": "issued", "points": 1, "requested_at": now_datetime()}
		)
		bogus.insert(ignore_permissions=True)
		check("an issued award without a txid is refused", False, "it was accepted")
	except frappe.ValidationError:
		check("an issued award without a txid is refused", True)

	frappe.db.commit()

	print("")
	for line in PASS:
		print(f"  PASS  {line}")
	for line in FAIL:
		print(f"  FAIL  {line}")
	print("")
	print(f"  {len(PASS)} passed, {len(FAIL)} failed")
	if facts:
		print(
			f"  live contract: rate={facts['redemption_rate']} "
			f"per_issue={facts['per_issue_ceiling']:,} per_epoch={facts['per_epoch_ceiling']:,} "
			f"epoch={facts['window_epoch']}"
		)
	if FAIL:
		raise SystemExit(1)
	return {"passed": len(PASS), "failed": len(FAIL)}
