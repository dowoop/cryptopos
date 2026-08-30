"""Addresses — the one check that stands between a sale and losing the money.

Everything else in this package can be wrong and recoverable. A rate that is
stale prices a sale badly; a QR that fails to scan is retried. An address that
is wrong is money sent to somewhere nobody holds a key to, and there is no
step after that.

So this module is checksums, not pattern-matching. `^bc1[a-z0-9]{39}$` accepts
a single-character typo that the bech32 checksum rejects, and a typo is the
common case — an operator pasting a receiving address into a settings field
with one character dropped by a mis-selected copy.

**The verdict is a three-valued thing and that is the point.**

    "ok"          a checksum verified AND the network matches
    "refused"     something was checkably wrong -- never charge this
    "unchecked"   this build cannot verify addresses for this rail

`unchecked` exists because the alternative is a lie. Tari's address format is
not specified anywhere this build could implement it from, and returning "ok"
for an address nothing verified would make the whole verdict worthless — a
caller cannot tell a checked "ok" from an unchecked one. Callers must decide
what `unchecked` means for them; `uri.build_uri` refuses it on mainnet and
allows it elsewhere.

**Network binding is half the value.** A mainnet URI carrying a testnet
address is well-formed, scannable, and sends real money into a hole. Bitcoin,
Dash, Zcash and Monero all encode their network in the address, so the
mismatch is detectable and is refused. EVM and Solana do not encode a
network — the same address is valid on every chain in the family — so for
those the network genuinely cannot be checked, and this module says so
rather than implying a check it did not perform.
"""

from ._keccak import keccak256
from .modes import VALID_MODES, address_network

OK = "ok"
REFUSED = "refused"
UNCHECKED = "unchecked"

# Every implemented address format is below 200 characters. Refuse before a
# base58 decoder starts arbitrary-precision multiplication: recipient strings
# are commonly supplied over an API, and an attacker should not get to choose
# how much CPU or memory a validation request consumes.
MAX_ADDRESS_TEXT_LENGTH = 256

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {character: value for value, character in enumerate(_B58_ALPHABET)}

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3


# ---------------------------------------------------------------------------
# Base58Check -- Bitcoin, Dash, Zcash transparent
# ---------------------------------------------------------------------------
def _b58_decode(text):
	"""Base58 -> bytes, or None if a character is outside the alphabet."""
	number = 0
	for character in text:
		value = _B58_INDEX.get(character)
		if value is None:
			return None
		number = number * 58 + value
	body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
	leading = len(text) - len(text.lstrip("1"))
	return b"\x00" * leading + body


def _b58check_decode(text, version_length=1):
	"""Return (version, payload) if the checksum verifies, else None.

	The checksum is the first four bytes of a double SHA-256 over everything
	before it. This is the check a regex cannot do and the reason one is not
	used here.
	"""
	import hashlib

	raw = _b58_decode(text)
	if raw is None or len(raw) < version_length + 4:
		return None
	body, checksum = raw[:-4], raw[-4:]
	expected = hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4]
	if checksum != expected:
		return None
	return body[:version_length], body[version_length:]


# ---------------------------------------------------------------------------
# Bech32 / Bech32m -- BIP-173 and BIP-350
# ---------------------------------------------------------------------------
def _bech32_polymod(values):
	generator = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
	checksum = 1
	for value in values:
		top = checksum >> 25
		checksum = ((checksum & 0x1FFFFFF) << 5) ^ value
		for bit in range(5):
			checksum ^= generator[bit] if ((top >> bit) & 1) else 0
	return checksum


def _bech32_hrp_expand(hrp):
	return [ord(character) >> 5 for character in hrp] + [0] + [ord(character) & 31 for character in hrp]


def _bech32_decode(text):
	"""Return (hrp, data5bit, spec) where spec is "bech32" or "bech32m"."""
	# Mixed case is forbidden outright: the checksum is defined over one case,
	# so a mixed-case string is not merely unusual, it is unverifiable.
	if text != text.lower() and text != text.upper():
		return None
	text = text.lower()
	if len(text) < 8 or len(text) > 90:
		return None
	position = text.rfind("1")
	if position < 1 or position + 7 > len(text):
		return None
	hrp, data_part = text[:position], text[position + 1 :]
	if any(not (33 <= ord(character) <= 126) for character in hrp):
		return None
	data = []
	for character in data_part:
		index = _BECH32_CHARSET.find(character)
		if index == -1:
			return None
		data.append(index)
	checksum = _bech32_polymod(_bech32_hrp_expand(hrp) + data)
	if checksum == _BECH32_CONST:
		return hrp, data[:-6], "bech32"
	if checksum == _BECH32M_CONST:
		return hrp, data[:-6], "bech32m"
	return None


