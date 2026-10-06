"""Owner-facing cash walk and operational loss reports."""
import math
import uuid
from datetime import date, datetime, time, timedelta

from flask import Blueprint, jsonify, request, g
from sqlalchemy import func

from routes.auth import roles_required
from services.db import db
from services.inventory import resolve_location
from services.ledger_service import post_gl_entry
from services.models import (
    Account, CashWalkOpening, Customer, EquipmentPurchase, FeedIngredient, GeneralLedgerEntry,
    GoodsReceiptLine, GoodsReceiptNote, InventoryTransfer, LocationStock,
    OperatingExpense, OrderHeader, OrderLine, OwnerWithdrawal, ProductionRun,
    PurchaseOrderHeader, PurchaseOrderLine, SalesRefund, StockMovement,
    TillCashMovement, TillSession,
)

reports_bp = Blueprint('reports', __name__)
CASH_CODES = ('1000', '1010', '1020', '1100')
ASSET_CODES = {'inventory': '1200', 'debtors': '1300', 'equipment': '1500'}
LOCAL_UTC_OFFSET = timedelta(hours=3)  # Africa/Nairobi reporting day


def _today_local():
    return (datetime.utcnow() + LOCAL_UTC_OFFSET).date()


def _money_text(value):
    return f"KSh {(value or 0.0):,.2f}"


def _date_arg(name, default):
    raw = request.args.get(name)
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f'Enter {name} as YYYY-MM-DD.')


def _period_entries(start, end):
    return GeneralLedgerEntry.query.filter(
        GeneralLedgerEntry.created_at >= datetime.combine(start, time.min) - LOCAL_UTC_OFFSET,
        GeneralLedgerEntry.created_at < datetime.combine(end + timedelta(days=1), time.min) - LOCAL_UTC_OFFSET,
    ).all()


def _opening_payload(row):
    return {key: round(getattr(row, key) or 0.0, 2)
            for key in ('cash', 'inventory', 'debtors', 'creditors', 'equipment')}


@reports_bp.route('/api/reports/opening-balances', methods=['POST'])
@roles_required('admin', 'accountant')
def save_cash_walk_opening():
    data = request.get_json(silent=True) or {}
    try:
        as_of = date.fromisoformat(str(data.get('as_of_date') or ''))
        fields = ('cash', 'inventory', 'debtors', 'creditors', 'equipment')
        if any(data.get(name) in (None, '') for name in fields):
            raise ValueError('Enter all five opening balances, including zero where applicable.')
        amounts = {name: round(float(data.get(name)), 2) for name in fields}
        if any(not math.isfinite(value) or value < 0 for value in amounts.values()):
            raise ValueError('Opening balances must be valid non-negative amounts.')
        opening = CashWalkOpening.query.filter_by(as_of_date=as_of).first()
        if not opening:
            opening = CashWalkOpening(as_of_date=as_of, created_by=g.user.username)
            db.session.add(opening)
        for name, value in amounts.items():
            setattr(opening, name, value)
        db.session.commit()
        return jsonify(status='success', as_of_date=as_of.isoformat(), balances=amounts)
    except (TypeError, ValueError) as exc:
        db.session.rollback()
        return jsonify(status='error', message=str(exc)), 400


