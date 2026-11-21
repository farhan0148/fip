"""Adaptable Record of Advice (ROA) drafts for the FC (Week 8).

* Private to the FC who owns the meeting. Nothing here is ever shown to the customer, and sending meeting notes does
  not share it.
* Built from a local template (no AI). Sources: the client's profile, simulator scenario and flagged items ONLY if the
  client has shared them with this FC (existing consent checks), the FC's own meeting notes, the demo plans' cited
  findings, and the recommendation the FC types in and approves. A missing fact says "To confirm".
* The shape follows the meeting: a plan comparison lists the options and the FC's reasoning, a coverage review lists
  the estimates and assumptions, and with no decision the open questions and next steps are shown.
* Manual edits, added sections and removed sections survive "Refresh from sources".
"""

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from . import comparison, gap_analysis, score as death_score, sharing
from .extensions import db
from .meetings import TOPIC_LABELS, access
from .models import ComparisonCell, Meeting, MeetingNote, Policy, PolicyClause, RoaDraft, Scenario
from .security import check_csrf, fc_required

bp = Blueprint("roa", __name__, url_prefix="/fc/roa")

TO_CONFIRM = "To confirm"
FINDING_ROWS = ("death_cover", "hospital", "critical_illness", "cash_value", "premium_waiver", "duration")
ROW_LABELS = dict(comparison.ROWS)
MAX_SECTIONS = 20
MAX_BODY = 4000


def money(value):
    return f"S${int(value):,}"


def _src(kind, label, url=None):
    return {"kind": kind, "label": label, "url": url}


def _fc_meeting(meeting_id):
    role, meeting = access(meeting_id)
    if role != "fc":
        abort(404)
    return meeting


def _draft(meeting):
    draft = RoaDraft.query.filter_by(meeting_id=meeting.id).first()
    if draft is not None and draft.fc_id != g.user.id:
        abort(404)
    return draft


# ---------- building the sections ----------

