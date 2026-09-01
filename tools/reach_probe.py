"""Can this process actually reach every enabled rail it is willing to sell?

Agreement about installed adapters is necessary and insufficient.  Four Frappe
processes can agree that a rail exists while one process cannot reach that
rail's endpoint.  A sale charged there can broadcast successfully in the
customer's wallet and still end unverified because the watcher never observed
the chain.

This probe makes a small, read-only request of the same kind each watcher makes:

* Bitcoin: the Esplora tip height;
* native EVM assets: the latest full block;
* ERC-20 assets: transfer logs from the latest block;
* Solana: signature history for a deterministic, unused reference; and
* Ootle: the indexer's own network/epoch identification.

An adapter key with no probe here is a **coverage gap, not an unreachable rail**.
Until 2026-08-31 `ootle` had none, and all four workers duly reported `xtr`
UNREACHABLE against an indexer that answered in half a second -- a true
condition ("no probe exists") wearing a false sentence, which is the shape
D25, D38, D39 and D40 all have.  It also refused every `prove_end_to_end.py
--rail xtr`, so the one tool that proves a rail works could not prove the
rail.  Add the concrete adapter dialect here when you add the adapter.

It reports transport and answer failures separately and returns non-zero when
any enabled rail cannot be confirmed.

    cd sites && ../env/bin/python ../apps/cryptopos/tools/reach_probe.py

Run it in every Frappe container.  It reads rail rows, the installed adapter
registry, and public chain endpoints; it does not mutate a row or touch a sale.
"""

import hashlib
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from cryptopos_core import chain as ootle_chain

_LEGACY_TIMEOUT_SECONDS = 10
_MAX_RESPONSE_BYTES = 4_000_000
_AGENT = "cryptopos-reach-probe/1.0"
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class ProbeFailure(Exception):
    """One refusal classification and its operator-facing detail."""

    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect means the configured endpoint itself did not answer."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


# The four pre-Ootle probe families retain their original transport: no
# redirects and no environment proxy.  Their watcher transports are separate
# implementations, so this intentionally makes no claim that they match.
# Ootle does not use this opener: `_ootle` calls `chain._urlopen`, the exact
# proxy-aware, HTTPS-redirect-following/no-downgrade seam used by OotleReader.
_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


class ProbeResult(int):
    """The old failure count, with machine-readable category counts attached."""

    def __new__(cls, failures, counts):
        result = int.__new__(cls, failures)
        result.counts = dict(counts)
        return result

    @property
    def failures(self):
        return int(self)


def _detail(exception):
    text = str(exception).strip()
    name = type(exception).__name__
    return f"{name}: {text}" if text else name


def _transport_failure(exception):
    """Convert urllib's nested exceptions into a stable failure category."""
    reason = exception.reason if isinstance(exception, urllib.error.URLError) else exception
    while isinstance(reason, urllib.error.URLError):
        reason = reason.reason
    if isinstance(reason, socket.gaierror):
        return ProbeFailure("DNS failure", _detail(reason))
    if isinstance(reason, (ConnectionError, TimeoutError, socket.timeout, OSError)):
        return ProbeFailure("connection refused/timeout", _detail(reason))
    return ProbeFailure("connection refused/timeout", _detail(reason))


def _request(
    request,
    *,
    open_request=None,
    timeout=_LEGACY_TIMEOUT_SECONDS,
    max_bytes=_MAX_RESPONSE_BYTES,
):
    if open_request is None:
        open_request = _OPENER.open
    try:
        with open_request(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exception:
        reason = f"HTTP {exception.code} {exception.reason}".strip()
        raise ProbeFailure("HTTP error", reason) from None
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exception:
        raise _transport_failure(exception) from None
    except ValueError as exception:
        raise ProbeFailure("configuration error", _detail(exception)) from None
    if len(body) > max_bytes:
        raise ProbeFailure("wrong-shaped answer", f"response exceeded the {max_bytes}-byte safety limit")
    return body


def _json_rpc(endpoint, method, params):
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": _AGENT,
        },
        method="POST",
    )
    payload = _request(request)
    try:
        answer = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exception:
        raise ProbeFailure("wrong-shaped answer", f"{method} returned invalid JSON: {exception}") from None
    if not isinstance(answer, dict):
        raise ProbeFailure("wrong-shaped answer", f"{method} did not return a JSON object")
    if answer.get("error") is not None:
        raise ProbeFailure("JSON-RPC error object", f"{method}: {answer['error']!r}")
    if answer.get("jsonrpc") != "2.0" or answer.get("id") != 1 or "result" not in answer:
        raise ProbeFailure("wrong-shaped answer", f"{method} returned a malformed JSON-RPC envelope")
    return answer["result"]


