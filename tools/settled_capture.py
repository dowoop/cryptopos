"""Every confirmed sale and what the books say it credited. Read-only.

**Why this exists.** `tender-apps/apps/settled.py` reconciles this deployment's
books against the chains that carried the money, and it is the only thing that
checks "the books agree with the chain" — the claim a point-of-sale has to be
able to make. It ran against a fixture somebody captured by hand, once, and a
hand capture is a claim no gate reads: on 2026-08-25 that fixture said it held
*every* confirmed sale and was missing two — `CPS-2026-00285` and
`CPS-2026-00286`, both real, both settled the previous evening, both agreeing
with the chain to the unit. Nothing was wrong with the arithmetic. The
population was wrong, and nothing could have noticed.

So the capture is a probe like `rails_probe` and `reorg_probe`, and the operator
runs it whenever they want the question re-answered.

    cd sites && ../env/bin/python ../apps/cryptopos/tools/settled_capture.py > books.json

    bench --site erp.localhost execute cryptopos.tools.settled_capture.run

**This half reads the books and nothing else.** It opens no socket. The chain
half belongs to `settled.py --capture`, which reads the transactions fresh over
the network. That split is the point: a reconciliation whose two sides are read
by one process in one pass agrees with itself by construction and proves
nothing. Two sources, read separately, is what makes the comparison mean
something.

**`tender` is not imported here and must not be.** It stays out of the
containers by decision; this emits plain JSON and the arithmetic happens on the
other side of that boundary.
"""

import json
import re
import sys
import urllib.parse
from datetime import UTC, datetime

import frappe

# THIS FILE READS ONLY THE HEADLINE `tx_id`, and so does the reconciliation
# built on it. cryptopos/watch.py:55-63 says a settlement can credit several
# transactions, stores all of them in watch_scratch's `settled_tx_ids`, and
# calls `tx_id` merely "the one a human quotes". A sale settled from two
# transactions is therefore captured as one, and a reconciliation that agrees
# with the chain about that one agrees about a part. No sale on this instance
# has settled from more than one transaction yet -- measured 2026-08-28, 0 of
# 29 -- so this is a blind spot, not a wrong number. tools/reorg_probe_core.py
# reads the whole list; this file has not been changed to match.

# A chain transaction id, in the shapes this deployment's rails produce.
# Anything else in `tx_id` was put there by a harness, and a harness fixture in
# a reconciliation of real money is worse than a missing one: it agrees.
TX_SHAPES = (
    ("evm", re.compile(r"^0x[0-9a-fA-F]{64}$")),
    ("btc", re.compile(r"^[0-9a-fA-F]{64}$")),
    # A Solana signature is 64 bytes in base58 — 86 to 88 characters, in an
    # alphabet that deliberately omits 0, O, I and l. It was missing from the
    # first draft of this list, and the first real Solana sale was excluded as
    # "a harness wrote it" (CPS-2026-00328, 2026-08-25). The guard behaved
    # correctly and its rule was wrong, which is the failure this whole file
    # exists to make visible rather than silent: an enumerated list of two
    # chains' id shapes is a reconciler that stops covering the shop the moment
    # its operator adds a third.
    ("solana", re.compile(r"^[1-9A-HJ-NP-Za-km-z]{86,88}$")),
)

WHAT = (
    "Every confirmed Crypto Sale on this ERPNext instance that carries a "
    "chain-shaped transaction id, paired with what the books say it credited. "
    "Captured read-only by cryptopos/tools/settled_capture.py; no key signed "
    "anything and no chain was read. Sales whose tx id is not chain-shaped are "
    "excluded and named in `excluded` rather than dropped in silence."
)


def _shape(tx_id):
    """Which chain's id shape this is, or None if it is not one."""
    for name, pattern in TX_SHAPES:
        if pattern.match(tx_id or ""):
            return name
    return None


