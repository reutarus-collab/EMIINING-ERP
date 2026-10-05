import uuid
import math
from services.db import db
from services.models import PurchaseOrderHeader, PurchaseOrderLine, FeedIngredient, Supplier
from services.inventory import resolve_location

def create_purchase_order(data):
    """Stage 1: Commitment (No Stock or GL Impact)"""
    if not isinstance(data, dict):
        raise ValueError('Invalid purchase order.')
    location = resolve_location(data.get('location_id'))
    supplier_id = int(data.get('supplier_id'))
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
    for item in items:
        qty, unit_cost = float(item['qty']), float(item['unit_cost'])
        ingredient_id = int(item['ingredient_id'])
        if not math.isfinite(qty) or not math.isfinite(unit_cost) or qty <= 0 or unit_cost < 0:
            raise ValueError('PO quantities must be above zero and costs must be zero or more.')
        if not db.session.get(FeedIngredient, ingredient_id):
            raise ValueError('A purchase order item no longer exists.')
        subtotal = qty * unit_cost
        total += subtotal
        line = PurchaseOrderLine(
            po_header_id=po.id,
            ingredient_id=ingredient_id,
            qty_ordered=qty,
            unit_cost=unit_cost,
            subtotal=subtotal
        )
        db.session.add(line)
    
    po.total_amount = total
    db.session.commit()
    return po.po_number
