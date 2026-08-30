"""A complete native-SOL payment rail for Solana devnet.

Each intent derives its Solana Pay reference from its immutable intent id. The
provider is verified as devnet before every baseline and observation, and only
the recipient's lamport balance delta is considered. Incomplete transaction
answers are preserved as warnings instead of being guessed as zero payment.
"""

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping

from cryptopos_core.addresses import REFUSED, validate
from cryptopos_core.errors import AddressRefused, InvalidRailPlugin, RailProviderError
from cryptopos_core.plugin import (
	ADDRESS_VALIDATION,
	NEEDS_REVIEW,
	OBSERVATION,
	PAYMENT_REQUEST,
	PENDING,
	SETTLED,
	SETTLEMENT,
	UNCONDITIONAL_PER_SALE,
	Asset,
	Network,
	ObservationBatch,
	PaymentIntent,
	PaymentRequest,
	Readiness,
	RecipientBaseline,
	SettlementDecision,
	TransferObservation,
)
from cryptopos_core.uri import base58_encode, build_uri

# Read off the chain, not off a memory: `getGenesisHash` on
# https://api.devnet.solana.com answered this on 2026-08-25. The first
# draft of this constant was the same value truncated at 32 characters,
# and every one of this package's 14 tests passed against it, because a
# test that never touches a node cannot notice that no node will ever
# match. It would have refused every real devnet endpoint as "not devnet".
DEVNET_GENESIS_HASH = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"
MAX_RESPONSE_BYTES = 4_000_000
MAX_SIGNATURES = 10_000
SIGNATURE_PAGE_SIZE = 1_000
DEFAULT_TIMEOUT_SECONDS = 5.0

_UNKNOWN_PREFIX = "unknown transaction "

SYSTEM_PROGRAM = "11111111111111111111111111111111"
_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {character: value for value, character in enumerate(_B58_ALPHABET)}

# `SystemInstruction::Transfer` is discriminant 2, little-endian u32, followed by
# a little-endian u64 of lamports. Twelve bytes, no more and no less.
_TRANSFER_DISCRIMINANT = 2
_TRANSFER_DATA_LENGTH = 12


def _base58_decode(text):
	"""base58 text -> bytes, or None if it is not base58 at all."""
	if not isinstance(text, str) or not text:
		return None
	value = 0
	for character in text:
		digit = _B58_INDEX.get(character)
		if digit is None:
			return None
		value = value * 58 + digit
	body = value.to_bytes((value.bit_length() + 7) // 8, "big") if value else b""
	leading = len(text) - len(text.lstrip("1"))
	return b"\x00" * leading + body


class _NoRedirect(urllib.request.HTTPRedirectHandler):
	def redirect_request(self, request, file_pointer, code, message, headers, new_url):
		return None


class _JsonRpcTransport:
	def __init__(self):
		self._opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))

	def post(self, url, body, timeout, max_bytes):
		request = urllib.request.Request(
			url,
			data=body,
			headers={
				"Accept": "application/json",
				"Content-Type": "application/json",
				"User-Agent": "cryptopos-rail-solana/1",
			},
			method="POST",
		)
		with self._opener.open(request, timeout=timeout) as response:
			payload = response.read(max_bytes + 1)
		if len(payload) > max_bytes:
			raise ValueError("response exceeded the safety limit")
		return payload


def _configuration(configuration, rail_key):
	if not isinstance(configuration, Mapping):
		raise RailProviderError(rail_key, "configuration must be a mapping")
	endpoint = configuration.get("endpoint")
	if not isinstance(endpoint, str) or not endpoint.strip():
		raise RailProviderError(rail_key, "an explicit JSON-RPC endpoint is required")
	parts = urllib.parse.urlsplit(endpoint.strip())
	if (
		parts.scheme != "https"
		or not parts.hostname
		or parts.username is not None
		or parts.password is not None
		or parts.query
		or parts.fragment
	):
		raise RailProviderError(
			rail_key,
			"endpoint must be an HTTPS URL without credentials, query text, or a fragment",
		)
	base = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
	transport = configuration.get("transport") or _JsonRpcTransport()
	if not callable(getattr(transport, "post", None)):
		raise RailProviderError(rail_key, "transport must provide a post method")
	timeout = configuration.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
	if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
		raise RailProviderError(rail_key, "timeout_seconds must be greater than 0 and at most 30")
	return base, transport, float(timeout)


