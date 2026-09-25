import uuid
from datetime import datetime
from services.db import db
from services.models import (PurchaseOrderHeader, PurchaseOrderLine, 
                             GoodsReceiptNote, GoodsReceiptLine, StockMovement, FeedIngredient)

def create_purchase_order(data):
    """Stage 1: Commitment (No Stock or GL Impact)"""
    po = PurchaseOrderHeader(
        po_number=f"PO-{uuid.uuid4().hex[:6].upper()}",
        supplier_id=data['supplier_id'],
        location_id=data.get('location_id', 'Emining Main Store'),
        payment_terms=data.get('payment_terms', 'Cash'),
        status='APPROVED' # Skipping DRAFT for immediate workflow
    )
    db.session.add(po)
    db.session.flush()

    total = 0.0
    for item in data['items']:
        subtotal = float(item['qty']) * float(item['unit_cost'])
        total += subtotal
        line = PurchaseOrderLine(
            po_header_id=po.id,
            ingredient_id=item['ingredient_id'],
            qty_ordered=float(item['qty']),
            unit_cost=float(item['unit_cost']),
            subtotal=subtotal
        )
        db.session.add(line)
    
    po.total_amount = total
    db.session.commit()
    return po.po_number

def post_goods_receipt(po_id, grn_data):
    """Stage 2: Receiving (Immutable Stock Movement & GRNI Accounting)"""
    po = PurchaseOrderHeader.query.get(po_id)
    if not po or po.status in ['CLOSED', 'CANCELLED', 'FULLY_RECEIVED']:
        raise Exception("Invalid or closed Purchase Order.")

    grn_number = f"GRN-{uuid.uuid4().hex[:6].upper()}"
    grn = GoodsReceiptNote(
        grn_number=grn_number,
        po_header_id=po.id,
        supplier_id=po.supplier_id,
        delivery_note=grn_data.get('delivery_note', ''),
        vehicle_reg=grn_data.get('vehicle_reg', '')
    )
    db.session.add(grn)
    db.session.flush()

    total_accepted_value = 0.0
    all_lines_fully_received = True

    for item in grn_data['receipt_lines']:
        po_line = PurchaseOrderLine.query.get(item['po_line_id'])
        if not po_line:
            continue

        qty_received = float(item.get('qty_received', 0.0))
        qty_rejected = float(item.get('qty_rejected', 0.0))
        qty_accepted = qty_received - qty_rejected

        if qty_accepted > 0:
            # 1. Create the GRN Line for audit
            grn_line = GoodsReceiptLine(
                grn_id=grn.id,
                po_line_id=po_line.id,
                ingredient_id=po_line.ingredient_id,
                qty_received=qty_received,
                qty_accepted=qty_accepted,
                qty_rejected=qty_rejected,
                batch_number=item.get('batch_number', ''),
                unit_cost=po_line.unit_cost
            )
            db.session.add(grn_line)
            
            total_accepted_value += (qty_accepted * po_line.unit_cost)

            # 2. Immutable Stock Movement (This IS the stock balance driver)
            movement = StockMovement(
                ingredient_id=po_line.ingredient_id,
                movement_type='RECEIPT',
                qty_kg=qty_accepted,
                reference_id=grn_number
            )
            db.session.add(movement)
            
            # (Optional Sync) If you still maintain a cached quantity on the FeedIngredient model for fast UI loading:
            ing = FeedIngredient.query.get(po_line.ingredient_id)
            if ing:
                ing.stock_quantity_kg += qty_accepted
                # Safely update average cost based on new inventory intake
                ing.cost_per_kg = po_line.unit_cost 

        # Calculate if PO is fully received by summing all past GRNs for this line
        past_receipts = db.session.query(db.func.sum(GoodsReceiptLine.qty_accepted)).filter_by(po_line_id=po_line.id).scalar() or 0.0
        total_accepted_historically = past_receipts + qty_accepted
        
        if total_accepted_historically < po_line.qty_ordered:
            all_lines_fully_received = False

    # Update PO Status dynamically
    po.status = 'FULLY_RECEIVED' if all_lines_fully_received else 'PARTIALLY_RECEIVED'

    # 3. Post to General Ledger (GRNI Accrual)
    if total_accepted_value > 0:
        try:
            from services.ledger_service import post_gl_entry
            # Debit: Raw Materials Inventory Asset
            post_gl_entry(grn_number, '1200', total_accepted_value, 0.0, 'GRN', grn.id)
            # Credit: Goods Received Not Invoiced (Liability)
            post_gl_entry(grn_number, '2010', 0.0, total_accepted_value, 'GRN', grn.id)
        except Exception as e:
            print(f"GRNI Posting Error: {e}")

    db.session.commit()
    return grn_number