"""Daily, per-rail oversight of settled takings."""

import frappe
from frappe import _
from frappe.utils import add_days, getdate, nowdate

COLUMNS = (
	{"fieldname": "date", "label": _("Date"), "fieldtype": "Date", "width": 100},
	{
		"fieldname": "rail",
		"label": _("Rail"),
		"fieldtype": "Link",
		"options": "Crypto Rail",
		"width": 120,
	},
	{"fieldname": "sales", "label": _("Sales"), "fieldtype": "Int", "width": 80},
	{
		"fieldname": "booked_usd",
		"label": _("Booked USD"),
		"fieldtype": "Currency",
		"options": "USD",
		"width": 130,
	},
	{
		"fieldname": "unbooked_usd",
		"label": _("Unbooked USD"),
		"fieldtype": "Currency",
		"options": "USD",
		"width": 140,
	},
	{
		"fieldname": "credited_native",
		"label": _("Credited Native"),
		"fieldtype": "Data",
		"width": 190,
	},
	{"fieldname": "unit", "label": _("Unit"), "fieldtype": "Data", "width": 100},
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date = getdate(filters.get("from_date") or nowdate())
	to_date = getdate(filters.get("to_date") or nowdate())
	if from_date > to_date:
		frappe.throw(_("From Date must be on or before To Date."))

	sale_filters = [
		["state", "=", "confirmed"],
		["settled_at", ">=", from_date],
		["settled_at", "<", add_days(to_date, 1)],
	]
	if filters.get("rail"):
		sale_filters.append(["rail_key", "=", filters.rail])

	sales = frappe.get_all(
		"Crypto Sale",
		filters=sale_filters,
		fields=["settled_at", "rail_key", "usd_cents", "sales_invoice", "credited_native"],
		order_by="settled_at asc, rail_key asc, name asc",
	)
	units = {
		row.name: row.unit_name
		for row in frappe.get_all("Crypto Rail", fields=["name", "unit_name"])
	}
	grouped = {}
	for sale in sales:
		key = (getdate(sale.settled_at), sale.rail_key)
		row = grouped.setdefault(
			key,
			{
				"date": key[0],
				"rail": key[1],
				"sales": 0,
				"booked_cents": 0,
				"unbooked_cents": 0,
				"credited_native": 0,
				"unit": units.get(key[1], ""),
			},
		)
		row["sales"] += 1
		if sale.sales_invoice:
			row["booked_cents"] += int(sale.usd_cents or 0)
		else:
			row["unbooked_cents"] += int(sale.usd_cents or 0)
		# Native values cross the JavaScript boundary only after exact integer
		# addition within one rail. They are never valued and never cross rails.
		row["credited_native"] += int(sale.credited_native or 0)

	rows = []
	for key in sorted(grouped):
		group = grouped[key]
		rows.append(
			{
				"date": group["date"],
				"rail": group["rail"],
				"sales": group["sales"],
				"booked_usd": group["booked_cents"] / 100.0,
				"unbooked_usd": group["unbooked_cents"] / 100.0,
				"credited_native": str(group["credited_native"]),
				"unit": group["unit"],
			}
		)

	booked_by_date = {}
	for (date, _rail), group in grouped.items():
		booked_by_date[date] = booked_by_date.get(date, 0) + group["booked_cents"]
	chart_dates = []
	date = from_date
	while date <= to_date:
		chart_dates.append(date)
		date = add_days(date, 1)
	chart = {
		"data": {
			"labels": [str(date) for date in chart_dates],
			"datasets": [
				{
					"name": _("Booked USD"),
					"values": [booked_by_date.get(date, 0) / 100.0 for date in chart_dates],
				}
			],
		},
		"type": "line",
		"colors": ["#2490ef"],
	}
	return list(COLUMNS), rows, None, chart
