"""Feed formulation engine: manual evaluation, least-cost optimisation and independent verification.

Design rules
- Missing nutrient data is UNKNOWN, never zero. Anything that needs it is reported as missing and blocks release.
- A requirement line is value = scale * N / D where
    N = sum(x_i * amount_i[nutrient])            (amount per kg of feed, as fed)
    D = 1                  when per = 'asfed'
        sum(x_i * DM_i)    when per = 'dm'       (so "Mcal per kg DM" works)
        sum(x_i * amount_i[other nutrient])      (ratios: Ca:P, RDP as % of CP, lysine as % of protein ...)
  Min/max on such a ratio is still linear:  sum x_i (scale*a_i - limit*d_i) >= 0  (or <= 0), so the LP stays exact.
- evaluate()/verify() do NOT use the LP. They recompute every line from the raw table, so a solver
  or linearisation bug cannot slip through as "optimal".
"""
import hashlib
import json
import math
import re
from datetime import datetime

import numpy as np
from scipy.optimize import linprog

from services.db import db
from services.models import (FeedIngredient, IngredientNutrient, Nutrient, RequirementLine, RequirementProfile)

EPS = 1e-9


def _n(code, name, unit, basis, group, aliases=(), per='asfed', scale=1.0):
    return dict(code=code, name=name, unit=unit, basis=basis, group=group, aliases=list(aliases), per=per, scale=scale)


