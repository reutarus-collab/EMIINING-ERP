from services.db import db
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
class FeedIngredient(db.Model):
    __tablename__ = 'feed_ingredients'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50))

    # --- UOM CONVERSION COLUMNS ---
    purchase_uom = db.Column(db.String(20), default='KG')
    stock_uom = db.Column(db.String(20), default='KG')
    conversion_type = db.Column(db.String(20), default='FIXED')
    conversion_factor = db.Column(db.Float, default=1.0)

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

class CustomerPayment(db.Model):
    __tablename__ = 'customer_payments'
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(50), unique=True, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=False)
    till_session_id = db.Column(db.Integer, db.ForeignKey('till_sessions.id'))
    amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(20), nullable=False)
    created_by = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class Location(db.Model):
    __tablename__ = 'locations'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    code = db.Column(db.String(20))
    location_type = db.Column(db.String(20))

class LocationStock(db.Model):
    __tablename__ = 'location_stocks'
    id = db.Column(db.Integer, primary_key=True)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=False)
    ingredient_id = db.Column(db.Integer, db.ForeignKey('feed_ingredients.id'), nullable=False)
    quantity_kg = db.Column(db.Float, nullable=False, default=0.0)
    reserved_quantity_kg = db.Column(db.Float, nullable=False, default=0.0)
    unit_cost_per_kg = db.Column(db.Float, nullable=False, default=0.0)
    __table_args__ = (db.UniqueConstraint('location_id', 'ingredient_id', name='uq_location_stock_item'),)

class Supplier(db.Model):
    __tablename__ = 'suppliers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    contact_info = db.Column(db.String(255)) # Legacy field
    
    # --- NEW SUPPLIER FIELDS ---
    phone = db.Column(db.String(50))
    location = db.Column(db.String(100))
    items_dealing = db.Column(db.String(255))
    description = db.Column(db.Text)
    
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
    __table_args__ = (db.Index('uq_till_open_key', 'open_key', unique=True),)
    id = db.Column(db.Integer, primary_key=True)
    cashier_name = db.Column(db.String(50))
    opening_cash = db.Column(db.Float, default=0.0)
    expected_cash = db.Column(db.Float, default=0.0)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))
    opened_at = db.Column(db.DateTime, default=datetime.utcnow)
    closed_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), nullable=False, default='OPEN')
    counted_cash = db.Column(db.Float)
    cash_variance = db.Column(db.Float)
    open_key = db.Column(db.String(120))

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
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))
    till_session_id = db.Column(db.Integer, db.ForeignKey('till_sessions.id'))

class OrderLine(db.Model):
    __tablename__ = 'order_lines'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer)
    ingredient_id = db.Column(db.Integer)
    unit_type = db.Column(db.String(20))
    qty_entered = db.Column(db.Float, default=0.0)
    subtotal = db.Column(db.Float, default=0.0)
    unit_cost_per_kg = db.Column(db.Float, nullable=False, default=0.0)

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
    request_hash = db.Column(db.String(64))
    created_by = db.Column(db.String(50))
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class StockMovement(db.Model):
    __tablename__ = 'stock_movements'
    id = db.Column(db.Integer, primary_key=True)
    ingredient_id = db.Column(db.Integer)
    movement_type = db.Column(db.String(50))
    qty_kg = db.Column(db.Float, default=0.0)
    reference_id = db.Column(db.String(100))
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))
    reason = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=True)

class InventoryTransfer(db.Model):
    __tablename__ = 'inventory_transfers'
    id = db.Column(db.Integer, primary_key=True)
    transfer_no = db.Column(db.String(50), unique=True, nullable=False)
    from_location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=False)
    to_location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=False)
    ingredient_id = db.Column(db.Integer, db.ForeignKey('feed_ingredients.id'), nullable=False)
    quantity_kg = db.Column(db.Float, nullable=False)
    unit_cost_per_kg = db.Column(db.Float, nullable=False, default=0.0)
    received_quantity_kg = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(30), nullable=False, default='IN_TRANSIT')
    reason = db.Column(db.String(200), nullable=False)
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    received_by = db.Column(db.String(50))
    completed_at = db.Column(db.DateTime)
    shortfall_quantity_kg = db.Column(db.Float, nullable=False, default=0.0)
    shortfall_reason = db.Column(db.String(200))
    closed_by = db.Column(db.String(50))

class InventoryTransferReceipt(db.Model):
    __tablename__ = 'inventory_transfer_receipts'
    id = db.Column(db.Integer, primary_key=True)
    transfer_id = db.Column(db.Integer, db.ForeignKey('inventory_transfers.id'), nullable=False)
    quantity_kg = db.Column(db.Float, nullable=False)
    received_by = db.Column(db.String(50), nullable=False)
    received_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    note = db.Column(db.String(200))

