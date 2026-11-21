"""Meeting notes: the FC's private draft and the customer's Saved Notes.

* The FC's working copy (`fc_draft`) is always private. "Send to customer" copies it into `sent_json` after a
  confirmation. Later edits stay private until the FC sends again. Discarding drops the private draft.
* The customer sees only `sent_json` of meetings that are theirs: the meeting date, the FC's name and the notes.
  Audio, transcripts and private drafts are never rendered for a customer.
* Opening these pages never calls an AI provider. Only "Create AI draft" does, once per recording.
"""

from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, g, jsonify, redirect, render_template, request, url_for

from . import sharing
from .ai import meeting_ai
from .extensions import db
from .meetings import TOPIC_LABELS, access
from .models import Meeting, MeetingNote, MeetingRecording
from .security import check_csrf, fc_required, onboarding_complete_required

bp = Blueprint("meeting_notes", __name__, url_prefix="/fc/meetings")
customer_bp = Blueprint("notes", __name__, url_prefix="/notes")

SECTIONS = (("discussion", "Discussion points"), ("next_steps", "Agreed next steps"), ("open_questions", "Open questions"))
MAX_ITEMS = 30
MAX_ITEM_CHARS = 400
ERRORS = {
    "unclear": "Resolve the [unclear] items first: check them against what was said, then delete the marker.",
    "empty": "Add at least one note before sending.",
    "nothing_to_process": "There is no recording to process.",
}


def empty_draft():
    return {key: [] for key, _ in SECTIONS}


def _note(meeting):
    note = MeetingNote.query.filter_by(meeting_id=meeting.id).first()
    if note is None:
        note = MeetingNote(meeting_id=meeting.id)
        db.session.add(note)
        db.session.commit()
    return note


def _fc_meeting(meeting_id):
    role, meeting = access(meeting_id)
    if role != "fc":
        abort(404)
    return meeting, _note(meeting)


def parse_draft(form):
    """Lines of each textarea become the items of that section."""
    draft = {}
    for key, _ in SECTIONS:
        lines = [" ".join(line.split()) for line in form.get(key, "").splitlines()]
        draft[key] = [line[:MAX_ITEM_CHARS] for line in lines if line][:MAX_ITEMS]
    return draft


def as_text(items):
    return "\n".join(items)


def _unsent_changes(note):
    return bool(note.sent_at and note.fc_draft is not None and note.fc_draft != note.sent_json)


@bp.get("/<int:meeting_id>/notes")
@fc_required
def notes(meeting_id):
    meeting, note = _fc_meeting(meeting_id)
    recordings = MeetingRecording.query.filter_by(meeting_id=meeting.id).order_by(MeetingRecording.id).all()
    return render_template(
        "meetings/notes.html", meeting=meeting, note=note, sections=SECTIONS, draft=note.fc_draft,
        recordings={"client": sum(1 for r in recordings if r.role == "client"),
                    "fc": sum(1 for r in recordings if r.role == "fc")},
        unsent=_unsent_changes(note), error=ERRORS.get(request.args.get("error")), as_text=as_text,
        saved=request.args.get("saved") == "1", sent=request.args.get("sent") == "1",
        topics=TOPIC_LABELS, page_title="Meeting notes")


@bp.post("/<int:meeting_id>/notes/process")
@fc_required
def process(meeting_id):
    """Create the AI draft from the finished, consented recording. The only place a provider is called."""
    check_csrf()
    meeting, note = _fc_meeting(meeting_id)
    if meeting.status != "ended" or not meeting.recording_started or \
            MeetingRecording.query.filter_by(meeting_id=meeting.id).count() == 0:
        return redirect(url_for("meeting_notes.notes", meeting_id=meeting.id, error="nothing_to_process"))
    meeting_ai.start(current_app._get_current_object(), meeting.id)
    return redirect(url_for("meeting_notes.notes", meeting_id=meeting.id))


@bp.get("/<int:meeting_id>/notes/status")
@fc_required
def status(meeting_id):
    meeting, note = _fc_meeting(meeting_id)
    return jsonify(status=note.process_status, progress=note.progress, error=note.error)


