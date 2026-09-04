import hashlib
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_PACKAGES = PACKAGE_ROOT.parent
sys.path.insert(0, str(REPOSITORY_PACKAGES / "cryptopos-core" / "src"))
sys.path.insert(0, str(PACKAGE_ROOT / "src"))

from cryptopos_core.conformance import conformance_issues
from cryptopos_core.plugin import (
	NEEDS_REVIEW,
	PENDING,
	SETTLED,
	UNCONDITIONAL_PER_SALE,
	ObservationBatch,
	PaymentIntent,
	RecipientBaseline,
)
from cryptopos_core.registry import validate_plugin
from cryptopos_core.uri import base58_encode

from cryptopos_rail_solana import (
	DEVNET_GENESIS_HASH,
	MAX_SIGNATURES,
	reference_for_intent,
	solana_devnet_sol,
)

# A real devnet merchant address -- the one the rail was proved against. It used
# to be "11111111111111111111111111111111", which is the SYSTEM PROGRAM: the
# fixtures were paying the runtime itself, and once the rail started reading
# transfer instructions the recipient appeared twice in the account list and
# nothing could be attributed. A recipient that is not a wallet is a fixture
# that cannot represent a payment.
RECIPIENT = "GyKqcxqdA7PbgbFXMW55G8rht5FhWPvgj9T96psdtZKc"
PAYER = "SysvarC1ock11111111111111111111111111111111"
ENDPOINT = "https://rpc.example/solana"


class RpcFixture:
	def __init__(self, signatures=(), transactions=None, confirmed_tip=30, finalized_tip=29,
		minimum_ledger_slot=0):
		self.signatures = list(signatures)
		self.transactions = transactions or {}
		self.confirmed_tip = confirmed_tip
		self.finalized_tip = finalized_tip
		# What the node still retains. A real one prunes, and an empty signature
		# list from a node that has pruned past this sale is not the same answer
		# as an empty list from one that has not.
		self.minimum_ledger_slot = minimum_ledger_slot
		self.calls = []

	def post(self, url, body, timeout, max_bytes):
		request = json.loads(body)
		method = request["method"]
		params = request["params"]
		self.calls.append((url, method, params, timeout, max_bytes))
		if method == "getGenesisHash":
			result = DEVNET_GENESIS_HASH
		elif method == "getSlot":
			commitment = params[0]["commitment"]
			result = self.finalized_tip if commitment == "finalized" else self.confirmed_tip
		elif method == "minimumLedgerSlot":
			result = self.minimum_ledger_slot
		elif method == "getSignaturesForAddress":
			result = self.signatures
		elif method == "getTransaction":
			result = self.transactions[params[0]]
		else:
			raise AssertionError(f"unexpected RPC method {method}")
		return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()


def configuration(fixture):
	return {"endpoint": ENDPOINT, "transport": fixture}


def baseline(recipient=RECIPIENT):
	return RecipientBaseline(solana_devnet_sol.key, recipient, ENDPOINT, 10)


def intent(amount=100, *, intent_id="invoice-123", recipient=RECIPIENT, payment_reference="invoice-text"):
	return PaymentIntent(
		intent_id,
		solana_devnet_sol.key,
		recipient,
		amount,
		100,
		200,
		payment_reference,
		baseline(recipient),
	)


def signature(name, slot, status="finalized", err=None):
	return {"signature": name, "slot": slot, "err": err, "confirmationStatus": status}


SYSTEM_PROGRAM = "11111111111111111111111111111111"


def transfer_data(lamports):
	"""`SystemInstruction::Transfer`: discriminant 2 then a u64, both little-endian."""
	return base58_encode((2).to_bytes(4, "little") + lamports.to_bytes(8, "little"))


