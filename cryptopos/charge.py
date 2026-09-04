"""charge() — the one write that decides what a sale is.

Everything except the state, the watcher's discoveries and the scratchpad is
written here and never again. That is the rule the whole terminal rests on:
nothing mid-flight can reclassify a sale. A rate that moves, a mode switch
flipped at the till, a merchant rename, an earn rate changed for a promotion
-- none of them may reach backwards into a sale already in flight, because a
receipt reprinted next month has to say what the customer was handed.
"""

import json
import secrets
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import add_to_date, get_system_timezone, now_datetime

from cryptopos import catalog, rates
from cryptopos_core import qr
from cryptopos_core import rails as _core_rails
from cryptopos_core.errors import CryptoPosError
from cryptopos_core.plugin import PaymentIntent

RATE_LOCK_SECONDS = 15 * 60
DEFAULT_MAX_OPEN_SALES = 5
DEFAULT_MAX_SALES_PER_HOUR = 20
OPEN_SALE_STATES = ("awaiting", "detected", "confirming")


def _epoch(moment):
	"""A Frappe datetime as a real Unix epoch.

	`now_datetime()` returns a NAIVE datetime in the SITE's timezone, and
	`datetime.timestamp()` reads a naive value as the PROCESS's local time. Those
	agree only when the site timezone and the container timezone happen to
	match, and here they do not: the site is `America/Adak` and the container
	has no TZ set, so it is UTC.

	The result was a flat nine-hour error, and it was invisible for the worst
	possible reason. `expires_at_epoch` landed nine hours in the PAST, and both
	the Bitcoin and EVM adapters credit a transfer only when
	`block_time_epoch <= expires_at_epoch` -- so a payment made *now* was always
	"after expiry" and no live sale could ever settle, while the harness's
	fixtures, which point at payments that are genuinely days old, settled
	perfectly. Fifty sales were taken here without one genuine end-to-end
	settlement, and the suites stayed green throughout.

	Measured 2026-08-24 by charging a sale, paying it from the bundled wallet
	within seconds, watching the transaction reach eleven confirmations on
	Sepolia, and watching the terminal call it "payment arrived after expiry".
	"""
	return int(moment.replace(tzinfo=ZoneInfo(get_system_timezone())).timestamp())

# Deliberately excludes vowels and the characters that a handwritten or
# read-aloud reference confuses: 0/O, 1/I/L, 5/S, 8/B.
REF_ALPHABET = "23467940ACDEFGHJKMNPQRTVWXYZ".replace("0", "")


def _invoice_ref():
	groups = []
	for _group in range(3):
		groups.append("".join(secrets.choice(REF_ALPHABET) for _c in range(4)))
	return "-".join(groups)


def _invoice_id(when):
	stamp = when.strftime("%Y%m%d")
	counter = frappe.db.count("Crypto Sale", {"invoice_id": ("like", f"INV-{stamp}-%")}) + 1
	return f"INV-{stamp}-{counter:04d}"


def _intent_id(sale_reference):
	"""A stable id for the payment intent this sale is.

	The invoice reference, which is already unique per sale and already the
	thing a customer and a cashier say out loud. Lowercased because the
	plugin contract's identifier grammar is lowercase ASCII.
	"""
	return sale_reference.lower()


def _scale_of(rail, adapter):
	"""The two numbers the amount math needs, taken from THIS deployment's row.

	`invoice_amount` and `usd_cents_to_native` read exactly `native_decimals`
	and `display_decimals` and nothing else, and the `Crypto Rail` row carries
	both -- `install.py` seeds them from the frozen table, and all five enabled
	rails were verified identical before this stopped reading that table.

	It used to be `_core_rails.rail_for(rail.rail_key)`, which knows twelve rail
	keys and raises a bare `KeyError` for a thirteenth. That made an operator's
	own rail row un-chargeable with a traceback rather than a sentence, and it
	was a split brain besides: the very next line of this function already
	prices its error message from `rail.unit_name`, the row. One source now.

	The row is validated by its own DocType, so this asserts only what that
	cannot: that the scales are ordered, because a display precision finer than
	the chain's own would ask a customer for an amount no URI can state, and
	that the row agrees with the adapter that will watch for the money.

	**That second check is the price of trusting the row.** The frozen table
	could not be edited by an operator; a DocType row can. A row claiming 6
	native decimals in front of an 18-decimal adapter would invoice a millionth
	of the intended amount and settle it as paid in full, and every arithmetic
	assertion in this codebase would agree, because they would all be reading
	the same wrong number.
	"""
	native = int(rail.native_decimals or 0)
	display = int(rail.display_decimals or 0)
	chain_native = getattr(getattr(adapter, "asset", None), "decimals", None)
	if chain_native is not None and native != int(chain_native):
		frappe.throw(
			_("{0} says {1} native decimals; the {2} adapter says {3}. "
			  "One of them is wrong and this refuses rather than guess.").format(
				rail.label or rail.rail_key, native, adapter.key, chain_native),
			title=_("Rail scale disagrees with its adapter"),
		)
	if native < 0 or display < 0 or display > native:
		frappe.throw(
			_("{0} declares {1} native decimals and {2} display decimals. "
			  "Display precision cannot be finer than the chain's own.").format(
				rail.label or rail.rail_key, native, display),
			title=_("Rail scale is impossible"),
		)
	return {"native_decimals": native, "display_decimals": display}


