from services.idempotency import idempotent
from flask import g
from services.errors import error_response
import math
from flask import Blueprint, request, jsonify
import uuid
import hashlib
import json
from sqlalchemy.exc import IntegrityError
from services.db import db
from services.models import Supplier, PurchaseOrderHeader, PurchaseOrderLine, FeedIngredient, StockMovement, GoodsReceiptNote, GoodsReceiptLine, IdempotencyKey
from services.po_service import create_purchase_order, item_usage, purge_empty_stock_rows
from routes.auth import roles_required, PURCHASING_STAFF
from services.inventory import resolve_location, location_stock, stock_quantity, change_stock, receive_stock
from services.uom import (rule_for_item, rule_for_line, normalise_rule, price_per_kg, check_price,
                          PriceCheckError, VALID_UOMS)
from services.item_groups import group_for, GROUP_LABEL, GROUP_RANK, CATEGORY_CHOICES, name_order

po_bp = Blueprint('po_bp', __name__)

@po_bp.route('/api/suppliers', methods=['GET', 'POST'])
@roles_required(*PURCHASING_STAFF)
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
@roles_required(*PURCHASING_STAFF)
@idempotent('po-create')
def create_po():
    try:
        data = request.get_json()
        po_number = create_purchase_order(data)
        return jsonify({'status': 'success', 'po_number': po_number})
    except PriceCheckError as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'code': 'PRICE_CHECK', 'message': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return error_response(e)

CLOSED_PO_STATUSES = ('FULLY_RECEIVED', 'CLOSED', 'CANCELLED')


@po_bp.route('/api/po', methods=['GET'])
@roles_required(*PURCHASING_STAFF)
def list_pos():
    """Expected deliveries only: orders that still have goods to come. Pass history=1 for everything."""
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return error_response(exc)
    query = PurchaseOrderHeader.query.filter_by(location_id=str(location.id))
    if request.args.get('history') not in ('1', 'true', 'True'):
        query = query.filter(PurchaseOrderHeader.status.notin_(CLOSED_PO_STATUSES))
    pos = query.order_by(PurchaseOrderHeader.order_date.desc()).limit(200).all()
    suppliers = {s.id: s for s in Supplier.query.filter(Supplier.id.in_({p.supplier_id for p in pos} or {0})).all()}
    result = []
    for po in pos:
        supplier = suppliers.get(po.supplier_id)
        result.append({
            'id': po.id,
            'po_number': po.po_number,
            'supplier_id': po.supplier_id,
            'supplier_name': supplier.name if supplier else 'Unknown',
            'supplier_phone': (supplier.phone or '') if supplier else '',
            'total_amount': po.total_amount,
            'status': po.status,
            'created_at': po.order_date.strftime('%Y-%m-%d') if po.order_date else ''
        })
    return jsonify(result)


def _kenya_phone(raw):
    """0712 345 678 / +254712345678 / 254712345678 -> 254712345678. Returns '' when it is not a usable number."""
    digits = ''.join(ch for ch in str(raw or '') if ch.isdigit())
    if digits.startswith('254') and len(digits) == 12:
        return digits
    if digits.startswith('0') and len(digits) == 10:
        return '254' + digits[1:]
    if len(digits) == 9 and digits[0] in '17':
        return '254' + digits
    return digits if 10 <= len(digits) <= 15 and str(raw or '').strip().startswith('+') else ''


