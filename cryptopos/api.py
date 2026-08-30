"""Whitelisted endpoints — what the till surface is allowed to call."""

import json

import frappe
from frappe import _

from cryptopos import charge as charge_module
from cryptopos import watch as watch_module
from cryptopos_core.plugin import UNCONDITIONAL_PER_SALE


@frappe.whitelist()
def charge(usd_cents, rail_key, loyalty_account=""):
	"""Snapshot a sale and arm its watcher."""
	frappe.only_for(["System Manager", "Sales User"])
	sale = charge_module.charge(int(usd_cents), rail_key, loyalty_account or "")
	return status(sale.name)


@frappe.whitelist()
def poll(sale_name):
	"""Single-step the watcher, the way pressing 'Poll the node' does."""
	frappe.only_for(["System Manager", "Sales User"])
	watch_module.poll(sale_name)
	return status(sale_name)


@frappe.whitelist()
def status(sale_name):
	"""Everything a surface needs to draw one sale honestly."""
	sale = frappe.get_doc("Crypto Sale", sale_name)
	rail = frappe.get_doc("Crypto Rail", sale.rail_key)
	bookable, reason = sale.may_book()

	return {
		"name": sale.name,
		"state": sale.state,
		"state_word": sale.state_word,
		"end_kind": sale.end_kind,
		"review_reason": sale.review_reason,
		"mode": sale.mode,
		"provenance": sale.provenance,
		"uri": sale.uri,
		"qr_modules": json.loads(sale.qr_modules) if sale.qr_modules else None,
		"usd_cents": sale.usd_cents,
		"invoiced_native": sale.invoiced_native,
		"credited_native": sale.credited_native,
		"sighted_native": sale.sighted_native,
		"unit_name": rail.unit_name,
		# The ceiling ships beside the promise it bounds, on every surface
		# that offers the feature -- not in documentation to be found later.
		"gate_text": rail.gate_text,
		"binding": sale.binding,
		"identity_source": sale.identity_source,
		"identity_address": sale.identity_address,
		"rate_microcents": sale.rate_microcents,
		"rate_source": sale.rate_source,
		"rate_at": str(sale.rate_at or ""),
		"rate_lock_end": str(sale.rate_lock_end or ""),
		"tx_id": sale.tx_id,
		"settled_at": str(sale.settled_at or ""),
		"invoice_id": sale.invoice_id,
		"invoice_ref": sale.invoice_ref,
		"sales_invoice": sale.sales_invoice,
		"bookable": bookable,
		"not_bookable_because": reason,
		"events": [
			{
				"at": str(event.at),
				"from_state": event.from_state,
				"to_state": event.to_state,
				"source": event.source,
				"detail": event.detail,
			}
			for event in sale.events
		],
	}


# ---------------------------------------------------------------------------
# The award queue. Spoken to only by the host-side drainer.
#
# The signing key never enters this container, so the write path cannot live
# here. The container holds intent; the host holds the key and does the
# writing. That split is a security property, not a workaround for the fact
# that the toolkit binary will not load against this image's glibc.
# ---------------------------------------------------------------------------
@frappe.whitelist()
def claim_awards(limit=5, peek=0):
	"""Hand the drainer the awards waiting to be written.

	Marks each as attempted in the same transaction it is handed over. An
	award is never handed out twice: a second submission is a second mint,
	and nothing on this contract can burn the duplicate.

	`peek` reads the queue without claiming it. A dry run that consumed the
	queue would leave real awards stranded at attempts=1 with nothing ever
	written for them, which is the failure this argument exists to prevent.
	"""
	frappe.only_for("System Manager")
	peek = int(peek or 0)
	names = frappe.get_all(
		"Crypto Loyalty Award",
		filters={"state": "pending", "attempts": 0},
		order_by="creation asc",
		limit=int(limit),
		pluck="name",
	)
	claimed = []
	for name in names:
		award = frappe.get_doc("Crypto Loyalty Award", name)
		if not peek:
			award.db_set("attempts", 1, update_modified=False)
			award.db_set("attempted_at", frappe.utils.now_datetime(), update_modified=False)
		claimed.append(
			{
				"name": award.name,
				"component": award.component,
				"account": award.account,
				"points": int(award.points or 0),
				"sale_ref": award.sale_ref,
				"points_resource": award.points_resource,
			}
		)
	frappe.db.commit()
	return claimed


@frappe.whitelist()
def report_award(name, state, tx_id="", output="", reason=""):
	"""Record what the network said. Terminal — an award is written once."""
	frappe.only_for("System Manager")
	if state not in ("issued", "refused", "unverified"):
		frappe.throw(_("{0} is not a reportable award state.").format(state))

	award = frappe.get_doc("Crypto Loyalty Award", name)
	if award.state != "pending":
		# Already resolved. Re-reporting must not rewrite a record the
		# customer may already have been handed.
		return {"name": award.name, "state": award.state, "rewritten": False}

	award.state = state
	award.tx_id = tx_id or ""
	award.toolkit_output = (output or "")[:8000]
	award.reason = reason or ""
	award.finished_at = frappe.utils.now_datetime()
	award.save(ignore_permissions=True)
	frappe.db.commit()
	return {"name": award.name, "state": award.state, "rewritten": True}


