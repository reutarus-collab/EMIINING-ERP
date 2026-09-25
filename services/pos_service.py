import uuid
from datetime import datetime
from services.db import db
from services.models import FeedIngredient, Customer, OrderHeader, OrderLine, PaymentSplit, StockMovement

def process_full_pos_checkout(data):
    customer_id = data.get('customer_id')
    discount_amount = float(data.get('discount_amount', 0.0))
    cart = data.get('cart', [])
    payments = data.get('payments', [])

    if not cart:
        raise Exception("Cart is empty.")

    total_bill = 0.0
    order_lines = []
    
    for item in cart:
        ing_id = item.get('ingredient_id')
        qty = float(item.get('qty', 0.0))
        unit_type = item.get('unit_type', 'KG')
        bag_size_kg = float(item.get('bag_size_kg', 1.0))
        
        total_kg_for_item = qty * bag_size_kg
        ingredient = FeedIngredient.query.get(ing_id)
        
        if not ingredient:
            raise Exception(f"Ingredient ID {ing_id} not found.")
        if ingredient.stock_quantity_kg < total_kg_for_item:
            raise Exception(f"Insufficient stock for {ingredient.name}. Available: {ingredient.stock_quantity_kg}kg")

        ingredient.stock_quantity_kg -= total_kg_for_item
        subtotal = qty * (ingredient.retail_price_per_kg * bag_size_kg)
        total_bill += subtotal
        
        order_lines.append(OrderLine(
            ingredient_id=ingredient.id,
            unit_type=unit_type,
            qty_entered=qty,
            subtotal=subtotal
        ))
        
        db.session.add(StockMovement(
            ingredient_id=ingredient.id,
            movement_type='POS_SALE',
            qty_kg=-total_kg_for_item
        ))

    final_due = max(0.0, total_bill - discount_amount)
    
    total_paid = sum(float(p.get('amount', 0.0)) for p in payments if p.get('payment_method') != 'CREDIT')
    explicit_credit = sum(float(p.get('amount', 0.0)) for p in payments if p.get('payment_method') == 'CREDIT')
    
    credit_amount = explicit_credit
    if total_paid < final_due and explicit_credit == 0:
        credit_amount = final_due - total_paid

    change_due = max(0.0, total_paid - final_due) if credit_amount == 0 else 0.0

    if credit_amount > 0:
        if not customer_id:
            raise Exception("Cannot sell on credit to a Walk-In customer. Please select a registered customer.")
        customer = Customer.query.get(customer_id)
        if (customer.current_balance + credit_amount) > customer.credit_limit:
            raise Exception(f"Credit limit exceeded! Customer can only take KSh {max(0, customer.credit_limit - customer.current_balance):.2f} more.")
        customer.current_balance += credit_amount

    sale_id = f"SALE-{uuid.uuid4().hex[:6].upper()}"
    order = OrderHeader(
        sale_id=sale_id,
        customer_id=customer_id,
        total_amount=total_bill,
        discount_amount=discount_amount,
        paid_amount=total_paid,
        change_due=change_due,
        credit_amount=credit_amount,
        status='COMPLETED'
    )
    db.session.add(order)
    db.session.flush() 

    for line in order_lines:
        line.order_id = order.id
        db.session.add(line)
        
    for p in payments:
        amt = float(p.get('amount', 0.0))
        if amt > 0:
            db.session.add(PaymentSplit(order_id=order.id, payment_method=p.get('payment_method'), amount=amt, reference=p.get('reference', '')))

    try:
        from services.ledger_service import post_gl_entry
        post_gl_entry(sale_id, '4000', 0.0, final_due, 'POS', order.id)
        if total_paid > 0:
            actual_cash_kept = total_paid - change_due
            post_gl_entry(sale_id, '1000', actual_cash_kept, 0.0, 'POS', order.id)
        if credit_amount > 0:
            post_gl_entry(sale_id, '1300', credit_amount, 0.0, 'POS', order.id)
    except Exception as e:
        print(f"GL Posting Failed/Skipped: {e}")

    db.session.commit()

    res_items = []
    for line in order_lines:
        res_items.append({
            "name": FeedIngredient.query.get(line.ingredient_id).name,
            "qty_entered": line.qty_entered, 
            "unit": line.unit_type, 
            "subtotal": line.subtotal
        })

    return { 
        "sale_id": sale_id, "total_amount": final_due, "paid_amount": total_paid, 
        "change_due": change_due, "credit_amount": credit_amount, "items": res_items 
    }