# Names and units only. No requirement or composition numbers are shipped: those come from your lab reports,
# your Excel sheets or a published table that you choose and cite in the "source" column.
NUTRIENT_SEED = [
    _n('DM', 'Dry matter', '%', 'asfed', 'basic', ['dry matter', 'dm']),
    _n('CP', 'Crude protein', '%', 'asfed', 'basic', ['crude protein', 'cp', 'protein']),
    _n('CF', 'Crude fibre', '%', 'asfed', 'basic', ['crude fibre', 'crude fiber', 'cf', 'fibre', 'fiber']),
    _n('FAT', 'Crude fat', '%', 'asfed', 'basic', ['crude fat', 'fat', 'ether extract', 'ee']),
    _n('NDF', 'NDF', '%', 'asfed', 'fibre', ['ndf']),
    _n('ADF', 'ADF', '%', 'asfed', 'fibre', ['adf']),
    _n('ME', 'ME', 'Mcal/kg', 'asfed', 'energy', ['me', 'me mcal kg', 'me mcal']),
    _n('ME_KCAL', 'ME', 'kcal/kg', 'asfed', 'energy', ['me kcal', 'me kcal kg']),
    _n('NEL', 'NEL', 'Mcal/kg DM', 'dm', 'energy', ['nel', 'nel mcal kg dm', 'nel mcal kg'], per='dm'),
    _n('RDP', 'RDP', '% of CP', 'pct_cp', 'protein', ['rdp'], per='CP', scale=100.0),
    _n('RUP', 'RUP', '% of CP', 'pct_cp', 'protein', ['rup'], per='CP', scale=100.0),
    _n('CA', 'Calcium', '%', 'asfed', 'mineral', ['calcium', 'ca']),
    _n('TP', 'Total phosphorus', '%', 'asfed', 'mineral', ['total phosphorus', 'total phos', 'total p', 'tp']),
    _n('AVP', 'Available phosphorus', '%', 'asfed', 'mineral',
       ['available phosphorus', 'avail phos', 'available p', 'avail p', 'avp', 'av p']),
    _n('NA', 'Sodium', '%', 'asfed', 'mineral', ['sodium', 'na']),
    _n('K', 'Potassium', '%', 'asfed', 'mineral', ['potassium', 'k']),
    _n('CL', 'Chloride', '%', 'asfed', 'mineral', ['chloride', 'cl']),
    _n('MG', 'Magnesium', '%', 'asfed', 'mineral', ['magnesium', 'mg']),
    _n('S', 'Sulphur', '%', 'asfed', 'mineral', ['sulphur', 'sulfur', 's']),
    _n('SALT', 'Salt', '%', 'asfed', 'mineral', ['salt', 'nacl']),
    _n('MN', 'Manganese', 'mg/kg', 'asfed', 'trace', ['manganese', 'mn']),
    _n('ZN', 'Zinc', 'mg/kg', 'asfed', 'trace', ['zinc', 'zn']),
    _n('CU', 'Copper', 'mg/kg', 'asfed', 'trace', ['copper', 'cu']),
    _n('FE', 'Iron', 'mg/kg', 'asfed', 'trace', ['iron', 'fe']),
    _n('SE', 'Selenium', 'mg/kg', 'asfed', 'trace', ['selenium', 'se']),
    _n('LYS_TOT', 'Lysine (TOTAL, not SID)', '%', 'asfed', 'amino', ['total lysine', 'total lys', 'lysine total']),
    _n('MET_TOT', 'Methionine (TOTAL, not SID)', '%', 'asfed', 'amino', ['total methionine', 'total met', 'methionine total']),
    _n('SID_LYS', 'SID Lysine', '%', 'asfed', 'amino', ['sid lysine', 'sid lys', 'lysine sid', 'lys sid']),
    _n('SID_MET', 'SID Methionine', '%', 'asfed', 'amino', ['sid methionine', 'sid met', 'methionine sid', 'met sid']),
    _n('SID_MC', 'SID Methionine + Cystine', '%', 'asfed', 'amino',
       ['sid met cys', 'sid methionine cystine', 'sid methionine cysteine', 'sid m c', 'sid mc']),
    _n('SID_THR', 'SID Threonine', '%', 'asfed', 'amino', ['sid threonine', 'sid thr', 'threonine sid']),
    _n('SID_TRP', 'SID Tryptophan', '%', 'asfed', 'amino', ['sid tryptophan', 'sid trp', 'tryptophan sid']),
    _n('SID_ARG', 'SID Arginine', '%', 'asfed', 'amino', ['sid arginine', 'sid arg', 'arginine sid']),
    _n('SID_ILE', 'SID Isoleucine', '%', 'asfed', 'amino', ['sid isoleucine', 'sid ile', 'isoleucine sid']),
    _n('SID_LEU', 'SID Leucine', '%', 'asfed', 'amino', ['sid leucine', 'sid leu', 'leucine sid']),
    _n('SID_VAL', 'SID Valine', '%', 'asfed', 'amino', ['sid valine', 'sid val', 'valine sid']),
    _n('SID_HIS', 'SID Histidine', '%', 'asfed', 'amino', ['sid histidine', 'sid his', 'histidine sid']),
    _n('SID_PHE', 'SID Phenylalanine', '%', 'asfed', 'amino', ['sid phenylalanine', 'sid phe', 'phenylalanine sid']),
    _n('LA', 'Linoleic acid C18:2', '%', 'asfed', 'basic', ['linoleic acid', 'linoleic', 'c18 2', 'la']),
    _n('CHOLINE', 'Choline', 'mg/kg', 'asfed', 'vitamin', ['choline']),
    _n('FOLATE', 'Folate', 'mg/kg', 'asfed', 'vitamin', ['folate', 'folic acid']),
    _n('BIOTIN', 'Biotin', 'mg/kg', 'asfed', 'vitamin', ['biotin']),
    _n('ARG_TOT', 'Arginine (TOTAL)', '%', 'asfed', 'amino', ['total arginine', 'total arg']),
    _n('GLY_TOT', 'Glycine (TOTAL)', '%', 'asfed', 'amino', ['total glycine', 'total gly']),
    _n('SER_TOT', 'Serine (TOTAL)', '%', 'asfed', 'amino', ['total serine', 'total ser']),
    _n('GLYSER_TOT', 'Glycine + Serine (TOTAL)', '%', 'asfed', 'amino', ['total gly ser', 'gly ser total']),
    _n('HIS_TOT', 'Histidine (TOTAL)', '%', 'asfed', 'amino', ['total histidine', 'total his']),
    _n('ILE_TOT', 'Isoleucine (TOTAL)', '%', 'asfed', 'amino', ['total isoleucine', 'total ile']),
    _n('LEU_TOT', 'Leucine (TOTAL)', '%', 'asfed', 'amino', ['total leucine', 'total leu']),
    _n('CYS_TOT', 'Cystine (TOTAL)', '%', 'asfed', 'amino', ['total cystine', 'total cysteine', 'total cys']),
    _n('TSAA_TOT', 'Methionine + Cystine (TOTAL)', '%', 'asfed', 'amino', ['total tsaa', 'tsaa total']),
    _n('PHE_TOT', 'Phenylalanine (TOTAL)', '%', 'asfed', 'amino', ['total phenylalanine', 'total phe']),
    _n('TYR_TOT', 'Tyrosine (TOTAL)', '%', 'asfed', 'amino', ['total tyrosine', 'total tyr']),
    _n('TAAA_TOT', 'Phenylalanine + Tyrosine (TOTAL)', '%', 'asfed', 'amino', ['total taaa', 'taaa total']),
    _n('THR_TOT', 'Threonine (TOTAL)', '%', 'asfed', 'amino', ['total threonine', 'total thr']),
    _n('TRP_TOT', 'Tryptophan (TOTAL)', '%', 'asfed', 'amino', ['total tryptophan', 'total trp']),
    _n('VAL_TOT', 'Valine (TOTAL)', '%', 'asfed', 'amino', ['total valine', 'total val']),
]

