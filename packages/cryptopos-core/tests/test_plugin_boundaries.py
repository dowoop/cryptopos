"""Adversarial boundary tests for the immutable plugin contract values."""

import unittest
from dataclasses import FrozenInstanceError

from cryptopos_core.errors import InvalidAmount, InvalidRailPlugin
from cryptopos_core.plugin import (
	ADDRESS_VALIDATION,
	NEEDS_REVIEW,
	OBSERVATION,
	PAYMENT_REQUEST,
	PENDING,
	SETTLED,
	SETTLEMENT,
	Asset,
	Network,
	ObservationBatch,
	PaymentIntent,
	PaymentRequest,
	Readiness,
	RecipientBaseline,
	SettlementDecision,
	TransferObservation,
	_identifier,
	_reference,
)

RAIL = "example:testnet/native:coin"


def baseline(**changes):
	values = {
		"rail_key": RAIL,
		"recipient": "merchant",
		"provider": "provider",
		"tip": 5,
	}
	values.update(changes)
	return RecipientBaseline(**values)


def intent(**changes):
	values = {
		"intent_id": "sale-1",
		"rail_key": RAIL,
		"recipient": "merchant",
		"amount_native": 10,
		"created_at_epoch": 100,
		"expires_at_epoch": 200,
		"baseline": baseline(),
	}
	values.update(changes)
	return PaymentIntent(**values)


def transfer(transaction_id="tx-1", block_height=6):
	return TransferObservation(transaction_id, 10, True, 1, block_height, 150)


def batch(**changes):
	values = {
		"rail_key": RAIL,
		"intent_id": "sale-1",
		"recipient": "merchant",
		"provider": "provider",
		"baseline_tip": 5,
		"tip": 7,
		"observed_after_tip": 5,
		"observed_through_tip": 7,
		"transfers": (transfer(),),
	}
	values.update(changes)
	return ObservationBatch(**values)


class IdentityBoundaries(unittest.TestCase):
	def test_network_and_asset_reject_wrong_shapes(self):
		for factory in (
			lambda: Network("bitcoin", "testnet4", 1),
			lambda: Asset("native", "bad:reference", "COIN", 8),
			lambda: Asset("native", "coin", "1COIN", 8),
		):
			with self.subTest(factory=factory), self.assertRaises(InvalidRailPlugin):
				factory()


class BaselineAndIntentBoundaries(unittest.TestCase):
	def test_baseline_rejects_unbound_or_malformed_provider_facts(self):
		for changes in (
			{"rail_key": ""},
			{"recipient": ""},
			{"provider": ""},
			{"tip": -1},
			{"transaction_ids": ["tx"]},
			{"transaction_ids": ("",)},
			{"transaction_ids": ("tx", "tx")},
			{"balance_native": -1},
		):
			with self.subTest(changes=changes), self.assertRaises(InvalidRailPlugin):
				baseline(**changes)

	def test_baseline_normalizes_integer_text(self):
		value = baseline(tip="5", balance_native="12")
		self.assertEqual((value.tip, value.balance_native), (5, 12))

	def test_intent_rejects_missing_identity_and_malformed_metadata(self):
		for changes, error in (
			({"intent_id": ""}, InvalidRailPlugin),
			({"rail_key": ""}, InvalidRailPlugin),
			({"recipient": ""}, InvalidRailPlugin),
			({"amount_native": 0}, InvalidAmount),
			({"created_at_epoch": -1}, InvalidAmount),
			({"payment_reference": None}, InvalidRailPlugin),
			({"baseline": object()}, InvalidRailPlugin),
			({"baseline": baseline(rail_key="other")}, InvalidRailPlugin),
			({"baseline": baseline(recipient="other")}, InvalidRailPlugin),
		):
			with self.subTest(changes=changes), self.assertRaises(error):
				intent(**changes)


class RequestAndTransferBoundaries(unittest.TestCase):
	def test_payment_request_rejects_unpresentable_instructions(self):
		for arguments, error in (
			(("", "coin:merchant", "merchant", 10), InvalidRailPlugin),
			((RAIL, "coin:merchant\n", "merchant", 10), InvalidRailPlugin),
			((RAIL, "coin:méchant", "merchant", 10), InvalidRailPlugin),
			((RAIL, "coin:merchant", "merchant", 10, None), InvalidRailPlugin),
			((RAIL, "coin:merchant", "merchant", 0), InvalidAmount),
		):
			with self.subTest(arguments=arguments), self.assertRaises(error):
				PaymentRequest(*arguments)

	def test_transfer_rejects_ambiguous_transaction_facts(self):
		for arguments, error in (
			(("", 10, True, 1), InvalidRailPlugin),
			(("bad id", 10, True, 1), InvalidRailPlugin),
			(("tx", 0, True, 1), InvalidAmount),
			(("tx", 10, 1, 1), InvalidRailPlugin),
			(("tx", 10, True, -1), InvalidRailPlugin),
			(("tx", 10, True, 0), InvalidRailPlugin),
			(("tx", 10, False, 0, 5), InvalidRailPlugin),
		):
			with self.subTest(arguments=arguments), self.assertRaises(error):
				TransferObservation(*arguments)


