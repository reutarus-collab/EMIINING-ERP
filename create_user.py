import getpass
from app import app
from services.models import db, User, Location

ROLES = ('admin', 'accountant', 'sales', 'warehouse', 'factory')
with app.app_context():
    db.create_all()
    name = input('Username: ').strip().lower()
    full = input('Full name: ').strip()
    role = input(f'Role {ROLES}: ').strip()
    locations = Location.query.order_by(Location.name).all()
    if not locations:
        raise SystemExit('No outlets exist. Create an outlet before adding location-scoped staff.')
    for location in locations:
        print(f'{location.id}: {location.name} ({location.code})')
    raw_location_id = input('Location ID: ').strip()
    try:
        location = db.session.get(Location, int(raw_location_id))
    except ValueError:
        location = None
    assert role in ROLES and (role in ('admin', 'accountant') or location), 'choose a valid role and exact outlet ID'
    pw = getpass.getpass('Four-digit PIN: ')
    assert pw.isdigit() and len(pw) == 4, 'PIN must be exactly 4 digits'
    if User.query.filter_by(username=name).first():
        raise SystemExit('username already exists')
    u = User(username=name, full_name=full, role=role,
             location=location.code if location else 'admin',
             location_id=location.id if location else None)
    u.set_password(pw)
    db.session.add(u)
    db.session.commit()
    print('created', name)