# Old fixed columns on feed_ingredients that still feed the matrix when no explicit value was entered.
# Zero in those legacy columns means "never filled in", so it is treated as missing.
LEGACY = {'CP': 'crude_protein_pct', 'ME': 'metabolizable_energy_mcal', 'CF': 'crude_fiber_pct',
          'CA': 'calcium_pct', 'AVP': 'phosphorus_pct', 'LYS_TOT': 'lysine_pct', 'MET_TOT': 'methionine_pct'}

# Row labels that mean "this nutrient divided by that nutrient" rather than a plain nutrient.
RATIO_ALIASES = {'ca p': ('CA', 'TP', 1.0), 'ca tp': ('CA', 'TP', 1.0), 'ca avp': ('CA', 'AVP', 1.0)}


def norm(text):
    """Lower-case, drop unit text in brackets, keep letters/digits only: 'Zn (mg/kg)' -> 'zn'."""
    s = re.sub(r'\([^)]*\)', ' ', str(text or '').lower())
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()


def seed_nutrients():
    """Create any missing built-in nutrient. Never overwrites something you edited."""
    have = {n.code for n in Nutrient.query.all()}
    for order, s in enumerate(NUTRIENT_SEED):
        if s['code'] in have:
            continue
        db.session.add(Nutrient(code=s['code'], name=s['name'], unit=s['unit'], basis=s['basis'], group=s['group'],
                                line_per=s['per'], line_scale=s['scale'],
                                aliases='|'.join(sorted({norm(a) for a in s['aliases']})), sort=order))
    db.session.commit()


def load_nutrients():
    return {n.code: n for n in Nutrient.query.order_by(Nutrient.sort, Nutrient.code).all()}


def alias_map(nutrients):
    out = {}
    for n in nutrients.values():
        out[norm(n.code)] = n.code
        if '(' not in n.name:                 # "Lysine (TOTAL, not SID)" must not answer to a bare "lysine"
            out.setdefault(norm(n.name), n.code)
        for a in (n.aliases or '').split('|'):
            if a:
                out.setdefault(a, n.code)
    return out


# --------------------------------------------------------------------------------------------- data table
def needed_codes(lines, nutrients):
    """Every nutrient code that must be known to compute these lines (incl. DM / CP for conversions)."""
    need = set()

    def add(code):
        need.add(code)
        n = nutrients.get(code)
        if n is not None and n.basis == 'dm':
            need.add('DM')
        elif n is not None and n.basis == 'pct_cp':
            need.add('CP')

    for ln in lines:
        add(ln['code'])
        if ln['per'] == 'dm':
            need.add('DM')
        elif ln['per'] != 'asfed':
            add(ln['per'])
    return need


def raw_matrix(ing_ids, codes=None):
    """-> (raw, src): the values exactly as entered (own basis), with old item-master fields as a fallback."""
    ids = list(ing_ids)
    raw = {i: {} for i in ids}
    src = {i: {} for i in ids}
    if not ids:
        return raw, src
    q = IngredientNutrient.query.filter(IngredientNutrient.ingredient_id.in_(ids))
    if codes is not None:
        q = q.filter(IngredientNutrient.nutrient_code.in_(list(codes)))
    for r in q.all():
        raw[r.ingredient_id][r.nutrient_code] = r.value
        src[r.ingredient_id][r.nutrient_code] = r.source or 'entered'
    for ing in FeedIngredient.query.filter(FeedIngredient.id.in_(ids)).all():
        for code, col in LEGACY.items():
            if (codes is None or code in codes) and code not in raw[ing.id]:
                v = getattr(ing, col, 0.0) or 0.0
                if v > 0:
                    raw[ing.id][code] = float(v)
                    src[ing.id][code] = 'item master (old field)'
    return raw, src


def build_table(ing_ids, nutrients, codes):
    """-> (table, sources). table[iid][code] = amount per kg AS FED, or None when unknown."""
    ids = list(ing_ids)
    raw, src = raw_matrix(ids, codes) if codes else ({i: {} for i in ids}, {i: {} for i in ids})
    table = {}
    for i in ids:
        r = raw[i]
        dm, cp = r.get('DM'), r.get('CP')
        t = {}
        for c in codes:
            v = r.get(c)
            basis = nutrients[c].basis if c in nutrients else 'asfed'
            if v is None:
                t[c] = None
            elif c in ('DM', 'CP') or basis == 'asfed':
                t[c] = v
            elif basis == 'dm':
                t[c] = None if dm is None else v * dm / 100.0
            elif basis == 'pct_cp':
                t[c] = None if cp is None else v * cp / 100.0
            else:
                t[c] = None
        table[i] = t
    return table, src