@frappe.whitelist()
def loyalty_status(sale_name=None, account=""):
	"""Everything a surface needs to talk about points honestly.

	Includes the ceilings, because a ceiling ships on the surface that
	offers the feature rather than in documentation to be found later.
	"""
	from cryptopos import ootle

	reachable, detail = ootle.available()
	facts, why = ootle.promise() if reachable else (None, detail)

	award = None
	if sale_name:
		found = frappe.get_all(
			"Crypto Loyalty Award", filters={"sale": sale_name}, limit=1, pluck="name"
		)
		if found:
			doc = frappe.get_doc("Crypto Loyalty Award", found[0])
			award = {
				"name": doc.name,
				"state": doc.state,
				"points": int(doc.points or 0),
				"reason": doc.reason,
				"tx_id": doc.tx_id,
				"wording": doc.wording(),
				"claims_points": doc.claims_points(),
			}

	balance = None
	balance_reason = ""
	if account and facts:
		balance, balance_reason = ootle.points_balance(account, facts["points_resource"])

	return {
		"reachable": reachable,
		"unreachable_because": "" if reachable else str(detail),
		"facts": facts,
		"unreadable_because": why if facts is None else "",
		"earning_only": ootle.earning_only_notice(),
		"ceilings": ootle.ceilings_wording(facts) if facts else [],
		"check_it_yourself": ootle.check_it_yourself(facts, account) if facts else [],
		"award": award,
		"balance": balance,
		"balance_reason": balance_reason,
	}


@frappe.whitelist()
def rails(with_readiness=0):
	"""Enabled rails, with the maturity note the operator is owed.

	`gap_run` is the count of consecutive most-recent endings on the rail that
	credited nothing. It matters only on a rail deriving a fresh address per
	sale: each unpaid sale leaves an unused address behind, and a wallet
	restored from the account key stops scanning after `catalog.GAP_LIMIT` of
	them, so money paid past that run is money the operator's own wallet will
	not find. It is a warning and never a gate -- refusing a customer over a
	wallet-scanning convention would be the terminal overreaching.
	"""
	from cryptopos import catalog

	with_readiness = bool(int(with_readiness or 0))
	mode = frappe.get_single("CryptoPoS Settings").mode if with_readiness else None
	rows = frappe.get_all(
		"Crypto Rail",
		filters={"enabled": 1},
		fields=[
			"name",
			"label",
			"asset",
			"chain",
			"family",
			"maturity",
			"maturity_note",
			"gate_text",
			"testnet_xpub",
		],
		order_by="label",
	)
	for row in rows:
		derives = bool((row.pop("testnet_xpub", "") or "").strip())
		rail = frappe.get_doc("Crypto Rail", row["name"])
		declared = catalog.declared_binding_category(catalog.plugin_for(rail))
		row["binding"] = "per-sale" if declared == UNCONDITIONAL_PER_SALE or derives else "shared"
		row["gap_run"] = catalog.gap_run_for(rail) if derives else 0
		row["gap_limit"] = catalog.GAP_LIMIT
		if with_readiness:
			readiness = catalog.readiness_for(rail, mode)
			row["readiness"] = {
				"rail_key": readiness.rail_key,
				"ready": sorted(readiness.ready),
				"unavailable": [
					{"capability": capability, "reason": reason}
					for capability, reason in readiness.unavailable
				],
				"chargeable": readiness.chargeable,
			}
	return rows


@frappe.whitelist()
def unbooked():
	"""Money this terminal took that the ledger has not been told about.

	The oversight question, asked from outside. It is read-only on purpose:
	the retry is the scheduler's (`cryptopos.settle.sweep_unbooked`), and a
	button that books on demand would let a surface decide something the
	booking equation is supposed to decide.
	"""
	from cryptopos import settle

	rows = settle.unbooked()
	return {
		"rows": rows,
		"count": len(rows),
		"bookable": sum(1 for row in rows if row["bookable"]),
		"usd_cents": sum(row["usd_cents"] or 0 for row in rows),
	}


@frappe.whitelist()
def settled_not_in_ledger_count(filters=None):
	"""Number-card value: settled sales still absent from the ledger."""
	return {
		"value": unbooked()["count"],
		"fieldtype": "Int",
		"route": ["List", "Crypto Sale"],
		"route_options": {"state": "confirmed", "sales_invoice": ["is", "not set"]},
	}


@frappe.whitelist()
def settled_not_in_ledger_usd(filters=None):
	"""Number-card value: charged USD still absent from the ledger."""
	return {
		"value": unbooked()["usd_cents"] / 100.0,
		"fieldtype": "Currency",
		"currency": "USD",
		"route": ["List", "Crypto Sale"],
		"route_options": {"state": "confirmed", "sales_invoice": ["is", "not set"]},
	}


@frappe.whitelist()
def late_payments():
	"""Ended sales whose receiving address took money afterwards.

	The other half of the oversight question. `unbooked` asks what the ledger
	has not been told about; this asks what the terminal itself never saw --
	a payment confirming after its sale's lock ran out, on an address no later
	sale watches. Read-only: honouring one is a new sale and refunding one is a
	transfer this terminal cannot make, so both stay the operator's.
	"""
	from cryptopos import reconcile

	rows = reconcile.late_payments()
	return {"rows": rows, "count": len(rows)}
