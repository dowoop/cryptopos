"""Addresses: published vectors, and the mistakes that actually happen.

Every "valid" case here is either a published test vector (BIP-173, BIP-350,
EIP-55, the Monero donation address) or is constructed from one — never an
address invented for the test. An invented address that this build's own
encoder produced would only prove the encoder agrees with itself.

The invalid cases are chosen to be the realistic ones: a single character
mistyped, an address pasted from the wrong network, an address for a coin
that shares a family with this one. Those are what an operator actually does
at 8am with a receiving address in a settings field.
"""

import hashlib
import unittest

from cryptopos_core import addresses
from cryptopos_core._keccak import keccak256
from cryptopos_core.addresses import OK, REFUSED, UNCHECKED, to_eip55, validate

GENESIS = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"


def b58encode(raw):
	alphabet = addresses._B58_ALPHABET
	number = int.from_bytes(raw, "big")
	text = ""
	while number:
		number, digit = divmod(number, 58)
		text = alphabet[digit] + text
	return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + text


def b58check(version, payload):
	body = bytes(version) + payload
	return b58encode(body + hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4])


# Bech32 and Monero encoders. Every address they build here is either derived
# from a published vector or is invalid ON PURPOSE -- the point of having an
# encoder in the test file is to construct the malformed cases exactly, so a
# refusal can be pinned to the rule that produced it rather than to whichever
# check happened to fail first.
BECH32_CHARSET = addresses._BECH32_CHARSET


def bech32_encode(hrp, data5, spec="bech32"):
	const = addresses._BECH32_CONST if spec == "bech32" else addresses._BECH32M_CONST
	values = [*addresses._bech32_hrp_expand(hrp), *data5]
	polymod = addresses._bech32_polymod([*values, 0, 0, 0, 0, 0, 0]) ^ const
	checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
	return hrp + "1" + "".join(BECH32_CHARSET[d] for d in [*data5, *checksum])


def segwit_address(hrp, witness_version, program):
	"""Build a segwit address, valid or not, exactly as asked."""
	spec = "bech32" if witness_version == 0 else "bech32m"
	data = [witness_version, *addresses._convert_bits(program, 8, 5, pad=True)]
	return bech32_encode(hrp, data, spec)


def monero_encode(raw):
	"""Monero's block-based base58 -- the inverse of `_monero_b58_decode`."""
	alphabet = addresses._B58_ALPHABET
	text = ""
	for offset in range(0, len(raw), 8):
		block = raw[offset : offset + 8]
		width = addresses._MONERO_BLOCK_SIZES[len(block)]
		number = int.from_bytes(block, "big")
		chunk = ""
		while number:
			number, digit = divmod(number, 58)
			chunk = alphabet[digit] + chunk
		text += chunk.rjust(width, alphabet[0])
	return text


def monero_address(prefix_varint, tail=None):
	"""A Monero address with an arbitrary prefix and a CORRECT checksum.

	The checksum has to verify, otherwise every one of these would be refused
	at the checksum and the prefix rules below would never be reached.
	"""
	if tail is None:
		# Integrated prefixes carry an eight-byte payment id in addition to the
		# two keys. Keeping the fixture valid matters: a malformed fixture made
		# the old minimum-length-only validator look correct.
		tail = b"\x11" * (72 if prefix_varint in (b"\x13", b"\x36", b"\x19") else 64)
	body = bytes(prefix_varint) + tail
	return monero_encode(body + keccak256(body)[:4])


# A Sapling shielded address: hrp "zs" and a 43-byte payload, checksum built
# by the encoder above so it decodes as bech32 rather than as a typo.
SHIELDED = bech32_encode("zs", addresses._convert_bits(b"\x07" * 43, 8, 5, pad=True))


class KeccakVectors(unittest.TestCase):
	"""`hashlib.sha3_256` is NOT this. One padding byte apart, and every
	EIP-55 checksum computed with the wrong one is wrong."""

	def test_the_empty_string_constant(self):
		self.assertEqual(
			keccak256(b"").hex(),
			"c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
		)

	def test_published_short_vectors(self):
		self.assertEqual(
			keccak256(b"abc").hex(),
			"4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
		)
		self.assertEqual(
			keccak256(b"testing").hex(),
			"5f16f4c7f149ac4f9510d9cf8cf384038ad348b3bcdc01915f95de12df9d1b02",
		)

	def test_it_is_not_sha3(self):
		# The mistake this module exists to avoid, asserted directly.
		self.assertNotEqual(keccak256(b"abc"), hashlib.sha3_256(b"abc").digest())

	def test_input_longer_than_the_rate_still_absorbs(self):
		# 200 bytes crosses the 136-byte rate, so this exercises the multi
		# block path that a short vector never reaches.
		self.assertEqual(len(keccak256(b"a" * 200)), 32)
		self.assertNotEqual(keccak256(b"a" * 200), keccak256(b"a" * 199))


class Base58CheckEncoder(unittest.TestCase):
	"""Proves the construction the Dash and Zcash fixtures rely on."""

	def test_it_round_trips_the_genesis_address_exactly(self):
		version, payload = addresses._b58check_decode(GENESIS)
		self.assertEqual(b58check(version, payload), GENESIS)
		self.assertEqual(payload.hex(), "62e907b15cbf27d5425399ebf6f0fb50ebb88f18")


