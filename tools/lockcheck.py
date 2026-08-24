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
    python3 tools/lockcheck.py --rpc sepolia   # the EVM side, at the real gate

**Read D15 before trusting an EVM number from this tool.** Block interval is the
right quantity for Bitcoin, whose one-confirmation gate is a block. It is the
WRONG quantity for an EVM rail: the terminal settles Sepolia after three
confirmations, which a reorg can undo after ERPNext has already booked. The gate
that would actually be safe is finality, and finality is not twelve seconds. So
`--rpc` reports the finality lag beside the interval, and judges against the lag.
Measuring the interval and calling it safety is the exact error D15 corrects.

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


def finality_lag(url):
    """How far `safe` and `finalized` trail the tip, in seconds.

    The number an EVM rail is actually judged on. `_is_mature` gates on three
    confirmations today, which is fast and reversible; a settlement that cannot
    be unbooked has to wait for one of these instead.
    """
    headers = {"Content-Type": "application/json",
               "User-Agent": "cryptopos-lockcheck/1.0"}

    def block(tag):
        body = json.dumps({"jsonrpc": "2.0", "id": 1,
                           "method": "eth_getBlockByNumber",
                           "params": [tag, False]}).encode()
        request = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response).get("result")
        if not result:
            return None
        return int(result["timestamp"], 16), int(result["number"], 16)

    # Blocks as well as seconds. A two-second lag and a tag that is simply
    # aliased to `latest` report the same "0.0 min", and on Amoy the honest
    # answer really is about two seconds -- so the height is what tells them
    # apart. Reporting only the time is why this rail was dismissed unmeasured
    # through D14 and D15; see D18.
    top = block("latest")
    lags = {}
    for tag in ("safe", "finalized"):
        try:
            answer = block(tag)
        except Exception:
            answer = None
        if answer is None or top is None:
            lags[tag] = None
        else:
            lags[tag] = (top[0] - answer[0], top[1] - answer[1])
    return lags


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

    if arguments.rpc:
        lags = finality_lag(RPC_ENDPOINTS[arguments.rpc])
        print()
        print("  but the interval is not this rail's gate — see DECISIONS.md D15:")
        # From the measured median, not a hardcoded block time. Amoy's blocks
        # are ~2 s and Sepolia's ~12 s, and one constant for both was wrong for
        # whichever rail it was not written for.
        median_interval = statistics.median(intervals)
        print(f"    3 confirmations (what runs today)  ~{3 * median_interval:.0f} s"
              "  — fits the lock, and a reorg can unbook it")
        for tag in ("safe", "finalized"):
            lag = lags.get(tag)
            if lag is None:
                print(f"    {tag:<34} unavailable from this endpoint")
                continue
            seconds, blocks = lag
            verdict = "fits" if seconds <= arguments.lock else "DOES NOT FIT"
            print(f"    {tag:<34} {seconds / 60:5.1f} min"
                  f" ({blocks} block{'' if blocks == 1 else 's'} behind tip)  — {verdict}")
        finalized = lags.get("finalized")
        if finalized is not None and finalized[0] > arguments.lock:
            print()
            print(f"FAIL: a settlement that cannot be unbooked needs"
                  f" {finalized / 60:.1f} min and the lock is"
                  f" {arguments.lock / 60:.0f}.")
            return 1

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
