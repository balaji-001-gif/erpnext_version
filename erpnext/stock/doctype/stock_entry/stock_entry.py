# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import json
from collections import defaultdict

import frappe
from frappe import _, bold
from frappe.model.mapper import get_mapped_doc
from frappe.query_builder import DocType
from frappe.query_builder.functions import Sum
from frappe.utils import (
	cint,
	comma_or,
	cstr,
	flt,
	format_time,
	formatdate,
	get_link_to_form,
	getdate,
	nowdate,
)

import erpnext
from erpnext.accounts.general_ledger import process_gl_map
from erpnext.buying.utils import check_on_hold_or_closed_status
from erpnext.controllers.taxes_and_totals import init_landed_taxes_and_totals
from erpnext.setup.doctype.brand.brand import get_brand_defaults
from erpnext.setup.doctype.item_group.item_group import get_item_group_defaults
from erpnext.stock.doctype.batch.batch import get_batch_qty
from erpnext.stock.doctype.item.item import get_item_defaults
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import (
	OpeningEntryAccountError,
)
from erpnext.stock.get_item_details import (
	get_barcode_data,
	get_bin_details,
	get_conversion_factor,
	get_default_cost_center,
)
from erpnext.stock.serial_batch_bundle import (
	SerialBatchCreation,
	get_serial_or_batch_items,
)
from erpnext.stock.stock_ledger import NegativeStockError, get_previous_sle, get_valuation_rate
from erpnext.stock.utils import get_bin, get_incoming_rate


from erpnext.controllers.stock_controller import StockController

form_grid_templates = {"items": "templates/form_grid/stock_entry_grid.html"}


