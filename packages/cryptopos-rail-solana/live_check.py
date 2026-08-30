#!/usr/bin/env python3
"""The claims in this package that only a node can settle.

    PYTHONPATH=../cryptopos-core/src:src python3 live_check.py

**Why this is separate from the tests.** `tests/` runs offline with the node
behind a seam, which is right: it can drive malformed answers, missing metadata
and hostile shapes on demand, and a network test cannot. But it means every
constant this package compares against the chain is checked against a copy of
itself.

That is not hypothetical here. The first `DEVNET_GENESIS_HASH` was the real
hash truncated at 32 of its 44 characters. `_verify_network` compares it to
`getGenesisHash`, so the rail would have refused **every** real devnet node as
not-devnet — and all fourteen tests passed, because none of them opens a socket.

So the offline suite proves the logic and this proves the facts. Neither is
sufficient. Read-only: it signs nothing and spends nothing.
"""

import json
import sys
import urllib.request

sys.path.insert(0, "src")

from cryptopos_rail_solana import (
    DEVNET_GENESIS_HASH,
    reference_for_intent,
    solana_devnet_sol,
)

ENDPOINT = "https://api.devnet.solana.com"

# The transfer that proved this rail: CPS-2026-00328, 102,000 lamports, booked
# into ERPNext as ACC-SINV-2026-00075 on 2026-08-25. It is here because a
# commitment check needs a signature the node will actually parse -- a made-up
# 87-character string is refused for its SHAPE before commitment is considered,
# which passes for the wrong reason. Using this one also asks, every run,
# whether the transaction the rail was proved with is still on the chain.
PROVING_SIGNATURE = ("4s8nk6WwiUDbM2DuFsVDHBQ2pyWbwm684TvY9DAjvbiCSCE18Vs"
                     "RLBP2PXKitCCXtbNu1uqjjqdKew98JL4yHPkG")
CHECKS = []


def check(label, got, want, why=""):
    ok = got == want
    CHECKS.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if why:
        print(f"        {why}")
    if not ok:
        print(f"        got      {got!r}")
        print(f"        expected {want!r}")


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(
        ENDPOINT, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "cryptopos-rail-solana/live"})
    return json.loads(urllib.request.urlopen(request, timeout=20).read())


print(f"live check against {ENDPOINT}\n")

print("1. the constants this package compares the chain against")
genesis = rpc("getGenesisHash", [])["result"]
check("DEVNET_GENESIS_HASH is what devnet actually answers", DEVNET_GENESIS_HASH, genesis,
      "the whole hash. A truncated one refuses every real node and no offline test can tell.")

print("\n2. the rail is chargeable through a real endpoint")
readiness = solana_devnet_sol.readiness({"endpoint": ENDPOINT, "timeout_seconds": 15.0})
check("all four capabilities are ready", readiness.chargeable, True,
      f"ready: {', '.join(sorted(readiness.ready))}")
check("nothing is listed unavailable", list(readiness.unavailable), [])

print("\n3. the reference is a key the chain will accept as an address")
reference = reference_for_intent("live-check-intent")
answer = rpc("getSignaturesForAddress", [reference, {"limit": 1}])
check("getSignaturesForAddress accepts a derived reference", "error" in answer, False,
      f"{reference} — a base58 32-byte key. If the derivation ever produced "
      f"something that is not one, every sale would bind to an address the node "
      f"rejects, and only a node can say so.")
check("a never-used reference has no history", answer.get("result"), [],
      "so a fresh sale starts from nothing and cannot inherit another's money.")

print("\n4. the transfer this rail was proved with")
proof = rpc("getTransaction", [PROVING_SIGNATURE,
                               {"commitment": "finalized", "maxSupportedTransactionVersion": 0}])
result = proof.get("result") or {}
meta = result.get("meta") or {}
keys = ((result.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
merchant = "GyKqcxqdA7PbgbFXMW55G8rht5FhWPvgj9T96psdtZKc"
credited = None
if merchant in keys and meta.get("preBalances") and meta.get("postBalances"):
    index = keys.index(merchant)
    credited = meta["postBalances"][index] - meta["preBalances"][index]
check("CPS-2026-00328 still moved 102,000 lamports to the merchant", credited, 102_000,
      "the sale that proved this rail, re-read off the chain rather than off a record of it.")

print("\n5. the commitment ladder this rail settles on")
for level in ("confirmed", "finalized"):
    slot = rpc("getSlot", [{"commitment": level}]).get("result")
    check(f"getSlot answers at {level!r}", isinstance(slot, int) and slot > 0, True, f"slot {slot}")
# `processed` is refused by the two methods this rail READS WITH, and accepted
# by `getSlot`, which it only uses for a tip. The first draft of this check
# asked `getSlot` and reported the lore as stale -- it is not stale, it is about
# the other two calls, and asserting it against the wrong method would have
# retired a true constraint on the strength of a passing test.
for method, params in (
    ("getSignaturesForAddress", [reference, {"limit": 1, "commitment": "processed"}]),
    ("getTransaction", [PROVING_SIGNATURE,
                        {"commitment": "processed", "maxSupportedTransactionVersion": 0}]),
):
    answer = rpc(method, params)
    message = answer.get("error", {}).get("message", "")
    check(f"{method} refuses 'processed'", "commitment below" in message, True,
          f"{message or '<it answered>'!r} — so the rail builds no `processed` branch, "
          f"and this is the measurement rather than the reputation.")

print(f"\n{sum(CHECKS)}/{len(CHECKS)} passed")
sys.exit(0 if all(CHECKS) else 1)
