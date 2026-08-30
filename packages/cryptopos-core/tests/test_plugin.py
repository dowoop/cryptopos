"""The installable rail contract: identity, capabilities, and discovery."""

import unittest
from unittest import mock

from cryptopos_core import plugin as plugin_module
from cryptopos_core.errors import DuplicateRail, InvalidAmount, InvalidRailPlugin, RailNotInstalled
from cryptopos_core.plugin import (
	ADDRESS_VALIDATION,
	OBSERVATION,
	PAYMENT_REQUEST,
	SETTLED,
	SETTLEMENT,
	Asset,
	Network,
	ObservationBatch,
	PaymentIntent,
	PaymentRequest,
	Readiness,
	RecipientBaseline,
	SettlementDecision,
	TransferObservation,
)
from cryptopos_core.registry import RailRegistry


class ExampleRail:
	binding_category = "unconditional-per-sale"
	network = Network("example", "testnet-1", True)
	asset = Asset("slip44", "999", "TST", 6)
	key = f"{network.key}/{asset.key}"
	capabilities = frozenset({ADDRESS_VALIDATION, PAYMENT_REQUEST, OBSERVATION, SETTLEMENT})

	def readiness(self, configuration):
		return Readiness(self.key, self.capabilities)

	def capture_baseline(self, recipient, configuration):
		return RecipientBaseline(self.key, recipient, "fixture", 7)

	def validate_recipient(self, recipient):
		return ("ok", "") if recipient == "merchant" else ("refused", "unknown recipient")

	def create_request(self, intent):
		return PaymentRequest(
			self.key,
			f"example:{intent.recipient}?amount={intent.amount_native}",
			intent.recipient,
			intent.amount_native,
		)

	def observe(self, intent, configuration, previous=None):
		return ObservationBatch(
			self.key,
			intent.intent_id,
			intent.recipient,
			"fixture",
			intent.baseline.tip,
			7,
			intent.baseline.tip,
			7,
			(),
		)

	def settle(self, intent, observations, claimed_transaction_ids=frozenset()):
		return SettlementDecision(SETTLED, intent.amount_native, intent.amount_native, ("tx",))


def legacy_rail():
	"""The published PaymentRail shape before binding categories existed."""
	attributes = {
		name: value
		for name, value in vars(ExampleRail).items()
		if not name.startswith("__") and name != "binding_category"
	}
	return type("LegacyRail", (), attributes)()


class Identity(unittest.TestCase):
	def test_networks_are_concrete_and_testnet_is_explicit(self):
		network = Network("bitcoin", "testnet4", True)
		self.assertEqual(network.key, "bitcoin:testnet4")
		self.assertTrue(network.is_testnet)

	def test_assets_have_network_qualified_identity_and_atomic_scale(self):
		asset = Asset("erc20", "0x1c7d4b196cb0c7b01d743fbc6116a902379c7238", "USDC", 6)
		self.assertEqual(asset.key, "erc20:0x1c7d4b196cb0c7b01d743fbc6116a902379c7238")
		self.assertEqual(asset.decimals, 6)

	def test_identifiers_refuse_ambiguous_or_structural_text(self):
		for reference in ("", "Testnet", "test/net", "testnet?mode=mainnet", "x" * 129):
			with self.subTest(reference=reference), self.assertRaises(InvalidRailPlugin):
				Network("bitcoin", reference, True)

	def test_asset_decimals_are_bounded(self):
		for decimals in (-1, 31, True, 1.5):
			with self.subTest(decimals=decimals), self.assertRaises(InvalidRailPlugin):
				Asset("native", "coin", "COIN", decimals)


class Intents(unittest.TestCase):
	def intent(self, **changes):
		values = {
			"intent_id": "sale-1",
			"rail_key": ExampleRail.key,
			"recipient": "merchant",
			"amount_native": 42,
			"created_at_epoch": 100,
			"expires_at_epoch": 200,
		}
		values.update(changes)
		return PaymentIntent(**values)

	def test_an_intent_is_framework_free_and_exact(self):
		intent = self.intent(amount_native="42")
		self.assertEqual(intent.amount_native, 42)
		self.assertEqual(intent.rail_key, ExampleRail.key)

	def test_nonpositive_or_lossy_amounts_are_refused(self):
		for amount in (0, -1, True, 1.5, "1.5"):
			with self.subTest(amount=amount), self.assertRaises(InvalidAmount):
				self.intent(amount_native=amount)

	def test_expiry_must_follow_creation(self):
		for expiry in (99, 100, True, "later"):
			with self.subTest(expiry=expiry), self.assertRaises(InvalidRailPlugin):
				self.intent(expires_at_epoch=expiry)

	def test_baseline_is_bound_to_the_intended_recipient(self):
		baseline = RecipientBaseline(ExampleRail.key, "someone-else", "fixture", 7)
		with self.assertRaises(InvalidRailPlugin):
			self.intent(baseline=baseline)


