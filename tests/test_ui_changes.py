import os, tempfile, unittest
from datetime import datetime
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-deployment')
from app import create_app
from services.db import db
from services.models import Customer, FeedIngredient, Location, LocationStock, OrderHeader, SalesRefund, User


class UIChangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'k' * 20,
                               'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + os.path.join(self.tmp.name, 't.db')})
        self.ctx = self.app.app_context(); self.ctx.push()
        self.a = Location(name='A', code='A1', location_type='BRANCH_STORE')
        self.b = Location(name='B', code='B1', location_type='BRANCH_STORE')
        db.session.add_all([self.a, self.b]); db.session.flush()
        admin = User(username='owner', role='admin', location='admin'); admin.set_password('1234')
        cashier = User(username='c', role='sales', location='A1'); cashier.set_password('4321')
        db.session.add_all([admin, cashier]); db.session.flush()
        cashier.location_id = self.a.id
        self.feed = FeedIngredient(name='Layer Mash', category='Finished Feed', retail_price_per_kg=65)
        self.raw = FeedIngredient(name='Maize', category='Raw - Energy', retail_price_per_kg=40)
        self.other = FeedIngredient(name='Pig Finisher', category='Finished Feed', retail_price_per_kg=70)
        self.mill = FeedIngredient(name='Milling', category='Milling Service', retail_price_per_kg=5)
        db.session.add_all([self.feed, self.raw, self.other, self.mill]); db.session.flush()
        for it in (self.feed, self.raw):      # outlet A carries feed + maize; Pig Finisher only at B
            db.session.add(LocationStock(location_id=self.a.id, ingredient_id=it.id, quantity_kg=50, unit_cost_per_kg=1))
        db.session.add(LocationStock(location_id=self.b.id, ingredient_id=self.other.id, quantity_kg=50, unit_cost_per_kg=1))
        self.cust = Customer(name='Old Name', phone='1', location_id=self.a.id)
        db.session.add(self.cust); db.session.commit()
        self.admin_id, self.cashier_id = admin.id, cashier.id

    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.ctx.pop(); self.tmp.cleanup()

    def client(self, uid):
        c = self.app.test_client()
        with c.session_transaction() as s: s['user_id'] = uid
        return c

    def names(self, c, qs=''):
        r = c.get(f'/api/products/search?location_id={self.a.id}{qs}')
        self.assertEqual(r.status_code, 200, r.json)
        return sorted(i['name'] for i in r.json if i['category'] != 'Milling Service' or i['name'] == 'Milling')

    def test_pos_hides_raw_and_other_outlet_items(self):
        c = self.client(self.cashier_id)
        self.assertEqual(self.names(c), ['Layer Mash', 'Milling'])
        self.assertEqual(self.names(c, '&category=Raw - Energy'), ['Maize'])
        self.assertEqual(self.names(c, '&include_raw=1'), ['Layer Mash', 'Maize', 'Milling'])

    def test_customer_edit_permissions(self):
        owner = self.client(self.admin_id)
        r = owner.put(f'/api/customers/{self.cust.id}', json={'name': 'New Name', 'phone': '0700', 'outlet_id': self.b.id})
        self.assertEqual(r.status_code, 200, r.json)
        db.session.expire_all()
        self.assertEqual((self.cust.name, self.cust.phone, self.cust.location_id), ('New Name', '0700', self.b.id))
        cashier = self.client(self.cashier_id)            # customer now belongs to outlet B
        self.assertEqual(cashier.put(f'/api/customers/{self.cust.id}', json={'name': 'x'}).status_code, 403)
        self.assertEqual(owner.put(f'/api/customers/{self.cust.id}', json={'name': '<b>'}).status_code, 400)

    def test_sales_summary_buckets(self):
        now = datetime.utcnow()
        db.session.add_all([
            OrderHeader(sale_id='S1', total_amount=1000, credit_amount=200, status='COMPLETED', created_at=now, location_id=self.a.id),
            OrderHeader(sale_id='S2', total_amount=500, status='COMPLETED', created_at=now, location_id=self.b.id),
            OrderHeader(sale_id='S3', total_amount=300, status='COMPLETED', created_at=now.replace(year=now.year - 1), location_id=self.a.id)])
        db.session.commit()
        r = self.client(self.cashier_id).get('/api/sales-history/summary')
        p = r.json['periods']
        self.assertEqual(p['today']['gross_sales'], 1000)
        self.assertEqual(p['today']['credit_given'], 200)
        self.assertEqual(p['this_year']['gross_sales'], 1000)   # last year's sale and outlet B excluded
        self.assertEqual(p['yesterday']['orders'], 0)

if __name__ == '__main__':
    unittest.main()
