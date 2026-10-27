"""Trading hours and the end-of-day till sweep.

Staff (not admin/accountant) can only open a till, sell, repay or record expenses between OPEN_HOUR and
CLOSE_HOUR Nairobi time. A till still open at CLOSE_HOUR is closed by the system the next time anyone
touches the server. The sweep does not invent a cash count: counted cash and variance stay empty and the
session is marked AUTO_CLOSED so the owner can see who left a till open and what the system expected.
"""
import os
from datetime import datetime, time, timedelta

from services.db import db

LOCAL_OFFSET = timedelta(hours=3)          # Africa/Nairobi, no daylight saving
OWNER_ROLES = ('admin', 'accountant')


def _hour(name, default):
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        return default
    return value if 0 <= value <= 23 else default


def open_hour():
    return _hour('BUSINESS_OPEN_HOUR', 6)


def close_hour():
    return _hour('BUSINESS_CLOSE_HOUR', 21)


def utcnow():
    """Single clock for this module so tests can move time."""
    return datetime.utcnow()


def enforced():
    """On by default. Off under TESTING, or with BUSINESS_HOURS_ENFORCED=0. The app config key wins over both."""
    from flask import current_app
    if 'BUSINESS_HOURS_ENFORCED' in current_app.config:
        return bool(current_app.config['BUSINESS_HOURS_ENFORCED'])
    if current_app.testing:
        return False
    return os.environ.get('BUSINESS_HOURS_ENFORCED', '1') != '0'


def local_now(now_utc=None):
    return (now_utc or utcnow()) + LOCAL_OFFSET


def is_open_now(now_utc=None):
    t = local_now(now_utc).time()
    return time(open_hour()) <= t < time(close_hour())


def window_text():
    return f'{open_hour():02d}:00 to {close_hour():02d}:00'


def require_open_for(user, now_utc=None):
    """Raise ValueError (shown to the user) when a staff member acts outside trading hours."""
    if getattr(user, 'role', '') in OWNER_ROLES or not enforced():
        return
    if not is_open_now(now_utc):
        raise ValueError(f'The till is closed outside trading hours ({window_text()}, Nairobi time). '
                         f'Come back at {open_hour():02d}:00.')


def last_close_cutoff_utc(now_utc=None):
    """The most recent CLOSE_HOUR that has already passed, as a naive UTC datetime."""
    local = local_now(now_utc)
    cutoff_local = datetime.combine(local.date(), time(close_hour()))
    if local < cutoff_local:
        cutoff_local -= timedelta(days=1)
    return cutoff_local - LOCAL_OFFSET


def sweep_stale_tills(now_utc=None):
    """Close every staff till opened before the latest CLOSE_HOUR that is still open. Returns how many."""
    from services.models import TillSession, User
    now = now_utc or utcnow()
    cutoff = last_close_cutoff_utc(now)
    owners = {u.username for u in User.query.filter(User.role.in_(OWNER_ROLES)).all()}
    stale = TillSession.query.filter(TillSession.status == 'OPEN', TillSession.opened_at < cutoff).all()
    closed = 0
    for till in stale:
        if till.cashier_name in owners:
            continue
        till.status = 'AUTO_CLOSED'
        till.open_key = None
        till.closed_at = cutoff
        closed += 1
    if closed:
        db.session.commit()
    return closed
