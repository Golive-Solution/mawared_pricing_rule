import frappe
from frappe import _
from frappe.utils import today , flt
from collections import defaultdict
from frappe.query_builder.functions import IfNull
from mawared_pricing_rule.event.erp_utils import get_pricing_rules
from mawared_pricing_rule.event.utils import (
    build_json_args,
    set_free_item_tax_template,
    get_free_qty,
    get_available_batches,
    get_batches_by_manufacturing_date,
    allocate_batches,
    get_available_stock,
)


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
                "warehouse": item.warehouse or doc.set_warehouse,
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

    # Paid rows take batches oldest manufacturing date first (split per batch), so the
    # free rows below see exactly which batch qty is left. Returns keep the batches they
    # were sold from.
    plan_batches = doc.update_stock and not doc.is_return
    if plan_batches:
        assign_paid_item_batches(doc)

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
                free_qty = get_free_qty(price_rule_doc, value["qty"])
                free_warehouse = value["warehouse"] or doc.set_warehouse

                # A different free item can't reuse the paid item's UOM / name
                if free_item_code == value["item_code"]:
                    free_uom = value["uom"]
                    free_conversion_factor = flt(value["conversion_factor"]) or 1
                    free_item_name = value["item_name"]
                else:
                    free_uom = frappe.get_value("Item", free_item_code, "stock_uom")
                    free_conversion_factor = 1
                    free_item_name = frappe.get_value("Item", free_item_code, "item_name")
                free_stock_qty = flt(free_qty) * free_conversion_factor

                # [(batch_no, stock_qty)]: one free row per batch
                allocation = [(None, free_stock_qty)]
                skip_free = False
                if plan_batches:
                    allocation, skip_free = check_free_item_stock(
                        doc, free_item_code, free_warehouse, free_stock_qty, free_conversion_factor
                    )

                free_item_base = {
                    "item_code": free_item_code,
                    "qty": free_qty,
                    "rate": 0,
                    "is_free_item": 1,
                    "price_list_rate":0,
                    "item_group": frappe.get_value("Item", free_item_code, "item_group"),
                    "brand": frappe.get_value("Item", free_item_code, "brand"),
                    "image": frappe.get_value("Item", free_item_code, "image"),
                    "stock_uom": frappe.get_value("Item", free_item_code, "stock_uom"),
                    "stock_qty": free_stock_qty,
                    "amount": 0,
                    "discount_amount": 0,
                    "discount_percentage":100,
                    "description": "Free item applied due to bulk purchase",
                    "income_account": value["income_account"],
                    "cost_center": value["cost_center"],
                    "expense_account": value["expense_account"],
                    "conversion_factor": free_conversion_factor,
                    "uom": free_uom,
                    "item_name": free_item_name,
                    "base_price_list_rate": 0,
                    "base_rate": 0,
                    "base_amount": 0,
                    "warehouse": free_warehouse,
                    "custom_is_prepayment":0,
                    "custom_is_down_payment":0,
                    "custom_vat_rate": value["custom_vat_rate"],
                    "custom_tax_category_code": value["custom_tax_category_code"],
                    "custom_support_ksa_e_invoice_integration_phase_ii": value["custom_support_ksa_e_invoice_integration_phase_ii"],
                }
                
                if not skip_free:
                    for batch_no, stock_qty in allocation:
                        free_item = dict(free_item_base)
                        free_item.update(
                            {
                                "qty": flt(stock_qty) / free_conversion_factor,
                                "stock_qty": stock_qty,
                                "batch_no": batch_no,
                                "use_serial_batch_fields": 1 if batch_no else 0,
                            }
                        )
                        set_free_item_tax_template(doc, free_item)
                        doc.append("items", free_item)
                # update the price in the bass item
                for item in doc.items:
                    if item.item_code == value["item_code"] and not item.is_free_item:
                        # don't record the rule if its free item could not be given
                        item.custom_applied_pricing_rule = "" if skip_free else price_rule_doc.name
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


def get_row_stock_qty(row):
    return flt(row.stock_qty) or flt(row.qty) * (flt(row.conversion_factor) or 1)


def get_batch_usage(doc, item_code, warehouse, include_unbatched=True):
    """
    {batch_no: stock_qty} taken by rows of this invoice for `item_code` + `warehouse`.

    Rows with a batch or a Serial and Batch Bundle count as they are. A batch-item row with
    neither gets its batches from ERPNext's own picker on submit; with `include_unbatched`
    that pick is simulated (ERPNext's order, on top of what earlier rows took) so other
    rows don't plan on the same qty.
    """
    used = defaultdict(float)
    erp_batches = None
    for row in doc.items:
        if row.item_code != item_code or (row.warehouse or doc.set_warehouse) != warehouse:
            continue
        if row.batch_no:
            used[row.batch_no] += get_row_stock_qty(row)
        elif row.serial_and_batch_bundle:
            # batches picked by the user through the Serial and Batch Bundle dialog
            for entry in frappe.get_all(
                "Serial and Batch Entry",
                filters={"parent": row.serial_and_batch_bundle},
                fields=["batch_no", "qty"],
            ):
                if entry.batch_no:
                    used[entry.batch_no] += abs(flt(entry.qty))
        elif include_unbatched and frappe.get_cached_value("Item", item_code, "has_batch_no"):
            if erp_batches is None:
                erp_batches = get_available_batches(
                    item_code, warehouse, doc.company, doc.posting_date, doc.posting_time
                )
            picked, _remaining = allocate_batches(erp_batches, get_row_stock_qty(row), used)
            for batch_no, qty in picked:
                used[batch_no] += qty
    return used


