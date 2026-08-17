"""CryptoPoS Settings — merchant configuration, read once per charge."""

import frappe
from frappe import _
from frappe.model.document import Document


class CryptoPoSSettings(Document):
	def validate(self):
		# Building the mainnet interface is not permission to use it. The
		# refusal happens at charge (see charge.py), but saying it here too
		# means the operator learns it when they choose, not when a customer
		# is standing at the counter.
		if self.mode == "mainnet":
			frappe.msgprint(
				_(
					"Mainnet is a non-working mode by decision. It is announced, "
					"not unreachable: no endpoint is published. Charging will refuse."
				),
				title=_("Mainnet selected"),
				indicator="orange",
			)
