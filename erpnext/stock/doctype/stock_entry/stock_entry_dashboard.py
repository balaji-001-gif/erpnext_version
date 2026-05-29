from frappe import _


def get_data():
	return {
		"fieldname": "stock_entry",
		"non_standard_fieldnames": {},
		"internal_links": {
			"Purchase Order": ["items", "purchase_order"],
		},
		"transactions": [
			{
				"label": _("Reference"),
				"items": ["Purchase Order"],
			},
		],
	}
