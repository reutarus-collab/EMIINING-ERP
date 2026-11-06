import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from test_grpo_and_customers import Base
from services.models import FeedIngredient, IngredientNutrient, LocationStock, PurchaseOrderLine
from services.db import db


class ReceivingTests(Base):
    def test_po_line_saved_with_broken_bag_rule_can_still_be_received_by_weight(self):
        line = PurchaseOrderLine.query.get(self.line_id)
        line.purchase_uom, line.conversion_type, line.conversion_factor = 'BAG', 'FIXED', 1.0    # the old default trap
        db.session.commit()
        lines = self.make_client(self.admin_id).get(f'/api/po/{self.po_id}/lines?location_id={self.factory.id}').json['lines']
        self.assertEqual((lines[0]['conversion_type'], lines[0]['rule_damaged']), ('VARIABLE', True))
        r = self.post(str(uuid.uuid4()), {'payment_method': 'ON_ACCOUNT', 'received_items': [
            {'line_id': self.line_id, 'qty_po_uom': 4, 'qty_rejected_po_uom': 0, 'qty_kg_accepted': 212.5}]})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(self.stock(), 212.5)

    def test_stale_cost_on_empty_stock_does_not_block_and_real_jump_can_be_confirmed(self):
        db.session.add(LocationStock(location_id=self.factory.id, ingredient_id=self.item.id, quantity_kg=0.0, unit_cost_per_kg=5.0))
        db.session.commit()
        self.assertEqual(self.post(str(uuid.uuid4()), self.body(2)).status_code, 200)
        LocationStock.query.filter_by(ingredient_id=self.item.id).update({'unit_cost_per_kg': 5.0})
        db.session.commit()
        key = str(uuid.uuid4())
        r = self.post(key, self.body(2))
        self.assertEqual((r.status_code, r.json['code']), (409, 'COST_JUMP'))
        body = self.body(2); body['accept_cost_change'] = True
        self.assertEqual(self.post(key, body).status_code, 200)


WUFFDA = ("poultry and pigs parameter\t\tCost\tMin.\tMax.\tWeight\tDry Matter\tM.E.\tProtein\tE.E.\tC18:2\tC.F\tCalcium\t"
          "Total Phos.\tAvail. Phos.\tCa:P=\tK\tCl\tMn\tNa\tZn\tCholine\tFolate\tARG\tLYS\tMET\tTSAA\tBiotin\n"
          "Maize\t\t38\t0\t60\t0\t88.0\t3,350\t8.5\t3.8\t1.9\t2.2\t0.02\t0.28\t0.08\t-\t0.33\t0.05\t7\t0.02\t15\t620\t0.2\t0.4\t0.24\t0.18\t0.4\t#N/A\n"
          "Brand New Meal\t\t55\t0\t25\t0\t90\t2,800\t44\t1.5\t0.5\t6\t0.3\t0.65\t0.2\t\t2.1\t0.07\t40\t0.03\t55\t2,800\t0.4\t1.0\t2.7\t0.6\t1.3\t0.3")


class ImportTests(Base):
    def test_wuffda_paste_reads_headers_thousands_and_creates_missing(self):
        c = self.make_client(self.admin_id)
        r = c.post('/api/nutrition/import/ingredients', json={'text': WUFFDA, 'commit': False, 'create_missing': 'true', 'apply_max': 'true'})
        self.assertEqual(r.status_code, 200, r.json)
        d = r.json
        self.assertEqual(d['unknown_columns'], [], d['unknown_columns'])
        statuses = {x['name']: x['status'] for x in d['rows']}
        self.assertEqual(statuses['Brand New Meal'], 'new')
        r = c.post('/api/nutrition/import/ingredients', json={'text': WUFFDA, 'commit': True, 'create_missing': 'true', 'apply_max': 'true', 'new_category': 'Raw - Protein'})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json['created'], 2)   # neither sheet row exists yet in this test DB
        new = FeedIngredient.query.filter_by(name='Brand New Meal').one()
        self.assertEqual((new.category, new.cost_per_kg, new.max_inclusion_pct), ('Raw - Protein', 0.0, 25.0))   # Cost column is never imported
        vals = {n.nutrient_code: n.value for n in IngredientNutrient.query.filter_by(ingredient_id=new.id)}
        self.assertEqual(vals['ME_KCAL'], 2800.0)      # 2,800 is a thousands separator, not 2.8
        self.assertEqual(vals['CHOLINE'], 2800.0)
        self.assertEqual(vals['CP'], 44.0)
        self.assertEqual(vals['FAT'], 1.5)
        self.assertEqual(vals['LYS_TOT'], 2.7)
        self.assertEqual(vals['ARG_TOT'], 1.0)
        self.assertEqual(vals['TSAA_TOT'], 1.3)
        self.assertNotIn('RATIO', vals)
        maize = FeedIngredient.query.filter_by(name='Maize').one()
        mv = {n.nutrient_code: n.value for n in IngredientNutrient.query.filter_by(ingredient_id=maize.id)}
        self.assertNotIn('BIOTIN', mv)                 # #N/A is skipped, never zero
        self.assertEqual(mv['ME_KCAL'], 3350.0)


