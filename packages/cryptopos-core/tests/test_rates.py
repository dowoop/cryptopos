"""Rates: the money path, and the two things it refuses to do.

The feeds are stubbed in every test here. A test suite that reaches Coinbase
is a test suite that fails on a train, and worse, one whose failure says
nothing about the code.
"""

import threading
import time
import unittest
import urllib.error
from decimal import Decimal

from cryptopos_core import rails, rates
from cryptopos_core.errors import (
	CryptoPosError,
	FeedsDisagree,
	InvalidAmount,
	InvalidAsset,
	InvalidMode,
	InvalidRate,
	RateUnavailable,
)


class StubbedFeeds:
	"""Swap `rates.FEEDS` for the duration of a block."""

	def __init__(self, *feeds):
		self.feeds = list(feeds)

	def __enter__(self):
		self.saved = rates.FEEDS
		rates.FEEDS = self.feeds
		return self

	def __exit__(self, *exc):
		rates.FEEDS = self.saved
		return False


def answers(price):
	return lambda asset: price


def refuses(exception):
	def fetch(asset):
		raise exception

	return fetch


class NativeForArithmetic(unittest.TestCase):
	"""The one integer every other number on a sale derives from."""

	def test_known_conversion(self):
		# $640.01234 per whole coin, 8 decimals, $10.99 sale.
		# Floored, not rounded: the exact quotient is 1_717_154.86...
		self.assertEqual(rates.native_for(1099, 640_012_340, 8), 1_717_154)

	def test_is_pure_integer_arithmetic(self):
		# No float anywhere in the path: the result of a big, awkward
		# conversion must be exactly the floor of the rational value, not
		# whatever a double happened to round to.
		usd_cents, rate, decimals = 999_999, 64_001_234_000, 18
		expected = (usd_cents * 10_000 * 10**decimals) // rate
		got = rates.native_for(usd_cents, rate, decimals)
		self.assertIsInstance(got, int)
		self.assertEqual(got, expected)

	def test_floors_rather_than_rounds(self):
		# Floor, not round: rounding up would invoice for native units the
		# cent amount does not cover, and the difference shows up as a sale
		# that can never settle within tolerance.
		self.assertEqual(rates.native_for(1, 10_000 * 3, 0), 0)
		self.assertEqual(rates.native_for(1, 10_000, 0), 1)

	def test_zero_decimals(self):
		self.assertEqual(rates.native_for(500, 10_000, 0), 500)

	def test_accepts_numeric_strings(self):
		# Doc fields arrive as strings from a host's form layer.
		self.assertEqual(
			rates.native_for("1099", 640_012_340, "8"),
			rates.native_for(1099, 640_012_340, 8),
		)


class NativeForRefusals(unittest.TestCase):
	def test_nonpositive_sale_amount_is_refused(self):
		for value in (0, -1, "0", 1.9, True, None):
			with self.subTest(value=value):
				with self.assertRaises(InvalidAmount):
					rates.native_for(value, 1, 8)

	def test_negative_native_precision_is_refused(self):
		for decimals in (-1, "not-a-precision"):
			with self.subTest(decimals=decimals):
				with self.assertRaises(InvalidAmount):
					rates.native_for(1099, 1, decimals)

	def test_a_malformed_rate_uses_the_documented_error(self):
		for rate in ("not-a-rate", 1.9, True):
			with self.subTest(rate=rate):
				with self.assertRaises(InvalidRate):
					rates.native_for(1099, rate, 8)

	def test_zero_rate_raises(self):
		with self.assertRaises(InvalidRate):
			rates.native_for(1099, 0, 8)

	def test_negative_rate_raises(self):
		with self.assertRaises(InvalidRate):
			rates.native_for(1099, -1, 8)

	def test_carries_the_offending_rate(self):
		# A host translating this into a message should not have to parse one.
		with self.assertRaises(InvalidRate) as caught:
			rates.native_for(1099, -7, 8)
		self.assertEqual(caught.exception.rate_microcents, -7)

	def test_amount_error_carries_its_boundary_and_wording(self):
		with self.assertRaises(InvalidAmount) as caught:
			rates.native_for(0, 1, 8)
		self.assertEqual(caught.exception.minimum, 1)
		self.assertEqual(str(caught.exception), "usd_cents must be a positive integer; got 0.")

	def test_is_catchable_as_the_base_error(self):
		# The documented way for a host to catch everything this package
		# raises without importing each name.
		with self.assertRaises(CryptoPosError):
			rates.native_for(1099, 0, 8)

	def test_the_smallest_positive_rate_is_usable(self):
		# Zero is refused; one microcent is not. The guard is `<= 0`, and a
		# `<= 1` typo would refuse the cheapest asset this scale exists to
		# price -- which is the direction the module docstring cares about.
		self.assertEqual(rates.native_for(1, 1, 0), 10_000)


