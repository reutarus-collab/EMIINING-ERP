"""Offline POS must use a local durable outbox and never invent sync APIs."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class OfflinePolicyTests(unittest.TestCase):
    def test_offline_pos_shell_and_store_exist(self):
        self.assertTrue((ROOT / 'static/service-worker.js').exists())
        self.assertTrue((ROOT / 'static/js/offline-store.js').exists())

    def test_offline_support_does_not_call_missing_sync_routes(self):
        for path in list(ROOT.glob('templates/**/*.html')) + list(ROOT.glob('static/**/*.js')):
            text = path.read_text(encoding='utf-8', errors='ignore')
            self.assertNotRegex(text, r'/api/(v1/)?sync', path.name)

    def test_offline_shell_is_registered_and_status_explains_queued_sales(self):
        base = (ROOT / 'templates/base.html').read_text(encoding='utf-8')
        self.assertIn('serviceWorker.register', base)
        self.assertIn('OFFLINE · SALES SAVED ON THIS PHONE', base)

    def test_every_script_a_template_loads_exists(self):
        for template in ROOT.glob('templates/**/*.html'):
            for src in re.findall(r'<script[^>]+src="(/static/[^"?]+)', template.read_text(encoding='utf-8')):
                self.assertTrue((ROOT / src.lstrip('/')).exists(), f'{template.name} loads missing {src}')


if __name__ == '__main__':
    unittest.main()
