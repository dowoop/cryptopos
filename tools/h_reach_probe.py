"""Offline regression and mutation gate for the rail reach probe.

``tools/reach_probe.py`` decides whether a Frappe process may charge on a rail
at all, and until 2026-08-31 it was in **no gate whatsoever** -- no Makefile
target, no harness, no test.  That is the same hole that let the third
``_referenced_transfer_total`` drift for a day under a docstring saying it was
checked, and it cost the same way: the ``ootle`` family had no probe, so all
four workers reported ``xtr`` UNREACHABLE against an indexer answering in half
a second, and ``prove_end_to_end.py --rail xtr`` refused every run.

This gate supplies recorded transport answers.  A meta-path guard refuses any
Frappe import and an audit guard refuses every socket operation before the
probe is imported, so it can never accidentally measure the network.

Pinned regressions:

  1. **Every installed adapter that declares ``observation`` has a probe for
     its exact catalog key.** Builtins and ``cryptopos.rails`` entry points are
     both included. Family-level coverage is insufficient because plugins may
     reuse a family while speaking a different provider dialect.
  2. A configuration error is NOT PROBED, not UNREACHABLE.  The endpoint is
     not being accused of anything.
  3. NOT PROBED still fails.  A rail nothing can confirm is a rail nothing may
     charge on, so it counts toward the exit code exactly as UNREACHABLE does.
  4. Ootle resolves the row's catalog key and takes the expected network from
     the installed adapter, not from editable row text or a hardcoded string.
  5. Ootle's epoch must be a non-negative ``int`` and ``True`` is not one.
  6. A wrong-shaped answer is distinguished from a transport failure: the
     first means the endpoint answered, the second means nothing did.

MUTATION PROOF.  ``H_REACH_MUTATION`` reinstalls one old behavior in memory.
The normal run sets none; every named mode must make this process print FAIL
and exit non-zero.

    H_REACH_MUTATION=ootle_missing|unprobed_is_green|unprobed_says_unreachable|
                     ootle_any_network|ootle_any_epoch|ootle_hardcoded_network|
                     plugin_uncovered_family|plugin_same_family_dialect
"""

import io
import json
import os
import socket
import sys
import urllib.error
from contextlib import redirect_stdout
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "cryptopos-core" / "src"))


class _NoFrappe:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "frappe" or fullname.startswith("frappe."):
            raise AssertionError("offline reach gate refused a Frappe import")
        return None


def _no_socket(event, arguments):
    if event.startswith("socket."):
        raise AssertionError("offline reach gate refused {}".format(event))


sys.meta_path.insert(0, _NoFrappe())
sys.addaudithook(_no_socket)

from cryptopos_core import catalog
from cryptopos_core.plugin import (
    ADDRESS_VALIDATION,
    NOT_UNCONDITIONAL,
    OBSERVATION,
    PAYMENT_REQUEST,
    SETTLEMENT,
    Asset,
    Network,
)
from cryptopos_core.registry import validate_plugin
from tools import reach_probe as live

CHECKS = []
MUTATION = os.environ.get("H_REACH_MUTATION", "")
KNOWN_MUTATIONS = {
    "",
    "ootle_missing",
    "unprobed_is_green",
    "unprobed_says_unreachable",
    "ootle_any_network",
    "ootle_any_epoch",
    "ootle_hardcoded_network",
    "plugin_uncovered_family",
    "plugin_same_family_dialect",
}
if MUTATION not in KNOWN_MUTATIONS:
    raise SystemExit("unknown H_REACH_MUTATION {!r}".format(MUTATION))
if MUTATION:
    print("MUTATION: replaying old {} behavior in memory\n".format(MUTATION))


def check(label, got, want, why=""):
    ok = got == want
    CHECKS.append((label, ok, got, want, why))
    print("  {:<4} {}".format("ok" if ok else "FAIL", label))
    if not ok:
        print("        got  {!r}\n        want {!r}".format(got, want))
        if why:
            print("        {}".format(why))