@reports_bp.route('/api/reports/opening-balances')
@roles_required('admin', 'accountant')
def get_cash_walk_opening():
    try:
        as_of = _date_arg('as_of_date', _today_local())
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    row = CashWalkOpening.query.filter_by(as_of_date=as_of).first()
    if row:
        return jsonify(as_of_date=as_of.isoformat(), exists=True,
                       balances=_opening_payload(row), needs_cash_count=False)
    cash_rows = (db.session.query(GeneralLedgerEntry.account_code,
                                  func.sum(GeneralLedgerEntry.debit - GeneralLedgerEntry.credit))
                 .filter(GeneralLedgerEntry.account_code.in_(CASH_CODES))
                 .group_by(GeneralLedgerEntry.account_code).all())
    cash_suggestion = sum(value or 0.0 for _, value in cash_rows)
    inventory_suggestion = sum((row.quantity_kg or 0.0) * (row.unit_cost_per_kg or 0.0)
                               for row in LocationStock.query.all())
    debtors_suggestion = sum(c.current_balance or 0.0 for c in Customer.query.all())
    creditors_row = db.session.query(func.sum(GeneralLedgerEntry.credit - GeneralLedgerEntry.debit)).filter(
        GeneralLedgerEntry.account_code == '2000').scalar()
    equipment_row = db.session.query(func.sum(GeneralLedgerEntry.debit - GeneralLedgerEntry.credit)).filter(
        GeneralLedgerEntry.account_code == ASSET_CODES['equipment']).scalar()
    today = _today_local()
    suggestions = None
    if as_of == today:
        suggestions = {'cash': round(cash_suggestion, 2),
                       'inventory': round(inventory_suggestion, 2),
                       'debtors': round(debtors_suggestion, 2),
                       'creditors': round(creditors_row or 0.0, 2),
                       'equipment': round(equipment_row or 0.0, 2)}
    return jsonify(as_of_date=as_of.isoformat(), exists=False, needs_cash_count=True,
                   balances=suggestions)


@reports_bp.route('/api/reports/owner-withdrawals', methods=['POST'])
@roles_required('admin', 'accountant')
def record_owner_withdrawal():
    data = request.get_json(silent=True) or {}
    try:
        amount = round(float(data.get('amount')), 2)
        method = str(data.get('payment_method') or '').upper()
        reason = str(data.get('reason') or '').strip()
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError('Withdrawal must be a positive amount.')
        if method not in ('CASH', 'MPESA', 'BANK'):
            raise ValueError('Choose Cash, M-Pesa, or Bank.')
        if len(reason) < 4 or len(reason) > 200 or any(ch in reason for ch in '<>'):
            raise ValueError('Enter a reason between 4 and 200 characters.')
        location = None
        till = None
        if method == 'CASH':
            location = resolve_location(data.get('location_id'))
            till = TillSession.query.filter_by(location_id=location.id, status='OPEN').order_by(TillSession.id.desc()).first()
            if not till:
                raise ValueError('There must be an open till at this outlet for a cash withdrawal.')
            if amount > (till.expected_cash or 0.0) + 0.001:
                raise ValueError('Withdrawal exceeds the open till cash.')
            till.expected_cash = round((till.expected_cash or 0.0) - amount, 2)
            db.session.add(TillCashMovement(reference='OWN-' + uuid.uuid4().hex[:10].upper(),
                                             till_session_id=till.id, movement_type='PAID_OUT',
                                             amount=amount, reason='Owner withdrawal: ' + reason,
                                             created_by=g.user.username))
        ref = 'OWN-' + uuid.uuid4().hex[:10].upper()
        withdrawal = OwnerWithdrawal(reference=ref, amount=amount, payment_method=method,
                                     location_id=location.id if location else None,
                                     till_session_id=till.id if till else None,
                                     reason=reason, created_by=g.user.username)
        db.session.add(withdrawal)
        db.session.flush()
        cash_account = {'CASH': '1000', 'MPESA': '1010', 'BANK': '1020'}[method]
        if not post_gl_entry(ref, '3000', amount, 0.0, 'OWNER_WITHDRAWAL', withdrawal.id):
            raise RuntimeError('Could not post owner withdrawal.')
        if not post_gl_entry(ref, cash_account, 0.0, amount, 'OWNER_WITHDRAWAL', withdrawal.id):
            raise RuntimeError('Could not post owner withdrawal payment.')
        db.session.commit()
        return jsonify(status='success', reference=ref), 201
    except Exception as exc:
        db.session.rollback()
        return jsonify(status='error', message=str(exc)), 400


