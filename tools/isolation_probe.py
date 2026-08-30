"""Does one visitor's sale leak to another? Executed against a live site.

D11 finding 2 says the terminal's API has no concept of ownership:
`api.status(sale_name)` is `@frappe.whitelist()` with no role check and no
permission check, and `frappe.get_doc` does not check permissions by default.
That was first established by reading the source. This runs it.

It matters because step 1 of the hosted-demo plan (`GOAL.md`) is "decide what
'anyone' means", and one of the two answers -- a shop each -- is only possible
if this probe refuses. Today it does not.

    bench --site erp.localhost execute cryptopos.tools.isolation_probe.run

or, from the backend container:

    cd sites && ../env/bin/python ../apps/cryptopos/tools/isolation_probe.py

Creates a disposable user, reads somebody else's sale as that user, and removes
the user again. It writes nothing else and changes no sale.
"""

import frappe

PROBE_USER = "isolation-probe@example.invalid"


def _ensure_probe_user():
    if frappe.db.exists("User", PROBE_USER):
        return False
    user = frappe.new_doc("User")
    user.email = PROBE_USER
    user.first_name = "Isolation"
    user.last_name = "Probe"
    user.enabled = 1
    user.new_password = frappe.generate_hash(length=20)
    user.insert(ignore_permissions=True)
    # The role the terminal actually requires. A genuinely restricted custom
    # role cannot charge without code changes -- which is itself part of the
    # finding.
    user.add_roles("Sales User")
    frappe.db.commit()
    return True


def _remove_probe_user():
    if frappe.db.exists("User", PROBE_USER):
        frappe.delete_doc("User", PROBE_USER, force=True, ignore_permissions=True)
        frappe.db.commit()


def run():
    """Returns True if isolation holds, False if a sale leaked."""
    created = _ensure_probe_user()
    print(f"  probe user {PROBE_USER} ({'created' if created else 'reused'})")

    sales = frappe.get_all(
        "Crypto Sale", fields=["name", "owner"], limit=1, order_by="creation desc")
    if not sales:
        print("  no Crypto Sale exists to probe; charge one first")
        _remove_probe_user()
        return None
    target = sales[0]
    print(f"  target {target.name}, owned by {target.owner}")

    from cryptopos import api

    holds = None
    try:
        frappe.set_user(PROBE_USER)
        try:
            result = api.status(target.name)
        except frappe.PermissionError as refusal:
            print(f"  api.status() refused: {refusal}")
            print("  RESULT: isolation holds.")
            holds = True
        else:
            revealing = [
                key for key in ("uri", "identity_address", "invoiced_native",
                                "usd_cents", "tx_id", "sales_invoice")
                if result.get(key) not in (None, "", 0)
            ]
            print("  api.status() SUCCEEDED for a sale this user does not own")
            print(f"  fields returned with content: {revealing}")
            print("  RESULT: NO ISOLATION — see DECISIONS.md D11, finding 2.")
            holds = False
    finally:
        frappe.set_user("Administrator")
        _remove_probe_user()
    return holds


if __name__ == "__main__":
    frappe.init(site="erp.localhost")
    frappe.connect()
    try:
        run()
    finally:
        frappe.destroy()
