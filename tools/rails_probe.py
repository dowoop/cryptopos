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


def _binds_without_derivation(rail):
    """Whether this rail's adapter binds a payment by something other than the
    address it arrives at.

    **A static address is only D5's problem when attribution depends on it.**
    Solana Pay puts a fresh `reference` account on every transfer and the rail
    watches that, so two sales to one merchant address are still told apart with
    no ambiguity — the money is bound, the address is merely where it lands.
    Reporting that rail as "shared, attribution is not a payment binding" would
    tell an operator the opposite of the truth about the safest rail they have.

    Asked of the ADAPTER, never of the row: `binding_text` is a free-text field
    an operator can type anything into, and a probe that reads its verdict off
    an editable string is a probe that can be talked into agreeing. The binding
    category is part of PaymentRail, so installed plugins make the same required
    declaration as built-ins.
    """
    from cryptopos import catalog
    from cryptopos_core.plugin import UNCONDITIONAL_PER_SALE

    try:
        adapter = catalog.plugins().get((rail.get("catalog_key") or "").strip())
    except Exception:
        return False
    return catalog.declared_binding_category(adapter) == UNCONDITIONAL_PER_SALE


def _binding(rail):
    """How this rail identifies a payment, in the terms D5 and D7 use."""
    xpub = (rail.get("testnet_xpub") or "").strip()
    recipient = (rail.get("testnet_recipient") or "").strip()
    if xpub:
        return "per-sale", f"xpub {xpub[:14]}…  index {rail.get('next_address_index')}"
    if recipient and _binds_without_derivation(rail):
        return "per-sale", f"{recipient} + a fresh per-sale reference"
    if recipient:
        return "shared", recipient
    return "none", "-"


def _plugins_section():
    """Which adapters this deployment can drive, and which it refused.

    Added 2026-08-25 with entry-point discovery. An operator who installs a
    rail wheel and cannot find the rail needs to be told whether it was never
    discovered or discovered and refused -- those are different problems with
    different fixes, and a probe that shows neither leaves them guessing.
    """
    from cryptopos import catalog

    adapters = catalog.plugins()
    refused = catalog.refused_plugins()
    described = catalog.described_rails()

    # Classified by who PROVIDES each key, not by which keys `builtin_rails()`
    # happens to name. A plugin can take over a key the package also lists as a
    # placeholder, so `set(adapters) - {builtin keys}` counts it as built in and
    # reports "0 installed" beside a rail that plainly was. `adapter_identity`
    # is the registry's own answer to the question.
    external = sorted(k for k in adapters if catalog.adapter_identity(k) != "builtin")

    print(f"  {len(adapters)} adapters can be driven"
          f" ({len(adapters) - len(external)} built in,"
          f" {len(external)} installed), {len(described)} known and not driveable")
    for key in external:
        print(f"    installed  {key}  <- {catalog.adapter_identity(key)}")
    for name, why in sorted(refused.items()):
        print(f"    REFUSED    {name}: {why}")
    return [f"entry point {name!r} advertised a rail and was refused: {why}"
            for name, why in sorted(refused.items())]


def run():
    """Returns the number of problems found. Zero is a clean deployment."""
    plugin_problems = _plugins_section()
    print()
    rails = frappe.get_all(
        "Crypto Rail",
        fields=["name", "label", "enabled", "testnet_xpub", "testnet_recipient",
                "next_address_index", "catalog_key"],
        order_by="name")

    problems = list(plugin_problems)
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
            # Group by (catalog_key, address). The FIRST version of this grouped
            # by (chain, address) and reported `eth` and `usdc-eth` colliding on
            # Sepolia. They do not, and the difference matters because a gate
            # that cries wolf is worse than no gate.
            #
            # Reproduced in `evm.py`: the native observer takes a transaction
            # only when `to == recipient` AND `value != 0`, and a USDC transfer
            # has `to` = the token contract and `value` = 0. The token observer
            # queries `eth_getLogs` with `"address": self.token_contract` and
            # re-checks every log against it. So the two rails observe disjoint
            # transaction shapes at the same address and cannot take each
            # other's payments.
            #
            # `catalog_key` already encodes chain and asset -- `native:eth` vs
            # `erc20:0x1c7d...` -- so two rails collide exactly when their
            # catalog_key and their recipient are both the same.
            key = rail.get("catalog_key") or "?"
            shared_by_address.setdefault((key, detail), []).append(rail.name)
        elif kind == "none":
            problems.append(
                f"{rail.name} is enabled with neither an xpub nor a recipient")

    print()
    for (key, address), names in sorted(shared_by_address.items()):
        if len(names) > 1:
            problems.append(
                f"{len(names)} enabled rails are the same asset at {address} "
                f"({key}): {', '.join(names)} — one payment, several claimants")
        else:
            problems.append(
                f"{names[0]} receives at a static address ({address}) — D5 says "
                "attribution there is not a payment binding, whatever else shares it")

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


def digest():
    """One line naming every driveable rail and who provides it.

    For comparing one container against another. The Frappe stack runs four
    processes with FOUR SEPARATE Python environments, so `pip install` of a rail
    plugin reaches exactly the one it was run in -- and a deployment where
    `backend` can charge on a rail the queue workers cannot watch will sell,
    take the money, and never credit it. That happened here on 2026-08-25 with a
    real payment (DECISIONS.md D31).

    Print it in every container and compare. Identical lines mean the four
    processes agree about what money this terminal can take.
    """
    from cryptopos import catalog

    parts = [f"{key}={catalog.adapter_identity(key)}" for key in sorted(catalog.plugins())]
    return " | ".join(parts)


if __name__ == "__main__":
    import sys
    frappe.init(site="erp.localhost")
    frappe.connect()
    try:
        if "--digest" in sys.argv:
            print(digest())
            sys.exit(0)
        sys.exit(1 if run() else 0)
    finally:
        frappe.destroy()
