import os
import tempfile
import unittest
import uuid

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-deployment')
from app import create_app
from services.db import db
from services.models import Customer, FeedIngredient, Location, LocationStock, TillSession, User


class Oct16FixTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix='emining-oct16-')
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only-secret-key-not-for-deployment',
                               'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + os.path.join(self.tempdir.name, 't.db')})
        self.context = self.app.app_context()
        self.context.push()
        self.branch = Location(name='Retail Outlet', code='RET-01', location_type='BRANCH_STORE')
        db.session.add(self.branch)
        db.session.flush()
        self.users = {}
        for role in ('admin', 'accountant', 'sales', 'warehouse', 'factory'):
            u = User(username=role, role=role, location='RET-01')
            u.set_password('1234')
            db.session.add(u)
            db.session.flush()
            u.location_id = self.branch.id
            self.users[role] = u.id
            db.session.add(TillSession(location_id=self.branch.id, cashier_name=role, opening_cash=0.0,
                                       expected_cash=0.0, status='OPEN', open_key=f'{self.branch.id}:{role}'))
        self.mash = FeedIngredient(name='Layers Mash', category='Finished Feed', cost_per_kg=30.0, retail_price_per_kg=40.0)
        self.empty = FeedIngredient(name='Dairy Meal', category='Finished Feed', cost_per_kg=30.0, retail_price_per_kg=40.0)
        self.maize = FeedIngredient(name='Maize', category='Raw - Energy', cost_per_kg=30.0, retail_price_per_kg=35.0)
        self.mill = FeedIngredient(name='Milling', category='Milling Service', cost_per_kg=0.0, retail_price_per_kg=5.0)
        db.session.add_all([self.mash, self.empty, self.maize, self.mill])
        db.session.flush()
        db.session.add(LocationStock(location_id=self.branch.id, ingredient_id=self.mash.id, quantity_kg=50.0, unit_cost_per_kg=30.0))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.tempdir.cleanup()

    def client(self, role):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s['user_id'] = self.users[role]
        return c

    def test_every_role_can_settle_debt(self):
        for role in self.users:
            cust = Customer(name='Debtor ' + role, current_balance=100.0, credit_limit=500.0, location_id=self.branch.id)
            db.session.add(cust)
            db.session.commit()
            r = self.client(role).post(f'/api/customers/{cust.id}/repay', headers={'Idempotency-Key': str(uuid.uuid4())},
                                       json={'amount': 40, 'payment_method': 'CASH', 'location_id': self.branch.id})
            self.assertEqual(r.status_code, 200, (role, r.get_data(as_text=True)))
            self.assertEqual(r.get_json()['new_balance'], 60.0)

    def test_customer_list_never_returns_nulls(self):
        db.session.add(Customer(name='Legacy', phone=None, current_balance=None, credit_limit=None, location_id=self.branch.id))
        db.session.commit()
        for url in ('/api/customers?all=1', f'/api/customers?location_id={self.branch.id}'):
            rows = self.client('admin').get(url).get_json()
            row = [c for c in rows if c['name'] == 'Legacy'][0]
            self.assertEqual((row['balance'], row['credit_limit'], row['phone']), (0.0, 0.0, ''))

    def test_all_categories_lists_every_item_including_unstocked_and_raw(self):
        names = {i['name'] for i in self.client('sales').get(f'/api/products/search?location_id={self.branch.id}').get_json()}
        self.assertTrue({'Layers Mash', 'Dairy Meal', 'Maize', 'Milling'} <= names)

    def test_category_lists_all_its_items_and_search_matches_words(self):
        c = self.client('sales')
        fin = c.get(f'/api/products/search?category=Finished%20Feed&location_id={self.branch.id}').get_json()
        self.assertEqual({i['name'] for i in fin}, {'Layers Mash', 'Dairy Meal'})
        hit = c.get(f'/api/products/search?q=mash%20layers&location_id={self.branch.id}').get_json()
        self.assertEqual([i['name'] for i in hit], ['Layers Mash'])
        by_cat = c.get(f'/api/products/search?q=raw&location_id={self.branch.id}').get_json()
        self.assertEqual([i['name'] for i in by_cat], ['Maize'])

    def test_categories_endpoint_is_dynamic(self):
        cats = self.client('sales').get('/api/products/categories').get_json()
        self.assertTrue({'Finished Feed', 'Milling Service', 'Raw - Energy'} <= set(cats))

    def test_ration_tab_is_on_dashboard_for_the_right_roles_only(self):
        html = self.client('admin').get('/').get_data(as_text=True)
        self.assertIn('id="matrix-tab"', html)
        btn = [l for l in html.splitlines() if "showTab('matrix-tab')" in l][0]
        self.assertIn('data-roles="admin,accountant,factory"', btn)
        self.assertEqual(self.client('sales').get('/api/formulate/requirements?species=x').status_code in (401, 403), True)


if __name__ == '__main__':
    unittest.main()


class LedgerTransactionTests(unittest.TestCase):
    def test_unbalanced_or_malformed_entries_are_refused(self):
        from services.ledger_service import post_gl_transaction
        app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only-secret-key-not-for-deployment',
                          'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:'})
        with app.app_context():
            for legs in ([('1000', 50, 0)], [('1000', 50, 0), ('3000', 0, 40)], [('1000', 50, 50), ('3000', 0, 0)],
                         [('1000', -5, 0), ('3000', 0, -5)]):
                with self.assertRaises(ValueError):
                    post_gl_transaction('T', legs, 'TEST', 1)
            post_gl_transaction('T', [('1000', 50, 0), ('3000', 0, 50)], 'TEST', 1)
            db.session.commit()
            from services.models import GeneralLedgerEntry
            self.assertEqual(GeneralLedgerEntry.query.filter_by(transaction_ref='T').count(), 2)
