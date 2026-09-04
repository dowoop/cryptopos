#!/usr/bin/env python3
"""Prove this deployment can take a testnet sale, start to finish.

    python3 bin/prove_end_to_end.py            # say what it would do, spend nothing
    python3 bin/prove_end_to_end.py --send     # actually charge, pay and verify

Charges a sale through the app, pays it with a real transaction, waits for
this app's own watcher to observe, settle and book it, and asserts every step.
The exit code decides.

**Why this exists, and why it is not `harness.py`.** On 2026-08-24 this
workspace discovered that no live sale had ever completed here -- fifty had
been taken and not one was a genuine charge -> pay -> settle -> book.
`expires_at_epoch` was nine hours in the past (`DECISIONS.md` D19), so every
payment made *now* arrived "after expiry" on every rail. Every suite was green
throughout, because every settlement fixture points at payments that are
genuinely days old, and a days-old block time compares fine against an expiry
nine hours behind.

`cryptopos.harness` drives the same path and settles BY HAND -- its own words:
"Nobody sends one." That is the right design for a suite, because it tests
everything that happens after a payment without spending anything. It is also
exactly why it could not have caught D19. This is the check that spends real
testnet money, which is why it refuses without `--send`.

**THE PAYER IS NOT IN THIS REPOSITORY AND MUST NOT BE.** This app is
watch-only: it holds no key, and a web application with a signing key in its
blast radius is a different risk from one without. So the wallet is named at
the command line or in `CRYPTOPOS_PAYER`, and this script drives it as a
subprocess. Any script accepting

    <payer> pay <agent> <destination> <amount> <reference>

will do. `catflix/payer/agent_wallet.py` is one, and was the one this was
rebuilt against on 2026-09-04 after the checkout that used to hold it was
retired.

Costs one sale's worth of testnet XTR. It is valueless by construction.
"""

import argparse
import json
import os
import subprocess
import sys
import time

CONTAINER = os.environ.get("CRYPTOPOS_CONTAINER", "frappe_docker-backend-1")
SITE = os.environ.get("CRYPTOPOS_SITE", "erp.localhost")
PAYER = os.environ.get("CRYPTOPOS_PAYER", "")

# Ootle finality is BFT and measured at a 58.7 s cycle, so three minutes is
# generous rather than hopeful. The poll is slow on purpose: this waits on a
# public indexer, and hammering it proves nothing faster.
SETTLE_TIMEOUT_SECONDS = 240
POLL_SECONDS = 10

_CHARGE = """
import frappe, json
frappe.init(site="{site}"); frappe.connect()
from cryptopos import charge as charge_module
sale = charge_module.charge({cents}, "{rail}", "")
frappe.db.commit()
sale.reload()
print("@@" + json.dumps({{"name": sale.name}}))
frappe.destroy()
"""

_READ = """
import frappe, json
frappe.init(site="{site}"); frappe.connect()
sale = frappe.get_doc("Crypto Sale", "{name}")
print("@@" + json.dumps({{
    "name": sale.name,
    "state": sale.state,
    "end_kind": sale.end_kind,
    "rail_key": sale.rail_key,
    "identity_address": sale.identity_address,
    "identity_extras": sale.identity_extras,
    "invoiced_native": sale.invoiced_native,
    "credited_native": sale.credited_native,
    "invoice_ref": sale.invoice_ref,
    "tx_id": sale.tx_id,
    "sales_invoice": sale.sales_invoice,
}}))
frappe.destroy()
"""


def _in_container(script, container):
    """Run a Python snippet inside the bench, returning its `@@` payload."""
    result = subprocess.run(
        ["docker", "exec", "-i", container, "bash", "-lc",
         "cd /home/frappe/frappe-bench/sites && ../env/bin/python -"],
        input=script, capture_output=True, text=True,
    )
    for line in (result.stdout or "").splitlines():
        if line.startswith("@@"):
            return json.loads(line[2:]), result
    return None, result


def check(rule, condition, detail=""):
    """Assert one rule by name, so a failure says which promise broke."""
    print(f"  {'PASS' if condition else 'FAIL'}  {rule}")
    if detail:
        print(f"        {detail}")
    return bool(condition)


