"""Crypto Sale — the state machine's memory, and the guard on its transitions.

The terminal has ONE state machine that never forks per rail:

    idle -> awaiting -> detected -> confirming -> confirmed (shown as SETTLED)
               |         (mempool)   (the gate)      +-> failed
               +-> expired (clean / part-paid)   \\-> needs_review

Four of those eight are endings, and the fourth ending is the point. Most
point-of-sale software collapses uncertainty into success or failure because
uncertainty is bad UX. `needs_review` is how this terminal declines to.

This class is deliberately not submittable. ERPNext's docstatus has three
values -- draft, submitted, cancelled -- and mapping eight states onto three
would delete exactly the distinction the terminal exists to make. The sale
owns its own state; an ERPNext Sales Invoice is EMITTED from it once the
sale is settled, bound and real, and never before.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


HAPPY_PATH = ("idle", "awaiting", "detected", "confirming", "confirmed")
DEAD_ENDS = ("expired", "failed", "needs_review")
TERMINAL = ("confirmed",) + DEAD_ENDS

# What the operator sees. Only two states are renamed, and both because the
# internal word is wrong at the till: "confirmed" is an engineering claim
# about the chain, "SETTLED" is what the merchant needs to know.
STATE_WORDS = {"confirmed": "SETTLED", "needs_review": "NEEDS REVIEW"}

# A transition not on this map does not happen. Written as a whitelist rather
# than a blacklist so that a new state added later is refused by default
# instead of silently becoming reachable from everywhere.
LEGAL = {
	"idle": {"awaiting", "failed"},
	# A payment often mines between two heartbeats, so awaiting -> confirming
	# with no mempool stop is the chain being fast, not a skipped step.
	#
	# awaiting -> confirmed direct is the same argument taken one step
	# further: if the first look finds a transaction already past the gate,
	# that is what was observed. Routing it through a synthetic `confirming`
	# would manufacture an observation the terminal never made, which is the
	# overclaim rule pointed the other way. The audit row records the confs
	# it actually saw, so the jump is legible rather than silent.
	"awaiting": {"detected", "confirming", "confirmed", "expired", "failed", "needs_review"},
	"detected": {"confirming", "confirmed", "expired", "failed", "needs_review"},
	"confirming": {"confirmed", "expired", "failed", "needs_review"},
	"confirmed": set(),
	"expired": set(),
	"failed": set(),
	"needs_review": set(),
}


class IllegalTransition(frappe.ValidationError):
	pass


class CryptoSale(Document):
	def validate(self):
		self.state_word = STATE_WORDS.get(self.state, (self.state or "").upper())

		# An ending must say which ending it was. A terminal state with no
		# end_kind is a sale that stopped without saying why, which is the
		# shape of a bug rather than an outcome.
		if self.state in DEAD_ENDS and not self.end_kind:
			frappe.throw(
				_("A sale ending in {0} must carry an end_kind.").format(self.state),
				title=_("Ending without a reason"),
			)

		if self.state == "needs_review" and not self.review_reason:
			frappe.throw(
				_("A sale parked as NEEDS REVIEW must carry a review_reason."),
				title=_("Parked without a reason"),
			)

	# ------------------------------------------------------------------
	# The only sanctioned way to move.
	# ------------------------------------------------------------------
	def transition_to(self, new_state, source, detail="", **fields):
		"""Move to `new_state`, recording who said so and why.

		`source` names the transport or actor that caused it -- "esplora-rest",
		"expiry-sweep", "operator". It is not decoration: a state this terminal
		cannot attribute is a state it cannot defend, and the audit table is
		where a claim on screen gets traced back to the call that justified it.
		"""
		old = self.state or "idle"

		if new_state == old:
			# Not an error -- a heartbeat that finds nothing new is the normal
			# case. Record the detail if there is one, move nothing.
			if detail:
				self._append_event(old, old, source, detail)
			return False

		if new_state not in LEGAL.get(old, set()):
			raise IllegalTransition(
				_("{0} -> {1} is not a legal transition (sale {2}).").format(
					old, new_state, self.name
				)
			)

		for key, value in fields.items():
			self.set(key, value)

		self.state = new_state
		self._append_event(old, new_state, source, detail)
		return True

	def _append_event(self, from_state, to_state, source, detail):
		self.append(
			"events",
			{
				"at": now_datetime(),
				"from_state": from_state,
				"to_state": to_state,
				"source": source,
				"detail": (detail or "")[:500],
			},
		)

	# ------------------------------------------------------------------
	# Booking. The narrowest gate in the app.
	# ------------------------------------------------------------------
	def may_book(self):
		"""Return (ok, reason). The booking equation is all four terms.

		mode AND provenance AND state AND identity_source -- a sale can be
		mainnet and REAL and SETTLED while watching a placeholder nobody
		holds the keys to, so the destination is a term too.
		"""
		if self.state != "confirmed":
			return False, _("not settled (state is {0})").format(self.state)
		if self.provenance != "REAL":
			return False, _("provenance is {0}, not REAL").format(self.provenance or "unset")
		if self.mode == "demo":
			return False, _("demo mode books nothing")
		if int(self.credited_native or 0) <= 0:
			# Sighted-but-unbound money is real and is not provably this
			# sale's. It displays; it never books.
			return False, _("no bound money to book")
		if self.identity_source in (None, "", "none"):
			return False, _("nobody is known to hold the receiving address")
		return True, ""

	def native_shortfall(self):
		"""How far the bound money falls short. Negative means overpaid."""
		return int(self.invoiced_native or 0) - int(self.credited_native or 0)

	def extras(self):
		try:
			return json.loads(self.identity_extras or "{}")
		except ValueError:
			return {}

	def scratch(self):
		try:
			return json.loads(self.watch_scratch or "{}")
		except ValueError:
			return {}

	def set_scratch(self, data):
		self.watch_scratch = json.dumps(data, indent=1, sort_keys=True)
