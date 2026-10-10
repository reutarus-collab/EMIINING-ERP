from services.idempotency import idempotent
import math
import uuid
from datetime import datetime

from flask import Blueprint, g, jsonify, request

from routes.auth import roles_required
from services.db import db
from services.models import (
    FeedIngredient,
    MillingRun,
    ProductionRun,
    ProductionRunLine,
    StockMovement,
)
from services.inventory import resolve_location, location_stock, change_stock, receive_stock

factory_bp = Blueprint('factory', __name__)


@factory_bp.route('/api/formulate', methods=['POST'])
@roles_required('admin', 'accountant', 'warehouse', 'factory')
def run_formulation():
    from services.formulator_service import solve_feed_formulation
    data = request.get_json(silent=True) or {}
    species = str(data.get('species') or '').strip()
    if not species or len(species) > 100:
        return jsonify(status='error', message='Choose a livestock stage.'), 400
    try:
        location = resolve_location(data.get('location_id'))
        batch = _positive_number(data.get('target_batch_kg'), 'Batch size')
        raw_ids = data.get('ingredient_ids')
        if raw_ids is not None:
            if not isinstance(raw_ids, list) or not raw_ids:
                raise ValueError('Choose at least one raw ingredient.')
            raw_ids = sorted({int(value) for value in raw_ids})
    except (TypeError, ValueError) as exc:
        return jsonify(status='error', message=str(exc)), 400
    try:
        target_cp = float(data.get('target_cp_pct')) if data.get('target_cp_pct') not in (None, '') else None
        target_me = float(data.get('target_me_mcal')) if data.get('target_me_mcal') not in (None, '') else None
    except (TypeError, ValueError):
        return jsonify(status='error', message='Enter valid protein and energy targets.'), 400
    result = solve_feed_formulation(species, batch, raw_ids, location.id, target_cp, target_me)
    return jsonify(result), 200 if result.get('status') == 'optimal' else 422


@factory_bp.route('/api/factory/milling-runs', methods=['GET', 'POST'])
@roles_required('admin', 'accountant', 'warehouse', 'factory')
@idempotent('milling-run')
def milling_runs():
    try:
        location = resolve_location((request.get_json(silent=True) or {}).get('location_id')
                                    if request.method == 'POST' else request.args.get('location_id'))
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400

    if request.method == 'GET':
        runs = (MillingRun.query.filter_by(location_id=location.id)
                .order_by(MillingRun.created_at.desc()).limit(100).all())
        return jsonify([{
            'run_no': run.run_no,
            'customer_name': run.customer_name,
            'customer_phone': run.customer_phone or '',
            'grain_description': run.grain_description,
            'input_qty_kg': round(run.input_qty_kg, 2),
            'output_qty_kg': round(run.output_qty_kg, 2),
            'loss_kg': round(run.variance_loss_kg, 2),
            'notes': run.notes or '',
            'service_sale_reference': run.service_sale_reference or '',
            'created_by': run.created_by or '',
            'created_at': run.created_at.strftime('%Y-%m-%d %H:%M') if run.created_at else '',
        } for run in runs])

    data = request.get_json(silent=True) or {}
    customer = str(data.get('customer_name') or 'Walk-in customer').strip()
    grain = str(data.get('grain_description') or 'Maize').strip()
    phone = str(data.get('customer_phone') or '').strip()
    notes = str(data.get('notes') or '').strip()
    sale_ref = str(data.get('service_sale_reference') or '').strip()
    if not customer or len(customer) > 100 or not grain or len(grain) > 100:
        return jsonify(status='error', message='Enter valid customer and grain details.'), 400
    if any(len(value) > limit for value, limit in ((phone, 20), (notes, 200), (sale_ref, 50))):
        return jsonify(status='error', message='Phone, notes, or sale reference is too long.'), 400
    try:
        input_kg = _positive_number(data.get('input_qty_kg'), 'Grain weight received')
        output_kg = _positive_number(data.get('output_qty_kg'), 'Milled weight returned', allow_zero=True)
        if output_kg > input_kg + 0.001:
            raise ValueError('Returned weight cannot exceed the grain received.')
        run = MillingRun(
            run_no=f'MILL-{uuid.uuid4().hex[:10].upper()}',
            customer_name=customer, customer_phone=phone or None,
            grain_description=grain, input_qty_kg=input_kg,
            output_qty_kg=output_kg, variance_loss_kg=round(input_kg - output_kg, 3),
            notes=notes or None, service_sale_reference=sale_ref or None,
            created_by=g.user.username, location_id=location.id,
            created_at=datetime.utcnow(),
        )
        db.session.add(run)
        db.session.commit()
    except (TypeError, ValueError) as exc:
        db.session.rollback()
        return jsonify(status='error', message=str(exc)), 400
    except Exception:
        db.session.rollback()
        return jsonify(status='error', message='Could not record the customer milling job.'), 500
    return jsonify(status='success', run_no=run.run_no, loss_kg=run.variance_loss_kg), 201


