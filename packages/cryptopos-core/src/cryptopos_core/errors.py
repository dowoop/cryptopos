"""Errors the core raises, and the reason it raises rather than reports.

Most of this package refuses to raise. `chain.py` in particular is total by
contract: a policy read that fails returns a sentinel and a reason, because a
sale must never fail because the policy layer is down.

Money-boundary refusals are the exception, and deliberately. There is no
honest sentinel for "this sale has no price" or "this payment URI is unsafe"
-- a caller handed `None` will either display it, multiply by it, or encode it,
and each is worse than stopping. Those conditions raise, and the host decides
how to say so.

A host framework should catch these at its boundary and translate them into
whatever its users see. The core does not know what a screen is.
"""


class CryptoPosError(Exception):
	"""Base for documented money-boundary refusals. Catch this at the host boundary."""


class RailPluginError(CryptoPosError):
	"""A rail plugin is missing, malformed, duplicated, or cannot do an operation."""


class InvalidRailPlugin(RailPluginError):
	"""An installed object does not satisfy the payment-rail plugin contract."""

	def __init__(self, reason):
		self.reason = reason
		super().__init__(f"Invalid payment-rail plugin: {reason}.")


class DuplicateRail(RailPluginError):
	"""Two installed plugins claim the same concrete rail identifier."""

	def __init__(self, rail_key):
		self.rail_key = rail_key
		super().__init__(f"More than one payment-rail plugin registered {rail_key!r}.")


class RailNotInstalled(RailPluginError):
	"""No installed plugin implements the requested concrete rail."""

	def __init__(self, rail_key):
		self.rail_key = rail_key
		super().__init__(f"No installed payment-rail plugin provides {rail_key!r}.")


class UnsupportedCapability(RailPluginError):
	"""A plugin was asked to perform a capability it did not declare."""

	def __init__(self, rail_key, capability):
		self.rail_key = rail_key
		self.capability = capability
		super().__init__(f"Rail {rail_key!r} does not provide capability {capability!r}.")


class RailProviderError(RailPluginError):
	"""A configured provider was unavailable, malformed, or the wrong network."""

	def __init__(self, rail_key, reason):
		self.rail_key = rail_key
		self.reason = reason
		super().__init__(f"Provider for rail {rail_key!r} is not safe to use: {reason}.")


def _coerce_integer(value):
	"""Return an exact integer form, or None without truncating a value.

	Host form fields arrive as strings, so integer strings are accepted. Floats
	are not: `int(1.9) == 1` is a lossy conversion a money boundary must never
	perform silently. Booleans are integers to Python and amounts to nobody.
	"""
	if isinstance(value, bool):
		return None
	if isinstance(value, int):
		return value
	if isinstance(value, str):
		try:
			return int(value.strip())
		except ValueError:
			return None
	return None


class RateUnavailable(CryptoPosError):
	"""No usable price could be established for the asset.

	Carries `asset` so a host can name it without parsing the message.

	`message` overrides the default wording, because on a real-money mode the
	reason is rarely "nothing answered" -- it is more often "something
	answered and this build will not price real money from it". Those need
	different words in front of an operator, and the same exception type so
	that a host catching this one stays correct without being rewritten.
	"""

	def __init__(self, asset, message=None):
		self.asset = asset
		super().__init__(message or f"No feed answered for {asset} and no fallback rate exists for it.")


class FeedsDisagree(RateUnavailable):
	"""Feeds answered and did not agree closely enough to price real money.

	**A subclass of `RateUnavailable` on purpose.** A host that already
	catches `RateUnavailable` at its boundary keeps working, and keeps
	refusing, without being changed -- the safe behaviour is the one you get
	by doing nothing. Catch this specifically only to say something better.

	Carries `prices` (feed name -> Decimal) and `spread` so an operator can
	be shown which source is the outlier rather than a shrug.
	"""

	def __init__(self, asset, prices, spread):
		self.prices = prices
		self.spread = spread
		quoted = ", ".join(f"{name} {price}" for name, price in sorted(prices.items()))
		super().__init__(
			asset,
			f"Feeds disagree about {asset} by {spread:.2%} ({quoted}). At least one is "
			f"wrong and nothing here can tell which, so this build will not pick one to "
			f"price real money with.",
		)


