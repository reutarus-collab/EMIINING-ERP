from services.db import db
from datetime import datetime

class FeedIngredient(db.Model):
    __tablename__ = 'feed_ingredients'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50))
    cost_per_kg = db.Column(db.Float, default=0.0)
    stock_quantity_kg = db.Column(db.Float, default=0.0)
    reserved_quantity_kg = db.Column(db.Float, default=0.0)
    bag_size_kg = db.Column(db.Float, default=70.0)
    retail_price_per_kg = db.Column(db.Float, default=0.0)
    crude_protein_pct = db.Column(db.Float, default=0.0)
    metabolizable_energy_mcal = db.Column(db.Float, default=0.0)

class Customer(db.Model):
    __tablename__ = 'customers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    location = db.Column(db.String(100))
    customer_type = db.Column(db.String(20), default='RETAIL')
    current_balance = db.Column(db.Float, default=0.0)
    credit_limit = db.Column(db.Float, default=0.0)

class Location(db.Model):
    __tablename__ = 'locations'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    code = db.Column(db.String(20))
    location_type = db.Column(db.String(20))

class Supplier(db.Model):
    __tablename__ = 'suppliers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    contact_info = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
class Account(db.Model):
    __tablename__ = 'accounts'
    id = db.Column(db.Integer, primary_key=True)
    account_code = db.Column(db.String(20))
    name = db.Column(db.String(100))
    category = db.Column(db.String(50))

class AnimalRequirement(db.Model):
    __tablename__ = 'animal_requirements'
    id = db.Column(db.Integer, primary_key=True)
    species_stage = db.Column(db.String(100))
    min_cp = db.Column(db.Float, default=0.0)
    min_me = db.Column(db.Float, default=0.0)

class TillSession(db.Model):
    __tablename__ = 'till_sessions'
    id = db.Column(db.Integer, primary_key=True)
    cashier_name = db.Column(db.String(50))
    opening_cash = db.Column(db.Float, default=0.0)
    expected_cash = db.Column(db.Float, default=0.0)

class OrderHeader(db.Model):
    __tablename__ = 'order_headers'
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.String(50))
    customer_id = db.Column(db.Integer)
    total_amount = db.Column(db.Float, default=0.0)
    discount_amount = db.Column(db.Float, default=0.0)
    paid_amount = db.Column(db.Float, default=0.0)
    change_due = db.Column(db.Float, default=0.0)
    credit_amount = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='COMPLETED')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class OrderLine(db.Model):
    __tablename__ = 'order_lines'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer)
    ingredient_id = db.Column(db.Integer)
    unit_type = db.Column(db.String(20))
    qty_entered = db.Column(db.Float, default=0.0)
    subtotal = db.Column(db.Float, default=0.0)

class PaymentSplit(db.Model):
    __tablename__ = 'payment_splits'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer)
    payment_method = db.Column(db.String(50))
    amount = db.Column(db.Float, default=0.0)
    reference = db.Column(db.String(100))

class IdempotencyKey(db.Model):
    __tablename__ = 'idempotency_keys'
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True)
    response_json = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class StockMovement(db.Model):
    __tablename__ = 'stock_movements'
    id = db.Column(db.Integer, primary_key=True)
    ingredient_id = db.Column(db.Integer)
    movement_type = db.Column(db.String(50))
    qty_kg = db.Column(db.Float, default=0.0)
    reference_id = db.Column(db.String(100))

class LedgerEntry(db.Model):
    __tablename__ = 'ledger_entries'
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.String(50))
    amount = db.Column(db.Float, default=0.0)
    idempotency_key = db.Column(db.String(100))
    description = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class ProductionBatch(db.Model):
    __tablename__ = 'production_batches'
    id = db.Column(db.Integer, primary_key=True)
    batch_no = db.Column(db.String(50))
    formula_name = db.Column(db.String(100))
    target_output_ingredient_id = db.Column(db.Integer)
    planned_output_kg = db.Column(db.Float, default=0.0)
    actual_output_kg = db.Column(db.Float, default=0.0)

class MillingRun(db.Model):
    __tablename__ = 'milling_runs'
    id = db.Column(db.Integer, primary_key=True)
    run_no = db.Column(db.String(50))
    input_ingredient_id = db.Column(db.Integer)
    output_ingredient_id = db.Column(db.Integer)
    input_qty_kg = db.Column(db.Float, default=0.0)
    output_qty_kg = db.Column(db.Float, default=0.0)
    variance_loss_kg = db.Column(db.Float, default=0.0)
    
class PurchaseOrderHeader(db.Model):
    __tablename__ = 'purchase_order_headers'
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50), unique=True, nullable=False)
    supplier_id = db.Column(db.Integer, nullable=False)
    location_id = db.Column(db.String(50), default='Main Store')
    order_date = db.Column(db.DateTime, default=datetime.utcnow)
    expected_date = db.Column(db.DateTime)
    payment_terms = db.Column(db.String(50)) # e.g., '30 Days'
    total_amount = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='DRAFT') # DRAFT, APPROVED, PARTIALLY_RECEIVED, FULLY_RECEIVED, CANCELLED

class PurchaseOrderLine(db.Model):
    __tablename__ = 'purchase_order_lines'
    id = db.Column(db.Integer, primary_key=True)
    po_header_id = db.Column(db.Integer, nullable=False)
    ingredient_id = db.Column(db.Integer, nullable=False)
    qty_ordered = db.Column(db.Float, default=0.0)
    unit_cost = db.Column(db.Float, default=0.0)
    subtotal = db.Column(db.Float, default=0.0)

class GoodsReceiptNote(db.Model):
    __tablename__ = 'goods_receipt_notes'
    id = db.Column(db.Integer, primary_key=True)
    grn_number = db.Column(db.String(50), unique=True, nullable=False)
    po_header_id = db.Column(db.Integer, nullable=False)
    supplier_id = db.Column(db.Integer, nullable=False)
    received_date = db.Column(db.DateTime, default=datetime.utcnow)
    delivery_note = db.Column(db.String(100))
    vehicle_reg = db.Column(db.String(20))
    status = db.Column(db.String(20), default='POSTED')

class GoodsReceiptLine(db.Model):
    __tablename__ = 'goods_receipt_lines'
    id = db.Column(db.Integer, primary_key=True)
    grn_id = db.Column(db.Integer, nullable=False)
    po_line_id = db.Column(db.Integer, nullable=False)
    ingredient_id = db.Column(db.Integer, nullable=False)
    qty_received = db.Column(db.Float, default=0.0)
    qty_accepted = db.Column(db.Float, default=0.0)
    qty_rejected = db.Column(db.Float, default=0.0)
    batch_number = db.Column(db.String(50))
    expiry_date = db.Column(db.DateTime)
    unit_cost = db.Column(db.Float, default=0.0) # Captured at time of receipt
    
class GeneralLedgerEntry(db.Model):
    __tablename__ = 'general_ledger_entries'
    id = db.Column(db.Integer, primary_key=True)
    transaction_ref = db.Column(db.String(50), nullable=False)  # Fixed column name
    account_code = db.Column(db.String(20), nullable=False)
    debit = db.Column(db.Float, default=0.0)
    credit = db.Column(db.Float, default=0.0)
    source_type = db.Column(db.String(50))
    source_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)