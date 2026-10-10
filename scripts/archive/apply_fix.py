# Run from ~/EMIINING-ERP:  python apply_fix.py
import os, sys

def edit(path, old, new, count=1):
    s = open(path, encoding='utf-8').read()
    if old not in s:
        sys.exit('FAILED: expected text not found in ' + path)
    open(path, 'w', encoding='utf-8').write(s.replace(old, new, count))

if os.path.exists('scripts/dedupe_items.py'):
    sys.exit('Already applied (scripts/dedupe_items.py exists).')

# 1. service helpers
with open('services/po_service.py', 'a', encoding='utf-8') as f:
    f.write('''

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
''')

# 2. routes
edit('routes/po.py',
     'from services.po_service import create_purchase_order',
     'from services.po_service import create_purchase_order, item_usage, purge_empty_stock_rows\nfrom routes.auth import roles_required')

edit('routes/po.py',
     '        new_item = FeedIngredient(\n            name=clean_name,',
     '''        existing = FeedIngredient.query.filter(
            db.func.lower(FeedIngredient.name) == clean_name.lower()).first()
        if existing:
            # safe retry: repeated save returns the same item, no duplicate
            return jsonify({'status': 'success', 'item_id': existing.id, 'duplicate': True})
        new_item = FeedIngredient(
            name=clean_name,''')

edit('routes/po.py',
     "@po_bp.route('/api/suppliers/add', methods=['POST'])",
     '''@po_bp.route('/api/po/inventory/<int:item_id>', methods=['DELETE'])
@roles_required('admin')
def delete_item(item_id):
    item = FeedIngredient.query.get_or_404(item_id)
    usage = item_usage(item.id)
    if usage:
        detail = ', '.join(f'{t}: {n}' for t, n in usage.items())
        return jsonify(status='error',
                       message=f'Cannot delete: item has history ({detail}).'), 409
    try:
        purge_empty_stock_rows(item.id)
        db.session.delete(item)
        db.session.commit()
        return jsonify(status='success')
    except Exception as e:
        db.session.rollback()
        return jsonify(status='error', message=str(e)), 400

@po_bp.route('/api/suppliers/add', methods=['POST'])''')

# 3. dedupe script
open('scripts/dedupe_items.py', 'w', encoding='utf-8').write('''"""Remove duplicate items (same name, any case). Dry run unless --apply.
Keeps the oldest id; deletes an extra only if nothing references it."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import app
from services.db import db
from services.models import FeedIngredient
from services.po_service import item_usage, purge_empty_stock_rows

apply = '--apply' in sys.argv
with app.app_context():
    groups = {}
    for i in FeedIngredient.query.order_by(FeedIngredient.id).all():
        groups.setdefault(i.name.strip().lower(), []).append(i)
    found = False
    for name, items in groups.items():
        if len(items) < 2:
            continue
        found = True
        keep, extras = items[0], items[1:]
        print(f'"{keep.name}": keeping id {keep.id}')
        for e in extras:
            use = item_usage(e.id)
            if use:
                print(f'  id {e.id}: HAS HISTORY {use} -> not touched (merge by hand)')
            else:
                print(f'  id {e.id}: unused -> {"DELETED" if apply else "would delete"}')
                if apply:
                    purge_empty_stock_rows(e.id)
                    db.session.delete(e)
    if apply:
        db.session.commit()
    if not found:
        print('No duplicate item names.')
    elif not apply:
        print('Dry run. Re-run with --apply to delete.')
''')
print('Applied. Now press Reload on the Web tab.')
