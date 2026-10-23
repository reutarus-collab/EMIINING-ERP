import os, tempfile, unittest, uuid
from datetime import datetime, timedelta

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-deployment')
from app import create_app
from services.db import db
from services.models import (Customer, CustomerDebtEntry, CustomerMergeLog, FeedIngredient, GeneralLedgerEntry,
                             InternalMillingRun, Location, LocationStock, OrderHeader, StockMovement, User)


def key():
    return {'Idempotency-Key': str(uuid.uuid4())}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'k' * 20,
                               'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + os.path.join(self.tmp.name, 't.db')})
        self.ctx = self.app.app_context(); self.ctx.push()
        self.loc = Location(name='Factory', code='FAC-01', location_type='FACTORY')
        db.session.add(self.loc); db.session.flush()
        self.admin = User(username='boss', role='admin', location='FAC-01', location_id=self.loc.id)
        self.admin.set_password('1234')
        self.fac = User(username='miller', role='factory', location='FAC-01', location_id=self.loc.id)
        self.fac.set_password('2345')
        db.session.add_all([self.admin, self.fac]); db.session.commit()
        self.client = self.app.test_client()
        self.as_user(self.admin)

    def as_user(self, user):
        with self.client.session_transaction() as s: s['user_id'] = user.id

    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.ctx.pop(); self.tmp.cleanup()

    def item(self, name, cat, qty=0.0, cost=0.0):
        i = FeedIngredient(name=name, category=cat, cost_per_kg=cost)
        db.session.add(i); db.session.flush()
        db.session.add(LocationStock(location_id=self.loc.id, ingredient_id=i.id, quantity_kg=qty, unit_cost_per_kg=cost))
        db.session.commit(); return i


class OwnMillingTests(Base):
    def setUp(self):
        super().setUp()
        self.whole = self.item('Whole Maize', 'Raw Energy', qty=100.0, cost=40.0)
        self.broken = self.item('Broken Maize', 'Raw Energy', qty=0.0, cost=0.0)

    def mill(self, **over):
        body = {'location_id': self.loc.id, 'input_ingredient_id': self.whole.id, 'output_ingredient_id': self.broken.id,
                'input_kg': 40, 'output_kg': 38}
        body.update(over)
        return self.client.post('/api/factory/own-milling', json=body, headers=key())

    def test_loss_and_electricity_raise_cost_per_kg(self):
        r = self.mill()
        self.assertEqual(r.status_code, 201, r.json)
        # 40 kg x 40 = 1600 grain + 40 kg x 2 = 80 electricity -> 1680 over 38 kg
        self.assertEqual(r.json['loss_kg'], 2.0)
        self.assertEqual(r.json['electricity_cost'], 80.0)
        self.assertEqual(r.json['total_cost'], 1680.0)
        self.assertAlmostEqual(r.json['output_cost_per_kg'], 1680 / 38, places=2)
        self.assertAlmostEqual(LocationStock.query.filter_by(ingredient_id=self.whole.id).one().quantity_kg, 60.0)
        out = LocationStock.query.filter_by(ingredient_id=self.broken.id).one()
        self.assertAlmostEqual(out.quantity_kg, 38.0)
        self.assertAlmostEqual(out.unit_cost_per_kg, 1680 / 38, places=4)
        kinds = {m.movement_type for m in StockMovement.query.all()}
        self.assertTrue({'MILLING_CONSUMPTION', 'MILLING_OUTPUT'} <= kinds)

    def test_postings_balance(self):
        self.mill()
        rows = GeneralLedgerEntry.query.filter_by(source_type='OWN_MILLING').all()
        self.assertEqual(round(sum(r.debit for r in rows), 2), round(sum(r.credit for r in rows), 2))
        self.assertEqual(round(sum(r.debit for r in rows), 2), 1680.0)

    def test_electricity_goes_to_its_own_absorption_account_not_utilities(self):
        from services.models import Account
        self.mill()
        codes = {r.account_code: (r.debit, r.credit) for r in GeneralLedgerEntry.query.filter_by(source_type='OWN_MILLING').all() if r.account_code != '1200'}
        self.assertEqual(codes, {'5370': (0.0, 80.0)})        # 40 kg x KES 2; 5310 (the real bill) is untouched
        self.assertEqual(GeneralLedgerEntry.query.filter_by(account_code='5310').count(), 0)
        self.assertIsNotNone(Account.query.filter_by(account_code='5370').first())

    def test_custom_rate_and_validation(self):
        self.assertEqual(self.mill(electricity_rate_per_kg=3).json['electricity_cost'], 120.0)
        self.assertEqual(self.mill(output_kg=50).status_code, 400)          # more out than in
        self.assertEqual(self.mill(input_kg=500, output_kg=400).status_code, 400)   # not enough stock
        self.assertEqual(self.mill(output_ingredient_id=self.whole.id).status_code, 400)  # same item
        self.assertEqual(self.mill(electricity_rate_per_kg=99).status_code, 400)

    def test_input_without_cost_is_refused(self):
        free = self.item('Free Grain', 'Raw Energy', qty=50.0, cost=0.0)
        r = self.mill(input_ingredient_id=free.id)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(InternalMillingRun.query.count(), 0)

    def test_retry_with_same_key_does_not_mill_twice(self):
        body = {'location_id': self.loc.id, 'input_ingredient_id': self.whole.id,
                'output_ingredient_id': self.broken.id, 'input_kg': 10, 'output_kg': 9.5}
        k = key()
        self.client.post('/api/factory/own-milling', json=body, headers=k)
        self.client.post('/api/factory/own-milling', json=body, headers=k)
        self.assertEqual(InternalMillingRun.query.count(), 1)
        self.assertAlmostEqual(LocationStock.query.filter_by(ingredient_id=self.whole.id).one().quantity_kg, 90.0)

    def test_factory_role_can_mill_and_list(self):
        self.as_user(self.fac)
        self.assertEqual(self.mill().status_code, 201)
        rows = self.client.get('/api/factory/own-milling').json
        self.assertEqual(rows[0]['output_name'], 'Broken Maize')


