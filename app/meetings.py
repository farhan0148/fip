"""Real video meetings between a client and their assigned FC (WebRTC, signalled through this app).

* Only the client and the FC of that client's assignment can open a meeting (anyone else, or a client who has
  changed FC, gets 404). Media goes browser to browser. This app only passes the small signalling messages.
* Audio is captured in each person's own browser, only while BOTH have agreed, and only the finished, consented
  segments are uploaded. Withdrawing consent or ending the meeting stops capture. The call itself continues.
* Nothing here calls an AI provider. Notes are made later, from the FC's notes page, by an explicit button.
"""

import json
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, g, jsonify, redirect, render_template, request, url_for

from . import sharing
from .catalogue import legacy_demo_policies
from .extensions import db
from .models import FCAssignment, Meeting, MeetingNote, MeetingRecording, MeetingSignal, User
from .security import check_csrf, fc_required, onboarding_complete_required, session_required

bp = Blueprint("meetings", __name__)

PRESENCE_SECONDS = 8
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_SEGMENTS = 20
MAX_SIGNAL_CHARS = 30000
TOPICS = (("plan_comparison", "Plan comparison"), ("coverage_review", "Coverage review"), ("general", "General conversation"))
TOPIC_LABELS = dict(TOPICS)


def now():
    return datetime.now(timezone.utc)


def access(meeting_id):
    """(role, meeting) for the signed-in participant, or 404. The FC must still be the client's assigned FC."""
    meeting = db.session.get(Meeting, meeting_id)
    if meeting is None:
        abort(404)
    assignment = sharing.assignment_for(meeting.client_id)
    if assignment is None or assignment.fc_id != meeting.fc_id:
        abort(404)
    if g.user.role == "client" and g.user.id == meeting.client_id:
        return "client", meeting
    if g.user.role == "fc" and g.user.id == meeting.fc_id:
        return "fc", meeting
    abort(404)


def ice_servers():
    raw = current_app.config.get("MEETING_ICE_SERVERS")
    if raw:
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return [{"urls": "stun:stun.l.google.com:19302"}]


def _config(role, meeting):
    return {
        "meetingId": meeting.id, "role": role, "csrf": None,
        "urls": {name: url_for(f"meetings.{name}", meeting_id=meeting.id)
                 for name in ("join", "state", "signal", "consent", "recording", "end", "leave")},
        "ice": ice_servers(), "ended": meeting.status == "ended",
        "afterEnd": url_for("meeting_notes.notes", meeting_id=meeting.id) if role == "fc" else url_for("meetings.index"),
    }


# ---------- pages ----------

@bp.get("/meetings")
@onboarding_complete_required
def index():
    meetings = Meeting.query.filter_by(client_id=g.user.id).order_by(Meeting.scheduled_at.desc()).all()
    assignment = sharing.assignment_for(g.user.id)
    meetings = [m for m in meetings if assignment is not None and assignment.fc_id == m.fc_id]
    return render_template("meetings/index.html", meetings=meetings, topics=TOPIC_LABELS, page_title="Meetings")


@bp.get("/meetings/<int:meeting_id>")
@onboarding_complete_required
def room_client(meeting_id):
    role, meeting = access(meeting_id)
    if role != "client":
        abort(404)
    return render_template("meetings/room_client.html", meeting=meeting, role=role, config=_config(role, meeting),
                           topics=TOPIC_LABELS, page_title=meeting.title)


@bp.get("/fc/meetings")
@fc_required
def fc_index():
    meetings = Meeting.query.filter_by(fc_id=g.user.id).order_by(Meeting.scheduled_at.desc()).all()
    clients = (User.query.join(FCAssignment, FCAssignment.client_id == User.id)
               .filter(FCAssignment.fc_id == g.user.id).order_by(User.full_name).all())
    notes = {n.meeting_id: n for n in MeetingNote.query.filter(MeetingNote.meeting_id.in_([m.id for m in meetings])).all()} if meetings else {}
    return render_template("meetings/fc_index.html", meetings=meetings, clients=clients, notes=notes, topics=TOPICS,
                           topic_labels=TOPIC_LABELS, plans=legacy_demo_policies(), error=request.args.get("error"),
                           page_title="Meetings")


