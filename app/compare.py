from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for

from . import catalogue, comparison, retrieval
from .ai import comparison_ai
from .extensions import db
from .models import ComparisonCell, ComparisonFlag, Policy, PolicyClause
from .security import check_csrf, onboarding_complete_required

bp = Blueprint("compare", __name__)

ROW_KEYS = {key for key, _ in comparison.ROWS}


def _compare_url(a, b, anchor=None):
    url = url_for("compare.index", a=_int_or_none(a), b=_int_or_none(b))
    return f"{url}#{anchor}" if anchor else url


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def comparable_policies():
    """Open catalogue plans with ingested contract wording. Findings may still need generating."""
    return [
        p for p in catalogue.selectable_policies()
        if p.contract_status == catalogue.CONTRACT_SAMPLE and retrieval.paragraphs_in_scope(p)
    ]


def plans_without_findings(policies):
    """Which of these plans have no findings stored yet (they can be generated on the spot)."""
    with_cells = {
        row[0]
        for row in db.session.query(ComparisonCell.policy_id)
        .filter(ComparisonCell.paragraphs_hash.isnot(None), ComparisonCell.policy_id.in_([p.id for p in policies]))
        .distinct()
    }
    return [p for p in policies if p.id not in with_cells]


GENERATE_MESSAGES = {
    "generated": "Findings generated and checked against the wording.",
    "cached": "Findings for this plan are already up to date.",
    "busy": "The AI service is busy right now. Try again in a minute.",
    "limit": "Too many requests just now. Try again in a minute.",
    "not_configured": "The AI service is not set up on this server.",
    "failed": "Findings could not be generated. Nothing was changed. Try again later.",
}


def a_scope(policy):
    """The set of plans a policy's contract wording is shared with (for the same-wording note)."""
    cell = ComparisonCell.query.filter(
        ComparisonCell.policy_id == policy.id, ComparisonCell.paragraph_id.isnot(None)
    ).first()
    return (cell.paragraph.scope or []) if cell is not None else [policy.slug]


@bp.get("/compare")
@onboarding_complete_required
def index():
    real = catalogue.prudential_enabled()
    policies = comparable_policies() if real else comparison.demo_policies()
    if len(policies) < 2:
        return render_template("compare.html", ready=False, real=real, page_title="Compare plans")

    a, b, notice = comparison.choose_pair(policies, request.args.get("a"), request.args.get("b"))
    shared, distinctive = comparison.build_rows(g.user, [a, b])
    return render_template(
        "compare.html",
        ready=True,
        policies=policies,
        real=real,
        a=a,
        b=b,
        notice=notice,
        shared=shared,
        distinctive=distinctive,
        premiums={p.id: catalogue.premium_view(p) for p in (a, b)} if real else {},
        missing=plans_without_findings([a, b]) if real else [],
        generate_message=GENERATE_MESSAGES.get(request.args.get("msg")),
        same_wording=real and set(a_scope(a)) == set(a_scope(b)),
        states=[(key, comparison.STATE_LABELS[key]) for key in comparison.STATE_ORDER],
        flag_count=comparison.flag_count(g.user, [a, b]),
        page_title="Compare plans",
    )


@bp.post("/compare/generate")
@onboarding_complete_required
def generate():
    """Generate the findings for one plan now (one provider call, rate-limited per user). No client data is sent."""
    check_csrf()
    policy_id = _int_or_none(request.form.get("policy_id"))
    policy = next((p for p in comparable_policies() if p.id == policy_id), None)
    if policy is None:
        abort(400)
    limiter = current_app.extensions.get("ai_summary_limiter")
    if limiter is not None and not limiter.check(g.user.id)[0]:
        code = "limit"
    else:
        result = comparison_ai.generate_for_plan(policy, retries=0)
        code = result["status"] if result["status"] in ("generated", "cached") else (
            result.get("reason") if result.get("reason") in ("busy", "not_configured") else "failed")
    other = _int_or_none(request.form.get("other"))
    return redirect(url_for("compare.index", a=policy.id, b=other, msg=code))


@bp.post("/compare/flag")
@onboarding_complete_required
def flag():
    """Toggle 'Flag for my FC to verify' on an Unconfirmed cell. Stays private for now."""
    check_csrf()
    policy_id = _int_or_none(request.form.get("policy_id"))
    row_key = request.form.get("row_key", "")
    policy = db.session.get(Policy, policy_id) if policy_id else None
    if policy is None or not (policy.is_demo or policy.slug) or row_key not in ROW_KEYS:
        abort(400)

    cell = ComparisonCell.query.filter_by(policy_id=policy.id, row_key=row_key).first()
    if cell is not None and cell.state != "unconfirmed":
        abort(400, description="Only Unconfirmed items can be flagged for your FC to verify.")

    existing = ComparisonFlag.query.filter_by(user_id=g.user.id, policy_id=policy.id, row_key=row_key).first()
    if existing:
        db.session.delete(existing)
    else:
        db.session.add(ComparisonFlag(user_id=g.user.id, policy_id=policy.id, row_key=row_key))
    db.session.commit()
    return redirect(
        _compare_url(request.form.get("a"), request.form.get("b"), f"cell-{policy.id}-{row_key}")
    )


@bp.get("/citations/demo/<int:clause_id>")
@onboarding_complete_required
def citation(clause_id):
    clause = db.session.get(PolicyClause, clause_id)
    if clause is None or not clause.policy.is_demo:
        abort(404)

    pages = [
        row[0]
        for row in db.session.query(PolicyClause.page)
        .filter_by(policy_id=clause.policy_id)
        .distinct()
        .order_by(PolicyClause.page)
    ]
    view_page = _int_or_none(request.args.get("page"))
    if view_page not in pages:
        view_page = clause.page
    lines = (
        PolicyClause.query.filter_by(policy_id=clause.policy_id, page=view_page)
        .order_by(PolicyClause.line)
        .all()
    )

    # The claim being checked, only if it really cites this clause.
    cell = None
    cell_id = _int_or_none(request.args.get("cell"))
    if cell_id:
        candidate = db.session.get(ComparisonCell, cell_id)
        if candidate is not None and candidate.clause_id == clause.id:
            cell = candidate

    a, b = request.args.get("a"), request.args.get("b")
    plan_id = _int_or_none(request.args.get("plan"))
    position = pages.index(view_page)
    nav = {"a": _int_or_none(a), "b": _int_or_none(b), "cell": cell.id if cell else None, "plan": plan_id}
    return render_template(
        "citation.html",
        clause=clause,
        cell=cell,
        state_label=comparison.STATE_LABELS[cell.state] if cell else None,
        lines=lines,
        view_page=view_page,
        pages=pages,
        prev_url=url_for("compare.citation", clause_id=clause.id, page=pages[position - 1], **nav) if position > 0 else None,
        next_url=url_for("compare.citation", clause_id=clause.id, page=pages[position + 1], **nav) if position < len(pages) - 1 else None,
        cited_url=url_for("compare.citation", clause_id=clause.id, **nav),
        back_url=url_for("plans.detail", plan_id=plan_id) if plan_id else _compare_url(a, b),
        back_label="Back to plan" if plan_id else "Back to comparison",
        page_title=f"Source · page {view_page}",
    )
