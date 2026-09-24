import sys
import os
import unittest
import concurrent.futures
import uuid
from sqlalchemy.pool import StaticPool

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from services.db import db
from services.models import FeedIngredient, Customer, OrderHeader
from services.pos_service import process_full_pos_checkout

class POSStressTestSuite(unittest.TestCase):

    def setUp(self):
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://' 
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'poolclass': StaticPool, 'connect_args': {'check_same_thread': False}}
        
        self.app_context = app.app_context()
        self.app_context.push()
        db.create_all()

        self.product = FeedIngredient(
            name="Test Layer Mash",
            category="Poultry",
            cost_per_kg=30.0,
            retail_price_per_kg=40.0,
            stock_quantity_kg=100.0, 
            reserved_quantity_kg=0.0,
            bag_size_kg=70.0
        )
        
        self.credit_cust = Customer(
            name="VIP Farmer",
            phone="0700000000",
            customer_type="RETAIL",
            credit_limit=20000.0,
            current_balance=0.0
        )

        self.zero_credit_cust = Customer(
            name="New Farmer",
            phone="0711111111",
            customer_type="RETAIL",
            credit_limit=0.0,
            current_balance=0.0
        )

        db.session.add_all([self.product, self.credit_cust, self.zero_credit_cust])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_01_stock_exhaustion_boundary(self):
        payload = {
            "sale_id": f"TEST-OVER-{uuid.uuid4().hex[:6]}",
            "cart": [{"ingredient_id": self.product.id, "unit_type": "KG", "qty": 100.1}],
            "payments": [{"payment_method": "CASH", "amount": 4004.0}]
        }
        with self.assertRaises(Exception) as context:
            process_full_pos_checkout(payload)
        self.assertIn("INSUFFICIENT STOCK", str(context.exception))
        
        p = db.session.get(FeedIngredient, self.product.id)
        self.assertEqual(p.stock_quantity_kg, 100.0)

    def test_02_credit_limit_strict_enforcement(self):
        payload = {
            "sale_id": f"TEST-CRED-{uuid.uuid4().hex[:6]}",
            "customer_id": self.zero_credit_cust.id,
            "cart": [{"ingredient_id": self.product.id, "unit_type": "KG", "qty": 10.0}],
            "payments": [{"payment_method": "CREDIT", "amount": 400.0}]
        }
        with self.assertRaises(Exception) as context:
            process_full_pos_checkout(payload)
        self.assertIn("CREDIT LIMIT EXCEEDED", str(context.exception))

    def test_03_automatic_bag_discount_math(self):
        payload = {
            "sale_id": f"TEST-DISC-{uuid.uuid4().hex[:6]}",
            "cart": [{"ingredient_id": self.product.id, "unit_type": "BAG", "bag_size_kg": 70.0, "qty": 1}],
            "payments": [{"payment_method": "CASH", "amount": 2660.0}]
        }
        res = process_full_pos_checkout(payload)
        self.assertEqual(res['subtotal'], 2800.0)
        self.assertEqual(res['discount'], 140.0)
        self.assertEqual(res['total_amount'], 2660.0)

    def test_04_concurrent_checkouts_stock_lock(self):
        def run_checkout(worker_id):
            with app.app_context():
                payload = {
                    "sale_id": f"RACE-{worker_id}-{uuid.uuid4().hex[:4]}",
                    "cart": [{"ingredient_id": self.product.id, "unit_type": "KG", "qty": 20.0}],
                    "payments": [{"payment_method": "CASH", "amount": 800.0}]
                }
                try:
                    process_full_pos_checkout(payload)
                    return True
                except Exception:
                    return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(run_checkout, i) for i in range(10)]
            results = [f.result() for f in futures]

        successful_sales = results.count(True)
        failed_sales = results.count(False)

        self.assertEqual(successful_sales, 5)
        self.assertEqual(failed_sales, 5)
        
        db.session.expire_all()
        p = db.session.get(FeedIngredient, self.product.id)
        self.assertEqual(p.stock_quantity_kg, 0.0)

    def test_05_idempotency_duplicate_prevention(self):
        sale_key = "STRESS-IDEM-9999"
        payload = {
            "sale_id": sale_key,
            "cart": [{"ingredient_id": self.product.id, "unit_type": "KG", "qty": 10.0}],
            "payments": [{"payment_method": "CASH", "amount": 400.0}]
        }
        
        with app.test_client() as client:
            res1 = client.post('/api/pos/checkout', json=payload, headers={'X-Idempotency-Key': sale_key})
            self.assertEqual(res1.status_code, 200)

            res2 = client.post('/api/pos/checkout', json=payload, headers={'X-Idempotency-Key': sale_key})
            self.assertEqual(res2.status_code, 200)
            self.assertEqual(res2.json['status'], 'already_processed')

        p = db.session.get(FeedIngredient, self.product.id)
        self.assertEqual(p.stock_quantity_kg, 90.0)

        order_count = OrderHeader.query.filter_by(sale_id=sale_key).count()
        self.assertEqual(order_count, 1)

if __name__ == '__main__':
    unittest.main()