# ---------------------------------------------------------------------------
# A transport that answers from memory.
# ---------------------------------------------------------------------------

class _Response:
    def __init__(self, body):
        self._body = body

    def read(self, _limit):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _Opener:
    """Answers one recorded body, or raises one recorded exception."""

    def __init__(self, body=None, error=None):
        self._body = body
        self._error = error
        self.seen = []
        self.timeouts = []

    def open(self, request, timeout=None):
        self.seen.append(request.full_url)
        self.timeouts.append(timeout)
        if self._error is not None:
            raise self._error
        return _Response(self._body)


def with_transport(opener, call, *arguments):
    previous = live._OPENER
    previous_ootle = live.ootle_chain._urlopen
    live._OPENER = opener
    live.ootle_chain._urlopen = opener.open
    try:
        return call(*arguments)
    finally:
        live._OPENER = previous
        live.ootle_chain._urlopen = previous_ootle


def refusal(opener, call, *arguments):
    """Run and return ``(kind, detail)``, or ``("", detail)`` when it passed."""
    try:
        return "", with_transport(opener, call, *arguments)
    except live.ProbeFailure as failure:
        return failure.kind, failure.detail


def body(payload):
    return _Opener(body=json.dumps(payload).encode("utf-8"))


XTR_ROW = {
    "name": "xtr",
    "family": "ootle",
    "catalog_key": "ootle:esmeralda/native:xtr",
    "testnet_url": "https://indexer.example",
}
ESMERALDA = {"network": "esmeralda", "network_byte": 38, "epoch": 10775}
XTR_KEY = XTR_ROW["catalog_key"]

INSTALLED_ADAPTERS = {
    adapter.key: adapter
    for adapter in catalog.builtin_rails()
    if {ADDRESS_VALIDATION, PAYMENT_REQUEST, OBSERVATION, SETTLEMENT} <= adapter.capabilities
}
live._installed_adapters = lambda: dict(INSTALLED_ADAPTERS)


class _SyntheticAdapter:
    """A valid entry-point rail; operations are irrelevant to coverage."""

    binding_category = NOT_UNCONDITIONAL
    capabilities = frozenset({ADDRESS_VALIDATION, PAYMENT_REQUEST, OBSERVATION, SETTLEMENT})

    def __init__(self, namespace, reference, asset_reference, dialect):
        self.network = Network(namespace, reference, True)
        self.asset = Asset("native", asset_reference, asset_reference.upper(), 8)
        self.key = f"{self.network.key}/{self.asset.key}"
        self.dialect = dialect

    def readiness(self, configuration):
        return None

    def capture_baseline(self, recipient, configuration):
        return None

    def validate_recipient(self, recipient):
        return "unchecked", "synthetic"

    def create_request(self, intent):
        return None

    def observe(self, intent, configuration, previous=None):
        return None

    def settle(self, intent, observations, claimed_transaction_ids=frozenset()):
        return None


class _SyntheticPoint:
    def __init__(self, name, adapter):
        self.name = name
        self._adapter = adapter

    def load(self):
        return self._adapter


SYNTHETIC_POINTS = []
if MUTATION == "plugin_uncovered_family":
    SYNTHETIC_POINTS.append(
        _SyntheticPoint("nova", _SyntheticAdapter("novachain", "test", "nova", "nova-rest"))
    )
if MUTATION == "plugin_same_family_dialect":
    SYNTHETIC_POINTS.append(
        _SyntheticPoint("ootle-graphql", _SyntheticAdapter("ootle", "sidecar", "plug", "graphql"))
    )


# ---------------------------------------------------------------------------
# Mutations: reinstall one old behavior.
# ---------------------------------------------------------------------------

if MUTATION == "ootle_missing":
    live._PROBES.pop(XTR_KEY, None)

