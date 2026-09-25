import os
from app import app
from services.db import db
from services.models import FeedIngredient, Customer, Location, Supplier, Account, AnimalRequirement, TillSession

def unified_seed():
    with app.app_context():
        print("--- 1. Wiping DB & Creating Complete Schema ---")
        db.drop_all()
        db.create_all()

        print("--- 2. Enterprise Setup (Locations, Accounts, Suppliers) ---")
        factory = Location(name="Emining Main Factory", code="FAC-01", location_type="FACTORY")
        branch = Location(name="Mogotio Retail Store", code="RET-01", location_type="BRANCH_STORE")
        db.session.add_all([factory, branch])
        
        db.session.add_all([
            Account(account_code="1000", name="Cash/Bank", category="ASSET"),
            Account(account_code="1200", name="Inventory Asset", category="ASSET"),
            Account(account_code="1300", name="Accounts Receivable", category="ASSET"),
            Account(account_code="2000", name="Accounts Payable", category="LIABILITY"),
            Account(account_code="4000", name="Sales Revenue", category="REVENUE")
        ])
        
        suppliers = [Supplier(name=f"Supplier {i}", phone=f"070000000{i}", balance_due=0.0) for i in range(1, 6)]
        db.session.add_all(suppliers)
        db.session.commit()

        print("--- 3. Inventory & Formulator Data ---")
        items = [
            FeedIngredient(name="Layer Mash", category="Finished Feed", cost_per_kg=55.0, stock_quantity_kg=20000.0, retail_price_per_kg=65.0, crude_protein_pct=16.0, bag_size_kg=70.0),
            FeedIngredient(name="Maize Grain", category="Raw - Energy", cost_per_kg=35.0, stock_quantity_kg=50000.0, retail_price_per_kg=40.0, metabolizable_energy_mcal=3.3, bag_size_kg=90.0),
            FeedIngredient(name="Ochonga (Fishmeal)", category="Raw - Protein", cost_per_kg=180.0, stock_quantity_kg=5000.0, retail_price_per_kg=200.0, crude_protein_pct=55.0, bag_size_kg=50.0)
        ]
        db.session.add_all(items)
        
        db.session.add_all([
            AnimalRequirement(species_stage="Dairy Cattle (High Yield 15L+)", min_cp=16.0, min_me=2.4),
            AnimalRequirement(species_stage="Poultry Broiler Starter", min_cp=22.0, min_me=3.0)
        ])
        db.session.commit()

        print("--- 4. Customers ---")
        c1 = Customer(name="Test Cust 1", phone="0711111111", location="Baringo", current_balance=65.0, credit_limit=50000.0)
        c2 = Customer(name="Test Cust 2", phone="0711222222", location="Nakuru", current_balance=405.0, credit_limit=50000.0)
        c3 = Customer(name="Test Cust 3", phone="0711333333", location="Mogotio", current_balance=0.0, credit_limit=50000.0)
        db.session.add_all([c1, c2, c3])
        db.session.commit()

        db.session.add(TillSession(cashier_name="admin", opening_cash=5000.0))
        db.session.commit()

        print("\n✅ SUCCESS: Full ERP System Seed Complete!")

if __name__ == '__main__':
    try:
        unified_seed()
    except Exception as e:
        print(f"\n❌ SEED FAILED! Error details:")
        print(str(e))