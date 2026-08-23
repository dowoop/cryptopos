"""Hostile transport and provider-shape tests for complete payment rails."""

import json
import unittest
from unittest import mock

from cryptopos_core import bitcoin, evm
from cryptopos_core.errors import AddressRefused, InvalidRailPlugin, RailProviderError
from cryptopos_core.plugin import (
	NEEDS_REVIEW,
	PENDING,
	ObservationBatch,
	PaymentIntent,
	RecipientBaseline,
	TransferObservation,
)

BTC_KEY = bitcoin.BitcoinTestnet4.key
EVM_KEY = evm.ethereum_sepolia.key
ENDPOINT = "https://provider.example/api"
BTC_ADDRESS = "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx"
BTC_TX = "a" * 64
EVM_RECIPIENT = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
EVM_TX = "0x" + "a" * 64
EVM_BLOCK = "0x" + "1" * 64


class RawGet:
	def __init__(self, value):
		self.value = value

	def get(self, url, timeout, max_bytes):
		if isinstance(self.value, Exception):
			raise self.value
		return self.value


class RawPost:
	def __init__(self, value):
		self.value = value

	def post(self, url, body, timeout, max_bytes):
		if isinstance(self.value, Exception):
			raise self.value
		return self.value


class RpcHandler:
	def __init__(self, handler):
		self.handler = handler

	def post(self, url, body, timeout, max_bytes):
		request = json.loads(body)
		result = self.handler(request["method"], request["params"])
		return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()


class Response:
	def __init__(self, body, content_length=None):
		self.body = body
		self.read_limit = None
		self.headers = {}
		if content_length is not None:
			self.headers["Content-Length"] = content_length

	def __enter__(self):
		return self

	def __exit__(self, *ignored):
		return False

	def read(self, limit):
		self.read_limit = limit
		return self.body


class Opener:
	def __init__(self, response):
		self.response = response
		self.requests = []

	def open(self, request, timeout):
		self.requests.append((request, timeout))
		return self.response


class BitcoinTransportFailures(unittest.TestCase):
	def test_redirects_are_never_followed(self):
		self.assertIsNone(bitcoin._NoRedirect().redirect_request(None, None, 302, "moved", {}, ENDPOINT))

	def test_default_transport_builds_a_bounded_get(self):
		opener = Opener(Response(b"abc", "3"))
		with mock.patch("urllib.request.build_opener", return_value=opener) as build_opener:
			transport = bitcoin._HttpsTransport()
			self.assertEqual(transport.get(ENDPOINT, 2, 3), b"abc")
		handlers = build_opener.call_args.args
		self.assertIs(handlers[0], bitcoin._NoRedirect)
		self.assertEqual(handlers[1].proxies, {})
		request, timeout = opener.requests[0]
		self.assertEqual((request.method, request.full_url, timeout), ("GET", ENDPOINT, 2))
		self.assertEqual(opener.response.read_limit, 4)

	def test_default_transport_accepts_an_explicit_zero_length_body(self):
		transport = bitcoin._HttpsTransport.__new__(bitcoin._HttpsTransport)
		transport._opener = Opener(Response(b"", "0"))
		self.assertEqual(transport.get(ENDPOINT, 2, 3), b"")

	def test_default_transport_refuses_declared_or_actual_oversize_bodies(self):
		for response in (
			Response(b"", "not-a-number"),
			Response(b"", "-1"),
			Response(b"", "4"),
			Response(b"abcd"),
		):
			with self.subTest(response=response):
				transport = bitcoin._HttpsTransport.__new__(bitcoin._HttpsTransport)
				transport._opener = Opener(response)
				with self.assertRaises(ValueError):
					transport.get(ENDPOINT, 2, 3)

	def test_configuration_rejects_unsafe_shapes(self):
		for configuration in (
			None,
			{},
			{"endpoint": "http://provider.example"},
			{"endpoint": "https://user:secret@provider.example"},
			{"endpoint": "https://provider.example?network=mainnet"},
			{"endpoint": ENDPOINT, "transport": object()},
			{"endpoint": ENDPOINT, "transport": RawGet(b""), "timeout_seconds": 0},
		):
			with self.subTest(configuration=configuration), self.assertRaises(RailProviderError):
				bitcoin._configuration(configuration)
		with mock.patch.object(bitcoin, "_HttpsTransport", return_value=RawGet(b"")) as factory:
			base, transport, timeout = bitcoin._configuration({"endpoint": ENDPOINT + "/"})
		self.assertEqual((base, timeout), (ENDPOINT, bitcoin.DEFAULT_TIMEOUT_SECONDS))
		self.assertIs(transport, factory.return_value)
		self.assertEqual(
			bitcoin._configuration({"endpoint": ENDPOINT, "transport": RawGet(b""), "timeout_seconds": 30})[
				2
			],
			30.0,
		)
		self.assertEqual(
			bitcoin._configuration({"endpoint": ENDPOINT, "transport": RawGet(b""), "timeout_seconds": 1})[2],
			1.0,
		)
		with self.assertRaises(RailProviderError):
			bitcoin._configuration(
				{"endpoint": ENDPOINT, "transport": RawGet(b""), "timeout_seconds": 30.0001}
			)

	def test_read_normalizes_transport_failures_and_bounds(self):
		provider_error = RailProviderError(BTC_KEY, "specific")
		for value, phrase in (
			(provider_error, "specific"),
			(ValueError("bad wire"), "failed"),
			("not bytes", "non-byte"),
			(b"x" * (bitcoin.MAX_RESPONSE_BYTES + 1), "safety limit"),
		):
			with self.subTest(value=value), self.assertRaises(RailProviderError) as caught:
				bitcoin._read(ENDPOINT, RawGet(value), 2, "/status")
			self.assertIn(phrase, caught.exception.reason)

	def test_text_and_json_refuse_bad_encodings_and_documents(self):
		for function, payload in (
			(bitcoin._text, b"\xff"),
			(bitcoin._json, b"\xff"),
			(bitcoin._json, b"{"),
		):
			with self.subTest(function=function, payload=payload), self.assertRaises(RailProviderError):
				function(ENDPOINT, RawGet(payload), 2, "/data")

	def test_tip_and_transaction_page_are_strictly_bounded(self):
		with self.assertRaises(RailProviderError):
			bitcoin._tip(ENDPOINT, RawGet(b"-1"), 2)
		for payload in ({"not": "a list"}, [{}] * (bitcoin.MAX_TRANSACTIONS + 1)):
			with self.subTest(payload=payload), self.assertRaises(RailProviderError):
				bitcoin._transactions(ENDPOINT, RawGet(json.dumps(payload).encode()), 2, "recipient")
		self.assertEqual(bitcoin._exact_nonnegative(0, "field"), 0)
		self.assertEqual(
			len(
				bitcoin._transactions(
					ENDPOINT,
					RawGet(json.dumps([{}] * bitcoin.MAX_TRANSACTIONS).encode()),
					2,
					"recipient",
				)
			),
			bitcoin.MAX_TRANSACTIONS,
		)

	def test_read_accepts_a_body_exactly_at_the_limit(self):
		body = b"x" * bitcoin.MAX_RESPONSE_BYTES
		self.assertIs(bitcoin._read(ENDPOINT, RawGet(body), 2, "/body"), body)

	def test_safety_constants_are_pinned(self):
		self.assertEqual(bitcoin.MAX_RESPONSE_BYTES, 2_000_000)
		self.assertEqual(bitcoin.MAX_TRANSACTIONS, 50)
		self.assertEqual(bitcoin.MAX_TRANSACTION_INPUTS, 10_000)
		self.assertEqual(bitcoin.MAX_TRANSACTION_OUTPUTS, 10_000)
		self.assertEqual(bitcoin.BitcoinTestnet4.asset.decimals, 8)


