"""Crypto Loyalty Award — the local mirror of what the network said.

The chain is the ledger. This record exists so that a receipt reprinted next
year says what the customer was handed at the counter, rather than what the
network happens to answer today. It is written once when the attempt finishes
and is not refreshed.

Five states, and the fourth is the one that matters:

  pending      queued, nothing submitted yet
  issued       the network committed the mint
  refused      the network refused; nothing was issued
  unverified   the attempt did not confirm in time. It MAY still have landed.
               This record does not claim it either way.
  not_offered  earning was not on offer for this sale at all

`unverified` is deliberate under-claiming. A customer told they hold nothing
who turns out to hold something is pleased; the reverse is a broken promise.
"""

import frappe
from frappe import _
from frappe.model.document import Document

ISSUED = "issued"
TERMINAL = ("issued", "refused", "unverified", "not_offered")


class CryptoLoyaltyAward(Document):
	def validate(self):
		# Anything that is not an issue must say why, in words. A refusal
		# with no reason is a refusal the cashier cannot explain to the
		# person standing in front of them.
		if self.state in ("refused", "unverified", "not_offered") and not self.reason:
			frappe.throw(
				_("An award that did not issue must carry a reason."),
				title=_("Refusal without a reason"),
			)

		# An issued award without a transaction id is a claim with nothing
		# behind it. The whole promise is that a customer can check it.
		if self.state == ISSUED and not self.tx_id:
			frappe.throw(
				_("An issued award must carry the transaction that issued it."),
				title=_("Claim without evidence"),
			)

	def claims_points(self):
		"""Only an issued award may be described to a customer as held."""
		return self.state == ISSUED

	def wording(self):
		"""What a receipt or screen may say about this award.

		Mirrors the tkinter terminal's HOLDS / WOULD split: the degraded
		path is the default, and only a committed mint upgrades it.
		"""
		rate = self.redemption_rate_at_award or 0
		if self.claims_points():
			return _("HOLDS {0:,} loyalty points ({1}/cent at the time of sale)").format(
				int(self.points or 0), rate
			)
		if self.state == "not_offered":
			return _("No points were offered on this sale.")
		return _(
			"WOULD have earned {0:,} loyalty points. NOT ISSUED. Nothing was "
			"minted and there is nothing here to claim, now or later."
		).format(int(self.points or 0))
