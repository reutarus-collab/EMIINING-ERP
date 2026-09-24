from services.db import db
from services.models import GeneralLedgerEntry

def post_gl_entry(ref, account_code, debit, credit, module, source_id):
    entry = GeneralLedgerEntry(
        transaction_ref=ref, account_code=account_code, 
        debit=debit, credit=credit, source_module=module, source_id=source_id
    )
    db.session.add(entry)