def transaction(amount, slot, *, block_time=150, instructions=None, reference=None,
                accounts=(0, 1, 2)):
	"""A payment in the shape a real node serves one.

	**These fixtures carry a real transfer INSTRUCTION, and they did not always.**
	The first version of this file described transactions with no instructions
	and no signatures at all, and they settled — because the rail credited the
	recipient's whole balance delta on the strength of the reference appearing
	anywhere in the account list. That is not a binding, and the fixture agreed
	with it because the fixture was built from the same misunderstanding.
	Reproduced 2026-08-25: one 100-lamport transfer naming two sales' references
	settled both a 60-lamport sale and a 40-lamport one.

	The account list here is exactly what devnet returned for the transfer that
	proved this rail — `[payer, merchant, reference, system program]`, one
	instruction over accounts `[0, 1, 2]`.
	"""
	reference = reference or reference_for_intent("invoice-123")
	keys = [PAYER, RECIPIENT, reference, SYSTEM_PROGRAM]
	if instructions is None:
		instructions = [{
			"programIdIndex": 3,
			"accounts": list(accounts),
			"data": transfer_data(amount),
		}]
	return {
		"slot": slot,
		"blockTime": block_time,
		"transaction": {"message": {"accountKeys": keys, "instructions": instructions}},
		"meta": {
			"err": None,
			"preBalances": [1_000, 10, 0, 1],
			"postBalances": [1_000 - amount, 10 + amount, 0, 1],
		},
	}


class ContractAndReference(unittest.TestCase):
	def test_plugin_passes_registry_and_conformance_gates(self):
		fixture = RpcFixture()
		self.assertIs(validate_plugin(solana_devnet_sol), solana_devnet_sol)
		self.assertEqual(solana_devnet_sol.binding_category, UNCONDITIONAL_PER_SALE)
		self.assertEqual(conformance_issues(solana_devnet_sol, configuration(fixture)), ())

	def test_reference_is_sha256_base58_and_uses_only_intent_id(self):
		alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
		raw = hashlib.sha256(b"invoice-123").digest()
		number = int.from_bytes(raw, "big")
		expected = ""
		while number:
			number, digit = divmod(number, 58)
			expected = alphabet[digit] + expected
		expected = "1" * (len(raw) - len(raw.lstrip(b"\0"))) + expected
		self.assertEqual(reference_for_intent("invoice-123"), expected)

		first = intent(amount=1_000_000, payment_reference="first invoice string")
		second = PaymentIntent(
			first.intent_id,
			first.rail_key,
			first.recipient,
			first.amount_native,
			101,
			300,
			"different invoice string",
			first.baseline,
		)
		first_reference = parse_qs(urlsplit(solana_devnet_sol.create_request(first).uri).query)["reference"]
		second_reference = parse_qs(urlsplit(solana_devnet_sol.create_request(second).uri).query)["reference"]
		self.assertEqual(first_reference, [expected])
		self.assertEqual(second_reference, [expected])

	def test_observe_recomputes_the_same_reference_and_requests_v0_transactions(self):
		reference = reference_for_intent("invoice-123")
		fixture = RpcFixture([signature("sig", 11)], {"sig": transaction(100, 11)})
		solana_devnet_sol.observe(intent(), configuration(fixture))
		signature_call = next(call for call in fixture.calls if call[1] == "getSignaturesForAddress")
		transaction_call = next(call for call in fixture.calls if call[1] == "getTransaction")
		self.assertEqual(signature_call[2][0], reference)
		self.assertEqual(transaction_call[2][1]["maxSupportedTransactionVersion"], 0)


class UnknownAnswers(unittest.TestCase):
	def assert_unknown(self, malformed, reason):
		fixture = RpcFixture([signature("sig", 11)], {"sig": malformed})
		observations = solana_devnet_sol.observe(intent(), configuration(fixture))
		self.assertEqual(observations.transfers, ())
		self.assertTrue(any(reason in warning for warning in observations.warnings), observations.warnings)
		decision = solana_devnet_sol.settle(intent(), observations)
		self.assertEqual(decision.state, NEEDS_REVIEW)
		self.assertEqual(decision.sighted_native, 0)

	def test_missing_account_keys_is_unknown(self):
		malformed = transaction(100, 11)
		malformed["transaction"]["message"].pop("accountKeys")
		self.assert_unknown(malformed, "accountKeys were missing")

	def test_short_balance_arrays_are_unknown(self):
		malformed = transaction(100, 11)
		malformed["meta"]["postBalances"].pop()
		self.assert_unknown(malformed, "balance arrays did not match")

	def test_non_integer_lamport_balance_is_unknown(self):
		malformed = transaction(100, 11)
		malformed["meta"]["postBalances"][1] = "110"
		self.assert_unknown(malformed, "balance was not a non-negative integer")

	def test_null_meta_is_unknown(self):
		malformed = transaction(100, 11)
		malformed["meta"] = None
		self.assert_unknown(malformed, "meta was null")

	def test_address_lookup_tables_are_unknown(self):
		malformed = transaction(100, 11)
		malformed["meta"]["loadedAddresses"] = {"writable": ["loaded"], "readonly": []}
		self.assert_unknown(malformed, "address lookup tables")

	def test_zero_recipient_delta_is_unknown_not_a_zero_transfer(self):
		malformed = transaction(0, 11)
		self.assert_unknown(malformed, "did not increase")


