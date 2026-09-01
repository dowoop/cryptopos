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
from cryptopos_core.errors import CryptoPosError, InvalidRailPlugin
from cryptopos_core.plugin import (
	ADDRESS_VALIDATION,
	OBSERVATION,
	PAYMENT_REQUEST,
	SETTLEMENT,
	UNCONDITIONAL_PER_SALE,
	PaymentIntent,
	RecipientBaseline,
	binding_category_for,
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


# What a rail adapter has to be before this deployment will drive one. Checked
# on anything arriving from outside the package, because an entry point is a
# string in somebody else's metadata: `pip install` is enough to put a name in
# this table, and a wheel exporting a module, a dict or a half-written class
# must be refused as a bad plugin rather than discovered as a broken rail.
_RAIL_SHAPE = ("key", "capabilities", "validate_recipient", "readiness",
               "capture_baseline", "create_request", "observe", "settle")

# Discovery is done once per process and kept. Frappe runs several -- web,
# scheduler, two queues -- and each one discovers on its own first use, which
# is why installing a wheel needs those processes restarted before the rail is
# visible everywhere. A hot install otherwise leaves workers disagreeing about
# what exists, and a rail that half the deployment can see is worse than one
# nobody can.
_DISCOVERED = None
_REFUSED = {}
_IDENTITY = {}
_DESCRIBED = {}


def _entry_point_rails():
	"""Rails advertised by installed distributions, and why any were refused.

	The entry point group is `cryptopos.rails`, which `cryptopos-core` already
	declares in its own `pyproject.toml` for four of its builtins. Those
	resolve to the identical objects `builtin_rails()` returns, so they are
	idempotent rather than duplicates -- identity is checked, not the name.
	"""
	from importlib import metadata

	# Keyed by ORIGIN, never by `point.name`. Two distributions may advertise
	# the same entry-point name -- nothing stops them, the name is theirs to
	# choose -- and a dict keyed on the name loses one of them silently,
	# BEFORE the collision check below ever sees it. The survivor would then be
	# whichever distribution metadata iteration happened to reach last.
	# Reproduced 2026-08-25 against this function's first draft.
	found, refused = [], {}
	for point in metadata.entry_points(group="cryptopos.rails"):
		distribution = getattr(point, "dist", None)
		origin = (f"{getattr(distribution, 'name', '?')} "
		          f"{getattr(distribution, 'version', '?')} [{point.name}]")
		try:
			adapter = point.load()
		except Exception as exception:
			# A broken wheel must not take the terminal down on import. It is
			# recorded instead, and `plugin_for` says so if a row names it --
			# because "no installed adapter provides that key" would send an
			# operator to install something that is already installed.
			refused[origin] = f"{type(exception).__name__}: {exception}"
			continue
		missing = [name for name in _RAIL_SHAPE if not hasattr(adapter, name)]
		if missing:
			refused[origin] = (
				f"does not look like a rail adapter: no {', '.join(missing)}")
			continue
		try:
			binding_category_for(adapter)
		except InvalidRailPlugin as exception:
			refused[origin] = exception.reason
			continue
		found.append((origin, adapter))
	return found, refused


def plugins():
	"""Every rail adapter this deployment can drive, by its catalog key.

	The built-ins, plus anything installed into this environment that
	advertises a `cryptopos.rails` entry point. That second half is what lets
	an operator add an asset by installing a wheel and creating a row, instead
	of editing this app -- the difference between a terminal that supports five
	rails and one that supports the rail its operator needs.

	**A duplicate key is refused, never resolved.** `network.key/asset.key`
	names the concrete money: one chain, one asset, one contract. Two adapters
	claiming it are not two assets, so there is no correct winner to pick.
	Letting the external one win makes the terminal's behaviour depend on
	install order; letting the built-in win silently defeats the install the
	operator performed on purpose. Both are worse than saying so.
	"""
	global _DISCOVERED, _REFUSED, _IDENTITY, _DESCRIBED
	if _DISCOVERED is not None:
		return dict(_DISCOVERED)

	# A BUILT-IN THAT CANNOT DO THE JOB DOES NOT HOLD THE KEY.
	#
	# Six of the twelve built-ins are `RequestRail` placeholders or partial
	# readers: they build a QR, or read a balance, and cannot prove a payment
	# arrived. `require_chargeable()` refuses every one of them, so none can
	# ever carry a sale -- and while they sat in this registry they owned the
	# NAME OF THE MONEY, which meant a plugin that can settle that money was
	# permanently locked out by a stub admitting it cannot.
	#
	# Decided here, in the host, at discovery: a placeholder is never an
	# adapter, in every process, always. That is what makes it different from
	# the runtime override rejected in D30 -- there is no install order to
	# depend on, and no window in which two processes disagree about which
	# implementation a key resolves to.
	#
	# They are not discarded. `described_rails()` keeps them with the blocker
	# each states about itself, so a rail this deployment cannot drive is still
	# one it can describe -- and `plugin_for` says which of the two an operator
	# is looking at.
	described, registry = {}, {}
	for rail in _core.builtin_rails():
		if CHARGE_CAPABILITIES <= rail.capabilities:
			registry[rail.key] = rail
		else:
			described[rail.key] = rail

	identity = dict.fromkeys(registry, "builtin")
	external, refused = _entry_point_rails()

	for origin, adapter in external:
		key = getattr(adapter, "key", "")
		existing = registry.get(key)
		if existing is adapter:
			continue                              # a builtin, advertising itself
		if existing is not None:
			refused[origin] = (
				f"claims {key}, which is already provided by "
				f"{identity.get(key, 'another adapter')}. That key names one "
				f"asset on one chain, so there is no second one for a second "
				f"adapter to be.")
			continue
		# THE SAME BAR THE BUILT-INS ARE HELD TO, and it has to be the same one.
		# A built-in that cannot observe or settle is filed as described rather
		# than driveable, a few lines above. An external arriving with the same
		# gap was being registered as an adapter anyway -- where it would
		# DISPLACE the described entry, so the operator lost the blocker
		# explaining why that money cannot be taken, and gained a rail that
		# `require_chargeable` refuses for reasons it no longer states.
		# Nothing was ever at risk of being mis-settled; the honest message was.
		missing = sorted(CHARGE_CAPABILITIES - set(getattr(adapter, "capabilities", ())))
		if missing:
			refused[origin] = (
				f"claims {key} without being able to "
				f"{', '.join(CAPABILITY_WORDS.get(c, c) for c in missing)}. A rail "
				f"that cannot do all four cannot carry a sale, and taking the key "
				f"would only hide whatever already explains why.")
			continue
		registry[key] = adapter
		identity[key] = origin

	# A plugin claiming a described key is not a collision -- nothing was
	# driving it. It stops being merely described the moment something can.
	for key in registry:
		described.pop(key, None)

	_DISCOVERED, _REFUSED, _IDENTITY, _DESCRIBED = registry, refused, identity, described
	return dict(registry)


#: What a rail is configured to receive WITH. Ordered strongest first, and
#: the first two are the ones that bind a payment to one sale.
XPUB, COMPONENT, STATIC_ADDRESS = "xpub", "component", "address"


def receiving_material(rail, mode):
	"""What this rail receives with, WITHOUT allocating any of it.

	Pure: it reads the row and returns. That is the whole point of its
	existence, because `recipient_for` answers a different question --
	*give me the address to put on this sale* -- and answering it DERIVES a
	fresh address, advances `next_address_index` and takes a `FOR UPDATE` row
	lock while it does.

	**Asking that function a yes/no question spends one of the operator's
	addresses per question, and `binding_label` was asking it one.** Measured
	2026-08-31: a single `api.rails()` -- what a till does every time it draws
	its rail list -- moved `btc`'s `next_address_index` from 2 to 3, with no
	sale in existence. `charge()` was worse: it called `recipient_for` for the
	address and then `binding_label` called it AGAIN, so every real Bitcoin
	sale consumed two indices and recorded the first. Nothing was stolen and
	no money was lost -- an xpub can re-derive any index -- but the skipped
	addresses eat BIP-44's twenty-address gap limit at twice the rate, and
	past the gap a restored wallet stops scanning and does not find the money.

	It is also the same shape as the defect it was introduced fixing: a
	function whose name says it reports, doing something underneath.
	"""
	if mode != "testnet":
		# Mainnet is refused upstream and demo deliberately has no recipient,
		# so neither can receive anything and neither has a binding to label.
		return ""
	if (getattr(rail, "testnet_xpub", "") or "").strip():
		return XPUB
	if (getattr(rail, "payment_component", "") or "").strip():
		return COMPONENT
	if (getattr(rail, "testnet_recipient", "") or "").strip():
		return STATIC_ADDRESS
	return ""


def binding_label(rail, mode):
	"""`per-sale`, `shared`, or "" when nothing can receive. ONE implementation.

	Three callers compute this -- the sale record at charge time, the rails
	list a till draws from, and `tools/snapshot.py` -- and until 2026-08-31
	they each had their own copy of the rule. D35 is what that costs: a rule
	in three places drifted in the one nobody searched for, under a docstring
	saying it was checked. D54 merged the first two and did not find the
	third, which then reported `xtr` as SHARED for a day while the till
	correctly reported it as per-sale.

	**Configuration, not adapter declaration, decides this.** D45 established
	that `binding_category` is a claim about an ADAPTER; whether a DEPLOYMENT
	binds per sale additionally depends on how the operator configured it. The
	same Ootle adapter is `shared` pointed at a plain account and per-sale
	pointed at a payment component, and the adapter's own declaration cannot
	know which. So the static declaration is read here but never overridden.
	"""
	material = receiving_material(rail, mode)
	if not material:
		return ""
	# A fresh address per sale from the merchant's xpub, or a payment
	# component the payer names the sale on. Both are FACTS about where the
	# money lands, rather than claims an adapter makes about itself.
	if material in (XPUB, COMPONENT):
		return "per-sale"
	# Or the adapter binds per sale on its own, the way a Solana Pay reference
	# does.
	if declared_binding_category(plugin_for(rail)) == UNCONDITIONAL_PER_SALE:
		return "per-sale"
	return "shared"


def declared_binding_category(adapter):
	"""Resolve a declaration without making pre-category plugins disappear.

	An explicit plugin value is authoritative. For an older plugin with no such
	field, a matching built-in concrete rail supplies the library declaration;
	otherwise the protocol helper's pessimistic ``not-unconditional`` default
	stands. Matching uses the concrete catalog key, never an editable row name.
	"""
	plugin_category = binding_category_for(adapter)
	if hasattr(adapter, "binding_category"):
		return plugin_category
	for known in _core.builtin_rails():
		if known.key == adapter.key:
			return binding_category_for(known)
	return plugin_category


def described_rails():
	"""Rails this deployment knows about and cannot drive, with the reason.

	Each states its own blocker. A rail that can build a payment request and
	cannot prove receipt is request-ready, not charge-ready, and charging on it
	would take money the terminal could never confirm arrived.
	"""
	plugins()
	return dict(_DESCRIBED)


def adapter_identity(key):
	"""Which implementation currently provides `key`, as a stable string.

	`catalog_key` names the MONEY -- one chain, one asset, one contract. It
	does not name the code that moves it, and those are not the same fact.
	Install adapter A, charge a sale under it, replace it with adapter B under
	the same key, and B will happily reinterpret A's persisted baseline and
	settle a payment A would have refused: nothing in the sale record said
	which implementation created it. So charge() stamps this, and watch()
	refuses an in-flight sale whose implementation changed underneath it.

	Found by Codex arguing against a proposal on 2026-08-25 and reproduced
	here; see DECISIONS.md D30.
	"""
	plugins()
	return _IDENTITY.get(key, "unknown")


def refused_plugins():
	"""Entry points that advertised a rail and did not become one.

	Read by `tools/rails_probe.py`. A refusal nobody can see is a rail an
	operator installed, cannot find, and has no way to ask about.
	"""
	plugins()
	return dict(_REFUSED)


def _forget_plugins():
	"""Drop the process cache. For tests and for a harness that installs one."""
	global _DISCOVERED, _REFUSED, _IDENTITY, _DESCRIBED
	_DISCOVERED, _REFUSED, _IDENTITY, _DESCRIBED = None, {}, {}, {}


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
		known = described_rails().get(key)
		if known is not None:
			frappe.throw(
				_(
					"Rail {0} names {1}, which this deployment knows about and "
					"cannot drive: {2}. It can be described and it cannot be "
					"charged. Installing a plugin that provides {1} is what "
					"would make it driveable."
				).format(
					rail.name, key,
					getattr(known, "blocker", "") or "it cannot prove a payment arrived",
				),
				title=_("Rail known, not driveable"),
			)
		refused = refused_plugins()
		if refused:
			frappe.throw(
				_(
					"Rail {0} names catalog key {1}, which no installed adapter "
					"provides. {2} installed entry point(s) advertised a rail and "
					"were refused: {3}. That is a different problem from nothing "
					"being installed, and it is fixed differently."
				).format(rail.name, key, len(refused),
				         "; ".join(f"{n}: {why}" for n, why in sorted(refused.items()))),
				title=_("Unknown adapter"),
			)
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
	configuration = {"endpoint": endpoint}
	# Only when the row names one. An adapter that does not read this ignores
	# it, and the Ootle adapter falls back to its shared-account path -- which
	# is what D48 measured and what the rail README warns about in a box.
	component = (rail.get("payment_component") or "").strip()
	if component:
		configuration["payment_component"] = component
	return configuration


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
# Version bytes are part of the choice: Bitcoin testnet accepts only its
# tpub/vpub serialisations, while EVM wallets conventionally export BIP-32
# xpub bytes even for testnets.
def _bitcoin_testnet_address(key):
	if key.version not in (0x043587CF, 0x045F1CF6):
		raise hd.InvalidExtendedKey(
			"Bitcoin testnet address derivation requires tpub or vpub version bytes"
		)
	return hd.p2wpkh_address(key, "tb")


_ADDRESS_BUILDERS = {
	"bitcoin": _bitcoin_testnet_address,
	"evm-native": hd.evm_address,
	"evm-erc20": hd.evm_address,
}
DERIVING_FAMILIES = frozenset(_ADDRESS_BUILDERS)


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
				address = _ADDRESS_BUILDERS[rail.family](child)
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
