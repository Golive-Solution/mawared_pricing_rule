import frappe

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