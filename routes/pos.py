import math
from flask import g
import uuid
from flask import Blueprint, request, jsonify
from services.db import db
from services.models import FeedIngredient, Customer, OrderHeader, OrderLine, PaymentSplit, StockMovement
from services.pos_service import process_full_pos_checkout

pos_bp = Blueprint('pos_bp', __name__)

@pos_bp.route('/api/products/search', methods=['GET'])
def search_products():
    query = request.args.get('q', '').lower()
    category = request.args.get('category', '')
    items_query = FeedIngredient.query
    if query:
        items_query = items_query.filter(FeedIngredient.name.ilike(f"%{query}%"))
    if category:
        items_query = items_query.filter_by(category=category)
    return jsonify([{
        'id': i.id, 'name': i.name, 'category': i.category,
        'available_stock_kg': (i.stock_quantity_kg or 0) - (i.reserved_quantity_kg or 0),
        'bag_size_kg': i.bag_size_kg,
        'retail_price_kg': round(i.retail_price_per_kg or 0, 2),
        'priced': (i.retail_price_per_kg or 0) > 0
    } for i in items_query.all()])
@pos_bp.route('/api/inventory', methods=['GET'])
def get_inventory():
    show_cost = g.user.role in ('admin', 'accountant', 'warehouse')
    out = []
    for i in FeedIngredient.query.all():
        row = {'id': i.id, 'name': i.name, 'category': i.category,
               'stock_quantity_kg': i.stock_quantity_kg}
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
        result = process_full_pos_checkout(data)
        return jsonify({'status': 'success', 'data': result})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

@pos_bp.route('/api/sales-history', methods=['GET'])
def get_sales_history():
    orders = OrderHeader.query.order_by(OrderHeader.created_at.desc()).limit(30).all()
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
            'items': [{'name': db.session.get(FeedIngredient, l.ingredient_id).name, 'qty_entered': l.qty_entered, 'unit': l.unit_type, 'subtotal': l.subtotal} for l in lines]
        })
    return jsonify(out)