def _rpc(rail_key, provider, method, params):
	base, transport, timeout = provider
	request = json.dumps(
		{"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
		separators=(",", ":"),
	).encode()
	try:
		payload = transport.post(base, request, timeout=timeout, max_bytes=MAX_RESPONSE_BYTES)
	except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, ValueError) as exception:
		raise RailProviderError(rail_key, f"{method} failed: {exception}") from None
	if not isinstance(payload, bytes):
		raise RailProviderError(rail_key, f"{method} returned non-byte data")
	if len(payload) > MAX_RESPONSE_BYTES:
		raise RailProviderError(rail_key, f"{method} exceeded the response safety limit")
	try:
		response = json.loads(payload.decode("utf-8"))
	except (UnicodeDecodeError, json.JSONDecodeError) as exception:
		raise RailProviderError(rail_key, f"{method} did not return valid JSON: {exception}") from None
	if not isinstance(response, dict) or response.get("jsonrpc") != "2.0" or response.get("id") != 1:
		raise RailProviderError(rail_key, f"{method} returned a malformed JSON-RPC envelope")
	if response.get("error") is not None:
		raise RailProviderError(rail_key, f"{method} returned JSON-RPC error {response['error']!r}")
	if "result" not in response:
		raise RailProviderError(rail_key, f"{method} returned no result")
	return response["result"]


def _nonnegative_integer(rail_key, value, field):
	if isinstance(value, bool) or not isinstance(value, int) or value < 0:
		raise RailProviderError(rail_key, f"{field} was not a non-negative integer")
	return value


def reference_for_intent(intent_id):
	"""Return the sale-binding reference: base58(sha256(UTF-8 intent id))."""
	if not isinstance(intent_id, str) or not intent_id:
		raise InvalidRailPlugin("Solana reference derivation requires a non-empty intent id")
	return base58_encode(hashlib.sha256(intent_id.encode("utf-8")).digest())