if MUTATION == "unprobed_is_green":
    live._classify = lambda kind: ("NOT PROBED", 1) if kind == "configuration error" else ("UNREACHABLE", 0)
    _real_run = live.run

    def _lenient_run(rails=None, mode=None):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            failures = _real_run(rails=rails, mode=mode)
        text = buffer.getvalue()
        print(text, end="")
        return failures - text.count("NOT PROBED")

    live.run = _lenient_run

if MUTATION == "unprobed_says_unreachable":
    live._classify = lambda _kind: ("UNREACHABLE", 0)

if MUTATION in ("ootle_any_network", "ootle_any_epoch", "ootle_hardcoded_network"):
    _strict = live._ootle

    def _loose(endpoint, rail, adapter=None):
        request = live.urllib.request.Request(
            f"{endpoint.rstrip('/')}/network",
            headers={"Accept": "application/json", "User-Agent": live._AGENT},
            method="GET",
        )
        payload = live._request(
            request,
            open_request=live.ootle_chain._urlopen,
            timeout=live.ootle_chain.READ_TIMEOUT_SECONDS,
            max_bytes=live.ootle_chain.MAX_RESPONSE_BYTES,
        )
        try:
            answer = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exception:
            raise live.ProbeFailure("wrong-shaped answer", str(exception)) from None
        if not isinstance(answer, dict):
            raise live.ProbeFailure("wrong-shaped answer", "not an object")
        network = answer.get("network")
        if MUTATION == "ootle_hardcoded_network" and network != "esmeralda":
            raise live.ProbeFailure("wrong-shaped answer", "not esmeralda")
        epoch = answer.get("epoch")
        if MUTATION != "ootle_any_epoch" and not isinstance(epoch, int):
            raise live.ProbeFailure("wrong-shaped answer", "epoch not an int")
        return f"/network returned {network} at epoch {epoch}"

    live._ootle = _loose
    live._PROBES[XTR_KEY] = _loose


# ---------------------------------------------------------------------------
# 1. Coverage: concrete installed adapters, never family names.
# ---------------------------------------------------------------------------

print("coverage — every installed observable adapter key has its own probe")


def _entry_point_adapters():
    points = list(metadata.entry_points(group="cryptopos.rails")) + SYNTHETIC_POINTS
    adapters = []
    for point in points:
        candidate = point.load()
        if not hasattr(candidate, "key") and callable(candidate):
            candidate = candidate()
        adapters.append(validate_plugin(candidate))
    return adapters


observable_adapters = dict(INSTALLED_ADAPTERS)
for adapter in _entry_point_adapters():
    if OBSERVATION in adapter.capabilities:
        observable_adapters[adapter.key] = adapter

check(
    "every installed observing adapter key has an exact reach probe",
    sorted(set(observable_adapters) - set(live._PROBES)),
    [],
    "entry-point rails and same-family dialects must not inherit an unrelated probe",
)
check(
    "ootle is among the concrete adapters that must be covered",
    XTR_KEY in observable_adapters,
    True,
    "if this fails the derivation broke, not the probe table",
)

same_family_plugin = _SyntheticAdapter("ootle", "sidecar", "plug", "graphql")
check(
    "same-family plugin dialect does not count as covered by built-in Ootle",
    same_family_plugin.key in live._PROBES,
    False,
    "runtime dispatch is by catalog key, so coverage must be too",
)


# ---------------------------------------------------------------------------
# 2. Ootle answer shapes.
# ---------------------------------------------------------------------------

print("\nootle — the indexer's own network/epoch identification")

# Resolved once, with a fallback, so the `ootle_missing` mutation produces one
# targeted coverage failure instead of a KeyError that hides every later check.
OOTLE = live._PROBES.get(XTR_KEY, live._ootle)

