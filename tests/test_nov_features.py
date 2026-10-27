import os
import sys
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from test_grpo_and_customers import Base
from services import business_hours
from services.db import db
from services.models import (Customer, FeedIngredient, GeneralLedgerEntry, GoodsReceiptLine, GoodsReceiptNote,
                             LocationStock, PurchaseOrderLine, Supplier, SupplierTxn, TillSession, User)


class UomAndTransportTests(Base):
    def make_po(self, item, qty, cost, **extra):
        r = self.make_client(self.admin_id).post('/api/po', json={
            'location_id': self.factory.id, 'supplier_id': self.supplier.id,
            'items': [dict(ingredient_id=item.id, qty=qty, unit_cost=cost, **extra)]},
            headers={'Idempotency-Key': str(uuid.uuid4())})
        return r

    def receive(self, po_number, line_id, body):
        from services.models import PurchaseOrderHeader
        po = PurchaseOrderHeader.query.filter_by(po_number=po_number).one()
        return self.client.post(f'/api/po/{po.id}/grpo', json=body, headers={'Idempotency-Key': str(uuid.uuid4())}), po

    def test_line_keeps_the_unit_it_was_written_with(self):
        line = PurchaseOrderLine.query.get(self.line_id)
        self.assertEqual((line.purchase_uom, line.conversion_type, line.conversion_factor), ('BAG', 'FIXED', 50.0))
        self.item.conversion_factor = 70.0           # item master edited after the order was placed
        db.session.commit()
        r = self.post(str(uuid.uuid4()), self.body(2))   # 2 bags = 100 kg under the PO's own rule
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.stock(), 100.0)

    def test_wrong_kg_for_bags_is_refused_server_side(self):
        bad = {'payment_method': 'ON_ACCOUNT', 'received_items': [
            {'line_id': self.line_id, 'qty_po_uom': 2, 'qty_rejected_po_uom': 0, 'qty_kg_accepted': 2}]}
        r = self.post(str(uuid.uuid4()), bad)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.stock(), 0.0)

    def test_price_per_kg_typed_as_price_per_bag_is_caught_at_po_time(self):
        r = self.make_po(self.item, 10, 43.33)          # per-kg price on a bag item: the 43.33 vs 3,900 mistake
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json['code'], 'PRICE_CHECK')
        ok = self.make_po(self.item2 if hasattr(self, 'item2') else self.item, 10, 43.33, price_confirmed=True)
        self.assertEqual(ok.status_code, 200)

    def test_client_cannot_override_the_unit(self):
        r = self.make_po(self.item, 10, 2000, uom='KG')
        self.assertEqual(r.status_code, 400)

    def test_ton_is_1000_kg_and_bag_without_kg_is_rejected(self):
        ton = FeedIngredient(name='Bulk Bran', category='Raw - Energy', cost_per_kg=30.0, purchase_uom='TON',
                             conversion_type='FIXED', conversion_factor=1.0)
        flat = FeedIngredient(name='Mystery Bag', category='Raw - Energy', cost_per_kg=30.0, purchase_uom='BAG',
                              conversion_type='FIXED', conversion_factor=1.0)
        db.session.add_all([ton, flat]); db.session.commit()
        r = self.make_po(ton, 2, 30000)
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(PurchaseOrderLine.query.filter_by(ingredient_id=ton.id).one().conversion_factor, 1000.0)
        self.assertEqual(self.make_po(flat, 2, 1500).status_code, 400)

    def test_transport_is_landed_cost_and_separate_from_supplier_invoice(self):
        transporter = Supplier(name='Rift Hauliers'); db.session.add(transporter); db.session.commit()
        body = self.body(10, method='ON_ACCOUNT')
        body['transport'] = {'amount': 1500, 'payee': 'Rift Hauliers', 'payment_method': 'ON_ACCOUNT',
                             'supplier_id': transporter.id, 'allocation': 'WEIGHT', 'reference': 'RC-9'}
        r = self.post(str(uuid.uuid4()), body)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.json['goods_value'], 20000.0)
        self.assertEqual(r.json['landed_total'], 21500.0)
        row = LocationStock.query.filter_by(location_id=self.factory.id, ingredient_id=self.item.id).one()
        self.assertAlmostEqual(row.unit_cost_per_kg, 43.0, places=4)          # (20000 + 1500) / 500 kg
        owed = {t.supplier_id: t.amount for t in SupplierTxn.query.all()}
        self.assertEqual(owed[self.supplier.id], 20000.0)                       # goods invoice only
        self.assertEqual(owed[transporter.id], 1500.0)
        grn = GoodsReceiptNote.query.one()
        self.assertEqual((grn.transport_amount, grn.transport_payee), (1500.0, 'Rift Hauliers'))
        rows = GeneralLedgerEntry.query.all()
        self.assertAlmostEqual(sum(x.debit for x in rows), sum(x.credit for x in rows), places=2)

    def test_unpaid_transport_to_the_goods_supplier_is_refused(self):
        body = self.body(10)
        body['transport'] = {'amount': 500, 'payee': 'x', 'payment_method': 'ON_ACCOUNT', 'supplier_id': self.supplier.id}
        self.assertEqual(self.post(str(uuid.uuid4()), body).status_code, 400)
        self.assertEqual(GoodsReceiptNote.query.count(), 0)

    def test_repeat_submission_does_not_double_count_transport(self):
        body = self.body(10, method='CASH')
        body['transport'] = {'amount': 1000, 'payee': 'Driver', 'payment_method': 'CASH'}
        key = str(uuid.uuid4())
        self.assertEqual(self.post(key, body).status_code, 200)
        self.assertEqual(self.post(key, body).status_code, 200)
        self.assertEqual(GoodsReceiptNote.query.count(), 1)
        self.assertEqual(self.stock(), 500.0)

    def test_expected_deliveries_hide_closed_orders_and_send_message_works(self):
        admin = self.make_client(self.admin_id)
        self.supplier.phone = '0712 345 678'; db.session.commit()
        listed = admin.get(f'/api/po?location_id={self.factory.id}').json
        self.assertEqual(len(listed), 1)
        msg = admin.get(f'/api/po/{self.po_id}/message?location_id={self.factory.id}').json
        self.assertEqual(msg['phone'], '254712345678')
        self.assertIn('10 BAG', msg['text'])
        self.assertTrue(msg['whatsapp_url'].startswith('https://wa.me/254712345678?text='))
        self.assertEqual(self.post(str(uuid.uuid4()), self.body(10)).status_code, 200)
        self.assertEqual(admin.get(f'/api/po?location_id={self.factory.id}').json, [])
        self.assertEqual(len(admin.get(f'/api/po?location_id={self.factory.id}&history=1').json), 1)

    def test_inventory_is_grouped_and_alphabetical_ignoring_case(self):
        db.session.add_all([FeedIngredient(name='zinc oxide', category='Raw - Additive'),
                            FeedIngredient(name='Apple Meal', category='Raw - Energy'),
                            FeedIngredient(name='broiler Finisher', category='Finished Feed'),
                            FeedIngredient(name='Limestone', category='Raw - Mineral')])
        db.session.commit()
        names = [(i['group'], i['name']) for i in self.make_client(self.admin_id).get(
            f'/api/po/inventory?location_id={self.factory.id}').json]
        self.assertEqual([g for g, _ in names], sorted([g for g, _ in names], key=['finished', 'raw', 'mineral', 'additive', 'packaging', 'other'].index))
        raw = [n for g, n in names if g == 'raw']
        self.assertEqual(raw, sorted(raw, key=str.lower))


