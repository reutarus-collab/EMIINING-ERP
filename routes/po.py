import math
from flask import Blueprint, request, jsonify
import uuid
from services.db import db
from services.models import Supplier, PurchaseOrderHeader, PurchaseOrderLine, FeedIngredient, StockMovement
from services.po_service import create_purchase_order, post_goods_receipt

po_bp = Blueprint('po_bp', __name__)

@po_bp.route('/api/suppliers', methods=['GET', 'POST'])
def manage_suppliers():
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        sname = str(data.get('name', '')).strip()
        sinfo = str(data.get('contact_info', '') or '').strip()
        if not sname or len(sname) > 100 or any(ch in (sname + sinfo) for ch in '<>'):
            return jsonify({'status': 'error', 'message': 'Invalid supplier details.'}), 400
        supplier = Supplier(name=sname, contact_info=sinfo[:255])
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
        raise Exception('This receiving route is retired. Use the Receive screen on the Purchase Orders tab.')
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
        lines = PurchaseOrderLine.query.filter_by(po_header_id=po_id).all()
        out = []
        for l in lines:
            # Ensure correct import
            from services.models import FeedIngredient 
            ing = db.session.get(FeedIngredient, l.ingredient_id)
            
            received = getattr(l, 'qty_received', 0.0)
            rejected = getattr(l, 'qty_rejected', 0.0)
            
            out.append({
                'id': l.id,
                'item_name': ing.name if ing else 'Unknown',
                'qty_ordered': l.qty_ordered,
                'qty_received_so_far': received,
                'qty_rejected_so_far': rejected,
                
                # Send the UOM rules to the GRN screen
                'purchase_uom': getattr(ing, 'purchase_uom', 'KG') if ing else 'KG',
                'conversion_type': getattr(ing, 'conversion_type', 'FIXED') if ing else 'FIXED',
                'conversion_factor': getattr(ing, 'conversion_factor', 1.0) if ing else 1.0
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
            'bag_size_kg': getattr(i, 'bag_size_kg', 50.0),
            'purchase_uom': getattr(i, 'purchase_uom', 'KG') # NEW: Send UOM to frontend
        })
    return jsonify(result)

@po_bp.route('/api/po/inventory/add', methods=['POST'])
def add_new_item():
    try:
        data = request.get_json()
        print("📥 Incoming new item data:", data)

        clean_name = str(data.get('name', '')).strip()
        clean_cat = str(data.get('category') or '').strip()[:50]
        text_fields = clean_name + clean_cat + str(data.get('purchase_uom') or '') + str(data.get('stock_uom') or '') + str(data.get('conversion_type') or '')
        if not clean_name or len(clean_name) > 100 or any(ch in text_fields for ch in '<>'):
            raise ValueError('Invalid item name or category.')
        new_item = FeedIngredient(
            name=clean_name,
            category=clean_cat,
            bag_size_kg=float(data.get('bag_size_kg', 50)),
            cost_per_kg=float(data.get('cost_per_kg', 0.0)),
            purchase_uom=data.get('purchase_uom', 'KG'),
            stock_uom=data.get('stock_uom', 'KG'),
            conversion_type=data.get('conversion_type', 'FIXED'),
            conversion_factor=float(data.get('conversion_factor', 1.0)) if data.get('conversion_type') == 'FIXED' else None
        )

        db.session.add(new_item)
        db.session.commit()
        print("✅ Item saved to DB successfully! ID:", new_item.id)
        
        return jsonify({'status': 'success', 'item_id': new_item.id})
    except Exception as e:
        db.session.rollback()
        print("❌ DB Save Error:", str(e))
        return jsonify({'status': 'error', 'message': str(e)}), 400

