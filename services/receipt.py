"""Receipts, check codes and customer statements, all built from SAVED sales (never from what was on the till screen)."""
import hmac
import hashlib
import base64
import io
import re
from datetime import timedelta

from flask import current_app
from sqlalchemy import func

from services.db import db
from services.models import (Customer, CustomerDebtEntry, CustomerPayment, FeedIngredient, Location, OrderHeader,
                             OrderLine, PaymentSplit, SalesRefund, TillSession)

LOCAL_OFFSET = timedelta(hours=3)
DISCOUNT_REASONS = ('BULK', 'LOYAL', 'PROMO', 'DAMAGED', 'STAFF', 'OTHER')
APPROVAL_ABOVE_PCT = 2.0     # discounts over this share of the bill need a named approver (admin approves themselves)


def kenya_phone(raw):
    """0712 345 678 / +254712345678 / 712345678 -> 254712345678, or '' when it is not a usable number."""
    digits = ''.join(ch for ch in str(raw or '') if ch.isdigit())
    if digits.startswith('254') and len(digits) == 12:
        return digits
    if digits.startswith('0') and len(digits) == 10:
        return '254' + digits[1:]
    if len(digits) == 9 and digits[0] in '17':
        return '254' + digits
    return ''


def line_kg(line):
    """Kg on an order line. Old lines (before qty_kg existed) are read from their unit label."""
    if line.qty_kg:
        return float(line.qty_kg)
    m = re.match(r'^\s*([\d.]+)\s*KG BAG', str(line.unit_type or ''), re.I)
    return float(line.qty_entered or 0) * (float(m.group(1)) if m else 1.0)


def check_code(ref, due, created_at):
    """Six characters only this server can produce for this receipt number, amount and time."""
    key = str(current_app.config.get('SECRET_KEY') or '').encode()
    msg = f"{ref}|{float(due):.2f}|{created_at:%Y%m%d%H%M%S}".encode()
    return base64.b32encode(hmac.new(key, msg, hashlib.sha256).digest()).decode()[:6]


def next_receipt(location_id):
    """Next gapless number for this outlet. Call inside the sale's transaction."""
    seq = (db.session.query(func.max(OrderHeader.receipt_seq)).filter(OrderHeader.location_id == location_id).scalar() or 0) + 1
    loc = db.session.get(Location, location_id)
    code = ''.join(ch for ch in (loc.code or 'R') if ch.isalnum()).upper()[:6] if loc else 'R'
    return seq, f"{code}-{seq:06d}"


def find_order(ref):
    ref = str(ref or '').strip().upper()
    return (OrderHeader.query.filter(db.func.upper(OrderHeader.receipt_no) == ref).first()
            or OrderHeader.query.filter(db.func.upper(OrderHeader.sale_id) == ref).first())


def build(order):
    """Everything printed on a receipt, taken from the saved rows."""
    loc = db.session.get(Location, order.location_id)
    cust = db.session.get(Customer, order.customer_id) if order.customer_id else None
    till = db.session.get(TillSession, order.till_session_id) if order.till_session_id else None
    ref = order.receipt_no or order.sale_id
    due = round((order.total_amount or 0.0) - (order.discount_amount or 0.0), 2)
    items = []
    for l in OrderLine.query.filter_by(order_id=order.id).order_by(OrderLine.id).all():
        ing = db.session.get(FeedIngredient, l.ingredient_id)
        kg = line_kg(l)
        items.append(dict(name=ing.name if ing else 'Item', kg=kg, pack=l.unit_type, qty=l.qty_entered or 0.0,
                          per_kg=round((l.subtotal or 0.0) / kg, 2) if kg else 0.0, amount=round(l.subtotal or 0.0, 2)))
    pays = [dict(method=p.payment_method, amount=round(p.amount or 0.0, 2), reference=p.reference or '')
            for p in PaymentSplit.query.filter_by(order_id=order.id).all()]
    if order.credit_amount:
        pays.append(dict(method='CREDIT', amount=round(order.credit_amount, 2), reference=''))
    refunded = round(db.session.query(func.coalesce(func.sum(SalesRefund.amount), 0.0))
                     .filter(SalesRefund.order_id == order.id).scalar() or 0.0, 2)
    return dict(
        outlet=loc.name if loc else 'Emining Feeds', outlet_phone=(loc.phone or '') if loc else '',
        width=(loc.receipt_width if loc and loc.receipt_width in (58, 80) else 80),
        ref=ref, sale_id=order.sale_id, when=(order.created_at + LOCAL_OFFSET) if order.created_at else None,
        customer=cust.name if cust else 'Walk-in customer', customer_phone=(cust.phone or '') if cust else '',
        cashier=till.cashier_name if till else '', items=items, subtotal=round(order.total_amount or 0.0, 2),
        discount=round(order.discount_amount or 0.0, 2), discount_reason=order.discount_reason or '',
        total=due, payments=pays, change=round(order.change_due or 0.0, 2),
        balance_owed=(round(order.balance_after, 2) if order.balance_after is not None and cust else None),
        credit_sale=bool(order.credit_amount), refunded=refunded,
        code=check_code(ref, due, order.created_at) if order.created_at else '')