check(
    "a real esmeralda answer is REACHABLE and names the epoch",
    refusal(body(ESMERALDA), OOTLE, "https://indexer.example", XTR_ROW),
    ("", "/network returned esmeralda at epoch 10775"),
)
check(
    "the URL asked for is the indexer's /network",
    with_transport(
        (probe_opener := body(ESMERALDA)),
        lambda: (OOTLE("https://indexer.example/", XTR_ROW), probe_opener.seen)[1],
    ),
    ["https://indexer.example/network"],
    "a trailing slash must not produce //network",
)
check(
    "another Ootle network is refused against the adapter's network",
    refusal(body({"network": "igor", "epoch": 4}), OOTLE, "https://indexer.example", XTR_ROW)[0],
    "wrong-shaped answer",
    "the installed adapter is the authority; the endpoint does not get a vote",
)
check(
    "a boolean epoch is not a non-negative integer",
    refusal(body({"network": "esmeralda", "epoch": True}), OOTLE, "https://indexer.example", XTR_ROW)[0],
    "wrong-shaped answer",
    "True == 1 in Python, so isinstance(epoch, int) alone passes it",
)
check(
    "a negative epoch is refused",
    refusal(body({"network": "esmeralda", "epoch": -1}), OOTLE, "https://indexer.example", XTR_ROW)[0],
    "wrong-shaped answer",
)
check(
    "a missing epoch is refused",
    refusal(body({"network": "esmeralda"}), OOTLE, "https://indexer.example", XTR_ROW)[0],
    "wrong-shaped answer",
)
check(
    "a non-JSON body is a wrong-shaped answer, not a transport failure",
    refusal(_Opener(body=b"<html>502</html>"), OOTLE, "https://indexer.example", XTR_ROW)[0],
    "wrong-shaped answer",
    "the endpoint answered; it answered wrongly",
)
check(
    "a JSON array is refused",
    refusal(_Opener(body=b"[1,2,3]"), OOTLE, "https://indexer.example", XTR_ROW)[0],
    "wrong-shaped answer",
)
check(
    "an uninstalled catalog key from another family is a configuration error",
    refusal(body(ESMERALDA), OOTLE, "https://indexer.example",
            dict(XTR_ROW, catalog_key="solana:devnet/native:sol"))[0],
    "configuration error",
)
check(
    "a made-up Ootle network does not resolve merely because the row names it",
    refusal(body({"network": "igor", "epoch": 4}), OOTLE, "https://indexer.example",
            dict(XTR_ROW, catalog_key="ootle:igor/native:xtr"))[0],
    "configuration error",
)
check(
    "a wrong Ootle asset does not resolve merely because network and family match",
    refusal(body(ESMERALDA), OOTLE, "https://indexer.example",
            dict(XTR_ROW, catalog_key="ootle:esmeralda/native:btc"))[0],
    "configuration error",
)
check(
    "a row whose family names another dialect is refused",
    refusal(body(ESMERALDA), OOTLE, "https://indexer.example",
            dict(XTR_ROW, family="bitcoin"))[0],
    "configuration error",
)

# ---------------------------------------------------------------------------
# 3. Transport failures stay transport failures.
# ---------------------------------------------------------------------------

print("\ntransport — nothing answered at all")

timeout_opener = body(ESMERALDA)
with_transport(timeout_opener, OOTLE, "https://indexer.example", XTR_ROW)
check(
    "Ootle uses chain.READ_TIMEOUT_SECONDS rather than the legacy probe timeout",
    timeout_opener.timeouts,
    [live.ootle_chain.READ_TIMEOUT_SECONDS],
)

legacy_opener = _Opener(error=AssertionError("Ootle used the legacy opener"))
ootle_opener = body(ESMERALDA)
previous_legacy = live._OPENER
previous_ootle = live.ootle_chain._urlopen
live._OPENER = legacy_opener
live.ootle_chain._urlopen = ootle_opener.open
try:
    OOTLE("https://indexer.example", XTR_ROW)
finally:
    live._OPENER = previous_legacy
    live.ootle_chain._urlopen = previous_ootle
check(
    "Ootle calls the reader's proxy/redirect transport seam, not the legacy opener",
    (legacy_opener.seen, ootle_opener.seen),
    ([], ["https://indexer.example/network"]),
)

