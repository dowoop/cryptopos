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
# `btc` is back on because the terminal now derives a fresh address for each
# sale from an operator's watch-only account key. A deployment with neither an
# xpub nor a recipient can display the offered rail, but charge still refuses
# before showing a payment request. See DECISIONS.md D5.
ADAPTERS = {
	"btc": (
		"bitcoin:testnet4/native:btc",
		"https://mempool.space/testnet4/api",
		"esplora-rest",
		"testnet4",
		1,
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
	# Native POL, seedable since the observer was composed on 2026-08-24. Same
	# endpoint and transport as the USDC rail above because it is the same
	# chain -- the two differ only in what they watch for at the address, and
	# `rails_probe` reproduces that they cannot take each other's payments.
	"pol": (
		"polygon:amoy/native:pol",
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
				# Maturity as THIS terminal can stand behind it, not as the
				# table describes the rail's potential.
				#
				# `cryptopos_core.rails` defines "works" as real testnet reads
				# AND a real payer, and its notes were carried over verbatim
				# from the tkinter terminal, which bundled a wallet that signed
				# and broadcast. This app does not: it is watch-only by charter
				# and there is no signing path anywhere in it. Seeding its notes
				# unchanged put "real payer (bundled wallet signs & broadcasts)"
				# on an operator's screen for three rails that have no payer at
				# all, which is precisely the overclaim the maturity field
				# exists to prevent.
				"maturity": "partial",
				"maturity_note": (
					f"real {network} reads through {transport}; this terminal is "
					f"watch-only, so the customer's own wallet is the payer. "
					f"Binding: {rail['binding']}."
				),
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


# Prose about the chain, refreshed on every migrate. These describe what a rail
# IS; the operator owns none of them, and a stale one is an untrue sentence on
# an operator's screen. `btc`'s note read "no HD derivation yet, so binding is
# by amount rather than per-sale" for eight days after the terminal started
# deriving a fresh address per sale.
_DESCRIPTIONS = (
	"label",
	"chain",
	"gate_text",
	"binding_text",
	"maturity",
	"maturity_note",
	"real_transport",
)

# Facts the arithmetic depends on. NEVER rewritten under an existing rail: a
# sale's `credited_native` is an integer whose meaning comes from the decimals
# in force when it was written, and changing them reinterprets money that has
# already been taken. A disagreement here is reported, not repaired.
_ARITHMETIC = ("asset", "family", "unit_name", "native_decimals", "display_decimals")


def seed_rails():
	"""Create any rail that is missing; refresh what an existing one only says.

	Idempotent, and deliberately conservative about anything an operator owns:
	`enabled`, the endpoint, the receiving material and the address index are
	never touched on a rail that already exists.
	"""
	created = adopted = refreshed = 0
	drifted = []
	for row in rail_rows():
		if not frappe.db.exists("Crypto Rail", row["rail_key"]):
			doc = frappe.new_doc("Crypto Rail")
			doc.update(row)
			doc.insert(ignore_permissions=True)
			created += 1
			continue

		existing = frappe.get_doc("Crypto Rail", row["rail_key"])
		if not (existing.catalog_key or "").strip():
			existing.db_set("catalog_key", row["catalog_key"], update_modified=False)
			adopted += 1
		for field in _DESCRIPTIONS:
			if (existing.get(field) or "") != (row[field] or ""):
				existing.db_set(field, row[field], update_modified=False)
				refreshed += 1
		for field in _ARITHMETIC:
			if existing.get(field) != row[field]:
				drifted.append(f"{row['rail_key']}.{field}: {existing.get(field)!r} != {row[field]!r}")
	return {"created": created, "adopted": adopted, "refreshed": refreshed, "drifted": drifted}


def after_install():
	seed_rails()

	settings = frappe.get_single("CryptoPoS Settings")
	if not settings.merchant_name:
		settings.merchant_name = "CryptoPoS Terminal"
		settings.mode = "testnet"
		settings.chain_reference = 1
		settings.save(ignore_permissions=True)

	frappe.db.commit()