if __name__ == '__main__':
    import unittest
    unittest.main()


# ---------------------------------------------------------------------------------------------------------------
import io
from datetime import datetime
from services.models import ItemPrice, InventoryTransfer, OrderHeader, OrderLine, PaymentSplit, PurchaseOrderHeader, PurchaseOrderLine


def _workbook():
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.title = 'Title'
    wb.active['B2'] = 'WUFFF DA Student'
    ws = wb.create_sheet('Nutrients')            # a requirements sheet full of repeated Min./Max. columns
    ws.append([None, None, 'Min.', 'Max.', 'Units', 'Min.', 'Max.', 'Min.', 'Max.'])
    ws.append([None, 'Protein', 18, 100, '%', 23, 100, 20, 100])
    ws = wb.create_sheet('Ingredients')
    ws.append([None, None, None, 'WUFFF DA'])
    ws.append([None, None, 'Cost', 'Min.', 'Max.', 'Weight', 'Dry Matter', 'M.E.', 'Protein', 'Total Phos.', 'Choline', 'LYS'])
    ws.append([None, None, None, None, None, None, None, None, None, 1.5])
    ws.append([None, None, 'ACTIVE INGREDIENT COMPOSITION MATRIX'])
    ws.append([None, 'Corn, Grain', 46, None, 100, 1, 89, 3.34, 8.8, 0.29, 1200, 0.25])
    ws.append([None, 'Wheat Bran', 34, None, 30, 1, 89, 2.5, 13, 0.9, 1100, 0.5])
    ws.append([None, 'Brand New Meal', 80, None, 100, 1, 90, 2.8, 44, 0.65, 2800, 2.7])
    ws = wb.create_sheet('Ingredients (Poultry)')   # two-row headings: Dry / Matter, Total / Phos.
    ws.append([None, None, None, None, None, None, 'Dry', None, None, None, None, None, None, 'Total'])
    ws.append([None, None, 'Cost', 'Min.', 'Max.', 'Weight', 'Matter', 'M.E.', 'Protein', 'E.E.', 'C18:2', 'C.F', 'Calcium', 'Phos.'])
    ws.append([None, 'Wheat Bran', None, None, 100, 1, 89, 2.5, 13, 3, 2, 11, 0.02, 0.9])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


class WorkbookImportTests(Base):
    def setUp(self):
        super().setUp()
        db.session.add_all([FeedIngredient(name='Maize', category='Raw - Energy', cost_per_kg=40.0, max_inclusion_pct=60.0),
                            FeedIngredient(name='Wheat Bran', category='Raw - Energy', cost_per_kg=30.0, max_inclusion_pct=25.0)])
        db.session.commit()

    def _post(self, **extra):
        data = {'file': (_workbook(), 'growers.xlsx'), 'commit': 'false'}
        data.update(extra)
        return self.make_client(self.admin_id).post('/api/nutrition/import/ingredients', data=data, content_type='multipart/form-data')

    def test_picks_the_ingredient_sheet_not_the_title_or_requirements_sheet(self):
        r = self._post()
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json['sheet'], 'Ingredients')
        self.assertIn('Ingredients (Poultry)', r.json['sheets'])
        names = {x['name']: x['status'] for x in r.json['rows']}
        self.assertEqual(names['Corn, Grain'], 'ok')            # Corn = Maize
        self.assertEqual(names['Wheat Bran'], 'ok')
        self.assertEqual(names['Brand New Meal'], 'no_match')    # never creates ingredients unless asked
        self.assertTrue(any('Cost' in c for c in r.json['ignored_columns']))

    def test_two_row_headings_are_joined_and_commit_writes_values(self):
        r = self._post(sheet='Ingredients (Poultry)', commit='true')
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json['unknown_columns'], [])
        bran = FeedIngredient.query.filter_by(name='Wheat Bran').one()
        vals = {n.nutrient_code: n.value for n in IngredientNutrient.query.filter_by(ingredient_id=bran.id)}
        self.assertEqual(vals['DM'], 89.0)
        self.assertEqual(vals['TP'], 0.9)
        self.assertEqual(vals['ME'], 2.5)

    def test_max_100_never_overwrites_a_real_limit_and_real_max_is_applied_and_cost_untouched(self):
        r = self._post(commit='true', apply_max='true')
        self.assertEqual(r.status_code, 200, r.json)
        maize = FeedIngredient.query.filter_by(name='Maize').one()
        bran = FeedIngredient.query.filter_by(name='Wheat Bran').one()
        self.assertEqual(maize.max_inclusion_pct, 60.0)      # sheet said 100 = no limit
        self.assertEqual(bran.max_inclusion_pct, 30.0)
        self.assertEqual((maize.cost_per_kg, bran.cost_per_kg), (40.0, 30.0))   # $/cwt costs are never imported

    def test_single_row_can_be_created_as_new_from_the_preview(self):
        first = self._post().json
        row = next(x['row'] for x in first['rows'] if x['name'] == 'Brand New Meal')
        import json
        r = self._post(commit='true', overrides=json.dumps({str(row): 'new'}), new_category='Raw - Protein')
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json['created'], 1)
        new = FeedIngredient.query.filter_by(name='Brand New Meal').one()
        self.assertEqual(new.category, 'Raw - Protein')
        self.assertEqual(new.cost_per_kg, 0.0)


