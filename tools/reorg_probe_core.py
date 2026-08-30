# ruff: noqa: UP006, UP035, UP045 -- these tools must parse and run on Python 3.9
"""Pure, stateless classification for :mod:`tools.reorg_probe`.

The question is block identity, not whether a transaction-shaped answer exists.
Every run reads a transaction's containing position, reads the canonical block
at that position, and compares the identities.  Nothing in this module imports
Frappe, opens a socket, or supplies a chain answer: callers inject ``transport``.

The optional journal in the live shell records observations only.  It never
supplies an expected hash because an expectation taken from a previous read of
the same endpoint is still that endpoint's opinion (D39).  A check must not
derive its expectation from the thing that can break.  There is deliberately no
rolling header cache and no common-ancestor walk here.

Sale precedence is ``GONE > REMINED > SHALLOW > UNREACHABLE > BACKED``.  Thus a
definitive missing transaction is not hidden by a two-read block mismatch, and
a known under-backed transaction is not hidden by a second unanswered read.
Only REMINED, GONE, and SHALLOW contribute to the actionable count.
UNREACHABLE contributes to a separate inconclusive process result.

Python 3.9 is supported.  Keep runtime annotations free of PEP 604 unions.
"""

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

BACKED = "BACKED"
SHALLOW = "SHALLOW"
REMINED = "REMINED"
GONE = "GONE"
UNREACHABLE = "UNREACHABLE"

ACTIONABLE_STATES = (REMINED, GONE, SHALLOW)
STATE_PRECEDENCE = {
    BACKED: 0,
    UNREACHABLE: 1,
    SHALLOW: 2,
    REMINED: 3,
    GONE: 4,
}

Transport = Callable[[str, str, str, Optional[Sequence[Any]]], Any]


class NotFound(Exception):
    """A REST endpoint gave the usable, negative answer HTTP 404."""


class UnusableAnswer(Exception):
    """A required input was absent or had a shape that cannot be trusted."""


@dataclass(frozen=True)
class Maturity:
    """An explicit adapter/rail policy and the source which supplied it."""

    kind: str
    threshold: Optional[int]
    source: str


@dataclass(frozen=True)
class TransactionObservation:
    tx_id: str
    state: str
    reason: str
    block_position: Optional[int] = None
    block_hash: Optional[str] = None


@dataclass(frozen=True)
class SaleObservation:
    sale: str
    state: str
    reason: str
    endpoint: str
    transactions: Tuple[TransactionObservation, ...]


def depth_maturity(threshold, source):
    """Build an explicit depth policy.  There is intentionally no default."""
    return Maturity("depth", threshold, source)


def finalized_maturity(source):
    """Build an explicit finalized-block/commitment policy."""
    return Maturity("finalized", None, source)


def _field(record, name, default=None):
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _maturity(maturity, allowed):
    if not isinstance(maturity, Maturity):
        raise UnusableAnswer(
            "no maturity policy was supplied; refusing to invent a threshold"
        )
    if maturity.kind not in allowed:
        raise UnusableAnswer(
            "maturity policy {!r} is not usable for this rail".format(maturity.kind)
        )
    if not isinstance(maturity.source, str) or not maturity.source.strip():
        raise UnusableAnswer("maturity policy did not name where it came from")
    if maturity.kind == "depth":
        if (
            isinstance(maturity.threshold, bool)
            or not isinstance(maturity.threshold, int)
            or maturity.threshold < 1
        ):
            raise UnusableAnswer(
                "maturity threshold {!r} from {} is unusable; refusing to "
                "default it".format(maturity.threshold, maturity.source)
            )
    return maturity


def _rpc(transport, endpoint, method, params):
    answer = transport(endpoint, "rpc", method, params)
    if not isinstance(answer, dict):
        raise UnusableAnswer("node returned an unusable JSON-RPC response")
    if "error" in answer:
        error = answer["error"]
        if isinstance(error, dict):
            detail = "{}: {}".format(error.get("code"), error.get("message"))
        else:
            detail = str(error)
        raise UnusableAnswer("JSON-RPC error {}".format(detail))
    if "result" not in answer:
        raise UnusableAnswer("JSON-RPC response did not contain a result")
    if answer["result"] is None:
        raise UnusableAnswer(
            "node returned null; it may have pruned or declined the query"
        )
    return answer["result"]


def _quantity(value, label):
    if not isinstance(value, str) or not value.startswith("0x"):
        raise UnusableAnswer("{} was not a JSON-RPC quantity".format(label))
    try:
        number = int(value, 16)
    except ValueError:
        raise UnusableAnswer("{} was not a JSON-RPC quantity".format(label))
    if number < 0:
        raise UnusableAnswer("{} was negative".format(label))
    return number


