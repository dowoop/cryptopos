"""Watch-only HD derivation, pinned to published BIP vectors.

The expected extended keys, public keys, and addresses below are copied from
the named BIPs. None was produced by ``cryptopos_core.hd``.
"""

import hashlib
import unittest
from dataclasses import FrozenInstanceError
from unittest import mock

from cryptopos_core import addresses, hd
from cryptopos_core.addresses import OK
from cryptopos_core.errors import CryptoPosError
from cryptopos_core.hd import (
	ExtendedKey,
	InvalidExtendedKey,
	derive_child,
	derive_path,
	evm_address,
	p2wpkh_address,
	parse_extended_key,
)


def b58encode(raw):
	number = int.from_bytes(raw, "big")
	text = ""
	while number:
		number, digit = divmod(number, 58)
		text = hd._B58_ALPHABET[digit] + text
	return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + text


def b58check(payload):
	checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
	return b58encode(payload + checksum)


def payload_of(text):
	return hd._b58_decode(text)[:-4]


# BIP-32, Test vectors 1, 2, and 3:
# https://github.com/bitcoin/bips/blob/master/bip-0032.mediawiki#test-vectors
# Only published public keys are pasted here, and only the non-hardened spans
# are derived. Each hardened boundary starts again from its published xpub.
BIP32_VECTOR_1 = {
	"m": "xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8",
	"m/0H": "xpub68Gmy5EdvgibQVfPdqkBBCHxA5htiqg55crXYuXoQRKfDBFA1WEjWgP6LHhwBZeNK1VTsfTFUHCdrfp1bgwQ9xv5ski8PX9rL2dZXvgGDnw",
	"m/0H/1": "xpub6ASuArnXKPbfEwhqN6e3mwBcDTgzisQN1wXN9BJcM47sSikHjJf3UFHKkNAWbWMiGj7Wf5uMash7SyYq527Hqck2AxYysAA7xmALppuCkwQ",
	"m/0H/1/2H": "xpub6D4BDPcP2GT577Vvch3R8wDkScZWzQzMMUm3PWbmWvVJrZwQY4VUNgqFJPMM3No2dFDFGTsxxpG5uJh7n7epu4trkrX7x7DogT5Uv6fcLW5",
	"m/0H/1/2H/2": "xpub6FHa3pjLCk84BayeJxFW2SP4XRrFd1JYnxeLeU8EqN3vDfZmbqBqaGJAyiLjTAwm6ZLRQUMv1ZACTj37sR62cfN7fe5JnJ7dh8zL4fiyLHV",
	"m/0H/1/2H/2/1000000000": "xpub6H1LXWLaKsWFhvm6RVpEL9P4KfRZSW7abD2ttkWP3SSQvnyA8FSVqNTEcYFgJS2UaFcxupHiYkro49S8yGasTvXEYBVPamhGW6cFJodrTHy",
}

BIP32_VECTOR_2 = {
	"m": "xpub661MyMwAqRbcFW31YEwpkMuc5THy2PSt5bDMsktWQcFF8syAmRUapSCGu8ED9W6oDMSgv6Zz8idoc4a6mr8BDzTJY47LJhkJ8UB7WEGuduB",
	"m/0": "xpub69H7F5d8KSRgmmdJg2KhpAK8SR3DjMwAdkxj3ZuxV27CprR9LgpeyGmXUbC6wb7ERfvrnKZjXoUmmDznezpbZb7ap6r1D3tgFxHmwMkQTPH",
	"m/0/2147483647H": "xpub6ASAVgeehLbnwdqV6UKMHVzgqAG8Gr6riv3Fxxpj8ksbH9ebxaEyBLZ85ySDhKiLDBrQSARLq1uNRts8RuJiHjaDMBU4Zn9h8LZNnBC5y4a",
	"m/0/2147483647H/1": "xpub6DF8uhdarytz3FWdA8TvFSvvAh8dP3283MY7p2V4SeE2wyWmG5mg5EwVvmdMVCQcoNJxGoWaU9DCWh89LojfZ537wTfunKau47EL2dhHKon",
	"m/0/2147483647H/1/2147483646H": "xpub6ERApfZwUNrhLCkDtcHTcxd75RbzS1ed54G1LkBUHQVHQKqhMkhgbmJbZRkrgZw4koxb5JaHWkY4ALHY2grBGRjaDMzQLcgJvLJuZZvRcEL",
	"m/0/2147483647H/1/2147483646H/2": "xpub6FnCn6nSzZAw5Tw7cgR9bi15UV96gLZhjDstkXXxvCLsUXBGXPdSnLFbdpq8p9HmGsApME5hQTZ3emM2rnY5agb9rXpVGyy3bdW6EEgAtqt",
}

