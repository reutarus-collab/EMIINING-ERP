import os
import tempfile
import unittest
import uuid

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-deployment')
from app import create_app
from services.db import db
from services.models import FeedIngredient, ItemPrice, Location, LocationStock, TillSession, User
from services.uom import normalise_rule


class CountedUnitRules(unittest.TestCase):
    def test_bale_and_tin_can_be_fixed_or_weighed(self):
        self.assertEqual(normalise_rule('BALE', 'VARIABLE', None), ('BALE', 'VARIABLE', None))
        self.assertEqual(normalise_rule('TIN', 'FIXED', 20), ('TIN', 'FIXED', 20.0))

    def test_packet_may_weigh_under_one_kg(self):
        self.assertEqual(normalise_rule('PACKET', 'FIXED', 0.5), ('PACKET', 'FIXED', 0.5))

    def test_bale_cannot_weigh_one_kg_or_less(self):
        for bad in (1, 0.5, 0, None):
            with self.assertRaises(ValueError):
                normalise_rule('BALE', 'FIXED', bad)

    def test_packet_cannot_be_zero(self):
        with self.assertRaises(ValueError):
            normalise_rule('PACKET', 'FIXED', 0)

    def test_unknown_unit_is_rejected(self):
        with self.assertRaises(ValueError):
            normalise_rule('SACK', 'FIXED', 20)

    def test_kg_and_ton_unchanged(self):
        self.assertEqual(normalise_rule('KG', None, None), ('KG', 'FIXED', 1.0))
        self.assertEqual(normalise_rule('TON', None, None), ('TON', 'FIXED', 1000.0))


class HayBaleSaleTests(unittest.TestCase):
    """Hay: bought by the bale (weighed), sold by the bale at a set price, stock always in kg."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='emining-units-')
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only-secret-key-not-for-deployment',
                               'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + os.path.join(self.tmp.name, 't.db')})
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.loc = Location(name='Retail Outlet', code='RET-01', location_type='BRANCH_STORE')
        admin = User(username='boss', role='admin', location='RET-01')
        admin.set_password('7351')
        db.session.add_all([self.loc, admin])
        db.session.flush()
        admin.location_id = self.loc.id
        # 9 bales bought for KSh 3,000 -> KSh 333.33 a bale; assume a 20 kg standard bale -> 16.67 per kg
        self.hay = FeedIngredient(name='Hay Bale', category='Finished Feed', cost_per_kg=16.67,
                                  bag_size_kg=20.0, purchase_uom='BALE', conversion_type='VARIABLE',
                                  conversion_factor=None, stock_uom='KG')
        db.session.add(self.hay)
        db.session.flush()
        db.session.add_all([
            LocationStock(location_id=self.loc.id, ingredient_id=self.hay.id, quantity_kg=180.0, unit_cost_per_kg=16.67),
            TillSession(location_id=self.loc.id, cashier_name='boss', opening_cash=0.0, expected_cash=0.0,
                        status='OPEN', open_key=f'{self.loc.id}:boss')])
        db.session.commit()
        self.admin_id, self.hay_id, self.loc_id = admin.id, self.hay.id, self.loc.id
        self.client = self.app.test_client()
        with self.client.session_transaction() as s:
            s['user_id'] = self.admin_id

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.ctx.pop()
        self.tmp.cleanup()

    def _post(self, url, body):
        return self.client.post(url, json=body, headers={'Idempotency-Key': str(uuid.uuid4())})

    def test_bale_price_below_cost_is_refused(self):
        r = self._post('/api/admin/packs', {'id': self.hay_id, 'packs': [{'pack_kg': 20, 'price': 300}]})
        self.assertEqual(r.status_code, 400, r.json)

    def test_bale_is_sold_by_the_bale_and_deducts_kg(self):
        r = self._post('/api/admin/packs', {'id': self.hay_id, 'packs': [{'pack_kg': 20, 'price': 370}]})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(ItemPrice.query.filter_by(ingredient_id=self.hay_id).one().pack_kg, 20.0)
        sale = self._post('/api/pos/checkout', {
            'location_id': self.loc_id,
            'cart': [{'ingredient_id': self.hay_id, 'unit_type': 'BAG', 'qty': 2, 'bag_size_kg': 20}],
            'payments': [{'payment_method': 'CASH', 'amount': 740}]})
        self.assertEqual(sale.status_code, 200, sale.json)
        self.assertEqual(sale.json['data']['total_amount'], 740.0)
        stock = LocationStock.query.filter_by(location_id=self.loc_id, ingredient_id=self.hay_id).one()
        self.assertAlmostEqual(stock.quantity_kg, 140.0)

    def test_item_unit_endpoint_accepts_bale(self):
        r = self._post(f'/api/po/inventory/{self.hay_id}/uom', {'purchase_uom': 'BALE', 'conversion_type': 'VARIABLE'})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json['purchase_uom'], 'BALE')


if __name__ == '__main__':
    unittest.main()
