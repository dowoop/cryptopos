#!/usr/bin/env python3
"""award_drainer — writes queued loyalty awards to Ootle, from the host.

The Frappe app holds intent; this holds the key. Nothing in the container can
mint, and that is the point rather than a side effect: a web application with
a signing key in its blast radius is a different risk from one without.

It is also the only arrangement that works. The toolkit is dynamically linked
against a newer glibc than the frappe image carries, so the binary cannot
load in the container at all:

    /tmp/toolkit: /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.38' not found

Standard library only, so it runs anywhere Python does and adds nothing to
the tree it drains from.

    ./award_drainer.py --once           drain what is waiting and exit
    ./award_drainer.py                  loop
    ./award_drainer.py --dry-run        show what WOULD be written, write nothing

Credentials come from the environment:
    CRYPTOPOS_URL     default http://localhost:8080
    CRYPTOPOS_KEY     api key
    CRYPTOPOS_SECRET  api secret
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Measured finality is around 29s; 90s is the terminal's own budget and is
# kept identical so the two write paths cannot disagree about what "too long"
# means.
TOOLKIT_TIMEOUT_SECONDS = 90.0

POLL_SECONDS = 15

# THE TOOLKIT IS NOT IN THIS REPOSITORY and cannot be: it is a Rust binary,
# and the reason above is why it cannot live in the container either. Its
# default used to be an absolute path into a sibling checkout on one machine,
# which was wrong everywhere else and became wrong here too when that checkout
# was retired on 2026-09-04.
#
# So it is named, not guessed: CRYPTOPOS_TOOLKIT, or `toolkit` on PATH, or
# --toolkit. An empty default is deliberate -- the check below then refuses
# with the three ways to fix it, rather than reporting a path nobody chose.
DEFAULT_TOOLKIT = os.environ.get("CRYPTOPOS_TOOLKIT") or shutil.which("toolkit") or ""

BASE = os.environ.get("CRYPTOPOS_URL", "http://localhost:8080").rstrip("/")
KEY = os.environ.get("CRYPTOPOS_KEY", "")
SECRET = os.environ.get("CRYPTOPOS_SECRET", "")


def call(method, payload=None):
    """POST to a whitelisted method. Returns the parsed message, or raises."""
    url = f"{BASE}/api/method/{method}"
    data = json.dumps(payload or {}).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", f"token {KEY}:{SECRET}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8")).get("message")


def interpret(completed):
    """Decide what actually happened. Returns (state, tx_id, reason).

    The toolkit exits 0 on a transaction that was SUBMITTED, even when the
    network then rejected it, so the exit code alone cannot be trusted. The
    text is the evidence.
    """
    output = (completed.stdout or "") + (completed.stderr or "")

    if "Reject" in output:
        return "refused", "", "The network refused the award. Nothing was issued."
    if "Commit" not in output:
        return (
            "refused",
            "",
            "The award did not commit. Nothing was issued.",
        )

    tx_id = ""
    for token in output.split():
        cleaned = token.strip(".,()[]\"'")
        if len(cleaned) == 64 and all(c in "0123456789abcdef" for c in cleaned.lower()):
            tx_id = cleaned
            break
    return "issued", tx_id, ""


def drain_one(job, toolkit, dry_run=False):
    argv = [
        toolkit,
        "loyalty",
        "award",
        str(job["component"]),
        str(job["account"]),
        str(int(job["points"])),
        str(job["sale_ref"]),
        str(job["points_resource"]),
    ]

    if dry_run:
        print(f"  WOULD RUN  {' '.join(argv)}")
        return None

    try:
        completed = subprocess.run(
            argv,
            cwd=os.path.dirname(os.path.dirname(toolkit)) or None,
            capture_output=True,
            text=True,
            timeout=TOOLKIT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # Deliberate under-claim. It may still land, and this record will not
        # say either way -- a customer told they hold nothing who turns out
        # to hold something is pleased; the reverse is a broken promise.
        return {
            "name": job["name"],
            "state": "unverified",
            "reason": (
                f"The award did not confirm within {int(TOOLKIT_TIMEOUT_SECONDS)}s. "
                "It may still land; nothing here claims it."
            ),
            "output": "timed out",
        }
    except OSError as exception:
        return {
            "name": job["name"],
            "state": "refused",
            "reason": f"The toolkit could not be run: {exception}",
            "output": str(exception),
        }

    state, tx_id, reason = interpret(completed)
    return {
        "name": job["name"],
        "state": state,
        "tx_id": tx_id,
        "reason": reason,
        "output": ((completed.stdout or "") + (completed.stderr or ""))[:8000],
    }


def drain(toolkit, dry_run=False):
    try:
        # A dry run peeks. Claiming would mark the award attempted and leave
        # it stranded -- pending forever, with nothing ever written for it.
        jobs = call(
            "cryptopos.api.claim_awards",
            {"limit": 5, "peek": 1 if dry_run else 0},
        ) or []
    except (urllib.error.URLError, OSError) as exception:
        print(f"  could not reach cryptopos: {exception}", file=sys.stderr)
        return 0

    if not jobs:
        return 0

    for job in jobs:
        print(f"  {job['name']}  {job['points']:,} points -> {job['account'][:24]}…")
        result = drain_one(job, toolkit, dry_run=dry_run)
        if result is None:
            continue
        try:
            reported = call("cryptopos.api.report_award", result)
            print(f"    {result['state']}  {reported}")
        except (urllib.error.URLError, OSError) as exception:
            # The award may have been written on chain. Losing the report is
            # bad but is not a reason to write it a second time, so this
            # never retries the mint -- it only reports the loss.
            print(
                f"    WROTE BUT COULD NOT REPORT {job['name']}: {exception}",
                file=sys.stderr,
            )
    return len(jobs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="drain once and exit")
    parser.add_argument("--dry-run", action="store_true", help="write nothing")
    parser.add_argument("--toolkit", default=DEFAULT_TOOLKIT)
    args = parser.parse_args()

    if not args.toolkit:
        print("no toolkit: set CRYPTOPOS_TOOLKIT, put `toolkit` on PATH, or"
              " pass --toolkit. Nothing was drained.", file=sys.stderr)
        return 2
    if not os.path.exists(args.toolkit):
        print(f"toolkit not found at {args.toolkit}", file=sys.stderr)
        return 2
    if not (KEY and SECRET) and not args.dry_run:
        print("set CRYPTOPOS_KEY and CRYPTOPOS_SECRET", file=sys.stderr)
        return 2

    while True:
        count = drain(args.toolkit, dry_run=args.dry_run)
        if args.once:
            print(f"drained {count}")
            return 0
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
