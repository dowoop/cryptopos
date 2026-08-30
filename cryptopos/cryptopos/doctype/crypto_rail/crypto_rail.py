"""Crypto Rail — one row per chain/asset the terminal can charge on."""

import frappe
from frappe import _
from frappe.model.document import Document

from cryptopos_core import hd

_BITCOIN_TESTNET_PUBLIC_VERSIONS = frozenset((0x043587CF, 0x045F1CF6))
_EVM_PUBLIC_VERSIONS = frozenset((0x0488B21E,))


# See `catalog.FRESH_RECIPIENT_FAMILIES` -- kept here as a literal rather than
# imported so that saving a rail never depends on the catalog importing cleanly.
_FRESH_RECIPIENT_FAMILIES = frozenset({"bitcoin"})

# Families for which this terminal can build an address from a derived key.
_PUBLIC_VERSIONS_BY_FAMILY = {
	"bitcoin": _BITCOIN_TESTNET_PUBLIC_VERSIONS,
	"evm-native": _EVM_PUBLIC_VERSIONS,
	"evm-erc20": _EVM_PUBLIC_VERSIONS,
}
_DERIVING_FAMILIES = frozenset(_PUBLIC_VERSIONS_BY_FAMILY)


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

		xpub = (self.testnet_xpub or "").strip()
		recipient = (self.testnet_recipient or "").strip()
		if recipient and (self.family or "") in _FRESH_RECIPIENT_FAMILIES:
			frappe.throw(
				_(
					"{0} requires a fresh receiving address for every payment, so a "
					"single address here would work until the first payment arrived "
					"and refuse every sale after that. Use Testnet Xpub instead."
				).format(self.label or self.name),
				title=_("This rail derives its own addresses"),
			)

		if xpub and (self.family or "") not in _DERIVING_FAMILIES:
			# Derivation is address-format specific. Without this guard, a new
			# family could inherit whichever builder happened to be written for
			# an existing one and offer a valid address for the wrong chain.
			frappe.throw(
				_(
					"{0} cannot derive its own addresses: this terminal only knows "
					"how to build them for {1} rails. Configure Testnet Recipient "
					"instead."
				).format(self.label or self.name, ", ".join(sorted(_DERIVING_FAMILIES))),
				title=_("No derivation for this rail"),
			)

		if xpub:
			# Two rails sharing one account key both start at index zero and
			# hand out the SAME address -- which is D5's collision again,
			# across assets this time, and it reproduces. One key per rail.
			twin = frappe.db.get_value(
				"Crypto Rail", {"testnet_xpub": xpub, "name": ("!=", self.name)}, "name"
			)
			if twin:
				frappe.throw(
					_(
						"Rail {0} already uses this extended public key. Two rails "
						"sharing one key derive the same address from the same index, "
						"so two customers would be shown one address. Give each rail "
						"its own account key."
					).format(twin),
					title=_("That key is already in use"),
				)
			try:
				key = hd.parse_extended_key(xpub)
			except hd.InvalidExtendedKey as exception:
				# Preserve the core's precise refusal: checksum, length, private
				# material, and invalid points each need a different remedy.
				frappe.throw(str(exception), title=_("Testnet extended key refused"))
			allowed_versions = _PUBLIC_VERSIONS_BY_FAMILY[self.family or ""]
			if key.version not in allowed_versions:
				if (self.family or "") == "bitcoin":
					reason = (
						"Bitcoin Testnet Xpub requires tpub/vpub version bytes. "
						"Mainnet xpub/zpub version bytes are refused."
					)
				else:
					reason = (
						"EVM Testnet Xpub requires conventional xpub version bytes. "
						"Bitcoin tpub, zpub, and vpub version bytes are refused."
					)
				frappe.throw(
					_(reason),
					title=_("Wrong-family key refused"),
				)
			if key.depth != 3:
				frappe.throw(
					_(
						"Testnet Xpub must be an account-level key at depth 3; "
						"the terminal derives only the external chain 0/i beneath it."
					),
					title=_("Account key required"),
				)
		if xpub and recipient:
			frappe.throw(
				_(
					"Testnet Xpub and Testnet Recipient are different payment bindings; "
					"configure one or the other, not both."
				),
				title=_("Two receiving bindings"),
			)
		if int(self.next_address_index or 0) < 0:
			frappe.throw(_("Next Address Index must be zero or more."))

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
