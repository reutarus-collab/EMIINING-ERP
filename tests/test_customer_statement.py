"""Customer statement (bank-style ledger), purchases history, shareable PDF, and repay from the Customers tab."""
import os
import sys
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from test_grpo_and_customers import Base
from services.db import db
from services.models import (Customer, CustomerDebtEntry, CustomerPayment, FeedIngredient, OrderHeader,
                             OrderLine, User)


class CustomerStatementTests(Base):
    def setUp(self):
        super().setUp()
        self.cust = Customer(name='Mama Wanjiru', phone='0712345678', credit_limit=20000, location_id=self.factory.id)
        self.mash = FeedIngredient(name='Layers Mash', category='Finished Feed', cost_per_kg=40.0,
                                   stock_quantity_kg=0.0, retail_price_per_kg=70.0)
        db.session.add_all([self.cust, self.mash])
        db.session.commit()
        self.admin_c = self.make_client(self.admin_id)
        self.keeper_c = self.make_client(self.keeper_id)

    def day(self, days_ago):
        return datetime.utcnow() - timedelta(days=days_ago)

    def sale(self, days_ago, total, paid, unit='70KG BAG', qty=2.0, sale_id=None):
        credit = round(total - paid, 2)
        o = OrderHeader(sale_id=sale_id or 'S-' + uuid.uuid4().hex[:5], customer_id=self.cust.id, total_amount=total,
                        paid_amount=paid, credit_amount=credit, status='COMPLETED', created_at=self.day(days_ago),
                        location_id=self.factory.id)
        db.session.add(o)
        db.session.flush()
        db.session.add(OrderLine(order_id=o.id, ingredient_id=self.mash.id, unit_type=unit, qty_entered=qty,
                                 subtotal=total, unit_cost_per_kg=40.0))
        if credit > 0:
            self.cust.current_balance = round((self.cust.current_balance or 0.0) + credit, 2)
        db.session.commit()
        return o

    def pay(self, days_ago, amount, method='MPESA'):
        db.session.add(CustomerPayment(reference='PAY-' + uuid.uuid4().hex[:5].upper(), customer_id=self.cust.id,
                                       location_id=self.factory.id, amount=amount, payment_method=method,
                                       created_by='keeper', created_at=self.day(days_ago)))
        self.cust.current_balance = round(self.cust.current_balance - amount, 2)
        db.session.commit()

    def test_statement_runs_like_a_bank_statement_and_ends_on_the_till_balance(self):
        self.sale(10, 8900, 0)              # +8,900 credit
        self.pay(6, 3000)                   # -3,000
        self.sale(2, 4450, 450)             # +4,000 credit, 450 paid in cash at the till
        d = self.keeper_c.get(f'/api/customers/{self.cust.id}/statement').json
        self.assertEqual([e['kind'] for e in d['entries']], ['CREDIT SALE', 'REPAYMENT', 'CREDIT SALE'])
        self.assertEqual([e['balance'] for e in d['entries']], [8900.0, 5900.0, 9900.0])
        self.assertEqual((d['opening_balance'], d['closing_balance'], d['balance']), (0.0, 9900.0, 9900.0))
        self.assertEqual((d['total_charges'], d['total_payments']), (12900.0, 3000.0))
        self.assertIn('Layers Mash x 2 (70kg bag)', d['entries'][0]['description'])
        self.assertEqual(d['unreconciled_gap'], 0.0)

    def test_gap_between_stored_balance_and_records_is_shown_not_hidden(self):
        self.sale(3, 1000, 0)
        self.cust.current_balance = 1500.0   # 500 that no recorded entry explains
        db.session.commit()
        d = self.admin_c.get(f'/api/customers/{self.cust.id}/statement').json
        self.assertEqual(d['unreconciled_gap'], 500.0)
        self.assertEqual(d['entries'][0]['balance'], 1500.0)
        self.assertEqual(d['closing_balance'], 1500.0)

    def test_date_range_carries_the_opening_balance_forward(self):
        self.sale(20, 5000, 0)
        self.pay(2, 1000)
        start = (datetime.utcnow() + timedelta(hours=3) - timedelta(days=5)).strftime('%Y-%m-%d')
        d = self.admin_c.get(f'/api/customers/{self.cust.id}/statement?from={start}').json
        self.assertEqual(d['opening_balance'], 5000.0)
        self.assertEqual([e['kind'] for e in d['entries']], ['REPAYMENT'])
        self.assertEqual(d['closing_balance'], 4000.0)
        self.assertEqual(self.admin_c.get(f'/api/customers/{self.cust.id}/statement?from=nonsense').status_code, 400)

    def test_every_role_can_read_history_but_only_for_their_own_outlet(self):
        self.sale(1, 700, 0, unit='KG', qty=10)
        for client in (self.admin_c, self.keeper_c):
            for path in ('statement', 'purchases', 'statement.pdf'):
                self.assertEqual(client.get(f'/api/customers/{self.cust.id}/{path}').status_code, 200, path)
        other = Customer(name='Elsewhere', location_id=self.branch.id)
        db.session.add(other)
        db.session.commit()
        for path in ('statement', 'purchases', 'statement.pdf'):
            self.assertEqual(self.keeper_c.get(f'/api/customers/{other.id}/{path}').status_code, 403, path)
        self.assertEqual(self.admin_c.get('/api/customers/99999/statement').status_code, 404)

    def test_pdf_is_a_real_pdf_with_a_safe_filename(self):
        self.cust.name = 'Mama <Wanjiru> & Sons'
        self.sale(1, 700, 0, unit='KG', qty=10)
        r = self.keeper_c.get(f'/api/customers/{self.cust.id}/statement.pdf')
        self.assertEqual(r.mimetype, 'application/pdf')
        self.assertTrue(r.data.startswith(b'%PDF'))
        self.assertIn('Emining_Statement_Mama_Wanjiru_Sons_', r.headers['Content-Disposition'])

    def test_purchases_include_cash_sales_and_convert_bags_to_kg(self):
        self.sale(5, 8900, 8900)                     # fully paid in cash: no debt, still a purchase
        self.sale(1, 700, 0, unit='KG', qty=10)
        d = self.keeper_c.get(f'/api/customers/{self.cust.id}/purchases').json
        self.assertEqual(d['total_orders'], 2)
        self.assertEqual((d['total_spent'], d['total_on_credit']), (9600.0, 700.0))
        newest = d['sales'][0]
        self.assertEqual((newest['items'][0]['kg'], newest['credit']), (10.0, 700.0))
        self.assertEqual(d['sales'][1]['items'][0]['kg'], 140.0)       # 2 x 70 kg bag
        self.assertEqual(d['top_items'][0]['name'], 'Layers Mash')
        self.assertEqual(d['top_items'][0]['kg'], 150.0)

    def test_purchases_paging_and_bad_numbers(self):
        for i in range(3):
            self.sale(i + 1, 100, 100, unit='KG', qty=1)
        d = self.admin_c.get(f'/api/customers/{self.cust.id}/purchases?limit=2').json
        self.assertEqual((len(d['sales']), d['total_orders']), (2, 3))
        self.assertEqual(self.admin_c.get(f'/api/customers/{self.cust.id}/purchases?limit=abc').status_code, 400)

    def test_repaying_from_the_customers_tab_lands_on_the_statement(self):
        self.sale(1, 2000, 0)
        r = self.admin_c.post(f'/api/customers/{self.cust.id}/repay',
                              json={'amount': 500, 'payment_method': 'MPESA', 'location_id': self.factory.id},
                              headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        d = self.keeper_c.get(f'/api/customers/{self.cust.id}/statement').json
        self.assertEqual(d['entries'][-1]['kind'], 'REPAYMENT')
        self.assertEqual((d['entries'][-1]['balance'], d['closing_balance']), (1500.0, 1500.0))

    def test_old_debt_appears_with_its_note(self):
        db.session.add(CustomerDebtEntry(reference='DEBT-T1', customer_id=self.cust.id, location_id=self.factory.id,
                                         amount=1200.0, note='feed taken in 2025', created_by='owner',
                                         created_at=self.day(30)))
        self.cust.current_balance = 1200.0
        db.session.commit()
        d = self.keeper_c.get(f'/api/customers/{self.cust.id}/statement').json
        self.assertEqual((d['entries'][0]['kind'], d['entries'][0]['description']), ('OLD DEBT', 'feed taken in 2025'))

    def test_dashboard_loads_the_statement_script_and_offers_the_buttons(self):
        html = self.admin_c.get('/').get_data(as_text=True)
        self.assertIn('/static/js/customer_statement.js', html)
        self.assertIn('openCustomerStatement(${c.id}, \'repay\')', html)      # Customers tab: Repay button
        self.assertIn('Statement &amp; purchases', html)                        # POS customer card
        js = open(os.path.join(os.path.dirname(__file__), '..', 'static', 'js', 'customer_statement.js')).read()
        self.assertIn('/repay', js)
        self.assertIn('navigator.canShare', js)