@po_bp.route('/api/po/<int:po_id>/message', methods=['GET'])
@roles_required(*PURCHASING_STAFF)
def po_message(po_id):
    """Order text for the supplier, plus ready-made WhatsApp and SMS links."""
    from urllib.parse import quote
    try:
        location = resolve_location(request.args.get('location_id'))
        po = db.session.get(PurchaseOrderHeader, po_id)
        if not po or str(po.location_id) != str(location.id):
            return jsonify(status='error', message='Purchase order not found at this outlet.'), 404
        supplier = db.session.get(Supplier, po.supplier_id)
        rows = []
        for l in PurchaseOrderLine.query.filter_by(po_header_id=po.id).order_by(PurchaseOrderLine.id).all():
            ing = db.session.get(FeedIngredient, l.ingredient_id)
            uom = (l.purchase_uom or (ing.purchase_uom if ing else 'KG') or 'KG').upper()
            outstanding = (l.qty_ordered or 0) - (l.qty_received or 0) - (l.qty_rejected or 0)
            if outstanding <= 0.0001:
                continue
            rows.append(f"- {ing.name if ing else 'Item'}: {outstanding:g} {uom} @ KSh {l.unit_cost:,.2f}/{uom}")
        lines = [f"Hello {supplier.name if supplier else ''}, purchase order {po.po_number} from Emining Feeds ({location.name}):"]
        lines += rows or ['(nothing outstanding)']
        lines.append(f"Total: KSh {po.total_amount:,.2f}")
        lines.append('Please confirm availability and delivery date. Thank you.')
        text = '\n'.join(lines)
        phone = _kenya_phone(supplier.phone if supplier else '')
        return jsonify(status='success', text=text, phone=phone, supplier=supplier.name if supplier else '',
                       whatsapp_url=(f"https://wa.me/{phone}?text={quote(text)}" if phone else f"https://wa.me/?text={quote(text)}"),
                       sms_url=f"sms:{('+' + phone) if phone else ''}?body={quote(text)}")
    except Exception as e:
        return error_response(e)


@po_bp.route('/api/po/<int:po_id>/lines', methods=['GET'])
@roles_required(*PURCHASING_STAFF)
def get_po_lines_by_id(po_id):
    try:
        location = resolve_location(request.args.get('location_id'))
        po = db.session.get(PurchaseOrderHeader, po_id)
        if not po or str(po.location_id) != str(location.id):
            return jsonify(status='error', message='Purchase order not found at this outlet.'), 404
        lines = PurchaseOrderLine.query.filter_by(po_header_id=po_id).order_by(PurchaseOrderLine.id).all()
        out = []
        for l in lines:
            ing = db.session.get(FeedIngredient, l.ingredient_id)
            try:
                uom, ctype, factor = rule_for_line(l, ing) if ing else ('KG', 'FIXED', 1.0)
            except ValueError:
                # a damaged rule must not hide the PO; posting a receipt re-checks it and says what to fix
                uom, ctype, factor = (l.purchase_uom or 'KG'), (l.conversion_type or 'FIXED'), l.conversion_factor
            out.append({
                'id': l.id,
                'item_name': ing.name if ing else 'Unknown',
                'qty_ordered': l.qty_ordered,
                'qty_received_so_far': l.qty_received or 0.0,
                'qty_rejected_so_far': l.qty_rejected or 0.0,
                'unit_cost': l.unit_cost,                  # price of ONE purchase_uom
                'purchase_uom': uom,
                'conversion_type': ctype,
                'conversion_factor': factor if factor is not None else 1.0,
                'price_basis': f'KSh per {uom}',
            })
        return jsonify({'id': po_id, 'lines': out})
    except Exception as e:
        return error_response(e)

# --- ISOLATED PURCHASING INVENTORY ROUTES ---
@po_bp.route('/api/po/inventory', methods=['GET'])
@roles_required(*PURCHASING_STAFF)
def get_po_inventory():
    try:
        location = resolve_location(request.args.get('location_id'))
    except ValueError as exc:
        return error_response(exc)
    items = FeedIngredient.query.filter(db.or_(FeedIngredient.category.is_(None), FeedIngredient.category != 'Milling Service')
                                        ).order_by(name_order()).all()
    result = []
    for i in items:
        group = group_for(i.category)
        try:
            _uom, _ctype, _factor = rule_for_item(i)
            unit_problem = ''
        except ValueError as exc:
            _uom, _ctype, _factor, unit_problem = (i.purchase_uom or 'KG'), (i.conversion_type or 'FIXED'), i.conversion_factor, str(exc)
        result.append({
            'id': i.id,
            'name': i.name,
            'category': i.category or '',
            'group': group,
            'group_label': GROUP_LABEL[group],
            'stock_quantity_kg': stock_quantity(location.id, i.id),
            'cost_per_kg': i.cost_per_kg or 0.0,
            'bag_size_kg': i.bag_size_kg or 50.0,
            'purchase_uom': _uom,
            'conversion_type': _ctype,
            'conversion_factor': _factor if _factor is not None else 0.0,
            'unit_problem': unit_problem,
            'is_active': i.is_active is not False,
            'crude_protein_pct': i.crude_protein_pct or 0.0,
            'metabolizable_energy_mcal': i.metabolizable_energy_mcal or 0.0,
            'crude_fiber_pct': i.crude_fiber_pct or 0.0,
            'calcium_pct': i.calcium_pct or 0.0, 'phosphorus_pct': i.phosphorus_pct or 0.0,
            'lysine_pct': i.lysine_pct or 0.0, 'methionine_pct': i.methionine_pct or 0.0,
            'max_inclusion_pct': 100.0 if i.max_inclusion_pct is None else i.max_inclusion_pct
        })
    result.sort(key=lambda r: (GROUP_RANK[r['group']], r['name'].lower()))
    return jsonify(result)