BIP32_VECTOR_3 = {
	"m": "xpub661MyMwAqRbcEZVB4dScxMAdx6d4nFc9nvyvH3v4gJL378CSRZiYmhRoP7mBy6gSPSCYk6SzXPTf3ND1cZAceL7SfJ1Z3GC8vBgp2epUt13",
	"m/0H": "xpub68NZiKmJWnxxS6aaHmn81bvJeTESw724CRDs6HbuccFQN9Ku14VQrADWgqbhhTHBaohPX4CjNLf9fq9MYo6oDaPPLPxSb7gwQN3ih19Zm4Y",
}

# BIP-84, "Test vectors":
# https://github.com/bitcoin/bips/blob/master/bip-0084.mediawiki#test-vectors
BIP84_ACCOUNT_ZPUB = "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
BIP84_RECEIVING = (
	(
		"0330d54fd0dd420a6e5f8d3624f5f3482cae350f79d5f0753bf5beef9c2d91af3c",
		"bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",
	),
	(
		"03e775fd51f0dfb8cd865d9ff1cca2a158cf651fe997fdc9fee9c1d3b5e995ea77",
		"bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g",
	),
)


class PublishedBip32Vectors(unittest.TestCase):
	def assert_derives(self, parent, path, expected):
		self.assertEqual(derive_path(parse_extended_key(parent), path), parse_extended_key(expected))

	def test_vector_1_public_derivation_spans(self):
		self.assert_derives(BIP32_VECTOR_1["m/0H"], "1", BIP32_VECTOR_1["m/0H/1"])
		self.assert_derives(
			BIP32_VECTOR_1["m/0H/1/2H"],
			"2/1000000000",
			BIP32_VECTOR_1["m/0H/1/2H/2/1000000000"],
		)
		self.assertEqual(
			derive_child(parse_extended_key(BIP32_VECTOR_1["m/0H/1/2H"]), 2),
			parse_extended_key(BIP32_VECTOR_1["m/0H/1/2H/2"]),
		)

	def test_vector_2_public_derivation_spans(self):
		self.assert_derives(BIP32_VECTOR_2["m"], "0", BIP32_VECTOR_2["m/0"])
		self.assert_derives(
			BIP32_VECTOR_2["m/0/2147483647H"],
			"1",
			BIP32_VECTOR_2["m/0/2147483647H/1"],
		)
		self.assert_derives(
			BIP32_VECTOR_2["m/0/2147483647H/1/2147483646H"],
			"2",
			BIP32_VECTOR_2["m/0/2147483647H/1/2147483646H/2"],
		)

	def test_vector_3_leading_zero_keys_parse_without_private_derivation(self):
		root = parse_extended_key(BIP32_VECTOR_3["m"])
		hardened_child = parse_extended_key(BIP32_VECTOR_3["m/0H"])
		self.assertEqual((root.depth, root.child_number), (0, 0))
		self.assertEqual((hardened_child.depth, hardened_child.child_number), (1, 2**31))
		with self.assertRaisesRegex(InvalidExtendedKey, "hardened.*underivable"):
			derive_child(root, 2**31)

	def test_extended_keys_are_immutable(self):
		key = parse_extended_key(BIP32_VECTOR_1["m"])
		with self.assertRaises(FrozenInstanceError):
			key.depth = 1


class PublishedBip84Vectors(unittest.TestCase):
	def test_zpub_and_first_two_receiving_addresses(self):
		account = parse_extended_key(BIP84_ACCOUNT_ZPUB)
		self.assertEqual(account.version, 0x04B24746)
		for index, (public_key, address) in enumerate(BIP84_RECEIVING):
			with self.subTest(index=index):
				child = derive_path(account, f"0/{index}")
				self.assertEqual(child.public_key.hex(), public_key)
				self.assertEqual(p2wpkh_address(child, "bc"), address)

	def test_every_testnet_address_emitted_round_trips_through_the_existing_decoder(self):
		account = parse_extended_key(BIP84_ACCOUNT_ZPUB)
		for index in range(3):
			address = p2wpkh_address(derive_path(account, f"0/{index}"), "tb")
			self.assertEqual(addresses.validate("btc", address, "testnet")[0], OK)


