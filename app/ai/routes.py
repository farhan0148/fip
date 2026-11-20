import logging
import time
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.exceptions import BadRequest

from .. import simulator
from ..models import Policy
from ..security import check_csrf
from . import explain as explain_service

log = logging.getLogger("app.ai")
bp = Blueprint("ai", __name__, url_prefix="/api")


def _json_error(status, code, message, **extra):
    response = jsonify(ok=False, error=code, message=message, **extra)
    response.status_code = status
    return response


def api_access_required(view):
    """Like the page decorators, but answers with JSON status codes instead of redirects."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return _json_error(401, "login_required", "Log in to continue.")
        if g.user.review_confirmed_at is None:
            return _json_error(403, "onboarding_incomplete", "Confirm your answers first.")
        return view(*args, **kwargs)

    return wrapped


def _catalogue():
    policies = Policy.query.filter_by(is_demo=True).order_by(Policy.id).all()
    return {p.id: simulator.catalogue_entry(p) for p in policies}


@bp.post("/simulator/explain")
@api_access_required
def explain():
    """Explain the calculated changes for a scenario. Nothing is stored.

    The browser sends the same payload as /simulator/save. Flask re-validates and
    recalculates it; no number from the client is used as a result.
    """
    try:
        check_csrf()
    except BadRequest:
        return _json_error(400, "csrf", "Your session expired. Reload the page and try again.")

    limiter = current_app.extensions["ai_limiter"]
    allowed, retry_after = limiter.check(g.user.id)
    if not allowed:
        response = _json_error(429, "rate_limited", "Too many explanation requests. Try again shortly.", retry_after=retry_after)
        response.headers["Retry-After"] = str(retry_after)
        return response

    catalogue = _catalogue()
    clean, errors = simulator.validate(request.get_json(silent=True), catalogue)
    if errors:
        return _json_error(400, "invalid_input", "Fix the marked fields first.", errors=errors)

    result = simulator.calculate(clean, catalogue)
    started = time.monotonic()
    explanation = explain_service.explain(result)
    # Log the outcome only: no figures, no prompt, no key, no user details.
    log.info("simulator explanation source=%s reason=%s ms=%d", explanation["source"], explanation["reason"],
             (time.monotonic() - started) * 1000)
    return jsonify(ok=True, **explanation)
