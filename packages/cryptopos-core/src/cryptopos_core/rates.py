"""Rates — a rate is not a number.

It is a number, a source, and a time, and all three ride the sale. A rate
read from a feed and a rate read from a hardcoded table are both usable and
are not the same claim, so the source is named rather than implied.

Microcents (cents x 10^4, i.e. USD x 10^6) because integer cents build the
error into the unit before any feed disagrees about anything: an asset
quoted at $0.07745 is 7.745 cents, which in integer cents is 8 -- a 3.3%
error on a cheap asset, which is exactly where a terminal handling more of
them must be more precise, not less.

**Mainnet is priced under stricter rules than anything else, and the rules
are here rather than in a caller's head.** Three of them:

    no demo fallback        a hardcoded constant may price a demo. It may
                            never price real money. `DEMO_MICROCENTS` says
                            BTC is $64,000; on the day that is half the real
                            price, a merchant hands over goods at half price
                            and the terminal reports success.

    no lone feed            one endpoint answering is not corroboration. A
                            stale cache or a compromised host is indis-
                            tinguishable from a correct answer until a
                            second source agrees with it.

    no wide disagreement    feeds that disagree by more than
                            `MAX_FEED_SPREAD` mean at least one is wrong,
                            and nothing here can tell which. Refusing is the
                            only honest move.

All three raise `RateUnavailable` (or its subclass `FeedsDisagree`), so a
host that already catches `RateUnavailable` catches these too and needs no
change to stay safe.

Prices are parsed as `Decimal`, never `float`, and the honest reason is
narrower than "float is inaccurate". At today's magnitudes it is not: a price
of 64001.23456789 scaled to microcents is about 6.4e10, comfortably inside
the range float64 represents exactly, and the two agree. `Decimal` is here
because feeds publish DECIMAL STRINGS, `Decimal` is the type that holds one
exactly, and averaging several of them in binary floating point is where
drift would eventually appear. It removes the class of error rather than
fixing an observed wrong answer -- which is the right time to remove it.
"""

import concurrent.futures
import datetime
import json
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, DecimalException, InvalidOperation

from .errors import FeedsDisagree, InvalidAmount, InvalidAsset, InvalidRate, RateUnavailable, _coerce_integer
from .modes import REAL_MONEY_MODES, require_mode

MICROCENTS_PER_USD = 1_000_000

# Used only when no feed answers, and NEVER in mainnet. Named "demo-fixed" on
# the sale so nobody mistakes it for a quote anybody actually made.
DEMO_MICROCENTS = {
	"btc": 64_000 * MICROCENTS_PER_USD,
	# XTR, at the $0.05 `rails.py` picked. It is a NUMBER SOMEBODY CHOSE and
	# not a price anybody quoted, which is exactly what this table is for and
	# why it can never be reached in a real-money mode.
	#
	# Tari is listed on none of the feeds here -- re-measured 2026-08-28 by
	# `live_tari_watch.py`, verdict "NOTHING CHANGED", Coinbase answering 404
	# for XTM-USD -- so without an entry the rail raises `RateUnavailable` and
	# cannot be charged at all, even on a testnet where nothing is at stake.
	#
	# It is keyed `xtr` rather than `xtm` because `charge()` asks with the
	# Crypto Rail ROW's asset (D26: the row, never the frozen table), and the
	# row says XTR. `rails.price_asset` exists to say XTR should be priced as
	# XTM and no caller consults it; when a feed does list Tari, that gap is
	# what has to close before this entry can be deleted.
	"xtr": 5 * MICROCENTS_PER_USD // 100,
}

FEED_TIMEOUT_SECONDS = 6

# Vendor payloads are a few hundred bytes. A compromised or broken endpoint
# must not be able to make a point-of-sale process buffer an unbounded body.
MAX_FEED_RESPONSE_BYTES = 64 * 1024

# A vendor price is tiny compared with the response ceiling. Bound its textual
# and numeric shape before Decimal arithmetic: values such as ``1e999999999``
# are finite Decimals but overflow the active context during spread
# calculation, allowing one hostile feed to cancel two healthy answers.
MAX_PRICE_TEXT_LENGTH = 128
MAX_PRICE_SIGNIFICANT_DIGITS = 64
MIN_PRICE_USD = Decimal("1e-18")
MAX_PRICE_USD = Decimal("1e15")

# Modes where a fabricated or uncorroborated price is not acceptable. A tuple
# rather than `== "mainnet"` scattered through the module: the day another
# real-money mode exists, this is the line that changes.
# Feeds normally agree to well within a percent. Two percent apart means one
# of them is stale, wrong, or answering about a different asset, and this
# module cannot tell which -- so on real money it refuses rather than picks.
MAX_FEED_SPREAD = Decimal("0.02")

# On real money, this many independent feeds must answer before a price is
# usable. One feed is a single point of failure holding the price of a sale.
MIN_FEEDS_FOR_REAL_MONEY = 2