class BitcoinProviderDataFailures(unittest.TestCase):
	def setUp(self):
		self.rail = bitcoin.BitcoinTestnet4()
		self.baseline = RecipientBaseline(self.rail.key, BTC_ADDRESS, ENDPOINT, 5)
		self.intent = PaymentIntent(
			"sale-1", self.rail.key, BTC_ADDRESS, 10, 100, 200, baseline=self.baseline
		)

	def transaction(self, **changes):
		value = {
			"txid": BTC_TX,
			"vin": [],
			"vout": [{"scriptpubkey_address": BTC_ADDRESS, "value": 10}],
			"status": {"confirmed": True, "block_height": 6, "block_time": 150},
		}
		value.update(changes)
		return value

	def test_request_and_observation_require_valid_bound_intents(self):
		bad_baseline = RecipientBaseline(self.rail.key, "bad", ENDPOINT, 5)
		bad_intent = PaymentIntent("sale", self.rail.key, "bad", 10, 100, 200, baseline=bad_baseline)
		with self.assertRaises(AddressRefused):
			self.rail.create_request(bad_intent)
		without_baseline = PaymentIntent("sale", self.rail.key, BTC_ADDRESS, 10, 100, 200)
		with self.assertRaises(InvalidRailPlugin):
			self.rail.observe(without_baseline, {})
		with self.assertRaises(InvalidRailPlugin):
			self.rail._intent(object())

	def test_observation_revalidates_previous_binding_and_monotonic_tip(self):
		previous = ObservationBatch(
			self.rail.key,
			self.intent.intent_id,
			BTC_ADDRESS,
			ENDPOINT,
			5,
			5,
			5,
			5,
			(),
		)
		with (
			mock.patch.object(bitcoin, "_verified_provider", return_value=(ENDPOINT, object(), 2)),
			mock.patch.object(bitcoin, "_tip", return_value=5),
			mock.patch.object(bitcoin, "_transactions", return_value=[]),
		):
			observed = self.rail.observe(self.intent, {}, previous)
		self.assertTrue(observed.complete)
		with (
			mock.patch.object(bitcoin, "_verified_provider", return_value=(ENDPOINT, object(), 2)),
			mock.patch.object(bitcoin, "_tip", return_value=4),
		):
			with self.assertRaises(RailProviderError):
				self.rail.observe(self.intent, {})

	def test_settlement_refuses_unknown_incomplete_or_malformed_claims(self):
		complete = ObservationBatch(self.rail.key, "sale-1", BTC_ADDRESS, ENDPOINT, 5, 6, 5, 6, ())
		incomplete = ObservationBatch(self.rail.key, "sale-1", BTC_ADDRESS, ENDPOINT, 5, 7, 5, 6, ())
		with self.assertRaises(InvalidRailPlugin):
			self.rail.settle(self.intent, object())
		with self.assertRaises(InvalidRailPlugin):
			self.rail.settle(self.intent, incomplete)
		with self.assertRaises(InvalidRailPlugin):
			self.rail.settle(self.intent, complete, {BTC_TX})

	def test_settlement_reports_overpayment_and_confirmed_underpayment(self):
		overpaid = ObservationBatch(
			self.rail.key,
			"sale-1",
			BTC_ADDRESS,
			ENDPOINT,
			5,
			6,
			5,
			6,
			(TransferObservation(BTC_TX, 11, True, 1, 6, 150),),
		)
		decision = self.rail.settle(self.intent, overpaid)
		self.assertIn("exceeds", decision.reason)
		underpaid = ObservationBatch(
			self.rail.key,
			"sale-1",
			BTC_ADDRESS,
			ENDPOINT,
			5,
			6,
			5,
			6,
			(TransferObservation(BTC_TX, 9, True, 1, 6, 150),),
		)
		decision = self.rail.settle(self.intent, underpaid)
		self.assertEqual(decision.state, PENDING)
		self.assertIn("below", decision.reason)

	def test_settlement_pins_expiry_overpayment_claim_and_late_boundaries(self):
		def observed(amount, *, block_time=150, transaction_id=BTC_TX):
			return ObservationBatch(
				self.rail.key,
				"sale-1",
				BTC_ADDRESS,
				ENDPOINT,
				5,
				6,
				5,
				6,
				(TransferObservation(transaction_id, amount, True, 1, 6, block_time),),
			)

		exact = self.rail.settle(self.intent, observed(10, block_time=200))
		self.assertEqual(exact.state, "settled")
		self.assertNotIn("exceeds", exact.reason)
		claimed = ObservationBatch(
			self.rail.key,
			"sale-1",
			BTC_ADDRESS,
			ENDPOINT,
			5,
			6,
			5,
			6,
			(
				TransferObservation(BTC_TX, 6, True, 1, 6, 150),
				TransferObservation("b" * 64, 5, True, 1, 6, 150),
			),
		)
		self.assertEqual(self.rail.settle(self.intent, claimed, frozenset({BTC_TX})).state, NEEDS_REVIEW)
		self.assertEqual(self.rail.settle(self.intent, observed(9, block_time=201)).state, PENDING)
		self.assertEqual(self.rail.settle(self.intent, observed(11, block_time=201)).state, NEEDS_REVIEW)

	def test_parser_skips_duplicates_baseline_transactions_and_zero_outputs(self):
		zero = self.transaction(vout=[{"scriptpubkey_address": "other", "value": 10}])
		self.assertEqual(self.rail._parse_transfers(self.intent, 6, [zero, zero]), [])
		bound = PaymentIntent(
			"sale-1",
			self.rail.key,
			BTC_ADDRESS,
			10,
			100,
			200,
			baseline=RecipientBaseline(self.rail.key, BTC_ADDRESS, ENDPOINT, 5, (BTC_TX,)),
		)
		self.assertEqual(self.rail._parse_transfers(bound, 6, [self.transaction()]), [])

	def test_parser_rejects_malformed_transaction_collections(self):
		cases = (
			self.transaction(vout={}),
			self.transaction(vout=[{}] * (bitcoin.MAX_TRANSACTION_OUTPUTS + 1)),
			self.transaction(vin={}),
			self.transaction(vin=[{}] * (bitcoin.MAX_TRANSACTION_INPUTS + 1)),
			self.transaction(vout=["output"]),
			self.transaction(vout=[{"scriptpubkey_address": 1, "value": 10}]),
			self.transaction(status={"confirmed": "yes"}),
			self.transaction(status={"confirmed": True, "block_height": 7, "block_time": 150}),
		)
		for transaction in cases:
			with self.subTest(transaction=transaction), self.assertRaises(RailProviderError):
				self.rail._parse_transfers(self.intent, 6, [transaction])
		with self.assertRaises(RailProviderError):
			self.rail._parse_transfers(self.intent, 6, [self.transaction(status="bad")])

	def test_parser_accepts_collection_ceilings_and_computes_exact_confirmations(self):
		outputs = [{"scriptpubkey_address": "other", "value": 0}] * (bitcoin.MAX_TRANSACTION_OUTPUTS - 1) + [
			{"scriptpubkey_address": BTC_ADDRESS, "value": 10}
		]
		inputs = [{"prevout": None}] * bitcoin.MAX_TRANSACTION_INPUTS
		parsed = self.rail._parse_transfers(self.intent, 6, [self.transaction(vout=outputs, vin=inputs)])
		self.assertEqual(parsed[0].confirmations, 1)

	def test_input_parser_refuses_malformed_prevouts_but_allows_coinbase(self):
		self.assertIs(self.rail._spends_from_recipient([{"prevout": None}], BTC_ADDRESS), False)
		for inputs in (
			["input"],
			[{"prevout": "bad"}],
			[{"prevout": {"scriptpubkey_address": 1}}],
		):
			with self.subTest(inputs=inputs), self.assertRaises(RailProviderError):
				self.rail._spends_from_recipient(inputs, BTC_ADDRESS)


