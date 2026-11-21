"""Week 7 prototype: death cover score, Ask AI, quiz and demo checkout, settings, FC demo and ROA.

Groq is mocked everywhere. No test makes a live call. _test database only."""

import json
import re
from types import SimpleNamespace

import groq
import httpx
import pytest
from conftest import Browser

from app import buy_routes, score as death_score, sharing
from app.ai import ask as ai_ask
from app.ai import client as ai_client
from app.extensions import db
from app.models import (
    AskAnswer, AskHistory, ComparisonCell, ComparisonFlag, DemoPurchase, FCAssignment, FCDemoState, Policy,
    PolicyClause, QuestionnaireAnswer, QuizAttempt, Scenario, ShareGrant, User, UserSetting,
)

KEY = "gsk_TEST_KEY_must_never_leak_0123456789"
WORKED = {"fields": {"age": "30", "support_years": "5", "support_annual": "50,000", "housing_debt": "80,000",
                     "other_debts": "0", "savings": "50,000", "existing_cover": "150,000"}, "plans": [], "events": []}


class FakeGroq:
    instances, responder, error = [], None, None

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.requests = []
        FakeGroq.instances.append(self)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if FakeGroq.error is not None:
            raise FakeGroq.error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=FakeGroq.responder(kwargs["messages"])))])

    @classmethod
    def calls(cls):
        return [r for i in cls.instances for r in i.requests]


@pytest.fixture(autouse=True)
def no_live_calls(monkeypatch, app):
    def refuse(*a, **k):
        raise AssertionError("a test tried to make a live network request")
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(ai_client, "Groq", refuse)
    for name in ("AI_DEBUG", "APP_ENV", "GROQ_API_KEY", "GROQ_MODEL"):
        monkeypatch.delenv(name, raising=False)
    FakeGroq.instances, FakeGroq.responder, FakeGroq.error = [], None, None
    yield


@pytest.fixture()
def fake_groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    monkeypatch.setenv("GROQ_MODEL", "test-model")
    monkeypatch.setattr(ai_client, "Groq", FakeGroq)
    FakeGroq.responder = lambda m: pytest.fail("the provider must not be called here")
    return FakeGroq


@pytest.fixture()
def user(browser, seeded):
    browser.signup(name="Wendy Week", email="wendy@example.com")
    browser.onboard()
    return browser


def plan(name="PRUActive Term"):
    return Policy.query.filter_by(name=name, is_demo=True).one()


def save_scenario(b, **fields):
    payload = json.loads(json.dumps(WORKED))
    payload["fields"].update(fields)
    return b.client.post("/simulator/save", json=payload, headers={"X-CSRF-Token": b.token()})


def term_reply(messages):
    sent = json.loads(messages[1]["content"])
    clause = next(c for c in sent["clauses"] if "critical" in c["text"].lower())
    return json.dumps({"sufficient": True, "statements": [
        {"text": "Critical illness cover is an optional rider that costs extra.", "clause_ids": [clause["id"]]}]})


# ---------- 1. death cover score ----------

def test_score_rule():
    assert death_score.score(150_000, 280_000) == 54
    assert death_score.score(500_000, 280_000) == 100
    assert death_score.score(0, 280_000) == 0
    assert death_score.score(-5, 280_000) == 0
    assert death_score.score(100, 0) is None and death_score.score(100, -50) is None


def test_dashboard_shows_setup_then_the_saved_baseline_and_updates_after_resave(user):
    page = user.get("/dashboard").get_data(as_text=True)
    assert 'id="cover-setup"' in page and "Set up your estimate" in page and 'id="cover-score"' not in page
    assert "62" not in re.findall(r'class="ring-number"[^>]*>(\d+)<', page)
    assert save_scenario(user).status_code == 200
    page = user.get("/dashboard").get_data(as_text=True)
    assert re.search(r'id="cover-score">54<', page)                      # 150,000 / 280,000
    assert "Death cover only" in page and "Estimated gap S$130,000" in page
    assert "Illustrative" in page                                         # the remaining radar stays labelled
    save_scenario(user, existing_cover="280,000")
    assert re.search(r'id="cover-score">100<', user.get("/dashboard").get_data(as_text=True))


def test_zero_need_shows_no_percentage(user):
    save_scenario(user, support_years="0", support_annual="0", housing_debt="0", other_debts="0", savings="0")
    page = user.get("/dashboard").get_data(as_text=True)
    assert 'id="cover-none"' in page and 'id="cover-score"' not in page and "no percentage" in page


