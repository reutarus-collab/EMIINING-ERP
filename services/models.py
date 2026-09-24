from datetime import datetime, timezone
from services.db import db

# ==========================================
# 1. ENTERPRISE ORG & DOUBLE-ENTRY GL
# ==========================================
class Location(db.Model):
    __tablename__ = 'locations'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)
    location_type = db.Column(db.String(50), nullable=False)

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=True)

class Supplier(db.Model):
    __tablename__ = 'suppliers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(30))
    address = db.Column(db.String(200))
    balance_due = db.Column(db.Float, default=0.0)

class Account(db.Model):
    __tablename__ = 'accounts'
    id = db.Column(db.Integer, primary_key=True)
    account_code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(30), nullable=False) 

class GeneralLedgerEntry(db.Model):
    __tablename__ = 'gl_entries'
    id = db.Column(db.Integer, primary_key=True)
    transaction_ref = db.Column(db.String(100), nullable=False)
    account_code = db.Column(db.String(20), db.ForeignKey('accounts.account_code'), nullable=False)
    debit = db.Column(db.Float, default=0.0)
    credit = db.Column(db.Float, default=0.0)
    source_module = db.Column(db.String(50))
    source_id = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

# ==========================================
# 2. INVENTORY (STOCK)
# ==========================================
class FeedIngredient(db.Model):
    __tablename__ = 'feed_ingredients'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), default='General Feed')
    cost_per_kg = db.Column(db.Float, nullable=False, default=0.0)
    stock_quantity_kg = db.Column(db.Float, default=0.0)
    reserved_quantity_kg = db.Column(db.Float, default=0.0)
    bag_size_kg = db.Column(db.Float, default=70.0)
    retail_price_per_kg = db.Column(db.Float, default=0.0)
    crude_protein_pct = db.Column(db.Float, default=0.0)
    metabolizable_energy_mcal = db.Column(db.Float, default=0.0)

class StockMovement(db.Model):
    __tablename__ = 'stock_movements'
    id = db.Column(db.Integer, primary_key=True)
    ingredient_id = db.Column(db.Integer, db.ForeignKey('feed_ingredients.id'), nullable=False)
    movement_type = db.Column(db.String(50), nullable=False)
    qty_kg = db.Column(db.Float, nullable=False)
    reference_id = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

# ==========================================
# 3. PURCHASING & TRANSFERS
# ==========================================
class PurchaseOrderHeader(db.Model):
    __tablename__ = 'purchase_order_headers'
    id = db.Column(db.Integer, primary_key=True)
    po_no = db.Column(db.String(50), unique=True, nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=False)
    total_amount = db.Column(db.Float, default=0.0)
    amount_paid = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(30), default='ISSUED') 
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class PurchaseOrderLine(db.Model):
    __tablename__ = 'purchase_order_lines'
    id = db.Column(db.Integer, primary_key=True)
    po_id = db.Column(db.Integer, db.ForeignKey('purchase_order_headers.id'), nullable=False)
    ingredient_id = db.Column(db.Integer, db.ForeignKey('feed_ingredients.id'), nullable=False)
    ordered_qty_kg = db.Column(db.Float, nullable=False)
    received_qty_kg = db.Column(db.Float, default=0.0)
    unit_cost = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, nullable=False)

class StockTransfer(db.Model):
    __tablename__ = 'stock_transfers'
    id = db.Column(db.Integer, primary_key=True)
    transfer_no = db.Column(db.String(50), unique=True, nullable=False)
    source_location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=False)
    dest_location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=False)
    status = db.Column(db.String(30), default='DISPATCHED')

# ==========================================
# 4. POINT OF SALE (POS) & CUSTOMERS
# ==========================================
class Customer(db.Model):
    __tablename__ = 'customers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    location = db.Column(db.String(100), nullable=True) # <--- THIS WAS MISSING
    customer_type = db.Column(db.String(20), default='RETAIL')
    current_balance = db.Column(db.Float, default=0.0)
    credit_limit = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    
class OrderHeader(db.Model):
    __tablename__ = 'order_headers'
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.String(100), unique=True, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=True)
    total_amount = db.Column(db.Float, nullable=False)
    paid_amount = db.Column(db.Float, nullable=False)
    credit_amount = db.Column(db.Float, default=0.0)
    change_due = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class OrderLine(db.Model):
    __tablename__ = 'order_lines'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order_headers.id'), nullable=False)
    ingredient_id = db.Column(db.Integer, db.ForeignKey('feed_ingredients.id'), nullable=False)
    unit_type = db.Column(db.String(20), nullable=False)
    qty_entered = db.Column(db.Float, nullable=False)
    total_kg = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, nullable=False)

class PaymentSplit(db.Model):
    __tablename__ = 'payment_splits'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order_headers.id'), nullable=False)
    payment_method = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    reference = db.Column(db.String(100))

class TillSession(db.Model):
    __tablename__ = 'till_sessions'
    id = db.Column(db.Integer, primary_key=True)
    cashier_name = db.Column(db.String(80), nullable=False)
    opening_cash = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='OPEN')
    opened_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class IdempotencyKey(db.Model):
    __tablename__ = 'idempotency_keys'
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    response_json = db.Column(db.JSON, nullable=False)

# ==========================================
# 5. FORMULATOR & FACTORY PRODUCTION
# ==========================================
class AnimalRequirement(db.Model):
    __tablename__ = 'animal_requirements'
    id = db.Column(db.Integer, primary_key=True)
    species_stage = db.Column(db.String(100), nullable=False, unique=True)
    min_cp = db.Column(db.Float, default=0.0)
    min_me = db.Column(db.Float, default=0.0)

class ProductionBatch(db.Model):
    __tablename__ = 'production_batches'
    id = db.Column(db.Integer, primary_key=True)
    batch_no = db.Column(db.String(50), unique=True, nullable=False)
    formula_code = db.Column(db.String(50), nullable=False)
    target_output_ingredient_id = db.Column(db.Integer, db.ForeignKey('feed_ingredients.id'), nullable=False)
    planned_output_kg = db.Column(db.Float, nullable=False)
    actual_output_kg = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='COMPLETED')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class MillingRun(db.Model):
    __tablename__ = 'milling_runs'
    id = db.Column(db.Integer, primary_key=True)
    run_no = db.Column(db.String(50), unique=True, nullable=False)
    input_ingredient_id = db.Column(db.Integer, db.ForeignKey('feed_ingredients.id'), nullable=False)
    output_ingredient_id = db.Column(db.Integer, db.ForeignKey('feed_ingredients.id'), nullable=False)
    input_qty_kg = db.Column(db.Float, nullable=False)
    output_qty_kg = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))