class JsonRpcTransportFailures(unittest.TestCase):
	def test_redirects_are_never_followed(self):
		self.assertIsNone(evm._NoRedirect().redirect_request(None, None, 302, "moved", {}, ENDPOINT))

	def test_default_transport_builds_a_bounded_post(self):
		opener = Opener(Response(b"{}"))
		with mock.patch("urllib.request.build_opener", return_value=opener) as build_opener:
			transport = evm._JsonRpcTransport()
			self.assertEqual(transport.post(ENDPOINT, b"{}", 2, 2), b"{}")
		handlers = build_opener.call_args.args
		self.assertIs(handlers[0], evm._NoRedirect)
		self.assertEqual(handlers[1].proxies, {})
		request, timeout = opener.requests[0]
		self.assertEqual((request.method, request.full_url, timeout), ("POST", ENDPOINT, 2))
		self.assertEqual(opener.response.read_limit, 3)

	def test_default_transport_refuses_oversize_body(self):
		transport = evm._JsonRpcTransport.__new__(evm._JsonRpcTransport)
		transport._opener = Opener(Response(b"abc"))
		with self.assertRaises(ValueError):
			transport.post(ENDPOINT, b"{}", 2, 2)

	def test_configuration_rejects_unsafe_shapes(self):
		for configuration in (
			None,
			{},
			{"endpoint": "http://provider.example"},
			{"endpoint": "https://provider.example#mainnet"},
			{"endpoint": ENDPOINT, "transport": object()},
			{"endpoint": ENDPOINT, "transport": RawPost(b""), "timeout_seconds": True},
		):
			with self.subTest(configuration=configuration), self.assertRaises(RailProviderError):
				evm._configuration(configuration, EVM_KEY)
		with mock.patch.object(evm, "_JsonRpcTransport", return_value=RawPost(b"")) as factory:
			base, transport, timeout = evm._configuration({"endpoint": ENDPOINT + "/"}, EVM_KEY)
		self.assertEqual((base, timeout), (ENDPOINT, evm.DEFAULT_TIMEOUT_SECONDS))
		self.assertIs(transport, factory.return_value)
		self.assertEqual(
			evm._configuration(
				{"endpoint": ENDPOINT, "transport": RawPost(b""), "timeout_seconds": 30}, EVM_KEY
			)[2],
			30.0,
		)
		self.assertEqual(
			evm._configuration(
				{"endpoint": ENDPOINT, "transport": RawPost(b""), "timeout_seconds": 1}, EVM_KEY
			)[2],
			1.0,
		)
		with self.assertRaises(RailProviderError):
			evm._configuration(
				{"endpoint": ENDPOINT, "transport": RawPost(b""), "timeout_seconds": 0}, EVM_KEY
			)
		with self.assertRaises(RailProviderError):
			evm._configuration(
				{"endpoint": ENDPOINT, "transport": RawPost(b""), "timeout_seconds": 30.0001},
				EVM_KEY,
			)

	def test_rpc_refuses_transport_and_envelope_corruption(self):
		valid = {"jsonrpc": "2.0", "id": 1, "result": "0x1"}
		cases = (
			(ValueError("wire"), "failed"),
			("not bytes", "non-byte"),
			(b"x" * (evm.MAX_RESPONSE_BYTES + 1), "safety limit"),
			(b"\xff", "valid JSON"),
			(json.dumps([]).encode(), "envelope"),
			(json.dumps({**valid, "id": 2}).encode(), "envelope"),
			(json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -1}}).encode(), "error"),
			(json.dumps({"jsonrpc": "2.0", "id": 1}).encode(), "no result"),
		)
		for value, phrase in cases:
			provider = (ENDPOINT, RawPost(value), 2.0)
			with self.subTest(value=value), self.assertRaises(RailProviderError) as caught:
				evm._rpc(EVM_KEY, provider, "eth_test", [])
			self.assertIn(phrase, caught.exception.reason)
		encoded = json.dumps(valid).encode()
		body = b" " * (evm.MAX_RESPONSE_BYTES - len(encoded)) + encoded
		self.assertEqual(len(body), evm.MAX_RESPONSE_BYTES)
		self.assertEqual(evm._rpc(EVM_KEY, (ENDPOINT, RawPost(body), 2.0), "eth_test", []), "0x1")

	def test_rpc_request_id_and_safety_constants_are_pinned(self):
		class Capture:
			request = None

			def post(self, url, body, timeout, max_bytes):
				self.request = json.loads(body)
				return b'{"jsonrpc":"2.0","id":1,"result":"0x1"}'

		transport = Capture()
		evm._rpc(EVM_KEY, (ENDPOINT, transport, 2.0), "eth_test", [])
		self.assertEqual(transport.request["id"], 1)
		self.assertEqual(evm.SEPOLIA_CHAIN_ID, 11_155_111)
		self.assertEqual(evm.MAX_RESPONSE_BYTES, 4_000_000)
		self.assertEqual(evm.MAX_TRANSACTIONS_PER_BLOCK, 20_000)
		self.assertEqual(evm.MAX_LOGS_PER_OBSERVATION, 20_000)
		self.assertEqual(evm.ethereum_sepolia.asset.decimals, 18)
		self.assertEqual(evm.usdc_ethereum_sepolia.asset.decimals, 6)
		self.assertEqual(evm.usdc_polygon_amoy.asset.decimals, 6)
		self.assertEqual(evm.usdc_polygon_amoy.max_blocks_per_observation, 5_000)

	def test_quantity_requires_canonical_hexadecimal(self):
		for value in (1, "0x", "0x00", "0xA", "1"):
			with self.subTest(value=value), self.assertRaises(RailProviderError):
				evm._quantity(EVM_KEY, value, "height")


