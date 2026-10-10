"""Retry-safety for every cash/stock-moving POST route (services/idempotency.py)."""
import concurrent.futures
import os
import re
import tempfile
import unittest
import uuid

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-deployment')
from app import create_app
from services.db import db
from services.models import (
    Customer, CustomerPayment, FeedIngredient, IdempotencyKey, InventoryTransfer, Location,
    LocationStock, OperatingExpense, OrderHeader, OwnerWithdrawal, ProductionRun,
    PurchaseOrderHeader, Supplier, SupplierTxn, TillCashMovement, TillSession, User,
)

IDEM_JS = os.path.join(os.path.dirname(__file__), '..', 'static', 'js', 'idem.js')


class Base(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix='emining-idem-test-')
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'test-only-secret-key-not-for-deployment',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + os.path.join(self.tempdir.name, 'test.db'),
            'SQLALCHEMY_ENGINE_OPTIONS': {'connect_args': {'timeout': 15}},
        })
        self.context = self.app.app_context()
        self.context.push()
        self.factory = Location(name='Factory', code='FAC-01', location_type='FACTORY')
        self.branch = Location(name='Retail Outlet', code='RET-01', location_type='BRANCH_STORE')
        self.admin = User(username='owner', role='admin', location='FAC-01')
        self.keeper = User(username='keeper', role='warehouse', location='FAC-01')
        self.cashier = User(username='cashier', role='sales', location='RET-01')
        for user, pin in ((self.admin, '1111'), (self.keeper, '2222'), (self.cashier, '3333')):
            user.set_password(pin)
        db.session.add_all([self.factory, self.branch, self.admin, self.keeper, self.cashier])
        db.session.flush()
        self.keeper.location_id = self.factory.id
        self.cashier.location_id = self.branch.id
        self.raw = FeedIngredient(name='Maize', category='Raw Material', cost_per_kg=40.0,
                                  stock_quantity_kg=500.0)
        self.feed = FeedIngredient(name='Layers Mash', category='Finished Feed', cost_per_kg=50.0,
                                   stock_quantity_kg=0.0, retail_price_per_kg=70.0, bag_size_kg=70.0)
        self.supplier = Supplier(name='Nakuru Millers')
        db.session.add_all([self.raw, self.feed, self.supplier])
        db.session.flush()
        db.session.add_all([
            LocationStock(location_id=self.factory.id, ingredient_id=self.raw.id,
                          quantity_kg=500.0, unit_cost_per_kg=40.0),
            LocationStock(location_id=self.branch.id, ingredient_id=self.feed.id,
                          quantity_kg=100.0, unit_cost_per_kg=50.0),
            SupplierTxn(supplier_id=self.supplier.id, ref='SEED', kind='PURCHASE',
                        amount=5000.0, method='ON_ACCOUNT', created_by='seed'),
            TillSession(location_id=self.branch.id, cashier_name='cashier', opening_cash=5000.0,
                        expected_cash=5000.0, status='OPEN', open_key=f'{self.branch.id}:cashier'),
            Customer(name='Debtor Farm', customer_type='RETAIL', current_balance=500.0,
                     credit_limit=1000.0, location_id=self.branch.id),
        ])
        db.session.commit()
        self.ids = {n: getattr(self, n).id for n in ('admin', 'keeper', 'cashier')}
        self.cust_id = Customer.query.filter_by(name='Debtor Farm').one().id

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.tempdir.cleanup()

    def client(self, who):
        c = self.app.test_client()
        with c.session_transaction() as sess:
            sess['user_id'] = self.ids[who]
        return c

    def post(self, who, url, body, key=None, client=None):
        return (client or self.client(who)).post(
            url, json=body, headers={'Idempotency-Key': key or str(uuid.uuid4())})

    def stock(self, loc, item):
        row = LocationStock.query.filter_by(location_id=loc.id, ingredient_id=item.id).first()
        return row.quantity_kg if row else 0.0

    def assertReplayed(self, first, second):
        self.assertIn(first.status_code, (200, 201), first.json)
        self.assertEqual(second.status_code, first.status_code, second.json)   # replay keeps the original status
        self.assertTrue(second.json.get('already_processed'), second.json)
        self.assertNotIn('_http_status', second.json)
        self.assertFalse(first.json.get('already_processed'))


