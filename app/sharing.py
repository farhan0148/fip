"""Consent-based sharing. The ONE place that decides what an FC may see.

Every FC route calls `visible_categories(fc, client_id)` and loads data only for the
categories it returns, so an ungranted category is never queried, let alone rendered.
A grant counts only while it is active (revoked_at is NULL) AND the client is assigned
to that same FC, so revoking takes effect on the very next request.

Grants are live permissions, not snapshots: the FC sees the latest saved data in a
category until the client revokes it.
"""

import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError

from . import comparison, simulator
from .extensions import db
from .models import (
    SHARE_CATEGORIES,
    ComparisonCell,
    ComparisonFlag,
    FCAssignment,
    FCProfile,
    Policy,
    QuestionnaireAnswer,
    Scenario,
    ShareEvent,
    ShareGrant,
    User,
)
from .questions import QUESTIONS
from .services import display_value

LANGUAGE_NAMES = {"en": "English", "zh": "中文", "ms": "Bahasa Melayu", "ta": "தமிழ்"}
_CODE = re.compile(r"^[A-Z0-9-]{3,20}$")

CATEGORY_INFO = {
    "profile_answers": {
        "short": "Profile and answers",
        "title": "Your name and questionnaire answers",
        "what": "Your name and your answers to the eight questions. Skipped questions show as “Not answered”.",
        "not": "Not included: your email address, password, simulator figures or anything else.",
    },
    "simulator": {
        "short": "Simulator scenario",
        "title": "Your saved simulator scenario",
        "what": "The inputs and results of your last saved scenario: age, support, debts, savings, existing cover, "
                "added plans, life events and the calculated gaps. Only what you have saved is shared, and it updates "
                "each time you save, until you stop sharing.",
        "not": "Not included: unsaved edits, the dashboard demo score and radar (sample values), or AI explanations.",
    },
    "flagged_items": {
        "short": "Flagged items",
        "title": "Comparison items you flagged for verification",
        "what": "The comparison cells you flagged with “Flag for my FC to verify”: the plan, the row, the finding "
                "and the policy wording it cites.",
        "not": "Not included: comparisons you did not flag.",
    },
}


def now():
    return datetime.now(timezone.utc)


def money(value):
    return ("-" if value < 0 else "") + "S$" + f"{abs(value):,}"


# ---------- who may see what ----------

def visible_categories(fc, client_id):
    """Categories this FC may see for this client right now. Empty means: show nothing, answer 404."""
    if fc is None or fc.role != "fc":
        return frozenset()
    rows = db.session.execute(
        select(ShareGrant.category)
        .join(
            FCAssignment,
            and_(FCAssignment.client_id == ShareGrant.client_id, FCAssignment.fc_id == ShareGrant.fc_id),
        )
        .join(User, User.id == ShareGrant.client_id)
        .where(
            ShareGrant.client_id == client_id,
            ShareGrant.fc_id == fc.id,
            ShareGrant.revoked_at.is_(None),
            User.role == "client",
        )
    ).all()
    return frozenset(row[0] for row in rows)


def ordered(categories):
    return [c for c in SHARE_CATEGORIES if c in categories]


def clients_for_fc(fc):
    """Clients with at least one active grant to this FC: [{client_id, label, categories, since}]."""
    rows = db.session.execute(
        select(ShareGrant.client_id, ShareGrant.category, ShareGrant.granted_at)
        .join(
            FCAssignment,
            and_(FCAssignment.client_id == ShareGrant.client_id, FCAssignment.fc_id == ShareGrant.fc_id),
        )
        .join(User, User.id == ShareGrant.client_id)
        .where(ShareGrant.fc_id == fc.id, ShareGrant.revoked_at.is_(None), User.role == "client")
        .order_by(ShareGrant.client_id, ShareGrant.granted_at)
    ).all()
    clients = {}
    for client_id, category, granted_at in rows:
        entry = clients.setdefault(client_id, {"client_id": client_id, "categories": {}, "since": granted_at})
        entry["categories"][category] = granted_at
        entry["since"] = min(entry["since"], granted_at)

    # A name is part of "profile_answers", so only look it up for clients who granted that category.
    named = [cid for cid, e in clients.items() if "profile_answers" in e["categories"]]
    names = {}
    if named:
        names = dict(db.session.execute(select(User.id, User.full_name).where(User.id.in_(named))).all())
    result = []
    for client_id, entry in clients.items():
        entry["label"] = names.get(client_id) or f"Client #{client_id}"
        entry["category_list"] = [(c, entry["categories"][c]) for c in ordered(entry["categories"])]
        result.append(entry)
    return sorted(result, key=lambda e: e["since"], reverse=True)