@po_bp.route('/api/suppliers/add', methods=['POST'])
def add_supplier():
    try:
        data = request.get_json()
        if not data or not data.get('name'):
            raise ValueError("Supplier name is required")
            
        vals = {k: str(data.get(k) or '').strip() for k in ('name', 'phone', 'location', 'items_dealing', 'description')}
        if len(vals['name']) > 100 or any(ch in ''.join(vals.values()) for ch in '<>'):
            raise ValueError('Invalid supplier details.')
        new_sup = Supplier(
            name=vals['name'],
            phone=vals['phone'][:50],
            location=vals['location'][:100],
            items_dealing=vals['items_dealing'][:255],
            description=vals['description'][:2000]
        )
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
        if po.status in ('FULLY_RECEIVED', 'CLOSED', 'CANCELLED'):
            raise ValueError("This purchase order is already closed.")
            
        grpo_total_value = 0.0
        grpo_ref = f"GRPO-{uuid.uuid4().hex[:6].upper()}"
        all_lines_fully_received = True

        for recv_item in data.get('received_items', []):
            line = db.session.get(PurchaseOrderLine, int(recv_item['line_id']))
            if not line or line.po_header_id != po_id:
                raise ValueError('A receipt line does not belong to this purchase order.')
                
            # SEPARATING UOM: How many Bags arrived, vs how many KGs go to stock
            incoming_po_qty = float(recv_item.get('qty_po_uom', 0.0))
            rejected_po_qty = float(recv_item.get('qty_rejected_po_uom', 0.0))
            incoming_kg = float(recv_item.get('qty_kg_accepted', 0.0))
            if not all(math.isfinite(v) and v >= 0 for v in (incoming_po_qty, rejected_po_qty, incoming_kg)):
                raise ValueError('Quantities must be zero or more.')
            if incoming_po_qty > 0 and incoming_kg <= 0:
                raise ValueError('Enter the weight in kg for the goods received.')

            if incoming_po_qty <= 0 and rejected_po_qty <= 0: 
                continue

            ing = db.session.get(FeedIngredient, line.ingredient_id)
            ordered = getattr(line, 'qty_ordered', 0.0) or 0.0
            received = getattr(line, 'qty_received', 0.0) or 0.0
            rejected = getattr(line, 'qty_rejected', 0.0) or 0.0
            
            # Ensure they don't receive more than ordered (including rejects)
            if (received + rejected + incoming_po_qty + rejected_po_qty) > ordered: 
                raise ValueError(f"Exceeds PO limit for {ing.name}")

            # Canonical Read: Current stock from ledger
            current_stock = db.session.query(db.func.sum(StockMovement.qty_kg)).filter_by(ingredient_id=ing.id).scalar() or 0.0
            
            # NOTE: Using incoming_kg instead of incoming_qty to match po.py
            new_subtotal = incoming_po_qty * line.unit_cost
            old_val = current_stock * (ing.cost_per_kg or 0.0)
            new_total_stock = current_stock + incoming_kg
            if incoming_kg > 0 and incoming_po_qty > 0:
                implied = new_subtotal / incoming_kg
                known = ing.cost_per_kg or 0.0
                if known > 0 and not (known / 3.0 <= implied <= known * 3.0):
                    raise ValueError(f"Cost per kg works out to {implied:.2f} for {ing.name}, far from its current {known:.2f}. Check the quantity received and the unit cost on the PO.")
            
            # Update moving average cost based on true ledger stock
            if new_total_stock > 0: 
                ing.cost_per_kg = (old_val + new_subtotal) / new_total_stock
            
            ing.stock_quantity_kg = (ing.stock_quantity_kg or 0.0) + incoming_kg
            
            # Write back the PO units
            line.qty_received = received + incoming_po_qty
            line.qty_rejected = rejected + rejected_po_qty
            grpo_total_value += new_subtotal
            
            # Write the exact KG to the ledger
            if incoming_kg > 0:
                db.session.add(StockMovement(ingredient_id=ing.id, movement_type='GRPO_RECEIPT', qty_kg=incoming_kg, reference_id=grpo_ref))
            
            if (line.qty_received + line.qty_rejected) < ordered: 
                all_lines_fully_received = False

        po.status = 'FULLY_RECEIVED' if all_lines_fully_received else 'PARTIAL_RECEIVED'

        db.session.commit()
        return jsonify({'status': 'success', 'grpo_no': grpo_ref, 'po_status': po.status})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 400