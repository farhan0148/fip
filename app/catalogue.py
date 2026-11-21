"""The plans the UI may show.

Two sources live in the `policies` table:

* the imported Prudential catalogue (slug set), shown on the Plans pages, and
* the old fictional demo plans (is_demo, hidden). Compare, the simulator and the dashboard's
  "Top recommended plans" still use only those, because they need a premium and a cover amount
  that the catalogue does not supply (open item in tasks.md).

Selector rules (every "pick a new plan" list must go through `selectable_policies`):
* a product family is never selectable,
* a plan closed to new members is never selectable (it stays viewable),
* a rider is selectable only together with one of its applicable base plans.
"""

from flask import current_app

from .models import Document, PlanDocument, Policy


def prudential_enabled():
    """The real Prudential plans are shown only when SHOW_PRUDENTIAL_PLANS=1 (hidden for now)."""
    return bool(current_app.config.get("SHOW_PRUDENTIAL_PLANS"))

KIND_LABELS = {
    "catalogue_entry": "Plan",
    "base_plan_variant": "Plan variant",
    "rider": "Rider",
    "legacy_plan_variant": "Closed plan",
    "product_family": "Product family",
}
KIND_FILTER_ORDER = ("catalogue_entry", "base_plan_variant", "rider", "legacy_plan_variant", "product_family")
CATEGORY_LABELS = {"health": "Health", "life": "Life", "wealth": "Wealth", "legacy": "Legacy"}

CLOSED = "closed_to_new_members"
CONTRACT_SAMPLE = "public_sample_contract_linked"
CONTRACT_DOCUMENT_TYPE = "regulator_published_sample_contract"
PREMIUM_TABLES_TYPE = "official_reference_page"

NOT_VERIFIED = "Contract wording not verified"
NO_CONTRACT_NOTE = "Contract wording not verified. Explanations from the brochure are not available yet."
PREMIUM_VARIES = "Premium varies. See Prudential's official rate tables"

DOCUMENT_TYPE_LABELS = {
    "brochure": "Brochure",
    "regulator_published_sample_contract": "Sample contract (published by MOH)",
    "product_summary": "Product summary",
    "official_reference_page": "Official reference page",
}


def catalogue_policies():
    """Imported Prudential plans that may be listed, in catalogue order. Empty while they are hidden."""
    if not prudential_enabled():
        return []
    return (
        Policy.query.filter(Policy.slug.isnot(None), Policy.is_hidden.is_(False)).order_by(Policy.id).all()
    )


def legacy_demo_policies():
    """The hidden fictional plans, for the pages that still need premiums and cover amounts."""
    return Policy.query.filter_by(is_demo=True).order_by(Policy.id).all()


def plan_by_slug(slug):
    if not prudential_enabled():
        return None
    return Policy.query.filter(Policy.slug == slug, Policy.is_hidden.is_(False)).first()


def is_closed(policy):
    return policy.catalogue_status == CLOSED


def base_plans_for(rider):
    """The base plans a rider can be attached to (matched by the names the catalogue gives)."""
    names = list(rider.applicable_base_plans or [])
    if not names:
        return []
    return (
        Policy.query.filter(Policy.name.in_(names), Policy.slug.isnot(None)).order_by(Policy.id).all()
    )


def riders_for(base):
    return [
        p for p in catalogue_policies()
        if p.kind == "rider" and base.name in (p.applicable_base_plans or [])
    ]


def is_selectable(policy, chosen_base_slugs=()):
    """May this plan appear in a new-plan selector, given the base plans already chosen?"""
    if policy.slug is None or policy.is_hidden:
        return False
    if policy.kind == "product_family" or is_closed(policy):
        return False
    if policy.kind == "rider":
        return any(base.slug in set(chosen_base_slugs) for base in base_plans_for(policy))
    return True


def selectable_policies(chosen_base_slugs=()):
    return [p for p in catalogue_policies() if is_selectable(p, chosen_base_slugs)]


def plan_documents(policy):
    rows = (
        Document.query.join(PlanDocument, PlanDocument.document_id == Document.id)
        .filter(PlanDocument.policy_id == policy.id)
        .order_by(PlanDocument.position)
        .all()
    )
    return rows


def contract_documents(policy):
    return [d for d in plan_documents(policy) if d.type == CONTRACT_DOCUMENT_TYPE]


def premium_tables(policy):
    for d in plan_documents(policy):
        if d.type == PREMIUM_TABLES_TYPE:
            return d
    return None


def premium_view(policy):
    """What to show for the premium. A missing premium is never replaced by a number."""
    if policy.monthly_premium_cents is not None:
        return {"known": True, "cents": policy.monthly_premium_cents, "text": None, "url": None}
    tables = premium_tables(policy)
    return {
        "known": False,
        "cents": None,
        "text": PREMIUM_VARIES,
        "url": (tables.url if tables else None) or policy.product_url,
        "url_is_rate_table": tables is not None,
    }


def contract_view(policy, ingested_slugs=None):
    """Contract status line. 'Verified' needs a linked sample contract that was ingested with a matching hash."""
    from .models import DocumentVersion  # local import keeps module import light

    docs = contract_documents(policy)
    verified = False
    detail = None
    if policy.contract_status == CONTRACT_SAMPLE:
        for doc in docs:
            ingested = DocumentVersion.query.filter_by(document_id=doc.id, sha256=doc.sha256).first()
            if ingested is not None:
                verified = True
                detail = (doc.version or "").split(";")[0].strip() or None
                break
    if verified:
        label = f"Contract wording verified (sample, {detail})" if detail else "Contract wording verified (sample)"
    else:
        label = NOT_VERIFIED
    return {"verified": verified, "label": label}


def plans_sharing_documents(policy, document_ids):
    """Other catalogue plans linked to any of these documents (for example the variants of a product family)."""
    rows = (
        Policy.query.join(PlanDocument, PlanDocument.policy_id == Policy.id)
        .filter(PlanDocument.document_id.in_(document_ids), Policy.id != policy.id, Policy.slug.isnot(None))
        .order_by(Policy.id)
        .all()
    )
    return list(dict.fromkeys(rows))  # a plan linked to several of the documents appears once
