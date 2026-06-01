// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors // License: GNU General Public License v3. See license.txt

frappe.provide("erpnext.stock");
frappe.provide("erpnext.accounts.dimensions");

erpnext.landed_cost_taxes_and_charges.setup_triggers("Stock Entry");

frappe.ui.form.on("Stock Entry", {
	setup: function (frm) {
		frm.ignore_doctypes_on_cancel_all = ["Serial and Batch Bundle"];

		frm.set_indicator_formatter("item_code", function (doc) {
			if (!doc.s_warehouse) {
				return "blue";
			} else {
				return doc.qty <= doc.actual_qty ? "green" : "orange";
			}
		});

		frm.set_query("outgoing_stock_entry", function () {
			return {
				filters: [
					["Stock Entry", "docstatus", "=", 1],
					["Stock Entry", "per_transferred", "<", "100"],
				],
			};
		});

		frm.set_query("source_warehouse_address", function () {
			return {
				query: "erpnext.controllers.queries.get_warehouse_address",
				filters: {
					warehouse: frm.doc.from_warehouse,
				},
			};
		});

		frm.set_query("target_warehouse_address", function () {
			return {
				query: "erpnext.controllers.queries.get_warehouse_address",
				filters: {
					warehouse: frm.doc.to_warehouse,
				},
			};
		});

		frappe.db.get_value(
			"Stock Settings",
			{ name: "Stock Settings" },
			"sample_retention_warehouse",
			(r) => {
				if (r.sample_retention_warehouse) {
					let filters = [
						["Warehouse", "company", "=", frm.doc.company],
						["Warehouse", "is_group", "=", 0],
						["Warehouse", "name", "!=", r.sample_retention_warehouse],
					];
					frm.set_query("from_warehouse", function () {
						return {
							filters: filters,
						};
					});
					frm.set_query("s_warehouse", "items", function () {
						return {
							filters: filters,
						};
					});
				}
			}
		);

		frm.set_query("batch_no", "items", function (doc, cdt, cdn) {
			let item = locals[cdt][cdn];
			let filters = {};

			if (!item.item_code) {
				frappe.throw(__("Please enter Item Code to get Batch Number"));
			} else {
				filters = {
					item_code: item.item_code,
				};

				// User could want to select a manually created empty batch (no warehouse)
				// or a pre-existing batch
				if (frm.doc.purpose != "Material Receipt") {
					filters["warehouse"] = item.s_warehouse || item.t_warehouse;
				}

				if (!item.s_warehouse && item.t_warehouse) {
					filters["is_inward"] = 1;
				}

				if (["Material Receipt", "Material Transfer", "Material Issue"].includes(doc.purpose)) {
					filters["include_expired_batches"] = 1;
				}

				return {
					query: "erpnext.controllers.queries.get_batch_no",
					filters: filters,
				};
			}
		});

		frm.set_query("serial_and_batch_bundle", "items", (doc, cdt, cdn) => {
			let row = locals[cdt][cdn];
			return {
				filters: {
					item_code: row.item_code,
					voucher_type: doc.doctype,
					voucher_no: ["in", [doc.name, ""]],
					is_cancelled: 0,
				},
			};
		});

		frm.set_query("project", "items", function (doc) {
			return {
				query: "erpnext.controllers.queries.get_project_name",
				filters: {
					company: doc.company,
				},
			};
		});

		frm.set_query("project", function (doc) {
			return {
				query: "erpnext.controllers.queries.get_project_name",
				filters: {
					company: doc.company,
				},
			};
		});

		erpnext.accounts.dimensions.setup_dimension_filters(frm, frm.doctype);

		frappe.db.get_single_value("Stock Settings", "disable_serial_no_and_batch_selector").then((value) => {
			if (value) {
				frappe.flags.hide_serial_batch_dialog = true;
			}
		});
	},

	outgoing_stock_entry: function (frm) {
		frappe.call({
			doc: frm.doc,
			method: "set_items_for_stock_in",
			callback: function () {
				refresh_field("items");
			},
		});
	},

	refresh: function (frm) {
		frm.trigger("get_items_from_transit_entry");

		if (!frm.doc.docstatus) {
			frm.trigger("validate_purpose_consumption");
			frm.add_custom_button(
				__("Material Request"),
				function () {
					frappe.model.with_doctype("Material Request", function () {
						var mr = frappe.model.get_new_doc("Material Request");
						var items = frm.get_field("items").grid.get_selected_children();
						if (!items.length) {
							items = frm.doc.items;
						}			items.forEach(function (item) {

							var mr_item = frappe.model.add_child(mr, "items");
							mr_item.item_code = item.item_code;
							mr_item.item_name = item.item_name;
							mr_item.uom = item.uom;
							mr_item.stock_uom = item.stock_uom;
							mr_item.conversion_factor = item.conversion_factor;
							mr_item.item_group = item.item_group;
							mr_item.description = item.description;
							mr_item.image = item.image;
							mr_item.qty = item.qty;
							mr_item.warehouse = item.s_warehouse;
							mr_item.required_date = frappe.datetime.nowdate();
						});
						frappe.set_route("Form", "Material Request", mr.name);
					});
				},
				__("Create")
			);
		}

		if (frm.doc.items) {
			const has_alternative = frm.doc.items.find((i) => i.allow_alternative_item === 1);

			if (frm.doc.docstatus == 0 && has_alternative) {
				frm.add_custom_button(__("Alternate Item"), () => {
					erpnext.utils.select_alternate_items({
						frm: frm,
						child_docname: "items",
						warehouse_field: "s_warehouse",
						child_doctype: "Stock Entry Detail",
						original_item_field: "original_item",
						condition: (d) => {
							if (d.s_warehouse && d.allow_alternative_item) {
								return true;
							}
						},
					});
				});
			}
		}

		if (frm.doc.docstatus === 1) {
			if (
				frm.doc.add_to_transit &&
				frm.doc.purpose == "Material Transfer" &&
				frm.doc.per_transferred < 100
			) {
				frm.add_custom_button(__("End Transit"), function () {
					frappe.model.open_mapped_doc({
						method: "erpnext.stock.doctype.stock_entry.stock_entry.make_stock_in_entry",
						frm: frm,
					});
				});
			}

			if (frm.doc.per_transferred > 0) {
				frm.add_custom_button(
					__("Received Stock Entries"),
					function () {
						frappe.route_options = {
							outgoing_stock_entry: frm.doc.name,
							docstatus: ["!=", 2],
						};

						frappe.set_route("List", "Stock Entry");
					},
					__("View")
				);
			}

	
		}

		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(
				__("Purchase Invoice"),
				function () {
					erpnext.utils.map_current_doc({
						method: "erpnext.accounts.doctype.purchase_invoice.purchase_invoice.make_stock_entry",
						source_doctype: "Purchase Invoice",
						target: frm,
						date_field: "posting_date",
						setters: {
							supplier: frm.doc.supplier || undefined,
						},
						get_query_filters: {
							docstatus: 1,
						},
					});
				},
				__("Get Items From")
			);

			frm.add_custom_button(
				__("Material Request"),
				function () {
					const allowed_request_types = [
						"Material Transfer",
						"Material Issue",
						"Customer Provided",
					];
					const depends_on_condition = "eval:doc.material_request_type==='Customer Provided'";
					const d = erpnext.utils.map_current_doc({
						method: "erpnext.stock.doctype.material_request.material_request.make_stock_entry",
						source_doctype: "Material Request",
						target: frm,
						date_field: "schedule_date",
						setters: [
							{
								fieldtype: "Select",
								label: __("Purpose"),
								options: allowed_request_types.join("\n"),
								fieldname: "material_request_type",
								default: "Material Transfer",
								mandatory: 1,
								change() {
									if (this.value === "Customer Provided") {
										d.dialog.get_field("customer").set_focus();
									}
								},
							},
							{
								fieldtype: "Link",
								label: __("Customer"),
								options: "Customer",
								fieldname: "customer",
								depends_on: depends_on_condition,
								mandatory_depends_on: depends_on_condition,
							},
						],
						get_query_filters: {
							docstatus: 1,
							material_request_type: ["in", allowed_request_types],
							status: ["not in", ["Transferred", "Issued", "Cancelled", "Stopped"]],
						},
					});
				},
				__("Get Items From")
			);
		}

		if (frm.doc.docstatus === 0 && frm.doc.purpose == "Material Issue") {
			frm.add_custom_button(
				__("Expired Batches"),
				function () {
					frappe.call({
						method: "erpnext.stock.doctype.stock_entry.stock_entry.get_expired_batch_items",
						freeze: true,
						callback: function (r) {
							if (!r.exc && r.message) {
								frm.set_value("items", []);
								r.message.forEach(function (element) {
									let d = frm.add_child("items");
									d.item_code = element.item;
									d.s_warehouse = element.warehouse;
									d.qty = element.qty;
									d.uom = element.stock_uom;
									d.conversion_factor = 1;
									d.batch_no = element.batch_no;
									d.transfer_qty = element.qty;
									frm.refresh_fields();
								});
							}
						},
					});
				},
				__("Get Items From")
			);
		}

		if (frm.doc.company) {
			frm.trigger("toggle_display_account_head");
		}

		if (
			frm.doc.docstatus == 1 &&
			frm.doc.purpose == "Material Receipt" &&
			frm.get_sum("items", "sample_quantity")
		) {
			frm.add_custom_button(__("Create Sample Retention Stock Entry"), function () {
				frm.trigger("make_retention_stock_entry");
			});
		}

		frm.events.set_route_options_for_new_doc(frm);
	},

	set_route_options_for_new_doc(frm) {
		let batch_no_field = frm.get_docfield("items", "batch_no");
		if (batch_no_field) {
			batch_no_field.get_route_options_for_new_doc = function (row) {
				return {
					item: row.doc.item_code,
				};
			};
		}

		let sbb_field = frm.get_docfield("items", "serial_and_batch_bundle");
		if (sbb_field) {
			sbb_field.get_route_options_for_new_doc = (row) => {
				return {
					item_code: row.doc.item_code,
					voucher_type: frm.doc.doctype,
					warehouse: row.doc.s_warehouse || row.doc.t_warehouse,
				};
			};
		}
	},

	get_items_from_transit_entry: function (frm) {
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(
				__("Transit Entry"),
				function () {
					erpnext.utils.map_current_doc({
						method: "erpnext.stock.doctype.stock_entry.stock_entry.make_stock_in_entry",
						source_doctype: "Stock Entry",
						target: frm,
						date_field: "posting_date",
						read_only_setters: ["stock_entry_type", "purpose", "add_to_transit"],
						setters: {
							stock_entry_type: "Material Transfer",
							purpose: "Material Transfer",
							add_to_transit: 1,
						},
						get_query_filters: {
							docstatus: 1,
							purpose: "Material Transfer",
							add_to_transit: 1,
							per_transferred: ["<", 100],
						},
					});
				},
				__("Get Items From")
			);
		}
	},

	before_save: function (frm) {
		frm.doc.items.forEach((item) => {
			item.uom = item.uom || item.stock_uom;
		});
	},

	stock_entry_type: function (frm) {
		frm.trigger("add_to_transit");
	},

	purpose: function (frm) {
		frm.fields_dict.items.grid.refresh();
		frm.cscript.toggle_related_fields(frm.doc);
	},
	cost_center(frm, cdt, cdn) {
		erpnext.utils.copy_value_in_all_rows(frm.doc, cdt, cdn, "items", "cost_center");
	},
	make_retention_stock_entry: function (frm) {
		frappe.call({
			method: "erpnext.stock.doctype.stock_entry.stock_entry.move_sample_to_retention_warehouse",
			args: {
				company: frm.doc.company,
				items: frm.doc.items,
			},
			callback: function (r) {
				if (r.message) {
					var doc = frappe.model.sync(r.message)[0];
					frappe.set_route("Form", doc.doctype, doc.name);
				} else {
					frappe.msgprint(
						__("Retention Stock Entry already created or Sample Quantity not provided")
					);
				}
			},
		});
	},

	toggle_display_account_head: function (frm) {
		var enabled = erpnext.is_perpetual_inventory_enabled(frm.doc.company);
		frm.fields_dict["items"].grid.set_column_disp(["cost_center", "expense_account"], enabled);
	},

	set_basic_rate: function (frm, cdt, cdn) {
		const item = locals[cdt][cdn];
		item.transfer_qty = flt(item.qty) * flt(item.conversion_factor);

		let args = {
			item_code: item.item_code,
			posting_date: frm.doc.posting_date,
			posting_time: frm.doc.posting_time,
			warehouse: cstr(item.s_warehouse) || cstr(item.t_warehouse),
			serial_no: item.serial_no,
			batch_no: item.batch_no,
			company: frm.doc.company,
			qty: item.s_warehouse ? -1 * flt(item.transfer_qty) : flt(item.transfer_qty),
			voucher_type: frm.doc.doctype,
			voucher_no: item.name,
			allow_zero_valuation: 1,
		};

		if (item.batch_no && frm.doc.purpose == "Material Receipt") {
			args.qty = Math.abs(args.qty) * -1;
		}

		if (item.item_code || item.serial_no) {
			frappe.call({
				method: "erpnext.stock.utils.get_incoming_rate",
				args: {
					args: args,
				},
				callback: function (r) {
					frappe.model.set_value(cdt, cdn, "basic_rate", r.message || 0.0);
					frm.events.calculate_basic_amount(frm, item);
				},
			});
		}
	},

	set_rate_and_fg_qty: function (frm, cdt, cdn) {
		frm.events.set_basic_rate(frm, cdt, cdn);
	},

	get_warehouse_details: function (frm, cdt, cdn) {
		var child = locals[cdt][cdn];
		frappe.call({
				method: "erpnext.stock.doctype.stock_entry.stock_entry.get_warehouse_details",
				args: {
					args: {
						item_code: child.item_code,
						warehouse: cstr(child.s_warehouse) || cstr(child.t_warehouse),
						transfer_qty: child.transfer_qty,
						serial_and_batch_bundle: child.serial_and_batch_bundle,
						qty: child.s_warehouse ? -1 * child.transfer_qty : child.transfer_qty,
						posting_date: frm.doc.posting_date,
						posting_time: frm.doc.posting_time,
						company: frm.doc.company,
						voucher_type: frm.doc.doctype,
						voucher_no: child.name,
						allow_zero_valuation: 1,
					},
				},
				callback: function (r) {
					if (!r.exc) {
						let fields = ["actual_qty", "basic_rate"];
						if (frm.doc.purpose == "Material Receipt") {
							fields = ["actual_qty"];
						}

						fields.forEach((field) => {
							frappe.model.set_value(cdt, cdn, field, r.message[field] || 0.0);
						});
						frm.events.calculate_basic_amount(frm, child);
					}
				},
			});
	},	

	calculate_basic_amount: function (frm, item) {
		item.basic_amount = flt(
			flt(item.transfer_qty) * flt(item.basic_rate),
			precision("basic_amount", item)
		);
		frm.events.calculate_total_additional_costs(frm);
	},

	calculate_total_additional_costs: function (frm) {
		const total_additional_costs = frappe.utils.sum(
			(frm.doc.additional_costs || []).map(function (c) {
				return flt(c.base_amount);
			})
		);

		frm.set_value(
			"total_additional_costs",
			flt(total_additional_costs, precision("total_additional_costs"))
		);
	},

	source_warehouse_address: function (frm) {
		erpnext.utils.get_address_display(frm, "source_warehouse_address", "source_address_display", false);
	},

	target_warehouse_address: function (frm) {
		erpnext.utils.get_address_display(frm, "target_warehouse_address", "target_address_display", false);
	},

	add_to_transit: function (frm) {
		if (frm.doc.purpose == "Material Transfer") {
			var filters = {
				is_group: 0,
				company: frm.doc.company,
			};

			if (frm.doc.add_to_transit) {
				filters["warehouse_type"] = "Transit";
				frm.set_value("to_warehouse", "");
				frm.trigger("set_transit_warehouse");
			}

			frm.fields_dict.to_warehouse.get_query = function () {
				return {
					filters: filters,
				};
			};
		}
	},

	set_transit_warehouse: function (frm) {
		if (
			frm.doc.add_to_transit &&
			frm.doc.purpose == "Material Transfer" &&
			!frm.doc.to_warehouse &&
			frm.doc.from_warehouse
		) {
			let dt = frm.doc.from_warehouse ? "Warehouse" : "Company";
			let dn = frm.doc.from_warehouse ? frm.doc.from_warehouse : frm.doc.company;
			frappe.db.get_value(dt, dn, "default_in_transit_warehouse", (r) => {
				if (r.default_in_transit_warehouse) {
					frm.set_value("to_warehouse", r.default_in_transit_warehouse);
				}
			});
		}
	},

	apply_putaway_rule: function (frm) {
		if (frm.doc.apply_putaway_rule) erpnext.apply_putaway_rule(frm, frm.doc.purpose);
	},

	});

