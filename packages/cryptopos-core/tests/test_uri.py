"""Payment URIs: the exact strings, because the string is what gets scanned.

These are pinned character by character rather than parsed and re-checked. A
URI that is "equivalent" to the right one is not the right one — the wallet
reading it does not negotiate, and a scheme, a separator or an amount form
that drifted is a payment that either fails at the counter or goes somewhere
real on the wrong network.

**Every address in this file is checksum-valid for the network it is used
with**, because `build_uri` verifies them now. The Bitcoin, Ethereum and
Monero ones are published test vectors; the Dash and Zcash ones were built by
re-encoding the Bitcoin genesis hash160 under those coins' version bytes, a
construction `test_addresses` proves correct by round-tripping genesis itself.
"""

import unittest

from cryptopos_core import modes, rails, uri
from cryptopos_core.errors import (
	AddressRefused,
	AmountNotRepresentable,
	CryptoPosError,
	InvalidAmount,
	InvalidMode,
	InvalidPaymentIdentity,
	UnsupportedRail,
)

# Bitcoin: BIP-173 / BIP-350 published vectors.
BTC_MAIN = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
BTC_TEST = "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx"
BTC_TAPROOT = "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"
BTC_LEGACY = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"

# EVM: the EIP-55 specification's own vectors. Same address on every EVM
# chain -- an EVM address encodes no network, which is why the mode cannot be
# checked for these and the chain id in the URI is doing all the work.
EVM = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"

# Solana: a well-formed 32-byte ed25519 key.
SOL = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
REF = "Fk9GjsPFVc7fB8kdEqf6bBLLnPFbYK2VoBEHkYqHqQyi"

DASH_MAIN = "XjhqDGJH37VEpecoDGmtJrmEB7VoD8Lb39"
DASH_TEST = "yVLSEDNiUf9KAPYLn86HLtBaTPzAhDfksR"
ZEC_MAIN = "t1StbPM4X3j4FGM57HpGnb9BMbS7C1nFW1r"
ZEC_TEST = "tmJjLiBYvSPZkQbGYxYaXSor7CRC1RjQEff"
XMR_MAIN = "44AFFq5kSiGBoZ4NMDwYtN18obc8AemS33DBLWs3H7otXft3XjrpDtQGv7SqSsaBYBb98uNbr2VBBEt7f2wfn3RVGQBEP3A"
XMR_TEST = "9wHThniAcyWbCFAqMLaVdCZHRgR97PtuDcUBSpu2RMCKj8RXp6e4iSSTiqWgo6Di6dgKRZZn3n1cccUYhcWGE8w53gxAQMY"

# Tari has no checkable address format, so any string is "unchecked".
TARI = "anythinggoeshere"

TESTNET_ADDRESSES = {
	"btc": BTC_TEST,
	"dash": DASH_TEST,
	"zec": ZEC_TEST,
	"xmr": XMR_TEST,
	"eth": EVM,
	"pol": EVM,
	"usdc-eth": EVM,
	"usdc-pol": EVM,
	"sol": SOL,
	"usdc-sol": SOL,
	"xtm": TARI,
	"xtr": TARI,
}


def rail(key):
	return rails.RAILS[key]


def identity(address):
	return {"address": address, "reference": REF, "memo": "aGk"}


