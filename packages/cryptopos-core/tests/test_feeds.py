"""The three feed adapters — the only code in this package that names a vendor.

Until this file existed these three functions were the largest unproven
surface here: every other test stubbed `rates.FEEDS` wholesale, which proves
the *policy* around a price and nothing at all about the code that goes and
gets one. What was never checked was the part most likely to be wrong and
least likely to be noticed — the URL, and the path through each vendor's
JSON.

Three things make that worth a file of its own:

    the URL       a wrong path returns 404, `_gather` swallows it as an
                  unanswered feed, and on a non-mainnet mode the sale prices
                  from the demo constant instead. Nothing anywhere says a
                  feed has been silently dead for a month.

    the shape     each vendor nests the number somewhere different, and
                  Kraken nests it under a key it picks rather than the one
                  asked for.

    the type      every adapter must hand back what the vendor SAID -- a
                  string -- because `_price_from` builds the Decimal from it.
                  An adapter that returned a float would reintroduce exactly
                  the binary rounding this module documents avoiding.

Nothing here reaches a network: `rates._urlopen` is the single door, and it
is patched for every test in the file.
"""

import io
import json
import unittest
import urllib.error
from decimal import Decimal
from unittest import mock

from cryptopos_core import rates

# Captured shapes, trimmed to the keys the adapters actually read.
COINBASE_BODY = {"data": {"base": "BTC", "currency": "USD", "amount": "64001.23"}}
KRAKEN_BODY = {
	"error": [],
	# Kraken answers under XXBTZUSD even though the request said XBTUSD.
	"result": {"XXBTZUSD": {"c": ["64010.70000", "0.00110000"], "v": ["1", "2"]}},
}
BITSTAMP_BODY = {"last": "63998.00", "high": "64500.00", "pair": "BTC/USD"}


class _Response(io.BytesIO):
	def __enter__(self):
		return self

	def __exit__(self, *exc):
		self.close()
		return False


class FakeDoor:
	"""Stands in for `rates._urlopen`, recording what was asked for."""

	def __init__(self, body=None, error=None):
		self.body = body
		self.error = error
		self.urls = []
		self.timeouts = []

	def __call__(self, url, timeout=None):
		self.urls.append(url)
		self.timeouts.append(timeout)
		if self.error is not None:
			raise self.error
		payload = self.body if isinstance(self.body, (bytes, str)) else json.dumps(self.body)
		if isinstance(payload, str):
			payload = payload.encode("utf-8")
		return _Response(payload)

	def patch(self):
		return mock.patch("cryptopos_core.rates._urlopen", self)


def door(body=None, error=None):
	"""A stand-in for the one door, ready to be installed with `.patch()`."""
	return FakeDoor(body, error)


class ResponseBounds(unittest.TestCase):
	def test_the_response_ceiling_is_sixty_four_kibibytes(self):
		self.assertEqual(rates.MAX_FEED_RESPONSE_BYTES, 65_536)

	def test_an_oversized_feed_response_is_refused_before_json_decoding(self):
		fake = door(b" " * (rates.MAX_FEED_RESPONSE_BYTES + 1))
		with fake.patch(), self.assertRaisesRegex(ValueError, "exceeds"):
			rates._read_json("https://feed.example/price")

	def test_the_largest_permitted_response_is_still_read(self):
		body = b"{}" + b" " * (rates.MAX_FEED_RESPONSE_BYTES - 2)
		fake = door(body)
		with fake.patch():
			self.assertEqual(rates._read_json("https://feed.example/price"), {})


class Coinbase(unittest.TestCase):
	def test_reads_the_spot_price(self):
		fake = door(COINBASE_BODY)
		with fake.patch():
			self.assertEqual(rates._coinbase("btc"), "64001.23")

	def test_asks_the_documented_url(self):
		fake = door(COINBASE_BODY)
		with fake.patch():
			rates._coinbase("btc")
		self.assertEqual(fake.urls, ["https://api.coinbase.com/v2/prices/BTC-USD/spot"])

	def test_upcases_the_asset(self):
		# The rails table keys assets lowercase; Coinbase's path is uppercase.
		fake = door(COINBASE_BODY)
		with fake.patch():
			rates._coinbase("eth")
		self.assertIn("/ETH-USD/", fake.urls[0])

	def test_answers_a_string_not_a_float(self):
		fake = door(COINBASE_BODY)
		with fake.patch():
			self.assertIsInstance(rates._coinbase("btc"), str)