class EvmAddresses(unittest.TestCase):
	def setUp(self):
		self.account = parse_extended_key(BIP32_VECTOR_1["m/0H/1/2H"])

	def test_every_derived_address_verifies_through_the_library_validator(self):
		for index in range(3):
			address = evm_address(derive_path(self.account, f"0/{index}"))
			with self.subTest(index=index, address=address):
				self.assertEqual(addresses.validate("eth", address, "testnet"), (OK, ""))

	def test_same_key_and_index_are_deterministic_and_another_index_is_distinct(self):
		first = evm_address(derive_path(self.account, "0/0"))
		self.assertEqual(first, evm_address(derive_path(self.account, "0/0")))
		self.assertNotEqual(first, evm_address(derive_path(self.account, "0/1")))

	def test_address_is_the_last_twenty_keccak_bytes_of_the_uncompressed_point(self):
		child = derive_path(self.account, "0/0")
		x, y = hd._point_from_public_key(child.public_key)
		uncompressed_point = x.to_bytes(32, "big") + y.to_bytes(32, "big")
		self.assertEqual(
			bytes.fromhex(evm_address(child)[2:]),
			hd.keccak256(uncompressed_point)[-20:],
		)

	def test_only_xpub_version_bytes_are_evm_receiving_material(self):
		payload = payload_of(BIP32_VECTOR_1["m/0H/1/2H"])
		for version in (0x043587CF, 0x04B24746, 0x045F1CF6):
			wrong_family = parse_extended_key(b58check(version.to_bytes(4, "big") + payload[4:]))
			with self.subTest(version=hex(version)), self.assertRaisesRegex(
				InvalidExtendedKey, "requires xpub.*tpub, zpub, and vpub.*refused"
			):
				evm_address(wrong_family)

	def test_evm_address_requires_an_extended_key_and_valid_public_point(self):
		with self.assertRaisesRegex(InvalidExtendedKey, "ExtendedKey"):
			evm_address(object())
		bad = ExtendedKey(0x0488B21E, 0, b"\0" * 4, 0, b"\0" * 32, b"bad")
		with self.assertRaisesRegex(InvalidExtendedKey, "compressed public key"):
			evm_address(bad)


# BIP-173 Appendix "Test vectors" and BIP-350 "Test vectors for Bech32m":
# https://github.com/bitcoin/bips/blob/master/bip-0173.mediawiki#test-vectors
# https://github.com/bitcoin/bips/blob/master/bip-0350.mediawiki#test-vectors-for-bech32m
BIP173_VALID_BECH32 = (
	"A12UEL5L",
	"a12uel5l",
	"an83characterlonghumanreadablepartthatcontainsthenumber1andtheexcludedcharactersbio1tt5tgs",
	"abcdef1qpzry9x8gf2tvdw0s3jn54khce6mua7lmqqqxw",
	"11qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqc8247j",
	"split1checkupstagehandshakeupstreamerranterredcaperred2y9e3w",
	"?1ezyfcl",
)
BIP350_VALID_BECH32M = (
	"A1LQFN3A",
	"a1lqfn3a",
	"an83characterlonghumanreadablepartthatcontainsthetheexcludedcharactersbioandnumber11sg7hg6",
	"abcdef1l7aum6echk45nj3s0wdvt2fg8x9yrzpqzd3ryx",
	"11llllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllludsr8",
	"split1checkupstagehandshakeupstreamerranterredcaperredlc445v",
	"?1v759aa",
)
BIP173_INVALID_BECH32 = (
	"\x201nwldj5",
	"\x7f1axkwrx",
	"\x801eym55h",
	"an84characterslonghumanreadablepartthatcontainsthenumber1andtheexcludedcharactersbio1569pvx",
	"pzry9x0s0muk",
	"1pzry9x0s0muk",
	"x1b4n0q5v",
	"li1dgmt3",
	"de1lg7wt\xff",
	"A1G7SGD8",
	"10a06t8",
	"1qzzfhee",
)
BIP350_INVALID_BECH32M = (
	"\x201xj0phk",
	"\x7f1g6xzxy",
	"\x801vctc34",
	"an84characterslonghumanreadablepartthatcontainsthetheexcludedcharactersbioandnumber11d6pts4",
	"qyrz8wqd2c9m",
	"1qyrz8wqd2c9m",
	"y1b0jsk6g",
	"lt1igcx5c0",
	"in1muywd",
	"mm1crxm3i",
	"au1s5cgom",
	"M1VUXWEZ",
	"16plkw9",
	"1p2gdwpf",
)


