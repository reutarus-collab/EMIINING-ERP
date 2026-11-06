import math
from services.idempotency import idempotent
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, g
from services.errors import error_response
from sqlalchemy import func
from routes.auth import roles_required, ALL_STAFF, SALES_FLOOR_STAFF
from services.models import db, Customer, FeedIngredient, OrderHeader, PaymentSplit, ItemPrice, TillSession, LocationStock, OperatingExpense, SalesRefund, TillCashMovement
from services.inventory import resolve_location, stock_quantity
from services.sales_rank import sales_rank
retail_bp = Blueprint('retail', __name__)
@retail_bp.route('/api/stock')
@roles_required(*ALL_STAFF)
def stock():
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return error_response(exc)
    rows = FeedIngredient.query.filter(db.or_(FeedIngredient.category.is_(None), FeedIngredient.category != 'Milling Service'),
                                       FeedIngredient.is_active.isnot(False)).all()
    ranked = sales_rank(location.id)
    rows.sort(key=lambda item: (-ranked.get(item.id, 0.0), (item.name or '').lower()))
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
        'sold_kg_30d': round(ranked.get(i.id, 0.0), 2),
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
    ranked = sales_rank()
    items = FeedIngredient.query.all()
    items.sort(key=lambda item: (-ranked.get(item.id, 0.0), (item.name or '').lower()))
    for i in items:
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
    auto_closed = TillSession.query.filter(
        TillSession.location_id == location.id, TillSession.status == 'AUTO_CLOSED',
        TillSession.closed_at >= start, TillSession.closed_at < end).order_by(TillSession.closed_at.desc()).all()
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
        auto_closed_tills=[{'cashier': t.cashier_name, 'opening_cash': round(t.opening_cash or 0, 2),
                            'expected_cash': round(t.expected_cash or 0, 2),
                            'opened_at': t.opened_at.strftime('%H:%M') if t.opened_at else '',
                            'closed_at': t.closed_at.strftime('%Y-%m-%d %H:%M') if t.closed_at else ''} for t in auto_closed],
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


PACK_SIZES = (50, 70)      # only the old two-column API uses this; the screen now sends a free list of sizes
MAX_PACKS_PER_ITEM = 8


@retail_bp.route('/api/admin/packs')
@roles_required('admin')
def packs_list():
    by_item = {}
    for r in ItemPrice.query.order_by(ItemPrice.pack_kg).all():
        by_item.setdefault(r.ingredient_id, []).append({'pack_kg': round(r.pack_kg, 3), 'price': round(r.price, 2)})
    out = []
    ranked = sales_rank()
    items = FeedIngredient.query.all()
    items.sort(key=lambda item: (-ranked.get(item.id, 0.0), (item.name or '').lower()))
    for i in items:
        packs = by_item.get(i.id, [])
        by_size = {int(round(p['pack_kg'])): p['price'] for p in packs}
        out.append({'id': i.id, 'name': i.name,
                    'cost_per_kg': _max_outlet_unit_cost(i.id, i.cost_per_kg or 0),
                    'kg_price': i.retail_price_per_kg or 0,
                    'bag_size_kg': i.bag_size_kg or 0,
                    'packs': packs,
                    'p50': by_size.get(50), 'p70': by_size.get(70)})
    return jsonify(out)


def _parse_pack_list(raw, cost, item_name):
    """Validate a list of {pack_kg, price}. Returns {pack_kg: price}. Any bag size is allowed (45, 40, 25.5 ...)."""
    if not isinstance(raw, list):
        raise ValueError('Bag prices must be a list.')
    if len(raw) > MAX_PACKS_PER_ITEM:
        raise ValueError(f'At most {MAX_PACKS_PER_ITEM} bag sizes per item.')
    out = {}
    for entry in raw:
        entry = entry or {}
        size_raw, price_raw = entry.get('pack_kg'), entry.get('price')
        if size_raw in (None, '') and price_raw in (None, ''):
            continue                                   # an untouched blank row
        try:
            size, price = round(float(size_raw), 3), round(float(price_raw), 2)
        except (TypeError, ValueError):
            raise ValueError('Enter both a bag size in kg and a price for each bag row.')
        if not math.isfinite(size) or not math.isfinite(price):
            raise ValueError('Enter both a bag size in kg and a price for each bag row.')
        if size <= 1 or size > 1000:
            raise ValueError('A bag must be more than 1 kg and at most 1000 kg. Per-kg prices are set under Retail Pricing.')
        if price <= 0:
            raise ValueError('%g kg bag: the price must be above 0.' % size)
        if price <= cost * size:
            raise ValueError('%g kg bag: price %.2f is not above its cost %.2f (%.2f per kg).' % (size, price, cost * size, cost))
        if any(abs(size - k) < 0.0005 for k in out):
            raise ValueError('%g kg bag is listed twice for %s.' % (size, item_name))
        out[size] = price
    return out


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
    try:
        if 'packs' in data:
            new_prices = _parse_pack_list(data.get('packs'), cost, item.name)
        else:                                          # older clients: the fixed 50 kg and 70 kg boxes
            legacy = [{'pack_kg': size, 'price': data.get('p%d' % size)} for size in PACK_SIZES
                      if data.get('p%d' % size) not in (None, '')]      # a blank old-style box means "remove it"
            new_prices = _parse_pack_list(legacy, cost, item.name)
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    existing = ItemPrice.query.filter_by(ingredient_id=item.id).all()
    managed = existing if 'packs' in data else [r for r in existing if any(abs(r.pack_kg - z) < 0.0005 for z in PACK_SIZES)]
    keep = set()
    for size, price in new_prices.items():
        row = next((r for r in existing if abs(r.pack_kg - size) < 0.0005), None)
        if row:
            row.price = price
            keep.add(row.id)
        else:
            db.session.add(ItemPrice(ingredient_id=item.id, pack_kg=float(size), price=price))
    for r in managed:
        if r.id not in keep and not any(abs(r.pack_kg - k) < 0.0005 for k in new_prices):
            db.session.delete(r)
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
            packs.setdefault(r.ingredient_id, {})[round(r.pack_kg, 3)] = r.price
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

    header = ['Product', 'Per kg (KSh)'] + ['%g kg bag (KSh)' % z for z in sizes]
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
