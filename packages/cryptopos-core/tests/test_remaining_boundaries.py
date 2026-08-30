"""Focused refusal tests for the smaller adapters and host-side checks."""

import io
import unittest
from decimal import Decimal, InvalidOperation
from unittest import mock

from cryptopos_core import addresses, chain, rates
from cryptopos_core.catalog import RequestRail, monero_stagenet, polygon_amoy
from cryptopos_core.conformance import conformance_issues, require_conformant
from cryptopos_core.errors import (
	AddressRefused,
	InvalidRailPlugin,
	RailProviderError,
	RateUnavailable,
	UnsupportedCapability,
)
from cryptopos_core.ootle import OotleEsmeralda
from cryptopos_core.plugin import (
	ADDRESS_VALIDATION,
	Asset,
	Network,
	ObservationBatch,
	PaymentIntent,
	Readiness,
	RecipientBaseline,
)
from cryptopos_core.registry import RailRegistry, validate_plugin


class RequestRailBoundaries(unittest.TestCase):
	def setUp(self):
		self.rail = RequestRail(
			"fixture",
			Network("fixture", "testnet", True),
			Asset("native", "coin", "COIN", 6),
			binding_category="not-unconditional",
			request_ready=True,
			address_validation_ready=False,
			blocker="validation unavailable",
		)
		self.intent = PaymentIntent("sale", self.rail.key, "recipient", 10, 100, 200)

	def test_unready_validation_refuses_instead_of_guessing(self):
		self.assertEqual(monero_stagenet.validate_recipient("anything")[0], "refused")
		with self.assertRaises(AddressRefused):
			self.rail.create_request(self.intent)

	def test_request_rail_keeps_the_pre_category_constructor_compatible(self):
		legacy = RequestRail(
			"fixture",
			Network("fixture", "testnet", True),
			Asset("native", "coin", "COIN", 6),
			blocker="fixture",
		)
		self.assertEqual(legacy.binding_category, "not-unconditional")

	def test_request_only_rails_refuse_baseline_observation_and_settlement(self):
		for operation in (
			lambda: self.rail.capture_baseline("recipient", {}),
			lambda: self.rail.observe(self.intent, {}),
			lambda: self.rail.settle(self.intent, object()),
		):
			with self.subTest(operation=operation), self.assertRaises(UnsupportedCapability):
				operation()

	def test_a_rail_rejects_an_intent_that_is_not_one(self):
		# This asserted only `polygon_amoy` until 2026-08-24, when that rail
		# stopped being request-only -- and the assertion kept passing while
		# `RequestRail._intent` stopped being executed by anything, because the
		# full rail rejects foreign intents through its own code path. Both are
		# named here so neither can go dark again without a test going red.
		for rail in (self.rail, polygon_amoy):
			with self.subTest(rail=rail.key), self.assertRaises(InvalidRailPlugin):
				rail.create_request(object())


class ConformanceBoundaries(unittest.TestCase):
	class Plugin:
		binding_category = "not-unconditional"
		network = Network("fixture", "testnet", True)
		asset = Asset("native", "coin", "COIN", 6)
		key = f"{network.key}/{asset.key}"
		capabilities = frozenset({ADDRESS_VALIDATION})

		def readiness(self, configuration):
			return Readiness(self.key, self.capabilities)

		def capture_baseline(self, recipient, configuration):
			return None

		def validate_recipient(self, recipient):
			return "unchecked", "fixture"

		def create_request(self, intent):
			return None

		def observe(self, intent, configuration, previous=None):
			return None

		def settle(self, intent, observations, claimed_transaction_ids=frozenset()):
			return None

	def test_invalid_plugin_and_non_readiness_results_are_reported(self):
		self.assertTrue(conformance_issues(object(), {}))
		plugin = self.Plugin()
		plugin.readiness = lambda configuration: None
		self.assertEqual(conformance_issues(plugin, {}), ("readiness did not return a Readiness value",))

	def test_wrong_identity_and_overlapping_capabilities_are_reported(self):
		plugin = self.Plugin()
		plugin.readiness = lambda configuration: Readiness(
			"another-rail",
			frozenset({ADDRESS_VALIDATION}),
			((ADDRESS_VALIDATION, "also unavailable"),),
		)
		issues = conformance_issues(plugin, {})
		self.assertIn("another rail", issues[0])
		self.assertTrue(any("both ready" in issue for issue in issues))

	def test_require_conformant_returns_the_original_plugin(self):
		plugin = self.Plugin()
		self.assertIs(require_conformant(plugin, {}), plugin)


