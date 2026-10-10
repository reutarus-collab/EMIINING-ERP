# Emining ERP setup and launch operations

## Install and start

1. Install the pinned/runtime dependencies with `pip install -r requirements.txt`.
2. Set a long random `SECRET_KEY` environment variable.
3. Set `DATABASE_URL` to the intended database. Back up any existing business database before deploying schema changes.
4. Run `flask --app app.py init-db` once, then create users with `python create_user.py`.
5. Start with `flask --app app.py run` locally or the configured WSGI server in production.

`seed.py` is optional demo data only. It refuses to run unless `ALLOW_DEMO_SEED=1`, refuses a non-empty business database, and never drops tables. Do not use it on production data.

## PythonAnywhere launch settings

- Set `SECRET_KEY` and `COOKIE_SECURE=1` in the web app environment. Serve the app over HTTPS.
- Each user signs in with a username and four-digit PIN. Set a different PIN for each person; admin resets are done with `python reset_password.py`.
- Assign each cashier and warehouse user to an exact `location_id`. Staff cannot choose another outlet. Admin/accounting users may select an exact location ID.
- Schedule the backup command below as a PythonAnywhere daily task, replacing paths with the account's actual paths. Create the backup directory first.

## Online-only checkout

Checkout needs a working server connection. If the till shows **OFFLINE · RECORD ON PAPER**, do not retry by making a new sale after an uncertain response. Keep the same browser tab and use its retry for the same pending checkout; the server idempotency key prevents a duplicate. If the device is closed or its browser storage is cleared before confirmation, reconcile the paper record and sales history before entering the sale again. Old IndexedDB offline queue records are not uploaded; review and reconcile any historical queued sales manually.

## Daily SQLite backup and restore check

The backup utility uses SQLite's online backup API (the safe equivalent of `.backup`), checks database integrity, atomically publishes `erp-YYYY-MM-DD.db`, and removes matching backups older than 14 days.

Example PythonAnywhere daily task (use the configured Python executable and absolute paths):

```sh
python /path/to/project/scripts/backup_sqlite.py --database /path/to/project/emining_erp.db --backup-dir /home/yourname/backups
```

Run a restore verification against a backup when validating operations:

```sh
python /path/to/project/scripts/backup_sqlite.py --database /path/to/project/emining_erp.db --backup-dir /home/yourname/backups --verify-restore
```

`--verify-restore` restores the newest backup into a temporary database and runs SQLite integrity checks; it does not overwrite the source database. Keep backup storage outside the repository and restrict its filesystem permissions.

## Inventory and cashier workflows

- Inter-location stock movement is dispatched, remains in transit, and is received by the destination cashier/warehouse user. A short delivery must be closed with a reason; the variance is posted to account 5100 at dispatch cost.
- Customer cash repayments require an open till and update both till expected cash and the cash ledger. Non-cash repayments require an explicit payment method.
- Refunds record the cash/ledger refund. At refund time, physically returned sale lines and accepted quantities can be received back into that outlet's inventory; COGS is reversed at the sale's recorded cost (legacy lines without a cost use the outlet's current average). Damaged goods should not be restocked.
- Formulation and milling routes that are not implemented return an explicit unavailable response.

## Weekly money and leak reports

- Admin and accountant users can view **Money & leaks**. Only admins can record owner withdrawals. Opening balances default from the latest saved close plus posted ledger movements; for the first reporting period, enter the initial balances. Count cash and compare the opening count with the ledger cash figure before saving.
- The cash walk compares counted opening cash with the ledger balance at that date, then shows the profit-based cash calculation, posted ledger close, and reconciliation difference. It is a management report; it does not create opening journal entries.
- Use **Use closing balances as next week's opening** to carry the closing snapshot forward. Save a snapshot for each week before running later periods.
- The leak report separates **Money lost** from **Cash tied up** so the value of unsold/slow stock and customer debt is not presented as realized loss. Rows are ranked by estimated KSh value and show the prior period.
- Purchase leakage compares received costs to the prior 90-day weighted average where history exists and includes rejected or short receipts. Price leakage estimates the 30-day sales gap against a 15% target gross margin by default; set `PRICE_TARGET_MARGIN_PCT` to change the target.
- During a refund, confirm each physically returned item and enter the quantity in kilograms. The ERP adds inspected saleable goods back to outlet stock and reverses estimated COGS at the outlet's current weighted-average cost. Damaged goods should not be marked as returned to stock; record them through stock adjustment instead.

## Retry safety for cash and stock changes

Every POST that moves cash or stock (expenses, supplier payments, repayments, refunds, stock adjustments, transfers, production, owner withdrawals, till cash, new customers/suppliers, purchase orders) carries an `Idempotency-Key`. The browser adds it automatically (`static/js/idem.js`); the server stores the key in the same database transaction as the change (`services/idempotency.py`), so a retried request returns the original result instead of recording twice. Goods receipts and checkout use the same table with their own keys.

When adding a new money- or stock-moving POST route: put `@idempotent('some-scope')` under its `@roles_required`, and add its path to `PROTECTED` in `static/js/idem.js`. `tests/test_idempotency.py` fails if the two lists drift apart.

Run the tests with `SECRET_KEY=test python -m unittest discover -s tests`. A request that fails validation does not use up its key, so the same screen can be corrected and resubmitted.