@reports_bp.route('/api/reports/equipment-purchases', methods=['POST'])
@roles_required('admin', 'accountant')
def record_equipment_purchase():
    data = request.get_json(silent=True) or {}
    try:
        amount = round(float(data.get('amount')), 2)
        method = str(data.get('payment_method') or '').upper()
        description = str(data.get('description') or '').strip()
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError('Equipment cost must be a positive amount.')
        if method not in ('CASH', 'MPESA', 'BANK', 'ON_ACCOUNT'):
            raise ValueError('Choose Cash, M-Pesa, Bank, or On account.')
        if len(description) < 3 or len(description) > 200 or any(ch in description for ch in '<>'):
            raise ValueError('Describe the equipment (3–200 characters).')
        location = None
        till = None
        if method == 'CASH':
            location = resolve_location(data.get('location_id'))
            till = TillSession.query.filter_by(location_id=location.id, status='OPEN').order_by(TillSession.id.desc()).first()
            if not till or amount > (till.expected_cash or 0.0) + 0.001:
                raise ValueError('Cash equipment purchases need an open outlet till with enough expected cash.')
            till.expected_cash = round((till.expected_cash or 0.0) - amount, 2)
            db.session.add(TillCashMovement(reference='EQP-' + uuid.uuid4().hex[:10].upper(),
                                             till_session_id=till.id, movement_type='PAID_OUT',
                                             amount=amount, reason='Equipment purchase: ' + description[:170],
                                             created_by=g.user.username))
        ref = 'EQP-' + uuid.uuid4().hex[:10].upper()
        purchase = EquipmentPurchase(reference=ref, amount=amount, payment_method=method,
                                     location_id=location.id if location else None,
                                     till_session_id=till.id if till else None,
                                     description=description, created_by=g.user.username)
        db.session.add(purchase)
        db.session.flush()
        credit_account = {'CASH': '1000', 'MPESA': '1010', 'BANK': '1020', 'ON_ACCOUNT': '2000'}[method]
        if not post_gl_entry(ref, '1500', amount, 0.0, 'EQUIPMENT_PURCHASE', purchase.id):
            raise RuntimeError('Could not post the equipment asset.')
        if not post_gl_entry(ref, credit_account, 0.0, amount, 'EQUIPMENT_PURCHASE', purchase.id):
            raise RuntimeError('Could not post the equipment payment.')
        db.session.commit()
        return jsonify(status='success', reference=ref), 201
    except Exception as exc:
        db.session.rollback()
        return jsonify(status='error', message=str(exc)), 400


@reports_bp.route('/api/reports/cash-walk')
@roles_required('admin', 'accountant')
def cash_walk():
    today = _today_local()
    try:
        end = _date_arg('end', today)
        start = _date_arg('start', end - timedelta(days=6))
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    if end < start or (end - start).days > 31:
        return jsonify(status='error', message='Choose a period of 1 to 32 days.'), 400
    opening = CashWalkOpening.query.filter_by(as_of_date=start).first()
    if not opening:
        return jsonify(status='error', needs_opening=True,
                       message=f'Save opening balances dated {start.isoformat()} before running this week.'), 400

    entries = _period_entries(start, end)
    by_code = {}
    for entry in entries:
        pair = by_code.setdefault(str(entry.account_code), [0.0, 0.0])
        pair[0] += entry.debit or 0.0
        pair[1] += entry.credit or 0.0
    categories = {str(code): category for code, category in
                  Account.query.with_entities(Account.account_code, Account.category).all()}
    profit = 0.0
    for code, (debit, credit) in by_code.items():
        category = str(categories.get(code) or '').upper()
        if category in ('INCOME', 'REVENUE'):
            profit += credit - debit
        elif category == 'CONTRA_INCOME':
            profit -= debit - credit
        elif category == 'EXPENSE':
            profit -= debit - credit
    delta_inventory = by_code.get(ASSET_CODES['inventory'], [0.0, 0.0])[0] - by_code.get(ASSET_CODES['inventory'], [0.0, 0.0])[1]
    delta_debtors = by_code.get(ASSET_CODES['debtors'], [0.0, 0.0])[0] - by_code.get(ASSET_CODES['debtors'], [0.0, 0.0])[1]
    delta_creditors = by_code.get('2000', [0.0, 0.0])[1] - by_code.get('2000', [0.0, 0.0])[0]
    equipment_bought = max(0.0, by_code.get(ASSET_CODES['equipment'], [0.0, 0.0])[0] - by_code.get(ASSET_CODES['equipment'], [0.0, 0.0])[1])
    owner_withdrawals = sum(max(0.0, (entry.debit or 0.0) - (entry.credit or 0.0))
                            for entry in entries if entry.source_type == 'OWNER_WITHDRAWAL' and entry.account_code == '3000')
    formula_close = (opening.cash + profit - delta_inventory - delta_debtors + delta_creditors
                     - owner_withdrawals - equipment_bought)
    cash_delta = sum(by_code.get(code, [0.0, 0.0])[0] - by_code.get(code, [0.0, 0.0])[1] for code in CASH_CODES)
    ledger_close = opening.cash + cash_delta
    balances = {
        'cash': round(ledger_close, 2),
        'inventory': round(opening.inventory + delta_inventory, 2),
        'debtors': round(opening.debtors + delta_debtors, 2),
        'creditors': round(opening.creditors + delta_creditors, 2),
        'equipment': round(opening.equipment + by_code.get(ASSET_CODES['equipment'], [0.0, 0.0])[0]
                           - by_code.get(ASSET_CODES['equipment'], [0.0, 0.0])[1], 2),
    }
    return jsonify(start=start.isoformat(), end=end.isoformat(),
                   opening=_opening_payload(opening),
                   lines={'profit': round(profit, 2), 'stock_increase': round(delta_inventory, 2),
                          'debtors_increase': round(delta_debtors, 2),
                          'creditors_increase': round(delta_creditors, 2),
                          'owner_withdrawals': round(owner_withdrawals, 2),
                          'equipment_bought': round(equipment_bought, 2)},
                   calculated_closing_cash=round(formula_close, 2),
                   ledger_closing_cash=round(ledger_close, 2),
                   reconciliation_difference=round(ledger_close - formula_close, 2),
                   closing_balances=balances)