class ObservationBoundaries(unittest.TestCase):
	def test_batch_rejects_missing_identity_and_invalid_ranges(self):
		for changes in (
			{"rail_key": ""},
			{"intent_id": ""},
			{"recipient": ""},
			{"provider": ""},
			{"tip": -1},
			{"baseline_tip": None},
			{"observed_after_tip": 4},
			{"observed_through_tip": 8},
			{"transfers": [transfer()]},
			{"transfers": (transfer(block_height=5),)},
			{"unattributed_native": -1},
			{"warnings": ("",)},
			{"finalized_tip": 8},
		):
			with self.subTest(changes=changes), self.assertRaises(InvalidRailPlugin):
				batch(**changes)

	def test_batch_normalizes_amount_and_positions(self):
		value = batch(
			baseline_tip="5",
			tip="7",
			observed_after_tip="5",
			observed_through_tip="7",
			unattributed_native="3",
			finalized_tip="6",
		)
		self.assertEqual((value.unattributed_native, value.finalized_tip), (3, 6))

	def test_require_intent_refuses_wrong_type_and_any_binding_difference(self):
		value = batch()
		with self.assertRaises(InvalidRailPlugin):
			value.require_intent(object())
		for payment in (
			intent(intent_id="other"),
			intent(baseline=None),
			intent(baseline=baseline(provider="other")),
		):
			with self.subTest(payment=payment), self.assertRaises(InvalidRailPlugin):
				value.require_intent(payment)

	def test_extend_refuses_pages_that_cannot_be_safely_accumulated(self):
		first = batch(tip=8, observed_through_tip=6)
		with self.assertRaises(InvalidRailPlugin):
			first.extend(object())
		for page in (
			batch(intent_id="other", observed_after_tip=6, transfers=(transfer(block_height=7),)),
			batch(observed_after_tip=7, transfers=()),
			batch(tip=7, observed_after_tip=6, transfers=(transfer(block_height=7),)),
			batch(tip=8, observed_after_tip=6, transfers=(), unattributed_native=1),
			batch(tip=8, observed_after_tip=6, transfers=(transfer(block_height=7),)),
		):
			with self.subTest(page=page), self.assertRaises(InvalidRailPlugin):
				first.extend(page)

	def test_extend_deduplicates_warnings_and_keeps_highest_finality(self):
		first = batch(
			tip=8,
			observed_through_tip=6,
			warnings=("first",),
			finalized_tip=5,
		)
		page = batch(
			tip=8,
			observed_after_tip=6,
			observed_through_tip=8,
			transfers=(transfer("tx-2", 7),),
			warnings=("first", "second"),
			finalized_tip=7,
		)
		combined = first.extend(page)
		self.assertEqual(combined.warnings, ("first", "second"))
		self.assertEqual(combined.finalized_tip, 7)


class DecisionAndReadinessBoundaries(unittest.TestCase):
	def test_settlement_rejects_incoherent_money_and_attribution(self):
		for arguments in (
			("unknown", 0, 0),
			(PENDING, -1, 0),
			(PENDING, 2, 1),
			(SETTLED, 1, 1, ["tx"]),
			(SETTLED, 1, 1, ("",)),
			(SETTLED, 1, 1, ("tx", "tx")),
			(PENDING, 0, 0, (), None),
			(SETTLED, 0, 1, ()),
			(NEEDS_REVIEW, 0, 1, ("tx",)),
		):
			with self.subTest(arguments=arguments), self.assertRaises(InvalidRailPlugin):
				SettlementDecision(*arguments)

	def test_transaction_id_is_only_a_display_convenience(self):
		self.assertEqual(SettlementDecision(PENDING, 0, 0).transaction_id, "")
		self.assertEqual(SettlementDecision(SETTLED, 1, 1, ("tx",)).transaction_id, "tx")

	def test_readiness_rejects_malformed_capability_reports(self):
		for arguments in (
			("", frozenset()),
			(RAIL, {ADDRESS_VALIDATION}),
			(RAIL, frozenset({"unknown"})),
			(RAIL, frozenset(), []),
			(RAIL, frozenset(), (("unknown", "why"),)),
			(RAIL, frozenset(), ((OBSERVATION, ""),)),
			(RAIL, frozenset(), ((OBSERVATION, "one"), (OBSERVATION, "two"))),
		):
			with self.subTest(arguments=arguments), self.assertRaises(InvalidRailPlugin):
				Readiness(*arguments)

	def test_reason_for_unknown_capability_is_empty(self):
		readiness = Readiness(
			RAIL,
			frozenset({ADDRESS_VALIDATION, PAYMENT_REQUEST}),
			((OBSERVATION, "offline"), (SETTLEMENT, "offline")),
		)
		self.assertEqual(readiness.reason_for("not-reported"), "")


