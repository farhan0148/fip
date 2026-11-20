"""Session helpers: CSRF token, current user, access decorators, response headers."""

import secrets
from functools import wraps

from flask import abort, g, redirect, request, session, url_for

from .extensions import db
from .models import User


def csrf_token():
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


def check_csrf():
    sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("_csrf", "")
    if not expected or not secrets.compare_digest(sent, expected):
        abort(400, description="Your session expired. Go back, refresh the page and try again.")


def load_current_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id is not None:
        g.user = db.session.get(User, user_id)
        if g.user is None:
            session.pop("user_id", None)


def home_for(user):
    """Where a signed-in user belongs: the FC portal, unfinished onboarding or the dashboard."""
    if user.role == "fc":
        return url_for("fc.index")
    if user.review_confirmed_at is None:
        return url_for("onboarding.resume")
    return url_for("dashboard.index")


def session_required(view):
    """Any signed-in user, whatever the role (used by log out)."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def login_required(view):
    """A signed-in CLIENT. FCs are sent to their own portal."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        if g.user.role == "fc":
            return redirect(url_for("fc.index"))
        return view(*args, **kwargs)

    return wrapped


def fc_required(view):
    """A signed-in FC. Anyone else gets a 404, so clients cannot tell /fc exists."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        if g.user.role != "fc":
            abort(404)
        return view(*args, **kwargs)

    return wrapped


def onboarding_complete_required(view):
    """Login required, and the review must have been confirmed."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        if g.user.role == "fc":
            return redirect(url_for("fc.index"))
        if g.user.review_confirmed_at is None:
            return redirect(url_for("onboarding.resume"))
        return view(*args, **kwargs)

    return wrapped


def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; object-src 'none'; "
        "base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
    )
    if request.endpoint != "static":
        response.headers.setdefault("Cache-Control", "no-store")
    return response
