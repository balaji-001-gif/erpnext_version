# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe.utils import escape_html


@frappe.whitelist(allow_guest=True)
def send_message(sender, message, subject="Website Query"):
	from frappe.www.contact import send_message as website_send_message

	website_send_message(sender, message, subject)

	message = escape_html(message)

	comm = frappe.get_doc(
		{
			"doctype": "Communication",
			"subject": subject,
			"content": message,
			"sender": sender,
			"sent_or_received": "Received",
		}
	)
	comm.insert(ignore_permissions=True)
