from services.idempotency import idempotent
import math
import uuid
from flask import Blueprint, jsonify, request, g
from sqlalchemy import func
from routes.auth import roles_required
from services.models import db, Supplier, SupplierTxn
payables_bp = Blueprint('payables', __name__)
# Chart of accounts used for purchases and supplier payments.
ACC_INVENTORY = '1200'
ACC_CASH = '1000'
ACC_PAYABLE = '2000'
def _payment_account(method):
    return {'CASH': '1000', 'MPESA': '1010', 'BANK': '1020'}.get(method, ACC_CASH)
def _balances():
    bought = dict(db.session.query(SupplierTxn.supplier_id, func.sum(SupplierTxn.amount))
                  .filter(SupplierTxn.kind == 'PURCHASE').group_by(SupplierTxn.supplier_id).all())
    paid = dict(db.session.query(SupplierTxn.supplier_id, func.sum(SupplierTxn.amount))
                .filter(SupplierTxn.kind == 'PAYMENT').group_by(SupplierTxn.supplier_id).all())
    out = {}
    for sid in set(bought) | set(paid):
        out[sid] = round((bought.get(sid) or 0) - (paid.get(sid) or 0), 2)
    return out
def record_purchase(supplier_id, po_id, ref, amount, method, username):
    """Books a receipt: inventory in, and either cash out or a supplier payable."""
    from services.ledger_service import post_gl_entry
    amount = round(amount, 2)
    if amount <= 0:
        return
    post_gl_entry(ref, ACC_INVENTORY, amount, 0.0, 'PURCHASE', po_id)
    if method == 'ON_ACCOUNT':
        post_gl_entry(ref, ACC_PAYABLE, 0.0, amount, 'PURCHASE', po_id)
        db.session.add(SupplierTxn(supplier_id=supplier_id, po_id=po_id, ref=ref,
                                   kind='PURCHASE', amount=amount, method='ON_ACCOUNT',
                                   created_by=username))
    else:
        post_gl_entry(ref, _payment_account(method), 0.0, amount, 'PURCHASE', po_id)
@payables_bp.route('/api/admin/payables')
@roles_required('admin', 'accountant')
def payables_list():
    balances = _balances()
    names = {s.id: s.name for s in Supplier.query.all()}
    rows = [{'supplier_id': sid, 'name': names.get(sid, 'Unknown'), 'owed': owed}
            for sid, owed in balances.items()]
    rows.sort(key=lambda r: -r['owed'])
    total = round(sum(r['owed'] for r in rows if r['owed'] > 0), 2)
    return jsonify({'total_owed': total, 'suppliers': rows})
@payables_bp.route('/api/admin/payables/<int:supplier_id>')
@roles_required('admin', 'accountant')
def payables_history(supplier_id):
    txns = (SupplierTxn.query.filter_by(supplier_id=supplier_id)
            .order_by(SupplierTxn.id.desc()).limit(50).all())
    return jsonify([{'ref': t.ref, 'kind': t.kind, 'amount': t.amount, 'method': t.method,
                     'note': t.note or '', 'by': t.created_by or '',
                     'date': t.created_at.strftime('%Y-%m-%d %H:%M') if t.created_at else ''}
                    for t in txns])
@payables_bp.route('/api/admin/payables/<int:supplier_id>/pay', methods=['POST'])
@roles_required('admin', 'accountant')
@idempotent('supplier-pay')
def pay_supplier(supplier_id):
    data = request.get_json(silent=True) or {}
    try:
        amount = round(float(data.get('amount')), 2)
    except (TypeError, ValueError):
        return jsonify(status='error', message='Enter a valid amount.'), 400
    method = str(data.get('method') or 'CASH').upper()
    note = str(data.get('note') or '').strip()[:200]
    if not math.isfinite(amount) or amount <= 0:
        return jsonify(status='error', message='Amount must be above 0.'), 400
    if method not in ('CASH', 'MPESA', 'BANK'):
        return jsonify(status='error', message='Invalid payment method.'), 400
    if any(ch in note for ch in '<>'):
        return jsonify(status='error', message='Invalid note.'), 400
    if not db.session.get(Supplier, supplier_id):
        return jsonify(status='error', message='Supplier not found.'), 404
    owed = _balances().get(supplier_id, 0.0)
    if amount > owed + 0.01:
        return jsonify(status='error',
                       message='Payment of KSh %.2f is more than the KSh %.2f owed.' % (amount, owed)), 400
    ref = 'SPAY-' + uuid.uuid4().hex[:6].upper()
    try:
        from services.ledger_service import post_gl_entry
        post_gl_entry(ref, ACC_PAYABLE, amount, 0.0, 'SUPPLIER_PAYMENT', supplier_id)
        post_gl_entry(ref, _payment_account(method), 0.0, amount, 'SUPPLIER_PAYMENT', supplier_id)
        db.session.add(SupplierTxn(supplier_id=supplier_id, ref=ref, kind='PAYMENT',
                                   amount=amount, method=method, note=note,
                                   created_by=g.user.username))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify(status='error', message='Could not record the payment: ' + str(e)), 400
    return jsonify(status='success', ref=ref, owed=round(owed - amount, 2))