def test_hypothetical_events_do_not_change_the_saved_baseline_score(user):
    payload = json.loads(json.dumps(WORKED))
    payload["events"] = [{"type": "buy_home", "age": "31", "amount": "300,000"}]
    user.client.post("/simulator/save", json=payload, headers={"X-CSRF-Token": user.token()})
    # The dashboard baseline is the 'today' figure. Events only appear in the what-if result on the simulator page.
    assert re.search(r'id="cover-score">54<', user.get("/dashboard").get_data(as_text=True))
    sim = user.get("/simulator").get_data(as_text=True)
    assert 'id="score-live"' in sim and "score-live.js" in sim and "Death cover only" in sim


# ---------- 2. Ask AI ----------

def test_no_page_load_calls_the_provider(user, fake_groq):
    demo = plan()
    for url in ("/dashboard", "/ask", f"/ask?plan={demo.id}", f"/plans/{demo.id}", f"/plans/{demo.id}?lang=zh",
                f"/plans/{demo.id}?lang=ms", "/plans", "/simulator", "/compare", "/settings"):
        assert user.get(url).status_code == 200, url
    assert fake_groq.calls() == []
    page = user.get(f"/plans/{demo.id}?lang=zh").get_data(as_text=True)
    assert "Translation not generated yet" in page and 'id="generate-summary"' in page


def test_ask_answers_with_a_citation_and_original_wording_and_caches(user, fake_groq):
    fake_groq.responder = term_reply
    demo = plan()
    form = {"plan": demo.id, "lang": "en", "action": "ask", "question": "Does this plan pay for critical illness?"}
    page = user.post("/ask", form).get_data(as_text=True)
    assert len(fake_groq.calls()) == 1
    assert "AI answer, checked against the policy wording" in page
    link = re.search(r'href="(/citations/demo/\d+\?plan=%d)" aria-label="Open source 1"' % demo.id, page).group(1)
    assert 'Critical Illness Rider (optional)' in page                      # original wording stays visible
    opened = user.get(link)
    assert opened.status_code == 200 and 'id="cited-line"' in opened.get_data(as_text=True)
    kwargs = fake_groq.instances[0].init_kwargs
    assert kwargs["max_retries"] == 0 and kwargs["timeout"] == ai_client.TIMEOUT_SECONDS   # short, no auto retry
    sent = json.loads(fake_groq.calls()[0]["messages"][1]["content"])
    assert set(sent) == {"plan", "question", "clauses"} and len(sent["clauses"]) <= ai_ask.MAX_CLAUSES
    assert "wendy" not in json.dumps(fake_groq.calls()[0]).lower() and KEY not in json.dumps(sent)
    again = user.post("/ask", {**form, "question": "  does THIS plan pay for critical illness? "}).get_data(as_text=True)
    assert len(fake_groq.calls()) == 1 and "Open source 1" in again          # cached (normalised question)
    user.post("/ask", {**form, "lang": "zh"})
    assert len(fake_groq.calls()) == 2                                       # another language is another entry
    assert AskAnswer.query.count() == 2


def test_ask_cache_follows_the_clause_version(user, fake_groq):
    fake_groq.responder = term_reply
    form = {"plan": plan().id, "lang": "en", "action": "ask", "question": "Does this plan pay for critical illness?"}
    user.post("/ask", form)
    clause = PolicyClause.query.filter_by(policy_id=plan().id).first()
    clause.text = clause.text + " (updated)"
    db.session.commit()
    user.post("/ask", form)
    assert len(fake_groq.calls()) == 2


def test_ask_says_when_the_source_is_insufficient_and_caches_that(user, fake_groq):
    fake_groq.responder = lambda m: json.dumps({"sufficient": False, "statements": []})
    form = {"plan": plan().id, "lang": "en", "action": "ask", "question": "Does it cover skydiving accidents abroad?"}
    page = user.post("/ask", form).get_data(as_text=True)
    assert 'id="insufficient"' in page and "does not answer that question" in page and "Source insufficient" in page
    assert 'id="result-relevant"' in page or "result-relevant" in page
    user.post("/ask", form)
    assert len(fake_groq.calls()) == 1