@po_bp.route('/api/po/inventory/<int:item_id>/uom', methods=['POST'])
@roles_required('admin', 'accountant', 'factory')
def set_item_uom(item_id):
    """Change how an item is bought. Open POs keep the rule they were written with."""
    data = request.get_json(silent=True) or {}
    item = db.session.get(FeedIngredient, item_id)
    if not item:
        return jsonify(status='error', message='Item not found.'), 404
    try:
        uom, ctype, factor = normalise_rule(data.get('purchase_uom'), data.get('conversion_type'),
                                            data.get('conversion_factor'), item.name)
        bag = data.get('bag_size_kg')
        if bag not in (None, ''):
            bag = float(bag)
            if not math.isfinite(bag) or bag <= 0 or bag > 1000:
                raise ValueError('Bag size must be between 0 and 1000 kg.')
            item.bag_size_kg = bag
    except (TypeError, ValueError) as exc:
        return error_response(exc)
    item.purchase_uom, item.conversion_type, item.conversion_factor = uom, ctype, factor
    db.session.commit()
    return jsonify(status='success', purchase_uom=uom, conversion_type=ctype, conversion_factor=factor)


@po_bp.route('/api/po/inventory/<int:item_id>/category', methods=['POST'])
@roles_required('admin', 'accountant', 'factory')
def set_item_category(item_id):
    data = request.get_json(silent=True) or {}
    category = str(data.get('category') or '').strip()
    if category not in CATEGORY_CHOICES:
        return jsonify(status='error', message='Choose one of: ' + ', '.join(CATEGORY_CHOICES)), 400
    item = db.session.get(FeedIngredient, item_id)
    if not item:
        return jsonify(status='error', message='Item not found.'), 404
    item.category = category
    db.session.commit()
    return jsonify(status='success', category=category, group=group_for(category))


@po_bp.route('/api/po/inventory/<int:item_id>/nutrients', methods=['POST'])
@roles_required('admin', 'accountant', 'factory')
def set_item_nutrients(item_id):
    """Nutrient profile the Ration Formulator uses: protein %, energy Mcal/kg, fibre %, max inclusion %."""
    import math
    data = request.get_json(silent=True) or {}
    limits = (('crude_protein_pct', 0, 100), ('metabolizable_energy_mcal', 0, 10),
              ('crude_fiber_pct', 0, 100), ('max_inclusion_pct', 0.01, 100))
    values = {}
    for key in ('calcium_pct', 'phosphorus_pct', 'lysine_pct', 'methionine_pct'):
        if data.get(key) in (None, ''):
            continue            # optional: leave the saved value alone when the field is not sent
        try:
            value = float(data.get(key))
        except (TypeError, ValueError):
            return jsonify(status='error', message='Enter a number for every nutrient field.'), 400
        if not math.isfinite(value) or value < 0 or value > 100:
            return jsonify(status='error', message=f'{key.replace("_", " ")} must be between 0 and 100.'), 400
        values[key] = value
    for key, low, high in limits:
        try:
            value = float(data.get(key))
        except (TypeError, ValueError):
            return jsonify(status='error', message='Enter a number for every nutrient field.'), 400
        if not math.isfinite(value) or value < low or value > high:
            return jsonify(status='error', message=f'{key.replace("_", " ")} must be between {low} and {high}.'), 400
        values[key] = value
    item = db.session.get(FeedIngredient, item_id)
    if not item:
        return jsonify(status='error', message='Item not found.'), 404
    for key, value in values.items():
        setattr(item, key, value)
    db.session.commit()
    return jsonify(status='success')


