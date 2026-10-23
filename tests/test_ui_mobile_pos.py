import os, re, tempfile, unittest
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-deployment')
from app import create_app
from services.db import db
from services.models import User

# Ids the existing POS JavaScript depends on. The UI patch must never remove or rename them.
REQUIRED_IDS = ['pos-location', 'till-state', 'till-opening-cash', 'till-open-btn', 'till-close-btn',
                'till-movement-type', 'till-movement-amount', 'till-movement-reason', 'till-movement-message',
                'till-movement-history', 'search-input', 'category-filter', 'search-results', 'customer-select',
                'cart-body', 'cart-total', 'change-due', 'discount-val', 'discount-type', 'payment-lines',
                'btn-hold', 'btn-restore', 'btn-print', 'btn-wa', 'printable-receipt', 'r-total']


class MobilePosUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'k' * 20,
                               'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + os.path.join(self.tmp.name, 't.db')})
        self.ctx = self.app.app_context(); self.ctx.push()
        admin = User(username='owner', role='admin', location='admin'); admin.set_password('1234')
        db.session.add(admin); db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess: sess['user_id'] = admin.id
        self.html = self.client.get('/').get_data(as_text=True)

    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.ctx.pop(); self.tmp.cleanup()

    def test_existing_pos_ids_survive(self):
        for i in REQUIRED_IDS:
            self.assertEqual(self.html.count('id="%s"' % i), 1, i)

    def test_new_ui_hooks_exist_once(self):
        for i in ('till-panel', 'till-state-mirror', 'sticky-total', 'sticky-go', 'checkout-card'):
            self.assertEqual(self.html.count('id="%s"' % i), 1, i)

    def test_assets_linked_and_served(self):
        c = self.client
        for path in ('/static/css/ui-mobile.css', '/static/js/ui-mobile.js'):
            self.assertIn(path, self.html)
            self.assertEqual(c.get(path).status_code, 200, path)

    def test_ui_files_are_versioned_by_modified_time_so_phones_never_keep_stale_copies(self):
        import re
        for rel, name in (('css/ui-mobile.css', 'ui-mobile.css'), ('js/ui-mobile.js', 'ui-mobile.js')):
            m = re.search(re.escape(name) + r'\?v=(\d+)', self.html)
            self.assertIsNotNone(m, name)
            path = os.path.join(os.path.dirname(__file__), '..', 'static', rel)
            self.assertEqual(int(m.group(1)), int(os.path.getmtime(path)))

    def test_css_braces_balance_and_mobile_fixes_are_present(self):
        css = open(os.path.join(os.path.dirname(__file__), '..', 'static', 'css', 'ui-mobile.css'), encoding='utf-8').read()
        self.assertEqual(css.count('{'), css.count('}'))
        for needle in ('.status-badge{white-space:nowrap}', 'clamp(1.1rem', '.till-panel[open] #till-state-mirror'):
            self.assertIn(needle, css)

    def test_online_only_policy_text_is_kept(self):
        self.assertIn('record the sale on paper', self.html)

    def test_sticky_bar_does_not_call_checkout(self):
        js = open(os.path.join(os.path.dirname(__file__), '..', 'static', 'js', 'ui-mobile.js'), encoding='utf-8').read()
        for forbidden in ('completeCheckout', 'fetch(', 'XMLHttpRequest', 'indexedDB', 'localStorage'):
            self.assertNotIn(forbidden, js)


if __name__ == '__main__':
    unittest.main()
