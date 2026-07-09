import frappe
from frappe.utils import today , flt
from collections import defaultdict
from frappe.query_builder.functions import IfNull
from mawared_pricing_rule.event.erp_utils import get_pricing_rules
from mawared_pricing_rule.event.utils import build_json_args
from erpnext.stock.doctype.batch.batch import get_batch_no


def before_insert(doc, method=None):
    doc.ignore_pricing_rule = 1

def before_validate(doc, method=None):
    # Seed custom_rate on both create and update. before_insert only fires on
    # create, so without this the update path enters validate with an empty
    # custom_rate and the pricing logic falls back to price_list[0] (IndexError).
    for item in doc.items:
        item.custom_rate = item.rate
def validate(doc , method = None):
    if not doc.custom_ignore_pricing_rule:
        # Skip if any item is linked to a Sales Order
        if not any(getattr(item, "sales_order", None) for item in doc.items) and not getattr(doc, "custom_sales_order", None):
            apply_free_item_based_on_same_item_qty(doc)
            calculate_total(doc)
        

def calculate_total(doc):
    total_qty = 0
    total_amount = 0
    for item in doc.items:
        total_qty += item.qty
        total_amount += item.amount
    doc.total_qty = total_qty
    doc.total = total_amount

def apply_free_item_based_on_same_item_qty(doc):
    """
    Apply free items based on the same item quantity in the sales invoice.
    """
    # doc.pricing_rules = []
    item_qty_map = defaultdict(float)

    # Step 1: Count non-free item quantities
    for item in doc.items:
        if item.is_free_item == 1:
            # Skip free items
            continue
        if item.item_code in item_qty_map:
            item_qty_map[item.item_code]["qty"] += item.qty
        else:
            item_qty_map[item.item_code] = {
                "item_code": item.item_code,
                "qty": item.qty,
                "conversion_factor": item.conversion_factor,
                "uom": item.uom,
                "income_account": item.income_account,
                "cost_center": item.cost_center,
                "expense_account": item.expense_account,
                "item_name": item.item_name,
                "is_free_item": 0,  # Set to 0 for non-free items
                "description": item.description,
                "custom_applied_pricing_rule": item.custom_applied_pricing_rule,
                "custom_vat_rate": item.custom_vat_rate,
                "custom_tax_category_code": item.custom_tax_category_code,
                "custom_support_ksa_e_invoice_integration_phase_ii": item.custom_support_ksa_e_invoice_integration_phase_ii,
            }
    non_free_items = [item for item in doc.items if not item.is_free_item]
    doc.items = []
    for item in non_free_items:
        new_item = doc.append("items", {})
        # Copy all fields from item to the new_item
        for fieldname, value in item.as_dict().items():
            setattr(new_item, fieldname, value)

        # Force set known values (if needed)
        new_item.is_free_item = 0
        new_item.pricing_rules = ""
        new_item.applied_pricing_rule = ""
        new_item.discount_percentage = 0
        new_item.discount_amount = 0
    # Step 2: Add free items where needed
    for key , value in item_qty_map.items():
        dect = frappe._dict(
            price_list=doc.selling_price_list,
            uom=value["uom"],
            transaction_date=doc.posting_date,
            customer=doc.customer,
        )
        price_list = get_item_price(dect, value["item_code"], ignore_party=True)
        price_rule = get_pricing_rules(build_json_args(doc, value),doc)
        if price_rule:
            price_rule_doc = frappe.get_doc("Pricing Rule", price_rule[0].name)
            if price_rule_doc.price_or_product_discount == "Product":  # Change threshold as needed
                free_item_code = price_rule_doc.free_item if not price_rule_doc.same_item else value["item_code"]
                batch_no = get_batch_no(item_code = free_item_code, qty = price_rule_doc.free_qty, warehouse = doc.set_warehouse)
                free_item = {
                    "item_code": free_item_code, 
                    "qty": price_rule_doc.free_qty,
                    "rate": 0,
                    "is_free_item": 1,
                    "price_list_rate":0,
                    "item_group": frappe.get_value("Item", free_item_code, "item_group"),
                    "brand": frappe.get_value("Item", free_item_code, "brand"),
                    "image": frappe.get_value("Item", free_item_code, "image"),
                    "stock_uom": frappe.get_value("Item", free_item_code, "stock_uom"),
                    "stock_qty":price_rule_doc.free_qty,
                    "amount": 0,
                    "discount_amount": 0,
                    "discount_percentage":100,
                    "description": "Free item applied due to bulk purchase",
                    "income_account": value["income_account"],
                    "cost_center": value["cost_center"],
                    "expense_account": value["expense_account"],
                    "conversion_factor": value["conversion_factor"],
                    "uom": value["uom"],
                    "item_name": value["item_name"],
                    "base_price_list_rate": 0,
                    "base_rate": 0,
                    "base_amount": 0,
                    "batch_no": batch_no,
                    "warehouse": doc.set_warehouse,
                    "custom_is_prepayment":0,
                    "custom_is_down_payment":0,
                    "custom_vat_rate": value["custom_vat_rate"],
                    "custom_tax_category_code": value["custom_tax_category_code"],
                    "custom_support_ksa_e_invoice_integration_phase_ii": value["custom_support_ksa_e_invoice_integration_phase_ii"],
                }
                
                doc.append("items", free_item)
                # update the price in the bass item
                for item in doc.items:
                    if item.item_code == value["item_code"]:
                        item.custom_applied_pricing_rule = price_rule_doc.name
                        item.custom_rate = price_list[0][1] if price_list else 0
                        item.rate = price_list[0][1] if price_list else 0
                        item.price_list_rate = price_list[0][1] if price_list else 0
                        item.base_price_list_rate = price_list[0][1] if price_list else 0
                        item.base_rate = price_list[0][1] if price_list else 0
                        item.base_amount = item.qty * item.rate
                        item.amount = item.qty * item.rate 
            elif price_rule_doc.price_or_product_discount == "Price" and price_rule_doc.rate_or_discount == "Rate":
                
                rate = price_rule_doc.rate

                for item in doc.items:
                    if item.item_code == value["item_code"]:
                        item.rate = rate
                        item.price_list_rate = rate
                        item.base_price_list_rate = rate
                        item.base_rate = rate
                        item.base_amount = item.qty * rate
                        item.amount = item.qty * rate
                        item.discount_amount = 0
                        item.discount_percentage = 0
                        item.custom_applied_pricing_rule = price_rule_doc.name
            elif price_rule_doc.price_or_product_discount == "Price" and price_rule_doc.rate_or_discount == "Discount Percentage":
                discount_percentage = price_rule_doc.discount_percentage
                
                for item in doc.items:
                    if item.item_code == value["item_code"]:
                        item.margin_type = "Percentage" 
                        item.price_list_rate = price_list[0][1] 
                        item.base_price_list_rate = price_list[0][1] 
                        item.rate = item.base_price_list_rate * (1 - (discount_percentage / 100)) 
                        item.amount = item.qty * item.rate
                        item.discount_percentage = discount_percentage
                        item.discount_amount = (item.qty * item.rate) * (discount_percentage / 100)
                        item.base_rate = item.rate
                        item.base_amount = item.qty * item.rate
                        item.custom_applied_pricing_rule = price_rule_doc.name
            elif price_rule_doc.price_or_product_discount == "Price" and price_rule_doc.rate_or_discount == "Discount Amount":
                discount_amount = price_rule_doc.discount_amount
                for item in doc.items:
                    if item.item_code == value["item_code"]:
                        item.margin_type = "Amount"
                        item.price_list_rate = price_list[0][1] 
                        item.base_price_list_rate = price_list[0][1] 
                        item.rate = item.base_price_list_rate - discount_amount 
                        item.amount = item.qty * item.rate
                        item.discount_percentage = 0
                        item.discount_amount = discount_amount
                        item.base_rate = item.rate
                        item.base_amount = item.qty * item.rate
                        item.custom_applied_pricing_rule = price_rule_doc.name
        else:
            # If the item is not free, set the rate and amount
            for item in doc.items:
                if item.is_free_item == 0 and item.item_code == value["item_code"]:
                    item.custom_rate =item.custom_rate if item.custom_rate else price_list[0][1]  
                    item.rate = item.custom_rate
                    item.amount = flt(item.qty) * flt(item.rate)
                    item.discount_amount = 0
                    item.discount_percentage = 0
                    item.base_rate = item.rate
                    item.base_amount = item.qty * item.rate
                    item.custom_man_change = 0

    # append pricing_rules table
    for item in doc.items:
        if item.custom_applied_pricing_rule and item.is_free_item == 0:
            doc.append("pricing_rules", {
                "pricing_rule": item.custom_applied_pricing_rule,
                "item_code": item.item_code,
                "margen_type": item.margin_type,
                "rate_or_discount": item.discount_amount,
                "child_docname": item.name,
                "rule_applied": 1,
                "parent": doc.name,
                "parentfield": "pricing_rules",
                "parenttype": doc.doctype,
            })
    # Step 3: Recalculate totals
    doc.calculate_taxes_and_totals()
    # Step 4: Save the document


