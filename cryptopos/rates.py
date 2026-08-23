"""Frappe adapter over `cryptopos_core.rates`.

The rate logic itself moved to the core package, where it has no framework
under it. What stays here is the one thing the core cannot do: turn a refusal
into something a cashier sees.

`cryptopos_core` raises subclasses of `CryptoPosError` rather than calling
`frappe.throw`, because a library that knows about `frappe` is a library
nobody else can use. This module is the boundary where those refusals become
cashier-facing messages.

Constants are re-exported so callers that read `rates.MICROCENTS_PER_USD`
keep working; there is one definition and it lives in the core.
"""

import frappe

from cryptopos_core import rates as _core
from cryptopos_core.errors import CryptoPosError

MICROCENTS_PER_USD = _core.MICROCENTS_PER_USD
DEMO_MICROCENTS = _core.DEMO_MICROCENTS
FEED_TIMEOUT_SECONDS = _core.FEED_TIMEOUT_SECONDS


def quote(asset, mode):
	"""Return (microcents_per_whole_coin, source, ok). See `cryptopos_core.rates.quote`.

	`ok` is False when this is a fallback rather than a quote. Nothing here
	dresses a fallback up as a feed answer.
	"""
	try:
		return _core.quote(asset, mode)
	except CryptoPosError as exception:
		frappe.throw(str(exception), title="Cannot price this sale")


def native_for(usd_cents, rate_microcents, native_decimals):
	"""Convert an invoiced cent amount into exact native units.

	Integer arithmetic throughout, in the core. The result is THE amount: the
	URI, the display and every tolerance check derive from this one integer.
	"""
	try:
		return _core.native_for(usd_cents, rate_microcents, native_decimals)
	except CryptoPosError as exception:
		frappe.throw(str(exception))
