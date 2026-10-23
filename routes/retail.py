import math
from services.idempotency import idempotent
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, g
from services.errors import error_response
from sqlalchemy import func
from routes.auth import roles_required, ALL_STAFF, SALES_FLOOR_STAFF
from services.models import db, Customer, FeedIngredient, OrderHeader, PaymentSplit, ItemPrice, TillSession, LocationStock, OperatingExpense, SalesRefund, TillCashMovement
from services.inventory import resolve_location, stock_quantity
retail_bp = Blueprint('retail', __name__)
@retail_bp.route('/api/stock')
@roles_required(*ALL_STAFF)
def stock():
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return error_response(exc)
    rows = FeedIngredient.query.filter(db.or_(FeedIngredient.category.is_(None), FeedIngredient.category != 'Milling Service'),
                                       FeedIngredient.is_active.isnot(False)).order_by(FeedIngredient.name).all()
    show_cost = g.user.role in ('admin', 'accountant', 'warehouse')
    result = []
    for i in rows:
        balance = LocationStock.query.filter_by(location_id=location.id, ingredient_id=i.id).first()
        qty = round(stock_quantity(location.id, i.id), 2)
        row = {
        'id': i.id,
        'name': i.name,
        'category': i.category or '',
        'stock_kg': qty,
        'location_id': location.id, 'location_name': location.name,
        'bag_size_kg': i.bag_size_kg or 0,
        'retail_price_per_kg': i.retail_price_per_kg or 0,
        'priced': (i.retail_price_per_kg or 0) > 0,
        }
        if show_cost:
            cost = (balance.unit_cost_per_kg if balance else 0.0) or 0.0
            row.update(unit_cost_per_kg=round(cost, 4), stock_value=round(qty * cost, 2))
        result.append(row)
    return jsonify(result)

def _max_outlet_unit_cost(ingredient_id, fallback=0.0):
    costs = [row[0] or 0.0 for row in db.session.query(LocationStock.unit_cost_per_kg)
             .filter(LocationStock.ingredient_id == ingredient_id,
                     LocationStock.quantity_kg > 0).all()]
    return max(costs or [fallback or 0.0])
@retail_bp.route('/api/admin/prices')
@roles_required('admin')
def prices_list():
    out = []
    for i in FeedIngredient.query.order_by(FeedIngredient.name).all():
        cost = _max_outlet_unit_cost(i.id, i.cost_per_kg or 0)
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
    cost = _max_outlet_unit_cost(item.id, item.cost_per_kg or 0)
    if price <= cost:
        return jsonify(status='error',
                       message=f'Price {price:.2f} is not above cost {cost:.2f}. Fix the cost or raise the price.'), 400
    item.retail_price_per_kg = price
    db.session.commit()
    return jsonify(status='success')
@retail_bp.route('/api/reports/daily')
@roles_required(*SALES_FLOOR_STAFF)
def daily_report():
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return error_response(exc)
    now_eat = datetime.utcnow() + timedelta(hours=3)
    start_eat = now_eat.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_eat - timedelta(hours=3)
    end = start + timedelta(days=1)
    orders = OrderHeader.query.filter(
        OrderHeader.created_at >= start,
        OrderHeader.created_at < end,
        OrderHeader.location_id == location.id,
        OrderHeader.status == 'COMPLETED').all()
    ids = [o.id for o in orders]
    by_method = {}
    if ids:
        rows = (db.session.query(PaymentSplit.payment_method, func.sum(PaymentSplit.amount))
                .filter(PaymentSplit.order_id.in_(ids))
                .group_by(PaymentSplit.payment_method).all())
        by_method = {m: round(a or 0, 2) for m, a in rows if m != 'CREDIT'}
        cash_rows = (db.session.query(PaymentSplit.order_id, func.sum(PaymentSplit.amount))
                     .filter(PaymentSplit.order_id.in_(ids), PaymentSplit.payment_method == 'CASH')
                     .group_by(PaymentSplit.order_id).all())
        cash_by_order = {order_id: amount or 0.0 for order_id, amount in cash_rows}
        cash_change = round(sum(min(o.change_due or 0, cash_by_order.get(o.id, 0.0)) for o in orders), 2)
        if cash_change:
            by_method['CASH'] = round(max(0.0, by_method.get('CASH', 0.0) - cash_change), 2)
    tills = TillSession.query.filter(
        TillSession.location_id == location.id,
        TillSession.closed_at >= start,
        TillSession.closed_at < end,
        TillSession.status == 'CLOSED').order_by(TillSession.closed_at.desc()).all()
    expenses = OperatingExpense.query.filter(OperatingExpense.location_id == location.id,
                                             OperatingExpense.created_at >= start,
                                             OperatingExpense.created_at < end).all()
    refunds = SalesRefund.query.filter(SalesRefund.location_id == location.id,
                                       SalesRefund.created_at >= start,
                                       SalesRefund.created_at < end).all()
    till_movements = (TillCashMovement.query.join(TillSession)
                      .filter(TillSession.location_id == location.id,
                              TillCashMovement.created_at >= start,
                              TillCashMovement.created_at < end).all())
    expense_categories = {}
    for expense in expenses:
        expense_categories[expense.category] = round(expense_categories.get(expense.category, 0.0) + (expense.amount or 0.0), 2)
    return jsonify(
        date=start_eat.strftime('%Y-%m-%d'),
        location_id=location.id, location_name=location.name,
        sales_count=len(orders),
        total_sales=round(sum(o.total_amount or 0 for o in orders), 2),
        discounts=round(sum(o.discount_amount or 0 for o in orders), 2),
        by_method=by_method,
        till_closures=[{'cashier': t.cashier_name, 'opening_cash': round(t.opening_cash or 0, 2),
                        'expected_cash': round(t.expected_cash or 0, 2),
                        'counted_cash': round(t.counted_cash or 0, 2),
                        'variance': round(t.cash_variance or 0, 2),
                        'closed_at': t.closed_at.strftime('%H:%M') if t.closed_at else ''} for t in tills],
        credit_sales=round(sum(o.credit_amount or 0 for o in orders), 2),
        expenses_total=round(sum(e.amount or 0 for e in expenses), 2),
        expenses_by_category=expense_categories,
        refunds_total=round(sum(r.amount or 0 for r in refunds), 2),
        cash_paid_in=round(sum(m.amount or 0 for m in till_movements if m.movement_type == 'PAID_IN'), 2),
        cash_paid_out=round(sum(m.amount or 0 for m in till_movements if m.movement_type == 'PAID_OUT'), 2),
        cash_to_mpesa=round(sum(m.amount or 0 for m in till_movements if m.movement_type == 'MPESA_DEPOSIT'), 2))


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