def _positive_number(value, label, allow_zero=False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label} must be a number.')
    if not math.isfinite(number) or (number < 0 if allow_zero else number <= 0):
        qualifier = 'zero or more' if allow_zero else 'above zero'
        raise ValueError(f'{label} must be {qualifier}.')
    return number


@factory_bp.route('/api/factory/produce', methods=['POST'])
@roles_required('admin', 'accountant', 'warehouse', 'factory')
@idempotent('production-run')
def record_production_run():
    data = request.get_json(silent=True) or {}
    formula_name = str(data.get('formula_name') or '').strip()
    loss_reason = str(data.get('loss_reason') or '').strip()
    if not formula_name or len(formula_name) > 100:
        return jsonify(status='error', message='Enter a formula name (up to 100 characters).'), 400
    if len(loss_reason) > 200:
        return jsonify(status='error', message='Loss reason must be 200 characters or fewer.'), 400

    try:
        location = resolve_location(data.get('location_id'))
        output_id = int(data.get('output_ingredient_id'))
        planned_output = _positive_number(data.get('planned_output_kg'), 'Planned output')
        actual_output = _positive_number(data.get('actual_output_kg'), 'Actual output')
        raw_lines = data.get('inputs')
        if not isinstance(raw_lines, list) or not raw_lines:
            raise ValueError('Add at least one ingredient to the batch.')

        inputs = []
        for index, raw in enumerate(raw_lines, start=1):
            if not isinstance(raw, dict):
                raise ValueError(f'Ingredient row {index} is invalid.')
            ingredient_id = int(raw.get('ingredient_id'))
            planned_qty = _positive_number(raw.get('planned_qty_kg', 0), f'Planned quantity on row {index}', allow_zero=True)
            actual_qty = _positive_number(raw.get('actual_qty_kg'), f'Actual quantity on row {index}')
            inputs.append((ingredient_id, planned_qty, actual_qty))

        ingredient_ids = sorted({row[0] for row in inputs})
        locked_ids = sorted(set(ingredient_ids) | {output_id})
        ingredients = (FeedIngredient.query.filter(FeedIngredient.id.in_(locked_ids))
                       .order_by(FeedIngredient.id).with_for_update().all())
        by_id = {item.id: item for item in ingredients}
        if len(by_id) != len(locked_ids):
            raise ValueError('One or more input ingredients were not found.')
        output_item = by_id.get(output_id)
        if not output_item:
            raise ValueError('Output item not found.')
        if output_item.category != 'Finished Feed':
            raise ValueError('Choose a Finished Feed item as the batch output.')
        for ingredient_id in ingredient_ids:
            if not (by_id[ingredient_id].category or '').startswith('Raw'):
                raise ValueError(f'{by_id[ingredient_id].name} is not a raw ingredient.')

        requested_by_item = {}
        for ingredient_id, _, actual_qty in inputs:
            requested_by_item[ingredient_id] = requested_by_item.get(ingredient_id, 0.0) + actual_qty
        for ingredient_id, requested in requested_by_item.items():
            available = location_stock(location.id, ingredient_id, lock=True).quantity_kg or 0.0
            if requested > available + 0.000001:
                raise ValueError(
                    f'Insufficient stock for {by_id[ingredient_id].name}. '
                    f'Available: {available:.2f} kg; required: {requested:.2f} kg.'
                )

        input_total = sum(row[2] for row in inputs)
        if actual_output > input_total + 0.01:
            raise ValueError('Actual output exceeds total ingredient input. Verify the scale readings and include every material added to the batch.')
        loss_kg = max(0.0, input_total - actual_output)
        loss_pct = (loss_kg / input_total * 100.0) if input_total else 0.0
        if loss_kg > 0.01 and not loss_reason:
            raise ValueError('Enter a reason for the batch loss.')

        batch_no = f'BATCH-{uuid.uuid4().hex[:10].upper()}'
        run = ProductionRun(
            batch_no=batch_no,
            formula_name=formula_name,
            output_ingredient_id=output_item.id,
            planned_output_kg=planned_output,
            actual_output_kg=actual_output,
            total_input_kg=input_total,
            loss_kg=loss_kg,
            loss_pct=loss_pct,
            loss_reason=loss_reason or None,
            created_by=g.user.username,
            created_at=datetime.utcnow(),
            location_id=location.id,
        )
        db.session.add(run)
        db.session.flush()

        input_cost = 0.0
        for ingredient_id, planned_qty, actual_qty in inputs:
            ingredient = by_id[ingredient_id]
            input_stock = location_stock(location.id, ingredient.id, lock=True)
            unit_cost = input_stock.unit_cost_per_kg or 0.0
            line_cost = round(actual_qty * unit_cost, 2)
            input_cost += line_cost
            change_stock(location.id, ingredient, -actual_qty, 'PRODUCTION_CONSUMPTION', batch_no)
            db.session.add(ProductionRunLine(
                production_run_id=run.id,
                ingredient_id=ingredient_id,
                planned_qty_kg=planned_qty,
                actual_qty_kg=actual_qty,
                cost_per_kg=unit_cost,
                line_cost=line_cost,
            ))

        input_cost = round(input_cost, 2)
        loss_cost = round(input_cost * loss_kg / input_total, 2) if input_total else 0.0
        output_cost = round(input_cost - loss_cost, 2)
        receive_stock(location.id, output_item, actual_output, output_cost / actual_output,
                      'PRODUCTION_OUTPUT', batch_no)
        run.total_input_cost = input_cost
        run.loss_cost = loss_cost
        from services.ledger_service import post_gl_entry
        ledger_ok = post_gl_entry(batch_no, '1200', output_cost, 0.0, 'PRODUCTION', run.id)
        if loss_cost > 0:
            ledger_ok = post_gl_entry(batch_no, '5100', loss_cost, 0.0, 'PRODUCTION_LOSS', run.id) and ledger_ok
        ledger_ok = post_gl_entry(batch_no, '1200', 0.0, input_cost, 'PRODUCTION', run.id) and ledger_ok
        if not ledger_ok:
            raise RuntimeError('Could not post production accounting entries.')
        db.session.commit()
    except (TypeError, ValueError) as exc:
        db.session.rollback()
        return jsonify(status='error', message=str(exc)), 400
    except Exception:
        db.session.rollback()
        return jsonify(status='error', message='Could not record the production batch.'), 500

    return jsonify(
        status='success',
        batch_no=batch_no,
        total_input_kg=round(input_total, 2),
        actual_output_kg=round(actual_output, 2),
        loss_kg=round(loss_kg, 2),
        loss_pct=round(loss_pct, 2),
        yield_gain_kg=round(max(0.0, actual_output - input_total), 2),
        total_input_cost=round(input_cost, 2),
    )