class BitcoinAddresses(unittest.TestCase):
	def test_bip173_segwit_vectors(self):
		self.assertEqual(validate("btc", "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", "mainnet")[0], OK)
		self.assertEqual(validate("btc", "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx", "testnet")[0], OK)

	def test_bip350_taproot_is_bech32m(self):
		taproot = "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"
		self.assertEqual(validate("btc", taproot, "mainnet")[0], OK)

	def test_legacy_and_p2sh_base58(self):
		self.assertEqual(validate("btc", GENESIS, "mainnet")[0], OK)
		self.assertEqual(validate("btc", "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy", "mainnet")[0], OK)

	def test_a_testnet_address_on_a_mainnet_sale_is_refused(self):
		# THE case this module exists for. Well-formed, scannable, and the
		# money lands on a key that exists only on a test network.
		verdict, reason = validate("btc", "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx", "mainnet")
		self.assertEqual(verdict, REFUSED)
		self.assertIn("testnet", reason)
		self.assertIn("mainnet", reason)

	def test_a_mainnet_address_on_a_testnet_sale_is_refused(self):
		verdict, _reason = validate("btc", "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", "testnet")
		self.assertEqual(verdict, REFUSED)

	def test_one_mistyped_character_is_caught(self):
		# What a regex would accept. The bech32 checksum is the whole point.
		for good in (
			"bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
			GENESIS,
		):
			with self.subTest(address=good[:10]):
				typo = good[:-1] + ("5" if good[-1] != "5" else "6")
				self.assertEqual(validate("btc", typo, "mainnet")[0], REFUSED)

	def test_a_bech32_address_of_the_wrong_case_mix_is_refused(self):
		# The checksum is defined over one case, so a mixed-case string is
		# not merely unusual -- it is unverifiable.
		mixed = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kV8f3t4"
		self.assertEqual(validate("btc", mixed, "mainnet")[0], REFUSED)

	def test_an_empty_address_is_refused(self):
		for empty in ("", "   ", None):
			with self.subTest(value=repr(empty)):
				self.assertEqual(validate("btc", empty, "mainnet")[0], REFUSED)


class DashAndZcash(unittest.TestCase):
	"""`btc` and `dash` share a FAMILY and not their version bytes. Keying
	validation on family would accept a Bitcoin address for a Dash sale."""

	def setUp(self):
		_version, self.h160 = addresses._b58check_decode(GENESIS)

	def test_dash_accepts_its_own_version_bytes(self):
		self.assertEqual(validate("dash", b58check((0x4C,), self.h160), "mainnet")[0], OK)
		self.assertEqual(validate("dash", b58check((0x8C,), self.h160), "testnet")[0], OK)

	def test_dash_refuses_a_bitcoin_address(self):
		verdict, reason = validate("dash", GENESIS, "mainnet")
		self.assertEqual(verdict, REFUSED)
		self.assertIn("another coin", reason)

	def test_dash_refuses_its_own_address_from_the_other_network(self):
		self.assertEqual(validate("dash", b58check((0x4C,), self.h160), "testnet")[0], REFUSED)

	def test_zcash_transparent_uses_a_two_byte_version(self):
		self.assertEqual(validate("zec", b58check((0x1C, 0xB8), self.h160), "mainnet")[0], OK)
		self.assertEqual(validate("zec", b58check((0x1D, 0x25), self.h160), "testnet")[0], OK)

	def test_zcash_refuses_a_shielded_address_with_a_reason_that_explains(self):
		# Not "malformed": it is a real address this build could never watch,
		# and an operator needs to know which of those two it is.
		#
		# The fixture must be a CHECKSUM-VALID bech32 string. An `zs1` prefix
		# followed by filler is refused too, but as "mistyped or truncated" --
		# the right verdict for the wrong reason, and an operator told to
		# retype a perfectly good shielded address gets no closer to a sale.
		# So the reason is asserted, not discarded.
		verdict, reason = validate("zec", SHIELDED, "mainnet")
		self.assertEqual(verdict, REFUSED)
		self.assertIn("shielded", reason)
		self.assertIn("could never see the payment arrive", reason)
		self.assertNotIn("mistyped", reason)

	def test_a_shielded_address_that_is_also_mistyped_is_a_different_refusal(self):
		# One character changed breaks the bech32 checksum, and then this
		# build genuinely cannot tell what it was handed.
		broken = SHIELDED[:-1] + ("q" if SHIELDED[-1] != "q" else "p")
		verdict, reason = validate("zec", broken, "mainnet")
		self.assertEqual(verdict, REFUSED)
		self.assertIn("mistyped", reason)

	def test_zcash_refuses_its_own_address_from_the_other_network(self):
		verdict, reason = validate("zec", b58check((0x1C, 0xB8), self.h160), "testnet")
		self.assertEqual(verdict, REFUSED)
		self.assertIn("valid mainnet transparent address", reason)
		self.assertIn("this sale is testnet", reason)

	def test_zcash_refuses_a_bitcoin_address_pasted_into_its_field(self):
		# Reads as two version bytes, checksum verifies, and it is not a
		# Zcash prefix. The likeliest paste-into-the-wrong-field mistake.
		verdict, reason = validate("zec", GENESIS, "mainnet")
		self.assertEqual(verdict, REFUSED)
		self.assertIn("not Zcash transparent", reason)