def _bitcoin(endpoint, _rail):
    request = urllib.request.Request(
        f"{endpoint.rstrip('/')}/blocks/tip/height",
        headers={"Accept": "text/plain", "User-Agent": _AGENT},
        method="GET",
    )
    payload = _request(request)
    try:
        height = payload.decode("ascii").strip()
    except UnicodeDecodeError as exception:
        raise ProbeFailure("wrong-shaped answer", f"tip height was not ASCII: {exception}") from None
    if not height.isascii() or not height.isdecimal():
        raise ProbeFailure("wrong-shaped answer", f"tip height was not a non-negative integer: {height!r}")
    return f"Esplora tip height {int(height)}"


def _quantity(value, field):
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ProbeFailure("wrong-shaped answer", f"{field} was not a hexadecimal quantity: {value!r}")
    digits = value[2:]
    if not digits or (len(digits) > 1 and digits.startswith("0")):
        raise ProbeFailure("wrong-shaped answer", f"{field} was not canonical: {value!r}")
    try:
        return int(digits, 16)
    except ValueError:
        raise ProbeFailure("wrong-shaped answer", f"{field} was not hexadecimal: {value!r}") from None


def _evm_tip(endpoint):
    return _quantity(_json_rpc(endpoint, "eth_blockNumber", []), "block number")


def _evm_native(endpoint, _rail):
    tip = _evm_tip(endpoint)
    block = _json_rpc(endpoint, "eth_getBlockByNumber", [hex(tip), True])
    if not isinstance(block, dict) or _quantity(block.get("number"), "latest block number") != tip:
        raise ProbeFailure("wrong-shaped answer", "eth_getBlockByNumber did not return the requested block")
    if not isinstance(block.get("transactions"), list):
        raise ProbeFailure("wrong-shaped answer", "latest block transactions were not a list")
    return f"eth_getBlockByNumber returned block {tip}"


def _token_contract(rail):
    catalog_key = _text_field(rail, "catalog_key")
    marker = "/erc20:"
    if marker not in catalog_key:
        raise ProbeFailure("configuration error", f"catalog key has no ERC-20 contract: {catalog_key!r}")
    contract = catalog_key.rsplit(marker, 1)[1]
    if len(contract) != 42 or not contract.startswith("0x"):
        raise ProbeFailure("configuration error", f"catalog key has a malformed ERC-20 contract: {contract!r}")
    try:
        int(contract[2:], 16)
    except ValueError:
        raise ProbeFailure("configuration error", f"catalog key has a malformed ERC-20 contract: {contract!r}") from None
    return contract


def _evm_erc20(endpoint, rail):
    tip = _evm_tip(endpoint)
    logs = _json_rpc(
        endpoint,
        "eth_getLogs",
        [
            {
                "fromBlock": hex(tip),
                "toBlock": hex(tip),
                "address": _token_contract(rail),
                "topics": [_TRANSFER_TOPIC],
            }
        ],
    )
    if not isinstance(logs, list):
        raise ProbeFailure("wrong-shaped answer", "eth_getLogs result was not a list")
    return f"eth_getLogs returned {len(logs)} transfer log(s) at block {tip}"


def _base58(data):
    value = int.from_bytes(data, "big")
    encoded = ""
    while value:
        value, digit = divmod(value, 58)
        encoded = _BASE58[digit] + encoded
    zeroes = len(data) - len(data.lstrip(b"\0"))
    return "1" * zeroes + (encoded or ("" if zeroes else "1"))


