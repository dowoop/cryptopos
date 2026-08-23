"""The receiving address belongs to the rail, not to the terminal.

`CryptoPoS Settings.btc_testnet_address` could only ever describe one rail,
and its name said so. A terminal that charges on Bitcoin, Ethereum and two
USDC deployments needs one receiving address per rail, because an address is
a fact about a chain.

This runs **after** the model sync, and it has to: the column it writes into
is created by that sync, so a pre-sync patch would be writing to a field that
does not exist yet. The old field is therefore kept rather than dropped —
read-only now, and saying on its own label where the value went. Removing it
is a second migration, once no site is still carrying the value.
"""

import frappe


def execute():
	# `CryptoPoS Settings` is a Single, so it has no table and `has_column`
	# raises TableMissingError on it. Its values live as rows in `tabSingles`,
	# which outlive the field's removal from the doctype -- so asking for the
	# value is both the existence check and the read.
	address = frappe.db.get_single_value("CryptoPoS Settings", "btc_testnet_address")
	if not address or not str(address).strip():
		return

	if not frappe.db.exists("Crypto Rail", "btc"):
		return

	# Only if the rail has not been given one already. A value an operator
	# typed on the rail is newer than the one this is migrating, and a patch
	# that overwrote it would be undoing a deliberate act.
	if frappe.db.has_column("Crypto Rail", "testnet_recipient") and frappe.db.get_value(
		"Crypto Rail", "btc", "testnet_recipient"
	):
		return

	frappe.db.set_value("Crypto Rail", "btc", "testnet_recipient", str(address).strip())
