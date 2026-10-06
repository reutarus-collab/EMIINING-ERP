"""One-time load of your physical stock count into an EMPTY database.

CSV columns (header row required):  item,category,outlet_code,kg,cost_per_kg
Optional column: bag_kg (default 70). Items that do not exist yet are created.

  python scripts/load_opening_stock.py opening_stock.csv            (preview only)
  python scripts/load_opening_stock.py opening_stock.csv --apply    (writes it)

Opening stock is NOT posted to the ledger; it is entered as the starting inventory
in Money & Leaks > opening balances. Refuses to run twice or on a database that
already has sales or ledger entries.
"""
import csv
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app                                   # noqa: E402
from services.db import db                            # noqa: E402
from services.inventory import receive_stock          # noqa: E402
from services.models import (FeedIngredient, GeneralLedgerEntry, Location,  # noqa: E402
                             OrderHeader, StockMovement)

REF = 'OPENING-STOCK'


def number(row, key, line):
    try:
        value = float(row.get(key, ''))
    except ValueError:
        raise SystemExit(f'Line {line}: "{key}" must be a number.')
    if not math.isfinite(value):
        raise SystemExit(f'Line {line}: "{key}" is not valid.')
    return value


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    apply = '--apply' in sys.argv
    if len(args) != 1:
        raise SystemExit('Usage: python scripts/load_opening_stock.py opening_stock.csv [--apply]')
    with open(args[0], newline='', encoding='utf-8-sig') as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit('The CSV has no rows.')

    with app.app_context():
        if StockMovement.query.filter_by(reference_id=REF).first():
            raise SystemExit('Stopped: opening stock was already loaded. Nothing changed.')
        if OrderHeader.query.count() or GeneralLedgerEntry.query.count():
            raise SystemExit('Stopped: this database already has sales or ledger entries. Nothing changed.')
        outlets = {l.code.upper(): l for l in Location.query.all()}
        plan, total = [], 0.0
        for n, row in enumerate(rows, start=2):
            name = (row.get('item') or '').strip()
            code = (row.get('outlet_code') or '').strip().upper()
            kg, cost = number(row, 'kg', n), number(row, 'cost_per_kg', n)
            bag = number({'bag_kg': row.get('bag_kg') or '70'}, 'bag_kg', n)
            if not name or len(name) > 100 or any(c in name for c in '<>'):
                raise SystemExit(f'Line {n}: invalid item name.')
            if code not in outlets:
                raise SystemExit(f'Line {n}: unknown outlet code "{code}". Known: {", ".join(outlets)}')
            if kg <= 0 or cost <= 0 or bag <= 0:
                raise SystemExit(f'Line {n}: kg, cost_per_kg and bag_kg must be above zero.')
            plan.append((name, (row.get('category') or '').strip()[:50], outlets[code], kg, cost, bag))
            total += kg * cost

        print(f'{"Item":32} {"Outlet":10} {"kg":>10} {"cost/kg":>9} {"value":>12}')
        for name, cat, loc, kg, cost, bag in plan:
            print(f'{name[:32]:32} {loc.code:10} {kg:>10,.1f} {cost:>9,.2f} {kg * cost:>12,.2f}')
        print(f'{"TOTAL STOCK VALUE":53} {total:>12,.2f}')
        if not apply:
            print('\nPreview only. Check the numbers, then add --apply.')
            return

        for name, cat, loc, kg, cost, bag in plan:
            item = FeedIngredient.query.filter(db.func.lower(FeedIngredient.name) == name.lower()).first()
            if not item:
                item = FeedIngredient(name=name, category=cat, bag_size_kg=bag, cost_per_kg=cost)
                db.session.add(item)
                db.session.flush()
            receive_stock(loc.id, item, kg, cost, 'OPENING_STOCK', REF, 'Opening stock count')
        db.session.commit()
        print('\nOpening stock loaded. Use the total above as "Stock at cost" in Money & Leaks > opening balances.')


main()