def _plain_integer(value, label):
    if isinstance(value, bool):
        raise UnusableAnswer("{} was not an integer".format(label))
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        try:
            number = int(value.strip())
        except ValueError:
            raise UnusableAnswer("{} was not an integer".format(label))
    else:
        raise UnusableAnswer("{} was not an integer".format(label))
    if number < 0:
        raise UnusableAnswer("{} was negative".format(label))
    return number


def _block_hash(value, label):
    if not isinstance(value, str) or not value.strip():
        raise UnusableAnswer("{} was missing or not text".format(label))
    return value.strip()


def _evm_canonical_hash(transport, endpoint, block_number):
    block = _rpc(
        transport,
        endpoint,
        "eth_getBlockByNumber",
        [block_number, False],
    )
    if not isinstance(block, dict):
        raise UnusableAnswer("canonical EVM block was not an object")
    return _block_hash(block.get("hash"), "canonical EVM block hash")


def _evm_receipt_transaction(receipt, tx_id):
    transaction_hash = receipt.get("transactionHash")
    if not isinstance(transaction_hash, str) or transaction_hash.lower() != tx_id.lower():
        raise UnusableAnswer(
            "receipt transactionHash {!r} did not match requested transaction {}"
            .format(transaction_hash, tx_id)
        )


def _evm_tip(transport, endpoint):
    return _quantity(
        _rpc(transport, endpoint, "eth_blockNumber", []), "EVM tip"
    )


def _evm_finalized(transport, endpoint):
    try:
        block = _rpc(
            transport,
            endpoint,
            "eth_getBlockByNumber",
            ["finalized", False],
        )
        if not isinstance(block, dict):
            raise UnusableAnswer("finalized EVM block was not an object")
        return _quantity(block.get("number"), "EVM finalized block number")
    except Exception as failure:
        raise UnusableAnswer(
            "could not read the EVM finalized tag (the node may not support "
            "it): {}".format(failure)
        )


def _evm_state(endpoint, tx_id, maturity, transport):
    policy = _maturity(maturity, ("depth", "finalized"))
    receipt = _rpc(
        transport, endpoint, "eth_getTransactionReceipt", [tx_id]
    )
    if not isinstance(receipt, dict):
        raise UnusableAnswer("node returned an unusable transaction receipt")
    _evm_receipt_transaction(receipt, tx_id)
    if "status" not in receipt:
        raise UnusableAnswer("transaction receipt omitted execution status")
    if receipt.get("status") != "0x1":
        return TransactionObservation(
            tx_id,
            GONE,
            "receipt status {!r} is not successful".format(receipt.get("status")),
        )

    block_number = receipt.get("blockNumber")
    block_height = _quantity(block_number, "receipt block number")
    receipt_hash = _block_hash(receipt.get("blockHash"), "receipt block hash")
    canonical_hash = _evm_canonical_hash(
        transport, endpoint, block_number
    )
    if receipt_hash.lower() != canonical_hash.lower():
        return TransactionObservation(
            tx_id,
            REMINED,
            "observed receipt block hash {} is not canonical hash {} at height {}; "
            "the mismatch does not by itself prove historical re-mining".format(
                receipt_hash, canonical_hash, block_height
            ),
            block_height,
            receipt_hash,
        )

    tip = _evm_tip(transport, endpoint)
    if tip < block_height:
        raise UnusableAnswer(
            "EVM tip {} is below transaction height {}".format(tip, block_height)
        )
    depth = tip - block_height + 1
    finalized = _evm_finalized(transport, endpoint)
    if finalized > tip:
        raise UnusableAnswer(
            "EVM finalized height {} is above tip {}".format(finalized, tip)
        )
    finality = (
        "at or below finalized height {}".format(finalized)
        if block_height <= finalized
        else "above finalized height {}".format(finalized)
    )

    if policy.kind == "finalized":
        mature = block_height <= finalized
        gate = "finalized-block policy from {}".format(policy.source)
    else:
        mature = depth >= policy.threshold
        gate = "threshold {} from {}".format(policy.threshold, policy.source)
    state = BACKED if mature else SHALLOW
    return TransactionObservation(
        tx_id,
        state,
        "canonical successful receipt at height {}; live depth {}; {}; {}"
        .format(block_height, depth, finality, gate),
        block_height,
        receipt_hash,
    )