class TradingHoursTests(Base):
    def at(self, hour, minute=0):
        local = datetime(2026, 10, 26, hour, minute)
        business_hours.utcnow = lambda: local - timedelta(hours=3)

    def setUp(self):
        super().setUp()
        self.app.config['BUSINESS_HOURS_ENFORCED'] = True
        self._orig = business_hours.utcnow

    def tearDown(self):
        business_hours.utcnow = self._orig
        super().tearDown()

    def test_staff_cannot_open_a_till_outside_6_to_21_but_owner_can(self):
        for hour, expected in ((5, 400), (6, 200), (20, 200), (21, 400)):
            self.at(hour)
            r = self.client.post('/api/till/open', json={'location_id': self.factory.id, 'opening_cash': 0})
            self.assertEqual(r.status_code, expected, (hour, r.get_data(as_text=True)))
            if r.status_code == 200:
                TillSession.query.update({'status': 'CLOSED', 'open_key': None}); db.session.commit()
        self.at(2)
        r = self.make_client(self.admin_id).post('/api/till/open', json={'location_id': self.factory.id, 'opening_cash': 0})
        self.assertEqual(r.status_code, 200)

    def test_open_staff_till_is_closed_by_the_system_after_9pm_without_inventing_a_count(self):
        self.at(15)
        self.assertEqual(self.client.post('/api/till/open', json={'location_id': self.factory.id, 'opening_cash': 500}).status_code, 200)
        self.at(21, 30)
        r = self.client.get(f'/api/till/current?location_id={self.factory.id}')
        self.assertIsNone(r.json['session'])
        till = TillSession.query.one()
        self.assertEqual(till.status, 'AUTO_CLOSED')
        self.assertIsNone(till.counted_cash)
        self.assertEqual(r.json['auto_closed']['expected_cash'], 500.0)
        self.at(7)
        self.assertEqual(self.client.post('/api/till/open', json={'location_id': self.factory.id, 'opening_cash': 500}).status_code, 200)