frappe.ui.form.on("Stock Entry Detail", {
	set_basic_rate_manually(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		frm.fields_dict.items.grid.update_docfield_property(
			"basic_rate",
			"read_only",
			row?.set_basic_rate_manually ? 0 : 1
		);
	},

	qty(frm, cdt, cdn) {
		frm.events.set_rate_and_fg_qty(frm, cdt, cdn);
	},

	conversion_factor(frm, cdt, cdn) {
		frm.events.set_rate_and_fg_qty(frm, cdt, cdn);
	},

	s_warehouse(frm, cdt, cdn) {
		frm.events.get_warehouse_details(frm, cdt, cdn);

		// set allow_zero_valuation_rate to 0 if s_warehouse is selected.
		let item = frappe.get_doc(cdt, cdn);
		if (item.s_warehouse) {
			frappe.model.set_value(cdt, cdn, "allow_zero_valuation_rate", 0);
		}
	},

	t_warehouse(frm, cdt, cdn) {
		frm.events.get_warehouse_details(frm, cdt, cdn);
	},

	basic_rate(frm, cdt, cdn) {
		var item = locals[cdt][cdn];
		frm.events.calculate_basic_amount(frm, item);
	},

	uom(doc, cdt, cdn) {
		var d = locals[cdt][cdn];
		if (d.uom && d.item_code) {
			return frappe.call({
				method: "erpnext.stock.doctype.stock_entry.stock_entry.get_uom_details",
				args: {
					item_code: d.item_code,
					uom: d.uom,
					qty: d.qty,
				},
				callback: function (r) {
					if (r.message) {
						frappe.model.set_value(cdt, cdn, r.message);
					}
				},
			});
		}
	},

	item_code(frm, cdt, cdn) {
		var d = locals[cdt][cdn];
		// since some items may not have image, so empty the image field to avoid setting the image of previous item
		d.image = "";

		if (d.item_code) {
			var args = {
				item_code: d.item_code,
				warehouse: cstr(d.s_warehouse) || cstr(d.t_warehouse),
				transfer_qty: d.transfer_qty,
				serial_no: d.serial_no,
				batch_no: d.batch_no,
				expense_account: d.expense_account,
				cost_center: d.cost_center,
				company: frm.doc.company,
				qty: d.qty,
				voucher_type: frm.doc.doctype,
				voucher_no: d.name,
				allow_zero_valuation: 1,
			};

			return frappe.call({
				doc: frm.doc,
				method: "get_item_details",
				args: args,
				callback: function (r) {
					if (r.message) {
						var d = locals[cdt][cdn];
						$.each(r.message, function (k, v) {
							if (v) {
								// set_value trigger barcode function and barcode set qty to 1 in stock_controller.js, to avoid this set value manually instead of set value.
								if (k != "barcode") {
									frappe.model.set_value(cdt, cdn, k, v); // qty and it's subsequent fields weren't triggered
								} else {
									d.barcode = v;
								}
							}
						});
						refresh_field("items");

						let no_batch_serial_number_value = false;
						if (d.has_serial_no || d.has_batch_no) {
							no_batch_serial_number_value = true;
						}

						if (no_batch_serial_number_value && !frappe.flags.hide_serial_batch_dialog) {
							if (!frappe.flags.dialog_set) {
								frappe.flags.dialog_set = true;
							}
							erpnext.stock.select_batch_and_serial_no(frm, d);
						} else {
							frappe.flags.dialog_set = false;
						}
					}
				},
			});
		}
	},

	expense_account(frm, cdt, cdn) {
		erpnext.utils.copy_value_in_all_rows(frm.doc, cdt, cdn, "items", "expense_account");
	},

	cost_center(frm, cdt, cdn) {
		erpnext.utils.copy_value_in_all_rows(frm.doc, cdt, cdn, "items", "cost_center");
	},

	sample_quantity(frm, cdt, cdn) {
		validate_sample_quantity(frm, cdt, cdn);
	},

	batch_no(frm, cdt, cdn) {
		let row = locals[cdt][cdn];

		if (row.batch_no) {
			frappe.model.set_value(cdt, cdn, {
				use_serial_batch_fields: 1,
				serial_and_batch_bundle: "",
			});
		}

		frm.events.set_basic_rate(frm, cdt, cdn);
		validate_sample_quantity(frm, cdt, cdn);
	},

	add_serial_batch_bundle(frm, cdt, cdn) {
		var child = locals[cdt][cdn];
		erpnext.stock.select_batch_and_serial_no(frm, child);
	},
});