class PublishedBech32Vectors(unittest.TestCase):
	def assert_encoder_vector(self, text, bech32m):
		separator = text.rfind("1")
		hrp = text[:separator]
		data = [hd._BECH32_CHARSET.index(character.lower()) for character in text[separator + 1 : -6]]
		self.assertEqual(hd._bech32_encode(hrp, data, bech32m), text.lower())

	def test_bip173_valid_encoder_vectors(self):
		for text in BIP173_VALID_BECH32:
			with self.subTest(text=text):
				self.assert_encoder_vector(text, False)

	def test_bip350_valid_encoder_vectors(self):
		for text in BIP350_VALID_BECH32M:
			with self.subTest(text=text):
				self.assert_encoder_vector(text, True)

	def test_published_invalid_vectors_are_refused(self):
		for text in BIP173_INVALID_BECH32 + BIP350_INVALID_BECH32M:
			with self.subTest(text=repr(text)):
				self.assertIsNone(addresses._bech32_decode(text))

	def test_encoder_refuses_the_invalid_vector_input_classes(self):
		for invalid_hrp in ("", "\x20", "\x7f", "\x80"):
			with self.subTest(hrp=repr(invalid_hrp)), self.assertRaises(InvalidExtendedKey):
				hd._bech32_encode(invalid_hrp, [])
		with self.assertRaisesRegex(InvalidExtendedKey, "90-character"):
			hd._bech32_encode("a" * 84, [])
		with self.assertRaisesRegex(InvalidExtendedKey, "90-character"):
			hd._bech32_encode("a" * 83, [0])
		for boundary_hrp in ("!", "~"):
			with self.subTest(hrp=boundary_hrp):
				self.assertTrue(hd._bech32_encode(boundary_hrp, []).startswith(boundary_hrp + "1"))
		for invalid_data in (-1, 32, True, "0"):
			with self.subTest(data=repr(invalid_data)), self.assertRaises(InvalidExtendedKey):
				hd._bech32_encode("bc", [invalid_data])
		with self.assertRaisesRegex(InvalidExtendedKey, "selector"):
			hd._bech32_encode("bc", [], 1)