def build_sections(meeting, fc, draft):
    """The generated sections for this meeting, in order. Pure function of the stored sources."""
    client = meeting.client
    visible = sharing.visible_categories(fc, client.id)
    note = MeetingNote.query.filter_by(meeting_id=meeting.id).first()
    notes = (note.fc_draft if note is not None and note.fc_draft else None)
    sections = []

    name = client.full_name if "profile_answers" in visible else "the client (name not shared)"
    sections.append({"key": "meeting", "title": "Meeting details", "sources": [_src("meeting", "Meeting record")],
                     "body": f"{meeting.title}, on {meeting.scheduled_at.strftime('%d %b %Y')}, between {fc.full_name} and "
                             f"{name}. Meeting type: {TOPIC_LABELS[meeting.topic]}."})

    if "profile_answers" in visible:
        data = sharing.load_category("profile_answers", client.id)
        lines = [f"{label}: {value}" if value != "Not answered" else f"{label}: {TO_CONFIRM} (not answered)"
                 for label, value in data["answers"]]
        sections.append({"key": "profile", "title": "Client profile", "body": "\n".join(lines),
                         "sources": [_src("profile", "Client profile (shared with you)", url_for("fc.client", client_id=client.id))]})
    else:
        sections.append({"key": "profile", "title": "Client profile",
                         "body": f"{TO_CONFIRM}: the client has not shared their profile with you.", "sources": []})

    if meeting.topic == "plan_comparison":
        sections.append(_options_section(meeting))
    elif meeting.topic == "coverage_review":
        sections.append(_estimates_section(meeting, visible))
    else:
        points = (notes or {}).get("discussion", [])
        sections.append({"key": "topics", "title": "Topics discussed",
                         "body": "\n".join(f"- {p}" for p in points) if points else f"{TO_CONFIRM}: no discussion points have been saved.",
                         "sources": [_src("notes", "Meeting notes", url_for("meeting_notes.notes", meeting_id=meeting.id))]})

    if notes and any(notes.values()):
        body = []
        for key, title in (("discussion", "Discussed"), ("next_steps", "Agreed next steps"), ("open_questions", "Open questions")):
            if notes.get(key):
                body.append(title + ":\n" + "\n".join(f"- {item}" for item in notes[key]))
        notes_body = "\n\n".join(body)
    else:
        notes_body = f"{TO_CONFIRM}: no meeting notes have been saved for this meeting."
    sections.append({"key": "notes", "title": "Meeting notes", "body": notes_body,
                     "sources": [_src("notes", "Your private meeting notes", url_for("meeting_notes.notes", meeting_id=meeting.id))]})

    sections.append(_gap_section(meeting, fc, client))

    approved = bool(draft is not None and draft.recommendation_approved and (draft.recommendation or "").strip())
    if approved:
        title = "FC's recorded reasoning and recommendation" if meeting.topic == "plan_comparison" else "FC's recorded recommendation"
        sections.append({"key": "decision", "title": title, "body": draft.recommendation.strip(),
                         "sources": [_src("fc", "Recorded by the FC")]})
    else:
        opens = (notes or {}).get("open_questions", [])
        steps = (notes or {}).get("next_steps", [])
        body = ["No decision has been recorded."]
        body.append("Open questions:\n" + ("\n".join(f"- {q}" for q in opens) if opens else f"- {TO_CONFIRM}: none were saved."))
        body.append("Next steps:\n" + ("\n".join(f"- {s}" for s in steps) if steps else f"- {TO_CONFIRM}: none were saved."))
        body.append(f"{TO_CONFIRM}: the FC's recommendation. Record and approve it below.")
        sections.append({"key": "decision", "title": "Decision status", "body": "\n\n".join(body),
                         "sources": [_src("notes", "Your private meeting notes", url_for("meeting_notes.notes", meeting_id=meeting.id))]})

    sections.append({"key": "ack", "title": "Client acknowledgement",
                     "body": f"{TO_CONFIRM}: the client's acknowledgement has not been recorded.", "sources": []})
    for section in sections:
        section.setdefault("edited", False)
        section["manual"] = False
        section["id"] = section["key"]
    return sections


def _options_section(meeting):
    plans = [p for p in (db.session.get(Policy, pid) for pid in (meeting.plan_ids or [])) if p is not None and p.is_demo]
    if not plans:
        return {"key": "options", "title": "Options discussed",
                "body": f"{TO_CONFIRM}: no plans were recorded for this meeting.", "sources": []}
    blocks, sources = [], []
    for plan in plans:
        cells = {c.row_key: c for c in ComparisonCell.query.filter_by(policy_id=plan.id) if c.clause_id}
        lines = [f"{plan.name} ({plan.type}), S${plan.monthly_premium_cents // 100} a month (sample price)."]
        for key in FINDING_ROWS:
            cell = cells.get(key)
            if cell is None:
                continue
            lines.append(f"- {ROW_LABELS.get(key, key)}: {cell.finding}")
            clause = db.session.get(PolicyClause, cell.clause_id)
            sources.append(_src("clause", f"{plan.name}: {ROW_LABELS.get(key, key)} (p.{clause.page}, cl.{clause.clause})",
                                url_for("roa.clause", meeting_id=meeting.id, clause_id=clause.id)))
        blocks.append("\n".join(lines))
    return {"key": "options", "title": "Options discussed", "body": "\n\n".join(blocks), "sources": sources}


