import os, sys, uuid, unittest
sys.path.insert(0, os.path.dirname(__file__))
import test_pos as tp           # reuse the POS fixture: outlet, cashier, stock, open till
from sqlalchemy import event
db, FeedIngredient, LocationStock = tp.db, tp.FeedIngredient, tp.LocationStock


class FastSaleTests(tp.POSCheckoutTests):
    def _sell(self, qty, **kw):
        return self.client.post('/api/pos/checkout', json=self.payload(qty, 0, payments=[{'payment_method': 'CASH', 'amount': 40 * qty}]),
                                headers={'Idempotency-Key': str(uuid.uuid4())})

    def test_top_items_ranks_by_sales_and_reports_usual_pack(self):
        for _ in range(3):
            self.assertEqual(self._sell(1).status_code, 200)
        r = self.client.get(f'/api/pos/top-items?location_id={self.branch.id}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json[0]['ingredient_id'], self.item.id)
        self.assertEqual(r.json[0]['sales'], 3)
        self.assertEqual(r.json[0]['pack_kg'], 1.0)

    def test_top_items_empty_for_outlet_with_no_sales(self):
        r = self.client.get(f'/api/pos/top-items?location_id={self.branch.id}')
        self.assertEqual((r.status_code, r.json), (200, []))

    def test_top_items_requires_login(self):
        c = self.app.test_client()
        self.assertIn(c.get('/api/pos/top-items').status_code, (401, 403))

    def test_search_stock_matches_and_query_count_does_not_grow_with_catalogue(self):
        for i in range(40):
            it = FeedIngredient(name=f'Bulk {i}', category='Finished Feed', cost_per_kg=30, retail_price_per_kg=40, bag_size_kg=50)
            db.session.add(it); db.session.flush()
            db.session.add(LocationStock(location_id=self.branch.id, ingredient_id=it.id, quantity_kg=100 + i, unit_cost_per_kg=30,
                                         reserved_quantity_kg=5))
        db.session.commit()
        counts = []
        @event.listens_for(db.engine, 'before_cursor_execute')
        def _c(*a): counts.append(1)
        r = self.client.get(f'/api/products/search?location_id={self.branch.id}')
        self.assertLess(len(counts), 12)                       # was 2 queries per item (80+)
        row = next(x for x in r.json if x['name'] == 'Bulk 7')
        self.assertEqual(row['available_stock_kg'], 107 - 5)  # quantity minus reserved
        self.assertEqual(next(x for x in r.json if x['id'] == self.item.id)['available_stock_kg'],
                         LocationStock.query.filter_by(location_id=self.branch.id, ingredient_id=self.item.id).one().quantity_kg
                         - (LocationStock.query.filter_by(location_id=self.branch.id, ingredient_id=self.item.id).one().reserved_quantity_kg or 0))

    def test_search_item_with_no_stock_row_shows_zero(self):
        it = FeedIngredient(name='Never stocked', category='Finished Feed', cost_per_kg=1, retail_price_per_kg=2)
        db.session.add(it); db.session.commit()
        r = self.client.get(f'/api/products/search?location_id={self.branch.id}&q=never')
        self.assertEqual(r.json[0]['available_stock_kg'], 0.0)


for _n in [n for n in dir(tp.POSCheckoutTests) if n.startswith('test_')]:
    setattr(FastSaleTests, _n, None)    # run only the new tests, not the inherited ones

if __name__ == '__main__':
    unittest.main()
