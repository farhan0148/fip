"""Settings (Week 7): accessibility and language preferences, Change FC and Clear my data.

Preferences are stored per account, so they survive a reload and a new login. "Clear my data" resets DEMO state
only (quiz results, demo purchases, Ask AI history, saved comparison flags and these preferences). Accounts,
questionnaire answers, saved simulator scenarios and what is already shared with an FC are never touched here.
"""

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, session, url_for

from . import sharing
from .ai import plan_summary
from .extensions import db
from .models import (
    SHARE_CATEGORIES, AskHistory, ComparisonFlag, DemoPurchase, FCAssignment, QuizAttempt, User, UserSetting,
)
from .security import check_csrf, login_required

bp = Blueprint("settings", __name__, url_prefix="/settings")

TEXT_SIZES = (("normal", "Normal"), ("large", "Large"), ("xlarge", "Extra large"))
LANGUAGES = plan_summary.LANGUAGES
CODE_FAILURE_KEY = "referral:{}"


def prefs(user):
    """The user's preferences, defaults if none are saved."""
    row = UserSetting.query.filter_by(user_id=user.id).first()
    return {"text_size": row.text_size if row else "normal", "high_contrast": bool(row and row.high_contrast),
            "read_aloud": bool(row and row.read_aloud), "summary_language": row.summary_language if row else "en"}


def body_classes(user):
    if user is None or user.role != "client":
        return ""
    p = prefs(user)
    out = []
    if p["text_size"] in ("large", "xlarge"):
        out.append("text-" + p["text_size"])
    if p["high_contrast"]:
        out.append("contrast-high")
    return " " + " ".join(out) if out else ""


@bp.get("")
@login_required
def index():
    assignment = sharing.assignment_for(g.user.id)
    fc = db.session.get(User, assignment.fc_id) if assignment else None
    return render_template(
        "settings/index.html", prefs=prefs(g.user), text_sizes=TEXT_SIZES, languages=LANGUAGES,
        fc=sharing.fc_preview(fc) if fc else None, saved=request.args.get("saved") == "1",
        cleared=request.args.get("cleared") == "1", fc_changed=request.args.get("fc_changed") == "1",
        page_title="Settings")


@bp.post("")
@login_required
def save():
    check_csrf()
    size = request.form.get("text_size")
    language = request.form.get("summary_language")
    if size not in dict(TEXT_SIZES) or language not in plan_summary.LANGUAGE_CODES:
        abort(400)
    row = UserSetting.query.filter_by(user_id=g.user.id).first()
    if row is None:
        row = UserSetting(user_id=g.user.id)
        db.session.add(row)
    row.text_size = size
    row.high_contrast = request.form.get("high_contrast") == "on"
    row.read_aloud = request.form.get("read_aloud") == "on"
    row.summary_language = language
    g.user.language = language
    db.session.commit()
    return redirect(url_for("settings.index", saved=1))


# ---------- Change FC: choose, preview, confirm ----------

def _pending():
    pending = session.get("change_fc")
    if not isinstance(pending, dict) or not isinstance(pending.get("fc_id"), int):
        return None, None
    fc = db.session.get(User, pending["fc_id"])
    if fc is None or fc.role != "fc" or pending.get("method") not in ("referral", "random"):
        session.pop("change_fc", None)
        return None, None
    return fc, pending["method"]


@bp.get("/fc")
@login_required
def fc_choose():
    assignment = sharing.assignment_for(g.user.id)
    current = db.session.get(User, assignment.fc_id) if assignment else None
    return render_template("settings/fc_choose.html", current=sharing.fc_preview(current) if current else None,
                           error=None, page_title="Change FC")


