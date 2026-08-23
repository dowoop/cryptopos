"""Frappe adapter over `cryptopos_core.chain`.

The reads moved to the core package, where configuration arrives through a
constructor instead of being fetched from a settings store. What stays here is
the fetch: build a reader from `CryptoPoS Settings` and expose the same
module-level surface the app already calls, so `loyalty.py`, `api.py` and the
harness did not have to change.

A reader is built per call rather than cached. That is deliberate and it
matches the behaviour this module had when it called `frappe.get_cached_doc`
on every function: settings edited at the till take effect on the next read,
and nothing holds a stale indexer URL. The object holds no connection, so
building one costs a settings lookup Frappe has already cached.

Everything here is still TOTAL. Nothing raises. A read that fails returns a
sentinel and a reason, because a sale must never fail because the policy
layer is down.
"""

import frappe

from cryptopos_core.chain import (
	OotleReader,
	ceilings_wording,
	earning_only_notice,
)

# Re-exported unchanged: both are pure functions of `facts` that read no chain
# and need no configuration, so a surface can render the wording with the
# indexer down. Named here so `ootle.ceilings_wording(...)` keeps resolving.
__all__ = [
	"available",
	"ceilings_wording",
	"check_it_yourself",
	"earning_only_notice",
	"indexer",
	"points_balance",
	"promise",
]


def _reader():
	settings = frappe.get_cached_doc("CryptoPoS Settings")
	return OotleReader(
		indexer=settings.ootle_indexer,
		loyalty_component=settings.loyalty_component,
		loyalty_points_resource=settings.loyalty_points_resource,
	)


def indexer():
	return _reader().indexer


def available():
	"""Is the policy layer reachable at all? Never raises."""
	return _reader().available()


def promise():
	"""Read the deployed contract's own account of itself.

	Returns (facts, None) or (None, reason).
	"""
	return _reader().promise()


def points_balance(account, points_resource):
	"""Read a customer's points balance. Returns (points, None) or (None, reason).

	NEVER call this on the path of a sale.
	"""
	return _reader().points_balance(account, points_resource)


def check_it_yourself(facts, account=""):
	"""The literal URLs a customer can open to check the promise themselves."""
	return _reader().check_it_yourself(facts, account)
