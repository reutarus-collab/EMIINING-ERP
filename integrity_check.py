"""Read-only health check. Changes NOTHING.   python scripts/integrity_check.py

1. Every journal reference balances (debits = credits)
2. Customer balances agree with the Accounts Receivable account (1300)
3. Supplier balances agree with the Payables account (2000)
4. Outlet stock agrees with the stock movement history
5. Company-wide stock total agrees with the sum of outlet stock
Tolerance: KSh 0.01 / 0.01 kg. A difference is a thing to investigate, not always a bug
(for example opening stock typed in directly has no movement history).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sqlalchemy import func
from app import app
from services.db import db
from services.models import (GeneralLedgerEntry as GL, Customer, LocationStock, StockMovement,
                             FeedIngredient, Location, SupplierTxn)

TOL = 0.01
problems = 0

def section(t): print('\n== ' + t)
def flag(msg):
    global problems
    problems += 1
    print('  PROBLEM  ' + msg)

with app.app_context():
    section('1. Journal balance (debits = credits per reference)')
    rows = db.session.query(GL.transaction_ref, func.sum(GL.debit), func.sum(GL.credit)).group_by(GL.transaction_ref).all()
    bad = [(r, d or 0, c or 0) for r, d, c in rows if abs((d or 0) - (c or 0)) > TOL]
    for r, d, c in bad[:25]:
        flag('%s  debits %.2f  credits %.2f  (off by %.2f)' % (r, d, c, d - c))
    if len(bad) > 25:
        print('  ... and %d more' % (len(bad) - 25))
    td = sum(d or 0 for _, d, _ in rows); tc = sum(c or 0 for _, _, c in rows)
    print('  %d references checked, total debits %.2f, total credits %.2f%s' % (len(rows), td, tc, '' if not bad else '  <-- see above'))

    section('2. Customer balances vs Accounts Receivable (1300)')
    cust = sum(c.current_balance or 0 for c in Customer.query.all())
    d, c = db.session.query(func.sum(GL.debit), func.sum(GL.credit)).filter(GL.account_code == '1300').one()
    gl_ar = (d or 0) - (c or 0)
    print('  customers owe %.2f   |   GL 1300 says %.2f' % (cust, gl_ar))
    if abs(cust - gl_ar) > TOL:
        flag('customer balances and AR differ by %.2f' % (cust - gl_ar))

    section('3. Supplier balances vs Payables (2000)')
    bought = db.session.query(func.sum(SupplierTxn.amount)).filter(SupplierTxn.kind == 'PURCHASE').scalar() or 0
    paid = db.session.query(func.sum(SupplierTxn.amount)).filter(SupplierTxn.kind == 'PAYMENT').scalar() or 0
    d, c = db.session.query(func.sum(GL.debit), func.sum(GL.credit)).filter(GL.account_code == '2000').one()
    gl_ap = (c or 0) - (d or 0)
    print('  suppliers owed %.2f   |   GL 2000 says %.2f' % (bought - paid, gl_ap))
    if abs((bought - paid) - gl_ap) > TOL:
        flag('supplier balances and payables differ by %.2f' % ((bought - paid) - gl_ap))

    section('4. Outlet stock vs stock movement history')
    names = {i.id: i.name for i in FeedIngredient.query.all()}
    locs = {l.id: l.name for l in Location.query.all()}
    moved = {(i, l): q or 0 for i, l, q in db.session.query(
        StockMovement.ingredient_id, StockMovement.location_id, func.sum(StockMovement.qty_kg)).group_by(
        StockMovement.ingredient_id, StockMovement.location_id).all()}
    n = 0
    for s in LocationStock.query.all():
        hist = moved.get((s.ingredient_id, s.location_id), 0)
        if abs((s.quantity_kg or 0) - hist) > TOL:
            n += 1
            if n <= 25:
                flag('%s @ %s: stock %.3f kg, history %.3f kg (off by %.3f)' % (
                    names.get(s.ingredient_id, s.ingredient_id), locs.get(s.location_id, s.location_id),
                    s.quantity_kg or 0, hist, (s.quantity_kg or 0) - hist))
    if n > 25:
        print('  ... and %d more' % (n - 25))
    if n == 0:
        print('  all outlet stock rows match their movement history')

    section('5. Company stock total vs sum of outlets')
    totals = {i: q or 0 for i, q in db.session.query(LocationStock.ingredient_id, func.sum(LocationStock.quantity_kg)).group_by(LocationStock.ingredient_id).all()}
    n = 0
    for it in FeedIngredient.query.all():
        if abs((it.stock_quantity_kg or 0) - totals.get(it.id, 0)) > TOL:
            n += 1
            if n <= 25:
                flag('%s: company total %.3f kg, outlets add up to %.3f kg' % (it.name, it.stock_quantity_kg or 0, totals.get(it.id, 0)))
    if n == 0:
        print('  all company totals match')

    print('\n%s' % ('ALL CHECKS PASSED' if problems == 0 else '%d problem(s) found. Nothing was changed.' % problems))
    sys.exit(1 if problems else 0)