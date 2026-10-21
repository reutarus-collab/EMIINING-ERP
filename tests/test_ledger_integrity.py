"""The ledger must never hold an unbalanced journal, and a failed posting must leave nothing behind."""
import math
import unittest
import uuid
from unittest import mock

import test_pos as _tp          # module import only, so pytest does not collect its tests twice
from services.db import db
from services.ledger_service import LedgerImbalanceError, post_gl_entry, post_gl_transaction
from services.models import (Customer, GeneralLedgerEntry, IdempotencyKey, LocationStock, OrderHeader,
                             PaymentSplit, StockMovement, TillSession)


class LedgerIntegrityTests(unittest.TestCase):
    setUp = _tp.POSCheckoutTests.setUp
    tearDown = _tp.POSCheckoutTests.tearDown
    make_client = _tp.POSCheckoutTests.make_client
    payload = _tp.POSCheckoutTests.payload

    # ---- the single posting function ------------------------------------------------------
    def assert_rejected_and_nothing_added(self, legs, **kw):
        with self.assertRaises(LedgerImbalanceError):
            post_gl_transaction('T-BAD', legs, 'TEST', 1, **kw)
        self.assertEqual([o for o in db.session.new if isinstance(o, GeneralLedgerEntry)], [])
        db.session.commit()
        self.assertEqual(GeneralLedgerEntry.query.count(), 0)

    def test_unbalanced_journal_is_rejected(self):
        self.assert_rejected_and_nothing_added([('1000', 100, 0), ('4000', 0, 90)])

    def test_single_leg_is_rejected(self):
        self.assert_rejected_and_nothing_added([('1000', 100, 0)])

    def test_negative_two_sided_zero_and_nan_legs_are_rejected(self):
        self.assert_rejected_and_nothing_added([('1000', -50, 0), ('4000', 0, -50)])
        self.assert_rejected_and_nothing_added([('1000', 50, 50), ('4000', 0, 0)])
        self.assert_rejected_and_nothing_added([('1000', 100, 0), ('4000', 0, 100), ('5000', 0, 0)])
        self.assert_rejected_and_nothing_added([('1000', math.nan, 0), ('4000', 0, math.nan)])

    def test_a_late_bad_leg_means_no_earlier_leg_is_added(self):
        self.assert_rejected_and_nothing_added([('1000', 100, 0), ('4000', 0, 100), ('5000', 0, 7)])

    def test_balanced_journal_posts_every_leg(self):
        post_gl_transaction('T-OK', [('1000', 100, 0), ('4000', 0, 60), ('2000', 0, 40)], 'TEST', 1)
        db.session.commit()
        rows = GeneralLedgerEntry.query.filter_by(transaction_ref='T-OK').all()
        self.assertEqual(len(rows), 3)
        self.assertAlmostEqual(sum(r.debit for r in rows), sum(r.credit for r in rows))

    def test_skip_zero_drops_empty_legs_but_still_demands_balance(self):
        post_gl_transaction('T-Z', [('1000', 50, 0), ('5360', 0, 0), ('4000', 0, 50)], 'TEST', 1, skip_zero=True)
        db.session.commit()
        self.assertEqual(GeneralLedgerEntry.query.filter_by(transaction_ref='T-Z').count(), 2)

    def test_one_sided_post_gl_entry_is_retired(self):
        with self.assertRaises(RuntimeError):
            post_gl_entry('X', '1000', 10, 0, 'TEST', 1)

    # ---- commit-time safety net -------------------------------------------------------------
    def test_commit_is_blocked_if_code_adds_ledger_rows_directly_and_unbalanced(self):
        db.session.add(GeneralLedgerEntry(transaction_ref='SNEAK', account_code='1000', debit=10.0, credit=0.0))
        with self.assertRaises(LedgerImbalanceError):
            db.session.commit()
        db.session.rollback()
        self.assertEqual(GeneralLedgerEntry.query.count(), 0)

    def test_a_balanced_journal_split_across_flushes_still_commits(self):
        db.session.add(GeneralLedgerEntry(transaction_ref='SPLIT', account_code='1000', debit=10.0, credit=0.0))
        db.session.flush()                                   # e.g. an autoflush caused by a query in between
        db.session.add(GeneralLedgerEntry(transaction_ref='SPLIT', account_code='4000', debit=0.0, credit=10.0))
        db.session.commit()
        self.assertEqual(GeneralLedgerEntry.query.filter_by(transaction_ref='SPLIT').count(), 2)

    # ---- a failed posting leaves no stock, payment, till or customer change -----------------
    def _snapshot(self):
        db.session.rollback()
        db.session.expire_all()
        return dict(
            stock=LocationStock.query.filter_by(location_id=self.branch.id, ingredient_id=self.item.id).one().quantity_kg,
            till=db.session.get(TillSession, self.till.id).expected_cash,
            balance=db.session.get(Customer, self.customer.id).current_balance,
            orders=OrderHeader.query.count(), payments=PaymentSplit.query.count(),
            movements=StockMovement.query.filter_by(movement_type='POS_SALE').count(),
            gl=GeneralLedgerEntry.query.count(), keys=IdempotencyKey.query.count())

    def _mixed_sale(self):
        self.customer.credit_limit = 5000.0
        db.session.commit()
        return self.payload(10, 0, customer_id=self.customer.id,
                            payments=[{'payment_method': 'CASH', 'amount': 200}, {'payment_method': 'CREDIT', 'amount': 200}])

    def test_failed_ledger_posting_leaves_no_stock_payment_till_or_debt_behind(self):
        body = self._mixed_sale()
        before = self._snapshot()
        with mock.patch('services.ledger_service.post_gl_transaction', side_effect=RuntimeError('disk on fire')):
            response = self.client.post('/api/pos/checkout', json=body, headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertGreaterEqual(response.status_code, 400, response.json)
        self.assertEqual(self._snapshot(), before)

    def test_unbalanced_journal_that_slips_past_posting_is_stopped_at_commit(self):
        body = self._mixed_sale()
        before = self._snapshot()

        def sneaky(ref, legs, module, source_id, skip_zero=False):
            db.session.add(GeneralLedgerEntry(transaction_ref=ref, account_code='4000', debit=0.0, credit=999.0))
        with mock.patch('services.ledger_service.post_gl_transaction', side_effect=sneaky):
            response = self.client.post('/api/pos/checkout', json=body, headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertGreaterEqual(response.status_code, 400, response.json)
        self.assertEqual(self._snapshot(), before)

    def test_a_good_sale_posts_a_balanced_journal_and_all_its_effects(self):
        body = self._mixed_sale()
        before = self._snapshot()
        response = self.client.post('/api/pos/checkout', json=body, headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200, response.json)
        after = self._snapshot()
        self.assertEqual(after['stock'], before['stock'] - 10)
        self.assertEqual(after['orders'], before['orders'] + 1)
        self.assertEqual(after['balance'], before['balance'] + 200)
        rows = GeneralLedgerEntry.query.filter_by(source_type='POS').all()
        self.assertAlmostEqual(sum(r.debit for r in rows), sum(r.credit for r in rows), places=2)
        accounts = {r.account_code for r in rows}
        self.assertTrue({'4000', '1000', '1300', '5000', '1200'} <= accounts, accounts)


if __name__ == '__main__':
    unittest.main()
