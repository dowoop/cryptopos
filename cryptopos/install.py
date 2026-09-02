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
	# THE ONE RAIL WHOSE TRANSPORT IS NOT A NODE RPC. The Ootle indexer is
	# REST plus server-sent events, and the events are the point: a deposit
	# arrives with a transaction id and an exact amount, filtered to one vault,
	# replayable from a cursor. Every other rail here rescans and can miss a
	# payment between reads; this one cannot.
	#
	# Enabled, and honestly so: a real customer payment of 1,234,000 microTari
	# was observed and settled through this adapter on 2026-08-31. What it
	# cannot do is price itself -- Tari is listed on no feed this build reads,
	# so it charges at the demo rate in `rates.DEMO_MICROCENTS`, which can
	# never be reached in a real-money mode.
	"xtr": (
		"ootle:esmeralda/native:xtr",
		"https://ootle-indexer-a.tari.com",
		"ootle-indexer-rest",
		"esmeralda",
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


# The demo-cover queue's two fields.
#
# CUSTOM FIELDS RATHER THAN DOCTYPE FIELDS, deliberately. Adding them to
# `crypto_sale.json` is the tidier home and needs `bench migrate`, which on a
# live public till is a bigger event than this feature deserves.
# `create_custom_fields` is idempotent and runs in a second.
#
# They exist because the container CANNOT PAY. The Ootle signing key is on the
# host, so a "cover this charge" button can only record intent and wait -- the
# same split `claim_awards`/`report_award` already make for loyalty, and these
# are the payment half of it.
_COVER_FIELDS = {
	"Crypto Sale": [
		{
			"fieldname": "demo_cover_state",
			"label": "Demo Cover State",
			"fieldtype": "Select",
			# The blank first option is the ordinary sale: nobody asked the
			# house to pay it, which is a different thing from having asked
			# and been refused.
			"options": "\nrequested\npaying\ncovered\nrefused",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "state",
		},
		{
			"fieldname": "demo_cover_note",
			"label": "Demo Cover Note",
			"fieldtype": "Small Text",
			"read_only": 1,
			"no_copy": 1,
			# WHY A REFUSAL NEEDS A PLACE TO BE WRITTEN. Without it the house
			# declining to cover a sale is indistinguishable from the house
			# never having looked, and the visitor watches a sale expire with
			# nothing on screen. Silence is the worst possible refusal.
			"insert_after": "demo_cover_state",
		},
	]
}


def ensure_cover_fields():
	"""Create the demo-cover fields if they are missing. Idempotent."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(_COVER_FIELDS, ignore_validate=True)
	return sorted(
		frappe.get_all(
			"Custom Field",
			filters={"dt": "Crypto Sale", "fieldname": ("like", "demo_cover%")},
			pluck="fieldname",
		)
	)


# WHERE THE MONEY LANDS IN THE LEDGER.
#
# `book()` wrote a Sales Invoice and stopped, so the ledger learned that a
# customer OWED for the sale and never that they had paid. Measured on the live
# instance 2026-09-02: 57 booked invoices, 57 of them Unpaid or Overdue,
# $839.81 of receivable against a walk-in customer, and ZERO Payment Entries.
# `Debtors` carried the whole of it and no asset account carried a cent, so the
# crypto that actually arrived appeared in the books nowhere at all. Every
# settled demo made the discrepancy one sale bigger.
#
# THE ACCOUNT TYPE IS DELIBERATELY BLANK. A crypto wallet is not Cash In Hand
# and it is not a Bank Account, and ERPNext does not require it to be either:
# probed against this site's own chart, a plain asset account under Current
# Assets submits a paid POS invoice exactly as a Cash- or Bank-typed one does.
# Labelling the wallet `Cash` to satisfy a dropdown would be a false statement
# about what the merchant holds, on the one record whose job is to be true.
#
# ONE ACCOUNT RATHER THAN ONE PER RAIL. The invoice is denominated in company
# currency, so a per-rail account would hold the USD value of XTR rather than
# XTR, and Crypto Takings already reports per rail from the sale records. Seven
# accounts would divide the same USD figure without adding a fact.
RECEIPT_ACCOUNT_NAME = "Crypto Receipts"
RECEIPT_MODE_OF_PAYMENT = "Crypto"

_RECEIPT_FIELDS = {
	"CryptoPoS Settings": [
		{
			"fieldname": "receipt_account",
			"label": "Crypto Receipt Account",
			"fieldtype": "Link",
			"options": "Account",
			"insert_after": "cost_center",
			"description": (
				"The asset account a settled sale's money is received into. "
				"Without it an invoice books the revenue and the receivable "
				"and never the receipt, so the sale reads Unpaid forever."
			),
		}
	]
}


def ensure_receipt_account():
	"""Create the receipt account, its mode of payment, and the setting.

	Idempotent, and it never overwrites an operator's choice: if the setting
	already names an account, that account is left alone and returned.
	"""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(_RECEIPT_FIELDS, ignore_validate=True)

	settings = frappe.get_single("CryptoPoS Settings")
	company = settings.company
	if not company:
		# No company yet means a fresh install that has not been configured.
		# `book()` already refuses in that case and says which settings are
		# missing; inventing a company here would be guessing at the answer.
		return None

	if settings.get("receipt_account") and frappe.db.exists(
		"Account", settings.get("receipt_account")
	):
		return settings.get("receipt_account")

	abbreviation = frappe.db.get_value("Company", company, "abbr")
	account_name = f"{RECEIPT_ACCOUNT_NAME} - {abbreviation}"
	if not frappe.db.exists("Account", account_name):
		parent = f"Current Assets - {abbreviation}"
		if not frappe.db.exists("Account", parent):
			# A chart without the standard group is somebody's own chart, and
			# picking a different parent for them is a decision this has no
			# standing to take. Say so rather than filing the money somewhere
			# arbitrary.
			frappe.log_error(
				title="cryptopos could not place the receipt account",
				message=f"{parent} does not exist in {company}; create the account by hand "
				f"and name it in CryptoPoS Settings.",
			)
			return None
		account = frappe.new_doc("Account")
		account.account_name = RECEIPT_ACCOUNT_NAME
		account.company = company
		account.parent_account = parent
		account.account_currency = frappe.db.get_value("Company", company, "default_currency")
		account.insert(ignore_permissions=True)

	if not frappe.db.exists("Mode of Payment", RECEIPT_MODE_OF_PAYMENT):
		mode = frappe.new_doc("Mode of Payment")
		mode.mode_of_payment = RECEIPT_MODE_OF_PAYMENT
		# Not Cash and not Bank, for the reason the account type is blank.
		mode.type = "General"
		mode.insert(ignore_permissions=True)

	mode = frappe.get_doc("Mode of Payment", RECEIPT_MODE_OF_PAYMENT)
	if not any(row.company == company for row in mode.accounts):
		mode.append("accounts", {"company": company, "default_account": account_name})
		mode.save(ignore_permissions=True)

	settings.db_set("receipt_account", account_name)
	return account_name


def after_install():
	seed_rails()
	ensure_cover_fields()
	ensure_receipt_account()

	settings = frappe.get_single("CryptoPoS Settings")
	if not settings.merchant_name:
		settings.merchant_name = "CryptoPoS Terminal"
		settings.mode = "testnet"
		settings.chain_reference = 1
		settings.save(ignore_permissions=True)

	frappe.db.commit()
