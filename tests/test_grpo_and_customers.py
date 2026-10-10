import concurrent.futures
import os
import tempfile
import unittest
import uuid

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-deployment')
from app import create_app
from services.db import db
from services.models import (
    Customer, FeedIngredient, GoodsReceiptNote, IdempotencyKey, Location,
    LocationStock, PurchaseOrderHeader, PurchaseOrderLine, Supplier, SupplierTxn, User,
)


class Base(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix='emining-grpo-test-')
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
        self.admin = User(username='owner', role='admin', location='FAC-01')   # NOTE: no location_id on purpose
        self.admin.set_password('7351')
        self.keeper = User(username='keeper', role='warehouse', location='FAC-01')
        self.keeper.set_password('7352')
        db.session.add_all([self.factory, self.branch, self.admin, self.keeper])
        db.session.flush()
        self.keeper.location_id = self.factory.id
        self.item = FeedIngredient(name='Test Maize', category='Raw Material', cost_per_kg=40.0,
                                   stock_quantity_kg=0.0, purchase_uom='BAG',
                                   conversion_type='FIXED', conversion_factor=50.0)
        self.supplier = Supplier(name='Nakuru Millers')
        db.session.add_all([self.item, self.supplier])
        db.session.flush()
        db.session.commit()
        self.keeper_id, self.admin_id = self.keeper.id, self.admin.id
        created = self.make_client(self.admin_id).post('/api/po', json={
            'location_id': self.factory.id, 'supplier_id': self.supplier.id,
            'items': [{'ingredient_id': self.item.id, 'qty': 10, 'unit_cost': 2000}]},
            headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(created.status_code, 200, created.json)
        self.po_id = PurchaseOrderHeader.query.filter_by(po_number=created.json['po_number']).one().id
        self.line_id = PurchaseOrderLine.query.filter_by(po_header_id=self.po_id).one().id
        self.client = self.make_client(self.keeper_id)

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.tempdir.cleanup()

    def make_client(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
        return client

    def body(self, bags, method='ON_ACCOUNT'):
        return {'payment_method': method, 'received_items': [
            {'line_id': self.line_id, 'qty_po_uom': bags, 'qty_rejected_po_uom': 0,
             'qty_kg_accepted': bags * 50.0}]}

    def stock(self):
        row = LocationStock.query.filter_by(location_id=self.factory.id, ingredient_id=self.item.id).first()
        return row.quantity_kg if row else 0.0

    def post(self, key, body, client=None):
        return (client or self.client).post(f'/api/po/{self.po_id}/grpo', json=body,
                                            headers={'Idempotency-Key': key} if key else {})


class GRPORetryTests(Base):
    def test_partial_receipt_retry_is_not_duplicated(self):
        key, body = str(uuid.uuid4()), self.body(4)
        first, retry = self.post(key, body), self.post(key, body)
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(retry.status_code, 200, retry.json)
        self.assertTrue(retry.json['already_processed'])
        self.assertEqual(first.json['grpo_no'], retry.json['grpo_no'])
        self.assertEqual(self.stock(), 200.0)
        self.assertEqual(GoodsReceiptNote.query.count(), 1)
        self.assertEqual(SupplierTxn.query.count(), 1)          # payable booked once
        self.assertEqual(PurchaseOrderLine.query.get(self.line_id).qty_received, 4)

    def test_retry_of_final_receipt_returns_success_not_closed_error(self):
        key, body = str(uuid.uuid4()), self.body(10)
        self.assertEqual(self.post(key, body).status_code, 200)
        retry = self.post(key, body)
        self.assertEqual(retry.status_code, 200, retry.json)
        self.assertTrue(retry.json['already_processed'])
        self.assertEqual(self.stock(), 500.0)

    def test_new_key_for_next_delivery_still_posts(self):
        self.assertEqual(self.post(str(uuid.uuid4()), self.body(4)).status_code, 200)
        self.assertEqual(self.post(str(uuid.uuid4()), self.body(3)).status_code, 200)
        self.assertEqual(self.stock(), 350.0)
        self.assertEqual(GoodsReceiptNote.query.count(), 2)

    def test_missing_or_bad_key_is_rejected_and_changes_nothing(self):
        for key in (None, 'not-a-uuid'):
            response = self.post(key, self.body(4))
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.stock(), 0.0)
        self.assertEqual(GoodsReceiptNote.query.count(), 0)

    def test_same_key_with_different_details_is_rejected(self):
        key = str(uuid.uuid4())
        self.assertEqual(self.post(key, self.body(4)).status_code, 200)
        response = self.post(key, self.body(5))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.stock(), 200.0)

    def test_failed_attempt_does_not_burn_the_key(self):
        key = str(uuid.uuid4())
        bad = self.post(key, {'payment_method': 'ON_ACCOUNT', 'received_items': [
            {'line_id': self.line_id, 'qty_po_uom': 99, 'qty_rejected_po_uom': 0, 'qty_kg_accepted': 4950}]})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(IdempotencyKey.query.filter_by(key='grpo:' + key).count(), 0)
        self.assertEqual(self.post(key, self.body(4)).status_code, 200)

    def test_parallel_retries_post_exactly_once(self):
        key, body = str(uuid.uuid4()), self.body(4)
        def run(_):
            return self.post(key, body, client=self.make_client(self.keeper_id))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(run, range(8)))
        self.assertEqual(GoodsReceiptNote.query.count(), 1)
        self.assertEqual(SupplierTxn.query.count(), 1)
        self.assertEqual(self.stock(), 200.0)
        self.assertEqual(IdempotencyKey.query.filter_by(key='grpo:' + key).count(), 1)
        self.assertGreaterEqual(sum(1 for r in responses if r.status_code == 200), 1)


class AdminCustomerListTests(Base):
    def setUp(self):
        super().setUp()
        db.session.add_all([
            Customer(name='Amina Farm', phone='0711', customer_type='RETAIL', location_id=self.factory.id),
            Customer(name='Kiprop Poultry', phone='0722', customer_type='RETAIL', location_id=self.branch.id),
            Customer(name='Unassigned Joe', phone='0733', customer_type='RETAIL', location_id=None),
        ])
        db.session.commit()
        self.admin_client = self.make_client(self.admin_id)

    def test_admin_without_an_outlet_sees_every_customer_with_all_flag(self):
        rows = self.admin_client.get('/api/customers?all=1').json
        self.assertEqual({r['name'] for r in rows}, {'Amina Farm', 'Kiprop Poultry', 'Unassigned Joe'})
        names = {r['name']: r['outlet_name'] for r in rows}
        self.assertEqual(names['Kiprop Poultry'], 'Retail Outlet')
        self.assertEqual(names['Unassigned Joe'], 'Unassigned')

    def test_till_view_is_unchanged_and_still_outlet_scoped(self):
        rows = self.admin_client.get(f'/api/customers?location_id={self.branch.id}').json
        self.assertEqual({r['name'] for r in rows}, {'Kiprop Poultry', 'Unassigned Joe'})

    def test_non_admin_cannot_use_all_flag_to_see_other_outlets(self):
        rows = self.client.get('/api/customers?all=1')
        # warehouse role is not allowed on /api/customers at all
        self.assertIn(rows.status_code, (401, 403))


if __name__ == '__main__':
    unittest.main()
