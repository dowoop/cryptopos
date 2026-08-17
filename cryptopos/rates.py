"""Rates — a rate is not a number.

It is a number, a source, and a time, and all three ride the sale. A rate
read from a feed and a rate read from a hardcoded table are both usable and
are not the same claim, so the source is named rather than implied.

Microcents (cents x 10^4, i.e. USD x 10^6) because integer cents build the
error into the unit before any feed disagrees about anything: an asset
quoted at $0.07745 is 7.745 cents, which in integer cents is 8 -- a 3.3%
error on a cheap asset, which is exactly where a terminal handling more of
them must be more precise, not less.
"""

import json
import urllib.error
import urllib.request

import frappe

MICROCENTS_PER_USD = 1_000_000

# Used only when no feed answers. Named "demo-fixed" on the sale so nobody
# mistakes it for a quote anybody actually made.
DEMO_MICROCENTS = {
	"btc": 64_000 * MICROCENTS_PER_USD,
}

FEED_TIMEOUT_SECONDS = 6


def _coinbase(asset):
	url = f"https://api.coinbase.com/v2/prices/{asset.upper()}-USD/spot"
	with urllib.request.urlopen(url, timeout=FEED_TIMEOUT_SECONDS) as response:
		body = json.loads(response.read().decode("utf-8"))
	return float(body["data"]["amount"])


def _kraken(asset):
	pair = {"btc": "XBTUSD"}.get(asset.lower(), f"{asset.upper()}USD")
	url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
	with urllib.request.urlopen(url, timeout=FEED_TIMEOUT_SECONDS) as response:
		body = json.loads(response.read().decode("utf-8"))
	result = body["result"]
	first = next(iter(result.values()))
	return float(first["c"][0])


FEEDS = (("coinbase", _coinbase), ("kraken", _kraken))


def quote(asset, mode):
	"""Return (microcents_per_whole_coin, source, ok).

	`ok` is False when this is a fallback rather than a quote. The caller
	decides what to do about it; this function will not dress a fallback up
	as a feed answer.
	"""
	# A testnet coin has no price. Quoting the mainnet asset is the honest
	# approximation and the source says which asset was actually quoted.
	answered = []
	for name, fetch in FEEDS:
		try:
			answered.append((name, fetch(asset)))
		except (urllib.error.URLError, OSError, KeyError, ValueError, StopIteration):
			continue

	if answered:
		source = "+".join(name for name, _price in answered)
		average = sum(price for _name, price in answered) / len(answered)
		return int(round(average * MICROCENTS_PER_USD)), source, True

	fallback = DEMO_MICROCENTS.get(asset.lower())
	if fallback is None:
		frappe.throw(
			f"No feed answered for {asset} and no fallback rate exists for it.",
			title="Cannot price this sale",
		)
	return fallback, "demo-fixed", False


def native_for(usd_cents, rate_microcents, native_decimals):
	"""Convert an invoiced cent amount into exact native units.

	Integer arithmetic throughout. The result is THE amount: the URI, the
	display and every tolerance check derive from this one integer, so a
	rounding choice made here is made once rather than three times slightly
	differently.
	"""
	if rate_microcents <= 0:
		frappe.throw("A rate of zero cannot price a sale.")
	microcents = int(usd_cents) * 10_000
	return (microcents * (10**int(native_decimals))) // int(rate_microcents)