class Bitcoin(unittest.TestCase):
	def test_bip21_decimal_amount_at_eight_places(self):
		# BIP-21: decimal BTC, 8dp, period separator, no commas.
		built = uri.build_uri("btc", identity(BTC_TEST), 195_300, "testnet")
		self.assertEqual(built, f"bitcoin:{BTC_TEST}?amount=0.00195300")

	def test_mainnet_uri_for_a_mainnet_address(self):
		built = uri.build_uri("btc", identity(BTC_MAIN), 195_300, "mainnet")
		self.assertEqual(built, f"bitcoin:{BTC_MAIN}?amount=0.00195300")

	def test_taproot_and_legacy_addresses_are_accepted(self):
		for address in (BTC_TAPROOT, BTC_LEGACY):
			with self.subTest(address=address[:12]):
				built = uri.build_uri("btc", identity(address), 195_300, "mainnet")
				self.assertIn(address, built)

	def test_dash_uses_the_same_shape_under_its_own_scheme(self):
		built = uri.build_uri("dash", identity(DASH_TEST), 195_300, "testnet")
		self.assertEqual(built, f"dash:{DASH_TEST}?amount=0.00195300")

	def test_dash_never_asks_for_instantsend(self):
		# req-IS=1 voids the URI. Locking is automatic on Dash; there is
		# nothing to ask for, and asking breaks the payment.
		built = uri.build_uri("dash", identity(DASH_MAIN), 195_300, "mainnet")
		self.assertNotIn("req-IS", built)


class Ethereum(unittest.TestCase):
	def test_native_carries_the_integer_amount_not_the_decimal_one(self):
		# ERC-681 native: value is in WEI, integer. The decimal form here
		# would be wrong by 10**18.
		built = uri.build_uri("eth", identity(EVM), 1_785_000_000_000_000, "mainnet")
		self.assertEqual(built, f"ethereum:{EVM}@1?value=1785000000000000")

	def test_the_charge_time_mode_picks_the_chain_id(self):
		built = uri.build_uri("eth", identity(EVM), 1_785_000_000_000_000, "testnet")
		self.assertEqual(built, f"ethereum:{EVM}@11155111?value=1785000000000000")

	def test_polygon_wears_its_own_chain_ids(self):
		mainnet = uri.build_uri("pol", identity(EVM), 10**15, "mainnet")
		testnet = uri.build_uri("pol", identity(EVM), 10**15, "testnet")
		self.assertIn(f"@{rail('pol')['chain_id']}?", mainnet)
		self.assertIn(f"@{rail('pol')['testnet_chain_id']}?", testnet)
		self.assertNotEqual(mainnet, testnet)

	def test_a_token_uri_targets_the_contract_and_names_the_merchant(self):
		# The merchant is a PARAMETER here, not the target. Addressing the
		# merchant directly would transfer native ETH instead of the token.
		built = uri.build_uri("usdc-eth", identity(EVM), 6_250_000, "mainnet")
		self.assertEqual(
			built,
			f"ethereum:{rails.USDC_ON_ETHEREUM}@1/transfer?address={EVM}&uint256=6250000",
		)

	def test_a_testnet_token_uri_targets_the_testnet_contract(self):
		built = uri.build_uri("usdc-pol", identity(EVM), 6_250_000, "testnet")
		self.assertTrue(built.startswith(f"ethereum:{rails.USDC_ON_AMOY}@"))
		self.assertNotIn(rails.USDC_ON_POLYGON, built)


class Solana(unittest.TestCase):
	def test_solana_pay_carries_the_reference_that_binds_the_sale(self):
		built = uri.build_uri("sol", identity(SOL), 41_666_000, "testnet")
		self.assertEqual(built, f"solana:{SOL}?amount=0.041666&reference={REF}")

	def test_no_cluster_appears_in_the_uri(self):
		# The wallet's own setting decides the cluster; naming it here would
		# be a claim this scheme does not carry.
		for mode in ("demo", "testnet"):
			with self.subTest(mode=mode):
				built = uri.build_uri("sol", identity(SOL), 10**6, mode)
				self.assertNotIn("cluster", built)

	def test_the_token_form_names_the_mint_for_the_mode(self):
		built = uri.build_uri("usdc-sol", identity(SOL), 6_250_000, "testnet")
		self.assertEqual(
			built,
			f"solana:{SOL}?amount=6.250000&spl-token={rails.USDC_MINT_DEVNET}&reference={REF}",
		)

	def test_the_binding_reference_must_be_a_public_key(self):
		bad = identity(SOL)
		bad["reference"] = "not a public key&amount=999"
		with self.assertRaises(InvalidPaymentIdentity):
			uri.build_uri("sol", bad, 10**6, "testnet")

	def test_the_binding_reference_is_required(self):
		with self.assertRaises(InvalidPaymentIdentity):
			uri.build_uri("sol", {"address": SOL}, 10**6, "testnet")

	def test_the_binding_reference_must_be_text(self):
		bad = identity(SOL)
		bad["reference"] = b"not-text"
		with self.assertRaises(InvalidPaymentIdentity):
			uri.build_uri("sol", bad, 10**6, "testnet")


