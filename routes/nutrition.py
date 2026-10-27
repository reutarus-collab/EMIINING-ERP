"""Formulation Lab API: nutrient data, requirement profiles, Excel/paste import, optimise, verify, release gate."""
import difflib
import io
import math
import re
from datetime import datetime

from flask import Blueprint, jsonify, request

from routes.auth import roles_required
from services import formulation_engine as fe
from services.db import db
from services.models import (FeedIngredient, FormulaAlias, IngredientNutrient, Nutrient, RequirementLine,
                             RequirementProfile, SavedFormula, SavedFormulaLine)

nutrition_bp = Blueprint('nutrition', __name__)
LAB_ROLES = ('admin', 'accountant', 'factory')
EDIT_ROLES = ('admin', 'factory')
MAX_UPLOAD = 2 * 1024 * 1024


def _err(message, status=400, **extra):
    return jsonify(status='error', message=message, **extra), status


def _raw_ingredients():
    return FeedIngredient.query.filter(FeedIngredient.category.ilike('Raw%')).order_by(db.func.lower(FeedIngredient.name)).all()


def _costs(ids):
    from routes.factory import _current_costs
    return _current_costs(ids)


def _info(ids):
    ids = list(ids)
    costs = _costs(ids)
    out = {}
    for ing in FeedIngredient.query.filter(FeedIngredient.id.in_(ids)).all():
        out[ing.id] = dict(name=ing.name, price=float(costs.get(ing.id, ing.cost_per_kg or 0.0)),
                           max_pct=float(ing.max_inclusion_pct if ing.max_inclusion_pct else 100.0),
                           is_raw='raw' in (ing.category or '').lower())
    return out


def _clean_lines(raw_lines, nutrients):
    if not isinstance(raw_lines, list) or len(raw_lines) > 80:
        raise ValueError('Send the requirement lines as a list (at most 80).')
    return [fe.clean_line(r, nutrients) for r in raw_lines if isinstance(r, dict)]


def _recipe(raw):
    """[{ingredient_id, pct}] -> {id: pct}"""
    if not isinstance(raw, list) or not raw or len(raw) > 60:
        raise ValueError('Add at least one ingredient (at most 60).')
    out = {}
    for r in raw:
        try:
            iid, pct = int(r.get('ingredient_id')), float(r.get('pct'))
        except (TypeError, ValueError, AttributeError):
            raise ValueError('Every row needs an ingredient and a percentage.')
        if not math.isfinite(pct) or pct < 0 or pct > 100:
            raise ValueError('Percentages must be between 0 and 100.')
        if iid in out:
            raise ValueError('An ingredient appears twice. Combine it into one row.')
        if pct > 0:
            out[iid] = pct
    if not out:
        raise ValueError('Enter an inclusion above 0 for at least one ingredient.')
    return out


def _bounds(raw):
    out = {}
    for k, v in (raw or {}).items():
        try:
            iid = int(k)
            lo = float(v.get('min') or 0)
            hi = float(v['max']) if v.get('max') not in (None, '') else 100.0
        except (TypeError, ValueError, KeyError, AttributeError):
            raise ValueError('Inclusion limits must be numbers.')
        if not (0 <= lo <= 100 and 0 <= hi <= 100) or lo > hi:
            raise ValueError('Each ingredient minimum must be 0-100 and not above its maximum.')
        out[iid] = {'min': lo, 'max': hi}
    return out


# ----------------------------------------------------------------------------------------- reference data
@nutrition_bp.route('/api/nutrition/bootstrap', methods=['GET'])
@roles_required(*LAB_ROLES)
def bootstrap():
    nutrients = fe.load_nutrients()
    ings = _raw_ingredients()
    ids = [i.id for i in ings]
    raw, src = fe.raw_matrix(ids)
    costs = _costs(ids)
    return jsonify(
        nutrients=[dict(code=n.code, name=n.name, unit=n.unit, basis=n.basis, group=n.group, per=n.line_per, scale=n.line_scale)
                   for n in nutrients.values()],
        profiles=[dict(id=p.id, name=p.name, species=p.species, stage=p.stage or '', source=p.source or '', notes=p.notes or '')
                  for p in RequirementProfile.query.order_by(RequirementProfile.name).all()],
        ingredients=[dict(id=i.id, name=i.name, price=round(float(costs.get(i.id, i.cost_per_kg or 0.0)), 4),
                          max_pct=float(i.max_inclusion_pct or 100.0),
                          values={c: dict(value=v, source=src[i.id].get(c, '')) for c, v in raw[i.id].items()})
                     for i in ings])


