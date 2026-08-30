"""Offline regression and mutation gate for the block-identity reorg probe.

The live probe spent days outside ``make check`` because it needed Frappe and a
network.  This gate supplies recorded transport answers.  A meta-path guard
refuses any Frappe import and an audit guard refuses every socket operation
before the core and import-safe live shell are imported.

Pinned regressions:

  1. Solana uses Solana methods, never an EVM transaction method (D38 #1).
  2. Null, JSON-RPC error, and transport error are UNREACHABLE, never GONE, and
     contribute nothing to exit status (D38 #2).
  3. A successful EVM receipt in a noncanonical block is REMINED.
  4. Every settled_tx_ids member is checked and can under-back the sale.
  5. Missing watch_scratch falls back to the headline transaction.
  6. An unreadable tip and an absent maturity policy both refuse; neither gets
     a default.

MUTATION PROOF.  ``H_REORG_MUTATION`` accepts the six original modes plus
unknown_is_green, solana_default_commitment, parallel_policy, wrong_population,
gone_outranked, corrupt_scratch_fallback, and wrong_receipt.  Each reinstalls
one old behavior in memory for one section only.  The normal run sets none;
every named mode must make this process print FAIL and exit non-zero.
"""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _NoFrappe:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "frappe" or fullname.startswith("frappe."):
            raise AssertionError("offline reorg gate refused a Frappe import")
        return None


def _no_socket(event, arguments):
    if event.startswith("socket."):
        raise AssertionError("offline reorg gate refused {}".format(event))


sys.meta_path.insert(0, _NoFrappe())
sys.addaudithook(_no_socket)

from tools import reorg_probe as live
from tools import reorg_probe_core as core

CHECKS = []
MUTATION = os.environ.get("H_REORG_MUTATION", "")
KNOWN_MUTATIONS = {
    "",
    "evm_method",
    "null_is_gone",
    "no_canonicity",
    "headline_only",
    "scratch_required",
    "default_tip",
    "unknown_is_green",
    "solana_default_commitment",
    "parallel_policy",
    "wrong_population",
    "gone_outranked",
    "corrupt_scratch_fallback",
    "wrong_receipt",
    "no_retry",
}

EVM_ENDPOINT = "recorded://sepolia"
BTC_ENDPOINT = "recorded://testnet4"
SOL_ENDPOINT = "recorded://devnet"
EVM_MATURITY = core.depth_maturity(3, "recorded Sepolia adapter gate")
BTC_MATURITY = core.depth_maturity(1, "recorded testnet4 adapter gate")
SOL_MATURITY = core.finalized_maturity("recorded Solana adapter gate")


def check(label, got, want, why=""):
    ok = got == want
    CHECKS.append(ok)
    print("  {}  {}".format("PASS" if ok else "FAIL", label))
    if why:
        print("        {}".format(why))
    if not ok:
        print("        got      {!r}".format(got))
        print("        expected {!r}".format(want))


@contextlib.contextmanager
def patched(obj, name, value):
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, original)


def _freeze(value):
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


