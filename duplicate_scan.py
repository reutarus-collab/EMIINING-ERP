"""Read-only scan for SUSPECTED duplicate records. Changes NOTHING.   python scripts/duplicate_scan.py

A duplicate receipt, payment or expense still balances in the books, so the integrity check
cannot see it. This looks for the same thing recorded twice within 10 minutes by the same
person. Two genuinely identical transactions can happen, so treat each hit as a question to ask.
"""
import os, sys
from datetime import timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import app
from services.models import (OperatingExpense, SupplierTxn, CustomerPayment, SalesRefund,
                             TillCashMovement, GoodsReceiptNote, GoodsReceiptLine)

WINDOW = timedelta(minutes=10)
hits = 0

def scan(title, rows, key, when, who, show):
    """rows with the same key whose times are within WINDOW of the previous one."""
    global hits
    groups = {}
    for r in rows:
        groups.setdefault(key(r), []).append(r)
    printed = False
    for k, items in groups.items():
        items.sort(key=lambda r: when(r) or 0)
        for a, b in zip(items, items[1:]):
            if when(a) and when(b) and when(b) - when(a) <= WINDOW:
                if not printed:
                    print('\n== ' + title)
                    printed = True
                hits += 1
                print('  SUSPECT  %s  <->  %s' % (show(a), show(b)))
    if not printed:
        print('\n== %s: none found' % title)

def t(r): return r.created_at.strftime('%d %b %H:%M') if r.created_at else '?'

with app.app_context():
    scan('Expenses (same amount, category, description, person)', OperatingExpense.query.all(),
         lambda r: (r.location_id, r.amount, r.category, r.description.strip().lower(), r.created_by),
         lambda r: r.created_at, None, lambda r: '%s KSh %.2f %s "%s" by %s at %s' % (r.reference, r.amount, r.category, r.description, r.created_by, t(r)))
    scan('Supplier payments (same supplier and amount)', SupplierTxn.query.filter_by(kind='PAYMENT').all(),
         lambda r: (r.supplier_id, r.amount, r.created_by), lambda r: r.created_at, None,
         lambda r: '%s KSh %.2f supplier #%s at %s' % (r.ref, r.amount, r.supplier_id, t(r)))
    scan('Goods received / purchases booked (same PO and amount)', SupplierTxn.query.filter_by(kind='PURCHASE').all(),
         lambda r: (r.supplier_id, r.po_id, r.amount), lambda r: r.created_at, None,
         lambda r: '%s KSh %.2f PO #%s at %s' % (r.ref, r.amount, r.po_id, t(r)))
    grns = GoodsReceiptNote.query.all()
    lines = {}
    for ln in GoodsReceiptLine.query.all():
        lines.setdefault(ln.grn_id, set()).add((ln.ingredient_id, round(ln.qty_accepted or 0, 3)))
    scan('Goods receipt notes (same PO, same items and quantities)', grns,
         lambda g: (g.po_header_id, frozenset(lines.get(g.id, set()))), lambda g: g.received_date, None,
         lambda g: '%s PO #%s at %s' % (g.grn_number, g.po_header_id, g.received_date.strftime('%d %b %H:%M') if g.received_date else '?'))
    scan('Customer repayments (same customer and amount)', CustomerPayment.query.all(),
         lambda r: (r.customer_id, r.amount, r.created_by), lambda r: r.created_at, None,
         lambda r: '%s KSh %.2f customer #%s at %s' % (r.reference, r.amount, r.customer_id, t(r)))
    scan('Refunds (same sale and amount)', SalesRefund.query.all(),
         lambda r: (r.order_id, r.amount), lambda r: r.created_at, None,
         lambda r: '%s KSh %.2f sale #%s at %s' % (r.reference, r.amount, r.order_id, t(r)))
    scan('Till paid-in / paid-out (same till, type, amount, reason)', TillCashMovement.query.all(),
         lambda r: (r.till_session_id, r.movement_type, r.amount, (r.reason or '').strip().lower()), lambda r: r.created_at, None,
         lambda r: '%s %s KSh %.2f "%s" at %s' % (r.reference, r.movement_type, r.amount, r.reason, t(r)))
    print('\n%s' % ('No suspected duplicates.' if hits == 0 else '%d suspected duplicate pair(s). Nothing was changed.' % hits))