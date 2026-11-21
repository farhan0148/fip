"""Week 8: real meetings, recording consent, AI draft notes, Saved Notes, Ask AI voice and ROA drafts.

Groq is mocked everywhere. No test makes a live call. _test database only."""

import html
import json
import re
from types import SimpleNamespace

import groq
import httpx
import pytest
from conftest import Browser

from app import meeting_seed, retrieval  # noqa: F401
from app.ai import client as ai_client
from app.ai import meeting_ai
from app.extensions import db
from app.models import (
    AskHistory, Meeting, MeetingNote, MeetingRecording, MeetingSignal, Policy, RoaDraft, Scenario, ShareGrant, User,
)

KEY = "gsk_TEST_KEY_must_never_leak_0123456789"


class FakeGroq:
    """chat: JSON text from `chat_reply(messages)`. audio: queue of transcript texts (or an exception to raise)."""

    instances, chat_reply, audio_queue = [], None, []
    audio_calls, chat_calls, init_kwargs = [], [], []

    def __init__(self, **kwargs):
        FakeGroq.init_kwargs.append(kwargs)
        FakeGroq.instances.append(self)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._audio))

    def _chat(self, **kwargs):
        FakeGroq.chat_calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=FakeGroq.chat_reply(kwargs["messages"])))])

    def _audio(self, **kwargs):
        FakeGroq.audio_calls.append(kwargs)
        item = FakeGroq.audio_queue.pop(0) if len(FakeGroq.audio_queue) > 1 else FakeGroq.audio_queue[0]
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(text=item, segments=[{"start": 0.0, "end": 5.0, "text": item}])

    @classmethod
    def reset(cls):
        cls.instances, cls.chat_reply, cls.audio_queue = [], None, []
        cls.audio_calls, cls.chat_calls, cls.init_kwargs = [], [], []

    @classmethod
    def total(cls):
        return len(cls.audio_calls) + len(cls.chat_calls)