@pytest.mark.parametrize("bad", [
    lambda m: "not json",
    lambda m: json.dumps({"sufficient": True, "statements": [{"text": "It pays S$987,654.", "clause_ids": [json.loads(m[1]["content"])["clauses"][0]["id"]]}]}),
    lambda m: json.dumps({"sufficient": True, "statements": [{"text": "Cover is optional.", "clause_ids": [999999]}]}),
    lambda m: json.dumps({"sufficient": True, "statements": [{"text": "You should buy the rider.", "clause_ids": [json.loads(m[1]["content"])["clauses"][0]["id"]]}]}),
])
def test_an_unsupported_answer_becomes_a_fallback_with_source_links_and_is_not_cached(user, fake_groq, bad):
    fake_groq.responder = bad
    form = {"plan": plan().id, "lang": "en", "action": "ask", "question": "Does this plan pay for critical illness?"}
    page = user.post("/ask", form).get_data(as_text=True)
    assert "No AI answer" in page and "did not match the policy wording" in page
    assert 'id="result-relevant"' in page and re.search(r'href="/citations/demo/\d+\?plan=', page)
    assert AskAnswer.query.count() == 0
    user.post("/ask", form)
    assert len(fake_groq.calls()) == 2          # nothing cached, and still no automatic retry within one request


def test_ask_fallback_on_quota_timeout_missing_key_and_rate_limit(user, fake_groq):
    form = {"plan": plan().id, "lang": "en", "action": "ask", "question": "Does this plan pay for critical illness?"}
    response = httpx.Response(429, request=httpx.Request("POST", "https://example.invalid"))
    fake_groq.error = groq.APIStatusError("quota", response=response, body=None)
    page = user.post("/ask", form).get_data(as_text=True)
    assert "busy or out of quota" in page and 'id="result-relevant"' in page and len(fake_groq.calls()) == 1
    fake_groq.error = groq.APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))
    assert "took too long" in user.post("/ask", form).get_data(as_text=True)
    assert len(fake_groq.calls()) == 2          # one attempt per press, never more

    from app.ai.limiter import SlidingWindowLimiter
    user.client.application.extensions["ai_summary_limiter"] = SlidingWindowLimiter(1, 60)
    fake_groq.error = None
    fake_groq.responder = term_reply
    user.post("/ask", {**form, "question": "A different question about critical illness"})
    assert "Too many requests" in user.post("/ask", {**form, "question": "Yet another question about the rider"}).get_data(as_text=True)


def test_ask_without_a_key_makes_no_call_and_validates_input(user):
    form = {"plan": plan().id, "lang": "en", "action": "ask", "question": "Does this plan pay for critical illness?"}
    assert "not set up on this server" in user.post("/ask", form).get_data(as_text=True)
    assert user.post("/ask", {**form, "question": "hi"}).status_code == 400
    assert user.post("/ask", {**form, "plan": 999999}).status_code == 400
    assert user.post("/ask", {**form, "lang": "xx"}).status_code == 400
    assert user.post("/ask", {**form, "action": "other"}).status_code == 400
    assert user.client.post("/ask", data=form).status_code == 400            # no CSRF token


def test_ask_only_offers_demo_plans(user):
    page = user.get("/ask").get_data(as_text=True)
    names = re.findall(r'<option value="\d+"[^>]*>([^<]+) \(', page)
    assert names[:6] == ["PRUActive Term", "PRUShield Premier", "PRUCritical Care", "PRULife Whole", "PRURetire Income", "PRUDisability Guard"]


def test_summary_buttons_generate_once_then_show_the_cached_ai_summary(user, fake_groq):
    demo = plan()

    def responder(messages):
        sent = json.loads(messages[1]["content"])
        if "clauses" in sent:
            ids = {c["clause"]: c["id"] for c in sent["clauses"]}
            spec = [("Cover lasts until the Life Assured reaches age 65.", "3.1"),
                    ("If the Life Assured dies, a lump sum is paid.", "3.2"),
                    ("Premiums are fixed for the whole policy term.", "2.1"),
                    ("The plan has no cash value.", "6.3")]
            return json.dumps({"statements": [{"text": t, "clause_ids": [ids[c]]} for t, c in spec]})
        return json.dumps({"statements": [{"text": "译文 " + s["text"], "clause_ids": s["clause_ids"]} for s in sent["statements"]]})

    fake_groq.responder = responder
    r = user.post(f"/plans/{demo.id}/summary", {"lang": "en"})
    assert r.status_code == 302 and "msg=ai" in r.location and len(fake_groq.calls()) == 1
    page = user.get(r.location).get_data(as_text=True)
    assert "AI-generated summary, checked against the policy wording" in page and 'id="generate-summary"' not in page
    assert re.search(r'/citations/demo/\d+\?plan=%d' % demo.id, page)
    user.get(f"/plans/{demo.id}")
    assert len(fake_groq.calls()) == 1                                         # views never call
    assert "Translation not generated yet" in user.get(f"/plans/{demo.id}?lang=zh").get_data(as_text=True)
    user.post(f"/plans/{demo.id}/summary", {"lang": "zh"})
    assert len(fake_groq.calls()) == 2
    zh = user.get(f"/plans/{demo.id}?lang=zh").get_data(as_text=True)
    assert "译文" in zh and "AI translation. Confirm details with your FC." in zh