class ObservationAndSettlement(unittest.TestCase):
	def test_failed_transaction_credits_nothing_and_is_explained(self):
		fixture = RpcFixture([signature("failed", 11, err={"InstructionError": [0, "Custom"]})])
		observations = solana_devnet_sol.observe(intent(), configuration(fixture))
		self.assertEqual(observations.transfers, ())
		self.assertTrue(any("all observed signatures failed" in item for item in observations.warnings))
		self.assertFalse(any(call[1] == "getTransaction" for call in fixture.calls))
		decision = solana_devnet_sol.settle(intent(), observations)
		self.assertEqual((decision.state, decision.credited_native, decision.sighted_native), (PENDING, 0, 0))

	def test_earliest_slot_binds_the_display_transaction_in_both_list_orders(self):
		for entries in (
			[signature("later", 12), signature("earliest", 11)],
			[signature("earliest", 11), signature("later", 12)],
		):
			with self.subTest(entries=[entry["signature"] for entry in entries]):
				fixture = RpcFixture(
					entries,
					{"earliest": transaction(60, 11), "later": transaction(40, 12)},
				)
				observations = solana_devnet_sol.observe(intent(), configuration(fixture))
				decision = solana_devnet_sol.settle(intent(), observations)
				self.assertEqual(decision.state, SETTLED)
				self.assertEqual(decision.transaction_id, "earliest")
				self.assertEqual(decision.transaction_ids, ("earliest", "later"))

	def test_two_transactions_for_one_reference_are_summed(self):
		fixture = RpcFixture(
			[signature("second", 12), signature("first", 11)],
			{"first": transaction(40, 11), "second": transaction(60, 12)},
		)
		observations = solana_devnet_sol.observe(intent(), configuration(fixture))
		decision = solana_devnet_sol.settle(intent(), observations)
		self.assertEqual([transfer.amount_native for transfer in observations.transfers], [40, 60])
		self.assertEqual((decision.state, decision.credited_native, decision.sighted_native), (SETTLED, 100, 100))

	def test_confirmed_payment_is_visible_and_cannot_settle(self):
		fixture = RpcFixture(
			[signature("confirmed", 11, status="confirmed")],
			{"confirmed": transaction(100, 11)},
			finalized_tip=29,
		)
		observations = solana_devnet_sol.observe(intent(), configuration(fixture))
		decision = solana_devnet_sol.settle(intent(), observations)
		self.assertEqual(observations.transfers[0].confirmations, 1)
		self.assertEqual((decision.state, decision.credited_native, decision.sighted_native), (PENDING, 0, 100))

	def test_token_balance_fields_never_override_the_lamport_delta(self):
		native = transaction(100, 11)
		native["meta"]["postTokenBalances"] = [{"uiTokenAmount": {"amount": "999999999"}}]
		fixture = RpcFixture([signature("sig", 11)], {"sig": native})
		observations = solana_devnet_sol.observe(intent(), configuration(fixture))
		self.assertEqual(observations.transfers[0].amount_native, 100)