def allocate_row_batches(doc, item_code, warehouse, stock_qty, conversion_factor, batches, used):
    """
    Split one row of `stock_qty` across `batches` (oldest manufacturing date first).

    When the row's qty is a whole number of its UOM, every part stays a whole number too:
    a batch only gives whole units (a batch with 9 pcs gives nothing towards a box of 12).
    Whole units no single batch can supply on its own (e.g. a box of 12 when batch A has 9
    and batch B has 3 left) come back as one part with batch None. That row is left without
    a batch, and ERPNext's own picker fills it from several batches on submit.

    Returns [(batch_no or None, stock_qty)], or [] when the batches can't cover the row.
    `used` ({batch_no: stock_qty}) is updated with what the parts take, ERPNext's pick for
    the batch-less part included.
    """
    conversion_factor = flt(conversion_factor) or 1
    qty = flt(stock_qty) / conversion_factor
    unit = conversion_factor if abs(qty - round(qty)) < 1e-6 else None

    taken = defaultdict(float, used)
    allocation, remaining = allocate_batches(batches, stock_qty, taken, unit)
    for batch_no, part_qty in allocation:
        taken[batch_no] += part_qty

    if remaining:
        picked, short = allocate_batches(
            get_available_batches(item_code, warehouse, doc.company, doc.posting_date, doc.posting_time),
            remaining,
            taken,
        )
        if short:
            return []
        for batch_no, part_qty in picked:
            taken[batch_no] += part_qty
        allocation.append((None, remaining))

    used.update(taken)
    return allocation


def is_plannable_batch_item(item_code):
    """Batch items whose rows we split. Serial-numbered items are left to ERPNext: their
    rows carry serial nos that must not be merged or copied onto split rows."""
    has_batch_no, has_serial_no = frappe.get_cached_value(
        "Item", item_code, ["has_batch_no", "has_serial_no"]
    ) or (0, 0)
    return bool(has_batch_no) and not has_serial_no


# Rows only merge when all of these match, so rows the user kept apart on purpose (other
# cost center, discount, account...) stay apart. Rows split by batch share all of them.
MERGE_KEY_FIELDS = (
    "item_code",
    "uom",
    "rate",
    "discount_percentage",
    "discount_amount",
    "cost_center",
    "income_account",
    "expense_account",
    "project",
)


def merge_paid_batch_rows(doc):
    """
    Merge paid rows of the same batch item and warehouse (and `MERGE_KEY_FIELDS`) into one
    row and clear its batch, so `assign_paid_item_batches` re-splits the whole qty from
    scratch.

    Re-planning the full qty on every save gives the same clean rows for the same total,
    instead of small extra rows piling up each time a qty is edited, and keeps batches in
    manufacturing-date order (a batch typed in by hand is replaced too). Rows with a
    Serial and Batch Bundle (picked through the batch dialog) are left as they are.
    """
    merged = {}
    rows = []
    for row in doc.items:
        warehouse = row.warehouse or doc.set_warehouse
        if (
            row.is_free_item
            or row.serial_and_batch_bundle
            or not warehouse
            or not is_plannable_batch_item(row.item_code)
        ):
            rows.append(row)
            continue

        key = (warehouse,) + tuple(
            flt(row.get(f), 6) if isinstance(row.get(f), (int, float)) else (row.get(f) or "")
            for f in MERGE_KEY_FIELDS
        )
        first = merged.get(key)
        if first is None:
            merged[key] = row
            row.stock_qty = get_row_stock_qty(row)
            row.batch_no = None
            row.use_serial_batch_fields = 0
            rows.append(row)
        else:
            first.stock_qty = flt(first.stock_qty) + get_row_stock_qty(row)
            first.qty = flt(first.qty) + flt(row.qty)

    if len(rows) != len(doc.items):
        doc.items = []
        for row in rows:
            doc.append("items", row)
        for idx, row in enumerate(doc.items, start=1):
            row.idx = idx