class StockEntry(StockController):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from erpnext.stock.doctype.landed_cost_taxes_and_charges.landed_cost_taxes_and_charges import (
			LandedCostTaxesandCharges,
		)
		from erpnext.stock.doctype.stock_entry_detail.stock_entry_detail import StockEntryDetail

		add_to_transit: DF.Check
		additional_costs: DF.Table[LandedCostTaxesandCharges]
		address_display: DF.SmallText | None
		amended_from: DF.Link | None
		apply_putaway_rule: DF.Check
		asset_repair: DF.Link | None
		company: DF.Link
		cost_center: DF.Link | None
		credit_note: DF.Link | None
		delivery_note_no: DF.Link | None
		from_warehouse: DF.Link | None
		is_opening: DF.Literal["No", "Yes"]
		is_return: DF.Check
		items: DF.Table[StockEntryDetail]
		letter_head: DF.Link | None
		naming_series: DF.Literal["MAT-STE-.YYYY.-"]
		outgoing_stock_entry: DF.Link | None
		per_transferred: DF.Percent
		pick_list: DF.Link | None
		posting_date: DF.Date | None
		posting_time: DF.Time | None
		project: DF.Link | None
		purchase_receipt_no: DF.Link | None
		purpose: DF.Literal[
			"Material Issue",
			"Material Receipt",
			"Material Transfer",
			"Repack",
		]
		remarks: DF.Text | None
		sales_invoice_no: DF.Link | None
		scan_barcode: DF.Data | None
		select_print_heading: DF.Link | None
		set_posting_time: DF.Check
		source_address_display: DF.SmallText | None
		source_warehouse_address: DF.Link | None
		stock_entry_type: DF.Link
		target_address_display: DF.SmallText | None
		target_warehouse_address: DF.Link | None
		to_warehouse: DF.Link | None
		total_additional_costs: DF.Currency
		total_amount: DF.Currency
		total_incoming_value: DF.Currency
		total_outgoing_value: DF.Currency
		value_difference: DF.Currency
	# end: auto-generated types

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
	def onload(self):
		self.update_items_from_bin_details()

	def before_print(self, settings=None):
		super().before_print(settings)
		self.update_items_from_bin_details()

	def update_items_from_bin_details(self):
		for item in self.get("items"):
			item.update(get_bin_details(item.item_code, item.s_warehouse))

	def before_validate(self):
		from erpnext.stock.doctype.putaway_rule.putaway_rule import apply_putaway_rule

		apply_rule = self.apply_putaway_rule and (self.purpose in ["Material Transfer", "Material Receipt"])

		if self.get("items") and apply_rule:
			apply_putaway_rule(self.doctype, self.get("items"), self.company, purpose=self.purpose)

	def validate(self):
		self.validate_duplicate_serial_and_batch_bundle("items")
		self.validate_posting_time()
		self.validate_purpose()
		self.validate_item()
		self.validate_customer_provided_item()
		self.set_transfer_qty()
		self.validate_uom_is_integer("uom", "qty")
		self.validate_uom_is_integer("stock_uom", "transfer_qty")
		self.validate_warehouse_of_sabb()
		self.validate_purchase_order()
		self.validate_company_in_accounting_dimension()

		self.validate_warehouse()
		self.validate_with_material_request()
		self.validate_batch()
		self.validate_difference_account()
		self.set_purpose_for_stock_entry()
		self.clean_serial_nos()
		self.validate_repack_entry()

		self.make_serial_and_batch_bundle_for_outward()
		self.validate_serialized_batch()
		self.calculate_rate_and_amount()
		self.validate_putaway_capacity()

		self.reset_default_field_value("from_warehouse", "items", "s_warehouse")
		self.reset_default_field_value("to_warehouse", "items", "t_warehouse")

		self.validate_same_source_target_warehouse_during_material_transfer()

	def validate_repack_entry(self):
		if self.purpose != "Repack":
			return

		fg_items = {row.item_code: row for row in self.items if row.is_finished_item}

		if len(fg_items) > 1 and not all(row.set_basic_rate_manually for row in fg_items.values()):
			frappe.throw(
				_(
					"When there are multiple finished goods ({0}) in a Repack stock entry, the basic rate for all finished goods must be set manually. To set rate manually, enable the checkbox 'Set Basic Rate Manually' in the respective finished good row."
				).format(", ".join(fg_items)),
				title=_("Set Basic Rate Manually"),
			)

	def on_submit(self):
		self.make_bundle_using_old_serial_batch_fields()
		self.update_stock_ledger()
		self.update_pick_list_status()

		self.make_gl_entries()

		self.repost_future_sle_and_gle()
		self.update_cost_in_project()
		self.update_transferred_qty()
		if self.purpose == "Material Transfer" and self.add_to_transit:
			self.set_material_request_transfer_status("In Transit")
		if self.purpose == "Material Transfer" and self.outgoing_stock_entry:
			self.set_material_request_transfer_status("Completed")

	def on_cancel(self):
		self.delink_asset_repair_sabb()
		self.update_stock_ledger()

		self.ignore_linked_doctypes = (
			"GL Entry",
			"Stock Ledger Entry",
			"Repost Item Valuation",
			"Serial and Batch Bundle",
		)

		self.make_gl_entries_on_cancel()
		self.repost_future_sle_and_gle()
		self.update_cost_in_project()
		self.update_transferred_qty()
		self.delete_auto_created_batches()
		self.delete_linked_stock_entry()

		if self.purpose == "Material Transfer" and self.add_to_transit:
			self.set_material_request_transfer_status("Not Started")
		if self.purpose == "Material Transfer" and self.outgoing_stock_entry:
			self.set_material_request_transfer_status("In Transit")

	def on_update(self):
		self.set_serial_and_batch_bundle()

	def validate_purpose(self):
		valid_purposes = [
			"Material Issue",
			"Material Receipt",
			"Material Transfer",
			"Repack",
		]

		if self.purpose not in valid_purposes:
			frappe.throw(_("Purpose must be one of {0}").format(comma_or(valid_purposes)))

	def delete_linked_stock_entry(self):
		if self.purpose == "Send to Warehouse":
			for d in frappe.get_all(
				"Stock Entry",
				filters={
					"docstatus": 0,
					"outgoing_stock_entry": self.name,
					"purpose": "Receive at Warehouse",
				},
			):
				frappe.delete_doc("Stock Entry", d.name)

	def delink_asset_repair_sabb(self):
		if not self.asset_repair:
			return

		for row in self.items:
			if row.serial_and_batch_bundle:
				voucher_detail_no = frappe.db.get_value(
					"Asset Repair Consumed Item",
					{"parent": self.asset_repair, "serial_and_batch_bundle": row.serial_and_batch_bundle},
					"name",
				)

				doc = frappe.get_doc("Serial and Batch Bundle", row.serial_and_batch_bundle)
				doc.db_set(
					{
						"voucher_type": "Asset Repair",
						"voucher_no": self.asset_repair,
						"voucher_detail_no": voucher_detail_no,
					}
				)

	def set_transfer_qty(self):
		self.validate_qty_is_not_zero()
		for item in self.get("items"):
			if not flt(item.conversion_factor):
				frappe.throw(_("Row {0}: UOM Conversion Factor is mandatory").format(item.idx))
			item.transfer_qty = flt(
				flt(item.qty) * flt(item.conversion_factor), self.precision("transfer_qty", item)
			)
			if not flt(item.transfer_qty):
				frappe.throw(
					_("Row {0}: Qty in Stock UOM can not be zero.").format(item.idx), title=_("Zero quantity")
				)

	def update_cost_in_project(self):
		if self.project:
			amount = frappe.db.sql(
				""" select ifnull(sum(sed.amount), 0)
				from
					`tabStock Entry` se, `tabStock Entry Detail` sed
				where
					se.docstatus = 1 and se.project = %s and sed.parent = se.name
					and (sed.t_warehouse is null or sed.t_warehouse = '')""",
				self.project,
				as_list=1,
			)

			amount = amount[0][0] if amount else 0
			project = frappe.get_doc("Project", self.project)
			project.total_consumed_material_cost = amount
			project.save()

	def validate_item(self):
		stock_items = self.get_stock_items()
		for item in self.get("items"):
			if flt(item.qty) and flt(item.qty) < 0:
				frappe.throw(
					_("Row {0}: The item {1}, quantity must be positive number").format(
						item.idx, frappe.bold(item.item_code)
					)
				)

			if item.item_code not in stock_items:
				frappe.throw(_("{0} is not a stock Item").format(item.item_code))

			item_details = self.get_item_details(
				frappe._dict(
					{
						"item_code": item.item_code,
						"company": self.company,
						"project": self.project,
						"uom": item.uom,
						"s_warehouse": item.s_warehouse,
						"is_finished_item": item.is_finished_item,
					}
				),
				for_update=True,
			)

			reset_fields = ("stock_uom", "item_name")
			for field in reset_fields:
				item.set(field, item_details.get(field))

			update_fields = (
				"uom",
				"description",
				"expense_account",
				"cost_center",
				"conversion_factor",
				"barcode",
			)

			for field in update_fields:
				if not item.get(field):
					item.set(field, item_details.get(field))
				if field == "conversion_factor" and item.uom == item_details.get("stock_uom"):
					item.set(field, item_details.get(field))

			if not item.transfer_qty and item.qty:
				item.transfer_qty = flt(
					flt(item.qty) * flt(item.conversion_factor), self.precision("transfer_qty", item)
				)



	def validate_difference_account(self):
		if not cint(erpnext.is_perpetual_inventory_enabled(self.company)):
			return

		for d in self.get("items"):
			if not d.expense_account:
				frappe.throw(
					_(
						"Please enter <b>Difference Account</b> or set default <b>Stock Adjustment Account</b> for company {0}"
					).format(frappe.bold(self.company))
				)

			acc_details = frappe.get_cached_value(
				"Account",
				d.expense_account,
				["account_type", "report_type"],
				as_dict=True,
			)

			if self.is_opening == "Yes" and acc_details.report_type == "Profit and Loss":
				frappe.throw(
					_(
						"Difference Account must be a Asset/Liability type account (Temporary Opening), since this Stock Entry is an Opening Entry"
					),
					OpeningEntryAccountError,
				)

			if acc_details.account_type == "Stock":
				frappe.throw(
					_(
						"At row #{0}: the Difference Account must not be a Stock type account, please change the Account Type for the account {1} or select a different account"
					).format(d.idx, get_link_to_form("Account", d.expense_account)),
					title=_("Difference Account in Items Table"),
				)

			if self.purpose != "Material Issue" and acc_details.account_type == "Cost of Goods Sold":
				frappe.msgprint(
					_(
						"At row #{0}: you have selected the Difference Account {1}, which is a Cost of Goods Sold type account. Please select a different account"
					).format(d.idx, bold(get_link_to_form("Account", d.expense_account))),
					title=_("Cost of Goods Sold Account in Items Table"),
					indicator="orange",
					alert=1,
				)

	def validate_warehouse(self):
		"""perform various (sometimes conditional) validations on warehouse"""

		source_mandatory = [
			"Material Issue",
			"Material Transfer",
		]

		target_mandatory = [
			"Material Receipt",
			"Material Transfer",
		]

		if self.purpose in source_mandatory and self.purpose not in target_mandatory:
			self.to_warehouse = None
			for d in self.get("items"):
				d.t_warehouse = None
		elif self.purpose in target_mandatory and self.purpose not in source_mandatory:
			self.from_warehouse = None
			for d in self.get("items"):
				d.s_warehouse = None

		for d in self.get("items"):
			if not d.s_warehouse and not d.t_warehouse:
				d.s_warehouse = self.from_warehouse
				d.t_warehouse = self.to_warehouse

			if self.purpose in source_mandatory and not d.s_warehouse:
				if self.from_warehouse:
					d.s_warehouse = self.from_warehouse
				else:
					frappe.throw(_("Source warehouse is mandatory for row {0}").format(d.idx))

			if self.purpose in target_mandatory and not d.t_warehouse:
				if self.to_warehouse:
					d.t_warehouse = self.to_warehouse
				else:
					frappe.throw(_("Target warehouse is mandatory for row {0}").format(d.idx))

			if self.purpose in ["Repack"]:
				if d.is_finished_item or d.is_scrap_item:
					d.s_warehouse = None
					if not d.t_warehouse:
						frappe.throw(_("Target warehouse is mandatory for row {0}").format(d.idx))
				else:
					d.t_warehouse = None
					if not d.s_warehouse:
						frappe.throw(_("Source warehouse is mandatory for row {0}").format(d.idx))

			if cstr(d.s_warehouse) == cstr(d.t_warehouse) and self.purpose not in [
				"Material Transfer",
			]:
				frappe.throw(_("Source and target warehouse cannot be same for row {0}").format(d.idx))

			if not (d.s_warehouse or d.t_warehouse):
				frappe.throw(_("Atleast one warehouse is mandatory"))



	def set_actual_qty(self):
		from erpnext.stock.stock_ledger import is_negative_stock_allowed

		for d in self.get("items"):
			allow_negative_stock = is_negative_stock_allowed(item_code=d.item_code)
			previous_sle = get_previous_sle(
				{
					"item_code": d.item_code,
					"warehouse": d.s_warehouse or d.t_warehouse,
					"posting_date": self.posting_date,
					"posting_time": self.posting_time,
				}
			)

			# get actual stock at source warehouse
			d.actual_qty = previous_sle.get("qty_after_transaction") or 0

			# validate qty during submit
			if (
				d.docstatus == 1
				and d.s_warehouse
				and not allow_negative_stock
				and flt(d.actual_qty, d.precision("actual_qty"))
				< flt(d.transfer_qty, d.precision("actual_qty"))
			):
				frappe.throw(
					_(
						"Row {0}: Quantity not available for {4} in warehouse {1} at posting time of the entry ({2} {3})"
					).format(
						d.idx,
						frappe.bold(d.s_warehouse),
						formatdate(self.posting_date),
						format_time(self.posting_time),
						frappe.bold(d.item_code),
					)
					+ "<br><br>"
					+ _("Available quantity is {0}, you need {1}").format(
						frappe.bold(flt(d.actual_qty, d.precision("actual_qty"))), frappe.bold(d.transfer_qty)
					),
					NegativeStockError,
					title=_("Insufficient Stock"),
				)

	def validate_same_source_target_warehouse_during_material_transfer(self):
		"""
		Validate Material Transfer entries where source and target warehouses are identical.

		For Material Transfer purpose, if an item has the same source and target warehouse,
		require that at least one inventory dimension (if configured) differs between source
		and target to ensure a meaningful transfer is occurring.

		Raises:
		frappe.ValidationError: If warehouses are same and no inventory dimensions differ
		"""

		if frappe.get_single_value("Stock Settings", "validate_material_transfer_warehouses"):
			from erpnext.stock.doctype.inventory_dimension.inventory_dimension import get_inventory_dimensions

			inventory_dimensions = get_inventory_dimensions()
			if self.purpose == "Material Transfer":
				for item in self.items:
					if cstr(item.s_warehouse) == cstr(item.t_warehouse):
						if not inventory_dimensions:
							frappe.throw(
								_(
									"Row #{0}: Source and Target Warehouse cannot be the same for Material Transfer"
								).format(item.idx),
								title=_("Invalid Source and Target Warehouse"),
							)
						else:
							difference_found = False
							for dimension in inventory_dimensions:
								fieldname = (
									dimension.source_fieldname
									if dimension.source_fieldname.startswith("to_")
									else f"to_{dimension.source_fieldname}"
								)
								if (
									item.get(dimension.source_fieldname)
									and item.get(fieldname)
									and item.get(dimension.source_fieldname) != item.get(fieldname)
								):
									difference_found = True
									break
							if not difference_found:
								frappe.throw(
									_(
										"Row #{0}: Source, Target Warehouse and Inventory Dimensions cannot be the exact same for Material Transfer"
									).format(item.idx),
									title=_("Invalid Source and Target Warehouse"),
								)

	def get_matched_items(self, item_code):
		for row in self.items:
			if row.item_code == item_code or row.original_item == item_code:
				return row

		return {}

	@frappe.whitelist()
	def get_stock_and_rate(self):
		"""
		Updates rate and availability of all the items.
		Called from Update Rate and Availability button.
		"""
		self.set_transfer_qty()
		self.set_actual_qty()
		self.calculate_rate_and_amount()

	def calculate_rate_and_amount(self, reset_outgoing_rate=True, raise_error_if_no_rate=True):
		self.set_basic_rate(reset_outgoing_rate, raise_error_if_no_rate)
		init_landed_taxes_and_totals(self)
		self.distribute_additional_costs()
		self.update_valuation_rate()
		self.set_total_incoming_outgoing_value()
		self.set_total_amount()

	def set_basic_rate(self, reset_outgoing_rate=True, raise_error_if_no_rate=True):
		"""
		Set rate for outgoing, scrapped and finished items
		"""
		# Set rate for outgoing items
		outgoing_items_cost = self.set_rate_for_outgoing_items(reset_outgoing_rate, raise_error_if_no_rate)

		items = []
		# Set basic rate for incoming items
		for d in self.get("items"):
			if d.s_warehouse or d.set_basic_rate_manually:
				continue

			if d.allow_zero_valuation_rate:
				d.basic_rate = 0.0
				items.append(d.item_code)

			elif d.is_finished_item and self.purpose == "Repack":
				d.basic_rate = self.get_basic_rate_for_repacked_items(d.transfer_qty, outgoing_items_cost)

			if not d.basic_rate and not d.allow_zero_valuation_rate:
				if self.is_new():
					raise_error_if_no_rate = False

				d.basic_rate = get_valuation_rate(
					d.item_code,
					d.t_warehouse,
					self.doctype,
					self.name,
					d.allow_zero_valuation_rate,
					currency=erpnext.get_company_currency(self.company),
					company=self.company,
					raise_error_if_no_rate=raise_error_if_no_rate,
					batch_no=d.batch_no,
					serial_and_batch_bundle=d.serial_and_batch_bundle,
				)

			# do not round off basic rate to avoid precision loss
			d.basic_rate = flt(d.basic_rate)
			d.basic_amount = flt(flt(d.transfer_qty) * flt(d.basic_rate), d.precision("basic_amount"))

		if items:
			message = ""

			if len(items) > 1:
				message = _(
					"Items rate has been updated to zero as Allow Zero Valuation Rate is checked for the following items: {0}"
				).format(", ".join(frappe.bold(item) for item in items))
			else:
				message = _(
					"Item rate has been updated to zero as Allow Zero Valuation Rate is checked for item {0}"
				).format(frappe.bold(items[0]))

			frappe.msgprint(message, alert=True)

	def set_rate_for_outgoing_items(self, reset_outgoing_rate=True, raise_error_if_no_rate=True):
		outgoing_items_cost = 0.0
		for d in self.get("items"):
			if d.s_warehouse:
				if reset_outgoing_rate:
					args = self.get_args_for_incoming_rate(d)
					rate = get_incoming_rate(args, raise_error_if_no_rate)
					if rate >= 0:
						d.basic_rate = rate

				d.basic_amount = flt(flt(d.transfer_qty) * flt(d.basic_rate), d.precision("basic_amount"))
				if not d.t_warehouse:
					outgoing_items_cost += flt(d.basic_amount)

		return outgoing_items_cost

	def get_args_for_incoming_rate(self, item):
		return frappe._dict(
			{
				"item_code": item.item_code,
				"warehouse": item.s_warehouse or item.t_warehouse,
				"posting_date": self.posting_date,
				"posting_time": self.posting_time,
				"qty": item.s_warehouse and -1 * flt(item.transfer_qty) or flt(item.transfer_qty),
				"voucher_type": self.doctype,
				"voucher_no": self.name,
				"company": self.company,
				"allow_zero_valuation": item.allow_zero_valuation_rate,
				"serial_and_batch_bundle": item.serial_and_batch_bundle,
				"voucher_detail_no": item.name,
				"batch_no": item.batch_no,
				"serial_no": item.serial_no,
			}
		)

	def get_basic_rate_for_repacked_items(self, finished_item_qty, outgoing_items_cost):
		finished_items = [
			d.item_code for d in self.get("items") if d.is_finished_item and not d.set_basic_rate_manually
		]
		if len(finished_items) == 1:
			return flt(outgoing_items_cost / finished_item_qty)
		else:
			unique_finished_items = set(finished_items)
			if unique_finished_items:
				total_fg_qty = sum(
					[
						flt(d.transfer_qty)
						for d in self.items
						if d.is_finished_item and not d.set_basic_rate_manually
					]
				)
				return flt(outgoing_items_cost / total_fg_qty)

	def distribute_additional_costs(self):
		# If no incoming items, set additional costs blank
		if not any(d.item_code for d in self.items if d.t_warehouse):
			self.additional_costs = []

		self.total_additional_costs = sum(flt(t.base_amount) for t in self.get("additional_costs"))

		if self.purpose == "Repack":
			incoming_items_cost = sum(flt(t.basic_amount) for t in self.get("items") if t.is_finished_item)
		else:
			incoming_items_cost = sum(flt(t.basic_amount) for t in self.get("items") if t.t_warehouse)

		if not incoming_items_cost:
			return

		for d in self.get("items"):
			if self.purpose == "Repack" and not d.is_finished_item:
				d.additional_cost = 0
				continue
			elif not d.t_warehouse:
				d.additional_cost = 0
				continue
			d.additional_cost = (flt(d.basic_amount) / incoming_items_cost) * self.total_additional_costs

	def update_valuation_rate(self):
		for d in self.get("items"):
			if d.transfer_qty:
				d.amount = flt(flt(d.basic_amount) + flt(d.additional_cost), d.precision("amount"))
				# Do not round off valuation rate to avoid precision loss
				d.valuation_rate = flt(d.basic_rate) + (flt(d.additional_cost) / flt(d.transfer_qty))

	def set_total_incoming_outgoing_value(self):
		self.total_incoming_value = self.total_outgoing_value = 0.0
		for d in self.get("items"):
			if d.t_warehouse:
				self.total_incoming_value += flt(d.amount)
			if d.s_warehouse:
				self.total_outgoing_value += flt(d.amount)

		self.value_difference = self.total_incoming_value - self.total_outgoing_value

	def set_total_amount(self):
		self.total_amount = sum([flt(item.amount) for item in self.get("items")])

	def set_stock_entry_type(self):
		if self.purpose:
			self.stock_entry_type = frappe.get_cached_value(
				"Stock Entry Type", {"purpose": self.purpose, "is_standard": 1}, "name"
			)

	def set_purpose_for_stock_entry(self):
		if self.stock_entry_type and not self.purpose:
			self.purpose = frappe.get_cached_value("Stock Entry Type", self.stock_entry_type, "purpose")

	def make_serial_and_batch_bundle_for_outward(self):
		if self.docstatus == 0:
			return

		serial_or_batch_items = get_serial_or_batch_items(self.items)
		if not serial_or_batch_items:
			return

		already_picked_serial_nos = []

		for row in self.items:
			if row.use_serial_batch_fields:
				continue

			if not row.s_warehouse:
				continue

			if row.item_code not in serial_or_batch_items:
				continue

			bundle_doc = None
			if row.serial_and_batch_bundle and abs(row.transfer_qty) != abs(
				frappe.get_cached_value("Serial and Batch Bundle", row.serial_and_batch_bundle, "total_qty")
			):
				bundle_doc = SerialBatchCreation(
					{
						"item_code": row.item_code,
						"warehouse": row.s_warehouse,
						"serial_and_batch_bundle": row.serial_and_batch_bundle,
						"type_of_transaction": "Outward",
						"ignore_serial_nos": already_picked_serial_nos,
						"qty": row.transfer_qty * -1,
					}
				).update_serial_and_batch_entries()
			elif not row.serial_and_batch_bundle and frappe.get_single_value(
				"Stock Settings", "auto_create_serial_and_batch_bundle_for_outward"
			):
				bundle_doc = SerialBatchCreation(
					{
						"item_code": row.item_code,
						"warehouse": row.s_warehouse,
						"posting_date": self.posting_date,
						"posting_time": self.posting_time,
						"voucher_type": self.doctype,
						"voucher_detail_no": row.name,
						"qty": row.transfer_qty * -1,
						"ignore_serial_nos": already_picked_serial_nos,
						"type_of_transaction": "Outward",
						"company": self.company,
						"do_not_submit": True,
					}
				).make_serial_and_batch_bundle()

			if not bundle_doc:
				continue

			for entry in bundle_doc.entries:
				if not entry.serial_no:
					continue

				already_picked_serial_nos.append(entry.serial_no)

			row.serial_and_batch_bundle = bundle_doc.name

	def update_stock_ledger(self, allow_negative_stock=False):
		sl_entries = []

		# make sl entries for source warehouse first
		self.get_sle_for_source_warehouse(sl_entries)

		# SLE for target warehouse
		self.get_sle_for_target_warehouse(sl_entries)

		# reverse sl entries if cancel
		if self.docstatus == 2:
			sl_entries.reverse()

		self.make_sl_entries(sl_entries, allow_negative_stock=allow_negative_stock)

	def validate_serial_batch_bundle_type(self, serial_and_batch_bundle):
		if (
			frappe.db.get_value("Serial and Batch Bundle", serial_and_batch_bundle, "type_of_transaction")
			!= "Outward"
		):
			frappe.throw(
				_(
					"The Serial and Batch Bundle {0} is not valid for this transaction. The 'Type of Transaction' should be 'Outward' instead of 'Inward' in Serial and Batch Bundle {0}"
				).format(get_link_to_form("Serial and Batch Bundle", serial_and_batch_bundle)),
				title=_("Invalid Serial and Batch Bundle"),
			)

	def get_sle_for_source_warehouse(self, sl_entries):
		for d in self.get("items"):
			if cstr(d.s_warehouse):
				if d.serial_and_batch_bundle and self.docstatus == 1:
					self.validate_serial_batch_bundle_type(d.serial_and_batch_bundle)

				sle = self.get_sl_entries(
					d,
					{
						"warehouse": cstr(d.s_warehouse),
						"actual_qty": -flt(d.transfer_qty),
						"incoming_rate": 0,
					},
				)
				if cstr(d.t_warehouse):
					sle.dependant_sle_voucher_detail_no = d.name

				if sle.serial_and_batch_bundle and self.docstatus == 2:
					bundle_id = frappe.get_cached_value(
						"Serial and Batch Bundle",
						{
							"voucher_detail_no": d.name,
							"voucher_no": self.name,
							"is_cancelled": 0,
							"type_of_transaction": "Outward",
						},
						"name",
					)

					if bundle_id:
						sle.serial_and_batch_bundle = bundle_id

				sl_entries.append(sle)

	def make_serial_and_batch_bundle_for_transfer(self):
		ids = frappe._dict(
			frappe.get_all(
				"Stock Entry Detail",
				fields=["name", "serial_and_batch_bundle"],
				filters={"parent": self.outgoing_stock_entry, "serial_and_batch_bundle": ("is", "set")},
				as_list=1,
			)
		)

		if not ids:
			return

		for d in self.get("items"):
			serial_and_batch_bundle = ids.get(d.ste_detail)
			if not serial_and_batch_bundle:
				continue

			d.serial_and_batch_bundle = self.make_package_for_transfer(
				serial_and_batch_bundle, d.s_warehouse, "Outward", do_not_submit=True
			)

	def get_sle_for_target_warehouse(self, sl_entries):
		for d in self.get("items"):
			if cstr(d.t_warehouse):
				sle = self.get_sl_entries(
					d,
					{
						"warehouse": cstr(d.t_warehouse),
						"actual_qty": flt(d.transfer_qty),
						"incoming_rate": flt(d.valuation_rate),
					},
				)

				if cstr(d.s_warehouse):
					sle.recalculate_rate = 1

				allowed_types = [
					"Material Transfer",
				]

				if self.purpose in allowed_types and d.serial_and_batch_bundle and self.docstatus == 1:
					sle.serial_and_batch_bundle = self.make_package_for_transfer(
						d.serial_and_batch_bundle, d.t_warehouse
					)

				if sle.serial_and_batch_bundle and self.docstatus == 2:
					bundle_id = frappe.get_cached_value(
						"Serial and Batch Bundle",
						{
							"voucher_detail_no": d.name,
							"voucher_no": self.name,
							"is_cancelled": 0,
							"type_of_transaction": "Inward",
						},
						"name",
					)

					if sle.serial_and_batch_bundle != bundle_id:
						sle.serial_and_batch_bundle = bundle_id

				sl_entries.append(sle)

	def get_gl_entries(self, warehouse_account):
		gl_entries = super().get_gl_entries(warehouse_account)

		total_basic_amount = sum(flt(t.basic_amount) for t in self.get("items") if t.t_warehouse)

		divide_based_on = total_basic_amount

		if self.get("additional_costs") and not total_basic_amount:
			# if total_basic_amount is 0, distribute additional charges based on qty
			divide_based_on = sum(item.qty for item in list(self.get("items")))

		item_account_wise_additional_cost = {}

		for t in self.get("additional_costs"):
			for d in self.get("items"):
				if not d.t_warehouse:
					continue

				item_account_wise_additional_cost.setdefault((d.item_code, d.name), {})
				item_account_wise_additional_cost[(d.item_code, d.name)].setdefault(
					t.expense_account, {"amount": 0.0, "base_amount": 0.0}
				)

				multiply_based_on = d.basic_amount if total_basic_amount else d.qty

				item_account_wise_additional_cost[(d.item_code, d.name)][t.expense_account]["amount"] += (
					flt(t.amount * multiply_based_on) / divide_based_on
				)

				item_account_wise_additional_cost[(d.item_code, d.name)][t.expense_account][
					"base_amount"
				] += flt(t.base_amount * multiply_based_on) / divide_based_on

		if item_account_wise_additional_cost:
			for d in self.get("items"):
				for account, amount in item_account_wise_additional_cost.get(
					(d.item_code, d.name), {}
				).items():
					if not amount:
						continue

					gl_entries.append(
						self.get_gl_dict(
							{
								"account": account,
								"against": d.expense_account,
								"cost_center": d.cost_center,
								"remarks": self.get("remarks") or _("Accounting Entry for Stock"),
								"credit_in_account_currency": flt(amount["amount"]),
								"credit": flt(amount["base_amount"]),
							},
							item=d,
						)
					)

					gl_entries.append(
						self.get_gl_dict(
							{
								"account": d.expense_account,
								"against": account,
								"cost_center": d.cost_center,
								"remarks": self.get("remarks") or _("Accounting Entry for Stock"),
								"credit": -1
								* amount[
									"base_amount"
								],  # put it as negative credit instead of debit purposefully
							},
							item=d,
						)
					)

		return process_gl_map(gl_entries, from_repost=frappe.flags.through_repost_item_valuation)

	@frappe.whitelist()
	def get_item_details(self, args=None, for_update=False):
		item = frappe.qb.DocType("Item")
		item_default = frappe.qb.DocType("Item Default")

		query = (
			frappe.qb.from_(item)
			.left_join(item_default)
			.on((item.name == item_default.parent) & (item_default.company == self.company))
			.select(
				item.name,
				item.stock_uom,
				item.description,
				item.image,
				item.item_name,
				item.item_group,
				item.has_batch_no,
				item.sample_quantity,
				item.has_serial_no,
				item.allow_alternative_item,
				item_default.expense_account,
				item_default.buying_cost_center,
			)
			.where(
				(item.name == args.get("item_code"))
				& (item.disabled == 0)
				& (
					(item.end_of_life.isnull())
					| (item.end_of_life < "1900-01-01")
					| (item.end_of_life > nowdate())
				)
			)
		)
		item = query.run(as_dict=True)

		if not item:
			frappe.throw(
				_("Item {0} is not active or end of life has been reached").format(args.get("item_code"))
			)

		item = item[0]
		item_group_defaults = get_item_group_defaults(item.name, self.company)
		brand_defaults = get_brand_defaults(item.name, self.company)

		ret = frappe._dict(
			{
				"uom": item.stock_uom,
				"stock_uom": item.stock_uom,
				"description": item.description,
				"image": item.image,
				"item_name": item.item_name,
				"cost_center": get_default_cost_center(
					args, item, item_group_defaults, brand_defaults, self.company
				),
				"qty": args.get("qty"),
				"transfer_qty": args.get("qty"),
				"conversion_factor": 1,
				"actual_qty": 0,
				"basic_rate": 0,
				"has_serial_no": item.has_serial_no,
				"has_batch_no": item.has_batch_no,
				"sample_quantity": item.sample_quantity,
				"expense_account": item.expense_account or item_group_defaults.get("expense_account"),
			}
		)

		ret["allow_alternative_item"] = item.allow_alternative_item

		# update uom
		if args.get("uom") and for_update:
			ret.update(get_uom_details(args.get("item_code"), args.get("uom"), args.get("qty")))

		if self.purpose == "Material Issue":
			ret["expense_account"] = item.get("expense_account") or item_group_defaults.get("expense_account")

		if not ret.get("expense_account"):
			ret["expense_account"] = frappe.get_cached_value(
				"Company", self.company, "stock_adjustment_account"
			)

		for company_field, field in {
			"stock_adjustment_account": "expense_account",
			"cost_center": "cost_center",
		}.items():
			if not ret.get(field):
				ret[field] = frappe.get_cached_value("Company", self.company, company_field)

		args["posting_date"] = self.posting_date
		args["posting_time"] = self.posting_time

		stock_and_rate = get_warehouse_details(args) if args.get("warehouse") else {}
		ret.update(stock_and_rate)

		barcode_data = get_barcode_data(item_code=item.name)
		if barcode_data and len(barcode_data.get(item.name)) == 1:
			ret["barcode"] = barcode_data.get(item.name)[0]

		return ret

	@frappe.whitelist()
	def set_items_for_stock_in(self):
		self.items = []

		if self.outgoing_stock_entry and self.purpose == "Material Transfer":
			doc = frappe.get_doc("Stock Entry", self.outgoing_stock_entry)

			if doc.per_transferred == 100:
				frappe.throw(_("Goods are already received against the outward entry {0}").format(doc.name))

			for d in doc.items:
				self.append(
					"items",
					{
						"s_warehouse": d.t_warehouse,
						"item_code": d.item_code,
						"qty": d.qty,
						"uom": d.uom,
						"against_stock_entry": d.parent,
						"ste_detail": d.name,
						"stock_uom": d.stock_uom,
						"conversion_factor": d.conversion_factor,
					},
				)

	@frappe.whitelist()
	def get_items(self):
		self.set("items", [])

		if not self.posting_date or not self.posting_time:
			frappe.throw(_("Posting date and posting time is mandatory"))

		self.set_actual_qty()
		self.validate_customer_provided_item()
		self.calculate_rate_and_amount(raise_error_if_no_rate=False)


	def add_to_stock_entry_detail(self, item_dict):
		precision = frappe.get_precision("Stock Entry Detail", "qty")
		for d in item_dict:
			item_row = item_dict[d]

			child_qty = flt(item_row["qty"], precision)
			if not self.is_return and child_qty <= 0 and not item_row.get("is_scrap_item"):
				continue

			se_child = self.append("items")
			stock_uom = item_row.get("stock_uom") or frappe.db.get_value("Item", d, "stock_uom")
			se_child.s_warehouse = item_row.get("from_warehouse")
			se_child.t_warehouse = item_row.get("to_warehouse")
			se_child.item_code = item_row.get("item_code") or cstr(d)
			se_child.uom = item_row["uom"] if item_row.get("uom") else stock_uom
			se_child.stock_uom = stock_uom
			se_child.qty = child_qty
			se_child.allow_alternative_item = item_row.get("allow_alternative_item", 0)
			se_child.cost_center = item_row.get("cost_center") or get_default_cost_center(
				item_row, company=self.company
			)
			se_child.is_finished_item = item_row.get("is_finished_item", 0)
			se_child.is_scrap_item = item_row.get("is_scrap_item", 0)
			se_child.po_detail = item_row.get("po_detail")
			se_child.sco_rm_detail = item_row.get("sco_rm_detail")

			for field in [
				"original_item",
				"expense_account",
				"description",
				"item_name",
				"serial_and_batch_bundle",
				"allow_zero_valuation_rate",
				"use_serial_batch_fields",
				"batch_no",
				"serial_no",
			]:
				if item_row.get(field):
					se_child.set(field, item_row.get(field))

			if se_child.s_warehouse is None:
				se_child.s_warehouse = self.from_warehouse
			if se_child.t_warehouse is None:
				se_child.t_warehouse = self.to_warehouse

			# in stock uom
			se_child.conversion_factor = flt(item_row.get("conversion_factor")) or 1
			se_child.transfer_qty = flt(
				item_row["qty"] * se_child.conversion_factor, se_child.precision("qty")
			)

	def validate_with_material_request(self):
		for item in self.get("items"):
			material_request = item.material_request or None
			material_request_item = item.material_request_item or None
			if self.purpose == "Material Transfer" and self.outgoing_stock_entry:
				parent_se = frappe.get_value(
					"Stock Entry Detail",
					item.ste_detail,
					["material_request", "material_request_item"],
					as_dict=True,
				)
				if parent_se:
					material_request = parent_se.material_request
					material_request_item = parent_se.material_request_item

			if material_request:
				mreq_item = frappe.db.get_value(
					"Material Request Item",
					{"name": material_request_item, "parent": material_request},
					["item_code", "warehouse", "idx"],
					as_dict=True,
				)
				if mreq_item.item_code != item.item_code:
					frappe.throw(
						_("Item for row {0} does not match Material Request").format(item.idx),
						frappe.MappingMismatchError,
					)
				elif self.purpose == "Material Transfer" and self.add_to_transit:
					continue

	def validate_batch(self):
		if self.purpose in [
			"Repack",
		]:
			for item in self.get("items"):
				if item.batch_no:
					disabled = frappe.db.get_value("Batch", item.batch_no, "disabled")
					if disabled == 0:
						expiry_date = frappe.db.get_value("Batch", item.batch_no, "expiry_date")
						if expiry_date:
							if getdate(self.posting_date) > getdate(expiry_date):
								frappe.throw(
									_("Batch {0} of Item {1} has expired.").format(
										item.batch_no, item.item_code
									)
								)
					else:
						frappe.throw(
							_("Batch {0} of Item {1} is disabled.").format(item.batch_no, item.item_code)
						)

	def update_transferred_qty(self):
		if self.purpose == "Material Transfer" and self.outgoing_stock_entry:
			stock_entries = {}
			stock_entries_child_list = []
			for d in self.items:
				if not (d.against_stock_entry and d.ste_detail):
					continue

				stock_entries_child_list.append(d.ste_detail)
				transferred_qty = frappe.get_all(
					"Stock Entry Detail",
					fields=["sum(transfer_qty) as qty"],
					filters={
						"against_stock_entry": d.against_stock_entry,
						"ste_detail": d.ste_detail,
						"docstatus": 1,
					},
				)

				if d.docstatus == 1:
					transfer_qty = frappe.get_value("Stock Entry Detail", d.ste_detail, "transfer_qty")

					if transferred_qty and transferred_qty[0]:
						if transferred_qty[0].qty > transfer_qty:
							frappe.throw(
								_(
									"Row {0}: Transferred quantity cannot be greater than the requested quantity."
								).format(d.idx)
							)

				stock_entries[(d.against_stock_entry, d.ste_detail)] = (
					transferred_qty[0].qty if transferred_qty and transferred_qty[0] else 0.0
				) or 0.0

			if not stock_entries:
				return None

			cond = ""
			for data, transferred_qty in stock_entries.items():
				cond += """ WHEN (parent = {} and name = {}) THEN {}
					""".format(
					frappe.db.escape(data[0]),
					frappe.db.escape(data[1]),
					transferred_qty,
				)

			if stock_entries_child_list:
				frappe.db.sql(
					""" UPDATE `tabStock Entry Detail`
					SET
						transferred_qty = CASE {cond} END
					WHERE
						name in ({ste_details}) """.format(
						cond=cond, ste_details=",".join(["%s"] * len(stock_entries_child_list))
					),
					tuple(stock_entries_child_list),
				)

			args = {
				"source_dt": "Stock Entry Detail",
				"target_field": "transferred_qty",
				"target_ref_field": "qty",
				"target_dt": "Stock Entry Detail",
				"join_field": "ste_detail",
				"target_parent_dt": "Stock Entry",
				"target_parent_field": "per_transferred",
				"source_field": "qty",
				"percent_join_field": "against_stock_entry",
			}

			self._update_percent_field_in_targets(args, update_modified=True)

	def set_material_request_transfer_status(self, status):
		material_requests = []
		if self.outgoing_stock_entry:
			parent_se = frappe.get_value("Stock Entry", self.outgoing_stock_entry, "add_to_transit")

		for item in self.items:
			material_request = item.get("material_request")
			if self.purpose == "Material Transfer" and material_request not in material_requests:
				if self.outgoing_stock_entry and parent_se:
					material_request = frappe.get_value(
						"Stock Entry Detail", item.ste_detail, "material_request"
					)

			if material_request and material_request not in material_requests:
				material_requests.append(material_request)
				if status == "Completed":
					qty = get_transferred_qty(material_request)
					if qty.get("transfer_qty") > qty.get("transferred_qty"):
						status = "In Transit"

				frappe.db.set_value("Material Request", material_request, "transfer_status", status)

	def update_pick_list_status(self):
		from erpnext.stock.doctype.pick_list.pick_list import update_pick_list_status

		update_pick_list_status(self.pick_list)

	def set_missing_values(self):
		"Updates rate and availability of all the items of mapped doc."
		self.set_transfer_qty()
		self.set_actual_qty()
		self.calculate_rate_and_amount()