@bp.post("/fc/meetings")
@fc_required
def fc_create():
    check_csrf()
    try:
        client_id = int(request.form.get("client_id", ""))
    except ValueError:
        abort(400)
    assignment = FCAssignment.query.filter_by(client_id=client_id, fc_id=g.user.id).first()
    if assignment is None:
        abort(404)                                   # not your client
    title = " ".join(request.form.get("title", "").split())[:120]
    topic = request.form.get("topic")
    if not title or topic not in TOPIC_LABELS:
        return redirect(url_for("meetings.fc_index", error="fields"))
    demo_ids = {p.id for p in legacy_demo_policies()}
    plan_ids = []
    for raw in request.form.getlist("plan_ids"):
        try:
            value = int(raw)
        except ValueError:
            continue
        if value in demo_ids and value not in plan_ids:
            plan_ids.append(value)
    meeting = Meeting(client_id=client_id, fc_id=g.user.id, title=title, topic=topic, plan_ids=plan_ids)
    db.session.add(meeting)
    db.session.commit()
    return redirect(url_for("meetings.room_fc", meeting_id=meeting.id))


@bp.get("/fc/meetings/<int:meeting_id>")
@fc_required
def room_fc(meeting_id):
    role, meeting = access(meeting_id)
    if role != "fc":
        abort(404)
    return render_template("meetings/room_fc.html", meeting=meeting, role=role, config=_config(role, meeting),
                           topics=TOPIC_LABELS, page_title=meeting.title)


# ---------- call and recording API (both participants) ----------

def _touch(role, meeting):
    if role == "client":
        meeting.client_seen_at = now()
    else:
        meeting.fc_seen_at = now()


@bp.post("/meetings/<int:meeting_id>/join")
@session_required
def join(meeting_id):
    check_csrf()
    role, meeting = access(meeting_id)
    if meeting.status == "ended":
        return jsonify(error="ended"), 409
    meeting.epoch += 1                                  # a fresh negotiation; old signals are meaningless now
    MeetingSignal.query.filter_by(meeting_id=meeting.id).delete(synchronize_session=False)
    if role == "client":
        meeting.client_rec = False                      # recording consent is asked again on every join
    else:
        meeting.fc_rec = False
    if meeting.status == "scheduled":
        meeting.status, meeting.started_at = "live", now()
    _touch(role, meeting)
    db.session.commit()
    return jsonify(epoch=meeting.epoch, role=role, status=meeting.status)


@bp.get("/meetings/<int:meeting_id>/state")
@session_required
def state(meeting_id):
    role, meeting = access(meeting_id)
    _touch(role, meeting)
    try:
        after = int(request.args.get("after", 0))
        epoch = int(request.args.get("epoch", -1))
    except ValueError:
        abort(400)
    signals = []
    if epoch == meeting.epoch:
        rows = (MeetingSignal.query.filter(MeetingSignal.meeting_id == meeting.id, MeetingSignal.epoch == meeting.epoch,
                                           MeetingSignal.sender != role, MeetingSignal.id > after)
                .order_by(MeetingSignal.id).all())
        signals = [{"id": r.id, "kind": r.kind, "payload": r.payload} for r in rows]
    other_seen = meeting.fc_seen_at if role == "client" else meeting.client_seen_at
    present = other_seen is not None and (now() - other_seen).total_seconds() < PRESENCE_SECONDS
    recording = bool(meeting.client_rec and meeting.fc_rec and meeting.status == "live")
    if recording and not meeting.recording_started:
        meeting.recording_started = True
    db.session.commit()
    return jsonify(epoch=meeting.epoch, status=meeting.status, other_present=present, client_rec=meeting.client_rec,
                   fc_rec=meeting.fc_rec, recording=recording, ended=meeting.status == "ended", signals=signals,
                   other_name=(meeting.fc.full_name if role == "client" else meeting.client.full_name))


