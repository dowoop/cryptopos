"""Seed data — the rails the terminal knows about.

Built from `cryptopos_core.rails`, not copied out of it. The table there is
the one description of what a chain counts in, what a human sees, and what
each rail promises; a second hand-typed copy in this file was a second thing
to keep in step, and the first to drift.

What this file adds is the part the package deliberately does not know:

  catalog_key      which concrete adapter drives the rail. A rail without
                   one can be described and cannot be charged.
  testnet_url      the endpoint this deployment reads through. A published
                   default, and an operator override sits above it.
  enabled          whether the operator has switched it on.

**Only rails whose adapter can do the whole job are seeded.** A rail that can
build a QR but cannot observe is request-ready, not charge-ready, and seeding
it would put a chargeable-looking button in front of a cashier for money the
terminal could never confirm arrived. Measured 2026-08-23, four adapters
answered with all four capabilities over public keyless endpoints; the rest
carry a `blocker` string in the catalog saying what is missing.
"""

import frappe

from cryptopos_core import rails as _rails

# rail_key -> (catalog key, endpoint, transport, network, enabled)
#
# The endpoint matters as much as the adapter. Three public Sepolia providers
# were measured and only `publicnode` supported observation; the other two
# answered `eth_chainId` and could not serve the log reads the watcher needs.
# So these are the ones proven to work, not the first ones found.
#
# **`btc` is seeded switched off, and that is a finding rather than an
# oversight.** Its adapter refuses `capture_baseline` on an address that has
# any transaction history, because a single paginated read of a reused
# address cannot tell old money from this sale's. That refusal is right: a
# payment broadcast for an earlier, expired sale and confirmed during this
# one's window is credited to this one, and the customer standing at the
# counter walks away without paying. The terminal has no per-sale address
# source yet, so the rail is described and not offered. See DECISIONS.md.
ADAPTERS = {
	"btc": (
		"bitcoin:testnet4/native:btc",
		"https://mempool.space/testnet4/api",
		"esplora-rest",
		"testnet4",
		0,
	),
	"eth": (
		"ethereum:sepolia/native:eth",
		"https://ethereum-sepolia-rpc.publicnode.com",
		"json-rpc",
		"sepolia",
		1,
	),
	"usdc-eth": (
		"ethereum:sepolia/erc20:0x1c7d4b196cb0c7b01d743fbc6116a902379c7238",
		"https://ethereum-sepolia-rpc.publicnode.com",
		"json-rpc",
		"sepolia",
		1,
	),
	"usdc-pol": (
		"polygon:amoy/erc20:0x41e94eb019c0762f9bfcf9fb1e58725bfb0e7582",
		"https://polygon-amoy-bor-rpc.publicnode.com",
		"json-rpc",
		"amoy",
		1,
	),
}


def rail_rows():
	"""Every seedable rail, as a `Crypto Rail` row."""
	rows = []
	for rail_key, (catalog_key, endpoint, transport, network, enabled) in ADAPTERS.items():
		rail = _rails.rail_for(rail_key)
		rows.append(
			{
				"rail_key": rail_key,
				"catalog_key": catalog_key,
				"label": rail["label"],
				"chain": rail["chain"],
				"asset": rail["asset"],
				"family": rail["family"],
				"unit_name": rail["unit_name"],
				"native_decimals": rail["native_decimals"],
				"display_decimals": rail["display_decimals"],
				# Polygon never counts confirmations -- it reads the
				# `finalized` tag -- so its rail carries no conf gate and the
				# gate_text is what says so.
				"gate_confs": rail["gate_confs"] or 0,
				"testnet_gate_confs": rail.get("testnet_gate_confs") or 0,
				"gate_text": rail["gate_text"],
				"binding_text": rail["binding"],
				"maturity": rail["maturity"],
				"maturity_note": rail["maturity_note"],
				"testnet_url": endpoint,
				"testnet_transport": transport,
				"testnet_name": network,
				"live_url": "",
				"real_transport": rail.get("real_transport", ""),
				"sim_block_seconds": rail.get("sim_block_seconds") or 15,
				# A rail with no receiving address refuses at charge time
				# anyway, which is the honest gate. `enabled` says something
				# narrower: whether this terminal is willing to offer the
				# rail at all.
				"enabled": enabled,
			}
		)
	return rows


# The desk navigation is NOT built here. Frappe v16 draws the sidebar from
# Workspace Sidebar records, and `frappe.model.sync.sync_for` imports them from
# `<app>/workspace_sidebar/*.json` on both install and migrate -- the same
# declarative path frappe, erpnext and hrms all ship. `workspace_sidebar/`
# holds ours. The earlier fix for the missing sidebar called the framework's
# one-time bootstrap generator from an after_migrate hook instead, which built
# the row at runtime: it left the only unversioned sidebar on the site, gave
# two items the same idx, dropped the per-item icons, and swept every public
# workspace on the site rather than this app's.


def seed_rails():
	"""Create any rail that is missing, and teach existing ones their adapter.

	Idempotent, and deliberately conservative on rails that already exist: a
	row an operator has edited keeps its endpoint and its enabled flag. Only
	`catalog_key` is filled in, because a rail with no adapter cannot be
	charged at all and leaving it blank would be leaving it broken.
	"""
	created = adopted = 0
	for row in rail_rows():
		if frappe.db.exists("Crypto Rail", row["rail_key"]):
			existing = frappe.get_doc("Crypto Rail", row["rail_key"])
			if not (existing.catalog_key or "").strip():
				existing.db_set("catalog_key", row["catalog_key"], update_modified=False)
				adopted += 1
			continue
		doc = frappe.new_doc("Crypto Rail")
		doc.update(row)
		doc.insert(ignore_permissions=True)
		created += 1
	return {"created": created, "adopted": adopted}


def after_install():
	seed_rails()

	settings = frappe.get_single("CryptoPoS Settings")
	if not settings.merchant_name:
		settings.merchant_name = "CryptoPoS Terminal"
		settings.mode = "testnet"
		settings.chain_reference = 1
		settings.save(ignore_permissions=True)

	frappe.db.commit()