@pytest.fixture(autouse=True)
def no_live_calls(monkeypatch, app):
    def refuse(*a, **k):
        raise AssertionError("a test tried to make a live network request")
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(ai_client, "Groq", refuse)
    for name in ("AI_DEBUG", "APP_ENV", "GROQ_API_KEY", "GROQ_MODEL", "GROQ_TRANSCRIBE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    app.config["MEETING_SYNC"] = True           # run the background job inline so tests are deterministic
    FakeGroq.reset()
    yield
    FakeGroq.reset()


@pytest.fixture()
def fake_groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    monkeypatch.setenv("GROQ_MODEL", "test-model")
    monkeypatch.setattr(ai_client, "Groq", FakeGroq)
    FakeGroq.chat_reply = lambda m: pytest.fail("the chat model must not be called here")
    FakeGroq.audio_queue = [AssertionError("transcription must not be called here")]
    return FakeGroq


def login(app, email, password="demo-fc-password-1"):
    b = Browser(app.test_client())
    assert b.post("/login", {"email": email, "password": password}).status_code == 302
    return b


@pytest.fixture()
def world(app, seeded):
    """Wendy (client, assigned to Daniel with everything shared), Daniel, Jane, and an outsider client."""
    client_b = Browser(app.test_client())
    client_b.signup(name="Wendy Week", email="wendy@example.com")
    client_b.onboard()
    wendy = User.query.filter_by(email="wendy@example.com").one()
    daniel = User.query.filter_by(email="fc.daniel@example.com").one()
    m1, m2, live = meeting_seed.seed_for(wendy, daniel)
    outsider = Browser(app.test_client())
    outsider.signup(name="Olive Outsider", email="olive@example.com")
    outsider.onboard()
    return SimpleNamespace(client=client_b, fc=login(app, "fc.daniel@example.com"), jane=login(app, "fc.jane@example.com"),
                           outsider=outsider, wendy=wendy, daniel=daniel, m1=m1.id, m2=m2.id, live=live.id, app=app)


def csrf(b):
    return {"X-CSRF-Token": b.token()}


def api(b, method, url, **kw):
    kw.setdefault("headers", csrf(b))
    return getattr(b.client, method)(url, **kw)


# ---------- access ----------

def test_only_the_assigned_client_and_fc_can_open_a_meeting(world):
    w = world
    assert w.client.get(f"/meetings/{w.live}").status_code == 200
    assert w.fc.get(f"/fc/meetings/{w.live}").status_code == 200
    for b in (w.outsider, w.jane):
        assert b.get(f"/meetings/{w.live}").status_code in (302, 404)
        assert b.get(f"/fc/meetings/{w.live}").status_code in (302, 404)
        assert api(b, "post", f"/meetings/{w.live}/join").status_code == 404
        assert api(b, "get", f"/meetings/{w.live}/state").status_code == 404
        assert api(b, "post", f"/meetings/{w.live}/signal", json={"epoch": 1, "kind": "offer", "payload": "x"}).status_code == 404
        assert api(b, "post", f"/meetings/{w.live}/recording", data=b"x", content_type="audio/webm").status_code == 404
    assert w.client.get(f"/fc/meetings/{w.live}").status_code in (302, 404)     # a client cannot use the FC page
    assert w.fc.get(f"/meetings/{w.live}").status_code in (302, 404)
    anon = w.app.test_client()
    assert anon.get(f"/meetings/{w.live}").status_code == 302
    assert anon.post(f"/meetings/{w.live}/join").status_code == 302


def test_a_client_who_changed_fc_loses_the_old_meetings(world):
    w = world
    from app import sharing
    from app.models import FCAssignment
    jane = User.query.filter_by(email="fc.jane@example.com").one()
    FCAssignment.query.filter_by(client_id=w.wendy.id).one().fc_id = jane.id
    db.session.commit()
    assert w.client.get(f"/meetings/{w.live}").status_code == 404
    assert w.fc.get(f"/fc/meetings/{w.live}").status_code == 404
    assert "Intro call" not in w.client.get("/meetings").get_data(as_text=True)


def test_an_fc_can_only_schedule_with_their_own_assigned_clients(world):
    w = world
    form = {"client_id": w.wendy.id, "title": "Follow up", "topic": "general"}
    r = w.fc.post("/fc/meetings", form)
    assert r.status_code == 302 and "/fc/meetings/" in r.location
    assert w.jane.post("/fc/meetings", form).status_code == 404            # Jane has no such client
    olive = User.query.filter_by(email="olive@example.com").one()
    assert w.fc.post("/fc/meetings", {**form, "client_id": olive.id}).status_code == 404
    assert w.fc.post("/fc/meetings", {**form, "title": ""}).status_code == 302
    assert w.client.post("/fc/meetings", form).status_code in (302, 404)
    page = w.fc.get("/fc/meetings").get_data(as_text=True)
    assert "Follow up" in page and "Wendy Week" in page


# ---------- the call ----------

def test_join_and_signalling_between_the_two_participants(world):
    w = world
    c = api(w.client, "post", f"/meetings/{w.live}/join").get_json()
    f = api(w.fc, "post", f"/meetings/{w.live}/join").get_json()
    assert f["epoch"] == c["epoch"] + 1 and f["status"] == "live"
    stale = api(w.client, "post", f"/meetings/{w.live}/signal", json={"epoch": c["epoch"], "kind": "offer", "payload": "{}"})
    assert stale.status_code == 409                                         # the FC joined since: old negotiation
    assert api(w.fc, "post", f"/meetings/{w.live}/signal", json={"epoch": f["epoch"], "kind": "offer", "payload": '{"sdp":"x"}'}).status_code == 200
    seen_by_client = api(w.client, "get", f"/meetings/{w.live}/state?after=0&epoch={f['epoch']}").get_json()
    assert [s["kind"] for s in seen_by_client["signals"]] == ["offer"] and seen_by_client["other_present"] is True
    seen_by_fc = api(w.fc, "get", f"/meetings/{w.live}/state?after=0&epoch={f['epoch']}").get_json()
    assert seen_by_fc["signals"] == []                                      # you never receive your own message
    assert api(w.fc, "post", f"/meetings/{w.live}/signal", json={"epoch": f["epoch"], "kind": "bogus", "payload": "x"}).status_code == 400
    again = api(w.client, "post", f"/meetings/{w.live}/join").get_json()   # a rejoin starts fresh
    assert again["epoch"] == f["epoch"] + 1 and MeetingSignal.query.count() == 0


def test_recording_needs_both_to_agree_and_stops_when_anyone_withdraws_or_it_ends(world):
    w = world
    api(w.client, "post", f"/meetings/{w.live}/join")
    api(w.fc, "post", f"/meetings/{w.live}/join")
    state = lambda b: api(b, "get", f"/meetings/{w.live}/state?epoch=99").get_json()
    assert state(w.client)["recording"] is False
    upload = lambda b: api(b, "post", f"/meetings/{w.live}/recording?started=1700000000000", data=b"audio-bytes", content_type="audio/webm;codecs=opus")
    assert upload(w.client).status_code == 403                              # nobody agreed
    api(w.client, "post", f"/meetings/{w.live}/consent", json={"agree": True})
    assert state(w.fc)["recording"] is False and state(w.fc)["client_rec"] is True
    assert upload(w.client).status_code == 403                              # one person is not enough
    api(w.fc, "post", f"/meetings/{w.live}/consent", json={"agree": True})
    assert state(w.client)["recording"] is True and state(w.fc)["recording"] is True
    assert upload(w.client).status_code == 200 and upload(w.fc).status_code == 200
    api(w.client, "post", f"/meetings/{w.live}/consent", json={"agree": False})
    assert state(w.fc)["recording"] is False and state(w.fc)["status"] == "live"        # the call goes on
    api(w.client, "post", f"/meetings/{w.live}/consent", json={"agree": True})
    assert state(w.fc)["recording"] is True
    assert api(w.fc, "post", f"/meetings/{w.live}/end").status_code == 200
    s = state(w.client)
    assert s["ended"] is True and s["recording"] is False and s["client_rec"] is False and s["fc_rec"] is False
    assert upload(w.client).status_code == 200                              # the last consented segment can still arrive
    assert api(w.client, "post", f"/meetings/{w.live}/join").status_code == 409
    assert MeetingRecording.query.count() == 3
    assert api(w.client, "post", f"/meetings/{w.live}/end").status_code in (302, 404)  # only the FC ends a meeting


def test_recording_upload_limits_and_types(world):
    w = world
    for b in (w.client, w.fc):
        api(b, "post", f"/meetings/{w.live}/join")
        api(b, "post", f"/meetings/{w.live}/consent", json={"agree": True})
    url = f"/meetings/{w.live}/recording"
    assert api(w.client, "post", url, data=b"x", content_type="text/plain").status_code == 400
    assert api(w.client, "post", url, data=b"", content_type="audio/webm").status_code == 400
    assert api(w.client, "post", url, data=b"x" * (26 * 1024 * 1024), content_type="audio/webm").status_code == 413
    assert api(w.client, "post", url, data=b"x", content_type="audio/webm", headers={}).status_code == 400   # no CSRF token
    assert MeetingRecording.query.count() == 0
    # a normal segment is far bigger than the app's default 64 KB request limit and must still be accepted
    assert api(w.client, "post", url, data=b"x" * (300 * 1024), content_type="audio/webm").status_code == 200
    assert api(w.client, "post", "/ask/transcribe", data=b"x" * (300 * 1024), content_type="audio/webm").status_code in (200, 503)
    assert MeetingRecording.query.count() == 1


def test_viewing_pages_and_polling_never_call_the_provider(world, fake_groq):
    w = world
    for b, urls in ((w.client, ["/meetings", f"/meetings/{w.live}", "/notes", "/dashboard"]),
                    (w.fc, ["/fc/meetings", f"/fc/meetings/{w.live}", f"/fc/meetings/{w.m1}/notes", f"/fc/meetings/{w.m1}/notes/status",
                            f"/fc/meetings/{w.m1}/notes/preview", "/fc/roa", f"/fc/roa/{w.m1}"])):
        for url in urls:
            assert b.get(url).status_code == 200, url
    api(w.client, "post", f"/meetings/{w.live}/join")
    api(w.client, "get", f"/meetings/{w.live}/state?epoch=1")
    assert fake_groq.total() == 0


# ---------- processing a consented recording into a private draft ----------

def finished_meeting(w, segments=(("client", "Hello, I worry about my child's cover."), ("fc", "We agreed to compare two plans by Friday."))):
    m = Meeting(client_id=w.wendy.id, fc_id=w.daniel.id, title="Recorded call", topic="general", status="ended", recording_started=True)
    db.session.add(m)
    db.session.flush()
    for i, (role, _) in enumerate(segments):
        db.session.add(MeetingRecording(meeting_id=m.id, role=role, audio=b"fake-audio-" + role.encode(), mime="audio/webm", started_ms=1000 * i))
    db.session.add(MeetingNote(meeting_id=m.id))
    db.session.commit()
    return m.id


GOOD_NOTES = json.dumps({
    "discussion_points": [{"text": "The client worries about cover for their child.", "unclear": False},
                          {"text": "Premium of 999 dollars a month was mentioned.", "unclear": False}],
    "next_steps": [{"text": "FC compares two plans by Friday.", "unclear": False}],
    "open_questions": [{"text": "Which hospital ward does the client want?", "unclear": True}]})


def test_processing_transcribes_each_segment_once_and_writes_a_draft_for_review(world, fake_groq):
    w = world
    mid = finished_meeting(w)
    fake_groq.audio_queue = ["Hello, I worry about my child's cover.", "We agreed to compare two plans by Friday."]
    fake_groq.chat_reply = lambda m: GOOD_NOTES
    r = w.fc.post(f"/fc/meetings/{mid}/notes/process")
    assert r.status_code == 302 and len(fake_groq.audio_calls) == 2 and len(fake_groq.chat_calls) == 1
    note = MeetingNote.query.filter_by(meeting_id=mid).one()
    assert note.process_status == "ready" and note.progress == "Draft ready for your review"
    assert [t["speaker"] for t in note.transcript] == ["Wendy Week", "Daniel Koh"]
    assert note.fc_draft["next_steps"] == ["FC compares two plans by Friday."]
    assert note.fc_draft["open_questions"][0].startswith("[unclear] ")               # flagged by the model
    assert any(i.startswith("[unclear] ") and "999" in i for i in note.fc_draft["discussion"])  # a figure nobody said
    assert all(kw["max_retries"] == 0 for kw in fake_groq.init_kwargs)                 # zero automatic retries
    assert fake_groq.audio_calls[0]["file"][0].startswith("client-") and fake_groq.audio_calls[0]["model"]
    sent = json.loads(fake_groq.chat_calls[0]["messages"][1]["content"])
    assert "Hello, I worry" in sent["transcript"] and "wendy@example.com" not in json.dumps(fake_groq.chat_calls[0])
    before = fake_groq.total()
    w.fc.post(f"/fc/meetings/{mid}/notes/process")                                     # already done: no new calls
    w.fc.get(f"/fc/meetings/{mid}/notes")
    assert fake_groq.total() == before
    page = w.fc.get(f"/fc/meetings/{mid}/notes").get_data(as_text=True)
    assert "[unclear]" in page and "Private to you" in page and 'id="create-draft"' not in page


def test_processing_failures_keep_manual_notes_and_retries_reuse_saved_transcripts(world, fake_groq):
    w = world
    mid = finished_meeting(w)
    fake_groq.audio_queue = ["First segment text.", groq.APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))]
    r = w.fc.post(f"/fc/meetings/{mid}/notes/process")
    note = MeetingNote.query.filter_by(meeting_id=mid).one()
    assert note.process_status == "failed" and note.error == "timeout" and len(fake_groq.audio_calls) == 2
    assert len(fake_groq.chat_calls) == 0
    page = w.fc.get(f"/fc/meetings/{mid}/notes").get_data(as_text=True)
    assert 'id="process-failed"' in page and "Try again" in page and 'id="start-manual"' in page
    w.fc.post(f"/fc/meetings/{mid}/notes/manual")                                      # manual notes still work
    w.fc.post(f"/fc/meetings/{mid}/notes/save", {"discussion": "Written by hand", "next_steps": "", "open_questions": ""})
    assert MeetingNote.query.filter_by(meeting_id=mid).one().fc_draft["discussion"] == ["Written by hand"]
    fake_groq.audio_queue = ["Second segment text."]
    fake_groq.chat_reply = lambda m: GOOD_NOTES
    w.fc.post(f"/fc/meetings/{mid}/notes/process")                                     # an explicit retry
    assert len(fake_groq.audio_calls) == 3 and len(fake_groq.chat_calls) == 1          # only the failed segment again
    note = MeetingNote.query.filter_by(meeting_id=mid).one()
    assert note.process_status == "ready" and note.fc_draft["discussion"] == ["Written by hand"]   # your manual notes are kept
    assert note.ai_draft["next_steps"]