class ParseRefusals(unittest.TestCase):
	BIP32_XPRV = "xprv9s21ZrQH143K3QTDL4LXw2F7HEK3wJUD2nW2nRk4stbPy6cq3jPPqjiChkVvvNKmPGJxWUtg6LnF5kejMRNNU3TGtRBeJgk33yuGBxrMPHi"

	def test_refusal_is_a_documented_money_boundary_error(self):
		self.assertTrue(issubclass(InvalidExtendedKey, CryptoPosError))

	def test_xprv_and_tprv_are_refused_by_version_bytes(self):
		private_payload = payload_of(self.BIP32_XPRV)
		private_keys = [self.BIP32_XPRV]
		for version in (0x04358394, 0x04B2430C, 0x045F18BC):
			private_keys.append(b58check(version.to_bytes(4, "big") + private_payload[4:]))
		for private in private_keys:
			with (
				self.subTest(prefix=private[:4]),
				self.assertRaisesRegex(InvalidExtendedKey, "private.*refused.*watch-only.*public"),
			):
				parse_extended_key(private)

	def test_all_four_public_version_bytes_are_accepted(self):
		payload = payload_of(BIP32_VECTOR_1["m"])
		for version in (0x0488B21E, 0x043587CF, 0x04B24746, 0x045F1CF6):
			with self.subTest(version=hex(version)):
				self.assertEqual(
					parse_extended_key(b58check(version.to_bytes(4, "big") + payload[4:])).version, version
				)

	def test_bad_checksum_length_text_and_base58_are_refused(self):
		good = BIP32_VECTOR_1["m"]
		bad_checksum = good[:-1] + ("1" if good[-1] != "1" else "2")
		for bad in (None, "", "1", "0" + good[1:], bad_checksum, "1" * 129):
			with self.subTest(value=repr(bad)), self.assertRaises(InvalidExtendedKey):
				parse_extended_key(bad)
		with self.assertRaisesRegex(InvalidExtendedKey, "78-byte"):
			parse_extended_key(b58check(payload_of(good)[:-1]))
		with self.assertRaisesRegex(InvalidExtendedKey, "non-empty text"):
			parse_extended_key(1)
		with self.assertRaisesRegex(InvalidExtendedKey, "78-byte"):
			parse_extended_key("1" * 128)
		with self.assertRaisesRegex(InvalidExtendedKey, "too long"):
			parse_extended_key("1" * 129)
		self.assertEqual(hd._b58_decode(b58encode(b"\x80")), b"\x80")

	def test_bip32_published_invalid_public_keys_are_refused(self):
		# BIP-32 Test vector 5, invalid compressed prefix and invalid curve point.
		invalid = (
			"xpub661MyMwAqRbcEYS8w7XLSVeEsBXy79zSzH1J8vCdxAZningWLdN3zgtU6Txnt3siSujt9RCVYsx4qHZGc62TG4McvMGcAUjeuwZdduYEvFn",
			"xpub661MyMwAqRbcEYS8w7XLSVeEsBXy79zSzH1J8vCdxAZningWLdN3zgtU6Q5JXayek4PRsn35jii4veMimro1xefsM58PgBMrvdYre8QyULY",
		)
		for text in invalid:
			with self.subTest(text=text[-8:]), self.assertRaises(InvalidExtendedKey):
				parse_extended_key(text)

	def test_unknown_version_and_inconsistent_master_metadata_are_refused(self):
		# BIP-32 Test vector 5: unknown version, then the two depth-zero metadata cases.
		invalid = (
			"DMwo58pR1QLEFihHiXPVykYB6fJmsTeHvyTp7hRThAtCX8CvYzgPcn8XnmdfHGMQzT7ayAmfo4z3gY5KfbrZWZ6St24UVf2Qgo6oujFktLHdHY4",
			"xpub661no6RGEX3uJkY4bNnPcw4URcQTrSibUZ4NqJEw5eBkv7ovTwgiT91XX27VbEXGENhYRCf7hyEbWrR3FewATdCEebj6znwMfQkhRYHRLpJ",
			"xpub661MyMwAuDcm6CRQ5N4qiHKrJ39Xe1R1NyfouMKTTWcguwVcfrZJaNvhpebzGerh7gucBvzEQWRugZDuDXjNDRmXzSZe4c7mnTK97pTvGS8",
		)
		for text in invalid:
			with self.subTest(text=text[:8]), self.assertRaises(InvalidExtendedKey):
				parse_extended_key(text)

	def test_x_coordinate_outside_the_field_is_refused(self):
		payload = bytearray(payload_of(BIP32_VECTOR_1["m"]))
		payload[45:] = b"\x02" + hd._FIELD_PRIME.to_bytes(32, "big")
		with self.assertRaisesRegex(InvalidExtendedKey, "outside secp256k1"):
			parse_extended_key(b58check(payload))