class InvalidRate(CryptoPosError):
	"""A rate that cannot price anything -- zero or negative.

	Separate from `RateUnavailable` because the remedies differ: one is a
	network or coverage problem, the other means a caller passed through a
	value it should have checked.
	"""

	def __init__(self, rate_microcents):
		self.rate_microcents = rate_microcents
		super().__init__(f"A rate must be a positive integer number of microcents; got {rate_microcents!r}.")


class InvalidAmount(CryptoPosError):
	"""A sale or payment amount is not a positive integer.

	Carries the field and value so a host can put the error beside the input
	that caused it without parsing prose.
	"""

	def __init__(self, field, value, minimum=1):
		self.field = field
		self.value = value
		self.minimum = minimum
		requirement = "a positive integer" if minimum == 1 else f"an integer of at least {minimum}"
		super().__init__(f"{field} must be {requirement}; got {value!r}.")


class InvalidPaymentIdentity(CryptoPosError):
	"""Sale-binding data cannot be represented safely in a payment URI."""

	def __init__(self, rail_key, field, value, reason):
		self.rail_key = rail_key
		self.field = field
		self.value = value
		self.reason = reason
		super().__init__(f"Refusing to build a {rail_key} URI: invalid {field}: {reason}.")


class InvalidMode(CryptoPosError):
	"""A mode typo cannot silently weaken real-money policy."""

	def __init__(self, mode, valid_modes):
		self.mode = mode
		self.valid_modes = tuple(valid_modes)
		choices = ", ".join(self.valid_modes)
		super().__init__(f"Unknown mode {mode!r}; expected one of: {choices}.")


class InvalidAsset(CryptoPosError):
	"""A price request did not name a safe ASCII ticker."""

	def __init__(self, asset):
		self.asset = asset
		super().__init__(f"An asset ticker must use only ASCII letters, digits, or hyphens; got {asset!r}.")


class UnsupportedRail(CryptoPosError):
	"""No safe payment-URI implementation exists for the requested rail."""

	def __init__(self, rail_key):
		self.rail_key = rail_key
		super().__init__(f"No payment URI builder exists for rail {rail_key!r}.")


class AddressRefused(CryptoPosError):
	"""The receiving address failed its check, so no URI was built.

	Raised rather than returned, for the same reason pricing raises: there is
	no honest sentinel. A caller handed `None` here either displays it or
	encodes it, and a QR built around a bad address is money sent to nobody.

	Carries `verdict` (`"refused"` or `"unchecked"`) so a host can tell a
	provably-wrong address from one this build simply cannot verify, and word
	the two differently -- they are different problems for an operator.
	"""

	def __init__(self, rail_key, address, verdict, reason):
		self.rail_key = rail_key
		self.address = address
		self.verdict = verdict
		self.reason = reason
		super().__init__(f"Refusing to build a {rail_key} URI: {reason}.")


class AmountNotRepresentable(CryptoPosError):
	"""The invoiced amount cannot be written exactly in this rail's URI.

	Only decimal-amount schemes can hit this. The URI would carry a TRUNCATED
	amount, the customer would pay exactly what they were shown, and the sale
	would sit short of its own invoice forever -- an unresolvable review over
	real money.

	`representable` is the nearest amount at or below the request that CAN be
	written exactly. Invoice that instead.
	"""

	def __init__(self, rail_key, native_units, representable):
		self.rail_key = rail_key
		self.native_units = native_units
		self.representable = representable
		super().__init__(
			f"A {rail_key} URI cannot state {native_units} exactly -- it would ask for "
			f"{representable} and the sale could never settle. Invoice {representable}."
		)
