"""FC portal demo (Week 7): a fictional incoming client, a simulated meeting and a sample ROA.

Everything here is a clickable prototype. The client is fictional and has no account, so no real client data is
involved and the consent checks of the real portal (app/fc_portal.py, app/sharing.py) are not bypassed: real
clients still appear only through their own grants. State is kept per FC in `fc_demo_state` and can be reset.
The ROA is built from a local template (no AI). Live video and actual sign-off wait for Week 14.
"""

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from . import demo_fixture
from .extensions import db
from .models import ComparisonCell, FCDemoState, Policy
from .security import check_csrf, fc_required

bp = Blueprint("fc_demo", __name__, url_prefix="/fc/demo")

CLIENT = {
    "name": "Priya Nair",
    "tag": "Fictional demo client",
    "code": "FC-2048",
    "age": 30,
    "summary_line": "30, married, one child, full-time employee. Budget S$100 to S$300 a month.",
    "concerns": "Income protection for the spouse and child, and private hospital cover.",
    "needs": ["Protect spouse and child if income stops", "Build a retirement fund by age 60",
              "Keep premiums under S$300 a month", "Hospital cover at private ward level"],
    "existing": [("PRULife Basic", "Whole life", "S$250,000", "S$110/mo"),
                 ("Employer group plan", "Hospitalisation", "Ward B1", "Employer-paid")],
    "shared": ["Profile and answers", "Coverage map", "Quiz results"],
    "plans_compared": ["PRUActive Term", "PRUShield Premier", "PRUCritical Care"],
    "questions": ["Does PRUActive Term pay out for critical illness?",
                  "How would adding critical illness cover change my budget?"],
}
MEETING_POINTS = [
    "Client is worried about income protection for the spouse and child.",
    "Budget is capped at S$300 a month and the client is open to term over whole life.",
    "Client wants hospital cover upgraded to a private ward.",
    "Follow-up: compare PRUActive Term with PRULife Whole.",
]
MEETING_ACTIONS = ["FC to send the plan comparison by Friday."]


def get_state(fc):
    state = FCDemoState.query.filter_by(fc_id=fc.id).first()
    if state is None:
        state = FCDemoState(fc_id=fc.id)
        db.session.add(state)
        db.session.commit()
    return state


@bp.app_context_processor
def inject():
    user = g.get("user")
    if user is not None and user.role == "fc":
        return {"fc_demo": get_state(user)}
    return {}


def _gaps():
    axes = sorted(demo_fixture.load()["axes"], key=lambda a: a["value"])
    return [(a["name"], a["value"]) for a in axes[:3]]


def _findings():
    """Available findings: the demo comparison cells of the plan the client is looking at."""
    plan = Policy.query.filter_by(is_demo=True, name="PRUActive Term").first()
    if plan is None:
        return None, []
    cells = ComparisonCell.query.filter_by(policy_id=plan.id).all()
    keep = {"death_cover": "Death and disability payout", "critical_illness": "Critical illness",
            "hospital": "Hospital bills"}
    return plan, [(keep[c.row_key], c.finding) for c in cells if c.row_key in keep]


def build_roa(state, use_edits=True):
    """The sample ROA from a local template. Returns (sections, missing items)."""
    plan, findings = _findings()
    gaps = _gaps()
    missing = []
    notes = (" ".join(MEETING_POINTS)) if state.notes_added else None
    if notes is None:
        missing.append("Meeting notes have not been added to the client profile.")
    missing.append("Client acknowledgement: the fictional client has no comprehension quiz record.")
    if not findings:
        missing.append("No comparison findings are available.")
    price = f"S${plan.monthly_premium_cents // 100}/mo" if plan else "price not available"
    sections = [
        {"key": "profile", "title": "1. Client profile", "source": "Client profile",
         "text": f"{CLIENT['summary_line']} Existing cover: " + "; ".join(f"{n} ({t}, {s})" for n, t, s, _ in CLIENT["existing"]) + "."},
        {"key": "needs", "title": "2. Needs and objectives", "source": "Meeting summary" if notes else "Client profile",
         "text": (notes or CLIENT["concerns"])},
        {"key": "gaps", "title": "3. Gaps identified", "source": "Gap analysis (illustrative demo values)",
         "text": "Lowest areas: " + ", ".join(f"{n} {v}%" for n, v in gaps) + ". These sample values are not from real policies."},
        {"key": "findings", "title": "4. Findings from the sample plan wording", "source": "Plan comparison",
         "text": (f"{plan.name} ({price}): " + " ".join(f"{label}: {text}" for label, text in findings)) if findings
                 else "No findings available."},
        {"key": "ack", "title": "5. Client acknowledgement", "source": "Quiz record",
         "text": "Not available. No quiz record exists for this fictional client."},
    ]
    for section in sections:
        edited = (state.roa_sections or {}).get(section["key"]) if use_edits else None
        section["text"] = edited if edited is not None else section["text"]
        section["edited"] = edited is not None
    return sections, missing