def fmt_when(dt):
    return dt.strftime('%d/%m/%Y %H:%M') if dt else ''


def _font(size, bold=False):
    from PIL import ImageFont
    name = 'DejaVuSansMono-Bold.ttf' if bold else 'DejaVuSansMono.ttf'
    for base in ('/usr/share/fonts/truetype/dejavu/', '/usr/share/fonts/dejavu/', '/usr/share/fonts/TTF/'):
        try:
            return ImageFont.truetype(base + name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_png(d, scale=2):
    """Black-on-white strip. 80 mm paper = 576 px, 58 mm = 384 px, drawn at 2x so it stays sharp after WhatsApp
    compression. No grey and no hairlines, which compression destroys."""
    from PIL import Image, ImageDraw
    base_w = 576 if d['width'] == 80 else 384
    fs = 24 if d['width'] == 80 else 20
    W, pad = base_w * scale, 14 * scale
    body, bold, big = _font(fs * scale), _font(fs * scale, True), _font(int(fs * 1.4) * scale, True)
    canvas = Image.new('L', (W, 6000 * scale), 255)
    dr = ImageDraw.Draw(canvas)
    y = [pad]

    def width(txt, f):
        return dr.textlength(txt, font=f)

    def center(txt, f=None):
        f = f or body
        for ln in wrap(txt, f, W - 2 * pad):
            dr.text(((W - width(ln, f)) / 2, y[0]), ln, font=f, fill=0)
            y[0] += int(f.size * 1.3)

    def wrap(txt, f, room):
        out, cur = [], ''
        for word in str(txt).split():
            trial = (cur + ' ' + word).strip()
            if width(trial, f) <= room or not cur:
                cur = trial
            else:
                out.append(cur); cur = word
        return out + ([cur] if cur else [])

    def left(txt, f=None):
        f = f or body
        for ln in wrap(txt, f, W - 2 * pad):
            dr.text((pad, y[0]), ln, font=f, fill=0)
            y[0] += int(f.size * 1.3)

    def row(l, r, f=None):
        f = f or body
        rw = width(r, f)
        lines = wrap(l, f, W - 2 * pad - rw - 12 * scale) or ['']
        dr.text((W - pad - rw, y[0] + (len(lines) - 1) * int(f.size * 1.3)), r, font=f, fill=0)
        for ln in lines:
            dr.text((pad, y[0]), ln, font=f, fill=0)
            y[0] += int(f.size * 1.3)

    def rule():
        y[0] += 4 * scale
        dr.line((pad, y[0], W - pad, y[0]), fill=0, width=2 * scale)
        y[0] += 10 * scale

    money = lambda v: f"{v:,.2f}"
    center('EMINING FEEDS', big)
    center(d['outlet'], bold)
    if d['outlet_phone']:
        center('Tel ' + d['outlet_phone'])
    rule()
    row('Receipt', d['ref'], bold)
    row('Date', fmt_when(d['when']))
    row('Served by', d['cashier'] or '-')
    row('Customer', d['customer'])
    if d['customer_phone']:
        row('Phone', d['customer_phone'])
    rule()
    for it in d['items']:
        left(it['name'], bold)
        qty_txt = f"{it['kg']:g} KG x {money(it['per_kg'])}"
        row('  ' + qty_txt, money(it['amount']))
        if it['pack'] and 'BAG' in str(it['pack']).upper():
            left('  (' + str(it['pack']).title().replace('Kg', 'kg') + f" x {it['qty']:g})")
    rule()
    if d['discount'] > 0:
        row('Subtotal', money(d['subtotal']))
        row('Discount' + (f" ({d['discount_reason'].title()})" if d['discount_reason'] else ''), '-' + money(d['discount']))
    row('TOTAL KSh', money(d['total']), big)
    for p in d['payments']:
        row({'MPESA': 'M-Pesa', 'CASH': 'Cash', 'BANK': 'Bank', 'CREDIT': 'On credit'}.get(p['method'], p['method']),
            money(p['amount']))
    if d['change'] > 0:
        row('Change', money(d['change']))
    if d['balance_owed'] is not None and (d['credit_sale'] or d['balance_owed'] > 0):
        rule()
        row('BALANCE OWED KSh', money(d['balance_owed']), bold)
    if d['refunded'] > 0:
        rule()
        center('REFUNDED KSh ' + money(d['refunded']), big)
        center('see verification before accepting', body)
    rule()
    center('Check code', body)
    center(d['code'], big)
    center('Thank you for choosing Emining Feeds')
    y[0] += pad
    return canvas.crop((0, 0, W, y[0]))


def png_bytes(d, scale=2):
    buf = io.BytesIO()
    render_png(d, scale).save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def statement_pdf(customer, days=None):
    """A4 statement of everything this customer bought, paid and still owes."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from datetime import datetime
    since = datetime.utcnow() - timedelta(days=days) if days else None
    q = OrderHeader.query.filter(OrderHeader.customer_id == customer.id)
    if since:
        q = q.filter(OrderHeader.created_at >= since)
    orders = q.order_by(OrderHeader.created_at).all()
    rows, spent = [['Date', 'Receipt', 'Items', 'KSh', 'Paid', 'On credit']], 0.0
    for o in orders:
        d = build(o)
        spent += d['total']
        names = '; '.join(f"{i['name']} {i['kg']:g}kg" for i in d['items'])[:90]
        rows.append([fmt_when(d['when']), d['ref'], names, money_fmt(d['total']),
                     money_fmt(d['total'] - (o.credit_amount or 0)), money_fmt(o.credit_amount or 0)])
    pays = CustomerPayment.query.filter_by(customer_id=customer.id).order_by(CustomerPayment.id).all()
    prow = [['Date', 'Reference', 'Method', 'KSh']] + [[fmt_when(p.created_at + LOCAL_OFFSET), p.reference, p.payment_method, money_fmt(p.amount)]
                                                     for p in pays if not since or p.created_at >= since]
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    st = getSampleStyleSheet()
    tbl = lambda data, widths: _styled(Table(data, colWidths=widths, repeatRows=1))
    loc = db.session.get(Location, customer.location_id) if customer.location_id else None
    story = [Paragraph('Emining Feeds - Customer Statement', st['Title']),
             Paragraph(f"{customer.name}" + (f" &middot; {customer.phone}" if customer.phone else '') +
                       (f" &middot; {loc.name}" if loc else ''), st['Normal']),
             Paragraph('Period: ' + (f'last {days} days' if days else 'all time') +
                       f" &middot; printed {fmt_when(datetime.utcnow() + LOCAL_OFFSET)}", st['Normal']), Spacer(1, 10),
             Paragraph(f"<b>Balance owed now: KSh {money_fmt(customer.current_balance or 0)}</b> &middot; "
                       f"bought in period: KSh {money_fmt(spent)} ({len(orders)} purchases)", st['Normal']), Spacer(1, 10),
             Paragraph('Purchases', st['Heading3']),
             tbl(rows, [80, 70, 210, 55, 55, 55]) if len(rows) > 1 else Paragraph('No purchases in this period.', st['Normal']),
             Spacer(1, 10), Paragraph('Repayments', st['Heading3']),
             tbl(prow, [110, 150, 100, 70]) if len(prow) > 1 else Paragraph('No repayments recorded.', st['Normal'])]
    doc.build(story)
    return out.getvalue()


def money_fmt(v):
    return f"{float(v or 0):,.2f}"


def _styled(t):
    from reportlab.lib import colors
    from reportlab.platypus import TableStyle
    t.setStyle(TableStyle([('FONTSIZE', (0, 0), (-1, -1), 8), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e8edf3')),
                           ('GRID', (0, 0), (-1, -1), 0.4, colors.grey), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                           ('ALIGN', (3, 1), (-1, -1), 'RIGHT')]))
    return t