@nutrition_bp.route('/api/nutrition/nutrients', methods=['POST'])
@roles_required(*EDIT_ROLES)
def add_nutrient():
    d = request.get_json(silent=True) or {}
    code = re.sub(r'[^A-Z0-9_]', '', str(d.get('code') or '').upper())[:20]
    name, unit, basis = str(d.get('name') or '').strip()[:100], str(d.get('unit') or '').strip()[:20], str(d.get('basis') or 'asfed')
    if not code or not name or not unit or basis not in ('asfed', 'dm', 'pct_cp'):
        return _err('Give the nutrient a code, a name, a unit and a basis (as fed, per kg DM, or % of CP).')
    if Nutrient.query.filter_by(code=code).first():
        return _err('That nutrient code already exists. Changing an existing nutrient would change the meaning of saved values.', 409)
    aliases = '|'.join(sorted({fe.norm(a) for a in [code, name] + str(d.get('aliases') or '').split(',') if fe.norm(a)}))
    db.session.add(Nutrient(code=code, name=name, unit=unit, basis=basis, group='custom', aliases=aliases,
                            sort=1000 + Nutrient.query.count()))
    db.session.commit()
    return jsonify(status='success', code=code)


@nutrition_bp.route('/api/nutrition/ingredient-values', methods=['POST'])
@roles_required(*EDIT_ROLES)
def set_values():
    """Set or clear single cells. value = null removes the value (back to UNKNOWN, never zero)."""
    d = request.get_json(silent=True) or {}
    cells = d.get('cells')
    nutrients = fe.load_nutrients()
    if not isinstance(cells, list) or not cells or len(cells) > 2000:
        return _err('Send 1 to 2000 cells.')
    valid_ids = {i.id for i in _raw_ingredients()}
    parsed = []
    for c in cells:
        try:
            iid, code = int(c.get('ingredient_id')), str(c.get('code'))
            value = None if c.get('value') in (None, '') else float(c.get('value'))
        except (TypeError, ValueError, AttributeError):
            return _err('Every cell needs an ingredient, a nutrient and a number.')
        if iid not in valid_ids or code not in nutrients:
            return _err('Unknown ingredient or nutrient in the request.')
        if value is not None and (not math.isfinite(value) or value < 0 or value > 1e6):
            return _err('Nutrient values must be between 0 and 1,000,000.')
        parsed.append((iid, code, value, str(c.get('source') or '').strip()[:200]))
    _apply_values(parsed)
    db.session.commit()
    return jsonify(status='success', saved=len(parsed))


# Highest value the Item Master columns accept (matches set_item_nutrients in routes/po.py).
LEGACY_CAPS = {'CP': 100.0, 'ME': 10.0, 'CF': 100.0, 'CA': 100.0, 'AVP': 100.0, 'LYS_TOT': 100.0, 'MET_TOT': 100.0}


def _mirror_to_item_master(writes):
    """An import writes the nutrient matrix. The Item Master list shows the seven older columns on the item
    itself, so copy those seven across, or an imported value would exist but never appear there."""
    for iid, code, value, _src in writes:
        col = fe.LEGACY.get(code)
        if not col or value is None or not (0 <= value <= LEGACY_CAPS[code]):
            continue
        item = db.session.get(FeedIngredient, iid)
        if item is not None:
            setattr(item, col, float(value))


def _apply_values(parsed, default_source='entered by hand'):
    for iid, code, value, source in parsed:
        row = IngredientNutrient.query.filter_by(ingredient_id=iid, nutrient_code=code).first()
        if value is None:
            if row:
                db.session.delete(row)
            continue
        if row is None:
            db.session.add(IngredientNutrient(ingredient_id=iid, nutrient_code=code, value=value,
                                              source=source or default_source))
        else:
            row.value, row.updated_at = value, datetime.utcnow()
            row.source = source or row.source or default_source