def den_coeff(row, per):
    if per == 'asfed':
        return 1.0
    if per == 'dm':
        v = row.get('DM')
        return None if v is None else v / 100.0
    return row.get(per)


def _label(line, nutrients):
    n = nutrients.get(line['code'])
    base = n.name if n else line['code']
    per = line['per']
    if per == 'asfed':
        return base
    if per == 'dm':
        return f'{base} (per kg DM)' if (n and n.basis != 'dm') else base
    d = nutrients.get(per)
    return f'{base} : {d.name if d else per}' if line['scale'] == 1.0 else f'{base} as % of {d.name if d else per}'


def clean_line(raw, nutrients):
    """Validate one requirement line from the UI or an import. Raises ValueError with a plain message."""
    code = str(raw.get('code') or raw.get('nutrient_code') or '').strip()
    if code not in nutrients:
        raise ValueError(f'Unknown nutrient "{code}".')
    per = str(raw.get('per') or 'asfed').strip()
    if per not in ('asfed', 'dm') and per not in nutrients:
        raise ValueError(f'Unknown "per" nutrient "{per}" on {code}.')
    if per == code:
        raise ValueError(f'{code} cannot be measured per itself.')

    def num(key, default=None, positive=False):
        v = raw.get(key)
        if v is None or v == '':
            return default
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ValueError(f'{code}: {key} must be a number.')
        if not math.isfinite(v) or v < 0 or (positive and v <= 0):
            raise ValueError(f'{code}: {key} must be a positive number.' if positive else f'{code}: {key} cannot be negative.')
        return v

    lo, hi = num('min'), num('max')
    if lo is not None and hi is not None and lo > hi:
        raise ValueError(f'{nutrients[code].name}: minimum is above maximum.')
    line = dict(code=code, per=per, scale=num('scale', 1.0, positive=True), min=lo, max=hi,
                tolerance=num('tolerance', 0.0), required=bool(raw.get('required', True)),
                enabled=bool(raw.get('enabled', True)))
    line['label'] = _label(line, nutrients)
    n = nutrients[code]
    if per == 'asfed':
        line['unit'] = n.unit
    elif per == 'dm':
        line['unit'] = n.unit if n.basis == 'dm' else f'{n.unit} of DM'
    elif line['scale'] == 100.0:
        line['unit'] = f'% of {nutrients[per].name}'
    else:
        line['unit'] = 'ratio'
    return line


def lines_from_profile(profile, nutrients):
    out = []
    for rl in profile.lines:
        if rl.nutrient_code not in nutrients:
            continue
        out.append(clean_line(dict(code=rl.nutrient_code, per=rl.per, scale=rl.scale, min=rl.min_value,
                                   max=rl.max_value, tolerance=rl.tolerance, required=rl.required,
                                   enabled=rl.enabled), nutrients))
    return out


# --------------------------------------------------------------------------------------------- evaluation
def _judge(line, value, missing):
    lo, hi = line.get('min'), line.get('max')
    res = dict(line)
    res.update(value=value, missing_ingredients=sorted(set(missing)))
    if not line.get('enabled', True):
        res['status'] = 'off'
    elif value is None:
        res['status'] = 'missing'
    elif lo is None and hi is None:
        res['status'] = 'info'
    else:
        tol = (line.get('tolerance') or 0.0) + EPS * max(1.0, abs(lo or 0.0), abs(hi or 0.0))
        bad = (lo is not None and value < lo - tol) or (hi is not None and value > hi + tol)
        res['status'] = 'pass' if not bad else ('fail' if line.get('required', True) else 'warn')
    return res


def evaluate(recipe, lines, table):
    """recipe {ingredient_id: fraction}. Pure recomputation from the table: no solver involved."""
    out = []
    for ln in lines:
        num = den = 0.0
        missing = []
        for iid, x in recipe.items():
            if x <= 0:
                continue
            row = table[iid]
            a, d = row.get(ln['code']), den_coeff(row, ln['per'])
            if a is None or d is None:
                missing.append(iid)
                continue
            num += x * a
            den += x * d
        value = ln['scale'] * num / den if (not missing and den > 1e-12) else None
        out.append(_judge(ln, value, missing))
    return out


def recipe_cost(recipe, prices):
    return sum(x * float(prices.get(i, 0.0)) for i, x in recipe.items())


