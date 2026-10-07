"""Remove duplicate items (same name, any case). Dry run unless --apply.
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