def test_a_bad_summary_fails_cleanly_without_retrying(world, fake_groq):
    w = world
    mid = finished_meeting(w)
    fake_groq.audio_queue = ["Some words.", "More words."]
    fake_groq.chat_reply = lambda m: "not json"
    w.fc.post(f"/fc/meetings/{mid}/notes/process")
    note = MeetingNote.query.filter_by(meeting_id=mid).one()
    assert note.process_status == "failed" and note.error == "invalid_output" and len(fake_groq.chat_calls) == 1
    assert all(r.transcript for r in MeetingRecording.query.filter_by(meeting_id=mid))     # transcripts are saved


def test_processing_needs_a_configured_key_a_finished_meeting_and_consented_audio(world, monkeypatch):
    w = world
    mid = finished_meeting(w)
    w.fc.post(f"/fc/meetings/{mid}/notes/process")
    assert MeetingNote.query.filter_by(meeting_id=mid).one().error == "not_configured"
    live_note = w.fc.post(f"/fc/meetings/{w.live}/notes/process")                      # live meeting: refused
    assert "nothing_to_process" in live_note.location
    m = db.session.get(Meeting, mid)
    m.recording_started = False
    db.session.commit()
    assert "nothing_to_process" in w.fc.post(f"/fc/meetings/{mid}/notes/process").location
    assert w.client.post(f"/fc/meetings/{mid}/notes/process").status_code in (302, 404)
    assert w.jane.post(f"/fc/meetings/{mid}/notes/process").status_code == 404


