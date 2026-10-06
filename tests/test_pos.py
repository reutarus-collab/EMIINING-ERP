import concurrent.futures
from datetime import datetime, timedelta
import os
import tempfile
import unittest
import uuid

# Importing the WSGI module initializes its default app. Keep that initialization
# isolated from a developer's configured/live DATABASE_URL.
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-deployment')
from app import create_app
from services.db import db
from services.models import (
    Customer, FeedIngredient, GeneralLedgerEntry, IdempotencyKey, ItemPrice,
    CashWalkOpening, Location, LocationStock, OrderHeader, OwnerWithdrawal,
    TillSession, User,
)


class POSCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix='emining-pos-test-')
        db_path = os.path.join(self.tempdir.name, 'test.db')
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'test-only-secret-key-not-for-deployment',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + db_path,
            'SQLALCHEMY_ENGINE_OPTIONS': {'connect_args': {'timeout': 15}},
        })
        self.context = self.app.app_context()
        self.context.push()
        self.factory = Location(name='Factory', code='FAC-01', location_type='FACTORY')
        self.branch = Location(name='Retail Outlet', code='RET-01', location_type='BRANCH_STORE')
        self.cashier = User(username='cashier', role='sales', location='RET-01')
        self.cashier.set_password('735192')
        db.session.add_all([self.factory, self.branch, self.cashier])
        db.session.flush()
        self.cashier.location_id = self.branch.id
        self.item = FeedIngredient(name='Test Layer Mash', category='Finished Feed',
                                   cost_per_kg=30.0, stock_quantity_kg=100.0,
                                   retail_price_per_kg=40.0, bag_size_kg=70.0)
        db.session.add(self.item)
        db.session.flush()
        self.stock = LocationStock(location_id=self.branch.id, ingredient_id=self.item.id,
                                   quantity_kg=100.0, unit_cost_per_kg=30.0)
        self.till = TillSession(location_id=self.branch.id, cashier_name=self.cashier.username,
                                opening_cash=5000.0, expected_cash=5000.0,
                                status='OPEN', open_key=f'{self.branch.id}:{self.cashier.username}')
        db.session.add_all([self.stock, self.till])
        self.customer = Customer(name='Test Farmer', customer_type='RETAIL',
                                  current_balance=0.0, credit_limit=100.0)
        db.session.add(self.customer)
        db.session.commit()
        self.cashier_id = self.cashier.id
        self.client = self.make_client()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.tempdir.cleanup()

    def make_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = self.cashier_id
        return client

    def payload(self, qty, amount, **extra):
        body = {
            'location_id': self.branch.id,
            'cart': [{'ingredient_id': self.item.id, 'unit_type': 'KG', 'qty': qty, 'bag_size_kg': 1}],
            'payments': [{'payment_method': 'CASH', 'amount': amount}],
        }
        body.update(extra)
        return body

    def test_stock_exhaustion_is_rejected_without_deducting_stock(self):
        response = self.client.post('/api/pos/checkout', json=self.payload(101, 4040),
                                    headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Insufficient stock', response.json['message'])
        self.assertEqual(LocationStock.query.filter_by(location_id=self.branch.id,
                                                       ingredient_id=self.item.id).one().quantity_kg, 100.0)

    def test_credit_limit_is_enforced(self):
        response = self.client.post('/api/pos/checkout',
            json=self.payload(10, 0, customer_id=self.customer.id,
                              payments=[{'payment_method': 'CREDIT', 'amount': 400}]),
            headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Credit limit exceeded', response.json['message'])

    def test_pack_price_and_discount_are_recorded(self):
        db.session.add(ItemPrice(ingredient_id=self.item.id, pack_kg=70, price=2800))
        db.session.commit()
        response = self.client.post('/api/pos/checkout',
            json=self.payload(1, 2660, discount_amount=140,
                              cart=[{'ingredient_id': self.item.id, 'unit_type': '70KG BAG', 'qty': 1, 'bag_size_kg': 70}]),
            headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['data']['total_amount'], 2660.0)
        self.assertEqual(LocationStock.query.filter_by(location_id=self.branch.id,
                                                       ingredient_id=self.item.id).one().quantity_kg, 30.0)

    def test_concurrent_checkouts_never_oversell(self):
        payload = self.payload(20, 800)
        def run_checkout(_):
            client = self.make_client()
            return client.post('/api/pos/checkout', json=payload,
                               headers={'Idempotency-Key': str(uuid.uuid4())})

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            responses = list(pool.map(run_checkout, range(10)))
        successful = sum(1 for response in responses if response.status_code == 200)
        balance = LocationStock.query.filter_by(location_id=self.branch.id,
                                                ingredient_id=self.item.id).one().quantity_kg
        self.assertLessEqual(successful, 5)
        self.assertAlmostEqual(balance, 100.0 - successful * 20.0, places=5)
        self.assertEqual(OrderHeader.query.filter_by(location_id=self.branch.id).count(), successful)

    def test_idempotent_retry_returns_original_sale_and_does_not_repeat_money_or_stock(self):
        key = str(uuid.uuid4())
        body = self.payload(10, 400)
        first = self.client.post('/api/pos/checkout', json=body, headers={'Idempotency-Key': key})
        retry = self.client.post('/api/pos/checkout', json=body, headers={'Idempotency-Key': key})
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(retry.status_code, 200, retry.json)
        self.assertTrue(retry.json['already_processed'])
        self.assertEqual(first.json['data'], retry.json['data'])
        self.assertEqual(IdempotencyKey.query.filter_by(key=key).count(), 1)
        self.assertEqual(OrderHeader.query.filter_by(location_id=self.branch.id).count(), 1)
        self.assertEqual(LocationStock.query.filter_by(location_id=self.branch.id,
                                                       ingredient_id=self.item.id).one().quantity_kg, 90.0)
        self.assertEqual(GeneralLedgerEntry.query.count(), 4)

    def test_reusing_a_key_for_a_different_checkout_is_rejected(self):
        key = str(uuid.uuid4())
        self.client.post('/api/pos/checkout', json=self.payload(10, 400), headers={'Idempotency-Key': key})
        response = self.client.post('/api/pos/checkout', json=self.payload(5, 200), headers={'Idempotency-Key': key})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(OrderHeader.query.filter_by(location_id=self.branch.id).count(), 1)

    def test_checkout_requires_uuid_idempotency_key(self):
        response = self.client.post('/api/pos/checkout', json=self.payload(1, 40))
        self.assertEqual(response.status_code, 400)
        self.assertIn('Idempotency-Key UUID', response.json['message'])

    def test_staff_location_is_resolved_from_exact_location_id(self):
        second = Location(name='Second Branch', code='RET-02', location_type='BRANCH_STORE')
        db.session.add(second)
        db.session.commit()
        response = self.client.get('/api/stock?location_id=' + str(second.id))
        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json if item['id'] == self.item.id)
        self.assertEqual(row['location_id'], self.branch.id)
        self.assertEqual(row['stock_kg'], 100.0)

    def make_admin_client(self):
        admin = User(username='owner', role='admin', location='admin')
        admin.set_password('long-test-password')
        db.session.add(admin)
        db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = admin.id
        return client

    def test_weekly_cash_walk_calculates_profit_working_capital_and_withdrawals(self):
        client = self.make_admin_client()
        start = (datetime.utcnow() + timedelta(hours=3)).date()
        payload = {'as_of_date': start.isoformat(), 'cash': 10000, 'inventory': 5000,
                   'debtors': 2000, 'creditors': 1000, 'equipment': 0}
        saved = client.post('/api/reports/opening-balances', json=payload)
        self.assertEqual(saved.status_code, 200, saved.json)
        rows = [
            ('1000', 1000, 0, 'POS'), ('1300', 200, 0, 'POS'), ('4000', 0, 1200, 'POS'),
            ('1200', 500, 0, 'PURCHASE'), ('2000', 0, 500, 'PURCHASE'),
            ('5000', 600, 0, 'POS'), ('1200', 0, 600, 'POS'),
            ('3000', 100, 0, 'OWNER_WITHDRAWAL'), ('1000', 0, 100, 'OWNER_WITHDRAWAL'),
        ]
        for code, debit, credit, source in rows:
            db.session.add(GeneralLedgerEntry(transaction_ref='CASH-WALK-TEST', account_code=code,
                                               debit=debit, credit=credit, source_type=source))
        db.session.commit()
        report = client.get(f'/api/reports/cash-walk?start={start.isoformat()}&end={start.isoformat()}')
        self.assertEqual(report.status_code, 200, report.json)
        self.assertEqual(report.json['lines']['profit'], 600)
        self.assertEqual(report.json['lines']['stock_increase'], -100)
        self.assertEqual(report.json['lines']['debtors_increase'], 200)
        self.assertEqual(report.json['lines']['creditors_increase'], 500)
        self.assertEqual(report.json['calculated_closing_cash'], 10900)
        self.assertEqual(report.json['ledger_closing_cash'], 10900)
        self.assertEqual(report.json['reconciliation_difference'], 0)

    def test_owner_cash_withdrawal_is_separate_and_reduces_open_till(self):
        client = self.make_admin_client()
        owner_till = TillSession(location_id=self.branch.id, cashier_name='owner', opening_cash=900,
                                 expected_cash=900, status='OPEN', open_key=f'{self.branch.id}:owner')
        db.session.add(owner_till)
        db.session.commit()
        response = client.post('/api/reports/owner-withdrawals', json={
            'amount': 100, 'payment_method': 'CASH', 'location_id': self.branch.id,
            'reason': 'Owner personal cash use',
        })
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(OwnerWithdrawal.query.count(), 1)
        self.assertEqual(TillSession.query.filter_by(id=self.till.id).one().expected_cash, 5000)
        self.assertEqual(TillSession.query.filter_by(id=owner_till.id).one().expected_cash, 800)

    def test_equipment_purchase_is_recorded_as_asset_and_not_expense(self):
        client = self.make_admin_client()
        rejected = client.post('/api/reports/equipment-purchases', json={
            'amount': 25000, 'payment_method': 'ON_ACCOUNT', 'description': 'Feed mixer motor',
        })
        self.assertEqual(rejected.status_code, 400)
        response = client.post('/api/reports/equipment-purchases', json={
            'amount': 25000, 'payment_method': 'BANK', 'description': 'Feed mixer motor',
        })
        self.assertEqual(response.status_code, 201, response.json)
        asset = GeneralLedgerEntry.query.filter_by(transaction_ref=response.json['reference'], account_code='1500').one()
        self.assertEqual(asset.debit, 25000)
        bank = GeneralLedgerEntry.query.filter_by(transaction_ref=response.json['reference'], account_code='1020').one()
        self.assertEqual(bank.credit, 25000)
        self.assertEqual(GeneralLedgerEntry.query.filter_by(transaction_ref=response.json['reference'], account_code='5000').count(), 0)

    def test_leak_report_returns_ranked_current_and_previous_period_rows(self):
        client = self.make_admin_client()
        response = client.get('/api/reports/leaks')
        self.assertEqual(response.status_code, 200, response.json)
        labels = [row['label'] for row in response.json['leaks']]
        self.assertIn('Transfer shortfall', labels)
        self.assertIn('Cash leakage', labels)
        self.assertTrue(all('previous_amount' in row and 'trend_delta' in row
                            for row in response.json['leaks']))


if __name__ == '__main__':
    unittest.main()
