"""The policy tier — awarding points against a settled sale.

Two rules govern everything in this file, and they point in opposite
directions on purpose.

  1. A sale must NEVER fail because the policy layer is down. Every function
     here is total, every refusal is a recorded outcome rather than an
     exception, and the whole path runs strictly after the sale is booked.

  2. Nothing here may claim more than the network confirmed. The default
     wording is the degraded one; only a committed mint upgrades it.

## Why ERPNext's own Loyalty Program is deliberately NOT used

ERPNext models a point's value as `Loyalty Program.conversion_factor` — one
editable Float, read live at redemption time, with no per-entry snapshot.
Changing it retroactively revalues every point ever earned under that
programme. That is precisely the airline-miles devaluation this contract was
built to make structurally impossible, so using those tables as the ledger
would hand back the property the contract exists to provide.

There is a second and sharper reason. Creating `Loyalty Point Entry` rows
lights up ERPNext's redemption UI: a cashier could apply a discount and post
GL entries against points that cannot be spent at all — `withdraw` is
`DenyAll` and `Locked` on chain, and enrolment is blocked on a co-signing
wallet that does not exist. The system would then be asserting the one thing
the operator is explicitly told never to say.

So the chain is the ledger, `Crypto Loyalty Award` is the local mirror, and
ERPNext's loyalty tables are left alone. `harness_loyalty.py` asserts that
no Loyalty Point Entry is ever created.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from cryptopos import ootle


def points_for(sale):
	"""Points this sale earns, from the rate snapshotted at charge.

	The earn rate is OPERATIONAL — the merchant changes it freely, and it is
	frozen onto the sale so a receipt reprinted after a promotion ends still
	says what the customer was told. It is NOT the redemption rate, which is
	constitutional and lives in the contract.
	"""
	rate = int(sale.loyalty_earn_rate or 0)
	if rate <= 0:
		return 0
	return rate * int(sale.usd_cents or 0)


def _refusal(sale, settings, points):
	"""Ordered checks. Returns (state, reason) or (None, None) to proceed."""
	if sale.mode == "demo":
		return "not_offered", _("Demo mode awards nothing.")
	if not settings.loyalty_enabled:
		return "not_offered", _("Loyalty is switched off for this terminal.")
	if points <= 0:
		return "not_offered", _("No earn rate was in effect for this sale.")
	if not (settings.loyalty_component or "").strip():
		return "refused", _("No loyalty component is configured.")
	if not (sale.loyalty_account or "").strip():
		# The ordinary case. A customer who presents nothing still gets their
		# sale; the award is the only thing that does not happen.
		return "not_offered", _("No customer account was presented at the till.")
	return None, None


def request_award(sale):
	"""Queue an award for a settled sale. Total: never raises, never blocks.

	Returns the Crypto Loyalty Award name, or None when nothing was recorded.
	"""
	try:
		return _request_award(sale)
	except Exception:
		# The sale is already income. Nothing below that line may reach back
		# and disturb it, including a bug in this file.
		frappe.log_error(
			title=f"cryptopos loyalty request failed for {sale.name}",
			message=frappe.get_traceback(),
		)
		return None


def _request_award(sale):
	if frappe.db.exists("Crypto Loyalty Award", {"sale": sale.name}):
		# One sale, one award. A second submission is a second mint, and
		# nothing on this contract can burn the duplicate.
		return None

	settings = frappe.get_cached_doc("CryptoPoS Settings")
	points = points_for(sale)

	award = frappe.new_doc("Crypto Loyalty Award")
	award.update(
		{
			"sale": sale.name,
			"points": points,
			"account": (sale.loyalty_account or "").strip(),
			"sale_ref": sale.invoice_ref,
			"usd_cents": sale.usd_cents,
			"earn_rate_snapshot": sale.loyalty_earn_rate or 0,
			"component": (settings.loyalty_component or "").strip(),
			"points_resource": (settings.loyalty_points_resource or "").strip(),
			"requested_at": now_datetime(),
		}
	)

	state, reason = _refusal(sale, settings, points)
	if state:
		award.state = state
		award.reason = reason
		award.insert(ignore_permissions=True)
		return award.name

	# Read the contract before queueing, so a ceiling refusal is caught here
	# rather than after a fee has been spent. This read is free and keyless;
	# if it fails the award is refused rather than guessed at.
	facts, why = ootle.promise()
	if facts is None:
		award.state = "refused"
		award.reason = _("The contract could not be read: {0}").format(why)
		award.insert(ignore_permissions=True)
		return award.name

	award.redemption_rate_at_award = facts["redemption_rate"]

	if points > facts["per_issue_ceiling"]:
		award.state = "refused"
		award.reason = _(
			"{0:,} points is above the per-award ceiling of {1:,}. The ceiling "
			"can be lowered and never raised, so this cannot be retried larger."
		).format(points, facts["per_issue_ceiling"])
		award.insert(ignore_permissions=True)
		return award.name

	award.state = "pending"
	award.insert(ignore_permissions=True)
	return award.name


def award_for_settled_sale(sale_name):
	"""Background entry point, enqueued after a sale books."""
	sale = frappe.get_doc("Crypto Sale", sale_name)
	if sale.state != "confirmed":
		return None
	name = request_award(sale)
	frappe.db.commit()
	return name
