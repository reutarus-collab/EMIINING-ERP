# Emining ERP — clean rebuild: setup

## Local
1. `pip install -r requirements.txt`
2. `export SECRET_KEY=$(python -c "import secrets;print(secrets.token_hex(32))")`
3. `flask --app app.py init-db`
4. `python seed.py` — copy down the generated passwords it prints, they're shown once
5. `flask --app app.py run`

## PythonAnywhere deploy
1. New repo, new deploy — don't point the WSGI file at the old EMIINING-ERP checkout.
2. Set `SECRET_KEY` as an environment variable in the Web tab. Never hardcode it in `app.py`.
3. Set `SEED_ADMIN_PASSWORD` / `SEED_FACTORY_PASSWORD` / `SEED_BRANCH_PASSWORD` as env vars before running `seed.py`, or copy down the generated ones immediately and store them somewhere real (password manager, not a notes file in the repo).
4. Confirm the WSGI file imports `app` from *this* `app.py` — don't assume.
5. Add `erp.db`, `.env`, `__pycache__/` to `.gitignore` before your first commit. Nothing with real credentials or real data goes into git.

## Roles
- `admin` — not tied to a location. Manages items, suppliers, purchase orders, payments. This is what "admin access" means now — it's an enforced role, not a shared login.
- `factory` / `branch` — tied to one location. Can log sales and sync offline transactions for that location only.

## What this does NOT include yet
- The frontend (`static/`) from the old repo still needs updating to send `payment_method`, `customer_name`, and a `type` field per queued transaction — the API contract changed. Don't deploy the old static files unmodified against this backend.
- Goods-receiving (ordered vs. received variance) — purchases post to stock immediately on PO creation.
- Transfers between factory and branch — schema supports `TRANSFER_OUT`/`TRANSFER_IN` as transaction types; no route uses them yet.
- General ledger / P&L — deferred on purpose. Query `transactions` directly for now.
