"""Give the existing factory login the dedicated factory workspace role.

Run this once after deploying the factory workspace changes. The script only
changes the role of the `factory` login when it is assigned to a FACTORY outlet;
it never changes the user's PIN or location.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from services.db import db
from services.models import Location, User

with app.app_context():
    user = User.query.filter_by(username='factory').first()
    if not user:
        raise SystemExit('No user named factory exists. Nothing changed.')
    location = db.session.get(Location, user.location_id) if user.location_id else None
    if not location or (location.location_type or '').upper() != 'FACTORY':
        raise SystemExit('The factory login is not assigned to a FACTORY outlet. Nothing changed.')
    old_role = user.role
    user.role = 'factory'
    db.session.commit()
    print(f'Updated {user.username} at {location.name}: {old_role} -> {user.role}. PIN and outlet are unchanged.')
