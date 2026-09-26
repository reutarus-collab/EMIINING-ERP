from app import app, db
from sqlalchemy import text

def run():
    with app.app_context():
        try:
            db.session.execute(text("ALTER TABLE suppliers ADD COLUMN phone VARCHAR(50);"))
            db.session.execute(text("ALTER TABLE suppliers ADD COLUMN location VARCHAR(100);"))
            db.session.execute(text("ALTER TABLE suppliers ADD COLUMN items_dealing VARCHAR(255);"))
            db.session.execute(text("ALTER TABLE suppliers ADD COLUMN description TEXT;"))
            db.session.commit()
            print("✅ SUCCESS: Added detailed supplier columns!")
        except Exception as e:
            print("❌ ALREADY EXISTS OR ERROR:", e)

if __name__ == '__main__':
    run()