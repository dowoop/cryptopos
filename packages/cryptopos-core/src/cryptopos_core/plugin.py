"""The framework-neutral contract implemented by installable payment rails.

A rail is concrete: one transfer mechanism, on one named network, for one
asset. ``ethereum:sepolia/usdc:<contract>`` and
``ethereum:mainnet/usdc:<contract>`` are different rails. There is no generic
``testnet`` mode for a plugin to reinterpret and no ``demo`` network; a demo is
a provider that returns simulated observations for an otherwise concrete rail.

The host owns persistence, scheduling and user interfaces. A plugin performs
one bounded operation at a time and returns immutable facts. That makes the
same plugin usable from a framework scheduler, an async worker, a CLI, or a
test.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from .errors import InvalidAmount, InvalidRailPlugin, _coerce_integer

ADDRESS_VALIDATION = "address-validation"
PAYMENT_REQUEST = "payment-request"
OBSERVATION = "observation"
SETTLEMENT = "settlement"

KNOWN_CAPABILITIES = frozenset({ADDRESS_VALIDATION, PAYMENT_REQUEST, OBSERVATION, SETTLEMENT})
CHARGE_CAPABILITIES = frozenset({ADDRESS_VALIDATION, PAYMENT_REQUEST, OBSERVATION, SETTLEMENT})

PENDING = "pending"
SETTLED = "settled"
NEEDS_REVIEW = "needs-review"
SETTLEMENT_STATES = frozenset({PENDING, SETTLED, NEEDS_REVIEW})

# Whether the rail itself binds money to one sale before a host chooses any
# receiving-address strategy. Hosts may strengthen NOT_UNCONDITIONAL rails by
# deriving a fresh address per sale; they must not infer this declaration from
# that deployment choice because references, subaddresses, and payment ids bind
# without an xpub field.
UNCONDITIONAL_PER_SALE = "unconditional-per-sale"
NOT_UNCONDITIONAL = "not-unconditional"
BINDING_CATEGORIES = frozenset({UNCONDITIONAL_PER_SALE, NOT_UNCONDITIONAL})

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SYMBOL = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,31}$")


def _identifier(field, value):
	if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
		raise InvalidRailPlugin(
			f"{field} must be 1-128 lowercase ASCII letters, digits, dots, underscores, or hyphens"
		)
	return value


def _reference(field, value):
	if not isinstance(value, str) or not _REFERENCE.fullmatch(value):
		raise InvalidRailPlugin(f"{field} must be a 1-128 character ASCII identifier without URI punctuation")
	return value


@dataclass(frozen=True)
class Network:
	"""A concrete network, never an ambiguous mainnet/testnet mode."""

	namespace: str
	reference: str
	is_testnet: bool

	def __post_init__(self):
		_identifier("network namespace", self.namespace)
		_identifier("network reference", self.reference)
		if not isinstance(self.is_testnet, bool):
			raise InvalidRailPlugin("network is_testnet must be a boolean")

	@property
	def key(self):
		return f"{self.namespace}:{self.reference}"


@dataclass(frozen=True)
class Asset:
	"""The asset transferred by a rail, qualified by its on-network identity."""

	namespace: str
	reference: str
	symbol: str
	decimals: int

	def __post_init__(self):
		_identifier("asset namespace", self.namespace)
		_reference("asset reference", self.reference)
		if not isinstance(self.symbol, str) or not _SYMBOL.fullmatch(self.symbol):
			raise InvalidRailPlugin("asset symbol must be a 1-32 character ASCII identifier")
		decimals = _coerce_integer(self.decimals)
		if decimals is None or decimals < 0 or decimals > 30:
			raise InvalidRailPlugin("asset decimals must be an integer from 0 through 30")
		object.__setattr__(self, "decimals", decimals)

	@property
	def key(self):
		return f"{self.namespace}:{self.reference}"


@dataclass(frozen=True)
class RecipientBaseline:
	"""Provider facts captured before a payment instruction is exposed."""

	rail_key: str
	recipient: str
	provider: str
	tip: Optional[int]
	transaction_ids: tuple[str, ...] = ()
	balance_native: Optional[int] = None

	def __post_init__(self):
		if not isinstance(self.rail_key, str) or not self.rail_key:
			raise InvalidRailPlugin("recipient baseline rail key must be non-empty text")
		if not isinstance(self.recipient, str) or not self.recipient:
			raise InvalidRailPlugin("recipient baseline recipient must be non-empty text")
		if not isinstance(self.provider, str) or not self.provider:
			raise InvalidRailPlugin("recipient baseline provider must be non-empty text")
		if self.tip is not None:
			tip = _coerce_integer(self.tip)
			if tip is None or tip < 0:
				raise InvalidRailPlugin("recipient baseline tip must be a non-negative integer")
			object.__setattr__(self, "tip", tip)
		if not isinstance(self.transaction_ids, tuple) or any(
			not isinstance(transaction_id, str) or not transaction_id
			for transaction_id in self.transaction_ids
		):
			raise InvalidRailPlugin("recipient baseline transaction ids must be non-empty text")
		if len(set(self.transaction_ids)) != len(self.transaction_ids):
			raise InvalidRailPlugin("recipient baseline transaction ids must be unique")
		if self.balance_native is not None:
			balance = _coerce_integer(self.balance_native)
			if balance is None or balance < 0:
				raise InvalidRailPlugin("recipient baseline balance must be a non-negative integer")
			object.__setattr__(self, "balance_native", balance)


@dataclass(frozen=True)
class PaymentIntent:
	"""The complete immutable input a rail needs to request and observe payment."""

	intent_id: str
	rail_key: str
	recipient: str
	amount_native: int
	created_at_epoch: int
	expires_at_epoch: int
	payment_reference: str = ""
	baseline: Optional[RecipientBaseline] = None

	def __post_init__(self):
		if not isinstance(self.intent_id, str) or not self.intent_id:
			raise InvalidRailPlugin("payment intent id must be non-empty text")
		if not isinstance(self.rail_key, str) or not self.rail_key:
			raise InvalidRailPlugin("payment intent rail key must be non-empty text")
		if not isinstance(self.recipient, str) or not self.recipient:
			raise InvalidRailPlugin("payment intent recipient must be non-empty text")
		amount = _coerce_integer(self.amount_native)
		if amount is None or amount <= 0:
			raise InvalidAmount("amount_native", self.amount_native)
		created = _coerce_integer(self.created_at_epoch)
		expires = _coerce_integer(self.expires_at_epoch)
		if created is None or created < 0:
			raise InvalidAmount("created_at_epoch", self.created_at_epoch, minimum=0)
		if expires is None or expires <= created:
			raise InvalidRailPlugin("payment intent expiry must be after its creation time")
		if not isinstance(self.payment_reference, str):
			raise InvalidRailPlugin("payment reference must be text")
		if self.baseline is not None:
			if not isinstance(self.baseline, RecipientBaseline):
				raise InvalidRailPlugin("payment intent baseline has an unknown shape")
			if self.baseline.rail_key != self.rail_key:
				raise InvalidRailPlugin("payment intent baseline belongs to another rail")
			if self.baseline.recipient != self.recipient:
				raise InvalidRailPlugin("payment intent baseline belongs to another recipient")
		object.__setattr__(self, "amount_native", amount)
		object.__setattr__(self, "created_at_epoch", created)
		object.__setattr__(self, "expires_at_epoch", expires)


@dataclass(frozen=True)
class PaymentRequest:
	"""The exact instruction presented to a payer."""

	rail_key: str
	uri: str
	recipient: str
	amount_native: int
	payer_notice: str = ""

	def __post_init__(self):
		if not all(isinstance(value, str) and value for value in (self.rail_key, self.uri, self.recipient)):
			raise InvalidRailPlugin("payment request identity and URI must be non-empty text")
		if (
			len(self.uri) > 4096
			or not self.uri.isascii()
			or any(character.isspace() for character in self.uri)
		):
			raise InvalidRailPlugin("payment request URI must be bounded ASCII text without whitespace")
		if not isinstance(self.payer_notice, str) or len(self.payer_notice) > 512:
			raise InvalidRailPlugin("payment request payer notice must be bounded text")
		amount = _coerce_integer(self.amount_native)
		if amount is None or amount <= 0:
			raise InvalidAmount("amount_native", self.amount_native)
		object.__setattr__(self, "amount_native", amount)


@dataclass(frozen=True)
class TransferObservation:
	"""One transfer fact reported by a configured rail provider."""

	transaction_id: str
	amount_native: int
	confirmed: bool
	confirmations: int = 0
	block_height: Optional[int] = None
	block_time_epoch: Optional[int] = None

	def __post_init__(self):
		if (
			not isinstance(self.transaction_id, str)
			or not self.transaction_id
			or len(self.transaction_id) > 256
			or any(character.isspace() for character in self.transaction_id)
		):
			raise InvalidRailPlugin("observed transaction id must be non-empty text")
		amount = _coerce_integer(self.amount_native)
		confirmations = _coerce_integer(self.confirmations)
		if amount is None or amount <= 0:
			raise InvalidAmount("amount_native", self.amount_native)
		if not isinstance(self.confirmed, bool):
			raise InvalidRailPlugin("observed confirmed flag must be a boolean")
		if confirmations is None or confirmations < 0:
			raise InvalidRailPlugin("observed confirmations must be a non-negative integer")
		if self.confirmed != (confirmations > 0):
			raise InvalidRailPlugin("observed confirmation flag and count disagree")
		for field in ("block_height", "block_time_epoch"):
			value = getattr(self, field)
			if value is None:
				continue
			normalized = _coerce_integer(value)
			if normalized is None or normalized < 0 or not self.confirmed:
				raise InvalidRailPlugin(f"observed {field} requires a confirmed non-negative integer")
			object.__setattr__(self, field, normalized)
		object.__setattr__(self, "amount_native", amount)
		object.__setattr__(self, "confirmations", confirmations)


@dataclass(frozen=True)
class ObservationBatch:
	"""One bounded provider read, including the chain position it was judged at."""

	rail_key: str
	intent_id: str
	recipient: str
	provider: str
	baseline_tip: Optional[int]
	tip: Optional[int]
	observed_after_tip: Optional[int]
	observed_through_tip: Optional[int]
	transfers: tuple[TransferObservation, ...]
	unattributed_native: int = 0
	warnings: tuple[str, ...] = ()
	finalized_tip: Optional[int] = None

	def __post_init__(self):
		if not isinstance(self.rail_key, str) or not self.rail_key:
			raise InvalidRailPlugin("observation rail key must be non-empty text")
		if not isinstance(self.intent_id, str) or not self.intent_id:
			raise InvalidRailPlugin("observation intent id must be non-empty text")
		if not isinstance(self.recipient, str) or not self.recipient:
			raise InvalidRailPlugin("observation recipient must be non-empty text")
		if not isinstance(self.provider, str) or not self.provider:
			raise InvalidRailPlugin("observation provider must be non-empty text")
		for field in ("baseline_tip", "tip", "observed_after_tip", "observed_through_tip"):
			value = getattr(self, field)
			if value is None:
				continue
			normalized = _coerce_integer(value)
			if normalized is None or normalized < 0:
				raise InvalidRailPlugin(f"observation {field} must be a non-negative integer")
			object.__setattr__(self, field, normalized)
		if any(
			value is None
			for value in (self.baseline_tip, self.tip, self.observed_after_tip, self.observed_through_tip)
		):
			raise InvalidRailPlugin("observation chain positions must all be present")
		if self.observed_after_tip < self.baseline_tip:
			raise InvalidRailPlugin("observation cannot start before its baseline")
		if not self.observed_after_tip <= self.observed_through_tip <= self.tip:
			raise InvalidRailPlugin("observation range must end between its start and provider tip")
		if not isinstance(self.transfers, tuple) or any(
			not isinstance(transfer, TransferObservation) for transfer in self.transfers
		):
			raise InvalidRailPlugin("observations must be a tuple of TransferObservation values")
		transaction_ids = [transfer.transaction_id for transfer in self.transfers]
		if len(set(transaction_ids)) != len(transaction_ids):
			raise InvalidRailPlugin("an observation batch must aggregate duplicate transaction ids")
		if any(
			transfer.block_height is not None
			and not self.observed_after_tip < transfer.block_height <= self.observed_through_tip
			for transfer in self.transfers
		):
			raise InvalidRailPlugin("an observed transfer block must be inside the observed range")
		unattributed = _coerce_integer(self.unattributed_native)
		if unattributed is None or unattributed < 0:
			raise InvalidRailPlugin("unattributed observation amount must be non-negative")
		if not isinstance(self.warnings, tuple) or any(
			not isinstance(warning, str) or not warning for warning in self.warnings
		):
			raise InvalidRailPlugin("observation warnings must be non-empty text")
		if self.finalized_tip is not None:
			finalized = _coerce_integer(self.finalized_tip)
			if finalized is None or finalized < 0 or (self.tip is not None and finalized > self.tip):
				raise InvalidRailPlugin("finalized observation tip must be between zero and the tip")
			object.__setattr__(self, "finalized_tip", finalized)
		object.__setattr__(self, "unattributed_native", unattributed)

	@property
	def complete(self):
		"""Whether the bounded read has reached the provider tip it reports."""
		return self.observed_through_tip == self.tip

	def require_intent(self, intent):
		"""Refuse a batch that was produced for another payment or baseline."""
		if not isinstance(intent, PaymentIntent):
			raise InvalidRailPlugin("observations require a PaymentIntent")
		if (
			self.rail_key != intent.rail_key
			or self.intent_id != intent.intent_id
			or self.recipient != intent.recipient
			or intent.baseline is None
			or self.provider != intent.baseline.provider
			or self.baseline_tip != intent.baseline.tip
			or self.observed_after_tip != self.baseline_tip
		):
			raise InvalidRailPlugin("observations belong to another payment intent or baseline")
		return self

	def extend(self, page):
		"""Return one cumulative batch after appending a contiguous bounded page."""
		if not isinstance(page, ObservationBatch):
			raise InvalidRailPlugin("an observation page must be an ObservationBatch")
		identity = (self.rail_key, self.intent_id, self.recipient, self.provider, self.baseline_tip)
		page_identity = (page.rail_key, page.intent_id, page.recipient, page.provider, page.baseline_tip)
		if identity != page_identity:
			raise InvalidRailPlugin("observation pages belong to different payment intents")
		if page.observed_after_tip != self.observed_through_tip:
			raise InvalidRailPlugin("observation pages must be contiguous")
		if page.tip < self.tip:
			raise InvalidRailPlugin("an observation page cannot move the provider tip backwards")
		if self.unattributed_native or page.unattributed_native:
			raise InvalidRailPlugin("unattributed balance snapshots cannot be accumulated as transfer pages")
		by_transaction = {transfer.transaction_id: transfer for transfer in self.transfers}
		for transfer in page.transfers:
			if transfer.transaction_id in by_transaction:
				raise InvalidRailPlugin("contiguous observation pages repeated a transaction")
			by_transaction[transfer.transaction_id] = transfer
		warnings = tuple(dict.fromkeys((*self.warnings, *page.warnings)))
		finalized = max(
			(value for value in (self.finalized_tip, page.finalized_tip) if value is not None),
			default=None,
		)
		return ObservationBatch(
			self.rail_key,
			self.intent_id,
			self.recipient,
			self.provider,
			self.baseline_tip,
			page.tip,
			self.observed_after_tip,
			page.observed_through_tip,
			tuple(by_transaction.values()),
			self.unattributed_native + page.unattributed_native,
			warnings,
			finalized,
		)


@dataclass(frozen=True)
class SettlementDecision:
	"""A pure decision over observations; the host decides how to persist it."""

	state: str
	credited_native: int
	sighted_native: int
	transaction_ids: tuple[str, ...] = ()
	reason: str = ""

	def __post_init__(self):
		if self.state not in SETTLEMENT_STATES:
			raise InvalidRailPlugin(f"unknown settlement state {self.state!r}")
		credited = _coerce_integer(self.credited_native)
		sighted = _coerce_integer(self.sighted_native)
		if credited is None or credited < 0 or sighted is None or sighted < 0:
			raise InvalidRailPlugin("settlement amounts must be non-negative integers")
		if credited > sighted:
			raise InvalidRailPlugin("settlement cannot credit more than was sighted")
		if not isinstance(self.transaction_ids, tuple) or any(
			not isinstance(transaction_id, str) or not transaction_id
			for transaction_id in self.transaction_ids
		):
			raise InvalidRailPlugin("settlement transaction ids must be non-empty text")
		if len(set(self.transaction_ids)) != len(self.transaction_ids):
			raise InvalidRailPlugin("settlement transaction ids must be unique")
		if not isinstance(self.reason, str):
			raise InvalidRailPlugin("settlement reason must be text")
		if self.state == SETTLED and (credited == 0 or not self.transaction_ids):
			raise InvalidRailPlugin("a settled decision requires credited money and transaction ids")
		if self.state != SETTLED and self.transaction_ids:
			raise InvalidRailPlugin("only a settled decision may claim transaction ids")
		object.__setattr__(self, "credited_native", credited)
		object.__setattr__(self, "sighted_native", sighted)

	@property
	def transaction_id(self):
		"""The first credited transaction, retained as a display convenience."""
		return self.transaction_ids[0] if self.transaction_ids else ""


@dataclass(frozen=True)
class Readiness:
	"""Capabilities proven usable for one plugin under one deployment config."""

	rail_key: str
	ready: frozenset[str]
	unavailable: tuple[tuple[str, str], ...] = ()

	def __post_init__(self):
		if not isinstance(self.rail_key, str) or not self.rail_key:
			raise InvalidRailPlugin("readiness rail key must be non-empty text")
		if not isinstance(self.ready, frozenset) or not self.ready <= KNOWN_CAPABILITIES:
			raise InvalidRailPlugin("readiness contains unknown capabilities")
		if not isinstance(self.unavailable, tuple):
			raise InvalidRailPlugin("readiness unavailable reasons must be a tuple")
		unavailable_names = []
		for capability, reason in self.unavailable:
			if capability not in KNOWN_CAPABILITIES or not isinstance(reason, str) or not reason:
				raise InvalidRailPlugin("readiness unavailable reason is malformed")
			unavailable_names.append(capability)
		if len(set(unavailable_names)) != len(unavailable_names):
			raise InvalidRailPlugin("readiness repeats an unavailable capability")

	@property
	def chargeable(self):
		return CHARGE_CAPABILITIES <= self.ready

	def reason_for(self, capability):
		for named, reason in self.unavailable:
			if named == capability:
				return reason
		return ""


def binding_category_for(rail):
	"""Return a rail's declared category, defaulting old plugins pessimistically.

	The first published PaymentRail plugins predate this declaration. Absence is
	therefore a known older contract, not a malformed plugin, and means only that
	the host has no evidence of an unconditional per-sale binding. A declaration
	that is present but outside the vocabulary remains a plugin defect.
	"""
	category = getattr(rail, "binding_category", NOT_UNCONDITIONAL)
	if not isinstance(category, str) or category not in BINDING_CATEGORIES:
		raise InvalidRailPlugin("binding category must be one of the documented PaymentRail values")
	return category


@runtime_checkable
class PaymentRail(Protocol):
	"""Structural interface loaded from the ``cryptopos.rails`` entry-point group.

	``binding_category`` is an optional declaration rather than a structural
	member because it was added after the first plugins were published. Hosts use
	:func:`binding_category_for`, which treats absence as ``not-unconditional``:
	old plugins remain driveable and their binding is understated safely. A plugin
	that does declare the field must use one of the two documented values.
	"""

	key: str
	network: Network
	asset: Asset
	capabilities: frozenset[str]

	def readiness(self, configuration: Mapping[str, object]) -> Readiness: ...

	def capture_baseline(self, recipient: str, configuration: Mapping[str, object]) -> RecipientBaseline: ...

	def validate_recipient(self, recipient: str) -> tuple[str, str]: ...

	def create_request(self, intent: PaymentIntent) -> PaymentRequest: ...

	def observe(
		self,
		intent: PaymentIntent,
		configuration: Mapping[str, object],
		previous: Optional[ObservationBatch] = None,
	) -> ObservationBatch: ...

	def settle(
		self,
		intent: PaymentIntent,
		observations: ObservationBatch,
		claimed_transaction_ids: frozenset[str] = frozenset(),
	) -> SettlementDecision: ...
