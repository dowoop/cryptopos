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


def _extras(sale):
	"""The sale's charge-time snapshot, or an empty mapping.

	Total on purpose: a surface asking what to tell the payer must never fail
	because a record is older than the field it is asking for.
	"""
	try:
		extras = json.loads(sale.identity_extras or "{}")
	except (TypeError, ValueError):
		return {}
	return extras if isinstance(extras, dict) else {}


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
		# THE INSTRUCTION, and it was stored and never shown until 2026-08-31.
		# `charge` has written `payer_notice` into `identity_extras` all along,
		# and no surface returned it -- so on a payment-component rail the
		# customer was handed an address and never told that a payment must
		# NAME the sale. Found the expensive way: a real 5,000,000 uT payment
		# was made to the right component, for the right amount, naming the
		# wrong reference, and was correctly refused by the binding and
		# stranded. The binding worked; the surface had not said what to name.
		"payer_notice": _extras(sale).get("payer_notice", ""),
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
		# THE COVER QUEUE, so the awaiting screen can say what the house is
		# doing. Read with `get` because these are Custom Fields: a sale
		# created before `ensure_cover_fields` ran has neither, and a surface
		# must not fail because a record predates a field.
		"demo_cover_state": sale.get("demo_cover_state") or "",
		"demo_cover_note": sale.get("demo_cover_note") or "",
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


# ---------------------------------------------------------------------------
# The demo-cover queue. The same split as the award queue above, for payment.
#
# A visitor on the public instance has no wallet, so the house pays. It cannot
# pay from HERE: the Ootle key is on the host and this container has no signer.
# So the button records INTENT and a host-side payer -- outside this
# container, holding the key this container must not -- claims it, pays, and
# reports back.
#
# The cap is not here either. What the house can afford is a fact about a
# wallet the host can see and this container cannot, so `request_cover` never
# promises payment -- it records that payment was asked for.
# ---------------------------------------------------------------------------
COVERABLE_RAILS = ("xtr",)

# PAUSING THE QUEUE, and what it cost to learn this is needed.
#
# On 2026-09-02 the app harness charged a $25.00 test sale, called
# `request_cover` on it to exercise this queue, and the LIVE host-side payer
# -- which polls every eight seconds -- claimed it and **paid 500,000,000 uT
# of real testnet money**. The harness then deleted the sale, so the payment
# names a sale reference that no longer exists and `report_cover` raised
# `DoesNotExistError` into the payer's log.
#
# A test that can spend real money is not a test. The harness already pauses
# the booking sweep for exactly this reason; this is the same idea for the
# payer, which cannot be paused through `Scheduled Job Type` because it is not
# a Frappe job at all.
#
# IT EXPIRES BY ITSELF. A harness that dies mid-run must not leave the public
# instance unable to pay anybody, so the pause is a cache key with a TTL
# rather than a flag somebody has to remember to clear.
_COVER_PAUSE_KEY = "cryptopos_cover_queue_paused"
_COVER_PAUSE_SECONDS = 900


def pause_covers(seconds=_COVER_PAUSE_SECONDS):
	"""Stop handing covers to the payer. Expires on its own."""
	frappe.cache().set_value(_COVER_PAUSE_KEY, "1", expires_in_sec=int(seconds))


def resume_covers():
	frappe.cache().delete_value(_COVER_PAUSE_KEY)


def covers_paused():
	return bool(frappe.cache().get_value(_COVER_PAUSE_KEY))


@frappe.whitelist()
def request_cover(sale_name):
	"""Ask the house to pay this sale. Records intent; promises nothing."""
	frappe.only_for(["System Manager", "Sales User"])
	sale = frappe.get_doc("Crypto Sale", sale_name)

	if sale.rail_key not in COVERABLE_RAILS:
		frappe.throw(
			_("The house can only cover {0}. This sale is on {1}, whose payer is "
			  "the customer's own wallet.").format(
				_(", ").join(COVERABLE_RAILS), sale.rail_key),
			title=_("Not a coverable rail"),
		)
	if sale.state != "awaiting":
		# A settled sale must never be re-paid, and an expired one must not be
		# paid at all: the rail credits nothing after the deadline and the
		# money would be spent for no sale. D10 -- terminal states never reopen.
		frappe.throw(
			_("This sale is {0}, not awaiting payment.").format(sale.state),
			title=_("Nothing to cover"),
		)
	already = sale.get("demo_cover_state") or ""
	if already in ("requested", "paying"):
		return {"name": sale.name, "demo_cover_state": already, "queued": False}
	if already == "covered":
		return {"name": sale.name, "demo_cover_state": already, "queued": False}

	sale.db_set("demo_cover_state", "requested", update_modified=False)
	sale.db_set("demo_cover_note", "", update_modified=False)
	frappe.db.commit()
	return {"name": sale.name, "demo_cover_state": "requested", "queued": True}


