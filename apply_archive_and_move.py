#!/usr/bin/env python3
"""
Emining ERP patch (anchor-based, so it works on your live files even when they differ from GitHub):
  1. RDP/RUP warning no longer fires for ingredients with no protein (lime, salt, DCP, premix).
  2. Archive / Restore for saved formulas (hidden from the production + Formulation Lab lists, blocked in production).
  3. Formula Library card moves from the Factory tab to the Formulation Lab tab.

Run from the repo folder:   python3 apply_archive_and_move.py            (applies)
                            python3 apply_archive_and_move.py --check    (shows what it would do, changes nothing)
Every file it touches is first copied to  _backup_before_archive/  . Re-running is safe (already-applied steps are skipped).
If a step says ANCHOR NOT FOUND, nothing in that file is changed: send me that file.
"""
import os, re, shutil, subprocess, sys

CHECK = '--check' in sys.argv
BACKUP = '_backup_before_archive'
results = []


class Missing(Exception):
    pass


def read(p):
    with open(p, encoding='utf-8', newline='') as f:
        return f.read()


def write(p, s):
    with open(p, 'w', encoding='utf-8', newline='') as f:
        f.write(s)


def need(cond, what):
    if not cond:
        raise Missing(what)


def func_span(src, name, lang):
    if lang == 'py':
        m = re.search(r'^def\s+' + name + r'\s*\(', src, re.M)
        need(m, 'def ' + name)
        n = re.search(r'^(?:def |@|class )', src[m.end():], re.M)
        end = m.end() + n.start() if n else len(src)
        return m.start(), end
    m = re.search(r'(?:async\s+)?function\s+' + name + r'\s*\(', src)
    need(m, 'function ' + name)
    i = src.index('{', m.end())
    depth = 0
    for j in range(i, len(src)):
        if src[j] == '{':
            depth += 1
        elif src[j] == '}':
            depth -= 1
            if depth == 0:
                return m.start(), j + 1
    raise Missing('end of function ' + name)


STAGED = []     # (path, new_text); written only if EVERY step found its anchor


def patch_file(path, fn, label):
    """fn(src)->new_src, or None if already applied. Nothing is written until all steps succeed."""
    if not os.path.exists(path):
        results.append(('MISSING FILE', label, path)); return
    src = read(path)
    try:
        new = fn(src)
    except Missing as e:
        results.append(('ANCHOR NOT FOUND', label, '%s (looking for: %s)' % (path, e))); return
    if new is None:
        results.append(('already applied', label, path)); return
    STAGED.append((path, new))
    results.append(('will change', label, path))


# ------------------------------------------------------------------ 1. RDP/RUP warning
NEW_RDP = '''def rdp_rup_warnings(ids, nutrients, names):
    """RDP + RUP as % of CP should be close to 100 for an ingredient. Warn (never block) when they are not.
    An ingredient with no protein (CP stored as 0: lime, salt, DCP, premix) has nothing to split, so it is skipped."""
    if 'RDP' not in nutrients or 'RUP' not in nutrients:
        return []
    rows = IngredientNutrient.query.filter(IngredientNutrient.ingredient_id.in_(list(ids)),
                                           IngredientNutrient.nutrient_code.in_(['RDP', 'RUP', 'CP'])).all()
    by = {}
    for r in rows:
        by.setdefault(r.ingredient_id, {})[r.nutrient_code] = r.value
    out = []
    for iid, d in by.items():
        if 'RDP' not in d or 'RUP' not in d:
            continue
        if d.get('CP') == 0:
            continue
        total = d['RDP'] + d['RUP']
        if abs(total - 100.0) <= 2.0:
            continue
        if total == 0:
            out.append(f'{names.get(iid, iid)}: RDP and RUP are both stored as 0. If that is a placeholder, clear them '
                       f'(blank = unknown); otherwise enter the real split.')
        else:
            out.append(f'{names.get(iid, iid)}: RDP + RUP = {total:.2f}% of CP (expected about 100%). '
                       f'Check the degradability data, or an undigestible fraction is missing.')
    return out


'''