class Quote(unittest.TestCase):
	"""Feed handling in a mode where a fallback is legitimate. The stricter
	rules real money is held to are in `RealMoneyRules` below."""

	def test_an_unknown_mode_is_refused_before_any_feed_is_called(self):
		called = []

		def feed(_asset):
			called.append(True)
			return "1"

		with StubbedFeeds(("feed", feed)):
			with self.assertRaises(InvalidMode):
				rates.quote("btc", "maintnet")
		self.assertEqual(called, [])

	def test_an_invalid_asset_is_refused_before_any_feed_is_called(self):
		for asset in (None, 7, "", "   ", "btc/usd", "btc?amount=1", "βtc"):
			with self.subTest(asset=asset):
				with StubbedFeeds(("feed", lambda _asset: self.fail("feed was called"))):
					with self.assertRaises(InvalidAsset) as caught:
						rates.quote(asset, "testnet")
				self.assertEqual(caught.exception.asset, asset)

	def test_asset_text_is_trimmed_before_feeds_and_fallback_lookup(self):
		seen = []

		def feed(asset):
			seen.append(asset)
			raise OSError("down")

		with StubbedFeeds(("dead", feed)):
			microcents, _source, ok = rates.quote("  BTC  ", "demo")
		self.assertEqual(seen, ["BTC"])
		self.assertEqual(microcents, rates.DEMO_MICROCENTS["btc"])
		self.assertFalse(ok)

	def test_feeds_are_asked_concurrently_and_reported_in_registration_order(self):
		lock = threading.Lock()
		active = 0
		most_active = 0

		def concurrent_answer(price):
			def fetch(_asset):
				nonlocal active, most_active
				with lock:
					active += 1
					most_active = max(most_active, active)
				time.sleep(0.02)
				with lock:
					active -= 1
				return price

			return fetch

		with StubbedFeeds(
			("first", concurrent_answer("1")),
			("second", concurrent_answer("2")),
			("third", concurrent_answer("3")),
		):
			answered = rates._gather("btc")
		self.assertGreaterEqual(most_active, 2)
		self.assertEqual([name for name, _price in answered], ["first", "second", "third"])

	def test_an_empty_feed_registry_is_an_empty_answer_set(self):
		with StubbedFeeds():
			self.assertEqual(rates._gather("btc"), [])

	def test_averages_every_feed_that_answered(self):
		with StubbedFeeds(("a", answers(100.0)), ("b", answers(200.0))):
			microcents, source, ok = rates.quote("btc", "testnet")
		self.assertEqual(microcents, 150 * rates.MICROCENTS_PER_USD)
		self.assertEqual(source, "a+b")
		self.assertTrue(ok)

	def test_names_only_the_feeds_that_answered(self):
		# Provenance is what actually replied. A feed that timed out has not
		# endorsed the number and must not appear beside it.
		with StubbedFeeds(("a", answers(100.0)), ("dead", refuses(urllib.error.URLError("down")))):
			microcents, source, ok = rates.quote("btc", "testnet")
		self.assertEqual(source, "a")
		self.assertEqual(microcents, 100 * rates.MICROCENTS_PER_USD)
		self.assertTrue(ok)

	def test_survives_every_way_a_feed_breaks(self):
		broken = [
			urllib.error.URLError("no route"),
			OSError("connection reset"),
			KeyError("data"),
			ValueError("not json"),
			StopIteration(),
		]
		for exception in broken:
			with self.subTest(exception=type(exception).__name__):
				with StubbedFeeds(("bad", refuses(exception)), ("good", answers(50.0))):
					microcents, source, _ok = rates.quote("btc", "testnet")
				self.assertEqual(source, "good")
				self.assertEqual(microcents, 50 * rates.MICROCENTS_PER_USD)

	def test_falls_back_without_claiming_a_feed_answered(self):
		# The whole point of the third return value: a fallback is usable and
		# is not a quote, and nothing here dresses one up as the other.
		with StubbedFeeds(("dead", refuses(OSError("down")))):
			microcents, source, ok = rates.quote("btc", "testnet")
		self.assertEqual(microcents, rates.DEMO_MICROCENTS["btc"])
		self.assertEqual(source, "demo-fixed")
		self.assertFalse(ok)

	def test_refuses_when_nothing_answers_and_nothing_covers_the_asset(self):
		with StubbedFeeds(("dead", refuses(OSError("down")))):
			with self.assertRaises(RateUnavailable) as caught:
				rates.quote("nosuchcoin", "testnet")
		self.assertEqual(caught.exception.asset, "nosuchcoin")

	def test_fallback_lookup_is_case_insensitive(self):
		with StubbedFeeds(("dead", refuses(OSError("down")))):
			microcents, _source, ok = rates.quote("BTC", "demo")
		self.assertEqual(microcents, rates.DEMO_MICROCENTS["btc"])
		self.assertFalse(ok)

	def test_returns_an_integer_number_of_microcents(self):
		# A fractional microcent would defeat the unit's entire purpose.
		with StubbedFeeds(("a", answers(0.07745))):
			microcents, _source, _ok = rates.quote("btc", "testnet")
		self.assertIsInstance(microcents, int)
		self.assertEqual(microcents, 77_450)