class DerivationRefusals(unittest.TestCase):
	def setUp(self):
		self.key = parse_extended_key(BIP32_VECTOR_1["m"])

	def test_child_index_and_key_shape_are_checked(self):
		with self.assertRaisesRegex(InvalidExtendedKey, "ExtendedKey"):
			derive_child(object(), 0)
		for index in (True, "0", -1, 2**32):
			with self.subTest(index=repr(index)), self.assertRaisesRegex(InvalidExtendedKey, "child index"):
				derive_child(self.key, index)
		with self.assertRaisesRegex(InvalidExtendedKey, "hardened.*underivable"):
			derive_child(self.key, 2**31)

	def test_depth_chain_code_and_public_key_are_checked(self):
		with self.assertRaisesRegex(InvalidExtendedKey, "depth 255"):
			derive_child(ExtendedKey(self.key.version, 255, b"\0" * 4, 0, b"\0" * 32, self.key.public_key), 0)
		for chain_code in (b"", "x"):
			with (
				self.subTest(chain=repr(chain_code)),
				self.assertRaisesRegex(InvalidExtendedKey, "chain code"),
			):
				derive_child(
					ExtendedKey(self.key.version, 0, b"\0" * 4, 0, chain_code, self.key.public_key), 0
				)
		with self.assertRaisesRegex(InvalidExtendedKey, "compressed public key"):
			derive_child(ExtendedKey(self.key.version, 0, b"\0" * 4, 0, b"\0" * 32, b"bad"), 0)

	def test_rare_invalid_scalar_and_infinity_results_raise(self):
		def digest(left):
			return left.to_bytes(32, "big") + b"\x55" * 32

		with mock.patch.object(hd, "_hmac_sha512", return_value=digest(hd._CURVE_ORDER)):
			with self.assertRaisesRegex(InvalidExtendedKey, "invalid BIP-32 scalar"):
				derive_child(self.key, 0)
		generator_key = ExtendedKey(
			self.key.version,
			0,
			b"\0" * 4,
			0,
			b"\x11" * 32,
			hd._public_key_from_point(hd._GENERATOR),
		)
		with mock.patch.object(hd, "_hmac_sha512", return_value=digest(hd._CURVE_ORDER - 1)):
			with self.assertRaisesRegex(InvalidExtendedKey, "point at infinity"):
				derive_child(generator_key, 0)

	def test_zero_scalar_is_a_valid_public_child(self):
		with mock.patch.object(hd, "_hmac_sha512", return_value=b"\0" * 32 + b"\x66" * 32):
			child = derive_child(self.key, 7)
		self.assertEqual(child.public_key, self.key.public_key)
		self.assertEqual(child.chain_code, b"\x66" * 32)

	def test_relative_path_only(self):
		for path in (None, "", "m", "m/0", "/0", "0/", "0//1", "-1", "1H", "\u0661"):
			with self.subTest(path=repr(path)), self.assertRaises(InvalidExtendedKey):
				derive_path(self.key, path)
		with self.assertRaisesRegex(InvalidExtendedKey, "hardened"):
			derive_path(self.key, str(2**31))
		with self.assertRaisesRegex(InvalidExtendedKey, "non-empty text"):
			derive_path(self.key, 1)
		for master_path in ("m", "m/0"):
			with self.subTest(path=master_path), self.assertRaisesRegex(InvalidExtendedKey, "leading m"):
				derive_path(self.key, master_path)


class InternalBoundaries(unittest.TestCase):
	def test_point_identity_inverse_and_serialization_refusal(self):
		generator = hd._GENERATOR
		self.assertEqual(hd._point_add(None, generator), generator)
		self.assertEqual(hd._point_add(generator, None), generator)
		inverse = (generator[0], hd._FIELD_PRIME - generator[1])
		self.assertIsNone(hd._point_add(generator, inverse))
		self.assertNotEqual(hd._point_add(generator, generator), generator)
		with self.assertRaisesRegex(InvalidExtendedKey, "point at infinity"):
			hd._public_key_from_point(None)

	def test_bit_conversion_refuses_values_outside_the_source_width(self):
		self.assertEqual(hd._convert_bits(b"\xff", 8, 5), [31, 28])
		self.assertEqual(hd._convert_bits([0], 8, 5), [0, 0])
		self.assertEqual(hd._convert_bits([1], 5, 5), [1])
		for value in (-1, 256):
			with self.subTest(value=value), self.assertRaises(InvalidExtendedKey):
				hd._convert_bits([value], 8, 5)

	def test_address_requires_an_extended_key_and_a_valid_public_point(self):
		with self.assertRaisesRegex(InvalidExtendedKey, "ExtendedKey"):
			p2wpkh_address(object(), "tb")
		bad = ExtendedKey(0x0488B21E, 0, b"\0" * 4, 0, b"\0" * 32, b"bad")
		with self.assertRaisesRegex(InvalidExtendedKey, "compressed public key"):
			p2wpkh_address(bad, "tb")


if __name__ == "__main__":
	unittest.main()
