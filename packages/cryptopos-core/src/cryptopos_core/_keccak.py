"""Keccak-256, because EIP-55 needs it and `hashlib` does not have it.

`hashlib.sha3_256` is NIST SHA-3, which differs from Keccak-256 by one
padding byte (0x06 vs 0x01) and therefore produces entirely different
digests. Ethereum standardised on the original Keccak submission, so an
EIP-55 checksum computed with `hashlib.sha3_256` is wrong for every address.
That single byte is the whole reason this file exists.

This is the Keccak-f[1600] permutation at rate 1088, which is ~70 lines and
carries no dependency. `test_addresses.KeccakVectors` pins it against the
published digests, including the empty-string constant every implementation
is checked against.

Private to the package: nothing outside `addresses.py` should need it, and a
general-purpose hashing API is not what this package is for.
"""

_ROUND_CONSTANTS = (
	0x0000000000000001,
	0x0000000000008082,
	0x800000000000808A,
	0x8000000080008000,
	0x000000000000808B,
	0x0000000080000001,
	0x8000000080008081,
	0x8000000000008009,
	0x000000000000008A,
	0x0000000000000088,
	0x0000000080008009,
	0x000000008000000A,
	0x000000008000808B,
	0x800000000000008B,
	0x8000000000008089,
	0x8000000000008003,
	0x8000000000008002,
	0x8000000000000080,
	0x000000000000800A,
	0x800000008000000A,
	0x8000000080008081,
	0x8000000000008080,
	0x0000000080000001,
	0x8000000080008008,
)

_ROTATIONS = (
	(0, 36, 3, 41, 18),
	(1, 44, 10, 45, 2),
	(62, 6, 43, 15, 61),
	(28, 55, 25, 21, 56),
	(27, 20, 39, 8, 14),
)

_MASK = (1 << 64) - 1
_RATE_BYTES = 136  # 1088 bits, the rate for Keccak-256


def _rotl(value, shift):
	return ((value << shift) | (value >> (64 - shift))) & _MASK


def _permute(lanes):
	"""Keccak-f[1600], applied to `lanes` IN PLACE. Returns nothing."""
	for round_constant in _ROUND_CONSTANTS:
		# theta
		columns = [lanes[x][0] ^ lanes[x][1] ^ lanes[x][2] ^ lanes[x][3] ^ lanes[x][4] for x in range(5)]
		for x in range(5):
			delta = columns[(x - 1) % 5] ^ _rotl(columns[(x + 1) % 5], 1)
			for y in range(5):
				lanes[x][y] ^= delta

		# rho and pi
		moved = [[0] * 5 for _ in range(5)]
		for x in range(5):
			for y in range(5):
				moved[y][(2 * x + 3 * y) % 5] = _rotl(lanes[x][y], _ROTATIONS[x][y])

		# chi
		for x in range(5):
			for y in range(5):
				lanes[x][y] = moved[x][y] ^ ((~moved[(x + 1) % 5][y]) & moved[(x + 2) % 5][y])

		# iota
		lanes[0][0] ^= round_constant


def keccak256(data):
	"""Keccak-256 of `data` (bytes), as 32 bytes."""
	lanes = [[0] * 5 for _ in range(5)]

	# Pad10*1 with Keccak's 0x01 domain byte -- NOT SHA-3's 0x06.
	padded = bytearray(data)
	padded.append(0x01)
	while len(padded) % _RATE_BYTES != 0:
		padded.append(0x00)
	padded[-1] ^= 0x80

	for offset in range(0, len(padded), _RATE_BYTES):
		block = padded[offset : offset + _RATE_BYTES]
		for i in range(_RATE_BYTES // 8):
			lane = int.from_bytes(block[i * 8 : i * 8 + 8], "little")
			lanes[i % 5][i // 5] ^= lane
		_permute(lanes)

	# The squeeze is four lanes and no second permutation, and that is a fact
	# about these two constants rather than a shortcut. The rate is 136 bytes
	# -- seventeen lanes -- and the digest is 32, so the first squeeze pass
	# always has more than enough state and the general "permute and squeeze
	# again" loop could never run. Writing the loop anyway would leave a
	# branch in the one file here that must be exactly right, which no test
	# could ever reach and no reader could ever check.
	return b"".join(lanes[i][0].to_bytes(8, "little") for i in range(4))
