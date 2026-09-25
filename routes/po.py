from flask import Blueprint, request, jsonify
import uuid
from services.db import db
from services.models import Supplier, PurchaseOrderHeader, PurchaseOrderLine, FeedIngredient, StockMovement
from services.po_service import create_purchase_order, post_goods_receipt

po_bp = Blueprint('po_bp', __name__)

@po_bp.route('/api/suppliers', methods=['GET', 'POST'])
def manage_suppliers():
    if request.method == 'POST':
        data = request.get_json()
        supplier = Supplier(name=data['name'], contact_info=data.get('contact_info', ''))
        db.session.add(supplier)
        db.session.commit()
        return jsonify({'status': 'success', 'supplier_id': supplier.id})
    
    suppliers = Supplier.query.all()
    return jsonify([{'id': s.id, 'name': s.name, 'contact_info': s.contact_info} for s in suppliers])

@po_bp.route('/api/po', methods=['POST'])
def create_po():
    try:
        data = request.get_json()
        po_number = create_purchase_order(data)
        return jsonify({'status': 'success', 'po_number': po_number})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

@po_bp.route('/api/po/<int:po_id>/receive', methods=['POST'])
def receive_po(po_id):
    try:
        grn_data = request.get_json()
        grn_number = post_goods_receipt(po_id, grn_data)
        return jsonify({'status': 'success', 'grn_number': grn_number})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

@po_bp.route('/api/po', methods=['GET'])
def list_pos():
    pos = PurchaseOrderHeader.query.order_by(PurchaseOrderHeader.order_date.desc()).limit(50).all()
    result = []
    for po in pos:
        supplier = db.session.get(Supplier, po.supplier_id)
        result.append({
            'id': po.id,
            'po_number': po.po_number,
            'supplier_name': supplier.name if supplier else 'Unknown',
            'total_amount': po.total_amount,
            'status': po.status,
            'created_at': po.order_date.strftime('%Y-%m-%d') if po.order_date else ''
        })
    return jsonify(result)

@po_bp.route('/api/po/<int:po_id>/lines', methods=['GET'])
def get_po_lines_by_id(po_id):
    try:
        # FIX: Changed po_id to po_header_id to perfectly match your database model
        lines = PurchaseOrderLine.query.filter_by(po_header_id=po_id).all()
        out = []
        for l in lines:
            # Handle getting the ingredient safely
            from services.models import FeedIngredient # ensure correct import if needed
            ing = db.session.get(FeedIngredient, l.ingredient_id)
            
            # Safely grab the received quantity, regardless of what it's named in the model
            received = getattr(l, 'qty_received', getattr(l, 'received_qty_kg', 0.0))
            
            out.append({
                'id': l.id,
                'item_name': ing.name if ing else 'Unknown',
                'qty_ordered': l.qty_ordered,
                'qty_received_so_far': received
            })
        return jsonify({'id': po_id, 'lines': out})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

# --- ISOLATED PURCHASING INVENTORY ROUTES ---
@po_bp.route('/api/po/inventory', methods=['GET'])
def get_po_inventory():
    items = FeedIngredient.query.all()
    result = []
    for i in items:
        result.append({
            'id': i.id,
            'name': i.name,
            'category': getattr(i, 'category', 'Raw Material'),
            'stock_quantity_kg': getattr(i, 'stock_quantity_kg', 0.0),
            'cost_per_kg': getattr(i, 'cost_per_kg', 0.0),
            'bag_size_kg': getattr(i, 'bag_size_kg', 50.0)
        })
    return jsonify(result)

@po_bp.route('/api/po/inventory/add', methods=['POST'])
def add_new_item():
    try:
        data = request.get_json()
        print("📥 Incoming new item data:", data) # Debugging tool

        new_item = FeedIngredient(
            name=data['name'],
            category=data.get('category', 'Raw Material'),
            bag_size_kg=float(data.get('bag_size_kg', 50.0)),
            cost_per_kg=float(data.get('cost_per_kg', 0.0)),
            stock_quantity_kg=0.0,
            retail_price_per_kg=0.0 # <--- THIS FIXES THE DB REJECTION
        )
        db.session.add(new_item)
        db.session.commit()
        print("✅ Item saved to DB successfully! ID:", new_item.id)
        
        return jsonify({'status': 'success', 'item_id': new_item.id})
    except Exception as e:
        db.session.rollback() # Prevent DB crashes
        print("❌ DB Save Error:", str(e))
        return jsonify({'status': 'error', 'message': str(e)}), 400

@po_bp.route('/api/suppliers/add', methods=['POST'])
def add_supplier():
    try:
        data = request.get_json()
        if not data or not data.get('name'):
            raise ValueError("Supplier name is required")
            
        # Assuming Supplier is imported at the top of routes/po.py
        new_sup = Supplier(name=data['name'])
        db.session.add(new_sup)
        db.session.commit()
        return jsonify({'status': 'success', 'id': new_sup.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 400 

@po_bp.route('/api/po/<int:po_id>/grpo', methods=['POST'])
def receive_grpo_partial(po_id):
    try:
        data = request.get_json() or {}
        po = db.session.get(PurchaseOrderHeader, po_id)
        if not po:
            raise ValueError("Purchase Order not found.")
            
        grpo_total_value = 0.0
        grpo_ref = f"GRPO-{uuid.uuid4().hex[:6].upper()}"
        all_lines_fully_received = True

        for recv_item in data.get('received_items', []):
            line = db.session.get(PurchaseOrderLine, int(recv_item['line_id']))
            if not line:
                continue
                
            incoming_qty = float(recv_item['qty_kg'])
            if incoming_qty <= 0: 
                continue

            ing = db.session.get(FeedIngredient, line.ingredient_id)
            
            # PERFECT MATCH: Using qty_ordered and qty_received
            ordered = getattr(line, 'qty_ordered', 0.0) or 0.0
            received = getattr(line, 'qty_received', 0.0) or 0.0
            
            if (received + incoming_qty) > ordered: 
                raise ValueError(f"Exceeds PO limit for {ing.name}")
            
            new_subtotal = incoming_qty * line.unit_cost
            old_val = (ing.stock_quantity_kg or 0.0) * (ing.cost_per_kg or 0.0)
            new_total_stock = (ing.stock_quantity_kg or 0.0) + incoming_qty
            
            if new_total_stock > 0: 
                ing.cost_per_kg = (old_val + new_subtotal) / new_total_stock
            
            ing.stock_quantity_kg = new_total_stock
            
            # WRITE BACK to the correct database column
            line.qty_received = received + incoming_qty
                
            grpo_total_value += new_subtotal
            
            # Log the stock movement
            db.session.add(StockMovement(ingredient_id=ing.id, movement_type='GRPO_RECEIPT', qty_kg=incoming_qty, reference_id=grpo_ref))
            
            if (received + incoming_qty) < ordered: 
                all_lines_fully_received = False

        po.status = 'FULLY_RECEIVED' if all_lines_fully_received else 'PARTIAL_RECEIVED'

        db.session.commit()
        return jsonify({'status': 'success', 'grpo_no': grpo_ref, 'status': po.status})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 400       