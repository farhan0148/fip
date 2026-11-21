"""Week 7 readiness: FC gap analysis (cover need vs existing, possible overlaps, confirm/dismiss, ROA prefill)
and meeting audio retention (delete after transcription, expire failed audio).

Groq is mocked everywhere. No test makes a live call. _test database only."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from conftest import Browser

from app import gap_analysis, meeting_seed, sharing
from app.ai import meeting_ai
from app.extensions import db
from app.models import (
    ComparisonCell, ComparisonFlag, GapFinding, Meeting, MeetingNote, MeetingRecording, Policy, PolicyClause,
    RoaDraft, Scenario, User,
)

WORKED = {"fields": {"age": "30", "support_years": "5", "support_annual": "50,000", "housing_debt": "80,000",
                     "other_debts": "0", "savings": "50,000", "existing_cover": "150,000"}, "plans": [], "events": []}


def login(app, email, password="demo-fc-password-1"):
    b = Browser(app.test_client())
    assert b.post("/login", {"email": email, "password": password}).status_code == 302
    return b


def demo(name):
    return Policy.query.filter_by(is_demo=True, name=name).one()


def save_scenario(b, **fields):
    payload = json.loads(json.dumps(WORKED))
    payload["fields"].update(fields)
    return b.client.post("/simulator/save", json=payload, headers={"X-CSRF-Token": b.token()})


@pytest.fixture()
def world(app, seeded):
    """Wendy, a client who has saved the worked scenario and shares everything with Daniel."""
    client = Browser(app.test_client())
    client.signup(name="Wendy Week", email="wendy@example.com")
    client.onboard()
    wendy = User.query.filter_by(email="wendy@example.com").one()
    daniel = User.query.filter_by(email="fc.daniel@example.com").one()
    jane = User.query.filter_by(email="fc.jane@example.com").one()
    save_scenario(client)
    sharing.confirm_sharing(wendy, daniel, "referral", ["profile_answers", "simulator", "flagged_items"])
    return type("W", (), {"app": app, "client": client, "fc": login(app, "fc.daniel@example.com"),
                          "jane": login(app, "fc.jane@example.com"), "wendy": wendy, "daniel": daniel, "jane_u": jane})


# ---------- cover need vs existing ----------

def test_gap_shows_need_cover_and_shortfall_side_by_side(world):
    w = world
    page = w.fc.get(f"/fc/clients/{w.wendy.id}/gap").get_data(as_text=True)
    assert "Gap analysis" in page
    assert 'id="gap-need">S$280,000<' in page
    assert 'id="gap-cover">S$150,000<' in page
    assert 'id="gap-result">S$130,000<' in page and "Shortfall" in page


def test_surplus_when_cover_exceeds_need(world):
    w = world
    save_scenario(w.client, existing_cover="400,000")
    page = w.fc.get(f"/fc/clients/{w.wendy.id}/gap").get_data(as_text=True)
    assert 'id="gap-result">S$120,000<' in page and "Surplus" in page


def test_zero_entered_cover_is_treated_as_zero_full_shortfall(world):
    w = world
    save_scenario(w.client, existing_cover="0")
    page = w.fc.get(f"/fc/clients/{w.wendy.id}/gap").get_data(as_text=True)
    assert 'id="gap-cover">S$0<' in page and 'id="gap-result">S$280,000<' in page


def test_not_assessed_when_simulator_not_shared(world):
    w = world
    sharing.revoke(w.wendy, "simulator")
    page = w.fc.get(f"/fc/clients/{w.wendy.id}/gap").get_data(as_text=True)
    assert 'id="cover-not-assessed"' in page and "has not shared their simulator" in page


def test_not_assessed_when_nothing_saved(app, seeded):
    client = Browser(app.test_client())
    client.signup(name="Nora New", email="nora@example.com")
    client.onboard()
    nora = User.query.filter_by(email="nora@example.com").one()
    daniel = User.query.filter_by(email="fc.daniel@example.com").one()
    sharing.confirm_sharing(nora, daniel, "referral", ["profile_answers", "simulator"])
    fc = login(app, "fc.daniel@example.com")
    page = fc.get(f"/fc/clients/{nora.id}/gap").get_data(as_text=True)
    assert "has not saved a scenario" in page


# ---------- access / consent ----------

def test_gap_is_404_without_any_grant(world):
    w = world
    assert w.jane.get(f"/fc/clients/{w.wendy.id}/gap").status_code == 404     # Jane has no grant
    assert w.client.get(f"/fc/clients/{w.wendy.id}/gap").status_code in (302, 404)  # a client cannot open it
    assert w.fc.get("/fc/clients/999999/gap").status_code == 404


def test_revoking_all_sharing_blocks_the_gap_page(world):
    w = world
    for cat in ("profile_answers", "simulator", "flagged_items"):
        sharing.revoke(w.wendy, cat)
    assert w.fc.get(f"/fc/clients/{w.wendy.id}/gap").status_code == 404
    assert w.fc.post(f"/fc/clients/{w.wendy.id}/gap/decide",
                     {"finding_key": "cover", "status": "confirmed", "_csrf": w.fc.token()}).status_code == 404


# ---------- possible overlaps (only with supporting wording) ----------

def _flag(client_id, plan_name, row_key):
    db.session.add(ComparisonFlag(user_id=client_id, policy_id=demo(plan_name).id, row_key=row_key))
    db.session.commit()


def test_overlap_shown_only_with_two_cited_policies_for_the_same_event(world):
    w = world
    # One flagged policy on death_cover is not an overlap.
    _flag(w.wendy.id, "PRUActive Term", "death_cover")
    page = w.fc.get(f"/fc/clients/{w.wendy.id}/gap").get_data(as_text=True)
    assert 'id="overlap-none"' in page
    # Add a second policy citing wording for the same event: now it is a possible overlap.
    _flag(w.wendy.id, "PRUCritical Care", "death_cover")
    page = w.fc.get(f"/fc/clients/{w.wendy.id}/gap").get_data(as_text=True)
    assert 'id="overlap-death_cover"' in page
    assert "PRUActive Term" in page and "PRUCritical Care" in page
    assert "/citations/demo/" in page                                    # links to the exact clauses
    assert "not a reason to cancel" in page


def test_overlap_not_assessed_without_flagged_items_grant(world):
    w = world
    _flag(w.wendy.id, "PRUActive Term", "death_cover")
    _flag(w.wendy.id, "PRUCritical Care", "death_cover")
    sharing.revoke(w.wendy, "flagged_items")
    page = w.fc.get(f"/fc/clients/{w.wendy.id}/gap").get_data(as_text=True)
    assert 'id="overlap-not-assessed"' in page


# ---------- confirm / dismiss / notes ----------

def test_confirm_and_dismiss_persist_with_a_note(world):
    w = world
    r = w.fc.post(f"/fc/clients/{w.wendy.id}/gap/decide",
                  {"finding_key": "cover", "status": "confirmed", "note": "Checked with the client."})
    assert r.status_code == 302 and "saved=1" in r.location
    row = GapFinding.query.filter_by(client_id=w.wendy.id, finding_key="cover").one()
    assert row.status == "confirmed" and row.note == "Checked with the client." and row.source_hash
    page = w.fc.get(f"/fc/clients/{w.wendy.id}/gap").get_data(as_text=True)
    assert "Confirmed" in page and "Will prefill the ROA" in page
    w.fc.post(f"/fc/clients/{w.wendy.id}/gap/decide", {"finding_key": "cover", "status": "dismissed", "note": ""})
    assert GapFinding.query.filter_by(client_id=w.wendy.id, finding_key="cover").one().status == "dismissed"


def test_decision_for_an_unknown_finding_is_refused(world):
    w = world
    r = w.fc.post(f"/fc/clients/{w.wendy.id}/gap/decide", {"finding_key": "overlap:hospital", "status": "confirmed"})
    assert r.status_code == 400                                           # no such finding in the current analysis


def test_confirmed_finding_is_flagged_for_review_when_the_source_changes(world):
    w = world
    w.fc.post(f"/fc/clients/{w.wendy.id}/gap/decide", {"finding_key": "cover", "status": "confirmed", "note": "ok"})
    analysis = gap_analysis.build_analysis(w.daniel, w.wendy.id)
    assert analysis["cover_finding"]["confirmed_for_roa"] is True and analysis["cover_finding"]["stale"] is False
    # The client re-saves a different scenario: the confirmed finding must be flagged for review and not reused.
    save_scenario(w.client, existing_cover="100,000")
    analysis = gap_analysis.build_analysis(w.daniel, w.wendy.id)
    assert analysis["cover_finding"]["stale"] is True
    assert analysis["cover_finding"]["confirmed_for_roa"] is False
    assert "Source changed" in w.fc.get(f"/fc/clients/{w.wendy.id}/gap").get_data(as_text=True)


# ---------- only confirmed findings prefill the ROA ----------

def test_only_confirmed_not_stale_cover_prefills_the_roa(world):
    w = world
    meeting = Meeting(client_id=w.wendy.id, fc_id=w.daniel.id, title="Coverage review", topic="coverage_review",
                      status="ended")
    db.session.add(meeting)
    db.session.commit()
    mid = meeting.id

    # Not confirmed yet: the ROA says so and does not state the figure as confirmed.
    w.fc.post(f"/fc/roa/{mid}/generate")
    page = w.fc.get(f"/fc/roa/{mid}").get_data(as_text=True)
    assert "no gap-analysis findings have been confirmed" in page

    # Confirm it, refresh the draft from sources, and the confirmed position appears with its source.
    w.fc.post(f"/fc/clients/{w.wendy.id}/gap/decide", {"finding_key": "cover", "status": "confirmed", "note": ""})
    w.fc.post(f"/fc/roa/{mid}/save", {"action": "refresh"})
    page = w.fc.get(f"/fc/roa/{mid}").get_data(as_text=True)
    assert "Confirmed death-cover position" in page and "S$280,000" in page and "S$150,000" in page
    assert "shortfall of S$130,000" in page


def test_dismissed_finding_does_not_prefill_the_roa(world):
    w = world
    meeting = Meeting(client_id=w.wendy.id, fc_id=w.daniel.id, title="Coverage review", topic="coverage_review",
                      status="ended")
    db.session.add(meeting)
    db.session.commit()
    mid = meeting.id
    w.fc.post(f"/fc/clients/{w.wendy.id}/gap/decide", {"finding_key": "cover", "status": "dismissed", "note": ""})
    w.fc.post(f"/fc/roa/{mid}/generate")
    page = w.fc.get(f"/fc/roa/{mid}").get_data(as_text=True)
    assert "Confirmed death-cover position" not in page
    assert "no gap-analysis findings have been confirmed" in page


# ---------- meeting audio retention ----------

def _ended_meeting_with_audio(w, n=2):
    m = Meeting(client_id=w.wendy.id, fc_id=w.daniel.id, title="Recorded call", topic="general",
                status="ended", recording_started=True)
    db.session.add(m)
    db.session.flush()
    for i in range(n):
        db.session.add(MeetingRecording(meeting_id=m.id, role="client" if i % 2 == 0 else "fc",
                                         audio=b"fake-audio-%d" % i, mime="audio/webm", started_ms=1000 * i))
    db.session.add(MeetingNote(meeting_id=m.id))
    db.session.commit()
    return m.id


GOOD_NOTES = json.dumps({
    "discussion_points": [{"text": "The client worries about cover.", "unclear": False}],
    "next_steps": [{"text": "FC compares two plans by Friday.", "unclear": False}],
    "open_questions": []})


def _fake_groq(monkeypatch, audio_texts, chat_reply):
    from types import SimpleNamespace

    from app.ai import client as ai_client

    monkeypatch.setenv("GROQ_API_KEY", "gsk_TEST_KEY_must_never_leak_0123456789")
    monkeypatch.setenv("GROQ_MODEL", "test-model")
    queue = list(audio_texts)

    class FakeGroq:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))
            self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._audio))

        def _chat(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=chat_reply))])

        def _audio(self, **kwargs):
            item = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(item, Exception):
                raise item
            return SimpleNamespace(text=item, segments=[{"start": 0.0, "end": 5.0, "text": item}])

    monkeypatch.setattr(ai_client, "Groq", FakeGroq)


def test_raw_audio_is_deleted_after_successful_transcription(world, monkeypatch, app):
    w = world
    app.config["MEETING_SYNC"] = True
    mid = _ended_meeting_with_audio(w)
    _fake_groq(monkeypatch, ["First words.", "Second words."], GOOD_NOTES)
    w.fc.post(f"/fc/meetings/{mid}/notes/process")
    recs = MeetingRecording.query.filter_by(meeting_id=mid).all()
    assert all(r.audio is None for r in recs)                 # raw audio removed
    assert all(r.transcript is not None for r in recs)        # transcript kept for a summary retry
    assert all(r.audio_deleted_at is not None for r in recs)
    assert MeetingNote.query.filter_by(meeting_id=mid).one().process_status == "ready"


def test_transcript_is_reused_when_summary_generation_fails(world, monkeypatch, app):
    w = world
    app.config["MEETING_SYNC"] = True
    mid = _ended_meeting_with_audio(w)
    _fake_groq(monkeypatch, ["First words.", "Second words."], "not json")   # summary fails
    w.fc.post(f"/fc/meetings/{mid}/notes/process")
    note = MeetingNote.query.filter_by(meeting_id=mid).one()
    assert note.process_status == "failed" and note.error == "invalid_output"
    recs = MeetingRecording.query.filter_by(meeting_id=mid).all()
    assert all(r.audio is None and r.transcript is not None for r in recs)   # audio gone, transcript kept
    # Retry the summary only: a good reply now, and NO transcription happens again (audio is gone anyway).
    _fake_groq(monkeypatch, [AssertionError("audio must not be re-sent")], GOOD_NOTES)
    w.fc.post(f"/fc/meetings/{mid}/notes/process")
    assert MeetingNote.query.filter_by(meeting_id=mid).one().process_status == "ready"


def test_failed_transcription_keeps_audio_until_expiry_then_removes_it(world, monkeypatch, app):
    import groq
    import httpx

    w = world
    app.config["MEETING_SYNC"] = True
    mid = _ended_meeting_with_audio(w, n=1)
    err = groq.APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))
    _fake_groq(monkeypatch, [err], GOOD_NOTES)
    w.fc.post(f"/fc/meetings/{mid}/notes/process")
    rec = MeetingRecording.query.filter_by(meeting_id=mid).one()
    assert rec.transcript is None and rec.audio is not None       # kept for a Retry
    assert rec.expires_at is not None

    # Not yet due: nothing is removed.
    assert meeting_ai.expire_failed_audio(now=datetime.now(timezone.utc)) == 0
    assert MeetingRecording.query.filter_by(meeting_id=mid).one().audio is not None

    # After the TTL: the raw audio is removed and the removal is recorded.
    later = datetime.now(timezone.utc) + meeting_ai.FAILED_AUDIO_TTL + timedelta(minutes=1)
    assert meeting_ai.expire_failed_audio(now=later) == 1
    rec = MeetingRecording.query.filter_by(meeting_id=mid).one()
    assert rec.audio is None and rec.audio_deleted_at is not None and rec.transcript is None


def test_expire_audio_cli_runs(world, app):
    w = world
    _ended_meeting_with_audio(w)
    result = app.test_cli_runner().invoke(args=["expire-audio"])
    assert result.exit_code == 0 and "Expired raw audio" in result.output