class Monero(unittest.TestCase):
	def test_uses_the_opaque_form_only(self):
		# "monero://" is rejected by some wallets. One slash, always.
		built = uri.build_uri("xmr", identity(XMR_TEST), 10**12, "testnet")
		self.assertEqual(built, f"monero:{XMR_TEST}?tx_amount=1.000000")
		self.assertNotIn("monero://", built)


class Tari(unittest.TestCase):
	def test_the_amount_is_integer_microtari(self):
		# RFC-0154 deeplink: no decimal point in the amount.
		built = uri.build_uri("xtm", identity(TARI), 1_500_000, "demo")
		self.assertEqual(
			built,
			f"tari://esmeralda/transactions/send?tariAddress={TARI}&amount=1500000",
		)

	def test_the_network_authority_is_enforced_by_the_wallet(self):
		# A testnet QR literally cannot be paid with mainnet coin, because
		# the authority is in the URI and the wallet honours it.
		built = uri.build_uri("xtm", identity(TARI), 1_500_000, "testnet")
		self.assertIn("tari://esmeralda/", built)


class Zcash(unittest.TestCase):
	def test_zip321_transparent_payment_has_no_memo(self):
		built = uri.build_uri("zec", identity(ZEC_TEST), 195_300, "testnet")
		self.assertEqual(built, f"zcash:{ZEC_TEST}?amount=0.00195300")
		self.assertNotIn("memo", built)


