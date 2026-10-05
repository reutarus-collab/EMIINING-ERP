import math
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request
from sqlalchemy import func
from routes.auth import roles_required
from services.models import db, Customer, FeedIngredient, OrderHeader, PaymentSplit
retail_bp = Blueprint('retail', __name__)
@retail_bp.route('/api/stock')
def stock():
    rows = FeedIngredient.query.order_by(FeedIngredient.name).all()
    return jsonify([{
        'id': i.id,
        'name': i.name,
        'category': i.category or '',
        'stock_kg': round(i.stock_quantity_kg or 0, 2),
        'bag_size_kg': i.bag_size_kg or 0,
        'retail_price_per_kg': i.retail_price_per_kg or 0,
        'priced': (i.retail_price_per_kg or 0) > 0,
    } for i in rows])
@retail_bp.route('/api/admin/prices')
@roles_required('admin')
def prices_list():
    out = []
    for i in FeedIngredient.query.order_by(FeedIngredient.name).all():
        cost = i.cost_per_kg or 0
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
    cost = item.cost_per_kg or 0
    if price <= cost:
        return jsonify(status='error',
                       message=f'Price {price:.2f} is not above cost {cost:.2f}. Fix the cost or raise the price.'), 400
    item.retail_price_per_kg = price
    db.session.commit()
    return jsonify(status='success')
@retail_bp.route('/api/reports/daily')
def daily_report():
    now_eat = datetime.utcnow() + timedelta(hours=3)
    start_eat = now_eat.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_eat - timedelta(hours=3)
    end = start + timedelta(days=1)
    orders = OrderHeader.query.filter(
        OrderHeader.created_at >= start,
        OrderHeader.created_at < end,
        OrderHeader.status == 'COMPLETED').all()
    ids = [o.id for o in orders]
    by_method = {}
    if ids:
        rows = (db.session.query(PaymentSplit.payment_method, func.sum(PaymentSplit.amount))
                .filter(PaymentSplit.order_id.in_(ids))
                .group_by(PaymentSplit.payment_method).all())
        by_method = {m: round(a or 0, 2) for m, a in rows if m != 'CREDIT'}
        change_total = round(sum(o.change_due or 0 for o in orders), 2)
        if change_total and 'CASH' in by_method:
            by_method['CASH'] = round(by_method['CASH'] - change_total, 2)
    return jsonify(
        date=start_eat.strftime('%Y-%m-%d'),
        sales_count=len(orders),
        total_sales=round(sum(o.total_amount or 0 for o in orders), 2),
        discounts=round(sum(o.discount_amount or 0 for o in orders), 2),
        by_method=by_method,
        credit_sales=round(sum(o.credit_amount or 0 for o in orders), 2))


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
