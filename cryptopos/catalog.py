"""Frappe adapter over `cryptopos_core.catalog` — one rail row, one plugin.

The app half used to carry its own `URI_BUILDERS` and its own `WATCHERS`, both
keyed on a rail *family* and both holding exactly one entry: `bitcoin`. Beside
them, in the package this app already depends on, sat a catalog of concrete
rails with a uniform contract — validate a recipient, capture a baseline,
build a request, observe, settle — with adapters that reach four live testnets
between them. This module is the seam that was missing, and nothing else.

Two boundaries, both deliberate:

**`cryptopos_core` raises; a terminal explains.** The package raises subclasses
of `CryptoPosError` rather than calling `frappe.throw`, because a library that
imports `frappe` is a library nobody else can use. Every refusal that can reach
a cashier is turned into one here.

**A rail row is not a rail.** `Crypto Rail` holds what a *deployment* decided —
whether the rail is switched on, which endpoint answers, where money is
received. The catalog holds what is true about the chain. Neither belongs in
the other, which is why `catalog_key` is the whole of the link between them.
"""

import frappe
from frappe import _

from cryptopos_core import catalog as _core
from cryptopos_core import hd
from cryptopos_core.errors import CryptoPosError
from cryptopos_core.plugin import (
	ADDRESS_VALIDATION,
	OBSERVATION,
	PAYMENT_REQUEST,
	SETTLEMENT,
	PaymentIntent,
	RecipientBaseline,
)

# What a rail must prove it can do before a sale may be charged on it. All
# four, and the reason each is load-bearing: a rail that can build a QR but
# cannot observe is request-ready, not charge-ready, and charging on it would
# take money the terminal can never confirm arrived.
CHARGE_CAPABILITIES = frozenset({ADDRESS_VALIDATION, PAYMENT_REQUEST, OBSERVATION, SETTLEMENT})

CAPABILITY_WORDS = {
	ADDRESS_VALIDATION: "check the receiving address",
	PAYMENT_REQUEST: "build a payment request",
	OBSERVATION: "watch for the payment",
	SETTLEMENT: "decide when it has settled",
}


def plugins():
	"""Every built-in rail adapter, by its catalog key."""
	return {rail.key: rail for rail in _core.builtin_rails()}


def plugin_for(rail):
	"""The adapter a `Crypto Rail` row drives, or a cashier-facing refusal."""
	key = (getattr(rail, "catalog_key", "") or "").strip()
	if not key:
		frappe.throw(
			_(
				"Rail {0} names no catalog key, so nothing knows how to talk to "
				"that chain. A rail without an adapter can be described and "
				"cannot be charged."
			).format(rail.name),
			title=_("No adapter for this rail"),
		)
	adapter = plugins().get(key)
	if adapter is None:
		frappe.throw(
			_("Rail {0} names catalog key {1}, which no installed adapter provides.").format(
				rail.name, key
			),
			title=_("Unknown adapter"),
		)
	return adapter


def configuration_for(rail, mode):
	"""The deployment configuration this rail's adapter needs for `mode`."""
	endpoint = rail.endpoint_for(mode)
	if not endpoint:
		frappe.throw(
			_(
				"No endpoint is configured for {0} on {1}. A sale charged now "
				"would watch a chain nothing is reading."
			).format(rail.label, mode),
			title=_("No endpoint"),
		)
	return {"endpoint": endpoint}


# Rail families whose adapter requires a receiving address that has never been
# used. `bitcoin.py`'s `capture_baseline` refuses a recipient with any
# transaction history, and DECISIONS.md D5 is the seven reasons why that
# refusal is right.
#
# The trap this closes: a single configured address is *virgin* until its first
# payment, so a rail set up this way charges perfectly, takes one payment, and
# then refuses every charge afterwards -- mid-shift, at the counter, with an
# error about transaction history that says nothing about what to do. Refusing
# at configuration time costs the operator one field and saves them that.
FRESH_RECIPIENT_FAMILIES = frozenset({"bitcoin"})

