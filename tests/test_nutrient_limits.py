import os
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-deployment')
from app import create_app
from services.db import db
from services.formulator_service import solve_feed_formulation
from services.models import AnimalRequirement, FeedIngredient, Location, User


class NutrientLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='emining-nut-')
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only-secret-key-not-for-deployment',
                               'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + os.path.join(self.tmp.name, 't.db')})
        self.ctx = self.app.app_context()
        self.ctx.push()
        FeedIngredient.query.filter(FeedIngredient.category.ilike('Raw%')).delete(synchronize_session=False)
        self.loc = Location(name='F', code='F-1', location_type='FACTORY')
        db.session.add(self.loc)
        mk = lambda n, c, cp, me, ca=0, p=0, lys=0, met=0, mx=100: FeedIngredient(
            name=n, category='Raw - Test', cost_per_kg=c, crude_protein_pct=cp, metabolizable_energy_mcal=me,
            calcium_pct=ca, phosphorus_pct=p, lysine_pct=lys, methionine_pct=met, max_inclusion_pct=mx)
        db.session.add_all([mk('Maize', 40, 9, 3.3, 0.02, 0.1, 0.25, 0.18), mk('Soya', 90, 44, 2.8, 0.3, 0.2, 2.7, 0.6),
                            mk('Limestone', 15, 0, 0, 38, 0, 0, 0, mx=10)])
        db.session.commit()

    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.ctx.pop(); self.tmp.cleanup()

    def solve(self, **extra):
        return solve_feed_formulation('Test', 1000, None, self.loc.id, 16, 2.5, None, False, extra=extra)

    def test_baseline_without_limits_has_no_extra_constraints(self):
        r = self.solve()
        self.assertEqual(r['status'], 'optimal')
        self.assertEqual(r['extra_targets']['min_calcium'], 0.0)

    def test_calcium_range_forces_limestone_and_is_met(self):
        r = self.solve(min_calcium=3.5, max_calcium=4.2)
        self.assertEqual(r['status'], 'optimal')
        self.assertTrue(3.5 - 0.001 <= r['achieved']['calcium_pct'] <= 4.2 + 0.001)
        self.assertIn('Limestone', [x['ingredient_name'] for x in r['recipe']])

    def test_lysine_minimum_raises_cost_and_is_met(self):
        base = self.solve()
        r = self.solve(min_lysine=1.0)
        self.assertEqual(r['status'], 'optimal')
        self.assertGreaterEqual(r['achieved']['lysine_pct'] + 0.001, 1.0)
        self.assertGreaterEqual(r['cost_per_kg'], base['cost_per_kg'])

    def test_infeasible_limit_is_diagnosed_in_plain_words(self):
        r = self.solve(min_methionine=5.0)
        self.assertEqual(r['status'], 'infeasible')
        self.assertIn('methionine', r['message'].lower())

    def test_missing_nutrient_data_warns(self):
        FeedIngredient.query.update({'phosphorus_pct': 0}); db.session.commit()
        r = self.solve(min_phosphorus=0.5)
        self.assertEqual(r['status'], 'infeasible')
        self.assertTrue(any('phosphorus' in w for w in r['warnings']))

    def test_bad_limits_rejected(self):
        self.assertEqual(self.solve(min_calcium=-1)['status'], 'error')
        self.assertEqual(self.solve(min_calcium=5, max_calcium=2)['status'], 'error')

    def test_saved_stage_defaults_are_used(self):
        db.session.add(AnimalRequirement(species_stage='Test', min_cp=16, min_me=2.5, max_fiber=0, min_calcium=3.5))
        db.session.commit()
        r = self.solve()
        self.assertGreaterEqual(r['achieved']['calcium_pct'] + 0.001, 3.5)

    def test_requirements_and_nutrient_routes_roundtrip(self):
        u = User(username='boss', role='admin', location='x'); u.set_password('1234')
        db.session.add(u); db.session.commit()
        c = self.app.test_client()
        with c.session_transaction() as s: s['user_id'] = u.id
        r = c.post('/api/formulate/requirements', json={'species': 'Layer', 'min_cp': 17, 'min_me': 2.7, 'max_fiber': 6,
                                                         'min_calcium': 3.5, 'max_calcium': 4.2, 'min_lysine': 0.8})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        got = c.get('/api/formulate/requirements?species=Layer').get_json()
        self.assertEqual((got['min_calcium'], got['max_calcium'], got['min_lysine']), (3.5, 4.2, 0.8))
        bad = c.post('/api/formulate/requirements', json={'species': 'Layer', 'min_cp': 17, 'min_me': 2.7, 'min_calcium': 5, 'max_calcium': 2})
        self.assertEqual(bad.status_code, 400)
        item = FeedIngredient.query.filter_by(name='Maize').first()
        ok = c.post(f'/api/po/inventory/{item.id}/nutrients', json={'crude_protein_pct': 9, 'metabolizable_energy_mcal': 3.3,
                    'crude_fiber_pct': 2, 'max_inclusion_pct': 100, 'calcium_pct': 0.05, 'lysine_pct': 0.3})
        self.assertEqual(ok.status_code, 200, ok.get_data(as_text=True))
        db.session.expire_all()
        self.assertEqual((item.calcium_pct, item.lysine_pct, item.phosphorus_pct), (0.05, 0.3, 0.1))


if __name__ == '__main__':
    unittest.main()
