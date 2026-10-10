from services.idempotency import idempotent
from services.models import ItemPrice
from datetime import datetime, timedelta, time, date
import math
from flask import g
import uuid
from flask import Blueprint, request, jsonify
from sqlalchemy.exc import IntegrityError
import hashlib
import json
from services.db import db
from services.models import LocationStock, FeedIngredient, Customer, CustomerPayment, OrderHeader, OrderLine, PaymentSplit, StockMovement, Location, TillSession, InventoryTransfer, InventoryTransferReceipt, OperatingExpense, TillCashMovement, SalesRefund, SalesRefundLine, IdempotencyKey
from services.pos_service import process_full_pos_checkout
from services.inventory import resolve_location, location_stock, stock_quantity, reserved_quantity, active_till, change_stock, receive_stock
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
    # POS shows sellable products carried by THIS outlet. Raw materials stay hidden
    # unless the cashier explicitly picks a "Raw - ..." category. Milling services
    # have no stock rows, so they always show.
    stocked_ids = {r.ingredient_id for r in LocationStock.query.filter_by(location_id=location.id).all()}
    show_raw = request.args.get('include_raw') == '1' or category.lower().startswith('raw')

    def _sellable(i):
        cat = (i.category or '').strip().lower()
        if cat == 'milling service':
            return True
        if i.id not in stocked_ids:
            return False
        return show_raw or not cat.startswith('raw')
    items = [i for i in items_query.all() if _sellable(i)]
    packs_by_item = {}
    for pr in ItemPrice.query.order_by(ItemPrice.pack_kg).all():
        if (pr.price or 0) > 0:
            packs_by_item.setdefault(pr.ingredient_id, []).append({'pack_kg': pr.pack_kg, 'price': round(pr.price, 2)})
    return jsonify([{
        'id': i.id, 'name': i.name, 'category': i.category,
        'is_service': i.category == 'Milling Service',
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
    for i in FeedIngredient.query.filter(db.or_(FeedIngredient.category.is_(None), FeedIngredient.category != 'Milling Service')).all():
        row = {'id': i.id, 'name': i.name, 'category': i.category,
               'stock_quantity_kg': stock_quantity(location.id, i.id),
               'location_id': location.id, 'location_name': location.name}
        if show_cost:
            row['cost_per_kg'] = i.cost_per_kg
        out.append(row)
    return jsonify(out)

@pos_bp.route('/api/customers', methods=['GET', 'POST'])
@idempotent('customer-add')
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
        try:
            outlet = resolve_location(data.get('location_id'))
        except ValueError as exc:
            return jsonify({'status': 'error', 'message': str(exc)}), 400
        cust = Customer(name=name, phone=phone, location=loc, customer_type=ctype,
                        credit_limit=limit, location_id=outlet.id)
        db.session.add(cust)
        db.session.commit()
        return jsonify({'status': 'success', 'customer_id': cust.id})
    is_owner = g.user.role in ('admin', 'accountant')
    if is_owner and request.args.get('all') == '1':
        # admin credit-limit screen: every customer across every outlet
        q = Customer.query
    else:
        try:
            outlet = resolve_location(request.args.get('location_id'))
        except ValueError:
            return jsonify([])
        q = Customer.query.filter(Customer.location_id == outlet.id)
        if is_owner:
            # owners also see customers not yet assigned to an outlet, so none get lost
            q = Customer.query.filter(db.or_(Customer.location_id == outlet.id, Customer.location_id.is_(None)))
    outlet_names = {l.id: l.name for l in Location.query.all()}
    return jsonify([{'id': c.id, 'name': c.name, 'phone': c.phone, 'location': c.location or 'Unknown',
                     'type': c.customer_type, 'balance': c.current_balance, 'credit_limit': c.credit_limit,
                     'outlet_id': c.location_id, 'outlet_name': outlet_names.get(c.location_id, 'Unassigned')}
                    for c in q.order_by(Customer.name).all()])

@pos_bp.route('/api/customers/<int:customer_id>/repay', methods=['POST'])
@roles_required('admin', 'accountant', 'sales')
@idempotent('customer-repay')
def repay_customer_debt(customer_id):
    try:
        data = request.get_json(silent=True) or {}
        amount = round(float(data.get('amount', 0.0)), 2)
        if not math.isfinite(amount) or amount <= 0:
            return jsonify({'status': 'error', 'message': 'Invalid amount.'}), 400
        method = str(data.get('payment_method') or '').strip().upper()
        if method not in ('CASH', 'MPESA', 'BANK'):
            return jsonify({'status': 'error', 'message': 'Choose CASH, MPESA, or BANK.'}), 400
        location = resolve_location(data.get('location_id'))
        cust = db.session.get(Customer, customer_id)
        if not cust:
            return jsonify({'status': 'error', 'message': 'Customer not found.'}), 404
        if cust.location_id is not None and cust.location_id != location.id:
            return jsonify({'status': 'error', 'message': 'This customer belongs to another outlet.'}), 400
        owed = round(cust.current_balance or 0.0, 2)
        if owed <= 0:
            return jsonify({'status': 'error', 'message': 'This customer owes nothing.'}), 400
        if amount > owed + 0.01:
            return jsonify({'status': 'error', 'message': 'Repayment of KSh %.2f is more than the KSh %.2f owed.' % (amount, owed)}), 400
        amount = min(amount, owed)
        cust.current_balance = round(owed - amount, 2)
        ref = f"PAY-{uuid.uuid4().hex[:6].upper()}"
        till = None
        if method == 'CASH':
            till = active_till(location.id, g.user.username, lock=True)
            if not till:
                raise ValueError('Open your outlet till before accepting a cash repayment.')
            till.expected_cash = round((till.expected_cash or 0.0) + amount, 2)
        payment = CustomerPayment(reference=ref, customer_id=cust.id, location_id=location.id,
                                  till_session_id=till.id if till else None, amount=amount,
                                  payment_method=method, created_by=g.user.username)
        db.session.add(payment)
        db.session.flush()
        from services.ledger_service import post_gl_entry
        payment_account = {'CASH': '1000', 'MPESA': '1010', 'BANK': '1020'}[method]
        posted = post_gl_entry(ref, payment_account, amount, 0.0, "DEBT_REPAYMENT", payment.id)
        posted = post_gl_entry(ref, "1300", 0.0, amount, "DEBT_REPAYMENT", payment.id) and posted
        if not posted:
            raise RuntimeError('Could not post the customer payment to the ledger.')
        db.session.commit()
        return jsonify({'status': 'success', 'reference': ref, 'new_balance': cust.current_balance})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 400
@pos_bp.route('/api/pos/checkout', methods=['POST'])
def checkout():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(status='error', message='Invalid checkout request.'), 400
    key = (request.headers.get('Idempotency-Key') or '').strip()
    try:
        parsed_key = uuid.UUID(key)
        key = str(parsed_key)
    except (ValueError, TypeError, AttributeError):
        return jsonify(status='error', message='A valid Idempotency-Key UUID is required.'), 400
    request_hash = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    try:
        location = resolve_location(data.get('location_id'))
        existing = IdempotencyKey.query.filter_by(key=key).first()
        if existing:
            if existing.created_by != g.user.username or existing.location_id != location.id:
                return jsonify(status='error', message='This checkout key belongs to a different cashier or outlet.'), 409
            if existing.request_hash != request_hash:
                return jsonify(status='error', message='This idempotency key was already used for a different checkout.'), 409
            return jsonify(status='success', data=existing.response_json, already_processed=True)
        till = active_till(location.id, g.user.username, lock=True)
        if not till:
            raise ValueError('Open a till session for this outlet before completing a sale.')
        data['location_id'] = location.id
        data['till_session_id'] = till.id
        result = process_full_pos_checkout(data, idempotency_key=key, request_hash=request_hash,
                                           created_by=g.user.username)
        return jsonify(status='success', data=result)
    except IntegrityError:
        db.session.rollback()
        existing = IdempotencyKey.query.filter_by(key=key).first()
        if (existing and existing.request_hash == request_hash
                and existing.created_by == g.user.username and existing.location_id == location.id):
            return jsonify(status='success', data=existing.response_json, already_processed=True)
        return jsonify(status='error', message='Checkout conflicted with another request. Retry with the same key.'), 409
    except Exception as e:
        db.session.rollback()
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
                           opening_cash=opening, expected_cash=opening, status='OPEN',
                           open_key=f'{location.id}:{g.user.username}')
        db.session.add(till)
        db.session.commit()
        return jsonify(status='success', session_id=till.id, location_name=location.name)
    except Exception as exc:
        db.session.rollback()
        if isinstance(exc, IntegrityError):
            return jsonify(status='error', message='A till was opened for you at this outlet in another request. Refresh the till status.'), 409
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
        till.open_key = None
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

@pos_bp.route('/api/till/cash-movements', methods=['GET', 'POST'])
@roles_required('admin', 'accountant', 'sales', 'warehouse', 'factory')
@idempotent('till-cash')
def till_cash_movements():
    if request.method == 'GET':
        try:
            location = resolve_location(request.args.get('location_id'))
        except ValueError as exc:
            return jsonify(status='error', message=str(exc)), 400
        rows = (TillCashMovement.query.join(TillSession)
                .filter(TillSession.location_id == location.id)
                .order_by(TillCashMovement.created_at.desc()).limit(50).all())
        return jsonify(movements=[{'reference': m.reference, 'type': m.movement_type,
                                   'amount': m.amount, 'reason': m.reason,
                                   'created_by': m.created_by,
                                   'created_at': m.created_at.strftime('%Y-%m-%d %H:%M') if m.created_at else ''}
                                  for m in rows])
    data = request.get_json(silent=True) or {}
    try:
        location = resolve_location(data.get('location_id'))
        movement_type = str(data.get('movement_type') or '').strip().upper()
        amount = round(float(data.get('amount')), 2)
        reason = str(data.get('reason') or '').strip()
        if movement_type not in ('PAID_IN', 'PAID_OUT'):
            raise ValueError('Choose cash paid-in or paid-out.')
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError('Cash movement amount must be above zero.')
        if len(reason) < 4 or len(reason) > 200 or any(ch in reason for ch in '<>'):
            raise ValueError('Enter a valid reason (4–200 characters).')
        till = active_till(location.id, g.user.username, lock=True)
        if not till:
            raise ValueError('Open your outlet till before recording a cash movement.')
        if movement_type == 'PAID_OUT' and amount > (till.expected_cash or 0.0) + 0.001:
            raise ValueError('Cash paid-out exceeds the expected till cash.')
        ref = 'TILL-' + uuid.uuid4().hex[:10].upper()
        db.session.add(TillCashMovement(reference=ref, till_session_id=till.id,
                                        movement_type=movement_type, amount=amount,
                                        reason=reason, created_by=g.user.username))
        sign = 1 if movement_type == 'PAID_IN' else -1
        till.expected_cash = round((till.expected_cash or 0.0) + sign * amount, 2)
        from services.ledger_service import post_gl_entry
        if movement_type == 'PAID_IN':
            posted = post_gl_entry(ref, '1000', amount, 0.0, 'TILL_CASH_MOVEMENT', till.id)
            posted = post_gl_entry(ref, '1100', 0.0, amount, 'TILL_CASH_MOVEMENT', till.id) and posted
        else:
            posted = post_gl_entry(ref, '1100', amount, 0.0, 'TILL_CASH_MOVEMENT', till.id)
            posted = post_gl_entry(ref, '1000', 0.0, amount, 'TILL_CASH_MOVEMENT', till.id) and posted
        if not posted:
            raise RuntimeError('Could not post the cash movement to the ledger.')
        db.session.commit()
        return jsonify(status='success', reference=ref, expected_cash=till.expected_cash), 201
    except Exception as exc:
        db.session.rollback()
        return jsonify(status='error', message=str(exc)), 400

@pos_bp.route('/api/pos/orders/<int:order_id>/refund', methods=['POST'])
@roles_required('admin', 'accountant', 'sales')
@idempotent('sale-refund')
def refund_sale(order_id):
    data = request.get_json(silent=True) or {}
    try:
        order = db.session.get(OrderHeader, order_id)
        if not order:
            return jsonify(status='error', message='Sale not found.'), 404
        location = resolve_location(order.location_id if g.user.role in ('admin', 'accountant') else None)
        if location.id != order.location_id:
            raise ValueError('This sale belongs to another outlet.')
        amount = round(float(data.get('amount')), 2)
        method = str(data.get('payment_method') or '').strip().upper()
        reason = str(data.get('reason') or '').strip()
        if method not in ('CASH', 'MPESA', 'BANK'):
            raise ValueError('Choose a valid refund method.')
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError('Refund amount must be above zero.')
        if len(reason) < 4 or len(reason) > 200 or any(ch in reason for ch in '<>'):
            raise ValueError('Enter a refund reason (4–200 characters).')
        returned_items = data.get('returned_items') or []
        if not isinstance(returned_items, list):
            raise ValueError('Returned stock must be a list of sale lines and quantities.')
        order_lines = {line.id: line for line in OrderLine.query.filter_by(order_id=order.id).all()}
        return_rows = []
        seen_line_ids = set()
        for returned in returned_items:
            if not isinstance(returned, dict):
                raise ValueError('Each returned-stock entry must include a sale line and quantity.')
            line_id = int(returned.get('order_line_id'))
            quantity = float(returned.get('quantity_kg'))
            line = order_lines.get(line_id)
            if not line or line_id in seen_line_ids or not math.isfinite(quantity) or quantity <= 0:
                raise ValueError('Choose a valid sale line and positive returned quantity.')
            seen_line_ids.add(line_id)
            unit_type = str(line.unit_type or 'KG').upper()
            bag_size = float(unit_type.split('KG BAG')[0]) if 'KG BAG' in unit_type else 1.0
            sold_kg = (line.qty_entered or 0.0) * bag_size
            previous_kg = db.session.query(db.func.sum(SalesRefundLine.quantity_kg)).join(
                SalesRefund, SalesRefund.id == SalesRefundLine.refund_id).filter(
                    SalesRefund.order_id == order.id, SalesRefundLine.order_line_id == line_id).scalar() or 0.0
            if quantity > sold_kg - previous_kg + 0.000001:
                raise ValueError('Returned quantity exceeds the unreturned quantity sold for that line.')
            ingredient = db.session.get(FeedIngredient, line.ingredient_id)
            if not ingredient:
                raise ValueError('A returned item no longer exists in inventory.')
            balance = location_stock(location.id, ingredient.id, lock=True)
            return_rows.append((line, ingredient, quantity,
                                line.unit_cost_per_kg or balance.unit_cost_per_kg or 0.0))
        original = PaymentSplit.query.filter_by(order_id=order.id, payment_method=method).all()
        original_amount = sum(p.amount or 0.0 for p in original)
        if method == 'CASH':
            original_amount = max(0.0, original_amount - (order.change_due or 0.0))
        already_method = sum(r.amount or 0.0 for r in SalesRefund.query.filter_by(order_id=order.id, payment_method=method).all())
        already_total = sum(r.amount or 0.0 for r in SalesRefund.query.filter_by(order_id=order.id).all())
        net_paid = max(0.0, (order.paid_amount or 0.0) - (order.change_due or 0.0))
        if amount > original_amount - already_method + 0.001 or amount > net_paid - already_total + 0.001:
            raise ValueError('Refund exceeds the amount this sale received by that payment method.')
        till = None
        if method == 'CASH':
            till = active_till(location.id, g.user.username, lock=True)
            if not till:
                raise ValueError('Open your outlet till before issuing a cash refund.')
            if amount > (till.expected_cash or 0.0) + 0.001:
                raise ValueError('Cash refund exceeds the expected cash available in this till.')
        ref = 'REF-' + uuid.uuid4().hex[:10].upper()
        refund = SalesRefund(reference=ref, order_id=order.id, location_id=location.id,
                             till_session_id=till.id if till else None, amount=amount,
                             payment_method=method, reason=reason, created_by=g.user.username)
        db.session.add(refund)
        db.session.flush()
        if till:
            till.expected_cash = round((till.expected_cash or 0.0) - amount, 2)
        from services.ledger_service import post_gl_entry
        payment_account = {'CASH': '1000', 'MPESA': '1010', 'BANK': '1020'}[method]
        posted = post_gl_entry(ref, '4100', amount, 0.0, 'SALES_REFUND', order.id)
        posted = post_gl_entry(ref, payment_account, 0.0, amount, 'SALES_REFUND', order.id) and posted
        return_value = 0.0
        for line, ingredient, quantity, unit_cost in return_rows:
            receive_stock(location.id, ingredient, quantity, unit_cost, 'SALES_RETURN', ref,
                          f'Returned from sale {order.sale_id or order.id}')
            db.session.add(SalesRefundLine(refund_id=refund.id, order_line_id=line.id,
                                           ingredient_id=ingredient.id, quantity_kg=quantity,
                                           unit_cost_per_kg=unit_cost))
            return_value += quantity * unit_cost
        if return_value > 0:
            posted = post_gl_entry(ref, '1200', return_value, 0.0, 'SALES_RETURN', order.id) and posted
            posted = post_gl_entry(ref, '5000', 0.0, return_value, 'SALES_RETURN', order.id) and posted
        if not posted:
            raise RuntimeError('Could not post the refund to the ledger.')
        db.session.commit()
        return jsonify(status='success', reference=ref,
                       restocked_kg=round(sum(row[2] for row in return_rows), 3),
                       restocked_cost=round(return_value, 2)), 201
    except Exception as exc:
        db.session.rollback()
        return jsonify(status='error', message=str(exc)), 400

@pos_bp.route('/api/inventory/adjust', methods=['POST'])
@roles_required('admin', 'warehouse')
@idempotent('stock-adjust')
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
        balance = location_stock(location.id, ingredient.id, lock=True)
        adjustment_value = round(abs(delta) * (balance.unit_cost_per_kg or 0.0), 2)
        ref = 'ADJ-' + uuid.uuid4().hex[:10].upper()
        change_stock(location.id, ingredient, delta, 'STOCK_ADJUSTMENT', ref, reason)
        from services.ledger_service import post_gl_entry
        if delta < 0:
            posted = post_gl_entry(ref, '5100', adjustment_value, 0.0, 'STOCK_ADJUSTMENT', ingredient.id)
            posted = post_gl_entry(ref, '1200', 0.0, adjustment_value, 'STOCK_ADJUSTMENT', ingredient.id) and posted
        else:
            posted = post_gl_entry(ref, '1200', adjustment_value, 0.0, 'STOCK_ADJUSTMENT', ingredient.id)
            posted = post_gl_entry(ref, '5400', 0.0, adjustment_value, 'STOCK_ADJUSTMENT', ingredient.id) and posted
        if not posted:
            raise RuntimeError('Could not post the stock adjustment to the ledger.')
        db.session.commit()
        return jsonify(status='success', quantity_kg=location_stock(location.id, ingredient.id).quantity_kg)
    except Exception as exc:
        db.session.rollback()
        return jsonify(status='error', message=str(exc)), 400

@pos_bp.route('/api/inventory/transfers', methods=['POST'])
@roles_required('admin', 'warehouse')
@idempotent('transfer-send')
def transfer_inventory():
    data = request.get_json(silent=True) or {}
    try:
        source = resolve_location(data.get('from_location_id'))
        destination_id = int(data.get('to_location_id'))
        ingredient = db.session.get(FeedIngredient, int(data.get('ingredient_id')))
        quantity = float(data.get('quantity_kg'))
        reason = str(data.get('reason') or '').strip()
        destination = db.session.get(Location, destination_id)
        if not source or not destination or source.id == destination.id:
            raise ValueError('Choose two different valid outlets.')
        if not ingredient or not math.isfinite(quantity) or quantity <= 0:
            raise ValueError('Choose an item and enter a positive transfer quantity.')
        if len(reason) < 4 or len(reason) > 200:
            raise ValueError('Enter a transfer reason (4–200 characters).')
        ref = 'TRF-' + uuid.uuid4().hex[:10].upper()
        source_stock = location_stock(source.id, ingredient.id, lock=True)
        dispatch_cost = source_stock.unit_cost_per_kg or 0.0
        change_stock(source.id, ingredient, -quantity, 'TRANSFER_OUT', ref, reason)
        db.session.add(InventoryTransfer(transfer_no=ref, from_location_id=source.id,
                                         to_location_id=destination.id, ingredient_id=ingredient.id,
                                         quantity_kg=quantity, received_quantity_kg=0.0,
                                         unit_cost_per_kg=dispatch_cost,
                                         status='IN_TRANSIT', reason=reason,
                                         created_by=g.user.username))
        db.session.commit()
        return jsonify(status='success', transfer_no=ref, status_text='IN_TRANSIT')
    except Exception as exc:
        db.session.rollback()
        return jsonify(status='error', message=str(exc)), 400

@pos_bp.route('/api/inventory/transfers/incoming', methods=['GET'])
@roles_required('admin', 'sales', 'warehouse')
def incoming_inventory_transfers():
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    query = InventoryTransfer.query.filter(
        InventoryTransfer.to_location_id == location.id,
        InventoryTransfer.status.in_(('IN_TRANSIT', 'PARTIALLY_RECEIVED')))
    rows = []
    for transfer in query.order_by(InventoryTransfer.created_at.asc()).all():
        ingredient = db.session.get(FeedIngredient, transfer.ingredient_id)
        source = db.session.get(Location, transfer.from_location_id)
        remaining = round((transfer.quantity_kg or 0.0) - (transfer.received_quantity_kg or 0.0), 6)
        rows.append({'id': transfer.id, 'transfer_no': transfer.transfer_no,
                     'item': ingredient.name if ingredient else 'Deleted item',
                     'from_location': source.name if source else 'Unknown location',
                     'quantity_kg': transfer.quantity_kg,
                     'received_quantity_kg': transfer.received_quantity_kg or 0.0,
                     'remaining_quantity_kg': remaining, 'reason': transfer.reason,
                     'created_by': transfer.created_by or '',
                     'created_at': transfer.created_at.strftime('%Y-%m-%d %H:%M') if transfer.created_at else ''})
    return jsonify(location_id=location.id, location_name=location.name, transfers=rows)

@pos_bp.route('/api/inventory/transfers/<int:transfer_id>/receive', methods=['POST'])
@roles_required('admin', 'sales', 'warehouse')
@idempotent('transfer-recv')
def receive_inventory_transfer(transfer_id):
    data = request.get_json(silent=True) or {}
    try:
        transfer = InventoryTransfer.query.filter_by(id=transfer_id).with_for_update().first()
        if not transfer:
            return jsonify(status='error', message='Transfer not found.'), 404
        location = resolve_location(transfer.to_location_id if g.user.role == 'admin' else None)
        if location.id != transfer.to_location_id:
            raise ValueError('This transfer is addressed to a different outlet.')
        if transfer.status not in ('IN_TRANSIT', 'PARTIALLY_RECEIVED'):
            raise ValueError('This transfer has already been received or closed.')
        quantity = float(data.get('quantity_kg'))
        note = str(data.get('note') or '').strip()
        remaining = round((transfer.quantity_kg or 0.0) - (transfer.received_quantity_kg or 0.0), 6)
        if not math.isfinite(quantity) or quantity <= 0 or quantity > remaining + 0.000001:
            raise ValueError(f'Enter a receipt quantity above 0 and no more than {remaining:.2f} kg.')
        if len(note) > 200 or any(ch in note for ch in '<>'):
            raise ValueError('Receipt note must be 200 characters or fewer.')
        ingredient = db.session.get(FeedIngredient, transfer.ingredient_id)
        if not ingredient:
            raise ValueError('The transferred inventory item no longer exists.')
        receipt_ref = f'{transfer.transfer_no}-REC-{uuid.uuid4().hex[:6].upper()}'
        receive_stock(location.id, ingredient, quantity, transfer.unit_cost_per_kg or 0.0,
                      'TRANSFER_IN', receipt_ref,
                      note or f'Received against {transfer.transfer_no}')
        db.session.add(InventoryTransferReceipt(transfer_id=transfer.id, quantity_kg=quantity,
                                                received_by=g.user.username, note=note or None))
        transfer.received_quantity_kg = round((transfer.received_quantity_kg or 0.0) + quantity, 6)
        transfer.received_by = g.user.username
        if transfer.received_quantity_kg >= transfer.quantity_kg - 0.000001:
            transfer.received_quantity_kg = transfer.quantity_kg
            transfer.status = 'RECEIVED'
            from datetime import datetime
            transfer.completed_at = datetime.utcnow()
        else:
            transfer.status = 'PARTIALLY_RECEIVED'
        db.session.commit()
        return jsonify(status='success', transfer_no=transfer.transfer_no,
                       received_quantity_kg=transfer.received_quantity_kg,
                       remaining_quantity_kg=max(0.0, round(transfer.quantity_kg - transfer.received_quantity_kg, 6)),
                       transfer_status=transfer.status)
    except Exception as exc:
        db.session.rollback()
        return jsonify(status='error', message=str(exc)), 400

@pos_bp.route('/api/inventory/transfers/<int:transfer_id>/close-short', methods=['POST'])
@roles_required('admin', 'sales', 'warehouse')
@idempotent('transfer-short')
def close_transfer_shortfall(transfer_id):
    data = request.get_json(silent=True) or {}
    try:
        transfer = InventoryTransfer.query.filter_by(id=transfer_id).with_for_update().first()
        if not transfer:
            return jsonify(status='error', message='Transfer not found.'), 404
        location = resolve_location(transfer.to_location_id if g.user.role == 'admin' else None)
        if location.id != transfer.to_location_id:
            raise ValueError('Only the receiving outlet can close this transfer short.')
        if transfer.status not in ('IN_TRANSIT', 'PARTIALLY_RECEIVED'):
            raise ValueError('This transfer is already closed.')
        reason = str(data.get('reason') or '').strip()
        if len(reason) < 4 or len(reason) > 200 or any(ch in reason for ch in '<>'):
            raise ValueError('Enter a shortfall reason (4–200 characters).')
        shortfall = round((transfer.quantity_kg or 0.0) - (transfer.received_quantity_kg or 0.0), 6)
        if shortfall <= 0:
            raise ValueError('There is no remaining quantity to close short.')
        loss_value = round(shortfall * (transfer.unit_cost_per_kg or 0.0), 2)
        ref = f'{transfer.transfer_no}-SHORT'
        if loss_value > 0:
            from services.ledger_service import post_gl_entry
            posted = post_gl_entry(ref, '5100', loss_value, 0.0, 'TRANSFER_SHORTFALL', transfer.id)
            posted = post_gl_entry(ref, '1200', 0.0, loss_value, 'TRANSFER_SHORTFALL', transfer.id) and posted
            if not posted:
                raise RuntimeError('Could not post the transfer shortfall loss to the ledger.')
        from datetime import datetime
        transfer.shortfall_quantity_kg = shortfall
        transfer.shortfall_reason = reason
        transfer.closed_by = g.user.username
        transfer.status = 'RECEIVED_SHORT'
        transfer.completed_at = datetime.utcnow()
        db.session.commit()
        return jsonify(status='success', transfer_no=transfer.transfer_no,
                       shortfall_quantity_kg=shortfall, loss_value=loss_value,
                       transfer_status=transfer.status)
    except Exception as exc:
        db.session.rollback()
        return jsonify(status='error', message=str(exc)), 400

EXPENSE_CATEGORIES = {
    'TRANSPORT': ('Transport', '5300'), 'UTILITIES': ('KPLC / Utilities', '5310'),
    'MEALS': ('Staff lunch / meals', '5320'), 'RENT': ('Rent', '5330'),
    'REPAIRS': ('Repairs and maintenance', '5340'),
    'SUPPLIES': ('Operating supplies', '5350'), 'OTHER': ('Other', '5390'),
}

@pos_bp.route('/api/expenses', methods=['GET', 'POST'])
@roles_required('admin', 'accountant', 'sales', 'warehouse')
@idempotent('expense')
def operating_expenses():
    if request.method == 'GET':
        try:
            location = resolve_location(request.args.get('location_id'))
        except ValueError as exc:
            return jsonify(status='error', message=str(exc)), 400
        records = OperatingExpense.query.filter_by(location_id=location.id).order_by(
            OperatingExpense.created_at.desc()).limit(100).all()
        return jsonify(location_id=location.id, location_name=location.name,
                       expenses=[{'reference': e.reference, 'category': EXPENSE_CATEGORIES.get(e.category, (e.category, ''))[0],
                                  'amount': e.amount, 'payment_method': e.payment_method,
                                  'description': e.description, 'created_by': e.created_by,
                                  'created_at': e.created_at.strftime('%Y-%m-%d %H:%M') if e.created_at else ''}
                                 for e in records])
    data = request.get_json(silent=True) or {}
    try:
        location = resolve_location(data.get('location_id'))
        category = str(data.get('category') or '').strip().upper()
        method = str(data.get('payment_method') or '').strip().upper()
        description = str(data.get('description') or '').strip()
        amount = round(float(data.get('amount')), 2)
        if category not in EXPENSE_CATEGORIES:
            raise ValueError('Choose a valid expense category.')
        if method not in ('CASH', 'MPESA', 'BANK'):
            raise ValueError('Choose Cash, M-Pesa, or Bank as the payment method.')
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError('Expense amount must be above zero.')
        if not description or len(description) > 200 or any(ch in description for ch in '<>'):
            raise ValueError('Enter a description of 1–200 characters.')
        till = None
        if method == 'CASH':
            till = active_till(location.id, g.user.username, lock=True)
            if not till:
                raise ValueError('Open your outlet till before recording a cash expense.')
            if amount > (till.expected_cash or 0.0) + 0.001:
                raise ValueError('Cash expense exceeds the expected cash available in this till.')
        ref = 'EXP-' + uuid.uuid4().hex[:10].upper()
        expense = OperatingExpense(reference=ref, location_id=location.id,
                                   till_session_id=till.id if till else None,
                                   category=category, amount=amount, payment_method=method,
                                   description=description, created_by=g.user.username)
        db.session.add(expense)
        if till:
            till.expected_cash = round((till.expected_cash or 0.0) - amount, 2)
        from services.ledger_service import post_gl_entry
        expense_account = EXPENSE_CATEGORIES[category][1]
        payment_account = {'CASH': '1000', 'MPESA': '1010', 'BANK': '1020'}[method]
        if not post_gl_entry(ref, expense_account, amount, 0.0, 'OPERATING_EXPENSE', None):
            raise RuntimeError('Could not post the expense debit to the ledger.')
        if not post_gl_entry(ref, payment_account, 0.0, amount, 'OPERATING_EXPENSE', None):
            raise RuntimeError('Could not post the payment credit to the ledger.')
        db.session.commit()
        return jsonify(status='success', reference=ref), 201
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
        refunds = SalesRefund.query.filter_by(order_id=o.id).order_by(SalesRefund.created_at).all()
        refunded = sum(r.amount or 0.0 for r in refunds)
        net_paid = max(0.0, (o.paid_amount or 0.0) - (o.change_due or 0.0))
        out.append({
            'order_id': o.id,
            'sale_id': o.sale_id, 'created_at': o.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'customer_name': cust.name if cust else 'Walk-In Cash Customer',
            'total_amount': o.total_amount, 'paid_amount': o.paid_amount, 'credit_amount': o.credit_amount,
            'payments': [{'method': p.payment_method, 'amount': p.amount} for p in splits],
            'refunded_amount': round(refunded, 2),
            'refundable_amount': round(max(0.0, net_paid - refunded), 2),
            'refunds': [{'reference': r.reference, 'amount': r.amount,
                         'payment_method': r.payment_method, 'reason': r.reason,
                         'created_by': r.created_by,
                         'created_at': r.created_at.strftime('%Y-%m-%d %H:%M') if r.created_at else ''}
                        for r in refunds],
            'items': [{'order_line_id': l.id, 'ingredient_id': l.ingredient_id,
                       'name': (getattr(db.session.get(FeedIngredient, l.ingredient_id), 'name', None) or '(deleted item)'),
                       'qty_entered': l.qty_entered, 'unit': l.unit_type,
                       'quantity_kg': (l.qty_entered or 0.0) * (float(str(l.unit_type).upper().split('KG BAG')[0]) if l.unit_type and 'KG BAG' in str(l.unit_type).upper() else 1.0),
                       'returned_kg': round(db.session.query(db.func.sum(SalesRefundLine.quantity_kg)).join(
                           SalesRefund, SalesRefund.id == SalesRefundLine.refund_id).filter(
                               SalesRefund.order_id == o.id, SalesRefundLine.order_line_id == l.id).scalar() or 0.0, 3),
                       'subtotal': l.subtotal} for l in lines]
        })
    return jsonify(out)


@pos_bp.route('/api/customers/<int:customer_id>', methods=['PUT', 'PATCH'])
def edit_customer(customer_id):
    cust = db.session.get(Customer, customer_id)
    if not cust:
        return jsonify(status='error', message='Customer not found.'), 404
    is_owner = g.user.role in ('admin', 'accountant')
    if not is_owner and cust.location_id != getattr(g.user, 'location_id', None):
        return jsonify(status='error', message='This customer belongs to another outlet.'), 403
    data = request.get_json(silent=True) or {}
    name = str(data.get('name', cust.name) or '').strip()
    phone = str(data.get('phone', cust.phone) or '').strip()[:20]
    loc = str(data.get('location', cust.location) or '').strip()[:100]
    ctype = str(data.get('customer_type', cust.customer_type) or 'RETAIL').strip()[:20]
    if not name or len(name) > 100 or any(ch in (name + phone + loc + ctype) for ch in '<>'):
        return jsonify(status='error', message='Invalid customer details.'), 400
    cust.name, cust.phone, cust.location, cust.customer_type = name, phone, loc, ctype
    if is_owner and data.get('outlet_id') not in (None, ''):
        outlet = db.session.get(Location, int(data['outlet_id']))
        if not outlet:
            return jsonify(status='error', message='Unknown outlet.'), 400
        cust.location_id = outlet.id
    db.session.commit()
    return jsonify(status='success', customer_id=cust.id)


LOCAL_OFFSET = timedelta(hours=3)  # Africa/Nairobi; same as reports.LOCAL_UTC_OFFSET


def _utc_window(first_day, last_day):
    start = datetime.combine(first_day, time.min) - LOCAL_OFFSET
    end = datetime.combine(last_day + timedelta(days=1), time.min) - LOCAL_OFFSET
    return start, end


@pos_bp.route('/api/sales-history/summary', methods=['GET'])
def sales_summary():
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    today = (datetime.utcnow() + LOCAL_OFFSET).date()
    windows = {
        'today': (today, today),
        'yesterday': (today - timedelta(days=1), today - timedelta(days=1)),
        'this_week': (today - timedelta(days=today.weekday()), today),   # Monday to today
        'this_month': (today.replace(day=1), today),
        'this_year': (today.replace(month=1, day=1), today),
    }
    out = {}
    for label, (first, last) in windows.items():
        start, end = _utc_window(first, last)
        count, gross, credit = db.session.query(
            db.func.count(OrderHeader.id), db.func.coalesce(db.func.sum(OrderHeader.total_amount), 0.0),
            db.func.coalesce(db.func.sum(OrderHeader.credit_amount), 0.0)).filter(
            OrderHeader.location_id == location.id, OrderHeader.created_at >= start,
            OrderHeader.created_at < end, OrderHeader.status == 'COMPLETED').one()
        refunds = db.session.query(db.func.coalesce(db.func.sum(SalesRefund.amount), 0.0)).filter(
            SalesRefund.location_id == location.id, SalesRefund.created_at >= start,
            SalesRefund.created_at < end).scalar() or 0.0
        out[label] = {'from': first.isoformat(), 'to': last.isoformat(), 'orders': count,
                      'gross_sales': round(gross, 2), 'refunds': round(refunds, 2),
                      'net_sales': round(gross - refunds, 2), 'credit_given': round(credit, 2)}
    return jsonify(location=location.name, periods=out)
