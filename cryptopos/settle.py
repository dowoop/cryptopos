"""Booking — where a settled sale becomes an ERPNext Sales Invoice.

This is the seam between the two halves, and it only opens one way. The
Crypto Sale owns uncertainty; ERPNext owns the ledger. A sale that is
settled, bound and real produces an invoice. Every other sale -- expired,
part-paid, sighted-but-unidentified, could-not-verify -- produces nothing,
and that silence is the design rather than a gap in it.

The alternative was mapping the sale's eight states onto a Sales Invoice's
three docstatus values, which would have required deciding whether
"I cannot tell" is a submit or a cancel. It is neither, so it does not go
into a document that can only say those two things.
"""

import frappe
from frappe import _


def book(sale):
	"""Emit a Sales Invoice for a settled sale. Idempotent."""
	if sale.sales_invoice:
		return sale.sales_invoice

	ok, reason = sale.may_book()
	if not ok:
		sale._append_event(sale.state, sale.state, "book", _("not booked: {0}").format(reason))
		sale.save(ignore_permissions=True)
		return None

	settings = frappe.get_single("CryptoPoS Settings")
	missing = [
		label
		for label, value in (
			("Default Customer", settings.customer),
			("Default Item", settings.item_code),
			("Company", settings.company),
		)
		if not value
	]
	if missing:
		# The sale is settled and stays settled. What failed is the booking,
		# and saying so on the sale is better than throwing: the money did
		# arrive, and a configuration gap must not rewrite that fact.
		sale._append_event(
			sale.state,
			sale.state,
			"book",
			_("cannot book, CryptoPoS Settings missing: {0}").format(", ".join(missing)),
		)
		sale.save(ignore_permissions=True)
		return None

	invoice = frappe.new_doc("Sales Invoice")
	invoice.customer = settings.customer
	invoice.company = settings.company
	invoice.currency = "USD"
	invoice.append(
		"items",
		{
			"item_code": settings.item_code,
			"qty": 1,
			"rate": sale.usd_cents / 100.0,
			"cost_center": settings.cost_center or None,
		},
	)
	# The crypto reference travels with the ledger entry, so a dispute months
	# later can get from the accounting record back to the chain.
	invoice.remarks = _(
		"CryptoPoS {invoice_id} ({ref})\n"
		"rail: {rail}  mode: {mode}  provenance: {provenance}\n"
		"paid: {credited} {unit} (invoiced {invoiced})\n"
		"rate: {rate} microcents/coin from {source}\n"
		"txid: {tx}"
	).format(
		invoice_id=sale.invoice_id,
		ref=sale.invoice_ref,
		rail=sale.rail_key,
		mode=sale.mode,
		provenance=sale.provenance,
		credited=sale.credited_native,
		unit=frappe.db.get_value("Crypto Rail", sale.rail_key, "unit_name"),
		invoiced=sale.invoiced_native,
		rate=sale.rate_microcents,
		source=sale.rate_source,
		tx=sale.tx_id or _("not recorded"),
	)
	invoice.insert(ignore_permissions=True)
	invoice.submit()

	sale.db_set("sales_invoice", invoice.name, update_modified=False)
	sale.reload()
	sale._append_event(sale.state, sale.state, "book", _("booked as {0}").format(invoice.name))
	sale.save(ignore_permissions=True)
	return invoice.name