class CustomerAdminTests(Base):
    def setUp(self):
        super().setUp()
        self.a = Customer(name='Kipkemei', current_balance=500.0, credit_limit=1000.0, location_id=self.loc.id)
        self.b = Customer(name='Kipkemei Farm', phone='0700', current_balance=200.0, credit_limit=3000.0, location_id=self.loc.id)
        self.empty = Customer(name='Typo Name', location_id=self.loc.id)
        db.session.add_all([self.a, self.b, self.empty]); db.session.flush()
        db.session.add(OrderHeader(sale_id='S1', customer_id=self.a.id, total_amount=700, credit_amount=500, location_id=self.loc.id))
        db.session.add(CustomerDebtEntry(reference='D1', customer_id=self.a.id, location_id=self.loc.id, amount=0.01, note='old', created_by='boss'))
        db.session.commit()

    def test_delete_blocked_when_history_exists_and_allowed_when_clean(self):
        r = self.client.delete('/api/admin/customers/%d' % self.a.id)
        self.assertEqual(r.status_code, 409)
        self.assertIsNotNone(db.session.get(Customer, self.a.id))
        r = self.client.delete('/api/admin/customers/%d' % self.empty.id)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(db.session.get(Customer, self.empty.id))

    def test_merge_moves_history_and_adds_balances(self):
        r = self.client.post('/api/admin/customers/merge', json={'source_id': self.a.id, 'target_id': self.b.id}, headers=key())
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json['orders_moved'], 1)
        self.assertIsNone(db.session.get(Customer, self.a.id))
        keep = db.session.get(Customer, self.b.id)
        self.assertEqual(keep.current_balance, 700.0)
        self.assertEqual(keep.credit_limit, 3000.0)
        self.assertEqual(OrderHeader.query.filter_by(sale_id='S1').one().customer_id, keep.id)
        self.assertEqual(CustomerDebtEntry.query.one().customer_id, keep.id)
        log = CustomerMergeLog.query.one()
        self.assertEqual((log.source_name, log.target_name, log.balance_moved), ('Kipkemei', 'Kipkemei Farm', 500.0))

    def test_merge_validation_and_admin_only(self):
        same = self.client.post('/api/admin/customers/merge', json={'source_id': self.a.id, 'target_id': self.a.id}, headers=key())
        self.assertEqual(same.status_code, 400)
        self.as_user(self.fac)
        denied = self.client.post('/api/admin/customers/merge', json={'source_id': self.a.id, 'target_id': self.b.id}, headers=key())
        self.assertIn(denied.status_code, (401, 403))
        self.assertEqual(self.client.delete('/api/admin/customers/%d' % self.empty.id).status_code in (401, 403), True)


class LedgerFilterTests(Base):
    def setUp(self):
        super().setUp()
        now = datetime.utcnow()
        def sale(sid, total, credit, when):
            db.session.add(OrderHeader(sale_id=sid, total_amount=total, paid_amount=total - credit, credit_amount=credit,
                                       status='COMPLETED', created_at=when, location_id=self.loc.id))
        sale('NEW-CASH', 100, 0, now)
        sale('NEW-CREDIT-SMALL', 200, 50, now - timedelta(minutes=1))
        sale('NEW-CREDIT-BIG', 300, 250, now - timedelta(minutes=2))
        sale('OLD-CREDIT', 400, 400, now - timedelta(days=400))
        db.session.commit()

    def ids(self, qs):
        return [s['sale_id'] for s in self.client.get('/api/sales-history?location_id=%d&%s' % (self.loc.id, qs)).json]

    def test_sort_by_credit_puts_largest_debt_first(self):
        self.assertEqual(self.ids('sort=credit')[:2], ['OLD-CREDIT', 'NEW-CREDIT-BIG'])

    def test_credit_only_filter(self):
        self.assertEqual(set(self.ids('credit_only=1')), {'NEW-CREDIT-SMALL', 'NEW-CREDIT-BIG', 'OLD-CREDIT'})

    def test_period_filter_excludes_old_sales(self):
        got = self.ids('period=this_year')
        self.assertNotIn('OLD-CREDIT', got)
        self.assertIn('NEW-CASH', got)
        self.assertEqual(self.client.get('/api/sales-history?period=bogus&location_id=%d' % self.loc.id).status_code, 400)

    def test_items_still_returned_for_receipts(self):
        row = self.client.get('/api/sales-history?location_id=%d' % self.loc.id).json[0]
        for k in ('items', 'payments', 'credit_amount', 'sale_id', 'customer_name'):
            self.assertIn(k, row)


if __name__ == '__main__':
    unittest.main()