def test_validate_notes_marks_unsupported_details_unclear():
    transcript = "FC: We will meet again on Friday. Client: The premium is 300 a month."
    out = meeting_ai.validate_notes(json.dumps({"discussion_points": [{"text": "Premium is 300 a month.", "unclear": False},
                                                                       {"text": "Cover is 500000.", "unclear": False}],
                                                "next_steps": ["Meet on Friday."], "open_questions": []}), transcript)
    assert out["discussion"] == ["Premium is 300 a month.", "[unclear] Cover is 500000."] and out["next_steps"] == ["Meet on Friday."]
    for bad in ("nope", json.dumps({"discussion_points": [], "next_steps": [], "open_questions": []}),
                json.dumps({"discussion_points": "x"})):
        with pytest.raises(Exception):
            meeting_ai.validate_notes(bad, transcript)


# ---------- the FC's actions and the customer's Saved Notes ----------

def test_the_customer_sees_nothing_until_the_fc_confirms_sending(world):
    w = world
    assert 'id="no-notes"' in w.client.get("/notes").get_data(as_text=True)
    assert w.client.get(f"/notes/{w.m1}").status_code == 404                            # a private draft exists but is not sent
    page = w.client.get("/notes").get_data(as_text=True)
    assert "Plan comparison" not in page and "Compared PRUActive Term" not in page
    w.fc.post(f"/fc/meetings/{w.m1}/notes/save", {"discussion": "Private edit one", "next_steps": "Do the thing", "open_questions": "Anything else?"})
    assert w.client.get("/notes").get_data(as_text=True).count("Private edit") == 0
    preview = w.fc.get(f"/fc/meetings/{w.m1}/notes/preview").get_data(as_text=True)
    assert "Private edit one" in preview and "Confirm and send to customer" in preview and "does not share the transcript" in preview
    assert w.client.get("/notes").get_data(as_text=True).count("Private edit") == 0      # previewing shares nothing
    no_confirm = w.fc.post(f"/fc/meetings/{w.m1}/notes/send")
    assert no_confirm.status_code == 400 and MeetingNote.query.filter_by(meeting_id=w.m1).one().sent_at is None
    assert w.fc.post(f"/fc/meetings/{w.m1}/notes/send", {"confirm": "yes"}).status_code == 302
    index = w.client.get("/notes").get_data(as_text=True)
    assert "Plan comparison: term vs hospital" in index and "Daniel Koh" in index and re.search(r"\d{2} \w{3} \d{4}", index)
    detail = w.client.get(f"/notes/{w.m1}").get_data(as_text=True)
    assert "Private edit one" in detail and "Do the thing" in detail and "Daniel Koh" in detail and "Anything else?" in detail
    assert w.client.get(f"/notes/{w.m2}").status_code == 404                            # the other meeting is still private