class AttributionIsInstructionLevel(unittest.TestCase):
	"""The findings from the 2026-08-25 adversarial pass, each locked down.

	Every one of these passed BEFORE the fix, which is the point: the rail
	credited the recipient's whole transaction-wide balance delta whenever the
	reference appeared anywhere in the account list, so a search hit was being
	read as proof of payment.
	"""

	def _observe(self, intent_object, instructions, *, pre, post, keys=None):
		reference = reference_for_intent(intent_object.intent_id)
		keys = keys or [PAYER, RECIPIENT, reference, SYSTEM_PROGRAM]
		document = {
			"slot": 20,
			"blockTime": 150,
			"transaction": {"message": {"accountKeys": keys, "instructions": instructions}},
			"meta": {"err": None, "preBalances": pre, "postBalances": post},
		}
		fixture = RpcFixture(
			signatures=[signature("sig-one", 20)],
			transactions={"sig-one": document},
		)
		return solana_devnet_sol.observe(intent_object, configuration(fixture))

	def test_one_transfer_naming_two_sales_settles_neither(self):
		# Solana Pay permits several references on one transfer. A payer who can
		# see two QRs could otherwise send ONE payment and settle BOTH invoices:
		# reproduced at 100 lamports against a 60-lamport and a 40-lamport sale.
		first = intent(amount=60, intent_id="sale-A")
		second_reference = reference_for_intent("sale-B")
		keys = [PAYER, RECIPIENT, reference_for_intent("sale-A"), second_reference, SYSTEM_PROGRAM]
		observations = self._observe(
			first,
			[{"programIdIndex": 4, "accounts": [0, 1, 2, 3], "data": transfer_data(100)}],
			pre=[1_000, 0, 0, 0, 1], post=[900, 100, 0, 0, 1], keys=keys,
		)
		self.assertEqual(observations.transfers, ())
		decision = solana_devnet_sol.settle(first, observations)
		self.assertEqual(decision.state, NEEDS_REVIEW)

	def test_an_unreferenced_transfer_to_the_merchant_is_not_credited(self):
		# The balance delta would have said 102. Only the referenced 60 is ours;
		# the other 42 belongs to whoever sent it.
		one = intent(amount=60)
		observations = self._observe(
			one,
			[
				{"programIdIndex": 3, "accounts": [0, 1, 2], "data": transfer_data(60)},
				{"programIdIndex": 3, "accounts": [0, 1], "data": transfer_data(42)},
			],
			pre=[1_000, 0, 0, 1], post=[898, 102, 0, 1],
		)
		self.assertEqual([t.amount_native for t in observations.transfers], [60])

	def test_the_shape_devnet_actually_serves_is_the_shape_that_parses(self):
		# Read off `getTransaction` for the transfer that proved this rail:
		# accountKeys [payer, merchant, reference, system program], one
		# instruction over accounts [0, 1, 2]. A fixture that drifts from what a
		# node serves is a fixture that stops being evidence.
		one = intent(amount=102_000)
		observations = self._observe(
			one,
			[{"programIdIndex": 3, "accounts": [0, 1, 2], "data": transfer_data(102_000)}],
			pre=[1_000_000, 0, 0, 1], post=[898_000, 102_000, 0, 1],
		)
		self.assertEqual([t.amount_native for t in observations.transfers], [102_000])
		self.assertEqual(solana_devnet_sol.settle(one, observations).state, SETTLED)

	def test_a_transfer_the_balance_cannot_account_for_is_refused(self):
		# The instructions claim more arrived than the recipient's balance moved.
		# Picking the larger number is how a lying or inconsistent node gets
		# believed; saying so is the only other option.
		one = intent(amount=60)
		observations = self._observe(
			one,
			[{"programIdIndex": 3, "accounts": [0, 1, 2], "data": transfer_data(60)}],
			pre=[1_000, 0, 0, 1], post=[960, 40, 0, 1],
		)
		self.assertEqual(observations.transfers, ())
		self.assertTrue(any("balance moved" in warning for warning in observations.warnings))


