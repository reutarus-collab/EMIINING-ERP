import os, random, uuid
from app import app
from services.db import db
from services.models import FeedIngredient, Customer, Location, Supplier, Account, PurchaseOrderHeader, PurchaseOrderLine, StockMovement, TillSession, AnimalRequirement
from services.pos_service import process_full_pos_checkout, post_gl_entry

def stress_test_seed():
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
            FeedIngredient(name="Layer Mash", category="Finished Feed", cost_per_kg=55.0, stock_quantity_kg=20000.0, retail_price_per_kg=65.0, crude_protein_pct=16.0),
            FeedIngredient(name="Maize Grain", category="Raw - Energy", cost_per_kg=35.0, stock_quantity_kg=50000.0, retail_price_per_kg=40.0, metabolizable_energy_mcal=3.3),
            FeedIngredient(name="Ochonga (Fishmeal)", category="Raw - Protein", cost_per_kg=180.0, stock_quantity_kg=5000.0, retail_price_per_kg=200.0, crude_protein_pct=55.0)
        ]
        db.session.add_all(items)
        
        db.session.add_all([
            AnimalRequirement(species_stage="Dairy Cattle (High Yield 15L+)", min_cp=16.0, min_me=2.4),
            AnimalRequirement(species_stage="Poultry Broiler Starter", min_cp=22.0, min_me=3.0)
        ])
        db.session.commit()

        print("--- 4. Customers & POS Stress Test ---")
        customers = [Customer(name=f"Test Cust {i}", phone=f"0711{i}{i}{i}{i}{i}{i}", credit_limit=50000.0) for i in range(1, 6)]
        db.session.add_all(customers)
        db.session.commit()

        db.session.add(TillSession(cashier_name="admin", opening_cash=5000.0))
        db.session.commit()

        payment_methods = ['CASH', 'MPESA', 'BANK', 'CREDIT']
        for i in range(20):
            cust = random.choice(customers)
            item = random.choice(items)
            method = random.choice(payment_methods)
            qty = random.randint(1, 5)
            val = qty * item.retail_price_per_kg
            
            if method == 'CREDIT' and (cust.current_balance + val) > cust.credit_limit: method = 'CASH'

            payload = {
                "customer_id": cust.id, "discount_amount": 0,
                "cart": [{"ingredient_id": item.id, "qty": qty, "unit_type": "KG", "bag_size_kg": 1.0}],
                "payments": [{"payment_method": method, "amount": val, "reference": f"REF-{i}"}]
            }
            try:
                process_full_pos_checkout(payload)
            except Exception as e:
                pass

        print("--- 5. Purchase Orders ---")
        po = PurchaseOrderHeader(po_no="PO-TEST-1", supplier_id=suppliers[0].id, location_id=factory.id, status='ISSUED', total_amount=175000.0)
        db.session.add(po)
        db.session.flush()
        db.session.add(PurchaseOrderLine(po_id=po.id, ingredient_id=items[1].id, ordered_qty_kg=5000, unit_cost=35.0, subtotal=175000.0))
        suppliers[0].balance_due += 175000.0
        db.session.commit()

        print("--- Full ERP System Seed Complete! ---")

if __name__ == '__main__':
    stress_test_seed()