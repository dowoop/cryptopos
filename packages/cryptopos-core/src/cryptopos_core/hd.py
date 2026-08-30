"""Watch-only BIP-32 public derivation and Bitcoin/EVM receiving addresses.

This module accepts only extended public keys. It has no private-key
derivation and no signing operation: the terminal can calculate where money
should arrive, but nothing in this module can spend it.
"""

import hashlib
from dataclasses import dataclass

from ._keccak import keccak256
from .addresses import to_eip55
from .errors import CryptoPosError

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {character: value for value, character in enumerate(_B58_ALPHABET)}

_PUBLIC_VERSIONS = frozenset((0x0488B21E, 0x043587CF, 0x04B24746, 0x045F1CF6))
_PRIVATE_VERSIONS = frozenset((0x0488ADE4, 0x04358394, 0x04B2430C, 0x045F18BC))
_EVM_PUBLIC_VERSION = 0x0488B21E

_FIELD_PRIME = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_SQRT_EXPONENT = 0x3FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFBFFFFF0C
_CURVE_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_GENERATOR = (
	0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
	0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3


class InvalidExtendedKey(CryptoPosError):
	"""An extended key or requested public derivation is unsafe or invalid."""


@dataclass(frozen=True)
class ExtendedKey:
	version: int
	depth: int
	fingerprint: bytes
	child_number: int
	chain_code: bytes
	public_key: bytes


def _b58_decode(text):
	number = 0
	for character in text:
		value = _B58_INDEX.get(character)
		if value is None:
			raise InvalidExtendedKey("extended key contains a character outside base58")
		number = number * 58 + value
	body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
	return b"\0" * (len(text) - len(text.lstrip("1"))) + body


def _point_from_public_key(public_key):
	if not isinstance(public_key, bytes) or len(public_key) != 33 or public_key[0] not in (2, 3):
		raise InvalidExtendedKey("extended key does not contain a compressed public key")
	x = int.from_bytes(public_key[1:], "big")
	if x >= _FIELD_PRIME:
		raise InvalidExtendedKey("compressed public key x-coordinate is outside secp256k1")
	right = (pow(x, 3, _FIELD_PRIME) + 7) % _FIELD_PRIME
	y = pow(right, _SQRT_EXPONENT, _FIELD_PRIME)
	if pow(y, 2, _FIELD_PRIME) != right:
		raise InvalidExtendedKey("compressed public key is not a point on secp256k1")
	if y % 2 != public_key[0] % 2:
		y = _FIELD_PRIME - y
	return x, y


def _point_add(left, right):
	if left is None:
		return right
	if right is None:
		return left
	x1, y1 = left
	x2, y2 = right
	if x1 == x2 and (y1 + y2) % _FIELD_PRIME == 0:
		return None
	if left == right:
		slope = (3 * x1 * x1) * pow(2 * y1, -1, _FIELD_PRIME)
	else:
		slope = (y2 - y1) * pow(x2 - x1, -1, _FIELD_PRIME)
	slope %= _FIELD_PRIME
	x3 = (slope * slope - x1 - x2) % _FIELD_PRIME
	y3 = (slope * (x1 - x3) - y1) % _FIELD_PRIME
	return x3, y3


def _point_multiply(scalar, point):
	result = None
	addend = point
	while scalar:
		if scalar & 1:
			result = _point_add(result, addend)
		addend = _point_add(addend, addend)
		scalar >>= 1
	return result


def _public_key_from_point(point):
	if point is None:
		raise InvalidExtendedKey("public derivation produced the point at infinity")
	x, y = point
	return bytes((2 + y % 2,)) + x.to_bytes(32, "big")


def _hash160(payload):
	return hashlib.new("ripemd160", hashlib.sha256(payload).digest()).digest()


def _hmac_sha512(key, message):
	"""RFC 2104 HMAC for SHA-512's 128-byte block size."""
	padded = key.ljust(128, b"\0")
	inner = bytes(byte ^ 0x36 for byte in padded)
	outer = bytes(byte ^ 0x5C for byte in padded)
	return hashlib.sha512(outer + hashlib.sha512(inner + message).digest()).digest()


def parse_extended_key(text: str) -> ExtendedKey:
	"""Parse a supported Base58Check extended public key."""
	if not isinstance(text, str) or not text:
		raise InvalidExtendedKey("extended key must be non-empty text")
	if len(text) > 128:
		raise InvalidExtendedKey("extended key text is too long")
	raw = _b58_decode(text)
	if len(raw) != 82:
		raise InvalidExtendedKey("extended key must decode to a 78-byte payload and 4-byte checksum")
	payload, checksum = raw[:-4], raw[-4:]
	expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
	if checksum != expected:
		raise InvalidExtendedKey("extended key checksum is invalid")
	version = int.from_bytes(payload[:4], "big")
	if version in _PRIVATE_VERSIONS:
		raise InvalidExtendedKey(
			"private extended keys are refused; this watch-only module derives public keys only"
		)
	if version not in _PUBLIC_VERSIONS:
		raise InvalidExtendedKey("extended key version is not xpub, tpub, zpub, or vpub")
	depth = payload[4]
	fingerprint = payload[5:9]
	child_number = int.from_bytes(payload[9:13], "big")
	if depth == 0 and (fingerprint != b"\0\0\0\0" or child_number != 0):
		raise InvalidExtendedKey("a depth-zero extended key must have a zero parent and child number")
	chain_code = payload[13:45]
	public_key = payload[45:]
	_point_from_public_key(public_key)
	return ExtendedKey(version, depth, fingerprint, child_number, chain_code, public_key)


def derive_child(key: ExtendedKey, index: int) -> ExtendedKey:
	"""Derive one non-hardened BIP-32 child from an extended public key."""
	if not isinstance(key, ExtendedKey):
		raise InvalidExtendedKey("public derivation requires an ExtendedKey")
	if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= 2**32:
		raise InvalidExtendedKey("child index must be an integer from 0 through 2**32 - 1")
	if index >= 2**31:
		raise InvalidExtendedKey("hardened children are underivable from an extended public key")
	if key.depth >= 255:
		raise InvalidExtendedKey("an extended key cannot be derived beyond depth 255")
	parent = _point_from_public_key(key.public_key)
	if not isinstance(key.chain_code, bytes) or len(key.chain_code) != 32:
		raise InvalidExtendedKey("extended key chain code must be 32 bytes")
	digest = _hmac_sha512(key.chain_code, key.public_key + index.to_bytes(4, "big"))
	left = int.from_bytes(digest[:32], "big")
	# BIP-32 says to move to the next index in these astronomically rare cases.
	# This single-index API must raise instead; returning a different index would
	# silently break the caller's address allocation.
	if left >= _CURVE_ORDER:
		raise InvalidExtendedKey("public derivation produced an invalid BIP-32 scalar")
	child_point = _point_add(_point_multiply(left, _GENERATOR), parent)
	if child_point is None:
		raise InvalidExtendedKey("public derivation produced the point at infinity")
	return ExtendedKey(
		key.version,
		key.depth + 1,
		_hash160(key.public_key)[:4],
		index,
		digest[32:],
		_public_key_from_point(child_point),
	)


def derive_path(key: ExtendedKey, path: str) -> ExtendedKey:
	"""Derive a slash-separated, non-hardened path relative to ``key``."""
	if not isinstance(path, str) or not path:
		raise InvalidExtendedKey("derivation path must be non-empty text such as '0/17'")
	if path == "m" or path.startswith("m/"):
		raise InvalidExtendedKey("a leading m refers to a master key this watch-only module cannot have")
	current = key
	for component in path.split("/"):
		if not component or not component.isascii() or not component.isdecimal():
			raise InvalidExtendedKey("derivation path components must be non-negative decimal integers")
		current = derive_child(current, int(component))
	return current


def _bech32_polymod(values):
	generator = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
	checksum = 1
	for value in values:
		top = checksum >> 25
		checksum = ((checksum & 0x1FFFFFF) << 5) ^ value
		for bit, constant in enumerate(generator):
			if (top >> bit) & 1:
				checksum ^= constant
	return checksum


def _bech32_hrp_expand(hrp):
	return [ord(character) >> 5 for character in hrp] + [0] + [ord(character) & 31 for character in hrp]


def _convert_bits(data, from_bits, to_bits):
	accumulator = bits = 0
	converted = []
	maximum = (1 << to_bits) - 1
	for value in data:
		if value < 0 or value >> from_bits:
			raise InvalidExtendedKey("witness program contains a value outside its bit width")
		accumulator = (accumulator << from_bits) | value
		bits += from_bits
		while bits not in range(to_bits):
			bits -= to_bits
			converted.append((accumulator >> bits) & maximum)
	if bits:
		converted.append((accumulator << (to_bits - bits)) & maximum)
	return converted


def _bech32_encode(hrp, data, bech32m=False):
	if not isinstance(hrp, str) or not hrp:
		raise InvalidExtendedKey("bech32 human-readable part must be non-empty text")
	if any(not 33 <= ord(character) <= 126 for character in hrp):
		raise InvalidExtendedKey("bech32 human-readable part must contain printable ASCII")
	if not isinstance(bech32m, bool):
		raise InvalidExtendedKey("bech32m selector must be a boolean")
	hrplower = hrp.lower()
	values = list(data)
	if any(
		isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 31 for value in values
	):
		raise InvalidExtendedKey("bech32 data values must be integers from 0 through 31")
	if len(hrplower) + 1 + len(values) + 6 > 90:
		raise InvalidExtendedKey("bech32 text would exceed its 90-character limit")
	constant = _BECH32M_CONST if bech32m else _BECH32_CONST
	polymod = _bech32_polymod(_bech32_hrp_expand(hrplower) + values + [0] * 6) ^ constant
	checksum = [(polymod >> (5 * (5 - index))) & 31 for index in range(6)]
	return hrplower + "1" + "".join(_BECH32_CHARSET[value] for value in values + checksum)


def p2wpkh_address(key: ExtendedKey, hrp: str) -> str:
	"""Encode the key as a BIP-84 witness-v0 P2WPKH address."""
	if not isinstance(key, ExtendedKey):
		raise InvalidExtendedKey("address derivation requires an ExtendedKey")
	_point_from_public_key(key.public_key)
	program = _hash160(key.public_key)
	return _bech32_encode(hrp, [0, *_convert_bits(program, 8, 5)])


def evm_address(key: ExtendedKey) -> str:
	"""Encode an xpub child as its EIP-55 checksummed EVM address."""
	if not isinstance(key, ExtendedKey):
		raise InvalidExtendedKey("address derivation requires an ExtendedKey")
	if key.version != _EVM_PUBLIC_VERSION:
		raise InvalidExtendedKey(
			"EVM address derivation requires xpub version bytes; "
			"Bitcoin tpub, zpub, and vpub keys are refused"
		)
	x, y = _point_from_public_key(key.public_key)
	public_point = x.to_bytes(32, "big") + y.to_bytes(32, "big")
	return to_eip55(keccak256(public_point)[-20:].hex())