class Monero(unittest.TestCase):
	MAINNET = (
		"44AFFq5kSiGBoZ4NMDwYtN18obc8AemS33DBLWs3H7otXft3XjrpDtQGv7SqSsaBYBb98uNbr2VBBEt7f2wfn3RVGQBEP3A"
	)

	def test_the_published_donation_address(self):
		self.assertEqual(len(self.MAINNET), 95)
		self.assertEqual(validate("xmr", self.MAINNET, "mainnet")[0], OK)

	def test_it_is_refused_on_testnet(self):
		self.assertEqual(validate("xmr", self.MAINNET, "testnet")[0], REFUSED)

	def test_a_mistyped_character_fails_the_keccak_checksum(self):
		typo = self.MAINNET[:-1] + ("B" if self.MAINNET[-1] != "B" else "C")
		self.assertEqual(validate("xmr", typo, "mainnet")[0], REFUSED)

	def test_monero_base58_is_not_bitcoin_base58(self):
		# Block-based, 8 bytes to 11 characters. A standard decoder produces
		# garbage here, which is why the two are separate functions.
		self.assertEqual(len(addresses._monero_b58_decode(self.MAINNET)), 69)
		self.assertIsNone(addresses._monero_b58_decode("0OIl"))


class EvmAddresses(unittest.TestCase):
	"""The four vectors from EIP-55 itself."""

	VECTORS = (
		"0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
		"0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
		"0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
		"0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
	)

	def test_every_eip55_vector_verifies(self):
		for address in self.VECTORS:
			with self.subTest(address=address):
				self.assertEqual(validate("eth", address, "mainnet")[0], OK)

	def test_a_flipped_case_breaks_the_checksum(self):
		broken = self.VECTORS[0][:-1] + self.VECTORS[0][-1].upper()
		self.assertEqual(validate("eth", broken, "mainnet")[0], REFUSED)

	def test_an_all_lowercase_address_is_unchecked_not_ok(self):
		# Well-formed and carrying no checksum. Calling it "ok" would make
		# the verdict meaningless -- a caller could not tell a verified
		# address from an unverifiable one.
		verdict, reason = validate("eth", self.VECTORS[0].lower(), "mainnet")
		self.assertEqual(verdict, UNCHECKED)
		self.assertIn("EIP-55", reason)

	def test_length_and_hex_are_enforced(self):
		for bad in ("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAe", "0x" + "z" * 40, "5aAe"):
			with self.subTest(address=bad[:12]):
				self.assertEqual(validate("eth", bad, "mainnet")[0], REFUSED)

	def test_to_eip55_formats_trusted_address_bytes(self):
		# Formatting is not validation: callers may use this only after obtaining
		# the bytes from a trusted wallet or verifying control independently.
		for address in self.VECTORS:
			with self.subTest(address=address):
				self.assertEqual(to_eip55(address.lower()), address)

	def test_an_evm_address_carries_no_network(self):
		# The same address is valid on every EVM chain, so the mode cannot be
		# checked and this module does not pretend it was. The chain id in
		# the URI is what actually binds the network.
		for mode in ("mainnet", "testnet", "demo"):
			with self.subTest(mode=mode):
				self.assertEqual(validate("eth", self.VECTORS[0], mode)[0], OK)


class SolanaAddresses(unittest.TestCase):
	GOOD = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"

	def test_a_32_byte_key_is_unchecked_rather_than_ok(self):
		verdict, reason = validate("sol", self.GOOD, "mainnet")
		self.assertEqual(verdict, UNCHECKED)
		self.assertIn("no checksum", reason)

	def test_characters_outside_the_alphabet_are_refused(self):
		self.assertEqual(validate("sol", "0OIl" + self.GOOD[4:], "mainnet")[0], REFUSED)

	def test_the_wrong_length_is_refused(self):
		verdict, reason = validate("sol", "abc", "mainnet")
		self.assertEqual(verdict, REFUSED)
		self.assertIn("32 bytes", reason)


class Unverifiable(unittest.TestCase):
	def test_tari_rails_are_unchecked_and_say_why(self):
		for key in ("xtm", "xtr"):
			with self.subTest(rail=key):
				verdict, reason = validate(key, "whatever", "mainnet")
				self.assertEqual(verdict, UNCHECKED)
				self.assertIn("not specified", reason)

	def test_an_unknown_rail_is_unchecked_rather_than_ok(self):
		# Failing open to "ok" for a rail nobody wrote a check for is how a
		# new rail silently arrives with no validation at all.
		verdict, _reason = validate("nosuchrail", "whatever", "mainnet")
		self.assertEqual(verdict, UNCHECKED)