def p_rdp(src):
    if 'has nothing to split' in src:
        return None
    a, b = func_span(src, 'rdp_rup_warnings', 'py')
    return src[:a] + NEW_RDP + src[b:]


# ------------------------------------------------------------------ 2. archive: model, columns, routes
def p_models(src):
    a = src.find('class SavedFormula(')
    need(a >= 0, 'class SavedFormula')
    b = src.find('\nclass ', a + 10)
    block = src[a:b if b > 0 else len(src)]
    if 'archived = db.Column' in block:
        return None
    anchor = 'last_verified_at = db.Column(db.DateTime, nullable=True)'
    need(anchor in block, anchor)
    block = block.replace(anchor, anchor + "\n    archived = db.Column(db.Boolean, nullable=False, default=False, server_default='0')", 1)
    return src[:a] + block + (src[b:] if b > 0 else '')


def p_db(src):
    if "'archived': 'BOOLEAN NOT NULL DEFAULT 0'" in src:
        return None
    m = re.search(r"('saved_formulas':\s*\{[^}]*?)(\})", src)
    need(m, "'saved_formulas': {...} in services/db.py")
    return src[:m.start(2)] + ", 'archived': 'BOOLEAN NOT NULL DEFAULT 0'" + src[m.start(2):]


ARCHIVE_ROUTE = '''@factory_bp.route('/api/factory/formulas/<int:formula_id>/archive', methods=['POST'])
@roles_required('admin', 'factory')
def archive_formula(formula_id):
    """Archive hides a formula from the production and Formulation Lab lists and blocks it in production. Nothing is deleted."""
    from services.models import SavedFormula
    fm = db.session.get(SavedFormula, formula_id)
    if fm is None:
        return jsonify(status='error', message='That formula no longer exists.'), 404
    data = request.get_json(silent=True) or {}
    fm.archived = bool(data.get('archived', True))
    db.session.commit()
    return jsonify(status='success', id=fm.id, archived=fm.archived)


'''


def p_factory_py(src):
    if 'def archive_formula' in src:
        return None
    s = src
    a = "'unpriced': unpriced}"
    need(s.count(a) == 1, a)
    s = s.replace(a, "'unpriced': unpriced, 'archived': bool(getattr(fm, 'archived', False))}", 1)
    a = '    fms = SavedFormula.query.order_by(SavedFormula.name).all()'
    need(s.count(a) == 1, a)
    s = s.replace(a, "    q = SavedFormula.query\n"
                     "    if str(request.args.get('include_archived', '')).lower() not in ('1', 'true', 'yes'):\n"
                     "        q = q.filter(SavedFormula.archived.is_(False))\n"
                     "    fms = q.order_by(SavedFormula.name).all()", 1)
    a = '    saved = SavedFormula.query.filter(db.func.lower(SavedFormula.name) == formula_name.lower()).first()\n    if saved is not None:\n        gate = release_check(saved)'
    need(s.count(a) == 1, 'production-run formula lookup + release_check')
    s = s.replace(a, "    saved = SavedFormula.query.filter(db.func.lower(SavedFormula.name) == formula_name.lower()).first()\n"
                     "    if saved is not None and getattr(saved, 'archived', False):\n"
                     "        return jsonify(status='error', message='Formula \"%s\" is archived. Restore it in the Formulation tab (Formula Library) before using it in production.' % saved.name), 409\n"
                     "    if saved is not None:\n        gate = release_check(saved)", 1)
    m = re.search(r"^_ING_HEADERS = ", s, re.M)
    need(m, '_ING_HEADERS (insert point for the archive route)')
    return s[:m.start()] + ARCHIVE_ROUTE + s[m.start():]