@factory_bp.route('/api/factory/production-runs', methods=['GET'])
@roles_required('admin', 'accountant', 'warehouse', 'factory')
def production_run_history():
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    runs = ProductionRun.query.filter_by(location_id=location.id).order_by(ProductionRun.created_at.desc()).limit(100).all()
    item_names = {item.id: item.name for item in FeedIngredient.query.all()}
    return jsonify([{
        'batch_no': run.batch_no,
        'formula_name': run.formula_name,
        'output_item': item_names.get(run.output_ingredient_id, 'Unknown'),
        'planned_output_kg': round(run.planned_output_kg, 2),
        'actual_output_kg': round(run.actual_output_kg, 2),
        'output_variance_kg': round(run.actual_output_kg - run.planned_output_kg, 2),
        'total_input_kg': round(run.total_input_kg, 2),
        'loss_kg': round(run.loss_kg, 2),
        'loss_pct': round(run.loss_pct, 2),
        'loss_reason': run.loss_reason or '',
        'total_input_cost': round(run.total_input_cost, 2),
        'loss_cost': round(run.loss_cost, 2),
        'created_by': run.created_by or '',
        'created_at': run.created_at.strftime('%Y-%m-%d %H:%M') if run.created_at else '',
    } for run in runs])


@factory_bp.route('/api/factory/production-runs/<batch_no>/lines', methods=['GET'])
@roles_required('admin', 'accountant', 'warehouse', 'factory')
def production_run_lines(batch_no):
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    run = ProductionRun.query.filter_by(batch_no=batch_no, location_id=location.id).first()
    if not run:
        return jsonify(status='error', message='Production batch not found.'), 404
    rows = (db.session.query(ProductionRunLine, FeedIngredient.name)
            .join(FeedIngredient, FeedIngredient.id == ProductionRunLine.ingredient_id)
            .filter(ProductionRunLine.production_run_id == run.id)
            .order_by(ProductionRunLine.id).all())
    return jsonify([{
        'ingredient': name,
        'planned_qty_kg': round(line.planned_qty_kg, 2),
        'actual_qty_kg': round(line.actual_qty_kg, 2),
        'variance_kg': round(line.actual_qty_kg - line.planned_qty_kg, 2),
        'cost_per_kg': round(line.cost_per_kg, 2),
        'line_cost': round(line.line_cost, 2),
    } for line, name in rows])