def _price_from(raw):
	"""Whatever a feed handed back -> Decimal, exactly. None if unusable."""
	try:
		# str() first on purpose: Decimal(float) would faithfully preserve the
		# float's error, which is precisely what we are trying not to inherit.
		text = str(raw).strip()
		if not text or len(text) > MAX_PRICE_TEXT_LENGTH:
			return None
		price = Decimal(text)
		if not price.is_finite():
			return None
		if len(price.as_tuple().digits) > MAX_PRICE_SIGNIFICANT_DIGITS:
			return None
		return price if MIN_PRICE_USD <= price <= MAX_PRICE_USD else None
	except (InvalidOperation, ValueError, TypeError):
		return None


def _urlopen(url, timeout=None):
	"""The single seam through which this module reaches a feed.

	The three adapters below call this rather than `urllib.request.urlopen`
	directly, for the same reason `chain._urlopen` exists: one door is what
	makes "no test in this package touches a network" a property the suite
	can assert rather than a habit it hopes everyone keeps. A fourth feed
	added later inherits the property by calling this.
	"""
	global _OPENER
	if _OPENER is None:
		_OPENER = urllib.request.build_opener(_NoDowngradeRedirects())
	return _OPENER.open(url, timeout=timeout)


class _NoDowngradeRedirects(urllib.request.HTTPRedirectHandler):
	"""Follow redirects except when an HTTPS feed leaves HTTPS."""

	def redirect_request(self, request, fp, code, msg, headers, newurl):
		source_scheme = urllib.parse.urlsplit(request.full_url).scheme.lower()
		resolved_url = urllib.parse.urljoin(request.full_url, newurl)
		destination_scheme = urllib.parse.urlsplit(resolved_url).scheme.lower()
		if source_scheme == "https" and destination_scheme != "https":
			raise urllib.error.URLError(
				f"refusing a redirect from https to {destination_scheme} "
				f"({newurl}); a downgraded "
				f"price is not a trustworthy feed answer"
			)
		return super().redirect_request(request, fp, code, msg, headers, resolved_url)


_OPENER = None


def _read_json(url):
	"""GET `url` and parse it as JSON, under the feed timeout."""
	with _urlopen(url, timeout=FEED_TIMEOUT_SECONDS) as response:
		body = response.read(MAX_FEED_RESPONSE_BYTES + 1)
	if len(body) > MAX_FEED_RESPONSE_BYTES:
		raise ValueError(f"feed response exceeds {MAX_FEED_RESPONSE_BYTES} bytes")
	return json.loads(body.decode("utf-8"))


# Each adapter returns whatever the feed said, UNPARSED. `_price_from` is the
# one place a feed's answer becomes a Decimal, and it does that from the
# original string -- an adapter that helpfully returned float(...) here would
# bake in the binary rounding this module goes out of its way to avoid.
def _coinbase(asset):
	url = f"https://api.coinbase.com/v2/prices/{asset.upper()}-USD/spot"
	return _read_json(url)["data"]["amount"]


def _kraken(asset):
	# Kraken calls Bitcoin XBT, and answers under a pair name it chooses
	# rather than the one asked for -- hence reading the first value rather
	# than indexing by `pair`.
	pair = {"btc": "XBTUSD"}.get(asset.lower(), f"{asset.upper()}USD")
	url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
	result = _read_json(url)["result"]
	first = next(iter(result.values()))
	return first["c"][0]


def _bitstamp(asset):
	pair = f"{asset.lower()}usd"
	url = f"https://www.bitstamp.net/api/v2/ticker/{pair}/"
	return _read_json(url)["last"]


# Three, not two, and the third is not decoration. With two feeds a
# disagreement tells you something is wrong and nothing about which one; with
# three, the median is robust to one bad answer.
FEEDS = (("coinbase", _coinbase), ("kraken", _kraken), ("bitstamp", _bitstamp))


def _median(values):
	ordered = sorted(values)
	middle = len(ordered) // 2
	if len(ordered) % 2:
		return ordered[middle]
	return (ordered[middle - 1] + ordered[middle]) / 2


def _spread(values):
	"""Widest disagreement as a fraction of the middle value."""
	if len(values) < 2:
		return Decimal(0)
	middle = _median(values)
	if middle <= 0:
		return Decimal(0)
	return (max(values) - min(values)) / middle


def _fetch_price(feed, asset):
	"""One feed result, isolated so one broken adapter cannot cancel its peers."""
	name, fetch = feed
	try:
		price = _price_from(fetch(asset))
	except (urllib.error.URLError, OSError, KeyError, ValueError, TypeError, StopIteration, RecursionError):
		return None
	return (name, price) if price is not None else None


