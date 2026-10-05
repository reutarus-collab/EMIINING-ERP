from services.models import ItemPrice
import math
from flask import g
import uuid
from flask import Blueprint, request, jsonify
from services.db import db
from services.models import FeedIngredient, Customer, OrderHeader, OrderLine, PaymentSplit, StockMovement, Location, TillSession, InventoryTransfer
from services.pos_service import process_full_pos_checkout
from services.inventory import resolve_location, location_stock, stock_quantity, reserved_quantity, active_till
from routes.auth import roles_required

pos_bp = Blueprint('pos_bp', __name__)

@pos_bp.route('/api/products/search', methods=['GET'])
def search_products():
    query = request.args.get('q', '').lower()
    category = request.args.get('category', '')
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    items_query = FeedIngredient.query
    if query:
        items_query = items_query.filter(FeedIngredient.name.ilike(f"%{query}%"))
    if category:
        items_query = items_query.filter_by(category=category)
    items = items_query.all()
    packs_by_item = {}
    for pr in ItemPrice.query.order_by(ItemPrice.pack_kg).all():
        if (pr.price or 0) > 0:
            packs_by_item.setdefault(pr.ingredient_id, []).append({'pack_kg': pr.pack_kg, 'price': round(pr.price, 2)})
    return jsonify([{
        'id': i.id, 'name': i.name, 'category': i.category,
        'available_stock_kg': max(0.0, stock_quantity(location.id, i.id) - reserved_quantity(location.id, i.id)),
        'bag_size_kg': i.bag_size_kg,
        'retail_price_kg': round(i.retail_price_per_kg or 0, 2),
        'priced': (i.retail_price_per_kg or 0) > 0 or bool(packs_by_item.get(i.id)),
        'packs': packs_by_item.get(i.id, [])
    } for i in items])
@pos_bp.route('/api/inventory', methods=['GET'])
def get_inventory():
    show_cost = g.user.role in ('admin', 'accountant', 'warehouse')
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    out = []
    for i in FeedIngredient.query.all():
        row = {'id': i.id, 'name': i.name, 'category': i.category,
               'stock_quantity_kg': stock_quantity(location.id, i.id),
               'location_id': location.id, 'location_name': location.name}
        if show_cost:
            row['cost_per_kg'] = i.cost_per_kg
        out.append(row)
    return jsonify(out)

@pos_bp.route('/api/customers', methods=['GET', 'POST'])
def manage_customers():
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        name = str(data.get('name', '')).strip()
        phone = str(data.get('phone') or '').strip()[:20]
        loc = str(data.get('location', '') or '').strip()[:100]
        ctype = str(data.get('customer_type', 'RETAIL') or 'RETAIL').strip()[:20]
        if not name or len(name) > 100 or any(ch in (name + phone + loc + ctype) for ch in '<>'):
            return jsonify({'status': 'error', 'message': 'Invalid customer details.'}), 400
        limit = 0.0
        if g.user.role in ('admin', 'accountant'):
            try:
                limit = float(data.get('credit_limit', 0.0))
            except (TypeError, ValueError):
                limit = -1.0
            if not math.isfinite(limit) or limit < 0:
                return jsonify({'status': 'error', 'message': 'Invalid credit limit.'}), 400
        cust = Customer(name=name, phone=phone, location=loc, customer_type=ctype, credit_limit=limit)
        db.session.add(cust)
        db.session.commit()
        return jsonify({'status': 'success', 'customer_id': cust.id})
    return jsonify([{'id': c.id, 'name': c.name, 'phone': c.phone, 'location': c.location or 'Unknown', 'type': c.customer_type, 'balance': c.current_balance, 'credit_limit': c.credit_limit} for c in Customer.query.all()])