# ----------------------------------------------------------------------------------------- profiles
def _profile_json(p, nutrients):
    return dict(id=p.id, name=p.name, species=p.species, stage=p.stage or '', source=p.source or '', notes=p.notes or '',
                lines=fe.lines_from_profile(p, nutrients))


@nutrition_bp.route('/api/nutrition/profiles/<int:pid>', methods=['GET'])
@roles_required(*LAB_ROLES)
def get_profile(pid):
    p = db.session.get(RequirementProfile, pid)
    if not p:
        return _err('Profile not found.', 404)
    return jsonify(_profile_json(p, fe.load_nutrients()))


@nutrition_bp.route('/api/nutrition/profiles', methods=['POST'])
@roles_required(*EDIT_ROLES)
def save_profile():
    d = request.get_json(silent=True) or {}
    nutrients = fe.load_nutrients()
    name = str(d.get('name') or '').strip()
    if not name or len(name) > 100:
        return _err('Give the profile a name (up to 100 characters).')
    try:
        lines = _clean_lines(d.get('lines'), nutrients)
    except ValueError as exc:
        return _err(str(exc))
    p = db.session.get(RequirementProfile, int(d['id'])) if d.get('id') not in (None, '') else None
    same = RequirementProfile.query.filter(db.func.lower(RequirementProfile.name) == name.lower()).first()
    if same and (p is None or same.id != p.id):
        return _err('Another profile already uses that name.', 409)
    if p is None:
        p = RequirementProfile(name=name)
        db.session.add(p)
    p.name = name
    p.species = str(d.get('species') or 'other')[:30]
    p.stage, p.source, p.notes = (str(d.get(k) or '').strip()[:n] for k, n in (('stage', 60), ('source', 300), ('notes', 300)))
    p.lines = [RequirementLine(nutrient_code=l['code'], per=l['per'], scale=l['scale'], min_value=l['min'], max_value=l['max'],
                               tolerance=l['tolerance'], required=l['required'], enabled=l['enabled'], sort=k)
               for k, l in enumerate(lines)]
    db.session.commit()
    return jsonify(status='success', id=p.id)


@nutrition_bp.route('/api/nutrition/profiles/<int:pid>', methods=['DELETE'])
@roles_required('admin')
def delete_profile(pid):
    p = db.session.get(RequirementProfile, pid)
    if not p:
        return _err('Profile not found.', 404)
    used = SavedFormula.query.filter_by(profile_id=pid).count()
    if used:
        return _err(f'{used} saved formula(s) are checked against this profile. Re-save them under another profile first.', 409)
    db.session.delete(p)
    db.session.commit()
    return jsonify(status='success')


# ----------------------------------------------------------------------------------------- calculation
def _prep(ids, lines):
    nutrients = fe.load_nutrients()
    table, _ = fe.build_table(ids, nutrients, fe.needed_codes(lines, nutrients))
    return nutrients, table, _info(ids)


@nutrition_bp.route('/api/nutrition/verify', methods=['POST'])
@roles_required(*LAB_ROLES)
def verify_route():
    """Manual and Verify modes: recompute a typed-in formula from the raw data. Does not save anything."""
    d = request.get_json(silent=True) or {}
    nutrients = fe.load_nutrients()
    try:
        recipe = _recipe(d.get('recipe'))
        lines = _clean_lines(d.get('lines'), nutrients)
        bounds = _bounds(d.get('bounds'))
    except ValueError as exc:
        return _err(str(exc))
    ids = list(recipe)
    nutrients, table, info = _prep(ids, lines)
    res = fe.verify(recipe, lines, table, info, bounds)
    res['warnings'] += fe.rdp_rup_warnings(ids, nutrients, {i: info[i]['name'] for i in info})
    res['data_hash'] = fe.formula_data_hash(recipe, lines, table)
    res['recipe'] = [dict(ingredient_id=i, ingredient=info[i]['name'], pct=recipe[i],
                          price=info[i]['price']) for i in ids if i in info]
    return jsonify(**res)          # res['status'] is the verdict: pass | fail | incomplete


