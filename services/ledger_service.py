from services.db import db
from services.models import LedgerEntry

def record_idempotent_transaction(account_id, amount, idempotency_key, description=""):
    existing_entry = LedgerEntry.query.filter_by(idempotency_key=idempotency_key).first()
    if existing_entry:
        return {
            "status": "duplicate_prevented",
            "ledger_id": existing_entry.id,
            "account_id": existing_entry.account_id,
            "amount": existing_entry.amount,
            "timestamp": existing_entry.timestamp.isoformat()
        }

    entry = LedgerEntry(
        account_id=account_id,
        amount=amount,
        idempotency_key=idempotency_key,
        description=description
    )
    db.session.add(entry)
    db.session.commit()

    return {
        "status": "recorded",
        "ledger_id": entry.id,
        "account_id": entry.account_id,
        "amount": entry.amount,
        "timestamp": entry.timestamp.isoformat()
    }