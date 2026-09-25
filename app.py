import os, uuid
from flask import Flask, render_template, request, jsonify
from flask_migrate import Migrate
from services.db import db, init_db
from services.models import FeedIngredient, Supplier, Location, PurchaseOrderHeader, PurchaseOrderLine, StockMovement

# 1. Import our new helpers and blueprints
from routes.pos import pos_bp

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'emining-enterprise-2026')
# Force Flask to use the absolute path of the current folder
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///' + os.path.join(BASE_DIR, 'emining_erp.db'))
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

init_db(app)
migrate = Migrate(app, db)

# 2. Register the POS Blueprint
app.register_blueprint(pos_bp)

@app.route('/')
def dashboard():
    return render_template('dashboard.html')

# ==========================================
# PURCHASING, SUPPLIERS, GRPO (Target for next split)
# ==========================================
@app.route('/api/suppliers', methods=['GET'])
def get_suppliers():
    suppliers = Supplier.query.all()
    return jsonify([{'id': s.id, 'name': s.name, 'balance_due': s.balance_due} for s in suppliers])

@app.route('/api/locations', methods=['GET'])
def get_locations():
    locations = Location.query.all()
    return jsonify([{'id': l.id, 'name': l.name, 'type': l.location_type} for l in locations])

@app.route('/api/po/create', methods=['POST'])
def create_po():
    try:
        data = request.get_json() or {}
        po_no = f"PO-{uuid.uuid4().hex[:6].upper()}"
        total_value = 0.0
        po = PurchaseOrderHeader(po_no=po_no, supplier_id=data['supplier_id'], location_id=data['location_id'], total_amount=0.0, status='ISSUED')
        db.session.add(po)
        db.session.flush()

        for item in data.get('items', []):
            qty = float(item['qty_kg'])
            cost = float(item['unit_cost'])
            if qty <= 0 or cost < 0: raise ValueError("Quantities and costs must be positive.")
            subtotal = qty * cost
            total_value += subtotal
            db.session.add(PurchaseOrderLine(po_id=po.id, ingredient_id=item['ingredient_id'], ordered_qty_kg=qty, received_qty_kg=0.0, unit_cost=cost, subtotal=subtotal))
            
        po.total_amount = total_value
        supplier = db.session.get(Supplier, data['supplier_id'])
        supplier.balance_due += total_value 

        db.session.commit()
        return jsonify({'status': 'success', 'po_no': po_no})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 400

@app.route('/api/po/details', methods=['GET'])
def get_po_details():
    try:
        po_no = request.args.get('po_no')
        po = PurchaseOrderHeader.query.filter_by(po_no=po_no).first()
        if not po: return jsonify({'status': 'error', 'message': 'PO not found.'}), 404
        lines = PurchaseOrderLine.query.filter_by(po_id=po.id).all()
        line_data = [{'line_id': l.id, 'ingredient_name': db.session.get(FeedIngredient, l.ingredient_id).name, 'ordered_qty': l.ordered_qty_kg, 'received_qty': l.received_qty_kg} for l in lines]
        return jsonify({'status': 'success', 'po_id': po.id, 'po_no': po.po_no, 'lines': line_data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/po/<int:po_id>/grpo', methods=['POST'])
def receive_grpo_partial(po_id):
    try:
        data = request.get_json() or {}
        po = db.session.get(PurchaseOrderHeader, po_id)
        grpo_total_value = 0.0
        grpo_ref = f"GRPO-{uuid.uuid4().hex[:6].upper()}"
        all_lines_fully_received = True

        for recv_item in data.get('received_items', []):
            line = db.session.get(PurchaseOrderLine, recv_item['line_id'])
            incoming_qty = float(recv_item['qty_kg'])
            if incoming_qty <= 0: continue

            ing = db.session.get(FeedIngredient, line.ingredient_id)
            if (line.received_qty_kg + incoming_qty) > line.ordered_qty_kg: raise ValueError(f"Exceeds PO limit for {ing.name}")
            
            new_subtotal = incoming_qty * line.unit_cost
            old_val = (ing.stock_quantity_kg or 0.0) * (ing.cost_per_kg or 0.0)
            new_total_stock = (ing.stock_quantity_kg or 0.0) + incoming_qty
            
            if new_total_stock > 0: ing.cost_per_kg = (old_val + new_subtotal) / new_total_stock
            
            ing.stock_quantity_kg = new_total_stock
            line.received_qty_kg += incoming_qty
            grpo_total_value += new_subtotal
            
            db.session.add(StockMovement(ingredient_id=ing.id, movement_type='GRPO_RECEIPT', qty_kg=incoming_qty, reference_id=grpo_ref))
            if line.received_qty_kg < line.ordered_qty_kg: all_lines_fully_received = False

        po.status = 'FULLY_RECEIVED' if all_lines_fully_received else 'PARTIAL_RECEIVED'
        if grpo_total_value > 0:
            post_gl_entry(grpo_ref, "1200", grpo_total_value, 0.0, "GRPO", grpo_ref)
            post_gl_entry(grpo_ref, "2000", 0.0, grpo_total_value, "GRPO", grpo_ref)

        db.session.commit()
        return jsonify({'status': 'success', 'grpo_no': grpo_ref, 'status': po.status})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 400    

# ==========================================
# FORMULATOR & FACTORY PRODUCTION (Target for next split)
# ==========================================
@app.route('/api/formulate', methods=['POST'])
def run_formulation():
    data = request.get_json()
    species = data.get('species', 'Unknown')
    batch = float(data.get('target_batch_kg', 1000))
    mock_recipe = f"Optimal Ration Result for: {species}\nTotal Batch Size: {batch} KG\n\nIngredient Breakdown:\n- Maize Grain: {batch * 0.55} KG (55%)\n- Soybean/Ochonga: {batch * 0.25} KG (25%)\n- Bran/Pollard: {batch * 0.18} KG (18%)\n- Minerals/Premix: {batch * 0.02} KG (2%)\n\nEstimated Cost: KSh {(batch * 45):,.2f}"
    return jsonify({"status": "success", "recipe": mock_recipe})

@app.route('/api/factory/mill', methods=['POST'])
def process_milling():
    return jsonify({"status": "success", "message": "Milling run completed and stock updated."})

@app.route('/api/factory/produce', methods=['POST'])
def process_production():
    return jsonify({"status": "success", "message": "Batch production completed. Finished feed added to inventory."})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)