def p_nutrition_py(src):
    a = "    fm.lines = [SavedFormulaLine(ingredient_id=i, pct=round(p, 4)) for i, p in recipe.items()]\n"
    if "fm.archived = False" in src:
        return None
    need(src.count(a) == 1, 'fm.lines = [...] in save_formula (routes/nutrition.py)')
    return src.replace(a, a + "    fm.archived = False          # saving from the Lab brings an archived formula back into use\n", 1)


# ------------------------------------------------------------------ JS
JS_LOAD = '''let showArchivedFormulas = false;

async function loadSavedFormulas() {
    const sel = document.getElementById('saved-formula-select');
    if (!sel) return;
    try {
        const res = await fetch('/api/factory/formulas?include_archived=1', { credentials: 'same-origin' });
        if (!res.ok) return;
        savedFormulas = await res.json();
    } catch (e) { return; }
    const active = savedFormulas.filter(fm => !fm.archived);
    sel.innerHTML = active.length ? '' : '<option value="">-- none saved --</option>';
    active.forEach(fm => {
        const o = document.createElement('option');
        o.value = fm.id;
        o.textContent = fm.name;
        sel.appendChild(o);
    });
    renderFormulaLibrary();
}'''

JS_RENDER = '''function renderFormulaLibrary() {
    const body = document.getElementById('formula-list');
    if (!body) return;
    const nArch = savedFormulas.filter(fm => fm.archived).length;
    const cnt = document.getElementById('formula-archived-count');
    if (cnt) cnt.textContent = nArch ? ' (' + nArch + ')' : '';
    const rows = savedFormulas.filter(fm => showArchivedFormulas || !fm.archived);
    if (!rows.length) {
        body.innerHTML = '<tr><td colspan="5">' + (savedFormulas.length
            ? 'No active formulas. Tick "Show archived" to see the archived ones.'
            : 'No saved formulas yet. Click + New formula.') + '</td></tr>';
        return;
    }
    body.innerHTML = '';
    rows.forEach(fm => {
        const tr = document.createElement('tr');
        const warn = fm.unpriced && fm.unpriced.length ? ' (no cost: ' + fm.unpriced.join(', ') + ')' : '';
        tr.innerHTML = '<td></td><td>' + fm.lines.length + '</td><td>' + Number(fm.total_pct).toFixed(3) +
            '</td><td>' + Number(fm.cost_per_kg).toFixed(2) + escHtml(warn) + '</td><td></td>';
        tr.children[0].textContent = fm.name + (fm.archived ? ' (archived)' : '');
        if (fm.archived) tr.style.opacity = '0.6';
        const mk = (cls, text, fn) => {
            const b = document.createElement('button');
            b.type = 'button'; b.className = 'btn-sm ' + cls; b.textContent = text;
            b.addEventListener('click', fn);
            return b;
        };
        const cell = tr.children[4];
        cell.appendChild(mk('btn-success', 'View', () => viewFormula(fm.id)));
        if (!fm.archived) {
            cell.appendChild(document.createTextNode(' '));
            cell.appendChild(mk('btn-info', 'Edit', () => editFormula(fm.id)));
        }
        cell.appendChild(document.createTextNode(' '));
        cell.appendChild(mk('btn-warning', fm.archived ? 'Restore' : 'Archive', () => toggleArchiveFormula(fm.id, !fm.archived)));
        cell.appendChild(document.createTextNode(' '));
        cell.appendChild(mk('btn-danger', 'Delete', () => deleteFormula(fm.id)));
        body.appendChild(tr);
    });
}

function setShowArchivedFormulas(on) {
    showArchivedFormulas = !!on;
    renderFormulaLibrary();
}

async function toggleArchiveFormula(id, archive) {
    const fm = savedFormulas.find(x => x.id === id);
    if (!fm) return;
    if (archive && !confirm('Archive "' + fm.name + '"? It is hidden from the production and Formulation Lab lists and cannot be used in a production batch until you restore it. Nothing is deleted.')) return;
    try {
        const res = await fetch('/api/factory/formulas/' + id + '/archive', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ archived: !!archive })
        });
        if (res.status === 403) { alert('Only an admin or factory user can archive formulas.'); return; }
        if (!res.ok) { alert('Could not ' + (archive ? 'archive' : 'restore') + ' this formula.'); return; }
        await loadSavedFormulas();
        if (typeof labLoadSavedList === 'function') labLoadSavedList();
    } catch (err) {
        alert('Network problem. Check the list and try again.');
    }
}'''


