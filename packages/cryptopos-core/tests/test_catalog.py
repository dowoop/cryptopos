"""The built-in catalog states breadth without inflating readiness."""

import unittest

from cryptopos_core import rails
from cryptopos_core.catalog import (
	BUILTIN_RAILS,
	dash_testnet,
	ethereum_sepolia,
	minotari_esmeralda,
	monero_stagenet,
	ootle_esmeralda,
	polygon_amoy,
	solana_devnet,
	usdc_ethereum_sepolia,
	usdc_polygon_amoy,
	usdc_solana_devnet,
	zcash_testnet,
)
from cryptopos_core.errors import UnsupportedCapability
from cryptopos_core.plugin import (
	ADDRESS_VALIDATION,
	CHARGE_CAPABILITIES,
	OBSERVATION,
	PAYMENT_REQUEST,
	SETTLEMENT,
	PaymentIntent,
	RecipientBaseline,
)
from cryptopos_core.registry import RailRegistry

EVM = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
SOL = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
REFERENCE = "Fk9GjsPFVc7fB8kdEqf6bBLLnPFbYK2VoBEHkYqHqQyi"
TARI = "12HVCEeZQhPauMqdDzV4nYZt67FB8fuRrUWFg8RbY7F7D8FyQh8"
DASH = "yVLSEDNiUf9KAPYLn86HLtBaTPzAhDfksR"
ZCASH = "tmFJH9WpMiH4tC3agcY8qt7zUa2Jw3y9RZK"


def intent(rail, recipient, amount, reference="", baseline=None):
	return PaymentIntent("sale-1", rail.key, recipient, amount, 100, 200, reference, baseline)


class CatalogIdentity(unittest.TestCase):
	def test_all_twelve_legacy_rails_have_concrete_test_network_entries(self):
		self.assertEqual(len(BUILTIN_RAILS), 12)
		self.assertEqual(len({rail.key for rail in BUILTIN_RAILS}), 12)
		self.assertTrue(all(rail.network.is_testnet for rail in BUILTIN_RAILS))

	def test_registry_accepts_every_builtin(self):
		registry = RailRegistry()
		loaded = registry.register_builtins()
		self.assertEqual(len(loaded), 12)
		self.assertEqual(set(registry.keys()), {rail.key for rail in BUILTIN_RAILS})

	def test_every_builtin_declares_the_legacy_rail_binding_category(self):
		legacy = (
			"btc",
			"eth",
			"usdc-eth",
			"pol",
			"usdc-pol",
			"sol",
			"usdc-sol",
			"xmr",
			"xtm",
			"xtr",
			"dash",
			"zec",
		)
		self.assertEqual(
			[rail.binding_category for rail in BUILTIN_RAILS],
			[rails.RAILS[key]["binding_category"] for key in legacy],
		)

	def test_only_the_extracted_observers_declare_the_complete_charge_path(self):
		complete = [rail.key for rail in BUILTIN_RAILS if CHARGE_CAPABILITIES <= rail.capabilities]
		self.assertEqual(
			complete,
			[
				"bitcoin:testnet4/native:btc",
				"ethereum:sepolia/native:eth",
				"ethereum:sepolia/erc20:0x1c7d4b196cb0c7b01d743fbc6116a902379c7238",
				# Native POL joined this list on 2026-08-24. It had been a
				# `RequestRail` blocked on "the observer has not been extracted",
				# and the extraction turned out to be composition: Sepolia's
				# native path plus Amoy's finalized-block gate, both already here.
				"polygon:amoy/native:pol",
				"polygon:amoy/erc20:0x41e94eb019c0762f9bfcf9fb1e58725bfb0e7582",
			],
		)

	def test_case_sensitive_solana_mint_survives_asset_identity(self):
		self.assertIn("4zMMC9", usdc_solana_devnet.asset.reference)
		self.assertIn(usdc_solana_devnet.asset.reference, usdc_solana_devnet.key)

	def test_chargeable_asset_atomic_scales_are_pinned(self):
		"""The scale of a rail that can actually take money.

		Native POL was pinned only in the request-only test above until
		2026-08-24. When it became chargeable it left that list, and its scale
		stopped being asserted anywhere -- a mutation of `18` to `19` survived
		the whole suite. A wrong exponent here misprices every sale on the rail
		by a factor of ten, so it is pinned where the rail now lives.
		"""
		self.assertEqual(
			{
				rail.key: (rail.asset.decimals, rail.asset.symbol)
				for rail in (
					ethereum_sepolia,
					usdc_ethereum_sepolia,
					polygon_amoy,
					usdc_polygon_amoy,
				)
			},
			{
				"ethereum:sepolia/native:eth": (18, "SepoliaETH"),
				"ethereum:sepolia/erc20:0x1c7d4b196cb0c7b01d743fbc6116a902379c7238": (6, "USDC"),
				"polygon:amoy/native:pol": (18, "AmoyPOL"),
				"polygon:amoy/erc20:0x41e94eb019c0762f9bfcf9fb1e58725bfb0e7582": (6, "USDC"),
			},
		)

	def test_request_only_asset_atomic_scales_are_pinned(self):
		self.assertEqual(
			{
				rail.key: rail.asset.decimals
				for rail in (
					solana_devnet,
					usdc_solana_devnet,
					monero_stagenet,
					minotari_esmeralda,
					dash_testnet,
					zcash_testnet,
				)
			},
			{
				"solana:devnet/native:sol": 9,
				"solana:devnet/spl:4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU": 6,
				"monero:stagenet/native:xmr": 12,
				"minotari:esmeralda/native:xtm": 6,
				"dash:testnet/native:dash": 8,
				"zcash:testnet/native:zec": 8,
			},
		)


