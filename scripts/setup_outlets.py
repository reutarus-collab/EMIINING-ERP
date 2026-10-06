"""One-time setup for an EMPTY database: turn the placeholder outlet into the factory
and add the Kambi ya Moto and Mogotio branches. Edit the names below if needed.

Run from the project folder:  python scripts/setup_outlets.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FACTORY = {'name': 'Emining Main Factory', 'code': 'FAC-01', 'location_type': 'FACTORY'}
BRANCHES = (
    {'name': 'Kambi ya Moto Branch', 'code': 'KYM-01', 'location_type': 'BRANCH_STORE'},
    {'name': 'Mogotio Branch', 'code': 'MOG-01', 'location_type': 'BRANCH_STORE'},
)

from app import app                                   # noqa: E402
from services.db import db                            # noqa: E402
from services.models import GeneralLedgerEntry, Location, LocationStock, OrderHeader  # noqa: E402

with app.app_context():
    outlets = Location.query.all()
    if len(outlets) != 1 or outlets[0].code != 'LEGACY-01':
        raise SystemExit('Stopped: outlets are already set up. Nothing changed.')
    if LocationStock.query.count() or OrderHeader.query.count() or GeneralLedgerEntry.query.count():
        raise SystemExit('Stopped: this database already has stock or sales. Nothing changed.')
    placeholder = outlets[0]
    for key, value in FACTORY.items():
        setattr(placeholder, key, value)
    for branch in BRANCHES:
        db.session.add(Location(**branch))
    db.session.commit()
    for loc in Location.query.order_by(Location.id):
        print(f'{loc.id}: {loc.name} ({loc.code}, {loc.location_type})')
    print('Outlets ready.')
