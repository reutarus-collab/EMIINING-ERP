import os, uuid
from flask import Flask, render_template, request, jsonify
from services.db import db, init_db
from services.models import FeedIngredient, Customer, OrderHeader, IdempotencyKey, StockMovement, PaymentSplit, OrderLine, Supplier, Location, PurchaseOrderHeader, PurchaseOrderLine, GeneralLedgerEntry, Account

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'emining-enterprise-2026')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///emining_erp.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

init_db(app)

def post_gl_entry(ref, account_code, debit, credit, module, source_id):
    entry = GeneralLedgerEntry(
        transaction_ref=ref, account_code=account_code, 
        debit=debit, credit=credit, source_module=module, source_id=source_id
    )
    db.session.add(entry)

@app.route('/')
def dashboard():
    return render_template('dashboard.html')

# ==========================================
# INVENTORY, SUPPLIERS & CUSTOMERS
# ==========================================
@app.route('/api/products/search', methods=['GET'])
def search_products():
    query = request.args.get('q', '').lower()
    category = request.args.get('category', '')
    items_query = FeedIngredient.query
    if query:
        items_query = items_query.filter(FeedIngredient.name.ilike(f"%{query}%"))
    if category:
        items_query = items_query.filter_by(category=category)
    items = items_query.all()
    return jsonify([{
        'id': i.id, 'name': i.name, 'category': i.category,
        'available_stock_kg': i.stock_quantity_kg - i.reserved_quantity_kg,
        'bag_size_kg': i.bag_size_kg, 'retail_price_kg': round(i.retail_price_per_kg or i.cost_per_kg, 2)
    } for i in items])

@app.route('/api/inventory', methods=['GET'])
def get_inventory():
    return jsonify([{'id': i.id, 'name': i.name, 'category': i.category, 'stock_quantity_kg': i.stock_quantity_kg, 'cost_per_kg': i.cost_per_kg} for i in FeedIngredient.query.all()])

@app.route('/api/suppliers', methods=['GET'])
def get_suppliers():
    suppliers = Supplier.query.all()
    return jsonify([{'id': s.id, 'name': s.name, 'balance_due': s.balance_due} for s in suppliers])

@app.route('/api/locations', methods=['GET'])
def get_locations():
    locations = Location.query.all()
    return jsonify([{'id': l.id, 'name': l.name, 'type': l.location_type} for l in locations])

@app.route('/api/customers', methods=['GET', 'POST'])
def manage_customers():
    if request.method == 'POST':
        data = request.get_json()
        cust = Customer(name=data['name'], phone=data.get('phone'), location=data.get('location'), customer_type=data.get('customer_type', 'RETAIL'), credit_limit=float(data.get('credit_limit', 0.0)))
        db.session.add(cust)
        db.session.commit()
        return jsonify({'status': 'success', 'customer_id': cust.id})
    return jsonify([{'id': c.id, 'name': c.name, 'phone': c.phone, 'type': c.customer_type, 'balance': c.current_balance, 'credit_limit': c.credit_limit} for c in Customer.query.all()])

@app.route('/api/customers/<int:customer_id>/repay', methods=['POST'])
def repay_customer_debt(customer_id):
    try:
        data = request.get_json() or {}
        amount = float(data.get('amount', 0.0))
        if amount <= 0:
            return jsonify({'status': 'error', 'message': 'Invalid amount.'}), 400

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

# ==========================================
# POS CHECKOUT ENGINE
# ==========================================
@app.route('/api/pos/checkout', methods=['POST'])
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

        if not cart:
            raise ValueError("Cart is empty.")

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

@app.route('/api/sales-history', methods=['GET'])
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

# ==========================================
# PO & GRPO ENGINE
# ==========================================
@app.route('/api/po/create', methods=['POST'])
def create_po():
    try:
        data = request.get_json() or {}
        po_no = f"PO-{uuid.uuid4().hex[:6].upper()}"
        total_value = 0.0

        po = PurchaseOrderHeader(po_no=po_no, supplier_id=data['supplier_id'], location_id=data['location_id'], total_amount=0.0, status='ISSUED')
        db.session.add(po)
        db.session.flush()

        for item in data.get('items', []):
            qty = float(item['qty_kg'])
            cost = float(item['unit_cost'])
            if qty <= 0 or cost < 0: raise ValueError("Quantities and costs must be positive.")
            
            subtotal = qty * cost
            total_value += subtotal
            db.session.add(PurchaseOrderLine(po_id=po.id, ingredient_id=item['ingredient_id'], ordered_qty_kg=qty, received_qty_kg=0.0, unit_cost=cost, subtotal=subtotal))
            
        po.total_amount = total_value
        supplier = db.session.get(Supplier, data['supplier_id'])
        supplier.balance_due += total_value 

        db.session.commit()
        return jsonify({'status': 'success', 'po_no': po_no})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 400

@app.route('/api/po/details', methods=['GET'])
def get_po_details():
    try:
        po_no = request.args.get('po_no')
        po = PurchaseOrderHeader.query.filter_by(po_no=po_no).first()
        if not po: return jsonify({'status': 'error', 'message': 'PO not found.'}), 404

        lines = PurchaseOrderLine.query.filter_by(po_id=po.id).all()
        line_data = [{'line_id': l.id, 'ingredient_name': db.session.get(FeedIngredient, l.ingredient_id).name, 'ordered_qty': l.ordered_qty_kg, 'received_qty': l.received_qty_kg} for l in lines]
        return jsonify({'status': 'success', 'po_id': po.id, 'po_no': po.po_no, 'lines': line_data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/po/<int:po_id>/grpo', methods=['POST'])
def receive_grpo_partial(po_id):
    try:
        data = request.get_json() or {}
        po = db.session.get(PurchaseOrderHeader, po_id)
        grpo_total_value = 0.0
        grpo_ref = f"GRPO-{uuid.uuid4().hex[:6].upper()}"
        all_lines_fully_received = True

        for recv_item in data.get('received_items', []):
            line = db.session.get(PurchaseOrderLine, recv_item['line_id'])
            incoming_qty = float(recv_item['qty_kg'])
            if incoming_qty <= 0: continue

            ing = db.session.get(FeedIngredient, line.ingredient_id)
            if (line.received_qty_kg + incoming_qty) > line.ordered_qty_kg: raise ValueError(f"Exceeds PO limit for {ing.name}")
            
            new_subtotal = incoming_qty * line.unit_cost
            old_val = (ing.stock_quantity_kg or 0.0) * (ing.cost_per_kg or 0.0)
            new_total_stock = (ing.stock_quantity_kg or 0.0) + incoming_qty
            
            if new_total_stock > 0: ing.cost_per_kg = (old_val + new_subtotal) / new_total_stock
            
            ing.stock_quantity_kg = new_total_stock
            line.received_qty_kg += incoming_qty
            grpo_total_value += new_subtotal
            
            db.session.add(StockMovement(ingredient_id=ing.id, movement_type='GRPO_RECEIPT', qty_kg=incoming_qty, reference_id=grpo_ref))
            if line.received_qty_kg < line.ordered_qty_kg: all_lines_fully_received = False

        po.status = 'FULLY_RECEIVED' if all_lines_fully_received else 'PARTIAL_RECEIVED'
        if grpo_total_value > 0:
            post_gl_entry(grpo_ref, "1200", grpo_total_value, 0.0, "GRPO", grpo_ref)
            post_gl_entry(grpo_ref, "2000", 0.0, grpo_total_value, "GRPO", grpo_ref)

        db.session.commit()
        return jsonify({'status': 'success', 'grpo_no': grpo_ref, 'status': po.status})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 400    

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)