def _configured_limit(settings, fieldname, default):
	"""A missing or non-positive setting stays capped at the safe default."""
	try:
		value = int(settings.get(fieldname) or default)
	except (TypeError, ValueError):
		return default
	return value if value > 0 else default


def _enforce_charge_limits(settings):
	"""Serialise charge admission, then count the shared database state.

	The singleton's DocType row is the mutex. Every charge transaction takes the
	same guaranteed-to-exist row lock before its locking reads of Crypto Sale, so
	two web workers cannot both observe the last available slot and insert into
	it. The lock lives until the request transaction commits, after the admitted
	sale has been written.
	"""
	frappe.db.sql(
		"""SELECT name FROM `tabDocType`
		   WHERE name = %(doctype)s
		   FOR UPDATE""",
		{"doctype": "CryptoPoS Settings"},
	)

	open_limit = _configured_limit(settings, "max_open_sales", DEFAULT_MAX_OPEN_SALES)
	hour_limit = _configured_limit(
		settings, "max_sales_per_hour", DEFAULT_MAX_SALES_PER_HOUR
	)
	window_start = add_to_date(now_datetime(), hours=-1)
	rows = frappe.db.sql(
		"""SELECT state, creation FROM `tabCrypto Sale`
		   WHERE state IN %(open_states)s OR creation >= %(window_start)s
		   FOR UPDATE""",
		{"open_states": OPEN_SALE_STATES, "window_start": window_start},
		as_dict=True,
	)

	open_sales = sum(row.state in OPEN_SALE_STATES for row in rows)
	if open_sales >= open_limit:
		frappe.throw(
			_(
				"Having more than {0} sales open at once is refused by decision. "
				"Wait for an open sale to settle or expire before charging another."
			).format(open_limit),
			title=_("Open-sale limit reached"),
		)

	window_sales = sum(row.creation >= window_start for row in rows)
	if window_sales >= hour_limit:
		frappe.throw(
			_(
				"Opening more than {0} sales in one hour is refused by decision. "
				"Wait until an earlier sale leaves the rolling one-hour window "
				"before charging another."
			).format(hour_limit),
			title=_("Hourly charge limit reached"),
		)


