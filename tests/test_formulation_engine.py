import itertools
import os
import random
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-deployment')
from app import create_app
from services import formulation_engine as fe
from services.db import db
from services.models import FeedIngredient, IngredientNutrient, Nutrient


class EngineBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='emining-fe-')
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only-secret-key-not-for-deployment',
                               'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + os.path.join(self.tmp.name, 't.db')})
        self.ctx = self.app.app_context()
        self.ctx.push()
        FeedIngredient.query.filter(FeedIngredient.category.ilike('Raw%')).delete(synchronize_session=False)
        db.session.commit()
        fe.seed_nutrients()
        self.nut = fe.load_nutrients()

    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.ctx.pop(); self.tmp.cleanup()

    def add(self, name, price, **vals):
        ing = FeedIngredient(name=name, category='Raw - Test', cost_per_kg=price, max_inclusion_pct=vals.pop('max_pct', 100))
        db.session.add(ing); db.session.flush()
        for code, v in vals.items():
            if v is not None:
                db.session.add(IngredientNutrient(ingredient_id=ing.id, nutrient_code=code, value=v, source='test'))
        db.session.commit()
        return ing

    def info(self, ings):
        return {i.id: dict(name=i.name, max_pct=i.max_inclusion_pct, price=i.cost_per_kg) for i in ings}

    def line(self, code, lo=None, hi=None, per='asfed', scale=1.0, required=True):
        return fe.clean_line(dict(code=code, min=lo, max=hi, per=per, scale=scale, required=required), self.nut)

    def table(self, ings, lines):
        t, _ = fe.build_table([i.id for i in ings], self.nut, fe.needed_codes(lines, self.nut))
        return t


class SeedTests(EngineBase):
    def test_seed_is_idempotent_and_total_amino_acids_are_never_sid(self):
        n = Nutrient.query.count()
        fe.seed_nutrients()
        self.assertEqual(Nutrient.query.count(), n)
        amap = fe.alias_map(self.nut)
        self.assertNotIn('lysine', amap)               # a bare "Lysine" column is ambiguous: not guessed to be SID
        self.assertEqual(amap['sid lys'], 'SID_LYS')
        self.assertEqual(amap[fe.norm('Zn (mg/kg)')], 'ZN')


class EvaluateTests(EngineBase):
    def test_per_dm_and_percent_of_cp_lines_are_computed_exactly(self):
        a = self.add('A', 10, DM=90, CP=20, NEL=1.8, RDP=70, RUP=30, CA=1.0, TP=0.5)
        b = self.add('B', 10, DM=50, CP=10, NEL=1.0, RDP=50, RUP=50, CA=0.2, TP=0.1)
        lines = [self.line('NEL', per='dm'), self.line('RDP', per='CP', scale=100), self.line('CA', per='TP'),
                 self.line('DM'), self.line('CA')]
        t = self.table([a, b], lines)
        r = {x['code'] + x['per']: x['value'] for x in fe.evaluate({a.id: .5, b.id: .5}, lines, t)}
        # NEL per kg DM = (0.5*1.8*.9 + 0.5*1.0*.5) / (0.5*.9 + 0.5*.5)
        self.assertAlmostEqual(r['NELdm'], (0.5 * 1.8 * .9 + 0.5 * 1.0 * .5) / (0.5 * .9 + 0.5 * .5), places=9)
        # RDP % of CP = (0.5*.7*20 + 0.5*.5*10) / (0.5*20+0.5*10) *100
        self.assertAlmostEqual(r['RDPCP'], 100 * (0.5 * .7 * 20 + 0.5 * .5 * 10) / (0.5 * 20 + 0.5 * 10), places=9)
        self.assertAlmostEqual(r['CATP'], (0.5 * 1.0 + 0.5 * 0.2) / (0.5 * 0.5 + 0.5 * 0.1), places=9)
        self.assertAlmostEqual(r['DMasfed'], 70.0, places=9)

    def test_missing_value_is_unknown_not_zero(self):
        a = self.add('A', 10, CP=20)
        b = self.add('B', 10)                                 # no CP entered
        lines = [self.line('CP', lo=10)]
        res = fe.evaluate({a.id: .5, b.id: .5}, lines, self.table([a, b], lines))[0]
        self.assertEqual(res['status'], 'missing')
        self.assertIsNone(res['value'])


