"""Seed data — the rails the terminal knows about.

Values carried over from the tkinter terminal's rails.py, including the
maturity notes. A rail that says "works" and a rail that says "partial" are
making different promises to the operator, and dropping the distinction on
the way across would be the first overclaim of the port.
"""

import frappe

RAILS = [
	{
		"rail_key": "btc",
		"label": "Bitcoin / BTC",
		"chain": "Bitcoin",
		"asset": "BTC",
		"family": "bitcoin",
		"unit_name": "satoshi",
		"native_decimals": 8,
		"display_decimals": 8,
		"gate_confs": 3,
		"testnet_gate_confs": 1,
		"gate_text": "confs >= 3 (mainnet; testnet settles at 1)",
		"binding_text": "shared address + exact-amount match in the lock window",
		"maturity": "partial",
		"maturity_note": (
			"real testnet4 reads against a shared configured address; no HD "
			"derivation yet, so binding is by amount rather than per-sale"
		),
		"testnet_url": "https://mempool.space/testnet4/api",
		"testnet_transport": "esplora-rest",
		"testnet_name": "testnet4",
		"live_url": "",
		"real_transport": "Electrum/Esplora WS + RPC polling (no free public JSON-RPC)",
		"sim_block_seconds": 15,
		"enabled": 1,
	}
]


# The desk navigation is NOT built here. Frappe v16 draws the sidebar from
# Workspace Sidebar records, and `frappe.model.sync.sync_for` imports them from
# `<app>/workspace_sidebar/*.json` on both install and migrate -- the same
# declarative path frappe, erpnext and hrms all ship. `workspace_sidebar/`
# holds ours. The earlier fix for the missing sidebar called the framework's
# one-time bootstrap generator from an after_migrate hook instead, which built
# the row at runtime: it left the only unversioned sidebar on the site, gave
# two items the same idx, dropped the per-item icons, and swept every public
# workspace on the site rather than this app's.


def after_install():
	for rail in RAILS:
		if frappe.db.exists("Crypto Rail", rail["rail_key"]):
			continue
		doc = frappe.new_doc("Crypto Rail")
		doc.update(rail)
		doc.insert(ignore_permissions=True)

	settings = frappe.get_single("CryptoPoS Settings")
	if not settings.merchant_name:
		settings.merchant_name = "CryptoPoS Terminal"
		settings.mode = "testnet"
		settings.chain_reference = 1
		settings.save(ignore_permissions=True)

	frappe.db.commit()