class ReadinessReport(unittest.TestCase):
	def test_chargeability_requires_the_whole_charge_path(self):
		complete = Readiness(ExampleRail.key, ExampleRail.capabilities)
		partial = Readiness(
			ExampleRail.key,
			frozenset({ADDRESS_VALIDATION, PAYMENT_REQUEST}),
			((OBSERVATION, "no provider configured"), (SETTLEMENT, "cannot observe")),
		)
		self.assertTrue(complete.chargeable)
		self.assertFalse(partial.chargeable)
		self.assertEqual(partial.reason_for(OBSERVATION), "no provider configured")

	def test_return_values_enforce_money_and_identity_invariants(self):
		with self.assertRaises(InvalidRailPlugin):
			PaymentRequest(ExampleRail.key, "example:merchant\namount=42", "merchant", 42)
		transfer = TransferObservation("tx", 42, True, 1, 7)
		with self.assertRaises(InvalidRailPlugin):
			ObservationBatch(
				ExampleRail.key,
				"sale-1",
				"merchant",
				"fixture",
				6,
				7,
				6,
				7,
				(transfer, transfer),
			)
		with self.assertRaises(InvalidRailPlugin):
			SettlementDecision(SETTLED, 43, 42, ("tx",))

	def test_bounded_observation_pages_accumulate_without_losing_binding(self):
		baseline = RecipientBaseline(ExampleRail.key, "merchant", "fixture", 5)
		payment = Intents().intent(baseline=baseline)
		first = ObservationBatch(
			ExampleRail.key,
			payment.intent_id,
			payment.recipient,
			"fixture",
			5,
			8,
			5,
			6,
			(TransferObservation("tx-1", 20, True, 3, 6, 120),),
		)
		page = ObservationBatch(
			ExampleRail.key,
			payment.intent_id,
			payment.recipient,
			"fixture",
			5,
			8,
			6,
			8,
			(TransferObservation("tx-2", 22, True, 2, 7, 130),),
		)
		combined = first.extend(page)
		self.assertTrue(combined.complete)
		self.assertEqual([transfer.transaction_id for transfer in combined.transfers], ["tx-1", "tx-2"])
		self.assertIs(combined.require_intent(payment), combined)


class Registry(unittest.TestCase):
	def test_register_and_resolve_a_plugin(self):
		registry = RailRegistry()
		plugin = registry.register(ExampleRail())
		self.assertIs(registry.get(ExampleRail.key), plugin)
		self.assertEqual(registry.keys(), (ExampleRail.key,))

	def test_duplicate_concrete_rail_is_refused(self):
		registry = RailRegistry()
		registry.register(ExampleRail())
		with self.assertRaises(DuplicateRail):
			registry.register(ExampleRail())

	def test_missing_rail_has_a_documented_error(self):
		with self.assertRaises(RailNotInstalled):
			RailRegistry().get("bitcoin:testnet4/native:btc")

	def test_wrong_key_or_unknown_capability_is_refused(self):
		for key, capabilities in (
			("wrong", ExampleRail.capabilities),
			(ExampleRail.key, frozenset({"teleport"})),
		):
			plugin = ExampleRail()
			plugin.key = key
			plugin.capabilities = capabilities
			with self.subTest(key=key, capabilities=capabilities), self.assertRaises(InvalidRailPlugin):
				RailRegistry().register(plugin)

	def test_unknown_binding_category_is_refused(self):
		for category in ("probably-per-sale", True):
			plugin = ExampleRail()
			plugin.binding_category = category
			with self.subTest(category=category), self.assertRaises(InvalidRailPlugin):
				RailRegistry().register(plugin)

	def test_a_published_plugin_without_the_new_field_stays_driveable_and_defaults_safely(self):
		legacy = legacy_rail()
		registry = RailRegistry()
		self.assertIs(registry.register(legacy), legacy)
		self.assertEqual(plugin_module.binding_category_for(legacy), "not-unconditional")

	def test_discovery_accepts_a_published_plugin_without_the_new_field(self):
		legacy = legacy_rail()

		class Point:
			def load(self):
				return legacy

		class Points:
			def select(self, **selection):
				return (Point(),)

		with mock.patch("importlib.metadata.entry_points", return_value=Points()):
			self.assertIs(RailRegistry().discover()[0], legacy)

	def test_discovery_loads_an_installed_plugin(self):
		class Point:
			def load(self):
				return ExampleRail()

		class Points:
			def select(self, **selection):
				self.selection = selection
				return (Point(),)

		points = Points()
		registry = RailRegistry()
		with mock.patch("importlib.metadata.entry_points", return_value=points):
			loaded = registry.discover()
		self.assertEqual(points.selection, {"group": "cryptopos.rails"})
		self.assertEqual(loaded[0].key, ExampleRail.key)


if __name__ == "__main__":
	unittest.main()