@frappe.whitelist()
def move_sample_to_retention_warehouse(company, items):
	from erpnext.stock.doctype.serial_and_batch_bundle.test_serial_and_batch_bundle import (
		get_batch_from_bundle,
	)
	from erpnext.stock.serial_batch_bundle import SerialBatchCreation

	if isinstance(items, str):
		items = json.loads(items)
	retention_warehouse = frappe.db.get_single_value("Stock Settings", "sample_retention_warehouse")
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = company
	stock_entry.purpose = "Material Transfer"
	stock_entry.set_stock_entry_type()
	for item in items:
		if item.get("sample_quantity") and item.get("serial_and_batch_bundle"):
			batch_no = get_batch_from_bundle(item.get("serial_and_batch_bundle"))
			sample_quantity = validate_sample_quantity(
				item.get("item_code"),
				item.get("sample_quantity"),
				item.get("transfer_qty") or item.get("qty"),
				batch_no,
			)

			if sample_quantity:
				cls_obj = SerialBatchCreation(
					{
						"type_of_transaction": "Outward",
						"serial_and_batch_bundle": item.get("serial_and_batch_bundle"),
						"item_code": item.get("item_code"),
						"warehouse": item.get("t_warehouse"),
					}
				)

				cls_obj.duplicate_package()

				stock_entry.append(
					"items",
					{
						"item_code": item.get("item_code"),
						"s_warehouse": item.get("t_warehouse"),
						"t_warehouse": retention_warehouse,
						"qty": item.get("sample_quantity"),
						"basic_rate": item.get("valuation_rate"),
						"uom": item.get("uom"),
						"stock_uom": item.get("stock_uom"),
						"conversion_factor": item.get("conversion_factor") or 1.0,
						"serial_and_batch_bundle": cls_obj.serial_and_batch_bundle,
					},
				)
	if stock_entry.get("items"):
		return stock_entry.as_dict()


