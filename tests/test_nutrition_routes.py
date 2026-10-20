import io
import os
import tempfile
import unittest
import uuid

os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-deployment')
from app import create_app
from services.db import db
from services.models import (FeedIngredient, IngredientNutrient, Location, RequirementProfile, SavedFormula,
                             SavedFormulaLine, User)

USER_TABLE = """Dry Matter\t0.00\t88.70\t100\t%
NEL (Mcal/kg DM)\t1.60\t1.60\t100\tMcal/kg
RDP (% of CP)\t60.00\t64.05\t100\t% of CP
RUP (% of CP)\t35.00\t27.50\t100\t% of CP
NDF (%)\t22.00\t22.35\t100\t%
Calcium\t1.00\t1.03\t100\t%
Total Phos.\t0.00\t0.92\t100\t%
Avail. Phos.\t0.35\t0.36\t100\t%
Ca:P=\t0.00\t-0.15\t0\t-
K\t0.80\t1.27\t100\t%
Mn\t50.00\t72.91\t1000\tmg/kg
Zn\t50.00\t57.37\t1000\tmg/kg
Mystery\t1\t2\t3\t%"""


class NutritionRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='emining-nr-')
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only-secret-key-not-for-deployment',
                               'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + os.path.join(self.tmp.name, 't.db')})
        self.ctx = self.app.app_context(); self.ctx.push()
        FeedIngredient.query.filter(FeedIngredient.category.ilike('Raw%')).delete(synchronize_session=False)
        loc = Location(name='F', code='F-1', location_type='FACTORY'); db.session.add(loc); db.session.flush()
        self.users = {}
        for role in ('admin', 'factory', 'sales', 'warehouse'):
            u = User(username=role, role=role, location='F-1'); u.set_password('1234')
            db.session.add(u); db.session.flush(); u.location_id = loc.id; self.users[role] = u.id
        self.maize = FeedIngredient(name='Maize', category='Raw - Energy', cost_per_kg=40)
        self.soya = FeedIngredient(name='Soybean meal', category='Raw - Protein', cost_per_kg=90)
        db.session.add_all([self.maize, self.soya]); db.session.commit()

    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.ctx.pop(); self.tmp.cleanup()

    def c(self, role='factory'):
        c = self.app.test_client()
        with c.session_transaction() as s: s['user_id'] = self.users[role]
        return c

    def post(self, url, role='factory', **kw):
        return self.c(role).post(url, **kw)

    # ---- access ------------------------------------------------------------------------------
    def test_bootstrap_has_nutrients_and_starter_profile_and_is_role_gated(self):
        d = self.c('factory').get('/api/nutrition/bootstrap').get_json()
        codes = {n['code'] for n in d['nutrients']}
        self.assertTrue({'CP', 'SID_LYS', 'NEL', 'RDP'} <= codes)
        self.assertIn('Dairy concentrate (from your sheet)', [p['name'] for p in d['profiles']])
        self.assertEqual(self.c('sales').get('/api/nutrition/bootstrap').status_code, 403)
        self.assertEqual(self.c('warehouse').get('/api/nutrition/bootstrap').status_code, 403)

    # ---- poultry SID-AA ----------------------------------------------------------------------
    def test_poultry_sid_profiles_ship_lines_but_no_invented_limits(self):
        d = self.c('factory').get('/api/nutrition/bootstrap').get_json()
        names = [p['name'] for p in d['profiles'] if p['species'] == 'poultry']
        self.assertEqual(len(names), 6)
        self.assertIn('Poultry - Layer (SID-AA)', names)
        prof = next(p for p in d['profiles'] if p['name'] == 'Poultry - Layer (SID-AA)')
        lines = self.c('factory').get(f"/api/nutrition/profiles/{prof['id']}").get_json()['lines']
        self.assertEqual(len(lines), 21)
        self.assertTrue(all(l['min'] is None and l['max'] is None for l in lines))      # nothing invented
        codes = {(l['code'], l['per']) for l in lines}
        self.assertIn(('SID_LYS', 'asfed'), codes)
        met = next(l for l in lines if l['code'] == 'SID_MET' and l['per'] == 'SID_LYS')
        self.assertEqual((met['scale'], met['unit']), (100.0, '% of SID Lysine'))
        from services import formulation_engine as fe
        fe.seed_templates()                                                          # idempotent
        self.assertEqual(RequirementProfile.query.filter_by(species='poultry').count(), 6)

    def test_poultry_profile_with_lysine_and_ratio_limits_optimises_and_verifies(self):
        c = self.c('factory')
        cells = []
        for iid, vals in ((self.maize.id, dict(SID_LYS=0.22, SID_MET=0.16, CP=8.5)), (self.soya.id, dict(SID_LYS=2.6, SID_MET=0.55, CP=44))):
            cells += [dict(ingredient_id=iid, code=k, value=v, source='test') for k, v in vals.items()]
        c.post('/api/nutrition/ingredient-values', json={'cells': cells})
        lines = [{'code': 'SID_LYS', 'min': 0.9}, {'code': 'SID_MET', 'per': 'SID_LYS', 'scale': 100, 'min': 28},
                 {'code': 'SID_MC', 'per': 'SID_LYS', 'scale': 100}]            # last one has no limit: display only
        r = c.post('/api/nutrition/optimize', json={'ingredient_ids': [self.maize.id, self.soya.id], 'lines': lines}).get_json()
        self.assertEqual(r['status'], 'optimal', r)
        got = {(x['code'], x['per']): x for x in r['lines']}
        self.assertGreaterEqual(got[('SID_LYS', 'asfed')]['value'] + 1e-6, 0.9)
        self.assertGreaterEqual(got[('SID_MET', 'SID_LYS')]['value'] + 1e-6, 28)
        self.assertEqual(got[('SID_MC', 'SID_LYS')]['status'], 'missing')              # no SID M+C data: shown as unknown, not zero
        v = c.post('/api/nutrition/verify', json={'recipe': [{'ingredient_id': x['ingredient_id'], 'pct': x['pct']} for x in r['recipe']], 'lines': lines}).get_json()
        self.assertEqual(v['status'], 'pass', v)           # the display-only line with no data must not block release
        # With these two ingredients, 0.9% SID lysine forces Met:Lys to about 30%, so a 38% minimum is genuinely impossible:
        tough = [lines[0], dict(lines[1], min=38)]
        r2 = c.post('/api/nutrition/optimize', json={'ingredient_ids': [self.maize.id, self.soya.id], 'lines': tough}).get_json()
        self.assertEqual(r2['status'], 'infeasible')
        self.assertIn('Relaxing any ONE', r2['diagnosis'][0])

    # ---- ingredient import ---------------------------------------------------------------------
    def test_paste_import_previews_then_commits_and_never_guesses_sid_or_zero(self):
        text = 'Ingredient\tCP\tCa\tSID Lys\tLysine\tSource\nMaize\t8.5\t0.02\t\t0.25\tlab 2026\nSoya bean meal\t44\t0.3\t2.6\t2.9\t\nGhost\t1\t1\t1\t1\t'
        pre = self.post('/api/nutrition/import/ingredients', json={'text': text}).get_json()
        self.assertFalse(pre['committed'])
        self.assertTrue(any('Lysine' in h and 'never assumed to be SID' in h for h in pre['unknown_columns']))
        st = {r['name']: r['status'] for r in pre['rows']}
        self.assertEqual(st, {'Maize': 'ok', 'Soya bean meal': 'confirm', 'Ghost': 'no_match'})
        self.assertEqual(IngredientNutrient.query.count(), 0)                    # preview writes nothing
        soya_row = next(r for r in pre['rows'] if r['name'] == 'Soya bean meal')
        done = self.post('/api/nutrition/import/ingredients',
                         json={'text': text, 'commit': True, 'overrides': {str(soya_row['row']): self.soya.id}}).get_json()
        self.assertEqual(done['skipped_rows'], [pre['rows'][2]['row']] if False else done['skipped_rows'])
        vals = {(r.ingredient_id, r.nutrient_code): (r.value, r.source) for r in IngredientNutrient.query.all()}
        self.assertEqual(vals[(self.maize.id, 'CP')], (8.5, 'lab 2026'))
        self.assertNotIn((self.maize.id, 'SID_LYS'), vals)                       # blank cell: not written, not zero
        self.assertEqual(vals[(self.soya.id, 'SID_LYS')][0], 2.6)
        self.assertFalse(any(code == 'LYS_TOT' for (_, code) in vals))           # bare "Lysine" was not guessed

    def test_non_numeric_cells_are_reported_not_stored(self):
        text = 'Ingredient\tCP\tCa\nMaize\t#VALUE!\t0.02'
        r = self.post('/api/nutrition/import/ingredients', json={'text': text, 'commit': True}).get_json()
        self.assertEqual(r['error_count'], 1)
        got = {x.nutrient_code for x in IngredientNutrient.query.all()}
        self.assertEqual(got, {'CA'})

    def test_sheet_with_nutrients_down_the_side_is_flipped(self):
        text = 'Nutrient\tMaize\tSoybean meal\nCP\t8.5\t44\nCa\t0.02\t0.3'
        r = self.post('/api/nutrition/import/ingredients', json={'text': text, 'commit': True}).get_json()
        self.assertTrue(r['transposed'])
        self.assertEqual(IngredientNutrient.query.filter_by(ingredient_id=self.soya.id, nutrient_code='CP').first().value, 44)

    def test_cells_can_be_set_and_cleared(self):
        c = self.c('factory')
        c.post('/api/nutrition/ingredient-values', json={'cells': [{'ingredient_id': self.maize.id, 'code': 'CP', 'value': 9, 'source': 'lab'}]})
        self.assertEqual(IngredientNutrient.query.count(), 1)
        c.post('/api/nutrition/ingredient-values', json={'cells': [{'ingredient_id': self.maize.id, 'code': 'CP', 'value': None}]})
        self.assertEqual(IngredientNutrient.query.count(), 0)
        bad = c.post('/api/nutrition/ingredient-values', json={'cells': [{'ingredient_id': self.maize.id, 'code': 'CP', 'value': -1}]})
        self.assertEqual(bad.status_code, 400)

    # ---- requirements import ----------------------------------------------------------------------
    def test_your_pasted_table_becomes_correct_lines(self):
        pre = self.post('/api/nutrition/import/requirements', json={'text': USER_TABLE}).get_json()
        by = {(l['code'], l['per']): l for l in pre['lines']}
        self.assertEqual((by[('DM', 'asfed')]['min'], by[('DM', 'asfed')]['max']), (None, None))   # 0 and 100 = no limit
        nel = by[('NEL', 'dm')]; self.assertEqual((nel['min'], nel['max']), (1.6, None))
        rup = by[('RUP', 'CP')]; self.assertEqual((rup['min'], rup['scale']), (35.0, 100.0))
        self.assertIn(('CA', 'TP'), by)                                                          # Ca:P is a ratio line
        self.assertEqual(by[('MN', 'asfed')]['min'], 50.0)
        self.assertEqual(by[('ZN', 'asfed')]['max'], None)                                       # 1000 mg/kg placeholder
        self.assertTrue(any('Mystery' in p for p in pre['problems']))
        done = self.post('/api/nutrition/import/requirements', json={'text': USER_TABLE, 'commit': True, 'profile_name': 'Test dairy'}).get_json()
        self.assertEqual(done['status'], 'success')
        self.assertEqual(RequirementProfile.query.filter_by(name='Test dairy').first().lines[1].nutrient_code, 'NEL')

    # ---- optimise / verify through the API ---------------------------------------------------------------
    def test_optimise_reports_missing_data_then_solves(self):
        lines = [{'code': 'CP', 'min': 20}, {'code': 'CA', 'min': 0.1}]
        body = {'ingredient_ids': [self.maize.id, self.soya.id], 'lines': lines, 'batch_kg': 1000}
        r = self.post('/api/nutrition/optimize', json=body).get_json()
        self.assertEqual(r['status'], 'missing_data')
        c = self.c('factory')
        c.post('/api/nutrition/ingredient-values', json={'cells': [
            {'ingredient_id': self.maize.id, 'code': 'CP', 'value': 9}, {'ingredient_id': self.maize.id, 'code': 'CA', 'value': 0.02},
            {'ingredient_id': self.soya.id, 'code': 'CP', 'value': 44}, {'ingredient_id': self.soya.id, 'code': 'CA', 'value': 0.3}]})
        r = self.post('/api/nutrition/optimize', json=body).get_json()
        self.assertEqual(r['status'], 'optimal', r)
        v = self.post('/api/nutrition/verify', json={'recipe': [{'ingredient_id': i['ingredient_id'], 'pct': i['pct']} for i in r['recipe']], 'lines': lines}).get_json()
        self.assertEqual(v['status'], 'pass', v)
        self.assertEqual(self.post('/api/nutrition/optimize', role='sales', json=body).status_code, 403)

    # ---- release gate ---------------------------------------------------------------------------------------
    def _controlled_formula(self):
        c = self.c('factory')
        c.post('/api/nutrition/ingredient-values', json={'cells': [
            {'ingredient_id': self.maize.id, 'code': 'CP', 'value': 9}, {'ingredient_id': self.soya.id, 'code': 'CP', 'value': 44}]})
        pid = c.post('/api/nutrition/profiles', json={'name': 'P1', 'species': 'poultry', 'lines': [{'code': 'CP', 'min': 20}]}).get_json()['id']
        r = c.post('/api/nutrition/formulas', json={'name': 'Layer X', 'profile_id': pid, 'recipe': [
            {'ingredient_id': self.maize.id, 'pct': 50}, {'ingredient_id': self.soya.id, 'pct': 50}]}).get_json()
        return pid, r

    def test_passing_formula_is_releasable_and_failing_one_is_blocked_everywhere(self):
        pid, r = self._controlled_formula()
        self.assertTrue(r['releasable'], r)
        fid = r['id']
        self.assertTrue(self.c('warehouse').get(f'/api/nutrition/formulas/{fid}/release-check').get_json()['ok'])
        # the data changes after approval: soya protein is revised down. The gate re-checks live.
        row = IngredientNutrient.query.filter_by(ingredient_id=self.soya.id, nutrient_code='CP').first()
        row.value = 20; db.session.commit()
        chk = self.c('warehouse').get(f'/api/nutrition/formulas/{fid}/release-check').get_json()
        self.assertFalse(chk['ok'])
        self.assertIn('Crude protein', chk['reasons'][0])
        # and production refuses it
        resp = self.post('/api/factory/produce', json={'formula_name': 'Layer X'}, headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(resp.status_code, 409, resp.get_data(as_text=True))
        self.assertIn('blocked', resp.get_json()['message'])

    def test_profile_limits_cannot_be_loosened_on_screen_to_pass_a_save(self):
        c = self.c('factory')
        c.post('/api/nutrition/ingredient-values', json={'cells': [
            {'ingredient_id': self.maize.id, 'code': 'CP', 'value': 9}, {'ingredient_id': self.soya.id, 'code': 'CP', 'value': 44}]})
        pid = c.post('/api/nutrition/profiles', json={'name': 'P2', 'lines': [{'code': 'CP', 'min': 30}]}).get_json()['id']
        r = c.post('/api/nutrition/formulas', json={'name': 'Weak', 'profile_id': pid, 'lines': [{'code': 'CP', 'min': 1}],
                                                    'recipe': [{'ingredient_id': self.maize.id, 'pct': 80}, {'ingredient_id': self.soya.id, 'pct': 20}]}).get_json()
        self.assertFalse(r['releasable'])                    # verified against the SAVED profile, not the on-screen edit

    def test_formulas_without_a_profile_are_not_blocked(self):
        fm = SavedFormula(name='Old formula'); db.session.add(fm); db.session.flush()
        db.session.add(SavedFormulaLine(formula_id=fm.id, ingredient_id=self.maize.id, pct=100)); db.session.commit()
        resp = self.post('/api/factory/produce', json={'formula_name': 'Old formula'}, headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertNotEqual(resp.status_code, 409)

    def test_profile_in_use_cannot_be_deleted(self):
        pid, _ = self._controlled_formula()
        self.assertEqual(self.c('admin').delete(f'/api/nutrition/profiles/{pid}').status_code, 409)


if __name__ == '__main__':
    unittest.main()
