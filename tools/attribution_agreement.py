"""Do all three attribution consumers answer the same question the same way?

    python3 tools/attribution_agreement.py

**The class of defect this exists for.** Three programs independently answer
"how much of this transaction belongs to this sale": the rail
(`cryptopos-rail-solana`) decides what to credit a customer, the reconciler
(`tender-apps/apps/settled.py`) decides whether the books agree with the chain,
and the checkout terminal watched the payment at the till.
The terminal is the copy `DECISIONS.md` D35 caught after it missed the D33 fix.
They are deliberately separate — a reconciliation that shares an implementation
with the thing it reconciles proves nothing — and that same separation means a
fix to one silently leaves the others wrong.

It has now happened twice in one day. `DECISIONS.md` D33 corrected the rail to
credit only referenced transfer instructions. The reconciler was then updated
*to apply D33* and still credited one 100-lamport transfer naming two sales'
references to **both** of them, because the multi-reference rejection was left
out of the copy. Nothing compared them, so nothing objected.

**What this is, and what it is not.** It is not shared code — that would destroy
the independence which makes the reconciliation worth running. It is a shared
set of **examples**, four of them real transactions read off devnet, with the
amount each one actually paid. All three implementations run over the same
vectors and must agree with the recorded answer and with each other.

When attribution changes, change the vector first. Then all three consumers
have something that fails.
"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT.parent
VECTORS = ROOT / "packages" / "cryptopos-rail-solana" / "tests" / "attribution_vectors.json"
RECONCILER = WORKSPACE / "tender-apps" / "apps" / "settled.py"
TERMINAL = WORKSPACE / "Point of Sale"

sys.path.insert(0, str(ROOT / "packages" / "cryptopos-core" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "cryptopos-rail-solana" / "src"))

from cryptopos_rail_solana import solana_devnet_sol as rail


def reconciler():
    """The tender-apps reconciler, or None when that repository is not here.

    Same reasoning as `terminal()`: another repository, optional, and its
    absence is reported rather than raised.
    """
    if not RECONCILER.is_file():
        return None
    sys.path.insert(0, str(WORKSPACE / "tender-apps" / "site-packages"))
    spec = importlib.util.spec_from_file_location("settled_under_test", RECONCILER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def terminal():
    """The checkout terminal, or None when its repository is not beside this one.

    RETIRED 2026-09-04, and this returns None rather than raising because the
    point of this tool is that three implementations agree. Losing one does not
    make the remaining comparison worthless -- it makes it a two-way comparison,
    which is worth saying out loud rather than crashing over.
    """
    if not TERMINAL.is_dir():
        return None
    sys.path.insert(0, str(TERMINAL))
    import watchers
    return watchers


def main():
    document = json.loads(VECTORS.read_text())
    vectors = document["vectors"]
    other = reconciler()
    checkout = terminal()

    present = ["the rail"]
    present += ["the reconciler"] if other else []
    present += ["the terminal"] if checkout else []
    for name, source in (("the reconciler", RECONCILER), ("the terminal", TERMINAL)):
        if name not in present:
            print(f"ABSENT: {name} ({source}) is not beside this repository,"
                  f" so it is not part of this comparison.")
    print(f"{len(vectors)} vectors, {sum(1 for v in vectors if v['expected_lamports'] is None)} "
          f"of which must be refused by {'all ' if len(present) > 2 else 'both ' if len(present) == 2 else ''}"
          f"{len(present)} consumer(s): {', '.join(present)}\n")
    failures = 0

    for vector in vectors:
        transaction = vector["transaction"]
        recipient, reference = vector["recipient"], vector["reference"]
        expected = vector["expected_lamports"]

        parsed, reason = rail._transaction_amount(
            transaction, recipient, reference, transaction["slot"])
        from_rail = None if parsed is None else parsed[0]

        answers = {"rail": from_rail}
        if other is not None:
            other._rpc = lambda *arguments, _tx=transaction: _tx
            answers["reconciler"] = other._solana_credit(
                "signature", recipient, reference)
        if checkout is not None:
            answers["terminal"], _terminal_reason = checkout._solana_lamport_credit(
                transaction, transaction["meta"], recipient, reference)

        # ONE CONSUMER CANNOT DISAGREE WITH ITSELF. With the others retired the
        # comparison collapses to "is the rail right", which is still worth
        # asserting -- but calling that agreement would be the overclaim this
        # tool exists to catch.
        agree = len(set(answers.values())) == 1
        correct = all(answer == expected for answer in answers.values())
        ok = agree and correct
        failures += 0 if ok else 1

        print(f"  {'PASS' if ok else 'FAIL'}  {vector['name']}")
        print("        " + " · ".join(f"{k} {v!r}" for k, v in answers.items())
              + f" · expected {expected!r}")
        if not agree:
            print("        THEY DISAGREE — one transaction, different answers")
        elif not correct:
            print(f"        every consumer present agrees and every one is"
                  f" wrong: {vector['why']}")
        if from_rail is None and reason:
            print(f"        rail's reason: {reason}")

    print(f"\n{len(vectors) - failures}/{len(vectors)} vectors agree with the record and each other")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