def _current_costs(ids=None):
    """Cost per kg at the user's outlet (falls back to the item cost), like the Ration Formulator."""
    from services.models import LocationStock
    q = FeedIngredient.query
    if ids is not None:
        q = q.filter(FeedIngredient.id.in_(list(ids)))
    items = q.all()
    stocks = {}
    try:
        loc = resolve_location(request.args.get('location_id'))
        stocks = {r.ingredient_id: r for r in LocationStock.query.filter_by(location_id=loc.id).all()}
    except ValueError:
        pass
    out = {}
    for i in items:
        st = stocks.get(i.id)
        cost = st.unit_cost_per_kg if st and (st.unit_cost_per_kg or 0) > 0 else (i.cost_per_kg or 0.0)
        out[i.id] = float(cost or 0.0)
    return out


def _formula_json(fm, costs):
    lines, unpriced, weighted = [], [], 0.0
    for ln in fm.lines:
        ing = db.session.get(FeedIngredient, ln.ingredient_id)
        c = costs.get(ln.ingredient_id, 0.0)
        if c <= 0:
            unpriced.append(ing.name if ing else '?')
        weighted += ln.pct * c
        lines.append({'ingredient_id': ln.ingredient_id,
                      'ingredient_name': ing.name if ing else '?',
                      'pct': ln.pct, 'cost_per_kg': round(c, 2)})
    total = sum(l['pct'] for l in lines)
    return {'id': fm.id, 'name': fm.name, 'notes': fm.notes or '',
            'total_pct': round(total, 4), 'lines': lines,
            'cost_per_kg': round(weighted / total, 2) if total else 0.0,
            'unpriced': unpriced}


@factory_bp.route('/api/factory/formulas', methods=['GET'])
@roles_required('admin', 'accountant', 'warehouse', 'factory')
def list_saved_formulas():
    from services.models import SavedFormula
    fms = SavedFormula.query.order_by(SavedFormula.name).all()
    ids = {ln.ingredient_id for fm in fms for ln in fm.lines}
    costs = _current_costs(ids)
    return jsonify([_formula_json(fm, costs) for fm in fms])


