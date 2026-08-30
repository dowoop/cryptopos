"""The watcher — one heartbeat, driving one state machine.

The terminal is watch-only: nothing here ever signs or sends. It asks the
chain what it can see, and it is careful about the difference between three
answers that are easy to blur together:

  * money arrived and can be tied to THIS sale        -> credited (books)
  * money arrived and cannot be tied to this sale     -> sighted (never books)
  * the question could not be asked at all            -> unverified

The third one is why the network errors are not swallowed. A heartbeat that
fails is not a heartbeat that found nothing, and a sale whose final look
never reached the chain must end saying "could not verify" rather than
"expired, unpaid" -- the second is a claim about the world, and the terminal
did not make an observation that supports it.
"""

import json

import frappe
from frappe.utils import get_datetime, now_datetime

from cryptopos import catalog
from cryptopos_core.plugin import NEEDS_REVIEW, SETTLED


def _why(exception):
	"""An exception as text that says something, even when it carries no message.

	Fourteen sales on this instance recorded their failure as `"final look did
	not reach the chain: "` -- with nothing after the colon, because
	`str(exception)` is empty for any exception raised without arguments, and
	`ChainUnreachable`, `TimeoutError` and `OSError` are all commonly raised
	that way. A rate limit, a DNS failure and a read timeout were therefore
	indistinguishable in the only place that remembered them, and the root
	cause of the only failure mode this terminal has ever shown could not be
	recovered afterwards.

	The class name is always available and is never empty, so it leads.
	"""
	text = str(exception).strip()
	name = type(exception).__name__
	return f"{name}: {text}" if text else name


class ChainUnreachable(Exception):
	"""The question could not be asked. Not the same as a negative answer.

	Kept as the name the terminal uses for that third answer. The reads
	themselves now belong to the rail adapters, which raise their own
	`CryptoPosError` subclasses; this is what the app calls the category.
	"""


def _claimed_transaction_ids(sale):
	"""Every transaction other sales at this address have already bound.

	**All of them, not just each sale's headline `tx_id`.** A settlement can
	be made of several transfers -- a customer who pays 600 and then 400
	against a 1,000 invoice settles on two -- and `SettlementDecision`
	returns every id it credited. Reading only `tx_id` back would leave the
	second transfer looking unclaimed, so a later sale at the same address
	could credit itself with money already booked. `tx_id` remains the one a
	receipt prints; the full set is what the defense is made of.

	This is a defense, not a proof of exclusivity. See DECISIONS.md on
	per-sale addresses: a shared address cannot be made safe by bookkeeping.
	"""
	# `FOR UPDATE`, and it is the whole defense against two workers crediting
	# one transaction twice. The claimed set used to be read, then settled
	# against, then written -- with nothing holding the rows in between, so a
	# scheduler heartbeat and a cashier pressing the button could both read
	# "unclaimed", both settle, and both book the same coins. Locking the
	# other sales at this address until this transaction commits serialises
	# that window. `identity_address` is indexed so the lock is on those rows
	# rather than on the table.
	claimed = set()
	for row in frappe.db.sql(
		"""SELECT tx_id, watch_scratch FROM `tabCrypto Sale`
		   WHERE identity_address = %(address)s AND name != %(name)s
		   FOR UPDATE""",
		{"address": sale.identity_address, "name": sale.name},
		as_dict=True,
	):
		if row.tx_id:
			claimed.add(row.tx_id)
		try:
			claimed.update(json.loads(row.watch_scratch or "{}").get("settled_tx_ids") or [])
		except (TypeError, ValueError):
			continue
	return frozenset(identifier for identifier in claimed if identifier)


def _unbindable_reason(rail, sighted, gate):
	"""Why money sitting at this address is not booked, in terms this rail earns.

	A rail that derives a fresh address per sale (D7) **can** prove the money is
	this sale's: the address exists for no other purpose and no other sale will
	ever be given it. Telling that operator the payment "is not provably this
	customer's" is not a hedge, it is false -- and false in the direction that
	makes them distrust the one binding in this deployment that actually works.

	A rail receiving at a shared address cannot make that claim (D5), and for it
	the original wording is exactly right. So the sentence follows the binding.
	"""
	if (getattr(rail, "testnet_xpub", "") or "").strip():
		gate_text = f"{gate} confirmation{'' if gate == 1 else 's'}" if gate else "its settlement gate"
		return (
			f"{sighted} arrived at this sale's own address. This address was "
			f"derived for this sale and for nothing else, so the money IS this "
			f"customer's payment. What it did not do is reach {gate_text} before "
			f"the rate lock ran out. Once the transaction confirms it can be "
			f"booked by hand against this sale, and against no other."
		)
	return (
		f"{sighted} arrived at this address inside the window but could not be "
		f"tied to this sale. It is real money and it is not provably this "
		f"customer's payment."
	)