def _convert_bits(data, from_bits, to_bits, pad=True):
	accumulator = 0
	bits = 0
	result = []
	maximum = (1 << to_bits) - 1
	for value in data:
		if value < 0 or (value >> from_bits):
			return None
		accumulator = (accumulator << from_bits) | value
		bits += from_bits
		while bits >= to_bits:
			bits -= to_bits
			result.append((accumulator >> bits) & maximum)
	if pad:
		if bits:
			result.append((accumulator << (to_bits - bits)) & maximum)
	elif bits >= from_bits or ((accumulator << (to_bits - bits)) & maximum):
		return None
	return result


def _segwit_decode(address, expected_hrps):
	"""Return (hrp, witness_version, program) for a valid segwit address."""
	decoded = _bech32_decode(address)
	if decoded is None:
		return None
	hrp, data, spec = decoded
	if hrp not in expected_hrps or not data:
		return None
	witness_version = data[0]
	program = _convert_bits(data[1:], 5, 8, pad=False)
	if program is None or len(program) < 2 or len(program) > 40:
		return None
	if witness_version > 16:
		return None
	# BIP-350: v0 must be bech32, v1+ must be bech32m. Accepting either for
	# both is the bug BIP-350 was written to close.
	if witness_version == 0:
		if spec != "bech32" or len(program) not in (20, 32):
			return None
	elif spec != "bech32m":
		return None
	return hrp, witness_version, bytes(program)


# ---------------------------------------------------------------------------
# Monero base58 -- block-based, not the same encoding as Bitcoin's
# ---------------------------------------------------------------------------
# Monero encodes in 8-byte blocks of 11 characters rather than treating the
# whole payload as one big number. A standard base58 decoder produces garbage
# on a Monero address, which is why this is separate rather than reused.
_MONERO_BLOCK_SIZES = (0, 2, 3, 5, 6, 7, 9, 10, 11)


def _monero_b58_decode(text):
	raw = bytearray()
	for offset in range(0, len(text), 11):
		chunk = text[offset : offset + 11]
		if len(chunk) not in _MONERO_BLOCK_SIZES:
			return None
		byte_count = _MONERO_BLOCK_SIZES.index(len(chunk))
		number = 0
		for character in chunk:
			value = _B58_INDEX.get(character)
			if value is None:
				return None
			number = number * 58 + value
		if number >= (1 << (8 * byte_count)):
			return None
		raw += number.to_bytes(byte_count, "big")
	return bytes(raw)


def _read_varint(raw):
	value = 0
	shift = 0
	for index, byte in enumerate(raw):
		value |= (byte & 0x7F) << shift
		if not byte & 0x80:
			return value, index + 1
		shift += 7
		if shift > 63:
			return None, 0
	return None, 0


# ---------------------------------------------------------------------------
# Per-family checks
# ---------------------------------------------------------------------------
# Version bytes are per-COIN, not per-family. `btc` and `dash` share
# `family == "bitcoin"` in the rails table because they share a watcher
# shape, and their address version bytes are completely different -- so this
# module keys on the rail, never on the family. Keying on family here would
# accept a Bitcoin address for a Dash sale.
_BASE58_VERSIONS = {
	# rail: {mode: (accepted version byte tuples,)}
	"btc": {
		"mainnet": ((0x00,), (0x05,)),
		"testnet": ((0x6F,), (0xC4,)),
	},
	"dash": {
		"mainnet": ((0x4C,), (0x10,)),
		"testnet": ((0x8C,), (0x13,)),
	},
}

# Zcash transparent addresses carry a TWO byte version prefix.
_ZCASH_VERSIONS = {
	"mainnet": ((0x1C, 0xB8), (0x1C, 0xBD)),
	"testnet": ((0x1D, 0x25), (0x1C, 0xBA)),
}

_SEGWIT_HRPS = {"mainnet": ("bc",), "testnet": ("tb",)}

