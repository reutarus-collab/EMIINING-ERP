from app import app, db

def rebuild():
    with app.app_context():
        print("Dropping existing tables...")
        db.drop_all()
        print("Creating fresh tables...")
        db.create_all()
        print("Database successfully rebuilt and ready!")

if __name__ == '__main__':
    rebuild()