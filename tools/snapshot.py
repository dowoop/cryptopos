"""The whole deployment in fifteen lines. Read-only.

    cd sites && ../env/bin/python ../apps/cryptopos/tools/snapshot.py

**Why this exists.** Every session that touches this stack starts by asking the
same four questions — which rails are on, how many sales are in what state, is
there money that settled and never booked, and what happened last — and answers
them by writing a `frappe` heredoc and reading forty lines of output. Four times
over, that is most of a session's context spent re-learning what a `SELECT`
already knows.

It is deliberately dense and deliberately not configurable. A snapshot with
options is a snapshot somebody has to read the flags of first.

Every identifier it prints is real and can be copied. That matters more than it
sounds: sale names, invoice names, addresses and transaction ids are exactly the
strings that cannot be produced from memory, and the failure mode of guessing
one is a plausible string that no gate objects to. Copy from here; do not type.
`tools/idcheck.py` is the other half — it says whether a string you are holding
is whole.
"""

import frappe

# READ FROM THE SCHEMA, never typed. The first version of this line enumerated
# seven states from memory and omitted `idle` -- the doctype's own default -- so
# an idle sale vanished from the breakdown AND from the total, and the total is
# the number a reader trusts most. A snapshot tool that quietly under-counts is
# worse than no snapshot.
IN_FLIGHT = ("awaiting", "detected", "confirming")


def states():
    field = frappe.get_meta("Crypto Sale").get_field("state")
    return tuple(option for option in (field.options or "").split("\n") if option)


def run():
    from cryptopos import catalog

    settings = frappe.get_single("CryptoPoS Settings")
    rails = frappe.get_all(
        "Crypto Rail", filters={"enabled": 1},
        fields=["name", "catalog_key", "testnet_xpub", "testnet_recipient", "unit_name"],
        order_by="name")
    adapters = catalog.plugins()
    external = [key for key in adapters if catalog.adapter_identity(key) != "builtin"]

    print(f"site     {frappe.local.site}   mode {settings.mode or 'testnet'}")
    print("         THIS PROCESS ONLY. The four Frappe containers have separate")
    print("         Python environments — run erpnext-hr/rails_agree.sh before charging.")
    print(f"adapters {len(adapters)} discovered ({len(adapters) - len(external)} builtin,"
          f" {len(external)} installed), {len(catalog.described_rails())} described")
    print("         'discovered' is not 'reachable': no readiness call is made here.")
    for key in external:
        print(f"         installed: {key}  <- {catalog.adapter_identity(key)}")
    for refusal, why in sorted(catalog.refused_plugins().items()):
        print(f"         REFUSED:   {refusal}: {why[:70]}")

    binding = []
    from cryptopos_core.plugin import UNCONDITIONAL_PER_SALE

    for rail in rails:
        adapter = adapters.get((rail.get("catalog_key") or "").strip())
        # The binding category is a CLAIM the adapter makes about itself, and
        # one such claim was false for an hour on 2026-08-25 (D33). A derived
        # address is a fact; a declaration is an assertion. They are printed
        # differently on purpose.
        if (rail.get("testnet_xpub") or "").strip():
            binding.append(f"{rail['name']} per-sale")
        elif catalog.declared_binding_category(adapter) == UNCONDITIONAL_PER_SALE:
            binding.append(f"{rail['name']} per-sale(claimed)")
        else:
            binding.append(f"{rail['name']} SHARED")
    print(f"rails    {len(rails)} enabled: {' · '.join(binding)}")

    counts = {}
    for state in states():
        found = frappe.db.count("Crypto Sale", {"state": state})
        if found:
            counts[state] = found
    total = sum(counts.values())
    in_flight = sum(counts.get(state, 0) for state in IN_FLIGHT)
    print(f"sales    {total} total: "
          + ", ".join(f"{count} {state}" for state, count in sorted(counts.items()))
          + f"   ({in_flight} in flight)")

    # ASK THE DOCUMENT, do not re-derive the rule. `may_book()` is a five-term
    # gate -- state, provenance, mode, bound credit, and a known receiving
    # identity -- and the first version of this tool approximated it as "has a
    # tx id". That reports a simulated harness sale as bookable, which is money
    # the application itself refuses to book. A summary that disagrees with the
    # thing it summarises is the defect this tool was written against.
    unbooked = frappe.get_all(
        "Crypto Sale", filters={"state": "confirmed", "sales_invoice": ["is", "not set"]},
        fields=["name", "usd_cents"])
    verdicts = []
    for row in unbooked:
        ok, reason = frappe.get_doc("Crypto Sale", row["name"]).may_book()
        verdicts.append((row, ok, reason))
    cents = sum(row["usd_cents"] or 0 for row, _ok, _why in verdicts)
    bookable = [row for row, ok, _why in verdicts if ok]
    print(f"unbooked {len(unbooked)} settled sales carry no invoice"
          f" (${cents / 100:.2f}, {len(bookable)} bookable by may_book())")
    for row, ok, reason in verdicts[:3]:
        print(f"         {row['name']}  ${(row['usd_cents'] or 0) / 100:.2f}"
              f"  {'bookable' if ok else 'NOT bookable: ' + reason}")

    recent = frappe.get_all(
        "Crypto Sale", filters={"state": "confirmed"},
        fields=["name", "rail_key", "credited_native", "sales_invoice", "tx_id", "settled_at"],
        order_by="settled_at desc", limit=3)
    print("last     the three most recent settled sales, ids as they really are:")
    for row in recent:
        print(f"         {row['name']}  {row['rail_key']:<9} {row['credited_native']:>20}"
              f"  {row['sales_invoice'] or '(unbooked)'}")
        print(f"           tx {row['tx_id']}")
    return 0


if __name__ == "__main__":
    frappe.init(site="erp.localhost")
    frappe.connect()
    try:
        run()
    finally:
        frappe.destroy()