class OperatingExpense(db.Model):
    __tablename__ = 'operating_expenses'
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(50), unique=True, nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=False)
    till_session_id = db.Column(db.Integer, db.ForeignKey('till_sessions.id'))
    category = db.Column(db.String(30), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(20), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    created_by = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class OwnerWithdrawal(db.Model):
    __tablename__ = 'owner_withdrawals'
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(50), unique=True, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(20), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))
    till_session_id = db.Column(db.Integer, db.ForeignKey('till_sessions.id'))
    reason = db.Column(db.String(200), nullable=False)
    created_by = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class EquipmentPurchase(db.Model):
    __tablename__ = 'equipment_purchases'
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(50), unique=True, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(20), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))
    till_session_id = db.Column(db.Integer, db.ForeignKey('till_sessions.id'))
    description = db.Column(db.String(200), nullable=False)
    created_by = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class CashWalkOpening(db.Model):
    __tablename__ = 'cash_walk_openings'
    id = db.Column(db.Integer, primary_key=True)
    as_of_date = db.Column(db.Date, unique=True, nullable=False)
    cash = db.Column(db.Float, nullable=False, default=0.0)
    inventory = db.Column(db.Float, nullable=False, default=0.0)
    debtors = db.Column(db.Float, nullable=False, default=0.0)
    creditors = db.Column(db.Float, nullable=False, default=0.0)
    equipment = db.Column(db.Float, nullable=False, default=0.0)
    created_by = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class TillCashMovement(db.Model):
    __tablename__ = 'till_cash_movements'
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(50), unique=True, nullable=False)
    till_session_id = db.Column(db.Integer, db.ForeignKey('till_sessions.id'), nullable=False)
    movement_type = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    reason = db.Column(db.String(200), nullable=False)
    created_by = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class SalesRefund(db.Model):
    __tablename__ = 'sales_refunds'
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(50), unique=True, nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('order_headers.id'), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=False)
    till_session_id = db.Column(db.Integer, db.ForeignKey('till_sessions.id'))
    amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.String(200), nullable=False)
    created_by = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class SalesRefundLine(db.Model):
    __tablename__ = 'sales_refund_lines'
    id = db.Column(db.Integer, primary_key=True)
    refund_id = db.Column(db.Integer, db.ForeignKey('sales_refunds.id'), nullable=False)
    order_line_id = db.Column(db.Integer, db.ForeignKey('order_lines.id'), nullable=False)
    ingredient_id = db.Column(db.Integer, db.ForeignKey('feed_ingredients.id'), nullable=False)
    quantity_kg = db.Column(db.Float, nullable=False)
    unit_cost_per_kg = db.Column(db.Float, nullable=False, default=0.0)

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
    run_no = db.Column(db.String(50), unique=True)
    customer_name = db.Column(db.String(100), nullable=False, default='Walk-in customer')
    customer_phone = db.Column(db.String(20))
    grain_description = db.Column(db.String(100), nullable=False, default='Maize')
    input_qty_kg = db.Column(db.Float, nullable=False, default=0.0)
    output_qty_kg = db.Column(db.Float, nullable=False, default=0.0)
    variance_loss_kg = db.Column(db.Float, nullable=False, default=0.0)
    notes = db.Column(db.String(200))
    service_sale_reference = db.Column(db.String(50))
    created_by = db.Column(db.String(50))
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class ProductionRun(db.Model):
    __tablename__ = 'production_runs'
    id = db.Column(db.Integer, primary_key=True)
    batch_no = db.Column(db.String(50), unique=True, nullable=False)
    formula_name = db.Column(db.String(100), nullable=False)
    output_ingredient_id = db.Column(db.Integer, nullable=False)
    planned_output_kg = db.Column(db.Float, nullable=False)
    actual_output_kg = db.Column(db.Float, nullable=False)
    total_input_kg = db.Column(db.Float, nullable=False)
    loss_kg = db.Column(db.Float, nullable=False, default=0.0)
    loss_pct = db.Column(db.Float, nullable=False, default=0.0)
    loss_reason = db.Column(db.String(200))
    total_input_cost = db.Column(db.Float, nullable=False, default=0.0)
    loss_cost = db.Column(db.Float, nullable=False, default=0.0)
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))

class ProductionRunLine(db.Model):
    __tablename__ = 'production_run_lines'
    id = db.Column(db.Integer, primary_key=True)
    production_run_id = db.Column(db.Integer, nullable=False)
    ingredient_id = db.Column(db.Integer, nullable=False)
    planned_qty_kg = db.Column(db.Float, nullable=False, default=0.0)
    actual_qty_kg = db.Column(db.Float, nullable=False)
    cost_per_kg = db.Column(db.Float, nullable=False, default=0.0)
    line_cost = db.Column(db.Float, nullable=False, default=0.0)
    
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
    
    # --- NEW: GRN TRACKING COLUMNS ---
    qty_received = db.Column(db.Float, default=0.0)
    qty_rejected = db.Column(db.Float, default=0.0)

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
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))

class GoodsReceiptLine(db.Model):
    __tablename__ = 'goods_receipt_lines'
    id = db.Column(db.Integer, primary_key=True)
    grn_id = db.Column(db.Integer, nullable=False)
    po_line_id = db.Column(db.Integer, nullable=False)
    ingredient_id = db.Column(db.Integer, nullable=False)
    qty_received = db.Column(db.Float, default=0.0)
    qty_accepted = db.Column(db.Float, default=0.0)
    qty_rejected = db.Column(db.Float, default=0.0)
    qty_rejected_po_uom = db.Column(db.Float, default=0.0)
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
    
class User(db.Model):
    __tablename__ = 'app_users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(100))
    role = db.Column(db.String(20), nullable=False)       # admin / accountant / sales / warehouse
    location = db.Column(db.String(30), nullable=False)   # factory / branch1
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    failed_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime)

    def set_password(self, pw):
        if not pw.isdigit() or len(pw) != 4:
            raise ValueError('User PINs must be exactly four numeric digits.')
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class SupplierTxn(db.Model):
    __tablename__ = 'supplier_txns'
    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, nullable=False)
    po_id = db.Column(db.Integer)
    ref = db.Column(db.String(50))
    kind = db.Column(db.String(20), nullable=False)   # PURCHASE (owed) or PAYMENT
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(20))
    note = db.Column(db.String(200))
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ItemPrice(db.Model):
    __tablename__ = 'item_prices'
    id = db.Column(db.Integer, primary_key=True)
    ingredient_id = db.Column(db.Integer, nullable=False)
    pack_kg = db.Column(db.Float, nullable=False)   # 50, 70 ... (1 kg uses retail_price_per_kg)
    price = db.Column(db.Float, nullable=False)     # price of ONE pack
