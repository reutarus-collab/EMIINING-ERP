import getpass
from app import app
from services.models import db, User

ROLES = ('admin', 'accountant', 'sales', 'warehouse')
LOCATIONS = ('factory', 'branch1')

with app.app_context():
    db.create_all()
    name = input('Username: ').strip().lower()
    full = input('Full name: ').strip()
    role = input(f'Role {ROLES}: ').strip()
    loc = input(f'Location {LOCATIONS}: ').strip()
    assert role in ROLES and loc in LOCATIONS, 'bad role or location'
    MIN_PIN = 6   # change to 4 if you insist
    PIN_ROLES = ('sales', 'warehouse')
    WEAK = {'123456', '654321', '000000', '111111', '121212', '123123', '1234', '0000', '1111'}

    pw = getpass.getpass('PIN (digits) or password: ')
    if role in PIN_ROLES:
        assert pw.isdigit() and MIN_PIN <= len(pw) <= 8, f'PIN must be {MIN_PIN}-8 digits'
        assert pw not in WEAK and len(set(pw)) > 1, 'PIN too easy to guess'
    else:
        assert len(pw) >= 10, 'admin/accountant need 10+ characters'
    if User.query.filter_by(username=name).first():
        raise SystemExit('username already exists')
    u = User(username=name, full_name=full, role=role, location=loc)
    u.set_password(pw)
    db.session.add(u)
    db.session.commit()
    print('created', name)