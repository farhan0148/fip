from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from . import comparison
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


@bp.get("/compare")
@onboarding_complete_required
def index():
    policies = comparison.demo_policies()
    if len(policies) < 2:
        return render_template("compare.html", ready=False, page_title="Compare plans")

    a, b, notice = comparison.choose_pair(policies, request.args.get("a"), request.args.get("b"))
    shared, distinctive = comparison.build_rows(g.user, [a, b])
    return render_template(
        "compare.html",
        ready=True,
        policies=policies,
        a=a,
        b=b,
        notice=notice,
        shared=shared,
        distinctive=distinctive,
        states=[(key, comparison.STATE_LABELS[key]) for key in comparison.STATE_ORDER],
        flag_count=comparison.flag_count(g.user, [a, b]),
        page_title="Compare plans",
    )


@bp.post("/compare/flag")
@onboarding_complete_required
def flag():
    """Toggle 'Flag for my FC to verify' on an Unconfirmed cell. Stays private for now."""
    check_csrf()
    policy_id = _int_or_none(request.form.get("policy_id"))
    row_key = request.form.get("row_key", "")
    policy = db.session.get(Policy, policy_id) if policy_id else None
    if policy is None or not policy.is_demo or row_key not in ROW_KEYS:
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


@bp.get("/citations/<int:clause_id>")
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
    position = pages.index(view_page)
    nav = {"a": _int_or_none(a), "b": _int_or_none(b), "cell": cell.id if cell else None}
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
        back_url=_compare_url(a, b),
        page_title=f"Source · page {view_page}",
    )
