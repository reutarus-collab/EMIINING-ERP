"""Offline sales: the sale time is audit-only, bounded, and never re-dates the books."""
import os
import unittest
from datetime import datetime, timedelta

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-deployment')
from services.pos_service import _offline_sold_at

ROOT = os.path.join(os.path.dirname(__file__), '..')


def _read(rel):
    return open(os.path.join(ROOT, rel), encoding='utf-8').read()


class OfflineSoldAtTests(unittest.TestCase):
    def test_absent_is_fine(self):
        self.assertIsNone(_offline_sold_at(None))
        self.assertIsNone(_offline_sold_at(''))

    def test_recent_utc_time_is_accepted_and_naive(self):
        when = datetime.utcnow() - timedelta(hours=3)
        got = _offline_sold_at(when.isoformat(timespec='seconds') + 'Z')
        self.assertIsNone(got.tzinfo)
        self.assertLess(abs((got - when).total_seconds()), 2)

    def test_garbage_future_and_stale_are_refused(self):
        for bad in ('yesterday', (datetime.utcnow() + timedelta(hours=1)).isoformat() + 'Z',
                    (datetime.utcnow() - timedelta(days=8)).isoformat() + 'Z'):
            with self.assertRaises(ValueError, msg=bad):
                _offline_sold_at(bad)


class OfflineClientPolicyTests(unittest.TestCase):
    def test_payload_is_frozen_once_a_request_may_have_reached_the_server(self):
        js = _read('static/js/pos.js')
        self.assertIn('attempt.sentOnce = true', js)
        self.assertIn('!uncertain && !attempt.sentOnce', js)

    def test_only_server_rejections_can_be_discarded_and_a_reason_is_logged(self):
        js = _read('static/js/pos.js')
        self.assertIn("sale.status !== 400", js)
        self.assertIn('logDiscard', js)

    def test_login_page_wipes_cached_customers_but_keeps_unsynced_sales(self):
        auth = _read('routes/auth.py')
        self.assertIn("objectStore('cache').clear()", auth)
        self.assertNotIn("objectStore('sales').clear()", auth)


if __name__ == '__main__':
    unittest.main()


# ---- end to end through the real checkout route (reuses the POS fixtures) ----
import uuid
import tests.test_pos as _tp
from services.models import OrderHeader, LocationStock


class OfflineCheckoutRouteTests(unittest.TestCase):
    # borrow the POS fixtures without inheriting (and re-running) the whole POS suite
    setUp, tearDown, make_client, payload = (_tp.POSCheckoutTests.setUp, _tp.POSCheckoutTests.tearDown,
                                             _tp.POSCheckoutTests.make_client, _tp.POSCheckoutTests.payload)
    tempdir = None

    def _sale(self, key, **extra):
        return self.client.post('/api/pos/checkout', json=self.payload(2, 80, **extra),
                                headers={'Idempotency-Key': key})

    def test_offline_time_is_stored_and_replay_is_a_no_op(self):
        key = str(uuid.uuid4())
        sold = (datetime.utcnow() - timedelta(hours=2)).replace(microsecond=0)
        r1 = self._sale(key, offline_sold_at=sold.isoformat() + 'Z')
        self.assertEqual(r1.status_code, 200, r1.json)
        order = OrderHeader.query.one()
        self.assertEqual(order.offline_sold_at, sold)
        self.assertGreater(order.created_at, sold + timedelta(hours=1))     # books still post at sync time
        r2 = self._sale(key, offline_sold_at=sold.isoformat() + 'Z')
        self.assertTrue(r2.json.get('already_processed'))
        self.assertEqual(OrderHeader.query.count(), 1)
        self.assertEqual(LocationStock.query.filter_by(location_id=self.branch.id).one().quantity_kg, 98.0)

    def test_adding_the_offline_time_to_an_already_sent_key_is_refused_not_double_posted(self):
        key = str(uuid.uuid4())
        self.assertEqual(self._sale(key).status_code, 200)
        r = self._sale(key, offline_sold_at=datetime.utcnow().isoformat() + 'Z')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(OrderHeader.query.count(), 1)

    def test_bad_offline_time_is_a_400_and_posts_nothing(self):
        r = self._sale(str(uuid.uuid4()), offline_sold_at='not-a-date')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(OrderHeader.query.count(), 0)
