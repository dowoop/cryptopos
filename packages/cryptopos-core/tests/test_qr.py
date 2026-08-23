"""QR: the symbol a customer actually points a phone at.

There is no decoder in the standard library, so this asserts structure rather
than round-tripping. Structure is enough to catch what actually goes wrong
here: a grid that is the wrong shape, a quiet zone that quietly disappears, or
a generator swap that changes the symbol between two surfaces of the same
terminal.
"""

import unittest

from cryptopos_core import qr, qrcodegen

FINDER = (
	"1111111",
	"1000001",
	"1011101",
	"1011101",
	"1011101",
	"1000001",
	"1111111",
)

URI = "bitcoin:tb1qexample?amount=0.00017171"


class Shape(unittest.TestCase):
	def setUp(self):
		self.grid = qr.modules_for(URI)

	def test_returns_the_three_documented_keys(self):
		self.assertEqual(sorted(self.grid), ["quiet", "rows", "size"])

	def test_rows_are_square_and_match_size(self):
		size = self.grid["size"]
		self.assertEqual(len(self.grid["rows"]), size)
		for index, row in enumerate(self.grid["rows"]):
			self.assertEqual(len(row), size, f"row {index}")

	def test_rows_are_bits_and_nothing_else(self):
		# The client draws these directly. Anything but "0"/"1" is a rendering
		# bug that only shows up as a scanner that will not lock on.
		self.assertEqual(set("".join(self.grid["rows"])), {"0", "1"})

	def test_size_is_a_valid_qr_version(self):
		# Versions 1..40 are 21..177 modules, in steps of 4.
		size = self.grid["size"]
		self.assertGreaterEqual(size, 21)
		self.assertLessEqual(size, 177)
		self.assertEqual((size - 21) % 4, 0)

	def test_grows_with_the_payload(self):
		sizes = [qr.modules_for("x" * n)["size"] for n in (1, 60, 300)]
		self.assertEqual(sizes, sorted(sizes))
		self.assertLess(sizes[0], sizes[-1])


class QuietZone(unittest.TestCase):
	def test_is_the_four_modules_the_spec_requires(self):
		# Scanners fail INTERMITTENTLY without it, which is the worst way for
		# a payment surface to fail: it looks like the customer's phone.
		self.assertEqual(qr.QUIET_ZONE_MODULES, 4)
		self.assertEqual(qr.modules_for(URI)["quiet"], 4)

	def test_is_declared_but_not_baked_into_the_rows(self):
		# The client adds it, so the payload stays small. If it were baked in
		# the first four rows would be blank.
		rows = qr.modules_for(URI)["rows"]
		self.assertTrue(rows[0].startswith("1"))


class FinderPatterns(unittest.TestCase):
	"""Three corners carry a fixed 7x7 pattern. If those are wrong, nothing scans."""

	def setUp(self):
		self.grid = qr.modules_for(URI)
		self.rows = self.grid["rows"]
		self.size = self.grid["size"]

	def corner(self, top, left):
		return tuple(row[left : left + 7] for row in self.rows[top : top + 7])

	def test_top_left(self):
		self.assertEqual(self.corner(0, 0), FINDER)

	def test_top_right(self):
		self.assertEqual(self.corner(0, self.size - 7), FINDER)

	def test_bottom_left(self):
		self.assertEqual(self.corner(self.size - 7, 0), FINDER)


class SameEncoderEverywhere(unittest.TestCase):
	def test_is_deterministic(self):
		# Two surfaces rendering the same URI must produce the same symbol.
		self.assertEqual(qr.modules_for(URI), qr.modules_for(URI))

	def test_uses_medium_error_correction(self):
		# Displayed on a screen at the counter, not printed on a crumpled
		# receipt: a higher level costs module density the scanner wants.
		expected = qrcodegen.QrCode.encode_text(URI, qrcodegen.QrCode.Ecc.MEDIUM)
		self.assertEqual(qr.modules_for(URI)["size"], expected.get_size())

	def test_matches_the_vendored_generator_module_for_module(self):
		code = qrcodegen.QrCode.encode_text(URI, qrcodegen.QrCode.Ecc.MEDIUM)
		size = code.get_size()
		expected = ["".join("1" if code.get_module(x, y) else "0" for x in range(size)) for y in range(size)]
		self.assertEqual(qr.modules_for(URI)["rows"], expected)

	def test_handles_an_empty_string(self):
		grid = qr.modules_for("")
		self.assertEqual(grid["size"], 21)


if __name__ == "__main__":
	unittest.main()
