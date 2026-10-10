"""Populate the Item Master from the WUFFDA "Formulate" ingredient list (growers_10.xlsx).

Dry run (changes nothing):    python scripts/populate_ingredients.py
Save to the database:         python scripts/populate_ingredients.py --apply
Only the 16 Growers 10 items: add  --used-only

What it does
  * Adds each ingredient as a Raw item, with protein % and energy from the workbook.
  * Gives every outlet its own empty stock row (0 kg) for each ingredient.
  * Leaves cost at 0: the workbook prices are US dollars per cwt, not your KSh costs.
    Real costs come in when you receive stock in Purchasing.
  * Skips items you already have (matched by name). For items you already have it only
    fills in protein/energy when yours is 0; it never overwrites a number you set.
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import app
from services.db import db
from services.models import FeedIngredient, Location, LocationStock

# workbook name, % used in Growers 10, protein %, energy (Kcal/g = Mcal/kg)
INGREDIENTS = [
    ('Corn, Grain', 50.0, 8.8, 3.34),
    ('Corn germ meal, dry milled', 0.0, 11.3, 3.1),
    ('Corn germ meal, wet milled', 0.0, 11.3, 3.1),
    ('Wheat Bran', 16.0, 13.0, 2.5),
    ('Wheat Pollard', 14.0, 15.0, 2.7),
    ('Rice Bran', 0.0, 11.0, 1.8),
    ('Menhaden Meal', 3.0, 35.0, 2.82),
    ('Soybean Meal -48%', 0.0, 46.0, 2.4),
    ('Heated Soybeans', 13.0, 37.0, 3.3),
    ('Sunflower Ml-45%', 0.0, 38.0, 2.32),
    ('Canola', 0.0, 41.0, 2.85),
    ('Soybean OiL', 0.0, 0.0, 8.8),
    ('Common salt', 0.12, 0.0, 0.0),
    ('Limestone', 2.0, 0.0, 0.0),
    ('Dical. Phos.', 0.7, 0.0, 0.0),
    ('Na2SO3', 0.1, 0.0, 0.0),
    ('Vitamin Premix', 0.2, 0.0, 0.0),
    ('Mineral Premix', 0.0, 0.0, 0.0),
    ('DL-Methionine', 0.2, 58.7, 4.6),
    ('L-Lysine HCl', 0.4, 95.9, 3.3),
    ('L-Threonine', 0.15, 73.5, 3.02),
    ('L-Tryptophan', 0.0, 84.3, 5.85),
    ('Coccidiostat', 0.0, 0.0, 0.0),
    ('Choline Cl -70%', 0.0, 0.0, 0.0),
    ('Phytase', 0.0, 0.0, 0.0),
    ('Probiotics', 0.0, 0.0, 0.0),
    ('Toxin binder', 0.05, 0.0, 0.0),
    ('Enzymes', 0.1, 0.0, 0.0),
    ('zinc', 0.0, 0.0, 0.0),
    ('mg', 0.005, 0.0, 0.0),
]

# workbook name -> the name used in YOUR system (edit if you call something differently)
NAME_MAP = {
    'Corn, Grain': 'Maize',
    'zinc': 'Zinc',
    'mg': 'Mg',
}
# force a category for a name (after mapping), e.g. {'Mg': 'Raw - Mineral'}
CATEGORY_OVERRIDE = {}


def norm(s):
    return re.sub(r'[^a-z0-9]', '', str(s).lower())


def category_for(cp, me):
    if cp >= 20:
        return 'Raw - Protein'
    if me >= 1.5:
        return 'Raw - Energy'
    return 'Raw - Mineral'


apply = '--apply' in sys.argv
used_only = '--used-only' in sys.argv

with app.app_context():
    items = {norm(i.name): i for i in FeedIngredient.query.all()}
    try:
        from services.models import FormulaAlias
        aliases = {a.alias: a.ingredient_id for a in FormulaAlias.query.all()}
    except ImportError:
        aliases = {}
    by_id = {i.id: i for i in items.values()}
    outlets = Location.query.order_by(Location.id).all()
    print('Outlets that will each get a stock row:', ', '.join(o.name for o in outlets) or 'NONE FOUND')
    print()

    created = filled = skipped = 0
    touched = []
    for wname, amount, cp, me in INGREDIENTS:
        if used_only and amount <= 0:
            continue
        name = NAME_MAP.get(wname, wname).strip()
        key = norm(name)
        existing = items.get(key) or by_id.get(aliases.get(norm(wname)))
        if existing:
            if 'raw' not in (existing.category or '').lower():
                print('  SKIP     %-28s exists as "%s" but its category is "%s" (not Raw)' % (wname, existing.name, existing.category))
                skipped += 1
                continue
            notes = []
            if not existing.crude_protein_pct and cp:
                notes.append('protein %s' % cp)
                if apply:
                    existing.crude_protein_pct = cp
            if not existing.metabolizable_energy_mcal and me:
                notes.append('energy %s' % me)
                if apply:
                    existing.metabolizable_energy_mcal = me
            extra = ('  (fills ' + ', '.join(notes) + ')') if notes else ''
            print('  EXISTS   %-28s -> %s%s' % (wname, existing.name, extra))
            if notes:
                filled += 1
            touched.append(existing)
        else:
            cat = CATEGORY_OVERRIDE.get(name) or category_for(cp, me)
            print('  NEW      %-28s -> %s  [%s, protein %s, energy %s]' % (wname, name, cat, cp, me))
            created += 1
            if apply:
                item = FeedIngredient(name=name, category=cat, cost_per_kg=0.0, crude_protein_pct=cp,
                                      metabolizable_energy_mcal=me)
                db.session.add(item)
                touched.append(item)
                items[key] = item

    stock_rows = 0
    if apply:
        db.session.flush()
        for item in touched:
            for o in outlets:
                if not LocationStock.query.filter_by(location_id=o.id, ingredient_id=item.id).first():
                    db.session.add(LocationStock(location_id=o.id, ingredient_id=item.id,
                                                 quantity_kg=0.0, reserved_quantity_kg=0.0, unit_cost_per_kg=0.0))
                    stock_rows += 1
        db.session.commit()

    print()
    print('New items: %d | existing items given protein/energy: %d | skipped: %d' % (created, filled, skipped))
    if apply:
        print('Saved. Stock rows created: %d (one per outlet per item).' % stock_rows)
    else:
        print('Dry run only. Re-run with --apply to save.')