def reference_of(sale):
    """The sale reference a payment must NAME, from `identity_extras`.

    A plain transfer that does not carry it names no sale and is never
    credited, so this is the difference between paying and losing money.
    """
    extras = sale.get("identity_extras")
    if isinstance(extras, str):
        try:
            extras = json.loads(extras)
        except Exception:                              # noqa: BLE001 - total
            extras = {}
    if isinstance(extras, dict):
        # THE INTENT IS THE AUTHORITY. `invoice_ref` mirrors it and is easier
        # to reach, but the payment must name what the INTENT asked for -- if
        # those two ever disagree, the intent is the one the watcher matches
        # against, and paying the mirror would be paying the wrong string.
        intent = extras.get("intent")
        if isinstance(intent, dict) and intent.get("payment_reference"):
            return str(intent["payment_reference"])
        for key in ("payment_reference", "reference", "sale_ref"):
            if extras.get(key):
                return str(extras[key])
    return str(sale.get("invoice_ref") or "")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--send", action="store_true",
                        help="actually charge and pay; without it nothing is spent")
    parser.add_argument("--payer", default=PAYER,
                        help="a wallet script that can spend (or CRYPTOPOS_PAYER)")
    parser.add_argument("--agent", default="cryptopos-e2e",
                        help="the payer's identity to spend from")
    parser.add_argument("--rail", default="xtr")
    parser.add_argument("--cents", type=int, default=100)
    parser.add_argument("--container", default=CONTAINER)
    parser.add_argument("--site", default=SITE)
    args = parser.parse_args()
    container, site = args.container, args.site

    if not args.send:
        print(__doc__.split("**Why this exists")[0].strip())
        print(f"\nWould charge {args.cents} cents on {args.rail!r}, pay it from"
              f" {args.payer or '<no payer set>'} as {args.agent!r},"
              f" and wait up to {SETTLE_TIMEOUT_SECONDS}s for it to settle and book.")
        print("Nothing was spent. Pass --send to actually run it.")
        return 0

    # THE PAYER IS CHECKED BEFORE THE SALE IS CHARGED. Charging first would
    # leave a real open sale behind for a mistake this script could have
    # caught with no side effect at all.
    if not args.payer:
        print("no payer: set CRYPTOPOS_PAYER or pass --payer. This app is"
              " watch-only and holds no key, so it cannot pay its own sale.",
              file=sys.stderr)
        return 2
    if not os.path.isfile(args.payer):
        print(f"no payer script at {args.payer}", file=sys.stderr)
        return 2
    # ABSOLUTE, because the payer is run with its OWN directory as cwd -- it
    # resolves its toolkit relative to itself -- and a relative --payer would
    # then be resolved a second time, against that directory.
    payer = os.path.abspath(args.payer)

    print("1. charge")
    charged, result = _in_container(_CHARGE.format(
        site=site, cents=args.cents, rail=args.rail), container)
    if charged is None:
        print("        could not charge; the bench said:", file=sys.stderr)
        print((result.stderr or result.stdout or "")[-600:], file=sys.stderr)
        return 1
    name = charged["name"]
    ok = check("a sale was charged", bool(name), name)

    sale, _ = _in_container(_READ.format(site=site, name=name), container)
    if sale is None:
        print("        the sale could not be read back", file=sys.stderr)
        return 1
    destination = sale.get("identity_address") or ""
    reference = reference_of(sale)
    amount = sale.get("invoiced_native") or ""
    ok &= check("it names an address to pay", bool(destination), destination)
    ok &= check("it carries a reference a payment must name", bool(reference), reference)
    ok &= check("it has an invoiced amount", bool(amount), f"{amount} native units")
    if not ok:
        print("\nrefusing to pay a sale that is not fully formed.", file=sys.stderr)
        return 1

    print("\n2. pay")
    payment = subprocess.run(
        [sys.executable, payer, "pay", args.agent,
         destination, str(amount), reference],
        capture_output=True, text=True,
        cwd=os.path.dirname(payer),
    )
    paid = check("the payer accepted and broadcast",
                 payment.returncode == 0,
                 (payment.stdout or payment.stderr).strip().splitlines()[-1]
                 if (payment.stdout or payment.stderr).strip() else "")
    if not paid:
        print((payment.stderr or payment.stdout or "")[-600:], file=sys.stderr)
        return 1

    print(f"\n3. wait for THIS APP to observe, settle and book it"
          f" (up to {SETTLE_TIMEOUT_SECONDS}s)")
    deadline = time.time() + SETTLE_TIMEOUT_SECONDS
    final = sale
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        final, _ = _in_container(_READ.format(site=site, name=name), container)
        if final is None:
            continue
        print(f"        state={final.get('state')!r}"
              f" credited={final.get('credited_native')!r}"
              f" invoice={final.get('sales_invoice')!r}")
        if final.get("sales_invoice"):
            break

    # THE BOOKING IS THE PROOF, not the settlement. A sale can settle and fail
    # to book -- that is a defect this workspace has actually had -- so the
    # Sales Invoice is what decides, and `tx_id` is what makes it checkable
    # against the chain by somebody who does not trust this script.
    ok = check("it settled", bool(final and final.get("tx_id")),
               (final or {}).get("tx_id", ""))
    ok &= check("it credited what was invoiced",
                bool(final) and final.get("credited_native") == amount,
                f"credited {(final or {}).get('credited_native')!r},"
                f" invoiced {amount!r}")
    ok &= check("it booked a Sales Invoice",
                bool(final and final.get("sales_invoice")),
                (final or {}).get("sales_invoice", ""))

    print(f"\n{'PROVEN' if ok else 'NOT PROVEN'}: charge -> pay -> settle -> book"
          f" for {name}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
