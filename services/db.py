from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text

db = SQLAlchemy()

def init_db(app):
    db.init_app(app)
    with app.app_context():
        db.create_all()
        _migrate_location_schema()
        # seed_data()  <-- Comment this out or delete it completely

def _migrate_location_schema():
    """Small additive migration for existing SQLite installs; safe to run at startup."""
    inspector = inspect(db.engine)
    additions = {
        'stock_movements': {'location_id': 'INTEGER', 'reason': 'VARCHAR(200)'},
        'order_headers': {'location_id': 'INTEGER', 'till_session_id': 'INTEGER'},
        'till_sessions': {'location_id': 'INTEGER', 'opened_at': 'DATETIME', 'closed_at': 'DATETIME', 'status': "VARCHAR(20) DEFAULT 'OPEN'", 'counted_cash': 'FLOAT', 'cash_variance': 'FLOAT'},
        'production_runs': {'location_id': 'INTEGER'},
        'goods_receipt_lines': {'qty_rejected_po_uom': 'FLOAT'},
        'goods_receipt_notes': {'location_id': 'INTEGER'},
        'location_stocks': {'reserved_quantity_kg': 'FLOAT DEFAULT 0'},
    }
    if db.engine.dialect.name != 'sqlite':
        additions['till_sessions']['opened_at'] = 'TIMESTAMP'
        additions['till_sessions']['closed_at'] = 'TIMESTAMP'
    with db.engine.begin() as conn:
        for table, columns in additions.items():
            if table not in inspector.get_table_names():
                continue
            existing = {c['name'] for c in inspector.get_columns(table)}
            for name, declaration in columns.items():
                if name not in existing:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}'))
    from services.models import FeedIngredient, Location, LocationStock, PurchaseOrderHeader, Account, StockMovement, GoodsReceiptNote
    if Location.query.count() == 0:
        legacy = Location(name='Legacy Main Store', code='LEGACY-01', location_type='STORE')
        db.session.add(legacy)
        db.session.flush()
    # Existing single-balance stock is assigned to one location. It cannot be
    # split between outlets automatically; users must transfer/adjust it after
    # configuring their real locations.
    migration_location = (Location.query.filter(Location.location_type.ilike('%FACTORY%')).first()
                          or Location.query.order_by(Location.id).first())
    if migration_location:
        for item in FeedIngredient.query.all():
            row = LocationStock.query.filter_by(location_id=migration_location.id, ingredient_id=item.id).first()
            if not row:
                db.session.add(LocationStock(location_id=migration_location.id,
                                             ingredient_id=item.id,
                                             quantity_kg=item.stock_quantity_kg or 0.0))
                if (item.stock_quantity_kg or 0.0) > 0:
                    db.session.add(StockMovement(ingredient_id=item.id, location_id=migration_location.id,
                                                 movement_type='OPENING_BALANCE', qty_kg=item.stock_quantity_kg,
                                                 reference_id='MIGRATION-OPENING', reason='Legacy global stock assigned to initial outlet'))
        # Historical records had no outlet dimension. Assign them to the legacy
        # stock's location so reports retain a consistent default scope.
        db.session.execute(text('UPDATE order_headers SET location_id = :loc WHERE location_id IS NULL'), {'loc': migration_location.id})
        db.session.execute(text('UPDATE production_runs SET location_id = :loc WHERE location_id IS NULL'), {'loc': migration_location.id})
        db.session.execute(text("UPDATE till_sessions SET location_id = :loc, status = 'CLOSED' WHERE location_id IS NULL"), {'loc': migration_location.id})
        for po in PurchaseOrderHeader.query.all():
            try:
                existing_location = db.session.get(Location, int(po.location_id))
            except (TypeError, ValueError):
                existing_location = next((loc for loc in Location.query.all()
                                          if (po.location_id or '').strip().lower() in
                                          ((loc.name or '').lower(), (loc.code or '').lower())), None)
            po.location_id = str((existing_location or migration_location).id)
        db.session.flush()
        for grn in GoodsReceiptNote.query.filter_by(location_id=None).all():
            po = db.session.get(PurchaseOrderHeader, grn.po_header_id)
            if po:
                try:
                    grn.location_id = int(po.location_id)
                except (TypeError, ValueError):
                    grn.location_id = migration_location.id
    for code, name, category in (('1010', 'M-Pesa Clearing', 'ASSET'),
                                 ('1020', 'Bank Account', 'ASSET'),
                                 ('5200', 'Cash Over/Short', 'INCOME')):
        if not Account.query.filter_by(account_code=code).first():
            db.session.add(Account(account_code=code, name=name, category=category))
    db.session.commit()