class OptimiseTests(EngineBase):
    def _grid_best(self, ings, lines, table, info, step=0.01):
        best, feas = None, False
        n = len(ings)
        k = int(round(1 / step))
        for combo in itertools.product(range(k + 1), repeat=n - 1):
            if sum(combo) > k:
                continue
            fr = [c / k for c in combo] + [(k - sum(combo)) / k]
            if any(f * 100 > info[i.id]['max_pct'] + 1e-9 for f, i in zip(fr, ings)):
                continue
            rec = {i.id: f for f, i in zip(fr, ings)}
            ev = fe.evaluate(rec, lines, table)
            if all(e['status'] != 'fail' for e in ev):
                feas = True
                c = sum(f * info[i.id]['price'] for f, i in zip(fr, ings))
                best = c if best is None else min(best, c)
        return feas, best

    def test_matches_brute_force_and_passes_independent_check(self):
        maize = self.add('Maize', 40, CP=9, ME=3.3, CA=0.02, DM=88)
        soya = self.add('Soya', 90, CP=44, ME=2.8, CA=0.3, DM=90)
        lime = self.add('Lime', 15, CP=0.0, ME=0.0, CA=38, DM=99, max_pct=8)
        ings = [maize, soya, lime]
        lines = [self.line('CP', lo=18), self.line('ME', lo=2.8), self.line('CA', lo=1.0, hi=1.4)]
        table, info = self.table(ings, lines), self.info(ings)
        r = fe.optimize([i.id for i in ings], lines, table, self.nut, info, batch_kg=1000)
        self.assertEqual(r['status'], 'optimal', r)
        feas, best = self._grid_best(ings, lines, table, info)
        self.assertTrue(feas)
        self.assertLessEqual(r['cost_per_kg'], best + 1e-9)           # LP can't be worse than a 1% grid
        self.assertLess(best - r['cost_per_kg'], 1.5)                  # ...and is close to it
        self.assertTrue(all(x['status'] in ('pass', 'info') for x in r['lines']))
        self.assertAlmostEqual(sum(x['kg'] for x in r['recipe']), 1000, places=1)

    def test_recipe_on_a_binding_limit_still_passes_after_rounding_to_4_decimals(self):
        # Regression: the optimiser sat exactly on Ca >= 1.0, and 4-decimal rounding of the percentages made
        # the saved formula fail its own verification by ~1e-6.
        rnd = random.Random(11)
        for _ in range(30):
            FeedIngredient.query.filter(FeedIngredient.category.ilike('Raw%')).delete(synchronize_session=False)
            IngredientNutrient.query.delete(); db.session.commit()
            ings = [self.add(f'I{k}', rnd.uniform(10, 90), CP=rnd.uniform(0, 45), CA=rnd.uniform(0, 38)) for k in range(4)]
            lines = [self.line('CP', lo=rnd.uniform(8, 25)), self.line('CA', lo=1.0, hi=1.5)]
            table, info = self.table(ings, lines), self.info(ings)
            r = fe.optimize([i.id for i in ings], lines, table, self.nut, info)
            if r['status'] != 'optimal':
                continue
            pct = {x['ingredient_id']: x['pct'] for x in r['recipe']}
            v = fe.verify(pct, lines, table, info)
            self.assertEqual(v['status'], 'pass', (v['issues'], r['safety_margin']))

    def test_ratio_and_dm_lines_are_enforced_by_the_solver(self):
        a = self.add('Hi-rdp', 20, DM=90, CP=30, RDP=80, RUP=20, NEL=1.5, CA=0.5, TP=0.5)
        b = self.add('Hi-rup', 60, DM=90, CP=40, RDP=40, RUP=60, NEL=2.0, CA=0.5, TP=0.2)
        lines = [self.line('RUP', lo=40, per='CP', scale=100), self.line('NEL', lo=1.7, per='dm'),
                 self.line('CA', lo=1.5, hi=3.0, per='TP')]
        r = fe.optimize([a.id, b.id], lines, self.table([a, b], lines), self.nut, self.info([a, b]))
        self.assertEqual(r['status'], 'optimal', r)
        v = {x['code']: x['value'] for x in r['lines']}
        self.assertGreaterEqual(v['RUP'] + 1e-6, 40)
        self.assertGreaterEqual(v['NEL'] + 1e-6, 1.7)
        self.assertGreaterEqual(v['CA'] + 1e-6, 1.5)

    def test_missing_data_blocks_until_fixed_or_excluded(self):
        a = self.add('A', 10, CP=20)
        b = self.add('B', 10)                       # CP unknown
        lines = [self.line('CP', lo=10)]
        args = ([a.id, b.id], lines, self.table([a, b], lines), self.nut, self.info([a, b]))
        r = fe.optimize(*args)
        self.assertEqual(r['status'], 'missing_data')
        self.assertEqual([m['ingredient'] for m in r['missing']], ['B'])
        r2 = fe.optimize(*args, exclude_missing=True)
        self.assertEqual(r2['status'], 'optimal')
        self.assertEqual([x['ingredient'] for x in r2['recipe']], ['A'])
        self.assertEqual(r2['excluded'], ['B'])

    def test_no_price_is_treated_as_missing_not_free(self):
        a = self.add('A', 0, CP=20)
        lines = [self.line('CP', lo=10)]
        r = fe.optimize([a.id], lines, self.table([a], lines), self.nut, self.info([a]))
        self.assertEqual(r['status'], 'missing_data')
        self.assertEqual(r['missing'][0]['nutrient'], 'Price')

    def test_infeasible_is_explained_in_plain_words(self):
        a = self.add('A', 10, CP=10, CA=0.1)
        b = self.add('B', 10, CP=12, CA=0.2)
        lines = [self.line('CP', lo=30)]
        r = fe.optimize([a.id, b.id], lines, self.table([a, b], lines), self.nut, self.info([a, b]))
        self.assertEqual(r['status'], 'infeasible')
        self.assertIn('Crude protein', r['diagnosis'][0])
        self.assertIn('12.000', r['diagnosis'][0])

    def test_clashing_limits_name_the_ones_to_relax(self):
        a = self.add('A', 10, CP=40, CA=0.1)
        b = self.add('B', 10, CP=5, CA=20)
        lines = [self.line('CP', lo=30), self.line('CA', lo=10)]            # each reachable alone, not together
        r = fe.optimize([a.id, b.id], lines, self.table([a, b], lines), self.nut, self.info([a, b]))
        self.assertEqual(r['status'], 'infeasible')
        self.assertIn('Relaxing any ONE', r['diagnosis'][0])

    def test_binding_limit_reports_its_cost(self):
        a = self.add('Cheap', 10, CP=10)
        b = self.add('Rich', 50, CP=40)
        lines = [self.line('CP', lo=20)]
        r = fe.optimize([a.id, b.id], lines, self.table([a, b], lines), self.nut, self.info([a, b]))
        s = r['sensitivity'][0]
        # CP = 10 + 30*x_rich; cost = 10 + 40*x_rich -> each extra 1 CP point costs 40/30 per kg
        self.assertAlmostEqual(s['cost_per_kg_per_unit_tighter'], 40 / 30, places=3)

    def test_rounding_to_weighing_precision_is_rechecked(self):
        a = self.add('A', 10, CP=10)
        b = self.add('B', 50, CP=40)
        lines = [self.line('CP', lo=20.0)]
        r = fe.optimize([a.id, b.id], lines, self.table([a, b], lines), self.nut, self.info([a, b]), batch_kg=7, precision_kg=1.0)
        self.assertEqual(sum(x['rounded_kg'] for x in r['recipe']), 7)
        self.assertIn('rounded_ok', r)

    def test_fuzz_never_reports_false_optimal_or_false_infeasible(self):
        rnd = random.Random(7)
        for _ in range(25):
            FeedIngredient.query.filter(FeedIngredient.category.ilike('Raw%')).delete(synchronize_session=False)
            IngredientNutrient.query.delete(); db.session.commit()
            ings = [self.add(f'I{k}', rnd.uniform(10, 90), CP=rnd.uniform(0, 45), ME=rnd.uniform(0, 3.5), CA=rnd.uniform(0, 30),
                             DM=rnd.uniform(80, 99), max_pct=rnd.choice([100, 60, 30])) for k in range(3)]
            lines = [self.line('CP', lo=rnd.uniform(5, 30)), self.line('ME', lo=rnd.uniform(0.5, 3.0)),
                     self.line('CA', lo=rnd.uniform(0, 2), hi=rnd.uniform(2, 6))]
            table, info = self.table(ings, lines), self.info(ings)
            r = fe.optimize([i.id for i in ings], lines, table, self.nut, info)
            feas, _ = self._grid_best(ings, lines, table, info, step=0.02)
            if r['status'] == 'optimal':
                rec = {x['ingredient_id']: x['pct'] / 100 for x in r['recipe']}
                self.assertTrue(all(e['status'] != 'fail' for e in fe.evaluate(rec, lines, table)))
            else:
                self.assertEqual(r['status'], 'infeasible', r)
                self.assertFalse(feas, 'solver said infeasible but a feasible recipe exists on the grid')