class Totality(unittest.TestCase):
	def test_nothing_raises_on_any_input(self):
		# A validator that throws on malformed input turns a refusable sale
		# into a crashed terminal, which is strictly worse.
		nasty = ["", "   ", None, "0" * 500, "\x00\x01", "1" * 90, "bc1" + "q" * 200, "💸"]
		for rail_key in ("btc", "dash", "zec", "xmr", "eth", "sol", "xtm", "nope"):
			for value in nasty:
				with self.subTest(rail=rail_key, value=repr(value)[:16]):
					verdict, reason = validate(rail_key, value, "mainnet")
					self.assertIn(verdict, (OK, REFUSED, UNCHECKED))
					self.assertIsInstance(reason, str)

	def test_non_text_inputs_are_refused_not_raised(self):
		for address in (7, [], {}, b"address"):
			with self.subTest(address=address):
				verdict, reason = validate("btc", address, "mainnet")
				self.assertEqual(verdict, REFUSED)
				self.assertIn("text", reason)

	def test_non_text_rail_keys_are_unchecked_not_raised(self):
		for rail_key in (None, 7, [], {}):
			with self.subTest(rail_key=rail_key):
				verdict, reason = validate(rail_key, "whatever", "mainnet")
				self.assertEqual(verdict, UNCHECKED)
				self.assertIn("rail key", reason)

	def test_an_unknown_mode_is_refused_not_guessed(self):
		verdict, reason = validate("btc", "whatever", "maintnet")
		self.assertEqual(verdict, REFUSED)
		self.assertIn("unknown mode", reason)

	def test_oversized_text_is_refused_before_any_family_decoder(self):
		for rail_key in ("btc", "dash", "zec", "xmr", "sol"):
			with self.subTest(rail=rail_key):
				verdict, reason = validate(rail_key, "z" * (addresses.MAX_ADDRESS_TEXT_LENGTH + 1), "testnet")
				self.assertEqual(verdict, REFUSED)
				self.assertIn("safety limit", reason)


if __name__ == "__main__":
	unittest.main()


# ---------------------------------------------------------------------------
# The decoders' own refusals.
#
# Everything below reaches a branch that no address in the sections above
# does. They are the paths a validator takes when it is handed something that
# is not merely the wrong address but not an address at all, and they matter
# for one reason: every one of them must end in a REFUSAL and never in an
# exception. `validate` is documented as never raising, and a decoder that
# throws on a malformed string turns a refusable sale into a crashed terminal.
# ---------------------------------------------------------------------------
class Bech32Decoder(unittest.TestCase):
	def test_a_valid_string_round_trips(self):
		text = bech32_encode("bc", [0, 1, 2, 3])
		self.assertEqual(addresses._bech32_decode(text), ("bc", [0, 1, 2, 3], "bech32"))

	def test_bech32m_is_recognised_as_itself(self):
		# Not "bech32 with a bad checksum": the spec is what BIP-350 keys the
		# witness-version rule on, so conflating them is the bug it closes.
		text = bech32_encode("bc", [1, 2, 3], spec="bech32m")
		self.assertEqual(addresses._bech32_decode(text)[2], "bech32m")

	def test_an_unprintable_character_in_the_prefix_is_refused(self):
		# A space is 0x20, one below the permitted range. This is what a
		# wrapped or copy-pasted address looks like after a line break.
		self.assertIsNone(addresses._bech32_decode("a b1qqqqqqq"))

	def test_a_character_outside_the_data_charset_is_refused(self):
		# `b`, `i`, `o` and `1` are excluded from the charset precisely
		# because they are the characters people confuse; one appearing in
		# the data half means a misread, not a valid address.
		for character in "bio":
			with self.subTest(character=character):
				self.assertIsNone(addresses._bech32_decode("bc1qqqqqq" + character))

	def test_the_checksum_is_what_decides(self):
		text = bech32_encode("bc", [0, 1, 2, 3])
		flipped = text[:-1] + ("q" if text[-1] != "q" else "p")
		self.assertIsNone(addresses._bech32_decode(flipped))


class ConvertBits(unittest.TestCase):
	"""The 5-bit/8-bit repacking under both padding rules."""

	def test_it_repacks_eight_into_five_and_back(self):
		packed = addresses._convert_bits(b"\xff\x00\xff", 8, 5, pad=True)
		self.assertEqual(addresses._convert_bits(packed, 5, 8, pad=False), [0xFF, 0x00, 0xFF])

	def test_a_value_wider_than_its_declared_bits_is_refused(self):
		# 32 does not fit in five bits. Accepting it would silently truncate
		# and produce a program that is not the one encoded.
		self.assertIsNone(addresses._convert_bits([32], 5, 8))

	def test_a_negative_value_is_refused(self):
		self.assertIsNone(addresses._convert_bits([-1], 5, 8))

	def test_padding_emits_the_final_partial_group(self):
		# Encoding direction: leftover bits become a final group, zero-filled.
		self.assertEqual(addresses._convert_bits([1], 5, 8, pad=True), [8])

	def test_without_padding_leftover_bits_are_refused(self):
		# Decoding direction: leftover bits mean the data half is not a whole
		# number of bytes, which is a malformed address rather than a short one.
		self.assertIsNone(addresses._convert_bits([1], 5, 8, pad=False))

	def test_without_padding_nonzero_filler_is_refused(self):
		# BIP-173 requires the padding bits be zero. Non-zero filler is a
		# distinct encoding of the same program, and accepting it makes the
		# address non-canonical.
		# 21 bytes is 168 bits, which is not a whole number of 5-bit groups:
		# the encoding carries two bits of filler that must be zero. A length
		# that divides evenly (20 bytes, 32 groups) has no filler at all and
		# would test nothing here.
		program = addresses._convert_bits(b"\x00" * 21, 8, 5, pad=True)
		self.assertIsNotNone(addresses._convert_bits(program, 5, 8, pad=False))
		program[-1] |= 1
		self.assertIsNone(addresses._convert_bits(program, 5, 8, pad=False))