def _estimates_section(meeting, visible):
    client_id = meeting.client_id
    if "simulator" not in visible:
        return {"key": "estimates", "title": "Estimates and assumptions",
                "body": f"{TO_CONFIRM}: the client has not shared a simulator scenario with you.", "sources": []}
    data = sharing.load_category("simulator", client_id)
    if not data.get("saved"):
        return {"key": "estimates", "title": "Estimates and assumptions",
                "body": f"{TO_CONFIRM}: the client has not saved a scenario.", "sources": []}
    scenario = Scenario.query.filter_by(user_id=client_id).first()
    cover = scenario.inputs["fields"]["existing_cover"]
    need = scenario.result["need_today"]
    value = death_score.score(cover, need)
    lines = ["Death cover only. These are estimates from the client's saved scenario, to be confirmed.",
             f"Estimated need today: {money(need)}. Existing death cover: {money(cover)}.",
             "Death cover score: " + (f"{value} / 100." if value is not None else "no percentage (estimated need is zero)."),
             "Gaps: " + "; ".join(f"{label} {amount}" for label, amount in data["gaps"]) + ".",
             "Assumptions: " + "; ".join(f"{label} {value_}" for label, value_ in data["fields"]) + "."]
    if data["events"]:
        lines.append("Life events included: " + "; ".join(f"{label} at age {age} ({amount})" for label, age, amount in data["events"]) + ".")
    if data["plans"]:
        lines.append("Plans added in the what-if: " + ", ".join(data["plans"]) + ".")
    return {"key": "estimates", "title": "Estimates and assumptions", "body": "\n".join(lines),
            "sources": [_src("simulator", "Client's saved simulator scenario (shared with you)", url_for("fc.client", client_id=client_id))]}


def _gap_section(meeting, fc, client):
    """Only CONFIRMED, not-stale gap-analysis findings reach the ROA. Dismissed, open or stale ones are left out."""
    confirmed = gap_analysis.confirmed_for_roa(fc, client.id)
    cover, overlaps = confirmed["cover"], confirmed["overlaps"]
    source = _src("gap", "Gap analysis (confirmed findings)", url_for("fc.gap", client_id=client.id))
    if cover is None and not overlaps:
        return {"key": "gap", "title": "Gap analysis", "sources": [source],
                "body": f"{TO_CONFIRM}: no gap-analysis findings have been confirmed yet. Confirm them in the gap analysis to include them here."}
    lines = []
    if cover is not None:
        if cover["shortfall"] > 0:
            result = f"a shortfall of {money(cover['shortfall'])}"
        elif cover["surplus"] > 0:
            result = f"a surplus of {money(cover['surplus'])}"
        else:
            result = "need and existing cover match"
        lines.append(f"Confirmed death-cover position: estimated need {money(cover['need'])}, existing cover "
                     f"{money(cover['cover'])}, {result}. Death cover only, to be confirmed with the client.")
    else:
        lines.append(f"{TO_CONFIRM}: the death-cover need and existing cover have not been confirmed in the gap analysis.")
    if overlaps:
        lines.append("Confirmed possible overlaps (check the cited wording before any change; not a reason to cancel):")
        for o in overlaps:
            plans = " and ".join(p["plan"] for p in o["policies"])
            lines.append(f"- {o['row_label']}: {plans} both cite wording for {o['event']}." + (f" Note: {o['note']}" if o["note"] else ""))
    return {"key": "gap", "title": "Gap analysis", "body": "\n".join(lines), "sources": [source]}


def merge(existing, generated, removed):
    """Refresh from sources without losing the FC's work. Edited and manual sections are kept exactly as they are."""
    by_id = {s["id"]: s for s in existing}
    merged = []
    for section in generated:
        if section["key"] in removed:
            continue
        current = by_id.get(section["id"])
        if current is not None and current.get("edited"):
            merged.append(current)
        else:
            merged.append(section)
    merged += [s for s in existing if s.get("manual")]
    return merged


# ---------- pages ----------

@bp.get("")
@fc_required
def index():
    meetings = Meeting.query.filter_by(fc_id=g.user.id).order_by(Meeting.scheduled_at.desc()).all()
    drafts = {d.meeting_id: d for d in RoaDraft.query.filter_by(fc_id=g.user.id)}
    return render_template("roa/index.html", meetings=meetings, drafts=drafts, topics=TOPIC_LABELS, page_title="ROA drafts")