@frappe.whitelist()
def make_stock_in_entry(source_name, target_doc=None):
	def set_missing_values(source, target):
		target.stock_entry_type = "Material Transfer"
		target.set_missing_values()

		if not frappe.db.get_single_value("Stock Settings", "use_serial_batch_fields"):
			target.make_serial_and_batch_bundle_for_transfer()

	def update_item(source_doc, target_doc, source_parent):
		target_doc.t_warehouse = ""

		if source_doc.material_request_item and source_doc.material_request:
			add_to_transit = frappe.db.get_value("Stock Entry", source_name, "add_to_transit")
			if add_to_transit:
				warehouse = frappe.get_value(
					"Material Request Item", source_doc.material_request_item, "warehouse"
				)
				target_doc.t_warehouse = warehouse

		target_doc.s_warehouse = source_doc.t_warehouse
		target_doc.qty = source_doc.qty - source_doc.transferred_qty

	doclist = get_mapped_doc(
		"Stock Entry",
		source_name,
		{
			"Stock Entry": {
				"doctype": "Stock Entry",
				"field_map": {"name": "outgoing_stock_entry"},
				"validation": {"docstatus": ["=", 1]},
			},
			"Stock Entry Detail": {
				"doctype": "Stock Entry Detail",
				"field_map": {
					"name": "ste_detail",
					"parent": "against_stock_entry",
					"serial_no": "serial_no",
					"batch_no": "batch_no",
				},
				"postprocess": update_item,
				"condition": lambda doc: flt(doc.qty) - flt(doc.transferred_qty) > 0.00001,
			},
		},
		target_doc,
		set_missing_values,
	)

	return doclist