def test_summary_button_falls_back_with_a_message_when_the_provider_fails(user, fake_groq):
    fake_groq.responder = lambda m: "not json"
    r = user.post(f"/plans/{plan().id}/summary", {"lang": "en"})
    page = user.get(r.location).get_data(as_text=True)
    assert "msg=fallback" in r.location and "standard summary is shown" in page
    assert 'id="summary-label">Standard summary<' in page and re.search(r'/citations/demo/\d+\?plan=', page)


def test_dashboard_and_plan_page_enable_ask_ai_and_get_this_plan(user):
    demo = plan()
    assert 'href="/ask"' in user.get("/dashboard").get_data(as_text=True)
    page = user.get(f"/plans/{demo.id}").get_data(as_text=True)
    assert f'href="/ask?plan={demo.id}"' in page and f'href="/plans/{demo.id}/proceed"' in page and "Soon" not in page.split("plan-head")[1].split("plan-cols")[0]


# ---------- 3. buy or consult, quiz, checkout ----------

def answers_for(policy, right):
    """Pick answers: `right` of them correct."""
    out = []
    for i, q in enumerate(buy_routes.build_questions(policy)):
        out.append(q["correct"] if i < right else (q["correct"] + 1) % 3)
    return out


def take_quiz(b, policy, right):
    b.post(f"/plans/{policy.id}/quiz/start")
    for n, choice in enumerate(answers_for(policy, right), start=1):
        r = b.post(f"/plans/{policy.id}/quiz/answer", {"q": n, "choice": choice})
        assert r.status_code == 302
    return b.get(f"/plans/{policy.id}/quiz/result").get_data(as_text=True)


def test_every_demo_plan_has_four_questions_backed_by_clauses(app, seeded):
    for p in Policy.query.filter_by(is_demo=True):
        questions = buy_routes.build_questions(p)
        assert len(questions) == 4, p.name
        for q in questions:
            clause = db.session.get(PolicyClause, q["clause_id"])
            assert clause.policy_id == p.id and len(q["options"]) == 3 and 0 <= q["correct"] <= 2


def test_proceed_offers_consult_and_buy(user):
    demo = plan()
    page = user.get(f"/plans/{demo.id}/proceed").get_data(as_text=True)
    assert f"How would you like to proceed with {demo.name}?" in page
    assert 'href="/sharing/new?category=profile_answers"' in page and 'id="buy-now"' in page
    assert user.get("/sharing/new?category=profile_answers").status_code == 200
    assert user.get("/plans/9999/proceed").status_code == 404


def test_quiz_feedback_citations_retry_and_pass_mark(user):
    demo = plan()
    user.post(f"/plans/{demo.id}/quiz/start")
    first = buy_routes.build_questions(demo)[0]
    page = user.get(f"/plans/{demo.id}/quiz?q=1").get_data(as_text=True)
    assert "Question 1 of 4" in page and "you need 3 of 4" in page and first["options"][0] in page
    wrong = user.post(f"/plans/{demo.id}/quiz/answer", {"q": 1, "choice": (first["correct"] + 1) % 3})
    fb = user.get(wrong.location).get_data(as_text=True)
    assert "Not quite" in fb and re.search(r'id="quiz-source"', fb) and re.search(r'/citations/demo/\d+', fb)
    assert user.get(f"/plans/{demo.id}/quiz?q=3").status_code == 200       # clamped to the first unanswered question
    user.post(f"/plans/{demo.id}/quiz/answer", {"q": 1, "choice": first["correct"]})   # cannot change an answer
    assert QuizAttempt.query.one().answers["1"] != first["correct"]
    assert user.post(f"/plans/{demo.id}/quiz/answer", {"q": 3, "choice": 0}).status_code == 400   # cannot skip ahead
    result = take_quiz(user, demo, 2)
    assert "Your result: 2 of 4" in result and 'id="quiz-failed"' in result and 'id="retry-quiz"' in result
    assert "to-checkout" not in result
    result = take_quiz(user, demo, 3)                                         # retry: a new attempt
    assert "3 of 4" in result and 'id="quiz-passed"' in result and 'id="to-checkout"' in result
    assert QuizAttempt.query.count() == 3 or QuizAttempt.query.filter_by(finished=True).count() == 2