class HistoryBoundaries(unittest.TestCase):
	"""Two ways the rail could lose or misstate money without anybody noticing."""

	def test_the_baseline_is_one_slot_below_the_one_in_progress(self):
		# A transfer landing in the slot that was current when the request was
		# built used to be dropped -- forever, because nothing looks below the
		# baseline again -- and the sale expired unpaid with the money on chain.
		fixture = RpcFixture(confirmed_tip=41, finalized_tip=40)
		captured = solana_devnet_sol.capture_baseline(RECIPIENT, configuration(fixture))
		self.assertEqual(captured.tip, 40)

		one = intent(amount=100)
		paid_in_the_slot_that_was_in_progress = PaymentIntent(
			one.intent_id, one.rail_key, one.recipient, one.amount_native,
			one.created_at_epoch, one.expires_at_epoch, one.payment_reference, captured,
		)
		fixture = RpcFixture(
			signatures=[signature("sig-edge", 41)],
			transactions={"sig-edge": transaction(100, 41)},
			confirmed_tip=45, finalized_tip=44,
		)
		observations = solana_devnet_sol.observe(
			paid_in_the_slot_that_was_in_progress, configuration(fixture))
		self.assertEqual([t.amount_native for t in observations.transfers], [100])
		self.assertEqual(
			solana_devnet_sol.settle(paid_in_the_slot_that_was_in_progress, observations).state,
			SETTLED)

	def test_a_history_known_to_be_incomplete_does_not_settle(self):
		# The signature walk stops at a safety limit and says so. Settling on
		# that number writes a knowingly-partial credit into a state that can
		# never be reopened.
		one = intent(amount=100)
		fixture = RpcFixture(
			signatures=[signature("sig-one", 20)],
			transactions={"sig-one": transaction(100, 20)},
		)
		observations = solana_devnet_sol.observe(one, configuration(fixture))
		self.assertEqual(solana_devnet_sol.settle(one, observations).state, SETTLED)

		capped = ObservationBatch(
			observations.rail_key, observations.intent_id, observations.recipient,
			observations.provider, observations.baseline_tip, observations.tip,
			observations.observed_after_tip, observations.observed_through_tip,
			observations.transfers, observations.unattributed_native,
			(f"unknown transaction signature history: exceeded the "
				f"{MAX_SIGNATURES}-signature safety limit",),
			finalized_tip=observations.finalized_tip,
		)
		decision = solana_devnet_sol.settle(one, capped)
		self.assertEqual(decision.state, NEEDS_REVIEW)
		self.assertIn("could not be read in full", decision.reason)


	def test_a_node_that_pruned_past_the_sale_does_not_report_nothing_paid(self):
		# An empty signature list from a node that has thrown away the slots
		# this sale cares about is not "nobody paid" -- and the difference is a
		# finalized payment lost to a clean expiry, with nothing saying why.
		one = intent(amount=100)
		fixture = RpcFixture(minimum_ledger_slot=one.baseline.tip + 5)
		observations = solana_devnet_sol.observe(one, configuration(fixture))
		self.assertEqual(observations.transfers, ())
		self.assertTrue(any("pruned to slot" in warning for warning in observations.warnings))

		decision = solana_devnet_sol.settle(one, observations)
		self.assertEqual(decision.state, NEEDS_REVIEW)
		self.assertIn("could not be read in full", decision.reason)

	def test_a_node_retaining_the_sale_is_not_warned_about(self):
		one = intent(amount=100)
		fixture = RpcFixture(
			signatures=[signature("sig-one", 20)],
			transactions={"sig-one": transaction(100, 20)},
			minimum_ledger_slot=1,
		)
		observations = solana_devnet_sol.observe(one, configuration(fixture))
		self.assertEqual(observations.warnings, ())
		self.assertEqual(solana_devnet_sol.settle(one, observations).state, SETTLED)


class RecordedVectors(unittest.TestCase):
	"""The shared attribution vectors, four of them real devnet transactions.

	The same file is run against every other consumer of these vectors by
	`cryptopos/tools/attribution_agreement.py`. Sharing the EXAMPLES rather than
	the implementation is deliberate: a reconciliation that shares code with the
	thing it reconciles proves nothing, and two implementations with no shared
	examples drift apart — which is exactly what happened between D33 and the
	reconciler's copy of it.
	"""

	def test_every_recorded_vector_attributes_as_recorded(self):
		import json
		document = json.loads(
			(PACKAGE_ROOT / "tests" / "attribution_vectors.json").read_text())
		for vector in document["vectors"]:
			with self.subTest(vector=vector["name"]):
				transaction = vector["transaction"]
				parsed, reason = solana_devnet_sol._transaction_amount(
					transaction, vector["recipient"], vector["reference"],
					transaction["slot"])
				got = None if parsed is None else parsed[0]
				self.assertEqual(got, vector["expected_lamports"],
					f"{vector['why']} (rail said: {reason})")


if __name__ == "__main__":
	unittest.main()