def seed_data():
    from services.models import Account, Location, Supplier, FeedIngredient
    
    if Account.query.count() == 0:
        for acc in [
            {"code": "1000", "name": "Cash and Bank", "category": "ASSET"},
            {"code": "1010", "name": "M-Pesa Clearing", "category": "ASSET"},
            {"code": "1020", "name": "Bank Account", "category": "ASSET"},
            {"code": "1200", "name": "Inventory Asset", "category": "ASSET"},
            {"code": "1300", "name": "Accounts Receivable", "category": "ASSET"},
            {"code": "2000", "name": "Accounts Payable", "category": "LIABILITY"},
            {"code": "3000", "name": "Owner's Equity", "category": "EQUITY"},
            {"code": "4000", "name": "Sales Revenue", "category": "INCOME"},
            {"code": "5000", "name": "Cost of Goods Sold (COGS)", "category": "EXPENSE"},
            {"code": "5100", "name": "Shrinkage & Variance Loss", "category": "EXPENSE"},
            {"code": "5200", "name": "Cash Over/Short", "category": "INCOME"},
        ]:
            db.session.add(Account(account_code=acc["code"], name=acc["name"], category=acc["category"]))

    if Location.query.count() == 0:
        db.session.add(Location(name="Emining Main Factory", code="FAC-01", location_type="FACTORY"))
        db.session.add(Location(name="Mogotio Retail Branch", code="RET-01", location_type="BRANCH_STORE"))

    if Supplier.query.count() == 0:
        db.session.add(Supplier(name="Rift Valley Grain Handlers", phone="0711223344", address="Nakuru"))

    if FeedIngredient.query.count() == 0:
        items = [
            {"name": "Maize Grain (Whole)", "category": "Raw - Energy", "cost": 32.0, "retail": 38.0, "stock": 5000.0},
            {"name": "Wheat Pollard", "category": "Raw - Energy", "cost": 28.0, "retail": 34.0, "stock": 3000.0},
            {"name": "Ochonga (Fishmeal)", "category": "Raw - Protein", "cost": 110.0, "retail": 130.0, "stock": 1000.0},
            {"name": "Emining Layers Mash", "category": "Finished Feed", "cost": 40.0, "retail": 52.0, "wholesale": 47.0, "distributor": 44.0, "stock": 3500.0, "bag": 70.0},
            {"name": "Emining Dairy Meal", "category": "Finished Feed", "cost": 38.0, "retail": 48.0, "wholesale": 44.0, "distributor": 41.0, "stock": 2500.0, "bag": 50.0},
            {"name": "Custom Milling Service", "category": "Service", "cost": 0.0, "retail": 5.0, "stock": 99999.0}
        ]
        for i in items:
            db.session.add(FeedIngredient(
                name=i["name"], category=i["category"], cost_per_kg=i["cost"],
                retail_price_per_kg=i.get("retail", 0.0), wholesale_price_per_kg=i.get("wholesale", 0.0),
                distributor_price_per_kg=i.get("distributor", 0.0), stock_quantity_kg=i["stock"],
                bag_size_kg=i.get("bag", 70.0)
            ))
    db.session.commit()
