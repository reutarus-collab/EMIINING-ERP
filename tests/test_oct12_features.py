import os
import tempfile
import unittest
import uuid
from datetime import date

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-deployment')
from app import create_app
from services.db import db
from services.models import (
    Account, CashWalkOpening, FeedIngredient, GeneralLedgerEntry, ItemPrice, Location, LocationStock,
    PaymentSplit, SavedFormula, SavedFormulaLine, TillSession, User,
)


class Oct12FeatureTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix='emining-oct12-')
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test-only-secret-key-not-for-deployment',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + os.path.join(self.tempdir.name, 't.db'),
        })
        self.context = self.app.app_context()
        self.context.push()
        self.branch = Location(name='Retail Outlet', code='RET-01', location_type='BRANCH_STORE')
        db.session.add(self.branch)
        db.session.flush()
        self.cashier = User(username='cashier', role='sales', location='RET-01')
        self.cashier.set_password('7351')
        self.admin = User(username='boss', role='admin', location='RET-01')
        self.admin.set_password('1234')
        db.session.add_all([self.cashier, self.admin])
        db.session.flush()
        self.cashier.location_id = self.branch.id
        self.admin.location_id = self.branch.id
        self.item = FeedIngredient(name='Test Layer Mash', category='Finished Feed', cost_per_kg=30.0,
                                   stock_quantity_kg=100.0, retail_price_per_kg=40.0, bag_size_kg=70.0)
        self.maize = FeedIngredient(name='Maize', category='Raw - Energy', cost_per_kg=30.0)
        self.soy = FeedIngredient(name='Soya', category='Raw - Protein', cost_per_kg=60.0)
        db.session.add_all([self.item, self.maize, self.soy])
        db.session.flush()
        db.session.add(LocationStock(location_id=self.branch.id, ingredient_id=self.item.id,
                                     quantity_kg=100.0, unit_cost_per_kg=30.0))
        db.session.add(TillSession(location_id=self.branch.id, cashier_name='cashier', opening_cash=0.0,
                                   expected_cash=0.0, status='OPEN', open_key=f'{self.branch.id}:cashier'))
        db.session.commit()
        self.cashier_id, self.admin_id = self.cashier.id, self.admin.id

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.tempdir.cleanup()

    def client_for(self, user_id):
        c = self.app.test_client()
        with c.session_transaction() as sess:
            sess['user_id'] = user_id
        return c

    def checkout(self, payments, qty=10):
        return self.client_for(self.cashier_id).post(
            '/api/pos/checkout', headers={'Idempotency-Key': str(uuid.uuid4())},
            json={'location_id': self.branch.id,
                  'cart': [{'ingredient_id': self.item.id, 'unit_type': 'KG', 'qty': qty, 'bag_size_kg': 1}],
                  'payments': payments})

    # ---- M-Pesa / bank deduction -------------------------------------------------
    def test_mpesa_deduction_is_booked_as_expense_and_net_to_account(self):
        r = self.checkout([{'payment_method': 'MPESA', 'amount': 400, 'fee': 2.2}])
        self.assertEqual(r.status_code, 200, r.json)
        split = PaymentSplit.query.one()
        self.assertEqual(split.channel_fee, 2.2)
        rows = {(e.account_code): e for e in GeneralLedgerEntry.query.all()}
        self.assertAlmostEqual(rows['1010'].debit, 397.8, places=2)
        self.assertAlmostEqual(rows['5360'].debit, 2.2, places=2)
        self.assertAlmostEqual(rows['4000'].credit, 400.0, places=2)   # revenue stays gross
        total_debit = sum(e.debit or 0 for e in GeneralLedgerEntry.query.all())
        total_credit = sum(e.credit or 0 for e in GeneralLedgerEntry.query.all())
        self.assertAlmostEqual(total_debit, total_credit, places=2)    # books still balance

    def test_deduction_rejected_on_cash_or_above_amount_or_negative(self):
        for pay in ({'payment_method': 'CASH', 'amount': 400, 'fee': 5},
                    {'payment_method': 'BANK', 'amount': 400, 'fee': 401},
                    {'payment_method': 'MPESA', 'amount': 400, 'fee': -1}):
            r = self.checkout([pay])
            self.assertEqual(r.status_code, 400, pay)
        self.assertEqual(PaymentSplit.query.count(), 0)
        self.assertEqual(LocationStock.query.filter_by(ingredient_id=self.item.id).one().quantity_kg, 100.0)

    def test_payment_without_fee_still_works(self):
        r = self.checkout([{'payment_method': 'BANK', 'amount': 400}])
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(PaymentSplit.query.one().channel_fee, 0.0)
        self.assertIsNone(GeneralLedgerEntry.query.filter_by(account_code='5360').first())

    # ---- item disable: hidden from Stock and POS only -----------------------------
    def test_disable_is_admin_only(self):
        self.assertEqual(self.client_for(self.cashier_id).post(
            f'/api/po/inventory/{self.item.id}/active', json={'active': False}).status_code, 403)
        self.assertEqual(self.client_for(self.admin_id).post(
            f'/api/po/inventory/{self.item.id}/active', json={'active': 'no'}).status_code, 400)

    def test_disabled_item_hidden_from_stock_pos_and_price_list_only(self):
        admin = self.client_for(self.admin_id)
        self.assertEqual(admin.post(f'/api/po/inventory/{self.item.id}/active', json={'active': False}).status_code, 200)
        cashier = self.client_for(self.cashier_id)
        q = '?location_id=%d' % self.branch.id
        self.assertNotIn(self.item.id, [r['id'] for r in cashier.get('/api/products/search' + q).json])
        self.assertNotIn(self.item.id, [r['id'] for r in cashier.get('/api/stock' + q).json])
        self.assertEqual(self.checkout([{'payment_method': 'CASH', 'amount': 400}]).status_code, 400)
        self.assertEqual(LocationStock.query.filter_by(ingredient_id=self.item.id).one().quantity_kg, 100.0)
        # still visible to purchasing / Item Master and to formulation
        inv = {r['id']: r for r in admin.get('/api/po/inventory' + q).json}
        self.assertFalse(inv[self.item.id]['is_active'])
        self.assertIn(self.item.id, [r['id'] for r in admin.get('/api/inventory' + q).json])
        # re-enable restores selling
        admin.post(f'/api/po/inventory/{self.item.id}/active', json={'active': True})
        self.assertEqual(self.checkout([{'payment_method': 'CASH', 'amount': 400}]).status_code, 200)

    def test_disabled_raw_ingredient_still_usable_in_formulas_and_solver(self):
        admin = self.client_for(self.admin_id)
        admin.post(f'/api/po/inventory/{self.maize.id}/active', json={'active': False})
        lines = [{'ingredient_id': self.maize.id, 'pct': 60}, {'ingredient_id': self.soy.id, 'pct': 40}]
        self.assertEqual(admin.post('/api/factory/formulas', json={'name': 'Mix', 'lines': lines}).status_code, 200)


    # ---- GL statements -----------------------------------------------------------
    def test_statements_tie_out_after_a_sale_with_fee(self):
        self.assertEqual(self.checkout([{'payment_method': 'MPESA', 'amount': 400, 'fee': 2.2}]).status_code, 200)
        admin = self.client_for(self.admin_id)
        tb = admin.get('/api/reports/trial-balance').json
        self.assertTrue(tb['balanced'], tb)
        pl = admin.get('/api/reports/profit-loss').json
        self.assertAlmostEqual(pl['net_sales'], 400.0, places=2)
        self.assertAlmostEqual(pl['cogs'], 300.0, places=2)           # 10 kg x 30
        self.assertAlmostEqual(pl['gross_profit'], 100.0, places=2)
        self.assertAlmostEqual(pl['net_profit'], 97.8, places=2)       # less 2.20 channel fee
        self.assertEqual([e['code'] for e in pl['expenses']], ['5360'])
        cf = admin.get('/api/reports/cash-flow').json
        self.assertAlmostEqual(cf['net_change'], 397.8, places=2)
        self.assertAlmostEqual(cf['closing_cash'], 397.8, places=2)
        self.assertEqual(cf['groups'][0]['lines'][0]['amount'], 397.8)

    def test_profit_matches_cash_walk_profit_line(self):
        self.checkout([{'payment_method': 'CASH', 'amount': 400}])
        admin = self.client_for(self.admin_id)
        pl = admin.get('/api/reports/profit-loss').json
        db.session.add(CashWalkOpening(as_of_date=date.fromisoformat(pl['start']), cash=0, inventory=0,
                                       debtors=0, creditors=0, equipment=0, created_by='test'))
        db.session.commit()
        cw = admin.get('/api/reports/cash-walk?start=%s&end=%s' % (pl['start'], pl['end'])).json
        self.assertAlmostEqual(cw['lines']['profit'], pl['net_profit'], places=2)

    def test_trial_balance_flags_one_sided_postings(self):
        from services.ledger_service import post_gl_entry
        post_gl_entry('X1', '1000', 50.0, 0.0, 'TEST', 1)
        db.session.commit()
        tb = self.client_for(self.admin_id).get('/api/reports/trial-balance').json
        self.assertFalse(tb['balanced'])
        self.assertAlmostEqual(tb['difference'], 50.0, places=2)

    def test_statements_are_admin_accountant_only(self):
        for path in ('trial-balance', 'profit-loss', 'cash-flow'):
            self.assertEqual(self.client_for(self.cashier_id).get('/api/reports/' + path).status_code, 403)


    # ---- least-cost formulation engine --------------------------------------------
    def _raw(self, name, cost, cp, me, fiber, stock_kg=1000.0, max_incl=100.0):
        item = FeedIngredient(name=name, category='Raw - Test', cost_per_kg=cost, crude_protein_pct=cp,
                              metabolizable_energy_mcal=me, crude_fiber_pct=fiber, max_inclusion_pct=max_incl)
        db.session.add(item)
        db.session.flush()
        db.session.add(LocationStock(location_id=self.branch.id, ingredient_id=item.id,
                                     quantity_kg=stock_kg, unit_cost_per_kg=cost))
        db.session.commit()
        return item

    def _solve(self, **kw):
        from services.formulator_service import solve_feed_formulation
        ids = kw.pop('ids')
        return solve_feed_formulation('Test Stage', 1000.0, ids, self.branch.id, kw.pop('cp', 16.0),
                                      kw.pop('me', 2.6), **kw)

    def test_fibre_cap_forces_a_costlier_but_compliant_recipe(self):
        a = self._raw('Maize', 30, 8, 3.3, 2.5)
        b = self._raw('Soya', 60, 44, 2.9, 6)
        c = self._raw('Bran', 15, 15, 1.9, 11)
        ids = [a.id, b.id, c.id]
        free = self._solve(ids=ids)
        capped = self._solve(ids=ids, target_fiber_max_pct=4.0)
        self.assertEqual(free['status'], 'optimal')
        self.assertEqual(capped['status'], 'optimal')
        self.assertLessEqual(capped['achieved']['crude_fiber_pct'], 4.005)
        self.assertGreaterEqual(capped['achieved']['crude_protein_pct'], 15.99)
        self.assertGreaterEqual(capped['achieved']['metabolizable_energy_mcal'], 2.599)
        self.assertGreaterEqual(capped['cost_per_kg'], free['cost_per_kg'] - 1e-6)
        self.assertGreater(free['achieved']['crude_fiber_pct'], 4.0)   # the cap actually bit

    def test_max_inclusion_limit_is_respected(self):
        a = self._raw('Maize', 30, 8, 3.3, 2.5)
        b = self._raw('Soya', 60, 44, 2.9, 6)
        c = self._raw('Cheap filler', 5, 15, 2.8, 3, max_incl=20.0)
        out = self._solve(ids=[a.id, b.id, c.id])
        row = [r for r in out['recipe'] if r['ingredient_name'] == 'Cheap filler'][0]
        self.assertLessEqual(row['fraction'], 20.0 + 0.01)

    def test_limit_to_stock_caps_use_and_flags_shortfall_otherwise(self):
        a = self._raw('Maize', 30, 8, 3.3, 2.5)
        b = self._raw('Soya', 60, 44, 2.9, 6)
        scarce = self._raw('Scarce cheap', 5, 20, 3.0, 2, stock_kg=100.0)
        ids = [a.id, b.id, scarce.id]
        free = self._solve(ids=ids)
        self.assertTrue(any('Not enough stock' in w for w in free['warnings']))
        limited = self._solve(ids=ids, limit_to_stock=True)
        used = [r for r in limited['recipe'] if r['ingredient_name'] == 'Scarce cheap'][0]
        self.assertLessEqual(used['kg_required'], 100.01)

    def test_infeasible_explains_which_target_is_out_of_reach(self):
        a = self._raw('Maize', 30, 8, 3.3, 2.5)
        b = self._raw('Soya', 60, 44, 2.9, 6)
        out = self._solve(ids=[a.id, b.id], cp=60.0)
        self.assertEqual(out['status'], 'infeasible')
        self.assertIn('Best possible protein is 44.00%', out['message'])
        self.assertEqual(out['diagnosis']['best_crude_protein_pct'], 44.0)

    def test_missing_nutrient_data_and_cost_are_called_out(self):
        a = self._raw('Maize', 30, 8, 3.3, 2.5)
        b = self._raw('Mystery', 20, 0, 0, 0)
        c = self._raw('Free stuff', 0, 10, 2.0, 1)
        out = self._solve(ids=[a.id, b.id, c.id], cp=7.0, me=3.0)
        text = ' '.join(out['warnings'])
        self.assertIn('Mystery', text)
        self.assertIn('Free stuff', text)

    def test_nutrient_editor_validates_and_is_permissioned(self):
        a = self._raw('Maize', 30, 0, 0, 0)
        good = {'crude_protein_pct': 8.5, 'metabolizable_energy_mcal': 3.3, 'crude_fiber_pct': 2.5, 'max_inclusion_pct': 60}
        url = f'/api/po/inventory/{a.id}/nutrients'
        self.assertEqual(self.client_for(self.cashier_id).post(url, json=good).status_code, 403)
        admin = self.client_for(self.admin_id)
        self.assertEqual(admin.post(url, json=dict(good, crude_protein_pct=150)).status_code, 400)
        self.assertEqual(admin.post(url, json=dict(good, max_inclusion_pct=0)).status_code, 400)
        self.assertEqual(admin.post(url, json={'crude_protein_pct': 5}).status_code, 400)
        self.assertEqual(admin.post(url, json=good).status_code, 200)
        row = [r for r in admin.get('/api/po/inventory').json if r['id'] == a.id][0]
        self.assertEqual((row['crude_protein_pct'], row['crude_fiber_pct'], row['max_inclusion_pct']), (8.5, 2.5, 60))

    def test_stage_requirements_round_trip_and_drive_the_solver(self):
        admin = self.client_for(self.admin_id)
        self.assertFalse(admin.get('/api/formulate/requirements?species=Test%20Stage').json['saved'])
        self.assertEqual(admin.post('/api/formulate/requirements', json={
            'species': 'Test Stage', 'min_cp': 18, 'min_me': 2.7, 'max_fiber': 5}).status_code, 200)
        r = admin.get('/api/formulate/requirements?species=Test%20Stage').json
        self.assertEqual((r['min_cp'], r['min_me'], r['max_fiber']), (18, 2.7, 5))
        a = self._raw('Maize', 30, 8, 3.3, 2.5)
        b = self._raw('Soya', 60, 44, 2.9, 6)
        res = admin.post('/api/formulate', json={'species': 'Test Stage', 'target_batch_kg': 1000,
                                                 'ingredient_ids': [a.id, b.id], 'location_id': self.branch.id})
        self.assertEqual(res.status_code, 200, res.json)
        self.assertEqual(res.json['target_crude_protein_pct'], 18)
        self.assertEqual(res.json['target_max_crude_fiber_pct'], 5)
        self.assertEqual(self.client_for(self.cashier_id).post('/api/formulate/requirements', json={
            'species': 'Test Stage', 'min_cp': 1, 'min_me': 1}).status_code, 403)


    # ---- every role can put a sale on customer credit --------------------------------
    def test_every_selling_role_can_sell_on_credit_within_the_limit(self):
        from services.models import Customer
        for role in ('sales', 'warehouse', 'factory'):
            user = User(username=f'u_{role}', role=role, location='RET-01', location_id=self.branch.id)
            user.set_password('4321')
            db.session.add(user)
            cust = Customer(name=f'Cust {role}', location_id=self.branch.id, credit_limit=1000.0)
            db.session.add(cust)
            db.session.add(TillSession(location_id=self.branch.id, cashier_name=f'u_{role}', opening_cash=0.0,
                                       expected_cash=0.0, status='OPEN', open_key=f'{self.branch.id}:u_{role}'))
            db.session.commit()
            c = self.client_for(user.id)
            self.assertEqual(c.get('/api/customers?location_id=%d' % self.branch.id).status_code, 200, role)
            body = {'location_id': self.branch.id, 'customer_id': cust.id,
                    'cart': [{'ingredient_id': self.item.id, 'unit_type': 'KG', 'qty': 5, 'bag_size_kg': 1}],
                    'payments': [{'payment_method': 'CREDIT', 'amount': 200}]}
            r = c.post('/api/pos/checkout', headers={'Idempotency-Key': str(uuid.uuid4())}, json=body)
            self.assertEqual(r.status_code, 200, (role, r.json))
            self.assertAlmostEqual(db.session.get(Customer, cust.id).current_balance, 200.0, places=2)
            body['cart'][0]['qty'] = 50
            body['payments'][0]['amount'] = 2000   # above the 1,000 limit
            r = c.post('/api/pos/checkout', headers={'Idempotency-Key': str(uuid.uuid4())}, json=body)
            self.assertEqual(r.status_code, 400, role)


    # ---- staff credit limits and old-debt entries --------------------------------------
    def _post(self, client, url, body, key=None):
        return client.post(url, json=body, headers={'Idempotency-Key': key or str(uuid.uuid4())})

    def _customer(self, limit=0.0, outlet=None):
        from services.models import Customer
        cust = Customer(name='Debtor', location_id=outlet or self.branch.id, credit_limit=limit)
        db.session.add(cust)
        db.session.commit()
        return cust.id

    def test_staff_can_set_a_credit_limit_up_to_the_cap_only(self):
        from services.models import Customer
        cashier = self.client_for(self.cashier_id)
        url = '/api/customers'
        ok = self._post(cashier, url, {'name': 'New A', 'credit_limit': 5000, 'location_id': self.branch.id})
        self.assertEqual(ok.status_code, 200, ok.json)
        self.assertEqual(db.session.get(Customer, ok.json['customer_id']).credit_limit, 5000.0)
        over = self._post(cashier, url, {'name': 'New B', 'credit_limit': 10001, 'location_id': self.branch.id})
        self.assertEqual(over.status_code, 400)
        self.assertIn('10,000', over.json['message'])
        self.assertEqual(self._post(cashier, url, {'name': 'New C', 'credit_limit': -5, 'location_id': self.branch.id}).status_code, 400)
        self.assertEqual(Customer.query.filter(Customer.name.in_(['New B', 'New C'])).count(), 0)
        admin = self.client_for(self.admin_id)   # owners are not capped
        big = self._post(admin, url, {'name': 'New D', 'credit_limit': 50000, 'location_id': self.branch.id})
        self.assertEqual(big.status_code, 200)

    def test_cashier_created_customer_can_actually_buy_on_credit(self):
        from services.models import Customer
        cashier = self.client_for(self.cashier_id)
        cid = self._post(cashier, '/api/customers', {'name': 'Walk', 'credit_limit': 1000,
                                                     'location_id': self.branch.id}).json['customer_id']
        r = self._post(cashier, '/api/pos/checkout', {
            'location_id': self.branch.id, 'customer_id': cid,
            'cart': [{'ingredient_id': self.item.id, 'unit_type': 'KG', 'qty': 10, 'bag_size_kg': 1}],
            'payments': [{'payment_method': 'CREDIT', 'amount': 400}]})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertAlmostEqual(db.session.get(Customer, cid).current_balance, 400.0, places=2)

    def test_every_role_can_record_old_debt_and_books_stay_balanced(self):
        from services.models import Customer
        for role in ('sales', 'warehouse', 'factory'):
            user = User(username=f'd_{role}', role=role, location='RET-01', location_id=self.branch.id)
            user.set_password('4321')
            db.session.add(user)
            db.session.commit()
            cid = self._customer()
            r = self._post(self.client_for(user.id), f'/api/customers/{cid}/add-debt',
                           {'amount': 2500, 'note': 'Feed taken in 2025', 'location_id': self.branch.id})
            self.assertEqual(r.status_code, 200, (role, r.json))
            self.assertAlmostEqual(db.session.get(Customer, cid).current_balance, 2500.0, places=2)
        admin = self.client_for(self.admin_id)
        tb = admin.get('/api/reports/trial-balance').json
        self.assertTrue(tb['balanced'], tb)
        by = {r['code']: r for r in tb['rows']}
        self.assertAlmostEqual(by['1300']['debit'], 7500.0, places=2)
        self.assertAlmostEqual(by['3000']['credit'], 7500.0, places=2)
        pl = admin.get('/api/reports/profit-loss').json
        self.assertEqual(pl['net_profit'], 0.0)                      # old debt is not income

    def test_old_debt_guards_cap_note_outlet_and_replay(self):
        from services.models import Customer, CustomerDebtEntry
        cashier = self.client_for(self.cashier_id)
        cid = self._customer()
        url = f'/api/customers/{cid}/add-debt'
        base = {'amount': 1000, 'note': 'Old balance', 'location_id': self.branch.id}
        self.assertEqual(self._post(cashier, url, dict(base, amount=10001)).status_code, 400)
        self.assertEqual(self._post(cashier, url, dict(base, note=' ')).status_code, 400)
        self.assertEqual(self._post(cashier, url, dict(base, amount=0)).status_code, 400)
        other = Location(name='Other', code='OTH', location_type='BRANCH_STORE')
        db.session.add(other)
        db.session.commit()
        foreign = self._customer(outlet=other.id)
        self.assertEqual(self._post(cashier, f'/api/customers/{foreign}/add-debt', base).status_code, 400)
        key = str(uuid.uuid4())
        self.assertEqual(self._post(cashier, url, base, key).status_code, 200)
        self.assertEqual(self._post(cashier, url, base, key).status_code, 200)   # retry, same key
        self.assertEqual(db.session.get(Customer, cid).current_balance, 1000.0)  # counted once
        self.assertEqual(CustomerDebtEntry.query.filter_by(customer_id=cid).count(), 1)
        admin = self.client_for(self.admin_id)                                    # owners are not capped
        self.assertEqual(self._post(admin, url, dict(base, amount=50000)).status_code, 200)

    def test_old_debt_does_not_break_the_cash_walk_reconciliation(self):
        cid = self._customer()
        self.assertEqual(self._post(self.client_for(self.cashier_id), f'/api/customers/{cid}/add-debt',
                                    {'amount': 4000, 'note': 'Old balance', 'location_id': self.branch.id}).status_code, 200)
        admin = self.client_for(self.admin_id)
        pl = admin.get('/api/reports/profit-loss').json
        db.session.add(CashWalkOpening(as_of_date=date.fromisoformat(pl['start']), cash=0, inventory=0,
                                       debtors=0, creditors=0, equipment=0, created_by='test'))
        db.session.commit()
        cw = admin.get('/api/reports/cash-walk?start=%s&end=%s' % (pl['start'], pl['end'])).json
        self.assertEqual(cw['reconciliation_difference'], 0.0)
        self.assertEqual(cw['lines']['old_debts_recorded'], 4000.0)

    def test_debt_audit_list_is_owner_only_and_me_exposes_the_cap(self):
        cid = self._customer()
        cashier = self.client_for(self.cashier_id)
        self._post(cashier, f'/api/customers/{cid}/add-debt', {'amount': 700, 'note': 'Old balance', 'location_id': self.branch.id})
        self.assertEqual(cashier.get('/api/customers/debt-entries').status_code, 403)
        rows = self.client_for(self.admin_id).get('/api/customers/debt-entries').json
        self.assertEqual((rows[0]['amount'], rows[0]['created_by'], rows[0]['note']), (700.0, 'cashier', 'Old balance'))
        self.assertEqual(cashier.get('/api/me').json['staff_credit_limit_max'], 10000.0)


    # ---- ledger rounding ------------------------------------------------------------------
    def test_fractional_cent_bill_posts_a_perfectly_balanced_entry(self):
        from sqlalchemy import func
        self.item.retail_price_per_kg = 41.35           # 7.5 kg -> 310.125, a half cent
        db.session.commit()
        r = self.client_for(self.cashier_id).post(
            '/api/pos/checkout', headers={'Idempotency-Key': str(uuid.uuid4())},
            json={'location_id': self.branch.id,
                  'cart': [{'ingredient_id': self.item.id, 'unit_type': 'KG', 'qty': 7.5, 'bag_size_kg': 1}],
                  'payments': [{'payment_method': 'CASH', 'amount': 310.13}]})
        self.assertEqual(r.status_code, 200, r.json)
        d, c = db.session.query(func.sum(GeneralLedgerEntry.debit), func.sum(GeneralLedgerEntry.credit)).one()
        self.assertAlmostEqual(d, c, places=9)

    def test_trial_balance_ignores_sub_cent_residue_but_catches_real_one_sided_entries(self):
        from services.ledger_service import post_gl_entry
        post_gl_entry('OLD1', '1000', 10.004, 0.0, 'TEST', 1)       # legacy half-cent noise
        post_gl_entry('OLD1', '4000', 0.0, 10.0, 'TEST', 1)
        db.session.commit()
        admin = self.client_for(self.admin_id)
        tb = admin.get('/api/reports/trial-balance').json
        self.assertTrue(tb['balanced'], tb)
        post_gl_entry('BAD1', '1000', 5.0, 0.0, 'TEST', 2)           # a real one-sided leg
        db.session.commit()
        tb = admin.get('/api/reports/trial-balance').json
        self.assertFalse(tb['balanced'])
        self.assertEqual([e['reference'] for e in tb['unbalanced_entries']], ['BAD1'])
    # ---- price list PDF ----------------------------------------------------------
    def test_price_list_pdf_has_prices_but_no_costs(self):
        db.session.add(ItemPrice(ingredient_id=self.item.id, pack_kg=70, price=2800))
        db.session.commit()
        r = self.client_for(self.cashier_id).get('/api/price-list.pdf')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, 'application/pdf')
        self.assertTrue(r.data.startswith(b'%PDF'))
        self.assertIn('attachment', r.headers['Content-Disposition'])

    def test_price_list_pdf_404_when_nothing_priced(self):
        FeedIngredient.query.update({'retail_price_per_kg': 0})
        db.session.commit()
        self.assertEqual(self.client_for(self.cashier_id).get('/api/price-list.pdf').status_code, 404)


if __name__ == '__main__':
    unittest.main()