def test_later_edits_stay_private_until_the_fc_sends_the_update(world):
    w = world
    w.fc.post(f"/fc/meetings/{w.m1}/notes/save", {"discussion": "Version one", "next_steps": "Step", "open_questions": ""})
    w.fc.post(f"/fc/meetings/{w.m1}/notes/send", {"confirm": "yes"})
    w.fc.post(f"/fc/meetings/{w.m1}/notes/save", {"discussion": "Version two (private)", "next_steps": "Step", "open_questions": ""})
    assert "Version one" in w.client.get(f"/notes/{w.m1}").get_data(as_text=True)
    assert "Version two" not in w.client.get(f"/notes/{w.m1}").get_data(as_text=True)
    fc_page = w.fc.get(f"/fc/meetings/{w.m1}/notes").get_data(as_text=True)
    assert 'id="unsent"' in fc_page and "still sees the version you sent" in fc_page and "Version two (private)" in fc_page
    w.fc.post(f"/fc/meetings/{w.m1}/notes/send", {"confirm": "yes"})
    assert "Version two (private)" in w.client.get(f"/notes/{w.m1}").get_data(as_text=True)
    assert 'id="unsent"' not in w.fc.get(f"/fc/meetings/{w.m1}/notes").get_data(as_text=True)


def test_unclear_items_must_be_resolved_before_sending_and_empty_notes_cannot_be_sent(world):
    w = world
    w.fc.post(f"/fc/meetings/{w.m1}/notes/save", {"discussion": "[unclear] Maybe a price", "next_steps": "", "open_questions": ""})
    r = w.fc.post(f"/fc/meetings/{w.m1}/notes/send", {"confirm": "yes"})
    assert "error=unclear" in r.location and MeetingNote.query.filter_by(meeting_id=w.m1).one().sent_at is None
    assert "Resolve the [unclear] items" in w.fc.get(r.location).get_data(as_text=True)
    assert "Resolve the [unclear] items" in w.fc.get(f"/fc/meetings/{w.m1}/notes/preview").get_data(as_text=True)
    w.fc.post(f"/fc/meetings/{w.m1}/notes/save", {"discussion": "", "next_steps": "", "open_questions": ""})
    assert "error=empty" in w.fc.post(f"/fc/meetings/{w.m1}/notes/send", {"confirm": "yes"}).location


def test_discard_drops_the_private_draft_or_goes_back_to_what_was_sent(world):
    w = world
    assert w.fc.post(f"/fc/meetings/{w.m1}/notes/discard").status_code == 400            # needs confirmation
    w.fc.post(f"/fc/meetings/{w.m1}/notes/discard", {"confirm": "yes"})
    note = MeetingNote.query.filter_by(meeting_id=w.m1).one()
    assert note.fc_draft is None and note.discarded_at is not None and note.ai_draft
    page = w.fc.get(f"/fc/meetings/{w.m1}/notes").get_data(as_text=True)
    assert "You discarded the draft" in page and 'id="restore-ai"' in page and 'id="start-manual"' in page
    w.fc.post(f"/fc/meetings/{w.m1}/notes/manual", {"source": "ai"})
    assert MeetingNote.query.filter_by(meeting_id=w.m1).one().fc_draft == note.ai_draft
    w.fc.post(f"/fc/meetings/{w.m1}/notes/send", {"confirm": "yes"})
    w.fc.post(f"/fc/meetings/{w.m1}/notes/save", {"discussion": "Changed later", "next_steps": "x", "open_questions": ""})
    w.fc.post(f"/fc/meetings/{w.m1}/notes/discard", {"confirm": "yes"})
    after = MeetingNote.query.filter_by(meeting_id=w.m1).one()
    assert after.fc_draft == after.sent_json and "Changed later" not in w.client.get(f"/notes/{w.m1}").get_data(as_text=True)