class MutationBoundaryPins(unittest.TestCase):
	def test_identity_helpers_return_the_validated_value(self):
		self.assertEqual(_identifier("field", "valid-id"), "valid-id")
		self.assertEqual(_reference("field", "Case-Sensitive"), "Case-Sensitive")

	def test_every_contract_value_is_immutable(self):
		values = (
			Network("example", "testnet", True),
			Asset("native", "coin", "COIN", 6),
			baseline(),
			intent(),
			PaymentRequest(RAIL, "coin:merchant", "merchant", 10),
			TransferObservation("tx", 1, False),
			batch(transfers=()),
			SettlementDecision(PENDING, 0, 0),
			Readiness(RAIL, frozenset()),
		)
		for value in values:
			with self.subTest(value=value), self.assertRaises(FrozenInstanceError):
				value.__setattr__(next(iter(value.__dataclass_fields__)), "changed")

	def test_exact_numeric_contract_boundaries_are_accepted(self):
		self.assertEqual(Asset("native", "zero", "ZERO", 0).decimals, 0)
		self.assertEqual(Asset("native", "thirty", "THIRTY", 30).decimals, 30)
		self.assertEqual(baseline(tip=0, balance_native=0).tip, 0)
		created_at_zero = intent(created_at_epoch=0, expires_at_epoch=1)
		self.assertEqual(created_at_zero.created_at_epoch, 0)
		self.assertEqual(PaymentRequest(RAIL, "x" * 4096, "merchant", 1, "n" * 512).amount_native, 1)
		unconfirmed = TransferObservation("x" * 256, 1, False, 0)
		self.assertEqual(unconfirmed.confirmations, 0)
		confirmed = TransferObservation("tx", 1, True, 1, 0, 0)
		self.assertEqual((confirmed.block_height, confirmed.block_time_epoch), (0, 0))
		positions = batch(
			baseline_tip=0,
			tip=0,
			observed_after_tip=0,
			observed_through_tip=0,
			transfers=(),
			unattributed_native=0,
			finalized_tip=0,
		)
		self.assertTrue(positions.complete)

	def test_each_identity_type_check_stands_on_its_own(self):
		for changes in (
			{"intent_id": 1},
			{"rail_key": 1, "baseline": None},
			{"recipient": 1, "baseline": None},
		):
			with self.subTest(changes=changes), self.assertRaises(InvalidRailPlugin):
				intent(**changes)

	def test_created_time_error_preserves_its_zero_minimum(self):
		with self.assertRaises(InvalidAmount) as caught:
			intent(created_at_epoch=-1)
		self.assertEqual(caught.exception.minimum, 0)

	def test_size_ceilings_reject_the_first_excess_value(self):
		for factory in (
			lambda: PaymentRequest(RAIL, "x" * 4097, "merchant", 1),
			lambda: PaymentRequest(RAIL, "coin:merchant", "merchant", 1, "n" * 513),
			lambda: TransferObservation("x" * 257, 1, False),
		):
			with self.subTest(factory=factory), self.assertRaises(InvalidRailPlugin):
				factory()

	def test_independent_integer_shape_guards_do_not_rely_on_short_circuiting(self):
		for arguments in (("tx", 1, False, "bad"), ("tx", 1, False, -1)):
			with self.subTest(arguments=arguments), self.assertRaises(InvalidRailPlugin):
				TransferObservation(*arguments)
		for arguments in (
			("tx", 1, True, 1, "bad"),
			("tx", 1, False, 0, 0),
		):
			with self.subTest(arguments=arguments), self.assertRaises(InvalidRailPlugin):
				TransferObservation(*arguments)
		for changes in (
			{"baseline_tip": "bad"},
			{"baseline_tip": -1},
			{"finalized_tip": "bad"},
			{"finalized_tip": -1},
		):
			with self.subTest(changes=changes), self.assertRaises(InvalidRailPlugin):
				batch(**changes)

	def test_extend_accepts_a_forward_provider_tip(self):
		first = batch(tip=8, observed_through_tip=6, transfers=())
		page = batch(
			tip=9,
			observed_after_tip=6,
			observed_through_tip=9,
			transfers=(transfer(block_height=7),),
		)
		self.assertEqual(first.extend(page).tip, 9)

	def test_settled_money_and_attribution_requirements_are_independent(self):
		for arguments in (
			(SETTLED, 0, 1, ("tx",)),
			(SETTLED, 1, 1, ()),
		):
			with self.subTest(arguments=arguments), self.assertRaises(InvalidRailPlugin):
				SettlementDecision(*arguments)


if __name__ == "__main__":
	unittest.main()
