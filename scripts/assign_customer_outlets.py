"""Put existing customers into an outlet. Dry run unless --apply.

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
