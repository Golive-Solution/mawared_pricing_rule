import frappe
from frappe.utils import flt, getdate, get_datetime
from erpnext.stock.get_item_details import get_item_tax_template, get_item_tax_map

def build_json_args(doc, item=None):
    """
    Build JSON-serializable arguments from the Sales Invoice/Sales Order document and item
    Args:
        doc: Sales Invoice or Sales Order document
        item: Sales Invoice Item/Sales Order Item or dictionary (optional)
    Returns:
        dict: JSON-serializable arguments dictionary for pricing rule calculation
    """
    doctype = doc.get("parenttype") or doc.doctype
    
    # Determine the appropriate date field based on document type
    if doctype == "Sales Order":
        date_field = "transaction_date"
        item_doctype = "Sales Order Item"
        price_list_field = "selling_price_list"
    else:  # Sales Invoice
        date_field = (
            "transaction_date" if frappe.get_meta(doctype).has_field("transaction_date") else "posting_date"
        )
        item_doctype = "Sales Invoice Item"
        price_list_field = "selling_price_list"
    
    # Get the transaction date based on document type and available fields
    if doctype == "Sales Order":
        transaction_date = doc.transaction_date
    else:
        transaction_date = doc.posting_date if date_field == "posting_date" else doc.transaction_date
    
    args = {
        'customer': str(doc.customer or ''),
        'customer_group': str(doc.customer_group or ''),
        'territory': str(doc.territory or ''),
        'currency': str(doc.currency or ''),
        'conversion_rate': float(doc.conversion_rate or 1.0),
        'price_list': str(getattr(doc, price_list_field, '') or ''),
        'price_list_currency': str(doc.currency or ''),
        'plc_conversion_rate': float(doc.plc_conversion_rate or 1.0),
        'company': str(doc.company or ''),
        'transaction_date': str(transaction_date or ''),
        'ignore_pricing_rule': 0,
        'doctype': item_doctype,
        'name': str(doc.name or ''),
        'transaction_type': 'selling',
        'exchange_rate': 'for_selling'
    }
    
    # Add document-specific fields
    if doctype == "Sales Invoice":
        args.update({
            'is_return': int(doc.is_return or 0),
            'update_stock': int(doc.update_stock or 0),
            'is_internal_customer': int(doc.is_internal_customer or 0),
        })
    elif doctype == "Sales Order":
        args.update({
            'is_return': 0,  # Sales Orders don't have returns
            'update_stock': 0,  # Sales Orders don't update stock directly
            'is_internal_customer': int(getattr(doc, 'is_internal_customer', 0) or 0),
        })

    if item:
        item_dict = item if isinstance(item, dict) else {
            'item_code': str(getattr(item, 'item_code', '') or ''),
            'item_group': str(getattr(item, 'item_group', '') or ''),
            'brand': str(getattr(item, 'brand', '') or ''),
            'qty': float(getattr(item, 'qty', 0) or 0),
            'stock_qty': float(getattr(item, 'qty', 0) or 0),
            'uom': str(getattr(item, 'uom', '') or ''),
            'stock_uom': str(getattr(item, 'stock_uom', '') or ''),
            'pricing_rules': str(getattr(item, 'pricing_rules','') or ''),
            'warehouse': str(getattr(item, 'warehouse', '') or ''),
            'price_list_rate': float(getattr(item, 'price_list_rate', 0) or 0),
            'conversion_factor': float(getattr(item, 'conversion_factor', 1.0) or 1.0),
            'margin_type': str(getattr(item, 'margin_type', '') or ''),
            'margin_rate_or_amount': float(getattr(item, 'margin_rate_or_amount', 0) or 0),
            'is_free_item': int(getattr(item, 'is_free_item', 0) or 0),
            'price_list_uom_dependant': int(getattr(item, 'price_list_uom_dependant', 0) or 0),
            'child_docname': str(getattr(item, 'name', '') or ''),
            'batch_no': str(getattr(item, 'batch_no', '') or '')
        }

        args.update({
            'price_list_uom_dependant': int(item_dict.get('price_list_uom_dependant', 0) or 0),
            'child_docname': str(item_dict.get('child_docname', '') or ''),
            'item_code': str(item_dict.get('item_code', '') or ''),
            'item_group': frappe.get_value('Item', item_dict.get('item_code', ''), 'item_group') or '',
            'brand': frappe.get_value('Item', item_dict.get('item_code', ''), 'brand') or '',
            'qty': float(item_dict.get('qty', 0) or 0),
            'stock_qty': float(item_dict.get('qty', 0) or 0),
            'uom': str(item_dict.get('uom', '') or ''),
            'stock_uom': str(item_dict.get('stock_uom', '') or ''),
            'parenttype': doctype,
            'parent': str(doc.name or ''),
            'pricing_rules': str(item_dict.get('pricing_rules', '') or ''),
            'is_free_item': int(item_dict.get('is_free_item', 0) or 0),
            'warehouse': str(item_dict.get('warehouse', '') or ''),
            'batch_no': str(item_dict.get('batch_no', '') or ''),
            'price_list_rate': float(item_dict.get('price_list_rate', 0) or 0),
            'conversion_factor': float(item_dict.get('conversion_factor', 1.0) or 1.0),
            'margin_type': str(item_dict.get('margin_type', '') or ''),
            'margin_rate_or_amount': float(item_dict.get('margin_rate_or_amount', 0) or 0)
        })

    return frappe._dict(args)