def formula_data_hash(recipe_pct, lines, table):
    """Fingerprint of everything a verification depended on."""
    body = {'r': sorted((int(i), round(float(p), 4)) for i, p in recipe_pct.items()),
            'l': [[l['code'], l['per'], l['scale'], l['min'], l['max'], l['tolerance'], l['required'], l['enabled']] for l in lines],
            't': {str(i): {c: (None if v is None else round(v, 6)) for c, v in sorted(table[i].items())} for i in sorted(table)}}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def rdp_rup_warnings(ids, nutrients, names):
    """RDP + RUP as % of CP should be close to 100 for an ingredient. Warn (never block) when they are not."""
    if 'RDP' not in nutrients or 'RUP' not in nutrients:
        return []
    rows = IngredientNutrient.query.filter(IngredientNutrient.ingredient_id.in_(list(ids)),
                                           IngredientNutrient.nutrient_code.in_(['RDP', 'RUP'])).all()
    by = {}
    for r in rows:
        by.setdefault(r.ingredient_id, {})[r.nutrient_code] = r.value
    out = []
    for iid, d in by.items():
        if 'RDP' in d and 'RUP' in d and abs(d['RDP'] + d['RUP'] - 100.0) > 2.0:
            out.append(f'{names.get(iid, iid)}: RDP + RUP = {d["RDP"] + d["RUP"]:.2f}% of CP (expected about 100%). '
                       f'Check the degradability data, or an undigestible fraction is missing.')
    return out


def verify(recipe_pct, lines, table, info, bounds=None, total_tol=0.1):
    """Independent release check for a recipe given as {ingredient_id: percent}. Never calls the LP."""
    bounds = bounds or {}
    issues, warnings = [], []
    total = sum(recipe_pct.values())
    if any(p < 0 or not math.isfinite(p) for p in recipe_pct.values()):
        issues.append('An inclusion is negative or not a number.')
    if abs(total - 100.0) > total_tol:
        issues.append(f'Inclusions add up to {total:.3f}%, not 100% (allowed difference {total_tol}%).')
    for iid, p in recipe_pct.items():
        meta = info.get(iid)
        if meta is None:
            issues.append(f'Ingredient {iid} not found.')
            continue
        hi = min(meta['max_pct'], bounds.get(iid, {}).get('max', 100.0))
        lo = bounds.get(iid, {}).get('min', 0.0)
        if p > hi + 1e-4:
            issues.append(f'{meta["name"]} is {p:.3f}%, above its limit of {hi:g}%.')
        if p < lo - 1e-4:
            issues.append(f'{meta["name"]} is {p:.3f}%, below its minimum of {lo:g}%.')
        if p > 0 and (meta.get('price') or 0) <= 0:
            warnings.append(f'{meta["name"]} has no price, so cost is understated.')
    frac = {i: p / total for i, p in recipe_pct.items()} if total > 0 else {}
    results = evaluate(frac, lines, table) if frac else []
    failed = [r for r in results if r['status'] == 'fail']
    missing = [r for r in results if r['status'] == 'missing' and r.get('required', True) and (r.get('min') is not None or r.get('max') is not None)]
    for r in missing:
        who = ', '.join(info[i]['name'] for i in r['missing_ingredients'] if i in info) or 'the ingredients'
        issues.append(f'Cannot check {r["label"]}: no value for {who}.')
    for r in failed:
        issues.append(f'{r["label"]} is {r["value"]:.3f} {r["unit"]} (limit {_limit_text(r)}).')
    for r in results:
        if r['status'] == 'warn':
            warnings.append(f'Advisory: {r["label"]} is {r["value"]:.3f} {r["unit"]} (limit {_limit_text(r)}).')
    only_missing = bool(issues) and len(issues) == len(missing)
    status = 'pass' if not issues else ('incomplete' if only_missing else 'fail')
    return dict(status=status, releasable=status == 'pass', issues=issues, warnings=warnings, lines=results,
                total_pct=round(total, 4), cost_per_kg=round(recipe_cost(frac, {i: info[i]['price'] for i in info if i in frac}), 4) if frac else 0.0)


def _limit_text(r):
    lo, hi = r.get('min'), r.get('max')
    if lo is not None and hi is not None:
        return f'{lo:g} to {hi:g}'
    return f'at least {lo:g}' if lo is not None else f'at most {hi:g}'


# --------------------------------------------------------------------------------------------- optimisation
def _coeffs(ids, line, table):
    a = np.array([table[i][line['code']] for i in ids], dtype=float)
    d = np.array([den_coeff(table[i], line['per']) for i in ids], dtype=float)
    return a, d


