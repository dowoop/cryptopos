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
import urllib.error
import urllib.request

import frappe
from frappe.utils import get_datetime, now_datetime

HTTP_TIMEOUT_SECONDS = 10

# A heartbeat walks at most this many transactions on an address. A busy
# shared address is a performance question, not a correctness one -- but an
# unbounded walk on a merchant address with years of history would hang the
# worker, so the cap is stated rather than assumed.
MAX_TXS_SCANNED = 50


class ChainUnreachable(Exception):
	"""The question could not be asked. Not the same as a negative answer."""


def _get_json(url):
	try:
		request = urllib.request.Request(url, headers={"User-Agent": "cryptopos/0.0.1"})
		with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
			return json.loads(response.read().decode("utf-8"))
	except (urllib.error.URLError, OSError, ValueError) as exception:
		raise ChainUnreachable(str(exception)) from exception


def _get_text(url):
	try:
		request = urllib.request.Request(url, headers={"User-Agent": "cryptopos/0.0.1"})
		with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
			return response.read().decode("utf-8").strip()
	except (urllib.error.URLError, OSError) as exception:
		raise ChainUnreachable(str(exception)) from exception


def _claimed_elsewhere(tx_id, sale_name):
	"""Has another sale already bound this transaction?

	On a shared receiving address two sales for the same amount inside the
	same window are indistinguishable by amount alone. First binder wins and
	the second must not book the same coins twice -- it parks instead.
	"""
	if not tx_id:
		return False
	other = frappe.db.exists(
		"Crypto Sale", {"tx_id": tx_id, "name": ("!=", sale_name)}
	)
	return bool(other)


def _credit_to(tx, address):
	"""Sum the outputs of `tx` that pay `address`, in satoshis."""
	total = 0
	for output in tx.get("vout", []):
		if output.get("scriptpubkey_address") == address:
			total += int(output.get("value", 0))
	return total


def watch_bitcoin_esplora(sale, rail):
	"""One look at the chain for one Bitcoin-family sale.

	Returns a dict of observations. Raises ChainUnreachable if the endpoint
	did not answer -- the caller decides what an unanswered look means, and
	that decision depends on whether the lock has run out.
	"""
	endpoint = sale.extras().get("endpoint")
	if not endpoint:
		raise ChainUnreachable("no endpoint configured for this rail and mode")

	address = sale.identity_address
	tip = int(_get_text(f"{endpoint}/blocks/tip/height"))
	txs = _get_json(f"{endpoint}/address/{address}/txs")[:MAX_TXS_SCANNED]

	charged_at = get_datetime(sale.charged_at).timestamp()
	invoiced = int(sale.invoiced_native)

	best = None      # a bindable candidate
	sighted = 0      # money seen that we could not bind

	for tx in txs:
		credit = _credit_to(tx, address)
		if credit <= 0:
			continue

		status = tx.get("status", {})
		confirmed = bool(status.get("confirmed"))
		block_time = status.get("block_time")

		# A transaction that predates the charge cannot be payment for it.
		# Mempool transactions carry no block_time; they are candidates
		# because they cannot be older than the charge if we are seeing
		# them now for the first time.
		if confirmed and block_time and block_time < charged_at:
			continue

		tx_id = tx.get("txid", "")

		if _claimed_elsewhere(tx_id, sale.name):
			# Real money, provably not this sale's. It is not sighted
			# either -- another sale has named it.
			continue

		# Binding on a shared address is by amount inside the lock window.
		# That is the weakest binding this terminal offers and the sale
		# records it as such.
		if credit < invoiced:
			sighted += credit
			continue

		height = status.get("block_height")
		confs = (tip - int(height) + 1) if (confirmed and height is not None) else 0
		candidate = {
			"tx_id": tx_id,
			"credit": credit,
			"confirmed": confirmed,
			"confs": confs,
			"block_time": block_time,
		}
		if best is None or confs > best["confs"]:
			best = candidate

	return {"tip": tip, "best": best, "sighted": sighted, "source": "esplora-rest"}


