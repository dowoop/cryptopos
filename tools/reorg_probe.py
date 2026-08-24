"""Does every booked sale's transaction still exist on the chain?

DECISIONS D15: the EVM adapter's `_finalized_tip` returns `None` and `_is_mature`
gates on three confirmations, so a Sepolia sale can be settled and booked into
ERPNext and then have its block reorganised away. `may_book` checks a positive
credited amount and the presence of a transaction id -- never that the block
survived. Nothing in the application ever looks again.

So look. For every sale that reached a booked or settled state, ask the chain
whether its recorded `tx_id` is still there and still confirmed. A sale whose
transaction has vanished is an ERPNext invoice with no payment behind it.

    bench --site erp.localhost execute cryptopos.tools.reorg_probe.run

or, from the backend container:

    cd sites && ../env/bin/python ../apps/cryptopos/tools/reorg_probe.py

Read-only, against the endpoint each rail is configured with. It changes
nothing, and it does not settle, unsettle or reopen anything -- D10 stands.
"""

import json
import urllib.error
import urllib.request

import frappe


_AGENT = {"User-Agent": "cryptopos-reorg-probe/1.0"}


def _esplora_confirmed(endpoint, tx_id):
    """(known, confirmed) for a Bitcoin-family transaction."""
    request = urllib.request.Request(
        f"{endpoint.rstrip('/')}/tx/{tx_id}", headers=_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False, False
        raise
    return True, bool(body.get("status", {}).get("confirmed"))


def _evm_confirmed(endpoint, tx_id):
    """(known, confirmed) for an EVM transaction, by its receipt."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "method": "eth_getTransactionReceipt",
                       "params": [tx_id]}).encode()
    request = urllib.request.Request(
        endpoint, data=body,
        headers={**_AGENT, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=25) as response:
        result = json.load(response).get("result")
    if result is None:
        # No receipt: either never mined, or mined into a block that is no
        # longer canonical. Both mean the booking has nothing behind it now.
        return False, False
    return True, result.get("status") == "0x1"


def run():
    """Returns the number of booked sales whose transaction is missing."""
    sales = frappe.get_all(
        "Crypto Sale",
        filters={"tx_id": ["is", "set"]},
        fields=["name", "state", "end_kind", "tx_id", "rail_key",
                "sales_invoice", "credited_native"],
        order_by="creation desc")

    if not sales:
        print("  no sale carries a transaction id yet; nothing to check")
        return 0

    endpoints = {}
    for rail in frappe.get_all("Crypto Rail",
                               fields=["name", "testnet_url", "testnet_transport"]):
        endpoints[rail.name] = (rail.testnet_url, rail.testnet_transport)

    missing = 0
    checked = 0
    for sale in sales:
        endpoint, transport = endpoints.get(sale.rail_key, (None, None))
        if not endpoint:
            print(f"  {sale.name}: rail {sale.rail_key} has no endpoint — skipped")
            continue
        try:
            if transport == "esplora-rest":
                known, confirmed = _esplora_confirmed(endpoint, sale.tx_id)
            else:
                known, confirmed = _evm_confirmed(endpoint, sale.tx_id)
        except Exception as failure:
            print(f"  {sale.name}: could not ask the chain — {failure}")
            continue

        checked += 1
        if known and confirmed:
            continue
        missing += 1
        why = "unknown to the node" if not known else "known but not confirmed"
        invoice = sale.sales_invoice or "(not booked)"
        print(f"  PROBLEM {sale.name}: tx {sale.tx_id} is {why}")
        print(f"          state={sale.state} end={sale.end_kind} invoice={invoice}")

    print()
    print(f"  checked {checked} sale(s) carrying a transaction id")
    if missing:
        print(f"  {missing} have no live transaction behind them — see DECISIONS.md D15.")
        print("  This probe does not correct them: D10 says a terminal state is")
        print("  not edited afterwards, and a correction is a human decision.")
    else:
        print("  OK: every recorded transaction is still on its chain and confirmed.")
    return missing


if __name__ == "__main__":
    import sys
    frappe.init(site="erp.localhost")
    frappe.connect()
    try:
        sys.exit(1 if run() else 0)
    finally:
        frappe.destroy()
