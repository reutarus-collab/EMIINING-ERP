"""Formulation Lab API: nutrient data, requirement profiles, Excel/paste import, optimise, verify, release gate."""
import difflib
import html
import io
import json
import math
import re
from datetime import datetime

from flask import Blueprint, jsonify, request

from routes.auth import roles_required
from services import formulation_engine as fe
from services.db import db
from services.models import (FeedIngredient, FormulaAlias, IngredientNutrient, IngredientNutrientHistory,
                             Nutrient, ProfileIngredientBound, RequirementLine, RequirementPreset,
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


@nutrition_bp.route('/api/nutrition/audit')
@roles_required(*LAB_ROLES)
def ingredient_audit():
    raw = _raw_ingredients()
    profiles = RequirementProfile.query.order_by(RequirementProfile.name).all()
    used = {}
    for p in profiles:
        for line in fe.lines_from_profile(p, fe.load_nutrients()):
            if line.get('enabled') and line.get('required') and (line.get('min') is not None or line.get('max') is not None):
                used.setdefault(line['code'], []).append(p.name)
            if line.get('per') not in ('asfed', 'dm'):
                used.setdefault(line['per'], []).append(p.name)
    ids = [i.id for i in raw]
    values = IngredientNutrient.query.filter(IngredientNutrient.ingredient_id.in_(ids)).all() if ids else []
    by_ing = {}
    for row in values:
        by_ing.setdefault(row.ingredient_id, {})[row.nutrient_code] = row
    costs = _costs(ids)
    positive = [float(costs.get(i.id, i.cost_per_kg or 0) or 0) for i in raw if float(costs.get(i.id, i.cost_per_kg or 0) or 0) > 0]
    median = sorted(positive)[len(positive)//2] if positive else None
    deviations = sorted(abs(x - median) for x in positive) if median is not None else []
    mad = deviations[len(deviations)//2] if deviations else 0
    rows = []
    for ing in raw:
        price = float(costs.get(ing.id, ing.cost_per_kg or 0) or 0)
        cells = by_ing.get(ing.id, {})
        missing = sorted({code for code in used if code not in cells})
        outlier = bool(price > 0 and median is not None and mad > 0 and abs(price - median) > 3 * mad)
        imported = [dict(code=code, value=row.value, source=row.source or '', date=row.updated_at.isoformat() if row.updated_at else None)
                    for code, row in cells.items() if (row.source_type or 'import') == 'import']
        rows.append(dict(ingredient_id=ing.id, ingredient=ing.name, price_per_kg=price,
            missing_price=price <= 0, possible_price_outlier=outlier, outlier_method='greater than 3 median absolute deviations across priced raw items' if outlier else None,
            missing_required_nutrients=[dict(code=c, profiles=sorted(set(used[c]))) for c in missing], imported_values=imported))
    return jsonify(status='success', read_only=True, profiles=[p.name for p in profiles], rows=rows)


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
        source = str(c.get('source') or '').strip()[:200]
        if value is not None and not source:
            return _err('Enter the local lab report or other source before saving a nutrient value.')
        parsed.append((iid, code, value, source or 'cleared by owner', 'local', True))
    _apply_values(parsed)
    db.session.commit()
    return jsonify(status='success', saved=len(parsed))


@nutrition_bp.route('/api/nutrition/ingredient-values/<int:ingredient_id>/<code>/history')
@roles_required(*LAB_ROLES)
def ingredient_value_history(ingredient_id, code):
    rows = IngredientNutrientHistory.query.filter_by(ingredient_id=ingredient_id, nutrient_code=code).order_by(
        IngredientNutrientHistory.recorded_at.desc()).all()
    active = IngredientNutrient.query.filter_by(ingredient_id=ingredient_id, nutrient_code=code).first()
    result = [dict(value=r.value, source=r.source, source_type=r.source_type, confirmed=r.confirmed,
                   date=r.recorded_at.isoformat() if r.recorded_at else None) for r in rows]
    if active:
        result.insert(0, dict(value=active.value, source=active.source, source_type=active.source_type,
                              confirmed=active.confirmed, date=active.updated_at.isoformat() if active.updated_at else None, active=True))
    return jsonify(ingredient_id=ingredient_id, code=code, values=result)


# Highest value the Item Master columns accept (matches set_item_nutrients in routes/po.py).
LEGACY_CAPS = {'CP': 100.0, 'ME': 10.0, 'CF': 100.0, 'CA': 100.0, 'AVP': 100.0, 'LYS_TOT': 100.0, 'MET_TOT': 100.0}


def _mirror_to_item_master(writes):
    """An import writes the nutrient matrix. The Item Master list shows the seven older columns on the item
    itself, so copy those seven across, or an imported value would exist but never appear there."""
    for iid, code, value, _src in writes:
        col = fe.LEGACY.get(code)
        if not col or value is None or not (0 <= value <= LEGACY_CAPS[code]):
            continue
        active = IngredientNutrient.query.filter_by(ingredient_id=iid, nutrient_code=code).first()
        if active and active.confirmed:
            continue
        item = db.session.get(FeedIngredient, iid)
        if item is not None:
            setattr(item, col, float(value))


def _apply_values(parsed, default_source='entered by hand'):
    for cell in parsed:
        iid, code, value, source = cell[:4]
        source_type = cell[4] if len(cell) > 4 else 'import'
        confirmed = bool(cell[5]) if len(cell) > 5 else False
        row = IngredientNutrient.query.filter_by(ingredient_id=iid, nutrient_code=code).first()
        if value is None:
            if row:
                db.session.add(IngredientNutrientHistory(ingredient_id=iid, nutrient_code=code, value=row.value,
                    source=row.source, source_type=row.source_type or 'legacy', confirmed=bool(row.confirmed)))
                db.session.delete(row)
            continue
        if row is not None:
            db.session.add(IngredientNutrientHistory(ingredient_id=iid, nutrient_code=code, value=row.value,
                source=row.source, source_type=row.source_type or 'import', confirmed=bool(row.confirmed)))
        # An import must never replace a confirmed local lab value.
        if row is not None and row.confirmed and source_type == 'import':
            continue
        if row is None:
            db.session.add(IngredientNutrient(ingredient_id=iid, nutrient_code=code, value=value,
                source=source or default_source, source_type=source_type, confirmed=confirmed))
        else:
            row.value, row.updated_at = value, datetime.utcnow()
            row.source = source or row.source or default_source
            row.source_type, row.confirmed = source_type, confirmed


# ----------------------------------------------------------------------------------------- profiles
def _profile_json(p, nutrients):
    return dict(id=p.id, name=p.name, species=p.species, stage=p.stage or '', source=p.source or '', notes=p.notes or '',
                lines=fe.lines_from_profile(p, nutrients), bounds=[dict(ingredient_id=b.ingredient_id, min=b.min_pct,
                    max=b.max_pct, source=b.source) for b in p.ingredient_bounds])


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
    bounds = []
    seen = set()
    for raw in d.get('bounds') or []:
        try:
            iid, lo, hi = int(raw['ingredient_id']), float(raw.get('min', 0) or 0), float(raw.get('max', 100))
            src = str(raw.get('source') or '').strip()[:300]
        except (TypeError, ValueError, KeyError, AttributeError):
            return _err('Ingredient caps require an ingredient, numeric limits, and a source.')
        if iid in seen or not 0 <= lo <= hi <= 100 or not src or iid not in {i.id for i in _raw_ingredients()}:
            return _err('Each ingredient cap needs a valid raw ingredient, 0–100% limits, and a source.')
        seen.add(iid); bounds.append(ProfileIngredientBound(ingredient_id=iid, min_pct=lo, max_pct=hi, source=src))
    p.lines = [RequirementLine(nutrient_code=l['code'], per=l['per'], scale=l['scale'], min_value=l['min'], max_value=l['max'],
                               tolerance=l['tolerance'], required=l['required'], enabled=l['enabled'], sort=k)
               for k, l in enumerate(lines)]
    p.ingredient_bounds = bounds
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


@nutrition_bp.route('/api/nutrition/presets', methods=['GET', 'POST'])
@roles_required(*EDIT_ROLES)
def requirement_presets():
    if request.method == 'GET':
        return jsonify([dict(id=p.id, name=p.name, genotype=p.genotype, stage=p.stage,
            age_min_days=p.age_min_days, age_max_days=p.age_max_days, sid_lys_g_mcal=p.sid_lys_g_mcal,
            ideal_aa=json.loads(p.ideal_aa_json or '{}'), me_min_mcal=p.me_min_mcal,
            me_max_mcal=p.me_max_mcal, source=p.source) for p in RequirementPreset.query.order_by(RequirementPreset.name)])
    d = request.get_json(silent=True) or {}
    try:
        name, genotype, stage = [str(d.get(k) or '').strip() for k in ('name', 'genotype', 'stage')]
        source = str(d.get('source') or '').strip()[:300]
        aa = d.get('ideal_aa') or {}
        if not name or not genotype or not stage or not source or not isinstance(aa, dict):
            raise ValueError('Preset name, genotype, stage, ideal amino-acid ratios and source are required.')
        lys_ratio = float(d['sid_lys_g_mcal'])
        me_min = float(d['me_min_mcal']); me_max = float(d['me_max_mcal'])
        if not all(math.isfinite(x) and x > 0 for x in (lys_ratio, me_min, me_max)) or me_min > me_max:
            raise ValueError('Enter a positive SID Lys g/Mcal target and valid ME min/max values.')
        checked = {}
        for code, value in aa.items():
            code = str(code).upper()
            if not code.startswith('SID_') or code == 'SID_LYS' or code not in fe.load_nutrients():
                raise ValueError(f'{code}: choose a valid SID amino acid other than SID_LYS.')
            value = float(value)
            if not math.isfinite(value) or value <= 0 or value > 200:
                raise ValueError(f'{code}: ideal-protein ratios must be positive percentages.')
            checked[code] = value
        if not checked:
            raise ValueError('Enter at least one sourced SID amino-acid ratio.')
        lo_age = int(d['age_min_days']) if d.get('age_min_days') not in (None, '') else None
        hi_age = int(d['age_max_days']) if d.get('age_max_days') not in (None, '') else None
        if lo_age is not None and lo_age < 0 or hi_age is not None and hi_age < 0 or (lo_age is not None and hi_age is not None and lo_age > hi_age):
            raise ValueError('Age range must be non-negative and the minimum cannot exceed the maximum.')
    except (ValueError, TypeError, KeyError) as exc:
        return _err(str(exc))
    try:
        pid = int(d['id']) if d.get('id') else None
    except (TypeError, ValueError):
        return _err('Invalid preset id.')
    p = db.session.get(RequirementPreset, pid) if pid else RequirementPreset()
    if RequirementPreset.query.filter(db.func.lower(RequirementPreset.name) == name.lower(),
        RequirementPreset.id != (p.id if p.id else -1)).first():
        return _err('Another preset already uses that name.', 409)
    p.name, p.genotype, p.stage, p.source = name[:100], genotype[:100], stage[:100], source
    p.age_min_days, p.age_max_days = lo_age, hi_age
    p.sid_lys_g_mcal, p.ideal_aa_json = lys_ratio, json.dumps(checked, sort_keys=True)
    p.me_min_mcal, p.me_max_mcal, p.updated_at = me_min, me_max, datetime.utcnow()
    db.session.add(p); db.session.commit()
    return jsonify(status='success', id=p.id)


@nutrition_bp.route('/api/nutrition/profiles/generate', methods=['POST'])
@roles_required(*EDIT_ROLES)
def generate_requirement_profile():
    d = request.get_json(silent=True) or {}
    try:
        preset = db.session.get(RequirementPreset, int(d.get('preset_id') or 0))
    except (TypeError, ValueError):
        preset = None
    if preset is None:
        return _err('Choose an owner-entered preset.')
    try:
        me = float(d.get('target_me_mcal'))
        age = int(d['age_days']) if d.get('age_days') not in (None, '') else None
        name = str(d.get('name') or '').strip()
        if not name or not math.isfinite(me) or me <= 0:
            raise ValueError('Give the generated profile a name and a positive target ME value.')
        if me < preset.me_min_mcal or me > preset.me_max_mcal:
            raise ValueError('Target ME must fall within this preset’s saved ME min/max window.')
        if age is not None and age < 0:
            raise ValueError('Age in days cannot be negative.')
        if age is not None and ((preset.age_min_days is not None and age < preset.age_min_days) or
                                (preset.age_max_days is not None and age > preset.age_max_days)):
            raise ValueError('Age falls outside the saved preset age range.')
    except (ValueError, TypeError) as exc:
        return _err(str(exc))
    lys_pct = float(preset.sid_lys_g_mcal) * me / 10.0
    lines = [dict(code='ME', per='asfed', scale=1, min=preset.me_min_mcal, max=preset.me_max_mcal,
                  tolerance=0, required=True, enabled=True),
             dict(code='SID_LYS', per='asfed', scale=1, min=lys_pct, max=None,
                  tolerance=0, required=True, enabled=True)]
    for code, ratio in json.loads(preset.ideal_aa_json or '{}').items():
        lines.append(dict(code=code, per='SID_LYS', scale=100, min=float(ratio), max=None,
                          tolerance=0, required=True, enabled=True))
    nutrients = fe.load_nutrients()
    clean = _clean_lines(lines, nutrients)
    if RequirementProfile.query.filter(db.func.lower(RequirementProfile.name) == name.lower()).first():
        return _err('A requirement profile already uses that name.', 409)
    p = RequirementProfile(name=name[:100], species='poultry', stage=f'{preset.stage} · {age} d' if age is not None else preset.stage,
        source=f'Generated from owner preset: {preset.name}; {preset.source}', notes=f'Target ME: {me:g} Mcal/kg; generated profile is editable and not released.')
    p.lines = [RequirementLine(nutrient_code=l['code'], per=l['per'], scale=l['scale'], min_value=l['min'], max_value=l['max'],
        tolerance=l['tolerance'], required=True, enabled=True, sort=i) for i, l in enumerate(clean)]
    db.session.add(p); db.session.commit()
    return jsonify(status='success', id=p.id, sid_lys_pct=lys_pct, released=False)


# ----------------------------------------------------------------------------------------- calculation
def _prep(ids, lines):
    nutrients = fe.load_nutrients()
    table, _ = fe.build_table(ids, nutrients, fe.needed_codes(lines, nutrients))
    return nutrients, table, _info(ids)


def _saved_bounds(profile):
    return {b.ingredient_id: {'min': float(b.min_pct), 'max': float(b.max_pct)} for b in profile.ingredient_bounds} if profile else {}


def _merge_bounds(saved, supplied):
    out = {i: dict(v) for i, v in (saved or {}).items()}
    for iid, val in (supplied or {}).items():
        base = out.get(iid, {'min': 0.0, 'max': 100.0})
        out[iid] = {'min': max(base['min'], val['min']), 'max': min(base['max'], val['max'])}
    return out


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
    profile = db.session.get(RequirementProfile, int(d.get('profile_id'))) if d.get('profile_id') else None
    bounds = _merge_bounds(_saved_bounds(profile), bounds)
    nutrients, table, info = _prep(ids, lines)
    res = fe.verify(recipe, lines, table, info, bounds)
    res['warnings'] += fe.rdp_rup_warnings(ids, nutrients, {i: info[i]['name'] for i in info})
    res['data_hash'] = fe.formula_data_hash(recipe, lines, table, bounds)
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
    profile = db.session.get(RequirementProfile, int(d.get('profile_id'))) if d.get('profile_id') else None
    bounds = _merge_bounds(_saved_bounds(profile), bounds)
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
        return dict(controlled=False, ok=False, reasons=['This formula has no saved requirement profile and cannot be released for production.'])
    prof = db.session.get(RequirementProfile, fm.profile_id)
    if prof is None:
        return dict(controlled=True, ok=False, reasons=['The requirement profile this formula was checked against no longer exists.'])
    nutrients = fe.load_nutrients()
    lines = fe.lines_from_profile(prof, nutrients)
    recipe = {l.ingredient_id: float(l.pct) for l in fm.lines}
    nutrients, table, info = _prep(list(recipe), lines)
    bounds = _saved_bounds(prof)
    res = fe.verify(recipe, lines, table, info, bounds)
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


def _read_rows(file_storage, text, preferred_sheet=None):
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
            if preferred_sheet:
                wanted = fe.norm(preferred_sheet)
                # Nutrition workbooks often have a cover sheet first. Prefer the
                # explicitly named data sheet (for example WUFFDA's Ingredients)
                # instead of silently importing the cover page.
                exact = next((sheet for sheet in wb.worksheets
                              if fe.norm(sheet.title) == wanted), None)
                starts = next((sheet for sheet in wb.worksheets
                               if fe.norm(sheet.title).startswith(wanted + ' ')), None)
                ws = exact or starts or ws
            return [[_cell(c) for c in row] for row in ws.iter_rows(values_only=True)][:500]
        text = blob.decode('utf-8-sig', errors='replace')
    text = text or ''
    if len(text) > MAX_UPLOAD:
        raise ValueError('That is too much text to import at once.')
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        raise ValueError('Nothing to import. Copy the cells from Excel and paste them in.')
    def markdown_separator(line):
        cells = line.strip().strip('|').split('|')
        return len(cells) > 1 and all(re.fullmatch(r'\s*:?-{2,}:?\s*', c) for c in cells)
    lines = [l for l in lines if not markdown_separator(l)]
    pipe_table = any('|' in line for line in lines[:5])
    delim = '|' if pipe_table else ('\t' if any('\t' in l for l in lines[:5]) else (';' if lines[0].count(';') > lines[0].count(',') else ','))
    import csv
    parsed = []
    for row in csv.reader(lines, delimiter=delim):
        cells = [html.unescape(c).strip() for c in row]
        if pipe_table and cells:
            if not cells[0]:
                cells = cells[1:]
            if cells and not cells[-1]:
                cells = cells[:-1]
        if any(c for c in cells):
            parsed.append(cells)
    return parsed[:500]


def _header_norm(value):
    """Normalize labels copied from rich/Markdown tables, including <br> and unit lines."""
    value = html.unescape(str(value or ''))
    value = re.sub(r'<\s*br\s*/?\s*>', ' ', value, flags=re.I)
    value = re.sub(r'<[^>]+>', ' ', value)
    return fe.norm(value)


def _sid_energy_ratio_code(label, nutrients):
    """Recognize labels such as "g SID Lys/Mcal" or "digestible Lys:ME ratio"."""
    key = _header_norm(label)
    if 'total' in key.split():
        return None
    if not any(token in key.split() for token in ('mcal', 'me', 'energy', 'metabolizable', 'metabolic')):
        return None
    names = {
        'lys': 'SID_LYS', 'lysine': 'SID_LYS', 'met': 'SID_MET', 'methionine': 'SID_MET',
        'mc': 'SID_MC', 'tsaa': 'SID_MC', 'cys': 'SID_CYS', 'cystine': 'SID_CYS',
        'thr': 'SID_THR', 'threonine': 'SID_THR', 'trp': 'SID_TRP', 'tryptophan': 'SID_TRP',
        'arg': 'SID_ARG', 'arginine': 'SID_ARG', 'ile': 'SID_ILE', 'isoleucine': 'SID_ILE',
        'val': 'SID_VAL', 'valine': 'SID_VAL', 'leu': 'SID_LEU', 'leucine': 'SID_LEU',
        'his': 'SID_HIS', 'histidine': 'SID_HIS', 'phe': 'SID_PHE', 'phenylalanine': 'SID_PHE',
        'gly': 'SID_GLY', 'glycine': 'SID_GLY', 'ser': 'SID_SER', 'serine': 'SID_SER',
        'tyr': 'SID_TYR', 'tyrosine': 'SID_TYR', 'taaa': 'SID_TAAA', 'glyser': 'SID_GLYSER',
    }
    words = set(key.split())
    if {'gly', 'ser'} <= words and 'SID_GLYSER' in nutrients:
        return 'SID_GLYSER'
    if {'phe', 'tyr'} <= words and 'SID_TAAA' in nutrients:
        return 'SID_TAAA'
    if {'met', 'cys'} <= words and 'SID_MC' in nutrients:
        return 'SID_MC'
    if {'methionine', 'cystine'} <= words and 'SID_MC' in nutrients:
        return 'SID_MC'
    if {'methionine', 'cysteine'} <= words and 'SID_MC' in nutrients:
        return 'SID_MC'
    for word, code in names.items():
        if word in words and code in nutrients:
            return code
    return None


def _num(s):
    s = str(s).strip().replace('%', '').replace(' ', '').replace('\u00a0', '')
    if s == '' or s.lower() in ('-', '--', '\u2014', 'n/a', 'na', '#n/a', 'nd', 'n.d.', 'null'):
        return None, None
    if re.fullmatch(r'-?\d{1,3}(,\d{3})+(\.\d+)?', s):      # 3,350 or 1,100.5 = thousands separator (Excel display)
        s = s.replace(',', '')
    elif re.fullmatch(r'-?\d+,\d+', s):                        # 0,45 = decimal comma
        s = s.replace(',', '.')
    try:
        v = float(s)
    except ValueError:
        return None, f'"{s}" is not a number'
    if not math.isfinite(v):
        return None, f'"{s}" is not a number'
    return v, None


# WUFFDA's bare amino-acid headings are treated as SID values per the Lab import convention.
# Explicitly labelled total amino acids still use the *_TOT nutrient codes.
WUFFDA_HEADERS = {
    'm e': 'ME_AUTO', 'e e': 'FAT', 'c f': 'CF', 'c18 2': 'LA', 'protein': 'CP', 'dry matter': 'DM',
    'total phos': 'TP', 'avail phos': 'AVP',
    'arg': 'SID_ARG', 'gly': 'SID_GLY', 'ser': 'SID_SER', 'gly ser': 'SID_GLYSER', 'his': 'SID_HIS', 'ile': 'SID_ILE',
    'leu': 'SID_LEU', 'lys': 'SID_LYS', 'met': 'SID_MET', 'cys': 'SID_CYS', 'tsaa': 'SID_MC', 'phe': 'SID_PHE',
    'tyr': 'SID_TYR', 'taaa': 'SID_TAAA', 'thr': 'SID_THR', 'trp': 'SID_TRP', 'val': 'SID_VAL',
}
SKIP_HEADERS = {'ca p': 'Ca:P is calculated by the Lab, not stored', 'weight': 'Weight', 'min': 'Min', 'max': 'Max'}


def _importable_ingredients():
    """Everything that can go in a formula: raw materials, minerals and additives. Not finished feed, services, packaging."""
    skip = ('finished', 'service', 'milling', 'packaging')
    return [i for i in FeedIngredient.query.order_by(FeedIngredient.name).all()
            if not any((i.category or '').lower().startswith(k) for k in skip)]


def _name_column(rows, hdr_i):
    """Find the ingredient-name column, including sheets with leading blank columns."""
    body = [r for r in rows[hdr_i + 1:] if r and any(str(c).strip() for c in r)][:25]
    if not body:
        return 0
    def numeric(c):
        return _num(c)[0] is not None
    width = min(max((len(r) for r in body), default=1), 6)
    candidates = []
    for col in range(width):
        values = [str(r[col]).strip() for r in body if col < len(r) and str(r[col]).strip()]
        labels = [v for v in values if not numeric(v)]
        distinct = {fe.norm(v) for v in labels if fe.norm(v)}
        candidates.append((len(distinct), len(labels), -col, col))
    best = max(candidates, default=(0, 0, 0, 0))
    return best[3] if best[0] else 0


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
        return sum(1 for c in cells if _header_norm(c) in amap)
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
        rows = _read_rows(f, None if f is not None else form.get('text'), preferred_sheet='Ingredients')
    except ValueError as exc:
        return _err(str(exc))
    nutrients = fe.load_nutrients()
    amap = fe.alias_map(nutrients)
    rows, flipped = _transposed_if_needed(rows, amap)
    for k, code in WUFFDA_HEADERS.items():
        if code == 'ME_AUTO' or code in nutrients:
            amap.setdefault(k, code)
    # Bare names (including full nutrient names) default to SID. A source label
    # explicitly containing "total" remains mapped to its *_TOT nutrient.
    for label, code in {
        'arginine': 'SID_ARG', 'glycine': 'SID_GLY', 'serine': 'SID_SER', 'glycine serine': 'SID_GLYSER',
        'histidine': 'SID_HIS', 'isoleucine': 'SID_ILE', 'leucine': 'SID_LEU', 'lysine': 'SID_LYS',
        'methionine': 'SID_MET', 'cystine': 'SID_CYS', 'cysteine': 'SID_CYS', 'methionine cystine': 'SID_MC',
        'methionine cysteine': 'SID_MC', 'phenylalanine': 'SID_PHE', 'tyrosine': 'SID_TYR',
        'phenylalanine tyrosine': 'SID_TAAA', 'threonine': 'SID_THR', 'tryptophan': 'SID_TRP', 'valine': 'SID_VAL',
    }.items():
        if code in nutrients:
            amap[label] = code
    amap.setdefault('ca p', 'RATIO')
    hdr_i = next((i for i, r in enumerate(rows[:20]) if sum(1 for c in r[1:] if _header_norm(c) in amap or _header_norm(c) in SKIP_HEADERS) >= 2), None)
    if hdr_i is None:
        return _err('Could not find a header row. Row 1 should have the ingredient column first, then nutrient names such as CP, Ca, SID Lys.')
    header = rows[hdr_i]
    name_col = _name_column(rows, hdr_i)
    create_missing = str(form.get('create_missing', '')).lower() in ('1', 'true', 'yes')
    apply_max = str(form.get('apply_max', '')).lower() in ('1', 'true', 'yes')
    new_category = str(form.get('new_category') or 'Raw - Other').strip()[:50] or 'Raw - Other'
    if '<' in new_category or '>' in new_category:
        return _err('Invalid category.')
    colmap, unknown, ignored, source_col, max_col, cost_col = {}, [], [], None, None, None
    total_aa_headers = {'lys': 'LYS_TOT', 'lysine': 'LYS_TOT', 'met': 'MET_TOT', 'methionine': 'MET_TOT',
                        'arg': 'ARG_TOT', 'arginine': 'ARG_TOT', 'gly': 'GLY_TOT', 'glycine': 'GLY_TOT',
                        'ser': 'SER_TOT', 'serine': 'SER_TOT', 'gly ser': 'GLYSER_TOT',
                        'his': 'HIS_TOT', 'histidine': 'HIS_TOT', 'ile': 'ILE_TOT', 'isoleucine': 'ILE_TOT',
                        'leu': 'LEU_TOT', 'leucine': 'LEU_TOT', 'cys': 'CYS_TOT', 'cystine': 'CYS_TOT',
                        'cysteine': 'CYS_TOT', 'tsaa': 'TSAA_TOT', 'phe': 'PHE_TOT', 'phenylalanine': 'PHE_TOT',
                        'tyr': 'TYR_TOT', 'tyrosine': 'TYR_TOT', 'taaa': 'TAAA_TOT', 'thr': 'THR_TOT',
                        'threonine': 'THR_TOT', 'trp': 'TRP_TOT', 'tryptophan': 'TRP_TOT', 'val': 'VAL_TOT', 'valine': 'VAL_TOT'}
    for j, h in enumerate(header):
        if j == name_col or not h:
            continue
        n = _header_norm(h)
        if n in ('source', 'reference', 'notes', 'note'):
            source_col = j if n in ('source', 'reference') else source_col
        elif n == 'max':
            max_col = j
        elif n in ('cost', 'price'):
            cost_col = j
        elif n in ('unit', 'units', 'kes', 'ksh', 'min', 'weight'):
            ignored.append(h)
        elif n in amap and amap[n] == 'RATIO':
            ignored.append(h + ' (calculated)')
        elif re.search(r'\btotal\b', str(h), re.I) and n in total_aa_headers and total_aa_headers[n] in nutrients:
            colmap[j] = total_aa_headers[n]
        elif n in amap:
            colmap[j] = amap[n]
        else:
            unknown.append(h)
    hints = [f'"{u}": not matched. Bare amino-acid headings are treated as SID; label a total value "Total Lys"/"Total Met" (or add a nutrient).'
             if fe.norm(u) in ('lysine', 'lys', 'methionine', 'met', 'threonine', 'thr', 'tryptophan', 'trp', 'cysteine', 'cystine', 'arginine', 'valine', 'isoleucine')
             else f'"{u}": not matched to a nutrient. Add it under Nutrients first, or rename the column.' for u in unknown]
    raw_ings = _importable_ingredients()
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
    created = max_updated = 0
    for ridx, r in enumerate(rows[hdr_i + 1:], start=hdr_i + 2):
        if not r or name_col >= len(r) or not str(r[name_col]).strip():
            continue
        label_name = str(r[name_col]).strip()
        iid, fuzzy = _match_ingredient(label_name, by_norm, aliases, raw_ids)
        ov = overrides.get(str(ridx))
        if ov is not None and int(ov) in raw_ids:
            iid, fuzzy = int(ov), False
        cells, row_err = [], []
        for j, code in colmap.items():
            v, e = _num(r[j]) if j < len(r) else (None, None)
            if e:
                row_err.append(f'{nutrients.get(code).name if code in nutrients else code}: {e}')
                continue
            if v is None:
                continue
            if code == 'ME_AUTO':                          # WUFFDA M.E. is kcal/kg for poultry/pigs; small numbers are Mcal
                code = 'ME_KCAL' if v > 20 else 'ME'
                if code not in nutrients:
                    continue
            if v < 0 or v > 1e6:
                row_err.append(f'{nutrients[code].name}: {v} is out of range')
            else:
                cells.append(dict(code=code, value=v))
        errors += len(row_err)
        max_pct = cost = None
        if max_col is not None and max_col < len(r):
            mv, _e = _num(r[max_col])
            if mv is not None and 0 < mv <= 100:
                max_pct = mv
        if cost_col is not None and cost_col < len(r):
            cv, _e = _num(r[cost_col])
            if cv is not None and cv > 0:
                cost = cv
        status = 'ok' if iid and not fuzzy else ('confirm' if iid else ('new' if create_missing else 'no_match'))
        out_rows.append(dict(row=ridx, name=label_name, ingredient_id=iid, ingredient=names.get(iid), status=status,
                             errors=row_err, cells=cells, suggested=fuzzy, max_pct=max_pct, cost=cost))
        if commit and (status in ('ok', 'new')):
            if status == 'new':
                item = FeedIngredient(name=label_name[:100], category=new_category, cost_per_kg=cost or 0.0,
                                      purchase_uom='KG', stock_uom='KG', conversion_type='FIXED', conversion_factor=1.0)
                db.session.add(item)
                db.session.flush()
                iid = item.id
                names[iid] = item.name
                by_norm[fe.norm(item.name)] = iid
                raw_ids.add(iid)
                created += 1
            if apply_max and max_pct is not None:
                ing = db.session.get(FeedIngredient, iid)
                if ing is not None:
                    ing.max_inclusion_pct = max_pct
                    max_updated += 1
            src = (str(r[source_col]).strip() if source_col is not None and source_col < len(r) and str(r[source_col]).strip()
                   else f'import {label} {today}')
            writes += [(iid, c['code'], c['value'], src) for c in cells]
    if not out_rows:
        return _err('No ingredient rows were found on the Ingredients worksheet. Check that it has an ingredient-name column and nutrient headers.')
    if commit:
        _apply_values(writes)
        _mirror_to_item_master(writes)
        db.session.commit()
    return jsonify(status='success', committed=commit, written=len(writes), transposed=flipped, rows=out_rows,
                   created=created, max_updated=max_updated,
                   columns=sorted({nutrients[c].name if c in nutrients else ('ME' if c == 'ME_AUTO' else c) for c in colmap.values()}),
                   unknown_columns=hints, ignored_columns=ignored,
                   skipped_rows=[r['row'] for r in out_rows if r['status'] not in ('ok', 'new')], error_count=errors,
                   note='Blank cells are left alone: they never overwrite a value and are never treated as zero.')


@nutrition_bp.route('/api/nutrition/ingredients/<int:ingredient_id>/values', methods=['DELETE'])
@roles_required('admin')
def clear_ingredient_values(ingredient_id):
    """Clear an ingredient's nutrient profile without deleting stock or sales history."""
    ingredient = db.session.get(FeedIngredient, ingredient_id)
    if not ingredient:
        return _err('Ingredient not found.', 404)
    active_values = IngredientNutrient.query.filter_by(ingredient_id=ingredient_id).all()
    for row in active_values:
        db.session.add(IngredientNutrientHistory(ingredient_id=row.ingredient_id, nutrient_code=row.nutrient_code,
            value=row.value, source=row.source, source_type=row.source_type or 'legacy', confirmed=bool(row.confirmed)))
    cleared = len(active_values)
    for row in active_values:
        db.session.delete(row)
    # Clear the legacy Item Master mirrors too; otherwise stale values keep feeding
    # older formulation screens after the matrix row has been removed.
    for field in set(fe.LEGACY.values()):
        setattr(ingredient, field, 0.0)
    db.session.commit()
    return jsonify(status='success', cleared=cleared)


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
        rows = _read_rows(f, None if f is not None else form.get('text'), preferred_sheet='Nutrients')
    except ValueError as exc:
        return _err(str(exc))
    nutrients = fe.load_nutrients()
    amap = fe.alias_map(nutrients)
    for label, code in WUFFDA_HEADERS.items():
        if code in nutrients:
            amap.setdefault(label, code)
    cols = None
    label_col = 0
    wuffda_specs = []
    for header_i, r in enumerate(rows[:30]):
        ns = [_header_norm(c) for c in r]
        # WUFFDA Nutrients sheet: B is the nutrient, C/D are the current
        # specification Min/Max, E is Units, then saved specifications use
        # named Min/Max pairs (F/G, H/I, ...).
        if len(ns) > 4 and ns[2:5] == ['min', 'max', 'units']:
            labels = rows[header_i - 1] if header_i else []
            current_name = str(labels[2]).strip() if len(labels) > 2 and str(labels[2]).strip() else 'Current Specification'
            wuffda_specs.append(dict(key='current', name=f'Current - {current_name}', min=2, max=3, unit=4))
            for start in range(5, len(r) - 1, 2):
                spec_name = str(labels[start]).strip() if start < len(labels) else ''
                if spec_name and ns[start:start + 2] == ['min', 'max']:
                    wuffda_specs.append(dict(key=str(start), name=spec_name, min=start, max=start + 1, unit=4))
            selected_spec = str(form.get('wuffda_spec') or 'current')
            spec = next((item for item in wuffda_specs if item['key'] == selected_spec), wuffda_specs[0])
            cols = dict(min=spec['min'], max=spec['max'], unit=spec['unit'])
            label_col = 1
            rows = rows[header_i + 1:]
            break
        if 'min' in ns and 'max' in ns:
            label_col = ns.index('nutrient') if 'nutrient' in ns else 0
            min_cols = [i for i, value in enumerate(ns) if value == 'min' and (i > label_col or 'nutrient' not in ns)]
            max_cols = [i for i, value in enumerate(ns) if value == 'max' and (i > label_col or 'nutrient' not in ns)]
            if not min_cols or not max_cols:
                continue
            unit_cols = [i for i, value in enumerate(ns) if value in ('unit', 'units') and i > label_col]
            cols = dict(min=min_cols[-1] if 'nutrient' in ns else min_cols[0],
                        max=max_cols[-1] if 'nutrient' in ns else max_cols[0],
                        unit=unit_cols[0] if unit_cols else next((ns.index(label) for label in ('unit', 'units') if label in ns), None))
            rows = rows[header_i + 1:]
            break
    if cols is None:                       # headerless paste: Name | Min | Actual | Max | Unit   or   Name | Min | Max
        wide = max((len(r) for r in rows), default=0)
        cols = dict(min=1, max=3 if wide >= 4 else 2, unit=4 if wide >= 5 else None)
    sid_requirement_codes = {
        'arginine': 'SID_ARG', 'arg': 'SID_ARG', 'glycine': 'SID_GLY', 'gly': 'SID_GLY', 'serine': 'SID_SER', 'ser': 'SID_SER',
        'histidine': 'SID_HIS', 'his': 'SID_HIS', 'isoleucine': 'SID_ILE', 'ile': 'SID_ILE', 'leucine': 'SID_LEU', 'leu': 'SID_LEU',
        'lysine': 'SID_LYS', 'lys': 'SID_LYS', 'methionine': 'SID_MET', 'met': 'SID_MET', 'cystine': 'SID_CYS', 'cysteine': 'SID_CYS',
        'methionine cystine': 'SID_MC', 'methionine cysteine': 'SID_MC', 'tsaa': 'SID_MC',
        'phenylalanine': 'SID_PHE', 'phe': 'SID_PHE', 'tyrosine': 'SID_TYR', 'tyr': 'SID_TYR',
        'threonine': 'SID_THR', 'thr': 'SID_THR', 'tryptophan': 'SID_TRP', 'trp': 'SID_TRP', 'valine': 'SID_VAL', 'val': 'SID_VAL',
    }
    total_requirement_codes = {'arginine': 'ARG_TOT', 'arg': 'ARG_TOT', 'glycine': 'GLY_TOT', 'gly': 'GLY_TOT',
                               'serine': 'SER_TOT', 'ser': 'SER_TOT', 'histidine': 'HIS_TOT', 'his': 'HIS_TOT',
                               'isoleucine': 'ILE_TOT', 'ile': 'ILE_TOT', 'leucine': 'LEU_TOT', 'leu': 'LEU_TOT',
                               'lysine': 'LYS_TOT', 'lys': 'LYS_TOT', 'methionine': 'MET_TOT', 'met': 'MET_TOT',
                               'cystine': 'CYS_TOT', 'cysteine': 'CYS_TOT', 'tsaa': 'TSAA_TOT',
                               'phenylalanine': 'PHE_TOT', 'phe': 'PHE_TOT', 'tyrosine': 'TYR_TOT', 'tyr': 'TYR_TOT',
                               'threonine': 'THR_TOT', 'thr': 'THR_TOT', 'tryptophan': 'TRP_TOT', 'trp': 'TRP_TOT',
                               'valine': 'VAL_TOT', 'val': 'VAL_TOT'}
    parsed, problems = [], []
    for r in rows:
        if not r or label_col >= len(r) or not str(r[label_col]).strip():
            continue
        label = str(r[label_col]).strip()
        key = _header_norm(label)
        # Explicit total annotations take precedence; bare amino-acid labels are SID.
        explicit_total = bool(re.search(r'\btotal\b', label, re.I))
        sid_code = sid_requirement_codes.get(key) if not explicit_total else None
        total_code = total_requirement_codes.get(key) if explicit_total else None
        energy_ratio_code = _sid_energy_ratio_code(label, nutrients)
        if key in fe.RATIO_ALIASES:
            code, per, scale = fe.RATIO_ALIASES[key]
        elif energy_ratio_code:
            code, per, scale = energy_ratio_code, 'ME', 10.0
            if code not in nutrients or per not in nutrients:
                problems.append(f'"{label}": SID amino acid or ME nutrient is missing, so the ratio was skipped.')
                continue
        elif sid_code and sid_code in nutrients:
            code = sid_code
            per, scale = nutrients[code].line_per, nutrients[code].line_scale
        elif total_code and total_code in nutrients:
            code = total_code
            per, scale = nutrients[code].line_per, nutrients[code].line_scale
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
        return _err('No usable rows found. Upload the WUFFDA workbook with its Nutrients sheet, or paste a table with Nutrient, Min, (Actual), Max, (Unit).', problems=problems)
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
        source_name = f.filename if f is not None and f.filename else 'pasted table'
        if wuffda_specs:
            source_name += f' ({spec["name"]})'
        p.source = f'Imported {datetime.utcnow():%Y-%m-%d} from {source_name}. Confirm against your own standard.'
        p.lines = [RequirementLine(nutrient_code=l['code'], per=l['per'], scale=l['scale'], min_value=l['min'], max_value=l['max'],
                                   tolerance=0.0, required=l['required'], enabled=True, sort=k) for k, l in enumerate(lines)]
        db.session.commit()
        return jsonify(status='success', committed=True, id=p.id, lines=len(lines), problems=problems)
    return jsonify(status='success', committed=False, lines=parsed, problems=problems,
                   wuffda_specs=[{'key': item['key'], 'name': item['name']} for item in wuffda_specs],
                   selected_wuffda_spec=spec['key'] if wuffda_specs else '')
