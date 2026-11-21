"""Buy or consult, the four-question check, and demo checkout (Week 7).

The questions are built from the plan's own comparison findings, each backed by its sample clause. Checkout is
gated on the server: it needs a passed quiz (3 of 4) for this user and plan. It is a DEMO: no payment details are
asked for or stored and no money moves.
"""

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from .catalogue import legacy_demo_policies
from .comparison import ROWS
from .extensions import db
from .models import ComparisonCell, DemoPurchase, QuizAttempt
from .security import check_csrf, onboarding_complete_required

bp = Blueprint("buy", __name__, url_prefix="/plans")

PASS_MARK = 3
QUESTIONS = 4
LABELS = dict(ROWS)

# The order rows are tried in, and the situation each one is turned into.
SCENARIOS = (
    ("critical_illness", "If you are diagnosed with a critical illness,"),
    ("death_cover", "If the Life Assured passes away during the policy term,"),
    ("hospital", "If you are admitted to hospital for surgery,"),
    ("cash_value", "If you cancel the policy after a few years and ask for money back,"),
    ("premium_waiver", "If you cannot work and cannot keep paying premiums,"),
    ("duration", "If you are still alive after the cover period has ended,"),
    ("premium", "If the premium goes up or down during the policy,"),
)
ANSWERS = {
    "included": "Yes, it is covered (subject to the conditions in the wording)",
    "optional_rider": "Only if you add the optional rider",
    "not_included": "No, the plan does not cover this",
    "unconfirmed": "The sample wording does not say",
}
ORDER = ("included", "optional_rider", "not_included", "unconfirmed")


def _plan(plan_id):
    policy = next((p for p in legacy_demo_policies() if p.id == plan_id), None)
    if policy is None:
        abort(404)
    return policy


def build_questions(policy):
    """Up to four questions from this plan's cited findings: {text, options, correct, feedback, clause_id, cell_id}."""
    cells = {c.row_key: c for c in ComparisonCell.query.filter_by(policy_id=policy.id) if c.clause_id}
    questions = []
    for row_key, scenario in SCENARIOS:
        cell = cells.get(row_key)
        if cell is None:
            continue
        others = [s for s in ORDER if s != cell.state][:2]
        keys = [cell.state] + others
        shift = len(questions) % 3          # so the right answer is not always first
        keys = keys[-shift:] + keys[:-shift] if shift else keys
        questions.append({
            "text": f"{scenario} does {policy.name} cover that?",
            "options": [ANSWERS[k] for k in keys],
            "correct": keys.index(cell.state),
            "finding": cell.finding,
            "row": LABELS.get(row_key, row_key),
            "clause_id": cell.clause_id,
            "cell_id": cell.id,
        })
        if len(questions) == QUESTIONS:
            break
    return questions


def _clause_url(policy, question):
    return url_for("compare.citation", clause_id=question["clause_id"], cell=question["cell_id"], plan=policy.id)


def _active_attempt(policy):
    return (QuizAttempt.query.filter_by(user_id=g.user.id, policy_id=policy.id, finished=False)
            .order_by(QuizAttempt.id.desc()).first())


def passed_attempt(policy):
    return (QuizAttempt.query.filter_by(user_id=g.user.id, policy_id=policy.id, passed=True)
            .order_by(QuizAttempt.id.desc()).first())


@bp.get("/<int:plan_id>/proceed")
@onboarding_complete_required
def proceed(plan_id):
    policy = _plan(plan_id)
    return render_template("buy/proceed.html", policy=policy, ready=len(build_questions(policy)) == QUESTIONS,
                           page_title=f"Proceed with {policy.name}")


@bp.post("/<int:plan_id>/quiz/start")
@onboarding_complete_required
def start(plan_id):
    check_csrf()
    policy = _plan(plan_id)
    if len(build_questions(policy)) != QUESTIONS:
        abort(400, description="The check is not available for this plan.")
    db.session.add(QuizAttempt(user_id=g.user.id, policy_id=policy.id, answers={}))
    db.session.commit()
    return redirect(url_for("buy.quiz", plan_id=plan_id, q=1))


