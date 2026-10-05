import os, uuid
from flask import Flask, render_template, request, jsonify
from flask_migrate import Migrate
from services.db import db, init_db
from services.models import Location
from routes.auth import auth_bp, init_auth, roles_required
from routes.retail import retail_bp
from routes.payables import payables_bp
from routes.factory import factory_bp

# 1. Import our new helpers and blueprints
from routes.pos import pos_bp
# top of file
import os

# where the config is
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ['SECRET_KEY']
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///' + os.path.join(BASE_DIR, 'emining_erp.db'))
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

init_db(app)
migrate = Migrate(app, db)
app.register_blueprint(auth_bp)
app.register_blueprint(retail_bp)
app.register_blueprint(payables_bp)
app.register_blueprint(factory_bp)
init_auth(app)
app.register_blueprint(pos_bp)
from routes.po import po_bp
app.register_blueprint(po_bp)
@app.route('/')
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/locations', methods=['GET'])
def get_locations():
    locations = Location.query.all()
    return jsonify([{'id': l.id, 'name': l.name, 'code': l.code, 'type': l.location_type} for l in locations])

@app.route('/api/admin/locations', methods=['POST'])
@roles_required('admin')
def create_location():
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()
    code = str(data.get('code') or '').strip().upper()
    kind = str(data.get('type') or '').strip().upper()
    if not name or len(name) > 100 or not code or len(code) > 20 or kind not in ('FACTORY', 'BRANCH_STORE', 'RETAIL_OUTLET', 'WAREHOUSE') or any(c in name + code for c in '<>'):
        return jsonify(status='error', message='Enter a valid outlet name, unique code, and supported type.'), 400
    if Location.query.filter(db.func.lower(Location.code) == code.lower()).first():
        return jsonify(status='error', message='That location code already exists.'), 400
    loc = Location(name=name, code=code, location_type=kind)
    db.session.add(loc)
    db.session.commit()
    return jsonify(status='success', id=loc.id, name=loc.name, code=loc.code, type=loc.location_type)
# ==========================================
# FORMULATOR & FACTORY PRODUCTION (Target for next split)
# ==========================================
@app.route('/api/formulate', methods=['POST'])
def run_formulation():
    return jsonify({"status": "error", "message": "Ration formulation is not available in the ERP yet."}), 501
@app.route('/api/factory/mill', methods=['POST'])
def process_milling():
    return jsonify({"status": "error", "message": "Milling is not available in the ERP yet. Nothing was recorded."}), 501
if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', host='127.0.0.1')
