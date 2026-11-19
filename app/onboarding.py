from datetime import datetime, timezone
from math import ceil

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from . import services
from .extensions import db
from .questions import OTHER_KEY, OTHER_LABEL, OTHER_MAX_LENGTH, QUESTION_COUNT, get_question, validate_answer
from .security import check_csrf, login_required

bp = Blueprint("onboarding", __name__, url_prefix="/onboarding")


@bp.before_request
@login_required
def _require_login():
    return None


def _step_url(number, from_review=False):
    if from_review:
        return url_for("onboarding.question", number=number, **{"from": "review"})
    return url_for("onboarding.question", number=number)


@bp.get("")
def resume():
    """Send the user to where they left off."""
    if g.user.review_confirmed_at is not None:
        return redirect(url_for("dashboard.index"))
    number = services.next_unrecorded_number(g.user)
    if number > QUESTION_COUNT:
        return redirect(url_for("onboarding.review"))
    return redirect(_step_url(number))


def _minutes_left(number):
    # Roughly 15 seconds per question.
    remaining = QUESTION_COUNT - number + 1
    if remaining <= 2:
        return "Less than a minute left"
    minutes = ceil(remaining * 15 / 60)
    return "About 1 minute left" if minutes == 1 else f"About {minutes} minutes left"


@bp.route("/question/<int:number>", methods=["GET", "POST"])
def question(number):
    if g.user.review_confirmed_at is not None:
        return redirect(url_for("dashboard.index"))
    q = get_question(number)
    if q is None:
        abort(404)

    ready = services.review_is_ready(g.user)
    from_review = request.args.get("from") == "review" and ready
    if not from_review and number > services.next_unrecorded_number(g.user):
        return redirect(url_for("onboarding.resume"))

    rows = services.answers_by_question(g.user)
    row = rows.get(q.id)
    error = None
    selected_key = None
    other_text = ""
    if row is not None and row.status == "answered" and row.selected:
        selected_key = row.selected[0]
        other_text = row.other_text or ""

    if request.method == "POST":
        check_csrf()
        action = request.form.get("action", "next")
        key, text, problem = validate_answer(q, request.form)

        if action == "skip" and not from_review:
            services.save_skip(g.user, q)
            return redirect(_step_url(number + 1) if number < QUESTION_COUNT else url_for("onboarding.review"))

        if action == "back" and not from_review:
            # Keep a valid pick so it survives going back. Never block going back.
            if problem is None:
                services.save_answer(g.user, q, key, text)
            return redirect(_step_url(number - 1) if number > 1 else _step_url(1))

        if action in ("next", "save"):
            if problem is None:
                services.save_answer(g.user, q, key, text)
                if from_review or action == "save":
                    return redirect(url_for("onboarding.review"))
                if number < QUESTION_COUNT:
                    return redirect(_step_url(number + 1))
                return redirect(url_for("onboarding.review"))
            error = problem
            selected_key = request.form.get("answer") or None
            other_text = request.form.get("other_text") or ""
        else:
            abort(400)

    return render_template(
        "onboarding/question.html",
        q=q,
        selected_key=selected_key,
        other_text=other_text,
        error=error,
        from_review=from_review,
        form_action=_step_url(number, from_review),
        back_url=_step_url(number - 1) if number > 1 else None,
        review_url=url_for("onboarding.review"),
        is_last=number == QUESTION_COUNT,
        minutes_left=_minutes_left(number),
        other_key=OTHER_KEY,
        other_label=OTHER_LABEL,
        other_max=OTHER_MAX_LENGTH,
        total=QUESTION_COUNT,
        page_title=f"Question {number} of {QUESTION_COUNT}",
    ), (400 if error else 200)


@bp.route("/review", methods=["GET", "POST"])
def review():
    if g.user.review_confirmed_at is not None:
        return redirect(url_for("dashboard.index"))
    number = services.next_unrecorded_number(g.user)
    if number <= QUESTION_COUNT:
        return redirect(_step_url(number))

    if request.method == "POST":
        check_csrf()
        g.user.review_confirmed_at = datetime.now(timezone.utc)
        db.session.commit()
        return redirect(url_for("dashboard.index"))

    return render_template(
        "onboarding/review.html",
        rows=services.review_rows(g.user),
        back_url=_step_url(QUESTION_COUNT),
        page_title="Review your answers",
    )