@factory_bp.route('/api/factory/ingredient-costs', methods=['GET'])
@roles_required('admin', 'accountant', 'warehouse', 'factory')
def formula_ingredient_costs():
    raw = FeedIngredient.query.filter(FeedIngredient.category.ilike('%raw%')).all()
    costs = _current_costs({i.id for i in raw})
    return jsonify({str(k): round(v, 2) for k, v in costs.items()})


def _clean_formula(data):
    name = str(data.get('name') or '').strip()
    if not name or len(name) > 100:
        raise ValueError('Give the formula a name (up to 100 characters).')
    notes = str(data.get('notes') or '').strip()[:300]
    raw_lines = data.get('lines')
    if not isinstance(raw_lines, list) or not raw_lines or len(raw_lines) > 60:
        raise ValueError('Add at least one ingredient.')
    seen, lines = set(), []
    for ln in raw_lines:
        try:
            iid = int(ln.get('ingredient_id'))
            pct = float(ln.get('pct'))
        except (TypeError, ValueError, AttributeError):
            raise ValueError('Every line needs an ingredient and a percentage.')
        if not math.isfinite(pct) or pct <= 0 or pct > 100:
            raise ValueError('Percentages must be above 0 and at most 100.')
        if iid in seen:
            raise ValueError('An ingredient appears twice. Combine it into one line.')
        ing = db.session.get(FeedIngredient, iid)
        if not ing or 'raw' not in (ing.category or '').lower():
            raise ValueError('Every ingredient must be a Raw item.')
        seen.add(iid)
        lines.append((iid, round(pct, 4)))
    total = sum(p for _, p in lines)
    if abs(total - 100.0) > 0.1:
        raise ValueError('Percentages add up to %.3f%%. They must total 100%% (within 0.1).' % total)
    return name, notes, lines


@factory_bp.route('/api/factory/formulas', methods=['POST'])
@roles_required('admin', 'factory')
def save_formula():
    from sqlalchemy.exc import IntegrityError
    from services.models import SavedFormula, SavedFormulaLine
    data = request.get_json(silent=True) or {}
    try:
        name, notes, lines = _clean_formula(data)
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    same_name = SavedFormula.query.filter(db.func.lower(SavedFormula.name) == name.lower()).first()
    fm = None
    if data.get('id') not in (None, ''):
        fm = db.session.get(SavedFormula, int(data['id']))
        if not fm:
            return jsonify(status='error', message='That formula no longer exists.'), 404
        if same_name and same_name.id != fm.id:
            return jsonify(status='error', message='Another formula already uses that name.'), 409
    elif same_name:
        existing = {l.ingredient_id: round(l.pct, 4) for l in same_name.lines}
        if existing == dict(lines) and (same_name.notes or '') == notes:
            return jsonify(status='success', id=same_name.id, duplicate=True)  # safe retry
        return jsonify(status='error', message='A formula with this name already exists. Open it from the list to edit.'), 409
    try:
        if fm is None:
            fm = SavedFormula(name=name, notes=notes)
            db.session.add(fm)
        else:
            fm.name, fm.notes = name, notes
            fm.lines = []
            db.session.flush()
        for iid, pct in lines:
            fm.lines.append(SavedFormulaLine(ingredient_id=iid, pct=pct))
        db.session.commit()
        return jsonify(status='success', id=fm.id)
    except IntegrityError:
        db.session.rollback()
        return jsonify(status='error', message='A formula with this name already exists.'), 409
    except Exception as exc:
        db.session.rollback()
        return jsonify(status='error', message=str(exc)), 400


@factory_bp.route('/api/factory/formulas/<int:formula_id>', methods=['DELETE'])
@roles_required('admin')
def delete_formula(formula_id):
    from services.models import SavedFormula
    fm = SavedFormula.query.get_or_404(formula_id)
    db.session.delete(fm)
    db.session.commit()
    return jsonify(status='success')


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
