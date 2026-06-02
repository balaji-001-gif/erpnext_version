from frappe import _


def get_data():
	return {
		"fieldname": "supplier_quotation",
		"non_standard_fieldnames": {"Auto Repeat": "reference_document"},
		"internal_links": {
			"Material Request": ["items", "material_request"],
			"Request for Quotation": ["items", "request_for_quotation"],
		},
		"transactions": [
			{"label": _("Related"), "items": ["Purchase Order", "Quotation"]},
			{"label": _("Reference"), "items": ["Material Request", "Request for Quotation"]},
			{"label": _("Subscription"), "items": ["Auto Repeat"]},
		],
	}
