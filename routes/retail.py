import math
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, g
from sqlalchemy import func
from routes.auth import roles_required
from services.models import db, Customer, FeedIngredient, OrderHeader, PaymentSplit, ItemPrice, TillSession, LocationStock, OperatingExpense, SalesRefund, TillCashMovement
from services.inventory import resolve_location, stock_quantity
retail_bp = Blueprint('retail', __name__)
@retail_bp.route('/api/stock')
def stock():
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    rows = FeedIngredient.query.order_by(FeedIngredient.name).all()
    show_cost = g.user.role in ('admin', 'accountant', 'warehouse')
    result = []
    for i in rows:
        balance = LocationStock.query.filter_by(location_id=location.id, ingredient_id=i.id).first()
        qty = round(stock_quantity(location.id, i.id), 2)
        row = {
        'id': i.id,
        'name': i.name,
        'category': i.category or '',
        'stock_kg': qty,
        'location_id': location.id, 'location_name': location.name,
        'bag_size_kg': i.bag_size_kg or 0,
        'retail_price_per_kg': i.retail_price_per_kg or 0,
        'priced': (i.retail_price_per_kg or 0) > 0,
        }
        if show_cost:
            cost = (balance.unit_cost_per_kg if balance else 0.0) or 0.0
            row.update(unit_cost_per_kg=round(cost, 4), stock_value=round(qty * cost, 2))
        result.append(row)
    return jsonify(result)

def _max_outlet_unit_cost(ingredient_id, fallback=0.0):
    costs = [row[0] or 0.0 for row in db.session.query(LocationStock.unit_cost_per_kg)
             .filter(LocationStock.ingredient_id == ingredient_id,
                     LocationStock.quantity_kg > 0).all()]
    return max(costs or [fallback or 0.0])
@retail_bp.route('/api/admin/prices')
@roles_required('admin')
def prices_list():
    out = []
    for i in FeedIngredient.query.order_by(FeedIngredient.name).all():
        cost = _max_outlet_unit_cost(i.id, i.cost_per_kg or 0)
        price = i.retail_price_per_kg or 0
        margin = round((price - cost) / price * 100, 1) if price > 0 else None
        out.append({'id': i.id, 'name': i.name, 'cost_per_kg': cost,
                    'retail_price_per_kg': price, 'margin_pct': margin})
    return jsonify(out)
@retail_bp.route('/api/admin/prices', methods=['POST'])
@roles_required('admin')
def prices_save():
    data = request.get_json(silent=True) or {}
    try:
        item = db.session.get(FeedIngredient, int(data.get('id')))
        price = round(float(data.get('retail_price_per_kg')), 2)
    except (TypeError, ValueError):
        return jsonify(status='error', message='Enter a valid price.'), 400
    if not item:
        return jsonify(status='error', message='Item not found.'), 404
    if not math.isfinite(price) or price <= 0:
        return jsonify(status='error', message='Price must be above 0.'), 400
    cost = _max_outlet_unit_cost(item.id, item.cost_per_kg or 0)
    if price <= cost:
        return jsonify(status='error',
                       message=f'Price {price:.2f} is not above cost {cost:.2f}. Fix the cost or raise the price.'), 400
    item.retail_price_per_kg = price
    db.session.commit()
    return jsonify(status='success')