@bp.get("/<int:plan_id>/quiz")
@onboarding_complete_required
def quiz(plan_id):
    policy = _plan(plan_id)
    attempt = _active_attempt(policy)
    if attempt is None:
        return redirect(url_for("buy.proceed", plan_id=plan_id))
    questions = build_questions(policy)
    answered = len(attempt.answers)
    try:
        n = int(request.args.get("q", answered + 1))
    except ValueError:
        n = answered + 1
    n = max(1, min(n, min(answered + 1, QUESTIONS)))
    question = questions[n - 1]
    choice = attempt.answers.get(str(n))
    return render_template(
        "buy/quiz.html", policy=policy, n=n, total=QUESTIONS, question=question, choice=choice,
        correct=(choice == question["correct"]) if choice is not None else None,
        clause_url=_clause_url(policy, question), all_answered=answered == QUESTIONS,
        page_title="Quick check before you buy")


@bp.post("/<int:plan_id>/quiz/answer")
@onboarding_complete_required
def answer(plan_id):
    check_csrf()
    policy = _plan(plan_id)
    attempt = _active_attempt(policy)
    if attempt is None:
        return redirect(url_for("buy.proceed", plan_id=plan_id))
    try:
        n, choice = int(request.form["q"]), int(request.form["choice"])
    except (KeyError, ValueError):
        abort(400)
    if not 1 <= n <= QUESTIONS or n != len(attempt.answers) + 1 and str(n) not in attempt.answers or not 0 <= choice <= 2:
        abort(400)
    if str(n) not in attempt.answers:          # an answered question stays answered until the next try
        attempt.answers = {**attempt.answers, str(n): choice}
        db.session.commit()
    return redirect(url_for("buy.quiz", plan_id=plan_id, q=n))


@bp.get("/<int:plan_id>/quiz/result")
@onboarding_complete_required
def result(plan_id):
    policy = _plan(plan_id)
    attempt = _active_attempt(policy) or (QuizAttempt.query.filter_by(user_id=g.user.id, policy_id=policy.id, finished=True)
                                          .order_by(QuizAttempt.id.desc()).first())
    if attempt is None or len(attempt.answers) < QUESTIONS:
        return redirect(url_for("buy.quiz", plan_id=plan_id) if attempt else url_for("buy.proceed", plan_id=plan_id))
    questions = build_questions(policy)
    if not attempt.finished:
        attempt.score = sum(1 for i, q in enumerate(questions, start=1) if attempt.answers.get(str(i)) == q["correct"])
        attempt.passed = attempt.score >= PASS_MARK
        attempt.finished = True
        db.session.commit()
    review = [{"q": q, "ok": attempt.answers.get(str(i)) == q["correct"], "url": _clause_url(policy, q)}
              for i, q in enumerate(questions, start=1)]
    return render_template("buy/result.html", policy=policy, attempt=attempt, review=review, total=QUESTIONS,
                           pass_mark=PASS_MARK, page_title="Quiz result")


@bp.get("/<int:plan_id>/checkout")
@onboarding_complete_required
def checkout(plan_id):
    policy = _plan(plan_id)
    attempt = passed_attempt(policy)
    if attempt is None:                                         # the server gate: no pass, no checkout
        return redirect(url_for("buy.proceed", plan_id=plan_id, need="quiz"))
    return render_template("buy/checkout.html", policy=policy, attempt=attempt, total=QUESTIONS,
                           page_title="Checkout (demo)")


@bp.post("/<int:plan_id>/checkout")
@onboarding_complete_required
def confirm(plan_id):
    check_csrf()
    policy = _plan(plan_id)
    attempt = passed_attempt(policy)
    if attempt is None:
        return redirect(url_for("buy.proceed", plan_id=plan_id, need="quiz"))
    if DemoPurchase.query.filter_by(user_id=g.user.id, policy_id=policy.id).first() is None:
        db.session.add(DemoPurchase(user_id=g.user.id, policy_id=policy.id, quiz_attempt_id=attempt.id))
        db.session.commit()
    return redirect(url_for("buy.done", plan_id=plan_id))


@bp.get("/<int:plan_id>/checkout/done")
@onboarding_complete_required
def done(plan_id):
    policy = _plan(plan_id)
    purchase = DemoPurchase.query.filter_by(user_id=g.user.id, policy_id=policy.id).first()
    if purchase is None or passed_attempt(policy) is None:
        return redirect(url_for("buy.proceed", plan_id=plan_id, need="quiz"))
    return render_template("buy/done.html", policy=policy, purchase=purchase, page_title="Demo purchase confirmed")