def test_saved_notes_never_show_audio_transcripts_or_private_drafts(world, fake_groq):
    w = world
    mid = finished_meeting(w)
    fake_groq.audio_queue = ["SECRET transcript words about the budget.", "More SECRET words."]
    fake_groq.chat_reply = lambda m: GOOD_NOTES.replace("999", "300")
    w.fc.post(f"/fc/meetings/{mid}/notes/process")
    w.fc.post(f"/fc/meetings/{mid}/notes/save", {"discussion": "A clean summary line", "next_steps": "Send plans", "open_questions": ""})
    w.fc.post(f"/fc/meetings/{mid}/notes/send", {"confirm": "yes"})
    pages = [w.client.get(u).get_data(as_text=True) for u in ("/notes", f"/notes/{mid}", "/meetings", "/dashboard", "/ask")]
    blob = " ".join(pages)
    assert "A clean summary line" in blob
    for secret in ("SECRET", "fake-audio", "transcript", "[unclear]", "The client worries about cover"):
        assert secret.lower() not in blob.lower() or secret == "transcript" and "transcript" not in pages[1].lower(), secret
    assert w.outsider.get(f"/notes/{mid}").status_code == 404 and w.jane.get(f"/notes/{mid}").status_code in (302, 404)


# ---------- Ask AI by voice ----------

def ask_reply(messages):
    sent = json.loads(messages[1]["content"])
    clause = next(c for c in sent["clauses"] if "critical" in c["text"].lower())
    return json.dumps({"sufficient": True, "statements": [{"text": "Critical illness cover is an optional rider that costs extra.", "clause_ids": [clause["id"]]}]})


def test_recorded_question_becomes_an_editable_transcript_and_is_not_sent_until_asked(world, fake_groq):
    w = world
    fake_groq.audio_queue = ["Does this plan pay for critical illness?"]
    r = api(w.client, "post", "/ask/transcribe?lang=en", data=b"fake-audio", content_type="audio/webm;codecs=opus")
    assert r.status_code == 200 and r.get_json() == {"text": "Does this plan pay for critical illness?"}
    assert len(fake_groq.audio_calls) == 1 and fake_groq.audio_calls[0]["language"] == "en" and len(fake_groq.chat_calls) == 0
    assert fake_groq.init_kwargs[0]["max_retries"] == 0
    assert AskHistory.query.count() == 0 and MeetingRecording.query.count() == 0       # the audio is not kept anywhere
    fake_groq.chat_reply = ask_reply
    term = Policy.query.filter_by(name="PRUActive Term", is_demo=True).one()
    page = w.client.post("/ask", {"plan": term.id, "lang": "en", "action": "ask", "question": "Does this plan pay for critical illness?"}).get_data(as_text=True)
    assert len(fake_groq.chat_calls) == 1
    assert re.search(r'href="/citations/demo/\d+\?plan=%d" aria-label="Open source 1"' % term.id, page)
    assert 'id="speak-row"' in page and 'id="speak-play"' in page and 'id="speak-replay"' in page and 'id="speak-stop"' in page
    spoken = json.loads(re.search(r'id="speak-text">(.*?)</script>', page, re.S).group(1))
    assert spoken == "Critical illness cover is an optional rider that costs extra."
    assert 'data-lang="en"' in page and "mic-start" in page and "transcript-note" in page
    again = w.client.get("/ask").get_data(as_text=True)
    assert 'id="speak-row"' not in again and len(fake_groq.chat_calls) == 1             # reopening makes no call


def test_transcription_endpoint_limits_and_failures(world, fake_groq):
    w = world
    url = "/ask/transcribe"
    assert api(w.client, "post", url, data=b"x", content_type="text/plain").status_code == 400
    assert api(w.client, "post", url, data=b"", content_type="audio/webm").status_code == 400
    assert api(w.client, "post", url, data=b"x" * (6 * 1024 * 1024), content_type="audio/webm").status_code == 413
    assert api(w.client, "post", url, data=b"x", content_type="audio/webm", headers={}).status_code == 400
    assert w.fc.client.post(url, data=b"x", content_type="audio/webm", headers=csrf(w.fc)).status_code in (302, 404)
    response = httpx.Response(429, request=httpx.Request("POST", "https://example.invalid"))
    fake_groq.audio_queue = [groq.APIStatusError("quota", response=response, body=None)]
    r = api(w.client, "post", url, data=b"x", content_type="audio/webm")
    assert r.status_code == 503 and r.get_json() == {"error": "busy"} and len(fake_groq.audio_calls) == 1
    fake_groq.audio_queue = [groq.APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))]
    assert api(w.client, "post", url, data=b"x", content_type="audio/webm").status_code == 504
    from app.ai.limiter import SlidingWindowLimiter
    w.app.extensions["ai_summary_limiter"] = SlidingWindowLimiter(1, 60)
    fake_groq.audio_queue = ["hello there"]
    assert api(w.client, "post", url, data=b"x", content_type="audio/webm").status_code == 200
    assert api(w.client, "post", url, data=b"x", content_type="audio/webm").status_code == 429