check(
    "a DNS failure is classified as one",
    refusal(_Opener(error=urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))),
            OOTLE, "https://indexer.example", XTR_ROW)[0],
    "DNS failure",
)
check(
    "an HTTP status is an HTTP error",
    refusal(_Opener(error=urllib.error.HTTPError("u", 502, "Bad Gateway", {}, None)),
            OOTLE, "https://indexer.example", XTR_ROW)[0],
    "HTTP error",
)


# ---------------------------------------------------------------------------
# 4. What run() prints, and what it counts.
# ---------------------------------------------------------------------------

print("\nrun — the word the operator reads, and the exit code")


def run_text(rows, opener):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        failures = with_transport(opener, lambda: live.run(rails=rows, mode="testnet"))
    return failures, buffer.getvalue()


unknown_family = [dict(XTR_ROW, name="zec", family="zcash", catalog_key="zcash:testnet/native:zec")]
failures, text = run_text(unknown_family, body(ESMERALDA))
check(
    "a family with no probe reads NOT PROBED, not UNREACHABLE",
    ("NOT PROBED" in text, "UNREACHABLE" in text),
    (True, False),
    "the endpoint was never asked; it is not the thing that failed",
)
check("a family with no probe still fails", failures, 1,
      "refusing to charge is right; blaming the network is not")

failures, text = run_text(
    [XTR_ROW],
    _Opener(error=urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))),
)
check("a real transport failure still reads UNREACHABLE", "UNREACHABLE" in text, True)
check("a real transport failure fails", failures, 1)

failures, text = run_text([XTR_ROW], body(ESMERALDA))
check("a reachable rail passes", (failures, "REACHABLE" in text), (0, True))

# THE REGRESSION CONTROL, and the one that was missing. A check asserting
# `family == adapter.network.namespace` was shipped on 2026-08-31 and took all
# four EVM rails down in every worker at once -- `evm-native` is a TRANSPORT
# DIALECT and `ethereum` is a CHAIN, and they can never be equal. It passed
# every offline test because every row in this file was an Ootle row, where the
# two words coincide. So the fixture set must contain a rail whose family and
# namespace legitimately differ, or the same mistake is free to come back.
for _row, _label in (
    ({"name": "eth", "family": "evm-native",
      "catalog_key": "ethereum:sepolia/native:eth",
      "testnet_url": "https://rpc.example"}, "eth"),
    ({"name": "usdc-pol", "family": "evm-erc20",
      "catalog_key": "polygon:amoy/erc20:0x41e94eb019c0762f9bfcf9fb1e58725bfb0e7582",
      "testnet_url": "https://rpc.example"}, "usdc-pol"),
):
    _failures, _text = run_text(
        [_row], body({"jsonrpc": "2.0", "id": 1, "result": "0x66"}))
    check(
        f"{_label}: family and chain differ legitimately, so it must still be PROBED",
        "NOT PROBED" in _text,
        False,
        "an EVM family never equals its chain namespace; refusing on that took"
        " all four EVM rails down",
    )

failures, text = run_text([dict(XTR_ROW, testnet_url="")], body(ESMERALDA))
check("a rail with no endpoint is NOT PROBED and fails",
      ("NOT PROBED" in text, failures), (True, 1))

failures, text = run_text([dict(XTR_ROW, testnet_url="http://indexer.example")], body(ESMERALDA))
check("a non-HTTPS endpoint is NOT PROBED and fails",
      ("NOT PROBED" in text, failures), (True, 1))

mixed_rows = [
    dict(XTR_ROW, name="bad-row", catalog_key=17),
    dict(XTR_ROW, name="dns-outage"),
]
result, text = run_text(
    mixed_rows,
    _Opener(error=urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))),
)
check(
    "structured counts keep a bad row separate from a real endpoint outage",
    getattr(result, "counts", None),
    {"REACHABLE": 0, "UNREACHABLE": 1, "NOT PROBED": 1},
    "callers must inspect categories, never search the aggregate prose",
)
check(
    "the structured result retains the old integer failure count",
    (result, getattr(result, "failures", None)),
    (2, 2),
)