class AddressGuard(unittest.TestCase):
	"""The last check before money moves. Every case here is one where the
	old build produced a perfectly scannable QR pointing somewhere wrong."""

	def test_a_testnet_address_cannot_ride_a_mainnet_uri(self):
		# The one that loses real money: well-formed, scannable, and the
		# funds land on a key that exists only on a test network.
		with self.assertRaises(AddressRefused) as caught:
			uri.build_uri("btc", identity(BTC_TEST), 195_300, "mainnet")
		self.assertEqual(caught.exception.verdict, "refused")
		self.assertIn("testnet", str(caught.exception))

	def test_a_mainnet_address_cannot_ride_a_testnet_uri(self):
		with self.assertRaises(AddressRefused):
			uri.build_uri("btc", identity(BTC_MAIN), 195_300, "testnet")

	def test_a_single_character_typo_is_refused(self):
		# What a regex accepts and a checksum does not.
		typo = BTC_MAIN[:-1] + "5"
		with self.assertRaises(AddressRefused):
			uri.build_uri("btc", identity(typo), 195_300, "mainnet")

	def test_a_bitcoin_address_cannot_be_used_for_a_dash_sale(self):
		# Both are `family == "bitcoin"` in the rails table. Version bytes
		# are per COIN, and this is why validation keys on the rail.
		with self.assertRaises(AddressRefused):
			uri.build_uri("dash", identity(BTC_LEGACY), 195_300, "mainnet")

	def test_mainnet_refuses_an_address_it_cannot_check(self):
		# Solana carries no checksum. On testnet that is tolerable; on
		# mainnet an unverifiable address is not one to send real money to.
		with self.assertRaises(AddressRefused) as caught:
			uri.build_uri("sol", identity(SOL), 10**6, "mainnet")
		self.assertEqual(caught.exception.verdict, "unchecked")

	def test_testnet_allows_an_address_it_cannot_check(self):
		self.assertIn(SOL, uri.build_uri("sol", identity(SOL), 10**6, "testnet"))

	def test_mainnet_ignores_an_attempt_to_switch_the_guard_off(self):
		# A flag that lets a caller skip the last check before real funds
		# move is a flag that will eventually be passed by accident.
		with self.assertRaises(AddressRefused):
			uri.build_uri("btc", identity(BTC_TEST), 195_300, "mainnet", strict=False)

	def test_strict_false_is_honoured_off_mainnet(self):
		built = uri.build_uri("btc", identity("not-an-address"), 195_300, "demo", strict=False)
		self.assertIn("not-an-address", built)

	def test_the_refusal_is_catchable_as_the_base_error(self):
		with self.assertRaises(CryptoPosError):
			uri.build_uri("btc", identity(BTC_TEST), 195_300, "mainnet")

	def test_demo_uses_the_testnet_address_shape(self):
		built = uri.build_uri("btc", identity(BTC_TEST), 195_300, "demo")
		self.assertIn(BTC_TEST, built)
		with self.assertRaises(AddressRefused):
			uri.build_uri("btc", identity(BTC_MAIN), 195_300, "demo")

	def test_validated_address_whitespace_is_not_emitted(self):
		spaced = identity(f"  {BTC_MAIN}\n")
		built = uri.build_uri("btc", spaced, 195_300, "mainnet")
		self.assertEqual(built, f"bitcoin:{BTC_MAIN}?amount=0.00195300")

	def test_the_address_is_required_and_must_be_text(self):
		for sale_identity in ({}, {"address": 7}):
			with self.subTest(identity=sale_identity):
				with self.assertRaises(InvalidPaymentIdentity):
					uri.build_uri("btc", sale_identity, 195_300, "mainnet")

	def test_uri_structure_cannot_be_injected_through_a_lenient_address(self):
		for address in ("", "bad address", "bad?amount=999", "bad&label=x", "bad#fragment", "bad%3Fquery"):
			with self.subTest(address=address):
				with self.assertRaises(InvalidPaymentIdentity):
					uri.build_uri("btc", identity(address), 1, "demo", strict=False)


class AmountGuard(unittest.TestCase):
	"""A decimal URI that truncates asks for less than the sale invoiced, and
	the sale then sits short of itself forever."""

	def test_an_unstatable_amount_is_refused(self):
		# 73266666 lamports displays as 0.073266 SOL, which is 73266000.
		# The customer would pay what they were shown and be 666 short.
		with self.assertRaises(AmountNotRepresentable) as caught:
			uri.build_uri("sol", identity(SOL), 73_266_666, "testnet")
		self.assertEqual(caught.exception.representable, 73_266_000)

	def test_the_refusal_names_the_amount_to_invoice_instead(self):
		with self.assertRaises(AmountNotRepresentable) as caught:
			uri.build_uri("xmr", identity(XMR_TEST), 66_606_060_606, "testnet")
		self.assertEqual(caught.exception.representable, 66_606_000_000)
		self.assertIn("66606000000", str(caught.exception))

	def test_invoice_amount_is_always_statable(self):
		# The charge-path helper satisfies the invariant by construction, on
		# every rail, so a caller using it can never hit the refusal above.
		for key, address in TESTNET_ADDRESSES.items():
			with self.subTest(rail=key):
				amount = rails.invoice_amount(
					rails.RAILS[key], 1099, rails.rail_demo_microcents(rails.RAILS[key])
				)
				if key == "xtr":
					continue  # no URI builder at all; covered below
				uri.build_uri(key, identity(address), amount, "testnet")

	def test_integer_amount_rails_are_never_affected(self):
		# ERC-681 and RFC-0154 carry the native integer, so there is nothing
		# to truncate and no amount they cannot state.
		odd = 1_785_714_285_714_285
		self.assertIn(str(odd), uri.build_uri("eth", identity(EVM), odd, "testnet"))

	def test_nonpositive_or_noninteger_amounts_are_refused(self):
		for amount in (0, -1, 1.9, True, None, "not-an-amount"):
			with self.subTest(amount=amount):
				with self.assertRaises(InvalidAmount):
					uri.build_uri("eth", identity(EVM), amount, "testnet")

	def test_strict_false_does_not_disable_amount_exactness(self):
		with self.assertRaises(AmountNotRepresentable):
			uri.build_uri("sol", identity("unchecked"), 73_266_666, "demo", strict=False)

	def test_the_smallest_positive_native_amount_is_accepted(self):
		built = uri.build_uri("btc", identity("unchecked"), 1, "demo", strict=False)
		self.assertIn("amount=0.00000001", built)