@po_bp.route('/api/po/inventory/<int:item_id>/active', methods=['POST'])
@roles_required('admin')
def set_item_active(item_id):
    """Disable = hidden from Stock and POS only. Formulation, purchasing and rations still use the item."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get('active'), bool):
        return jsonify(status='error', message='Send active as true or false.'), 400
    item = db.session.get(FeedIngredient, item_id)
    if not item:
        return jsonify(status='error', message='Item not found.'), 404
    item.is_active = data['active']
    db.session.commit()
    return jsonify(status='success', id=item.id, is_active=item.is_active)

@po_bp.route('/api/po/inventory/add', methods=['POST'])
@roles_required(*PURCHASING_STAFF)
def add_new_item():
    try:
        data = request.get_json()

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
        uom, ctype, factor = normalise_rule(data.get('purchase_uom'), data.get('conversion_type'),
                                            data.get('conversion_factor'), clean_name)
        new_item = FeedIngredient(
            name=clean_name,
            category=clean_cat,
            bag_size_kg=float(data.get('bag_size_kg', 50)),
            cost_per_kg=float(data.get('cost_per_kg', 0.0)),
            purchase_uom=uom,
            stock_uom='KG',
            conversion_type=ctype,
            conversion_factor=factor
        )

        db.session.add(new_item)
        db.session.commit()
        
        return jsonify({'status': 'success', 'item_id': new_item.id})
    except Exception as e:
        db.session.rollback()
        return error_response(e)

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
        return error_response(e)

@po_bp.route('/api/suppliers/add', methods=['POST'])
@roles_required(*PURCHASING_STAFF)
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
        return error_response(e) 

def _grpo_replay(key, request_hash):
    """Return the original response if this receipt key was already posted."""
    existing = IdempotencyKey.query.filter_by(key=key).first()
    if not existing:
        return None
    if existing.created_by != g.user.username or existing.request_hash != request_hash:
        return jsonify(status='error', message='This receipt was already posted with different details. Check the PO history before posting again.'), 409
    return jsonify(dict(existing.response_json or {'status': 'success'}, already_processed=True))

def _parse_transport(data, po):
    """Optional transport block. Returns None, or a cleaned dict. Transport is never part of the supplier invoice."""
    t = data.get('transport')
    if t in (None, '', {}):
        return None
    if not isinstance(t, dict):
        raise ValueError('Invalid transport details.')
    try:
        amount = round(float(t.get('amount') or 0), 2)
    except (TypeError, ValueError):
        raise ValueError('Enter the transport amount as a number.')
    if not math.isfinite(amount) or amount < 0:
        raise ValueError('Transport amount must be zero or more.')
    if amount == 0:
        return None
    payee = str(t.get('payee') or '').strip()
    reference = str(t.get('reference') or '').strip()
    if not payee or len(payee) > 100 or len(reference) > 100 or any(ch in payee + reference for ch in '<>'):
        raise ValueError('Enter who was paid for transport (the driver or transport company).')
    method = str(t.get('payment_method') or '').strip().upper()
    if method not in ('CASH', 'MPESA', 'BANK', 'ON_ACCOUNT'):
        raise ValueError('Choose how transport was paid, or Owed if it is still unpaid.')
    allocation = str(t.get('allocation') or 'WEIGHT').strip().upper()
    if allocation not in ('WEIGHT', 'VALUE'):
        raise ValueError('Spread transport by weight or by value.')
    transporter_id = None
    if method == 'ON_ACCOUNT':
        try:
            transporter_id = int(t.get('supplier_id'))
        except (TypeError, ValueError):
            raise ValueError('Unpaid transport needs the transporter picked from the supplier list, so it can be paid later.')
        if not db.session.get(Supplier, transporter_id):
            raise ValueError('Pick a valid transporter.')
        if transporter_id == po.supplier_id:
            raise ValueError('Transport must be owed to the transporter, not to the goods supplier. Add the transporter as its own supplier.')
    return dict(amount=amount, payee=payee, reference=reference, method=method,
                allocation=allocation, supplier_id=transporter_id)


def _allocate_transport(amount, entries, mode):
    """Spread transport over the accepted lines in cents. Last line takes the rounding remainder."""
    if not entries:
        return []
    basis = [(e['kg'] if mode == 'WEIGHT' else e['subtotal']) for e in entries]
    total_basis = sum(basis)
    if total_basis <= 0:
        basis, total_basis = [1.0] * len(entries), float(len(entries))
    shares, running = [], 0.0
    for i, b in enumerate(basis):
        share = round(amount - running, 2) if i == len(entries) - 1 else round(amount * b / total_basis, 2)
        shares.append(share)
        running = round(running + share, 2)
    return shares


@po_bp.route('/api/po/<int:po_id>/grpo', methods=['POST'])
@roles_required(*PURCHASING_STAFF)
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
        # How it was paid only matters when something is accepted. Rejecting a whole delivery needs no payment method.
        if payment_method and payment_method not in ('CASH', 'MPESA', 'BANK', 'ON_ACCOUNT'):
            raise ValueError('Choose how this purchase was paid.')
        transport = _parse_transport(data, po)

        # ---- pass 1: check every line against the PO's own unit rule. Nothing is written yet.
        entries, claimed = [], {}
        for recv_item in data.get('received_items', []):
            try:
                line = db.session.get(PurchaseOrderLine, int(recv_item['line_id']))
            except (KeyError, TypeError, ValueError):
                raise ValueError('A receipt line is missing its PO line.')
            if not line or line.po_header_id != po_id:
                raise ValueError('A receipt line does not belong to this purchase order.')
            try:
                incoming_po_qty = float(recv_item.get('qty_po_uom', 0.0))
                rejected_po_qty = float(recv_item.get('qty_rejected_po_uom', 0.0))
                incoming_kg = float(recv_item.get('qty_kg_accepted', 0.0))
            except (TypeError, ValueError):
                raise ValueError('Quantities must be numbers.')
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
            uom, ctype, factor = rule_for_line(line, ing)          # the rule the PO was written with
            if incoming_po_qty > 0 and ctype == 'FIXED':
                expected = incoming_po_qty * factor
                if abs(incoming_kg - expected) > max(0.05, expected * 0.01):
                    raise ValueError(f'Entered kg for {ing.name} does not match {incoming_po_qty:g} {uom} '
                                     f'x {factor:g} kg ({expected:.2f} kg expected).')
            ordered = line.qty_ordered or 0.0
            claimed[line.id] = claimed.get(line.id, 0.0) + incoming_po_qty + rejected_po_qty
            if (line.qty_received or 0.0) + (line.qty_rejected or 0.0) + claimed[line.id] > ordered + 1e-6:
                raise ValueError(f"Exceeds PO limit for {ing.name}")
            subtotal = round(incoming_po_qty * line.unit_cost, 2)
            if incoming_kg > 0:
                implied = subtotal / incoming_kg                    # goods only, per kg: catches wrong units
                known = location_stock(location.id, ing.id, lock=True).unit_cost_per_kg or 0.0
                if known > 0 and not (known / 3.0 <= implied <= known * 3.0):
                    raise ValueError(f"Cost per kg works out to {implied:.2f} for {ing.name}, far from its current {known:.2f}. "
                                     f"Check the quantity received and the unit cost on the PO.")
            entries.append(dict(line=line, ing=ing, po_qty=incoming_po_qty, rej=rejected_po_qty, kg=incoming_kg,
                                subtotal=subtotal, batch=str(recv_item.get('batch_number') or '')[:50]))

        # ---- landed cost: goods value plus its share of transport, per accepted kg
        accepted = [e for e in entries if e['kg'] > 0]
        shares = _allocate_transport(transport['amount'], accepted, transport['allocation']) if transport and accepted else []
        for e, share in zip(accepted, shares):
            e['transport'] = share
        for e in entries:
            e.setdefault('transport', 0.0)
            e['landed'] = round((e['subtotal'] + e['transport']) / e['kg'], 6) if e['kg'] > 0 else None

        # ---- pass 2: write
        grpo_ref = f"GRPO-{uuid.uuid4().hex[:6].upper()}"
        goods_value = round(sum(e['subtotal'] for e in entries), 2)
        grn = GoodsReceiptNote(grn_number=grpo_ref, po_header_id=po.id, supplier_id=po.supplier_id,
                               location_id=location.id,
                               delivery_note=str(data.get('delivery_note') or '')[:100],
                               vehicle_reg=str(data.get('vehicle_reg') or '')[:20],
                               goods_value=goods_value)
        if transport:
            grn.transport_amount, grn.transport_payee = transport['amount'], transport['payee']
            grn.transport_method, grn.transport_supplier_id = transport['method'], transport['supplier_id']
            grn.transport_allocation, grn.transport_reference = transport['allocation'], transport['reference']
        db.session.add(grn)
        db.session.flush()
        for e in entries:
            line, ing = e['line'], e['ing']
            if e['kg'] > 0:
                receive_stock(location.id, ing, e['kg'], e['landed'], 'GRPO_RECEIPT', grpo_ref)
            line.qty_received = (line.qty_received or 0.0) + e['po_qty']
            line.qty_rejected = (line.qty_rejected or 0.0) + e['rej']
            db.session.add(GoodsReceiptLine(grn_id=grn.id, po_line_id=line.id, ingredient_id=ing.id,
                                            qty_received=e['kg'], qty_accepted=e['kg'], qty_rejected=0.0,
                                            qty_rejected_po_uom=e['rej'], batch_number=e['batch'],
                                            unit_cost=line.unit_cost, landed_cost_per_kg=e['landed'],
                                            transport_allocated=e['transport']))

        db.session.flush()
        all_po_lines = PurchaseOrderLine.query.filter_by(po_header_id=po.id).all()
        all_lines_fully_received = all(((l.qty_received or 0.0) + (l.qty_rejected or 0.0)) >= (l.qty_ordered or 0.0) - 0.0001 for l in all_po_lines)
        po.status = 'FULLY_RECEIVED' if all_lines_fully_received else 'PARTIAL_RECEIVED'
        if goods_value > 0 and payment_method not in ('CASH', 'MPESA', 'BANK', 'ON_ACCOUNT'):
            raise ValueError('Choose how this purchase was paid.')
        if goods_value > 0:
            from routes.payables import record_purchase
            record_purchase(po.supplier_id, po.id, grpo_ref, goods_value, payment_method, g.user.username)
        if transport:
            from services.ledger_service import post_gl_transaction
            from services.models import SupplierTxn
            debit = '1200' if accepted else '5300'                  # no accepted stock to carry it: expense it
            credit = {'CASH': '1000', 'MPESA': '1010', 'BANK': '1020', 'ON_ACCOUNT': '2000'}[transport['method']]
            post_gl_transaction(grpo_ref + '-TRN', [(debit, transport['amount'], 0.0), (credit, 0.0, transport['amount'])],
                                'TRANSPORT', po.id)
            if transport['method'] == 'ON_ACCOUNT':
                db.session.add(SupplierTxn(supplier_id=transport['supplier_id'], po_id=po.id, ref=grpo_ref + '-TRN',
                                           kind='PURCHASE', amount=transport['amount'], method='ON_ACCOUNT',
                                           note=f"Transport for {po.po_number} ({transport['payee']})"[:200],
                                           created_by=g.user.username))

        result = {'status': 'success', 'grpo_no': grpo_ref, 'po_status': po.status,
                  'goods_value': goods_value, 'transport': transport['amount'] if transport else 0.0,
                  'landed_total': round(goods_value + (transport['amount'] if transport else 0.0), 2)}
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
        return error_response(e)