def _extreme_ratio(a, d, scale, lo, hi, maximize):
    """Exact best/worst value of scale*sum(xa)/sum(xd) over the bounds and mass balance (Dinkelbach)."""
    n = len(a)
    A_eq, b_eq = [np.ones(n)], [1.0]
    bounds = list(zip(lo, hi))
    lam = 0.0
    for _ in range(60):
        obj = (scale * a - lam * d)
        res = linprog(-obj if maximize else obj, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method='highs')
        if res.status != 0:
            return None
        x = res.x
        den = float(np.dot(x, d))
        if den <= 1e-12:
            return None
        new = scale * float(np.dot(x, a)) / den
        if abs(new - lam) < 1e-11:
            return new
        lam = new
    return lam


def _solve(ids, lp_lines, table, prices, lo, hi, shift=None, margin=0.0):
    """Min-cost LP. shift = {line_index: ('min'|'max', delta)} tightens/relaxes a limit for sensitivity runs."""
    n = len(ids)
    A, b = [], []
    for k, ln in enumerate(lp_lines):
        a, d = _coeffs(ids, ln, table)
        for side in ('min', 'max'):
            lim = ln.get(side)
            if lim is None:
                continue
            if shift and shift.get(k) and shift[k][0] == side:
                lim = lim + shift[k][1]
            if margin:                                     # keep a hair inside the limit so rounded numbers still pass
                lim = lim + margin * max(1.0, abs(lim)) * (1 if side == 'min' else -1)
            row = ln['scale'] * a - lim * d
            if side == 'min':
                A.append(-row); b.append(0.0)
            else:
                A.append(row); b.append(0.0)
    c = np.array([prices[i] for i in ids], dtype=float)
    res = linprog(c, A_ub=np.array(A) if A else None, b_ub=np.array(b) if b else None,
                  A_eq=[np.ones(n)], b_eq=[1.0], bounds=list(zip(lo, hi)), method='highs')
    return res


def round_to_precision(x, batch_kg, precision):
    kgs = [round(v * batch_kg / precision) * precision for v in x]
    diff = batch_kg - sum(kgs)
    if kgs:
        k = max(range(len(kgs)), key=lambda j: kgs[j])
        kgs[k] = max(0.0, kgs[k] + diff)
    return [round(v, 6) for v in kgs]