def assign_paid_item_batches(doc):
    """
    Give each paid row of a batch item that has no batch yet a batch, oldest manufacturing
    date first. A row larger than what is left in its batch is split into one row per
    batch (same rate and fields, qty divided between them, whole UOM units kept whole;
    see `allocate_row_batches`).

    Paid rows are first merged back (same item, warehouse, rate, accounts...) and re-split from
    scratch (see `merge_paid_batch_rows`); rows with a Serial and Batch Bundle are left as
    they are. A row the warehouse can't cover is left without a batch, and ERPNext reports
    the shortage on submit.
    """
    def needs_batch(row):
        return (
            not row.is_free_item
            and not row.batch_no
            and not row.serial_and_batch_bundle
            and (row.warehouse or doc.set_warehouse)
            and is_plannable_batch_item(row.item_code)
        )

    merge_paid_batch_rows(doc)
    if not any(needs_batch(row) for row in doc.items):
        return

    batches = {}
    used = {}
    for row in doc.items:
        if needs_batch(row):
            key = (row.item_code, row.warehouse or doc.set_warehouse)
            if key not in batches:
                batches[key] = get_batches_by_manufacturing_date(
                    key[0], key[1], doc.company, doc.posting_date, doc.posting_time
                )
                # rows still waiting for a batch are the ones being allocated now
                used[key] = get_batch_usage(doc, *key, include_unbatched=False)

    rows = list(doc.items)
    doc.items = []
    for row in rows:
        if not needs_batch(row):
            doc.append("items", row)
            continue

        key = (row.item_code, row.warehouse or doc.set_warehouse)
        conversion_factor = flt(row.conversion_factor) or 1
        allocation = allocate_row_batches(
            doc, key[0], key[1], get_row_stock_qty(row), conversion_factor, batches[key], used[key]
        )
        if not allocation:
            doc.append("items", row)
            continue

        template = row.as_dict()
        for idx, (batch_no, stock_qty) in enumerate(allocation):
            if idx == 0:
                new_row = row
                doc.append("items", new_row)
            else:
                values = dict(template)
                for field in ("name", "idx", "serial_and_batch_bundle"):
                    values.pop(field, None)
                new_row = doc.append("items", values)

            new_row.batch_no = batch_no
            new_row.use_serial_batch_fields = 1 if batch_no else 0
            new_row.stock_qty = stock_qty
            new_row.qty = flt(stock_qty) / conversion_factor
            new_row.amount = flt(new_row.qty) * flt(new_row.rate)
            new_row.base_amount = flt(new_row.qty) * flt(new_row.base_rate)

    for idx, row in enumerate(doc.items, start=1):
        row.idx = idx


def check_free_item_stock(doc, item_code, warehouse, stock_qty, conversion_factor=1):
    """
    Check that `warehouse` can supply `stock_qty` (stock UOM) of the free item on top of
    what the other rows of this invoice already take from it.

    Returns (allocation, skip). `allocation` is [(batch_no, stock_qty)] from
    `allocate_row_batches` (oldest manufacturing date first, whole UOM units, a None batch
    for a part ERPNext picks), or [(None, stock_qty)] for items without batches. When stock
    is short, a warning is shown and skip is True.
    """
    if not warehouse:
        _warn_free_item_skipped(item_code, _("not set"), 0, stock_qty)
        return [], True

    # qty already consumed by rows in this invoice from the same item + warehouse
    used_in_doc = sum(
        get_row_stock_qty(row)
        for row in doc.items
        if row.item_code == item_code and (row.warehouse or doc.set_warehouse) == warehouse
    )

    available = get_available_stock(item_code, warehouse, doc.posting_date, doc.posting_time)
    free_available = available - used_in_doc
    if free_available < flt(stock_qty):
        _warn_free_item_skipped(item_code, warehouse, free_available, stock_qty)
        return [], True

    if not frappe.get_cached_value("Item", item_code, "has_batch_no"):
        return [(None, stock_qty)], False

    allocation = allocate_row_batches(
        doc,
        item_code,
        warehouse,
        stock_qty,
        conversion_factor,
        get_batches_by_manufacturing_date(
            item_code, warehouse, doc.company, doc.posting_date, doc.posting_time
        ),
        get_batch_usage(doc, item_code, warehouse),
    )
    if not allocation:
        # stock exists, but not enough of it is in usable (unexpired, unreserved) batches
        _warn_free_item_skipped(item_code, warehouse, free_available, stock_qty, batch=True)
        return [], True

    return allocation, False


def _warn_free_item_skipped(item_code, warehouse, available, required, batch=False):
    if batch:
        msg = _(
            "Free item {0} was not added: batches in warehouse {1} don't have {2} usable (unexpired, unreserved) qty."
        ).format(frappe.bold(item_code), frappe.bold(warehouse), flt(required))
    else:
        msg = _(
            "Free item {0} was not added: insufficient stock in warehouse {1} (available {2}, required {3})."
        ).format(frappe.bold(item_code), frappe.bold(warehouse), max(flt(available), 0), flt(required))
    frappe.msgprint(msg, title=_("Free Item Skipped"), indicator="orange")


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