@frappe.whitelist()
def claim_covers(limit=5, peek=0, ignore_pause=0):
	"""Hand the host the covers waiting to be paid.

	Marks each `paying` in the same transaction it is handed over, for the
	reason `claim_awards` gives: a second hand-out is a second payment, and
	nothing here can un-spend it. `peek` reads without claiming.
	"""
	frappe.only_for("System Manager")
	peek = int(peek or 0)
	if covers_paused() and not int(ignore_pause or 0):
		# Something is exercising this queue and must not have its test
		# requests paid with real money. See `pause_covers`.
		#
		# `ignore_pause` is for the thing that RAISED the pause -- the harness
		# testing this function -- and for nothing else. It exists so the
		# pause can stay up for the whole of a harness run: lifting it around
		# the call would leave a window, small but real, in which the live
		# payer claims a test request and spends money on it. The payer never
		# passes it.
		return []
	# EVERY REQUESTED COVER, NOT ONLY THE LIVE ONES.
	#
	# This filtered on `state == "awaiting"` as well, which made the re-read
	# below unreachable for the case it was written for: a sale that had
	# already ended was simply not selected, so it stayed `requested` forever
	# and the visitor's screen kept saying "paying from the demo wallet" about
	# a sale nothing would ever pay. Measured 2026-09-02 by expiring a sale
	# between the request and the claim.
	#
	# A guard that cannot be reached is not a guard. Selecting on the request
	# alone lets the loop below decide, which is where the decision belongs.
	names = frappe.get_all(
		"Crypto Sale",
		filters={"demo_cover_state": "requested"},
		order_by="creation asc",
		limit=int(limit),
		pluck="name",
	)
	claimed = []
	for name in names:
		sale = frappe.get_doc("Crypto Sale", name)
		# RE-READ THE STATE AT CLAIM TIME. The filter above ran before this
		# loop; a sale can settle or expire in between, and paying an expired
		# sale spends money the rail will credit to nothing.
		if sale.state != "awaiting":
			sale.db_set("demo_cover_state", "refused", update_modified=False)
			sale.db_set("demo_cover_note",
			            f"the sale was {sale.state} before the house could pay it",
			            update_modified=False)
			continue
		if not peek:
			sale.db_set("demo_cover_state", "paying", update_modified=False)
		extras = _extras(sale)
		intent = extras.get("intent") or {}
		claimed.append(
			{
				"name": sale.name,
				"rail_key": sale.rail_key,
				"component": extras.get("payment_component") or "",
				"reference": intent.get("payment_reference") or "",
				"amount": str(sale.invoiced_native),
				"expires_at_epoch": int(intent.get("expires_at_epoch") or 0),
			}
		)
	frappe.db.commit()
	return claimed


@frappe.whitelist()
def report_cover(sale_name, state, reason=""):
	"""Record what the house did, so a refusal is visible rather than silent."""
	frappe.only_for("System Manager")
	if state not in ("covered", "refused", "requested"):
		frappe.throw(_("{0} is not a reportable cover state.").format(state))
	if not frappe.db.exists("Crypto Sale", sale_name):
		# THE SALE CAN BE GONE BY NOW, and that must not raise into the
		# payer's loop. It happened: a harness deleted the sale it had asked
		# to have covered while the payment was in flight. The payment is the
		# thing that mattered and it already happened; there is simply nowhere
		# left to write the outcome.
		return {"name": sale_name, "demo_cover_state": state, "recorded": False}
	sale = frappe.get_doc("Crypto Sale", sale_name)
	# `covered` IS TERMINAL. THE HOUSE CANNOT UN-PAY A SALE.
	#
	# A claim can be rolled back underneath the payer -- `claim_covers` marks
	# `paying` in an ordinary transaction, and a deadlock with the watcher
	# writing the same row (both touch `tabCrypto Sale`) rolls that write back
	# and returns the sale to `requested`. The payer then claims it a second
	# time, and `agent_wallet.pay_sale`'s write-ahead record correctly refuses
	# to pay twice -- naming the transaction that already paid it.
	#
	# That refusal was then reported here and OVERWROTE `covered`. Measured on
	# CPS-2026-00772: the house paid 12,000,000 uT, the sale settled on that
	# very transaction and booked ACC-SINV-2026-00186, and the record ended
	# saying the house did not cover it, with the paying tx id quoted inside
	# the refusal. The money was right and the story was backwards.
	#
	# So a refusal never displaces a payment. This is D10's rule wearing
	# different clothes: a terminal outcome is not reopened by a later look.
	current = sale.get("demo_cover_state")
	if current == "covered" and state != "covered":
		return {
			"name": sale.name,
			"demo_cover_state": current,
			"recorded": False,
			"why": "the house already covered this sale; a later refusal does not undo a payment",
		}
	sale.db_set("demo_cover_state", state, update_modified=False)
	sale.db_set("demo_cover_note", (reason or "")[:1000], update_modified=False)
	frappe.db.commit()
	return {"name": sale.name, "demo_cover_state": state, "recorded": True}


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
	# ALWAYS, not only when readiness is asked for. `binding_label` needs the
	# mode to know where a rail receives, and passing None made every rail
	# report an empty binding -- a whole column of the till going blank because
	# a variable was computed conditionally for a different caller's benefit.
	mode = frappe.get_single("CryptoPoS Settings").mode
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
		row["binding"] = catalog.binding_label(rail, mode)
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