var validate_sample_quantity = function (frm, cdt, cdn) {
	var d = locals[cdt][cdn];
	if (d.sample_quantity && frm.doc.purpose == "Material Receipt") {
		frappe.call({
			method: "erpnext.stock.doctype.stock_entry.stock_entry.validate_sample_quantity",
			args: {
				batch_no: d.batch_no,
				item_code: d.item_code,
				sample_quantity: d.sample_quantity,
				qty: d.transfer_qty,
			},
			callback: (r) => {
				frappe.model.set_value(cdt, cdn, "sample_quantity", r.message);
			},
		});
	}
};

frappe.ui.form.on("Landed Cost Taxes and Charges", {
	amount: function (frm, cdt, cdn) {
		frm.events.set_base_amount(frm, cdt, cdn);
	},

	expense_account: function (frm, cdt, cdn) {
		frm.events.set_account_currency(frm, cdt, cdn);
	},
});

erpnext.stock.StockEntry = class StockEntry extends erpnext.stock.StockController {
	setup() {
		var me = this;

		this.barcode_scanner = new erpnext.utils.BarcodeScanner({
			frm: this.frm,
			warehouse_field: (doc) => {
				return doc.purpose === "Material Receipt" ? "t_warehouse" : "s_warehouse";
			},
		});

		this.setup_posting_date_time_check();

		this.frm.fields_dict.items.grid.get_field("item_code").get_query = function () {
			return erpnext.queries.item({ is_stock_item: 1 });
		};

		this.frm.set_query("uom", "items", function (doc, cdt, cdn) {
			let row = locals[cdt][cdn];

			if (!row.item_code) {
				return;
			}

			return {
				query: "erpnext.controllers.queries.get_item_uom_query",
				filters: {
					item_code: row.item_code,
				},
			};
		});

		this.frm.fields_dict.items.grid.get_field("expense_account").get_query = function () {
			if (erpnext.is_perpetual_inventory_enabled(me.frm.doc.company)) {
				return {
					filters: {
						company: me.frm.doc.company,
						is_group: 0,
					},
				};
			}
		};

		frappe.dynamic_link = { doc: this.frm.doc, fieldname: "supplier", doctype: "Supplier" };
		this.frm.set_query("supplier_address", erpnext.queries.address_query);
	}

	onload_post_render() {
		var me = this;
		if (me.frm.doc.__islocal && me.frm.doc.company && !me.frm.doc.amended_from) {
			me.company();
		}

		this.frm.get_field("items").grid.set_multiple_add("item_code", "qty");
	}

	refresh() {
		var me = this;
		erpnext.toggle_naming_series();
		this.toggle_related_fields(this.frm.doc);
		this.show_stock_ledger();
		this.set_fields_onload_for_line_item();
		erpnext.utils.view_serial_batch_nos(this.frm);
		if (this.frm.doc.docstatus === 1 && erpnext.is_perpetual_inventory_enabled(this.frm.doc.company)) {
			this.show_general_ledger();
		}
		erpnext.hide_company(this.frm);
		erpnext.utils.add_item(this.frm);
	}

	serial_no(doc, cdt, cdn) {
		var item = frappe.get_doc(cdt, cdn);

		if (item.serial_no) {
			frappe.model.set_value(cdt, cdn, {
				use_serial_batch_fields: 1,
				serial_and_batch_bundle: "",
			});
		}

		if (item?.serial_no) {
			// Replace all occurences of comma with line feed
			item.serial_no = item.serial_no.replace(/,/g, "\n");
			item.conversion_factor = item.conversion_factor || 1;

			let valid_serial_nos = [];
			let serialnos = item.serial_no.split("\n");
			for (var i = 0; i < serialnos.length; i++) {
				if (serialnos[i] != "") {
					valid_serial_nos.push(serialnos[i]);
				}
			}
			frappe.model.set_value(
				item.doctype,
				item.name,
				"qty",
				valid_serial_nos.length / item.conversion_factor
			);
		}
	}

	set_fields_onload_for_line_item() {
		if (
			this.frm.is_new() &&
			this.frm.doc?.items &&
			cint(frappe.user_defaults?.use_serial_batch_fields) === 1
		) {
			this.frm.doc.items.forEach((item) => {
				if (!item.serial_and_batch_bundle) {
					frappe.model.set_value(item.doctype, item.name, "use_serial_batch_fields", 1);
				}
			});
		}
	}

	scan_barcode() {
		frappe.flags.dialog_set = false;
		this.barcode_scanner.process_scan();
	}

	on_submit() {
		this.refresh_serial_batch_bundle_field();
	}

	refresh_serial_batch_bundle_field() {
		frappe.route_hooks.after_submit = (frm_obj) => {
			frm_obj.reload_doc();
		};
	}

	company() {
		if (this.frm.doc.company) {
			var company_doc = frappe.get_doc(":Company", this.frm.doc.company);
			if (company_doc.default_letter_head) {
				this.frm.set_value("letter_head", company_doc.default_letter_head);
			}
			this.frm.trigger("toggle_display_account_head");

			erpnext.accounts.dimensions.update_dimension(this.frm, this.frm.doctype);
			this.set_default_account("cost_center", "cost_center");

			this.frm.refresh_fields("items");
		}
	}

	set_default_account(company_fieldname, fieldname) {
		var me = this;

		return this.frm.call({
			method: "erpnext.accounts.utils.get_company_default",
			args: {
				fieldname: company_fieldname,
				company: this.frm.doc.company,
			},
			callback: function (r) {
				if (!r.exc) {
					$.each(me.frm.doc.items || [], function (i, d) {
						d[fieldname] = r.message;
					});
				}
			},
		});
	}

	items_add(doc, cdt, cdn) {
		var row = frappe.get_doc(cdt, cdn);

		if (!(row.expense_account && row.cost_center)) {
			this.frm.script_manager.copy_from_first_row("items", row, ["expense_account", "cost_center"]);
		}

		if (this.frm.doc.from_warehouse) row.s_warehouse = this.frm.doc.from_warehouse;
		if (this.frm.doc.to_warehouse) row.t_warehouse = this.frm.doc.to_warehouse;

		if (cint(frappe.user_defaults?.use_serial_batch_fields)) {
			frappe.model.set_value(row.doctype, row.name, "use_serial_batch_fields", 1);
		}
	}

	from_warehouse(doc) {
		this.frm.trigger("set_transit_warehouse");
		this.set_warehouse_in_children(doc.items, "s_warehouse", doc.from_warehouse);
	}

	to_warehouse(doc) {
		this.set_warehouse_in_children(doc.items, "t_warehouse", doc.to_warehouse);
	}

	set_warehouse_in_children(child_table, warehouse_field, warehouse) {
		let transaction_controller = new erpnext.TransactionController();
		transaction_controller.autofill_warehouse(child_table, warehouse_field, warehouse);
	}

	items_on_form_rendered(doc, grid_row) {
		erpnext.setup_serial_or_batch_no();
	}

	toggle_related_fields(doc) {
		this.frm.toggle_enable("from_warehouse", doc.purpose != "Material Receipt");
		this.frm.toggle_enable("to_warehouse", doc.purpose != "Material Issue");

		this.frm.fields_dict["items"].grid.set_column_disp(
			"retain_sample",
			doc.purpose == "Material Receipt"
		);
		this.frm.fields_dict["items"].grid.set_column_disp(
			"sample_quantity",
			doc.purpose == "Material Receipt"
		);

		doc.customer =
			doc.customer_name =
			doc.customer_address =
			doc.delivery_note_no =
			doc.sales_invoice_no =
			doc.supplier =
			doc.supplier_name =
			doc.supplier_address =
			doc.purchase_receipt_no =
			doc.address_display =
				null;

		// Addition costs based on purpose
		this.frm.toggle_display(
			["additional_costs", "total_additional_costs", "additional_costs_section"],
			doc.purpose != "Material Issue"
		);

		this.frm.fields_dict["items"].grid.set_column_disp(
			"additional_cost",
			doc.purpose != "Material Issue"
		);
	}

	supplier(doc) {
		erpnext.utils.get_party_details(this.frm, null, null, null);
	}
};

erpnext.stock.select_batch_and_serial_no = (frm, item) => {
	let path = "assets/erpnext/js/utils/serial_no_batch_selector.js";

	frappe.db.get_value("Item", item.item_code, ["has_batch_no", "has_serial_no"]).then((r) => {
		if (r.message && (r.message.has_batch_no || r.message.has_serial_no)) {
			item.has_serial_no = r.message.has_serial_no;
			item.has_batch_no = r.message.has_batch_no;
			item.type_of_transaction = item.s_warehouse ? "Outward" : "Inward";

			new erpnext.SerialBatchPackageSelector(frm, item, (r) => {
				if (r) {
					frappe.model.set_value(item.doctype, item.name, {
						serial_and_batch_bundle: r.name,
						use_serial_batch_fields: 0,
						basic_rate: r.avg_rate,
						qty:
							Math.abs(r.total_qty) /
							flt(item.conversion_factor || 1, precision("conversion_factor", item)),
					});
				}
			});
		}
	});
};

extend_cscript(cur_frm.cscript, new erpnext.stock.StockEntry({ frm: cur_frm }));
