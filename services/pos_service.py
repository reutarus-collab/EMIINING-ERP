import uuid
from datetime import datetime
from services.db import db
from services.models import FeedIngredient, Customer, OrderHeader, OrderLine, PaymentSplit, StockMovement, ItemPrice, TillSession, IdempotencyKey
from services.inventory import location_stock, change_stock

import math
from flask import g
# Max discount as % of the bill, per role. A role not listed gets 0%.
DISCOUNT_CAP_PCT = {'sales': 5.0, 'accountant': 10.0, 'admin': 30.0}
def _unit_price(ingredient, pack_kg):
    """Price of ONE pack of the given size. No fallback to cost, no guessing."""
    if abs(pack_kg - 1.0) < 0.0005:
        price = ingredient.retail_price_per_kg or 0.0
        if price <= 0:
            raise Exception(f"'{ingredient.name}' has no per-kg price. An admin must set it under Retail Pricing.")
        return price
    for row in ItemPrice.query.filter_by(ingredient_id=ingredient.id).all():
        if abs(row.pack_kg - pack_kg) < 0.0005 and (row.price or 0) > 0:
            return row.price
    raise Exception(f"'{ingredient.name}' has no price for a {pack_kg:g} kg pack. An admin must set it under Bag Prices.")
def _num(value, name):
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise Exception(f"Invalid {name}.")
    if not math.isfinite(v):
        raise Exception(f"Invalid {name}.")
    return v
