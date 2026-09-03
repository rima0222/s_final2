import hmac
import secrets
from flask import abort, request, session

def csrf_token():
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token

def validate_csrf():
    expected = session.get("_csrf", "")
    supplied = request.form.get("_csrf", "") or request.headers.get("X-CSRF-Token", "")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        abort(400, "Invalid CSRF token")