@retail_bp.route('/api/admin/customers/<int:customer_id>', methods=['DELETE'])
@roles_required('admin')
def delete_customer(customer_id):
    """Delete only a customer with no history at all. Anyone with sales, payments or debt is merged instead."""
    from services.models import CustomerPayment, CustomerDebtEntry
    cust = db.session.get(Customer, customer_id)
    if not cust:
        return jsonify(status='error', message='Customer not found.'), 404
    orders = OrderHeader.query.filter_by(customer_id=cust.id).count()
    pays = CustomerPayment.query.filter_by(customer_id=cust.id).count()
    debts = CustomerDebtEntry.query.filter_by(customer_id=cust.id).count()
    if orders or pays or debts or abs(cust.current_balance or 0.0) > 0.005:
        return jsonify(status='error', message=(
            'This customer has sales, payments or a balance on record, so it cannot be deleted. '
            'Use Merge to move the history into the correct customer.')), 409
    db.session.delete(cust)
    db.session.commit()
    return jsonify(status='success')


@retail_bp.route('/api/admin/customers/merge', methods=['POST'])
@roles_required('admin')
@idempotent('customer-merge')
def merge_customers():
    """Move every sale, payment and debt entry from `source_id` to `target_id`, add the balances, remove the source."""
    from services.models import CustomerPayment, CustomerDebtEntry, CustomerMergeLog
    data = request.get_json(silent=True) or {}
    try:
        source_id, target_id = int(data.get('source_id')), int(data.get('target_id'))
    except (TypeError, ValueError):
        return jsonify(status='error', message='Choose the customer to merge and the customer to keep.'), 400
    if source_id == target_id:
        return jsonify(status='error', message='Choose two different customers.'), 400
    source, target = db.session.get(Customer, source_id), db.session.get(Customer, target_id)
    if not source or not target:
        return jsonify(status='error', message='Customer not found.'), 404
    try:
        moved_orders = OrderHeader.query.filter_by(customer_id=source.id).update({'customer_id': target.id})
        moved_pays = CustomerPayment.query.filter_by(customer_id=source.id).update({'customer_id': target.id})
        moved_debts = CustomerDebtEntry.query.filter_by(customer_id=source.id).update({'customer_id': target.id})
        moved_balance = round(source.current_balance or 0.0, 2)
        target.current_balance = round((target.current_balance or 0.0) + moved_balance, 2)
        target.credit_limit = max(target.credit_limit or 0.0, source.credit_limit or 0.0)
        if not target.phone and source.phone:
            target.phone = source.phone
        db.session.add(CustomerMergeLog(
            source_id=source.id, source_name=source.name, target_id=target.id, target_name=target.name,
            orders_moved=moved_orders, payments_moved=moved_pays, debt_entries_moved=moved_debts,
            balance_moved=moved_balance, merged_by=g.user.username))
        db.session.delete(source)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify(status='error', message='Could not merge the customers.'), 500
    return jsonify(status='success', orders_moved=moved_orders, payments_moved=moved_pays,
                   debt_entries_moved=moved_debts, balance_moved=moved_balance, kept_id=target.id)


PACK_SIZES = (50, 70)
@retail_bp.route('/api/admin/packs')
@roles_required('admin')
def packs_list():
    by_item = {}
    for r in ItemPrice.query.all():
        by_item.setdefault(r.ingredient_id, {})[int(round(r.pack_kg))] = r.price
    out = []
    for i in FeedIngredient.query.order_by(FeedIngredient.name).all():
        prices = by_item.get(i.id, {})
        out.append({'id': i.id, 'name': i.name,
                    'cost_per_kg': _max_outlet_unit_cost(i.id, i.cost_per_kg or 0),
                    'kg_price': i.retail_price_per_kg or 0,
                    'p50': prices.get(50), 'p70': prices.get(70)})
    return jsonify(out)