def _gather(asset):
	"""Ask every feed concurrently, preserving registration order."""
	if not FEEDS:
		return []
	# Each production adapter has its own six-second socket timeout. Asking
	# serially turned that into an 18-second worst case at the counter; the
	# slowest independent vendor, not their sum, should bound a quote.
	with concurrent.futures.ThreadPoolExecutor(max_workers=len(FEEDS)) as pool:
		results = pool.map(lambda feed: _fetch_price(feed, asset), FEEDS)
		return [answer for answer in results if answer is not None]


def quote_detailed(asset, mode):
	"""The full account of a quote: the number, who said so, and when.

	Returns a dict. `quote()` is this with three fields pulled out, and exists
	because a host that only wants the number should not have to know the
	shape of this one.

	Raises the same things `quote()` raises, under the same rules.
	"""
	require_mode(mode)
	if (
		not isinstance(asset, str)
		or not asset.strip()
		or any(
			not (character.isascii() and (character.isalnum() or character == "-"))
			for character in asset.strip()
		)
	):
		raise InvalidAsset(asset)
	asset = asset.strip()
	real_money = mode in REAL_MONEY_MODES
	answered = _gather(asset)
	prices = [price for _name, price in answered]
	taken_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

	if answered:
		try:
			spread = _spread(prices)
		except DecimalException:
			raise RateUnavailable(asset, "feed prices could not be compared safely") from None

		if real_money:
			if len(answered) < MIN_FEEDS_FOR_REAL_MONEY:
				raise RateUnavailable(
					asset,
					f"only {len(answered)} feed answered for {asset} and real money needs "
					f"{MIN_FEEDS_FOR_REAL_MONEY} that agree; a single uncorroborated price "
					f"is one stale cache away from mispricing the sale",
				)
			if spread > MAX_FEED_SPREAD:
				raise FeedsDisagree(asset, dict(answered), spread)

		# Median, not mean: with three feeds one bad answer moves the mean and
		# cannot move the median past its neighbours.
		middle = _median(prices)
		source = "+".join(name for name, _price in answered)
		try:
			microcents = int((middle * MICROCENTS_PER_USD).to_integral_value())
		except (DecimalException, ValueError, OverflowError):
			raise RateUnavailable(asset, "the agreed feed price cannot be represented safely") from None
		return {
			"microcents": microcents,
			"source": source,
			"ok": True,
			"taken_at": taken_at,
			"feeds": {name: str(price) for name, price in answered},
			"spread": str(spread),
			"fallback": False,
		}

	# Nothing answered.
	if real_money:
		raise RateUnavailable(
			asset,
			f"no feed answered for {asset}. There is a fallback rate and it is a DEMO "
			f"constant -- pricing real money from it would invent a number and call it "
			f"a quote",
		)

	fallback = DEMO_MICROCENTS.get(asset.lower())
	if fallback is None:
		raise RateUnavailable(asset)
	return {
		"microcents": fallback,
		"source": "demo-fixed",
		"ok": False,
		"taken_at": taken_at,
		"feeds": {},
		"spread": "0",
		"fallback": True,
	}


def quote(asset, mode):
	"""Return (microcents_per_whole_coin, source, ok).

	`ok` is False when this is a fallback rather than a quote. The caller
	decides what to do about it; this function will not dress a fallback up
	as a feed answer.

	Raises `RateUnavailable` when nothing usable was found -- see errors.py
	for why this one refuses to return a sentinel, and the module docstring
	for the three extra rules real-money modes are held to.
	"""
	detail = quote_detailed(asset, mode)
	return detail["microcents"], detail["source"], detail["ok"]


def native_for(usd_cents, rate_microcents, native_decimals):
	"""Convert an invoiced cent amount into exact native units.

	Integer arithmetic throughout. The result is THE amount: the URI, the
	display and every tolerance check derive from this one integer, so a
	rounding choice made here is made once rather than three times slightly
	differently.

	**This is the primitive, not the charge path.** It divides straight to
	native precision, which on a rail whose display precision is coarser
	produces an amount no decimal URI can state -- see
	`rails.is_exactly_displayable`. To price a sale, use
	`rails.invoice_amount`, which is guaranteed statable.
	"""
	normalized_rate = _coerce_integer(rate_microcents)
	if normalized_rate is None:
		raise InvalidRate(rate_microcents) from None
	rate_microcents = normalized_rate
	if rate_microcents <= 0:
		raise InvalidRate(rate_microcents)
	normalized_cents = _coerce_integer(usd_cents)
	if normalized_cents is None:
		raise InvalidAmount("usd_cents", usd_cents) from None
	usd_cents = normalized_cents
	if usd_cents <= 0:
		raise InvalidAmount("usd_cents", usd_cents)
	normalized_decimals = _coerce_integer(native_decimals)
	if normalized_decimals is None:
		raise InvalidAmount("native_decimals", native_decimals) from None
	native_decimals = normalized_decimals
	if native_decimals < 0:
		raise InvalidAmount("native_decimals", native_decimals)
	microcents = usd_cents * 10_000
	return (microcents * (10**native_decimals)) // rate_microcents
