import os, re, shutil, sqlite3, sys
def load(p):
    return open(p, encoding='utf-8').read()
fails, out = [], {}
# ---------- routes/po.py : payment choice, payable posting, PO status ----------
po = load('routes/po.py')
closed = '            raise ValueError("This purchase order is already closed.")\n'
status_re = re.compile(r"^([ \t]*)po\.status = 'FULLY_RECEIVED' if all_lines_fully_received else 'PARTIAL_RECEIVED'[ \t]*$", re.M)
if po.count(closed) != 1 or len(status_re.findall(po)) != 1 or 'record_purchase' in po:
    fails.append('routes/po.py: closed-PO check or status line not found once, or already patched')
else:
    po = po.replace(closed, closed +
        "        payment_method = str(data.get('payment_method') or '').strip().upper()\n"
        "        if payment_method not in ('CASH', 'MPESA', 'BANK', 'ON_ACCOUNT'):\n"
        "            raise ValueError('Choose how this purchase was paid.')\n", 1)
    def status_repl(m):
        i = m.group(1)
        return '\n'.join([
            i + "db.session.flush()",
            i + "all_po_lines = PurchaseOrderLine.query.filter_by(po_header_id=po.id).all()",
            i + "all_lines_fully_received = all(((l.qty_received or 0.0) + (l.qty_rejected or 0.0)) >= (l.qty_ordered or 0.0) - 0.0001 for l in all_po_lines)",
            i + "po.status = 'FULLY_RECEIVED' if all_lines_fully_received else 'PARTIAL_RECEIVED'",
            i + "if grpo_total_value > 0:",
            i + "    from routes.payables import record_purchase",
            i + "    record_purchase(po.supplier_id, po.id, grpo_ref, grpo_total_value, payment_method, g.user.username)",
        ])
    po = status_re.sub(status_repl, po, count=1)
    if 'from flask import g' not in po:
        po = 'from flask import g\n' + po
    out['routes/po.py'] = po
# ---------- routes/pos.py : sales history survives deleted items ----------
pos = load('routes/pos.py')
old = "db.session.get(FeedIngredient, l.ingredient_id).name"
if pos.count(old) != 1:
    fails.append('routes/pos.py: sales-history item name expression not found once')
else:
    out['routes/pos.py'] = pos.replace(old, "(getattr(db.session.get(FeedIngredient, l.ingredient_id), 'name', None) or '(deleted item)')")
# ---------- services/pos_service.py : cost of goods sold ----------
ps = load('services/pos_service.py')
cogs_re = re.compile(r"^([ \t]*)if credit_amount > 0:\n[ \t]*post_gl_entry\(sale_id, '1300', credit_amount, 0\.0, 'POS', order\.id\)\n", re.M)
if len(cogs_re.findall(ps)) != 1 or "'5000'" in ps:
    fails.append('services/pos_service.py: credit posting block not found once, or COGS already present')
else:
    def cogs_repl(m):
        i = m.group(1)
        return (m.group(0) +
                i + "if total_cost > 0:\n" +
                i + "    post_gl_entry(sale_id, '5000', total_cost, 0.0, 'POS', order.id)\n" +
                i + "    post_gl_entry(sale_id, '1200', 0.0, total_cost, 'POS', order.id)\n")
    out['services/pos_service.py'] = cogs_re.sub(cogs_repl, ps, count=1)
# ---------- routes/retail.py : daily report counts real receipts ----------
rt = load('routes/retail.py')
a = "        by_method = {m: round(a or 0, 2) for m, a in rows}\n"
b = "        by_method=by_method)"
if rt.count(a) != 1 or rt.count(b) != 1:
    fails.append('routes/retail.py: daily report lines not found once')
else:
    rt = rt.replace(a,
        "        by_method = {m: round(a or 0, 2) for m, a in rows if m != 'CREDIT'}\n"
        "        change_total = round(sum(o.change_due or 0 for o in orders), 2)\n"
        "        if change_total and 'CASH' in by_method:\n"
        "            by_method['CASH'] = round(by_method['CASH'] - change_total, 2)\n", 1)
    rt = rt.replace(b, "        by_method=by_method,\n        credit_sales=round(sum(o.credit_amount or 0 for o in orders), 2))", 1)
    out['routes/retail.py'] = rt
# ---------- templates/tabs/retail_tab.html : show credit sales ----------
rtab = load('templates/tabs/retail_tab.html')
c = "Discounts: KSh ${d.discounts.toFixed(2)}</p>"
if rtab.count(c) != 1:
    fails.append('retail_tab.html: report summary line not found once')
else:
    out['templates/tabs/retail_tab.html'] = rtab.replace(c,
        "Discounts: KSh ${d.discounts.toFixed(2)} &nbsp; On credit: KSh ${(d.credit_sales || 0).toFixed(2)}</p>", 1)
# ---------- templates/tabs/po_tab.html : payment dropdown ----------
pt = load('templates/tabs/po_tab.html')
inp = re.findall(r'<input type="text" id="grn-delivery-note"[^>]*>', pt)
body_old = "body: JSON.stringify({ received_items: receiptLines })"
open_old = "document.getElementById('receive-grn-modal').style.display = 'block';"
if len(inp) != 1 or pt.count(body_old) != 1 or pt.count(open_old) != 1 or 'grn-payment-method' in pt:
    fails.append('po_tab.html: delivery-note input, request body or modal-open line not found once, or already patched')
else:
    select = ('\n            <select id="grn-payment-method" style="flex:1; padding:8px;">'
              '<option value="">-- How was this paid? --</option>'
              '<option value="CASH">Paid - Cash</option>'
              '<option value="MPESA">Paid - M-Pesa</option>'
              '<option value="BANK">Paid - Bank</option>'
              '<option value="ON_ACCOUNT">Owed to supplier</option></select>')
    pt = pt.replace(inp[0], inp[0] + select, 1)
    pt = pt.replace(body_old, "body: JSON.stringify({ received_items: receiptLines, payment_method: document.getElementById('grn-payment-method').value })", 1)
    pt = pt.replace(open_old, "document.getElementById('grn-payment-method').value = '';\n            " + open_old, 1)
    out['templates/tabs/po_tab.html'] = pt
if fails:
    print('NOTHING CHANGED. Problems:')
    for f in fails:
        print(' -', f)
    sys.exit(1)
for path, text in out.items():
    shutil.copy(path, path + '.bak')
    open(path, 'w', encoding='utf-8', newline='\n').write(text)
    print('PATCHED', path)
# ---------- chart of accounts (only if the table is empty) ----------
db = sqlite3.connect('emining_erp.db')
if db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0] == 0:
    db.executemany('INSERT INTO accounts (account_code, name, category) VALUES (?,?,?)', [
        ('1000', 'Cash and Bank', 'ASSET'),
        ('1200', 'Inventory', 'ASSET'),
        ('1300', 'Customer Receivables', 'ASSET'),
        ('2000', 'Supplier Payables', 'LIABILITY'),
        ('3000', 'Opening Balance Equity', 'EQUITY'),
        ('4000', 'Sales Revenue', 'REVENUE'),
        ('5000', 'Cost of Goods Sold', 'EXPENSE')])
    db.commit()
    print('Chart of accounts loaded')
else:
    print('Accounts table already has rows - left alone')