def set_free_item_tax_template(doc, free_item):
    """Set item_tax_template and item_tax_rate on a hand-built free item row,
    resolved from the Item then its Item Group tree like get_item_details does."""
    out = {}
    get_item_tax_template(
        {
            "company": doc.company,
            "tax_category": doc.get("tax_category"),
            "posting_date": doc.get("posting_date"),
            "transaction_date": doc.get("transaction_date"),
            "net_rate": 0,
        },
        frappe.get_cached_doc("Item", free_item["item_code"]),
        out,
    )
    if out.get("item_tax_template"):
        free_item["item_tax_template"] = out["item_tax_template"]
        free_item["item_tax_rate"] = get_item_tax_map(doc.company, out["item_tax_template"])


def get_free_qty(pricing_rule, transaction_qty):
    # .get(): apply_recursion_over / round_free_qty only exist from ERPNext v14.5.0
    free_qty = flt(pricing_rule.get("free_qty")) or 1
    recurse_for = flt(pricing_rule.get("recurse_for"))
    qty = free_qty
    if pricing_rule.get("is_recursive") and recurse_for:
        recursive_qty = flt(transaction_qty) - flt(pricing_rule.get("apply_recursion_over"))
        if recursive_qty > 0:
            qty = recursive_qty * free_qty / recurse_for
            if pricing_rule.get("round_free_qty"):
                qty = (recursive_qty // recurse_for) * free_qty
    return qty


def uses_serial_and_batch_bundle():
    """True on ERPNext v15+, where batches are picked through Serial and Batch Bundles.
    On v14 each row carries a single `batch_no`."""
    return frappe.get_meta("Sales Invoice Item").has_field("serial_and_batch_bundle")


def get_available_batches(item_code, warehouse, company=None, posting_date=None, posting_time=None):
    """
    Available batches of `item_code` in `warehouse` as [frappe._dict(batch_no, qty)]
    (qty in stock UOM), in the order ERPNext itself auto-picks them (Stock Settings
    "Pick Serial / Batch Based On": FIFO / LIFO by batch creation, or Expiry).

    Uses `get_auto_batch_nos`, which drops expired, reserved and POS-held qty. ERPNext v15
    changed `erpnext.stock.doctype.batch.batch.get_batch_no` to take a Serial and Batch
    Bundle id, which is why that function isn't used.
    """
    if not item_code or not warehouse:
        return []
    if not frappe.get_cached_value("Item", item_code, "has_batch_no"):
        return []

    if not uses_serial_and_batch_bundle():
        # v14: the same batches, in the same order (expiry, then creation), that
        # set_batch_nos picks from
        from erpnext.stock.doctype.batch.batch import get_batches

        return [
            frappe._dict({"batch_no": b.batch_id, "qty": flt(b.qty)})
            for b in get_batches(item_code, warehouse)
            if flt(b.qty) > 0
        ]

    from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
        get_auto_batch_nos,
    )

    qty_by_batch = {}
    for row in get_auto_batch_nos(
        frappe._dict(
            {
                "item_code": item_code,
                "warehouse": warehouse,
                "company": company,
                "posting_date": posting_date,
                "posting_time": posting_time,
                "based_on": frappe.db.get_single_value(
                    "Stock Settings", "pick_serial_and_batch_based_on"
                ),
            }
        )
    ):
        if row.get("warehouse") and row.get("warehouse") != warehouse:
            continue
        if flt(row.get("qty")) > 0:
            batch_no = row.get("batch_no")
            qty_by_batch[batch_no] = qty_by_batch.get(batch_no, 0.0) + flt(row.get("qty"))

    return [frappe._dict({"batch_no": b, "qty": q}) for b, q in qty_by_batch.items()]


