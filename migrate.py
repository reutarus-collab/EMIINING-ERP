from app import app, db
from sqlalchemy import text

def run_migration():
    with app.app_context():
        commands = [
            "ALTER TABLE feed_ingredients ADD COLUMN purchase_uom VARCHAR(20) DEFAULT 'KG';",
            "ALTER TABLE feed_ingredients ADD COLUMN stock_uom VARCHAR(20) DEFAULT 'KG';",
            "ALTER TABLE feed_ingredients ADD COLUMN conversion_factor FLOAT DEFAULT 1.0;",
            "ALTER TABLE purchase_order_lines ADD COLUMN qty_received FLOAT DEFAULT 0.0;",
            "ALTER TABLE purchase_order_lines ADD COLUMN qty_rejected FLOAT DEFAULT 0.0;"
        ]
        
        for cmd in commands:
            try:
                db.session.execute(text(cmd))
                print(f"Success: {cmd}")
            except Exception as e:
                print(f"Skipped (Column likely exists): {cmd}")
        
        db.session.commit()
        print("Database migration complete!")

if __name__ == '__main__':
    run_migration()