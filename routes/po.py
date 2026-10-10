from services.idempotency import idempotent
from flask import g
import math
from flask import Blueprint, request, jsonify
import uuid
import hashlib
import json
from sqlalchemy.exc import IntegrityError
from services.db import db
from services.models import Supplier, PurchaseOrderHeader, PurchaseOrderLine, FeedIngredient, StockMovement, GoodsReceiptNote, GoodsReceiptLine, IdempotencyKey
from services.po_service import create_purchase_order, item_usage, purge_empty_stock_rows
from routes.auth import roles_required
from services.inventory import resolve_location, location_stock, stock_quantity, change_stock, receive_stock

po_bp = Blueprint('po_bp', __name__)

@po_bp.route('/api/suppliers', methods=['GET', 'POST'])
@idempotent('supplier-add')
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
@idempotent('po-create')
def create_po():
    try:
        data = request.get_json()
        po_number = create_purchase_order(data)
        return jsonify({'status': 'success', 'po_number': po_number})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

@po_bp.route('/api/po', methods=['GET'])
def list_pos():
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    pos = PurchaseOrderHeader.query.filter_by(location_id=str(location.id)).order_by(PurchaseOrderHeader.order_date.desc()).limit(50).all()
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
        location = resolve_location(request.args.get('location_id'))
        po = db.session.get(PurchaseOrderHeader, po_id)
        if not po or str(po.location_id) != str(location.id):
            return jsonify(status='error', message='Purchase order not found at this outlet.'), 404
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
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return jsonify(status='error', message=str(exc)), 400
    items = FeedIngredient.query.all()
    result = []
    for i in items:
        result.append({
            'id': i.id,
            'name': i.name,
            'category': getattr(i, 'category', 'Raw Material'),
            'stock_quantity_kg': stock_quantity(location.id, i.id),
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
        existing = FeedIngredient.query.filter(
            db.func.lower(FeedIngredient.name) == clean_name.lower()).first()
        if existing:
            # safe retry: repeated save returns the same item, no duplicate
            return jsonify({'status': 'success', 'item_id': existing.id, 'duplicate': True})
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

@po_bp.route('/api/po/inventory/<int:item_id>', methods=['DELETE'])
@roles_required('admin')
def delete_item(item_id):
    item = FeedIngredient.query.get_or_404(item_id)
    usage = item_usage(item.id)
    if usage:
        detail = ', '.join(f'{t}: {n}' for t, n in usage.items())
        return jsonify(status='error',
                       message=f'Cannot delete: item has history ({detail}).'), 409
    try:
        purge_empty_stock_rows(item.id)
        db.session.delete(item)
        db.session.commit()
        return jsonify(status='success')
    except Exception as e:
        db.session.rollback()
        return jsonify(status='error', message=str(e)), 400

@po_bp.route('/api/suppliers/add', methods=['POST'])
@idempotent('supplier-add-2')
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

def _grpo_replay(key, request_hash):
    """Return the original response if this receipt key was already posted."""
    existing = IdempotencyKey.query.filter_by(key=key).first()
    if not existing:
        return None
    if existing.created_by != g.user.username or existing.request_hash != request_hash:
        return jsonify(status='error', message='This receipt was already posted with different details. Check the PO history before posting again.'), 409
    return jsonify(dict(existing.response_json or {'status': 'success'}, already_processed=True))

@po_bp.route('/api/po/<int:po_id>/grpo', methods=['POST'])
def receive_grpo_partial(po_id):
    key = None
    request_hash = None
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('Invalid receipt request.')
        try:
            key = 'grpo:' + str(uuid.UUID((request.headers.get('Idempotency-Key') or '').strip()))
        except (ValueError, TypeError, AttributeError):
            return jsonify(status='error', message='Reload the app and try again (receipt key missing).'), 400
        request_hash = hashlib.sha256(json.dumps({'po_id': po_id, 'body': data}, sort_keys=True,
                                                 separators=(',', ':')).encode('utf-8')).hexdigest()
        po = db.session.get(PurchaseOrderHeader, po_id)
        if not po:
            raise ValueError("Purchase Order not found.")
        # A retry of an already-posted receipt returns the original result, even if the PO is now closed.
        replay = _grpo_replay(key, request_hash)
        if replay is not None:
            return replay
        if po.status in ('FULLY_RECEIVED', 'CLOSED', 'CANCELLED'):
            raise ValueError("This purchase order is already closed.")
        location = resolve_location(po.location_id)
        if str(po.location_id) != str(location.id):
            raise ValueError('This PO belongs to a different outlet than your assigned location.')
        payment_method = str(data.get('payment_method') or '').strip().upper()
        if payment_method not in ('CASH', 'MPESA', 'BANK', 'ON_ACCOUNT'):
            raise ValueError('Choose how this purchase was paid.')
            
        grpo_total_value = 0.0
        grpo_ref = f"GRPO-{uuid.uuid4().hex[:6].upper()}"
        grn = GoodsReceiptNote(grn_number=grpo_ref, po_header_id=po.id,
                               supplier_id=po.supplier_id,
                               location_id=location.id,
                               delivery_note=str(data.get('delivery_note') or '')[:100],
                               vehicle_reg=str(data.get('vehicle_reg') or '')[:20])
        db.session.add(grn)
        db.session.flush()
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
            if incoming_po_qty <= 0 and incoming_kg > 0:
                raise ValueError('Accepted stock weight requires an accepted purchase quantity.')

            if incoming_po_qty <= 0 and rejected_po_qty <= 0: 
                continue

            ing = db.session.get(FeedIngredient, line.ingredient_id)
            if not ing:
                raise ValueError('The received inventory item no longer exists.')
            if incoming_po_qty > 0 and (ing.conversion_type or 'FIXED') == 'FIXED':
                expected_kg = incoming_po_qty * (ing.conversion_factor or 1.0)
                if abs(incoming_kg - expected_kg) > max(0.05, expected_kg * 0.01):
                    raise ValueError(f'Entered kg for {ing.name} does not match its fixed UOM conversion ({expected_kg:.2f} kg expected).')
            ordered = getattr(line, 'qty_ordered', 0.0) or 0.0
            received = getattr(line, 'qty_received', 0.0) or 0.0
            rejected = getattr(line, 'qty_rejected', 0.0) or 0.0
            
            # Ensure they don't receive more than ordered (including rejects)
            if (received + rejected + incoming_po_qty + rejected_po_qty) > ordered: 
                raise ValueError(f"Exceeds PO limit for {ing.name}")

            stock_row = location_stock(location.id, ing.id, lock=True)
            
            # NOTE: Using incoming_kg instead of incoming_qty to match po.py
            new_subtotal = incoming_po_qty * line.unit_cost
            if incoming_kg > 0 and incoming_po_qty > 0:
                implied = new_subtotal / incoming_kg
                known = stock_row.unit_cost_per_kg or 0.0
                if known > 0 and not (known / 3.0 <= implied <= known * 3.0):
                    raise ValueError(f"Cost per kg works out to {implied:.2f} for {ing.name}, far from its current {known:.2f}. Check the quantity received and the unit cost on the PO.")
            receive_stock(location.id, ing, incoming_kg, new_subtotal / incoming_kg,
                          'GRPO_RECEIPT', grpo_ref)
            
            # Write back the PO units
            line.qty_received = received + incoming_po_qty
            line.qty_rejected = rejected + rejected_po_qty
            grpo_total_value += new_subtotal
            
            # Write the exact KG to the ledger
            db.session.add(GoodsReceiptLine(grn_id=grn.id, po_line_id=line.id,
                                            ingredient_id=ing.id, qty_received=incoming_kg,
                                            qty_accepted=incoming_kg,
                                            qty_rejected=0.0,
                                            qty_rejected_po_uom=rejected_po_qty,
                                            batch_number=str(recv_item.get('batch_number') or '')[:50],
                                            unit_cost=line.unit_cost))
            
            if (line.qty_received + line.qty_rejected) < ordered: 
                all_lines_fully_received = False

        db.session.flush()
        all_po_lines = PurchaseOrderLine.query.filter_by(po_header_id=po.id).all()
        all_lines_fully_received = all(((l.qty_received or 0.0) + (l.qty_rejected or 0.0)) >= (l.qty_ordered or 0.0) - 0.0001 for l in all_po_lines)
        po.status = 'FULLY_RECEIVED' if all_lines_fully_received else 'PARTIAL_RECEIVED'
        if grpo_total_value > 0:
            from routes.payables import record_purchase
            record_purchase(po.supplier_id, po.id, grpo_ref, grpo_total_value, payment_method, g.user.username)

        result = {'status': 'success', 'grpo_no': grpo_ref, 'po_status': po.status}
        # Same transaction as the receipt, stock and GL rows: all commit together or none do.
        db.session.add(IdempotencyKey(key=key, request_hash=request_hash, response_json=result,
                                      created_by=g.user.username, location_id=location.id))
        db.session.commit()
        return jsonify(result)
    except IntegrityError:
        db.session.rollback()
        replay = _grpo_replay(key, request_hash) if key else None
        if replay is not None:
            return replay
        return jsonify(status='error', message='Receipt conflicted with another request. Check the PO history before posting again.'), 409
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 400