def get_item_price(args, item_code, ignore_party=False):
    """
    Get name, price_list_rate from Item Price based on conditions
            Check if the desired qty is within the increment of the packing list.
    :param args: dict (or frappe._dict) with mandatory fields price_list, uom
            optional fields transaction_date, customer, supplier
    :param item_code: str, Item Doctype field item_code
    """
    ip = frappe.qb.DocType("Item Price")
    query = (
        frappe.qb.from_(ip)
        .select(ip.name, ip.price_list_rate, ip.uom)
        .where(
            (ip.item_code == item_code)
            & (ip.price_list == args.get("price_list"))
            & (IfNull(ip.uom, "").isin(["", args.get("uom")]))
            & (IfNull(ip.batch_no, "").isin(["", args.get("batch_no")]))
        )
        .orderby(ip.valid_from, order=frappe.qb.desc)
        .orderby(IfNull(ip.batch_no, ""), order=frappe.qb.desc)
        .orderby(ip.uom, order=frappe.qb.desc)
    )

    if not ignore_party:
        if args.get("customer"):
            query = query.where(ip.customer == args.get("customer"))
        elif args.get("supplier"):
            query = query.where(ip.supplier == args.get("supplier"))
        else:
            query = query.where((IfNull(ip.customer, "") == "") & (IfNull(ip.supplier, "") == ""))

    if args.get("transaction_date"):
        query = query.where(
            (IfNull(ip.valid_from, "2000-01-01") <= args["transaction_date"])
            & (IfNull(ip.valid_upto, "2500-12-31") >= args["transaction_date"])
        )

    return query.run()