class RecordedTransport:
    """Strict answer tape: an unrecorded read is a failing fixture."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, endpoint, kind, target, params):
        key = (endpoint, kind, target, _freeze(params))
        self.calls.append((endpoint, kind, target, copy.deepcopy(params)))
        if key not in self.answers:
            raise AssertionError(
                "unrecorded transport read: {} {} {} {!r}".format(
                    endpoint, kind, target, params
                )
            )
        answer = self.answers[key]
        if isinstance(answer, BaseException):
            raise answer
        return copy.deepcopy(answer)


def answer(endpoint, kind, target, params, value):
    return ((endpoint, kind, target, _freeze(params)), value)


def evm_answers(tx_id, receipt_hash="0xaaa", canonical_hash="0xaaa", tip="0x66"):
    return dict(
        [
            answer(
                EVM_ENDPOINT,
                "rpc",
                "eth_getTransactionReceipt",
                [tx_id],
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "transactionHash": tx_id,
                        "status": "0x1",
                        "blockNumber": "0x64",
                        "blockHash": receipt_hash,
                    },
                },
            ),
            answer(
                EVM_ENDPOINT,
                "rpc",
                "eth_getBlockByNumber",
                ["0x64", False],
                {"jsonrpc": "2.0", "id": 1, "result": {"hash": canonical_hash}},
            ),
            answer(
                EVM_ENDPOINT,
                "rpc",
                "eth_blockNumber",
                [],
                {"jsonrpc": "2.0", "id": 1, "result": tip},
            ),
            answer(
                EVM_ENDPOINT,
                "rpc",
                "eth_getBlockByNumber",
                ["finalized", False],
                {"jsonrpc": "2.0", "id": 1, "result": {"number": "0x63"}},
            ),
        ]
    )


def btc_backed_answers(tx_id, height=200, tip=200, block_hash="btc-block"):
    return dict(
        [
            answer(
                BTC_ENDPOINT,
                "json",
                "/tx/{}/status".format(tx_id),
                None,
                {
                    "confirmed": True,
                    "block_height": height,
                    "block_hash": block_hash,
                },
            ),
            answer(
                BTC_ENDPOINT,
                "text",
                "/block-height/{}".format(height),
                None,
                block_hash,
            ),
            answer(
                BTC_ENDPOINT,
                "text",
                "/blocks/tip/height",
                None,
                str(tip),
            ),
        ]
    )


if MUTATION not in KNOWN_MUTATIONS:
    raise SystemExit("unknown H_REORG_MUTATION {!r}".format(MUTATION))
if MUTATION:
    print("MUTATION: replaying old {} behavior in memory\n".format(MUTATION))


# ===========================================================================
print("1. SOLANA DISPATCH: signatures use Solana methods")
sol_tx = "recorded-solana-signature"
sol_answers = dict(
    [
        answer(
            SOL_ENDPOINT,
            "rpc",
            "getTransaction",
            [
                sol_tx,
                {
                    "encoding": "json",
                    "maxSupportedTransactionVersion": 0,
                    "commitment": "confirmed",
                },
            ],
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"slot": 487871523, "meta": {"err": None}},
            },
        ),
        # The reproduced devnet behavior: omitting commitment defaults to
        # finalized, so a merely confirmed transaction is not returned.
        answer(
            SOL_ENDPOINT,
            "rpc",
            "getTransaction",
            [sol_tx, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
            {"jsonrpc": "2.0", "id": 1, "result": None},
        ),
        answer(
            SOL_ENDPOINT,
            "rpc",
            "getBlock",
            [
                487871523,
                {
                    "transactionDetails": "none",
                    "rewards": False,
                    "maxSupportedTransactionVersion": 0,
                },
            ],
            {"jsonrpc": "2.0", "id": 1, "result": {"blockhash": "sol-block"}},
        ),
        answer(
            SOL_ENDPOINT,
            "rpc",
            "getSignatureStatuses",
            [[sol_tx], {"searchTransactionHistory": True}],
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "value": [
                        {"err": None, "confirmationStatus": "confirmed"}
                    ]
                },
            },
        ),
    ]
)
sol_transport = RecordedTransport(sol_answers)
with contextlib.ExitStack() as solana_mutations:
    if MUTATION == "evm_method":
        solana_mutations.enter_context(
            patched(core, "_SOLANA_TRANSACTION_METHOD", "eth_getTransactionReceipt")
        )
    if MUTATION == "solana_default_commitment":
        solana_mutations.enter_context(
            patched(core, "_SOLANA_TRANSACTION_COMMITMENT", None)
        )
    sol_observation = core.classify_transaction(
        "solana", SOL_ENDPOINT, sol_tx, SOL_MATURITY, sol_transport
    )
sol_methods = [call[2] for call in sol_transport.calls]
check(
    "a confirmed Solana signature is SHALLOW and no EVM method was asked",
    (sol_observation.state, sol_methods, any(method.startswith("eth_") for method in sol_methods)),
    (core.SHALLOW, ["getTransaction", "getBlock", "getSignatureStatuses"], False),
    "The answers are recorded; no hostname, socket, or Frappe site exists here.",
)
check(
    "getTransaction explicitly requests confirmed commitment before status decides finality",
    sol_transport.calls[0][3],
    [
        sol_tx,
        {
            "encoding": "json",
            "maxSupportedTransactionVersion": 0,
            "commitment": "confirmed",
        },
    ],
    "Without commitment=confirmed, devnet returned null for the reproduced confirmed slot.",
)


# ===========================================================================
print("\n2. THREE UNANSWERED SHAPES: never GONE, always inconclusive")
unanswered = []
unanswered_calls = []
for name, result in (
    ("null", {"jsonrpc": "2.0", "id": 1, "result": None}),
    (
        "rpc-error",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32000, "message": "recorded refusal"},
        },
    ),
    ("transport-error", OSError("recorded transport failure")),
):
    tx_id = "unanswered-{}".format(name)
    tape = RecordedTransport(
        dict(
            [
                answer(
                    EVM_ENDPOINT,
                    "rpc",
                    "eth_getTransactionReceipt",
                    [tx_id],
                    result,
                )
            ]
        )
    )
    observation = core.classify_transaction(
        "evm-native", EVM_ENDPOINT, tx_id, EVM_MATURITY, tape
    )
    unanswered.append(observation)
    unanswered_calls.extend(tape.calls)

real_classify = core.classify_transaction


def null_is_gone(family, endpoint, tx_id, maturity, transport):
    observation = real_classify(family, endpoint, tx_id, maturity, transport)
    if observation.state == core.UNREACHABLE:
        return replace(observation, state=core.GONE)
    return observation


if MUTATION == "null_is_gone":
    # Replay the D38 collapse only in this section over fresh answer tapes.
    unanswered = []
    with patched(core, "classify_transaction", null_is_gone):
        for name, result in (
            ("null", {"jsonrpc": "2.0", "id": 1, "result": None}),
            (
                "rpc-error",
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32000, "message": "recorded refusal"},
                },
            ),
            ("transport-error", OSError("recorded transport failure")),
        ):
            tx_id = "mutated-{}".format(name)
            tape = RecordedTransport(
                dict(
                    [
                        answer(
                            EVM_ENDPOINT,
                            "rpc",
                            "eth_getTransactionReceipt",
                            [tx_id],
                            result,
                        )
                    ]
                )
            )
            unanswered.append(
                core.classify_transaction(
                    "evm-native", EVM_ENDPOINT, tx_id, EVM_MATURITY, tape
                )
            )

unanswered_sales = [
    core.SaleObservation(
        "sale-{}".format(index),
        observation.state,
        "tx {}: {}".format(observation.tx_id, observation.reason),
        EVM_ENDPOINT,
        (observation,),
    )
    for index, observation in enumerate(unanswered)
]
check(
    "null, RPC error, and transport error are all UNREACHABLE",
    [observation.state for observation in unanswered],
    [core.UNREACHABLE, core.UNREACHABLE, core.UNREACHABLE],
)
if MUTATION == "unknown_is_green":
    result_context = patched(
        core,
        "result_code",
        lambda observations: 1 if core.actionable_count(observations) else 0,
    )
else:
    result_context = contextlib.nullcontext()
with result_context:
    unanswered_output = io.StringIO()
    unanswered_result = (
        core.actionable_count(unanswered_sales),
        live._report_outcome(unanswered_sales, unanswered_output),
        "INCONCLUSIVE" in unanswered_output.getvalue(),
        "PASS:" in unanswered_output.getvalue(),
    )
check(
    "UNREACHABLE is absent from actionable count but makes the run inconclusive",
    unanswered_result,
    (0, 2, True, False),
    "Nobody answered is not GONE, and an unproven universal is not green.",
)


# ===========================================================================
print("\n3. EVM BLOCK IDENTITY: successful receipt can still be REMINED")
remined_tx = "0xrecorded-remined"
remined_transport = RecordedTransport(
    evm_answers(remined_tx, receipt_hash="0xorphan", canonical_hash="0xcanonical")
)
if MUTATION == "no_canonicity":
    canonical_context = patched(
        core,
        "_evm_canonical_hash",
        lambda transport, endpoint, block_number: "0xorphan",
    )
else:
    canonical_context = contextlib.nullcontext()
with canonical_context:
    remined_observation = core.classify_transaction(
        "evm-native", EVM_ENDPOINT, remined_tx, EVM_MATURITY, remined_transport
    )
check(
    "status 0x1 with a noncanonical containing block is REMINED",
    remined_observation.state,
    core.REMINED,
)
check(
    "the reason carries both competing identities and the height",
    all(
        value in remined_observation.reason
        for value in ("0xorphan", "0xcanonical", "100")
    ),
    True,
)

wrong_receipt_tx = "0xrequested-receipt"
wrong_receipt_answers = evm_answers(wrong_receipt_tx)
wrong_receipt_answers[
    (
        EVM_ENDPOINT,
        "rpc",
        "eth_getTransactionReceipt",
        _freeze([wrong_receipt_tx]),
    )
]["result"]["transactionHash"] = "0xdifferent-transaction"
wrong_receipt_transport = RecordedTransport(wrong_receipt_answers)
if MUTATION == "wrong_receipt":
    receipt_context = patched(
        core, "_evm_receipt_transaction", lambda receipt, tx_id: None
    )
else:
    receipt_context = contextlib.nullcontext()
with receipt_context:
    wrong_receipt_observation = core.classify_transaction(
        "evm-native",
        EVM_ENDPOINT,
        wrong_receipt_tx,
        EVM_MATURITY,
        wrong_receipt_transport,
    )
check(
    "an EVM receipt must identify the transaction that was requested",
    (
        wrong_receipt_observation.state,
        "did not match requested transaction" in wrong_receipt_observation.reason,
    ),
    (core.UNREACHABLE, True),
)


# ===========================================================================
print("\n4. SETTLEMENT SET: the second credited transaction counts")
first_tx = "btc-first"
second_tx = "btc-second"
split_answers = btc_backed_answers(first_tx)
split_answers.update(
    dict(
        [
            answer(
                BTC_ENDPOINT,
                "json",
                "/tx/{}/status".format(second_tx),
                None,
                core.NotFound("recorded 404"),
            )
        ]
    )
)
split_sale = {
    "name": "sale-split",
    "tx_id": first_tx,
    "watch_scratch": '{"settled_tx_ids": ["btc-first", "btc-second"]}',
}
btc_rail = {"family": "bitcoin", "endpoint": BTC_ENDPOINT}
split_transport = RecordedTransport(split_answers)
if MUTATION == "headline_only":
    ids_context = patched(
        core,
        "settled_transaction_ids",
        lambda sale: (sale["tx_id"],),
    )
else:
    ids_context = contextlib.nullcontext()
with ids_context:
    split_observation = core.classify_sale(
        split_sale, btc_rail, BTC_MATURITY, split_transport
    )
check(
    "a gone second settled_tx_id makes the sale GONE",
    (split_observation.state, "btc-second" in split_observation.reason),
    (core.GONE, True),
    "A sale is BACKED only when every credited transaction is BACKED.",
)

mismatch_tx = "btc-noncanonical"
missing_tx = "btc-definitively-missing"
precedence_answers = dict(
    [
        answer(
            BTC_ENDPOINT,
            "json",
            "/tx/{}/status".format(mismatch_tx),
            None,
            {"confirmed": True, "block_height": 201, "block_hash": "old-block"},
        ),
        answer(
            BTC_ENDPOINT,
            "text",
            "/block-height/201",
            None,
            "canonical-block",
        ),
        answer(
            BTC_ENDPOINT,
            "json",
            "/tx/{}/status".format(missing_tx),
            None,
            core.NotFound("recorded 404"),
        ),
    ]
)
precedence_sale = {
    "name": "sale-precedence",
    "tx_id": mismatch_tx,
    "watch_scratch": {
        "settled_tx_ids": [mismatch_tx, missing_tx]
    },
}
precedence_transport = RecordedTransport(precedence_answers)
if MUTATION == "gone_outranked":
    precedence_context = patched(
        core,
        "STATE_PRECEDENCE",
        {
            core.BACKED: 0,
            core.UNREACHABLE: 1,
            core.SHALLOW: 2,
            core.GONE: 3,
            core.REMINED: 4,
        },
    )
else:
    precedence_context = contextlib.nullcontext()
with precedence_context:
    precedence_observation = core.classify_sale(
        precedence_sale, btc_rail, BTC_MATURITY, precedence_transport
    )
check(
    "GONE outranks a block mismatch and every transaction retains its own state",
    (
        precedence_observation.state,
        missing_tx in precedence_observation.reason,
        tuple(tx.state for tx in precedence_observation.transactions),
        live._transaction_summary(precedence_observation),
    ),
    (
        core.GONE,
        True,
        (core.REMINED, core.GONE),
        "REMINED tx btc-noncanonical, GONE tx btc-definitively-missing",
    ),
)
check(
    "block mismatch wording does not claim historical re-mining as truth",
    (
        "does not by itself prove" in precedence_observation.transactions[0].reason,
        "re-mined" not in precedence_observation.transactions[0].reason.lower(),
    ),
    (True, True),
)


# ===========================================================================
print("\n5. EARLY SALES: absent watch_scratch falls back to headline")
legacy_tx = "btc-legacy-headline"
legacy_sale = {"name": "sale-legacy", "tx_id": legacy_tx}
legacy_transport = RecordedTransport(btc_backed_answers(legacy_tx))


def scratch_required(sale):
    if not sale.get("watch_scratch"):
        raise ValueError("old behavior required watch_scratch")
    return core.settled_transaction_ids(sale)


try:
    if MUTATION == "scratch_required":
        scratch_context = patched(core, "settled_transaction_ids", scratch_required)
    else:
        scratch_context = contextlib.nullcontext()
    with scratch_context:
        legacy_observation = core.classify_sale(
            legacy_sale, btc_rail, BTC_MATURITY, legacy_transport
        )
    legacy_result = (legacy_observation.state, legacy_observation.transactions[0].tx_id)
except Exception as failure:
    legacy_result = ("ERROR", str(failure))
check(
    "a sale with no watch_scratch checks its headline without error",
    legacy_result,
    (core.BACKED, legacy_tx),
    "The 16 early Bitcoin sales without settled_tx_ids are a supported shape.",
)

corrupt_tx = "btc-corrupt-scratch-headline"
corrupt_sale = {
    "name": "sale-corrupt-scratch",
    "tx_id": corrupt_tx,
    "watch_scratch": "{not-json",
}
corrupt_transport = RecordedTransport(btc_backed_answers(corrupt_tx))
real_settled_ids = core.settled_transaction_ids


def corrupt_scratch_fallback(sale):
    try:
        return real_settled_ids(sale)
    except core.UnusableAnswer:
        return (sale["tx_id"],)


if MUTATION == "corrupt_scratch_fallback":
    corrupt_context = patched(
        core, "settled_transaction_ids", corrupt_scratch_fallback
    )
else:
    corrupt_context = contextlib.nullcontext()
with corrupt_context:
    corrupt_observation = core.classify_sale(
        corrupt_sale, btc_rail, BTC_MATURITY, corrupt_transport
    )
check(
    "non-empty corrupt watch_scratch is UNREACHABLE and never falls back",
    (
        corrupt_observation.state,
        "watch_scratch is corrupt" in corrupt_observation.reason,
        len(corrupt_transport.calls),
    ),
    (core.UNREACHABLE, True, 0),
    "Only absent scratch earns the legacy headline fallback.",
)


# ===========================================================================
print("\n6. NO DEFAULTS: unreadable tip and missing maturity both refuse")
tip_tx = "0xrecorded-tip-refusal"
tip_answers = evm_answers(tip_tx)
tip_answers[
    (EVM_ENDPOINT, "rpc", "eth_blockNumber", _freeze([]))
] = OSError("recorded tip unavailable")
tip_transport = RecordedTransport(tip_answers)
if MUTATION == "default_tip":
    tip_context = patched(core, "_evm_tip", lambda transport, endpoint: 102)
else:
    tip_context = contextlib.nullcontext()
with tip_context:
    tip_observation = core.classify_transaction(
        "evm-native", EVM_ENDPOINT, tip_tx, EVM_MATURITY, tip_transport
    )

missing_tx = "0xrecorded-missing-threshold"
missing_transport = RecordedTransport(evm_answers(missing_tx))
missing_observation = core.classify_transaction(
    "evm-native", EVM_ENDPOINT, missing_tx, None, missing_transport
)
check(
    "an unreadable live tip is UNREACHABLE, not derived from a constant",
    tip_observation.state,
    core.UNREACHABLE,
)
check(
    "a missing maturity threshold refuses before any chain read",
    (
        missing_observation.state,
        len(missing_transport.calls),
        "threshold" in missing_observation.reason
        and "invent" in missing_observation.reason,
    ),
    (core.UNREACHABLE, 0, True),
    "The reason explicitly says it refused to invent a threshold.",
)


# ===========================================================================
print("\n7. JOURNAL: observation evidence never becomes chain truth")
old_tx = core.TransactionObservation("journal-tx", core.BACKED, "recorded", 10, "0xaaa")
old_sale = core.SaleObservation(
    "journal-sale", core.BACKED, "tx journal-tx", EVM_ENDPOINT, (old_tx,)
)
new_tx = replace(old_tx, block_position=11, block_hash="0xbbb")
new_sale = replace(old_sale, transactions=(new_tx,))
journal_output = io.StringIO()
with tempfile.TemporaryDirectory(prefix="h-reorg-") as temporary:
    journal_path = Path(temporary) / "observations.jsonl"
    live._record_journal(journal_path, [old_sale], journal_output)
    live._record_journal(journal_path, [new_sale], journal_output)
    journal_rows = [
        json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()
    ]
check(
    "journal appends one complete JSON observation per transaction",
    (
        len(journal_rows),
        set(journal_rows[-1]),
    ),
    (
        2,
        {
            "timestamp",
            "endpoint",
            "sale",
            "tx_id",
            "block_height_or_slot",
            "block_hash",
            "state",
        },
    ),
)
check(
    "a changed journal hash is labelled observation-vs-observation only",
    (
        "RE-MINED (journal evidence)" in journal_output.getvalue(),
        "observation-vs-observation" in journal_output.getvalue(),
        new_sale.state,
        core.result_code([new_sale]),
    ),
    (True, True, core.BACKED, 0),
    "The live BACKED state and exit status are unchanged by historical evidence.",
)


# ===========================================================================
print("\n8. LIVE POLICY: the Crypto Rail DocType owns mode, endpoint, and gate")


class RecordedRail:
    name = "mode-rail"
    family = "evm-native"
    testnet_url = "recorded://wrong-testnet"
    live_url = "recorded://right-mainnet"
    gate_confs = 3
    testnet_gate_confs = -2

    def __init__(self):
        self.calls = []

    def endpoint_for(self, mode):
        self.calls.append(("endpoint_for", mode))
        return self.live_url if mode == "mainnet" else self.testnet_url

    def gate_for(self, mode):
        self.calls.append(("gate_for", mode))
        return -2 if mode == "testnet" else 3


def parallel_policy(rail, sale, explicit_thresholds):
    """Old copy: always testnet, ignore negative, fall through to main gate."""
    gate = rail.testnet_gate_confs
    if not isinstance(gate, int) or gate <= 0:
        gate = rail.gate_confs
    return (
        rail.testnet_url,
        core.depth_maturity(gate, "parallel copied fields"),
        None,
    )


policy_rail = RecordedRail()
if MUTATION == "parallel_policy":
    policy_context = patched(live, "_probe_inputs", parallel_policy)
else:
    policy_context = contextlib.nullcontext()
with policy_context:
    main_endpoint, main_policy, main_reason = live._probe_inputs(
        policy_rail, {"mode": "mainnet", "rail_key": "mode-rail"}, {}
    )
    refused_endpoint, refused_policy, refused_reason = live._probe_inputs(
        policy_rail, {"mode": "testnet", "rail_key": "mode-rail"}, {}
    )
    override_endpoint, override_policy, override_reason = live._probe_inputs(
        policy_rail,
        {"mode": "testnet", "rail_key": "mode-rail"},
        {"mode-rail": 7},
    )
check(
    "mode methods choose mainnet correctly and preserve a negative testnet refusal",
    (
        main_endpoint,
        main_policy.threshold if main_policy else None,
        main_reason,
        refused_endpoint,
        refused_policy,
        "gate_for('testnet') returned -2" in (refused_reason or ""),
        policy_rail.calls[:4],
    ),
    (
        "recorded://right-mainnet",
        3,
        None,
        "recorded://wrong-testnet",
        None,
        True,
        [
            ("endpoint_for", "mainnet"),
            ("gate_for", "mainnet"),
            ("endpoint_for", "testnet"),
            ("gate_for", "testnet"),
        ],
    ),
)
check(
    "explicit maturity overrides only the DocType's unusable gate and says so",
    (
        override_endpoint,
        override_policy.threshold if override_policy else None,
        "overrode Crypto Rail gate_for('testnet')=-2"
        in (override_policy.source if override_policy else ""),
        override_reason,
    ),
    ("recorded://wrong-testnet", 7, True, None),
)


# ===========================================================================
print("\n9. POPULATION: booked invoices are the claim, tx_id is evidence")
population_rows = [
    {"name": "booked-with-tx", "sales_invoice": "INV-1", "tx_id": "tx-1"},
    {"name": "booked-no-tx", "sales_invoice": "INV-2", "tx_id": ""},
    {"name": "unbooked-with-tx", "sales_invoice": "", "tx_id": "tx-3"},
]


def wrong_population(rows):
    return (
        [dict(row) for row in rows if row.get("tx_id")],
        [],
        [],
    )


if MUTATION == "wrong_population":
    population_context = patched(live, "_partition_sales", wrong_population)
else:
    population_context = contextlib.nullcontext()
with population_context:
    booked_rows, excluded_rows, invisible_rows = live._partition_sales(population_rows)
missing_tx_result = (
    core.result_code(
        [core.unreachable_sale(invisible_rows[0], "", "booked sale has no tx_id")]
    )
    if invisible_rows
    else 0
)
check(
    "booked-no-tx stays visible and unbooked-with-tx is separately excluded",
    (
        [row["name"] for row in booked_rows],
        [row["name"] for row in excluded_rows],
        [row["name"] for row in invisible_rows],
        missing_tx_result,
    ),
    (
        ["booked-with-tx", "booked-no-tx"],
        ["unbooked-with-tx"],
        ["booked-no-tx"],
        2,
    ),
)


# ---------------------------------------------------------------------------
# 8. RETRY: a silent endpoint is asked twice, a refusal is not
#
# The measured behaviour of the public Sepolia and Amoy nodes is that a read
# answers nothing once and everything the next time; a single unlucky read put
# 7 to 12 booked sales in UNREACHABLE on runs where nothing on any chain had
# changed.  Retrying serves D38's rule rather than evading it -- the question
# is about the chain, and one silent read is not the chain's answer -- while a
# refusal (NotFound) is a real answer and must never be asked again.
# ---------------------------------------------------------------------------
print("\n8. RETRY: a silent endpoint is asked twice, a refusal is asked once")

retry_under_test = live.read_with_retry
if MUTATION == "no_retry":
    def retry_under_test(attempt, attempts=2, on_retry=None):
        return attempt()

def flaky(failures, outcome=None):
    state = {"left": failures}
    failure = outcome if outcome is not None else RuntimeError("endpoint said nothing")
    def attempt():
        if state["left"]:
            state["left"] -= 1
            raise failure
        return "answer"
    return attempt

retries = {"n": 0}
def counted():
    retries["n"] += 1

check(
    "a read that fails once and then answers returns the answer",
    retry_under_test(flaky(1), on_retry=counted),
    "answer",
    "the endpoint was silent once; the chain still had an answer",
)
check("and the retry is counted, once", retries["n"], 1)

refusals = {"n": 0}
try:
    retry_under_test(
        flaky(1, core.NotFound("esplora 404")),
        on_retry=lambda: refusals.__setitem__("n", refusals["n"] + 1),
    )
    refused = "did not raise"
except core.NotFound:
    refused = "raised"
except Exception as failure:
    refused = "raised {}".format(type(failure).__name__)
check(
    "a refusal is never retried; NotFound is a real answer",
    (refused, refusals["n"]),
    ("raised", 0),
)

persistent = {"n": 0}
try:
    retry_under_test(
        flaky(99), on_retry=lambda: persistent.__setitem__("n", persistent["n"] + 1)
    )
    outcome = "did not raise"
except RuntimeError:
    outcome = "raised"
check(
    "an endpoint that never answers still fails, after a bounded number of tries",
    (outcome, persistent["n"] <= 1),
    ("raised", True),
)


classify_under_test = live.classify_with_retry
if MUTATION == "no_retry":
    def classify_under_test(classify, is_unreachable, on_retry=None, attempts=2):
        return classify()

def answering(sequence):
    state = {"i": 0}
    def classify():
        value = sequence[min(state["i"], len(sequence) - 1)]
        state["i"] += 1
        return value
    return classify, state

unreachable_then_backed, seen = answering([core.UNREACHABLE, core.BACKED])
asked = {"n": 0}
check(
    "an UNREACHABLE sale is classified again, and the chain's answer wins",
    classify_under_test(
        unreachable_then_backed,
        lambda answer: answer == core.UNREACHABLE,
        on_retry=lambda: asked.__setitem__("n", asked["n"] + 1),
    ),
    core.BACKED,
    "a null read is not the chain saying no",
)
check("and that repeat is counted", asked["n"], 1)

for verdict in (core.GONE, core.REMINED, core.SHALLOW, core.BACKED):
    answered, state = answering([verdict, core.BACKED])
    again = {"n": 0}
    result = classify_under_test(
        answered,
        lambda answer: answer == core.UNREACHABLE,
        on_retry=lambda: again.__setitem__("n", again["n"] + 1),
    )
    check(
        "a {} verdict is never asked twice, so a repeat cannot improve it".format(verdict),
        (result, again["n"], state["i"]),
        (verdict, 0, 1),
    )

still_silent, _ = answering([core.UNREACHABLE])
bounded = {"n": 0}
check(
    "an endpoint silent twice stays UNREACHABLE, and is not asked forever",
    (
        classify_under_test(
            still_silent,
            lambda answer: answer == core.UNREACHABLE,
            on_retry=lambda: bounded.__setitem__("n", bounded["n"] + 1),
        ),
        bounded["n"] <= 1,
    ),
    (core.UNREACHABLE, True),
)


passed = sum(CHECKS)
print(
    "\nRESULT: {} ({}/{} checks)".format(
        "PASS" if passed == len(CHECKS) else "FAIL", passed, len(CHECKS)
    )
)
raise SystemExit(0 if passed == len(CHECKS) else 1)
