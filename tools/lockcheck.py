#!/usr/bin/env python3
"""Measure a chain's block intervals against the terminal's rate lock.

D11 rejected a public multi-visitor demo on one number: testnet4's median block
interval, measured live, was 20.0 minutes against a 15-minute
`RATE_LOCK_SECONDS`. Bitcoin settlement credits only confirmations inside that
window, and D10 never reopens a terminal state, so the tail is lost for good.

A number like that in a document is a claim nobody can check. This is the
measurement, so the claim can be re-run instead of believed -- and so it can be
noticed if testnet4's timing changes.

    python3 tools/lockcheck.py                 # testnet4, against 15 minutes
    python3 tools/lockcheck.py --lock 3600     # what an hour would buy
    python3 tools/lockcheck.py --blocks 50     # a longer sample
    python3 tools/lockcheck.py --rpc sepolia   # the EVM side, for comparison

The comparison is the point. D14 concluded that the only route to an unattended
demo is a fast chain, and that conclusion rests on Sepolia's interval being as
short as Bitcoin's is long. Both halves are measured here or neither is.

Read-only, one HTTP GET, no bench and no container.
"""

import argparse
import json
import statistics
import sys
import urllib.request


DEFAULT_API = "https://mempool.space/testnet4/api"

# The endpoints install.py seeds, so the tool measures what the terminal
# actually talks to rather than a nearby chain that happens to be easier.
RPC_ENDPOINTS = {
    "sepolia": "https://ethereum-sepolia-rpc.publicnode.com",
    "amoy": "https://polygon-amoy-bor-rpc.publicnode.com",
}

# charge.py's RATE_LOCK_SECONDS. Named here rather than imported because this
# tool must run without a bench, and a wrong copy is caught by the fact that
# the number is printed next to the verdict.
RATE_LOCK_SECONDS = 15 * 60


def recent_intervals(api, count):
    """Seconds between consecutive recent blocks, newest first."""
    request = urllib.request.Request(
        f"{api}/v1/blocks", headers={"User-Agent": "cryptopos-lockcheck/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        blocks = json.load(response)
    stamps = sorted((block["timestamp"] for block in blocks), reverse=True)
    stamps = stamps[: count + 1]
    intervals = [stamps[i] - stamps[i + 1] for i in range(len(stamps) - 1)]
    return [interval for interval in intervals if interval >= 0]


def rpc_intervals(url, count):
    """The same measurement over JSON-RPC, for the EVM rails."""
    headers = {"Content-Type": "application/json",
               "User-Agent": "cryptopos-lockcheck/1.0"}

    def call(method, params):
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        request = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)["result"]

    tip = int(call("eth_blockNumber", []), 16)
    stamps = [
        int(call("eth_getBlockByNumber", [hex(number), False])["timestamp"], 16)
        for number in range(tip, tip - count - 1, -1)
    ]
    intervals = [stamps[i] - stamps[i + 1] for i in range(len(stamps) - 1)]
    return [interval for interval in intervals if interval >= 0]


def report(intervals, lock_seconds):
    """What the intervals say about a payment broadcast at a random moment."""
    if not intervals:
        raise SystemExit("no intervals returned; the endpoint answered nothing usable")

    over = [interval for interval in intervals if interval > lock_seconds]

    # A payment does not arrive at the start of an interval, it arrives at a
    # uniformly random point inside one -- and a random moment is likelier to
    # land in a long gap than a short one. So each interval is weighted by its
    # own length, and the part of it that is already past the lock is the part
    # where an arriving payment cannot confirm in time.
    total = sum(intervals)
    stranded = sum(max(0, interval - lock_seconds) for interval in intervals)

    print(f"  sample                {len(intervals)} intervals")
    print(f"  mean                  {statistics.mean(intervals) / 60:6.1f} min")
    print(f"  median                {statistics.median(intervals) / 60:6.1f} min")
    print(f"  longest               {max(intervals) / 60:6.1f} min")
    print(f"  rate lock             {lock_seconds / 60:6.1f} min")
    print(f"  intervals over lock   {len(over)}/{len(intervals)}"
          f" = {100 * len(over) / len(intervals):.0f}%")
    print()
    share = 100 * stranded / total
    print(f"  a payment broadcast at a random moment misses the lock {share:.0f}% of the time")
    return share


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--rpc", choices=sorted(RPC_ENDPOINTS),
                        help="measure an EVM rail over JSON-RPC instead")
    parser.add_argument("--lock", type=int, default=RATE_LOCK_SECONDS,
                        help="the acceptance window in seconds")
    parser.add_argument("--blocks", type=int, default=15,
                        help="how many recent intervals to measure")
    parser.add_argument("--max-share", type=float, default=5.0,
                        help="fail above this percentage of stranded payments")
    arguments = parser.parse_args(argv)

    if arguments.rpc:
        url = RPC_ENDPOINTS[arguments.rpc]
        print(f"lockcheck: {arguments.rpc} — {url}")
        intervals = rpc_intervals(url, arguments.blocks)
    else:
        print(f"lockcheck: {arguments.api}")
        intervals = recent_intervals(arguments.api, arguments.blocks)
    share = report(intervals, arguments.lock)
    if share > arguments.max_share:
        print(f"\nFAIL: {share:.0f}% is above the {arguments.max_share:.0f}% this"
              " tool is willing to call acceptable.")
        print("      See DECISIONS.md D11, and D13 for why lengthening the lock"
              " is not the fix.")
        return 1
    print(f"\nOK: {share:.0f}% is within the {arguments.max_share:.0f}% threshold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