def _bitcoin_state(endpoint, tx_id, maturity, transport):
    policy = _maturity(maturity, ("depth",))
    try:
        status = transport(
            endpoint, "json", "/tx/{}/status".format(tx_id), None
        )
    except NotFound:
        return TransactionObservation(
            tx_id, GONE, "Esplora answered 404: transaction not found"
        )
    if not isinstance(status, dict):
        raise UnusableAnswer("Esplora transaction status was not an object")
    if "confirmed" not in status:
        raise UnusableAnswer("Esplora status omitted confirmed")
    if not isinstance(status.get("confirmed"), bool):
        raise UnusableAnswer("Esplora confirmed field was not boolean")
    if not status["confirmed"]:
        return TransactionObservation(
            tx_id, GONE, "Esplora knows the transaction but it is unconfirmed"
        )

    height = _plain_integer(status.get("block_height"), "Bitcoin block height")
    observed_hash = _block_hash(
        status.get("block_hash"), "Bitcoin transaction block hash"
    )
    canonical_hash = _block_hash(
        transport(endpoint, "text", "/block-height/{}".format(height), None),
        "canonical Bitcoin block hash",
    )
    if observed_hash.lower() != canonical_hash.lower():
        return TransactionObservation(
            tx_id,
            REMINED,
            "observed transaction block hash {} is not canonical hash {} at "
            "height {}; the mismatch does not by itself prove historical "
            "re-mining".format(observed_hash, canonical_hash, height),
            height,
            observed_hash,
        )

    tip = _plain_integer(
        transport(endpoint, "text", "/blocks/tip/height", None),
        "Bitcoin tip",
    )
    if tip < height:
        raise UnusableAnswer(
            "Bitcoin tip {} is below transaction height {}".format(tip, height)
        )
    depth = tip - height + 1
    state = BACKED if depth >= policy.threshold else SHALLOW
    return TransactionObservation(
        tx_id,
        state,
        "canonical successful transaction at height {}; live depth {}; "
        "threshold {} from {}".format(
            height, depth, policy.threshold, policy.source
        ),
        height,
        observed_hash,
    )


_SOLANA_TRANSACTION_METHOD = "getTransaction"
_SOLANA_TRANSACTION_COMMITMENT = "confirmed"


def _solana_state(endpoint, tx_id, maturity, transport):
    policy = _maturity(maturity, ("finalized",))
    transaction_options = {
        "encoding": "json",
        "maxSupportedTransactionVersion": 0,
    }
    if _SOLANA_TRANSACTION_COMMITMENT is not None:
        transaction_options["commitment"] = _SOLANA_TRANSACTION_COMMITMENT
    transaction = _rpc(
        transport,
        endpoint,
        _SOLANA_TRANSACTION_METHOD,
        [tx_id, transaction_options],
    )
    if not isinstance(transaction, dict):
        raise UnusableAnswer("node returned an unusable Solana transaction")
    slot = _plain_integer(transaction.get("slot"), "Solana transaction slot")
    meta = transaction.get("meta")
    if not isinstance(meta, dict) or "err" not in meta:
        raise UnusableAnswer("Solana transaction omitted execution status")
    if meta["err"] is not None:
        return TransactionObservation(
            tx_id,
            GONE,
            "Solana transaction execution failed: {!r}".format(meta["err"]),
            slot,
        )

    block = _rpc(
        transport,
        endpoint,
        "getBlock",
        [
            slot,
            {
                "transactionDetails": "none",
                "rewards": False,
                "maxSupportedTransactionVersion": 0,
            },
        ],
    )
    if not isinstance(block, dict):
        raise UnusableAnswer("canonical Solana block was not an object")
    canonical_hash = _block_hash(
        block.get("blockhash"), "canonical Solana block hash"
    )

    statuses = _rpc(
        transport,
        endpoint,
        "getSignatureStatuses",
        [[tx_id], {"searchTransactionHistory": True}],
    )
    if not isinstance(statuses, dict) or not isinstance(statuses.get("value"), list):
        raise UnusableAnswer("Solana signature-status result was unusable")
    if len(statuses["value"]) != 1 or not isinstance(statuses["value"][0], dict):
        raise UnusableAnswer("Solana signature status was absent or unusable")
    status = statuses["value"][0].get("confirmationStatus")
    if status not in ("processed", "confirmed", "finalized"):
        raise UnusableAnswer("Solana confirmationStatus was absent or unusable")

    state = BACKED if status == "finalized" else SHALLOW
    return TransactionObservation(
        tx_id,
        state,
        "successful transaction in canonical slot {} block {}; "
        "confirmationStatus={!r}; finalized policy from {}".format(
            slot, canonical_hash, status, policy.source
        ),
        slot,
        canonical_hash,
    )


_READERS = {
    "bitcoin": _bitcoin_state,
    "evm-native": _evm_state,
    "evm-erc20": _evm_state,
    "solana": _solana_state,
}