class EvmProviderDataFailures(unittest.TestCase):
	def setUp(self):
		self.rail = evm.ethereum_sepolia
		self.baseline = RecipientBaseline(self.rail.key, EVM_RECIPIENT, ENDPOINT, 5)
		self.intent = PaymentIntent(
			"sale-1", self.rail.key, EVM_RECIPIENT, 10, 100, 200, baseline=self.baseline
		)

	def provider(self, handler):
		return (ENDPOINT, RpcHandler(handler), 2.0)

	def block(self, **changes):
		value = {
			"number": "0x6",
			"hash": EVM_BLOCK,
			"timestamp": "0x96",
			"transactions": [],
		}
		value.update(changes)
		return value

	def receipt(self, **changes):
		value = {
			"transactionHash": EVM_TX,
			"blockNumber": "0x6",
			"blockHash": EVM_BLOCK,
			"status": "0x1",
		}
		value.update(changes)
		return value

	def token_log(self, **changes):
		recipient_topic = "0x" + "0" * 24 + EVM_RECIPIENT[2:].lower()
		value = {
			"address": evm.usdc_ethereum_sepolia.token_contract,
			"topics": [evm.TRANSFER_TOPIC, "0x" + "0" * 64, recipient_topic],
			"data": "0x" + format(10, "064x"),
			"blockNumber": "0x6",
			"blockHash": EVM_BLOCK,
			"logIndex": "0x0",
			"transactionHash": EVM_TX,
			"removed": False,
		}
		value.update(changes)
		return value

	def test_request_observation_and_intent_require_a_bound_baseline(self):
		without_baseline = PaymentIntent("sale", self.rail.key, EVM_RECIPIENT, 10, 100, 200)
		with self.assertRaises(InvalidRailPlugin):
			self.rail.create_request(without_baseline)
		with self.assertRaises(InvalidRailPlugin):
			self.rail.observe(without_baseline, {})
		with self.assertRaises(InvalidRailPlugin):
			self.rail._intent(object())

	def test_observation_refuses_provider_switch_rewind_and_bad_previous_page(self):
		with self.assertRaises(RailProviderError):
			self.rail.observe(
				self.intent,
				{"endpoint": "https://other.example", "transport": RawPost(b"")},
			)
		with (
			mock.patch.object(self.rail, "_provider", return_value=(ENDPOINT, object(), 2)),
			mock.patch.object(self.rail, "_verify_network"),
			mock.patch.object(self.rail, "_tip", return_value=4),
		):
			with self.assertRaises(RailProviderError):
				self.rail.observe(self.intent, {})
		with (
			mock.patch.object(self.rail, "_provider", return_value=(ENDPOINT, object(), 2)),
			mock.patch.object(self.rail, "_verify_network"),
			mock.patch.object(self.rail, "_tip", return_value=6),
		):
			with self.assertRaises(InvalidRailPlugin):
				self.rail.observe(self.intent, {}, object())
		previous = ObservationBatch(self.rail.key, "sale-1", EVM_RECIPIENT, ENDPOINT, 5, 8, 5, 7, ())
		with (
			mock.patch.object(self.rail, "_provider", return_value=(ENDPOINT, object(), 2)),
			mock.patch.object(self.rail, "_verify_network"),
			mock.patch.object(self.rail, "_tip", return_value=6),
		):
			with self.assertRaises(RailProviderError):
				self.rail.observe(self.intent, {}, previous)

	def test_observation_at_unchanged_tip_performs_no_chain_scan(self):
		with (
			mock.patch.object(self.rail, "_provider", return_value=(ENDPOINT, object(), 2)),
			mock.patch.object(self.rail, "_verify_network"),
			mock.patch.object(self.rail, "_tip", return_value=5),
			mock.patch.object(self.rail, "_native_transfers") as scan,
		):
			observed = self.rail.observe(self.intent, {})
		scan.assert_not_called()
		self.assertEqual(observed.transfers, ())

	def test_observation_scan_starts_immediately_after_the_cursor(self):
		with (
			mock.patch.object(self.rail, "_provider", return_value=(ENDPOINT, object(), 2)),
			mock.patch.object(self.rail, "_verify_network"),
			mock.patch.object(self.rail, "_tip", return_value=6),
			mock.patch.object(self.rail, "_native_transfers", return_value=[]) as scan,
		):
			self.rail.observe(self.intent, {})
		scan.assert_called_once_with(mock.ANY, EVM_RECIPIENT, 6, 6, 6)
		token_rail = evm.usdc_ethereum_sepolia
		token_baseline = RecipientBaseline(token_rail.key, EVM_RECIPIENT, ENDPOINT, 5)
		token_intent = PaymentIntent(
			"sale-1", token_rail.key, EVM_RECIPIENT, 10, 100, 200, baseline=token_baseline
		)
		with (
			mock.patch.object(token_rail, "_provider", return_value=(ENDPOINT, object(), 2)),
			mock.patch.object(token_rail, "_verify_network"),
			mock.patch.object(token_rail, "_tip", return_value=6),
			mock.patch.object(token_rail, "_token_transfers", return_value=[]) as token_scan,
		):
			token_rail.observe(token_intent, {})
		token_scan.assert_called_once_with(mock.ANY, EVM_RECIPIENT, 6, 6, 6)

	def test_settlement_refuses_unknown_observations_and_malformed_claims(self):
		observed = ObservationBatch(self.rail.key, "sale-1", EVM_RECIPIENT, ENDPOINT, 5, 6, 5, 6, ())
		with self.assertRaises(InvalidRailPlugin):
			self.rail.settle(self.intent, object())
		with self.assertRaises(InvalidRailPlugin):
			self.rail.settle(self.intent, observed, {EVM_TX})

	def test_claimed_transfer_enters_review_instead_of_settling_twice(self):
		observed = ObservationBatch(
			self.rail.key,
			"sale-1",
			EVM_RECIPIENT,
			ENDPOINT,
			5,
			8,
			5,
			8,
			(TransferObservation(EVM_TX, 10, True, 3, 6, 150),),
		)
		decision = self.rail.settle(self.intent, observed, frozenset({EVM_TX}))
		self.assertEqual(decision.state, NEEDS_REVIEW)

	def test_settlement_pins_expiry_claimed_late_and_pending_boundaries(self):
		def observations(*transfers):
			return ObservationBatch(
				self.rail.key, "sale-1", EVM_RECIPIENT, ENDPOINT, 5, 8, 5, 8, tuple(transfers)
			)

		exact = self.rail.settle(self.intent, observations(TransferObservation(EVM_TX, 10, True, 3, 6, 200)))
		self.assertEqual(exact.state, "settled")
		claimed = observations(
			TransferObservation(EVM_TX, 6, True, 3, 6, 150),
			TransferObservation("0x" + "b" * 64, 5, True, 3, 6, 150),
		)
		self.assertEqual(self.rail.settle(self.intent, claimed, frozenset({EVM_TX})).state, NEEDS_REVIEW)
		late_under = observations(TransferObservation(EVM_TX, 9, True, 3, 6, 201))
		late_over = observations(TransferObservation(EVM_TX, 11, True, 3, 6, 201))
		self.assertEqual(self.rail.settle(self.intent, late_under).state, PENDING)
		self.assertEqual(self.rail.settle(self.intent, late_over).state, NEEDS_REVIEW)
		for amount in (10, 11):
			immature = observations(TransferObservation(EVM_TX, amount, True, 1, 8, 150))
			self.assertIn("awaiting", self.rail.settle(self.intent, immature).reason)

	def test_native_readiness_probe_rejects_incoherent_latest_blocks(self):
		cases = (
			{"number": "0x5", "hash": EVM_BLOCK, "timestamp": "0x96", "transactions": []},
			{"number": "0x6", "hash": "bad", "timestamp": "0x96", "transactions": []},
			{"number": "0x6", "hash": EVM_BLOCK, "timestamp": "0x96", "transactions": {}},
		)
		for block in cases:
			with self.subTest(block=block), self.assertRaises(RailProviderError):
				self.rail._probe_observation(self.provider(lambda method, params, b=block: b), 6)
		transactions = [{}] * evm.MAX_TRANSACTIONS_PER_BLOCK

		def exact_limit(method, params):
			self.assertEqual(params, ["0x6", True])
			return self.block(transactions=transactions)

		self.rail._probe_observation(self.provider(exact_limit), 6)

	def test_token_readiness_probe_checks_contract_code_and_log_method(self):
		rail = evm.usdc_ethereum_sepolia

		def success(method, params):
			if method == "eth_getCode":
				return "0x01"
			self.assertEqual(len(params[0]["topics"][2]), 66)
			return []

		rail._probe_observation(self.provider(success), 6)
		with self.assertRaises(RailProviderError):
			rail._probe_observation(self.provider(lambda method, params: "0x"), 6)
		with self.assertRaises(RailProviderError):
			rail._probe_observation(self.provider(lambda method, params: None), 6)

		def bad_logs(method, params):
			return "0x01" if method == "eth_getCode" else {}

		with self.assertRaises(RailProviderError):
			rail._probe_observation(self.provider(bad_logs), 6)

		def max_logs(method, params):
			return "0x01" if method == "eth_getCode" else [{}] * evm.MAX_LOGS_PER_OBSERVATION

		rail._probe_observation(self.provider(max_logs), 6)

	def test_receipt_is_bound_to_transaction_height_and_block_hash(self):
		for receipt in (
			None,
			self.receipt(transactionHash="0x" + "b" * 64),
			self.receipt(blockNumber="0x7"),
			self.receipt(blockHash="0x" + "2" * 64),
		):
			with self.subTest(receipt=receipt), self.assertRaises(RailProviderError):
				self.rail._receipt_success(
					self.provider(lambda method, params, r=receipt: r), EVM_TX, 6, EVM_BLOCK
				)

	def test_native_block_parser_rejects_hostile_shapes(self):
		cases = (
			None,
			self.block(number="0x7"),
			self.block(hash="bad"),
			self.block(transactions={}),
			self.block(transactions=["transaction"]),
			self.block(transactions=[{"to": 1}]),
			self.block(transactions=[{"to": EVM_RECIPIENT, "value": "0xa", "hash": "bad"}]),
		)
		for block in cases:
			with self.subTest(block=block), self.assertRaises(RailProviderError):
				self.rail._native_transfers(
					self.provider(lambda method, params, b=block: b), EVM_RECIPIENT, 6, 6, 6
				)
		with self.assertRaises(RailProviderError):
			self.rail._native_transfers(
				self.provider(
					lambda method, params: self.block(
						transactions=[{"to": EVM_RECIPIENT, "value": "0xa", "hash": None}]
					)
				),
				EVM_RECIPIENT,
				6,
				6,
				6,
			)

	def test_native_block_parser_skips_contract_creation_other_recipients_and_zero(self):
		transactions = [
			{"to": None},
			{"to": "0x" + "1" * 40},
			{"to": EVM_RECIPIENT, "value": "0x0"},
		]
		block = self.block(transactions=transactions)
		self.assertEqual(
			self.rail._native_transfers(self.provider(lambda method, params: block), EVM_RECIPIENT, 6, 6, 6),
			[],
		)
		limit_block = self.block(transactions=[{"to": None}] * evm.MAX_TRANSACTIONS_PER_BLOCK)
		self.assertEqual(
			self.rail._native_transfers(
				self.provider(lambda method, params: limit_block), EVM_RECIPIENT, 6, 6, 6
			),
			[],
		)

	def test_native_parser_computes_confirmations_from_tip_and_height(self):
		transaction = {"to": EVM_RECIPIENT, "value": "0xa", "hash": EVM_TX}

		def answer(method, params):
			if method == "eth_getTransactionReceipt":
				return self.receipt()
			self.assertEqual(params, ["0x6", True])
			return self.block(transactions=[transaction])

		observed = self.rail._native_transfers(self.provider(answer), EVM_RECIPIENT, 6, 6, 6)
		self.assertEqual(observed[0].confirmations, 1)

	def test_token_parser_rejects_hostile_log_shapes(self):
		rail = evm.usdc_ethereum_sepolia
		recipient_topic = "0x" + "0" * 24 + EVM_RECIPIENT[2:].lower()
		cases = (
			{},
			[None],
			[self.token_log(removed=True)],
			[self.token_log(address="0x" + "1" * 40)],
			[self.token_log(topics=[])],
			[self.token_log(topics=["bad", "0x" + "0" * 64, recipient_topic])],
			[self.token_log(topics=[evm.TRANSFER_TOPIC, "bad", recipient_topic])],
			[self.token_log(data="0x1")],
			[self.token_log(blockNumber="0x7")],
			[self.token_log(transactionHash="bad")],
			[self.token_log(blockHash="bad")],
		)
		for logs in cases:
			with self.subTest(logs=logs), self.assertRaises(RailProviderError):
				rail._token_transfers(
					self.provider(lambda method, params, value=logs: value), EVM_RECIPIENT, 6, 6, 6
				)
		for changes in ({"address": None}, {"data": None}, {"transactionHash": None}, {"blockHash": None}):
			malformed_log = self.token_log(**changes)
			with self.subTest(changes=changes), self.assertRaises(RailProviderError):
				rail._token_transfers(
					self.provider(lambda method, params, log=malformed_log: [log]),
					EVM_RECIPIENT,
					6,
					6,
					6,
				)

	def test_token_parser_skips_zero_and_aggregates_same_transaction_logs(self):
		rail = evm.usdc_ethereum_sepolia
		self.assertEqual(
			rail._token_transfers(
				self.provider(lambda method, params: [self.token_log(data="0x" + "0" * 64)]),
				EVM_RECIPIENT,
				6,
				6,
				6,
			),
			[],
		)
		logs = [self.token_log(), self.token_log(logIndex="0x1")]

		def answer(method, params):
			if method == "eth_getLogs":
				return logs
			if method == "eth_getTransactionReceipt":
				return self.receipt()
			return self.block(transactions=[])

		observed = rail._token_transfers(self.provider(answer), EVM_RECIPIENT, 6, 6, 6)
		self.assertEqual(observed[0].amount_native, 20)
		self.assertEqual(observed[0].confirmations, 1)

	def test_token_sender_validation_and_requested_range_are_independent(self):
		rail = evm.usdc_ethereum_sepolia
		recipient_topic = "0x" + "0" * 24 + EVM_RECIPIENT[2:].lower()
		bad_sender = self.token_log(topics=[evm.TRANSFER_TOPIC, "bad", recipient_topic], data="0x" + "0" * 64)
		with self.assertRaises(RailProviderError):
			rail._token_transfers(self.provider(lambda method, params: [bad_sender]), EVM_RECIPIENT, 6, 6, 6)
		bad_sender_type = self.token_log(
			topics=[evm.TRANSFER_TOPIC, None, recipient_topic], data="0x" + "0" * 64
		)
		with self.assertRaises(RailProviderError):
			rail._token_transfers(
				self.provider(lambda method, params: [bad_sender_type]), EVM_RECIPIENT, 6, 6, 6
			)
		bad_event = self.token_log(topics=["bad", "0x" + "0" * 64, recipient_topic], data="0x" + "0" * 64)
		with self.assertRaises(RailProviderError):
			rail._token_transfers(self.provider(lambda method, params: [bad_event]), EVM_RECIPIENT, 6, 6, 6)

		def provider_for(log, height):
			def answer(method, params):
				return (
					[log] if method == "eth_getLogs" else self.receipt(blockNumber=hex(height), status="0x0")
				)

			return self.provider(answer)

		for height in (5, 7):
			with self.subTest(height=height), self.assertRaises(RailProviderError):
				rail._token_transfers(
					provider_for(self.token_log(blockNumber=hex(height)), height),
					EVM_RECIPIENT,
					6,
					6,
					6,
				)

	def test_token_parser_rejects_one_transaction_at_two_block_positions(self):
		rail = evm.usdc_ethereum_sepolia
		logs = [
			self.token_log(),
			self.token_log(blockNumber="0x7", blockHash="0x" + "2" * 64, logIndex="0x1"),
		]
		with self.assertRaises(RailProviderError):
			rail._token_transfers(self.provider(lambda method, params: logs), EVM_RECIPIENT, 6, 7, 7)

	def test_transfer_block_timestamp_is_height_and_hash_bound(self):
		for block in (None, self.block(number="0x7"), self.block(hash="0x" + "2" * 64)):
			with self.subTest(block=block), self.assertRaises(RailProviderError):
				self.rail._block_timestamp(
					self.provider(lambda method, params, value=block: value), 6, EVM_BLOCK
				)

	def test_polygon_finality_is_bounded_by_latest_tip(self):
		rail = evm.usdc_polygon_amoy
		with self.assertRaises(RailProviderError):
			rail._finalized_tip(self.provider(lambda method, params: None), 6)
		with self.assertRaises(RailProviderError):
			rail._finalized_tip(self.provider(lambda method, params: {"number": "0x7"}), 6)
		self.assertEqual(rail._finalized_tip(self.provider(lambda method, params: {"number": "0x6"}), 6), 6)
		transfer = TransferObservation(EVM_TX, 1, True, 1, 6, 150)
		self.assertFalse(
			self.rail._is_mature(
				transfer,
				ObservationBatch(self.rail.key, "sale-1", EVM_RECIPIENT, ENDPOINT, 5, 7, 5, 7, (transfer,)),
			)
		)
		self.assertTrue(
			self.rail._is_mature(
				transfer,
				ObservationBatch(self.rail.key, "sale-1", EVM_RECIPIENT, ENDPOINT, 5, 8, 5, 8, (transfer,)),
			)
		)


if __name__ == "__main__":
	unittest.main()
