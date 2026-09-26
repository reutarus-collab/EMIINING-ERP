from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

def init_db(app):
    db.init_app(app)
    with app.app_context():
        db.create_all()
        # seed_data()  <-- Comment this out or delete it completely
def seed_data():
    from services.models import Account, Location, Supplier, FeedIngredient
    
    if Account.query.count() == 0:
        for acc in [
            {"code": "1000", "name": "Cash and Bank", "category": "ASSET"},
            {"code": "1200", "name": "Inventory Asset", "category": "ASSET"},
            {"code": "1300", "name": "Accounts Receivable", "category": "ASSET"},
            {"code": "2000", "name": "Accounts Payable", "category": "LIABILITY"},
            {"code": "3000", "name": "Owner's Equity", "category": "EQUITY"},
            {"code": "4000", "name": "Sales Revenue", "category": "INCOME"},
            {"code": "5000", "name": "Cost of Goods Sold (COGS)", "category": "EXPENSE"},
            {"code": "5100", "name": "Shrinkage & Variance Loss", "category": "EXPENSE"}
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