def get_batches_by_manufacturing_date(
    item_code, warehouse, company=None, posting_date=None, posting_time=None
):
    """
    Same as `get_available_batches`, but oldest `Batch.manufacturing_date` first. A batch
    with no manufacturing date uses its creation date instead. (ERPNext can only order by
    batch creation or expiry, so the list is re-sorted here.)
    """
    batches = get_available_batches(item_code, warehouse, company, posting_date, posting_time)
    if not batches:
        return []

    sort_keys = {
        b.name: (getdate(b.manufacturing_date or b.creation), get_datetime(b.creation), b.name)
        for b in frappe.get_all(
            "Batch",
            filters={"name": ["in", [b.batch_no for b in batches]]},
            fields=["name", "manufacturing_date", "creation"],
        )
    }
    last = (getdate("9999-12-31"), get_datetime("9999-12-31"))
    return sorted(batches, key=lambda b: sort_keys.get(b.batch_no) or (*last, b.batch_no))


def allocate_batches(batches, qty, used_qty_map=None, unit=None):
    """
    Take `qty` (stock UOM) from `batches` in their given order, spilling into the next
    batch when one runs out.

    `used_qty_map` ({batch_no: stock_qty}) is qty already taken from a batch by other rows
    of the same unsaved document; it is subtracted from that batch's availability.

    `unit` is the stock qty of one transaction-UOM unit (the row's conversion factor).
    When given, each batch only gives whole multiples of it, so every resulting row has a
    whole qty in its UOM (a batch holding 9 pcs gives nothing towards a box of 12).

    Returns (allocation, remaining): allocation is [(batch_no, stock_qty)], remaining is
    the stock qty the batches couldn't cover (0 when fully covered).
    """
    remaining = flt(qty)
    unit = flt(unit)
    used_qty_map = used_qty_map or {}
    allocation = []
    for batch in batches:
        if flt(remaining, 6) <= 0:
            break
        available = flt(batch.qty) - flt(used_qty_map.get(batch.batch_no))
        take = min(available, remaining)
        if unit > 0:
            take = int(flt(take / unit, 6)) * unit
        if flt(take, 6) <= 0:
            continue
        allocation.append((batch.batch_no, take))
        remaining -= take

    return allocation, max(flt(remaining, 6), 0.0)


def get_available_stock(item_code, warehouse, posting_date=None, posting_time=None):
    """Actual stock (stock UOM) of `item_code` in `warehouse` as of the posting date/time."""
    if not item_code or not warehouse:
        return 0.0

    from erpnext.stock.utils import get_stock_balance

    return flt(get_stock_balance(item_code, warehouse, posting_date, posting_time))