class MoneyAndStockReplayTests(Base):
    def twice(self, who, url, body):
        key = str(uuid.uuid4())
        return self.post(who, url, body, key), self.post(who, url, body, key)

    def test_supplier_payment(self):
        first, second = self.twice('admin', f'/api/admin/payables/{self.supplier.id}/pay',
                                   {'amount': 1000, 'method': 'CASH'})
        self.assertReplayed(first, second)
        self.assertEqual(first.json['ref'], second.json['ref'])
        self.assertEqual(SupplierTxn.query.filter_by(kind='PAYMENT').count(), 1)

    def test_customer_repayment(self):
        first, second = self.twice('cashier', f'/api/customers/{self.cust_id}/repay',
                                   {'amount': 200, 'payment_method': 'CASH', 'location_id': self.branch.id})
        self.assertReplayed(first, second)
        self.assertEqual(CustomerPayment.query.count(), 1)
        self.assertEqual(db.session.get(Customer, self.cust_id).current_balance, 300.0)

    def test_expense(self):
        first, second = self.twice('cashier', '/api/expenses', {
            'location_id': self.branch.id, 'category': 'TRANSPORT', 'payment_method': 'CASH',
            'amount': 300, 'description': 'fuel for delivery'})
        self.assertReplayed(first, second)
        self.assertEqual(OperatingExpense.query.count(), 1)

    def test_till_cash_movement(self):
        first, second = self.twice('cashier', '/api/till/cash-movements', {
            'location_id': self.branch.id, 'movement_type': 'PAID_IN', 'amount': 100, 'reason': 'float top up'})
        self.assertReplayed(first, second)
        self.assertEqual(TillCashMovement.query.count(), 1)
        self.assertEqual(TillSession.query.one().expected_cash, 5100.0)

    def test_stock_adjustment(self):
        first, second = self.twice('keeper', '/api/inventory/adjust', {
            'location_id': self.factory.id, 'ingredient_id': self.raw.id,
            'delta_kg': -10, 'reason': 'spillage during moving'})
        self.assertReplayed(first, second)
        self.assertEqual(self.stock(self.factory, self.raw), 490.0)

    def test_transfer_send_and_receive(self):
        first, second = self.twice('keeper', '/api/inventory/transfers', {
            'from_location_id': self.factory.id, 'to_location_id': self.branch.id,
            'ingredient_id': self.raw.id, 'quantity_kg': 50, 'reason': 'restock branch'})
        self.assertReplayed(first, second)
        self.assertEqual(InventoryTransfer.query.count(), 1)
        self.assertEqual(self.stock(self.factory, self.raw), 450.0)
        transfer_id = InventoryTransfer.query.one().id
        got1, got2 = self.twice('cashier', f'/api/inventory/transfers/{transfer_id}/receive',
                                {'quantity_kg': 20})
        self.assertReplayed(got1, got2)
        self.assertEqual(db.session.get(InventoryTransfer, transfer_id).received_quantity_kg, 20.0)
        self.assertEqual(self.stock(self.branch, self.raw), 20.0)

    def test_production_batch(self):
        first, second = self.twice('keeper', '/api/factory/produce', {
            'formula_name': 'Test Layers', 'location_id': self.factory.id,
            'output_ingredient_id': self.feed.id, 'planned_output_kg': 100, 'actual_output_kg': 99,
            'loss_reason': 'dust', 'inputs': [
                {'ingredient_id': self.raw.id, 'planned_qty_kg': 100, 'actual_qty_kg': 100}]})
        self.assertReplayed(first, second)
        self.assertEqual(ProductionRun.query.count(), 1)
        self.assertEqual(self.stock(self.factory, self.raw), 400.0)
        self.assertEqual(self.stock(self.factory, self.feed), 99.0)

    def test_owner_withdrawal(self):
        first, second = self.twice('admin', '/api/reports/owner-withdrawals', {
            'amount': 500, 'payment_method': 'MPESA', 'reason': 'owner drawings'})
        self.assertReplayed(first, second)
        self.assertEqual(OwnerWithdrawal.query.count(), 1)

    def test_refund(self):
        sale = self.post('cashier', '/api/pos/checkout', {
            'location_id': self.branch.id,
            'cart': [{'ingredient_id': self.feed.id, 'unit_type': 'KG', 'qty': 10, 'bag_size_kg': 1}],
            'payments': [{'payment_method': 'CASH', 'amount': 700}]})
        self.assertIn(sale.status_code, (200, 201), sale.json)
        order_id = OrderHeader.query.one().id
        first, second = self.twice('cashier', f'/api/pos/orders/{order_id}/refund', {
            'amount': 70, 'payment_method': 'CASH', 'reason': 'customer returned one bag',
            'returned_items': []})
        self.assertReplayed(first, second)
        from services.models import SalesRefund
        self.assertEqual(SalesRefund.query.count(), 1)

    def test_customer_creation_and_po_creation(self):
        first, second = self.twice('cashier', '/api/customers', {'name': 'Fresh Farmer', 'phone': '0700'})
        self.assertReplayed(first, second)
        self.assertEqual(Customer.query.filter_by(name='Fresh Farmer').count(), 1)
        first, second = self.twice('admin', '/api/po', {
            'location_id': self.factory.id, 'supplier_id': self.supplier.id,
            'items': [{'ingredient_id': self.raw.id, 'qty': 10, 'unit_cost': 2000}]})
        self.assertReplayed(first, second)
        self.assertEqual(PurchaseOrderHeader.query.count(), 1)