# ---------- the data in each category (used for the client's preview and the FC's view) ----------

def load_category(category, client_id):
    if category == "profile_answers":
        return _load_profile(client_id)
    if category == "simulator":
        return _load_simulator(client_id)
    if category == "flagged_items":
        return _load_flagged(client_id)
    raise ValueError(f"unknown category {category!r}")


def _load_profile(client_id):
    name = db.session.execute(select(User.full_name).where(User.id == client_id)).scalar_one()
    rows = {a.question_id: a for a in QuestionnaireAnswer.query.filter_by(user_id=client_id)}
    answers = [(q.review_label, display_value(q, rows.get(q.id))) for q in QUESTIONS]
    return {"kind": "profile", "name": name, "answers": answers}


def _load_simulator(client_id):
    scenario = Scenario.query.filter_by(user_id=client_id).first()
    if scenario is None:
        return {"kind": "simulator", "saved": False}
    inputs, result = scenario.inputs, scenario.result
    fields = []
    for key, (label, kind) in simulator.FIELDS.items():
        value = inputs["fields"].get(key)
        fields.append((label, f"{value}" if kind in ("age", "years") else money(value)))
    plan_ids = inputs.get("plans", [])
    names = dict(db.session.execute(select(Policy.id, Policy.name).where(Policy.id.in_(plan_ids))).all()) if plan_ids else {}
    events = [
        (simulator.EVENT_TYPES[e["type"]]["label"], e["age"], money(e["amount"]))
        for e in inputs.get("events", [])
    ]
    return {
        "kind": "simulator",
        "saved": True,
        "saved_at": scenario.updated_at,
        "fields": fields,
        "plans": [names.get(pid, "Plan no longer available") for pid in plan_ids],
        "events": events,
        "gaps": [
            ("Gap today", money(result["gap_today"])),
            ("After life events", money(result["gap_after_events"])),
            ("With added plans", money(result["gap_with_plans"]) if result["gap_with_plans"] is not None else "No plan added"),
        ],
        "changes": [(c["label"], money(c["gap_effect"])) for c in result["contributions"]],
    }


def _load_flagged(client_id):
    flags = ComparisonFlag.query.filter_by(user_id=client_id).order_by(ComparisonFlag.created_at).all()
    row_labels = dict(comparison.ROWS)
    items = []
    for flag in flags:
        policy = db.session.get(Policy, flag.policy_id)
        cell = ComparisonCell.query.filter_by(policy_id=flag.policy_id, row_key=flag.row_key).first()
        clause = cell.clause if cell is not None else None
        items.append({
            "plan": policy.name if policy else "Plan no longer available",
            "row": row_labels.get(flag.row_key, flag.row_key),
            "state": comparison.STATE_LABELS[cell.state] if cell else comparison.STATE_LABELS["unconfirmed"],
            "finding": cell.finding if cell else comparison.NOT_STATED,
            "source": f"p.{clause.page}, cl.{clause.clause}, line {clause.line}" if clause else None,
            "wording": clause.text if clause else None,
            "flagged_at": flag.created_at,
        })
    return {"kind": "flagged", "entries": items}


# ---------- referral codes and matching ----------

def normalize_code(raw):
    code = re.sub(r"\s+", "", (raw or "")).upper()
    return code if _CODE.match(code) else None


