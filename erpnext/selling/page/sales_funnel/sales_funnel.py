# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from itertools import groupby

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.accounts.report.utils import convert


def validate_filters(from_date, to_date, company):
	if from_date and to_date and (from_date >= to_date):
		frappe.throw(_("To Date must be greater than From Date"))

	if not company:
		frappe.throw(_("Please Select a Company"))


@frappe.whitelist()
def get_funnel_data(from_date, to_date, company):
	validate_filters(from_date, to_date, company)

	quotations = frappe.db.sql(
		"""select count(*) from `tabQuotation`
		where docstatus = 1 and (date(`creation`) between %s and %s)
		and company=%s""",
		(from_date, to_date, company),
	)[0][0]

	return [
		{"title": _("Quotations"), "value": quotations, "color": "#006685"},
	]



