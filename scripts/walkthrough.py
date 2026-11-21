"""Focused connected-demo walkthrough for the Week 7 readiness pass.

Runs against the dedicated TEST database with Groq mocked, so it makes ZERO live provider calls. It mirrors the
manual browser demo: client saves the estimate and shares it, the FC confirms the gap, it reaches the ROA with its
source, a dismissed finding does not, the customer sees notes only after the FC sends, notes are generated with a
mocked Groq, and ROA edits survive a reload.

Usage (PowerShell):
    $env:TEST_DATABASE_URL = "postgresql+psycopg://postgres@127.0.0.1:5433/insurance_navigator_test"
    .venv\Scripts\python scripts\walkthrough.py
"""

import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, meeting_seed, sharing            # noqa: E402
from app.ai import client as ai_client                       # noqa: E402
from app.extensions import db                                # noqa: E402
from app.models import GapFinding, Meeting, MeetingNote, MeetingRecording, Policy, User  # noqa: E402

PASS, FAIL = [], []


def check(label, condition):
    (PASS if condition else FAIL).append(label)
    print(("  PASS  " if condition else "  FAIL  ") + label)


class FakeGroq:
    """Deterministic stand-in for Groq. No network."""

    audio_queue = ["The client worries about cover for the child.", "We agreed to compare two plans by Friday."]
    notes = json.dumps({"discussion_points": [{"text": "The client worries about cover for the child.", "unclear": False}],
                        "next_steps": [{"text": "FC compares two plans by Friday.", "unclear": False}],
                        "open_questions": []})

    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._audio))

    def _chat(self, **kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=FakeGroq.notes))])

    def _audio(self, **kwargs):
        q = FakeGroq.audio_queue
        item = q.pop(0) if len(q) > 1 else q[0]
        return SimpleNamespace(text=item, segments=[{"start": 0.0, "end": 5.0, "text": item}])


class B:
    def __init__(self, app):
        self.c = app.test_client()

    def token(self):
        with self.c.session_transaction() as s:
            return s.setdefault("_csrf", "walkthrough-csrf")

    def post(self, url, data=None, **kw):
        data = dict(data or {})
        data.setdefault("_csrf", self.token())
        return self.c.post(url, data=data, **kw)

    def postjson(self, url, payload):
        return self.c.post(url, json=payload, headers={"X-CSRF-Token": self.token()})

    def get(self, url):
        return self.c.get(url)