class VerifyTests(EngineBase):
    def setUp(self):
        super().setUp()
        self.a = self.add('A', 10, CP=20, CA=1.0, max_pct=60)
        self.b = self.add('B', 20, CP=40, CA=0.1)
        self.c = self.add('C', 5, CP=None)

    def v(self, rec, lines, **kw):
        ings = [self.a, self.b, self.c]
        return fe.verify(rec, lines, self.table(ings, lines), self.info(ings), **kw)

    def test_pass_and_block_cases(self):
        lines = [self.line('CP', lo=25), self.line('CA', lo=0.5)]
        ok = self.v({self.a.id: 50, self.b.id: 50}, lines)
        self.assertEqual((ok['status'], ok['releasable']), ('pass', True))
        bad = self.v({self.a.id: 90, self.b.id: 10}, lines)
        self.assertEqual(bad['status'], 'fail')
        self.assertFalse(bad['releasable'])
        self.assertTrue(any('above its limit of 60%' in i for i in bad['issues']))

    def test_total_must_be_100(self):
        r = self.v({self.a.id: 40, self.b.id: 40}, [self.line('CP', lo=1)])
        self.assertEqual(r['status'], 'fail')
        self.assertIn('not 100%', r['issues'][0])

    def test_missing_data_is_incomplete_and_blocks(self):
        r = self.v({self.a.id: 40, self.c.id: 60}, [self.line('CP', lo=1)])
        self.assertEqual((r['status'], r['releasable']), ('incomplete', False))
        self.assertIn('no value for C', r['issues'][0])

    def test_advisory_failure_warns_but_does_not_block(self):
        r = self.v({self.a.id: 50, self.b.id: 50}, [self.line('CP', lo=99, required=False)])
        self.assertTrue(r['releasable'])
        self.assertTrue(r['warnings'])

    def test_hash_changes_when_data_changes(self):
        lines = [self.line('CP', lo=1)]
        h1 = fe.formula_data_hash({self.a.id: 100}, lines, self.table([self.a], lines))
        row = IngredientNutrient.query.filter_by(ingredient_id=self.a.id, nutrient_code='CP').first()
        row.value = 21; db.session.commit()
        h2 = fe.formula_data_hash({self.a.id: 100}, lines, self.table([self.a], lines))
        self.assertNotEqual(h1, h2)

    def test_rdp_rup_sanity_warning(self):
        x = self.add('X', 10, CP=30, RDP=64.05, RUP=27.5)
        w = fe.rdp_rup_warnings([x.id], self.nut, {x.id: 'X'})
        self.assertEqual(len(w), 1)
        self.assertIn('91.55', w[0])


if __name__ == '__main__':
    unittest.main()