_MONERO_PREFIXES = {
	# Standard, integrated and subaddress prefixes per network.
	"mainnet": (18, 19, 42),
	"testnet": (53, 54, 63),
}

# A standard address and a subaddress encode two 32-byte public keys; an
# integrated address carries those plus an eight-byte payment id. The checksum
# verifies arbitrary bytes, so it cannot prove the payload has the shape a
# wallet accepts -- the prefix has to bind the exact decoded length as well.
_MONERO_LENGTHS = {
	18: 69,
	19: 77,
	42: 69,  # mainnet
	53: 69,
	54: 77,
	63: 69,  # testnet
	24: 69,
	25: 77,
	36: 69,  # stagenet (recognized only to explain refusal)
}
_MONERO_STAGENET_PREFIXES = (24, 25, 36)


def _check_bitcoin_like(rail_key, address, mode):
	network = address_network(mode)

	if rail_key == "btc":
		segwit = _segwit_decode(address, _SEGWIT_HRPS[network])
		if segwit is not None:
			return OK, ""
		# A valid segwit address for the OTHER network is the dangerous case,
		# and it deserves its own words rather than "malformed".
		other = "testnet" if network == "mainnet" else "mainnet"
		if _segwit_decode(address, _SEGWIT_HRPS[other]) is not None:
			return REFUSED, (
				f"that is a valid {other} address and this sale is {network}; "
				f"paying it would send {network} coin to a {other} key"
			)

	versions = _BASE58_VERSIONS.get(rail_key, {}).get(network, ())
	decoded = _b58check_decode(address)
	if decoded is not None:
		version, payload = decoded
		if tuple(version) in versions and len(payload) == 20:
			return OK, ""
		other = "testnet" if network == "mainnet" else "mainnet"
		if tuple(version) in _BASE58_VERSIONS.get(rail_key, {}).get(other, ()):
			return REFUSED, (
				f"that is a valid {other} address and this sale is {network}; "
				f"paying it would send {network} coin to a {other} key"
			)
		return REFUSED, "the address checksum verifies but its version byte is for another coin"

	return REFUSED, "the address checksum does not verify -- it is mistyped or truncated"


def _check_zcash(address, mode):
	network = address_network(mode)
	decoded = _b58check_decode(address, version_length=2)
	if decoded is not None:
		version, payload = decoded
		if tuple(version) in _ZCASH_VERSIONS[network] and len(payload) == 20:
			return OK, ""
		other = "testnet" if network == "mainnet" else "mainnet"
		if tuple(version) in _ZCASH_VERSIONS[other]:
			return REFUSED, (f"that is a valid {other} transparent address and this sale is {network}")
		return REFUSED, "the address checksum verifies but its version prefix is not Zcash transparent"

	# Shielded addresses are bech32 and this build cannot watch them anyway.
	decoded = _bech32_decode(address)
	if decoded is not None:
		return REFUSED, (
			"that is a shielded Zcash address; this build watches transparent "
			"addresses only and could never see the payment arrive"
		)
	return REFUSED, "the address checksum does not verify -- it is mistyped or truncated"


def _check_monero(address, mode):
	network = address_network(mode)
	raw = _monero_b58_decode(address)
	if raw is None or len(raw) < 69:
		return REFUSED, "the address is not valid Monero base58"
	body, checksum = raw[:-4], raw[-4:]
	if keccak256(body)[:4] != checksum:
		return REFUSED, "the address checksum does not verify -- it is mistyped or truncated"
	prefix, prefix_bytes = _read_varint(raw)
	if prefix is None:
		return REFUSED, "the address carries no readable network prefix"
	# Every currently defined Monero address prefix is below 128 and therefore
	# has one canonical byte. Accepting an overlong varint keeps the total blob
	# length unchanged only by stealing bytes from the two 32-byte public keys;
	# such a blob can carry a valid checksum but is not a wallet-usable address.
	if prefix_bytes != 1:
		return REFUSED, "the address carries a non-canonical network prefix"
	expected_length = _MONERO_LENGTHS.get(prefix)
	if expected_length is not None and len(raw) != expected_length:
		return REFUSED, (
			f"the address checksum verifies, but prefix {prefix} requires "
			f"{expected_length} decoded bytes and this address has {len(raw)}"
		)
	if prefix in _MONERO_PREFIXES[network]:
		return OK, ""
	other = "testnet" if network == "mainnet" else "mainnet"
	if prefix in _MONERO_PREFIXES[other] or prefix in _MONERO_STAGENET_PREFIXES:
		return REFUSED, f"that is a {other} (or stagenet) Monero address and this sale is {network}"
	return REFUSED, "the address prefix is not one this build recognises"