class Coverage(unittest.TestCase):
	def test_require_mode_returns_the_validated_mode(self):
		self.assertEqual(modes.require_mode("mainnet"), "mainnet")

	def test_an_unknown_mode_is_refused_before_a_uri_is_built(self):
		with self.assertRaises(InvalidMode):
			uri.build_uri("btc", identity(BTC_MAIN), 195_300, "maintnet")

	def test_every_rail_with_a_builder_produces_a_scheme_qualified_uri(self):
		for key, address in TESTNET_ADDRESSES.items():
			if key == "xtr":
				continue
			with self.subTest(rail=key):
				amount = rails.invoice_amount(
					rails.RAILS[key], 1099, rails.rail_demo_microcents(rails.RAILS[key])
				)
				built = uri.build_uri(key, identity(address), amount, "testnet")
				self.assertIn(":", built)
				self.assertTrue(built.split(":")[0].isalpha())

	def test_the_ootle_rail_has_no_builder_and_says_so(self):
		# The one rail in the table with no URI branch. It raises rather than
		# returning a plausible-looking string, because a QR that encodes a
		# guess is worse at the counter than a rail that refuses to charge.
		with self.assertRaises(UnsupportedRail):
			uri.build_uri("xtr", identity(TARI), 10**6, "testnet")

	def test_an_unknown_rail_raises(self):
		with self.assertRaises(UnsupportedRail):
			uri.build_uri("nosuchrail", identity(SOL), 10**6, "testnet")

	def test_a_non_hashable_rail_is_a_documented_error(self):
		with self.assertRaises(UnsupportedRail):
			uri.build_uri([], identity(SOL), 10**6, "testnet")


class Base58(unittest.TestCase):
	"""Ten lines rather than a dependency. Same alphabet Bitcoin invented:
	no 0/O/I/l, so an address survives being read aloud."""

	def test_the_canonical_bitcoin_vector(self):
		raw = bytes.fromhex("00010966776006953D5567439E5E39F86A0D273BEED61967F6")
		self.assertEqual(uri.base58_encode(raw), "16UwLL9Risc3QfPqBUvKofHmBQ7wMtjvM")

	def test_leading_zero_bytes_become_leading_ones(self):
		# Not decoration: they carry information, and dropping them changes
		# the address.
		self.assertEqual(uri.base58_encode(b"\x00"), "1")
		self.assertEqual(uri.base58_encode(b"\x00\x00\x01"), "112")

	def test_the_alphabet_omits_the_confusable_characters(self):
		for confusable in "0OIl":
			with self.subTest(character=confusable):
				self.assertNotIn(confusable, uri._B58_ALPHABET)

	def test_a_reference_key_is_thirty_two_bytes_and_deterministic(self):
		first = uri.fresh_32_bytes("sale-INV-20260818-0001")
		self.assertEqual(len(first), 32)
		self.assertEqual(first, uri.fresh_32_bytes("sale-INV-20260818-0001"))
		self.assertNotEqual(first, uri.fresh_32_bytes("sale-INV-20260818-0002"))


if __name__ == "__main__":
	unittest.main()