def _pending_state(batch, gate):
	"""Which in-flight state an incomplete payment is in, for the screen.

	The settlement decision says pending or not; it does not distinguish
	"seen in the mempool" from "mined, two confirmations short", and the
	terminal has always shown that difference because a customer standing at
	a counter can tell them apart. Derived from the observations rather than
	stored, so nothing can disagree with what was seen.
	"""
	if not batch.transfers:
		return None, ""
	best = max(batch.transfers, key=lambda transfer: transfer.confirmations)
	if best.confirmed:
		return "confirming", f"mined, {best.confirmations}/{gate} confs"
	return "detected", "seen in mempool, not yet mined"


def poll(sale_name):
	"""Advance one sale by one heartbeat. Safe to call on a finished sale."""
	sale = frappe.get_doc("Crypto Sale", sale_name)

	if sale.state in ("confirmed", "expired", "failed", "needs_review"):
		return sale.state

	rail = frappe.get_doc("Crypto Rail", sale.rail_key)
	extras = sale.extras()

	# The intent is rebuilt from what charge() wrote, never re-derived. A
	# fresh baseline would be a different chain position, and money that
	# arrived in between would silently change which side of the line it
	# fell on.
	try:
		adapter = catalog.plugin_for(rail)
		# The implementation this sale was charged under. A rail plugin can be
		# uninstalled and a different one installed under the same catalog key
		# -- the key names the money, not the code -- and the replacement would
		# otherwise reinterpret a baseline it did not capture and settle a
		# payment the original would have refused. Older sales carry no stamp
		# and are left alone; there is nothing to compare them against, and
		# refusing them would strand money over a field that did not exist.
		charged_under = extras.get("adapter")
		running = catalog.adapter_identity(adapter.key)
		if charged_under and charged_under != running:
			raise RuntimeError(
				f"this sale was charged under {charged_under} and this process "
				f"is running {running}. A different implementation of "
				f"{adapter.key} must not settle an intent it did not create.")
		intent = catalog.intent_from_record(extras.get("intent"))
	except Exception as exception:
		intent = None
		adapter_error = _why(exception)
	else:
		adapter_error = ""

	if intent is None:
		# A sale charged before this rail had an adapter, or by a version
		# that wrote no intent. It cannot be advanced, and saying so is
		# better than guessing: the money, if any, is real.
		sale.transition_to(
			"needs_review",
			source="poll",
			detail=f"no payment intent on this sale: {adapter_error or 'not recorded at charge time'}",
			end_kind="unverified",
			review_reason=(
				"This sale carries no payment intent, so the terminal cannot "
				"ask the chain about it. Any money at the address must be "
				"reconciled by hand."
			),
		)
		sale.save(ignore_permissions=True)
		return sale.state

	lock_expired = now_datetime() > get_datetime(sale.rate_lock_end)
	configuration = {"endpoint": extras.get("endpoint") or ""}
	source = adapter.key

	try:
		batch = adapter.observe(intent, configuration)
		while not batch.complete:
			# A bounded read answers through a tip it names, not necessarily
			# the chain tip. Settlement requires observations through the
			# provider tip, so the pages are walked until they meet it.
			batch = batch.extend(adapter.observe(intent, configuration, previous=batch))
		decision = adapter.settle(intent, batch, _claimed_transaction_ids(sale))
	except Exception as unreachable:
		# The look failed. If the lock still has time, this is just a missed
		# heartbeat and the sale stays where it is. If it does not, the sale
		# ends -- and it ends saying the terminal could not check, because
		# "expired unpaid" is a claim about the world that this heartbeat
		# did not earn.
		if lock_expired:
			sale.transition_to(
				"needs_review",
				source=source,
				detail=f"final look did not reach the chain: {_why(unreachable)}",
				end_kind="unverified",
				review_reason=(
					"The rate lock ran out and the last look never reached the "
					"chain, so the terminal cannot say whether this was paid."
				),
			)
		else:
			sale._append_event(sale.state, sale.state, source, f"unreachable: {_why(unreachable)}")
		sale.save(ignore_permissions=True)
		return sale.state

	# A real endpoint answered. Provenance may be set, and may only ever be
	# set to REAL here -- a simulated answer downgrades it elsewhere.
	if not sale.provenance:
		sale.provenance = "REAL"

	sale.sighted_native = str(decision.sighted_native)
	scratch = sale.scratch()
	scratch.update(
		{
			# Who answered, and when. Events record transitions; a heartbeat
			# that reached the chain and found nothing makes no transition,
			# and without this there would be nothing at all to distinguish
			# "looked, saw nothing" from "never looked" -- which are the two
			# answers this whole module exists to keep apart.
			"provider": batch.provider,
			"rail": batch.rail_key,
			"tip": batch.tip,
			"baseline_tip": batch.baseline_tip,
			"observed_through_tip": batch.observed_through_tip,
			"warnings": list(batch.warnings),
			"last_look": now_datetime().isoformat(),
		}
	)
	sale.set_scratch(scratch)

	gate = rail.gate_for(sale.mode)
	invoiced = int(sale.invoiced_native)

	if decision.state == SETTLED:
		# Every credited transaction is recorded, not only the first. See
		# `_claimed_transaction_ids` for why keeping just the headline one
		# leaves the rest looking unspent to the next sale.
		sale.tx_id = decision.transaction_id
		scratch["settled_tx_ids"] = list(decision.transaction_ids)
		sale.set_scratch(scratch)
		sale.credited_native = str(decision.credited_native)
		if sale.state in ("awaiting", "detected", "confirming"):
			sale.transition_to(
				"confirmed",
				source=source,
				detail=decision.reason,
				end_kind="over" if decision.credited_native > invoiced else "clean",
				settled_at=now_datetime(),
			)

	elif decision.state == NEEDS_REVIEW:
		sale.credited_native = str(decision.credited_native)
		sale.transition_to(
			"needs_review",
			source=source,
			detail=decision.reason,
			end_kind="unidentified",
			review_reason=(
				decision.reason
				or _unbindable_reason(rail, decision.sighted_native, gate)
			),
		)

	else:
		in_flight, detail = _pending_state(batch, gate)
		if in_flight and sale.state in ("awaiting", "detected") and in_flight != sale.state:
			sale.transition_to(in_flight, source=source, detail=detail)
		elif detail:
			sale._append_event(sale.state, sale.state, source, detail)

		if lock_expired and sale.state in ("awaiting", "detected", "confirming"):
			# Nothing settled, and time is up. Which ending depends entirely
			# on whether the terminal saw money it could not name.
			if decision.sighted_native > 0:
				sale.transition_to(
					"needs_review",
					source=source,
					detail=f"sighted {decision.sighted_native}, none bindable",
					end_kind="unidentified",
					review_reason=_unbindable_reason(
						rail, decision.sighted_native, gate
					),
				)
			else:
				sale.transition_to(
					"expired",
					source=source,
					detail="lock ran out with nothing seen",
					end_kind="clean",
				)

	sale.save(ignore_permissions=True)

	if sale.state == "confirmed":
		from cryptopos import settle

		settle.book(sale)

		# AFTER THE BOOKING, WHICH IS THE WHOLE RULE. The sale is income the
		# instant it settles; everything below this line is a separate act
		# that may fail completely without touching it. Enqueued rather than
		# called, so even a hung policy layer cannot reach the till.
		frappe.enqueue(
			"cryptopos.loyalty.award_for_settled_sale",
			queue="long",
			sale_name=sale.name,
			enqueue_after_commit=True,
		)

	return sale.state


def heartbeat():
	"""Scheduler entry point: poll every sale still in flight."""
	in_flight = frappe.get_all(
		"Crypto Sale",
		filters={"state": ("in", ["awaiting", "detected", "confirming"])},
		pluck="name",
	)
	for name in in_flight:
		try:
			poll(name)
		except Exception:
			# One unhealthy sale must not stop the others from being watched.
			frappe.log_error(
				title=f"cryptopos heartbeat failed for {name}",
				message=frappe.get_traceback(),
			)
		frappe.db.commit()
	return len(in_flight)