def test_checkout_is_gated_on_the_server_and_collects_no_payment_details(user):
    demo = plan()
    assert user.get(f"/plans/{demo.id}/checkout").location.endswith(f"/plans/{demo.id}/proceed?need=quiz")
    assert user.post(f"/plans/{demo.id}/checkout").status_code == 302
    assert DemoPurchase.query.count() == 0
    assert user.get(f"/plans/{demo.id}/checkout/done").status_code == 302
    take_quiz(user, demo, 2)                                                  # failed attempt: still gated
    assert user.get(f"/plans/{demo.id}/checkout").status_code == 302 and DemoPurchase.query.count() == 0
    take_quiz(user, demo, 4)
    page = user.get(f"/plans/{demo.id}/checkout").get_data(as_text=True)
    assert "Order summary" in page and demo.name in page and "4/4" in page and "S$89" in page
    payment = re.sub(r'<input type="hidden"[^>]*>', "", page).split("Payment details")[1].split("Order summary")[0]
    assert "<input" not in payment and "<select" not in payment
    assert "card number" not in page.lower() and "cvv" not in page.lower()
    r = user.post(f"/plans/{demo.id}/checkout")
    done = user.get(r.location).get_data(as_text=True)
    assert "Demo purchase confirmed" in done and "no money moved" in done
    user.post(f"/plans/{demo.id}/checkout")
    assert DemoPurchase.query.count() == 1                                    # confirming twice makes one order


def test_a_pass_for_one_plan_does_not_open_checkout_for_another(user):
    take_quiz(user, plan("PRUActive Term"), 4)
    assert user.get(f"/plans/{plan('PRUCritical Care').id}/checkout").status_code == 302
    assert user.get(f"/plans/{plan('PRUActive Term').id}/checkout").status_code == 200


def test_another_users_pass_does_not_count(user, app):
    take_quiz(user, plan(), 4)
    other = Browser(app.test_client())
    other.signup(email="other@example.com")
    other.onboard()
    assert other.get(f"/plans/{plan().id}/checkout").status_code == 302


# ---------- 4. settings ----------

def test_preferences_persist_across_reloads_and_logins(user, app):
    page = user.get("/settings").get_data(as_text=True)
    assert 'id="text_size"' in page and "High contrast" in page and "Summary language" in page and "text-large" not in page
    r = user.post("/settings", {"text_size": "large", "high_contrast": "on", "read_aloud": "on", "summary_language": "zh"})
    assert r.status_code == 302
    page = user.get("/settings?saved=1").get_data(as_text=True)
    assert 'class="text-large contrast-high"' in page.replace("  ", " ") or ("text-large" in page and "contrast-high" in page)
    assert 'value="large" selected' in page and 'id="high_contrast" checked' in page and 'value="zh" selected' in page
    assert "Saved." in page
    user.logout()
    user.login(email="wendy@example.com")
    again = user.get("/dashboard").get_data(as_text=True)
    assert "text-large" in again and "contrast-high" in again                 # applied on every page
    row = UserSetting.query.one()
    assert (row.text_size, row.high_contrast, row.read_aloud, row.summary_language) == ("large", True, True, "zh")
    assert user.post("/settings", {"text_size": "huge", "summary_language": "en"}).status_code == 400
    assert user.post("/settings", {"text_size": "normal", "summary_language": "xx"}).status_code == 400


def test_the_saved_summary_language_is_the_default_on_plan_and_ask_pages(user):
    user.post("/settings", {"text_size": "normal", "summary_language": "ms"})
    demo = plan()
    assert "Translation not generated yet" in user.get(f"/plans/{demo.id}").get_data(as_text=True)
    assert 'value="ms" selected' in user.get("/ask").get_data(as_text=True)
    assert "Translation not generated yet" not in user.get(f"/plans/{demo.id}?lang=en").get_data(as_text=True)