@retail_bp.route('/api/reports/daily')
def daily_report():
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    now_eat = datetime.utcnow() + timedelta(hours=3)
    start_eat = now_eat.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_eat - timedelta(hours=3)
    end = start + timedelta(days=1)
    orders = OrderHeader.query.filter(
        OrderHeader.created_at >= start,
        OrderHeader.created_at < end,
        OrderHeader.location_id == location.id,
        OrderHeader.status == 'COMPLETED').all()
    ids = [o.id for o in orders]
    by_method = {}
    if ids:
        rows = (db.session.query(PaymentSplit.payment_method, func.sum(PaymentSplit.amount))
                .filter(PaymentSplit.order_id.in_(ids))
                .group_by(PaymentSplit.payment_method).all())
        by_method = {m: round(a or 0, 2) for m, a in rows if m != 'CREDIT'}
        cash_rows = (db.session.query(PaymentSplit.order_id, func.sum(PaymentSplit.amount))
                     .filter(PaymentSplit.order_id.in_(ids), PaymentSplit.payment_method == 'CASH')
                     .group_by(PaymentSplit.order_id).all())
        cash_by_order = {order_id: amount or 0.0 for order_id, amount in cash_rows}
        cash_change = round(sum(min(o.change_due or 0, cash_by_order.get(o.id, 0.0)) for o in orders), 2)
        if cash_change:
            by_method['CASH'] = round(max(0.0, by_method.get('CASH', 0.0) - cash_change), 2)
    tills = TillSession.query.filter(
        TillSession.location_id == location.id,
        TillSession.closed_at >= start,
        TillSession.closed_at < end,
        TillSession.status == 'CLOSED').order_by(TillSession.closed_at.desc()).all()
    expenses = OperatingExpense.query.filter(OperatingExpense.location_id == location.id,
                                             OperatingExpense.created_at >= start,
                                             OperatingExpense.created_at < end).all()
    refunds = SalesRefund.query.filter(SalesRefund.location_id == location.id,
                                       SalesRefund.created_at >= start,
                                       SalesRefund.created_at < end).all()
    till_movements = (TillCashMovement.query.join(TillSession)
                      .filter(TillSession.location_id == location.id,
                              TillCashMovement.created_at >= start,
                              TillCashMovement.created_at < end).all())
    expense_categories = {}
    for expense in expenses:
        expense_categories[expense.category] = round(expense_categories.get(expense.category, 0.0) + (expense.amount or 0.0), 2)
    return jsonify(
        date=start_eat.strftime('%Y-%m-%d'),
        location_id=location.id, location_name=location.name,
        sales_count=len(orders),
        total_sales=round(sum(o.total_amount or 0 for o in orders), 2),
        discounts=round(sum(o.discount_amount or 0 for o in orders), 2),
        by_method=by_method,
        till_closures=[{'cashier': t.cashier_name, 'opening_cash': round(t.opening_cash or 0, 2),
                        'expected_cash': round(t.expected_cash or 0, 2),
                        'counted_cash': round(t.counted_cash or 0, 2),
                        'variance': round(t.cash_variance or 0, 2),
                        'closed_at': t.closed_at.strftime('%H:%M') if t.closed_at else ''} for t in tills],
        credit_sales=round(sum(o.credit_amount or 0 for o in orders), 2),
        expenses_total=round(sum(e.amount or 0 for e in expenses), 2),
        expenses_by_category=expense_categories,
        refunds_total=round(sum(r.amount or 0 for r in refunds), 2),
        cash_paid_in=round(sum(m.amount or 0 for m in till_movements if m.movement_type == 'PAID_IN'), 2),
        cash_paid_out=round(sum(m.amount or 0 for m in till_movements if m.movement_type == 'PAID_OUT'), 2))


@retail_bp.route('/api/admin/customers/<int:customer_id>/credit-limit', methods=['POST'])
@roles_required('admin')
def set_credit_limit(customer_id):
    data = request.get_json(silent=True) or {}
    try:
        limit = round(float(data.get('credit_limit')), 2)
    except (TypeError, ValueError):
        return jsonify(status='error', message='Enter a valid limit.'), 400
    if not math.isfinite(limit) or limit < 0:
        return jsonify(status='error', message='Invalid limit.'), 400
    cust = db.session.get(Customer, customer_id)
    if not cust:
        return jsonify(status='error', message='Customer not found.'), 404
    cust.credit_limit = limit
    db.session.commit()
    return jsonify(status='success')


PACK_SIZES = (50, 70)
@retail_bp.route('/api/admin/packs')
@roles_required('admin')
def packs_list():
    by_item = {}
    for r in ItemPrice.query.all():
        by_item.setdefault(r.ingredient_id, {})[int(round(r.pack_kg))] = r.price
    out = []
    for i in FeedIngredient.query.order_by(FeedIngredient.name).all():
        prices = by_item.get(i.id, {})
        out.append({'id': i.id, 'name': i.name,
                    'cost_per_kg': _max_outlet_unit_cost(i.id, i.cost_per_kg or 0),
                    'kg_price': i.retail_price_per_kg or 0,
                    'p50': prices.get(50), 'p70': prices.get(70)})
    return jsonify(out)
@retail_bp.route('/api/admin/packs', methods=['POST'])
@roles_required('admin')
def packs_save():
    data = request.get_json(silent=True) or {}
    try:
        item = db.session.get(FeedIngredient, int(data.get('id')))
    except (TypeError, ValueError):
        return jsonify(status='error', message='Bad item.'), 400
    if not item:
        return jsonify(status='error', message='Item not found.'), 404
    cost = _max_outlet_unit_cost(item.id, item.cost_per_kg or 0)
    new_prices = {}
    for size in PACK_SIZES:
        raw = data.get('p%d' % size)
        if raw is None or raw == '':
            new_prices[size] = None
            continue
        try:
            price = round(float(raw), 2)
        except (TypeError, ValueError):
            return jsonify(status='error', message='Invalid %d kg price.' % size), 400
        if not math.isfinite(price) or price <= 0:
            return jsonify(status='error', message='%d kg price must be above 0.' % size), 400
        if price <= cost * size:
            return jsonify(status='error',
                           message='%d kg price %.2f is not above its cost %.2f (%.2f per kg).' % (size, price, cost * size, cost)), 400
        new_prices[size] = price
    for size, price in new_prices.items():
        row = None
        for r in ItemPrice.query.filter_by(ingredient_id=item.id).all():
            if abs(r.pack_kg - size) < 0.0005:
                row = r
        if price is None:
            if row:
                db.session.delete(row)
        elif row:
            row.price = price
        else:
            db.session.add(ItemPrice(ingredient_id=item.id, pack_kg=float(size), price=price))
    db.session.commit()
    return jsonify(status='success')