class SegwitDecoder(unittest.TestCase):
	"""BIP-173 and BIP-350 length and version rules, one refusal each."""

	def decode(self, address, hrps=("bc",)):
		return addresses._segwit_decode(address, hrps)

	def test_a_v0_twenty_byte_program_is_accepted(self):
		self.assertIsNotNone(self.decode(segwit_address("bc", 0, b"\x11" * 20)))

	def test_a_prefix_for_another_network_is_refused(self):
		self.assertIsNone(self.decode(segwit_address("tb", 0, b"\x11" * 20)))

	def test_a_program_shorter_than_two_bytes_is_refused(self):
		self.assertIsNone(self.decode(segwit_address("bc", 1, b"\x11")))

	def test_a_program_longer_than_forty_bytes_is_refused(self):
		self.assertIsNone(self.decode(segwit_address("bc", 1, b"\x11" * 41)))

	def test_a_witness_version_above_sixteen_is_refused(self):
		# The data half carries 5 bits, so 17..31 are encodable and none of
		# them name a witness version that exists.
		self.assertIsNone(self.decode(segwit_address("bc", 17, b"\x11" * 20)))

	def test_v0_must_be_bech32_not_bech32m(self):
		# The exact confusion BIP-350 was written to close: same characters,
		# same length, different constant.
		data = [0, *addresses._convert_bits(b"\x11" * 20, 8, 5, pad=True)]
		self.assertIsNone(self.decode(bech32_encode("bc", data, spec="bech32m")))

	def test_v0_must_be_twenty_or_thirty_two_bytes(self):
		self.assertIsNone(self.decode(segwit_address("bc", 0, b"\x11" * 21)))

	def test_v1_must_be_bech32m_not_bech32(self):
		data = [1, *addresses._convert_bits(b"\x11" * 32, 8, 5, pad=True)]
		self.assertIsNone(self.decode(bech32_encode("bc", data, spec="bech32")))

	def test_an_empty_data_half_is_refused(self):
		self.assertIsNone(self.decode(bech32_encode("bc", [])))


class Varint(unittest.TestCase):
	"""Monero's network prefix is a varint, and a varint can be unreadable."""

	def test_a_single_byte_prefix(self):
		self.assertEqual(addresses._read_varint(b"\x12rest"), (18, 1))

	def test_a_two_byte_prefix(self):
		self.assertEqual(addresses._read_varint(b"\x80\x01"), (128, 2))

	def test_a_run_that_never_terminates_is_unreadable(self):
		# Every byte sets the continuation bit and the data runs out.
		self.assertEqual(addresses._read_varint(b"\x80\x80"), (None, 0))

	def test_a_prefix_wider_than_a_machine_word_is_unreadable(self):
		# Refused rather than accumulated: a value this build cannot hold is
		# not a network it can recognise, and pretending otherwise would
		# compare a truncated number against the prefix table.
		self.assertEqual(addresses._read_varint(b"\x80" * 10), (None, 0))