def test_transcription_without_a_key_says_so_and_typing_still_works(world):
    w = world
    r = api(w.client, "post", "/ask/transcribe", data=b"x", content_type="audio/webm")
    assert r.status_code == 503 and r.get_json() == {"error": "not_configured"}
    assert 'id="ask-question"' in w.client.get("/ask").get_data(as_text=True)


def test_ask_conversations_stay_separate_from_meeting_notes(world, fake_groq):
    w = world
    fake_groq.chat_reply = ask_reply
    term = Policy.query.filter_by(name="PRUActive Term", is_demo=True).one()
    w.client.post("/ask", {"plan": term.id, "lang": "en", "action": "ask", "question": "Does this plan pay for critical illness?"})
    assert AskHistory.query.count() == 1
    assert "critical illness" not in w.client.get("/notes").get_data(as_text=True).lower()
    note_pages = w.fc.get(f"/fc/meetings/{w.m1}/notes").get_data(as_text=True) + w.fc.get(f"/fc/roa/{w.m1}").get_data(as_text=True)
    assert "Does this plan pay for critical illness" not in note_pages


# ---------- ROA drafts ----------

def generate(w, meeting_id):
    return w.fc.post(f"/fc/roa/{meeting_id}/generate")


def field(page, name):
    return re.search(r'<textarea name="%s"[^>]*>(.*?)</textarea>' % name, page, re.S).group(1)


def test_two_meetings_produce_different_roa_drafts(world):
    w = world
    generate(w, w.m1)
    generate(w, w.m2)
    a = html.unescape(w.fc.get(f"/fc/roa/{w.m1}").get_data(as_text=True))
    b = html.unescape(w.fc.get(f"/fc/roa/{w.m2}").get_data(as_text=True))
    assert "Options discussed" in a and "PRUActive Term" in a and "PRUShield Premier" in a and "Estimates and assumptions" not in a
    assert "Estimates and assumptions" in b and "Death cover score: 54 / 100" in b and "Options discussed" not in b
    assert "S$280,000" in b and "Assumptions:" in b and "Death cover only" in b
    assert "Compared PRUActive Term and PRUShield Premier" in a and "Reviewed the saved death cover estimate" in b   # from each meeting's notes
    assert "No decision has been recorded" in a and "No decision has been recorded" in b
    assert a != b and "Wendy" in a
    assert "To confirm" in a and "To confirm: the FC's recommendation" in a
    assert RoaDraft.query.count() == 2


def test_the_fc_supplies_the_recommendation_and_it_adapts_the_draft(world):
    w = world
    generate(w, w.m1)
    w.fc.post(f"/fc/roa/{w.m1}/save", {"recommendation": "Take the term plan for income cover.", "action": "save"})
    page = w.fc.get(f"/fc/roa/{w.m1}").get_data(as_text=True)
    assert "No decision has been recorded" in page                                   # typed but not approved: not used
    w.fc.post(f"/fc/roa/{w.m1}/save", {"recommendation": "Take the term plan for income cover.", "recommendation_approved": "on", "action": "save"})
    page = html.unescape(w.fc.get(f"/fc/roa/{w.m1}").get_data(as_text=True))
    assert "FC's recorded reasoning and recommendation" in page and "Take the term plan for income cover." in page
    assert "No decision has been recorded" not in page
    draft = RoaDraft.query.filter_by(meeting_id=w.m1).one()
    assert draft.recommendation_approved is True and draft.status == "draft"


def test_roa_edits_survive_reload_and_refresh_while_unedited_sections_update(world):
    w = world
    generate(w, w.m1)
    page = w.fc.get(f"/fc/roa/{w.m1}").get_data(as_text=True)
    form = {"action": "save"}
    for sid in ("meeting", "profile", "options", "notes", "decision", "ack"):
        form[f"title_{sid}"] = re.search(r'name="title_%s" value="([^"]*)"' % sid, page).group(1)
        form[f"body_{sid}"] = field(page, f"body_{sid}").replace("&#39;", "'").replace("&amp;", "&")
    form["body_profile"] = "Edited profile by the FC."
    w.fc.post(f"/fc/roa/{w.m1}/save", form)
    assert "Edited profile by the FC." in w.fc.get(f"/fc/roa/{w.m1}").get_data(as_text=True)
    # the source changes: the FC's notes get a new line
    note = MeetingNote.query.filter_by(meeting_id=w.m1).one()
    note.fc_draft = {**note.fc_draft, "next_steps": note.fc_draft["next_steps"] + ["A brand new next step"]}
    db.session.commit()
    w.fc.post(f"/fc/roa/{w.m1}/save", {"action": "refresh"})
    page = w.fc.get(f"/fc/roa/{w.m1}").get_data(as_text=True)
    assert "Edited profile by the FC." in page                                      # the edit survived the refresh
    assert "A brand new next step" in field(page, "body_notes")                    # an unedited section took the update
    assert RoaDraft.query.one().status == "draft"