def p_factory_js(src):
    if 'toggleArchiveFormula' in src:
        return None
    s = src
    a, b = func_span(s, 'loadSavedFormulas', 'js')
    s = s[:a] + JS_LOAD + s[b:]
    a, b = func_span(s, 'renderFormulaLibrary', 'js')
    s = s[:a] + JS_RENDER + s[b:]
    return s


def p_nutrition_js(src):
    if 'loadSavedFormulas' in src:
        return None
    m = re.search(r'async function labInit\s*\(\s*\)\s*\{\n', src)
    need(m, 'async function labInit() {')
    return src[:m.end()] + "  if (typeof loadSavedFormulas === 'function') loadSavedFormulas();   // Formula Library lives in this tab now\n" + src[m.end():]


# ------------------------------------------------------------------ 3. move the Formula Library card
CARD_RE = re.compile(r'<div[^>]*\bid="formula-library"[^>]*>')


def card_span(html):
    m = CARD_RE.search(html)
    need(m, '<div ... id="formula-library">')
    depth, pos = 0, m.start()
    for t in re.finditer(r'<div\b|</div>', html[m.start():]):
        depth += 1 if t.group().startswith('<div') else -1
        if depth == 0:
            return m.start(), m.start() + t.end()
    raise Missing('end of the formula-library card')


MOVED = {}


def p_factory_tab(src):
    if not CARD_RE.search(src):
        return None
    a, b = card_span(src)
    card = src[a:b]
    btn = '<button type="button" class="btn-sm btn-info" onclick="newFormula()">+ New formula</button>'
    need(btn in card, 'the "+ New formula" button inside the card')
    card = card.replace(btn, btn + '\n  <label style="margin-left:12px;font-size:.9rem;cursor:pointer"><input type="checkbox" onchange="setShowArchivedFormulas(this.checked)"> Show archived<span id="formula-archived-count"></span></label>', 1)
    MOVED['card'] = card
    end = b
    while src[end:end + 1] in ('\r', '\n'):
        end += 1
    return src[:a] + src[end:]


def p_formulator_tab(src):
    if CARD_RE.search(src):
        return None
    need('card' in MOVED, 'the card (factory_tab step must succeed first)')
    i = src.rstrip().rfind('</div>')
    need(i > 0, 'closing </div> of the Formulation Lab tab')
    return src[:i] + MOVED['card'] + '\n' + src[i:]