@bp.post("/meetings/<int:meeting_id>/signal")
@session_required
def signal(meeting_id):
    check_csrf()
    role, meeting = access(meeting_id)
    data = request.get_json(silent=True) or {}
    if data.get("epoch") != meeting.epoch or meeting.status == "ended":
        return jsonify(stale=True), 409
    kind, payload = data.get("kind"), data.get("payload")
    if kind not in ("offer", "answer", "candidate") or not isinstance(payload, str) or len(payload) > MAX_SIGNAL_CHARS:
        abort(400)
    db.session.add(MeetingSignal(meeting_id=meeting.id, epoch=meeting.epoch, sender=role, kind=kind, payload=payload))
    db.session.commit()
    return jsonify(ok=True)


@bp.post("/meetings/<int:meeting_id>/consent")
@session_required
def consent(meeting_id):
    """Agree to, or withdraw from, recording. Recording happens only while both have agreed."""
    check_csrf()
    role, meeting = access(meeting_id)
    if meeting.status != "live":
        return jsonify(error="not_live"), 409
    agree = bool((request.get_json(silent=True) or {}).get("agree"))
    if role == "client":
        meeting.client_rec = agree
    else:
        meeting.fc_rec = agree
    if meeting.client_rec and meeting.fc_rec:
        meeting.recording_started = True
    db.session.commit()
    return jsonify(client_rec=meeting.client_rec, fc_rec=meeting.fc_rec,
                   recording=bool(meeting.client_rec and meeting.fc_rec))


@bp.post("/meetings/<int:meeting_id>/recording")
@session_required
def recording(meeting_id):
    """Upload one finished audio segment from the signed-in participant's own browser."""
    request.max_content_length = MAX_AUDIO_BYTES        # before anything reads the request (the app default is small)
    check_csrf()
    role, meeting = access(meeting_id)
    if not meeting.recording_started:
        return jsonify(error="no_consent"), 403         # both people never agreed, so nothing may be stored
    mime = (request.mimetype or "").lower()
    if not (mime.startswith("audio/") or mime == "video/webm"):
        abort(400)
    data = request.get_data()
    if not data:
        abort(400)
    if MeetingRecording.query.filter_by(meeting_id=meeting.id, role=role).count() >= MAX_SEGMENTS:
        abort(400)
    try:
        started = int(request.args.get("started", 0))
    except ValueError:
        started = 0
    db.session.add(MeetingRecording(meeting_id=meeting.id, role=role, audio=data, mime=mime[:60], started_ms=started))
    db.session.commit()
    return jsonify(ok=True, bytes=len(data))


@bp.post("/meetings/<int:meeting_id>/end")
@fc_required
def end(meeting_id):
    check_csrf()
    role, meeting = access(meeting_id)
    if meeting.status != "ended":
        meeting.status, meeting.ended_at = "ended", now()
        meeting.client_rec = meeting.fc_rec = False
        if MeetingNote.query.filter_by(meeting_id=meeting.id).first() is None:
            db.session.add(MeetingNote(meeting_id=meeting.id))
        db.session.commit()
    return jsonify(ok=True)


@bp.post("/meetings/<int:meeting_id>/leave")
@session_required
def leave(meeting_id):
    """The client leaves the call. Their recording consent ends with it. The meeting stays open for the FC."""
    check_csrf()
    role, meeting = access(meeting_id)
    if role == "client":
        meeting.client_rec, meeting.client_seen_at = False, None
    else:
        meeting.fc_rec, meeting.fc_seen_at = False, None
    db.session.commit()
    return jsonify(ok=True)