@retail_bp.route('/api/admin/packs', methods=['POST'])
@roles_required('admin')
def packs_save():
    data = request.get_json(silent=True) or {}
    try:
        item = db.session.get(FeedIngredient, int(data.get('id')))
    except (TypeError, ValueError):
        return jsonify(status='error', message='Bad item.'), 400
    if not item:
        return jsonify(status='error', message='Item not found.'), 404
    cost = _max_outlet_unit_cost(item.id, item.cost_per_kg or 0)
    new_prices = {}
    for size in PACK_SIZES:
        raw = data.get('p%d' % size)
        if raw is None or raw == '':
            new_prices[size] = None
            continue
        try:
            price = round(float(raw), 2)
        except (TypeError, ValueError):
            return jsonify(status='error', message='Invalid %d kg price.' % size), 400
        if not math.isfinite(price) or price <= 0:
            return jsonify(status='error', message='%d kg price must be above 0.' % size), 400
        if price <= cost * size:
            return jsonify(status='error',
                           message='%d kg price %.2f is not above its cost %.2f (%.2f per kg).' % (size, price, cost * size, cost)), 400
        new_prices[size] = price
    for size, price in new_prices.items():
        row = None
        for r in ItemPrice.query.filter_by(ingredient_id=item.id).all():
            if abs(r.pack_kg - size) < 0.0005:
                row = r
        if price is None:
            if row:
                db.session.delete(row)
        elif row:
            row.price = price
        else:
            db.session.add(ItemPrice(ingredient_id=item.id, pack_kg=float(size), price=price))
    db.session.commit()
    return jsonify(status='success')


@retail_bp.route('/api/price-list.pdf')
@roles_required(*ALL_STAFF)
def price_list_pdf():
    """Customer-facing retail price list. Prices only: no costs, margins or stock."""
    import io
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from flask import Response

    packs = {}
    for r in ItemPrice.query.all():
        if (r.price or 0) > 0:
            packs.setdefault(r.ingredient_id, {})[int(round(r.pack_kg))] = r.price
    sizes = sorted({size for p in packs.values() for size in p})

    def group_rank(cat):
        cat = (cat or '').strip().lower()
        return 0 if cat == 'finished feed' else (2 if cat == 'milling service' else 1)

    items = [i for i in FeedIngredient.query.filter(FeedIngredient.is_active.isnot(False)).all()
             if (i.retail_price_per_kg or 0) > 0 or packs.get(i.id)]
    items.sort(key=lambda i: (group_rank(i.category), (i.name or '').lower()))
    if not items:
        return jsonify(status='error', message='No priced items yet. Set prices under Retail Pricing first.'), 404

    def money(v):
        return '{:,.0f}'.format(v) if abs(v - round(v)) < 0.005 else '{:,.2f}'.format(v)

    styles = getSampleStyleSheet()
    title = ParagraphStyle('t', parent=styles['Title'], fontSize=22, spaceAfter=2)
    sub = ParagraphStyle('s', parent=styles['Normal'], alignment=1, textColor=colors.HexColor('#555555'))
    cell = ParagraphStyle('c', parent=styles['Normal'], fontSize=10, leading=12)
    now_eat = datetime.utcnow() + timedelta(hours=3)

    header = ['Product', 'Per kg (KSh)'] + ['%d kg bag (KSh)' % z for z in sizes]
    rows = [header]
    for i in items:
        rows.append([Paragraph((i.name or '').replace('&', '&amp;').replace('<', '&lt;'), cell),
                     money(i.retail_price_per_kg) if (i.retail_price_per_kg or 0) > 0 else '-']
                    + [money(packs[i.id][z]) if z in packs.get(i.id, {}) else '-' for z in sizes])

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title='Emining Feeds Price List')
    usable = A4[0] - 36 * mm
    first = usable * 0.4
    other = (usable - first) / max(1, len(header) - 1)
    table = Table(rows, colWidths=[first] + [other] * (len(header) - 1), repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1f6f3f')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f2f6f3')]),
        ('LINEBELOW', (0, 0), (-1, -1), 0.4, colors.HexColor('#d0d7d2')),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    doc.build([Paragraph('EMINING FEEDS', title),
               Paragraph('Retail Price List &nbsp;|&nbsp; %s' % now_eat.strftime('%d %B %Y'), sub),
               Spacer(1, 10 * mm), table, Spacer(1, 6 * mm),
               Paragraph('Prices in Kenya Shillings. Bag prices are discounted against the per-kg price. '
                         'Prices may change without notice.', sub)])
    return Response(buf.getvalue(), mimetype='application/pdf', headers={
        'Content-Disposition': 'attachment; filename=Emining_Feeds_Price_List_%s.pdf' % now_eat.strftime('%Y-%m-%d')})
