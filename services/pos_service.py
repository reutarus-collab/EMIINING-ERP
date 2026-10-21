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
            raise ValueError(f"'{ingredient.name}' has no per-kg price. An admin must set it under Retail Pricing.")
        return price
    for row in ItemPrice.query.filter_by(ingredient_id=ingredient.id).all():
        if abs(row.pack_kg - pack_kg) < 0.0005 and (row.price or 0) > 0:
            return row.price
    raise ValueError(f"'{ingredient.name}' has no price for a {pack_kg:g} kg pack. An admin must set it under Bag Prices.")
def _num(value, name):
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid {name}.")
    if not math.isfinite(v):
        raise ValueError(f"Invalid {name}.")
    return v
def process_full_pos_checkout(data, idempotency_key=None, request_hash=None, created_by=None):
    customer_id = data.get('customer_id')
    discount_amount = round(_num(data.get('discount_amount', 0.0), 'discount amount'), 2)
    if discount_amount < 0:
        raise ValueError("Invalid discount amount.")
    cart = data.get('cart', [])
    payments = data.get('payments', [])
    location_id = int(data.get('location_id'))
    till_session_id = int(data.get('till_session_id'))
    if not isinstance(payments, list):
        raise ValueError('Invalid payments.')
    for p in payments:
        if not isinstance(p, dict) or p.get('payment_method') not in ('CASH', 'MPESA', 'BANK', 'CREDIT'):
            raise ValueError('Invalid payment method.')
        if _num(p.get('amount', 0.0), 'payment amount') < 0:
            raise ValueError('Payment amounts cannot be negative.')
        fee = _num(p.get('fee', 0.0) or 0.0, 'M-Pesa/bank deduction')
        if fee < 0:
            raise ValueError('M-Pesa/bank deduction cannot be negative.')
        if fee > 0 and p.get('payment_method') not in ('MPESA', 'BANK'):
            raise ValueError('A deduction can only be entered on M-Pesa or Bank payments.')
        if fee > _num(p.get('amount', 0.0), 'payment amount') + 0.001:
            raise ValueError('M-Pesa/bank deduction cannot be more than the amount paid.')
    if not cart:
        raise ValueError("Cart is empty.")
    total_bill = 0.0
    total_cost = 0.0
    order_lines = []
    for item in cart:
        ing_id = item.get('ingredient_id')
        qty = _num(item.get('qty', 0.0), 'quantity')
        unit_type = item.get('unit_type', 'KG')
        bag_size_kg = _num(item.get('bag_size_kg', 1.0), 'bag size')
        if qty <= 0 or bag_size_kg <= 0:
            raise ValueError("Quantity and bag size must be above zero.")
        total_kg_for_item = qty * bag_size_kg
        ingredient = FeedIngredient.query.get(ing_id)
        if ingredient is not None and ingredient.is_active is False:
            raise ValueError(f'{ingredient.name} is disabled and cannot be sold.')
        if not ingredient:
            raise ValueError(f"Ingredient ID {ing_id} not found.")
        is_service = ingredient.category == 'Milling Service'
        if is_service:
            if abs(bag_size_kg - 1.0) > 0.0005:
                raise ValueError('Milling services must be sold by the kilogram.')
            stock_row = None
        else:
            stock_row = location_stock(location_id, ingredient.id, lock=True)
            available = (stock_row.quantity_kg or 0.0) - (stock_row.reserved_quantity_kg or 0.0)
            if available < total_kg_for_item:
                raise ValueError(f"Insufficient stock at this outlet for {ingredient.name}. Available: {available}kg")
        unit_price = _unit_price(ingredient, bag_size_kg)
        subtotal = qty * unit_price
        unit_type = ('KG SERVICE' if is_service else
                     ('KG' if abs(bag_size_kg - 1.0) < 0.0005 else ('%g' % bag_size_kg) + 'KG BAG'))
        if not is_service:
            change_stock(location_id, ingredient, -total_kg_for_item, 'POS_SALE')
        total_bill += subtotal
        unit_cost = (stock_row.unit_cost_per_kg or 0.0) if stock_row else 0.0
        total_cost += total_kg_for_item * unit_cost
        order_lines.append(OrderLine(
            ingredient_id=ingredient.id,
            unit_type=unit_type,
            qty_entered=qty,
            subtotal=subtotal,
            unit_cost_per_kg=unit_cost
        ))
    role = getattr(getattr(g, 'user', None), 'role', None)
    max_pct = DISCOUNT_CAP_PCT.get(role, 0.0)
    max_discount = round(total_bill * max_pct / 100.0, 2)
    if discount_amount > max_discount + 0.01:
        raise ValueError(f"Discount KSh {discount_amount:.2f} is above the {max_pct:.0f}% limit for your role (max KSh {max_discount:.2f}).")
    # Whole cents: the ledger legs below are posted from this, and customers can only pay in cents.
    final_due = round(max(0.0, total_bill - discount_amount), 2)
    if final_due + 0.01 < total_cost:
        raise ValueError(f"Sale is below the cost of the goods (KSh {final_due:.2f} vs cost KSh {total_cost:.2f}). Reduce the discount.")
    
    total_paid = sum(float(p.get('amount', 0.0)) for p in payments if p.get('payment_method') != 'CREDIT')
    explicit_credit = sum(float(p.get('amount', 0.0)) for p in payments if p.get('payment_method') == 'CREDIT')
    
    credit_amount = explicit_credit
    total_tendered = total_paid + credit_amount
    if credit_amount > 0 and total_paid + credit_amount > final_due + 0.01:
        raise ValueError('Cash plus credit is more than the amount due. Reduce the credit to the unpaid balance.')

    if total_tendered < (final_due - 0.01):
        raise ValueError(f"Payment incomplete! KSh {total_tendered:.2f} tendered but KSh {final_due:.2f} is due. Select 'Credit' as the payment method and enter the amount if this is a debt sale.")

    change_due = max(0.0, total_paid - final_due) if credit_amount == 0 else 0.0
    cash_tendered = sum(_num(p.get('amount', 0.0), 'payment amount') for p in payments
                        if p.get('payment_method') == 'CASH')
    if change_due > cash_tendered + 0.01:
        raise ValueError('Change can only be issued from the cash tender. Reduce non-cash overpayment or enter exact payment amounts.')

    if customer_id:
        sale_customer = Customer.query.get(customer_id)
        if not sale_customer:
            raise ValueError('Customer not found.')
        if sale_customer.location_id is not None and sale_customer.location_id != location_id:
            raise ValueError('This customer belongs to another outlet. Register them at this outlet first.')

    if credit_amount > 0:
        if not customer_id:
            raise ValueError("Cannot sell on credit to a Walk-In customer. Please select a registered customer.")
        customer = Customer.query.get(customer_id)
        if (customer.current_balance + credit_amount) > customer.credit_limit:
            raise ValueError(f"Credit limit exceeded! Customer can only take KSh {max(0, customer.credit_limit - customer.current_balance):.2f} more.")
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
            fee = round(float(p.get('fee', 0.0) or 0.0), 2) if p.get('payment_method') in ('MPESA', 'BANK') else 0.0
            db.session.add(PaymentSplit(order_id=order.id, payment_method=p.get('payment_method'), amount=amt,
                                        reference=p.get('reference', ''), channel_fee=fee))

    till = TillSession.query.filter_by(id=till_session_id, location_id=location_id,
                                       cashier_name=getattr(g.user, 'username', ''),
                                       status='OPEN').with_for_update().first()
    if not till:
        raise ValueError('Till session is no longer open. Open a till and retry.')
    cash_change = min(change_due, cash_tendered)
    till.expected_cash = round((till.expected_cash or 0.0) + cash_tendered - cash_change, 2)

    try:
        from services.ledger_service import post_gl_transaction
        debit_legs = []
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
                fee = round(float(payment.get('fee', 0.0) or 0.0), 2) if method in ('MPESA', 'BANK') else 0.0
                if amount > returned:
                    # The account receives the amount net of the channel deduction; the deduction is an expense.
                    debit_legs.append((account_by_method[method], round(amount - returned - fee, 2), 0.0))
                    if fee > 0:
                        debit_legs.append(('5360', fee, 0.0))
        if credit_amount > 0:
            debit_legs.append(('1300', round(credit_amount, 2), 0.0))
        # Revenue is exactly what the customer settled (cash, mobile money, bank, credit), so the journal
        # balances by construction. Earlier checks already limit rounding drift to one cent.
        revenue = round(sum(leg[1] for leg in debit_legs), 2)
        if abs(revenue - final_due) > 0.011:
            raise ValueError(f'Settled amount KSh {revenue:.2f} does not match the amount due KSh {final_due:.2f}.')
        legs = [('4000', 0.0, revenue)] + debit_legs
        if total_cost > 0:
            legs += [('5000', round(total_cost, 2), 0.0), ('1200', 0.0, round(total_cost, 2))]
        # One journal for the whole sale: it posts completely or not at all.
        post_gl_transaction(sale_id, legs, 'POS', order.id, skip_zero=True)
    except Exception as e:
        db.session.rollback()
        if isinstance(e, ValueError):
            raise ValueError(f"Sale blocked: the accounting entry did not balance ({e}). No stock or payment was recorded.")
        raise   # a real fault: the route logs it with a reference code and shows a safe message

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