class ExpensesAndDebtTests(Base):
    def test_factory_user_can_record_an_expense(self):
        f = User(username='fac', role='factory', location='FAC-01', location_id=self.factory.id)
        f.set_password('7353'); db.session.add(f); db.session.commit()
        c = self.make_client(f.id)
        self.assertEqual(c.get(f'/api/expenses?location_id={self.factory.id}').status_code, 200)
        r = c.post('/api/expenses', json={'location_id': self.factory.id, 'category': 'TRANSPORT', 'amount': 200,
                                          'payment_method': 'MPESA', 'description': 'Boda to depot'},
                   headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))

    def test_debt_statement_lists_old_debt_and_is_outlet_scoped(self):
        cust = Customer(name='Zawadi', phone='0700', credit_limit=5000, location_id=self.factory.id)
        db.session.add(cust); db.session.commit()
        admin = self.make_client(self.admin_id)
        r = admin.post(f'/api/customers/{cust.id}/add-debt', json={'amount': 1200, 'note': 'old feed bill', 'location_id': self.factory.id},
                       headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        d = admin.get(f'/api/customers/{cust.id}/debt').json
        self.assertEqual((d['balance'], d['available_credit']), (1200.0, 3800.0))
        self.assertEqual(d['entries'][0]['kind'], 'OLD DEBT')
        other = Customer(name='Elsewhere', location_id=self.branch.id); db.session.add(other); db.session.commit()
        self.assertEqual(self.client.get(f'/api/customers/{other.id}/debt').status_code, 403)


class NutrientImportMirrorTests(Base):
    def test_commit_updates_the_item_master_columns(self):
        admin = self.make_client(self.admin_id)
        r = admin.post('/api/nutrition/import/ingredients', json={'text': 'Ingredient\tCP\tME\nTest Maize\t8.5\t3.2', 'commit': True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        item = db.session.get(FeedIngredient, self.item.id)
        self.assertEqual((item.crude_protein_pct, item.metabolizable_energy_mcal), (8.5, 3.2))


if __name__ == '__main__':
    import unittest
    unittest.main()
