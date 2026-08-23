"""The built-in catalog states breadth without inflating readiness."""

import unittest

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

	def test_only_the_extracted_observers_declare_the_complete_charge_path(self):
		complete = [rail.key for rail in BUILTIN_RAILS if CHARGE_CAPABILITIES <= rail.capabilities]
		self.assertEqual(
			complete,
			[
				"bitcoin:testnet4/native:btc",
				"ethereum:sepolia/native:eth",
				"ethereum:sepolia/erc20:0x1c7d4b196cb0c7b01d743fbc6116a902379c7238",
				"polygon:amoy/erc20:0x41e94eb019c0762f9bfcf9fb1e58725bfb0e7582",
			],
		)

	def test_case_sensitive_solana_mint_survives_asset_identity(self):
		self.assertIn("4zMMC9", usdc_solana_devnet.asset.reference)
		self.assertIn(usdc_solana_devnet.asset.reference, usdc_solana_devnet.key)

	def test_request_only_asset_atomic_scales_are_pinned(self):
		self.assertEqual(
			{
				rail.key: rail.asset.decimals
				for rail in (
					polygon_amoy,
					solana_devnet,
					usdc_solana_devnet,
					monero_stagenet,
					minotari_esmeralda,
					dash_testnet,
					zcash_testnet,
				)
			},
			{
				"polygon:amoy/native:pol": 18,
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


if __name__ == "__main__":
	unittest.main()