@nutrition_bp.route('/api/nutrition/optimize', methods=['POST'])
@roles_required(*LAB_ROLES)
def optimize_route():
    d = request.get_json(silent=True) or {}
    nutrients = fe.load_nutrients()
    try:
        ids = sorted({int(i) for i in (d.get('ingredient_ids') or [])})
        if not ids or len(ids) > 60:
            raise ValueError('Tick between 1 and 60 ingredients to allow.')
        lines = _clean_lines(d.get('lines'), nutrients)
        bounds = _bounds(d.get('bounds'))
        batch = float(d.get('batch_kg') or 1000)
        precision = float(d.get('precision_kg') or 0.1)
        if not (0 < batch <= 1e6) or not (0 < precision <= batch):
            raise ValueError('Batch size and weighing precision must be positive (precision cannot exceed the batch).')
    except (ValueError, TypeError) as exc:
        return _err(str(exc) if isinstance(exc, ValueError) else 'Check the batch size and ingredient list.')
    nutrients, table, info = _prep(ids, lines)
    if any(i not in info or not info[i]['is_raw'] for i in ids):
        return _err('Every ingredient must be a Raw item.')
    for k, v in (d.get('prices') or {}).items():          # one-off price overrides, like typing a price into Excel
        try:
            if int(k) in info and float(v) > 0:
                info[int(k)]['price'] = float(v)
        except (TypeError, ValueError):
            return _err('Price overrides must be numbers above 0.')
    res = fe.optimize(ids, lines, table, nutrients, info, bounds, batch, precision, bool(d.get('exclude_missing')))
    if res.get('status') in ('optimal', 'infeasible'):
        res['warnings'] = fe.rdp_rup_warnings(ids, nutrients, {i: info[i]['name'] for i in info})
    return jsonify(**res)


# ----------------------------------------------------------------------------------------- save + release gate
def release_check(fm):
    """Live re-verification of a saved formula against its profile's CURRENT limits and data."""
    if fm.profile_id is None:
        return dict(controlled=False, ok=True, reasons=[])
    prof = db.session.get(RequirementProfile, fm.profile_id)
    if prof is None:
        return dict(controlled=True, ok=False, reasons=['The requirement profile this formula was checked against no longer exists.'])
    nutrients = fe.load_nutrients()
    lines = fe.lines_from_profile(prof, nutrients)
    recipe = {l.ingredient_id: float(l.pct) for l in fm.lines}
    nutrients, table, info = _prep(list(recipe), lines)
    res = fe.verify(recipe, lines, table, info)
    return dict(controlled=True, ok=res['releasable'], verdict=res['status'], profile=prof.name, reasons=res['issues'])


@nutrition_bp.route('/api/nutrition/formulas', methods=['POST'])
@roles_required(*EDIT_ROLES)
def save_checked_formula():
    """Save a formula and verify it against the SAVED profile (not the on-screen edits, which could be loosened)."""
    d = request.get_json(silent=True) or {}
    name = str(d.get('name') or '').strip()
    if not name or len(name) > 100:
        return _err('Give the formula a name (up to 100 characters).')
    try:
        recipe = _recipe(d.get('recipe'))
    except ValueError as exc:
        return _err(str(exc))
    try:
        prof = db.session.get(RequirementProfile, int(d.get('profile_id')))
    except (ValueError, TypeError):
        prof = None
    if prof is None:
        return _err('Choose a saved requirement profile first. Save the profile, then save the formula.')
    if abs(sum(recipe.values()) - 100.0) > 0.1:
        return _err('Percentages add up to %.3f%%. A saved formula must total 100%% (within 0.1).' % sum(recipe.values()))
    info = _info(list(recipe))
    if any(i not in info or not info[i]['is_raw'] for i in recipe):
        return _err('Every ingredient must be a Raw item.')
    same = SavedFormula.query.filter(db.func.lower(SavedFormula.name) == name.lower()).first()
    fm = db.session.get(SavedFormula, int(d['id'])) if d.get('id') not in (None, '') else same
    if fm is not None and same is not None and same.id != fm.id:
        return _err('Another formula already uses that name.', 409)
    if fm is None:
        fm = SavedFormula(name=name)
        db.session.add(fm)
    fm.name, fm.notes, fm.profile_id = name, str(d.get('notes') or '').strip()[:300], prof.id
    fm.lines = [SavedFormulaLine(ingredient_id=i, pct=round(p, 4)) for i, p in recipe.items()]
    db.session.flush()
    chk = release_check(fm)
    fm.last_verified_ok, fm.last_verified_at = chk['ok'], datetime.utcnow()
    db.session.commit()
    return jsonify(status='success', id=fm.id, releasable=chk['ok'], reasons=chk['reasons'], profile=prof.name)


