"""Retry-safety for POST routes that move cash or stock.

How it works
------------
1. The client sends an ``Idempotency-Key`` header (a UUID) with every protected POST.
2. Before the route runs, the decorator *claims* the key by inserting an
   ``IdempotencyKey`` row and flushing it.  That row lives in the SAME database
   transaction as whatever the route writes, so the claim and the business rows
   commit together or not at all.
3. A second request with the same key (a retry, a double-tap, two phones racing)
   either finds the committed row and gets the original result back, or collides on
   the unique key and is told the same thing.  The business code never runs twice.
4. If the route fails (HTTP >= 400 or ``status: error``), the claim is dropped, so
   the same key can be retried after the user fixes the problem.

Routes keep their own commit/rollback code unchanged.
"""
import hashlib
import json
import uuid
from functools import wraps

from flask import g, jsonify, make_response, request
from sqlalchemy.exc import IntegrityError, OperationalError

from services.db import db
from services.models import IdempotencyKey

MISSING_KEY_MESSAGE = 'Reload the app and try again (request key missing).'


def _hash(scope, view_args, body):
    raw = json.dumps({'scope': scope, 'args': view_args, 'body': body},
                     sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _replay(existing, username, request_hash):
    if existing.created_by != username or existing.request_hash != request_hash:
        return jsonify(status='error', message=(
            'This request key was already used for different details. '
            'Check the records before submitting again.')), 409
    stored = dict(existing.response_json) if isinstance(existing.response_json, dict) else {
        'status': 'success', 'message': 'Already recorded.'}
    status_code = stored.pop('_http_status', 200)   # replay the exact status the first request got (e.g. 201)
    return jsonify(dict(stored, already_processed=True)), status_code


def _is_failure(response):
    if response.status_code >= 400:
        return True
    payload = response.get_json(silent=True)
    return isinstance(payload, dict) and payload.get('status') == 'error'


def idempotent(scope):
    """Protect a POST view.  Non-POST methods (e.g. the GET half of a GET/POST route) pass through."""
    def decorate(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if request.method != 'POST':
                return view(*args, **kwargs)
            try:
                key = f'{scope}:{uuid.UUID((request.headers.get("Idempotency-Key") or "").strip())}'
            except (ValueError, TypeError, AttributeError):
                return jsonify(status='error', message=MISSING_KEY_MESSAGE), 400
            username = g.user.username
            request_hash = _hash(scope, kwargs, request.get_json(silent=True))

            existing = IdempotencyKey.query.filter_by(key=key).first()
            if existing:
                return _replay(existing, username, request_hash)
            try:
                db.session.add(IdempotencyKey(key=key, request_hash=request_hash, created_by=username))
                db.session.flush()
            except IntegrityError:
                db.session.rollback()
                existing = IdempotencyKey.query.filter_by(key=key).first()
                if existing:
                    return _replay(existing, username, request_hash)
                return jsonify(status='error', message='Request conflicted with another one. Check the records, then try again.'), 409
            except OperationalError:
                db.session.rollback()
                return jsonify(status='error', message='The server is busy. Nothing was saved - try again in a moment.'), 503

            response = make_response(view(*args, **kwargs))
            if _is_failure(response):
                db.session.rollback()
                # If the view committed before failing, don't leave a claim that would replay as success.
                IdempotencyKey.query.filter_by(key=key).delete()
                db.session.commit()
                return response
            try:
                row = IdempotencyKey.query.filter_by(key=key).first()
                payload = response.get_json(silent=True)
                if row is not None and isinstance(payload, dict):
                    row.response_json = dict(payload, _http_status=response.status_code)
                db.session.commit()
            except Exception:
                db.session.rollback()   # the business write is already committed; only the cached reply is lost
            return response
        wrapper.idempotent_scope = scope
        return wrapper
    return decorate