class BagPriceTests(Base):
    def test_any_bag_size_per_item_and_old_two_size_api_still_works(self):
        c = self.make_client(self.admin_id)
        r = c.post('/api/admin/packs', json={'id': self.item.id, 'packs': [{'pack_kg': 45, 'price': 2500}, {'pack_kg': 40, 'price': 2300}]})
        self.assertEqual(r.status_code, 200, r.json)
        sizes = sorted(p.pack_kg for p in ItemPrice.query.filter_by(ingredient_id=self.item.id))
        self.assertEqual(sizes, [40.0, 45.0])
        listed = next(i for i in c.get('/api/admin/packs').json if i['id'] == self.item.id)
        self.assertEqual([p['pack_kg'] for p in listed['packs']], [40.0, 45.0])
        # replacing the list removes the sizes that are no longer in it
        c.post('/api/admin/packs', json={'id': self.item.id, 'packs': [{'pack_kg': 45, 'price': 2600}]})
        self.assertEqual([(p.pack_kg, p.price) for p in ItemPrice.query.filter_by(ingredient_id=self.item.id)], [(45.0, 2600.0)])
        old = c.post('/api/admin/packs', json={'id': self.item.id, 'p50': 2600, 'p70': ''})
        self.assertEqual(old.status_code, 200, old.json)
        self.assertEqual(sorted(p.pack_kg for p in ItemPrice.query.filter_by(ingredient_id=self.item.id)), [45.0, 50.0])  # 45 is not a legacy size: kept

    def test_bad_bag_rows_are_refused(self):
        c = self.make_client(self.admin_id)
        for packs, text in (([{'pack_kg': 45, 'price': 1000}], 'not above its cost'),       # cost 40/kg * 45 = 1800
                            ([{'pack_kg': 45, 'price': 3000}, {'pack_kg': 45, 'price': 3100}], 'twice'),
                            ([{'pack_kg': 1, 'price': 3000}], 'more than 1 kg'),
                            ([{'pack_kg': 45, 'price': ''}], 'both a bag size')):
            r = c.post('/api/admin/packs', json={'id': self.item.id, 'packs': packs})
            self.assertEqual(r.status_code, 400, packs)
            self.assertIn(text, r.json['message'])


