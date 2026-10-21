"""The app is online-only by decision: no half-built offline path may sneak back in."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class OfflinePolicyTests(unittest.TestCase):
    def test_dead_offline_code_is_gone(self):
        for rel in ('static/app.js', 'static/sw.js', 'static/service-worker.js',
                    'static/js/offline-db.js', 'static/js/outbox.js', 'static/index.html'):
            self.assertFalse((ROOT / rel).exists(), f'{rel} was removed: it called sync routes that do not exist')

    def test_no_code_registers_a_service_worker_or_calls_the_missing_sync_routes(self):
        for path in list(ROOT.glob('templates/**/*.html')) + list(ROOT.glob('static/**/*.js')):
            text = path.read_text(encoding='utf-8', errors='ignore')
            self.assertNotRegex(text, r'serviceWorker\s*\.\s*register', path.name)
            self.assertNotRegex(text, r'/api/(v1/)?sync', path.name)

    def test_old_service_workers_in_browsers_are_still_cleaned_up_and_status_is_honest(self):
        base = (ROOT / 'templates/base.html').read_text(encoding='utf-8')
        self.assertIn('getRegistrations', base)
        self.assertIn('unregister', base)
        self.assertIn('OFFLINE · RECORD ON PAPER', base)

    def test_every_script_a_template_loads_exists(self):
        for template in ROOT.glob('templates/**/*.html'):
            for src in re.findall(r'<script[^>]+src="(/static/[^"?]+)', template.read_text(encoding='utf-8')):
                self.assertTrue((ROOT / src.lstrip('/')).exists(), f'{template.name} loads missing {src}')


if __name__ == '__main__':
    unittest.main()