bad_types = [
    dict(XTR_ROW, name="bad-family", family=17),
    dict(XTR_ROW, name="bad-key", catalog_key=17),
    dict(XTR_ROW, name="bad-endpoint", testnet_url=17),
    dict(XTR_ROW, name="still-runs"),
]
result, text = run_text(bad_types, body(ESMERALDA))
check(
    "non-text row fields are NOT PROBED and do not stop later rails",
    (getattr(result, "counts", None), "still-runs REACHABLE" in text),
    ({"REACHABLE": 1, "UNREACHABLE": 0, "NOT PROBED": 3}, True),
)

# BOTH tables, because dispatch cross-checks them against each other: a row's
# `family` must resolve to the same probe its catalog key does, so patching one
# alone makes the row read "no probe exists" instead of exercising the defect
# path this check is about.
previous_probe = live._PROBES.get(XTR_KEY)
previous_family = live._FAMILY_PROBES.get("ootle")
_boom = lambda *_: (_ for _ in ()).throw(RuntimeError("synthetic tool defect"))  # noqa: E731
live._PROBES[XTR_KEY] = _boom
live._FAMILY_PROBES["ootle"] = _boom
try:
    result, text = run_text([XTR_ROW], body(ESMERALDA))
finally:
    if previous_probe is None:
        live._PROBES.pop(XTR_KEY, None)
    else:
        live._PROBES[XTR_KEY] = previous_probe
    if previous_family is None:
        live._FAMILY_PROBES.pop("ootle", None)
    else:
        live._FAMILY_PROBES["ootle"] = previous_family
check(
    "an unexpected probe defect is NOT PROBED, never UNREACHABLE",
    (getattr(result, "counts", None), "probe error" in text),
    ({"REACHABLE": 0, "UNREACHABLE": 0, "NOT PROBED": 1}, True),
)


# ---------------------------------------------------------------------------
# 5. The families that already worked must keep working.
# ---------------------------------------------------------------------------

print("\nregression — the four families that were already covered")

check(
    "bitcoin reads an Esplora tip height",
    refusal(_Opener(body=b"150540"), live._PROBES["bitcoin:testnet4/native:btc"],
            "https://esplora.example", {}),
    ("", "Esplora tip height 150540"),
)
check(
    "a non-numeric tip height is refused",
    refusal(_Opener(body=b"soon"), live._PROBES["bitcoin:testnet4/native:btc"],
            "https://esplora.example", {})[0],
    "wrong-shaped answer",
)
check(
    "solana accepts a list of at most one signature",
    refusal(body({"jsonrpc": "2.0", "id": 1, "result": []}),
            live._PROBES["solana:devnet/native:sol"], "https://rpc.example", {}),
    ("", "getSignaturesForAddress returned a usable answer"),
)
check(
    "a JSON-RPC error object is refused",
    refusal(body({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601}}),
            live._PROBES["solana:devnet/native:sol"], "https://rpc.example", {})[0],
    "JSON-RPC error object",
)
check(
    "an ERC-20 rail with no contract in its catalog key is a configuration error",
    refusal(body({"jsonrpc": "2.0", "id": 1, "result": "0x66"}),
            live._PROBES["polygon:amoy/erc20:0x41e94eb019c0762f9bfcf9fb1e58725bfb0e7582"],
            "https://rpc.example", {"catalog_key": "polygon:amoy/native:pol"})[0],
    "configuration error",
)


# ---------------------------------------------------------------------------

failed = [label for label, ok, *_ in CHECKS if not ok]
print("\n{} check(s), {} failed".format(len(CHECKS), len(failed)))
if failed:
    print("FAIL")
    for label in failed:
        print("  - {}".format(label))
    sys.exit(1)
print("PASS")