class Microcents(unittest.TestCase):
	def test_the_precision_claim_in_the_docstring(self):
		# $0.07745 is 7.745 cents. In integer cents that is 8 -- a 3.3% error
		# baked into the unit. Microcents carry it exactly, and that is the
		# entire argument for the unit.
		exact = rates.native_for(100, 77_450, 8)
		coarse = rates.native_for(100, 8 * 10_000, 8)
		self.assertNotEqual(exact, coarse)
		self.assertGreater(abs(exact - coarse) / exact, 0.03)


if __name__ == "__main__":
	unittest.main()


class Constants(unittest.TestCase):
	"""The numbers themselves, pinned.

	Every other test in this file reads these constants to build its
	expectation, which makes the assertions self-referential: change the
	constant and the test changes with it. That is exactly the shape of test
	that goes green while the code is wrong, so the values are stated here as
	literals, once.
	"""

	def test_the_scale_is_microcents(self):
		# cents x 10^4, i.e. USD x 10^6. An asset at $0.07745 is 7.745 cents,
		# which in integer cents is 8 -- a 3.3% error built into the unit.
		self.assertEqual(rates.MICROCENTS_PER_USD, 1_000_000)

	def test_the_demo_rate_is_the_documented_sixty_four_thousand(self):
		self.assertEqual(rates.DEMO_MICROCENTS["btc"], 64_000_000_000)
		self.assertEqual(rates.DEMO_MICROCENTS["btc"] // rates.MICROCENTS_PER_USD, 64_000)

	def test_the_demo_table_covers_only_assets_no_feed_will_price(self):
		"""btc and xtr, and the reason they are both here is different.

		Until 2026-08-31 this asserted `{"btc"}` and said every other rail
		raises rather than inventing a price -- which is still the safe
		direction and still true of every asset absent from this table.

		`xtr` was added because Tari is listed on NONE of the feeds this build
		reads. `live_tari_watch.py` re-measured it on 2026-08-28 and returned
		"NOTHING CHANGED", with Coinbase answering 404 for XTM-USD. Without an
		entry the rail raises `RateUnavailable` and cannot be charged at all,
		even on a testnet where nothing is at stake -- so the choice was a
		picked number or no Ootle rail.

		A picked number is safe HERE and nowhere else, and that is enforced
		rather than promised: `REAL_MONEY_MODES` can never reach this table,
		and a quote drawn from it comes back `ok=False`, sourced `demo-fixed`.
		The day two feeds list Tari, this entry should be deleted rather than
		corrected.
		"""
		self.assertEqual(set(rates.DEMO_MICROCENTS), {"btc", "xtr"})

	def test_the_xtr_demo_rate_is_the_five_cents_the_rail_table_picked(self):
		self.assertEqual(rates.DEMO_MICROCENTS["xtr"], 50_000)
		self.assertEqual(rails.RAILS["xtr"]["rate_cents"], 5)
		self.assertEqual(
			rates.DEMO_MICROCENTS["xtr"],
			rails.RAILS["xtr"]["rate_cents"] * rates.MICROCENTS_PER_USD // 100,
			"the demo price and the rail table's picked price must not drift apart",
		)

	def test_real_money_needs_two_feeds(self):
		self.assertEqual(rates.MIN_FEEDS_FOR_REAL_MONEY, 2)

	def test_the_spread_limit_is_two_percent(self):
		self.assertEqual(rates.MAX_FEED_SPREAD, Decimal("0.02"))

	def test_the_feed_timeout_is_six_seconds(self):
		# Stated as a literal on purpose. `test_feeds` asserts that each
		# adapter passes FEED_TIMEOUT_SECONDS through, which proves the wiring
		# and says nothing about the number -- so the number is pinned here.
		self.assertEqual(rates.FEED_TIMEOUT_SECONDS, 6)

	def test_mainnet_is_the_only_real_money_mode(self):
		self.assertEqual(rates.REAL_MONEY_MODES, ("mainnet",))


class RealMoneyRules(unittest.TestCase):
	"""Mainnet is priced under three extra rules, and each one exists because
	the alternative silently misprices a real sale.

	All three raise `RateUnavailable` or its subclass, so a host that already
	catches `RateUnavailable` refuses correctly without being changed. The
	safe behaviour is the one you get by doing nothing.
	"""

	def test_a_demo_constant_may_never_price_real_money(self):
		# DEMO_MICROCENTS says BTC is $64,000. On the day that is half the
		# real price, this fallback hands over goods at half price and the
		# terminal reports success.
		with StubbedFeeds(("dead", refuses(OSError("down")))):
			with self.assertRaises(RateUnavailable) as caught:
				rates.quote("btc", "mainnet")
		self.assertIn("DEMO", str(caught.exception))

	def test_the_same_fallback_is_still_fine_off_mainnet(self):
		with StubbedFeeds(("dead", refuses(OSError("down")))):
			microcents, source, ok = rates.quote("btc", "testnet")
		self.assertEqual(microcents, rates.DEMO_MICROCENTS["btc"])
		self.assertEqual(source, "demo-fixed")
		self.assertFalse(ok)

	def test_one_feed_is_not_corroboration(self):
		# A stale cache and a correct answer are indistinguishable until a
		# second source agrees.
		with StubbedFeeds(("a", answers("64000")), ("dead", refuses(OSError("down")))):
			with self.assertRaises(RateUnavailable) as caught:
				rates.quote("btc", "mainnet")
		self.assertIn("only 1 feed", str(caught.exception))

	def test_two_agreeing_feeds_are_enough(self):
		with StubbedFeeds(("a", answers("64000")), ("b", answers("64100"))):
			microcents, source, ok = rates.quote("btc", "mainnet")
		self.assertEqual(source, "a+b")
		self.assertTrue(ok)
		self.assertEqual(microcents, 64_050 * rates.MICROCENTS_PER_USD)

	def test_the_spread_limit_is_a_ceiling_and_not_a_bar(self):
		# Exactly at MAX_FEED_SPREAD is ALLOWED, and the boundary is the whole
		# question: 99 and 101 have a median of 100 and a spread of exactly
		# 0.02, which is the documented limit. Refusing here would refuse a
		# sale the rule says is priceable, and a `>=` typo does precisely that
		# while every other test in this class stays green.
		with StubbedFeeds(("a", answers("99")), ("b", answers("101"))):
			microcents, _source, ok = rates.quote("btc", "mainnet")
		self.assertTrue(ok)
		self.assertEqual(microcents, 100 * rates.MICROCENTS_PER_USD)

	def test_a_hair_over_the_limit_is_refused(self):
		# The other side of the same boundary, so the pair pins it from both
		# directions rather than asserting one and hoping.
		with StubbedFeeds(("a", answers("98.9")), ("b", answers("101.1"))):
			with self.assertRaises(FeedsDisagree):
				rates.quote("btc", "mainnet")

	def test_feeds_that_disagree_widely_are_refused(self):
		with StubbedFeeds(("a", answers("64000")), ("b", answers("32000"))):
			with self.assertRaises(FeedsDisagree) as caught:
				rates.quote("btc", "mainnet")
		self.assertEqual(caught.exception.asset, "btc")
		self.assertIn("a", caught.exception.prices)
		self.assertIn("b", caught.exception.prices)

	def test_disagreement_is_catchable_as_rate_unavailable(self):
		# The compatibility property: a host catching the general error keeps
		# refusing correctly without knowing this subclass exists.
		with StubbedFeeds(("a", answers("64000")), ("b", answers("32000"))):
			with self.assertRaises(RateUnavailable):
				rates.quote("btc", "mainnet")

	def test_a_spread_inside_tolerance_is_accepted(self):
		# Feeds normally differ slightly and refusing on that would refuse
		# every sale. One percent apart is normal; two is the line.
		with StubbedFeeds(("a", answers("64000")), ("b", answers("64300"))):
			_microcents, _source, ok = rates.quote("btc", "mainnet")
		self.assertTrue(ok)

	def test_the_median_resists_one_bad_answer(self):
		# Three feeds, one wrong by a factor of two. The median sits between
		# the two that agree, where a mean would be dragged 17% off.
		with StubbedFeeds(("a", answers("64000")), ("b", answers("64100")), ("c", answers("64050"))):
			microcents, _source, _ok = rates.quote("btc", "mainnet")
		self.assertEqual(microcents, 64_050 * rates.MICROCENTS_PER_USD)


class QuoteDetail(unittest.TestCase):
	def test_a_quote_carries_a_number_a_source_and_a_time(self):
		# The docstring's claim, asserted: a rate is not a number.
		with StubbedFeeds(("a", answers("64000")), ("b", answers("64100"))):
			detail = rates.quote_detailed("btc", "mainnet")
		self.assertEqual(detail["microcents"], 64_050 * rates.MICROCENTS_PER_USD)
		self.assertEqual(detail["source"], "a+b")
		self.assertTrue(detail["taken_at"])
		self.assertFalse(detail["fallback"])
		self.assertEqual(detail["feeds"], {"a": "64000", "b": "64100"})

	def test_it_reports_the_spread_it_measured(self):
		with StubbedFeeds(("a", answers("100")), ("b", answers("101"))):
			detail = rates.quote_detailed("btc", "testnet")
		self.assertEqual(Decimal(detail["spread"]), Decimal(1) / Decimal("100.5"))

	def test_a_fallback_is_labelled_as_one(self):
		with StubbedFeeds(("dead", refuses(OSError("down")))):
			detail = rates.quote_detailed("btc", "demo")
		self.assertTrue(detail["fallback"])
		self.assertFalse(detail["ok"])
		self.assertEqual(detail["feeds"], {})

	def test_prices_are_parsed_exactly_from_their_decimal_strings(self):
		with StubbedFeeds(("a", answers("64001.23456789")), ("b", answers("64001.23456789"))):
			detail = rates.quote_detailed("btc", "mainnet")
		self.assertEqual(detail["feeds"]["a"], "64001.23456789")
		self.assertEqual(detail["microcents"], 64_001_234_568)

	def test_a_feed_answering_nonsense_is_dropped_not_trusted(self):
		for junk in ("", "not-a-number", "-5", "0", "NaN", "Infinity", "-Infinity", None):
			with self.subTest(value=repr(junk)):
				with StubbedFeeds(("junk", answers(junk)), ("good", answers("64000"))):
					detail = rates.quote_detailed("btc", "testnet")
				self.assertEqual(detail["source"], "good")

	def test_extreme_finite_decimals_are_dropped_before_consensus_math(self):
		for hostile in ("1e999999999", "9" * 129, "1e16", "1e-19"):
			with self.subTest(hostile=hostile[:20]):
				with StubbedFeeds(
					("hostile", answers(hostile)),
					("good-a", answers("64000")),
					("good-b", answers("64001")),
				):
					detail = rates.quote_detailed("btc", "mainnet")
				self.assertEqual(detail["source"], "good-a+good-b")
				self.assertEqual(set(detail["feeds"]), {"good-a", "good-b"})

	def test_a_hostile_feed_never_leaks_a_decimal_exception(self):
		with StubbedFeeds(
			("hostile", answers("1e999999999")),
			("good-a", answers("64000")),
			("good-b", answers("64001")),
		):
			try:
				rates.quote("btc", "mainnet")
			except Exception as exception:  # pragma: no cover - assertion reports the leaked type
				self.fail(f"hostile finite Decimal leaked {type(exception).__name__}: {exception}")