def _assign(client_email, fc_email, categories):
    client = User.query.filter_by(email=client_email).one()
    fc = User.query.filter_by(email=fc_email).one()
    sharing.confirm_sharing(client, fc, "referral", categories)
    return client, fc


def test_change_fc_previews_the_new_fc_and_changes_nothing_until_confirmed(user, app):
    client, daniel = _assign("wendy@example.com", "fc.daniel@example.com", ["profile_answers", "flagged_items"])
    page = user.get("/settings").get_data(as_text=True)
    assert "Daniel Koh" in page and 'id="change-fc"' in page
    assert user.get("/settings/fc").status_code == 200
    bad = user.post("/settings/fc", {"choice": "referral", "code": "FC-0000"})
    assert bad.status_code == 400 and "could not find that referral code" in bad.get_data(as_text=True)
    same = user.post("/settings/fc", {"choice": "referral", "code": "FC-2048"})
    assert same.status_code == 400 and "already your FC" in same.get_data(as_text=True)
    r = user.post("/settings/fc", {"choice": "referral", "code": "FC-3117"})
    assert r.location.endswith("/settings/fc/preview")
    preview = user.get("/settings/fc/preview").get_data(as_text=True)
    assert "Jane" in preview and "Nothing is shared with" in preview and "stops seeing your data" in preview
    for category in ("profile_answers", "simulator", "flagged_items"):
        assert f'value="{category}"' in preview
    assert re.search(r'value="profile_answers" checked', preview) and not re.search(r'value="simulator" checked', preview)
    assert sharing.assignment_for(client.id).fc_id == daniel.id              # nothing changed yet
    assert len(sharing.active_grants(client.id)) == 2
    user.post("/settings/fc/cancel")
    assert sharing.assignment_for(client.id).fc_id == daniel.id and user.get("/settings/fc/preview").status_code == 302


def test_confirming_change_fc_moves_access_to_the_new_fc(user, app):
    client, daniel = _assign("wendy@example.com", "fc.daniel@example.com", ["profile_answers", "flagged_items"])
    user.post("/settings/fc", {"choice": "referral", "code": "FC-3117"})
    r = user.post("/settings/fc/confirm", {"categories": ["profile_answers", "simulator"]})
    assert r.location.endswith("/settings?fc_changed=1")
    jane = User.query.filter_by(email="fc.jane@example.com").one()
    assert sharing.assignment_for(client.id).fc_id == jane.id
    active = {(g_.fc_id, g_.category) for g_ in sharing.active_grants(client.id)}
    assert active == {(jane.id, "profile_answers"), (jane.id, "simulator")}
    assert sharing.visible_categories(daniel, client.id) == frozenset()       # the old FC lost access
    assert sharing.visible_categories(jane, client.id) == frozenset({"profile_answers", "simulator"})
    assert "Jane" in user.get("/settings?fc_changed=1").get_data(as_text=True)
    assert ShareGrant.query.filter_by(client_id=client.id, fc_id=daniel.id, revoked_at=None).count() == 0


def test_change_fc_can_match_randomly_to_a_different_fc(user):
    _assign("wendy@example.com", "fc.daniel@example.com", ["profile_answers"])
    r = user.post("/settings/fc", {"choice": "random"})
    assert r.location.endswith("/settings/fc/preview")
    assert "Jane" in user.get("/settings/fc/preview").get_data(as_text=True)
    assert user.post("/settings/fc", {"choice": "bogus"}).status_code == 400


