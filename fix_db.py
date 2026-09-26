from app import app, db
from sqlalchemy import text

def fix_database():
    with app.app_context():
        try:
            # Force add the missing column
            db.session.execute(text("ALTER TABLE feed_ingredients ADD COLUMN conversion_type VARCHAR(20) DEFAULT 'FIXED';"))
            db.session.commit()
            print("✅ SUCCESS: Added 'conversion_type' column to feed_ingredients!")
        except Exception as e:
            print("❌ ERROR or ALREADY EXISTS:", str(e))

if __name__ == '__main__':
    fix_database()