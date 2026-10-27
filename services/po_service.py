import uuid
import math
from services.db import db
from services.models import PurchaseOrderHeader, PurchaseOrderLine, FeedIngredient, Supplier
from services.inventory import resolve_location
from services.uom import rule_for_item, price_per_kg, check_price

def create_purchase_order(data):
    """Stage 1: Commitment (No Stock or GL Impact).

    qty is a count of the item's purchase unit (BAG/KG/TON) and unit_cost is the price of ONE such unit.
    The unit and its kg conversion are read from the item master on the server and written onto each PO
    line, so nothing the browser sends can change what 10 BAG means."""
    if not isinstance(data, dict):
        raise ValueError('Invalid purchase order.')
    location = resolve_location(data.get('location_id'))
    try:
        supplier_id = int(data.get('supplier_id'))
    except (TypeError, ValueError):
        raise ValueError('Choose a valid supplier.')
    if not db.session.get(Supplier, supplier_id):
        raise ValueError('Choose a valid supplier.')
    items = data.get('items')
    if not isinstance(items, list) or not items:
        raise ValueError('Add at least one item to the purchase order.')
    po = PurchaseOrderHeader(
        po_number=f"PO-{uuid.uuid4().hex[:6].upper()}",
        supplier_id=supplier_id,
        location_id=str(location.id),
        payment_terms=data.get('payment_terms', 'Cash'),
        status='APPROVED' # Skipping DRAFT for immediate workflow
    )
    db.session.add(po)
    db.session.flush()

    total = 0.0
    seen = set()
    for item in items:
        try:
            qty, unit_cost = float(item['qty']), float(item['unit_cost'])
            ingredient_id = int(item['ingredient_id'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('Every PO line needs an item, a quantity and a unit cost.')
        if not math.isfinite(qty) or not math.isfinite(unit_cost) or qty <= 0 or unit_cost < 0:
            raise ValueError('PO quantities must be above zero and costs must be zero or more.')
        ing = db.session.get(FeedIngredient, ingredient_id)
        if not ing:
            raise ValueError('A purchase order item no longer exists.')
        if ingredient_id in seen:
            raise ValueError(f'{ing.name} is on this order twice. Combine it into one line.')
        seen.add(ingredient_id)
        uom, ctype, factor = rule_for_item(ing)
        sent = str(item.get('uom') or '').strip().upper()
        if sent and sent != uom:
            raise ValueError(f'{ing.name} is now bought per {uom}, but this screen had {sent}. Reload the page and re-enter the line.')
        if not item.get('price_confirmed'):
            check_price(ing.name, price_per_kg(factor, unit_cost), ing.cost_per_kg, uom, factor)
        subtotal = round(qty * unit_cost, 2)
        total += subtotal
        db.session.add(PurchaseOrderLine(
            po_header_id=po.id, ingredient_id=ingredient_id, qty_ordered=qty, unit_cost=unit_cost,
            subtotal=subtotal, purchase_uom=uom, conversion_type=ctype, conversion_factor=factor))

    po.total_amount = round(total, 2)
    db.session.commit()
    return po.po_number


def item_usage(item_id):
    """Return {table: row_count} for every table that references this item."""
    from sqlalchemy import inspect, text
    from services.db import db
    insp = inspect(db.engine)
    usage = {}
    for table in insp.get_table_names():
        for fk in insp.get_foreign_keys(table):
            if fk.get('referred_table') == 'feed_ingredients':
                col = fk['constrained_columns'][0]
                extra = ''
                if table == 'location_stocks':
                    # boot-time backfill creates empty 0-kg rows; those are not history
                    extra = ' AND (quantity_kg <> 0 OR reserved_quantity_kg <> 0)'
                n = db.session.execute(
                    text(f'SELECT COUNT(*) FROM "{table}" WHERE "{col}" = :i{extra}'),
                    {'i': item_id}).scalar()
                if n:
                    usage[table] = usage.get(table, 0) + n
    return usage


def purge_empty_stock_rows(item_id):
    """Remove empty 0-kg stock rows so an unused item can be deleted."""
    from services.models import LocationStock
    LocationStock.query.filter_by(ingredient_id=item_id, quantity_kg=0.0,
                                  reserved_quantity_kg=0.0).delete()
