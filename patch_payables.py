import os, re, shutil
def load(p):
    return open(p, encoding='utf-8').read()
def save(p, s):
    if os.path.exists(p):
        shutil.copy(p, p + '.bak')
    open(p, 'w', encoding='utf-8', newline='\n').write(s)
PAY_PY = '''import math
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
        post_gl_entry(ref, ACC_CASH, 0.0, amount, 'PURCHASE', po_id)
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
        post_gl_entry(ref, ACC_CASH, 0.0, amount, 'SUPPLIER_PAYMENT', supplier_id)
        db.session.add(SupplierTxn(supplier_id=supplier_id, ref=ref, kind='PAYMENT',
                                   amount=amount, method=method, note=note,
                                   created_by=g.user.username))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify(status='error', message='Could not record the payment: ' + str(e)), 400
    return jsonify(status='success', ref=ref, owed=round(owed - amount, 2))
'''
MODEL = '''class SupplierTxn(db.Model):
    __tablename__ = 'supplier_txns'
    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, nullable=False)
    po_id = db.Column(db.Integer)
    ref = db.Column(db.String(50))
    kind = db.Column(db.String(20), nullable=False)   # PURCHASE (owed) or PAYMENT
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(20))
    note = db.Column(db.String(200))
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
'''
PAY_TAB = '''<div id="payables-tab" class="tab-content">
  <div class="card">
    <h3>Supplier Payables (admin / accountant)</h3>
    <p>Total owed to suppliers: <b>KSh <span id="pay-total">0.00</span></b></p>
    <table class="data-table">
      <thead><tr><th>Supplier</th><th>Owed (KSh)</th><th>Pay now</th><th>Method</th><th></th></tr></thead>
      <tbody id="pay-body"><tr><td colspan="5">Loading...</td></tr></tbody>
    </table>
    <div id="pay-msg" style="margin-top:10px"></div>
  </div>
</div>
<script>
async function loadPayables() {
  const body = document.getElementById('pay-body');
  const r = await fetch('/api/admin/payables', {credentials: 'same-origin'});
  if (!r.ok) { body.innerHTML = '<tr><td colspan="5">Cannot load payables.</td></tr>'; return; }
  const d = await r.json();
  document.getElementById('pay-total').textContent = d.total_owed.toFixed(2);
  const rows = d.suppliers.filter(s => s.owed > 0.004);
  body.innerHTML = rows.map(s =>
    `<tr><td>${retailEsc(s.name)}</td><td>${s.owed.toFixed(2)}</td>
     <td><input type="number" min="0" step="0.01" id="pay-amt-${s.supplier_id}" value="${s.owed}" style="width:120px;padding:6px"></td>
     <td><select id="pay-method-${s.supplier_id}" style="padding:6px"><option value="CASH">Cash</option><option value="MPESA">M-Pesa</option><option value="BANK">Bank</option></select></td>
     <td><button class="btn-sm btn-primary" onclick="paySupplier(${s.supplier_id})">Pay</button></td></tr>`
  ).join('') || '<tr><td colspan="5">No supplier debts.</td></tr>';
}
async function paySupplier(id) {
  const amount = parseFloat(document.getElementById('pay-amt-' + id).value);
  const method = document.getElementById('pay-method-' + id).value;
  const msg = document.getElementById('pay-msg');
  const r = await fetch('/api/admin/payables/' + id + '/pay', {
    method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({amount: amount, method: method})
  });
  const d = await r.json().catch(() => ({status: 'error', message: 'Server error.'}));
  msg.innerHTML = d.status === 'success'
    ? '<span style="color:green">Paid. Reference ' + retailEsc(d.ref) + '.</span>'
    : '<span style="color:red">' + retailEsc(d.message) + '</span>';
  if (d.status === 'success') loadPayables();
}
</script>
'''
BUTTON = '''  <button class="tab-btn" data-roles="admin,accountant" onclick="showTab('payables-tab'); loadPayables();">Supplier Payables</button>\n'''
# 1. new route file
if os.path.exists('routes/payables.py'):
    print('SKIPPED routes/payables.py - already exists')
else:
    save('routes/payables.py', PAY_PY)
    print('CREATED routes/payables.py')
# 2. model
m = load('services/models.py')
if 'class SupplierTxn' in m:
    print('SKIPPED services/models.py - already has SupplierTxn')
else:
    save('services/models.py', m.rstrip('\n') + '\n\n\n' + MODEL)
    print('PATCHED services/models.py')
# 3. tab file
if os.path.exists('templates/tabs/payables_tab.html'):
    print('SKIPPED payables_tab.html - already exists')
else:
    save('templates/tabs/payables_tab.html', PAY_TAB)
    print('CREATED templates/tabs/payables_tab.html')
# 4. app.py registration
a = load('app.py')
mi = re.search(r'^([ \t]*)from routes\.retail import retail_bp[ \t]*$', a, re.M)
mr = re.search(r'^([ \t]*)app\.register_blueprint\(retail_bp\)[ \t]*$', a, re.M)
if 'payables_bp' in a:
    print('SKIPPED app.py - already registered')
elif not mi or not mr or mr.end() < mi.end():
    print('SKIPPED app.py - retail import/register lines not found as expected')
else:
    a = a[:mr.end()] + '\n' + mr.group(1) + 'app.register_blueprint(payables_bp)' + a[mr.end():]
    a = a[:mi.end()] + '\n' + mi.group(1) + 'from routes.payables import payables_bp' + a[mi.end():]
    save('app.py', a)
    print('PATCHED app.py')
# 5. dashboard button + include
d = load('templates/dashboard.html')
mb = re.search(r"^.*showTab\('pricing-tab'\).*\n", d, re.M)
inc = '{% include "tabs/retail_tab.html" %}'
if 'payables-tab' in d:
    print('SKIPPED dashboard.html - already present')
elif not mb or d.count(inc) != 1:
    print('SKIPPED dashboard.html - pricing button or retail include not found exactly once')
else:
    d = d.replace(mb.group(0), mb.group(0) + BUTTON)
    d = d.replace(inc, inc + '\n{% include "tabs/payables_tab.html" %}')
    save('templates/dashboard.html', d)
    print('PATCHED dashboard.html')
