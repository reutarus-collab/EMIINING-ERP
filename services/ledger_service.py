from services.db import db
from services.models import GeneralLedgerEntry

def post_gl_entry(ref, account_code, debit, credit, module, source_id):
    try:
        entry = GeneralLedgerEntry(
            transaction_ref=ref, 
            account_code=account_code, 
            debit=debit, 
            credit=credit, 
            source_module=module, 
            description=str(source_id)
        )
        db.session.add(entry)
        # No commit here! The POS service commits everything at once.
        return True
    except Exception as e:
        print(f"GL Error: {e}")
        return False