@bp.get("/<int:meeting_id>")
@fc_required
def open(meeting_id):
    meeting = _fc_meeting(meeting_id)
    draft = _draft(meeting)
    return render_template("roa/editor.html", meeting=meeting, draft=draft, topics=TOPIC_LABELS,
                           saved=request.args.get("saved") == "1", page_title="Record of advice")


@bp.post("/<int:meeting_id>/generate")
@fc_required
def generate(meeting_id):
    check_csrf()
    meeting = _fc_meeting(meeting_id)
    if _draft(meeting) is None:
        draft = RoaDraft(meeting_id=meeting.id, fc_id=g.user.id, client_id=meeting.client_id, sections=[], removed=[])
        draft.sections = build_sections(meeting, g.user, draft)
        db.session.add(draft)
        db.session.commit()
    return redirect(url_for("roa.open", meeting_id=meeting.id))


@bp.post("/<int:meeting_id>/save")
@fc_required
def save(meeting_id):
    check_csrf()
    meeting = _fc_meeting(meeting_id)
    draft = _draft(meeting)
    if draft is None:
        abort(404)
    action = request.form.get("action", "save")
    sections = [dict(s) for s in draft.sections]

    # 1. the typed recommendation
    recommendation = request.form.get("recommendation", draft.recommendation or "").strip()[:MAX_BODY]
    approved = request.form.get("recommendation_approved") == "on" and bool(recommendation)
    rec_changed = (recommendation != (draft.recommendation or "").strip()) or approved != draft.recommendation_approved
    draft.recommendation, draft.recommendation_approved = recommendation or None, approved

    # 2. the edits to each section
    for section in sections:
        title = request.form.get(f"title_{section['id']}")
        body = request.form.get(f"body_{section['id']}")
        if title is not None and body is not None:
            title, body = " ".join(title.split())[:120], body.replace("\r\n", "\n")[:MAX_BODY]
            if section["id"] == "decision" and rec_changed:
                continue                              # the recorded recommendation drives this section
            if title != section["title"] or body != section["body"]:
                section["title"], section["body"], section["edited"] = title or section["title"], body, True

    removed = list(draft.removed or [])
    if action.startswith("remove:"):
        sid = action.split(":", 1)[1]
        target = next((s for s in sections if s["id"] == sid), None)
        if target is not None:
            sections.remove(target)
            if target.get("key"):
                removed.append(target["key"])
    elif action == "add" and len(sections) < MAX_SECTIONS:
        number = 1 + max([int(s["id"][2:]) for s in sections if s["id"].startswith("m-")] or [0])
        sections.append({"id": f"m-{number}", "key": None, "title": "New section", "body": "", "sources": [],
                         "edited": True, "manual": True})
    draft.sections, draft.removed = sections, removed

    # 3. refresh generated sections (never the edited or manual ones) when asked, or when the recommendation changed
    if action == "refresh" or rec_changed:
        if rec_changed:
            for section in draft.sections:
                if section["id"] == "decision":
                    section["edited"] = False         # the typed recommendation replaces the generated decision text
        draft.sections = merge(draft.sections, build_sections(meeting, g.user, draft), set(draft.removed))
    if action == "review":
        draft.status = "reviewed"
    else:
        draft.status = "draft"                        # any change after review needs a new review
    db.session.commit()
    return redirect(url_for("roa.open", meeting_id=meeting.id, saved=1))


@bp.get("/<int:meeting_id>/clause/<int:clause_id>")
@fc_required
def clause(meeting_id, clause_id):
    """The cited sample clause, in its original wording, for a source link in the ROA."""
    meeting = _fc_meeting(meeting_id)
    row = db.session.get(PolicyClause, clause_id)
    if row is None or row.policy_id not in (meeting.plan_ids or []):
        abort(404)
    lines = PolicyClause.query.filter_by(policy_id=row.policy_id, page=row.page).order_by(PolicyClause.line).all()
    return render_template("roa/clause.html", meeting=meeting, clause=row, lines=lines, page_title="Source clause")
