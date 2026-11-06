import os
import tempfile
import unittest
import uuid

os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-deployment')

from app import create_app
from services.db import db
from services.models import (FeedIngredient, IngredientNutrient, Location, LocationStock, ProductionRun,
                             RequirementLine, RequirementProfile, SavedFormula, SavedFormulaLine, User)


class GateOverrideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='emining-gate-')
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only-secret-key-not-for-deployment',
                               'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + os.path.join(self.tmp.name, 't.db')})
        self.ctx = self.app.app_context(); self.ctx.push()
        self.factory = Location(name='Factory', code='FAC-01', location_type='FACTORY'); db.session.add(self.factory); db.session.flush()
        self.ids = {}
        for role in ('admin', 'factory'):
            u = User(username=role, role=role, location='FAC-01'); u.set_password('1234')
            db.session.add(u); db.session.flush(); u.location_id = self.factory.id; self.ids[role] = u.id
        self.raw = FeedIngredient(name='Maize', category='Raw Material', cost_per_kg=40.0, stock_quantity_kg=500.0)
        self.feed = FeedIngredient(name='Grower Mash', category='Finished Feed', cost_per_kg=50.0, retail_price_per_kg=70.0, bag_size_kg=70.0)
        db.session.add_all([self.raw, self.feed]); db.session.flush()
        db.session.add(LocationStock(location_id=self.factory.id, ingredient_id=self.raw.id, quantity_kg=500.0, unit_cost_per_kg=40.0))
        db.session.add(IngredientNutrient(ingredient_id=self.raw.id, nutrient_code='CP', value=9.0, source='test analysis'))
        self.profile = RequirementProfile(name='Strict', species='poultry', source='test'); db.session.add(self.profile); db.session.flush()
        db.session.add(RequirementLine(profile_id=self.profile.id, nutrient_code='CP', per='asfed', min_value=20.0, required=True, enabled=True, sort=0))
        self.formula = SavedFormula(name='Grower 1', profile_id=self.profile.id); db.session.add(self.formula); db.session.flush()
        db.session.add(SavedFormulaLine(formula_id=self.formula.id, ingredient_id=self.raw.id, pct=100.0))
        db.session.commit()

    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.ctx.pop(); self.tmp.cleanup()

    def produce(self, role='admin', **extra):
        c = self.app.test_client()
        with c.session_transaction() as s: s['user_id'] = self.ids[role]
        body = {'formula_name': 'Grower 1', 'formula_id': self.formula.id, 'location_id': self.factory.id,
                'output_ingredient_id': self.feed.id, 'planned_output_kg': 100, 'actual_output_kg': 99, 'loss_reason': 'dust',
                'inputs': [{'ingredient_id': self.raw.id, 'planned_qty_kg': 100, 'actual_qty_kg': 100}]}
        body.update(extra)
        return c.post('/api/factory/produce', json=body, headers={'Idempotency-Key': str(uuid.uuid4())})

    def test_blocked_by_default_and_tells_an_admin_they_can_override(self):
        r = self.produce('admin')
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.get_json()['can_override'])
        self.assertFalse(self.produce('factory').get_json()['can_override'])
        self.assertEqual(ProductionRun.query.count(), 0)

    def test_admin_override_needs_a_real_reason(self):
        r = self.produce('admin', override=True, override_reason='short')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(ProductionRun.query.count(), 0)

    def test_admin_override_records_who_why_and_what_failed(self):
        r = self.produce('admin', override=True, override_reason='Analysis pending; trial batch for the pullet flock')
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()['gate_status'], 'OVERRIDDEN')
        run = ProductionRun.query.one()
        self.assertEqual((run.gate_status, run.override_by), ('OVERRIDDEN', 'admin'))
        self.assertIn('Crude protein', run.gate_reasons)
        self.assertIn('trial batch', run.override_reason)
        hist = self.app.test_client()
        with hist.session_transaction() as s: s['user_id'] = self.ids['admin']
        row = hist.get(f'/api/factory/production-runs?location_id={self.factory.id}').get_json()[0]
        self.assertEqual(row['gate_status'], 'OVERRIDDEN')

    def test_only_admin_can_override_in_enforce_mode(self):
        r = self.produce('factory', override=True, override_reason='I need this batch today please')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(ProductionRun.query.count(), 0)

    def test_grace_mode_lets_production_roles_run_but_still_stamps_the_batch(self):
        self.app.config['FORMULA_GATE_MODE'] = 'grace'
        r = self.produce('factory')
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        run = ProductionRun.query.one()
        self.assertEqual((run.gate_status, run.override_by), ('GRACE', 'factory'))
        self.assertIn('Crude protein', run.gate_reasons)

    def test_passing_formula_is_stamped_pass_and_needs_no_override(self):
        db.session.get(IngredientNutrient, IngredientNutrient.query.filter_by(ingredient_id=self.raw.id).one().id).value = 25.0
        db.session.commit()
        r = self.produce('factory')
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(ProductionRun.query.one().gate_status, 'PASS')


if __name__ == '__main__':
    unittest.main()
