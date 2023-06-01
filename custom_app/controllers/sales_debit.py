import frappe
from frappe import _
from frappe.model.meta import get_field_precision
from frappe.utils import flt, format_datetime, get_datetime

import erpnext
from erpnext.stock.utils import get_incoming_rate
from erpnext.controllers.sales_and_purchase_return import get_returned_qty_map_for_row

def make_add_doc(doctype: str, source_name: str, target_doc=None):
	from frappe.model.mapper import get_mapped_doc

	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	company = frappe.db.get_value("Delivery Note", source_name, "company")
	default_warehouse_for_sales_return = frappe.get_cached_value(
		"Company", company, "default_warehouse_for_sales_return"
	)

	def set_missing_values(source, target):
		doc = frappe.get_doc(target)
		doc.debit_note = 1
		doc.debit_against = source.name
		doc.payment_schedule = []
		doc.set_warehouse = ""
		if doctype == "Sales Invoice" or doctype == "POS Invoice":
			doc.is_pos = source.is_pos

			# look for Print Heading "Credit Note"
			if not doc.select_print_heading:
				doc.select_print_heading = frappe.get_cached_value("Print Heading", _("Credit Note"))

		for tax in doc.get("taxes") or []:
			if tax.charge_type == "Actual":
				tax.tax_amount = 1 * tax.tax_amount

		if doc.get("debit_note"):
			if doc.doctype == "Sales Invoice" or doc.doctype == "POS Invoice":
				doc.consolidated_invoice = ""
				doc.set("payments", [])
				for data in source.payments:
					paid_amount = 0.00
					base_paid_amount = 0.00
					data.base_amount = flt(
						data.amount * source.conversion_rate, source.precision("base_paid_amount")
					)
					paid_amount += data.amount
					base_paid_amount += data.base_amount
					doc.append(
						"payments",
						{
							"mode_of_payment": data.mode_of_payment,
							"type": data.type,
							"amount": 1 * paid_amount,
							"base_amount": 1 * base_paid_amount,
							"account": data.account,
							"default": data.default,
						},
					)
				if doc.is_pos:
					doc.paid_amount = 1 * source.paid_amount

		if doc.get("debit_note") and hasattr(doc, "packed_items"):
			for d in doc.get("packed_items"):
				d.qty = d.qty * 1

		if doc.get("discount_amount"):
			doc.discount_amount = 1 * source.discount_amount

		if doctype != "Subcontracting Receipt":
			doc.run_method("calculate_taxes_and_totals")

	def update_item(source_doc, target_doc, source_parent):
		target_doc.qty = 1 * source_doc.qty

		if source_doc.serial_no:
			returned_serial_nos = get_returned_serial_nos(source_doc, source_parent)
			serial_nos = list(set(get_serial_nos(source_doc.serial_no)) - set(returned_serial_nos))
			if serial_nos:
				target_doc.serial_no = "\n".join(serial_nos)

		if doctype in ["Purchase Receipt", "Subcontracting Receipt"]:
			returned_qty_map = get_returned_qty_map_for_row(
				source_parent.name, source_parent.supplier, source_doc.name, doctype
			)

			if doctype == "Subcontracting Receipt":
				target_doc.received_qty = 1 * flt(source_doc.qty)
			else:
				target_doc.received_qty = 1 * flt(
					source_doc.received_qty - (returned_qty_map.get("received_qty") or 0)
				)
				target_doc.rejected_qty = 1 * flt(
					source_doc.rejected_qty - (returned_qty_map.get("rejected_qty") or 0)
				)

			target_doc.qty = 1 * flt(source_doc.qty - (returned_qty_map.get("qty") or 0))

			if hasattr(target_doc, "stock_qty"):
				target_doc.stock_qty = 1 * flt(
					source_doc.stock_qty - (returned_qty_map.get("stock_qty") or 0)
				)
				target_doc.received_stock_qty = 1 * flt(
					source_doc.received_stock_qty - (returned_qty_map.get("received_stock_qty") or 0)
				)

			if doctype == "Subcontracting Receipt":
				target_doc.subcontracting_order = source_doc.subcontracting_order
				target_doc.subcontracting_order_item = source_doc.subcontracting_order_item
				target_doc.rejected_warehouse = source_doc.rejected_warehouse
				target_doc.subcontracting_receipt_item = source_doc.name
			else:
				target_doc.purchase_order = source_doc.purchase_order
				target_doc.purchase_order_item = source_doc.purchase_order_item
				target_doc.rejected_warehouse = source_doc.rejected_warehouse
				target_doc.purchase_receipt_item = source_doc.name

		elif doctype == "Sales Invoice" or doctype == "POS Invoice":
			returned_qty_map = get_returned_qty_map_for_row(
				source_parent.name, source_parent.customer, source_doc.name, doctype
			)
			target_doc.qty = 1 * flt(source_doc.qty - (returned_qty_map.get("qty") or 0))
			target_doc.stock_qty = 1 * flt(source_doc.stock_qty - (returned_qty_map.get("stock_qty") or 0))

			target_doc.sales_order = source_doc.sales_order
			target_doc.delivery_note = source_doc.delivery_note
			target_doc.so_detail = source_doc.so_detail
			target_doc.dn_detail = source_doc.dn_detail
			target_doc.expense_account = source_doc.expense_account

			if doctype == "Sales Invoice":
				target_doc.sales_invoice_item = source_doc.name
			else:
				target_doc.pos_invoice_item = source_doc.name

			if default_warehouse_for_sales_return:
				target_doc.warehouse = default_warehouse_for_sales_return

	def update_terms(source_doc, target_doc, source_parent):
		target_doc.payment_amount = -source_doc.payment_amount

	doclist = get_mapped_doc(
		doctype,
		source_name,
		{
			doctype: {
				"doctype": doctype,
				"validation": {
					"docstatus": ["=", 1],
				},
			},
			doctype
			+ " Item": {
				"doctype": doctype + " Item",
				"field_map": {"serial_no": "serial_no", "batch_no": "batch_no", "bom": "bom"},
				"postprocess": update_item,
			},
			"Payment Schedule": {"doctype": "Payment Schedule", "postprocess": update_terms},
		},
		target_doc,
		set_missing_values,
	)

	doclist.set_onload("ignore_price_list", True)

	return doclist



@frappe.whitelist()
def make_sales_debit(source_name, target_doc=None):

	return make_add_doc("Sales Invoice", source_name, target_doc)
