"""Authorization audit as code: who can reach what, outlet isolation, and the error boundary."""
import re
import unittest
import uuid
from unittest import mock

import test_pos as _tp
from sqlalchemy.exc import OperationalError
from services.db import db
from services.models import FeedIngredient, Location, LocationStock, PurchaseOrderHeader, Supplier, User

ROLES = ('admin', 'accountant', 'sales', 'warehouse', 'factory')
OPEN_RULES = {'/login', '/logout'}


class RBACMatrixTests(unittest.TestCase):
    setUp_base = _tp.POSCheckoutTests.setUp
    tearDown = _tp.POSCheckoutTests.tearDown
    make_client = _tp.POSCheckoutTests.make_client
    payload = _tp.POSCheckoutTests.payload

    def setUp(self):
        self.setUp_base()
        self.users = {'sales': self.cashier.id}
        for role in ('admin', 'accountant', 'warehouse', 'factory'):
            user = User(username=f'u_{role}', role=role, location='RET-01', location_id=self.branch.id)
            user.set_password('2468')
            db.session.add(user)
            db.session.flush()
            self.users[role] = user.id
        db.session.commit()

    def client_for(self, role):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = self.users[role]
        return client

    def rules(self):
        for rule in self.app.url_map.iter_rules():
            if rule.endpoint != 'static' and rule.rule not in OPEN_RULES:
                yield rule

    # ---- structure: nothing is open by accident -----------------------------------------------
    def test_anonymous_users_are_turned_away_from_every_route(self):
        anon = self.app.test_client()
        for rule in self.rules():
            path = re.sub(r'<[^>]+>', '1', rule.rule)
            for method in rule.methods - {'HEAD', 'OPTIONS'}:
                response = anon.open(path, method=method, json={} if method != 'GET' else None)
                self.assertIn(response.status_code, (401, 302), f'{method} {rule.rule} -> {response.status_code}')

    def test_every_route_declares_the_roles_it_serves(self):
        undeclared = [r.rule for r in self.rules()
                      if getattr(self.app.view_functions[r.endpoint], 'allowed_roles', None) is None]
        self.assertEqual(undeclared, [], 'Add @roles_required(...) to these routes; the path-prefix gate is not enough.')

    def test_declared_roles_are_all_real_roles(self):
        for rule in self.rules():
            declared = getattr(self.app.view_functions[rule.endpoint], 'allowed_roles', frozenset())
            self.assertTrue(set(declared) <= set(ROLES), (rule.rule, declared))

    def test_money_reports_and_admin_routes_are_closed_to_floor_staff(self):
        floor = {'sales', 'warehouse', 'factory'}
        allowed_to_floor = {'/api/reports/daily'}   # the per-outlet daily sheet is meant for the till
        for rule in self.rules():
            if rule.rule.startswith(('/api/admin', '/api/reports')) and rule.rule not in allowed_to_floor:
                declared = set(self.app.view_functions[rule.endpoint].allowed_roles)
                self.assertFalse(declared & floor, f'{rule.rule} is open to {declared & floor}')

    # ---- behaviour: real requests, role by role -------------------------------------------------
    MATRIX = [
        ('GET',    '/api/admin/users',                      {'admin'}),
        ('POST',   '/api/admin/locations',                  {'admin'}),
        ('POST',   '/api/admin/prices',                     {'admin'}),
        ('GET',    '/api/admin/payables',                   {'admin', 'accountant'}),
        ('GET',    '/api/reports/trial-balance',            {'admin', 'accountant'}),
        ('GET',    '/api/reports/profit-loss',              {'admin', 'accountant'}),
        ('POST',   '/api/reports/owner-withdrawals',        {'admin'}),
        ('POST',   '/api/reports/opening-balances',         {'admin', 'accountant'}),
        ('GET',    '/api/customers/debt-entries',           {'admin', 'accountant'}),
        ('POST',   '/api/inventory/adjust',                 {'admin', 'warehouse'}),
        ('POST',   '/api/inventory/transfers',              {'admin', 'warehouse'}),
        ('POST',   '/api/pos/orders/1/refund',              {'admin', 'accountant', 'sales'}),
        ('DELETE', '/api/po/inventory/1',                   {'admin'}),
        ('POST',   '/api/po/inventory/1/active',            {'admin'}),
        ('POST',   '/api/po',                               {'admin', 'accountant', 'warehouse', 'factory'}),
        ('POST',   '/api/po/1/grpo',                        {'admin', 'accountant', 'warehouse', 'factory'}),
        ('POST',   '/api/suppliers/add',                    {'admin', 'accountant', 'warehouse', 'factory'}),
        ('GET',    '/api/sales-history',                    {'admin', 'accountant', 'sales', 'factory'}),
        ('POST',   '/api/pos/checkout',                     set(ROLES)),
        ('GET',    '/api/stock',                            set(ROLES)),
        ('GET',    '/api/me',                               set(ROLES)),
    ]

    def test_role_matrix(self):
        for method, path, allowed in self.MATRIX:
            for role in ROLES:
                response = self.client_for(role).open(
                    path, method=method, json={}, headers={'Idempotency-Key': str(uuid.uuid4())})
                if role in allowed:
                    self.assertNotIn(response.status_code, (401, 403), f'{role} should reach {method} {path}')
                else:
                    self.assertEqual(response.status_code, 403, f'{role} must NOT reach {method} {path}')

    # ---- outlet isolation ----------------------------------------------------------------------
    def test_floor_staff_cannot_read_another_outlets_stock_or_purchase_orders(self):
        other = Location(name='Other Outlet', code='RET-02', location_type='BRANCH_STORE')
        db.session.add(other)
        db.session.flush()
        db.session.add(LocationStock(location_id=other.id, ingredient_id=self.item.id, quantity_kg=777.0,
                                     unit_cost_per_kg=30.0))
        supplier = Supplier(name='Sup')
        db.session.add(supplier)
        db.session.flush()
        po = PurchaseOrderHeader(po_number='PO-OTHER', supplier_id=supplier.id, location_id=str(other.id), status='APPROVED')
        db.session.add(po)
        db.session.commit()
        for role in ('sales', 'warehouse'):
            client = self.client_for(role)
            stock = client.get(f'/api/stock?location_id={other.id}')
            self.assertEqual(stock.status_code, 200)
            self.assertTrue(all(row['location_id'] == self.branch.id for row in stock.json))
            self.assertNotIn(777.0, [row['stock_kg'] for row in stock.json])
        pos = self.client_for('warehouse').get(f'/api/po?location_id={other.id}')
        self.assertNotIn('PO-OTHER', str(pos.json))
        lines = self.client_for('warehouse').get(f'/api/po/{po.id}/lines?location_id={other.id}')
        self.assertGreaterEqual(lines.status_code, 400)

    # ---- error boundary ---------------------------------------------------------------------------
    def test_database_errors_are_logged_not_shown_to_the_browser(self):
        leak = OperationalError('SELECT password_hash FROM app_users WHERE id=?', {'id': 1}, Exception('no such table'))
        with mock.patch('routes.pos.process_full_pos_checkout', side_effect=leak):
            response = self.client_for('sales').post('/api/pos/checkout', json=self.payload(1, 40),
                                                     headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 500)
        text = response.get_data(as_text=True)
        self.assertNotIn('password_hash', text)
        self.assertNotIn('SELECT', text)
        self.assertRegex(response.json['reference'], r'^ERR-[0-9A-F]{8}$')

    def test_validation_errors_stay_readable_and_are_400(self):
        response = self.client_for('sales').post('/api/pos/checkout', json=self.payload(1000, 40000),
                                                 headers={'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Insufficient stock', response.json['message'])


if __name__ == '__main__':
    unittest.main()