class TransferAndReportTests(Base):
    def setUp(self):
        super().setUp()
        self.item2 = FeedIngredient(name='Test Bran', category='Raw Material', cost_per_kg=30.0)
        db.session.add(self.item2)
        db.session.flush()
        db.session.add_all([LocationStock(location_id=self.factory.id, ingredient_id=self.item.id, quantity_kg=500.0, unit_cost_per_kg=40.0),
                            LocationStock(location_id=self.factory.id, ingredient_id=self.item2.id, quantity_kg=100.0, unit_cost_per_kg=30.0)])
        db.session.commit()

    def _send(self, items, key=None):
        return self.make_client(self.admin_id).post('/api/inventory/transfers', json={
            'from_location_id': self.factory.id, 'to_location_id': self.branch.id, 'items': items, 'reason': 'Weekly restock'},
            headers={'Idempotency-Key': key or str(uuid.uuid4())})

    def qty(self, item, loc=None):
        row = LocationStock.query.filter_by(location_id=(loc or self.factory).id, ingredient_id=item.id).first()
        return row.quantity_kg if row else 0.0

    def test_many_items_go_in_one_dispatch_and_can_be_received(self):
        r = self._send([{'ingredient_id': self.item.id, 'quantity_kg': 200}, {'ingredient_id': self.item2.id, 'quantity_kg': 40}])
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json['lines'], 2)
        self.assertEqual((self.qty(self.item), self.qty(self.item2)), (300.0, 60.0))
        rows = InventoryTransfer.query.order_by(InventoryTransfer.id).all()
        self.assertEqual([t.status for t in rows], ['IN_TRANSIT', 'IN_TRANSIT'])
        for t in rows:
            rr = self.make_client(self.admin_id).post(f'/api/inventory/transfers/{t.id}/receive', json={'quantity_kg': t.quantity_kg},
                                                      headers={'Idempotency-Key': str(uuid.uuid4())})
            self.assertEqual(rr.status_code, 200, rr.json)
        self.assertEqual((self.qty(self.item, self.branch), self.qty(self.item2, self.branch)), (200.0, 40.0))

    def test_one_short_line_stops_the_whole_transfer(self):
        r = self._send([{'ingredient_id': self.item.id, 'quantity_kg': 200}, {'ingredient_id': self.item2.id, 'quantity_kg': 400}])
        self.assertEqual(r.status_code, 400)
        self.assertIn('Insufficient stock', r.json['message'])
        self.assertEqual((self.qty(self.item), self.qty(self.item2)), (500.0, 100.0))   # first line was rolled back
        self.assertEqual(InventoryTransfer.query.count(), 0)

    def test_same_item_twice_is_merged_and_old_single_item_body_still_works(self):
        r = self._send([{'ingredient_id': self.item.id, 'quantity_kg': 100}, {'ingredient_id': self.item.id, 'quantity_kg': 50}])
        self.assertEqual((r.status_code, r.json['lines']), (200, 1))
        self.assertEqual(self.qty(self.item), 350.0)
        old = self.make_client(self.admin_id).post('/api/inventory/transfers', json={
            'from_location_id': self.factory.id, 'to_location_id': self.branch.id, 'ingredient_id': self.item2.id,
            'quantity_kg': 10, 'reason': 'Single item'}, headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(old.status_code, 200, old.json)

    def test_top_products_ranks_by_sales_and_subtracts_nothing_it_should_not(self):
        now = datetime.utcnow()
        o = OrderHeader(sale_id='S1', total_amount=9500, status='COMPLETED', created_at=now, location_id=self.factory.id)
        db.session.add(o)
        db.session.flush()
        db.session.add_all([OrderLine(order_id=o.id, ingredient_id=self.item.id, unit_type='45KG BAG', qty_entered=2, subtotal=9000, unit_cost_per_kg=40.0),
                            OrderLine(order_id=o.id, ingredient_id=self.item2.id, unit_type='KG', qty_entered=10, subtotal=500, unit_cost_per_kg=30.0)])
        db.session.commit()
        r = self.make_client(self.admin_id).get(f'/api/sales-history/top-products?period=today&location_id={self.factory.id}')
        self.assertEqual(r.status_code, 200, r.json)
        top = r.json['products']
        self.assertEqual([p['name'] for p in top], ['Test Maize', 'Test Bran'])
        self.assertEqual((top[0]['kg_sold'], top[0]['bags_sold'], top[0]['sales']), (90.0, 2.0, 9000.0))
        self.assertEqual(top[0]['profit'], 9000 - 90 * 40.0)
        self.assertEqual(top[0]['share_pct'], 94.7)
        self.assertEqual(self.make_client(self.admin_id).get('/api/sales-history/top-products?period=bogus').status_code, 400)

    def test_delete_purchase_order_only_when_nothing_received(self):
        c = self.make_client(self.admin_id)
        # received -> refused
        self.assertEqual(self.post(str(uuid.uuid4()), self.body(2)).status_code, 200)
        r = c.delete(f'/api/po/{self.po_id}?location_id={self.factory.id}')
        self.assertEqual(r.status_code, 409)
        # a fresh untouched order -> deleted
        made = c.post('/api/po', json={'location_id': self.factory.id, 'supplier_id': self.supplier.id,
                                       'items': [{'ingredient_id': self.item.id, 'qty': 3, 'unit_cost': 2000}]},
                      headers={'Idempotency-Key': str(uuid.uuid4())})
        po = PurchaseOrderHeader.query.filter_by(po_number=made.json['po_number']).one()
        d = c.delete(f'/api/po/{po.id}?location_id={self.factory.id}')
        self.assertEqual(d.status_code, 200, d.json)
        self.assertIsNone(PurchaseOrderHeader.query.get(po.id))
        self.assertEqual(PurchaseOrderLine.query.filter_by(po_header_id=po.id).count(), 0)
        # the keeper cannot delete
        self.assertEqual(self.client.delete(f'/api/po/{self.po_id}?location_id={self.factory.id}').status_code, 403)
