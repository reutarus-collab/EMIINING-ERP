# Run from ~/EMIINING-ERP AFTER add_formula_editor.py:  python add_excel_import.py
import sys

def read(p): return open(p, encoding='utf-8').read()
def write(p, s): open(p, 'w', encoding='utf-8').write(s)

if 'formula-editor' not in read('templates/tabs/factory_tab.html'):
    sys.exit('Run add_formula_editor.py first.')
if 'formula-import-file' in read('templates/tabs/factory_tab.html'):
    sys.exit('Already applied.')

def rep(path, old, new):
    s = read(path)
    if s.count(old) != 1:
        sys.exit('FAILED: expected text not found exactly once in %s:\n%s' % (path, old[:70]))
    write(path, s.replace(old, new, 1))

# 1. remembered Excel-name -> item mappings (no foreign key, so item deletes are not blocked)
with open('services/models.py', 'a', encoding='utf-8') as f:
    f.write('''

class FormulaAlias(db.Model):
    __tablename__ = 'formula_aliases'
    id = db.Column(db.Integer, primary_key=True)
    alias = db.Column(db.String(150), nullable=False, unique=True)
    ingredient_id = db.Column(db.Integer, nullable=False)
''')

# 2. server
with open('routes/factory.py', 'a', encoding='utf-8') as f:
    f.write('''

_ING_HEADERS = {'ingredient', 'ingredients', 'item', 'items', 'raw material', 'raw materials', 'name', 'feedstuff'}
_PCT_HEADERS = {'%', 'percent', 'percentage', 'pct', 'inclusion', 'inclusion %', 'inclusion%', '% inclusion', 'amount', 'amount %', 'amount%'}
_SKIP_HEADERS = {'cost', 'price', 'min', 'min.', 'max', 'max.', 'notes', 'note', 'unit', 'units', '$/cwt', 'kes', 'ksh'}
_XL_ERRORS = {'#VALUE!', '#REF!', '#N/A', '#NAME?', '#DIV/0!', '#NULL!', '#NUM!'}
_GENERIC_SHEETS = {'sheet', 'sheet1', 'sheet2', 'formulate', 'formula', 'formulas', 'data'}


def _xl_num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    s = str(v).strip().replace(',', '')
    if s.endswith('%'):
        s = s[:-1]
    try:
        f = float(s)
        return f if math.isfinite(f) else None
    except ValueError:
        return None


def _parse_sheet(ws, default_name):
    rows = list(ws.iter_rows(min_row=1, max_row=400, max_col=40, values_only=True))
    hdr_i = ing_c = None
    for i, row in enumerate(rows[:20]):
        for c, v in enumerate(row):
            if isinstance(v, str) and v.strip().lower() in _ING_HEADERS:
                hdr_i, ing_c = i, c
                break
        if hdr_i is not None:
            break
    if hdr_i is None:
        return []
    header = rows[hdr_i]
    pct_cols = [c for c, v in enumerate(header)
                if c > ing_c and isinstance(v, str) and v.strip().lower() in _PCT_HEADERS]
    if pct_cols:
        cols = [(pct_cols[0], default_name)]
    else:
        cols = [(c, str(v).strip()) for c, v in enumerate(header)
                if c > ing_c and isinstance(v, str) and v.strip() and v.strip().lower() not in _SKIP_HEADERS]
    out = []
    for col, name in cols:
        merged = {}
        damaged = False
        for row in rows[hdr_i + 1:]:
            if ing_c >= len(row):
                continue
            n = row[ing_c]
            cell = row[col] if col < len(row) else None
            if (isinstance(n, str) and n.strip().upper() in _XL_ERRORS) or (isinstance(cell, str) and cell.strip().upper() in _XL_ERRORS):
                damaged = True
                continue
            if not isinstance(n, str) or not n.strip():
                continue
            if n.strip().lower() in ('total', 'totals', 'sum'):
                break
            pct = _xl_num(row[col]) if col < len(row) else None
            if pct is None or pct <= 0:
                continue
            key = n.strip()
            merged[key] = merged.get(key, 0.0) + pct
        if not merged:
            continue
        total = sum(merged.values())
        if 0.99 <= total <= 1.01:  # file stores fractions (0.5 = 50%)
            merged = {k: round(v * 100, 4) for k, v in merged.items()}
        out.append({'name': name, 'damaged': damaged,
                    'lines': [{'source_name': k, 'pct': round(v, 4)} for k, v in merged.items()]})
    return out


def _norm_name(s):
    import re
    return re.sub(r'[^a-z0-9]', '', str(s).lower())


@factory_bp.route('/api/factory/formulas/import-preview', methods=['POST'])
@roles_required('admin', 'factory')
def import_formulas_preview():
    import io, difflib, os
    from services.models import SavedFormula, FormulaAlias
    f = request.files.get('file')
    if not f or not (f.filename or '').lower().endswith('.xlsx'):
        return jsonify(status='error', message='Upload an Excel .xlsx file (File > Save As > Excel Workbook).'), 400
    blob = f.read(2 * 1024 * 1024 + 1)
    if len(blob) > 2 * 1024 * 1024:
        return jsonify(status='error', message='That file is larger than 2 MB.'), 400
    try:
        import openpyxl
    except ImportError:
        return jsonify(status='error', message='The server is missing openpyxl. In a PythonAnywhere console run: pip install openpyxl  then press Reload.'), 500
    try:
        wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    except Exception:
        return jsonify(status='error', message='Could not read this file. Save it again as .xlsx and retry.'), 400

    stem = os.path.splitext(os.path.basename(f.filename))[0].replace('_', ' ').replace('-', ' ').strip().title() or 'Imported formula'
    formulas = []
    for ws in wb.worksheets[:10]:
        title = (ws.title or '').strip()
        default = stem if title.lower() in _GENERIC_SHEETS else title
        formulas.extend(_parse_sheet(ws, default))
    skipped, seen_sigs, kept = [], set(), []
    for fm in formulas:
        sig = tuple(sorted((_norm_name(l['source_name']), round(l['pct'], 3)) for l in fm['lines']))
        if fm.get('damaged'):
            skipped.append('"%s" skipped: it contains Excel errors (like #VALUE!), so some ingredients are missing.' % fm['name'])
        elif sig in seen_sigs:
            skipped.append('"%s" skipped: identical to another formula in the file.' % fm['name'])
        else:
            seen_sigs.add(sig)
            kept.append(fm)
    formulas = kept
    if not formulas:
        return jsonify(status='error', message='No usable formula found. The sheet needs an "Ingredient" column and a "%" (or "Amount") column, or one column per formula.'), 400

    raw = [i for i in FeedIngredient.query.all() if 'raw' in (i.category or '').lower()]
    by_norm = {_norm_name(i.name): i for i in raw}
    aliases = {a.alias: a.ingredient_id for a in FormulaAlias.query.all()}
    raw_ids = {i.id: i for i in raw}
    existing = {fm.name.lower() for fm in SavedFormula.query.all()}
    used_names = set()
    for fm in formulas[:30]:
        base, n = fm['name'], 2
        while fm['name'].lower() in used_names:
            fm['name'] = '%s %d' % (base, n)
            n += 1
        used_names.add(fm['name'].lower())
        fm['exists'] = fm['name'].lower() in existing
        for ln in fm['lines']:
            key = _norm_name(ln['source_name'])
            ln['match_id'], ln['suggested'] = None, False
            if aliases.get(key) in raw_ids:
                ln['match_id'] = aliases[key]
            elif key in by_norm:
                ln['match_id'] = by_norm[key].id
            else:
                close = difflib.get_close_matches(key, list(by_norm.keys()), n=1, cutoff=0.8)
                if close:
                    ln['match_id'], ln['suggested'] = by_norm[close[0]].id, True
        fm['total_pct'] = round(sum(l['pct'] for l in fm['lines']), 4)
    return jsonify(status='success', formulas=formulas[:30], skipped=skipped)


@factory_bp.route('/api/factory/formula-aliases', methods=['POST'])
@roles_required('admin', 'factory')
def save_formula_aliases():
    from services.models import FormulaAlias
    pairs = (request.get_json(silent=True) or {}).get('aliases') or []
    saved = 0
    for p in pairs[:200]:
        try:
            ing = db.session.get(FeedIngredient, int(p.get('ingredient_id')))
        except (TypeError, ValueError, AttributeError):
            continue
        key = _norm_name(p.get('alias') or '')
        if not key or len(key) > 150 or not ing or 'raw' not in (ing.category or '').lower():
            continue
        row = FormulaAlias.query.filter_by(alias=key).first()
        if row:
            row.ingredient_id = ing.id
        else:
            db.session.add(FormulaAlias(alias=key, ingredient_id=ing.id))
        saved += 1
    db.session.commit()
    return jsonify(status='success', saved=saved)
''')

