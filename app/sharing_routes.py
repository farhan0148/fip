from flask import Blueprint, abort, current_app, g, redirect, render_template, request, session, url_for

from . import sharing
from .extensions import db
from .models import SHARE_CATEGORIES, User
from .security import check_csrf, onboarding_complete_required

bp = Blueprint("sharing", __name__, url_prefix="/sharing")

CODE_FAILURE_KEY = "referral:{}"
# Fixed messages only. A query-string value is never shown as text, so a crafted link cannot inject wording.
ERRORS = {
    "none_ticked": "Tick at least one item to share.",
    "already_shared": "Those items are already shared.",
}


def _category_param(raw):
    return raw if raw in SHARE_CATEGORIES else None


def _pending_fc():
    """The FC the client picked in step 1 but has not confirmed yet (nothing is assigned or shared yet)."""
    pending = session.get("pending_fc")
    if not isinstance(pending, dict):
        return None, None
    fc = db.session.get(User, pending.get("fc_id")) if isinstance(pending.get("fc_id"), int) else None
    if fc is None or fc.role != "fc" or pending.get("method") not in ("referral", "random"):
        session.pop("pending_fc", None)
        return None, None
    return fc, pending["method"]


def _current_fc():
    """(fc, method, is_assigned): the assigned FC if any, otherwise the pending pick."""
    assignment = sharing.assignment_for(g.user.id)
    if assignment is not None:
        fc = db.session.get(User, assignment.fc_id)
        return fc, assignment.method, True
    fc, method = _pending_fc()
    return fc, method, False


@bp.get("")
@onboarding_complete_required
def index():
    assignment = sharing.assignment_for(g.user.id)
    fc = db.session.get(User, assignment.fc_id) if assignment else None
    grants = sharing.active_grants(g.user.id)
    return render_template(
        "sharing/index.html",
        fc=sharing.fc_preview(fc) if fc else None,
        grants=[(g_, sharing.CATEGORY_INFO[g_.category]) for g_ in grants],
        events=sharing.recent_events(g.user.id),
        info=sharing.CATEGORY_INFO,
        just_shared=request.args.get("shared") == "1",
        just_revoked=request.args.get("revoked") == "1",
        page_title="Sharing with my FC",
    )


@bp.get("/new")
@onboarding_complete_required
def new():
    preselect = _category_param(request.args.get("category"))
    fc, method, assigned = _current_fc()
    if fc is None:
        return render_template("sharing/step1.html", category=preselect, error=None, page_title="Choose your FC")
    active = {gr.category for gr in sharing.active_grants(g.user.id) if gr.fc_id == fc.id}
    previews = {c: sharing.load_category(c, g.user.id) for c in SHARE_CATEGORIES}
    return render_template(
        "sharing/step2.html",
        fc=sharing.fc_preview(fc),
        assigned=assigned,
        method=method,
        categories=SHARE_CATEGORIES,
        info=sharing.CATEGORY_INFO,
        previews=previews,
        active=active,
        preselect=preselect,
        error=ERRORS.get(request.args.get("error")),
        page_title="Choose what to share",
    )


@bp.post("/fc")
@onboarding_complete_required
def choose_fc():
    """Step 1: pick an FC by referral code or random match. Assigns nothing and shares nothing."""
    check_csrf()
    if sharing.assignment_for(g.user.id) is not None:
        abort(400, description="You already have an FC. Changing FC is not available yet.")
    category = _category_param(request.form.get("category"))
    choice = request.form.get("choice")

    if choice == "random":
        fc = sharing.pick_random_fc(g.user)
        if fc is None:
            return render_template("sharing/step1.html", category=category, page_title="Choose your FC",
                                   error="No FC is available to match right now. Try a referral code."), 200
        session["pending_fc"] = {"fc_id": fc.id, "method": "random"}
    elif choice == "referral":
        limiter = current_app.extensions["referral_limiter"]
        key = CODE_FAILURE_KEY.format(g.user.id)
        blocked, retry_after = limiter.blocked(key)
        if blocked:
            return render_template(
                "sharing/step1.html", category=category, page_title="Choose your FC",
                error=f"Too many tries. Wait about {retry_after} seconds and try again."), 429
        fc = sharing.fc_by_referral_code(request.form.get("code"))
        if fc is None:
            limiter.record_failure(key)
            return render_template(
                "sharing/step1.html", category=category, page_title="Choose your FC",
                error="We could not find that referral code. Check it and try again."), 400
        session["pending_fc"] = {"fc_id": fc.id, "method": "referral"}
    else:
        abort(400)
    return redirect(url_for("sharing.new", category=category) if category else url_for("sharing.new"))


@bp.post("/fc/reset")
@onboarding_complete_required
def reset_fc():
    check_csrf()
    session.pop("pending_fc", None)
    category = _category_param(request.form.get("category"))
    return redirect(url_for("sharing.new", category=category) if category else url_for("sharing.new"))


@bp.post("/confirm")
@onboarding_complete_required
def confirm():
    """Step 2: the only place data starts being shared. Everything is checked again here."""
    check_csrf()
    fc, method, _assigned = _current_fc()
    if fc is None:
        return redirect(url_for("sharing.new"))
    chosen = [c for c in request.form.getlist("category") if c in SHARE_CATEGORIES]
    if not chosen:
        return redirect(url_for("sharing.new", error="none_ticked"))
    try:
        granted = sharing.confirm_sharing(g.user, fc, method, chosen)
    except ValueError:
        abort(400, description="You already have a different FC.")
    session.pop("pending_fc", None)
    if not granted:
        return redirect(url_for("sharing.new", error="already_shared"))
    return redirect(url_for("sharing.index", shared=1))


@bp.post("/revoke")
@onboarding_complete_required
def revoke():
    check_csrf()
    category = _category_param(request.form.get("category"))
    if category is None:
        abort(400)
    sharing.revoke(g.user, category)
    return redirect(url_for("sharing.index", revoked=1))
