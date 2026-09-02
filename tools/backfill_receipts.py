"""Record the receipt for settled sales whose invoice was booked unpaid.

`settle.book()` wrote a Sales Invoice and stopped, so the ledger learned that
a customer OWED for each sale and never that they had paid. Measured on the
live instance 2026-09-02, before the fix: 57 booked invoices, every one of them
Unpaid or Overdue, $839.81 of receivable against a walk-in customer who had
paid every cent of it on chain, and no asset account holding the crypto that
arrived. `book()` now books `is_pos` with a payment row, so new sales are
correct; this is the arrears.

**It is a Payment Entry here and not `is_pos`, because these invoices are
already submitted.** A submitted document's `is_pos` and payment table cannot
be edited, and cancel-and-reamend would destroy the booking history of sales a
customer was shown a receipt for. A payment against an outstanding invoice is
the ordinary accounting act for money that arrived after the invoice was
raised, which is exactly what happened.

**This does not reopen anything and does not touch a single Crypto Sale.** D10
governs sale state and no sale state is read, written, or considered here
beyond using `Crypto Sale.sales_invoice` as the list of invoices this
application is responsible for. Nothing outside that list is eligible, so an
invoice the operator raised by hand is never swept up by this.

**The posting date is the invoice's own**, not today's. The money arrived when
the sale settled; pairing the receipt with its invoice keeps both in one
period, and inventing a third date would misstate when the merchant was paid.

    cd sites && ../env/bin/python ../apps/cryptopos/tools/backfill_receipts.py
    cd sites && ../env/bin/python ../apps/cryptopos/tools/backfill_receipts.py --send

Dry by default: without `--send` it reports exactly what it would write and
writes nothing, because this posts to a real ledger.
"""

import sys

import frappe

from cryptopos.install import RECEIPT_MODE_OF_PAYMENT


def candidates():
	"""Submitted invoices this app booked that still carry an outstanding balance.

	The join is deliberately through `Crypto Sale`: an invoice is eligible only
	because a settled sale of ours names it. An operator's own unpaid invoice
	is not this tool's business and must never be marked paid by it.
	"""
	rows = []
	for sale in frappe.get_all(
		"Crypto Sale",
		filters={"state": "confirmed", "sales_invoice": ("is", "set")},
		fields=["name", "sales_invoice", "usd_cents", "rail_key", "tx_id"],
		order_by="creation asc",
	):
		invoice = frappe.db.get_value(
			"Sales Invoice",
			sale.sales_invoice,
			["name", "docstatus", "outstanding_amount", "grand_total", "customer", "company", "posting_date", "currency"],
			as_dict=True,
		)
		if not invoice or invoice.docstatus != 1:
			continue
		if not invoice.outstanding_amount or invoice.outstanding_amount <= 0:
			continue
		rows.append({"sale": sale, "invoice": invoice})
	return rows


def run(send=False, limit=0):
	settings = frappe.get_single("CryptoPoS Settings")
	account = settings.get("receipt_account")
	if not account:
		print("no receipt account configured -- run cryptopos.install.ensure_receipt_account first")
		return {"considered": 0, "written": 0, "failed": 0}

	rows = candidates()
	if limit:
		rows = rows[: int(limit)]

	total = sum(row["invoice"].outstanding_amount for row in rows)
	print(f"{len(rows)} invoice(s) booked by a settled sale and still outstanding, ${total:,.2f} in all")
	print(f"receipt account: {account}   mode of payment: {RECEIPT_MODE_OF_PAYMENT}")
	print("DRY RUN -- nothing will be written" if not send else "WRITING")
	print()

	written = failed = 0
	for row in rows:
		sale, invoice = row["sale"], row["invoice"]
		line = (
			f"  {sale.name} -> {invoice.name}  ${invoice.outstanding_amount:,.2f} "
			f"outstanding of ${invoice.grand_total:,.2f}  [{sale.rail_key}]"
		)
		if not send:
			print(line)
			continue
		try:
			entry = frappe.new_doc("Payment Entry")
			entry.payment_type = "Receive"
			entry.company = invoice.company
			entry.posting_date = invoice.posting_date
			entry.mode_of_payment = RECEIPT_MODE_OF_PAYMENT
			entry.party_type = "Customer"
			entry.party = invoice.customer
			entry.paid_to = account
			entry.paid_amount = invoice.outstanding_amount
			entry.received_amount = invoice.outstanding_amount
			entry.source_exchange_rate = 1
			entry.target_exchange_rate = 1
			# The chain reference travels with the receipt for the same reason
			# `book()` puts it in the invoice remarks: a dispute months later
			# has to be able to get from the ledger back to the transaction.
			entry.remarks = (
				f"CryptoPoS receipt for {sale.name} on {sale.rail_key}\n"
				f"txid: {sale.tx_id or 'not recorded'}\n"
				f"Backfilled: the sale settled on chain and its invoice was booked "
				f"before the receipt was recorded."
			)
			entry.append(
				"references",
				{
					"reference_doctype": "Sales Invoice",
					"reference_name": invoice.name,
					"total_amount": invoice.grand_total,
					"outstanding_amount": invoice.outstanding_amount,
					"allocated_amount": invoice.outstanding_amount,
				},
			)
			entry.insert(ignore_permissions=True)
			entry.submit()
			frappe.db.commit()
			after = frappe.db.get_value(
				"Sales Invoice", invoice.name, ["status", "outstanding_amount"], as_dict=True
			)
			print(f"{line}  -> {entry.name}  now {after.status}, outstanding ${after.outstanding_amount:,.2f}")
			written += 1
		except Exception as exception:
			frappe.db.rollback()
			# One refusal must not end the run: the invoices are independent
			# and a fiscal-year or dimension rule that blocks one says nothing
			# about the next.
			print(f"{line}  -> REFUSED: {type(exception).__name__}: {str(exception)[:160]}")
			failed += 1

	print()
	print(f"considered {len(rows)}, written {written}, failed {failed}")
	return {"considered": len(rows), "written": written, "failed": failed}


if __name__ == "__main__":
	frappe.init(site="erp.localhost")
	frappe.connect()
	try:
		run(send="--send" in sys.argv)
	finally:
		frappe.destroy()
