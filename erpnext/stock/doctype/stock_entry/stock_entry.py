".join(row.serial_nos)
			if row.serial_nos and not row.batches_to_be_consume
			else "",
			"use_serial_batch_fields": use_serial_batch_fields,
		}

		if self.is_return:
			ste_item_details["to_warehouse"] = item.s_warehouse

		if use_serial_batch_fields and not row.serial_no and row.batches_to_be_consume:
			for batch_no, batch_qty in row.batches_to_be_consume.items():
				ste_item_details.update(
					{
						"batch_no": batch_no,
						"qty": batch_qty,
					}
				)

				self.add_to_stock_entry_detail({item.item_code: ste_item_details})
		else:
			self.add_to_stock_entry_detail({item.item_code: ste_item_details})

	@staticmethod
	def get_serial_nos_based_on_transferred_batch(batch_no, serial_nos) -> list:
		serial_nos = frappe.get_all(
			"Serial No",
			filters={"batch_no": batch_no, "name": ("in", serial_nos), "warehouse": ("is", "not set")},
			order_by="creation",
		)

		return [d.name for d in serial_nos]

	def get_pending_raw_materials(self, backflush_based_on=None):
		"""
		issue (item quantity) that is pending to issue or desire to transfer,
		whichever is less
		"""
		item_dict = self.get_pro_order_required_items(backflush_based_on)

		max_qty = flt(self.pro_doc.qty)

		allow_overproduction = False
		overproduction_percentage = flt(
			frappe.db.get_single_value("Manufacturing Settings", "overproduction_percentage_for_work_order")
		)

		to_transfer_qty = flt(self.pro_doc.material_transferred_for_manufacturing) + flt(
			self.fg_completed_qty
		)
		transfer_limit_qty = max_qty + ((max_qty * overproduction_percentage) / 100)

		if transfer_limit_qty >= to_transfer_qty:
			allow_overproduction = True

		for item, item_details in item_dict.items():
			pending_to_issue = flt(item_details.required_qty) - flt(item_details.transferred_qty)
			desire_to_transfer = flt(self.fg_completed_qty) * flt(item_details.required_qty) / max_qty

			if (
				desire_to_transfer <= pending_to_issue
				or (desire_to_transfer > 0 and backflush_based_on == "Material Transferred for Manufacture")
				or allow_overproduction
			):
				# "No need for transfer but qty still pending to transfer" case can occur
				# when transferring multiple RM in different Stock Entries
				item_dict[item]["qty"] = desire_to_transfer if (desire_to_transfer > 0) else pending_to_issue
			elif pending_to_issue > 0:
				item_dict[item]["qty"] = pending_to_issue
			else:
				item_dict[item]["qty"] = 0

		# delete items with 0 qty
		list_of_items = list(item_dict.keys())
		for item in list_of_items:
			if not item_dict[item]["qty"]:
				del item_dict[item]

		# show some message
		if not len(item_dict):
			frappe.msgprint(_("""All items have already been transferred for this Work Order."""))

		return item_dict

	def get_pro_order_required_items(self, backflush_based_on=None):
		"""
		Gets Work Order Required Items only if Stock Entry purpose is **Material Transferred for Manufacture**.
		"""
		item_dict, job_card_items = frappe._dict(), []
		work_order = frappe.get_doc("Work Order", self.work_order)

		consider_job_card = work_order.transfer_material_against == "Job Card" and self.get("job_card")
		if consider_job_card:
			job_card_items = self.get_job_card_item_codes(self.get("job_card"))

		if not frappe.db.get_value("Warehouse", work_order.wip_warehouse, "is_group"):
			wip_warehouse = work_order.wip_warehouse
		else:
			wip_warehouse = None

		for d in work_order.get("required_items"):
			if consider_job_card and (d.item_code not in job_card_items):
				continue

			transfer_pending = flt(d.required_qty) > flt(d.transferred_qty)
			can_transfer = transfer_pending or (backflush_based_on == "Material Transferred for Manufacture")

			if not can_transfer:
				continue

			if d.include_item_in_manufacturing:
				item_row = d.as_dict()
				item_row["idx"] = len(item_dict) + 1

				if consider_job_card:
					job_card_item = frappe.db.get_value(
						"Job Card Item", {"item_code": d.item_code, "parent": self.get("job_card")}
					)
					item_row["job_card_item"] = job_card_item or None

				if d.source_warehouse and not frappe.db.get_value(
					"Warehouse", d.source_warehouse, "is_group"
				):
					item_row["from_warehouse"] = d.source_warehouse

				item_row["to_warehouse"] = wip_warehouse
				if item_row["allow_alternative_item"]:
					item_row["allow_alternative_item"] = work_order.allow_alternative_item

				item_dict.setdefault(d.item_code, item_row)

		return item_dict

	def get_job_card_item_codes(self, job_card=None):
		if not job_card:
			return []

		job_card_items = frappe.get_all(
			"Job Card Item", filters={"parent": job_card}, fields=["item_code"], distinct=True
		)
		return [d.item_code for d in job_card_items]

	def add_to_stock_entry_detail(self, item_dict, bom_no=None):
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
			se_child.subcontracted_item = item_row.get("main_item_code")
			se_child.cost_center = item_row.get("cost_center") or get_default_cost_center(
				item_row, company=self.company
			)
			se_child.is_finished_item = item_row.get("is_finished_item", 0)
			se_child.is_scrap_item = item_row.get("is_scrap_item", 0)
			se_child.po_detail = item_row.get("po_detail")
			se_child.sco_rm_detail = item_row.get("sco_rm_detail")

			for field in [
				self.subcontract_data.rm_detail_field,
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

			se_child.bom_no = bom_no  # to be assigned for finished item
			se_child.job_card_item = item_row.get("job_card_item") if self.get("job_card") else None

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
			"Material Transfer for Manufacture",
			"Manufacture",
			"Repack",
			"Material Transfer",
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

	def update_subcontract_order_supplied_items(self):
		if self.get(self.subcontract_data.order_field) and (
			self.purpose in ["Material Transfer", "Material Transfer"] or self.is_return
		):
			# Get Subcontract Order Supplied Items Details
			order_supplied_items = frappe.db.get_all(
				self.subcontract_data.order_supplied_items_field,
				filters={"parent": self.get(self.subcontract_data.order_field)},
				fields=["name", "rm_item_code", "reserve_warehouse"],
			)

			# Get Items Supplied in Stock Entries against Subcontract Order
			supplied_items = get_supplied_items(
				self.get(self.subcontract_data.order_field),
				self.subcontract_data.rm_detail_field,
				self.subcontract_data.order_field,
			)

			for row in order_supplied_items:
				key, item = row.name, {}
				if not supplied_items.get(key):
					# no stock transferred against Subcontract Order Supplied Items row
					item = {"supplied_qty": 0, "returned_qty": 0, "total_supplied_qty": 0}
				else:
					item = supplied_items.get(key)

				frappe.db.set_value(self.subcontract_data.order_supplied_items_field, row.name, item)

			# RM Item-Reserve Warehouse Dict
			item_wh = {x.get("rm_item_code"): x.get("reserve_warehouse") for x in order_supplied_items}

			for d in self.get("items"):
				# Update reserved sub contracted quantity in bin based on Supplied Item Details and
				item_code = d.get("original_item") or d.get("item_code")
				reserve_warehouse = item_wh.get(item_code)
				if not (reserve_warehouse and item_code):
					continue
				stock_bin = get_bin(item_code, reserve_warehouse)
				stock_bin.update_reserved_qty_for_sub_contracting()

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

	def update_quality_inspection(self):
		if self.inspection_required:
			reference_type = reference_name = ""
			if self.docstatus == 1:
				reference_name = self.name
				reference_type = "Stock Entry"

			for d in self.items:
				if d.quality_inspection:
					frappe.db.set_value(
						"Quality Inspection",
						d.quality_inspection,
						{"reference_type": reference_type, "reference_name": reference_name},
					)

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

	def set_serial_no_batch_for_finished_good(self):
		if not (
			(self.pro_doc.has_serial_no or self.pro_doc.has_batch_no)
			and frappe.db.get_single_value("Manufacturing Settings", "make_serial_no_batch_from_work_order")
		):
			return

		for d in self.items:
			if (
				d.is_finished_item
				and d.item_code == self.pro_doc.production_item
				and not d.serial_and_batch_bundle
			):
				serial_nos = self.get_available_serial_nos()
				if serial_nos:
					row = frappe._dict({"serial_nos": serial_nos[0 : cint(d.qty)]})

					id = create_serial_and_batch_bundle(
						self,
						row,
						frappe._dict(
							{
								"item_code": d.item_code,
								"warehouse": d.t_warehouse,
							}
						),
					)

					d.serial_and_batch_bundle = id
					d.use_serial_batch_fields = 0

	def get_available_serial_nos(self) -> list[str]:
		serial_nos = []
		data = frappe.get_all(
			"Serial No",
			filters={
				"item_code": self.pro_doc.production_item,
				"warehouse": ("is", "not set"),
				"status": "Inactive",
				"work_order": self.pro_doc.name,
			},
			fields=["name"],
			order_by="creation asc",
		)

		for row in data:
			serial_nos.append(row.name)

		return serial_nos

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
def get_work_order_details(work_order, company):
	work_order = frappe.get_doc("Work Order", work_order)
	pending_qty_to_produce = flt(work_order.qty) - flt(work_order.produced_qty)

	return {
		"from_bom": 1,
		"bom_no": work_order.bom_no,
		"use_multi_level_bom": work_order.use_multi_level_bom,
		"wip_warehouse": work_order.wip_warehouse,
		"fg_warehouse": work_order.fg_warehouse,
		"fg_completed_qty": pending_qty_to_produce,
	}


def get_consumed_operating_cost(work_order, bom_no, operation_id=None):
	table = frappe.qb.DocType("Stock Entry")
	child_table = frappe.qb.DocType("Landed Cost Taxes and Charges")
	query = (
		frappe.qb.from_(child_table)
		.join(table)
		.on(child_table.parent == table.name)
		.select(
			Sum(child_table.amount).as_("consumed_cost"),
			Sum(child_table.qty).as_("consumed_qty"),
		)
		.where(
			(table.docstatus == 1)
			& (table.work_order == work_order)
			& (table.purpose == "Manufacture")
			& (table.bom_no == bom_no)
			& (child_table.has_operating_cost == 1)
		)
	)

	if operation_id:
		query = query.where(child_table.operation_id == operation_id)

	data = query.run(as_dict=True)
	return data[0] if data else frappe._dict()


def get_operating_cost_per_unit(work_order=None, bom_no=None):
	operating_cost_per_unit = 0
	if work_order:
		if (
			bom_no
			and frappe.db.get_single_value(
				"Manufacturing Settings", "set_op_cost_and_scrap_from_sub_assemblies"
			)
			and frappe.get_cached_value("Work Order", work_order, "use_multi_level_bom")
		):
			return get_op_cost_from_sub_assemblies(bom_no)

		if not bom_no:
			bom_no = work_order.bom_no

		for d in work_order.get("operations"):
			if flt(d.completed_qty):
				operating_cost_per_unit += flt(d.actual_operating_cost) / flt(d.completed_qty)
			elif work_order.qty:
				operating_cost_per_unit += flt(d.planned_operating_cost) / flt(work_order.qty)

	# Get operating cost from BOM if not found in work_order.
	if not operating_cost_per_unit and bom_no:
		bom = frappe.db.get_value("BOM", bom_no, ["operating_cost", "quantity"], as_dict=1)
		if bom.quantity:
			operating_cost_per_unit = flt(bom.operating_cost) / flt(bom.quantity)

	return operating_cost_per_unit


def get_used_alternative_items(
	subcontract_order=None, subcontract_order_field="subcontracting_order", work_order=None
):
	cond = ""

	if subcontract_order:
		cond = f"and ste.purpose = 'Send to Subcontractor' and ste.{subcontract_order_field} = '{subcontract_order}'"
	elif work_order:
		cond = f"and ste.purpose = 'Material Transfer for Manufacture' and ste.work_order = '{work_order}'"

	if not cond:
		return {}

	used_alternative_items = {}
	data = frappe.db.sql(
		f""" select sted.original_item, sted.uom, sted.conversion_factor,
			sted.item_code, sted.item_name, sted.conversion_factor,sted.stock_uom, sted.description
		from
			`tabStock Entry` ste, `tabStock Entry Detail` sted
		where
			sted.parent = ste.name and ste.docstatus = 1 and sted.original_item !=  sted.item_code
			{cond} """,
		as_dict=1,
	)

	for d in data:
		used_alternative_items[d.original_item] = d

	return used_alternative_items


def get_valuation_rate_for_finished_good_entry(work_order):
	work_order_qty = flt(
		frappe.get_cached_value("Work Order", work_order, "material_transferred_for_manufacturing")
	)

	field = "(SUM(total_outgoing_value) / %s) as valuation_rate" % (work_order_qty)

	stock_data = frappe.get_all(
		"Stock Entry",
		fields=field,
		filters={
			"docstatus": 1,
			"purpose": "Material Transfer for Manufacture",
			"work_order": work_order,
		},
	)

	if stock_data:
		return stock_data[0].valuation_rate


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


def get_supplied_items(
	subcontract_order, rm_detail_field="sco_rm_detail", subcontract_order_field="subcontracting_order"
):
	fields = [
		"`tabStock Entry Detail`.`transfer_qty`",
		"`tabStock Entry`.`is_return`",
		f"`tabStock Entry Detail`.`{rm_detail_field}`",
		"`tabStock Entry Detail`.`item_code`",
	]

	filters = [
		["Stock Entry", "docstatus", "=", 1],
		["Stock Entry", subcontract_order_field, "=", subcontract_order],
	]

	supplied_item_details = {}
	for row in frappe.get_all("Stock Entry", fields=fields, filters=filters):
		if not row.get(rm_detail_field):
			continue

		key = row.get(rm_detail_field)
		if key not in supplied_item_details:
			supplied_item_details.setdefault(
				key, frappe._dict({"supplied_qty": 0, "returned_qty": 0, "total_supplied_qty": 0})
			)

		supplied_item = supplied_item_details[key]

		if row.is_return:
			supplied_item.returned_qty += row.transfer_qty
		else:
			supplied_item.supplied_qty += row.transfer_qty

		supplied_item.total_supplied_qty = flt(supplied_item.supplied_qty) - flt(supplied_item.returned_qty)

	return supplied_item_details


@frappe.whitelist()
def get_items_from_subcontract_order(source_name, target_doc=None):
	from erpnext.controllers.subcontracting_controller import make_rm_stock_entry

	if isinstance(target_doc, str):
		target_doc = frappe.get_doc(json.loads(target_doc))

	order_doctype = "Purchase Order"
	target_doc = make_rm_stock_entry(
		subcontract_order=source_name, order_doctype=order_doctype, target_doc=target_doc
	)

	return target_doc


def get_available_materials(work_order, stock_entry_doc=None) -> dict:
	data = get_stock_entry_data(work_order, stock_entry_doc=stock_entry_doc)

	available_materials = {}
	for row in data:
		key = (row.item_code, row.warehouse)
		if row.purpose != "Material Transfer for Manufacture":
			key = (row.item_code, row.s_warehouse)

		if stock_entry_doc and stock_entry_doc.purpose == "Disassemble":
			key = (row.item_code, row.s_warehouse or row.warehouse)

		if key not in available_materials:
			available_materials.setdefault(
				key,
				frappe._dict(
					{"item_details": row, "batch_details": defaultdict(float), "qty": 0, "serial_nos": []}
				),
			)

		item_data = available_materials[key]

		if row.purpose == "Material Transfer for Manufacture" or (
			stock_entry_doc and stock_entry_doc.purpose == "Disassemble" and row.purpose == "Manufacture"
		):
			item_data.qty += row.qty
			if row.batch_no:
				item_data.batch_details[row.batch_no] += row.qty

			elif row.batch_nos:
				for batch_no, qty in row.batch_nos.items():
					item_data.batch_details[batch_no] += qty

			if row.serial_no:
				item_data.serial_nos.extend(get_serial_nos(row.serial_no))
				item_data.serial_nos.sort()

			elif row.serial_nos:
				item_data.serial_nos.extend(get_serial_nos(row.serial_nos))
				item_data.serial_nos.sort()
		else:
			# Consume raw material qty in case of 'Manufacture' or 'Material Consumption for Manufacture'

			item_data.qty -= row.qty
			if row.batch_no:
				item_data.batch_details[row.batch_no] -= row.qty

			elif row.batch_nos:
				for batch_no, qty in row.batch_nos.items():
					item_data.batch_details[batch_no] += qty

			if row.serial_no:
				for serial_no in get_serial_nos(row.serial_no):
					if serial_no in item_data.serial_nos:
						item_data.serial_nos.remove(serial_no)

			elif row.serial_nos:
				for serial_no in get_serial_nos(row.serial_nos):
					if serial_no in item_data.serial_nos:
						item_data.serial_nos.remove(serial_no)

	return available_materials


def get_stock_entry_data(work_order, stock_entry_doc=None):
	from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
		get_voucher_wise_serial_batch_from_bundle,
	)

	stock_entry = frappe.qb.DocType("Stock Entry")
	stock_entry_detail = frappe.qb.DocType("Stock Entry Detail")

	data = (
		frappe.qb.from_(stock_entry)
		.from_(stock_entry_detail)
		.select(
			stock_entry_detail.item_name,
			stock_entry_detail.original_item,
			stock_entry_detail.item_code,
			stock_entry_detail.qty,
			(stock_entry_detail.t_warehouse).as_("warehouse"),
			(stock_entry_detail.s_warehouse).as_("s_warehouse"),
			stock_entry_detail.description,
			stock_entry_detail.stock_uom,
			stock_entry_detail.expense_account,
			stock_entry_detail.cost_center,
			stock_entry_detail.serial_and_batch_bundle,
			stock_entry_detail.batch_no,
			stock_entry_detail.serial_no,
			stock_entry.purpose,
			stock_entry.name,
		)
		.where(
			(stock_entry.name == stock_entry_detail.parent)
			& (stock_entry.work_order == work_order)
			& (stock_entry.docstatus == 1)
		)
		.orderby(stock_entry.creation, stock_entry_detail.item_code, stock_entry_detail.idx)
	)

	if stock_entry_doc and stock_entry_doc.purpose == "Disassemble":
		data = data.where(
			stock_entry.purpose.isin(
				[
					"Disassemble",
					"Manufacture",
				]
			)
		)

		data = data.where(stock_entry.name != stock_entry_doc.name)
	else:
		data = data.where(
			stock_entry.purpose.isin(
				[
					"Manufacture",
					"Material Consumption for Manufacture",
					"Material Transfer for Manufacture",
				]
			)
		)

		data = data.where(stock_entry_detail.s_warehouse.isnotnull())

	data = data.run(as_dict=1)

	if not data:
		return []

	voucher_nos = [row.get("name") for row in data if row.get("name")]
	if voucher_nos:
		bundle_data = get_voucher_wise_serial_batch_from_bundle(voucher_no=voucher_nos)
		for row in data:
			key = (row.item_code, row.warehouse, row.name)
			if row.purpose != "Material Transfer for Manufacture":
				key = (row.item_code, row.s_warehouse, row.name)

			if stock_entry_doc and stock_entry_doc.purpose == "Disassemble":
				key = (row.item_code, row.s_warehouse or row.warehouse, row.name)

			if bundle_data.get(key):
				row.update(bundle_data.get(key))

	return data


