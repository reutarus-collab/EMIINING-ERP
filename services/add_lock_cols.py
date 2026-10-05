import sqlite3
db = sqlite3.connect('emining_erp.db')
existing = [r[1] for r in db.execute("PRAGMA table_info(app_users)")]
print("before:", existing)
if 'failed_attempts' not in existing:
    db.execute("ALTER TABLE app_users ADD COLUMN failed_attempts INTEGER DEFAULT 0")
if 'locked_until' not in existing:
    db.execute("ALTER TABLE app_users ADD COLUMN locked_until DATETIME")
db.commit()
print("after:", [r[1] for r in db.execute("PRAGMA table_info(app_users)")])