def _solana(endpoint, _rail):
    reference = _base58(hashlib.sha256(b"cryptopos-readiness-probe").digest())
    signatures = _json_rpc(
        endpoint,
        "getSignaturesForAddress",
        [reference, {"commitment": "confirmed", "limit": 1}],
    )
    if not isinstance(signatures, list) or len(signatures) > 1:
        raise ProbeFailure("wrong-shaped answer", "getSignaturesForAddress result was not a list of at most one")
    return "getSignaturesForAddress returned a usable answer"


def _installed_adapters():
    """Return the same catalog-key registry used by charge and watch."""
    from cryptopos import catalog

    return catalog.plugins()


def _text_field(rail, field):
    value = _get(rail, field)
    if not isinstance(value, str):
        raise ProbeFailure("configuration error", f"{field} must be text, not {type(value).__name__}")
    value = value.strip()
    if not value:
        raise ProbeFailure("configuration error", f"{field} must not be empty")
    return value


def _adapter_for(rail):
    """Resolve and cross-check the row against the runtime adapter registry."""
    catalog_key = _text_field(rail, "catalog_key")
    family = _text_field(rail, "family")
    try:
        adapters = _installed_adapters()
        adapter = adapters.get(catalog_key)
    except Exception as exception:
        raise ProbeFailure("configuration error", f"adapter registry failed: {_detail(exception)}") from None
    if adapter is None:
        raise ProbeFailure(
            "configuration error",
            f"catalog key {catalog_key!r} resolves to no installed adapter",
        )
    if getattr(adapter, "key", None) != catalog_key:
        raise ProbeFailure(
            "configuration error",
            f"registry entry {catalog_key!r} returned adapter {getattr(adapter, 'key', None)!r}",
        )
    network = getattr(adapter, "network", None)
    namespace = getattr(network, "namespace", None)
    reference = getattr(network, "reference", None)
    if not isinstance(namespace, str) or not isinstance(reference, str) or not reference:
        raise ProbeFailure("configuration error", f"adapter {catalog_key!r} has no usable network identity")
    # NOT `family == namespace`. That was shipped on 2026-08-31 and took all
    # four EVM rails down inside every worker, because the two words are not
    # the same kind of thing: `family` is the TRANSPORT DIALECT this probe
    # dispatches on (`evm-native`, `evm-erc20`, `bitcoin`, `solana`, `ootle`)
    # and `namespace` is the CHAIN (`ethereum`, `polygon`, `bitcoin`,
    # `solana`, `ootle`). They coincide for bitcoin, solana and ootle and can
    # never coincide for an EVM rail. The equality passed every offline test
    # because the harness's rows were Ootle rows.
    #
    # What is actually worth cross-checking is that the row's catalog key
    # resolves to an installed adapter and that the adapter agrees the row
    # names it -- both asserted above -- and that the family the probe is
    # about to dispatch on is one this adapter can be driven through.
    if _probe_for(catalog_key, family, adapter) is None:
        raise ProbeFailure(
            "configuration error",
            f"no reach probe exists for catalog key {catalog_key!r} (family {family!r})",
        )
    return catalog_key, adapter


def _probe_for(catalog_key, family, adapter):
    """The probe to drive this row with, or None.

    Two lookups on purpose, and the order is the point.

    An EXACT catalog-key hit wins. That is what stops a plugin wheel reusing
    an existing `family` with a different API dialect from being declared
    covered by a probe written for somebody else's watcher.

    Falling back to `family` is what keeps D26 working: an operator may add an
    asset by creating a row and pointing `catalog_key` at an EXISTING adapter
    -- proven with a fabricated `ZZZ` asset -- and a catalog-key-only table
    would refuse every such rail as NOT PROBED, turning a documented
    no-code feature into a code change. So the fallback is allowed only when
    the adapter is a BUILT-IN, because a built-in's dialect is the one these
    probes were written against. A plugin brings its own dialect and must
    bring its own probe.
    """
    probe = _PROBES.get(catalog_key)
    if probe is not None:
        # The catalog key decides, but a row whose `family` names a DIFFERENT
        # dialect is a misconfigured row, and failing closed on it is cheaper
        # than discovering it when money is in flight. Compared through the two
        # tables rather than against a hand-written key->family map, so there is
        # no third list to go stale.
        if _FAMILY_PROBES.get(family) is not probe:
            return None
        return probe
    if not _is_builtin(adapter):
        return None
    return _FAMILY_PROBES.get(family)


