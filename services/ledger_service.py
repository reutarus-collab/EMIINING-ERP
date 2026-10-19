from services.db import db
from services.models import GeneralLedgerEntry

def post_gl_transaction(ref, legs, module, source_id):
    """Post a whole journal entry or nothing. legs = [(account_code, debit, credit), ...].

    Refuses (ValueError) anything that would leave the books out of balance: fewer than two legs,
    a negative or two-sided leg, a zero leg, or debits that differ from credits by more than 0.5 cent.
    The caller's commit/rollback decides persistence, same as post_gl_entry.
    """
    legs = list(legs)
    if len(legs) < 2:
        raise ValueError('A ledger entry needs at least two legs.')
    total_debit = total_credit = 0.0
    for account, debit, credit in legs:
        debit, credit = float(debit or 0.0), float(credit or 0.0)
        if not account or debit < 0 or credit < 0 or (debit > 0) == (credit > 0):
            raise ValueError(f'Invalid ledger leg for account {account}: each leg must be a positive debit or a positive credit.')
        total_debit += debit
        total_credit += credit
    if abs(total_debit - total_credit) > 0.005:
        raise ValueError(f'Ledger entry {ref} is out of balance (debits {total_debit:.2f}, credits {total_credit:.2f}).')
    for account, debit, credit in legs:
        db.session.add(GeneralLedgerEntry(transaction_ref=ref, account_code=account, debit=float(debit or 0.0),
                                          credit=float(credit or 0.0), source_type=module, source_id=source_id))


def post_gl_entry(ref, account_code, debit, credit, module, source_id):
    try:
        entry = GeneralLedgerEntry(
            transaction_ref=ref,
            account_code=account_code,
            debit=debit,
            credit=credit,
            source_type=module,
            source_id=source_id
        )
        db.session.add(entry)
        # No commit here! The POS service commits everything at once.
        return True
    except Exception as e:
        print(f"GL Error: {e}")
        return False