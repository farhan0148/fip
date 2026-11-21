"""Plans pages.

/plans and /plans/<slug> show the imported Prudential catalogue (no AI here).
/plans/<id> (a number) is the older detail page for the hidden fictional demo plans. The dashboard's
"Top recommended plans" still links to it.
"""

from flask import Blueprint, abort, current_app, g, render_template, request, url_for

from . import catalogue, comparison, plan_match, services
from .ai import plan_summary
from .catalogue import legacy_demo_policies
from .extensions import db
from .models import ComparisonCell, Paragraph, PolicyClause
from .security import onboarding_complete_required

SUMMARY_MESSAGES = {
    "ai": "AI summary generated and checked against the clauses.",
    "fallback": "The AI summary could not be made right now, so the standard summary is shown with its sources.",
}

bp = Blueprint("plans", __name__, url_prefix="/plans")

SPEECH_LANG = {"en": "en", "zh": "zh", "ms": "ms", "ta": "ta"}


def _other_plan_id(policies, policy):
    """A plan to put beside this one on the compare page (the next plan in catalogue order)."""
    others = [p for p in policies if p.id != policy.id]
    return others[0].id if others else None


def _compare_url(policies, policy):
    """While the real plans are hidden, Compare shows the demo plans, so link to this plan. Otherwise the demo
    plans are not in Compare and the button opens the real-plan comparison."""
    if not catalogue.prudential_enabled():
        return url_for("compare.index", a=policy.id, b=_other_plan_id(policies, policy))
    return url_for("compare.index")


CATEGORY_FILTERS = (("all", "All"),) + tuple(catalogue.CATEGORY_LABELS.items())
# URL value, label, Policy.kind. Closed plans are viewable but are not listed under "All kinds".
KIND_FILTERS = (
    ("all", "All kinds", None),
    ("plan", "Plans", "catalogue_entry"),
    ("variant", "Plan variants", "base_plan_variant"),
    ("rider", "Riders", "rider"),
    ("family", "Product families", "product_family"),
    ("closed", "Closed to new members", "legacy_plan_variant"),
)
_KIND_BY_SLUG = {slug: (label, kind) for slug, label, kind in KIND_FILTERS}
_CATEGORY_LABEL = dict(CATEGORY_FILTERS)

SOURCE_HEADINGS = ("Deductible", "Co-insurance", "Pro-ration")


def _card(policy):
    return {
        "policy": policy,
        "categories": [catalogue.CATEGORY_LABELS[c] for c in policy.categories or []],
        "kind_label": catalogue.KIND_LABELS.get(policy.kind, policy.kind),
        "closed": catalogue.is_closed(policy),
        "contract": catalogue.contract_view(policy),
        "needs": [b.name for b in catalogue.base_plans_for(policy)] if policy.kind == "rider" else [],
    }


@bp.get("")
@onboarding_complete_required
def index():
    category = request.args.get("category", "all")
    if category not in _CATEGORY_LABEL:
        category = "all"
    kind_slug = request.args.get("kind", "all")
    if kind_slug not in _KIND_BY_SLUG:
        kind_slug = "all"
    kind = _KIND_BY_SLUG[kind_slug][1]

    policies = catalogue.catalogue_policies()
    demo = legacy_demo_policies()
    shown = [
        p for p in policies
        if (category == "all" or category in (p.categories or []))
        and ((kind is None and not catalogue.is_closed(p)) or (kind is not None and p.kind == kind))
    ]
    return render_template(
        "plans/index.html",
        prudential=catalogue.prudential_enabled(),
        category_filters=CATEGORY_FILTERS,
        kind_filters=KIND_FILTERS,
        category=category,
        kind=kind_slug,
        cards=[_card(p) for p in shown],
        demo_cards=[{"policy": p, "compare_url": _compare_url(demo, p)} for p in demo],
        total=len(policies),
        page_title="All plans",
    )


def _sources(policy):
    """A few real paragraphs from the plan's own sample contract, with citation links.

    Prefers the body of a clause with that heading (as in Premier, Plus and Standard). Falls back to a
    paragraph that starts with the word (the tables in the A and B contract). Only this plan's scope.
    """
    chosen = {}
    for word in SOURCE_HEADINGS:
        rows = (
            Paragraph.query.filter(
                db.or_(Paragraph.heading.ilike(word), Paragraph.text.ilike(word + "%"))
            )
            .order_by(Paragraph.id)
            .all()
        )
        rows = [p for p in rows if policy.slug in (p.scope or [])]
        by_heading = [
            p for p in rows
            if (p.heading or "").lower() == word.lower() and not p.text.startswith(f"{p.clause_number} ")
        ]
        by_text = [p for p in rows if p.text.lower().startswith(word.lower()) and len(p.text) >= 40]
        found = (by_heading or by_text or [None])[0]
        if found is not None:
            chosen[word] = found
    return [
        {
            "paragraph": chosen[w],
            "url": url_for("citations.view", paragraph_id=chosen[w].id, plan=policy.slug),
            "excerpt": chosen[w].text if len(chosen[w].text) <= 240 else chosen[w].text[:237].rstrip() + "...",
        }
        for w in SOURCE_HEADINGS if w in chosen
    ]