@nutrition_bp.route('/api/nutrition/formulas/<int:fid>/release-check', methods=['GET'])
@roles_required('admin', 'accountant', 'warehouse', 'factory')
def release_check_route(fid):
    fm = db.session.get(SavedFormula, fid)
    if not fm:
        return _err('Formula not found.', 404)
    return jsonify(status='success', **release_check(fm))


# ----------------------------------------------------------------------------------------- Excel / paste import
def _cell(v):
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _read_rows(file_storage, text):
    if file_storage is not None:
        blob = file_storage.read(MAX_UPLOAD + 1)
        if len(blob) > MAX_UPLOAD:
            raise ValueError('That file is larger than 2 MB.')
        fname = (file_storage.filename or '').lower()
        if fname.endswith('.xlsx'):
            try:
                import openpyxl
            except ImportError:
                raise ValueError('The server is missing openpyxl. Run: pip install openpyxl  then Reload.')
            try:
                wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
            except Exception:
                raise ValueError('Could not read that file. Save it again as .xlsx and retry.')
            ws = wb.worksheets[0]
            return [[_cell(c) for c in row] for row in ws.iter_rows(values_only=True)][:500]
        text = blob.decode('utf-8-sig', errors='replace')
    text = text or ''
    if len(text) > MAX_UPLOAD:
        raise ValueError('That is too much text to import at once.')
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        raise ValueError('Nothing to import. Copy the cells from Excel and paste them in.')
    delim = '\t' if any('\t' in l for l in lines[:5]) else (';' if lines[0].count(';') > lines[0].count(',') else ',')
    import csv
    return [[c.strip() for c in row] for row in csv.reader(lines, delimiter=delim)][:500]


def _num(s):
    s = str(s).strip().replace('%', '').replace(' ', '')
    if s == '':
        return None, None
    if re.fullmatch(r'-?\d+,\d+', s):
        s = s.replace(',', '.')
    try:
        v = float(s)
    except ValueError:
        return None, f'"{s}" is not a number'
    if not math.isfinite(v):
        return None, f'"{s}" is not a number'
    return v, None


def _match_ingredient(label, by_norm, aliases, raw_ids):
    key = fe.norm(label)
    if key in aliases and aliases[key] in raw_ids:
        return aliases[key], False
    if key in by_norm:
        return by_norm[key], False
    close = difflib.get_close_matches(key, list(by_norm), n=1, cutoff=0.85)
    return (by_norm[close[0]], True) if close else (None, False)


def _transposed_if_needed(rows, amap):
    """Excel sheets are often nutrients down the side and ingredients across the top: flip those."""
    def hits(cells):
        return sum(1 for c in cells if fe.norm(c) in amap)
    horizontal = max((hits(r[1:]) for r in rows[:10]), default=0)
    vertical = hits([r[0] for r in rows if r])
    if vertical >= 2 and vertical > horizontal:
        width = max(len(r) for r in rows)
        return [[(r[j] if j < len(r) else '') for r in rows] for j in range(width)], True
    return rows, False