@bp.post("/<int:meeting_id>/notes/manual")
@fc_required
def manual(meeting_id):
    """Start the notes by hand, or restore the AI draft after discarding."""
    check_csrf()
    meeting, note = _fc_meeting(meeting_id)
    restore = request.form.get("source") == "ai" and note.ai_draft
    note.fc_draft = {k: list(v) for k, v in note.ai_draft.items()} if restore else empty_draft()
    note.discarded_at = None
    db.session.commit()
    return redirect(url_for("meeting_notes.notes", meeting_id=meeting.id))


@bp.post("/<int:meeting_id>/notes/save")
@fc_required
def save(meeting_id):
    check_csrf()
    meeting, note = _fc_meeting(meeting_id)
    note.fc_draft = parse_draft(request.form)         # private: the customer's copy does not change
    db.session.commit()
    return redirect(url_for("meeting_notes.notes", meeting_id=meeting.id, saved=1))


def _problem(draft):
    items = [item for key, _ in SECTIONS for item in draft.get(key, [])]
    if not items:
        return "empty"
    if any(item.lower().startswith("[unclear]") for item in items):
        return "unclear"
    return None


@bp.get("/<int:meeting_id>/notes/preview")
@fc_required
def preview(meeting_id):
    meeting, note = _fc_meeting(meeting_id)
    if note.fc_draft is None:
        return redirect(url_for("meeting_notes.notes", meeting_id=meeting.id))
    problem = _problem(note.fc_draft)
    return render_template("meetings/notes_preview.html", meeting=meeting, note=note, draft=note.fc_draft,
                           sections=SECTIONS, problem=ERRORS.get(problem), resend=note.sent_at is not None,
                           page_title="Preview notes")


@bp.post("/<int:meeting_id>/notes/send")
@fc_required
def send(meeting_id):
    check_csrf()
    meeting, note = _fc_meeting(meeting_id)
    if request.form.get("confirm") != "yes":
        abort(400, description="Confirm that you want to send these notes to the customer.")
    if note.fc_draft is None:
        abort(400)
    problem = _problem(note.fc_draft)
    if problem:
        return redirect(url_for("meeting_notes.notes", meeting_id=meeting.id, error=problem))
    note.sent_json = {k: list(v) for k, v in note.fc_draft.items()}
    note.sent_at = datetime.now(timezone.utc)
    db.session.commit()
    return redirect(url_for("meeting_notes.notes", meeting_id=meeting.id, sent=1))


@bp.post("/<int:meeting_id>/notes/discard")
@fc_required
def discard(meeting_id):
    check_csrf()
    meeting, note = _fc_meeting(meeting_id)
    if request.form.get("confirm") != "yes":
        abort(400, description="Confirm that you want to discard this draft.")
    if note.sent_json is not None:
        note.fc_draft = {k: list(v) for k, v in note.sent_json.items()}   # back to what the customer already has
    else:
        note.fc_draft = None
    note.discarded_at = datetime.now(timezone.utc)
    db.session.commit()
    return redirect(url_for("meeting_notes.notes", meeting_id=meeting.id))


# ---------- the customer's Saved Notes ----------

def _sent_for(client, meeting_id):
    meeting = db.session.get(Meeting, meeting_id)
    note = MeetingNote.query.filter_by(meeting_id=meeting_id).first() if meeting else None
    assignment = sharing.assignment_for(client.id)
    if (meeting is None or meeting.client_id != client.id or note is None or note.sent_json is None
            or assignment is None or assignment.fc_id != meeting.fc_id):
        abort(404)
    return meeting, note


@customer_bp.get("")
@onboarding_complete_required
def index():
    rows = (db.session.query(Meeting, MeetingNote).join(MeetingNote, MeetingNote.meeting_id == Meeting.id)
            .filter(Meeting.client_id == g.user.id, MeetingNote.sent_at.isnot(None))
            .order_by(Meeting.scheduled_at.desc()).all())
    assignment = sharing.assignment_for(g.user.id)
    rows = [(m, n) for m, n in rows if assignment is not None and assignment.fc_id == m.fc_id]
    return render_template("notes/index.html", rows=rows, page_title="Saved Notes")


@customer_bp.get("/<int:meeting_id>")
@onboarding_complete_required
def detail(meeting_id):
    meeting, note = _sent_for(g.user, meeting_id)
    return render_template("notes/detail.html", meeting=meeting, note=note, sections=SECTIONS, data=note.sent_json,
                           page_title="Saved notes")