def optimize(ids, lines, table, nutrients, info, bounds=None, batch_kg=1000.0, precision_kg=0.1, exclude_missing=False):
    bounds = bounds or {}
    names = {i: info[i]['name'] for i in ids}
    active = [l for l in lines if l.get('enabled', True)]
    lp_lines = [l for l in active if l.get('required', True) and (l.get('min') is not None or l.get('max') is not None)]
    ids = list(ids)

    # 1) data completeness: missing data is never silently treated as zero
    missing = []
    for iid in ids:
        if not (info[iid]['price'] > 0):
            missing.append(dict(ingredient_id=iid, ingredient=names[iid], nutrient='Price', line='cost'))
        for ln in lp_lines:
            if table[iid].get(ln['code']) is None or den_coeff(table[iid], ln['per']) is None:
                missing.append(dict(ingredient_id=iid, ingredient=names[iid], nutrient=nutrients[ln['code']].name, line=ln['label']))
    if missing:
        bad = sorted({m['ingredient_id'] for m in missing})
        if not exclude_missing:
            return dict(status='missing_data', missing=missing, can_exclude=[dict(id=i, name=names[i]) for i in bad],
                        message='Some ingredients have no value for a nutrient you are enforcing (or no price). '
                                'Fill the values in, or exclude those ingredients.')
        ids = [i for i in ids if i not in bad]
        if not ids:
            return dict(status='error', message='Every selected ingredient was excluded for missing data.')

    n = len(ids)
    lo = [max(0.0, bounds.get(i, {}).get('min', 0.0)) / 100.0 for i in ids]
    hi = [min(info[i]['max_pct'], bounds.get(i, {}).get('max', 100.0), 100.0) / 100.0 for i in ids]
    for k, i in enumerate(ids):
        if lo[k] > hi[k] + 1e-12:
            return dict(status='error', message=f'{names[i]}: minimum inclusion is above its maximum.')
    if sum(lo) > 1 + 1e-9 or sum(hi) < 1 - 1e-9:
        return dict(status='infeasible', message='The ingredient minimums/maximums alone cannot add up to 100%. '
                    f'Selected ingredients can make {sum(hi) * 100:.1f}% at most and {sum(lo) * 100:.1f}% at least.',
                    diagnosis=[])
    prices = {i: float(info[i]['price']) for i in ids}

    res = _solve(ids, lp_lines, table, prices, lo, hi)
    if res.status != 0:
        return dict(status='infeasible', message='No recipe meets every required limit with these ingredients.',
                    diagnosis=_diagnose(ids, lp_lines, table, prices, lo, hi, nutrients, names), excluded=_excluded(ids, missing, names))

    # The numbers handed to the user are percentages to 4 decimals. A recipe sitting exactly ON a limit can fall a
    # hair under it after rounding, so verify the ROUNDED recipe and, if needed, solve slightly inside the limits.
    base_cost = float(res.fun)
    recipe, exact, margin_used = None, None, 0.0
    for margin in (0.0, 1e-5, 1e-4, 1e-3):
        r = res if margin == 0.0 else _solve(ids, lp_lines, table, prices, lo, hi, margin=margin)
        if r.status != 0:
            break
        x = np.clip(r.x, 0.0, None)
        x = x / x.sum()
        pct = [round(float(v) * 100, 4) for v in x]
        top = max(range(n), key=lambda j: pct[j])
        pct[top] = round(pct[top] + (100.0 - sum(pct)), 4)
        cand = {i: p / 100.0 for i, p in zip(ids, pct) if p > 0}
        ev = evaluate(cand, lines, table)
        if not any(e['status'] == 'fail' for e in ev):
            recipe, exact, margin_used = cand, ev, margin
            break
    if recipe is None:
        return dict(status='solver_mismatch', message='The recipe failed the independent check after rounding to 4 decimals. '
                    'Not released. Please report this.')
    cost = recipe_cost(recipe, prices)

    # 2) what each binding limit costs (re-solve with the limit nudged)
    sens = []
    x0 = np.clip(res.x, 0.0, None)
    x0 = x0 / x0.sum()
    exact_point = {i: float(v) for i, v in zip(ids, x0) if v > 1e-12}      # the un-rounded LP optimum: binding = on a limit
    for k, ln in enumerate(lp_lines):
        value = evaluate(exact_point, [ln], table)[0]['value']
        for side in ('min', 'max'):
            lim = ln.get(side)
            if lim is None or value is None or abs(value - lim) > 1e-6 * max(1.0, abs(lim)):
                continue                                   # not binding: costs nothing
            delta = max(1e-3 * abs(lim), 1e-3)
            sign = 1.0 if side == 'min' else -1.0         # which way is "tighter"
            tight = _solve(ids, lp_lines, table, prices, lo, hi, {k: (side, sign * delta)})
            loose = _solve(ids, lp_lines, table, prices, lo, hi, {k: (side, -sign * delta)})
            sens.append(dict(label=ln['label'], side=side, limit=lim, unit=ln.get('unit', ''),
                             cost_per_kg_per_unit_tighter=round((tight.fun - base_cost) / delta, 4) if tight.status == 0 else None,
                             saving_per_kg_per_unit_looser=round((base_cost - loose.fun) / delta, 4) if loose.status == 0 else None))

    # 3) real batches are weighed to a precision: recheck the rounded recipe
    kgs = round_to_precision([recipe.get(i, 0.0) for i in ids], batch_kg, precision_kg)
    rounded_frac = {i: k / batch_kg for i, k in zip(ids, kgs) if k > 0}
    rounded_eval = evaluate(rounded_frac, lines, table)
    lines_out = [dict(ingredient_id=i, ingredient=names[i], pct=round(recipe[i] * 100, 4), kg=round(recipe[i] * batch_kg, 3),
                      rounded_kg=kgs[ids.index(i)], price=prices[i], cost_per_kg_feed=round(recipe[i] * prices[i], 4))
                 for i in sorted(recipe, key=lambda j: -recipe[j])]
    return dict(status='optimal', cost_per_kg=round(cost, 4), batch_cost=round(cost * batch_kg, 2), batch_kg=batch_kg,
                recipe=lines_out, lines=exact, rounded_lines=rounded_eval,
                rounded_ok=not any(r['status'] == 'fail' for r in rounded_eval),
                rounded_cost_per_kg=round(recipe_cost(rounded_frac, prices), 4), precision_kg=precision_kg,
                sensitivity=sens, excluded=_excluded(ids, missing, names), safety_margin=margin_used)


def _excluded(ids, missing, names):
    return sorted({m['ingredient'] for m in missing})