@nutrition_bp.route('/api/nutrition/import/ingredients', methods=['POST'])
@roles_required(*EDIT_ROLES)
def import_ingredients():
    f = request.files.get('file')
    form = request.form if f is not None else (request.get_json(silent=True) or {})
    commit = str(form.get('commit', '')).lower() in ('1', 'true', 'yes')
    try:
        rows = _read_rows(f, None if f is not None else form.get('text'))
    except ValueError as exc:
        return _err(str(exc))
    nutrients = fe.load_nutrients()
    amap = fe.alias_map(nutrients)
    rows, flipped = _transposed_if_needed(rows, amap)
    hdr_i = next((i for i, r in enumerate(rows[:10]) if sum(1 for c in r[1:] if fe.norm(c) in amap) >= 1), None)
    if hdr_i is None:
        return _err('Could not find a header row. Row 1 should have the ingredient column first, then nutrient names such as CP, Ca, SID Lys.')
    header = rows[hdr_i]
    colmap, unknown, ignored, source_col = {}, [], [], None
    for j, h in enumerate(header):
        if j == 0 or not h:
            continue
        n = fe.norm(h)
        if n in ('source', 'reference', 'notes', 'note'):
            source_col = j if n in ('source', 'reference') else source_col
        elif n in ('price', 'cost', 'unit', 'units', 'kes', 'ksh'):
            ignored.append(h)
        elif n in amap:
            colmap[j] = amap[n]
        else:
            unknown.append(h)
    hints = [f'"{u}": not matched. If it is a total amino acid, name it "Total Lys"/"Total Met" (or add a nutrient). '
             f'A bare amino acid name is never assumed to be SID.' if fe.norm(u) in ('lysine', 'lys', 'methionine', 'met', 'threonine', 'thr',
                                                                                    'tryptophan', 'trp', 'cysteine', 'cystine', 'arginine', 'valine', 'isoleucine')
             else f'"{u}": not matched to a nutrient. Add it under Nutrients first, or rename the column.' for u in unknown]
    raw_ings = _raw_ingredients()
    by_norm, raw_ids = {fe.norm(i.name): i.id for i in raw_ings}, {i.id for i in raw_ings}
    names = {i.id: i.name for i in raw_ings}
    aliases = {fe.norm(a.alias): a.ingredient_id for a in FormulaAlias.query.all()}
    overrides = form.get('overrides') or {}
    if isinstance(overrides, str):
        import json
        try:
            overrides = json.loads(overrides)
        except ValueError:
            overrides = {}
    out_rows, writes, errors = [], [], 0
    today = datetime.utcnow().strftime('%Y-%m-%d')
    label = (f.filename if f is not None and f.filename else 'pasted from Excel')
    for ridx, r in enumerate(rows[hdr_i + 1:], start=hdr_i + 2):
        if not r or not str(r[0]).strip():
            continue
        iid, fuzzy = _match_ingredient(r[0], by_norm, aliases, raw_ids)
        ov = overrides.get(str(ridx))
        if ov is not None and int(ov) in raw_ids:
            iid, fuzzy = int(ov), False
        cells, row_err = [], []
        for j, code in colmap.items():
            v, e = _num(r[j]) if j < len(r) else (None, None)
            if e:
                row_err.append(f'{nutrients[code].name}: {e}')
            elif v is not None:
                if v < 0 or v > 1e6:
                    row_err.append(f'{nutrients[code].name}: {v} is out of range')
                else:
                    cells.append(dict(code=code, value=v))
        errors += len(row_err)
        status = 'ok' if iid and not fuzzy else ('confirm' if iid else 'no_match')
        out_rows.append(dict(row=ridx, name=r[0], ingredient_id=iid, ingredient=names.get(iid), status=status,
                             errors=row_err, cells=cells, suggested=fuzzy))
        if commit and iid and not fuzzy:
            src = (str(r[source_col]).strip() if source_col is not None and source_col < len(r) and str(r[source_col]).strip()
                   else f'import {label} {today}')
            writes += [(iid, c['code'], c['value'], src) for c in cells]
    if commit:
        _apply_values(writes)
        _mirror_to_item_master(writes)
        db.session.commit()
    return jsonify(status='success', committed=commit, written=len(writes), transposed=flipped, rows=out_rows,
                   columns=[nutrients[c].name for c in colmap.values()], unknown_columns=hints, ignored_columns=ignored,
                   skipped_rows=[r['row'] for r in out_rows if r['status'] != 'ok'], error_count=errors,
                   note='Blank cells are left alone: they never overwrite a value and are never treated as zero.')


