import getpass, sqlite3
from werkzeug.security import generate_password_hash
db = sqlite3.connect('emining_erp.db')
users = db.execute('SELECT id, username, role, location, active, locked_until FROM app_users').fetchall()
if not users:
    raise SystemExit('No users exist. Run create_user.py.')
for u in users:
    print(u)
name = input('Username to reset: ').strip().lower()
row = db.execute('SELECT role FROM app_users WHERE username=?', (name,)).fetchone()
if not row:
    raise SystemExit('No such user.')
role = row[0]
pw = getpass.getpass('New password/PIN (typing is hidden): ')
pw2 = getpass.getpass('Repeat it: ')
if pw != pw2:
    raise SystemExit('They did not match. Nothing changed.')
if role in ('sales', 'warehouse'):
    weak = {'123456', '654321', '000000', '111111', '121212', '123123'}
    ok = pw.isdigit() and 6 <= len(pw) <= 8 and pw not in weak and len(set(pw)) > 1
    rule = 'PIN must be 6 to 8 digits'
else:
    ok = len(pw) >= 10
    rule = 'password must be 10+ characters'
if not ok:
    raise SystemExit(rule + '. Nothing changed.')
db.execute('UPDATE app_users SET password_hash=?, failed_attempts=0, locked_until=NULL, active=1 WHERE username=?',
           (generate_password_hash(pw), name))
db.commit()
print('Password reset for', name)
