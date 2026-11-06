"""Recent net sales volume used to put popular products first in item lists."""
from datetime import datetime, timedelta

from services.models import db, FeedIngredient, OrderHeader, OrderLine, SalesRefund, SalesRefundLine


def sales_rank(location_id=None, days=30):
    """Return ingredient id -> net kg sold in the recent window, with one bounded query set."""
    since = datetime.utcnow() - timedelta(days=days)
    query = (db.session.query(OrderLine.ingredient_id, OrderLine.qty_kg, OrderLine.qty_entered,
                              OrderLine.unit_type, FeedIngredient.bag_size_kg)
             .join(OrderHeader, OrderHeader.id == OrderLine.order_id)
             .join(FeedIngredient, FeedIngredient.id == OrderLine.ingredient_id)
             .filter(OrderHeader.created_at >= since, OrderHeader.status == 'COMPLETED'))
    if location_id is not None:
        query = query.filter(OrderHeader.location_id == location_id)
    totals = {}
    for ingredient_id, qty_kg, qty, unit_type, default_bag in query.all():
        kg = qty_kg
        if kg is None:
            unit = (unit_type or '').upper().strip()
            size = 1.0
            if unit.endswith('KG BAG'):
                try:
                    size = float(unit[:-len('KG BAG')].strip())
                except ValueError:
                    size = float(default_bag or 50.0)
            elif unit in ('BAG', 'BAGS'):
                size = float(default_bag or 50.0)
            kg = float(qty or 0.0) * size
        totals[ingredient_id] = totals.get(ingredient_id, 0.0) + float(kg or 0.0)

    refunds = (db.session.query(SalesRefundLine.ingredient_id, db.func.sum(SalesRefundLine.quantity_kg))
               .join(SalesRefund, SalesRefund.id == SalesRefundLine.refund_id)
               .filter(SalesRefund.created_at >= since))
    if location_id is not None:
        refunds = refunds.filter(SalesRefund.location_id == location_id)
    for ingredient_id, kg in refunds.group_by(SalesRefundLine.ingredient_id).all():
        totals[ingredient_id] = max(0.0, totals.get(ingredient_id, 0.0) - float(kg or 0.0))
    return totals