# Families for which this terminal can build an address from a derived key.
# Only BIP-84 P2WPKH exists. See DECISIONS.md D9 for why EVM derivation was
# proposed and rejected.
DERIVING_FAMILIES = frozenset({"bitcoin"})


def requires_fresh_recipient(rail):
	"""Does this rail's adapter refuse a reused receiving address?"""
	return (rail.family or "") in FRESH_RECIPIENT_FAMILIES


def recipient_for(rail, mode):
	"""Where money on this rail is received, in this mode.

	Stated positively -- the operator configured this -- because the negative
	form ("not obviously wrong") was defeated once already by editing four
	version bytes. An empty answer is a refusal, not a default.
	"""
	if mode == "testnet":
		xpub = (getattr(rail, "testnet_xpub", "") or "").strip()
		if xpub and (rail.family or "") not in DERIVING_FAMILIES:
			frappe.throw(
				_(
					"{0} carries an extended public key but this terminal cannot "
					"build addresses for its chain. Nothing may be charged on it "
					"until that is corrected."
				).format(rail.label),
				title=_("No derivation for this rail"),
			)
		if xpub:
			# This row lock is what serialises address allocation. A scheduler
			# worker and a cashier request cannot both read the same next index:
			# the second SELECT waits until the first transaction has advanced it.
			rows = frappe.db.sql(
				"""SELECT testnet_xpub, next_address_index
				   FROM `tabCrypto Rail`
				   WHERE name = %(name)s
				   FOR UPDATE""",
				{"name": rail.name},
				as_dict=True,
			)
			if not rows:
				frappe.throw(
					_("Rail {0} no longer exists, so it cannot allocate an address.").format(
						rail.name
					),
					title=_("Receiving material disappeared"),
				)
			locked = rows[0]
			locked_xpub = (locked.testnet_xpub or "").strip()
			if not locked_xpub:
				frappe.throw(
					_("Rail {0} no longer has a testnet extended public key.").format(rail.name),
					title=_("Receiving material disappeared"),
				)
			index = int(locked.next_address_index or 0)
			try:
				account = hd.parse_extended_key(locked_xpub)
				child = hd.derive_path(account, f"0/{index}")
				address = hd.p2wpkh_address(child, "tb")
			except hd.InvalidExtendedKey as exception:
				frappe.throw(str(exception), title=_("Receiving key refused"))
			frappe.db.set_value(
				"Crypto Rail",
				rail.name,
				"next_address_index",
				index + 1,
				update_modified=False,
			)
			rail.next_address_index = index + 1
			return address
		recipient = (rail.testnet_recipient or "").strip()
		if recipient and requires_fresh_recipient(rail):
			frappe.throw(
				_(
					"{0} needs an extended public key, not a single address. Its "
					"chain requires a fresh receiving address for every payment, "
					"so one address would work until the first payment arrived "
					"and refuse every sale after that. Put an account-level "
					"testnet key in Testnet Xpub and clear Testnet Recipient."
				).format(rail.label),
				title=_("This rail derives its own addresses"),
			)
		return recipient
	# Mainnet is refused before this is reached, and demo has no recipient by
	# design: a demo that quietly borrowed the real address would be a demo
	# that can take money.
	return ""


# BIP-44's gap limit: a wallet restored from the account key stops scanning
# after this many consecutive unused addresses, so money paid beyond the run is
# money the operator's own wallet will not find.
GAP_LIMIT = 20

# How far back the run is counted. The answer saturates here, and saturating is
# harmless: anything at or above `GAP_LIMIT` is already the loudest the warning
# gets, and counting an unbounded history to say so would read every sale the
# rail has ever had in order to report a number that cannot change the advice.
GAP_SCAN_LIMIT = 100


