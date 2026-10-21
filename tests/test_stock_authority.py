"""LocationStock is the only authoritative stock. FeedIngredient.stock_quantity_kg is a derived summary."""
import pathlib
import re
import unittest
import uuid

import test_pos as _tp
from services.db import db
from services.models import FeedIngredient, LocationStock

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Files allowed to touch the aggregate attribute, and why.
ALLOWED = {
    'services/inventory.py': 'the only writer: refresh_global_valuation() rebuilds it from LocationStock',
    'services/models.py':    'column definition',
    'services/db.py':        'one-off opening-balance migration into LocationStock',
    'migrate.py':            'one-off migration script',
    'seed.py':               'demo data loader',
    'integrity_check.py':    'read-only audit that compares it with LocationStock',
}


class StockAuthorityTests(unittest.TestCase):
    setUp = _tp.POSCheckoutTests.setUp
    tearDown = _tp.POSCheckoutTests.tearDown
    make_client = _tp.POSCheckoutTests.make_client
    payload = _tp.POSCheckoutTests.payload

    def test_no_new_code_reads_or_writes_the_aggregate_stock_field(self):
        offenders = []
        for path in ROOT.rglob('*.py'):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(('tests/', 'venv/', 'scripts/')) or rel in ALLOWED:
                continue
            if re.search(r'\.stock_quantity_kg\b', path.read_text(encoding='utf-8', errors='ignore')):
                offenders.append(rel)
        self.assertEqual(offenders, [], 'Read stock through services.inventory.stock_quantity(location_id, item_id).')

    def test_aggregate_always_equals_the_sum_of_outlet_balances(self):
        before = db.session.get(FeedIngredient, self.item.id).stock_quantity_kg
        response = self.client.post('/api/pos/checkout', json=self.payload(10, 400),
                                    headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200, response.json)
        db.session.rollback()
        db.session.expire_all()
        outlets = sum(r.quantity_kg for r in LocationStock.query.filter_by(ingredient_id=self.item.id))
        self.assertEqual(outlets, 90.0)
        self.assertAlmostEqual(db.session.get(FeedIngredient, self.item.id).stock_quantity_kg, outlets)
        self.assertEqual(before, 100.0)

    def test_stock_api_reports_the_outlet_balance_not_the_company_total(self):
        from services.models import Location
        other = Location(name='Other', code='RET-09', location_type='BRANCH_STORE')
        db.session.add(other)
        db.session.flush()
        db.session.add(LocationStock(location_id=other.id, ingredient_id=self.item.id, quantity_kg=500.0, unit_cost_per_kg=30.0))
        db.session.commit()
        row = next(r for r in self.client.get('/api/stock').json if r['id'] == self.item.id)
        self.assertEqual(row['stock_kg'], 100.0)      # this outlet only, not 600


if __name__ == '__main__':
    unittest.main()
