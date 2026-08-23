"""Add per-sale receiving material and restore Bitcoin to the offered rails."""

import frappe

from cryptopos.install import seed_rails


def execute():
	frappe.reload_doc("cryptopos", "doctype", "crypto_rail")
	seed_rails()
	# `seed_rails` preserves an existing operator's enabled flag. Bitcoin was
	# disabled by the earlier safety migration, so this one-time patch applies
	# the decision that per-sale derivation makes it offerable again. A missing
	# xpub/recipient still refuses at charge time.
	if frappe.db.exists("Crypto Rail", "btc"):
		frappe.db.set_value("Crypto Rail", "btc", "enabled", 1, update_modified=False)