def _leak_metrics(start, end):
    period_start = datetime.combine(start, time.min) - LOCAL_UTC_OFFSET
    period_end = datetime.combine(end + timedelta(days=1), time.min) - LOCAL_UTC_OFFSET
    gl = GeneralLedgerEntry.query.filter(GeneralLedgerEntry.created_at >= period_start,
                                         GeneralLedgerEntry.created_at < period_end).all()
    loss_sources = {}
    for entry in gl:
        if entry.account_code == '5100':
            loss_sources[entry.source_type or 'OTHER'] = loss_sources.get(entry.source_type or 'OTHER', 0.0) + (entry.debit or 0.0) - (entry.credit or 0.0)
    prod_loss = sum(r.loss_cost or 0.0 for r in ProductionRun.query.filter(
        ProductionRun.created_at >= period_start, ProductionRun.created_at < period_end).all())
    adjustments = StockMovement.query.filter(StockMovement.movement_type == 'STOCK_ADJUSTMENT',
        StockMovement.qty_kg < 0, StockMovement.reason.isnot(None)).all()
    adjustment_loss = 0.0
    for move in adjustments:
        item = db.session.get(FeedIngredient, move.ingredient_id)
        stock = LocationStock.query.filter_by(location_id=move.location_id, ingredient_id=move.ingredient_id).first()
        if item and move.created_at and period_start <= move.created_at < period_end:
            adjustment_loss += abs(move.qty_kg or 0.0) * ((stock.unit_cost_per_kg if stock else item.cost_per_kg) or 0.0)
    transfer_loss = loss_sources.get('TRANSFER_SHORTFALL', 0.0)
    tills = TillSession.query.filter(TillSession.closed_at >= period_start,
                                      TillSession.closed_at < period_end,
                                      TillSession.status == 'CLOSED').all()
    cash_abs = sum(abs(t.cash_variance or 0.0) for t in tills)
    cash_net = sum(t.cash_variance or 0.0 for t in tills)
    expenses = OperatingExpense.query.filter(OperatingExpense.created_at >= period_start,
                                              OperatingExpense.created_at < period_end).all()
    transport = sum(e.amount or 0.0 for e in expenses if e.category == 'TRANSPORT')
    transport_kg = sum(t.quantity_kg or 0.0 for t in InventoryTransfer.query.filter(
        InventoryTransfer.created_at >= period_start, InventoryTransfer.created_at < period_end).all())
    receipts = GoodsReceiptNote.query.filter(GoodsReceiptNote.received_date >= period_start,
                                              GoodsReceiptNote.received_date < period_end).all()
    receipt_ids = [r.id for r in receipts]
    received_kg = sum(r.qty_accepted or 0.0 for r in GoodsReceiptLine.query.filter(
        GoodsReceiptLine.grn_id.in_(receipt_ids)).all()) if receipt_ids else 0.0
    transport_kg += received_kg

    orders = OrderHeader.query.filter(OrderHeader.created_at >= period_start,
                                      OrderHeader.created_at < period_end,
                                      OrderHeader.status == 'COMPLETED').all()
    order_ids = [o.id for o in orders]
    total_sales = sum(o.total_amount or 0.0 for o in orders)
    total_discounts = sum(o.discount_amount or 0.0 for o in orders)
    discount_cashier, discount_product = {}, {}
    if order_ids:
        till_map = {t.id: t.cashier_name for t in TillSession.query.filter(
            TillSession.id.in_([o.till_session_id for o in orders if o.till_session_id])).all()}
        lines_by_order = {}
        for line in OrderLine.query.filter(OrderLine.order_id.in_(order_ids)).all():
            lines_by_order.setdefault(line.order_id, []).append(line)
        for order in orders:
            rows = lines_by_order.get(order.id, [])
            subtotal = sum(line.subtotal or 0.0 for line in rows)
            if subtotal <= 0 or not order.discount_amount:
                continue
            cashier = till_map.get(order.till_session_id, 'Unassigned')
            cashier_row = discount_cashier.setdefault(cashier, {'discount': 0.0, 'sales': 0.0})
            cashier_row['discount'] += order.discount_amount
            cashier_row['sales'] += subtotal
            for line in rows:
                item = db.session.get(FeedIngredient, line.ingredient_id)
                if not item:
                    continue
                row = discount_product.setdefault(item.name, {'discount': 0.0, 'sales': 0.0})
                share = (line.subtotal or 0.0) / subtotal
                row['discount'] += order.discount_amount * share
                row['sales'] += line.subtotal or 0.0
    top_discounts = [{'name': name, 'discount': round(row['discount'], 2),
                      'sales': round(row['sales'], 2),
                      'rate_pct': round(100 * row['discount'] / row['sales'], 1) if row['sales'] else 0}
                     for name, row in sorted(discount_cashier.items(), key=lambda x: -x[1]['discount'])]
    product_discounts = [{'name': name, 'discount': round(row['discount'], 2),
                          'sales': round(row['sales'], 2),
                          'rate_pct': round(100 * row['discount'] / row['sales'], 1) if row['sales'] else 0}
                         for name, row in sorted(discount_product.items(), key=lambda x: -x[1]['discount'])]

    since_30 = end - timedelta(days=29)
    recent_movements = StockMovement.query.filter(
        StockMovement.created_at >= datetime.combine(since_30, time.min) - LOCAL_UTC_OFFSET,
        StockMovement.created_at < datetime.combine(end + timedelta(days=1), time.min) - LOCAL_UTC_OFFSET).all()
    last_move = {}
    for movement in recent_movements:
        key = (movement.location_id, movement.ingredient_id)
        last_move[key] = max(last_move.get(key, movement.created_at), movement.created_at)
    stock_rows = LocationStock.query.filter(LocationStock.quantity_kg > 0).all()
    dead_rows = []
    for stock in stock_rows:
        last = last_move.get((stock.location_id, stock.ingredient_id))
        if last is None or (end - last.date()).days >= 30:
            item = db.session.get(FeedIngredient, stock.ingredient_id)
            if item:
                dead_rows.append({'item': item.name, 'kg': stock.quantity_kg,
                                  'value': round(stock.quantity_kg * stock.unit_cost_per_kg, 2)})
    dead_value = sum(row['value'] for row in dead_rows)
    dead_rows.sort(key=lambda row: -row['value'])

    sold_30 = {}
    since_dt = datetime.combine(since_30, time.min) - LOCAL_UTC_OFFSET
    last_30_orders = OrderHeader.query.filter(OrderHeader.created_at >= since_dt,
                                               OrderHeader.status == 'COMPLETED').all()
    last_order_ids = [o.id for o in last_30_orders]
    if last_order_ids:
        for line in OrderLine.query.filter(OrderLine.order_id.in_(last_order_ids)).all():
            unit_type = (line.unit_type or 'KG').upper()
            try:
                kilograms_per_unit = float(unit_type.split('KG')[0]) if 'KG BAG' in unit_type else 1.0
            except ValueError:
                kilograms_per_unit = 1.0
            sold_30[line.ingredient_id] = sold_30.get(line.ingredient_id, 0.0) + (line.qty_entered or 0.0) * kilograms_per_unit
    finished_stock = []
    for stock in stock_rows:
        item = db.session.get(FeedIngredient, stock.ingredient_id)
        if item and 'finished' in (item.category or '').lower():
            sold = sold_30.get(item.id, 0.0)
            days = round(stock.quantity_kg / (sold / 30.0), 1) if sold > 0 else None
            finished_stock.append({'item': item.name, 'stock_kg': round(stock.quantity_kg, 2),
                                   'stock_value': round(stock.quantity_kg * stock.unit_cost_per_kg, 2),
                                   'sold_30d_kg': round(sold, 2), 'days_on_hand': days})
    finished_stock.sort(key=lambda row: -(row['stock_value'] if row['days_on_hand'] is None else row['stock_value'] * min(row['days_on_hand'], 365) / 30))

    transfer = InventoryTransfer.query.filter(InventoryTransfer.created_at >= period_start,
                                               InventoryTransfer.created_at < period_end).all()
    sent_kg = sum(t.quantity_kg or 0.0 for t in transfer)
    shortfall_kg = sum(t.shortfall_quantity_kg or 0.0 for t in transfer if t.status == 'RECEIVED_SHORT')
    shortfall_value = sum((t.shortfall_quantity_kg or 0.0) * (t.unit_cost_per_kg or 0.0)
                          for t in transfer if t.status == 'RECEIVED_SHORT')
    refunds = SalesRefund.query.filter(SalesRefund.created_at >= period_start,
                                        SalesRefund.created_at < period_end).all()
    refund_total = sum(r.amount or 0.0 for r in refunds)
    customer_debt = sum(c.current_balance or 0.0 for c in Customer.query.all())
    ar_delta = sum((e.debit or 0.0) - (e.credit or 0.0) for e in gl if e.account_code == '1300')

    products = []
    for item in FeedIngredient.query.all():
        costs = [s.unit_cost_per_kg or 0.0 for s in LocationStock.query.filter_by(ingredient_id=item.id).all() if s.quantity_kg > 0]
        cost = max(costs or [item.cost_per_kg or 0.0])
        price = item.retail_price_per_kg or 0.0
        margin = round((price - cost) / price * 100, 1) if price > 0 else None
        exposure = max(0.0, cost - price) * (sold_30.get(item.id, 0.0))
        products.append({'item': item.name, 'price': round(price, 2), 'cost': round(cost, 2),
                         'margin_pct': margin, 'below_cost_exposure': round(exposure, 2)})
    products.sort(key=lambda row: -row['below_cost_exposure'])

    rejected = 0.0
    purchase_premium = 0.0
    purchase_benchmarks = []
    if receipt_ids:
        current_lines = GoodsReceiptLine.query.filter(GoodsReceiptLine.grn_id.in_(receipt_ids)).all()
        rejected = sum((line.qty_rejected or 0.0) * (line.unit_cost or 0.0) for line in current_lines)
        current_receipt_dates = {r.id: r.received_date for r in receipts}
        for line in current_lines:
            received_at = current_receipt_dates.get(line.grn_id)
            if not received_at or (line.qty_accepted or 0.0) <= 0:
                continue
            history = (db.session.query(func.sum(GoodsReceiptLine.unit_cost * GoodsReceiptLine.qty_accepted),
                                        func.sum(GoodsReceiptLine.qty_accepted))
                       .join(GoodsReceiptNote, GoodsReceiptNote.id == GoodsReceiptLine.grn_id)
                       .filter(GoodsReceiptLine.ingredient_id == line.ingredient_id,
                               GoodsReceiptNote.received_date >= received_at - timedelta(days=90),
                               GoodsReceiptNote.received_date < received_at).first())
            historical_qty = history[1] or 0.0
            if historical_qty > 0:
                average = history[0] / historical_qty
                premium = max(0.0, (line.unit_cost or 0.0) - average) * (line.qty_accepted or 0.0)
                purchase_premium += premium
                item = db.session.get(FeedIngredient, line.ingredient_id)
                purchase_benchmarks.append({'item': item.name if item else 'Unknown item',
                                            'unit_cost': round(line.unit_cost or 0.0, 2),
                                            '90d_average': round(average, 2),
                                            'premium': round(premium, 2)})
    purchase_shortfall = 0.0
    received_pos = PurchaseOrderHeader.query.filter(
        PurchaseOrderHeader.status == 'FULLY_RECEIVED',
        PurchaseOrderHeader.order_date >= period_start,
        PurchaseOrderHeader.order_date < period_end).all()
    for po in received_pos:
        for line in PurchaseOrderLine.query.filter_by(po_header_id=po.id).all():
            missing_qty = max(0.0, (line.qty_ordered or 0.0) - (line.qty_received or 0.0) - (line.qty_rejected or 0.0))
            purchase_shortfall += missing_qty * (line.unit_cost or 0.0)
    production_defects = prod_loss + adjustment_loss + rejected
    return {
        'defects': {'amount': round(production_defects, 2), 'production_loss': round(prod_loss, 2),
                    'stock_adjustments': round(adjustment_loss, 2), 'rejected_receipts': round(rejected, 2)},
        'over_processing': {'amount': None, 'available': False, 'note': 'No formulation spec or bag-weight check records yet.'},
        'overproduction': {'amount': round(sum(row['stock_value'] for row in finished_stock if row['days_on_hand'] is None or row['days_on_hand'] > 30), 2), 'items': finished_stock},
        'inventory': {'amount': round(dead_value, 2), 'items': dead_rows[:20], 'definition': 'On-hand stock with no recorded stock movement in the previous 30 days.'},
        'transport': {'amount': round(transport, 2), 'kg': round(transport_kg, 2),
                      'per_kg': round(transport / transport_kg, 4) if transport_kg else None},
        'waiting': {'amount': None, 'available': False, 'note': 'No downtime log yet.'},
        'shrinkage': {'amount': round(adjustment_loss, 2), 'kg': round(sum(abs(m.qty_kg or 0) for m in adjustments if m.created_at and period_start <= m.created_at < period_end), 2)},
        'transfer_shortfall': {'amount': round(shortfall_value, 2), 'kg': round(shortfall_kg, 2), 'sent_kg': round(sent_kg, 2)},
        'cash_leakage': {'amount': round(cash_abs, 2), 'net_variance': round(cash_net, 2),
                         'tills': [{'cashier': t.cashier_name, 'variance': round(t.cash_variance or 0.0, 2)} for t in tills]},
        'discounts': {'amount': round(total_discounts, 2), 'rate_pct': round(total_discounts / total_sales * 100, 2) if total_sales else 0,
                      'by_cashier': top_discounts, 'by_product': product_discounts},
        'credit': {'amount': round(customer_debt, 2), 'period_change': round(ar_delta, 2), 'trend_value': round(ar_delta, 2), 'aging_available': False,
                   'note': 'Customer due dates are not recorded yet.'},
        'price': {'amount': round(sum(row['below_cost_exposure'] for row in products), 2), 'products': products[:20],
                  'definition': 'Estimated 30-day sales exposure at current prices below current outlet cost.'},
        'purchases': {'amount': round(rejected + purchase_premium + purchase_shortfall, 2),
                      'rejected_receipts': round(rejected, 2), 'price_premium': round(purchase_premium, 2),
                      'short_delivery_value': round(purchase_shortfall, 2), 'benchmarks': purchase_benchmarks[:20],
                      'note': 'Price premium compares receipts with prior 90-day weighted average when available.'},
        'refunds': {'amount': round(refund_total, 2)},
    }