def charge(usd_cents, rail_key, loyalty_account=""):
	"""Snapshot a sale and arm its watcher. Returns the Crypto Sale doc."""
	usd_cents = int(usd_cents)
	if usd_cents <= 0:
		frappe.throw(_("A sale must be for more than nothing."))

	settings = frappe.get_single("CryptoPoS Settings")
	rail = frappe.get_doc("Crypto Rail", rail_key)

	if not rail.enabled:
		frappe.throw(_("Rail {0} is not enabled.").format(rail_key))

	mode = settings.mode or "testnet"

	# Charter boundary: mainnet is a non-working mode by decision, not by
	# accident. It is refused in words that say *announced*, not
	# *unreachable* -- Tari has published no endpoint, and a refusal that
	# implies a network outage would be a different and false claim.
	if mode == "mainnet":
		frappe.throw(
			_(
				"Mainnet is a non-working mode by decision. No endpoint is "
				"published for it, and opening it is a decision that has not "
				"been made. Charge on testnet or demo."
			),
			title=_("Mainnet refused"),
		)

	endpoint = rail.endpoint_for(mode)

	# The adapter, and proof it can do the whole job through the endpoint this
	# deployment configured. Not what the rail is -- what it can do here.
	# Three public Sepolia endpoints were measured and only one of them
	# supported observation, so a rail that is charge-ready in the catalog can
	# be request-only on the operator's own connection.
	adapter = catalog.require_chargeable(rail, mode)
	configuration = catalog.configuration_for(rail, mode)

	# Admission is the last read-only act before recipient_for can allocate a
	# derived address, a quote is taken, a chain baseline is captured, an
	# invoice identity is minted or a sale is written. Demo takes this path too:
	# it uses the same database rows and watcher, and five simultaneous / twenty
	# hourly openings leave it useful without making a public demo an uncapped
	# automation endpoint.
	_enforce_charge_limits(settings)

	# Where the money goes, and whose it is. Stated positively -- "the
	# operator configured this" -- because the negative form was defeated
	# once already by editing four version bytes.
	address = catalog.recipient_for(rail, mode)
	identity_source = "config" if address else "none"
	# One implementation, in catalog.binding_label. The copy that used to live
	# here knew about xpubs and nothing else, so a rail bound by a payment
	# component reported `shared` -- understating a guarantee, which is the
	# rarer and quieter direction of the same defect D48 recorded.
	binding = catalog.binding_label(rail, mode)

	if identity_source == "none":
		frappe.throw(
			_(
				"No receiving address is configured for {0}. A sale charged now "
				"would watch an address nobody holds the keys to."
			).format(rail.label),
			title=_("No receiving material"),
		)

	try:
		adapter.validate_recipient(address)
	except CryptoPosError as exception:
		frappe.throw(str(exception), title=_("Receiving address refused"))

	rate_microcents, rate_source, feed_answered = rates.quote(rail.asset, mode)
	rate_at = now_datetime()

	# `rails.invoice_amount`, never `rates.native_for`. The primitive divides
	# straight to native precision and can produce an amount no URI can state:
	# on ETH, POL, SOL and XMR the display and native decimals differ, and the
	# QR would ask for less than the sale expects. Measured across the rail
	# table, five of twelve rails disagree between the two. `invoice_amount`
	# rounds once at display precision and then scales, so whatever comes back
	# is exactly what the payment request will say.
	try:
		invoiced_native = _core_rails.invoice_amount(
			_scale_of(rail, adapter), usd_cents, rate_microcents
		)
	except CryptoPosError as exception:
		frappe.throw(str(exception), title=_("Cannot invoice this amount"))
	if invoiced_native <= 0:
		frappe.throw(_("That amount rounds to zero {0}.").format(rail.unit_name))

	charged_at = now_datetime()
	invoice_ref = _invoice_ref()

	# The baseline is read BEFORE the payer is shown anything, and it is what
	# makes attribution possible on a shared address: everything after this
	# chain position is a candidate, everything at or before it is somebody
	# else's. The old watcher approximated this by comparing block timestamps
	# against the charge time, which is a clock comparison standing in for a
	# chain fact.
	try:
		baseline = adapter.capture_baseline(address, configuration)
		intent = PaymentIntent(
			intent_id=_intent_id(invoice_ref),
			rail_key=adapter.key,
			recipient=address,
			amount_native=invoiced_native,
			created_at_epoch=_epoch(charged_at),
			expires_at_epoch=_epoch(charged_at) + RATE_LOCK_SECONDS,
			payment_reference=invoice_ref,
			baseline=baseline,
		)
		request = adapter.create_request(intent)
	except CryptoPosError as exception:
		frappe.throw(str(exception), title=_("Cannot request payment on this rail"))
	uri = request.uri

	sale = frappe.new_doc("Crypto Sale")
	sale.update(
		{
			"state": "idle",
			"mode": mode,
			# Provenance is stamped from the transport that ACTUALLY answers.
			# Nothing has answered yet, so it stays empty until the first
			# heartbeat says who replied. Presuming REAL here would be the
			# exact overclaim this field exists to prevent.
			"provenance": "",
			"charged_at": charged_at,
			"rail_key": rail.name,
			"merchant_name": settings.merchant_name,
			"chain_reference": settings.chain_reference,
			"usd_cents": usd_cents,
			"invoiced_native": str(invoiced_native),
			"credited_native": "0",
			"sighted_native": "0",
			"rate_microcents": rate_microcents,
			"rate_source": rate_source if feed_answered else f"{rate_source} (no feed answered)",
			"rate_at": rate_at,
			"rate_lock_end": add_to_date(charged_at, seconds=RATE_LOCK_SECONDS),
			"identity_address": address,
			"identity_source": identity_source,
			"binding": binding,
			# The intent, written once and never again, exactly like every
			# other snapshot on this record. The watcher rebuilds it from
			# here rather than re-deriving it, because a baseline re-read on
			# a later heartbeat would be a different chain position and would
			# quietly re-attribute money that arrived in between.
			"identity_extras": json.dumps(
				{
					"endpoint": endpoint,
					# Snapshotted for the same reason the endpoint is: a sale is
					# observed in the world it was charged in. Repointing the row
					# at a different component mid-flight must not re-attribute
					# money that has already arrived.
					"payment_component": (rail.get("payment_component") or "").strip(),
					"gate": rail.gate_for(mode),
					"catalog_key": adapter.key,
					# WHICH IMPLEMENTATION, not just which money. `catalog_key`
					# names one asset on one chain; it does not name the code
					# that attributes and settles payments on it, and a rail
					# plugin can be replaced under a key that never changes.
					# The watcher compares this before it trusts an
					# observation -- see DECISIONS.md D30.
					"adapter": catalog.adapter_identity(adapter.key),
					"intent": catalog.intent_to_record(intent),
					"payer_notice": request.payer_notice,
				}
			),
			"uri": uri,
			"qr_modules": json.dumps(qr.modules_for(uri)),
			"invoice_id": _invoice_id(charged_at),
			"invoice_ref": invoice_ref,
			"loyalty_earn_rate": settings.loyalty_earn_rate or 0,
			"loyalty_account": loyalty_account or "",
		}
	)
	sale.insert(ignore_permissions=True)

	sale.transition_to(
		"awaiting",
		source="charge",
		detail=_("armed on {0}; gate is {1}").format(endpoint or "no endpoint", rail.gate_text),
	)
	sale.save(ignore_permissions=True)
	return sale