def main():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.rstrip("/").endswith("_test"):
        print("Refusing to run: set TEST_DATABASE_URL to a *_test database."); sys.exit(4)
    app = create_app({"TESTING": True, "SECRET_KEY": "walkthrough", "SQLALCHEMY_DATABASE_URI": url,
                      "MEETING_SYNC": True})
    ai_client.Groq = FakeGroq
    os.environ["GROQ_API_KEY"] = "gsk_TEST_ONLY_no_live_call_000000000000"
    os.environ["GROQ_MODEL"] = "test-model"

    with app.app_context():
        assert db.engine.url.database.endswith("_test")
        db.drop_all(); db.create_all()
        app.test_cli_runner().invoke(args=["seed-demo"])

        # --- client: save S$280,000 need (via worked inputs) and S$150,000 cover, then share ---
        client = B(app)
        client.post("/signup", {"full_name": "Wendy Walkthrough", "email": "wendy@example.com", "password": "correct-horse-9"})
        for n, k in {1: "30", 2: "full_time", 3: "spouse_child", 4: "100_300", 5: "family_income",
                     6: "at_60", 7: "private", 8: "personal_employer"}.items():
            client.post(f"/onboarding/question/{n}", {"answer": k, "other_text": "", "action": "next"})
        client.post("/onboarding/review", {})
        worked = {"fields": {"age": "30", "support_years": "5", "support_annual": "50,000", "housing_debt": "80,000",
                             "other_debts": "0", "savings": "50,000", "existing_cover": "150,000"}, "plans": [], "events": []}
        saved = client.postjson("/simulator/save", worked).get_json()
        check("Client scenario: estimated need is S$280,000", saved["result"]["need_today"] == 280000)
        check("Client scenario: gap_today is S$130,000 shortfall", saved["result"]["gap_today"] == 130000)
        dash = client.get("/dashboard").get_data(as_text=True)
        check("Dashboard shows the S$130,000 estimated gap", "Estimated gap S$130,000" in dash)

        wendy = User.query.filter_by(email="wendy@example.com").one()
        daniel = User.query.filter_by(email="fc.daniel@example.com").one()
        sharing.confirm_sharing(wendy, daniel, "referral", ["profile_answers", "simulator", "flagged_items"])
        check("Client consented to share the simulator result",
              "simulator" in sharing.visible_categories(daniel, wendy.id))

        # --- FC: open gap analysis, confirm the S$130,000 finding ---
        fc = B(app)
        fc.post("/login", {"email": "fc.daniel@example.com", "password": "demo-fc-password-1"})
        gap = fc.get(f"/fc/clients/{wendy.id}/gap").get_data(as_text=True)
        check("FC gap analysis shows need S$280,000, cover S$150,000, shortfall S$130,000",
              'id="gap-need">S$280,000<' in gap and 'id="gap-cover">S$150,000<' in gap and 'id="gap-result">S$130,000<' in gap)
        fc.post(f"/fc/clients/{wendy.id}/gap/decide", {"finding_key": "cover", "status": "confirmed", "note": "Confirmed with client."})
        check("FC confirmed the cover finding",
              GapFinding.query.filter_by(client_id=wendy.id, finding_key="cover").one().status == "confirmed")

        # --- the confirmed finding appears in the ROA with its source ---
        meeting = Meeting(client_id=wendy.id, fc_id=daniel.id, title="Coverage review", topic="coverage_review", status="ended")
        db.session.add(meeting); db.session.commit()
        mid = meeting.id
        fc.post(f"/fc/roa/{mid}/generate")
        roa = fc.get(f"/fc/roa/{mid}").get_data(as_text=True)
        check("ROA includes the confirmed shortfall of S$130,000",
              "Confirmed death-cover position" in roa and "shortfall of S$130,000" in roa)
        check("ROA cites the gap-analysis source", "Gap analysis (confirmed findings)" in roa)

        # --- a dismissed finding does not prefill the draft ---
        fc.post(f"/fc/clients/{wendy.id}/gap/decide", {"finding_key": "cover", "status": "dismissed", "note": ""})
        fc.post(f"/fc/roa/{mid}/save", {"action": "refresh"})
        roa = fc.get(f"/fc/roa/{mid}").get_data(as_text=True)
        check("Dismissed finding no longer prefills the ROA",
              "Confirmed death-cover position" not in roa and "no gap-analysis findings have been confirmed" in roa)
        # Re-confirm for the rest of the demo.
        fc.post(f"/fc/clients/{wendy.id}/gap/decide", {"finding_key": "cover", "status": "confirmed", "note": ""})

        # --- meeting notes: generated with mocked Groq, private until sent ---
        rec_meeting = Meeting(client_id=wendy.id, fc_id=daniel.id, title="Recorded call", topic="general",
                              status="ended", recording_started=True)
        db.session.add(rec_meeting); db.session.flush()
        for i in range(2):
            db.session.add(MeetingRecording(meeting_id=rec_meeting.id, role="client" if i == 0 else "fc",
                                             audio=b"fake-audio-%d" % i, mime="audio/webm", started_ms=1000 * i))
        db.session.add(MeetingNote(meeting_id=rec_meeting.id)); db.session.commit()
        rmid = rec_meeting.id
        fc.post(f"/fc/meetings/{rmid}/notes/process")
        note = MeetingNote.query.filter_by(meeting_id=rmid).one()
        check("Notes generated from mocked Groq (status ready)", note.process_status == "ready")
        check("Raw audio deleted after transcription",
              all(r.audio is None and r.transcript is not None for r in MeetingRecording.query.filter_by(meeting_id=rmid)))

        before = client.get("/notes").get_data(as_text=True)
        check("Customer sees nothing in Saved Notes before sending",
              'id="no-notes"' in before and client.get(f"/notes/{rmid}").status_code == 404)
        fc.post(f"/fc/meetings/{rmid}/notes/save", {"discussion": "Reviewed summary for the client.", "next_steps": "Send the comparison.", "open_questions": ""})
        fc.post(f"/fc/meetings/{rmid}/notes/send", {"confirm": "yes"})
        after = client.get(f"/notes/{rmid}").get_data(as_text=True)
        check("Reviewed notes appear for the correct customer after sending",
              "Reviewed summary for the client." in after and "Daniel Koh" in after)
        # wrong customer cannot see them
        other = B(app)
        other.post("/signup", {"full_name": "Olive Outsider", "email": "olive@example.com", "password": "correct-horse-9"})
        for n, k in {1: "30", 2: "full_time", 3: "spouse_child", 4: "100_300", 5: "family_income", 6: "at_60", 7: "private", 8: "personal_employer"}.items():
            other.post(f"/onboarding/question/{n}", {"answer": k, "other_text": "", "action": "next"})
        other.post("/onboarding/review", {})
        check("A different customer cannot see the notes", other.get(f"/notes/{rmid}").status_code == 404)

        # --- generate the ROA from reviewed notes + confirmed gap, edit it, verify edits survive reload ---
        fc.post(f"/fc/roa/{rmid}/generate")
        fc.post(f"/fc/roa/{rmid}/save", {"action": "refresh"})
        roa = fc.get(f"/fc/roa/{rmid}").get_data(as_text=True)
        check("ROA from the reviewed meeting carries the confirmed gap", "shortfall of S$130,000" in roa)
        import re
        body = re.search(r'<textarea name="body_meeting"[^>]*>(.*?)</textarea>', roa, re.S).group(1)
        fc.post(f"/fc/roa/{rmid}/save", {"action": "save", "title_meeting": "Meeting details",
                                         "body_meeting": body + "\nFC edit: client confirmed the figures."})
        reloaded = fc.get(f"/fc/roa/{rmid}").get_data(as_text=True)
        check("ROA edit survives reload", "FC edit: client confirmed the figures." in reloaded)

        db.session.remove()
        db.drop_all()

    print("\n==== Walkthrough summary ====")
    print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
    for f in FAIL:
        print("  FAILED: " + f)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
