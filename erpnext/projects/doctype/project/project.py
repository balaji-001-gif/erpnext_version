# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

# Compatibility shim: delegates to the standalone projects app.
# If the projects app is not installed, returns None gracefully.


import frappe


@frappe.whitelist()
def get_cost_center_name(project):
	try:
		from projects.projects.doctype.project.project import get_cost_center_name as _get_cost_center_name

		return _get_cost_center_name(project)
	except ImportError:
		return None
