"""Ootle Esmeralda observation and loyalty reads, kept as separate powers.

The payment rail can observe a public XTR vault balance. It cannot attribute a
shared-account balance increase to a transaction because the current indexer
does not enumerate transactions by vault or account, so it reports the amount
as sighted and never settles it. Loyalty policy and points reads remain the
independent :class:`cryptopos_core.chain.OotleReader` API.
"""

import re
from collections.abc import Mapping

from .chain import OotleReader
from .errors import InvalidRailPlugin, RailProviderError, UnsupportedCapability
from .plugin import (
	ADDRESS_VALIDATION,
	OBSERVATION,
	PAYMENT_REQUEST,
	SETTLEMENT,
	Asset,
	Network,
	ObservationBatch,
	PaymentIntent,
	Readiness,
	RecipientBaseline,
)

OOTLE_XTR_RESOURCE = "resource_" + "01" * 32
_ACCOUNT = re.compile(r"^(?:account|component)_[0-9a-f]{32,64}$")


class OotleEsmeralda:
	"""Read-only XTR balance observation with no false attribution claim."""

	network = Network("ootle", "esmeralda", True)
	asset = Asset("native", "xtr", "EsmeraldaXTR", 6)
	key = f"{network.key}/{asset.key}"
	capabilities = frozenset({ADDRESS_VALIDATION, OBSERVATION})

	def validate_recipient(self, recipient):
		if not isinstance(recipient, str) or not _ACCOUNT.fullmatch(recipient):
			return "refused", "supported Ootle accounts are account_ or component_ plus 32-64 lowercase hex"
		return "unchecked", "the account shape is valid but Ootle account identifiers carry no local checksum"

	def readiness(self, configuration):
		ready = {ADDRESS_VALIDATION}
		unavailable = [
			(PAYMENT_REQUEST, "no standardized Ootle payer URI exists"),
			(SETTLEMENT, "the indexer cannot bind a shared-account balance change to a transaction"),
		]
		try:
			reader = self._reader(configuration)
			self._network(reader)
		except RailProviderError as exception:
			unavailable.append((OBSERVATION, exception.reason))
		else:
			ready.add(OBSERVATION)
		return Readiness(self.key, frozenset(ready), tuple(unavailable))

	def capture_baseline(self, recipient, configuration):
		verdict, reason = self.validate_recipient(recipient)
		if verdict == "refused":
			raise RailProviderError(self.key, reason)
		reader = self._reader(configuration)
		epoch = self._network(reader)
		balance, balance_reason = reader.resource_balance(recipient, OOTLE_XTR_RESOURCE)
		if balance is None:
			raise RailProviderError(self.key, f"recipient balance could not be read: {balance_reason}")
		return RecipientBaseline(self.key, recipient, reader.indexer, epoch, balance_native=balance)

	def create_request(self, intent):
		raise UnsupportedCapability(self.key, PAYMENT_REQUEST)

	def observe(self, intent, configuration, previous=None):
		self._intent(intent)
		if intent.baseline is None or intent.baseline.balance_native is None:
			raise InvalidRailPlugin("Ootle observation requires a captured balance baseline")
		if previous is not None:
			previous.require_intent(intent)
		reader = self._reader(configuration)
		if intent.baseline.provider != reader.indexer:
			raise RailProviderError(self.key, "observation endpoint differs from the baseline endpoint")
		epoch = self._network(reader)
		if intent.baseline.tip is not None and epoch < intent.baseline.tip:
			raise RailProviderError(self.key, "indexer epoch is behind the captured baseline")
		balance, reason = reader.resource_balance(intent.recipient, OOTLE_XTR_RESOURCE)
		if balance is None:
			raise RailProviderError(self.key, f"recipient balance could not be read: {reason}")
		delta = balance - intent.baseline.balance_native
		warnings = ()
		unattributed = max(delta, 0)
		if delta > 0:
			warnings = (
				"balance increased, but the indexer cannot identify which transaction or sale caused it",
			)
		elif delta < 0:
			warnings = ("recipient balance fell after the baseline; this is not a payment",)
		return ObservationBatch(
			self.key,
			intent.intent_id,
			intent.recipient,
			reader.indexer,
			intent.baseline.tip,
			epoch,
			intent.baseline.tip,
			epoch,
			(),
			unattributed,
			warnings,
		)

	def settle(self, intent, observations, claimed_transaction_ids=frozenset()):
		raise UnsupportedCapability(self.key, SETTLEMENT)

	def _reader(self, configuration):
		if not isinstance(configuration, Mapping):
			raise RailProviderError(self.key, "configuration must be a mapping")
		indexer = configuration.get("endpoint")
		if not isinstance(indexer, str) or not indexer:
			raise RailProviderError(self.key, "an explicit Ootle indexer endpoint is required")
		timeout = configuration.get("timeout_seconds", 4.0)
		if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
			raise RailProviderError(self.key, "timeout_seconds must be greater than 0 and at most 30")
		return OotleReader(indexer=indexer, timeout=timeout)

	def _network(self, reader):
		body, reason = reader._get("network")
		if body is None:
			raise RailProviderError(self.key, reason)
		if not isinstance(body, dict) or body.get("network") != "esmeralda":
			raise RailProviderError(self.key, "indexer did not identify itself as esmeralda")
		epoch = body.get("epoch")
		if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
			raise RailProviderError(self.key, "indexer epoch was not a non-negative integer")
		return epoch

	def _intent(self, intent):
		if not isinstance(intent, PaymentIntent) or intent.rail_key != self.key:
			raise InvalidRailPlugin("payment intent belongs to another rail")


ootle_esmeralda = OotleEsmeralda()