def classify_transaction(family, endpoint, tx_id, maturity, transport):
    """Classify one transaction from current endpoint answers only."""
    reader = _READERS.get(family)
    if reader is None:
        return TransactionObservation(
            tx_id,
            UNREACHABLE,
            "rail family {!r} has no block-identity reader".format(family),
        )
    if not isinstance(endpoint, str) or not endpoint.strip():
        return TransactionObservation(
            tx_id, UNREACHABLE, "rail has no endpoint"
        )
    try:
        return reader(endpoint, tx_id, maturity, transport)
    except Exception as failure:
        return TransactionObservation(
            tx_id, UNREACHABLE, "could not obtain every required input: {}".format(failure)
        )


def settled_transaction_ids(sale):
    """Return every credited id, falling back to the human-facing headline.

    Early sales legitimately have no ``watch_scratch``.  An absent scratch or a
    valid object with no ``settled_tx_ids`` therefore falls back to ``tx_id``.
    Non-empty but unparsable or wrongly typed scratch is corruption: falling
    back would check only a headline while claiming to have checked the whole.
    """
    headline = _field(sale, "tx_id")
    scratch = _field(sale, "watch_scratch")
    if isinstance(scratch, str):
        if not scratch.strip():
            scratch = None
        else:
            try:
                scratch = json.loads(scratch)
            except (TypeError, ValueError) as failure:
                raise UnusableAnswer(
                    "watch_scratch is corrupt JSON: {}".format(failure)
                )
    if scratch is not None and not isinstance(scratch, dict):
        raise UnusableAnswer("watch_scratch is corrupt: expected a JSON object")
    identifiers = scratch.get("settled_tx_ids") if isinstance(scratch, dict) else None
    if identifiers is not None:
        if not isinstance(identifiers, list) or not identifiers:
            raise UnusableAnswer(
                "watch_scratch settled_tx_ids is corrupt: expected a non-empty list"
            )
        usable = [value for value in identifiers if isinstance(value, str) and value.strip()]
        if len(usable) != len(identifiers):
            raise UnusableAnswer("settled_tx_ids contained an unusable transaction id")
        return tuple(usable)
    if isinstance(headline, str) and headline.strip():
        return (headline,)
    return ()


def classify_sale(sale, rail, maturity, transport):
    """Classify all settled transactions and fold them with stated precedence."""
    sale_name = str(_field(sale, "name", "(unnamed sale)"))
    endpoint = _field(rail, "endpoint", "") or ""
    family = _field(rail, "family")
    try:
        transaction_ids = settled_transaction_ids(sale)
    except Exception as failure:
        invalid = TransactionObservation(
            "(settled_tx_ids)", UNREACHABLE, str(failure)
        )
        return SaleObservation(
            sale_name,
            UNREACHABLE,
            "tx (settled_tx_ids): {}".format(invalid.reason),
            endpoint,
            (invalid,),
        )
    if not transaction_ids:
        empty = TransactionObservation(
            "(missing)", UNREACHABLE, "sale has no transaction id to inspect"
        )
        return SaleObservation(
            sale_name,
            UNREACHABLE,
            "tx (missing): {}".format(empty.reason),
            endpoint,
            (empty,),
        )

    observations = tuple(
        classify_transaction(family, endpoint, tx_id, maturity, transport)
        for tx_id in transaction_ids
    )
    decisive = max(observations, key=lambda value: STATE_PRECEDENCE[value.state])
    if all(value.state == BACKED for value in observations):
        reason = "all {} settled transaction(s) are backed; tx {}: {}".format(
            len(observations), decisive.tx_id, decisive.reason
        )
    else:
        reason = "tx {}: {}".format(decisive.tx_id, decisive.reason)
    return SaleObservation(
        sale_name,
        decisive.state,
        reason,
        endpoint,
        observations,
    )


def unreachable_sale(sale, endpoint, reason):
    """Build an inconclusive sale without asking a transport."""
    sale_name = str(_field(sale, "name", "(unnamed sale)"))
    try:
        transaction_ids = settled_transaction_ids(sale)
    except Exception as failure:
        transaction_ids = ("(settled_tx_ids)",)
        reason = "{}; {}".format(reason, failure)
    if not transaction_ids:
        transaction_ids = ("(missing)",)
    transactions = tuple(
        TransactionObservation(tx_id, UNREACHABLE, reason) for tx_id in transaction_ids
    )
    return SaleObservation(
        sale_name,
        UNREACHABLE,
        "tx {}: {}".format(transactions[0].tx_id, reason),
        endpoint or "",
        transactions,
    )


def actionable_count(observations):
    """Count under-backed sales; UNREACHABLE explicitly contributes zero."""
    return sum(observation.state in ACTIONABLE_STATES for observation in observations)


def result_code(observations):
    """Return 1 actionable, 2 inconclusive, or 0 only when fully answered."""
    if actionable_count(observations):
        return 1
    if any(observation.state == UNREACHABLE for observation in observations):
        return 2
    return 0
