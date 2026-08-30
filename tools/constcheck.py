"""Are the constants this build compares the world against actually true?

    python3 tools/constcheck.py            # derived checks only, no network
    python3 tools/constcheck.py --live     # and ask the chains

**The gap this closes.** `make check` is lint, four Pythons, 100% executed lines
and full mutation coverage, and **not one of those gates can tell you that
`USDC_ON_AMOY` is USDC.** Every test that mentions it compares
`rails.USDC_ON_AMOY` to `rails.USDC_ON_AMOY`; `test_every_token_contract_is_eip55_valid`
proves the checksum holds, which catches a typo and passes a wrong-but-valid
address happily. A constant that names the wrong contract would take every sale
on that rail to the wrong token, silently, with a green suite.

That is not hypothetical. On 2026-08-25 a rail shipped with the Solana devnet
genesis hash truncated to 32 of its 44 characters. It would have refused every
real node, and fourteen unit tests passed against it because none opened a
socket. See `DECISIONS.md` D31.

**Two kinds of check, and the first is better.**

*Derived* — computed from something more primitive that is already in the tree,
so it needs no network and cannot drift. `TRANSFER_TOPIC` is the keccak-256 of a
function signature, and this repository ships keccak-256. It never needed to be
a remembered value at all, and anything else with that property should move here.

*Fetched* — asked of the chain, because nothing local can derive it. These need
`--live` and they are the reason this is a tool rather than a unit test: a gate
that requires the internet is a gate that fails on a train.

**What it cannot check, said out loud.** Native decimals (18 for wei, 8 for
satoshi, 9 for lamports) are protocol facts with no endpoint to ask. They are
cross-checked elsewhere -- `charge()` refuses if a rail row's `native_decimals`
disagrees with its adapter's (D26) -- which is agreement between two local
copies, not verification. If both are wrong, nothing here notices.
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "cryptopos-core" / "src"))

from cryptopos_core import rails
from cryptopos_core._keccak import keccak256
from cryptopos_core.evm import TRANSFER_TOPIC

AGENT = {"Content-Type": "application/json", "User-Agent": "cryptopos-constcheck/1.0"}
SEPOLIA = "https://ethereum-sepolia-rpc.publicnode.com"
AMOY = "https://polygon-amoy-bor-rpc.publicnode.com"
DEVNET = "https://api.devnet.solana.com"

# ERC-20 selectors, derived below rather than pasted -- same discipline as the
# topic. A wrong selector reads a different function and returns plausible bytes.
SELECTORS = {name: "0x" + keccak256(f"{name}()".encode()).hex()[:8]
             for name in ("decimals", "symbol", "name")}

RESULTS = []


def check(label, got, want, note=""):
    ok = got == want
    RESULTS.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if note:
        print(f"        {note}")
    if not ok:
        print(f"        got      {got!r}")
        print(f"        expected {want!r}")


def rpc(url, method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(url, data=body, headers=AGENT)
    return json.loads(urllib.request.urlopen(request, timeout=25).read()).get("result")


def abi_text(encoded):
    if not encoded or encoded == "0x":
        return None
    raw = bytes.fromhex(encoded[2:])
    if len(raw) >= 64:
        length = int.from_bytes(raw[32:64], "big")
        return raw[64:64 + length].decode(errors="replace")
    return raw.rstrip(b"\x00").decode(errors="replace")


def derived():
    print("derived — computed here from something more primitive, no network")
    check("TRANSFER_TOPIC is keccak256('Transfer(address,address,uint256)')",
          "0x" + keccak256(b"Transfer(address,address,uint256)").hex(), TRANSFER_TOPIC,
          "the repository ships keccak-256, so this value never needed remembering")


def token(label, url, address, expected_symbol, expected_decimals):
    code = rpc(url, "eth_getCode", [address, "latest"])
    check(f"{label}: {address} is a deployed contract", bool(code and code != "0x"), True)
    symbol = abi_text(rpc(url, "eth_call", [{"to": address, "data": SELECTORS["symbol"]}, "latest"]))
    check(f"{label}: it calls itself {expected_symbol}", symbol, expected_symbol,
          "an EIP-55 checksum proves an address is well formed, never that it is the right one")
    raw = rpc(url, "eth_call", [{"to": address, "data": SELECTORS["decimals"]}, "latest"])
    on_chain = int(raw, 16) if raw and raw != "0x" else None
    check(f"{label}: decimals on chain match the rail table", on_chain, expected_decimals,
          "a wrong exponent here is a factor of ten thousand in what a customer is charged")


def live():
    print("\nfetched — asked of the chain, because nothing local can derive it")
    token("USDC_ON_SEPOLIA", SEPOLIA, rails.USDC_ON_SEPOLIA, "USDC",
          rails.RAILS["usdc-eth"]["native_decimals"])
    token("USDC_ON_AMOY", AMOY, rails.USDC_ON_AMOY, "USDC",
          rails.RAILS["usdc-pol"]["native_decimals"])

    supply = rpc(DEVNET, "getTokenSupply", [rails.USDC_MINT_DEVNET])
    check("USDC_MINT_DEVNET is a real SPL mint with the rail table's decimals",
          (supply or {}).get("value", {}).get("decimals"),
          rails.RAILS["usdc-sol"]["native_decimals"],
          "the mint is named in the rail's catalog key, so a wrong one is a wrong rail identity")

    genesis = rpc(DEVNET, "getGenesisHash", [])
    check("the Solana endpoint this build talks to is devnet",
          genesis, "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG",
          "the value that shipped truncated to 32 of 44 characters — D31")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="also ask the chains")
    arguments = parser.parse_args()

    derived()
    if arguments.live:
        live()
    else:
        print("\n(--live not given; every fetched constant is unverified this run)")

    print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
