"""Whitelisted endpoints — what the till surface is allowed to call."""

import json

import frappe
from frappe import _

from cryptopos import charge as charge_module
from cryptopos import watch as watch_module


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
def rails():
	"""Enabled rails, with the maturity note the operator is owed."""
	return frappe.get_all(
		"Crypto Rail",
		filters={"enabled": 1},
		fields=["name", "label", "asset", "chain", "family", "maturity", "maturity_note", "gate_text"],
		order_by="label",
	)
