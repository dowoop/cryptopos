"""The promises on the tin, asserted mechanically.

Two claims sell this package: it has no dependencies, and it knows nothing
about any host framework. Both are the kind of claim that decays silently --
one convenient `import requests`, one `frappe.throw` added while fixing
something else, and the package still passes every other test in this suite
while no longer being what its README says it is.

So both are checked here by reading the source, not by trusting it.
"""

import ast
import hashlib
import unittest
from pathlib import Path

import cryptopos_core

PACKAGE = Path(cryptopos_core.__file__).parent

# Everything the package is allowed to import, and nothing else. Every name
# here is standard library. Adding to this list is a deliberate act; adding a
# NON-stdlib name to it is the thing this test exists to make impossible to do
# by accident.
ALLOWED_IMPORTS = {
	"__future__",
	"collections",
	# Independent feeds are bounded by the slowest timeout, not their sum.
	"concurrent",
	# Timestamping a quote: a rate is a number, a source AND a time.
	"datetime",
	"dataclasses",
	# Feed prices are decimal strings and Decimal is the type that holds one
	# exactly. See rates.py for why this is defensive rather than a fix.
	"decimal",
	# Base58Check checksums (sha256d) and Solana reference keys. Added
	# deliberately, which is the only way a name gets onto this list.
	"hashlib",
	# Payment-rail plugins are discovered from installed package metadata. The
	# discovery is explicit; importing cryptopos_core itself loads no plugins.
	"importlib",
	# Registration verifies bound method call shapes without executing plugin
	# operations against fabricated payment data.
	"inspect",
	"itertools",
	"json",
	"re",
	# Freeze rail configuration after construction.
	"types",
	"typing",
	"urllib",
}

# Names that must never appear anywhere in the package, in any form. A library
# that reaches for its host is a library nobody else can use.
FORBIDDEN_SUBSTRINGS = ("frappe", "erpnext", "bench")


def source_files():
	return sorted(PACKAGE.glob("*.py"))


def imported_roots(tree):
	"""Top-level module names imported absolutely; relative imports are ours."""
	roots = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Import):
			for alias in node.names:
				roots.add(alias.name.split(".")[0])
		elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
			roots.add(node.module.split(".")[0])
	return roots


class Dependencies(unittest.TestCase):
	def installed_distribution(self):
		from importlib.metadata import PackageNotFoundError, distribution

		try:
			return distribution("cryptopos-core")
		except PackageNotFoundError:
			self.skipTest("running from the source tree, not an installed distribution")

	def test_there_are_source_files_to_check(self):
		# Guards the rest of this class against silently passing on an empty
		# glob if the package layout ever moves.
		self.assertGreaterEqual(len(source_files()), 5)

	def test_imports_nothing_outside_the_standard_library(self):
		for path in source_files():
			with self.subTest(path.name):
				roots = imported_roots(ast.parse(path.read_text(encoding="utf-8")))
				unexpected = roots - ALLOWED_IMPORTS
				self.assertEqual(unexpected, set(), f"{path.name} imports {unexpected}")

	def test_declares_no_dependencies(self):
		# The installed metadata is what a user actually gets, so assert on
		# that rather than on pyproject.toml -- which is not shipped in a wheel.
		from importlib.metadata import PackageNotFoundError, requires

		try:
			declared = requires("cryptopos-core")
		except PackageNotFoundError:
			self.skipTest("running from the source tree, not an installed distribution")
		self.assertIn(declared, (None, []), f"declares {declared}")

	def test_installed_version_and_rail_entry_points_match_the_source(self):
		distribution = self.installed_distribution()
		self.assertEqual(distribution.version, cryptopos_core.__version__)
		points = {
			point.name: point.value for point in distribution.entry_points if point.group == "cryptopos.rails"
		}
		self.assertEqual(
			points,
			{
				"bitcoin-testnet4": "cryptopos_core.bitcoin:bitcoin_testnet4",
				"ethereum-sepolia-eth": "cryptopos_core.evm:ethereum_sepolia",
				"ethereum-sepolia-usdc": "cryptopos_core.evm:usdc_ethereum_sepolia",
				"polygon-amoy-usdc": "cryptopos_core.evm:usdc_polygon_amoy",
			},
		)


class FrameworkFree(unittest.TestCase):
	def test_no_module_mentions_a_host_framework(self):
		for path in source_files():
			text = path.read_text(encoding="utf-8").lower()
			for forbidden in FORBIDDEN_SUBSTRINGS:
				with self.subTest(module=path.name, term=forbidden):
					self.assertNotIn(forbidden, text)

	def test_imports_cleanly_with_the_framework_poisoned(self):
		# The strongest form of the claim: if anything reached for frappe at
		# import time, binding the name to None would break it loudly.
		import subprocess
		import sys

		script = (
			"import sys; sys.modules['frappe'] = None;"
			"import cryptopos_core;"
			"from cryptopos_core import rates, qr, chain, errors, rails, uri, addresses;"
			"assert rates.native_for(1099, 640_012_340, 8) == 1_717_154;"
			"assert qr.modules_for('x')['size'] == 21;"
			"assert chain.OotleReader(loyalty_component='').promise()[0] is None;"
			"assert len(rails.RAILS) == 12;"
			"assert rails.usd_cents_to_native(rails.RAILS['eth'], 625) == 1_785_000_000_000_000;"
			"a = 'tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx';"
			"assert uri.build_uri('btc', {'address': a}, 195_300, 'testnet')"
			" == 'bitcoin:' + a + '?amount=0.00195300';"
			"assert addresses.validate('btc', a, 'mainnet')[0] == 'refused';"
			"print('ok')"
		)
		result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(result.stdout.strip(), "ok")


class VendoredGenerator(unittest.TestCase):
	# Verified byte-for-byte against Project Nayuki's official master copy on
	# 2026-08-19. Pinning the bytes makes "vendored unchanged" an assertion,
	# not a comment that can silently become false after formatting or edits.
	UPSTREAM_SHA256 = "9f4ed1dd201dcb92b1bc0d6e14f46c754bcff0ce48580c5d7e8ace8f6926c8ef"

	def test_carries_its_upstream_notice(self):
		# It is MIT from Project Nayuki and stays attributed. Reformatting it
		# would also destroy the property the docstring claims: that it is
		# byte-identical to the copy every other surface uses.
		text = (PACKAGE / "qrcodegen.py").read_text(encoding="utf-8")
		self.assertIn("Project Nayuki", text)
		self.assertIn("MIT License", text)

	def test_is_byte_identical_to_the_verified_upstream_copy(self):
		digest = hashlib.sha256((PACKAGE / "qrcodegen.py").read_bytes()).hexdigest()
		self.assertEqual(digest, self.UPSTREAM_SHA256)


class PublicSurface(unittest.TestCase):
	def test_every_exported_name_resolves(self):
		for name in cryptopos_core.__all__:
			with self.subTest(name):
				self.assertTrue(hasattr(cryptopos_core, name))

	def test_version_is_exported_and_parseable(self):
		parts = cryptopos_core.__version__.split(".")
		self.assertGreaterEqual(len(parts), 2)
		self.assertTrue(all(part.isdigit() for part in parts[:2]))

	def test_errors_share_one_catchable_base(self):
		from cryptopos_core.errors import CryptoPosError, InvalidRate, RateUnavailable

		self.assertTrue(issubclass(RateUnavailable, CryptoPosError))
		self.assertTrue(issubclass(InvalidRate, CryptoPosError))
		self.assertTrue(issubclass(CryptoPosError, Exception))


if __name__ == "__main__":
	unittest.main()
