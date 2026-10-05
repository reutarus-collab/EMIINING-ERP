from flask import g
from services.db import db
from services.models import FeedIngredient, Location, LocationStock, TillSession


def resolve_location(requested_id=None):
    user = getattr(g, 'user', None)
    role = getattr(user, 'role', '')
    if role in ('admin', 'accountant') and requested_id not in (None, ''):
        try:
            location = db.session.get(Location, int(requested_id))
        except (TypeError, ValueError):
            location = None
    else:
        identity = str(getattr(user, 'location', '') or '').strip().lower()
        location = next((loc for loc in Location.query.all()
                         if identity in (str(loc.id).lower(), (loc.code or '').lower(),
                                         (loc.name or '').lower(), (loc.location_type or '').lower())), None)
        if not location and 'factory' in identity:
            location = next((loc for loc in Location.query.all() if 'factory' in (loc.location_type or '').lower()), None)
        if not location and ('branch' in identity or 'retail' in identity or 'outlet' in identity):
            location = next((loc for loc in Location.query.all()
                             if any(token in (loc.location_type or '').lower() for token in ('branch', 'retail', 'store'))), None)
        if not location and requested_id not in (None, '') and role in ('admin', 'accountant'):
            try:
                location = db.session.get(Location, int(requested_id))
            except (TypeError, ValueError):
                pass
    if not location and Location.query.count() == 1:
        location = Location.query.first()
    if not location:
        raise ValueError('Your user must be assigned to a valid outlet/location.')
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
    row = LocationStock(location_id=location_id, ingredient_id=ingredient_id, quantity_kg=0.0)
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
    new_qty = round((row.quantity_kg or 0.0) + delta_kg, 6)
    if new_qty < -0.000001:
        raise ValueError(f'Insufficient stock for {ingredient.name}. Available: {row.quantity_kg:.2f} kg.')
    row.quantity_kg = max(0.0, new_qty)
    # Keep the old field as an aggregate cache while location balances are authoritative.
    ingredient.stock_quantity_kg = round((ingredient.stock_quantity_kg or 0.0) + delta_kg, 6)
    db.session.add(StockMovement(ingredient_id=ingredient.id, location_id=location_id,
                                 movement_type=movement_type, qty_kg=delta_kg,
                                 reference_id=reference_id, reason=reason))
    return row


def active_till(location_id, username, lock=False):
    query = TillSession.query.filter_by(location_id=location_id, cashier_name=username, status='OPEN')
    if lock:
        query = query.with_for_update()
    return query.order_by(TillSession.id.desc()).first()