def fc_by_referral_code(raw):
    """The FC user for a code, or None. The same answer for 'no such code' and 'not an FC'."""
    code = normalize_code(raw)
    if code is None:
        return None
    row = db.session.execute(
        select(User)
        .join(FCProfile, FCProfile.user_id == User.id)
        .where(FCProfile.referral_code == code, User.role == "fc")
    ).scalar_one_or_none()
    return row


def fc_preview(fc):
    """What a client may see about an FC before choosing: name, speciality, languages. Never the email or code."""
    profile = FCProfile.query.filter_by(user_id=fc.id).first()
    return {
        "id": fc.id,
        "name": fc.full_name,
        "speciality": profile.speciality if profile else "",
        "languages": profile.languages if profile else [],
    }


def pick_random_fc(client):
    """A least-loaded FC who speaks the client's language (any FC if none does)."""
    fcs = db.session.execute(
        select(User, FCProfile).join(FCProfile, FCProfile.user_id == User.id).where(User.role == "fc")
    ).all()
    if not fcs:
        return None
    loads = dict(db.session.execute(select(FCAssignment.fc_id, db.func.count()).group_by(FCAssignment.fc_id)).all())
    wanted = LANGUAGE_NAMES.get(client.language, "English")
    speaking = [(u, p) for u, p in fcs if wanted in (p.languages or [])]
    pool = speaking or fcs
    least = min(loads.get(u.id, 0) for u, _ in pool)
    return secrets.choice([u for u, _ in pool if loads.get(u.id, 0) == least])


def assignment_for(client_id):
    return FCAssignment.query.filter_by(client_id=client_id).first()


# ---------- changing consent ----------

def active_grants(client_id):
    return (
        ShareGrant.query.filter_by(client_id=client_id, revoked_at=None)
        .order_by(ShareGrant.granted_at)
        .all()
    )


def _event(client_id, fc_id, category, action):
    db.session.add(ShareEvent(client_id=client_id, fc_id=fc_id, category=category, action=action, at=now()))


def confirm_sharing(client, fc, method, categories):
    """Assign the FC (first time only) and grant the categories. Returns the newly granted list.

    Raises ValueError if the client is already assigned to a different FC (changing FC is a later feature).
    """
    categories = [c for c in SHARE_CATEGORIES if c in set(categories)]
    existing = assignment_for(client.id)
    if existing is not None and existing.fc_id != fc.id:
        raise ValueError("client already has a different FC")
    if existing is None:
        db.session.add(FCAssignment(client_id=client.id, fc_id=fc.id, method=method))
    already = {g.category for g in active_grants(client.id) if g.fc_id == fc.id}
    granted = []
    for category in categories:
        if category in already:
            continue
        db.session.add(ShareGrant(client_id=client.id, fc_id=fc.id, category=category, granted_at=now()))
        _event(client.id, fc.id, category, "granted")
        granted.append(category)
    try:
        db.session.commit()
    except IntegrityError:  # a double submit raced us; the unique index kept it to one active grant
        db.session.rollback()
        return []
    return granted


def revoke(client, category):
    """End the active grant for a category. Returns True if there was one."""
    grants = ShareGrant.query.filter_by(client_id=client.id, category=category, revoked_at=None).all()
    for grant in grants:
        grant.revoked_at = now()
        _event(client.id, grant.fc_id, category, "revoked")
    db.session.commit()
    return bool(grants)


def record_views(fc, client_id, categories):
    for category in ordered(categories):
        _event(client_id, fc.id, category, "viewed")
    db.session.commit()


def recent_events(client_id, limit=20):
    rows = db.session.execute(
        select(ShareEvent.at, ShareEvent.action, ShareEvent.category, User.full_name)
        .join(User, User.id == ShareEvent.fc_id)
        .where(ShareEvent.client_id == client_id)
        .order_by(ShareEvent.at.desc(), ShareEvent.id.desc())
        .limit(limit)
    ).all()
    return rows