class Kraken(unittest.TestCase):
	def test_reads_the_last_trade_price(self):
		# `c` is [last trade price, lot volume]; the price is [0].
		fake = door(KRAKEN_BODY)
		with fake.patch():
			self.assertEqual(rates._kraken("btc"), "64010.70000")

	def test_calls_bitcoin_xbt(self):
		# Kraken is the only feed here that does not call it BTC. Asking for
		# BTCUSD returns an error body with an empty result, which `_gather`
		# would swallow as "did not answer" -- a feed silently absent.
		fake = door(KRAKEN_BODY)
		with fake.patch():
			rates._kraken("btc")
		self.assertIn("pair=XBTUSD", fake.urls[0])

	def test_other_assets_keep_their_ticker(self):
		fake = door(KRAKEN_BODY)
		with fake.patch():
			rates._kraken("eth")
		self.assertIn("pair=ETHUSD", fake.urls[0])

	def test_reads_the_key_kraken_chose_not_the_one_asked_for(self):
		# The request says XBTUSD and the answer is keyed XXBTZUSD. Indexing
		# by the requested pair would raise KeyError on every single call.
		fake = door(KRAKEN_BODY)
		with fake.patch():
			self.assertEqual(rates._kraken("btc"), "64010.70000")

	def test_an_empty_result_is_survivable(self):
		# Kraken reports a bad pair as {"error": [...], "result": {}}, and
		# `next(iter({}.values()))` raises StopIteration -- which is neither
		# an OSError nor a KeyError. `_gather` names it explicitly; this is
		# the test that says why that entry is there.
		fake = door({"error": ["EQuery:Unknown asset pair"], "result": {}})
		with fake.patch():
			with self.assertRaises(StopIteration):
				rates._kraken("nope")

	def test_an_empty_result_is_swallowed_by_gather(self):
		fake = door({"error": ["EQuery:Unknown asset pair"], "result": {}})
		with fake.patch():
			with mock.patch.object(rates, "FEEDS", (("kraken", rates._kraken),)):
				self.assertEqual(rates._gather("nope"), [])


class Bitstamp(unittest.TestCase):
	def test_reads_the_last_price(self):
		fake = door(BITSTAMP_BODY)
		with fake.patch():
			self.assertEqual(rates._bitstamp("btc"), "63998.00")

	def test_asks_a_lowercase_pair_with_the_trailing_slash(self):
		# Bitstamp 404s without the trailing slash.
		fake = door(BITSTAMP_BODY)
		with fake.patch():
			rates._bitstamp("BTC")
		self.assertEqual(fake.urls, ["https://www.bitstamp.net/api/v2/ticker/btcusd/"])


# Every adapter's captured body, keyed by the name it is registered under, so
# the shared properties below can be asserted over all three by iterating
# `rates.FEEDS` itself -- a fourth feed added without a fixture fails loudly
# rather than being skipped.
BODIES = {
	"coinbase": COINBASE_BODY,
	"kraken": KRAKEN_BODY,
	"bitstamp": BITSTAMP_BODY,
}


class EveryAdapter(unittest.TestCase):
	"""Properties all three must share, asserted over all three."""

	def test_all_three_are_registered_in_feeds(self):
		self.assertEqual([name for name, _fetch in rates.FEEDS], ["coinbase", "kraken", "bitstamp"])

	def test_each_answers_a_string_that_price_from_accepts(self):
		for name, fetch in rates.FEEDS:
			with self.subTest(feed=name):
				fake = door(BODIES[name])
				with fake.patch():
					raw = fetch("btc")
				self.assertIsInstance(raw, str, f"{name} must hand back the vendor's own string")
				self.assertIsInstance(rates._price_from(raw), Decimal)

	def test_each_carries_the_feed_timeout(self):
		# Without a timeout a hung vendor hangs the sale. The default socket
		# timeout is None, which means forever.
		for name, fetch in rates.FEEDS:
			with self.subTest(feed=name):
				fake = door(BODIES[name])
				with fake.patch():
					fetch("btc")
				self.assertEqual(fake.timeouts, [rates.FEED_TIMEOUT_SECONDS])

	def test_each_survives_a_dead_host_through_gather(self):
		for name, fetch in rates.FEEDS:
			with self.subTest(feed=name):
				fake = door(error=urllib.error.URLError("no route to host"))
				with fake.patch(), mock.patch.object(rates, "FEEDS", ((name, fetch),)):
					self.assertEqual(rates._gather("btc"), [])

	def test_each_survives_a_body_that_is_not_json(self):
		for name, fetch in rates.FEEDS:
			with self.subTest(feed=name):
				fake = door(b"<html>maintenance</html>")
				with fake.patch(), mock.patch.object(rates, "FEEDS", ((name, fetch),)):
					self.assertEqual(rates._gather("btc"), [])

	def test_each_survives_a_shape_change(self):
		# A vendor that reorganises its JSON must cost this build one absent
		# feed, never an exception on the charge path.
		for name, fetch in rates.FEEDS:
			with self.subTest(feed=name):
				fake = door({"unexpected": "shape"})
				with fake.patch(), mock.patch.object(rates, "FEEDS", ((name, fetch),)):
					self.assertEqual(rates._gather("btc"), [])

	def test_three_real_adapters_reach_a_quote_together(self):
		# The whole path with nothing stubbed but the door: three vendors,
		# three shapes, one median. Coinbase 64001.23, Kraken 64010.70,
		# Bitstamp 63998.00 -> the median is Coinbase's.
		def route(url, timeout=None):
			body = COINBASE_BODY if "coinbase" in url else KRAKEN_BODY if "kraken" in url else BITSTAMP_BODY
			return _Response(json.dumps(body).encode("utf-8"))

		with mock.patch("cryptopos_core.rates._urlopen", route):
			microcents, source, ok = rates.quote("btc", "mainnet")
		self.assertEqual(source, "coinbase+kraken+bitstamp")
		self.assertTrue(ok)
		self.assertEqual(microcents, 64_001_230_000)


