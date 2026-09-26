-- EMINING ERP — schema v3 (clean rebuild)
-- One transaction log drives stock, supplier debt, and (later) GL.
-- Never write to `stock` or update balances directly — always through
-- write_transaction() in app.py, so this table never drifts from reality.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS locations (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    type    TEXT NOT NULL CHECK(type IN ('factory','branch'))
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK(role IN ('admin','factory','branch')),
    location_id   INTEGER REFERENCES locations(id),  -- NULL for admin: not tied to one shop
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    item_type     TEXT NOT NULL CHECK(item_type IN ('raw_material','intermediate','finished_good')),
    bag_size_kg   REAL NOT NULL DEFAULT 50,
    price_per_kg  REAL NOT NULL DEFAULT 0,   -- selling price
    cost_per_kg   REAL NOT NULL DEFAULT 0,   -- latest purchase cost — Formulation Lab reads THIS, not price_per_kg
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS suppliers (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    phone           TEXT,
    opening_balance REAL NOT NULL DEFAULT 0,   -- what you owed them before this system existed
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id            INTEGER PRIMARY KEY,
    supplier_id   INTEGER NOT NULL REFERENCES suppliers(id),
    location_id   INTEGER NOT NULL REFERENCES locations(id),
    status        TEXT NOT NULL DEFAULT 'unpaid' CHECK(status IN ('unpaid','partial','paid')),
    total_amount  REAL NOT NULL DEFAULT 0,
    created_by    INTEGER REFERENCES users(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS purchase_order_items (
    id                  INTEGER PRIMARY KEY,
    purchase_order_id   INTEGER NOT NULL REFERENCES purchase_orders(id),
    item_id             INTEGER NOT NULL REFERENCES items(id),
    qty_kg              REAL NOT NULL,
    unit_cost           REAL NOT NULL,
    line_total          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS supplier_payments (
    id                  INTEGER PRIMARY KEY,
    supplier_id         INTEGER NOT NULL REFERENCES suppliers(id),
    purchase_order_id   INTEGER REFERENCES purchase_orders(id),  -- NULL = general payment, not tied to one PO
    amount              REAL NOT NULL,
    payment_method      TEXT NOT NULL CHECK(payment_method IN ('cash','bank','mpesa')),
    created_by          INTEGER REFERENCES users(id),
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- THE TRANSACTION ENGINE.
-- Every stock-moving or money-moving action writes exactly one row here.
-- Reports, audit trail, and GL (whenever you build it) all read from this
-- table instead of five disconnected ones. Do not bypass it from a route.
CREATE TABLE IF NOT EXISTS transactions (
    id                  TEXT PRIMARY KEY,     -- client-generated UUID — makes offline sync idempotent
    type                TEXT NOT NULL CHECK(type IN
                          ('SALE','PURCHASE','PAYMENT','TRANSFER_OUT','TRANSFER_IN','ADJUSTMENT')),
    location_id         INTEGER NOT NULL REFERENCES locations(id),
    item_id             INTEGER REFERENCES items(id),          -- NULL for PAYMENT
    qty_kg              REAL,                                  -- NULL for PAYMENT; signed for ADJUSTMENT
    unit_used           TEXT CHECK(unit_used IN ('kg','bag') OR unit_used IS NULL),
    qty_in_unit         REAL,
    unit_price          REAL,                                  -- price/cost per kg at time of transaction
    amount              REAL NOT NULL,                         -- total money value, always set
    payment_method      TEXT CHECK(payment_method IN ('cash','bank','mpesa') OR payment_method IS NULL),
    customer_name       TEXT,                                  -- SALE only, optional free text
    supplier_id         INTEGER REFERENCES suppliers(id),      -- PURCHASE / PAYMENT
    purchase_order_id   INTEGER REFERENCES purchase_orders(id),-- PURCHASE / PAYMENT
    reason              TEXT,                                  -- ADJUSTMENT only
    device_id           TEXT,
    user_id             INTEGER REFERENCES users(id),
    created_at          TEXT NOT NULL,         -- when it actually happened (client clock — matters offline)
    synced_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Materialized current stock, derived entirely from transactions.
-- Never write here directly except through write_transaction() in app.py.
CREATE TABLE IF NOT EXISTS stock (
    item_id      INTEGER NOT NULL REFERENCES items(id),
    location_id  INTEGER NOT NULL REFERENCES locations(id),
    qty_kg       REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (item_id, location_id)
);

CREATE INDEX IF NOT EXISTS idx_transactions_location ON transactions(location_id);
CREATE INDEX IF NOT EXISTS idx_transactions_supplier ON transactions(supplier_id);
CREATE INDEX IF NOT EXISTS idx_po_supplier ON purchase_orders(supplier_id);