class MoneroPrefixes(unittest.TestCase):
	"""Every prefix outcome, on blobs whose keccak checksum verifies.

	The checksum has to be right for any of these to be reached at all --
	otherwise all five would be refused one step earlier and the prefix rules
	would be untested while looking tested.
	"""

	def test_a_standard_mainnet_prefix_is_accepted(self):
		self.assertEqual(validate("xmr", monero_address(b"\x12"), "mainnet")[0], OK)

	def test_an_integrated_and_a_subaddress_prefix_are_accepted(self):
		for prefix in (b"\x13", b"\x2a"):
			with self.subTest(prefix=prefix):
				self.assertEqual(validate("xmr", monero_address(prefix), "mainnet")[0], OK)

	def test_a_testnet_prefix_on_a_mainnet_sale_names_the_network(self):
		verdict, reason = validate("xmr", monero_address(b"\x35"), "mainnet")
		self.assertEqual(verdict, REFUSED)
		self.assertIn("testnet", reason)

	def test_stagenet_is_named_alongside_testnet(self):
		# 24 is stagenet, which is neither of the two networks this build
		# knows and is still worth naming -- an operator who pasted one has
		# made a specific mistake and can fix it.
		verdict, reason = validate("xmr", monero_address(b"\x18"), "mainnet")
		self.assertEqual(verdict, REFUSED)
		self.assertIn("stagenet", reason)

	def test_an_unrecognised_prefix_says_so_plainly(self):
		verdict, reason = validate("xmr", monero_address(b"\x64"), "mainnet")
		self.assertEqual(verdict, REFUSED)
		self.assertIn("not one this build recognises", reason)

	def test_an_unreadable_prefix_is_its_own_refusal(self):
		# Ten continuation bytes: the checksum verifies, so this is a blob
		# that is Monero-shaped and still carries no readable network. It is
		# not "the wrong network" and must not be reported as one.
		verdict, reason = validate("xmr", monero_address(b"\x80" * 10, tail=b"\x11" * 55), "mainnet")
		self.assertEqual(verdict, REFUSED)
		self.assertIn("no readable network prefix", reason)

	def test_a_blob_too_short_to_be_an_address_is_refused(self):
		self.assertEqual(validate("xmr", monero_encode(b"\x12" * 20), "mainnet")[0], REFUSED)

	def test_a_checksum_valid_blob_with_the_wrong_length_is_refused(self):
		wrong_lengths = (
			(b"\x12", 65),
			(b"\x13", 64),
			(b"\x2a", 72),
			(b"\x35", 65),
			(b"\x36", 64),
			(b"\x3f", 72),
			(b"\x18", 65),
			(b"\x19", 64),
			(b"\x24", 72),
		)
		for prefix, tail_length in wrong_lengths:
			with self.subTest(prefix=prefix, tail_length=tail_length):
				verdict, reason = validate(
					"xmr", monero_address(prefix, tail=b"\x11" * tail_length), "mainnet"
				)
				self.assertEqual(verdict, REFUSED)
				self.assertIn("requires", reason)

	def test_an_overlong_varint_cannot_steal_a_public_key_byte(self):
		# 0x92 0x00 is a non-canonical encoding of mainnet prefix 18. Keeping
		# the standard 69-byte total then leaves 63 rather than 64 key bytes.
		verdict, reason = validate("xmr", monero_address(b"\x92\x00", tail=b"\x11" * 63), "mainnet")
		self.assertEqual(verdict, REFUSED)
		self.assertIn("non-canonical", reason)

	def test_every_stagenet_address_type_is_named_and_refused(self):
		for prefix in (b"\x18", b"\x19", b"\x24"):
			with self.subTest(prefix=prefix):
				verdict, reason = validate("xmr", monero_address(prefix), "mainnet")
				self.assertEqual(verdict, REFUSED)
				self.assertIn("stagenet", reason)


# ---------------------------------------------------------------------------
# Boundaries, and the constants nothing else pins.
#
# Everything below was written because a mutation survived: the code was made
# wrong -- a `<` widened to `<=`, a version byte moved by one, a length limit
# shifted -- and the whole suite stayed green. A limit that is only ever
# tested from one side is a limit that can drift to the other.
# ---------------------------------------------------------------------------
class Base58CheckLength(unittest.TestCase):
	"""The shortest thing that can carry a checksum at all."""

	def test_a_body_of_exactly_version_plus_checksum_decodes(self):
		# One version byte, an EMPTY payload, four checksum bytes. Nothing
		# useful, and it is the exact boundary the guard is written against:
		# `< version_length + 4` must accept five bytes and refuse four.
		text = b58check((0x00,), b"")
		self.assertEqual(addresses._b58check_decode(text), (b"\x00", b""))

	def test_a_body_one_byte_short_is_refused(self):
		raw = b"\x00" + hashlib.sha256(hashlib.sha256(b"\x00").digest()).digest()[:4]
		self.assertIsNotNone(addresses._b58check_decode(b58encode(raw)))
		self.assertIsNone(addresses._b58check_decode(b58encode(raw[:-1])))

	def test_a_checksum_of_nothing_is_not_an_address(self):
		# `3QJmnh` decodes to exactly four bytes, and those four bytes are the
		# double-SHA256 checksum of the empty string -- so it is internally
		# consistent and carries no version and no payload. Only the minimum
		# length rejects it; with that rule inverted it decodes happily to an
		# empty version and an empty payload.
		self.assertEqual(len(addresses._b58_decode("3QJmnh")), 4)
		self.assertIsNone(addresses._b58check_decode("3QJmnh"))

	def test_a_two_byte_version_needs_two_more_bytes(self):
		# Zcash reads two version bytes, so its minimum is one byte longer.
		text = b58check((0x1C, 0xB8), b"")
		self.assertEqual(addresses._b58check_decode(text, version_length=2)[0], b"\x1c\xb8")


