import io
import os
import sys
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from test_pos import POSCheckoutTests as PosBase
from services.db import db
from services.models import Customer, Location, OrderHeader, User


class ReceiptTests(PosBase):
    # inherit the harness only; do not re-run the parent's tests
    def sale(self, qty=2, amount=80, **extra):
        r = self.client.post('/api/pos/checkout', json=self.payload(qty, amount, **extra),
                             headers={'Idempotency-Key': str(uuid.uuid4())})
        return r

    def admin_client(self):
        a = User(username='boss', role='admin', location='FAC-01', location_id=self.factory.id)
        a.set_password('7353'); db.session.add(a); db.session.commit()
        c = self.app.test_client()
        with c.session_transaction() as s:
            s['user_id'] = a.id
        return c

    def test_receipts_are_numbered_without_gaps_and_have_a_png(self):
        a, b = self.sale(1, 40), self.sale(1, 40)
        self.assertEqual((a.status_code, b.status_code), (200, 200), a.get_data(as_text=True))
        self.assertEqual([a.json['data']['receipt_no'], b.json['data']['receipt_no']], ['RET01-000001', 'RET01-000002'])
        img = self.client.get(f"/api/receipts/{a.json['data']['order_id']}.png")
        self.assertEqual((img.status_code, img.mimetype), (200, 'image/png'))
        self.assertTrue(img.data.startswith(b'\x89PNG'))
        from PIL import Image
        self.assertEqual(Image.open(io.BytesIO(img.data)).width, 1152)      # 80 mm paper at 2x

    def test_58mm_outlet_gets_a_narrower_image(self):
        loc = db.session.get(Location, self.branch.id); loc.receipt_width = 58; db.session.commit()
        r = self.sale(1, 40)
        from PIL import Image
        img = self.client.get(f"/api/receipts/{r.json['data']['order_id']}.png")
        self.assertEqual(Image.open(io.BytesIO(img.data)).width, 768)

    def test_verify_accepts_the_real_code_and_rejects_a_forged_one(self):
        r = self.sale(2, 80).json['data']
        from services import receipt as rc
        order = db.session.get(OrderHeader, r['order_id'])
        code = rc.build(order)['code']
        ok = self.client.get(f"/api/receipts/verify?ref={r['receipt_no']}&code={code}").json
        self.assertTrue(ok['valid']); self.assertEqual(ok['total'], 80.0)
        bad = self.client.get(f"/api/receipts/verify?ref={r['receipt_no']}&code=AAAAAA").json
        self.assertFalse(bad['valid'])
        order.discount_amount = 40.0                       # someone alters the sale after the fact
        db.session.commit()
        self.assertFalse(self.client.get(f"/api/receipts/verify?ref={r['receipt_no']}&code={code}").json['valid'])

    def test_receipt_shows_price_per_kg_phone_and_credit_balance(self):
        self.customer.phone = '0712345678'; self.customer.credit_limit = 1000; db.session.commit()
        r = self.client.post('/api/pos/checkout', json=self.payload(
            3, 20, customer_id=self.customer.id, payments=[{'payment_method': 'CASH', 'amount': 20}, {'payment_method': 'CREDIT', 'amount': 100}]),
            headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        from services import receipt as rc
        d = rc.build(db.session.get(OrderHeader, r.json['data']['order_id']))
        self.assertEqual(d['items'][0]['per_kg'], 40.0)
        self.assertEqual(d['balance_owed'], 100.0)
        self.assertEqual(d['customer_phone'], '0712345678')
        self.assertRegex(rc.fmt_when(d['when']), r'^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$')

    def test_discount_needs_a_reason_and_a_named_approver_above_2_percent(self):
        no_reason = self.sale(10, 390, discount_amount=10)
        self.assertEqual(no_reason.status_code, 400)
        no_approver = self.sale(10, 390, discount_amount=10, discount_reason='BULK')
        self.assertEqual(no_approver.status_code, 400)
        self.assertIn('approved', no_approver.json['message'])
        small = self.sale(10, 392, discount_amount=8, discount_reason='LOYAL')       # 2% exactly: no approver needed
        self.assertEqual(small.status_code, 200, small.get_data(as_text=True))
        ok = self.sale(10, 380, discount_amount=20, discount_reason='BULK', discount_approver='Reu')
        self.assertEqual(ok.status_code, 200)
        o = OrderHeader.query.filter_by(sale_id=ok.json['data']['sale_id']).one()
        self.assertEqual((o.discount_reason, o.discount_approver), ('BULK', 'Reu'))

    def test_phone_at_the_till_creates_then_reuses_the_customer(self):
        a = self.sale(1, 40, customer_phone='0722 111 222', customer_name='Mama Njeri')
        b = self.sale(1, 40, customer_phone='+254722111222')
        self.assertEqual((a.status_code, b.status_code), (200, 200), a.get_data(as_text=True))
        rows = Customer.query.filter_by(phone='0722111222').all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(OrderHeader.query.filter_by(customer_id=rows[0].id).count(), 2)
        self.assertEqual(self.sale(1, 40, customer_phone='12').status_code, 400)

    def test_statement_pdf_for_a_customer(self):
        self.sale(1, 40, customer_phone='0722111222', customer_name='Mama Njeri')
        cust = Customer.query.filter_by(phone='0722111222').one()
        r = self.client.get(f'/api/customers/{cust.id}/statement.pdf')
        self.assertEqual((r.status_code, r.mimetype), (200, 'application/pdf'))
        self.assertTrue(r.data.startswith(b'%PDF'))

    def test_cashier_cannot_read_other_outlets_receipts_or_owner_insights(self):
        other = Location(name='Other', code='OTH', location_type='BRANCH_STORE'); db.session.add(other); db.session.commit()
        o = OrderHeader(sale_id='SALE-X', total_amount=10, location_id=other.id, status='COMPLETED'); db.session.add(o); db.session.commit()
        self.assertEqual(self.client.get(f'/api/receipts/{o.id}.png').status_code, 404)
        self.assertEqual(self.client.get('/api/insights/products').status_code, 403)

    def test_product_ranking_and_lapsed_customers(self):
        self.sale(10, 400, customer_phone='0722111222', customer_name='Mama Njeri')
        admin = self.admin_client()
        p = admin.get(f'/api/insights/products?location_id={self.branch.id}&days=30').json
        row = next(r for r in p['rows'] if r['name'] == 'Test Layer Mash')
        self.assertEqual((row['sold_kg'], row['revenue'], row['profit']), (10.0, 400.0, 100.0))
        self.assertEqual(row['rank_profit'], 1)
        self.assertEqual(row['days_cover'], 270.0)                     # 90 kg left at 1/3 kg a day
        c = admin.get(f'/api/insights/customers?location_id={self.branch.id}').json
        self.assertEqual(c['linked_pct'], 100.0)
        self.assertEqual(c['top'][0]['name'], 'Mama Njeri')

    def test_receipt_settings_are_validated(self):
        admin = self.admin_client()
        r = admin.post(f'/api/locations/{self.branch.id}/receipt-settings', json={'receipt_width': 58, 'phone': '0700 000 111'})
        self.assertEqual(r.json['receipt_width'], 58)
        self.assertEqual(admin.post(f'/api/locations/{self.branch.id}/receipt-settings', json={'receipt_width': 70}).status_code, 400)


if __name__ == '__main__':
    import unittest
    unittest.main()