# ------------------------------------------------------------------ test file
TEST = '''import os, tempfile, unittest
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-deployment')
from app import create_app
from services.db import db
from services.models import FeedIngredient, Location, User


class FormulaArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='emining-arch-')
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only-secret-key-not-for-deployment',
                               'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + os.path.join(self.tmp.name, 't.db')})
        self.ctx = self.app.app_context(); self.ctx.push()
        loc = Location(name='F', code='F-1', location_type='FACTORY'); db.session.add(loc); db.session.flush()
        self.users = {}
        for role in ('admin', 'factory', 'sales'):
            u = User(username=role, role=role, location='F-1'); u.set_password('1234')
            db.session.add(u); db.session.flush(); u.location_id = loc.id; self.users[role] = u.id
        self.a = FeedIngredient(name='Maize', category='Raw - Energy', cost_per_kg=40)
        self.b = FeedIngredient(name='Soya', category='Raw - Protein', cost_per_kg=90)
        db.session.add_all([self.a, self.b]); db.session.commit()

    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.ctx.pop(); self.tmp.cleanup()

    def c(self, role):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s['user_id'] = self.users[role]
        return c

    def test_archive_hides_then_restore_brings_back(self):
        c = self.c('admin')
        r = c.post('/api/factory/formulas', json={'name': 'Old mix', 'lines': [
            {'ingredient_id': self.a.id, 'pct': 70}, {'ingredient_id': self.b.id, 'pct': 30}]}).get_json()
        fid = r['id']
        self.assertEqual([f['name'] for f in c.get('/api/factory/formulas').get_json()], ['Old mix'])
        self.assertEqual(c.post('/api/factory/formulas/%d/archive' % fid, json={'archived': True}).get_json()['archived'], True)
        self.assertEqual(c.get('/api/factory/formulas').get_json(), [])
        allf = c.get('/api/factory/formulas?include_archived=1').get_json()
        self.assertEqual(len(allf), 1); self.assertTrue(allf[0]['archived'])
        c.post('/api/factory/formulas/%d/archive' % fid, json={'archived': False})
        self.assertEqual(len(c.get('/api/factory/formulas').get_json()), 1)

    def test_only_admin_or_factory_can_archive(self):
        fid = self.c('admin').post('/api/factory/formulas', json={'name': 'X', 'lines': [
            {'ingredient_id': self.a.id, 'pct': 100}]}).get_json()['id']
        self.assertEqual(self.c('sales').post('/api/factory/formulas/%d/archive' % fid, json={}).status_code, 403)
        self.assertEqual(self.c('factory').post('/api/factory/formulas/%d/archive' % fid, json={}).status_code, 200)

    def test_archive_unknown_formula_404(self):
        self.assertEqual(self.c('admin').post('/api/factory/formulas/9999/archive', json={}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
'''


def add_test():
    p = os.path.join('tests', 'test_formula_archive.py')
    if os.path.exists(p):
        results.append(('already applied', 'archive tests', p)); return
    if os.path.isdir('tests'):
        STAGED.append((p, TEST))
        results.append(('will add', 'archive tests', p))


def main():
    for must in ('app.py', 'services', 'routes', 'static', 'templates'):
        if not os.path.exists(must):
            sys.exit('Run this from the repo folder (the one containing app.py). Missing: ' + must)
    patch_file('services/formulation_engine.py', p_rdp, '1. RDP/RUP warning skips no-protein items')
    patch_file('services/models.py', p_models, '2a. SavedFormula.archived column')
    patch_file('services/db.py', p_db, '2b. auto-add archived column on start')
    patch_file('routes/factory.py', p_factory_py, '2c. list/archive routes + production block')
    patch_file('routes/nutrition.py', p_nutrition_py, '2d. saving from the Lab un-archives')
    patch_file('static/js/factory.js', p_factory_js, '2e. library list with Archive/Restore + Show archived')
    patch_file('static/js/nutrition.js', p_nutrition_js, '3a. Lab tab loads the library')
    patch_file('templates/tabs/factory_tab.html', p_factory_tab, '3b. remove card from Factory tab')
    patch_file('templates/tabs/formulator_tab.html', p_formulator_tab, '3c. add card to Formulation Lab tab')
    add_test()
    width = max(len(r[1]) for r in results)
    print()
    for status, label, detail in results:
        print('%-18s %-*s  %s' % (status, width, label, detail))
    bad = [r for r in results if r[0] in ('ANCHOR NOT FOUND', 'MISSING FILE')]
    if bad:
        print('\n%d step(s) failed, so NOTHING was changed (the steps depend on each other). Send me the file(s) named above.' % len(bad))
        sys.exit(1)
    if CHECK:
        print('\nCheck only: nothing was changed.')
        return
    for path, new in STAGED:
        if os.path.exists(path):
            dest = os.path.join(BACKUP, path)
            if not os.path.exists(dest):
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(path, dest)
        write(path, new)
    if STAGED:
        print('\nDone. Backups are in %s/. Now reload the web app (PythonAnywhere > Web > Reload).' % BACKUP)
        print('The new column is added automatically on restart; you do not need migrate.py for this change.')
    else:
        print('\nNothing to do: everything was already applied.')


if __name__ == '__main__':
    main()
