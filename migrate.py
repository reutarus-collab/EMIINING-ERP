from app import app
from services.db import db
from services.models import FeedIngredient, Location
from sqlalchemy import text

def safe_add_column(engine, table_name, column_definition):
    try:
        with engine.connect() as conn:
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_definition}"))
            conn.commit()
    except Exception as e:
        if "duplicate column name" not in str(e).lower():
            print(f"Error adding column: {e}")

def run_inventory_backfill():
    with app.app_context():
        print("1. Upgrading StockMovement schema...")
        engine = db.engine
        safe_add_column(engine, "stock_movements", "location_id INTEGER")
        safe_add_column(engine, "stock_movements", "is_variance_adjustment BOOLEAN DEFAULT 0")
        safe_add_column(engine, "stock_movements", "adjustment_reason VARCHAR(255)")

        print("2. Securing default Factory Location...")
        factory = Location.query.filter_by(location_type="FACTORY").first()
        if not factory:
            factory = Location(name="Emining Main Factory", code="FAC-01", location_type="FACTORY")
            db.session.add(factory)
            db.session.commit()

        with engine.connect() as conn:
            print("3. Undoing previous bad backfill run...")
            conn.execute(text("DELETE FROM stock_movements WHERE adjustment_reason = 'opening balance migration'"))
            conn.commit()

            print("4. Calculating and backfilling gaps...")
            ingredients = FeedIngredient.query.all()
            backfill_count = 0

            for item in ingredients:
                # Find what the ledger CURRENTLY says
                current_ledger_sum = conn.execute(
                    text("SELECT SUM(qty_kg) FROM stock_movements WHERE ingredient_id = :ing_id"),
                    {"ing_id": item.id}
                ).scalar() or 0.0
                
                target_stock = getattr(item, 'stock_quantity_kg', 0.0)
                gap = target_stock - current_ledger_sum
                
                # Only insert a backfill if there is an actual difference (allowing small float variance)
                if abs(gap) > 0.001:
                    conn.execute(
                        text("""
                            INSERT INTO stock_movements 
                            (ingredient_id, location_id, movement_type, qty_kg, reference_id, is_variance_adjustment, adjustment_reason) 
                            VALUES 
                            (:ing_id, :loc_id, 'ADJUSTMENT', :qty, :ref, 1, 'opening balance migration')
                        """),
                        {
                            "ing_id": item.id,
                            "loc_id": factory.id,
                            "qty": gap,
                            "ref": f"MIGRATE-GAP-{item.id}"
                        }
                    )
                    backfill_count += 1
            conn.commit()
        
        print(f"✅ Success: Generated {backfill_count} gap adjustments.")
        
        print("\n5. Verification Check:")
        with engine.connect() as conn:
            for item in ingredients:
                total_computed = conn.execute(
                    text("SELECT SUM(qty_kg) FROM stock_movements WHERE ingredient_id = :ing_id"),
                    {"ing_id": item.id}
                ).scalar() or 0.0
                
                # Format to 2 decimal places to avoid float rounding display issues
                target = round(getattr(item, 'stock_quantity_kg', 0.0), 2)
                computed = round(total_computed, 2)
                status = "MATCH" if target == computed else "MISMATCH"
                
                print(f" - {item.name}: Old Column = {target} | New Computed = {computed} [{status}]")

if __name__ == '__main__':
    run_inventory_backfill()