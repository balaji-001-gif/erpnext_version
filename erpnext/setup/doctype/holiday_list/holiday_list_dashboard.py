def get_data():
	return {
		"fieldname": "holiday_list",
		"non_standard_fieldnames": {
			"Company": "default_holiday_list",
		},
		"transactions": [
			{
				"items": ["Company", "Employee"],
			},
			{"items": ["Service Level Agreement"]},
		],
	}
