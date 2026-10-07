"""Save the Growers 10 formula (fixed percentages) from the WUFFDA workbook.
Dry run by default:   python scripts/seed_growers10.py
Save it:              python scripts/seed_growers10.py --apply
MAP below turns the workbook ingredient name into the EXACT name of your own item
(Purchasing > Item Master). Edit it until the dry run shows no MISSING lines."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import app
from services.db import db
from services.models import FeedIngredient, SavedFormula, SavedFormulaLine

NAME = 'Growers 10'
RECIPE = [  # workbook ingredient, % of the mix
    ('Corn, Grain', 50), ('Wheat Bran', 16), ('Wheat Pollard', 14), ('Menhaden Meal', 3),
    ('Heated Soybeans', 13), ('Common salt', 0.12), ('Limestone', 2), ('Dical. Phos.', 0.70),
    ('Na2SO3', 0.10), ('Vitamin Premix', 0.20), ('DL-Methionine', 0.20), ('L-Lysine HCl', 0.40),
    ('L-Threonine', 0.15), ('Toxin binder', 0.05), ('Enzymes', 0.10), ('mg', 0.005),
]
MAP = {  # workbook name -> your item name  (CHECK THESE)
    'Corn, Grain': 'Maize',
    'Menhaden Meal': 'Fresh Water Shrimp',
    'Heated Soybeans': 'Soybeans',
}

apply = '--apply' in sys.argv
with app.app_context():
    items = {i.name.strip().lower(): i for i in FeedIngredient.query.all()}
    resolved, missing = [], []
    for wname, pct in RECIPE:
        mine = MAP.get(wname, wname)
        item = items.get(mine.strip().lower())
        if item is None or 'raw' not in (item.category or '').lower():
            why = 'not a Raw item' if item else 'no item with this name'
            missing.append((wname, mine, why))
            print('  MISSING  %-18s %-8s -> %s (%s)' % (wname, pct, mine, why))
        else:
            resolved.append((item, pct))
            print('  ok       %-18s %-8s -> %s' % (wname, pct, item.name))
    print('Total %%: %.3f' % sum(p for _, p in RECIPE))
    if missing:
        print('\nNothing saved. Fix the MISSING lines: edit MAP in this file, or add the item as Raw.')
        print('Your Raw items:', ', '.join(sorted(i.name for i in items.values() if 'raw' in (i.category or '').lower())))
        sys.exit(1)
    if SavedFormula.query.filter(db.func.lower(SavedFormula.name) == NAME.lower()).first():
        sys.exit('A formula called "%s" already exists. Nothing changed.' % NAME)
    if not apply:
        sys.exit('\nDry run only. Re-run with --apply to save.')
    fm = SavedFormula(name=NAME, notes='From WUFFDA workbook growers_10.xlsx (fixed percentages)')
    db.session.add(fm)
    db.session.flush()
    for item, pct in resolved:
        db.session.add(SavedFormulaLine(formula_id=fm.id, ingredient_id=item.id, pct=pct))
    db.session.commit()
    print('Saved "%s".' % NAME)
