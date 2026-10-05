"""Create optional demo reference data without ever deleting existing data.

Set ALLOW_DEMO_SEED=1 explicitly before running. This script is not for production.
"""
import os
import sys

if os.environ.get('ALLOW_DEMO_SEED') != '1':
    raise SystemExit('Refusing to seed. Set ALLOW_DEMO_SEED=1 to add demo data; this never resets the database.')

from app import app
from services.db import db
from services.models import FeedIngredient, Customer, Location, Supplier, Account, AnimalRequirement


def seed_demo_data():
    with app.app_context():
        if FeedIngredient.query.first() or Customer.query.first() or Supplier.query.first():
            raise SystemExit('Database already contains business data; demo seed stopped without changes.')

        factory = Location(name='Emining Main Factory', code='FAC-01', location_type='FACTORY')
        branch = Location(name='Mogotio Retail Store', code='RET-01', location_type='BRANCH_STORE')
        db.session.add_all([factory, branch])
        db.session.flush()
        db.session.add_all([
            Account(account_code='1000', name='Cash/Bank', category='ASSET'),
            Account(account_code='1200', name='Inventory Asset', category='ASSET'),
            Account(account_code='1300', name='Accounts Receivable', category='ASSET'),
            Account(account_code='2000', name='Accounts Payable', category='LIABILITY'),
            Account(account_code='4000', name='Sales Revenue', category='REVENUE'),
            Supplier(name='ABC Suppliers Ltd', contact_info='0711000111 - Nakuru'),
            Supplier(name='Rift Valley Grains', contact_info='0722000222 - Eldoret'),
            Supplier(name='Mombasa Premix Co.', contact_info='0733000333 - Mombasa'),
            AnimalRequirement(species_stage='Dairy Cattle (High Yield 15L+)', min_cp=16.0, min_me=2.4),
            AnimalRequirement(species_stage='Poultry Broiler Starter', min_cp=22.0, min_me=3.0),
        ])
        db.session.flush()
        items = [
            FeedIngredient(name='Layer Mash', category='Finished Feed', cost_per_kg=55.0, stock_quantity_kg=20000.0, retail_price_per_kg=65.0, crude_protein_pct=16.0, bag_size_kg=70.0),
            FeedIngredient(name='Maize Grain', category='Raw - Energy', cost_per_kg=35.0, stock_quantity_kg=50000.0, retail_price_per_kg=40.0, metabolizable_energy_mcal=3.3, bag_size_kg=90.0),
            FeedIngredient(name='Ochonga (Fishmeal)', category='Raw - Protein', cost_per_kg=180.0, stock_quantity_kg=5000.0, retail_price_per_kg=200.0, crude_protein_pct=55.0, bag_size_kg=50.0),
        ]
        db.session.add_all(items)
        db.session.flush()
        from services.inventory import change_stock
        for item in items:
            initial = item.stock_quantity_kg
            item.stock_quantity_kg = 0
            change_stock(factory.id, item, initial, 'DEMO_SEED', 'demo-seed')
        db.session.commit()
        print('Demo reference data added. No users or passwords were created.')


if __name__ == '__main__':
    try:
        seed_demo_data()
    except Exception as exc:
        db.session.rollback()
        print(f'Demo seed failed: {exc}', file=sys.stderr)
        raise
