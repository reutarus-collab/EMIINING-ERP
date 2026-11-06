"""Receipt images, receipt verification, customer statements, outlet receipt settings and the owner's insight reports."""
from datetime import datetime, timedelta

from flask import Blueprint, Response, g, jsonify, request

from routes.auth import roles_required, ALL_STAFF
from services import receipt as rcpt
from services.db import db
from services.errors import error_response
from services.inventory import resolve_location
from services.item_groups import group_for, GROUP_LABEL, GROUP_RANK
from services.models import Customer, FeedIngredient, Location, LocationStock, OrderHeader, OrderLine

receipts_bp = Blueprint('receipts', __name__)
OWNER_ROLES = ('admin', 'accountant')


def _same_outlet(location_id):
    return g.user.role in OWNER_ROLES or location_id == getattr(g.user, 'location_id', None)


@receipts_bp.route('/api/receipts/<int:order_id>.png', methods=['GET'])
@roles_required(*ALL_STAFF)
def receipt_png(order_id):
    order = db.session.get(OrderHeader, order_id)
    if not order or not _same_outlet(order.location_id):
        return jsonify(status='error', message='Receipt not found at this outlet.'), 404
    data = rcpt.build(order)
    body = rcpt.png_bytes(data)
    name = (data['ref'] or 'receipt').replace('/', '-')
    return Response(body, mimetype='image/png', headers={
        'Content-Disposition': f'inline; filename="{name}.png"', 'Cache-Control': 'private, no-store'})


@receipts_bp.route('/api/receipts/verify', methods=['GET'])
@roles_required(*ALL_STAFF)
def verify_receipt():
    """Staff type the number and check code printed on a receipt. Only a receipt this server issued, for exactly this
    amount and time, passes."""
    ref, code = request.args.get('ref', ''), str(request.args.get('code', '')).strip().upper()
    order = rcpt.find_order(ref)
    if not order or not _same_outlet(order.location_id):
        return jsonify(valid=False, message='No such receipt at this outlet.')
    d = rcpt.build(order)
    if code != d['code']:
        return jsonify(valid=False, message='Check code does not match this receipt. Treat it as altered or fake.')
    return jsonify(valid=True, ref=d['ref'], when=rcpt.fmt_when(d['when']), customer=d['customer'], cashier=d['cashier'],
                   total=d['total'], refunded=d['refunded'], credit=bool(d['credit_sale']),
                   items=[f"{i['name']} {i['kg']:g} kg = {i['amount']:,.2f}" for i in d['items']],
                   message='Genuine receipt.' + (f" Note: KSh {d['refunded']:,.2f} of it was refunded." if d['refunded'] else ''))


@receipts_bp.route('/api/customers/<int:customer_id>/statement.pdf', methods=['GET'])
@roles_required(*ALL_STAFF)
def customer_statement(customer_id):
    cust = db.session.get(Customer, customer_id)
    if not cust:
        return jsonify(status='error', message='Customer not found.'), 404
    if not _same_outlet(cust.location_id):
        return jsonify(status='error', message='This customer belongs to another outlet.'), 403
    try:
        days = int(request.args.get('days', 0)) or None
    except ValueError:
        days = None
    return Response(rcpt.statement_pdf(cust, days), mimetype='application/pdf', headers={
        'Content-Disposition': f'inline; filename="statement-{cust.id}.pdf"', 'Cache-Control': 'private, no-store'})


@receipts_bp.route('/api/locations/<int:location_id>/receipt-settings', methods=['GET', 'POST'])
@roles_required(*OWNER_ROLES)
def receipt_settings(location_id):
    loc = db.session.get(Location, location_id)
    if not loc:
        return jsonify(status='error', message='Outlet not found.'), 404
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        try:
            width = int(data.get('receipt_width', loc.receipt_width or 80))
        except (TypeError, ValueError):
            width = 0
        if width not in (58, 80):
            return jsonify(status='error', message='Paper width must be 58 or 80 mm.'), 400
        phone = str(data.get('phone') or '').strip()[:30]
        if phone and not rcpt.kenya_phone(phone):
            return jsonify(status='error', message='That outlet phone number is not valid.'), 400
        loc.receipt_width, loc.phone = width, phone
        db.session.commit()
    return jsonify(status='success', id=loc.id, name=loc.name, phone=loc.phone or '', receipt_width=loc.receipt_width or 80)


def _window(days):
    try:
        days = max(1, min(int(days), 365))
    except (TypeError, ValueError):
        days = 30
    return days, datetime.utcnow() - timedelta(days=days)


