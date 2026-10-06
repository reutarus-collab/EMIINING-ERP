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
- Use a unique PIN of 6–8 digits for sales and warehouse accounts; avoid repeated digits or common sequences. Admin and accounting passwords must be at least 10 characters.
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
- Refunds record the cash/ledger refund. Returned goods are not automatically restored to inventory; use the stock adjustment workflow after inspection.
- Formulation and milling routes that are not implemented return an explicit unavailable response.

## Weekly money and leak reports

- Admin and accountant users can open **Money & leaks**. Set opening cash, inventory, customer debt, supplier debt, and equipment balances for the start date. For today's opening date, the form suggests ledger/stock/customer balances; physically count cash and confirm the other figures before saving.
- The cash walk uses posted ledger activity for profit, stock, debtors, creditors, owner withdrawals, and equipment. The difference between calculated closing cash and ledger cash is shown for reconciliation. It is a management report; it does not create opening journal entries.
- Use **Use closing balances as next week's opening** to carry the closing snapshot forward. Save a snapshot for each week before running later periods.
- Leak rows are ranked by KSh value and show the previous period. Current stock/debtor/price exposure is a point-in-time measure, so it does not show a weekly trend until historical balance snapshots exist. Formulation over-processing, bag-weight giveaway, downtime, and debtor aging are marked untracked until those source records are added.
- Purchase leakage compares received costs to the prior 90-day weighted average where history exists and includes rejected or short receipts. Price exposure estimates sales below current outlet cost; there is no target-margin setting yet.
