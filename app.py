import os

from flask import Flask, jsonify, render_template, request
from flask_migrate import Migrate

from services.db import db, init_db
from services.models import Location
from routes.auth import auth_bp, init_auth, roles_required
from routes.retail import retail_bp
from routes.payables import payables_bp
from routes.factory import factory_bp
from routes.pos import pos_bp
from routes.po import po_bp
from routes.reports import reports_bp


def create_app(config=None):
    app = Flask(__name__)
    base_dir = os.path.abspath(os.path.dirname(__file__))
    app.config.update(
        SECRET_KEY=os.environ.get('SECRET_KEY'),
        SQLALCHEMY_DATABASE_URI=os.getenv(
            'DATABASE_URL', 'sqlite:///' + os.path.join(base_dir, 'emining_erp.db')),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    if config:
        app.config.update(config)
    if not app.config.get('SECRET_KEY'):
        raise RuntimeError('Set SECRET_KEY before starting the ERP.')

    init_db(app)
    Migrate(app, db)
    app.register_blueprint(auth_bp)
    app.register_blueprint(retail_bp)
    app.register_blueprint(payables_bp)
    app.register_blueprint(factory_bp)
    app.register_blueprint(pos_bp)
    app.register_blueprint(po_bp)
    app.register_blueprint(reports_bp)
    init_auth(app)

    @app.route('/')
    def dashboard():
        return render_template('dashboard.html')

    @app.route('/api/locations', methods=['GET'])
    def get_locations():
        locations = Location.query.order_by(Location.name).all()
        return jsonify([{'id': loc.id, 'name': loc.name, 'code': loc.code,
                         'type': loc.location_type} for loc in locations])

    @app.route('/api/admin/locations', methods=['POST'])
    @roles_required('admin')
    def create_location():
        data = request.get_json(silent=True) or {}
        name = str(data.get('name') or '').strip()
        code = str(data.get('code') or '').strip().upper()
        kind = str(data.get('type') or '').strip().upper()
        if (not name or len(name) > 100 or not code or len(code) > 20
                or kind not in ('FACTORY', 'BRANCH_STORE', 'RETAIL_OUTLET', 'WAREHOUSE')
                or any(c in name + code for c in '<>')):
            return jsonify(status='error', message='Enter a valid outlet name, unique code, and supported type.'), 400
        if Location.query.filter(db.func.lower(Location.code) == code.lower()).first():
            return jsonify(status='error', message='That location code already exists.'), 400
        loc = Location(name=name, code=code, location_type=kind)
        db.session.add(loc)
        db.session.commit()
        return jsonify(status='success', id=loc.id, name=loc.name, code=loc.code, type=loc.location_type)

    return app


app = create_app()

if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', host='127.0.0.1')
