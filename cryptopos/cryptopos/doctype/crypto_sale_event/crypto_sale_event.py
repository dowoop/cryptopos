"""Crypto Sale Event — one row per state transition, append-only.

Nothing edits these. A record edited to stay current stops being a record,
and the whole value of this table is that a state on screen can be traced
back to the transport that caused it and the moment it happened.
"""

from frappe.model.document import Document


class CryptoSaleEvent(Document):
	pass