@pos_bp.route('/api/customers/<int:customer_id>/repay', methods=['POST'])
def repay_customer_debt(customer_id):
    try:
        data = request.get_json(silent=True) or {}
        amount = round(float(data.get('amount', 0.0)), 2)
        if not math.isfinite(amount) or amount <= 0:
            return jsonify({'status': 'error', 'message': 'Invalid amount.'}), 400
        cust = db.session.get(Customer, customer_id)
        if not cust:
            return jsonify({'status': 'error', 'message': 'Customer not found.'}), 404
        owed = round(cust.current_balance or 0.0, 2)
        if owed <= 0:
            return jsonify({'status': 'error', 'message': 'This customer owes nothing.'}), 400
        if amount > owed + 0.01:
            return jsonify({'status': 'error', 'message': 'Repayment of KSh %.2f is more than the KSh %.2f owed.' % (amount, owed)}), 400
        amount = min(amount, owed)
        cust.current_balance = round(owed - amount, 2)
        ref = f"PAY-{uuid.uuid4().hex[:6].upper()}"
        from services.ledger_service import post_gl_entry
        post_gl_entry(ref, "1000", amount, 0.0, "DEBT_REPAYMENT", customer_id)
        post_gl_entry(ref, "1300", 0.0, amount, "DEBT_REPAYMENT", customer_id)
        db.session.commit()
        return jsonify({'status': 'success', 'new_balance': cust.current_balance})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 400
@pos_bp.route('/api/pos/checkout', methods=['POST'])
def checkout():
    try:
        data = request.get_json() or {}
        location = resolve_location(data.get('location_id'))
        till = active_till(location.id, g.user.username, lock=True)
        if not till:
            raise ValueError('Open a till session for this outlet before completing a sale.')
        data['location_id'] = location.id
        data['till_session_id'] = till.id
        result = process_full_pos_checkout(data)
        return jsonify({'status': 'success', 'data': result})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

@pos_bp.route('/api/till/current')
def till_current():
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    till = active_till(location.id, g.user.username)
    return jsonify(location_id=location.id, location_name=location.name,
                   session={'id': till.id, 'opening_cash': till.opening_cash,
                            'expected_cash': till.expected_cash, 'opened_at': till.opened_at.isoformat()}
                   if till else None)

@pos_bp.route('/api/till/open', methods=['POST'])
def till_open():
    data = request.get_json(silent=True) or {}
    try:
        location = resolve_location(data.get('location_id'))
        opening = float(data.get('opening_cash', 0))
        if not math.isfinite(opening) or opening < 0:
            raise ValueError('Opening cash must be zero or more.')
        if active_till(location.id, g.user.username):
            raise ValueError('You already have an open till at this outlet.')
        till = TillSession(location_id=location.id, cashier_name=g.user.username,
                           opening_cash=opening, expected_cash=opening, status='OPEN')
        db.session.add(till)
        db.session.commit()
        return jsonify(status='success', session_id=till.id, location_name=location.name)
    except Exception as exc:
        db.session.rollback()
        return jsonify(status='error', message=str(exc)), 400

@pos_bp.route('/api/till/close', methods=['POST'])
def till_close():
    data = request.get_json(silent=True) or {}
    try:
        location = resolve_location(data.get('location_id'))
        till = active_till(location.id, g.user.username, lock=True)
        if not till:
            raise ValueError('There is no open till session to close.')
        counted = float(data.get('counted_cash'))
        if not math.isfinite(counted) or counted < 0:
            raise ValueError('Counted cash must be zero or more.')
        from datetime import datetime
        variance = round(counted - (till.expected_cash or 0.0), 2)
        till.status = 'CLOSED'
        till.closed_at = datetime.utcnow()
        till.counted_cash = round(counted, 2)
        till.cash_variance = variance
        if abs(variance) >= 0.01:
            from services.ledger_service import post_gl_entry
            ref = f'TILL-{till.id}-CLOSE'
            if variance > 0:
                posted = post_gl_entry(ref, '1000', variance, 0.0, 'TILL_OVER_SHORT', till.id)
                posted = post_gl_entry(ref, '5200', 0.0, variance, 'TILL_OVER_SHORT', till.id) and posted
            else:
                posted = post_gl_entry(ref, '5100', abs(variance), 0.0, 'TILL_OVER_SHORT', till.id)
                posted = post_gl_entry(ref, '1000', 0.0, abs(variance), 'TILL_OVER_SHORT', till.id) and posted
            if not posted:
                raise RuntimeError('Could not post till cash variance to the ledger.')
        db.session.commit()
        return jsonify(status='success', expected_cash=round(till.expected_cash, 2),
                       counted_cash=round(counted, 2), variance=variance)
    except Exception as exc:
        db.session.rollback()
        return jsonify(status='error', message=str(exc)), 400

