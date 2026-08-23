"""Money that arrived after the terminal stopped looking.

A sale ends. `poll` returns immediately for a terminal state, the heartbeat
selects only sales still in flight, and on a rail that derives a fresh address
per sale nothing else ever watches that address again. So a payment confirming
one minute after the lock ran out is invisible: the customer paid, the sale
says expired, and no surface in this application mentions it.

That gap is a consequence of D7 and it is named in D9. This module closes it,
and the shape of the closing is the constraint:

**Nothing here reopens a sale.** `LEGAL` gives terminal states no transitions,
deliberately — a sale that has already told a customer something is not edited
afterwards, and a correction is a new record. So a late payment becomes an
append-only audit row and an operator-facing list. What to do about it is a
human decision: refund it, honour it as a fresh sale, or leave it.

**Only per-sale addresses are reconciled.** On a shared address, money arriving
after a sale ended cannot be attributed to it — that is the whole of D5, and
guessing here would be the same mistake arriving through a later door.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from cryptopos import catalog

# How long after a sale ends its address is still worth a look. Long enough to
# cover a fee-starved transaction confirming overnight; short enough that the
# sweep does not grow without bound. An operator who needs to look further back
# has the address and the chain.
WINDOW_HOURS = 48

RECONCILE = "reconcile"
LATE = "late payment"


def _already_recorded(sale):
	return any(
		event.source == RECONCILE and LATE in (event.detail or "") for event in sale.events
	)


def look_again(sale):
	"""Observe one ended sale's address once more. Returns credited units, or 0.

	Raises nothing the caller must handle: an endpoint that does not answer is
	reported as zero found, because "nobody looked" and "nothing was there"
	are the same to a sweep that will run again in an hour. The distinction
	matters inside a sale's lifetime, and this is after it.
	"""
	rail = frappe.get_doc("Crypto Rail", sale.rail_key)
	extras = sale.extras()
	try:
		adapter = catalog.plugin_for(rail)
		intent = catalog.intent_from_record(extras.get("intent"))
	except Exception:
		return 0
	if intent is None:
		return 0

	configuration = {"endpoint": extras.get("endpoint") or ""}
	try:
		batch = adapter.observe(intent, configuration)
		while not batch.complete:
			batch = batch.extend(adapter.observe(intent, configuration, previous=batch))
	except Exception:
		return 0

	return sum(transfer.amount_native for transfer in batch.transfers)


def sweep_late_payments(limit=25):
	"""Scheduler entry point. Look again at recently ended, unpaid sales.

	Deliberately narrow, and every clause of the filter is load-bearing:

	  state       only endings that took nothing. A settled sale is already
	              accounted for and a part-paid one is already in review.
	  binding     per-sale only. See the module docstring, and D5.
	  credited    zero. Money already credited is not late.
	  window      recent. An address whose sale ended last month is the
	              operator's to reconcile by hand.
	"""
	since = add_to_date(now_datetime(), hours=-WINDOW_HOURS)
	found = []
	for name in frappe.get_all(
		"Crypto Sale",
		filters={
			"state": ("in", ("expired", "needs_review")),
			"binding": "per-sale",
			"credited_native": ("in", ("0", "", None)),
			"modified": (">=", since),
		},
		pluck="name",
		order_by="modified desc",
		limit=limit,
	):
		sale = frappe.get_doc("Crypto Sale", name)
		if _already_recorded(sale):
			continue
		native = look_again(sale)
		if native <= 0:
			continue
		sale._append_event(
			sale.state,
			sale.state,
			RECONCILE,
			_("{0}: {1} {2} arrived at this sale's address after it ended").format(
				LATE, native, frappe.db.get_value("Crypto Rail", sale.rail_key, "unit_name")
			),
		)
		sale.save(ignore_permissions=True)
		frappe.db.commit()
		found.append({"sale": name, "native": str(native), "rail_key": sale.rail_key})
	return {"checked": limit, "found": found}


def late_payments(limit=100):
	"""Ended sales whose address received money afterwards, for an operator.

	Read-only, and it reports rather than decides. Honouring a late payment is
	a new sale; refunding it is a transfer this terminal cannot make. Both are
	the operator's, and neither is something a sweep should do quietly.
	"""
	rows = []
	for name in frappe.get_all(
		"Crypto Sale",
		filters={"state": ("in", ("expired", "needs_review"))},
		pluck="name",
		order_by="modified desc",
		limit=limit,
	):
		sale = frappe.get_doc("Crypto Sale", name)
		for event in sale.events:
			if event.source == RECONCILE and LATE in (event.detail or ""):
				rows.append(
					{
						"sale": name,
						"rail_key": sale.rail_key,
						"address": sale.identity_address,
						"usd_cents": sale.usd_cents,
						"detail": event.detail,
						"at": event.at,
					}
				)
				break
	return rows