def _diagnose(ids, lp_lines, table, prices, lo, hi, nutrients, names):
    """Plain-language reasons for infeasibility: limits impossible alone, then limits that clash."""
    out = []
    for ln in lp_lines:
        a, d = _coeffs(ids, ln, table)
        if ln.get('min') is not None:
            best = _extreme_ratio(a, d, ln['scale'], lo, hi, True)
            if best is not None and best < ln['min'] - 1e-9:
                out.append(f'{ln["label"]}: the best these ingredients can reach is {best:.3f} but the minimum is {ln["min"]:g}.')
        if ln.get('max') is not None:
            low = _extreme_ratio(a, d, ln['scale'], lo, hi, False)
            if low is not None and low > ln['max'] + 1e-9:
                out.append(f'{ln["label"]}: the lowest these ingredients can reach is {low:.3f} but the maximum is {ln["max"]:g}.')
    if out:
        return out
    relax = []
    for k, ln in enumerate(lp_lines):
        rest = [l for j, l in enumerate(lp_lines) if j != k]
        r = _solve(ids, rest, table, prices, lo, hi)
        if r.status == 0:
            relax.append(ln['label'])
    if relax:
        out.append('Every limit is reachable on its own, but together they clash. Relaxing any ONE of these makes it feasible: ' + ', '.join(relax) + '.')
    else:
        out.append('Several limits clash with each other and with the ingredient limits. Add ingredients or relax more than one limit.')
    return out


TEMPLATE_DAIRY = [  # (code, per, scale, min, max): copied from the user's own formulation sheet, Oct 2026
    ('DM', 'asfed', 1, None, None), ('NEL', 'dm', 1, 1.60, None), ('RDP', 'CP', 100, 60, None), ('RUP', 'CP', 100, 35, None),
    ('NDF', 'asfed', 1, 22, None), ('ADF', 'asfed', 1, 12, None), ('CA', 'asfed', 1, 1.00, None), ('TP', 'asfed', 1, None, None),
    ('AVP', 'asfed', 1, 0.35, None), ('CA', 'TP', 1, None, None), ('K', 'asfed', 1, 0.80, None), ('CL', 'asfed', 1, 0.30, None),
    ('MN', 'asfed', 1, 50, None), ('NA', 'asfed', 1, 0.30, None), ('ZN', 'asfed', 1, 50, None)]


SID_CORE = ['SID_LYS', 'SID_MET', 'SID_MC', 'SID_THR', 'SID_TRP', 'SID_ARG', 'SID_ILE', 'SID_VAL']
POULTRY_STAGES = [('Chick starter', 'Poultry - Chick starter (SID-AA)'), ('Grower / pullet', 'Poultry - Grower (SID-AA)'),
                  ('Layer', 'Poultry - Layer (SID-AA)'), ('Broiler starter', 'Poultry - Broiler starter (SID-AA)'),
                  ('Broiler grower', 'Poultry - Broiler grower (SID-AA)'), ('Broiler finisher', 'Poultry - Broiler finisher (SID-AA)')]


def poultry_sid_lines():
    """(code, per, scale): energy, protein, SID amino acids, key minerals, then each SID amino acid as % of SID lysine."""
    lines = [('ME_KCAL', 'asfed', 1.0), ('CP', 'asfed', 1.0)] + [(c, 'asfed', 1.0) for c in SID_CORE]
    lines += [('CA', 'asfed', 1.0), ('AVP', 'asfed', 1.0), ('NA', 'asfed', 1.0), ('CL', 'asfed', 1.0)]
    lines += [(c, 'SID_LYS', 100.0) for c in SID_CORE if c != 'SID_LYS']
    return lines


def seed_templates():
    """Starter profiles. Existing ones are never overwritten. Poultry profiles carry the SID-AA lines with NO limits:
    requirement numbers come from your breeder / nutritionist specification, not from this software."""
    nutrients = load_nutrients()
    if not RequirementProfile.query.filter_by(name='Dairy concentrate (from your sheet)').first():
        prof = RequirementProfile(name='Dairy concentrate (from your sheet)', species='ruminant', stage='Dairy',
                                  source='Copied from your formulation sheet (Oct 2026). Confirm each limit against your own standard.',
                                  notes='Starter only. Edit or replace.')
        for k, (code, per, scale, lo, hi) in enumerate(TEMPLATE_DAIRY):
            if code in nutrients:
                prof.lines.append(RequirementLine(nutrient_code=code, per=per, scale=scale, min_value=lo, max_value=hi,
                                                  required=lo is not None or hi is not None, enabled=True, sort=k))
        db.session.add(prof)
    for stage, name in POULTRY_STAGES:
        if RequirementProfile.query.filter_by(name=name).first():
            continue
        prof = RequirementProfile(name=name, species='poultry', stage=stage,
                                  source='Template: lines only, no limits yet. Type the minimums from your breeder / nutritionist '
                                         'specification for this strain and age, then Save profile.',
                                  notes='Limits are blank on purpose.')
        for k, (code, per, scale) in enumerate(poultry_sid_lines()):
            if code in nutrients and (per == 'asfed' or per in nutrients):
                prof.lines.append(RequirementLine(nutrient_code=code, per=per, scale=scale, min_value=None, max_value=None,
                                                  required=True, enabled=True, sort=k))
        db.session.add(prof)
    db.session.commit()
