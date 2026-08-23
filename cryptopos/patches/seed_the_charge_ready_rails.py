"""Seed the rails whose adapters can do the whole job.

The app shipped with one rail, `btc`, and its own bitcoin-only URI builder
and watcher. Beside it, in the package it already depends on, sat adapters
reaching four live testnets through one contract. This is the migration that
lets an existing site see the other three, and teaches `btc` which adapter
drives it.

Nothing here switches a rail on for money: a seeded rail has no receiving
address, and `charge` refuses a rail with no receiving address. The operator
decides what this terminal accepts by filling that field in.
"""

import frappe

from cryptopos.install import seed_rails


def execute():
	frappe.reload_doc("cryptopos", "doctype", "crypto_rail")
	seed_rails()
