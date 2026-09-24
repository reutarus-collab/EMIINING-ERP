import uuid
from datetime import datetime
from .db import db
from .models import OrderHeader, OrderLine, PaymentSplit, Customer, FeedIngredient, StockMovement, GeneralLedgerEntry
def post_gl_entry(ref, account_code, debit, credit, module, source_id):
    entry = GeneralLedgerEntry(
        transaction_ref=ref, account_code=account_code, 
        debit=debit, credit=credit, source_module=module, source_id=source_id
    )
    db.session.add(entry)

def process_full_pos_checkout(data):
    sale_id = f"SL-{uuid.uuid4().hex[:8].upper()}"
    customer_id = data.get('customer_id')
    cart = data.get('cart', [])
    payments = data.get('payments', [])
    discount_amount = max(0.0, float(data.get('discount_amount', 0.0)))
    
    total_sales_value = 0.0
    items_processed = []

    if not cart:
        raise ValueError("Cart is empty.")

    # 1. Process Cart & Inventory
    for item in cart:
        ing = db.session.get(FeedIngredient, item['ingredient_id'])
        if not ing:
            raise ValueError(f"Ingredient ID {item['ingredient_id']} not found.")
        
        qty = float(item['qty'])
        if qty <= 0:
            raise ValueError("Item quantity must be strictly greater than 0.")

        bag_size = float(item.get('bag_size_kg', 1.0))
        total_kg = qty * bag_size
        
        if total_kg > (ing.stock_quantity_kg - ing.reserved_quantity_kg):
            raise ValueError(f"Insufficient stock for {ing.name}. Need {total_kg}kg.")

        price_per_unit = ing.retail_price_per_kg * bag_size
        subtotal = qty * price_per_unit
        
        if bag_size == 70.0:
            subtotal -= (subtotal * 0.05)
        elif bag_size == 50.0:
            subtotal -= (subtotal * 0.03)

        total_sales_value += subtotal
        ing.stock_quantity_kg -= total_kg

        line = OrderLine(
            ingredient_id=ing.id, qty_entered=qty, unit_type=item['unit_type'],
            total_kg=total_kg, subtotal=subtotal
        )
        items_processed.append(line)

        db.session.add(StockMovement(
            ingredient_id=ing.id, movement_type='SALE_DEDUCT',
            qty_kg=-total_kg, reference_id=sale_id
        ))

    total_due = max(0.0, total_sales_value - discount_amount)
    
    # 2. Process Payments
    total_paid = 0.0
    total_credit_used = 0.0
    payment_splits = []

    for p in payments:
        amt = float(p.get('amount', 0.0))
        if amt < 0:
            raise ValueError("Negative payments are strictly prohibited.")
        if amt == 0:
            continue
            
        method = p.get('payment_method', 'CASH').upper()
        total_paid += amt
        
        payment_splits.append(PaymentSplit(
            payment_method=method, amount=amt, reference=p.get('reference', '')
        ))

        if method == 'CREDIT':
            total_credit_used += amt

    # 3. Credit Validation
    if total_credit_used > 0:
        if not customer_id:
            raise ValueError("Cannot process CREDIT sale without a registered customer.")
        cust = db.session.get(Customer, customer_id)
        if (cust.current_balance + total_credit_used) > cust.credit_limit:
            raise ValueError(f"Credit limit exceeded. Available: {max(0, cust.credit_limit - cust.current_balance)}")
        cust.current_balance += total_credit_used

    change_due = max(0.0, total_paid - total_due)

    # 4. Save Header
    order = OrderHeader(
        sale_id=sale_id, customer_id=customer_id, 
        total_amount=total_due, paid_amount=total_paid, 
        credit_amount=total_credit_used, change_due=change_due
    )
    db.session.add(order)
    db.session.flush()

    for line in items_processed:
        line.order_id = order.id
        db.session.add(line)
    for split in payment_splits:
        split.order_id = order.id
        db.session.add(split)

    # 5. General Ledger Double Entry
    if total_due > 0:
        post_gl_entry(sale_id, "1000", total_paid - total_credit_used, 0.0, "POS", sale_id)
        if total_credit_used > 0:
            post_gl_entry(sale_id, "1300", total_credit_used, 0.0, "POS", sale_id)
        post_gl_entry(sale_id, "4000", 0.0, total_due, "POS", sale_id)

    return {
        "sale_id": sale_id, "total_amount": total_due, 
        "paid_amount": total_paid, "credit_amount": total_credit_used, 
        "change_due": change_due,
        "items": [{"name": db.session.get(FeedIngredient, l.ingredient_id).name, "qty_entered": l.qty_entered, "unit": l.unit_type, "subtotal": l.subtotal} for l in items_processed]
    }