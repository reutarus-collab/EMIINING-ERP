# Run from ~/EMIINING-ERP:  python add_customer_outlets.py
# Each outlet keeps its own customers. A customer belongs to ONE outlet.
import os, sys

def read(p): return open(p, encoding='utf-8').read()
def write(p, s): open(p, 'w', encoding='utf-8').write(s)

if 'location_id = db.Column(db.Integer, db.ForeignKey(\'locations.id\'))\n\nclass CustomerPayment' in read('services/models.py'):
    sys.exit('Already applied.')

def rep(path, old, new):
    s = read(path)
    if s.count(old) != 1:
        sys.exit('FAILED: expected text not found exactly once in %s:\n%s' % (path, old[:80]))
    write(path, s.replace(old, new, 1))

# 1. the column (the app adds it to the live database automatically on restart)
rep('services/models.py',
    "    credit_limit = db.Column(db.Float, default=0.0)\n\nclass CustomerPayment",
    "    credit_limit = db.Column(db.Float, default=0.0)\n"
    "    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))\n\nclass CustomerPayment")
rep('services/db.py',
    "        'app_users': {'location_id': 'INTEGER'},",
    "        'app_users': {'location_id': 'INTEGER'},\n        'customers': {'location_id': 'INTEGER'},")

# 2. customer list + new customers are per outlet
rep('routes/pos.py',
    "        cust = Customer(name=name, phone=phone, location=loc, customer_type=ctype, credit_limit=limit)",
    "        try:\n"
    "            outlet = resolve_location(data.get('location_id'))\n"
    "        except ValueError as exc:\n"
    "            return jsonify({'status': 'error', 'message': str(exc)}), 400\n"
    "        cust = Customer(name=name, phone=phone, location=loc, customer_type=ctype,\n"
    "                        credit_limit=limit, location_id=outlet.id)")
rep('routes/pos.py',
    "    return jsonify([{'id': c.id, 'name': c.name, 'phone': c.phone, 'location': c.location or 'Unknown', 'type': c.customer_type, 'balance': c.current_balance, 'credit_limit': c.credit_limit} for c in Customer.query.all()])",
    "    try:\n"
    "        outlet = resolve_location(request.args.get('location_id'))\n"
    "    except ValueError:\n"
    "        return jsonify([])\n"
    "    q = Customer.query.filter(Customer.location_id == outlet.id)\n"
    "    if g.user.role in ('admin', 'accountant'):\n"
    "        # owners also see customers not yet assigned to an outlet, so none get lost\n"
    "        q = Customer.query.filter(db.or_(Customer.location_id == outlet.id, Customer.location_id.is_(None)))\n"
    "    return jsonify([{'id': c.id, 'name': c.name, 'phone': c.phone, 'location': c.location or 'Unknown',\n"
    "                     'type': c.customer_type, 'balance': c.current_balance, 'credit_limit': c.credit_limit,\n"
    "                     'outlet_id': c.location_id} for c in q.order_by(Customer.name).all()])")

# 3. a customer cannot be used or repaid at another outlet
rep('routes/pos.py',
    "        cust = db.session.get(Customer, customer_id)\n        if not cust:\n            return jsonify({'status': 'error', 'message': 'Customer not found.'}), 404\n        owed = round(",
    "        cust = db.session.get(Customer, customer_id)\n        if not cust:\n            return jsonify({'status': 'error', 'message': 'Customer not found.'}), 404\n"
    "        if cust.location_id is not None and cust.location_id != location.id:\n"
    "            return jsonify({'status': 'error', 'message': 'This customer belongs to another outlet.'}), 400\n"
    "        owed = round(")
rep('services/pos_service.py',
    "    if credit_amount > 0:\n        if not customer_id:",
    "    if customer_id:\n"
    "        sale_customer = Customer.query.get(customer_id)\n"
    "        if not sale_customer:\n"
    "            raise Exception('Customer not found.')\n"
    "        if sale_customer.location_id is not None and sale_customer.location_id != location_id:\n"
    "            raise Exception('This customer belongs to another outlet. Register them at this outlet first.')\n"
    "\n    if credit_amount > 0:\n        if not customer_id:")