@bp.post("/fc")
@login_required
def fc_pick():
    """Step 1. Picks a new FC to preview. Assigns nothing and shares nothing yet."""
    check_csrf()
    assignment = sharing.assignment_for(g.user.id)
    current_id = assignment.fc_id if assignment else None
    choice = request.form.get("choice")
    error, fc, method = None, None, None
    if choice == "random":
        for _ in range(8):
            candidate = sharing.pick_random_fc(g.user)
            if candidate is not None and candidate.id != current_id:
                fc, method = candidate, "random"
                break
        error = None if fc else "No other FC is available to match right now. Try a referral code."
    elif choice == "referral":
        limiter = current_app.extensions["referral_limiter"]
        key = CODE_FAILURE_KEY.format(g.user.id)
        blocked, retry_after = limiter.blocked(key)
        if blocked:
            error = f"Too many tries. Wait about {retry_after} seconds and try again."
        else:
            candidate = sharing.fc_by_referral_code(request.form.get("code"))
            if candidate is None:
                limiter.record_failure(key)
                error = "We could not find that referral code. Check it and try again."
            elif candidate.id == current_id:
                error = "That is already your FC."
            else:
                fc, method = candidate, "referral"
    else:
        abort(400)
    if fc is None:
        current = db.session.get(User, current_id) if current_id else None
        return render_template("settings/fc_choose.html", current=sharing.fc_preview(current) if current else None,
                               error=error, page_title="Change FC"), 400
    session["change_fc"] = {"fc_id": fc.id, "method": method}
    return redirect(url_for("settings.fc_preview"))


@bp.get("/fc/preview")
@login_required
def fc_preview():
    new_fc, method = _pending()
    if new_fc is None:
        return redirect(url_for("settings.fc_choose"))
    assignment = sharing.assignment_for(g.user.id)
    old = db.session.get(User, assignment.fc_id) if assignment else None
    active = {gr.category for gr in sharing.active_grants(g.user.id)}
    return render_template(
        "settings/fc_preview.html", new=sharing.fc_preview(new_fc), old=sharing.fc_preview(old) if old else None,
        categories=SHARE_CATEGORIES, info=sharing.CATEGORY_INFO,
        previews={c: sharing.load_category(c, g.user.id) for c in SHARE_CATEGORIES}, active=active,
        error=request.args.get("error") == "none", page_title="Change FC")


@bp.post("/fc/confirm")
@login_required
def fc_confirm():
    check_csrf()
    new_fc, method = _pending()
    if new_fc is None:
        return redirect(url_for("settings.fc_choose"))
    chosen = [c for c in SHARE_CATEGORIES if c in request.form.getlist("categories")]
    assignment = sharing.assignment_for(g.user.id)
    # The old FC loses access first, then the new FC gets exactly the items the client ticked.
    for grant in sharing.active_grants(g.user.id):
        if grant.fc_id != new_fc.id:
            sharing.revoke(g.user, grant.category)
    if assignment is not None and assignment.fc_id != new_fc.id:
        assignment.fc_id, assignment.method = new_fc.id, method
        db.session.commit()
    sharing.confirm_sharing(g.user, new_fc, method, chosen)
    session.pop("change_fc", None)
    return redirect(url_for("settings.index", fc_changed=1))


@bp.post("/fc/cancel")
@login_required
def fc_cancel():
    check_csrf()
    session.pop("change_fc", None)
    return redirect(url_for("settings.index"))


# ---------- Clear my data (demo state only) ----------

RESET = ("Quiz results and demo purchases", "Ask AI history", "Saved comparison flags", "Your display preferences")
KEPT = ("Your account", "Questionnaire answers and profile", "Saved simulator scenario",
        "Data already shared with your FC (change it under Manage sharing)")


@bp.get("/clear")
@login_required
def clear_confirm():
    return render_template("settings/clear.html", reset=RESET, kept=KEPT, page_title="Clear my data")


@bp.post("/clear")
@login_required
def clear():
    check_csrf()
    uid = g.user.id
    DemoPurchase.query.filter_by(user_id=uid).delete(synchronize_session=False)
    QuizAttempt.query.filter_by(user_id=uid).delete(synchronize_session=False)
    AskHistory.query.filter_by(user_id=uid).delete(synchronize_session=False)
    ComparisonFlag.query.filter_by(user_id=uid).delete(synchronize_session=False)
    UserSetting.query.filter_by(user_id=uid).delete(synchronize_session=False)
    g.user.language = "en"
    db.session.commit()
    return redirect(url_for("settings.index", cleared=1))
