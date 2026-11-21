"""Fictional demo setup for the meeting, notes and ROA features. Used by `flask seed-meetings` and by the tests.

It never overwrites anything: answers, an FC assignment and a saved scenario are added only when the client has none,
and the demo meetings are created once (matched by title). No real policy is involved.
"""

from datetime import datetime, timedelta, timezone

from . import simulator, sharing
from .extensions import db
from .models import (
    SHARE_CATEGORIES, ComparisonFlag, Meeting, MeetingNote, Policy, QuestionnaireAnswer, Scenario,
)

DEMO_ANSWERS = {"q1": "30", "q2": "full_time", "q3": "spouse_child", "q4": "100_300",
                "q5": "family_income", "q6": "at_60", "q7": "private", "q8": "personal_employer"}
WORKED = {"fields": {"age": "30", "support_years": "5", "support_annual": "50,000", "housing_debt": "80,000",
                     "other_debts": "0", "savings": "50,000", "existing_cover": "150,000"}, "plans": [], "events": []}

NOTES = {
    "Plan comparison: term vs hospital": {
        "discussion": ["Compared PRUActive Term and PRUShield Premier for a family with one child.",
                       "Term cover is the cheaper way to protect the family's income.",
                       "Hospital cover matters most to the client; private ward level was discussed."],
        "next_steps": ["FC to send a side-by-side comparison by Friday.",
                       "Client to check what the employer hospital plan already covers."],
        "open_questions": ["Does the client want critical illness added to the term plan?"],
    },
    "Coverage review: protection gap": {
        "discussion": ["Reviewed the saved death cover estimate and its assumptions with the client.",
                       "The estimate counts five years of support and the housing debt."],
        "next_steps": ["Client to update savings and existing cover in the simulator."],
        "open_questions": ["Is the employer group life cover included in the existing cover figure?",
                           "Will a second child be planned in the next three years?"],
    },
}


def _demo(name):
    return Policy.query.filter_by(is_demo=True, name=name).first()


def ensure_client_ready(client):
    """Answers and a confirmed review, only if the client has none. Returns True if anything was added."""
    if QuestionnaireAnswer.query.filter_by(user_id=client.id).count() == 0:
        for question_id, key in DEMO_ANSWERS.items():
            db.session.add(QuestionnaireAnswer(user_id=client.id, question_id=question_id, status="answered", selected=[key]))
    if client.review_confirmed_at is None:
        client.review_confirmed_at = datetime.now(timezone.utc)
    db.session.commit()


def ensure_scenario(client):
    if Scenario.query.filter_by(user_id=client.id).first() is not None:
        return
    catalogue = {p.id: simulator.catalogue_entry(p) for p in Policy.query.filter_by(is_demo=True)}
    clean, errors = simulator.validate(WORKED, catalogue)
    if errors:
        return
    db.session.add(Scenario(user_id=client.id, inputs=clean, result=simulator.calculate(clean, catalogue)))
    db.session.commit()


def ensure_assignment(client, fc, categories=SHARE_CATEGORIES):
    if sharing.assignment_for(client.id) is None:
        sharing.confirm_sharing(client, fc, "referral", list(categories))
    plan = _demo("PRUShield Premier")
    if plan is not None and ComparisonFlag.query.filter_by(user_id=client.id).count() == 0:
        db.session.add(ComparisonFlag(user_id=client.id, policy_id=plan.id, row_key="premium_waiver"))
        db.session.commit()


def ensure_meetings(client, fc):
    """Two finished meetings with private AI-style drafts (not sent) and one upcoming call. Returns the three."""
    now = datetime.now(timezone.utc)
    term, shield = _demo("PRUActive Term"), _demo("PRUShield Premier")
    specs = [
        ("Plan comparison: term vs hospital", "plan_comparison", [p.id for p in (term, shield) if p], "ended", now - timedelta(days=7)),
        ("Coverage review: protection gap", "coverage_review", [], "ended", now - timedelta(days=2)),
        ("Intro call (live)", "general", [], "scheduled", now),
    ]
    out = []
    for title, topic, plan_ids, status, when in specs:
        meeting = Meeting.query.filter_by(client_id=client.id, fc_id=fc.id, title=title).first()
        if meeting is None:
            meeting = Meeting(client_id=client.id, fc_id=fc.id, title=title, topic=topic, plan_ids=plan_ids,
                              status=status, scheduled_at=when, started_at=when if status == "ended" else None,
                              ended_at=when + timedelta(minutes=25) if status == "ended" else None)
            db.session.add(meeting)
            db.session.flush()
            if title in NOTES:
                draft = {k: list(v) for k, v in NOTES[title].items()}
                db.session.add(MeetingNote(meeting_id=meeting.id, process_status="ready", progress="Draft ready for your review",
                                           ai_draft=draft, fc_draft=json_copy(draft)))
        out.append(meeting)
    db.session.commit()
    return out


def json_copy(value):
    return {k: list(v) for k, v in value.items()}


def seed_for(client, fc, categories=SHARE_CATEGORIES):
    ensure_client_ready(client)
    ensure_assignment(client, fc, categories)
    ensure_scenario(client)
    return ensure_meetings(client, fc)