def process_full_pos_checkout(data, idempotency_key=None, request_hash=None, created_by=None):
    customer_id = data.get('customer_id')
    discount_amount = round(_num(data.get('discount_amount', 0.0), 'discount amount'), 2)
    if discount_amount < 0:
        raise Exception("Invalid discount amount.")
    cart = data.get('cart', [])
    payments = data.get('payments', [])
    location_id = int(data.get('location_id'))
    till_session_id = int(data.get('till_session_id'))
    if not isinstance(payments, list):
        raise Exception('Invalid payments.')
    for p in payments:
        if not isinstance(p, dict) or p.get('payment_method') not in ('CASH', 'MPESA', 'BANK', 'CREDIT'):
            raise Exception('Invalid payment method.')
        if _num(p.get('amount', 0.0), 'payment amount') < 0:
            raise Exception('Payment amounts cannot be negative.')
    if not cart:
        raise Exception("Cart is empty.")
    total_bill = 0.0
    total_cost = 0.0
    order_lines = []
    for item in cart:
        ing_id = item.get('ingredient_id')
        qty = _num(item.get('qty', 0.0), 'quantity')
        unit_type = item.get('unit_type', 'KG')
        bag_size_kg = _num(item.get('bag_size_kg', 1.0), 'bag size')
        if qty <= 0 or bag_size_kg <= 0:
            raise Exception("Quantity and bag size must be above zero.")
        total_kg_for_item = qty * bag_size_kg
        ingredient = FeedIngredient.query.get(ing_id)
        if not ingredient:
            raise Exception(f"Ingredient ID {ing_id} not found.")
        stock_row = location_stock(location_id, ingredient.id, lock=True)
        available = (stock_row.quantity_kg or 0.0) - (stock_row.reserved_quantity_kg or 0.0)
        if available < total_kg_for_item:
            raise Exception(f"Insufficient stock at this outlet for {ingredient.name}. Available: {available}kg")
        unit_price = _unit_price(ingredient, bag_size_kg)
        subtotal = qty * unit_price
        unit_type = 'KG' if abs(bag_size_kg - 1.0) < 0.0005 else ('%g' % bag_size_kg) + 'KG BAG'
        change_stock(location_id, ingredient, -total_kg_for_item, 'POS_SALE')
        total_bill += subtotal
        total_cost += total_kg_for_item * (stock_row.unit_cost_per_kg or 0.0)
        order_lines.append(OrderLine(
            ingredient_id=ingredient.id,
            unit_type=unit_type,
            qty_entered=qty,
            subtotal=subtotal,
            unit_cost_per_kg=stock_row.unit_cost_per_kg or 0.0
        ))
    role = getattr(getattr(g, 'user', None), 'role', None)
    max_pct = DISCOUNT_CAP_PCT.get(role, 0.0)
    max_discount = round(total_bill * max_pct / 100.0, 2)
    if discount_amount > max_discount + 0.01:
        raise Exception(f"Discount KSh {discount_amount:.2f} is above the {max_pct:.0f}% limit for your role (max KSh {max_discount:.2f}).")
    final_due = max(0.0, total_bill - discount_amount)
    if final_due + 0.01 < total_cost:
        raise Exception(f"Sale is below the cost of the goods (KSh {final_due:.2f} vs cost KSh {total_cost:.2f}). Reduce the discount.")
    
    total_paid = sum(float(p.get('amount', 0.0)) for p in payments if p.get('payment_method') != 'CREDIT')
    explicit_credit = sum(float(p.get('amount', 0.0)) for p in payments if p.get('payment_method') == 'CREDIT')
    
    credit_amount = explicit_credit
    total_tendered = total_paid + credit_amount
    if credit_amount > 0 and total_paid + credit_amount > final_due + 0.01:
        raise Exception('Cash plus credit is more than the amount due. Reduce the credit to the unpaid balance.')

    if total_tendered < (final_due - 0.01):
        raise Exception(f"Payment incomplete! KSh {total_tendered:.2f} tendered but KSh {final_due:.2f} is due. Select 'Credit' as the payment method and enter the amount if this is a debt sale.")

    change_due = max(0.0, total_paid - final_due) if credit_amount == 0 else 0.0
    cash_tendered = sum(_num(p.get('amount', 0.0), 'payment amount') for p in payments
                        if p.get('payment_method') == 'CASH')
    if change_due > cash_tendered + 0.01:
        raise Exception('Change can only be issued from the cash tender. Reduce non-cash overpayment or enter exact payment amounts.')

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
        status='COMPLETED', location_id=location_id, till_session_id=till_session_id
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

    till = TillSession.query.filter_by(id=till_session_id, location_id=location_id,
                                       cashier_name=getattr(g.user, 'username', ''),
                                       status='OPEN').with_for_update().first()
    if not till:
        raise Exception('Till session is no longer open. Open a till and retry.')
    cash_change = min(change_due, cash_tendered)
    till.expected_cash = round((till.expected_cash or 0.0) + cash_tendered - cash_change, 2)

    try:
        from services.ledger_service import post_gl_entry
        post_gl_entry(sale_id, '4000', 0.0, final_due, 'POS', order.id)
        if total_paid > 0:
            change_remaining = change_due
            account_by_method = {'CASH': '1000', 'MPESA': '1010', 'BANK': '1020'}
            for payment in payments:
                method = payment.get('payment_method')
                if method == 'CREDIT':
                    continue
                amount = _num(payment.get('amount', 0.0), 'payment amount')
                returned = min(change_remaining, amount) if method == 'CASH' else 0.0
                change_remaining -= returned
                if amount > returned:
                    post_gl_entry(sale_id, account_by_method[method], amount - returned, 0.0, 'POS', order.id)
        if credit_amount > 0:
            post_gl_entry(sale_id, '1300', credit_amount, 0.0, 'POS', order.id)
        if total_cost > 0:
            post_gl_entry(sale_id, '5000', total_cost, 0.0, 'POS', order.id)
            post_gl_entry(sale_id, '1200', 0.0, total_cost, 'POS', order.id)
    except Exception as e:
        db.session.rollback()
        raise Exception(f"Sale blocked: accounting ledger failed to post ({e}). No stock or payment was recorded - try again or check ledger_service.py.")

    res_items = []
    for line in order_lines:
        res_items.append({
            "name": FeedIngredient.query.get(line.ingredient_id).name,
            "qty_entered": line.qty_entered, 
            "unit": line.unit_type, 
            "subtotal": line.subtotal
        })

    response = {
        "sale_id": sale_id, "total_amount": final_due, "paid_amount": total_paid, 
        "change_due": change_due, "credit_amount": credit_amount, "items": res_items 
    }
    if idempotency_key:
        db.session.add(IdempotencyKey(key=idempotency_key, request_hash=request_hash,
                                      response_json=response, created_by=created_by,
                                      location_id=location_id))
    db.session.commit()
    return response
