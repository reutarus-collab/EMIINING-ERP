from flask import g
from services.db import db
from services.models import FeedIngredient, Location, LocationStock, TillSession
from sqlalchemy import update


def resolve_location(requested_id=None):
    user = getattr(g, 'user', None)
    role = getattr(user, 'role', '')
    if role in ('admin', 'accountant'):
        identity = requested_id if requested_id not in (None, '') else getattr(user, 'location_id', None)
        try:
            location = db.session.get(Location, int(identity)) if identity is not None else None
        except (TypeError, ValueError):
            location = None
    else:
        location_id = getattr(user, 'location_id', None)
        location = db.session.get(Location, location_id) if location_id is not None else None
    if not location:
        raise ValueError('Your user must be assigned to a specific valid outlet by an administrator.')
    return location


def location_stock(location_id, ingredient_id, lock=False):
    query = LocationStock.query.filter_by(location_id=location_id, ingredient_id=ingredient_id)
    if lock:
        query = query.with_for_update()
    row = query.first()
    if row:
        return row
    item = db.session.get(FeedIngredient, ingredient_id)
    if not item:
        raise ValueError('Inventory item not found.')
    row = LocationStock(location_id=location_id, ingredient_id=ingredient_id, quantity_kg=0.0,
                       unit_cost_per_kg=item.cost_per_kg or 0.0)
    db.session.add(row)
    db.session.flush()
    return row


def stock_quantity(location_id, ingredient_id):
    row = LocationStock.query.filter_by(location_id=location_id, ingredient_id=ingredient_id).first()
    return (row.quantity_kg or 0.0) if row else 0.0


def reserved_quantity(location_id, ingredient_id):
    row = LocationStock.query.filter_by(location_id=location_id, ingredient_id=ingredient_id).first()
    return (row.reserved_quantity_kg or 0.0) if row else 0.0


def change_stock(location_id, ingredient, delta_kg, movement_type, reference_id=None, reason=None):
    from services.models import StockMovement
    row = location_stock(location_id, ingredient.id, lock=True)
    delta_kg = float(delta_kg)
    if delta_kg < 0:
        # A conditional database update prevents two SQLite requests from both
        # passing a stale read and overselling the same outlet's balance.
        result = db.session.execute(
            update(LocationStock)
            .where(LocationStock.id == row.id,
                   LocationStock.quantity_kg + 0.000001 >= -delta_kg,
                   LocationStock.quantity_kg - LocationStock.reserved_quantity_kg + 0.000001 >= -delta_kg)
            .values(quantity_kg=LocationStock.quantity_kg + delta_kg)
            .execution_options(synchronize_session='fetch')
        )
        if result.rowcount != 1:
            raise ValueError(f'Insufficient stock for {ingredient.name}. Available: {row.quantity_kg:.2f} kg.')
    else:
        row.quantity_kg = round((row.quantity_kg or 0.0) + delta_kg, 6)
    row = db.session.get(LocationStock, row.id)
    if row.quantity_kg is not None and row.quantity_kg < 0:
        raise ValueError(f'Insufficient stock for {ingredient.name}.')
    # Rebuild legacy aggregate fields from outlet balances; outlet valuation is
    # authoritative and the aggregate cost is only a weighted summary.
    refresh_global_valuation(ingredient)
    db.session.add(StockMovement(ingredient_id=ingredient.id, location_id=location_id,
                                 movement_type=movement_type, qty_kg=delta_kg,
                                 reference_id=reference_id, reason=reason))
    return row


def refresh_global_valuation(ingredient):
    rows = LocationStock.query.filter_by(ingredient_id=ingredient.id).all()
    total_qty = sum(max(0.0, row.quantity_kg or 0.0) for row in rows)
    total_value = sum(max(0.0, row.quantity_kg or 0.0) * max(0.0, row.unit_cost_per_kg or 0.0)
                      for row in rows)
    ingredient.stock_quantity_kg = round(total_qty, 6)
    if total_qty > 0:
        ingredient.cost_per_kg = round(total_value / total_qty, 6)


def receive_stock(location_id, ingredient, quantity_kg, unit_cost_per_kg,
                  movement_type, reference_id=None, reason=None):
    """Receive stock and value it at this location's weighted-average cost."""
    quantity_kg = float(quantity_kg)
    unit_cost_per_kg = float(unit_cost_per_kg or 0.0)
    if quantity_kg <= 0 or unit_cost_per_kg < 0:
        raise ValueError('Received quantity and unit cost must be valid.')
    row = location_stock(location_id, ingredient.id, lock=True)
    old_qty = max(0.0, row.quantity_kg or 0.0)
    old_cost = max(0.0, row.unit_cost_per_kg or 0.0)
    change_stock(location_id, ingredient, quantity_kg, movement_type, reference_id, reason)
    new_qty = old_qty + quantity_kg
    row.unit_cost_per_kg = round(((old_qty * old_cost) + (quantity_kg * unit_cost_per_kg)) / new_qty, 6)
    refresh_global_valuation(ingredient)
    return row


def active_till(location_id, username, lock=False):
    query = TillSession.query.filter_by(location_id=location_id, cashier_name=username, status='OPEN')
    if lock:
        query = query.with_for_update()
    return query.order_by(TillSession.id.desc()).first()