def gap_run_for(rail):
	"""Consecutive latest endings on this rail with no credited money.

	This is a warning counter, never a charge gate. BIP-44's gap limit tells
	the operator when a restored wallet may stop scanning; it does not give
	the terminal authority to refuse a customer.

	Bounded at `GAP_SCAN_LIMIT` -- see the constant for why the saturation
	costs nothing.
	"""
	run = 0
	for credited_native in frappe.get_all(
		"Crypto Sale",
		filters={
			"rail_key": rail.name,
			"state": ("in", ("confirmed", "expired", "failed", "needs_review")),
		},
		order_by="creation desc, name desc",
		pluck="credited_native",
		limit=GAP_SCAN_LIMIT,
	):
		if int(credited_native or 0) > 0:
			break
		run += 1
	return run


def readiness_for(rail, mode):
	"""What this rail can actually do through the endpoint that is configured.

	Not what the rail *is*, which is the catalog's answer and the same
	everywhere -- what it can do *here*. Measured 2026-08-23, three public
	Sepolia endpoints answered and only one of them supported observation, so
	this distinction decides whether a sale is chargeable and is not a
	formality.
	"""
	adapter = plugin_for(rail)
	try:
		return adapter.readiness(configuration_for(rail, mode))
	except CryptoPosError as exception:
		frappe.throw(str(exception), title=_("Rail unavailable"))


def require_chargeable(rail, mode):
	"""Return the adapter, or refuse in the words of what is missing."""
	adapter = plugin_for(rail)
	readiness = readiness_for(rail, mode)
	missing = CHARGE_CAPABILITIES - set(readiness.ready)
	if missing:
		frappe.throw(
			_(
				"{0} cannot be charged through the endpoint configured for it: "
				"nothing here can {1}. It can still be described, and a sale on "
				"it would be money this terminal could not confirm."
			).format(rail.label, _(", ").join(sorted(CAPABILITY_WORDS[name] for name in missing))),
			title=_("Rail is not charge-ready"),
		)
	return adapter


def refusal(exception):
	"""Turn a package refusal into something a cashier can act on."""
	return str(exception)


def intent_to_record(intent):
	"""A `PaymentIntent` as JSON-safe primitives, for the sale's scratchpad.

	Native amounts leave as decimal strings, which is the same rule the sale's
	own `invoiced_native` follows and holds for the same reason: this document
	is read by JavaScript, and an 18-decimal amount above 2^53 does not
	survive `JSON.parse` as a number. Chain positions stay integers -- a block
	height is nowhere near the limit and reads better as one.
	"""
	baseline = intent.baseline
	return {
		"intent_id": intent.intent_id,
		"rail_key": intent.rail_key,
		"recipient": intent.recipient,
		"amount_native": str(intent.amount_native),
		"created_at_epoch": intent.created_at_epoch,
		"expires_at_epoch": intent.expires_at_epoch,
		"payment_reference": intent.payment_reference,
		"baseline": None
		if baseline is None
		else {
			"rail_key": baseline.rail_key,
			"recipient": baseline.recipient,
			"provider": baseline.provider,
			"tip": baseline.tip,
			"transaction_ids": list(baseline.transaction_ids),
			"balance_native": None
			if baseline.balance_native is None
			else str(baseline.balance_native),
		},
	}


def intent_from_record(record):
	"""The intent a sale was charged with, rebuilt exactly as it was written.

	Rebuilt, never re-derived. Capturing a fresh baseline on a later heartbeat
	would be a different chain position, and money that arrived in between
	would silently change which side of the line it fell on.
	"""
	if not record:
		return None
	baseline = record.get("baseline")
	return PaymentIntent(
		intent_id=record["intent_id"],
		rail_key=record["rail_key"],
		recipient=record["recipient"],
		amount_native=int(record["amount_native"]),
		created_at_epoch=int(record["created_at_epoch"]),
		expires_at_epoch=int(record["expires_at_epoch"]),
		payment_reference=record.get("payment_reference", ""),
		baseline=None
		if baseline is None
		else RecipientBaseline(
			rail_key=baseline["rail_key"],
			recipient=baseline["recipient"],
			provider=baseline["provider"],
			tip=baseline["tip"],
			transaction_ids=tuple(baseline.get("transaction_ids") or ()),
			balance_native=None
			if baseline.get("balance_native") is None
			else int(baseline["balance_native"]),
		),
	)
