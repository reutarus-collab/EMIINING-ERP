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
        self.assertEqual((new.category, new.cost_per_kg, new.max_inclusion_pct), ('Raw - Protein', 55.0, 25.0))
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