def _related(policy):
    if policy.kind == "rider":
        return "Needs one of these base plans", catalogue.base_plans_for(policy)
    if policy.kind == "base_plan_variant":
        riders = catalogue.riders_for(policy)
        return ("Optional riders (each needs this base plan)", riders) if riders else (None, [])
    if policy.kind == "product_family":
        contract_ids = {d.id for d in catalogue.contract_documents(policy)}
        if not contract_ids:
            return None, []
        linked = catalogue.plans_sharing_documents(policy, contract_ids)
        return "Variants with a sample contract", linked
    return None, []


@bp.get("/<slug>")
@onboarding_complete_required
def detail_slug(slug):
    policy = catalogue.plan_by_slug(slug)
    if policy is None:
        abort(404)
    contract = catalogue.contract_view(policy)
    related_title, related = _related(policy)

    language = request.args.get("lang", "en")
    if language not in plan_summary.LANGUAGE_CODES:
        language = "en"
    summary = None
    if contract["verified"] and not catalogue.is_closed(policy):  # closed plans are not summarised
        raw = (plan_summary.summary_for(policy, language, g.user.id) if current_app.config.get("AI_GENERATE_ON_VIEW")
               else plan_summary.peek_contract_summary(policy, language))
        summary = _contract_summary_view(raw, policy)
    return render_template(
        "plans/plan.html",
        policy=policy,
        card=_card(policy),
        contract=contract,
        premium=catalogue.premium_view(policy),
        documents=[
            {"doc": d, "label": catalogue.DOCUMENT_TYPE_LABELS.get(d.type, d.type)}
            for d in catalogue.plan_documents(policy)
        ],
        sources=_sources(policy) if contract["verified"] else [],
        summary=summary,
        languages=plan_summary.LANGUAGES,
        language=language,
        no_contract_note=catalogue.NO_CONTRACT_NOTE,
        related_title=related_title,
        related=related,
        page_title=policy.name,
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


def _contract_summary_view(summary, policy):
    """Add citation markers [1] [2] and a Sources list. Only paragraphs in this plan's scope are linked."""
    ids = []
    for statement in summary["statements"]:
        for paragraph_id in statement.get("paragraph_ids", []):
            if paragraph_id not in ids:
                ids.append(paragraph_id)
    rows = {p.id: p for p in Paragraph.query.filter(Paragraph.id.in_(ids))} if ids else {}
    rows = {i: p for i, p in rows.items() if policy.slug in (p.scope or [])}
    numbers = {pid: n for n, pid in enumerate([i for i in ids if i in rows], start=1)}

    def url(pid):
        return url_for("citations.view", paragraph_id=pid, plan=policy.slug)

    statements = [
        {"text": st["text"],
         "markers": [{"n": numbers[i], "url": url(i)} for i in st.get("paragraph_ids", []) if i in numbers]}
        for st in summary["statements"]
    ]
    sources = [
        {"n": n, "url": url(pid), "page": rows[pid].page, "clause": rows[pid].clause_number,
         "heading": rows[pid].heading,
         "text": rows[pid].text if len(rows[pid].text) <= 240 else rows[pid].text[:237].rstrip() + "..."}
        for pid, n in numbers.items()
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
        "full_url": None if catalogue.prudential_enabled() else url_for("compare.index", a=policy.id, b=similar[0].id),
    }


@bp.get("/<int:plan_id>")
@onboarding_complete_required
def detail(plan_id):
    policies = legacy_demo_policies()
    policy = next((p for p in policies if p.id == plan_id), None)
    if policy is None:
        abort(404)

    from .settings_routes import prefs   # the saved summary language is the default; a language tab overrides it
    language = request.args.get("lang") or prefs(g.user)["summary_language"]
    if language not in plan_summary.LANGUAGE_CODES:
        language = "en"
    # Week 7: a page view never calls the provider. The "Generate AI summary" button does (ask_routes).
    raw = (plan_summary.summary_for(policy, language, g.user.id) if current_app.config.get("AI_GENERATE_ON_VIEW")
           else plan_summary.peek_summary(policy, language))
    summary = _summary_view(raw, policy)

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
        message=SUMMARY_MESSAGES.get(request.args.get("msg")),
        read_aloud_default=prefs(g.user)["read_aloud"],
        compare_label="Compare PRUShield plans" if catalogue.prudential_enabled() else "Compare",
        page_title=policy.name,
    )
