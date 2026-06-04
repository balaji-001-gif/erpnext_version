# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

# Compatibility shim: delegates to the standalone projects app.
# If the projects app is not installed, returns empty results gracefully.


import frappe


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def query_task(doctype, txt, searchfield, start, page_len, filters):
	try:
		from projects.projects.utils import query_task as _query_task

		return _query_task(doctype, txt, searchfield, start, page_len, filters)
	except ImportError:
		return []
