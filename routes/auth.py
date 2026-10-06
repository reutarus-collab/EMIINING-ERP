import os
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import (Blueprint, request, session, redirect, url_for,
                   jsonify, g, abort, render_template_string)
from services.models import db, User, Location

auth_bp = Blueprint('auth', __name__)

OPEN_PATHS = ('/login', '/manifest.json', '/sw.js')
FAILS = {}
MAX_FAILS = 5
WINDOW = 600  # seconds

ROLE_ALLOW = {
    'sales': ('/', '/api/me', '/api/customers', '/api/inventory',
              '/api/locations', '/api/products', '/api/pos', '/api/sales-history',
              '/api/stock', '/api/reports', '/api/till', '/api/expenses'),
    'warehouse': ('/', '/api/me', '/api/inventory', '/api/locations', '/api/products',
                  '/api/po', '/api/suppliers', '/api/factory', '/api/stock', '/api/expenses'),
}

LOGIN_HTML = """<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Emining ERP</title>
<body style="font-family:sans-serif;max-width:320px;margin:15vh auto;padding:0 16px">
<h2>Emining ERP</h2>
{% if error %}<p style="color:#b00">{{ error }}</p>{% endif %}
<form method="post">
<input name="username" placeholder="Username" autocomplete="username" required
 style="width:100%;padding:10px;margin:6px 0"><br>
<input name="password" type="password" placeholder="4-digit PIN"
 autocomplete="current-password" required style="width:100%;padding:10px;margin:6px 0"><br>
<button style="width:100%;padding:10px">Log in</button>
</form>
</body>"""


def _ip():
    # X-Forwarded-For is supplied by the client unless a trusted proxy has
    # explicitly normalized it. Use the socket peer for this in-app throttle.
    return request.remote_addr or 'unknown'


def _blocked(ip):
    now = time.time()
    FAILS[ip] = [t for t in FAILS.get(ip, []) if now - t < WINDOW]
    return len(FAILS[ip]) >= MAX_FAILS


def _allowed(role, path):
    if role in ('admin', 'accountant'):
        return True
    for p in ROLE_ALLOW.get(role, ()):
        if p == '/':
            if path == '/':
                return True
        elif path == p or path.startswith(p + '/'):
            return True
    return False


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        ip = _ip()
        if _blocked(ip):
            error = 'Too many attempts. Wait 10 minutes.'
        else:
            name = request.form.get('username', '').strip().lower()
            pw = request.form.get('password', '')
            u = User.query.filter_by(username=name).first()
            now = datetime.utcnow()
            if u and u.locked_until and u.locked_until > now:
                error = 'Account locked. Try again in 15 minutes.'
            elif u and u.active and u.check_password(pw):
                if u.role in ('sales', 'warehouse') and not (pw.isdigit() and (len(pw) == 4 or 6 <= len(pw) <= 8)):
                    error = 'Cashier and warehouse users sign in with a four-digit PIN. Ask an admin to reset yours.'
                else:
                    u.failed_attempts = 0
                    u.locked_until = None
                    db.session.commit()
                    session.clear()
                    session['user_id'] = u.id
                    session.permanent = True
                    nxt = request.args.get('next', '/')
                    if not nxt.startswith('/') or nxt.startswith('//'):
                        nxt = '/'
                    return redirect(nxt)
            else:
                if u:
                    u.failed_attempts = (u.failed_attempts or 0) + 1
                    if u.failed_attempts >= 5:
                        u.locked_until = now + timedelta(minutes=15)
                        u.failed_attempts = 0
                    db.session.commit()
                FAILS.setdefault(ip, []).append(time.time())
                error = 'Wrong username or password.'
    return render_template_string(LOGIN_HTML, error=error)


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


@auth_bp.route('/api/me')
def me():
    return jsonify(username=g.user.username, role=g.user.role,
                   location=g.user.location, location_id=g.user.location_id)


def roles_required(*roles):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **kw):
            if g.user.role not in roles:
                abort(403)
            return f(*a, **kw)
        return wrapper
    return deco

@auth_bp.route('/api/admin/users', methods=['GET'])
@roles_required('admin')
def list_users_for_location_admin():
    return jsonify([{'id': u.id, 'username': u.username, 'full_name': u.full_name or '',
                     'role': u.role, 'location': u.location, 'active': bool(u.active)}
                    for u in User.query.order_by(User.username).all()])

@auth_bp.route('/api/admin/users/<int:user_id>/location', methods=['POST'])
@roles_required('admin')
def assign_user_location(user_id):
    data = request.get_json(silent=True) or {}
    user = db.session.get(User, user_id)
    try:
        location = db.session.get(Location, int(data.get('location_id')))
    except (TypeError, ValueError):
        location = None
    if not user or not location:
        return jsonify(status='error', message='Choose a valid user and outlet.'), 404
    user.location = location.code or str(location.id)
    user.location_id = location.id
    db.session.commit()
    return jsonify(status='success', username=user.username, location=location.name)


def init_auth(app):
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE') == '1',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )

    @app.before_request
    def gate():
        if request.path in OPEN_PATHS or request.path.startswith('/static/'):
            return
        uid = session.get('user_id')
        u = db.session.get(User, uid) if uid else None
        if not u or not u.active:
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify(error='auth_required'), 401
            return redirect(url_for('auth.login', next=request.path))
        g.user = u
        if not _allowed(u.role, request.path):
            if request.path.startswith('/api/'):
                return jsonify(error='forbidden'), 403
            abort(403)
