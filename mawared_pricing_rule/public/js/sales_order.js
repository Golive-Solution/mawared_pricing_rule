frappe.ui.form.on('Sales Order', {
    onload: function(frm) {
        frm.doc.ignore_pricing_rule = 1
        // hide the ignore pricing rule checkbox
        frm.toggle_display('ignore_pricing_rule', false)
    },
    custom_ignore_pricing_rule: function(frm) {
        if (frm.doc.custom_ignore_pricing_rule) {
            custom_ignore_logic(frm);
        }else{
            // db set new values in items for custom_rate
            frm.doc.items.forEach(d => {
                d.custom_rate = d.rate
            })
            frm.refresh_field("items");
        }
    },
});

function custom_ignore_logic(frm) {
    let me = { 
        frm: frm,
        in_apply_price_list: false,
        _set_values_for_item_list: function(items) {
            items.forEach(item => {
                const row = frm.doc.items.find(i => i.name === item.name);
                if (row) {
                    frappe.model.set_value(row.doctype, row.name, {
                        "price_list_rate": item.price_list_rate || item.rate,
                        "rate": item.rate || item.price_list_rate || 0,
                        "discount_percentage": item.discount_percentage || 0,
                        "pricing_rules": "",
                        "amount": (item.qty || row.qty) * (item.rate || row.rate || 0),
                        "base_rate": item.base_rate || item.rate || 0,
                        "base_amount": (item.qty || row.qty) * (item.base_rate || item.rate || 0)
                    });
                }
            });

            frm.trigger("calculate_taxes_and_totals");
        },
        remove_pricing_rule: function(item) {
            const row = frm.doc.items.find(i => i.name === item.name);
            if (row) {
                frappe.model.set_value(row.doctype, row.name, "pricing_rules", "");
            }
        },
        calculate_taxes_and_totals: function() {
            frm.trigger("calculate_taxes_and_totals");
        }
    };

    if (me.in_apply_price_list) return;
    me.in_apply_price_list = true;

    let item_list = [];

    (frm.doc.items || []).forEach(d => {
        if (!d.item_code) return;

        if (d.is_free_item) {
            me.frm.get_row_manager().remove_child(d);
        } else {
            item_list.push({
                doctype: d.doctype,
                name: d.name,
                item_code: d.item_code,
                pricing_rules: d.pricing_rules,
                parenttype: d.parenttype,
                parent: d.parent,
                price_list_rate: d.price_list_rate,
                qty: d.qty,
                uom: d.uom,
                warehouse: d.warehouse,
                description: d.description
            });
        }
    });

    me.frm.refresh_field("items");

    // Ensure we have a selling_price_list
    if (!frm.doc.selling_price_list) {
        frappe.throw(__("Please select a Selling Price List"));
        me.in_apply_price_list = false;
        return;
    }

    if (item_list.length === 0) {
        me.in_apply_price_list = false;
        return;
    }

    return me.frm.call({
        method: "erpnext.stock.get_item_details.apply_price_list",
        args: {
            args: {
                items: item_list,
                price_list: me.frm.doc.selling_price_list
            },
            doc: me.frm.doc
        },
        callback: function(r) {
            if (!r.exc && r.message) {
                frappe.run_serially([
                    () => me.frm.set_value("price_list_currency", r.message.parent.price_list_currency),
                    () => me.frm.set_value("plc_conversion_rate", r.message.parent.plc_conversion_rate),
                    () => {
                        me._set_values_for_item_list(r.message.children);
                    }
                ]);
            } else {
                frappe.throw(__("Failed to apply price list."));
            }
            me.in_apply_price_list = false;
        }
    }).always(() => {
        me.in_apply_price_list = false;
    });
}

frappe.ui.form.on('Sales Order Item', {
    rate: function (frm, cdt, cdn) {
        let item = locals[cdt][cdn];
        // Assume user changed rate manually
        item.custom_rate = item.rate;
        item.custom_man_change = 1;
        frm.refresh_field('items');
    }
});