@reports_bp.route('/api/reports/leaks')
@roles_required('admin', 'accountant')
def leak_report():
    today = _today_local()
    try:
        end = _date_arg('end', today)
        start = _date_arg('start', end - timedelta(days=6))
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    if end < start or (end - start).days > 31:
        return jsonify(status='error', message='Choose a period of 1 to 32 days.'), 400
    current = _leak_metrics(start, end)
    span = end - start
    previous = _leak_metrics(start - span - timedelta(days=1), start - timedelta(days=1))
    order = ('defects', 'over_processing', 'overproduction', 'inventory', 'transport', 'waiting',
             'shrinkage', 'transfer_shortfall', 'cash_leakage', 'discounts', 'credit', 'price', 'purchases', 'refunds')
    labels = {'defects': 'Defects', 'over_processing': 'Over-processing / give-away',
              'overproduction': 'Overproduction', 'inventory': 'Slow / dead inventory',
              'transport': 'Transport', 'waiting': 'Waiting / downtime', 'shrinkage': 'Shrinkage',
              'transfer_shortfall': 'Transfer shortfall', 'cash_leakage': 'Cash leakage',
              'discounts': 'Discount leakage', 'credit': 'Credit exposure', 'price': 'Price leakage',
              'purchases': 'Purchase leakage', 'refunds': 'Refunds'}
    rows = []
    for key in order:
        row = dict(current[key])
        before = previous[key].get('amount')
        amount = row.get('amount')
        trend_current = row.get('trend_value', amount)
        trend_previous = previous[key].get('trend_value', before)
        point_in_time = key in ('overproduction', 'inventory', 'credit', 'price')
        if key == 'defects':
            row['note'] = (f"Production {_money_text(row.get('production_loss'))}; "
                           f"stock adjustments {_money_text(row.get('stock_adjustments'))}; "
                           f"rejected receipts {_money_text(row.get('rejected_receipts'))}.")
        elif key == 'transport':
            kg = row.get('kg') or 0.0
            per_kg = row.get('per_kg')
            row['note'] = f"{kg:,.1f} kg sourced or transferred; KSh/kg {per_kg:,.4f}." if per_kg is not None else 'No purchase or transfer kg recorded for this period.'
        elif key == 'transfer_shortfall':
            row['note'] = f"Short {row.get('kg', 0):,.2f} kg from {row.get('sent_kg', 0):,.2f} kg dispatched."
        elif key == 'cash_leakage':
            row['note'] = f"Net over/short: {_money_text(row.get('net_variance'))}."
        elif key == 'credit':
            row['note'] = f"Receivable change in period: {_money_text(row.get('period_change'))}. Due dates are not recorded yet."
        elif key == 'shrinkage':
            row['note'] = f"Negative adjustment quantity: {row.get('kg', 0):,.2f} kg."
        elif key == 'purchases':
            row['note'] = (f"Rejected {_money_text(row.get('rejected_receipts'))}; "
                           f"90-day price premium {_money_text(row.get('price_premium'))}; "
                           f"short deliveries {_money_text(row.get('short_delivery_value'))}.")
        row.update(key=key, label=labels[key], previous_amount=before,
                   trend_delta=round(trend_current - trend_previous, 2)
                   if not point_in_time and trend_current is not None and trend_previous is not None else None,
                   trend_note='Current balance measure; historical balance snapshots are needed for a week-over-week trend.'
                   if point_in_time else None)
        if point_in_time:
            row['previous_amount'] = None
        rows.append(row)
    rows.sort(key=lambda row: (row['amount'] is None, -(row['amount'] or 0.0)))
    return jsonify(start=start.isoformat(), end=end.isoformat(), leaks=rows)