# 3. dependency list
req = read('requirements.txt')
if 'openpyxl' not in req.lower():
    write('requirements.txt', req.rstrip('\n') + '\nopenpyxl\n')

# 4. UI
rep('templates/tabs/factory_tab.html',
    '<button type="button" class="btn-sm btn-info" onclick="newFormula()">+ New formula</button>',
    '''<button type="button" class="btn-sm btn-info" onclick="newFormula()">+ New formula</button>
  <div style="margin:10px 0">
    <input type="file" id="formula-import-file" accept=".xlsx" />
    <button type="button" class="btn-sm btn-info" onclick="previewFormulaImport()">Import from Excel</button>
  </div>
  <div id="formula-import-panel" style="display:none; background:#f8f9fa; padding:15px; border-radius:6px; margin:10px 0;"></div>''')

# 5. JS
with open('static/js/factory.js', 'a', encoding='utf-8') as f:
    f.write('''

let formulaImport = [];
let formulaImportSkipped = [];
let importingFormulas = false;

function importNorm(s) { return String(s || '').toLowerCase().replace(/[^a-z0-9]/g, ''); }

async function previewFormulaImport() {
    const input = document.getElementById('formula-import-file');
    const panel = document.getElementById('formula-import-panel');
    if (!input.files.length) { alert('Choose your Excel (.xlsx) file first.'); return; }
    const fd = new FormData();
    fd.append('file', input.files[0]);
    panel.style.display = 'block';
    panel.style.color = '';
    panel.textContent = 'Reading file...';
    try {
        const res = await fetch('/api/factory/formulas/import-preview', { method: 'POST', credentials: 'same-origin', body: fd });
        let data = {};
        try { data = await res.json(); } catch (e) {}
        if (!res.ok || data.status !== 'success') {
            panel.style.color = '#b00';
            panel.textContent = data.message || (res.status === 403 ? 'You do not have permission to import formulas.' : 'Could not read this file.');
            return;
        }
        formulaImport = data.formulas;
        formulaImportSkipped = data.skipped || [];
        renderFormulaImport();
    } catch (e) {
        panel.style.color = '#b00';
        panel.textContent = 'Network problem. Try again.';
    }
}

function renderFormulaImport() {
    const panel = document.getElementById('formula-import-panel');
    panel.style.color = '';
    panel.innerHTML = '';
    const raw = factoryInventory.filter(i => (i.category || '').includes('Raw'));
    const head = document.createElement('p');
    head.textContent = 'Check each Excel ingredient points at the right item in your system. Amber means a guess - confirm it. Your choices are remembered for next time.';
    panel.appendChild(head);
    formulaImportSkipped.forEach(m => {
        const d = document.createElement('div');
        d.style.color = '#b06000';
        d.textContent = m;
        panel.appendChild(d);
    });
    formulaImport.forEach(f => {
        const box = document.createElement('div');
        box.style.cssText = 'border:1px solid #ccc; border-radius:6px; padding:10px; margin-bottom:12px; background:#fff;';
        const include = document.createElement('input');
        include.type = 'checkbox'; include.checked = !f.exists;
        const nameInput = document.createElement('input');
        nameInput.type = 'text'; nameInput.value = f.name; nameInput.maxLength = 100;
        const top = document.createElement('div');
        top.appendChild(include);
        top.appendChild(document.createTextNode(' Import as: '));
        top.appendChild(nameInput);
        box.appendChild(top);
        if (f.exists) {
            const w = document.createElement('div');
            w.style.color = '#b06000';
            w.textContent = 'A formula with this name already exists. Rename it and tick the box to import.';
            box.appendChild(w);
        }
        const table = document.createElement('table');
        table.className = 'data-table';
        table.innerHTML = '<thead><tr><th>Excel ingredient</th><th>%</th><th>Your item</th><th></th></tr></thead>';
        const tb = document.createElement('tbody');
        const selects = [];
        f.lines.forEach(l => {
            const tr = document.createElement('tr');
            const c1 = document.createElement('td'); c1.textContent = l.source_name;
            const c2 = document.createElement('td'); c2.textContent = Number(l.pct).toFixed(3);
            const c3 = document.createElement('td');
            const sel = document.createElement('select');
            const blank = document.createElement('option'); blank.value = ''; blank.textContent = '-- choose item --';
            sel.appendChild(blank);
            raw.forEach(i => {
                const o = document.createElement('option');
                o.value = i.id; o.textContent = i.name;
                if (l.match_id != null && Number(l.match_id) === i.id) o.selected = true;
                sel.appendChild(o);
            });
            c3.appendChild(sel);
            const c4 = document.createElement('td');
            const paint = () => {
                if (!sel.value) { c4.textContent = 'needs an item'; c4.style.color = '#b00'; }
                else if (l.suggested && Number(sel.value) === Number(l.match_id)) { c4.textContent = 'check this guess'; c4.style.color = '#b06000'; }
                else { c4.textContent = 'ok'; c4.style.color = '#0a7a2f'; }
            };
            sel.addEventListener('change', paint);
            paint();
            [c1, c2, c3, c4].forEach(c => tr.appendChild(c));
            tb.appendChild(tr);
            selects.push(sel);
        });
        table.appendChild(tb);
        const wrap = document.createElement('div');
        wrap.style.overflowX = 'auto';
        wrap.appendChild(table);
        box.appendChild(wrap);
        const tot = document.createElement('div');
        tot.textContent = 'Total in Excel: ' + Number(f.total_pct).toFixed(3) + '%';
        box.appendChild(tot);
        let scale = null;
        if (Math.abs(f.total_pct - 100) > 0.1) {
            scale = document.createElement('input');
            scale.type = 'checkbox'; scale.checked = true;
            const lab = document.createElement('label');
            lab.appendChild(scale);
            lab.appendChild(document.createTextNode(' Scale to 100% (total is outside 99.9-100.1)'));
            box.appendChild(lab);
        }
        f.ui = { include, nameInput, selects, scale };
        panel.appendChild(box);
    });
    const go = document.createElement('button');
    go.type = 'button'; go.className = 'btn-success'; go.textContent = 'Import selected formulas';
    go.addEventListener('click', commitFormulaImport);
    const cancel = document.createElement('button');
    cancel.type = 'button'; cancel.className = 'btn-sm btn-info'; cancel.textContent = 'Cancel';
    cancel.addEventListener('click', () => { panel.style.display = 'none'; panel.innerHTML = ''; });
    panel.appendChild(go);
    panel.appendChild(document.createTextNode(' '));
    panel.appendChild(cancel);
    const result = document.createElement('div');
    result.id = 'formula-import-result';
    result.style.marginTop = '10px';
    panel.appendChild(result);
}

async function commitFormulaImport() {
    if (importingFormulas) return;
    importingFormulas = true;
    const result = document.getElementById('formula-import-result');
    const messages = [];
    const aliasPairs = [];
    try {
        for (const f of formulaImport) {
            if (!f.ui.include.checked) continue;
            const name = f.ui.nameInput.value.trim();
            const merged = new Map();
            let complete = true;
            f.lines.forEach((l, li) => {
                const id = Number(f.ui.selects[li].value);
                if (!id) { complete = false; return; }
                merged.set(id, (merged.get(id) || 0) + Number(l.pct));
            });
            if (!complete) { messages.push(name + ': choose an item for every line.'); continue; }
            let lines = Array.from(merged, ([id, pct]) => ({ ingredient_id: id, pct: pct }));
            const total = lines.reduce((s, l) => s + l.pct, 0);
            if (f.ui.scale && f.ui.scale.checked) lines = lines.map(l => ({ ingredient_id: l.ingredient_id, pct: l.pct * 100 / total }));
            const res = await fetch('/api/factory/formulas', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name, notes: 'Imported from Excel', lines: lines })
            });
            let data = {};
            try { data = await res.json(); } catch (e) {}
            if (res.ok && data.status === 'success') {
                messages.push(name + ': saved.');
                f.lines.forEach((l, li) => {
                    const sel = f.ui.selects[li];
                    const itemName = sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : '';
                    if (importNorm(l.source_name) !== importNorm(itemName)) aliasPairs.push({ alias: l.source_name, ingredient_id: Number(sel.value) });
                });
                f.ui.include.checked = false;
            } else {
                messages.push(name + ': ' + (data.message || 'could not be saved.'));
            }
        }
        if (aliasPairs.length) {
            try {
                await fetch('/api/factory/formula-aliases', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ aliases: aliasPairs })
                });
            } catch (e) {}
        }
        await loadSavedFormulas();
    } catch (err) {
        messages.push('Network problem. Check the Formula Library before importing again; nothing is duplicated if a formula was already saved.');
    } finally {
        importingFormulas = false;
    }
    result.style.color = '#0a58ca';
    result.innerHTML = '';
    messages.forEach(m => { const d = document.createElement('div'); d.textContent = m; result.appendChild(d); });
}
''')
print('Applied. Run: pip install openpyxl   then Reload the web app and hard-refresh the page.')