class RequestAdapters(unittest.TestCase):
	def test_evm_native_and_token_requests_carry_sepolia_identity(self):
		native_baseline = RecipientBaseline(ethereum_sepolia.key, EVM, "https://rpc.example", 100)
		token_baseline = RecipientBaseline(usdc_ethereum_sepolia.key, EVM, "https://rpc.example", 100)
		native = ethereum_sepolia.create_request(
			intent(ethereum_sepolia, EVM, 10**15, baseline=native_baseline)
		)
		token = usdc_ethereum_sepolia.create_request(
			intent(usdc_ethereum_sepolia, EVM, 6_250_000, baseline=token_baseline)
		)
		self.assertIn("@11155111?value=", native.uri)
		self.assertIn(usdc_ethereum_sepolia.asset.reference, token.uri.lower())

	def test_polygon_token_request_carries_amoy_chain_and_contract(self):
		baseline = RecipientBaseline(usdc_polygon_amoy.key, EVM, "https://rpc.example", 100)
		request = usdc_polygon_amoy.create_request(
			intent(usdc_polygon_amoy, EVM, 6_250_000, baseline=baseline)
		)
		self.assertIn("@80002/transfer", request.uri)
		self.assertIn(usdc_polygon_amoy.asset.reference, request.uri.lower())

	def test_solana_request_requires_and_carries_the_sale_reference(self):
		request = solana_devnet.create_request(intent(solana_devnet, SOL, 1_000_000, REFERENCE))
		self.assertIn(f"reference={REFERENCE}", request.uri)

	def test_non_observing_rails_refuse_observation_instead_of_simulating_it(self):
		for rail in (solana_devnet, dash_testnet, zcash_testnet):
			with self.subTest(rail=rail.key), self.assertRaises(UnsupportedCapability):
				rail.observe(intent(rail, EVM, 1), {})

	def test_minotari_request_is_esmeralda_bound(self):
		request = minotari_esmeralda.create_request(intent(minotari_esmeralda, TARI, 1_500_000))
		self.assertTrue(request.uri.startswith("tari://esmeralda/"))

	def test_unimplemented_payment_schemes_are_explicitly_unavailable(self):
		for rail in (monero_stagenet, ootle_esmeralda):
			with self.subTest(rail=rail.key):
				self.assertNotIn(PAYMENT_REQUEST, rail.capabilities)
				self.assertTrue(rail.readiness({}).reason_for(PAYMENT_REQUEST))
				with self.assertRaises(UnsupportedCapability):
					rail.create_request(intent(rail, TARI, 1_000_000))

	def test_monero_does_not_claim_stagenet_validation_before_it_exists(self):
		self.assertNotIn(ADDRESS_VALIDATION, monero_stagenet.capabilities)
		readiness = monero_stagenet.readiness({})
		self.assertIn("stagenet", readiness.reason_for(ADDRESS_VALIDATION).lower())

	def test_every_partial_rail_explains_why_observation_is_unavailable(self):
		for rail in BUILTIN_RAILS[1:]:
			with self.subTest(rail=rail.key):
				readiness = rail.readiness({})
				self.assertFalse(readiness.chargeable)
				self.assertTrue(readiness.reason_for(OBSERVATION))

	def test_the_earliest_blocked_step_is_reported_first(self):
		"""`unavailable` is ordered, and the order is the order a sale would hit.

		`RequestRail.readiness` inserts at position 0 twice, so a rail that can
		do nothing reports payment-request before address-validation before
		observation before settlement -- the sequence a cashier would meet them
		in. Nothing asserted that until 2026-08-24, when both inserts survived
		mutation to position 1: the list still held the same four reasons, in an
		order that no longer told the operator which wall they hit first.

		Monero is the only built-in missing both, so it is the only rail that
		exercises both inserts.
		"""
		self.assertEqual(
			[capability for capability, _ in monero_stagenet.readiness({}).unavailable],
			[PAYMENT_REQUEST, ADDRESS_VALIDATION, OBSERVATION, SETTLEMENT],
		)


if __name__ == "__main__":
	unittest.main()