class RegistryBoundaries(unittest.TestCase):
	def plugin(self):
		return ConformanceBoundaries.Plugin()

	def test_registry_rejects_missing_protocol_and_core_identity_values(self):
		with self.assertRaises(InvalidRailPlugin):
			validate_plugin(object())
		plugin = self.plugin()
		plugin.network = object()
		with self.assertRaises(InvalidRailPlugin):
			validate_plugin(plugin)

	def test_registry_rejects_mutable_capabilities_and_uncallable_methods(self):
		plugin = self.plugin()
		plugin.capabilities = {ADDRESS_VALIDATION}
		with self.assertRaises(InvalidRailPlugin):
			validate_plugin(plugin)
		plugin = self.plugin()
		plugin.observe = None
		with self.assertRaises(InvalidRailPlugin):
			validate_plugin(plugin)

	def test_registry_rejects_methods_that_cannot_accept_host_call_shapes(self):
		plugin = self.plugin()
		plugin.observe = lambda intent, configuration: None
		with self.assertRaises(InvalidRailPlugin) as caught:
			validate_plugin(plugin)
		self.assertIn("call shape", caught.exception.reason)
		plugin = self.plugin()
		plugin.observe = lambda intent, configuration, previous: None
		with self.assertRaises(InvalidRailPlugin):
			validate_plugin(plugin)
		plugin = self.plugin()
		plugin.settle = lambda intent, observations, claimed: None
		with self.assertRaises(InvalidRailPlugin):
			validate_plugin(plugin)

	def test_discovery_calls_zero_argument_factories(self):
		class Point:
			def load(self):
				return lambda: ConformanceBoundaries.Plugin()

		class Points:
			def select(self, **selection):
				return (Point(),)

		with mock.patch("importlib.metadata.entry_points", return_value=Points()):
			loaded = RailRegistry().discover()
		self.assertEqual(loaded[0].key, ConformanceBoundaries.Plugin.key)

	def test_discovery_does_not_call_an_already_structural_callable_plugin(self):
		class CallablePlugin(ConformanceBoundaries.Plugin):
			def __call__(self):
				raise AssertionError("an already loaded plugin must not be called as a factory")

		plugin = CallablePlugin()

		class Point:
			def load(self):
				return plugin

		class Points:
			def select(self, **selection):
				return (Point(),)

		with mock.patch("importlib.metadata.entry_points", return_value=Points()):
			self.assertIs(RailRegistry().discover()[0], plugin)


class OotleBoundaries(unittest.TestCase):
	class Reader:
		def __init__(self, indexer="https://ootle.example", body=None, balance=100, reason=None):
			self.indexer = indexer
			self.body = {"network": "esmeralda", "epoch": 6} if body is None else body
			self.balance = balance
			self.reason = reason

		def _get(self, path):
			return self.body, self.reason

		def resource_balance(self, recipient, resource):
			return self.balance, self.reason

	def setUp(self):
		self.rail = OotleEsmeralda()
		self.assertEqual(self.rail.asset.decimals, 6)
		self.account = "component_" + "a" * 32
		self.baseline = RecipientBaseline(
			self.rail.key, self.account, "https://ootle.example", 5, balance_native=90
		)
		self.intent = PaymentIntent("sale", self.rail.key, self.account, 10, 100, 200, baseline=self.baseline)

	def test_invalid_account_and_missing_balance_baseline_are_refused(self):
		self.assertEqual(self.rail.validate_recipient(1)[0], "refused")
		with self.assertRaises(RailProviderError):
			self.rail.capture_baseline("bad", {})
		without_baseline = PaymentIntent("sale", self.rail.key, self.account, 10, 100, 200)
		with self.assertRaises(InvalidRailPlugin):
			self.rail.observe(without_baseline, {})

	def test_observation_revalidates_previous_provider_epoch_and_balance(self):
		previous = ObservationBatch(
			self.rail.key,
			"sale",
			self.account,
			"https://ootle.example",
			5,
			6,
			5,
			6,
			(),
			10,
		)
		with mock.patch.object(self.rail, "_reader", return_value=self.Reader()):
			observed = self.rail.observe(self.intent, {}, previous)
		self.assertEqual(observed.unattributed_native, 10)
		for reader in (
			self.Reader(indexer="https://other.example"),
			self.Reader(body={"network": "esmeralda", "epoch": 4}),
			self.Reader(balance=None, reason="unreadable"),
		):
			with self.subTest(reader=reader), mock.patch.object(self.rail, "_reader", return_value=reader):
				with self.assertRaises(RailProviderError):
					self.rail.observe(self.intent, {})

	def test_equal_epoch_and_unit_balance_deltas_have_distinct_meanings(self):
		for epoch, balance, warning in (
			(5, 90, None),
			(6, 91, "increased"),
			(6, 89, "fell"),
		):
			reader = self.Reader(body={"network": "esmeralda", "epoch": epoch}, balance=balance)
			with (
				self.subTest(epoch=epoch, balance=balance),
				mock.patch.object(self.rail, "_reader", return_value=reader),
			):
				observed = self.rail.observe(self.intent, {})
			if warning is None:
				self.assertEqual(observed.warnings, ())
			else:
				self.assertIn(warning, observed.warnings[0])

	def test_reader_and_network_inputs_are_strict(self):
		for configuration in (
			None,
			{},
			{"endpoint": "https://ootle.example", "timeout_seconds": 0},
		):
			with self.subTest(configuration=configuration), self.assertRaises(RailProviderError):
				self.rail._reader(configuration)
		for reader in (
			self.Reader(body=None, reason="offline"),
			self.Reader(body={"network": "esmeralda", "epoch": True}),
		):
			if reader.reason:
				reader.body = None
			with self.subTest(reader=reader), self.assertRaises(RailProviderError):
				self.rail._network(reader)
		with self.assertRaises(InvalidRailPlugin):
			self.rail._intent(object())
		with self.assertRaises(RailProviderError):
			self.rail._reader({"endpoint": 1})
		self.assertEqual(
			self.rail._reader({"endpoint": "https://ootle.example", "timeout_seconds": 30}).timeout,
			30,
		)
		self.assertEqual(
			self.rail._reader({"endpoint": "https://ootle.example", "timeout_seconds": 1}).timeout,
			1,
		)
		with self.assertRaises(RailProviderError):
			self.rail._reader({"endpoint": "https://ootle.example", "timeout_seconds": 30.0001})
		self.assertEqual(self.rail._network(self.Reader(body={"network": "esmeralda", "epoch": 0})), 0)
		with self.assertRaises(RailProviderError):
			self.rail._network(self.Reader(body={"network": "esmeralda", "epoch": -1}))