@receipts_bp.route('/api/insights/products', methods=['GET'])
@roles_required(*OWNER_ROLES)
def product_ranking():
    """Per item: kg sold, revenue, profit after discount, stock, days of cover. Ranked inside each group."""
    try:
        location = resolve_location(request.args.get('location_id'))
        days, since = _window(request.args.get('days', 30))
        orders = {o.id: o for o in OrderHeader.query.filter(
            OrderHeader.location_id == location.id, OrderHeader.status == 'COMPLETED', OrderHeader.created_at >= since).all()}
        sold = {}
        for l in OrderLine.query.filter(OrderLine.order_id.in_(list(orders) or [0])).all():
            o = orders[l.order_id]
            kg = rcpt.line_kg(l)
            share = (o.discount_amount or 0.0) * (l.subtotal or 0.0) / o.total_amount if o.total_amount else 0.0
            row = sold.setdefault(l.ingredient_id, dict(kg=0.0, revenue=0.0, profit=0.0, sales=set()))
            row['kg'] += kg
            row['revenue'] += (l.subtotal or 0.0) - share
            row['profit'] += (l.subtotal or 0.0) - share - kg * (l.unit_cost_per_kg or 0.0)
            row['sales'].add(l.order_id)
        stock = {r.ingredient_id: r.quantity_kg or 0.0 for r in LocationStock.query.filter_by(location_id=location.id).all()}
        last_sold = dict(db.session.query(OrderLine.ingredient_id, db.func.max(OrderHeader.created_at))
                         .join(OrderHeader, OrderHeader.id == OrderLine.order_id)
                         .filter(OrderHeader.location_id == location.id, OrderHeader.status == 'COMPLETED')
                         .group_by(OrderLine.ingredient_id).all())
        rows = []
        for ing in FeedIngredient.query.filter(FeedIngredient.is_active.isnot(False)).all():
            s, kg_stock = sold.get(ing.id), stock.get(ing.id, 0.0)
            if not s and kg_stock <= 0:
                continue
            grp = group_for(ing.category)
            daily = (s['kg'] / days) if s else 0.0
            rows.append(dict(id=ing.id, name=ing.name, group=grp, group_label=GROUP_LABEL[grp],
                             sold_kg=round(s['kg'], 1) if s else 0.0, revenue=round(s['revenue'], 2) if s else 0.0,
                             profit=round(s['profit'], 2) if s else 0.0, sales=len(s['sales']) if s else 0,
                             margin_pct=round(100 * s['profit'] / s['revenue'], 1) if s and s['revenue'] else None,
                             stock_kg=round(kg_stock, 1), days_cover=round(kg_stock / daily, 1) if daily > 0 else None,
                             last_sold=(last_sold[ing.id] + rcpt.LOCAL_OFFSET).strftime('%d/%m/%Y') if last_sold.get(ing.id) else '',
                             slow=bool(kg_stock > 0 and not s)))
        for key in ('sold_kg', 'revenue', 'profit'):
            for i, r in enumerate(sorted(rows, key=lambda r: -r[key]), 1):
                r['rank_' + key] = i if r[key] > 0 else None
        rows.sort(key=lambda r: (GROUP_RANK[r['group']], -r['sold_kg'], r['name'].lower()))
        return jsonify(status='success', days=days, location=location.name, rows=rows,
                       note='Profit is after discounts and uses the stock cost at the time of sale. Refunds are not deducted.')
    except Exception as e:
        return error_response(e)


@receipts_bp.route('/api/insights/customers', methods=['GET'])
@roles_required(*OWNER_ROLES)
def customer_insights():
    try:
        location = resolve_location(request.args.get('location_id'))
        days, since = _window(request.args.get('days', 90))
        orders = OrderHeader.query.filter(OrderHeader.location_id == location.id, OrderHeader.status == 'COMPLETED').all()
        recent = [o for o in orders if o.created_at and o.created_at >= since]
        by_cust = {}
        for o in orders:
            if o.customer_id:
                by_cust.setdefault(o.customer_id, []).append(o)
        names = {c.id: c for c in Customer.query.filter(Customer.id.in_(list(by_cust) or [0])).all()}
        now, top, lapsed = datetime.utcnow(), [], []
        for cid, os_ in by_cust.items():
            c = names.get(cid)
            if not c:
                continue
            os_.sort(key=lambda o: o.created_at)
            due = lambda o: (o.total_amount or 0) - (o.discount_amount or 0)
            spent = sum(due(o) for o in os_ if o.created_at >= since)
            visits = sum(1 for o in os_ if o.created_at >= since)
            since_last = (now - os_[-1].created_at).days
            gaps = [(b.created_at - a.created_at).days for a, b in zip(os_, os_[1:])]
            avg_gap = sum(gaps) / len(gaps) if gaps else None
            rec = dict(id=cid, name=c.name, phone=c.phone or '', spent=round(spent, 2), visits=visits, days_since_last=since_last,
                       last_purchase=(os_[-1].created_at + rcpt.LOCAL_OFFSET).strftime('%d/%m/%Y'), balance=round(c.current_balance or 0, 2),
                       avg_gap_days=round(avg_gap, 1) if avg_gap is not None else None)
            if visits:
                top.append(rec)
            if len(os_) >= 3 and avg_gap is not None and since_last > max(30, 2 * avg_gap):
                lapsed.append(rec)
        top.sort(key=lambda r: -r['spent'])
        lapsed.sort(key=lambda r: -r['days_since_last'])
        disc = {}
        for o in recent:
            if o.discount_amount:
                d = disc.setdefault(o.discount_reason or 'NO REASON (old sale)', dict(amount=0.0, sales=0))
                d['amount'] += o.discount_amount
                d['sales'] += 1
        linked = sum(1 for o in recent if o.customer_id)
        return jsonify(status='success', days=days, sales=len(recent), linked_sales=linked,
                       linked_pct=round(100 * linked / len(recent), 1) if recent else None,
                       top=top[:20], lapsed=lapsed[:30],
                       discounts=[dict(reason=k, amount=round(v['amount'], 2), sales=v['sales'])
                                  for k, v in sorted(disc.items(), key=lambda kv: -kv[1]['amount'])])
    except Exception as e:
        return error_response(e)