WATCHERS = {"bitcoin": watch_bitcoin_esplora}


def poll(sale_name):
	"""Advance one sale by one heartbeat. Safe to call on a finished sale."""
	sale = frappe.get_doc("Crypto Sale", sale_name)

	if sale.state in ("confirmed", "expired", "failed", "needs_review"):
		return sale.state

	rail = frappe.get_doc("Crypto Rail", sale.rail_key)
	watcher = WATCHERS.get(rail.family)
	if watcher is None:
		sale.transition_to(
			"failed",
			source="poll",
			detail=f"no watcher implements family {rail.family}",
			end_kind="unverified",
		)
		sale.save(ignore_permissions=True)
		return sale.state

	lock_expired = now_datetime() > get_datetime(sale.rate_lock_end)

	try:
		observation = watcher(sale, rail)
	except ChainUnreachable as unreachable:
		# The look failed. If the lock still has time, this is just a missed
		# heartbeat and the sale stays where it is. If it does not, the sale
		# ends -- and it ends saying the terminal could not check, because
		# "expired unpaid" is a claim about the world that this heartbeat
		# did not earn.
		if lock_expired:
			sale.transition_to(
				"needs_review",
				source="esplora-rest",
				detail=f"final look did not reach the chain: {unreachable}",
				end_kind="unverified",
				review_reason=(
					"The rate lock ran out and the last look never reached the "
					"chain, so the terminal cannot say whether this was paid."
				),
			)
		else:
			sale._append_event(sale.state, sale.state, "esplora-rest", f"unreachable: {unreachable}")
		sale.save(ignore_permissions=True)
		return sale.state

	# A real endpoint answered. Provenance may be set, and may only ever be
	# set to REAL here -- a simulated answer downgrades it elsewhere.
	if not sale.provenance:
		sale.provenance = "REAL"

	best = observation["best"]
	sale.sighted_native = str(observation["sighted"])
	scratch = sale.scratch()
	scratch.update({"tip": observation["tip"], "last_look": now_datetime().isoformat()})
	sale.set_scratch(scratch)

	gate = rail.gate_for(sale.mode)

	if best:
		sale.tx_id = best["tx_id"]
		sale.credited_native = str(best["credit"])

		if best["confirmed"] and best["confs"] >= gate:
			if sale.state in ("awaiting", "detected", "confirming"):
				sale.transition_to(
					"confirmed",
					source=observation["source"],
					detail=f"{best['confs']} confs >= gate {gate}",
					end_kind="over" if best["credit"] > int(sale.invoiced_native) else "clean",
					settled_at=now_datetime(),
				)
		elif best["confirmed"]:
			if sale.state in ("awaiting", "detected"):
				sale.transition_to(
					"confirming",
					source=observation["source"],
					detail=f"mined, {best['confs']}/{gate} confs",
				)
			else:
				sale._append_event(
					sale.state, sale.state, observation["source"], f"{best['confs']}/{gate} confs"
				)
		else:
			if sale.state == "awaiting":
				sale.transition_to(
					"detected",
					source=observation["source"],
					detail="seen in mempool, not yet mined",
				)

	elif lock_expired:
		# Nothing bindable, and time is up. Which ending depends entirely on
		# whether the terminal saw money it could not name.
		if observation["sighted"] > 0:
			sale.transition_to(
				"needs_review",
				source=observation["source"],
				detail=f"sighted {observation['sighted']} sats, none bindable",
				end_kind="unidentified",
				review_reason=(
					f"{observation['sighted']} satoshi arrived at this address inside "
					"the window but could not be tied to this sale. It is real money "
					"and it is not provably this customer's payment."
				),
			)
		else:
			sale.transition_to(
				"expired",
				source=observation["source"],
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
