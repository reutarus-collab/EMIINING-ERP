import os, sys, uuid
sys.path.insert(0, os.path.dirname(__file__))
from test_grpo_and_customers import Base      # PO/GRN harness; its owner has NO outlet assigned on purpose
from services.db import db
from services.models import GeneralLedgerEntry, GoodsReceiptNote, PurchaseOrderHeader, TillSession


def key():
    return {'Idempotency-Key': str(uuid.uuid4())}


class PurchaseOrderRejectTests(Base):
    def test_whole_delivery_can_be_rejected_without_a_payment_method(self):
        body = {'received_items': [{'line_id': self.line_id, 'qty_po_uom': 0, 'qty_rejected_po_uom': 10, 'qty_kg_accepted': 0}]}
        r = self.post(str(uuid.uuid4()), body)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.stock(), 0.0)
        self.assertEqual(db.session.get(PurchaseOrderHeader, self.po_id).status, 'FULLY_RECEIVED')

    def test_accepting_goods_still_requires_a_payment_method(self):
        body = self.body(4); body.pop('payment_method')
        r = self.post(str(uuid.uuid4()), body)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(GoodsReceiptNote.query.count(), 0)         # nothing was kept
        self.assertEqual(self.stock(), 0.0)

    def test_nonsense_payment_method_is_refused(self):
        self.assertEqual(self.post(str(uuid.uuid4()), self.body(4, method='CHEQUE')).status_code, 400)


class OwnerOutletAndCashTests(Base):
    def owner(self):
        return self.make_client(self.admin_id)

    def test_owner_without_an_outlet_can_still_search_products(self):
        r = self.owner().get('/api/products/search?q=&category=&location_id=')
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertIsInstance(r.json, list)

    def test_owner_writes_still_need_an_explicit_outlet(self):
        r = self.owner().post('/api/till/cash-movements', json={'movement_type': 'PAID_OUT', 'amount': 5, 'reason': 'test run'}, headers=key())
        self.assertEqual(r.status_code, 400)
        self.assertIn('outlet', r.json['message'])

    def open_till(self, cash=1000.0):
        db.session.add(TillSession(location_id=self.factory.id, cashier_name='owner', opening_cash=cash,
                                   expected_cash=cash, status='OPEN', open_key=f'{self.factory.id}:owner'))
        db.session.commit()

    def move(self, kind, amount, reason='note here'):
        return self.owner().post('/api/till/cash-movements', headers=key(), json={
            'location_id': self.factory.id, 'movement_type': kind, 'amount': amount, 'reason': reason})

    def test_deposit_to_mpesa_reduces_till_cash_and_posts_to_mpesa_clearing(self):
        self.open_till(1000.0)
        r = self.move('MPESA_DEPOSIT', 300, 'M-Pesa deposit')
        self.assertEqual(r.status_code, 201, r.json)
        self.assertEqual(r.json['expected_cash'], 700.0)
        rows = {(g.account_code): (g.debit, g.credit) for g in GeneralLedgerEntry.query.filter_by(transaction_ref=r.json['reference']).all()}
        self.assertEqual(rows, {'1010': (300.0, 0.0), '1000': (0.0, 300.0)})

    def test_remove_from_counter_and_add_float_still_work(self):
        self.open_till(1000.0)
        self.assertEqual(self.move('PAID_OUT', 100, 'to safe').json['expected_cash'], 900.0)
        self.assertEqual(self.move('PAID_IN', 50, 'float').json['expected_cash'], 950.0)

    def test_cannot_move_more_cash_than_the_till_holds_and_bad_type_is_refused(self):
        self.open_till(100.0)
        self.assertEqual(self.move('MPESA_DEPOSIT', 5000).status_code, 400)
        self.assertEqual(self.move('PAID_OUT', 5000).status_code, 400)
        self.assertEqual(self.move('BANANA', 5).status_code, 400)

    def test_history_and_daily_report_show_the_deposit(self):
        self.open_till(1000.0)
        self.move('MPESA_DEPOSIT', 300, 'M-Pesa deposit')
        hist = self.owner().get('/api/till/cash-movements?location_id=%d' % self.factory.id).json['movements']
        self.assertEqual(hist[0]['type'], 'MPESA_DEPOSIT')
        rep = self.owner().get('/api/reports/daily?location_id=%d' % self.factory.id).json
        self.assertEqual(rep['cash_to_mpesa'], 300.0)
        self.assertEqual(rep['cash_paid_out'], 0.0)


if __name__ == '__main__':
    import unittest
    unittest.main()