def _limit_cap(unit):
    u = (unit or '').lower()
    if 'mg/kg' in u or 'ppm' in u:
        return 1000.0
    if '%' in u or 'mcal' in u:
        return 100.0
    return None


@nutrition_bp.route('/api/nutrition/import/requirements', methods=['POST'])
@roles_required(*EDIT_ROLES)
def import_requirements():
    """Turn a pasted requirements table (Nutrient | Min | Actual | Max | Unit) into profile lines."""
    f = request.files.get('file')
    form = request.form if f is not None else (request.get_json(silent=True) or {})
    commit = str(form.get('commit', '')).lower() in ('1', 'true', 'yes')
    name = str(form.get('profile_name') or '').strip()
    try:
        rows = _read_rows(f, None if f is not None else form.get('text'))
    except ValueError as exc:
        return _err(str(exc))
    nutrients = fe.load_nutrients()
    amap = fe.alias_map(nutrients)
    cols = None
    for r in rows[:5]:
        ns = [fe.norm(c) for c in r]
        if 'min' in ns and 'max' in ns:
            cols = dict(min=ns.index('min'), max=ns.index('max'), unit=ns.index('unit') if 'unit' in ns else None)
            rows = rows[rows.index(r) + 1:]
            break
    if cols is None:                       # headerless paste: Name | Min | Actual | Max | Unit   or   Name | Min | Max
        wide = max((len(r) for r in rows), default=0)
        cols = dict(min=1, max=3 if wide >= 4 else 2, unit=4 if wide >= 5 else None)
    parsed, problems = [], []
    for r in rows:
        if not r or not str(r[0]).strip():
            continue
        label = str(r[0]).strip()
        key = fe.norm(label)
        if key in fe.RATIO_ALIASES:
            code, per, scale = fe.RATIO_ALIASES[key]
        elif key in amap:
            code = amap[key]
            per, scale = nutrients[code].line_per, nutrients[code].line_scale
        else:
            problems.append(f'"{label}": not matched to a nutrient, so it was skipped.')
            continue
        notes = []
        lo, e1 = _num(r[cols['min']]) if cols['min'] < len(r) else (None, None)
        hi, e2 = _num(r[cols['max']]) if cols['max'] < len(r) else (None, None)
        if e1 or e2:
            problems.append(f'"{label}": {e1 or e2}.')
            continue
        unit = str(r[cols['unit']]).strip() if cols['unit'] is not None and cols['unit'] < len(r) else ''
        if lo == 0:
            lo = None
            notes.append('min 0 treated as no minimum')
        cap = _limit_cap(unit or nutrients[code].unit)
        if hi is not None and (hi == 0 or (cap is not None and hi >= cap)):
            notes.append(f'max {hi:g} treated as no maximum (placeholder)')
            hi = None
        parsed.append(dict(label=label, code=code, per=per, scale=scale, min=lo, max=hi,
                           required=lo is not None or hi is not None, enabled=True, notes=notes))
    if not parsed:
        return _err('No usable rows found. Paste cells with the nutrient name first, then Min, (Actual), Max, (Unit).', problems=problems)
    if commit:
        if not name or len(name) > 100:
            return _err('Give the profile a name before importing.')
        try:
            lines = [fe.clean_line(p, nutrients) for p in parsed]
        except ValueError as exc:
            return _err(str(exc))
        p = RequirementProfile.query.filter(db.func.lower(RequirementProfile.name) == name.lower()).first()
        if p is None:
            p = RequirementProfile(name=name, species=str(form.get('species') or 'other')[:30])
            db.session.add(p)
        p.source = f'Imported {datetime.utcnow():%Y-%m-%d} from {f.filename if f is not None and f.filename else "pasted table"}. Confirm against your own standard.'
        p.lines = [RequirementLine(nutrient_code=l['code'], per=l['per'], scale=l['scale'], min_value=l['min'], max_value=l['max'],
                                   tolerance=0.0, required=l['required'], enabled=True, sort=k) for k, l in enumerate(lines)]
        db.session.commit()
        return jsonify(status='success', committed=True, id=p.id, lines=len(lines), problems=problems)
    return jsonify(status='success', committed=False, lines=parsed, problems=problems)
