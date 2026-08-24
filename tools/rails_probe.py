"""What this deployment's rails actually are, and whether any two collide.

D11 finding 6: "EVM off" is an operational wish, not an invariant. `install.py`
seeds every rail `enabled=1`, and migration deliberately never rewrites an
existing rail's flag, so one missed deployment step leaves a rail on that the
operator believed was off. Measured on the running instance the same day: all
four enabled, and **all three EVM rails sharing one recipient address** — D5's
precondition, three times over.

That is detectable without waiting for a payment, so it should not be discovered
by one. This prints what is enabled, how each rail receives, and refuses if two
enabled rails share a receiving address.

    bench --site erp.localhost execute cryptopos.tools.rails_probe.run

or, from the backend container:

    cd sites && ../env/bin/python ../apps/cryptopos/tools/rails_probe.py

Read-only. It changes nothing.
"""

import frappe


def _binding(rail):
    """How this rail identifies a payment, in the terms D5 and D7 use."""
    xpub = (rail.get("testnet_xpub") or "").strip()
    recipient = (rail.get("testnet_recipient") or "").strip()
    if xpub:
        return "per-sale", f"xpub {xpub[:14]}…  index {rail.get('next_address_index')}"
    if recipient:
        return "shared", recipient
    return "none", "-"


def run():
    """Returns the number of problems found. Zero is a clean deployment."""
    rails = frappe.get_all(
        "Crypto Rail",
        fields=["name", "label", "enabled", "testnet_xpub", "testnet_recipient",
                "next_address_index", "catalog_key"],
        order_by="name")

    problems = []
    shared_by_address = {}

    print(f"  {'rail':<10} {'on':<3} {'binding':<9} how it receives")
    print(f"  {'-' * 10} {'-' * 3} {'-' * 9} {'-' * 40}")
    for rail in rails:
        kind, detail = _binding(rail)
        flag = "yes" if rail.enabled else "no"
        print(f"  {rail.name:<10} {flag:<3} {kind:<9} {detail}")
        if not rail.enabled:
            continue
        if kind == "shared":
            # Group by (chain, address), not by address. One address is valid on
            # every EVM chain, and two rails holding it on DIFFERENT chains are
            # watching different ledgers -- that is not D5's collision. Two on
            # the SAME chain are.
            chain = (rail.get("catalog_key") or "").split("/")[0] or "?"
            shared_by_address.setdefault((chain, detail), []).append(rail.name)
        elif kind == "none":
            problems.append(
                f"{rail.name} is enabled with neither an xpub nor a recipient")

    print()
    for (chain, address), names in sorted(shared_by_address.items()):
        if len(names) > 1:
            problems.append(
                f"{len(names)} enabled rails share {address} on {chain}: "
                f"{', '.join(names)} — one payment, several claimants")
        else:
            problems.append(
                f"{names[0]} receives at a static address on {chain} "
                f"({address}) — D5 says attribution there is not a binding")

    if not problems:
        print("  OK: every enabled rail binds payments to a fresh address per sale.")
        return 0

    for problem in problems:
        print(f"  PROBLEM: {problem}")
    print()
    print("  See DECISIONS.md D5 (a shared address cannot be made safe by")
    print("  bookkeeping), D7 (what Bitcoin does instead) and D9 (why the EVM")
    print("  rails do not). A public deployment must resolve every line above.")
    return len(problems)


if __name__ == "__main__":
    import sys
    frappe.init(site="erp.localhost")
    frappe.connect()
    try:
        sys.exit(1 if run() else 0)
    finally:
        frappe.destroy()
