import os, tempfile, unittest
from datetime import datetime, timedelta
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-deployment')
from app import create_app
from services.db import db
from services.models import Location, OrderHeader, User


class DashboardTrendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'k' * 20,
                               'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + os.path.join(self.tmp.name, 't.db')})
        self.ctx = self.app.app_context(); self.ctx.push()
        self.a = Location(name='A', code='A1', location_type='BRANCH_STORE')
        self.b = Location(name='B', code='B1', location_type='BRANCH_STORE')
        db.session.add_all([self.a, self.b]); db.session.flush()
        self.users = {}
        for role, pw, loc in (('admin', '1234', self.a), ('sales', '4321', self.a), ('warehouse', '2468', self.a), ('factory', '1357', self.a)):
            user = User(username='u_' + role, role=role, location=loc.code, location_id=loc.id)
            user.set_password(pw)
            db.session.add(user); db.session.flush()
            self.users[role] = user.id
        now = datetime.utcnow()
        def order(loc, total, credit, ago, status='COMPLETED'):
            db.session.add(OrderHeader(location_id=loc.id, total_amount=total, credit_amount=credit,
                                       status=status, created_at=now - ago))
        order(self.a, 1000, 400, timedelta(minutes=1))            # today
        order(self.a, 500, 0, timedelta(days=1))                  # yesterday
        order(self.a, 700, 100, timedelta(days=6))                # edge of the window
        order(self.a, 9999, 0, timedelta(days=9))                 # outside the window
        order(self.a, 888, 0, timedelta(minutes=2), 'VOIDED')     # not completed
        order(self.b, 5000, 0, timedelta(minutes=1))              # another outlet
        db.session.commit()

    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.ctx.pop(); self.tmp.cleanup()

    def client(self, role):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s['user_id'] = self.users[role]
        return c

    def test_returns_seven_days_for_the_users_outlet_only(self):
        r = self.client('sales').get('/api/dashboard/trend')
        self.assertEqual(r.status_code, 200)
        days = r.get_json()['days']
        self.assertEqual(len(days), 7)
        self.assertEqual([d['date'] for d in days], sorted(d['date'] for d in days))
        self.assertEqual(days[-1]['sales'], 1000.0)      # today is last; other outlet, void and old orders excluded
        self.assertEqual(days[-1]['credit'], 400.0)
        self.assertEqual(days[-2]['sales'], 500.0)
        self.assertEqual(sum(d['sales'] for d in days), 2200.0)

    def test_owner_can_pick_an_outlet(self):
        r = self.client('admin').get('/api/dashboard/trend?location_id=%d' % self.b.id)
        self.assertEqual(r.get_json()['days'][-1]['sales'], 5000.0)

    def test_roles_match_the_daily_report(self):
        for role, expected in (('admin', 200), ('sales', 200), ('factory', 200), ('warehouse', 403)):
            self.assertEqual(self.client(role).get('/api/dashboard/trend').status_code, expected, role)
        self.assertIn(self.app.test_client().get('/api/dashboard/trend').status_code, (401, 302))


if __name__ == '__main__':
    unittest.main()
