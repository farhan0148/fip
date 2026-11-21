"""Ask AI and explicit plan summaries (Week 7, demo plans only).

The provider is called only by the POST routes here, each from a button. Nothing on a GET calls it.
"""

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from . import services
from .ai import ask as ai_ask
from .ai import plan_summary
from .catalogue import legacy_demo_policies
from .extensions import db
from .models import AskHistory, Policy, UserSetting
from .plans import _summary_view
from .security import check_csrf, onboarding_complete_required

bp = Blueprint("ask", __name__)

REASONS = {
    "not_configured": "The AI service is not set up on this server.",
    "timeout": "The AI service took too long.",
    "error": "The AI service could not be reached.",
    "empty": "The AI service returned nothing.",
    "invalid_output": "The AI answer did not match the policy wording, so it was not used.",
    "rate_limited": "Too many requests just now.",
    "busy": "The AI service is busy or out of quota right now.",
}
SUMMARY_MESSAGES = {
    "ai": "AI summary generated and checked against the clauses.",
    "cached": "That summary was already up to date.",
    "fallback": "The AI summary could not be made, so the standard summary is shown with its sources.",
}


def default_language(user):
    setting = UserSetting.query.filter_by(user_id=user.id).first()
    return setting.summary_language if setting else "en"


def _plans():
    return legacy_demo_policies()


def _plan(plan_id):
    return next((p for p in _plans() if p.id == plan_id), None)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _language(raw):
    return raw if raw in plan_summary.LANGUAGE_CODES else None


def _history(kind, policy, question, language, outcome):
    db.session.add(AskHistory(user_id=g.user.id, policy_id=policy.id, kind=kind, question=(question or None),
                              language=language, outcome=outcome))
    db.session.commit()


def _page(**extra):
    plans = _plans()
    selected = extra.pop("policy", None) or _plan(_int(request.values.get("plan"))) or (plans[0] if plans else None)
    language = _language(request.values.get("lang")) or default_language(g.user)
    history = (AskHistory.query.filter_by(user_id=g.user.id).order_by(AskHistory.id.desc()).limit(5).all())
    names = {p.id: p.name for p in plans}
    return render_template("ask.html", plans=plans, selected=selected, language=language,
                           languages=plan_summary.LANGUAGES, history=history, names=names,
                           reasons=REASONS, page_title="Ask AI", **extra)


@bp.get("/ask")
@onboarding_complete_required
def index():
    return _page(question=request.args.get("q", "")[:ai_ask.MAX_QUESTION_CHARS])


@bp.post("/ask")
@onboarding_complete_required
def submit():
    check_csrf()
    policy = _plan(_int(request.form.get("plan")))
    language = _language(request.form.get("lang"))
    if policy is None or language is None:
        abort(400)
    action = request.form.get("action")
    question = " ".join(request.form.get("question", "").split())

    if action == "summary":
        summary = plan_summary.summary_for(policy, language, g.user.id)
        outcome = "ai" if summary["source"] == "ai" else "fallback"
        _history("summary", policy, None, language, outcome)
        return _page(policy=policy, result={
            "kind": "summary", "view": _summary_view(summary, policy), "fallback": outcome == "fallback",
            "reason": summary.get("reason"), "note": summary.get("note")}, question=question)

    if action != "ask":
        abort(400)
    if not ai_ask.MIN_QUESTION_CHARS <= len(question) <= ai_ask.MAX_QUESTION_CHARS:
        return _page(policy=policy, question=question,
                     error=f"Type a question of {ai_ask.MIN_QUESTION_CHARS} to {ai_ask.MAX_QUESTION_CHARS} characters."), 400
    answer = ai_ask.ask(policy, question, language, g.user.id)
    _history("question", policy, question, language, "fallback" if answer["status"] == "fallback" else (
        "cached" if answer.get("cached") else "ai"))
    view = _summary_view({"statements": answer["statements"], "reason": None}, policy)
    cited = {src["n"] for src in view["sources"]}
    return _page(policy=policy, question=question, result={
        "kind": "question", "status": answer["status"], "view": view, "reason": answer["reason"],
        "relevant": [{"clause": c, "url": url_for("compare.citation", clause_id=c.id, plan=policy.id)}
                     for c in answer["clauses"]],
        "insufficient": ai_ask.INSUFFICIENT, "cited": cited})


@bp.post("/plans/<int:plan_id>/summary")
@onboarding_complete_required
def generate_summary(plan_id):
    """Generate the AI summary (and translation) for this plan and language, from the plan page button."""
    check_csrf()
    policy = _plan(plan_id)
    language = _language(request.form.get("lang"))
    if policy is None or language is None:
        abort(400)
    summary = plan_summary.summary_for(policy, language, g.user.id)
    shown = "ai" if (summary["source"] == "ai") else "fallback"
    _history("summary", policy, None, language, shown)
    return redirect(url_for("plans.detail", plan_id=plan_id, lang=language if language != "en" else None, msg=shown))