@frappe.whitelist()
def get_uom_details(item_code, uom, qty):
	"""Returns dict `{"conversion_factor": [value], "transfer_qty": qty * [value]}`
	:param args: dict with `item_code`, `uom` and `qty`"""
	conversion_factor = get_conversion_factor(item_code, uom).get("conversion_factor")

	if not conversion_factor:
		frappe.msgprint(_("UOM conversion factor required for UOM: {0} in Item: {1}").format(uom, item_code))
		ret = {"uom": ""}
	else:
		ret = {
			"conversion_factor": flt(conversion_factor),
			"transfer_qty": flt(qty) * flt(conversion_factor),
		}
	return ret


@frappe.whitelist()
def get_expired_batch_items():
	from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import get_auto_batch_nos

	expired_batches = get_expired_batches()
	if not expired_batches:
		return []

	expired_batches_stock = get_auto_batch_nos(
		frappe._dict(
			{
				"batch_no": list(expired_batches.keys()),
				"for_stock_levels": True,
			}
		)
	)

	for row in expired_batches_stock:
		row.update(expired_batches.get(row.batch_no))

	return expired_batches_stock


def get_expired_batches():
	batch = frappe.qb.DocType("Batch")

	data = (
		frappe.qb.from_(batch)
		.select(batch.item, batch.name.as_("batch_no"), batch.stock_uom)
		.where((batch.expiry_date <= nowdate()) & (batch.expiry_date.isnotnull()))
	).run(as_dict=True)

	if not data:
		return []

	expired_batches = frappe._dict()
	for row in data:
		expired_batches[row.batch_no] = row

	return expired_batches