class SolanaDevnetSolRail:
	"""Address, request, reference-bound observation, and finality for devnet SOL."""

	# THE MONEY IS BOUND EVEN THOUGH THE ADDRESS IS NOT -- and the binding lives
	# in `_referenced_transfer_total`, which reads the transfer INSTRUCTION.
	# Searching the transaction's account list for the reference is not enough
	# and was not enough: one transfer naming two sales' references settled both
	# of them (D33). Credit comes from an instruction whose recipient is this
	# merchant and whose accounts carry this sale's reference and nothing else.
	#
	# With that, two concurrent sales to one address are told apart, and
	# cryptopos D5 -- a shared address cannot be made safe by bookkeeping -- does
	# not describe this rail. Declared for `tools/rails_probe.py`, which would
	# otherwise report it as an unbound shared address on the address alone.
	#
	# This category is a CLAIM. It was true of the design and false of the code
	# for an hour. Do not declare it on a rail whose binding has not been attacked.
	binding_category = UNCONDITIONAL_PER_SALE

	network = Network("solana", "devnet", True)
	asset = Asset("native", "sol", "DevnetSOL", 9)
	key = f"{network.key}/{asset.key}"
	capabilities = frozenset({ADDRESS_VALIDATION, PAYMENT_REQUEST, OBSERVATION, SETTLEMENT})

	def validate_recipient(self, recipient):
		return validate("sol", recipient, "testnet")

	def readiness(self, configuration):
		ready = {ADDRESS_VALIDATION, PAYMENT_REQUEST, SETTLEMENT}
		unavailable = []
		try:
			provider = self._provider(configuration)
			self._verify_network(provider)
			confirmed_tip = self._tip(provider, "confirmed")
			finalized_tip = self._tip(provider, "finalized")
			if finalized_tip > confirmed_tip:
				raise RailProviderError(self.key, "finalized slot is above the confirmed slot")
			probe = reference_for_intent("cryptopos-readiness-probe")
			signatures = _rpc(
				self.key,
				provider,
				"getSignaturesForAddress",
				[probe, {"commitment": "confirmed", "limit": 1}],
			)
			if not isinstance(signatures, list) or len(signatures) > 1:
				raise RailProviderError(self.key, "signature readiness probe was malformed")
		except RailProviderError as exception:
			unavailable.append((OBSERVATION, exception.reason))
		else:
			ready.add(OBSERVATION)
		return Readiness(self.key, frozenset(ready), tuple(unavailable))

	def capture_baseline(self, recipient, configuration):
		self._verified_recipient(recipient)
		provider = self._provider(configuration)
		self._verify_network(provider)
		# THE LAST SLOT ALREADY BEHIND US, not the one in progress.
		#
		# `ObservationBatch` requires every transfer to sit STRICTLY above the
		# baseline, so recording the current confirmed slot silently excludes any
		# payment landing in that same slot -- and a payer can be that fast. It
		# is a real loss and a permanent one: the sale expires unpaid with the
		# money on the chain and nothing ever looks below the baseline again.
		#
		# Recording one slot lower makes the slot that was in progress at capture
		# time admissible, which is what "everything from now on" actually means.
		# Nothing older can be this sale's money regardless: the reference is
		# derived per sale, so no transaction touching it predates the request.
		current = self._tip(provider, "confirmed")
		return RecipientBaseline(self.key, recipient, provider[0], max(current - 1, 0))

	def create_request(self, intent):
		self._intent(intent)
		self._verified_recipient(intent.recipient)
		if intent.baseline is None:
			raise InvalidRailPlugin("Solana devnet requires a slot baseline before request creation")
		reference = reference_for_intent(intent.intent_id)
		uri = build_uri(
			"sol",
			{"address": intent.recipient, "reference": reference},
			intent.amount_native,
			"testnet",
		)
		return PaymentRequest(
			self.key,
			uri,
			intent.recipient,
			intent.amount_native,
			"Configure the payer wallet for Solana devnet; Solana Pay does not encode a cluster.",
		)

	def observe(self, intent, configuration, previous=None):
		self._intent(intent)
		if intent.baseline is None or intent.baseline.tip is None:
			raise InvalidRailPlugin("Solana devnet observation requires a captured baseline")
		if previous is not None:
			if not isinstance(previous, ObservationBatch):
				raise InvalidRailPlugin("previous observations have an unknown shape")
			previous.require_intent(intent)
		provider = self._provider(configuration)
		if provider[0] != intent.baseline.provider:
			raise RailProviderError(self.key, "observation endpoint differs from the baseline endpoint")
		self._verify_network(provider)
		tip = self._tip(provider, "confirmed")
		if tip < intent.baseline.tip:
			raise RailProviderError(self.key, "provider tip is behind the captured baseline")
		finalized_tip = self._tip(provider, "finalized")
		if finalized_tip > tip:
			raise RailProviderError(self.key, "finalized slot is above the confirmed slot")

		# HAS THE NODE THROWN AWAY THE SLOTS THIS SALE CARES ABOUT?
		#
		# A node prunes. If its retained history advances past the slot a payment
		# landed in, `getSignaturesForAddress` returns an empty list -- and an
		# empty list is indistinguishable from "nobody has paid yet". The sale
		# would then expire cleanly while the money sat finalized on the chain,
		# with nothing anywhere saying why. `minimumLedgerSlot` is the only way
		# to tell those two apart, and it costs one call.
		#
		# Reported as a history warning on purpose: `settle` refuses to settle on
		# a history it knows is incomplete, so this reaches an operator as a
		# decision instead of a silent expiry.
		minimum = _rpc(self.key, provider, "minimumLedgerSlot", [])
		if isinstance(minimum, int) and not isinstance(minimum, bool) and minimum > intent.baseline.tip:
			pruned = (f"{_UNKNOWN_PREFIX}signature history: the node has pruned to slot "
				f"{minimum}, above this sale's baseline of {intent.baseline.tip}, so an "
				f"empty result cannot be read as 'nothing was paid'")
		else:
			pruned = ""

		reference = reference_for_intent(intent.intent_id)
		entries, history_warning = self._signatures(provider, reference, intent.baseline.tip, tip)
		transfers = []
		warnings = []
		if history_warning:
			warnings.append(history_warning)
		if pruned:
			warnings.append(pruned)
		relevant = 0
		failed = 0
		seen = set()
		for entry in sorted(entries, key=lambda item: (item["slot"], item["signature"])):
			signature = entry["signature"]
			if signature in seen:
				continue
			seen.add(signature)
			relevant += 1
			if entry["err"] is not None:
				failed += 1
				continue
			transaction = _rpc(
				self.key,
				provider,
				"getTransaction",
				[
					signature,
					{
						"commitment": entry["confirmationStatus"],
						"encoding": "json",
						"maxSupportedTransactionVersion": 0,
					},
				],
			)
			parsed, reason = self._transaction_amount(
				transaction,
				intent.recipient,
				reference,
				entry["slot"],
			)
			if parsed is None:
				warnings.append(f"{_UNKNOWN_PREFIX}{signature}: {reason}")
				continue
			amount, block_time = parsed
			confirmations = 2 if entry["confirmationStatus"] == "finalized" else 1
			transfers.append(
				TransferObservation(
					signature,
					amount,
					True,
					confirmations,
					entry["slot"],
					block_time,
				)
			)
		if relevant and failed == relevant:
			warnings.append("all observed signatures failed; failed transactions credit no SOL")
		return ObservationBatch(
			self.key,
			intent.intent_id,
			intent.recipient,
			provider[0],
			intent.baseline.tip,
			tip,
			intent.baseline.tip,
			tip,
			tuple(transfers),
			warnings=tuple(warnings),
			finalized_tip=finalized_tip,
		)

	def settle(self, intent, observations, claimed_transaction_ids=frozenset()):
		self._intent(intent)
		if not isinstance(observations, ObservationBatch):
			raise InvalidRailPlugin("observations have an unknown shape")
		observations.require_intent(intent)
		if not observations.complete:
			raise InvalidRailPlugin("settlement requires observations through the provider tip")
		if not isinstance(claimed_transaction_ids, frozenset) or any(
			not isinstance(transaction_id, str) for transaction_id in claimed_transaction_ids
		):
			raise InvalidRailPlugin("claimed transaction ids must be a frozenset of text")

		claimed = [
			transfer
			for transfer in observations.transfers
			if transfer.transaction_id in claimed_transaction_ids
		]
		available = [
			transfer
			for transfer in observations.transfers
			if transfer.transaction_id not in claimed_transaction_ids
		]
		sighted = sum(transfer.amount_native for transfer in available)
		mature = [
			transfer
			for transfer in available
			if transfer.confirmations >= 2
			and observations.finalized_tip is not None
			and transfer.block_height is not None
			and transfer.block_height <= observations.finalized_tip
		]
		timely = [
			transfer
			for transfer in mature
			if transfer.block_time_epoch is not None
			and transfer.block_time_epoch <= intent.expires_at_epoch
		]
		late = [transfer for transfer in mature if transfer not in timely]
		credited = sum(transfer.amount_native for transfer in timely)

		# A CREDIT THAT IS KNOWINGLY A LOWER BOUND MUST NOT BECOME A RECORD.
		# The signature walk stops at a safety limit and reports that it did.
		# Settling anyway writes a number the rail already knows is incomplete
		# into a state D10 says can never be reopened -- so an overpayment buried
		# below the cap would be lost permanently and silently. An operator
		# deciding is the worse-looking outcome and the only honest one.
		#
		# It can be provoked: spam the reference past the limit and every sale on
		# it goes to review. That is a denial of service, not a theft, and it is
		# the better of the two things an attacker can be given.
		incomplete = tuple(
			warning for warning in observations.warnings
			if warning.startswith(_UNKNOWN_PREFIX) and "signature history" in warning
		)
		if incomplete:
			return SettlementDecision(
				NEEDS_REVIEW,
				credited,
				sighted,
				reason=f"the payment history could not be read in full: {incomplete[0]}",
			)

		if credited >= intent.amount_native:
			ordered = tuple(
				transfer.transaction_id
				for transfer in sorted(timely, key=lambda item: (item.block_height, item.transaction_id))
			)
			reason = "Solana finalized commitment passed"
			if credited > intent.amount_native:
				reason += "; payment exceeds the invoice"
			return SettlementDecision(SETTLED, credited, sighted, ordered, reason)
		if claimed and sum(transfer.amount_native for transfer in claimed) + sighted >= intent.amount_native:
			return SettlementDecision(
				NEEDS_REVIEW,
				credited,
				sighted,
				reason="one or more observed transactions are already claimed by another intent",
			)
		if late and credited + sum(transfer.amount_native for transfer in late) >= intent.amount_native:
			return SettlementDecision(
				NEEDS_REVIEW,
				credited,
				sighted,
				reason="payment arrived after expiry or lacks a trustworthy block time",
			)
		unknown = tuple(warning for warning in observations.warnings if warning.startswith(_UNKNOWN_PREFIX))
		if unknown:
			return SettlementDecision(
				NEEDS_REVIEW,
				credited,
				sighted,
				reason=f"payment amount could not be read: {unknown[0]}",
			)
		if sighted >= intent.amount_native:
			return SettlementDecision(
				PENDING,
				credited,
				sighted,
				reason="payment is awaiting Solana finalized commitment",
			)
		if credited:
			reason = "finalized payment is below the invoice amount"
		elif sighted:
			reason = "payment seen but is below the invoice amount or awaiting finalization"
		elif any("all observed signatures failed" in warning for warning in observations.warnings):
			reason = "all observed transactions failed"
		else:
			reason = "no payment observed"
		return SettlementDecision(PENDING, credited, sighted, reason=reason)

	def _provider(self, configuration):
		return _configuration(configuration, self.key)

	def _verify_network(self, provider):
		genesis = _rpc(self.key, provider, "getGenesisHash", [])
		if genesis != DEVNET_GENESIS_HASH:
			raise RailProviderError(self.key, f"genesis hash {genesis!r} is not Solana devnet")

	def _tip(self, provider, commitment):
		return _nonnegative_integer(
			self.key,
			_rpc(self.key, provider, "getSlot", [{"commitment": commitment}]),
			f"{commitment} slot",
		)

	def _signatures(self, provider, reference, baseline_tip, tip):
		entries = []
		before = None
		warning = ""
		while len(entries) < MAX_SIGNATURES:
			options = {"commitment": "confirmed", "limit": SIGNATURE_PAGE_SIZE}
			if before is not None:
				options["before"] = before
			page = _rpc(self.key, provider, "getSignaturesForAddress", [reference, options])
			if not isinstance(page, list) or len(page) > SIGNATURE_PAGE_SIZE:
				raise RailProviderError(self.key, "signature history was malformed or excessive")
			if not page:
				break
			reached_baseline = False
			last_signature = None
			for raw in page:
				entry, reason = self._signature_entry(raw, tip)
				if entry is None:
					warning = f"{_UNKNOWN_PREFIX}signature history: {reason}"
					continue
				last_signature = entry["signature"]
				if entry["slot"] <= baseline_tip:
					# The baseline is the last slot already behind the sale --
					# see `capture_baseline`, which deliberately records one
					# below the slot in progress so that a payment landing in
					# that slot is still above this line. The boundary is a
					# paging stop; attribution is the reference's job.
					reached_baseline = True
					continue
				entries.append(entry)
			if reached_baseline or len(page) < SIGNATURE_PAGE_SIZE:
				break
			if last_signature is None or last_signature == before:
				warning = f"{_UNKNOWN_PREFIX}signature history: pagination made no progress"
				break
			before = last_signature
		if len(entries) >= MAX_SIGNATURES:
			warning = f"{_UNKNOWN_PREFIX}signature history: exceeded the {MAX_SIGNATURES}-signature safety limit"
		return entries, warning

	def _signature_entry(self, entry, tip):
		if not isinstance(entry, dict):
			return None, "signature entry was not an object"
		signature = entry.get("signature")
		if not isinstance(signature, str) or not signature or len(signature) > 256 or any(
			character.isspace() for character in signature
		):
			return None, "signature was malformed"
		slot = entry.get("slot")
		if isinstance(slot, bool) or not isinstance(slot, int) or slot < 0 or slot > tip:
			return None, "signature slot was malformed or above the provider tip"
		if "err" not in entry:
			return None, "signature result did not report transaction success"
		status = entry.get("confirmationStatus")
		if status not in ("confirmed", "finalized"):
			return None, "signature commitment was neither confirmed nor finalized"
		return {
			"signature": signature,
			"slot": slot,
			"err": entry["err"],
			"confirmationStatus": status,
		}, ""

	def _transaction_amount(self, transaction, recipient, reference, expected_slot):
		if transaction is None:
			return None, "getTransaction returned no transaction"
		if not isinstance(transaction, dict):
			return None, "getTransaction result was not an object"
		slot = transaction.get("slot")
		if isinstance(slot, bool) or not isinstance(slot, int) or slot != expected_slot:
			return None, "transaction slot was missing or did not match the signature"
		meta = transaction.get("meta")
		if meta is None:
			return None, "transaction meta was null"
		if not isinstance(meta, dict):
			return None, "transaction meta was not an object"
		if "err" not in meta:
			return None, "transaction meta did not report success"
		if meta.get("err") is not None:
			return None, "transaction metadata reports failure"
		loaded = meta.get("loadedAddresses")
		if loaded is not None:
			if not isinstance(loaded, dict):
				return None, "loaded addresses were malformed"
			writable = loaded.get("writable")
			readonly = loaded.get("readonly")
			if not isinstance(writable, list) or not isinstance(readonly, list):
				return None, "loaded addresses were malformed"
			if writable or readonly:
				return None, "transaction uses address lookup tables; amount cannot be read safely"
		payload = transaction.get("transaction")
		if not isinstance(payload, dict):
			return None, "transaction payload was missing"
		message = payload.get("message")
		if not isinstance(message, dict):
			return None, "transaction message was missing"
		account_keys = message.get("accountKeys")
		if not isinstance(account_keys, list) or not account_keys:
			return None, "transaction accountKeys were missing"
		if any(not isinstance(account, str) or not account for account in account_keys):
			return None, "transaction accountKeys were malformed"
		indexes = [index for index, account in enumerate(account_keys) if account == recipient]
		if len(indexes) != 1:
			return None, "recipient was missing from transaction accountKeys"
		if account_keys.count(reference) != 1:
			return None, "sale reference was missing from transaction accountKeys"
		pre_balances = meta.get("preBalances")
		post_balances = meta.get("postBalances")
		if not isinstance(pre_balances, list) or not isinstance(post_balances, list):
			return None, "lamport balance arrays were missing"
		if len(pre_balances) != len(account_keys) or len(post_balances) != len(account_keys):
			return None, "lamport balance arrays did not match accountKeys"
		if any(
			isinstance(balance, bool) or not isinstance(balance, int) or balance < 0
			for balance in (*pre_balances, *post_balances)
		):
			return None, "lamport balance was not a non-negative integer"
		index = indexes[0]
		delta = post_balances[index] - pre_balances[index]
		if delta <= 0:
			return None, "recipient lamport balance did not increase"

		# WHAT A REFERENCED TRANSFER MOVED, NOT WHAT THE BALANCE DID.
		#
		# `getSignaturesForAddress` returns every transaction whose account list
		# MENTIONS the reference. It does not say which instruction used it or
		# what that instruction moved, and Solana Pay allows several references
		# on one transfer. Crediting the recipient's whole transaction-wide
		# balance delta on the strength of a name in the account list is not a
		# binding; it is a search hit.
		#
		# Reproduced 2026-08-25 against this file: one 100-lamport transfer whose
		# account list named two sales' references settled BOTH of them at 100,
		# and the loser of the race went to needs-review. The reference is only a
		# binding when it is checked ON the instruction that paid.
		amount = self._referenced_transfer_total(message, account_keys, index, reference)
		if amount is None:
			return None, ("no System transfer instruction carrying this sale's "
				"reference paid the recipient")
		if amount <= 0:
			return None, "the referenced transfer instructions moved nothing"
		if delta < amount:
			# The instructions say more arrived than the balance shows. The
			# recipient also spent in this transaction, or paid its fee, or the
			# node is not telling a consistent story. Any of those is a reason to
			# say so rather than to pick whichever number is larger.
			return None, (f"referenced transfers total {amount} lamports but the "
				f"recipient balance moved {delta}")
		block_time = transaction.get("blockTime")
		if block_time is not None and (
			isinstance(block_time, bool) or not isinstance(block_time, int) or block_time < 0
		):
			return None, "transaction blockTime was malformed"
		return (amount, block_time), ""

	def _referenced_transfer_total(self, message, account_keys, recipient_index, reference):
		"""Lamports moved to the recipient by System transfers carrying `reference`.

		None when the transaction cannot be read this way at all -- which is an
		answer, and a better one than a number nobody can defend.
		"""
		instructions = message.get("instructions")
		if not isinstance(instructions, list) or not instructions:
			return None
		try:
			reference_index = account_keys.index(reference)
			system_index = account_keys.index(SYSTEM_PROGRAM)
		except ValueError:
			return None

		total = 0
		found = False
		for instruction in instructions:
			if not isinstance(instruction, dict):
				return None
			if instruction.get("programIdIndex") != system_index:
				continue
			accounts = instruction.get("accounts")
			if not isinstance(accounts, list) or len(accounts) < 2:
				continue
			# accounts[0] funds, accounts[1] receives. Solana Pay appends the
			# reference to the SAME instruction as an extra read-only key, which
			# is the whole mechanism -- so it has to be found here and nowhere
			# else in the transaction.
			if accounts[1] != recipient_index or reference_index not in accounts:
				continue
			# EXACTLY ONE REFERENCE ON THE INSTRUCTION, and this is what closes
			# the attack the instruction check alone does not. Solana Pay permits
			# several references on one transfer, so a payer who can see two
			# sales' QRs can send ONE transfer carrying BOTH references and
			# settle both invoices from one payment -- reproduced 2026-08-25:
			# a single 100-lamport transfer settled a 60-lamport sale and a
			# 40-lamport sale, and the loser of the race went to needs-review.
			#
			# A Solana Pay transfer for one sale carries [from, to, reference]
			# and nothing else. Anything beyond that cannot be attributed to this
			# sale by looking at it, so it is not attributed at all.
			extras = [account for account in accounts[2:] if account != reference_index]
			if extras:
				return None
			if accounts.count(reference_index) != 1:
				return None
			data = _base58_decode(instruction.get("data"))
			if data is None or len(data) != _TRANSFER_DATA_LENGTH:
				return None
			if int.from_bytes(data[:4], "little") != _TRANSFER_DISCRIMINANT:
				continue
			total += int.from_bytes(data[4:], "little")
			found = True
		return total if found else None

	def _verified_recipient(self, recipient):
		verdict, reason = self.validate_recipient(recipient)
		if verdict == REFUSED:
			raise AddressRefused("sol", recipient, verdict, reason)

	def _intent(self, intent):
		if not isinstance(intent, PaymentIntent) or intent.rail_key != self.key:
			raise InvalidRailPlugin("payment intent belongs to another rail")


solana_devnet_sol = SolanaDevnetSolRail()

__all__ = ["SolanaDevnetSolRail", "reference_for_intent", "solana_devnet_sol"]