def _check_evm(address):
	if not address.startswith("0x") or len(address) != 42:
		return REFUSED, "an EVM address is 0x followed by 40 hex characters"
	body = address[2:]
	try:
		int(body, 16)
	except ValueError:
		return REFUSED, "the address contains characters that are not hexadecimal"

	if body == body.lower() or body == body.upper():
		# No EIP-55 checksum to verify. Well-formed, and a typo inside it is
		# undetectable -- so this is not "ok", it is unchecked, and a caller
		# on mainnet should treat it as such.
		return UNCHECKED, (
			"the address is well-formed but all one case, so it carries no "
			"EIP-55 checksum and a typo in it cannot be detected"
		)

	# Formatting owns the casing rule. Comparing with its canonical result
	# keeps validation and every producer on one implementation of EIP-55.
	if body != to_eip55(body)[2:]:
		return REFUSED, ("the EIP-55 checksum does not verify -- this address is mistyped")
	return OK, ""

# `to_eip55` is defined below with the other public entry points; Python
# resolves that name when validation runs, after module initialisation.

def _check_solana(address):
	raw = _b58_decode(address)
	if raw is None:
		return REFUSED, "the address contains characters outside the base58 alphabet"
	if len(raw) != 32:
		return REFUSED, f"a Solana address is 32 bytes; this decodes to {len(raw)}"
	# Solana addresses are raw ed25519 public keys with no checksum, so a
	# typo that still decodes to 32 bytes is undetectable. Length is the only
	# real check there is, and saying "ok" would overclaim it.
	return UNCHECKED, (
		"the address is a well-formed 32-byte key, but Solana addresses carry "
		"no checksum, so a typo cannot be detected"
	)


# Rails whose address format this build cannot verify at all, with the reason.
_UNVERIFIABLE = {
	"xtm": "Tari address encoding is not specified anywhere this build can implement from",
	"xtr": "Ootle address encoding is not specified anywhere this build can implement from",
}


def validate(rail_key, address, mode):
	"""Check `address` for `rail_key` in `mode`. Returns (verdict, reason).

	Verdict is `OK`, `REFUSED` or `UNCHECKED`. Never raises — a validator that
	throws on malformed input is a validator that turns a refusable sale into
	a crashed terminal.
	"""
	if not isinstance(address, str):
		return REFUSED, "the address must be text"
	address = address.strip()
	if not address:
		return REFUSED, "no address given"
	if len(address) > MAX_ADDRESS_TEXT_LENGTH:
		return REFUSED, f"the address is longer than the {MAX_ADDRESS_TEXT_LENGTH}-character safety limit"
	if mode not in VALID_MODES:
		return REFUSED, f"unknown mode {mode!r}; refusing to guess which network the address belongs to"
	if not isinstance(rail_key, str):
		return UNCHECKED, "the rail key must be text; no address check was selected"

	if rail_key in _UNVERIFIABLE:
		return UNCHECKED, _UNVERIFIABLE[rail_key]

	if rail_key in ("btc", "dash"):
		return _check_bitcoin_like(rail_key, address, mode)
	if rail_key == "zec":
		return _check_zcash(address, mode)
	if rail_key == "xmr":
		return _check_monero(address, mode)
	if rail_key in ("eth", "pol", "usdc-eth", "usdc-pol"):
		return _check_evm(address)
	if rail_key in ("sol", "usdc-sol"):
		return _check_solana(address)

	return UNCHECKED, f"no address check exists for rail {rail_key}"


def to_eip55(address):
	"""Format a well-formed EVM address as EIP-55; this does not verify intent.

	A checksum computed *after* a typo faithfully checksums the wrong address.
	Use this only to format bytes obtained from a trusted wallet or verified by
	proof of control; never use it to make pasted, unchecked recipient text pass
	a mainnet validation gate.
	"""
	body = address[2:].lower() if address.startswith("0x") else address.lower()
	digest = keccak256(body.encode("ascii")).hex()
	out = "".join(
		character.upper() if character.isalpha() and int(hash_character, 16) >= 8 else character
		for character, hash_character in zip(body, digest)
	)
	return "0x" + out
