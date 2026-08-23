"""Ootle payment observation never impersonates transaction attribution."""

import io
import json
import unittest
from unittest import mock

from cryptopos_core.chain import OotleReader
from cryptopos_core.errors import RailProviderError, UnsupportedCapability
from cryptopos_core.ootle import OOTLE_XTR_RESOURCE, OotleEsmeralda
from cryptopos_core.plugin import OBSERVATION, PAYMENT_REQUEST, SETTLEMENT, PaymentIntent

ENDPOINT = "https://ootle.example"
ACCOUNT = "component_" + "aa" * 16
VAULT = "ee" * 32


def entry(hexed):
	return {"value": {"hex": hexed}}


def account_body():
	return {
		"substate": {
			"Component": {
				"body": {"state": [[entry("01" * 32), entry(VAULT)]]},
			}
		}
	}


def vault_body(amount, kind="Stealth"):
	container = {kind: {"revealed_amount": amount}}
	return {"substate": {"Vault": {"resource_container": container}}}


class Response(io.BytesIO):
	def __enter__(self):
		return self

	def __exit__(self, *args):
		self.close()


class Transport:
	def __init__(self, amount=100, network="esmeralda", epoch=10, kind="Stealth"):
		self.amount = amount
		self.network = network
		self.epoch = epoch
		self.kind = kind
		self.calls = []

	def __call__(self, request, timeout=None):
		url = request.full_url
		self.calls.append((url, timeout))
		if url.endswith("/network"):
			body = {"network": self.network, "epoch": self.epoch}
		elif url.endswith("/substates/" + ACCOUNT):
			body = account_body()
		elif url.endswith("/substates/vault_" + VAULT):
			body = vault_body(self.amount, self.kind)
		else:
			raise OSError("unmapped " + url)
		return Response(json.dumps(body).encode())


class OotleRailTest(unittest.TestCase):
	def setUp(self):
		self.rail = OotleEsmeralda()

	def configuration(self):
		return {"endpoint": ENDPOINT, "timeout_seconds": 2}

	def intent(self, baseline):
		return PaymentIntent("sale-1", self.rail.key, ACCOUNT, 60, 100, 200, baseline=baseline)

	def baseline(self, transport):
		with mock.patch("cryptopos_core.chain._urlopen", transport):
			return self.rail.capture_baseline(ACCOUNT, self.configuration())

	def test_payment_observation_and_loyalty_reads_are_separate_surfaces(self):
		self.assertIn(OBSERVATION, self.rail.capabilities)
		self.assertNotIn(PAYMENT_REQUEST, self.rail.capabilities)
		self.assertNotIn(SETTLEMENT, self.rail.capabilities)
		self.assertIsNot(OotleReader, OotleEsmeralda)

	def test_readiness_verifies_esmeralda_and_epoch(self):
		with mock.patch("cryptopos_core.chain._urlopen", Transport()):
			readiness = self.rail.readiness(self.configuration())
		self.assertIn(OBSERVATION, readiness.ready)
		self.assertFalse(readiness.chargeable)
		self.assertIn("payer URI", readiness.reason_for(PAYMENT_REQUEST))

		with mock.patch("cryptopos_core.chain._urlopen", Transport(network="localnet")):
			wrong = self.rail.readiness(self.configuration())
		self.assertNotIn(OBSERVATION, wrong.ready)
		self.assertIn("esmeralda", wrong.reason_for(OBSERVATION))

	def test_balance_increase_is_sighted_but_unattributed(self):
		baseline = self.baseline(Transport(amount=100, epoch=10))
		with mock.patch("cryptopos_core.chain._urlopen", Transport(amount=160, epoch=11)):
			observations = self.rail.observe(self.intent(baseline), self.configuration())
		self.assertEqual(observations.unattributed_native, 60)
		self.assertEqual(observations.transfers, ())
		self.assertIn("cannot identify", observations.warnings[0])
		with self.assertRaises(UnsupportedCapability):
			self.rail.settle(self.intent(baseline), observations)

	def test_a_balance_fall_is_not_mislabeled_as_a_payment(self):
		baseline = self.baseline(Transport(amount=100))
		with mock.patch("cryptopos_core.chain._urlopen", Transport(amount=90, epoch=11)):
			observations = self.rail.observe(self.intent(baseline), self.configuration())
		self.assertEqual(observations.unattributed_native, 0)
		self.assertIn("fell", observations.warnings[0])

	def test_confidential_balance_is_refused_not_reported_as_a_total(self):
		with mock.patch("cryptopos_core.chain._urlopen", Transport(kind="Confidential")):
			with self.assertRaises(RailProviderError) as caught:
				self.rail.capture_baseline(ACCOUNT, self.configuration())
		self.assertIn("confidential", caught.exception.reason)

	def test_resource_identity_is_the_protocol_xtr_constant(self):
		self.assertEqual(OOTLE_XTR_RESOURCE, "resource_" + "01" * 32)


if __name__ == "__main__":
	unittest.main()