class Bech32Boundaries(unittest.TestCase):
	"""BIP-173's stated limits, each tested from both sides."""

	def body(self, length, hrp="bc"):
		"""A checksum-valid string of exactly `length` characters."""
		# hrp + separator + data + 6 checksum characters.
		return bech32_encode(hrp, [0] * (length - len(hrp) - 7))

	def test_the_shortest_legal_string_is_accepted(self):
		# Eight characters is the floor a real string can reach: a
		# single-character prefix, the separator, no data at all, and six of
		# checksum. Nothing shorter can carry a checksum, which is why the
		# guard states a length rather than waiting for the checksum to fail.
		text = self.body(8, hrp="a")
		self.assertEqual(len(text), 8)
		self.assertIsNotNone(addresses._bech32_decode(text))

	def test_anything_shorter_cannot_carry_a_checksum(self):
		self.assertIsNone(addresses._bech32_decode(self.body(8, hrp="a")[:-1]))

	def test_the_longest_legal_string_is_accepted(self):
		text = self.body(90)
		self.assertEqual(len(text), 90)
		self.assertIsNotNone(addresses._bech32_decode(text))

	def test_one_character_longer_is_refused(self):
		# 91 characters is over BIP-173's limit even with a valid checksum.
		text = bech32_encode("bc", [0] * 82)
		self.assertEqual(len(text), 91)
		self.assertIsNone(addresses._bech32_decode(text))

	def test_a_one_character_prefix_is_accepted(self):
		# The separator may sit at position 1 but not at position 0: an empty
		# human-readable part is not a prefix, it is a missing one.
		self.assertIsNotNone(addresses._bech32_decode(bech32_encode("a", [0])))

	def test_an_empty_prefix_is_refused(self):
		# The fixture carries a VALID bech32 checksum over an empty prefix --
		# `1qzzfhee` really does polymod to the bech32 constant. A malformed
		# string would be refused by the checksum instead, leaving the
		# separator-position rule itself untested.
		self.assertEqual(
			addresses._bech32_polymod(
				addresses._bech32_hrp_expand("") + [BECH32_CHARSET.index(c) for c in "qzzfhee"]
			),
			addresses._BECH32_CONST,
		)
		self.assertIsNone(addresses._bech32_decode("1qzzfhee"))

	def test_a_data_half_too_short_to_be_a_checksum_is_refused(self):
		# `abc1c0693` has five data characters where a checksum needs six, and
		# it still polymods to the bech32m constant -- solving for it is
		# possible because the checksum is linear. So the length rule is the
		# only thing refusing it, and nothing else in this file would notice
		# that rule being removed.
		self.assertIsNone(addresses._bech32_decode("abc1c0693"))

	def test_the_data_half_must_carry_a_whole_checksum(self):
		# Six characters of checksum, so the separator cannot be closer than
		# six from the end. Exactly six is legal; five is not.
		text = bech32_encode("bc", [])
		self.assertEqual(len(text) - text.rfind("1") - 1, 6)
		self.assertIsNotNone(addresses._bech32_decode(text))
		self.assertIsNone(addresses._bech32_decode(text[:-1]))

	def test_the_printable_range_of_a_prefix(self):
		# 33 ('!') and 126 ('~') are inside; 32 (space) and 127 (DEL) are not.
		for character in ("!", "~"):
			with self.subTest(character=character):
				self.assertIsNotNone(addresses._bech32_decode(bech32_encode(character, [0])))
		for codepoint in (32, 127):
			with self.subTest(codepoint=codepoint):
				self.assertIsNone(addresses._bech32_decode(bech32_encode(chr(codepoint), [0])))


class ConvertBitsBoundaries(unittest.TestCase):
	def test_it_pads_by_default(self):
		# The default is `pad=True`, and only the encoding direction relies on
		# it. Every call inside this package passes the flag explicitly, so
		# nothing else would notice the default flipping.
		self.assertEqual(addresses._convert_bits([1], 5, 8), [8])
		self.assertEqual(addresses._convert_bits([1], 5, 8, pad=True), [8])

	def test_a_single_zero_group_is_still_leftover_bits(self):
		# Five bits left over is not a whole byte whatever those bits are.
		# With `> from_bits` instead of `>=` the all-zero case slips through
		# and returns an empty program rather than a refusal.
		self.assertIsNone(addresses._convert_bits([0], 5, 8, pad=False))
		self.assertIsNone(addresses._convert_bits([1], 5, 8, pad=False))


class SegwitBoundaries(unittest.TestCase):
	"""Program length and witness version, from both sides of each limit."""

	def decode(self, address):
		return addresses._segwit_decode(address, ("bc",))

	def test_the_shortest_legal_program_is_accepted(self):
		self.assertIsNotNone(self.decode(segwit_address("bc", 1, b"\x11" * 2)))

	def test_the_longest_legal_program_is_accepted(self):
		self.assertIsNotNone(self.decode(segwit_address("bc", 1, b"\x11" * 40)))

	def test_the_highest_witness_version_is_accepted(self):
		# 16 is a real witness version; 17 is not. `>` and `>=` differ by
		# exactly this address.
		self.assertIsNotNone(self.decode(segwit_address("bc", 16, b"\x11" * 20)))

	def test_v0_accepts_a_thirty_two_byte_program(self):
		# P2WSH. Only 20 (P2WPKH) and 32 are legal at version 0, and testing
		# 20 alone leaves the other half of that tuple unpinned.
		self.assertIsNotNone(self.decode(segwit_address("bc", 0, b"\x11" * 32)))

	def test_v0_refuses_the_lengths_between_and_beyond(self):
		for length in (19, 21, 31, 33):
			with self.subTest(length=length):
				self.assertIsNone(self.decode(segwit_address("bc", 0, b"\x11" * length)))


