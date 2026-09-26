import uuid
from datetime import datetime, timedelta
from app import app
from services.db import db
from services.models import Supplier, PurchaseOrderHeader, PurchaseOrderLine, FeedIngredient

with app.app_context():
    print("Seeding Purchasing Data...")

    # 1. Add Suppliers
    supplier_data = [
        {"name": "ABC Suppliers Ltd", "contact": "0711000111 - Nakuru"},
        {"name": "Rift Valley Grains", "contact": "0722000222 - Eldoret"},
        {"name": "Mombasa Premix Co.", "contact": "0733000333 - Mombasa"}
    ]
    
    for s_data in supplier_data:
        if not Supplier.query.filter_by(name=s_data["name"]).first():
            db.session.add(Supplier(name=s_data["name"], contact_info=s_data["contact"]))
    db.session.commit()
    print("✅ Suppliers added.")

    # 2. Get some existing ingredients to order (Fallback to whatever is in DB)
    supplier1 = Supplier.query.filter_by(name="ABC Suppliers Ltd").first()
    supplier2 = Supplier.query.filter_by(name="Rift Valley Grains").first()
    
    ingredients = FeedIngredient.query.limit(2).all()
    
    if len(ingredients) > 0:
        # --- Create PO 1 (Pending Receipt) ---
        po1 = PurchaseOrderHeader(
            po_number=f"PO-{uuid.uuid4().hex[:6].upper()}",
            supplier_id=supplier1.id,
            location_id='Main Store',
            order_date=datetime.utcnow() - timedelta(days=2),
            status='APPROVED'
        )
        db.session.add(po1)
        db.session.flush()

        item1 = ingredients[0]
        qty1, cost1 = 2000.0, 45.0
        db.session.add(PurchaseOrderLine(
            po_header_id=po1.id, ingredient_id=item1.id,
            qty_ordered=qty1, unit_cost=cost1, subtotal=(qty1 * cost1)
        ))
        po1.total_amount = qty1 * cost1

        # --- Create PO 2 (Pending Receipt - Multiple Items) ---
        if len(ingredients) > 1:
            po2 = PurchaseOrderHeader(
                po_number=f"PO-{uuid.uuid4().hex[:6].upper()}",
                supplier_id=supplier2.id,
                location_id='Main Store',
                order_date=datetime.utcnow() - timedelta(days=1),
                status='APPROVED'
            )
            db.session.add(po2)
            db.session.flush()

            item2 = ingredients[1]
            qty2, cost2 = 500.0, 120.0
            db.session.add(PurchaseOrderLine(
                po_header_id=po2.id, ingredient_id=item1.id,
                qty_ordered=1000.0, unit_cost=46.0, subtotal=(1000.0 * 46.0)
            ))
            db.session.add(PurchaseOrderLine(
                po_header_id=po2.id, ingredient_id=item2.id,
                qty_ordered=qty2, unit_cost=cost2, subtotal=(qty2 * cost2)
            ))
            po2.total_amount = (1000.0 * 46.0) + (qty2 * cost2)

        db.session.commit()
        print("✅ Pending Purchase Orders created.")
    else:
        print("⚠️ No ingredients found in the database. Please add inventory first.")

    print("🎉 Seeding Complete! You can now test the GRN workflow.")