"""Crypto Rail — one row per chain/asset the terminal can charge on."""

import frappe
from frappe import _
from frappe.model.document import Document


class CryptoRail(Document):
	def validate(self):
		if self.native_decimals is None or self.native_decimals < 0:
			frappe.throw(_("Native decimals must be zero or more."))

		# The gate is a ceiling, and a ceiling ships on the surface that
		# offers the feature. A rail that settles without saying at what
		# depth is a rail that oversells.
		if self.enabled and not self.gate_text:
			frappe.throw(
				_("An enabled rail must state its settle gate in words."),
				title=_("Ceiling missing"),
			)

	def gate_for(self, mode):
		"""Confirmations required before this rail calls a payment settled."""
		if mode == "testnet" and self.testnet_gate_confs:
			return int(self.testnet_gate_confs)
		return int(self.gate_confs or 0)

	def endpoint_for(self, mode):
		"""The URL that answers for this mode, or None.

		None is a real answer: it means no free public endpoint exists, the
		simulator will answer instead, and the sale's provenance will say so.
		"""
		if mode == "testnet":
			return self.testnet_url or None
		if mode == "mainnet":
			return self.live_url or None
		return None