def test_clear_my_data_resets_only_demo_state(user, app):
    demo = plan()
    client = User.query.filter_by(email="wendy@example.com").one()
    save_scenario(user)
    take_quiz(user, demo, 4)
    user.post(f"/plans/{demo.id}/checkout")
    db.session.add(ComparisonFlag(user_id=client.id, policy_id=demo.id, row_key="premium_waiver"))
    db.session.add(AskHistory(user_id=client.id, policy_id=demo.id, kind="question", question="q?", language="en", outcome="ai"))
    db.session.commit()
    user.post("/settings", {"text_size": "large", "summary_language": "zh"})
    _assign("wendy@example.com", "fc.daniel@example.com", ["profile_answers"])
    answers = QuestionnaireAnswer.query.filter_by(user_id=client.id).count()

    page = user.get("/settings/clear").get_data(as_text=True)
    assert "Clear my data?" in page and "Will be reset" in page and "Questionnaire answers" in page and "Cancel" in page
    assert DemoPurchase.query.count() == 1                                     # viewing the confirmation changes nothing
    r = user.post("/settings/clear")
    assert r.location.endswith("/settings?cleared=1")
    assert "Demo data reset" in user.get(r.location).get_data(as_text=True)
    assert (QuizAttempt.query.count(), DemoPurchase.query.count(), AskHistory.query.count(),
            ComparisonFlag.query.count(), UserSetting.query.count()) == (0, 0, 0, 0, 0)
    assert User.query.filter_by(email="wendy@example.com").count() == 1
    assert QuestionnaireAnswer.query.filter_by(user_id=client.id).count() == answers == 8
    assert Scenario.query.filter_by(user_id=client.id).count() == 1
    assert len(sharing.active_grants(client.id)) == 1                          # sharing is managed separately
    assert user.get(f"/plans/{demo.id}/checkout").status_code == 302           # the quiz pass is gone too


def test_clear_my_data_leaves_other_users_alone(user, app):
    other = Browser(app.test_client())
    other.signup(email="keep@example.com")
    other.onboard()
    save_scenario(other)
    take_quiz(other, plan(), 4)
    user.post("/settings/clear")
    assert Scenario.query.count() == 1 and QuizAttempt.query.count() == 1


def test_settings_pages_are_for_signed_in_clients_only(app, seeded):
    anon = app.test_client()
    for url in ("/settings", "/settings/fc", "/settings/clear", "/ask", "/plans/1/proceed"):
        assert anon.get(url).status_code == 302, url
    fc = Browser(app.test_client())
    fc.login(email="fc.daniel@example.com") if False else None


# ---------- 5. FC portal, meeting, ROA ----------

@pytest.fixture()
def fc(app, seeded):
    b = Browser(app.test_client())
    assert b.post("/login", {"email": "fc.daniel@example.com", "password": "demo-fc-password-1"}).status_code == 302
    return b


def test_clients_cannot_open_the_fc_demo_and_fcs_cannot_use_client_pages(user, fc):
    for url in ("/fc/demo/request", "/fc/demo/meeting", "/fc/demo/roa", "/fc/demo/profile"):
        assert user.get(url).status_code == 404, url
    assert user.post("/fc/demo/request/accept").status_code == 404
    assert fc.get("/ask").location.endswith("/fc") and fc.get("/settings").location.endswith("/fc")


def test_incoming_client_request_accept_and_decline(fc):
    index = fc.get("/fc").get_data(as_text=True)
    assert "New client request" in index and "Fictional demo client" in index and "Priya Nair" in index
    page = fc.get("/fc/demo/request").get_data(as_text=True)
    assert "New client request: Priya Nair" in page and "Fictional demo client" in page
    assert "Pre-meeting summary" in page and "Biggest gaps" in page and "Plans compared" in page and "Shared by client" in page
    assert 'id="accept"' in page and 'id="decline"' in page and "Schedule intro call" not in page
    assert fc.get("/fc/demo/meeting").status_code == 302 and fc.get("/fc/demo/roa").status_code == 302
    fc.post("/fc/demo/request/decline")
    page = fc.get("/fc/demo/request").get_data(as_text=True)
    assert "Declined" in page and 'id="accept"' not in page and fc.get("/fc/demo/meeting").status_code == 302
    fc.post("/fc/demo/reset")
    fc.post("/fc/demo/request/accept")
    page = fc.get("/fc/demo/request").get_data(as_text=True)
    assert "Accepted" in page and "Schedule intro call" in page and "Open client profile" in page
    assert fc.get("/fc/demo/profile").status_code == 200
    assert "Priya Nair" in fc.get("/fc").get_data(as_text=True)


def test_the_demo_client_never_appears_as_a_real_record(fc, app):
    fc.post("/fc/demo/request/accept")
    assert User.query.filter(User.full_name.like("%Priya%")).count() == 0
    assert fc.get("/fc/clients/1").status_code == 404           # real records still need a consent grant


