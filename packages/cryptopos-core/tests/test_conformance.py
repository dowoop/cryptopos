"""Third-party rail mistakes are caught before a host offers them."""

import unittest

from cryptopos_core.catalog import BUILTIN_RAILS
from cryptopos_core.conformance import conformance_issues, require_conformant
from cryptopos_core.errors import InvalidRailPlugin
from cryptopos_core.plugin import Readiness


class Conformance(unittest.TestCase):
	def test_every_builtin_explains_each_declared_capability(self):
		for rail in BUILTIN_RAILS:
			with self.subTest(rail=rail.key):
				self.assertEqual(conformance_issues(rail, {}), ())

	def test_readiness_cannot_claim_an_undeclared_capability(self):
		class Liar:
			def __init__(self, base):
				self.__dict__.update(base.__dict__)
				self.key = base.key
				self.network = base.network
				self.asset = base.asset
				self.capabilities = base.capabilities

			def readiness(self, configuration):
				return Readiness(self.key, frozenset({"observation"}))

			def validate_recipient(self, recipient):
				return "unchecked", "fixture"

			def capture_baseline(self, recipient, configuration):
				return None

			def create_request(self, intent):
				return None

			def observe(self, intent, configuration, previous=None):
				return None

			def settle(self, intent, observations, claimed_transaction_ids=frozenset()):
				return None

		issues = conformance_issues(Liar(BUILTIN_RAILS[-1]), {})
		self.assertIn("did not declare", issues[0])

	def test_readiness_exceptions_become_a_conformance_failure(self):
		class Broken(type(BUILTIN_RAILS[-1])):
			def readiness(self, configuration):
				raise RuntimeError("boom")

		broken = Broken(
			"broken",
			BUILTIN_RAILS[-1].network,
			BUILTIN_RAILS[-1].asset,
			binding_category="not-unconditional",
			blocker="fixture",
		)
		with self.assertRaises(InvalidRailPlugin) as caught:
			require_conformant(broken, {})
		self.assertIn("readiness raised RuntimeError", caught.exception.reason)


if __name__ == "__main__":
	unittest.main()