def create_serial_and_batch_bundle(parent_doc, row, child, type_of_transaction=None):
	item_details = frappe.get_cached_value(
		"Item", child.item_code, ["has_serial_no", "has_batch_no"], as_dict=1
	)

	if not (item_details.has_serial_no or item_details.has_batch_no):
		return

	if not type_of_transaction:
		type_of_transaction = "Inward"

	doc = frappe.get_doc(
		{
			"doctype": "Serial and Batch Bundle",
			"voucher_type": "Stock Entry",
			"item_code": child.item_code,
			"warehouse": child.warehouse,
			"type_of_transaction": type_of_transaction,
			"posting_date": parent_doc.posting_date,
			"posting_time": parent_doc.posting_time,
		}
	)

	precision = frappe.get_precision("Stock Entry Detail", "qty")
	if row.serial_nos and row.batches_to_be_consume:
		doc.has_serial_no = 1
		doc.has_batch_no = 1
		batchwise_serial_nos = get_batchwise_serial_nos(child.item_code, row)
		for batch_no, qty in row.batches_to_be_consume.items():
			while flt(qty, precision) > 0:
				qty -= 1
				doc.append(
					"entries",
					{
						"batch_no": batch_no,
						"serial_no": batchwise_serial_nos.get(batch_no).pop(0),
						"warehouse": row.warehouse,
						"qty": -1,
					},
				)

	elif row.serial_nos:
		doc.has_serial_no = 1
		for serial_no in row.serial_nos:
			doc.append("entries", {"serial_no": serial_no, "warehouse": row.warehouse, "qty": -1})

	elif row.batches_to_be_consume:
		precision = frappe.get_precision("Serial and Batch Entry", "qty")
		doc.has_batch_no = 1
		for batch_no, qty in row.batches_to_be_consume.items():
			if flt(qty, precision) > 0:
				qty = flt(qty, precision)
				doc.append("entries", {"batch_no": batch_no, "warehouse": row.warehouse, "qty": qty * -1})

	if not doc.entries:
		return None

	return doc.insert(ignore_permissions=True).name


def get_batchwise_serial_nos(item_code, row):
	batchwise_serial_nos = {}

	for batch_no in row.batches_to_be_consume:
		serial_nos = frappe.get_all(
			"Serial No",
			filters={"item_code": item_code, "batch_no": batch_no, "name": ("in", row.serial_nos)},
		)

		if serial_nos:
			batchwise_serial_nos[batch_no] = sorted([serial_no.name for serial_no in serial_nos])

	return batchwise_serial_nos


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