def _accepted(state):
    if state.request_status != "accepted":
        return False
    return True


@bp.get("/request")
@fc_required
def request_page():
    state = get_state(g.user)
    return render_template("fc/demo_request.html", client=CLIENT, state=state, gaps=_gaps(),
                           page_title="New client request")


@bp.post("/request/<action>")
@fc_required
def decide(action):
    check_csrf()
    if action not in ("accept", "decline"):
        abort(404)
    state = get_state(g.user)
    state.request_status = "accepted" if action == "accept" else "declined"
    db.session.commit()
    return redirect(url_for("fc_demo.request_page"))


@bp.get("/profile")
@fc_required
def profile():
    state = get_state(g.user)
    if not _accepted(state):
        return redirect(url_for("fc_demo.request_page"))
    return render_template("fc/demo_profile.html", client=CLIENT, state=state, gaps=_gaps(),
                           axes=demo_fixture.load()["axes"], notes=MEETING_POINTS if state.notes_added else [],
                           page_title=CLIENT["name"])


@bp.get("/meeting")
@fc_required
def meeting():
    state = get_state(g.user)
    if not _accepted(state):
        return redirect(url_for("fc_demo.request_page"))
    return render_template("fc/demo_meeting.html", client=CLIENT, state=state, points=MEETING_POINTS,
                           actions=MEETING_ACTIONS, page_title="Meeting")


@bp.post("/meeting/<action>")
@fc_required
def meeting_action(action):
    check_csrf()
    state = get_state(g.user)
    if not _accepted(state):
        abort(400)
    if action == "join" and state.meeting_status == "idle":
        state.meeting_status = "joined"
    elif action == "end" and state.meeting_status == "joined":
        state.meeting_status = "ended"
    elif action == "add-notes" and state.meeting_status in ("joined", "ended"):
        state.notes_added = True
    else:
        abort(400)
    db.session.commit()
    return redirect(url_for("fc_demo.meeting"))


@bp.get("/roa")
@fc_required
def roa():
    state = get_state(g.user)
    if not _accepted(state):
        return redirect(url_for("fc_demo.request_page"))
    sections, missing = build_roa(state)
    return render_template("fc/demo_roa.html", client=CLIENT, state=state, sections=sections, missing=missing,
                           saved=request.args.get("saved") == "1", page_title="Record of advice")


@bp.post("/roa/<action>")
@fc_required
def roa_action(action):
    check_csrf()
    state = get_state(g.user)
    if not _accepted(state):
        abort(400)
    if state.roa_status == "locked":
        abort(400, description="This draft is locked.")
    if action == "save":
        sections, _ = build_roa(state, use_edits=False)     # only text that differs from the template counts as an edit
        edits = dict(state.roa_sections or {})
        for section in sections:
            text = request.form.get(f"text_{section['key']}")
            if text is None:
                continue
            text = " ".join(text.split())[:2000]
            if text == section["text"]:
                edits.pop(section["key"], None)
            else:
                edits[section["key"]] = text
        state.roa_sections = edits
        if state.roa_status == "reviewed":
            state.roa_status = "draft"        # editing after review sends it back to draft
    elif action == "review":
        state.roa_status = "reviewed"
    elif action == "lock":
        if state.roa_status != "reviewed":
            abort(400, description="Mark the draft reviewed before locking it.")
        state.roa_status = "locked"
    else:
        abort(404)
    db.session.commit()
    return redirect(url_for("fc_demo.roa", saved=1))


@bp.post("/reset")
@fc_required
def reset():
    check_csrf()
    state = get_state(g.user)
    state.request_status, state.meeting_status, state.notes_added = "pending", "idle", False
    state.roa_sections, state.roa_status = None, "draft"
    db.session.commit()
    return redirect(url_for("fc.index"))