# 4. POS screen: load and save customers for the selected outlet
rep('static/js/pos.js',
    "async function loadCustomers() {\n  const res = await fetch('/api/customers');\n  customersCache = await res.json();",
    "async function loadCustomers() {\n"
    "  const q = window.activeLocationId ? '?location_id=' + encodeURIComponent(window.activeLocationId) : '';\n"
    "  const res = await fetch('/api/customers' + q);\n"
    "  customersCache = await res.json();\n"
    "  if (!Array.isArray(customersCache)) customersCache = [];")
rep('static/js/pos.js',
    "body: JSON.stringify({name: name, phone: phone, location: location, credit_limit: limit})",
    "body: JSON.stringify({name: name, phone: phone, location: location, credit_limit: limit, location_id: window.activeLocationId})")
rep('static/js/pos.js',
    "async function changePosLocation() {\n  activeLocationId = document.getElementById('pos-location').value;\n  window.activeLocationId = activeLocationId;\n  await refreshTill();\n  searchProducts();",
    "async function changePosLocation() {\n  activeLocationId = document.getElementById('pos-location').value;\n  window.activeLocationId = activeLocationId;\n  await refreshTill();\n  searchProducts();\n  if (typeof loadCustomers === 'function') loadCustomers();")
rep('static/js/pos.js',
    "    window.activeLocationId = activeLocationId;\n    await refreshTill();\n    if (typeof loadFactoryDropdowns",
    "    window.activeLocationId = activeLocationId;\n    await refreshTill();\n    if (typeof loadCustomers === 'function') loadCustomers();\n    if (typeof loadFactoryDropdowns")

# 5. one-time tool to put existing customers into their outlet
write('scripts/assign_customer_outlets.py', '''"""Put existing customers into an outlet. Dry run unless --apply.

  python scripts/assign_customer_outlets.py                 # preview
  python scripts/assign_customer_outlets.py --apply
  python scripts/assign_customer_outlets.py --default MOG-01 --apply

Rule: a customer with sales is assigned to the outlet where they bought most often.
A customer with no sales goes to the --default outlet (default: the factory, FAC-01).
Customers already assigned are never changed.
"""
import os, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import app
from services.db import db
from services.models import Customer, Location, OrderHeader

apply = '--apply' in sys.argv
default_code = 'FAC-01'
if '--default' in sys.argv:
    default_code = sys.argv[sys.argv.index('--default') + 1]

with app.app_context():
    default = Location.query.filter_by(code=default_code).first()
    if not default:
        sys.exit('No outlet with code %s. Outlets: %s' % (default_code, ', '.join(l.code or '?' for l in Location.query.all())))
    names = {l.id: l.name for l in Location.query.all()}
    todo = Customer.query.filter(Customer.location_id.is_(None)).order_by(Customer.id).all()
    if not todo:
        print('Every customer already has an outlet.')
    for c in todo:
        sold = Counter(o.location_id for o in OrderHeader.query.filter_by(customer_id=c.id).all() if o.location_id)
        if sold:
            target, why = sold.most_common(1)[0][0], 'bought at %s' % ', '.join('%s x%d' % (names.get(k, k), n) for k, n in sold.items())
            if len(sold) > 1:
                why += '  <-- bought at several outlets, check this one'
        else:
            target, why = default.id, 'no sales yet, using default outlet'
        print('  %-28s -> %-24s (%s, owes KSh %.2f)' % (c.name, names.get(target), why, c.current_balance or 0))
        if apply:
            c.location_id = target
    if apply:
        db.session.commit()
        print('Saved.')
    elif todo:
        print('Dry run only. Re-run with --apply to save.')
''')
print('Applied. Reload the web app (the new column is added on restart), then run scripts/assign_customer_outlets.py')