import uuid
from flask import Blueprint, request, jsonify
from services.db import db
from services.models import FeedIngredient, Customer, OrderHeader, OrderLine, PaymentSplit, StockMovement
from services.ledger_service import post_gl_entry

# This creates a mini-app just for POS routes
pos_bp = Blueprint('pos', __name__)

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
        'available_stock_kg': i.stock_quantity_kg - i.reserved_quantity_kg,
        'bag_size_kg': i.bag_size_kg, 'retail_price_kg': round(i.retail_price_per_kg or i.cost_per_kg, 2)
    } for i in items_query.all()])

@pos_bp.route('/api/inventory', methods=['GET'])
def get_inventory():
    return jsonify([{'id': i.id, 'name': i.name, 'category': i.category, 'stock_quantity_kg': i.stock_quantity_kg, 'cost_per_kg': i.cost_per_kg} for i in FeedIngredient.query.all()])

@pos_bp.route('/api/customers', methods=['GET', 'POST'])
def manage_customers():
    if request.method == 'POST':
        data = request.get_json()
        cust = Customer(name=data['name'], phone=data.get('phone'), location=data.get('location', ''), customer_type=data.get('customer_type', 'RETAIL'), credit_limit=float(data.get('credit_limit', 0.0)))
        db.session.add(cust)
        db.session.commit()
        return jsonify({'status': 'success', 'customer_id': cust.id})
    # Notice we added 'location' to the output here:
    return jsonify([{'id': c.id, 'name': c.name, 'phone': c.phone, 'location': c.location or 'Unknown', 'type': c.customer_type, 'balance': c.current_balance, 'credit_limit': c.credit_limit} for c in Customer.query.all()])
@pos_bp.route('/api/customers/<int:customer_id>/repay', methods=['POST'])
def repay_customer_debt(customer_id):
    try:
        data = request.get_json() or {}
        amount = float(data.get('amount', 0.0))
        if amount <= 0: return jsonify({'status': 'error', 'message': 'Invalid amount.'}), 400

        cust = db.session.get(Customer, customer_id)
        cust.current_balance = max(0.0, cust.current_balance - amount)
        
        ref = f"PAY-{uuid.uuid4().hex[:6].upper()}"
        post_gl_entry(ref, "1000", amount, 0.0, "DEBT_REPAYMENT", str(customer_id))
        post_gl_entry(ref, "1300", 0.0, amount, "DEBT_REPAYMENT", str(customer_id))
        db.session.commit()
        return jsonify({'status': 'success', 'new_balance': cust.current_balance})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 400

@pos_bp.route('/api/pos/checkout', methods=['POST'])
def api_pos_checkout():
    data = request.get_json() or {}
    try:
        sale_id = f"SL-{uuid.uuid4().hex[:8].upper()}"
        customer_id = data.get('customer_id')
        cart = data.get('cart', [])
        payments = data.get('payments', [])
        discount_amount = max(0.0, float(data.get('discount_amount', 0.0)))
        
        total_sales_value = 0.0
        items_processed = []

        if not cart: raise ValueError("Cart is empty.")

        for item in cart:
            ing = db.session.get(FeedIngredient, item['ingredient_id'])
            qty = float(item['qty'])
            bag_size = float(item.get('bag_size_kg', 1.0))
            total_kg = qty * bag_size
            
            if total_kg > (ing.stock_quantity_kg - ing.reserved_quantity_kg):
                raise ValueError(f"Insufficient stock for {ing.name}.")

            price_per_unit = ing.retail_price_per_kg * bag_size
            subtotal = qty * price_per_unit
            
            if bag_size == 70.0: subtotal -= (subtotal * 0.05)
            elif bag_size == 50.0: subtotal -= (subtotal * 0.03)

            total_sales_value += subtotal
            ing.stock_quantity_kg -= total_kg

            line = OrderLine(ingredient_id=ing.id, qty_entered=qty, unit_type=item['unit_type'], total_kg=total_kg, subtotal=subtotal)
            items_processed.append(line)
            db.session.add(StockMovement(ingredient_id=ing.id, movement_type='SALE_DEDUCT', qty_kg=-total_kg, reference_id=sale_id))

        total_due = max(0.0, total_sales_value - discount_amount)
        
        total_paid = 0.0
        total_credit_used = 0.0
        payment_splits = []

        for p in payments:
            amt = float(p.get('amount', 0.0))
            if amt < 0: raise ValueError("Negative payments prohibited.")
            if amt == 0: continue
                
            method = p.get('payment_method', 'CASH').upper()
            total_paid += amt
            payment_splits.append(PaymentSplit(payment_method=method, amount=amt, reference=p.get('reference', '')))
            if method == 'CREDIT': total_credit_used += amt

        if total_credit_used > 0:
            if not customer_id: raise ValueError("Cannot process CREDIT sale without a registered customer.")
            cust = db.session.get(Customer, customer_id)
            if (cust.current_balance + total_credit_used) > cust.credit_limit:
                raise ValueError("Credit limit exceeded.")
            cust.current_balance += total_credit_used

        change_due = max(0.0, total_paid - total_due)

        order = OrderHeader(sale_id=sale_id, customer_id=customer_id, total_amount=total_due, paid_amount=total_paid, credit_amount=total_credit_used, change_due=change_due)
        db.session.add(order)
        db.session.flush()

        for line in items_processed:
            line.order_id = order.id
            db.session.add(line)
        for split in payment_splits:
            split.order_id = order.id
            db.session.add(split)

        if total_due > 0:
            post_gl_entry(sale_id, "1000", total_paid - total_credit_used, 0.0, "POS", sale_id)
            if total_credit_used > 0: post_gl_entry(sale_id, "1300", total_credit_used, 0.0, "POS", sale_id)
            post_gl_entry(sale_id, "4000", 0.0, total_due, "POS", sale_id)

        db.session.commit()
        return jsonify({
            "status": "success", "data": {
                "sale_id": sale_id, "total_amount": total_due, "paid_amount": total_paid, "credit_amount": total_credit_used, "change_due": change_due,
                "items": [{"name": db.session.get(FeedIngredient, l.ingredient_id).name, "qty_entered": l.qty_entered, "unit": l.unit_type, "subtotal": l.subtotal} for l in items_processed]
            }
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)}), 400

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