@frappe.whitelist()
def get_warehouse_details(args):
	if isinstance(args, str):
		args = json.loads(args)

	args = frappe._dict(args)

	ret = {}
	if args.warehouse and args.item_code:
		args.update(
			{
				"posting_date": args.posting_date,
				"posting_time": args.posting_time,
			}
		)
		ret = {
			"actual_qty": get_previous_sle(args).get("qty_after_transaction") or 0,
			"basic_rate": get_incoming_rate(args),
		}
	return ret


@frappe.whitelist()
def validate_sample_quantity(item_code, sample_quantity, qty, batch_no=None):
	if cint(qty) < cint(sample_quantity):
		frappe.throw(
			_("Sample quantity {0} cannot be more than received quantity {1}").format(sample_quantity, qty)
		)
	retention_warehouse = frappe.db.get_single_value("Stock Settings", "sample_retention_warehouse")
	retainted_qty = 0
	if batch_no:
		retainted_qty = get_batch_qty(batch_no, retention_warehouse, item_code)
	max_retain_qty = frappe.get_value("Item", item_code, "sample_quantity")
	if retainted_qty >= max_retain_qty:
		frappe.msgprint(
			_(
				"Maximum Samples - {0} have already been retained for Batch {1} and Item {2} in Batch {3}."
			).format(retainted_qty, batch_no, item_code, batch_no),
			alert=True,
		)
		sample_quantity = 0
	qty_diff = max_retain_qty - retainted_qty
	if cint(sample_quantity) > cint(qty_diff):
		frappe.msgprint(
			_("Maximum Samples - {0} can be retained for Batch {1} and Item {2}.").format(
				max_retain_qty, batch_no, item_code
			),
			alert=True,
		)
		sample_quantity = qty_diff
	return sample_quantity

def get_transferred_qty(material_request):
	sed = DocType("Stock Entry Detail")

	query = (
		frappe.qb.from_(sed)
		.select(
			Sum(sed.transfer_qty).as_("transfer_qty"),
			Sum(sed.transferred_qty).as_("transferred_qty"),
		)
		.where((sed.material_request == material_request) & (sed.docstatus == 1))
	).run(as_dict=True)

	return query[0]