def test_meeting_join_end_and_add_to_profile_change_visible_state(fc):
    fc.post("/fc/demo/request/accept")
    idle = fc.get("/fc/demo/meeting").get_data(as_text=True)
    assert "Not started" in idle and 'id="join-meeting"' in idle and 'id="end-meeting"' not in idle and 'id="meeting-points"' not in idle
    assert fc.post("/fc/demo/meeting/end").status_code == 400 and fc.post("/fc/demo/meeting/add-notes").status_code == 400
    fc.post("/fc/demo/meeting/join")
    live = fc.get("/fc/demo/meeting").get_data(as_text=True)
    assert "live (simulated)" in live and 'id="end-meeting"' in live and 'id="meeting-points"' in live and 'id="add-notes"' in live
    assert 'id="join-meeting"' not in live
    fc.post("/fc/demo/meeting/add-notes")
    live = fc.get("/fc/demo/meeting").get_data(as_text=True)
    assert 'id="notes-added"' in live and 'id="add-notes"' not in live
    assert "Budget is capped" in fc.get("/fc/demo/profile").get_data(as_text=True)
    fc.post("/fc/demo/meeting/end")
    ended = fc.get("/fc/demo/meeting").get_data(as_text=True)
    assert "Call ended" in ended and 'id="end-meeting"' not in ended and 'id="join-meeting"' not in ended


def roa_text(page, key):
    return re.search(r'<textarea id="text_%s"[^>]*>(.*?)</textarea>' % key, page, re.S).group(1)


def test_roa_is_built_from_the_template_and_lists_missing_items(fc):
    fc.post("/fc/demo/request/accept")
    page = fc.get("/fc/demo/roa").get_data(as_text=True)
    for title in ("1. Client profile", "2. Needs and objectives", "3. Gaps identified", "4. Findings from the sample plan wording", "5. Client acknowledgement"):
        assert title in page
    assert "Missing items" in page and "Meeting notes have not been added" in page and "no comprehension quiz record" in page
    assert "30, married" in roa_text(page, "profile") and "PRUActive Term" in roa_text(page, "findings")
    assert "Budget is capped" not in roa_text(page, "needs")
    fc.post("/fc/demo/meeting/join")
    fc.post("/fc/demo/meeting/add-notes")                          # saved meeting notes feed the ROA
    page = fc.get("/fc/demo/roa").get_data(as_text=True)
    assert "Budget is capped" in roa_text(page, "needs") and "Meeting notes have not been added" not in page
    assert "no comprehension quiz record" in page                   # still missing


def test_roa_edit_review_lock_flow(fc):
    fc.post("/fc/demo/request/accept")
    assert 'id="roa-status">Draft<' in fc.get("/fc/demo/roa").get_data(as_text=True)
    fc.post("/fc/demo/roa/save", {"text_profile": "Edited profile text.", "text_needs": "Edited needs."})
    page = fc.get("/fc/demo/roa").get_data(as_text=True)
    assert roa_text(page, "profile") == "Edited profile text." and "(edited)" in page and "30, married" not in roa_text(page, "profile")
    assert fc.post("/fc/demo/roa/lock").status_code == 400          # review first
    fc.post("/fc/demo/roa/review")
    page = fc.get("/fc/demo/roa").get_data(as_text=True)
    assert 'id="roa-status">Reviewed<' in page and 'id="lock-roa"' in page and 'id="mark-reviewed"' not in page
    fc.post("/fc/demo/roa/save", {"text_profile": "Changed after review."})
    assert 'id="roa-status">Draft<' in fc.get("/fc/demo/roa").get_data(as_text=True)    # editing sends it back to draft
    fc.post("/fc/demo/roa/review")
    fc.post("/fc/demo/roa/lock")
    page = fc.get("/fc/demo/roa").get_data(as_text=True)
    assert 'id="roa-status">Locked<' in page and "readonly" in page and 'id="save-roa"' not in page and "locked" in page
    assert fc.post("/fc/demo/roa/save", {"text_profile": "Sneaky"}).status_code == 400
    assert fc.post("/fc/demo/roa/review").status_code == 400
    assert roa_text(fc.get("/fc/demo/roa").get_data(as_text=True), "profile") == "Changed after review."
    fc.post("/fc/demo/reset")
    assert FCDemoState.query.one().roa_status == "draft" and FCDemoState.query.one().request_status == "pending"


def test_each_fc_has_their_own_demo_state(fc, app):
    fc.post("/fc/demo/request/accept")
    jane = Browser(app.test_client())
    jane.post("/login", {"email": "fc.jane@example.com", "password": "demo-fc-password-1"})
    assert "Review request" in jane.get("/fc").get_data(as_text=True)
    assert FCDemoState.query.count() == 2
