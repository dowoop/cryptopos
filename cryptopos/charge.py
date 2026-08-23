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
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from cryptopos import rates
from cryptopos_core import qr

RATE_LOCK_SECONDS = 15 * 60

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


def _bitcoin_uri(address, sats, decimals):
	whole = (Decimal(sats) / (Decimal(10) ** decimals)).normalize()
	return f"bitcoin:{address}?amount={whole:f}"


URI_BUILDERS = {"bitcoin": _bitcoin_uri}


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

	# Where the money goes, and whose it is. Stated positively -- "the
	# operator configured this" -- because the negative form was defeated
	# once already by editing four version bytes.
	address = (settings.btc_testnet_address or "").strip() if rail.family == "bitcoin" else ""
	if address:
		identity_source = "config"
	else:
		identity_source = "none"

	if identity_source == "none" and mode != "demo":
		frappe.throw(
			_(
				"No receiving address is configured for {0}. A sale charged now "
				"would watch an address nobody holds the keys to."
			).format(rail.label),
			title=_("No receiving material"),
		)

	rate_microcents, rate_source, feed_answered = rates.quote(rail.asset, mode)
	rate_at = now_datetime()

	invoiced_native = rates.native_for(usd_cents, rate_microcents, rail.native_decimals)
	if invoiced_native <= 0:
		frappe.throw(_("That amount rounds to zero {0}.").format(rail.unit_name))

	charged_at = now_datetime()

	builder = URI_BUILDERS.get(rail.family)
	if builder is None:
		frappe.throw(_("No URI builder for rail family {0}.").format(rail.family))
	uri = builder(address, invoiced_native, rail.native_decimals)

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
			"binding": "shared" if identity_source == "config" else "",
			"identity_extras": json.dumps({"endpoint": endpoint, "gate": rail.gate_for(mode)}),
			"uri": uri,
			"qr_modules": json.dumps(qr.modules_for(uri)),
			"invoice_id": _invoice_id(charged_at),
			"invoice_ref": _invoice_ref(),
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