class ResidualDefensiveBranches(unittest.TestCase):
	def test_malformed_vault_mapping_returns_a_total_failure_reason(self):
		amount, reason = chain._balance_of({"substate": {"Vault": {"resource_container": {"Stealth": None}}}})
		self.assertIsNone(amount)
		self.assertIn("shape", reason)

	def test_price_significant_digit_ceiling_is_exercised(self):
		self.assertIsNone(rates._price_from("9" * 97))

	def test_address_and_price_size_boundaries_are_exact(self):
		self.assertEqual(addresses.MAX_ADDRESS_TEXT_LENGTH, 256)
		self.assertEqual(addresses.validate("unknown", "a" * 256, "testnet")[0], "unchecked")
		self.assertEqual(addresses.validate("unknown", "a" * 257, "testnet")[0], "refused")
		self.assertEqual(rates.MAX_PRICE_TEXT_LENGTH, 128)
		self.assertEqual(rates.MAX_PRICE_SIGNIFICANT_DIGITS, 64)
		self.assertEqual(rates._price_from("0" * 127 + "1"), Decimal(1))
		self.assertIsNone(rates._price_from("0" * 128 + "1"))
		accepted = "1." + "0" * 62 + "1"
		rejected = "1." + "0" * 63 + "1"
		self.assertEqual(len(Decimal(accepted).as_tuple().digits), 64)
		self.assertEqual(rates._price_from(accepted), Decimal(accepted))
		self.assertIsNone(rates._price_from(rejected))
		self.assertEqual(rates._price_from(rates.MIN_PRICE_USD), rates.MIN_PRICE_USD)
		self.assertEqual(rates._price_from(rates.MAX_PRICE_USD), rates.MAX_PRICE_USD)

	def test_resource_pair_walker_handles_the_resource_in_the_second_slot(self):
		pair = [{"value": {"hex": "vault"}}, {"value": {"hex": "resource"}}]
		self.assertEqual(chain._walk_for_resource(pair, "resource"), "vault_vault")

	def test_decimal_failures_are_normalized_at_both_consensus_boundaries(self):
		feeds = (("a", lambda asset: "100"), ("b", lambda asset: "101"))
		with (
			mock.patch.object(rates, "FEEDS", feeds),
			mock.patch.object(rates, "_spread", side_effect=InvalidOperation),
		):
			with self.assertRaises(RateUnavailable):
				rates.quote_detailed("btc", "testnet")

		class Unrepresentable:
			def __mul__(self, other):
				raise InvalidOperation

		with (
			mock.patch.object(rates, "FEEDS", feeds),
			mock.patch.object(rates, "_spread", return_value=0),
			mock.patch.object(rates, "_median", return_value=Unrepresentable()),
		):
			with self.assertRaises(RateUnavailable):
				rates.quote_detailed("btc", "testnet")

	def test_feed_reader_requests_exactly_one_byte_beyond_its_ceiling(self):
		class Response(io.BytesIO):
			read_limit = None

			def __enter__(self):
				return self

			def __exit__(self, *ignored):
				return False

			def read(self, limit=-1):
				self.read_limit = limit
				return super().read(limit)

		response = Response(b"{}")
		with mock.patch.object(rates, "_urlopen", return_value=response):
			self.assertEqual(rates._read_json("https://feed.example"), {})
		self.assertEqual(response.read_limit, rates.MAX_FEED_RESPONSE_BYTES + 1)


if __name__ == "__main__":
	unittest.main()