@pos_bp.route('/api/inventory/adjust', methods=['POST'])
@roles_required('admin', 'warehouse')
def adjust_inventory():
    data = request.get_json(silent=True) or {}
    try:
        location = resolve_location(data.get('location_id'))
        ingredient = db.session.get(FeedIngredient, int(data.get('ingredient_id')))
        delta = float(data.get('delta_kg'))
        reason = str(data.get('reason') or '').strip()
        if not ingredient or not math.isfinite(delta) or abs(delta) < 0.000001:
            raise ValueError('Choose an item and enter a non-zero valid adjustment.')
        if len(reason) < 4 or len(reason) > 200:
            raise ValueError('Enter an adjustment reason (4–200 characters).')
        change_stock(location.id, ingredient, delta, 'STOCK_ADJUSTMENT',
                     'ADJ-' + uuid.uuid4().hex[:10].upper(), reason)
        db.session.commit()
        return jsonify(status='success', quantity_kg=location_stock(location.id, ingredient.id).quantity_kg)
    except Exception as exc:
        db.session.rollback()
        return jsonify(status='error', message=str(exc)), 400

@pos_bp.route('/api/inventory/transfers', methods=['POST'])
@roles_required('admin')
def transfer_inventory():
    data = request.get_json(silent=True) or {}
    try:
        source_id, destination_id = int(data.get('from_location_id')), int(data.get('to_location_id'))
        ingredient = db.session.get(FeedIngredient, int(data.get('ingredient_id')))
        quantity = float(data.get('quantity_kg'))
        reason = str(data.get('reason') or '').strip()
        source, destination = db.session.get(Location, source_id), db.session.get(Location, destination_id)
        if not source or not destination or source.id == destination.id:
            raise ValueError('Choose two different valid outlets.')
        if not ingredient or not math.isfinite(quantity) or quantity <= 0:
            raise ValueError('Choose an item and enter a positive transfer quantity.')
        if len(reason) < 4 or len(reason) > 200:
            raise ValueError('Enter a transfer reason (4–200 characters).')
        ref = 'TRF-' + uuid.uuid4().hex[:10].upper()
        change_stock(source.id, ingredient, -quantity, 'TRANSFER_OUT', ref, reason)
        change_stock(destination.id, ingredient, quantity, 'TRANSFER_IN', ref, reason)
        db.session.add(InventoryTransfer(transfer_no=ref, from_location_id=source.id,
                                         to_location_id=destination.id, ingredient_id=ingredient.id,
                                         quantity_kg=quantity, reason=reason,
                                         created_by=g.user.username))
        db.session.commit()
        return jsonify(status='success', transfer_no=ref)
    except Exception as exc:
        db.session.rollback()
        return jsonify(status='error', message=str(exc)), 400

@pos_bp.route('/api/sales-history', methods=['GET'])
def get_sales_history():
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    orders = OrderHeader.query.filter_by(location_id=location.id).order_by(OrderHeader.created_at.desc()).limit(30).all()
    out = []
    for o in orders:
        cust = db.session.get(Customer, o.customer_id) if o.customer_id else None
        lines = OrderLine.query.filter_by(order_id=o.id).all()
        splits = PaymentSplit.query.filter_by(order_id=o.id).all()
        out.append({
            'sale_id': o.sale_id, 'created_at': o.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'customer_name': cust.name if cust else 'Walk-In Cash Customer',
            'total_amount': o.total_amount, 'paid_amount': o.paid_amount, 'credit_amount': o.credit_amount,
            'payments': [{'method': p.payment_method, 'amount': p.amount} for p in splits],
            'items': [{'name': (getattr(db.session.get(FeedIngredient, l.ingredient_id), 'name', None) or '(deleted item)'), 'qty_entered': l.qty_entered, 'unit': l.unit_type, 'subtotal': l.subtotal} for l in lines]
        })
    return jsonify(out)