def capture():
    """The books, as a JSON-ready dict. Reads; changes nothing."""
    rows = frappe.get_all(
        "Crypto Sale",
        filters={"state": "confirmed"},
        fields=["name", "rail_key", "sales_invoice", "credited_native", "tx_id",
                "identity_address", "settled_at", "uri"],
        order_by="name")

    # The rail row is where `native_decimals` lives, and D26 made it
    # authoritative: `charge()` refuses if it disagrees with the adapter. So the
    # exponent travels with the sale, and the reconciler on the other side does
    # not have to know this deployment's rails in advance — which is what lets
    # an operator add an asset and still be able to check their own books.
    rails = {r["rail_key"]: r for r in frappe.get_all(
        "Crypto Rail",
        fields=["rail_key", "native_decimals", "unit_name", "asset", "chain"])}

    sales, excluded = [], []
    for row in rows:
        shape = _shape(row["tx_id"])
        if shape is None:
            excluded.append({
                "sale": row["name"],
                "rail": row["rail_key"],
                "tx": row["tx_id"],
                "why": "tx id is not chain-shaped — a harness wrote it",
            })
            continue
        if not row["sales_invoice"]:
            excluded.append({
                "sale": row["name"],
                "rail": row["rail_key"],
                "tx": row["tx_id"],
                "why": "confirmed with a chain tx and no Sales Invoice — money "
                       "that settled and never booked; reconcile.late_payments "
                       "is where this belongs, not a books-vs-chain fixture",
            })
            continue
        rail = rails.get(row["rail_key"])
        if rail is None:
            excluded.append({
                "sale": row["name"],
                "rail": row["rail_key"],
                "tx": row["tx_id"],
                "why": "no Crypto Rail row — the exponent its amount is counted "
                       "in cannot be established, and guessing one is how a "
                       "number becomes wrong by a factor of ten thousand",
            })
            continue
        # What the payment request bound this sale with, when the rail binds
        # with anything beyond the address. The reconciler on the other side has
        # to credit the same money the rail credited: after D33 the Solana rail
        # counts only transfers carrying THIS sale's reference, so a reconciler
        # reading the recipient's whole balance delta would report a mismatch on
        # a transaction that was attributed correctly. Read off the sale's own
        # URI rather than re-derived, so this stays true for any rail that binds.
        binding = {}
        query = urllib.parse.urlsplit(row["uri"] or "").query
        for field, values in urllib.parse.parse_qs(query).items():
            if field in ("reference", "memo", "spl-token") and values:
                binding[field] = values[0]

        sales.append({
            "sale": row["name"],
            "rail": row["rail_key"],
            "invoice": row["sales_invoice"],
            "booked_native": str(row["credited_native"]),
            # This capture still emits only the human-facing headline tx_id.
            # A settlement may actually credit every id in watch_scratch's
            # settled_tx_ids; reorg_probe checks that full set.  Expanding this
            # reconciler is deliberately outside the block-identity change.
            "tx": row["tx_id"],
            "address": row["identity_address"],
            "settled_at": str(row["settled_at"]),
            "native_decimals": rail["native_decimals"],
            "unit_name": rail["unit_name"],
            "binding": binding,
        })

    return {
        "captured_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "what": WHAT,
        "site": frappe.local.site,
        "confirmed_total": len(rows),
        "sales": sales,
        "excluded": excluded,
    }


def run():
    """Write the capture to stdout. Returns the number of sales captured."""
    books = capture()
    json.dump(books, sys.stdout, indent="\t")
    sys.stdout.write("\n")

    print(f"captured {len(books['sales'])} of {books['confirmed_total']} confirmed sales",
          file=sys.stderr)
    for row in books["excluded"]:
        print(f"  excluded {row['sale']} ({row['rail']}): {row['why']}", file=sys.stderr)
    return len(books["sales"])


if __name__ == "__main__":
    frappe.init(site="erp.localhost")
    frappe.connect()
    try:
        run()
    finally:
        frappe.destroy()
