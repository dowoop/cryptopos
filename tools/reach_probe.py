"""Can this process actually reach every enabled rail it is willing to sell?

Agreement about installed adapters is necessary and insufficient.  Four Frappe
processes can agree that a rail exists while one process cannot reach that
rail's endpoint.  A sale charged there can broadcast successfully in the
customer's wallet and still end unverified because the watcher never observed
the chain.

This probe makes a small, read-only request of the same kind each watcher makes:

* Bitcoin: the Esplora tip height;
* native EVM assets: the latest full block;
* ERC-20 assets: transfer logs from the latest block; and
* Solana: signature history for a deterministic, unused reference.

It reports transport and answer failures separately and returns non-zero when
any enabled rail is unreachable.

    cd sites && ../env/bin/python ../apps/cryptopos/tools/reach_probe.py

Run it in every Frappe container.  It reads rail rows and public chain endpoints
only; it does not import an adapter, mutate a row, or touch a sale.
"""

import hashlib
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

_TIMEOUT_SECONDS = 10
_MAX_RESPONSE_BYTES = 4_000_000
_AGENT = "cryptopos-reach-probe/1.0"
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class ProbeFailure(Exception):
    """One unreachable classification and its operator-facing detail."""

    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect means the configured endpoint itself did not answer."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


# Match the watcher transports: connect directly, without silently sending a
# chain request through a shell-configured HTTP proxy.  The failure being gated
# is the network path available to the Frappe process itself.
_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


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


def _request(request):
    try:
        with _OPENER.open(request, timeout=_TIMEOUT_SECONDS) as response:
            body = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exception:
        reason = f"HTTP {exception.code} {exception.reason}".strip()
        raise ProbeFailure("HTTP error", reason) from None
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exception:
        raise _transport_failure(exception) from None
    except ValueError as exception:
        raise ProbeFailure("configuration error", _detail(exception)) from None
    if len(body) > _MAX_RESPONSE_BYTES:
        raise ProbeFailure("wrong-shaped answer", "response exceeded the 4 MB safety limit")
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
    catalog_key = (_get(rail, "catalog_key") or "").strip()
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


_PROBES = {
    "bitcoin": _bitcoin,
    "evm-native": _evm_native,
    "evm-erc20": _evm_erc20,
    "solana": _solana,
}


def _get(row, field):
    if isinstance(row, dict):
        return row.get(field)
    getter = getattr(row, "get", None)
    if callable(getter):
        return getter(field)
    return getattr(row, field, None)


def _endpoint(rail, mode):
    field = "testnet_url" if mode == "testnet" else "live_url" if mode == "mainnet" else ""
    return ((_get(rail, field) if field else "") or "").strip()


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
    """Probe enabled rails and return the number that are unreachable.

    ``rails`` and ``mode`` are injectable so controls can use in-memory rows;
    the normal command reads both from Frappe without modifying them.
    """
    if rails is None:
        deployed_mode, rails = _deployment()
        mode = mode or deployed_mode
    mode = mode or "testnet"
    rails = list(rails)
    failures = 0

    print(f"  reach probe: {len(rails)} enabled rail(s), mode={mode}, timeout={_TIMEOUT_SECONDS}s")
    for rail in rails:
        name = str(_get(rail, "name") or "(unnamed)")
        family = (_get(rail, "family") or "").strip()
        endpoint = _endpoint(rail, mode)
        print(f"  {name:<10} endpoint {endpoint or '(none)'}")
        try:
            if not endpoint:
                raise ProbeFailure("configuration error", f"no {mode} endpoint is configured")
            _validate_endpoint(endpoint)
            probe = _PROBES.get(family)
            if probe is None:
                raise ProbeFailure("configuration error", f"no reach probe exists for family {family!r}")
            detail = probe(endpoint, rail)
        except ProbeFailure as failure:
            failures += 1
            print(f"  {name:<10} UNREACHABLE — {failure.kind}: {failure.detail}")
        except Exception as failure:
            failures += 1
            print(f"  {name:<10} UNREACHABLE — connection refused/timeout: {_detail(failure)}")
        else:
            print(f"  {name:<10} REACHABLE — {detail}")

    print()
    if failures:
        print(f"  FAIL: {failures} of {len(rails)} enabled rail(s) unreachable from this process.")
    else:
        print(f"  PASS: all {len(rails)} enabled rail(s) reachable from this process.")
    return failures


if __name__ == "__main__":
    import sys

    import frappe

    frappe.init(site="erp.localhost")
    frappe.connect()
    try:
        sys.exit(1 if run() else 0)
    finally:
        frappe.destroy()
