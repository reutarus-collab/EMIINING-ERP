"""The single door into the general ledger.

Every journal entry must go through post_gl_transaction(): it refuses anything that does not
balance, before a single row is added to the session. As a second line of defence,
install_ledger_guard() blocks any COMMIT that would leave an unbalanced journal in the books,
even if some future code adds GeneralLedgerEntry rows directly.
"""
import math
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import event
from sqlalchemy.orm import Session

from services.db import db
from services.models import GeneralLedgerEntry

TOLERANCE = 0.005  # half a cent
CENT = Decimal('0.01')


def to_cents(value):
    """Exact money: float/str/None -> Decimal rounded half-up to the cent. NaN and infinity are refused."""
    number = float(value or 0.0)
    if not math.isfinite(number):
        raise LedgerImbalanceError('Ledger amounts must be finite numbers.')
    return Decimal(str(number)).quantize(CENT, rounding=ROUND_HALF_UP)


class LedgerImbalanceError(ValueError):
    """Raised when a journal does not balance. The caller's rollback discards the whole sale/receipt."""


def post_gl_transaction(ref, legs, module, source_id, skip_zero=False):
    """Post a whole journal entry or nothing. legs = [(account_code, debit, credit), ...].

    Refuses (LedgerImbalanceError, a ValueError) anything that would leave the books out of balance:
    fewer than two legs, a negative or two-sided leg, a zero leg, or debits that differ from credits
    by more than half a cent. Nothing is added to the session unless the whole entry is valid.
    With skip_zero=True, legs that are exactly zero (e.g. a fully-absorbed M-Pesa fee) are dropped first.
    The caller's commit/rollback decides persistence.
    """
    try:
        legs = [(a, to_cents(d), to_cents(c)) for a, d, c in legs]
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise LedgerImbalanceError(f'Invalid ledger amount: {exc}')
    zero = Decimal('0')
    if skip_zero:
        legs = [leg for leg in legs if leg[1] != zero or leg[2] != zero]
    if len(legs) < 2:
        raise LedgerImbalanceError('A ledger entry needs at least two legs.')
    total_debit = total_credit = zero
    for account, debit, credit in legs:
        if not account or debit < zero or credit < zero or (debit > zero) == (credit > zero):
            raise LedgerImbalanceError(
                f'Invalid ledger leg for account {account}: each leg must be a positive debit or a positive credit.')
        total_debit += debit
        total_credit += credit
    if total_debit != total_credit:   # exact, in cents: no float noise, no tolerance needed
        raise LedgerImbalanceError(
            f'Ledger entry {ref} is out of balance (debits {total_debit}, credits {total_credit}).')
    for account, debit, credit in legs:
        db.session.add(GeneralLedgerEntry(transaction_ref=ref, account_code=str(account), debit=float(debit),
                                          credit=float(credit), source_type=module, source_id=source_id))


def post_gl_entry(*args, **kwargs):
    """Retired. One-sided posting let the books drift out of balance. Use post_gl_transaction()."""
    raise RuntimeError('post_gl_entry() is retired. Post every journal with post_gl_transaction(ref, legs, module, source_id).')


# ---- second line of defence: no commit may leave an unbalanced journal ---------------------------

def _check_balanced(session):
    pending = session.info.get('gl_rows')
    if not pending:
        return
    totals = {}
    for row in pending:
        t = totals.setdefault(row.transaction_ref, [0.0, 0.0])
        t[0] += row.debit or 0.0
        t[1] += row.credit or 0.0
    bad = {ref: (d, c) for ref, (d, c) in totals.items() if abs(d - c) > TOLERANCE}
    if bad:
        ref, (d, c) = next(iter(bad.items()))
        raise LedgerImbalanceError(
            f'Refusing to save: journal {ref} is out of balance (debits {d:.2f}, credits {c:.2f}). Nothing was saved.')


def install_ledger_guard():
    """Idempotent. Tracks every GL row added in a transaction and refuses to commit if any journal is unbalanced."""
    if getattr(install_ledger_guard, 'installed', False):
        return
    install_ledger_guard.installed = True

    @event.listens_for(Session, 'after_flush')
    def _track(session, flush_context):
        for obj in session.new:
            if isinstance(obj, GeneralLedgerEntry):
                session.info.setdefault('gl_rows', []).append(obj)

    @event.listens_for(Session, 'before_commit')
    def _verify(session):
        # Flush first so rows added since the last autoflush are tracked too.
        session.flush()
        _check_balanced(session)

    @event.listens_for(Session, 'after_transaction_end')
    def _reset(session, transaction):
        if transaction.parent is None:
            session.info.pop('gl_rows', None)