class SafetyRuleTests(Base):
    def test_every_protected_route_rejects_a_missing_key_and_writes_nothing(self):
        routes = [r for r in self.app.url_map.iter_rules()
                  if getattr(self.app.view_functions[r.endpoint], 'idempotent_scope', None)
                  and 'POST' in r.methods]
        self.assertEqual(len(routes), 17)
        admin = self.client('admin')
        for rule in routes:
            url = re.sub(r'<int:\w+>', '1', rule.rule)
            for headers in ({}, {'Idempotency-Key': 'not-a-uuid'}):
                response = admin.post(url, json={}, headers=headers)
                self.assertEqual(response.status_code, 400, url)
                self.assertIn('key missing', response.json['message'], url)
        self.assertEqual(IdempotencyKey.query.count(), 0)

    def test_client_and_server_route_lists_match(self):
        js = open(IDEM_JS, encoding='utf-8').read()
        block = js[js.index('const PROTECTED'):js.index('];', js.index('const PROTECTED'))]
        patterns = [re.compile(p.replace('\\/', '/')) for p in re.findall(r'/(\^.+?\$)/,', block)]
        server = [re.sub(r'<int:\w+>', '7', r.rule) for r in self.app.url_map.iter_rules()
                  if getattr(self.app.view_functions[r.endpoint], 'idempotent_scope', None)
                  and 'POST' in r.methods]
        for path in server:
            self.assertTrue(any(p.match(path) for p in patterns), f'idem.js does not cover {path}')
        for pattern in patterns:
            self.assertTrue(any(pattern.match(p) for p in server), f'idem.js lists unprotected {pattern.pattern}')

    def test_a_rejected_request_does_not_burn_the_key(self):
        key = str(uuid.uuid4())
        bad = self.post('cashier', '/api/expenses', {
            'location_id': self.branch.id, 'category': 'TRANSPORT', 'payment_method': 'CASH',
            'amount': -5, 'description': 'bad amount'}, key)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(IdempotencyKey.query.count(), 0)
        good = self.post('cashier', '/api/expenses', {
            'location_id': self.branch.id, 'category': 'TRANSPORT', 'payment_method': 'CASH',
            'amount': 50, 'description': 'bus fare'}, key)
        self.assertIn(good.status_code, (200, 201), good.json)
        self.assertEqual(OperatingExpense.query.count(), 1)

    def test_same_key_with_different_details_is_rejected(self):
        key = str(uuid.uuid4())
        url = f'/api/admin/payables/{self.supplier.id}/pay'
        self.assertIn(self.post('admin', url, {'amount': 1000, 'method': 'CASH'}, key).status_code, (200, 201))
        clash = self.post('admin', url, {'amount': 2000, 'method': 'CASH'}, key)
        self.assertEqual(clash.status_code, 409)
        self.assertEqual(SupplierTxn.query.filter_by(kind='PAYMENT').count(), 1)

    def test_another_user_cannot_replay_someone_elses_key(self):
        key = str(uuid.uuid4())
        body = {'location_id': self.branch.id, 'category': 'TRANSPORT', 'payment_method': 'MPESA',
                'amount': 80, 'description': 'taxi'}
        self.assertIn(self.post('cashier', '/api/expenses', body, key).status_code, (200, 201))
        self.assertEqual(self.post('admin', '/api/expenses', body, key).status_code, 409)

    def test_two_real_expenses_with_fresh_keys_both_post(self):
        body = {'location_id': self.branch.id, 'category': 'MEALS', 'payment_method': 'CASH',
                'amount': 100, 'description': 'staff lunch'}
        self.assertIn(self.post('cashier', '/api/expenses', body).status_code, (200, 201))
        self.assertIn(self.post('cashier', '/api/expenses', body).status_code, (200, 201))
        self.assertEqual(OperatingExpense.query.count(), 2)

    def test_reads_need_no_key(self):
        response = self.client('cashier').get(f'/api/expenses?location_id={self.branch.id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(IdempotencyKey.query.count(), 0)

    def test_parallel_duplicate_supplier_payments_post_once(self):
        key = str(uuid.uuid4())
        url, body = f'/api/admin/payables/{self.supplier.id}/pay', {'amount': 1000, 'method': 'CASH'}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda _: self.post('admin', url, body, key, self.client('admin')), range(8)))
        self.assertEqual(SupplierTxn.query.filter_by(kind='PAYMENT').count(), 1)
        self.assertEqual(IdempotencyKey.query.filter_by(key='supplier-pay:' + key).count(), 1)
        self.assertGreaterEqual(sum(1 for r in responses if r.status_code in (200, 201)), 1)
        self.assertEqual({r.status_code for r in responses} - {200, 201, 409, 503}, set())


if __name__ == '__main__':
    unittest.main()
