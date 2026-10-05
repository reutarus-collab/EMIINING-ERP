import math
import uuid
from datetime import datetime

from flask import Blueprint, g, jsonify, request

from routes.auth import roles_required
from services.db import db
from services.models import (
    FeedIngredient,
    ProductionRun,
    ProductionRunLine,
    StockMovement,
)
from services.inventory import resolve_location, location_stock, change_stock, receive_stock

factory_bp = Blueprint('factory', __name__)


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
@roles_required('admin', 'accountant', 'warehouse')
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
@roles_required('admin', 'accountant', 'warehouse')
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
@roles_required('admin', 'accountant', 'warehouse')
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