def _is_builtin(adapter):
    try:
        from cryptopos_core import catalog as _catalog

        return any(built.key == getattr(adapter, "key", None) for built in _catalog.builtin_rails())
    except Exception:                                    # pragma: no cover - registry defect
        return False


def _ootle(endpoint, rail, adapter=None):
    """GET /network -- the same read the adapter's own `readiness` makes.

    Ootle observation is an event-stream replay keyed on the recipient's XTR
    vault, and resolving that vault needs a recipient this probe deliberately
    does not read: it takes rail rows, not merchant addresses.  `/network` is
    the call `OotleEsmeralda.readiness` itself uses to decide whether
    OBSERVATION is available -- keyless, free, and refused by the same
    conditions -- so a process that can make it can reach this rail.
    """
    _catalog_key, adapter = _adapter_for(rail) if adapter is None else (_text_field(rail, "catalog_key"), adapter)
    expected = adapter.network.reference
    request = urllib.request.Request(
        f"{endpoint.rstrip('/')}/network",
        headers={"Accept": "application/json", "User-Agent": _AGENT},
        method="GET",
    )
    payload = _request(
        request,
        open_request=ootle_chain._urlopen,
        timeout=ootle_chain.READ_TIMEOUT_SECONDS,
        max_bytes=ootle_chain.MAX_RESPONSE_BYTES,
    )
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exception:
        raise ProbeFailure("wrong-shaped answer", f"/network returned invalid JSON: {exception}") from None
    if not isinstance(body, dict):
        raise ProbeFailure("wrong-shaped answer", "/network did not return a JSON object")
    network = body.get("network")
    if network != expected:
        raise ProbeFailure(
            "wrong-shaped answer",
            f"/network identified itself as {network!r}, not the {expected!r} this rail sells",
        )
    epoch = body.get("epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        raise ProbeFailure("wrong-shaped answer", f"/network epoch was not a non-negative integer: {epoch!r}")
    return f"/network returned {network} at epoch {epoch}"


_FAMILY_PROBES = {
    "bitcoin": _bitcoin,
    "evm-native": _evm_native,
    "evm-erc20": _evm_erc20,
    "solana": _solana,
    "ootle": _ootle,
}

_PROBES = {
    "bitcoin:testnet4/native:btc": _bitcoin,
    "ethereum:sepolia/native:eth": _evm_native,
    "ethereum:sepolia/erc20:0x1c7d4b196cb0c7b01d743fbc6116a902379c7238": _evm_erc20,
    "polygon:amoy/native:pol": _evm_native,
    "polygon:amoy/erc20:0x41e94eb019c0762f9bfcf9fb1e58725bfb0e7582": _evm_erc20,
    "solana:devnet/native:sol": _solana,
    "solana:devnet/spl:4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU": _solana,
    "ootle:esmeralda/native:xtr": _ootle,
}


def _classify(kind):
    """How to headline one refusal: ``(word, counts_as_unprobed)``.

    A configuration error is not a statement about the network.  The endpoint
    may answer perfectly; what failed is this tool's own ability to ask.
    Printing UNREACHABLE for it sends an operator to go and fix a network that
    is not broken -- and did, for `xtr`, for as long as no `ootle` probe
    existed here, all the way through `prove_end_to_end.py`'s refusal advice.

    Both words still refuse.  A rail nothing can confirm is a rail nothing
    should charge on, so the caller's exit code does not soften.
    """
    if kind in {"configuration error", "probe error"}:
        return "NOT PROBED", 1
    return "UNREACHABLE", 0


def _get(row, field):
    if isinstance(row, dict):
        return row.get(field)
    getter = getattr(row, "get", None)
    if callable(getter):
        return getter(field)
    return getattr(row, field, None)


def _endpoint(rail, mode):
    field = "testnet_url" if mode == "testnet" else "live_url" if mode == "mainnet" else ""
    if not field:
        raise ProbeFailure("configuration error", f"unsupported deployment mode {mode!r}")
    value = _get(rail, field)
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ProbeFailure("configuration error", f"{field} must be text, not {type(value).__name__}")
    return value.strip()


def _validate_endpoint(endpoint):
    try:
        parts = urllib.parse.urlsplit(endpoint)
        port = parts.port
    except ValueError as exception:
        raise ProbeFailure("configuration error", f"endpoint URL is malformed: {exception}") from None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise ProbeFailure(
            "configuration error",
            "endpoint must be an HTTPS URL without credentials, query text, or a fragment",
        )
    if port is not None and not 1 <= port <= 65_535:
        raise ProbeFailure("configuration error", f"endpoint port is outside 1..65535: {port}")


def _deployment():
    import frappe

    settings = frappe.get_single("CryptoPoS Settings")
    mode = (settings.mode or "testnet").strip()
    rails = frappe.get_all(
        "Crypto Rail",
        filters={"enabled": 1},
        fields=["name", "family", "catalog_key", "testnet_url", "live_url"],
        order_by="name",
    )
    return mode, rails


def run(rails=None, mode=None):
    """Probe enabled rails and return an int-compatible structured result.

    ``rails`` and ``mode`` are injectable so controls can use in-memory rows;
    the normal command reads both from Frappe without modifying them.
    """
    if rails is None:
        deployed_mode, rails = _deployment()
        mode = mode or deployed_mode
    mode = mode or "testnet"
    rails = list(rails)
    failures = 0
    counts = {"REACHABLE": 0, "UNREACHABLE": 0, "NOT PROBED": 0}

    print(f"  reach probe: {len(rails)} enabled rail(s), mode={mode}, adapter-specific timeouts")
    for rail in rails:
        name = "(unnamed)"
        endpoint = ""
        try:
            name = str(_get(rail, "name") or "(unnamed)")
            endpoint = _endpoint(rail, mode)
            print(f"  {name:<10} endpoint {endpoint or '(none)'}")
            if not endpoint:
                raise ProbeFailure("configuration error", f"no {mode} endpoint is configured")
            _validate_endpoint(endpoint)
            catalog_key, adapter = _adapter_for(rail)
            probe = _probe_for(catalog_key, _text_field(rail, "family"), adapter)
            if probe is None:
                raise ProbeFailure("configuration error", f"no reach probe exists for adapter {catalog_key!r}")
            detail = probe(endpoint, rail, adapter) if probe is _ootle else probe(endpoint, rail)
        except ProbeFailure as failure:
            failures += 1
            headline, _is_unprobed = _classify(failure.kind)
            counts[headline] += 1
            print(f"  {name:<10} {headline} — {failure.kind}: {failure.detail}")
        except Exception as failure:
            failures += 1
            headline, _is_unprobed = _classify("probe error")
            counts[headline] += 1
            print(f"  {name:<10} {headline} — probe error: {_detail(failure)}")
        else:
            counts["REACHABLE"] += 1
            print(f"  {name:<10} REACHABLE — {detail}")

    print()
    if failures:
        print(f"  FAIL: {failures} of {len(rails)} enabled rail(s) could not be confirmed"
              " reachable from this process.")
        if counts["NOT PROBED"]:
            unprobed = counts["NOT PROBED"]
            print(f"        {unprobed} of those {'was' if unprobed == 1 else 'were'} NOT PROBED"
                  " — a gap in this tool, not a fault at the endpoint.")
    else:
        print(f"  PASS: all {len(rails)} enabled rail(s) reachable from this process.")
    return ProbeResult(failures, counts)


if __name__ == "__main__":
    import sys

    import frappe

    frappe.init(site="erp.localhost")
    frappe.connect()
    try:
        sys.exit(1 if run() else 0)
    finally:
        frappe.destroy()
