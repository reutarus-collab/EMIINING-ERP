"""One error boundary for the API.

Validation problems (ValueError and friends) are written for the user, so their text is returned.
Anything else (database errors, bugs, I/O) is logged in full on the server under a reference code,
and the browser only sees a safe message and that code. This stops SQL, file paths and stack details
leaking to a cashier's screen, and tells a definite rejection (400) apart from a server fault (500).
"""
import uuid

from flask import current_app, jsonify

# Raised on purpose by our own validation; the message is meant to be shown.
USER_FACING = (ValueError, TypeError, KeyError, IndexError, ArithmeticError)


def error_response(exc, status=400, prefix=''):
    if isinstance(exc, USER_FACING):
        message = str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)
        return jsonify(status='error', message=prefix + message), status
    reference = 'ERR-' + uuid.uuid4().hex[:8].upper()
    current_app.logger.error('Unhandled error %s', reference, exc_info=exc)
    return jsonify(status='error', reference=reference,
                   message=f'Something went wrong on the server. Nothing was saved. Quote {reference} to the administrator.'), 500
