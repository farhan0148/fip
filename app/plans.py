"""All plans (/plans) and plan detail (/plans/<id>). Plans come only from catalogue_policies()."""

from flask import Blueprint, abort, g, render_template, request, url_for

from . import comparison, plan_match, services
from .ai import plan_summary
from .catalogue import catalogue_policies
from .extensions import db
from .models import ComparisonCell, PolicyClause
from .security import onboarding_complete_required

bp = Blueprint("plans", __name__, url_prefix="/plans")

# slug, label, the Policy.type it selects (None = every plan)
TYPE_FILTERS = (
    ("all", "All", None),
    ("term_life", "Term Life", "Term life"),
    ("hospitalisation", "Hospitalisation", "Hospitalisation"),
    ("critical_illness", "Critical Illness", "Critical illness"),
    ("whole_life", "Whole Life", "Whole life"),
    ("retirement", "Retirement", "Retirement"),
    ("disability", "Disability", "Disability"),
)
_FILTER_BY_SLUG = {slug: (label, type_) for slug, label, type_ in TYPE_FILTERS}
SPEECH_LANG = {"en": "en", "zh": "zh", "ms": "ms", "ta": "ta"}


def _other_plan_id(policies, policy):
    """A plan to put beside this one on the compare page (the next plan in catalogue order)."""
    others = [p for p in policies if p.id != policy.id]
    return others[0].id if others else None


def _compare_url(policies, policy):
    return url_for("compare.index", a=policy.id, b=_other_plan_id(policies, policy))


@bp.get("")
@onboarding_complete_required
def index():
    policies = catalogue_policies()
    slug = request.args.get("type", "all")
    if slug not in _FILTER_BY_SLUG:
        slug = "all"
    label, type_ = _FILTER_BY_SLUG[slug]
    shown = [p for p in policies if type_ is None or p.type == type_]
    cards = [{"policy": p, "compare_url": _compare_url(policies, p)} for p in shown]
    return render_template(
        "plans/index.html",
        filters=TYPE_FILTERS,
        selected=slug,
        selected_label=label,
        cards=cards,
        total=len(policies),
        page_title="All plans",
    )


def _summary_view(summary, policy):
    """Add citation markers [1] [2] and the Sources list to a summary. Only this plan's clauses are used."""
    ids = []
    for statement in summary["statements"]:
        for clause_id in statement["clause_ids"]:
            if clause_id not in ids:
                ids.append(clause_id)
    clauses = {
        c.id: c
        for c in PolicyClause.query.filter(PolicyClause.policy_id == policy.id, PolicyClause.id.in_(ids)).all()
    } if ids else {}
    numbers = {clause_id: n for n, clause_id in enumerate([i for i in ids if i in clauses], start=1)}

    def url(clause_id):
        return url_for("compare.citation", clause_id=clause_id, plan=policy.id)

    statements = [
        {
            "text": s["text"],
            "markers": [{"n": numbers[i], "url": url(i)} for i in s["clause_ids"] if i in numbers],
        }
        for s in summary["statements"]
    ]
    sources = [
        {"n": n, "url": url(i), "ref": f"p.{clauses[i].page}, cl.{clauses[i].clause}, line {clauses[i].line}",
         "text": clauses[i].text}
        for i, n in numbers.items()
    ]
    return {**summary, "statements": statements, "sources": sources}


def _simple_comparison(policies, policy):
    """This plan against up to two similar ones (same type first), from the existing comparison cells."""
    similar = sorted((p for p in policies if p.id != policy.id), key=lambda p: (p.type != policy.type, p.id))[:2]
    if not similar:
        return None
    shared, distinctive = comparison.build_rows(g.user, [policy] + similar)
    return {
        "plans": [policy] + similar,
        "rows": shared + distinctive,
        "full_url": url_for("compare.index", a=policy.id, b=similar[0].id),
    }


@bp.get("/<int:plan_id>")
@onboarding_complete_required
def detail(plan_id):
    policies = catalogue_policies()
    policy = next((p for p in policies if p.id == plan_id), None)
    if policy is None:
        abort(404)

    language = request.args.get("lang", "en")
    if language not in plan_summary.LANGUAGE_CODES:
        language = "en"
    summary = _summary_view(plan_summary.summary_for(policy, language, g.user.id), policy)

    answers = services.answers_by_question(g.user)
    hospital = ComparisonCell.query.filter_by(policy_id=policy.id, row_key="hospital").first()
    top = plan_match.top_plans(g.user, policies)
    best = bool(top["plans"]) and top["plans"][0]["best"] and top["plans"][0]["policy"].id == policy.id

    return render_template(
        "plans/detail.html",
        policy=policy,
        best=best,
        summary=summary,
        languages=plan_summary.LANGUAGES,
        language=language,
        speech_lang=SPEECH_LANG[summary["language"]],
        tradeoff=plan_match.budget_tradeoff(policy.monthly_premium_cents, plan_match.budget_for(answers)),
        fit_rows=plan_match.compatibility_rows(g.user, policy, hospital.state if hospital else None),
        simple=_simple_comparison(policies, policy),
        compare_url=_compare_url(policies, policy),
        page_title=policy.name,
    )