class MoneroEncoding(unittest.TestCase):
	def test_only_the_documented_block_lengths_decode(self):
		# 8 bytes encode to 11 characters, and a final partial block may only
		# be one of these lengths. Any other length is not a truncated address
		# this build should try to read -- it is not an address.
		legal = {2, 3, 5, 6, 7, 9, 10, 11}
		for length in range(1, 13):
			with self.subTest(length=length):
				decoded = addresses._monero_b58_decode("1" * 11 + "1" * length)
				self.assertEqual(decoded is not None, length in legal)

	def test_a_block_that_overflows_its_byte_count_is_refused(self):
		# 11 characters carry at most 8 bytes. The largest 8-byte value
		# encodes inside the block; one more does not, and a `>` instead of
		# `>=` would accept the first value that cannot fit.
		alphabet = addresses._B58_ALPHABET

		def encode_block(number, width):
			text = ""
			while number:
				number, digit = divmod(number, 58)
				text = alphabet[digit] + text
			return text.rjust(width, alphabet[0])

		largest = (1 << 64) - 1
		self.assertIsNotNone(addresses._monero_b58_decode(encode_block(largest, 11)))
		self.assertIsNone(addresses._monero_b58_decode(encode_block(largest + 1, 11)))

	def test_a_69_byte_blob_is_the_shortest_readable_address(self):
		# The guard is a MINIMUM. Reversed, a short blob would fall through to
		# the checksum and be refused for the wrong reason -- so the reason is
		# asserted, not just the verdict.
		verdict, reason = validate("xmr", monero_encode(b"\x12" * 20), "mainnet")
		self.assertEqual(verdict, REFUSED)
		self.assertIn("not valid Monero base58", reason)


class VarintBoundaries(unittest.TestCase):
	def test_the_widest_readable_prefix(self):
		# Nine continuation bytes take the shift to 63, which is still
		# readable; the tenth is what tips it over.
		self.assertEqual(addresses._read_varint(b"\x80" * 9 + b"\x01")[0], 1 << 63)
		self.assertEqual(addresses._read_varint(b"\x80" * 10 + b"\x01"), (None, 0))


class VersionBytes(unittest.TestCase):
	"""Every accepted version byte, stated once.

	A version byte that is wrong by one refuses a valid receiving address, or
	-- worse -- accepts one from the wrong network. They are per-COIN, not
	per-family, and nothing else in this file pins the ones that are not on
	the happy path.
	"""

	def setUp(self):
		_version, self.h160 = addresses._b58check_decode(GENESIS)

	def accepts(self, rail, version, mode):
		verdict, reason = validate(rail, b58check(version, self.h160), mode)
		self.assertEqual(verdict, OK, f"{rail}/{mode} {version}: {reason}")

	def test_bitcoin_mainnet_p2pkh_and_p2sh(self):
		self.accepts("btc", (0x00,), "mainnet")
		self.accepts("btc", (0x05,), "mainnet")

	def test_bitcoin_testnet_p2pkh_and_p2sh(self):
		self.accepts("btc", (0x6F,), "testnet")
		self.accepts("btc", (0xC4,), "testnet")

	def test_dash_mainnet_p2pkh_and_p2sh(self):
		self.accepts("dash", (0x4C,), "mainnet")
		self.accepts("dash", (0x10,), "mainnet")

	def test_dash_testnet_p2pkh_and_p2sh(self):
		self.accepts("dash", (0x8C,), "testnet")
		self.accepts("dash", (0x13,), "testnet")

	def test_zcash_mainnet_both_transparent_prefixes(self):
		self.accepts("zec", (0x1C, 0xB8), "mainnet")
		self.accepts("zec", (0x1C, 0xBD), "mainnet")

	def test_zcash_testnet_both_transparent_prefixes(self):
		self.accepts("zec", (0x1D, 0x25), "testnet")
		self.accepts("zec", (0x1C, 0xBA), "testnet")

	def test_monero_accepts_all_three_prefixes_per_network(self):
		for prefix in (18, 19, 42):
			with self.subTest(mainnet=prefix):
				self.assertEqual(validate("xmr", monero_address(bytes([prefix])), "mainnet")[0], OK)
		for prefix in (53, 54, 63):
			with self.subTest(testnet=prefix):
				self.assertEqual(validate("xmr", monero_address(bytes([prefix])), "testnet")[0], OK)

	def test_the_cross_network_refusal_names_the_right_networks(self):
		# The message tells an operator which network the address IS for.
		# Getting the two round the wrong way sends them looking in the wrong
		# wallet, and the verdict alone cannot catch that.
		_verdict, reason = validate("btc", GENESIS, "testnet")
		self.assertIn("valid mainnet address", reason)
		self.assertIn("this sale is testnet", reason)
		_verdict, reason = validate("btc", "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx", "mainnet")
		self.assertIn("valid testnet address", reason)
		self.assertIn("this sale is mainnet", reason)


class EvmWellFormedness(unittest.TestCase):
	def test_forty_two_characters_without_the_prefix_is_refused(self):
		# Both halves of the guard matter. With `and` instead of `or`, a
		# 42-character string that does not start with 0x sails past.
		verdict, reason = validate("eth", "ab" * 21, "mainnet")
		self.assertEqual(verdict, REFUSED)
		self.assertIn("0x followed by 40 hex", reason)

	def test_the_prefix_alone_is_not_enough(self):
		verdict, _reason = validate("eth", "0x" + "ab" * 19, "mainnet")
		self.assertEqual(verdict, REFUSED)

	def test_the_character_after_f_is_not_hexadecimal(self):
		# 'g' is the realistic typo, and the only one that separates base 16
		# from a wider base: every other non-hex letter is rejected by both.
		verdict, reason = validate("eth", "0x" + "g" * 40, "mainnet")
		self.assertEqual(verdict, REFUSED)
		self.assertIn("not hexadecimal", reason)