def test_roa_sections_can_be_added_removed_and_stay_removed_after_a_refresh(world):
    w = world
    generate(w, w.m2)
    w.fc.post(f"/fc/roa/{w.m2}/save", {"action": "add"})
    page = w.fc.get(f"/fc/roa/{w.m2}").get_data(as_text=True)
    assert 'id="section-m-1"' in page and "Added by you" in page
    w.fc.post(f"/fc/roa/{w.m2}/save", {"action": "save", "title_m-1": "My own section", "body_m-1": "Client prefers evening calls."})
    w.fc.post(f"/fc/roa/{w.m2}/save", {"action": "remove:ack"})
    w.fc.post(f"/fc/roa/{w.m2}/save", {"action": "refresh"})
    page = w.fc.get(f"/fc/roa/{w.m2}").get_data(as_text=True)
    assert "Client prefers evening calls." in page and "My own section" in page
    assert 'id="section-ack"' not in page and 'id="section-estimates"' in page
    w.fc.post(f"/fc/roa/{w.m2}/save", {"action": "remove:m-1"})
    assert "My own section" not in w.fc.get(f"/fc/roa/{w.m2}").get_data(as_text=True)


def test_roa_review_and_edit_after_review(world):
    w = world
    generate(w, w.m1)
    w.fc.post(f"/fc/roa/{w.m1}/save", {"action": "review"})
    assert 'id="roa-status">Reviewed<' in w.fc.get(f"/fc/roa/{w.m1}").get_data(as_text=True)
    w.fc.post(f"/fc/roa/{w.m1}/save", {"action": "add"})
    assert 'id="roa-status">Draft<' in w.fc.get(f"/fc/roa/{w.m1}").get_data(as_text=True)


def test_roa_uses_only_what_the_client_shared_and_marks_the_rest_to_confirm(world):
    w = world
    from app import sharing
    sharing.revoke(w.wendy, "simulator")
    sharing.revoke(w.wendy, "profile_answers")
    generate(w, w.m2)
    page = w.fc.get(f"/fc/roa/{w.m2}").get_data(as_text=True)
    assert "To confirm: the client has not shared a simulator scenario with you." in page
    assert "To confirm: the client has not shared their profile with you." in page
    assert "S$280,000" not in page and "Death cover score" not in page
    assert "Wendy Week" not in page and "client (name not shared)" in page
    generate(w, w.m1)
    assert "Wendy Week" not in w.fc.get(f"/fc/roa/{w.m1}").get_data(as_text=True)


def test_roa_key_statements_link_to_their_sources(world):
    w = world
    generate(w, w.m1)
    page = w.fc.get(f"/fc/roa/{w.m1}").get_data(as_text=True)
    clauses = re.findall(r'href="(/fc/roa/%d/clause/(\d+))"' % w.m1, page)
    assert len(clauses) >= 4
    for href, clause_id in clauses:
        opened = w.fc.get(href)
        assert opened.status_code == 200 and 'id="cited-line"' in opened.get_data(as_text=True)
    assert f'href="/fc/meetings/{w.m1}/notes"' in page and f'href="/fc/clients/{w.wendy.id}"' in page
    other = Policy.query.filter_by(name="PRURetire Income", is_demo=True).one()
    from app.models import PolicyClause
    stray = PolicyClause.query.filter_by(policy_id=other.id).first()
    assert w.fc.get(f"/fc/roa/{w.m1}/clause/{stray.id}").status_code == 404           # not a plan discussed here
    generate(w, w.m2)
    assert 'href="/fc/clients/' in w.fc.get(f"/fc/roa/{w.m2}").get_data(as_text=True)


def test_roa_is_private_to_the_authorised_fc_and_separate_from_sending_notes(world):
    w = world
    generate(w, w.m1)
    w.fc.post(f"/fc/meetings/{w.m1}/notes/save", {"discussion": "Only this", "next_steps": "x", "open_questions": ""})
    w.fc.post(f"/fc/meetings/{w.m1}/notes/send", {"confirm": "yes"})
    for url in (f"/fc/roa/{w.m1}", "/fc/roa"):
        assert w.client.get(url).status_code in (302, 404)
        assert w.outsider.get(url).status_code in (302, 404)
    assert w.jane.get(f"/fc/roa/{w.m1}").status_code == 404
    assert w.jane.post(f"/fc/roa/{w.m1}/generate").status_code == 404
    assert w.jane.post(f"/fc/roa/{w.m1}/save", {"action": "save"}).status_code == 404
    seen = " ".join(w.client.get(u).get_data(as_text=True) for u in ("/notes", f"/notes/{w.m1}", "/meetings", "/dashboard"))
    assert "Options discussed" not in seen and "Record of Advice" not in seen and "To confirm" not in seen
    assert RoaDraft.query.filter_by(meeting_id=w.m1).one().fc_id == w.daniel.id
    assert w.fc.post(f"/fc/roa/{w.m1}/generate").status_code == 302 and RoaDraft.query.count() == 1


def test_roa_for_a_general_meeting_lists_topics_and_open_items(world):
    w = world
    generate(w, w.live)
    page = w.fc.get(f"/fc/roa/{w.live}").get_data(as_text=True)
    assert "Topics discussed" in page and "To confirm: no discussion points have been saved." in page
    assert "To confirm: no meeting notes have been saved" in page and "Options discussed" not in page
    assert "Estimates and assumptions" not in page