class TheDoor(unittest.TestCase):
	"""One seam, so "this suite touches no network" is checkable."""

	def setUp(self):
		self.saved = rates._OPENER
		rates._OPENER = None

	def tearDown(self):
		rates._OPENER = self.saved

	def test_every_adapter_goes_through_it(self):
		# If a fourth feed is added that calls urllib directly, this fails:
		# the patched door records nothing and the body is never read.
		for name, fetch in rates.FEEDS:
			with self.subTest(feed=name):
				fake = door(BODIES[name])
				with fake.patch():
					fetch("btc")
				self.assertEqual(len(fake.urls), 1, f"{name} did not go through rates._urlopen")

	def test_it_passes_the_timeout_through_to_the_protected_opener(self):
		captured = {}

		class FakeOpener:
			def open(self, url, timeout=None):
				captured["url"] = url
				captured["timeout"] = timeout
				return _Response(b"{}")

		with mock.patch("urllib.request.build_opener", lambda *handlers: FakeOpener()):
			rates._urlopen("https://example.invalid/x", timeout=3)
		self.assertEqual(captured, {"url": "https://example.invalid/x", "timeout": 3})

	def test_it_installs_the_no_downgrade_handler(self):
		installed = []

		class FakeOpener:
			def open(self, url, timeout=None):
				return _Response(b"{}")

		def build(*handlers):
			installed.extend(handlers)
			return FakeOpener()

		with mock.patch("urllib.request.build_opener", build):
			rates._urlopen("https://example.invalid/x")
		self.assertTrue(any(isinstance(handler, rates._NoDowngradeRedirects) for handler in installed))

	def test_a_redirect_from_https_to_http_is_refused(self):
		handler = rates._NoDowngradeRedirects()
		request = urllib.request.Request("https://feed.example/price")
		with self.assertRaises(urllib.error.URLError):
			handler.redirect_request(request, None, 302, "Found", {}, "http://evil.example/price")

	def test_redirect_schemes_are_normalized_and_only_https_is_allowed(self):
		handler = rates._NoDowngradeRedirects()
		request = urllib.request.Request("https://feed.example/price")
		for target in ("HTTP://evil.example/price", "ftp://evil.example/price"):
			with self.subTest(target=target), self.assertRaises(urllib.error.URLError):
				handler.redirect_request(request, None, 302, "Found", {}, target)

	def test_a_relative_redirect_inherits_https_and_is_allowed(self):
		handler = rates._NoDowngradeRedirects()
		request = urllib.request.Request("https://feed.example/price")
		redirected = handler.redirect_request(request, io.BytesIO(b""), 302, "Found", {}, "/other-price")
		self.assertEqual(redirected.full_url, "https://feed.example/other-price")

	def test_an_https_redirect_remains_urllibs_decision(self):
		handler = rates._NoDowngradeRedirects()
		request = urllib.request.Request("https://feed.example/price")
		redirected = handler.redirect_request(
			request, io.BytesIO(b""), 302, "Found", {}, "https://other.example/price"
		)
		self.assertEqual(redirected.full_url, "https://other.example/price")


class Median(unittest.TestCase):
	"""`_median` and `_spread` on the inputs `_gather` can actually produce."""

	def test_an_odd_count_takes_the_middle(self):
		self.assertEqual(rates._median([Decimal(1), Decimal(9), Decimal(2)]), Decimal(2))

	def test_an_even_count_averages_the_two_middles(self):
		values = [Decimal(1), Decimal(2), Decimal(3), Decimal(10)]
		self.assertEqual(rates._median(values), Decimal("2.5"))

	def test_a_lone_feed_has_no_spread(self):
		self.assertEqual(rates._spread([Decimal(64000)]), Decimal(0))

	def test_no_feeds_have_no_spread(self):
		# Unreachable through `quote_detailed`, which checks `if answered`
		# first -- but `_spread` is the function that would have to be right
		# if that guard were ever moved, and a zero-length sort must not
		# raise here.
		self.assertEqual(rates._spread([]), Decimal(0))

	def test_a_sub_dollar_median_still_has_a_spread(self):
		# The guard is `middle <= 0`, and it must not creep upward: a cheap
		# asset priced at 50-70 cents has a real 33% disagreement, and
		# reporting zero there would let two feeds that flatly contradict each
		# other price real money.
		spread = rates._spread([Decimal("0.5"), Decimal("0.7")])
		self.assertGreater(spread, Decimal("0.3"))

	def test_a_nonpositive_middle_has_no_spread(self):
		# Guards a division by zero. `_price_from` rejects non-positive
		# prices, so this is defence in depth rather than a live path.
		self.assertEqual(rates._spread([Decimal(0), Decimal(0)]), Decimal(0))


if __name__ == "__main__":
	unittest.main()
