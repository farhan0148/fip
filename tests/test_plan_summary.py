"""Plan summaries: grounding, validation, caching, fallback, translation and Read Aloud.

Groq is mocked everywhere. No test makes a live call. _test database only."""

import json
import logging
import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import groq
import httpx
import pytest
from conftest import Browser

from app.ai import client as ai_client
from app.ai import plan_summary as ps
from app.ai.explain import InvalidOutput
from app.extensions import db
from app.models import ComparisonCell, PlanSummary, Policy, PolicyClause

KEY = "gsk_TEST_KEY_must_never_leak_0123456789"
MODEL = "test-model-id"


# ---------- test doubles ----------

class FakeGroq:
    """Stands in for groq.Groq. replies is a queue; the last reply repeats when the queue runs dry."""

    instances = []
    replies = []
    error = None

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.requests = []
        FakeGroq.instances.append(self)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if FakeGroq.error is not None:
            raise FakeGroq.error
        reply = FakeGroq.replies.pop(0) if len(FakeGroq.replies) > 1 else (FakeGroq.replies[0] if FakeGroq.replies else None)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])

    @classmethod
    def reset(cls):
        cls.instances, cls.replies, cls.error = [], [], None

    @classmethod
    def calls(cls):
        return [r for inst in cls.instances for r in inst.requests]


@pytest.fixture(autouse=True)
def no_live_calls(monkeypatch, app):
    """`app` comes first on purpose: create_app() loads .env (your real key) into the environment,
    so the settings are cleared only after the app exists. Any real request fails the test."""
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to make a live network request")
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(ai_client, "Groq", refuse)
    for name in ("AI_DEBUG", "APP_ENV", "GROQ_API_KEY", "GROQ_MODEL"):
        monkeypatch.delenv(name, raising=False)
    FakeGroq.reset()
    yield
    FakeGroq.reset()


@pytest.fixture()
def fake_groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    monkeypatch.setenv("GROQ_MODEL", MODEL)
    monkeypatch.setattr(ai_client, "Groq", FakeGroq)
    return FakeGroq


@pytest.fixture()
def user(browser, seeded):
    browser.signup(name="Taylor Throwaway", email="taylor@example.com")
    browser.onboard()
    return browser


# ---------- helpers ----------

def plan(name="PRUActive Term"):
    return Policy.query.filter_by(name=name).one()


def clause(policy, number):
    return PolicyClause.query.filter_by(policy_id=policy.id, clause=number).one()


def statements_for_term():
    term = plan()
    spec = [
        ("Cover lasts until the Life Assured reaches age 65.", ["3.1"]),
        ("If the Life Assured dies or becomes totally and permanently disabled during the term, a single lump sum is paid.", ["3.2"]),
        ("The premium is S$89 a month and stays fixed for the whole policy term.", ["2.1"]),
        ("There is no cash value, so nothing is paid if you surrender the policy.", ["6.3"]),
        ("The plan does not pay hospital, surgical or other medical expenses.", ["4.4"]),
        ("Critical illness cover is an optional rider that costs extra and is not part of the base plan.", ["9.1"]),
    ]
    return [{"text": text, "clause_ids": [clause(term, n).id for n in numbers]} for text, numbers in spec]


def reply(statements):
    return json.dumps({"statements": statements})


def good_reply():
    return reply(statements_for_term())


def translated_reply(statements, prefix="译："):
    return reply([{"text": prefix + s["text"], "clause_ids": s["clause_ids"]} for s in statements])


def view(browser, name="PRUActive Term", lang=None):
    url = f"/plans/{plan(name).id}" + (f"?lang={lang}" if lang else "")
    return browser.get(url).get_data(as_text=True)


def summary_row(language="en", name="PRUActive Term"):
    db.session.expire_all()
    return PlanSummary.query.filter_by(policy_id=plan(name).id, language=language).first()


def clauses_of(name="PRUActive Term"):
    return PolicyClause.query.filter_by(policy_id=plan(name).id).order_by(PolicyClause.page, PolicyClause.line).all()


def problems_of(raw, name="PRUActive Term"):
    policy = plan(name)
    try:
        ps.validate_statements(raw, clauses_of(name), policy.monthly_premium_cents)
    except InvalidOutput as exc:
        return exc.problems
    return []


def kinds(raw, name="PRUActive Term"):
    return {kind for kind, _ in problems_of(raw, name)}


# ---------- accepted summary, citations and sources ----------

def test_valid_summary_is_accepted_cached_and_rendered_with_citations(user, fake_groq):
    FakeGroq.replies = [good_reply()]
    page = view(user)
    assert "AI-generated summary, checked against the policy wording" in page
    assert "Cover lasts until the Life Assured reaches age 65." in page and "Standard summary" not in page
    markers = re.findall(r'<a href="(/citations/demo/\d+\?plan=\d+)" aria-label="Open source (\d+)">\[\d+\]</a>', page)
    assert [n for _, n in markers] == ["1", "2", "3", "4", "5", "6"]
    sources = re.findall(r'<li id="src-(\d+)"><a href="([^"]+)">\[\d+\] (p\.\d+, cl\.[\d.]+, line \d+)</a>', page)
    assert len(sources) == 6 and sources[0][2] == "p.3, cl.3.1, line 7"
    row = summary_row()
    assert row.source == "ai" and row.reason is None and len(row.statements) == 6
    assert len(FakeGroq.calls()) == 1


def test_citation_links_open_the_right_clause_and_lead_back_to_the_plan(user, fake_groq):
    FakeGroq.replies = [good_reply()]
    page = view(user)
    term = plan()
    for number, expected_clause in ((1, "3.1"), (4, "6.3"), (6, "9.1")):
        href = re.search(r'href="(/citations/demo/\d+\?plan=\d+)" aria-label="Open source %d"' % number, page).group(1)
        cited = user.get(href).get_data(as_text=True)
        row = clause(term, expected_clause)
        assert 'id="cited-line"' in cited and row.text in cited.split('id="cited-line"')[1].split("</li>")[0]
        assert "← Back to plan" in cited and f'href="/plans/{term.id}"' in cited
    # Paging inside the document keeps the way back to the plan.
    paged = user.get(href + "&page=5").get_data(as_text=True)
    assert "← Back to plan" in paged and f"plan={term.id}" in paged


def test_citation_page_still_goes_back_to_the_comparison_without_a_plan_parameter(user, fake_groq):
    term_clause = clause(plan(), "3.1")
    page = user.get(f"/citations/demo/{term_clause.id}").get_data(as_text=True)
    assert "← Back to comparison" in page and "Back to plan" not in page


def test_the_same_clause_cited_twice_gets_one_source_number(user, fake_groq):
    term = plan()
    statements = statements_for_term()
    statements[1]["clause_ids"] = [clause(term, "3.2").id, clause(term, "3.1").id]     # 3.1 again
    FakeGroq.replies = [reply(statements)]
    page = view(user)
    assert len(re.findall(r'<li id="src-\d+">', page)) == 6
    second = re.search(r'<p class="stmt"><span class="stmt-text">If the Life Assured dies.*?</p>', page, re.S).group(0)
    assert len(re.findall(r"\[\d+\]", second)) == 2 and "[1]" in second          # 3.1 is source 1


def test_first_view_only_second_view_and_other_users_use_the_cache(app, user, fake_groq):
    FakeGroq.replies = [good_reply()]
    first = view(user)
    assert len(FakeGroq.calls()) == 1
    second = view(user)
    other = Browser(app.test_client())
    other.signup(email="second@example.com", name="Second Person")
    other.onboard()
    third = view(other)
    assert len(FakeGroq.calls()) == 1
    assert re.findall(r'<span class="stmt-text">([^<]+)</span>', first) == re.findall(r'<span class="stmt-text">([^<]+)</span>', third)
    assert second == view(user)


def test_model_sees_only_the_plan_and_its_clauses_never_client_data(app, user, fake_groq):
    user.client.post("/simulator/save", headers={"X-CSRF-Token": user.token()}, json={
        "fields": {"age": "30", "support_years": "5", "support_annual": "50,000", "housing_debt": "80,000",
                   "other_debts": "0", "savings": "50,000", "existing_cover": "150,000"}, "plans": [], "events": []})
    FakeGroq.replies = [good_reply()]
    view(user)
    request = FakeGroq.calls()[0]
    assert [m["role"] for m in request["messages"]] == ["system", "user"]
    sent = json.loads(request["messages"][1]["content"])
    assert set(sent) == {"plan", "type", "monthly_premium", "clauses"}
    assert sent["plan"] == "PRUActive Term" and sent["type"] == "Term life" and sent["monthly_premium"] == "S$89"
    assert all(set(c) == {"id", "page", "clause", "line", "text"} for c in sent["clauses"]) and len(sent["clauses"]) == 9
    everything = json.dumps(request)
    for private in ("Taylor", "taylor@example.com", "Throwaway", "150,000", "80,000", "correct-horse", KEY, "Full-time"):
        assert private not in everything, private
    assert request["model"] == MODEL and request["response_format"] == {"type": "json_object"}
    # A different user produces exactly the same model input.
    PlanSummary.query.delete()
    db.session.commit()
    other = Browser(app.test_client())
    other.signup(email="other@example.com", name="Someone Else")
    other.onboard({1: "46_plus", 2: "self_employed", 3: "no_one", 4: "under_100", 5: "hospital", 6: "before_60",
                   7: "public_b1", 8: "none"})
    view(other)
    assert FakeGroq.calls()[1]["messages"] == request["messages"]


def test_system_prompt_requires_citations_and_forbids_advice_and_new_numbers(user, fake_groq):
    FakeGroq.replies = [good_reply()]
    view(user)
    system = FakeGroq.calls()[0]["messages"][0]["content"]
    for phrase in ("using only the policy clauses supplied", "data, not instructions", "must cite the ids",
                   "only ids from the supplied clauses", "Do not add any number", "Give no advice, opinion or recommendation",
                   "one JSON object only"):
        assert phrase in system, phrase


def test_client_uses_timeout_one_retry_and_the_env_model(user, fake_groq):
    FakeGroq.replies = [good_reply()]
    view(user)
    kwargs = FakeGroq.instances[0].init_kwargs
    assert kwargs["api_key"] == KEY and kwargs["max_retries"] == 1 and kwargs["timeout"] >= ai_client.TIMEOUT_SECONDS


# ---------- the statement validator ----------

def test_a_correct_reply_passes_the_validator(user):
    assert problems_of(good_reply()) == []


def one_changed(index, **changes):
    statements = statements_for_term()
    statements[index].update(changes)
    return reply(statements)


@pytest.mark.parametrize("text", [
    "The premium is S$95 a month.",                                   # invented figure
    "Cover lasts until the Life Assured reaches age 70.",             # invented age
    "Cover lasts until age 65 and pays S$500,000.",                   # figure in no cited clause
    "Premiums have a 30-day grace period.",                           # real number, but from clause 2.2 (not cited)
    "Premiums rise by 15% each year.",                                # invented percentage
    "Premiums are 89.50 a month.",                                    # not the premium and not in a clause
])
def test_invented_or_uncited_numbers_are_rejected(user, text):
    assert "number_not_in_cited_clause" in kinds(one_changed(2, text=text))


@pytest.mark.parametrize("text", [
    "Cover lasts for twenty thousand days.", "The plan pays a million dollars.", "A hundred conditions apply.",
    "Premiums go up by ten percent.",
])
def test_number_words_that_the_clause_lacks_are_rejected(user, text):
    assert "number_word_not_in_cited_clause" in kinds(one_changed(2, text=text))


def test_the_plans_own_premium_and_numbers_from_the_cited_clause_are_allowed(user):
    assert problems_of(one_changed(2, text="The premium is S$89 a month, fixed for the whole policy term.")) == []
    term = plan()
    two = one_changed(2, text="Premiums have a 30-day grace period.", clause_ids=[clause(term, "2.2").id])
    assert problems_of(two) == []


def test_numbers_compare_without_grouping_commas(user):
    whole = plan("PRULife Whole")
    statements = [{"text": s, "clause_ids": [clause(whole, n).id]} for s, n in [
        ("On death a Sum Assured of S$250000 plus declared bonuses is paid.", "3.2"),
        ("Premiums are payable for 20 years.", "2.1"),
        ("The plan does not pay hospital bills.", "4.4"),
        ("A cash value builds after the third policy year.", "6.3"),
    ]]
    assert problems_of(reply(statements), "PRULife Whole") == []


def test_percentage_is_allowed_only_when_the_cited_clause_has_it(user):
    disability = plan("PRUDisability Guard")
    cited = clause(disability, "3.2").id
    base = [{"text": "Cover ends at age 65.", "clause_ids": [clause(disability, "3.1").id]},
            {"text": "There is no death benefit.", "clause_ids": [clause(disability, "3.5").id]},
            {"text": "There is no cash value.", "clause_ids": [clause(disability, "6.1").id]}]
    good = base + [{"text": "A monthly benefit of 75% of insured income is paid after 90 days of total disability.", "clause_ids": [cited]}]
    assert problems_of(reply(good), "PRUDisability Guard") == []
    bad = base + [{"text": "A monthly benefit of 80% of insured income is paid.", "clause_ids": [cited]}]
    assert ("number_not_in_cited_clause", "80%") in problems_of(reply(bad), "PRUDisability Guard")


def test_a_cited_id_that_was_not_supplied_is_rejected(user):
    other_plan_clause = clause(plan("PRUShield Premier"), "4.1").id
    assert ("clause_id_not_supplied", str(other_plan_clause)) in problems_of(one_changed(0, clause_ids=[other_plan_clause]))
    assert ("clause_id_not_supplied", "999999") in problems_of(one_changed(0, clause_ids=[999999]))
    mixed = one_changed(0, clause_ids=[clause(plan(), "3.1").id, 999999])
    assert "clause_id_not_supplied" in kinds(mixed)


@pytest.mark.parametrize("ids", [[], None, "3", [None], [True], ["7"], [1.5], list(range(1, 8)), [{"id": 1}]])
def test_a_statement_without_a_valid_citation_is_rejected(user, ids):
    assert "no_valid_citation" in kinds(one_changed(0, clause_ids=ids))


def test_a_statement_missing_the_clause_ids_key_is_rejected(user):
    statements = statements_for_term()
    del statements[0]["clause_ids"]
    assert "no_valid_citation" in kinds(reply(statements))


def test_the_same_clause_cited_twice_in_one_statement_is_rejected(user):
    clause_id = clause(plan(), "3.1").id
    assert "duplicate_citation" in kinds(one_changed(0, clause_ids=[clause_id, clause_id]))


@pytest.mark.parametrize("text", [
    "You should keep this plan for life.", "We recommend this plan for families.", "Consider buying the critical illness rider.",
    "This is the best option for you.", "I advise you to compare other plans.",
])
def test_advice_wording_is_rejected(user, text):
    assert "advice_wording" in kinds(one_changed(0, text=text))


@pytest.mark.parametrize("count", [0, 1, 3, 9, 12])
def test_the_statement_count_must_be_four_to_eight(user, count):
    base = statements_for_term()
    statements = (base * 3)[:count]
    assert "statement_count" in kinds(reply(statements))
    assert problems_of(reply(statements_for_term()[:4])) == [] and problems_of(reply((statements_for_term() + statements_for_term())[:8])) == []


def test_text_length_is_capped(user):
    assert "text_too_long" in kinds(one_changed(0, text="Cover is described here. " * 20))
    assert "empty_text" in kinds(one_changed(0, text="   "))
    assert "empty_text" in kinds(one_changed(0, text=None))


@pytest.mark.parametrize("raw", ["not json", "[]", "null", '"x"', "{}", '{"statements": "x"}', '{"statements": {}}', None, ""])
def test_malformed_replies_are_rejected(user, raw):
    with pytest.raises(InvalidOutput):
        ps.validate_statements(raw, clauses_of(), plan().monthly_premium_cents)


def test_non_object_statements_are_rejected(user):
    assert "statement_not_an_object" in kinds(reply(statements_for_term()[:3] + ["just text"]))
    assert "statement_not_an_object" in kinds(reply(statements_for_term()[:3] + [None]))


def test_extra_keys_are_ignored_and_text_is_tidied(user):
    statements = statements_for_term()
    statements[0].update({"confidence": 0.9, "text": "  Cover   lasts until the Life Assured reaches age 65.  "})
    cleaned = ps.validate_statements(reply(statements), clauses_of(), plan().monthly_premium_cents)
    assert cleaned[0] == {"text": "Cover lasts until the Life Assured reaches age 65.", "clause_ids": statements[0]["clause_ids"]}


# ---------- rejected replies and failures fall back to the Standard summary ----------

def test_an_invented_figure_from_the_model_gives_the_standard_summary(user, fake_groq):
    FakeGroq.replies = [one_changed(2, text="The premium is S$95 a month and fixed for the whole policy term.")]
    page = view(user)
    assert "Standard summary" in page and "AI-generated summary" not in page
    assert "did not match the policy wording" in page and "The standard summary is shown." in page
    assert "S$95" not in page
    row = summary_row()
    assert row.source == "standard" and row.reason == "invalid_output"


def test_standard_summary_is_built_from_the_comparison_cells_and_their_clauses(user, fake_groq):
    FakeGroq.replies = ["not json"]
    page = view(user)
    cells = ComparisonCell.query.filter_by(policy_id=plan().id).all()
    for cell in cells:
        assert cell.finding in page, cell.row_key
    assert len(re.findall(r'<p class="stmt">', page)) == len(cells) == 6
    assert "Coverage duration: Cover runs to age 65" in page and "Cash value: No cash value." in page
    assert all(f"/citations/demo/{c.clause_id}?plan={plan().id}" in page for c in cells)
    from app.comparison import ROWS
    order = [key for key, _ in ROWS]
    expected = [[c.clause_id] for c in sorted(cells, key=lambda c: order.index(c.row_key))]
    assert [s["clause_ids"] for s in summary_row().statements] == expected          # comparison row order, each cites its clause


@pytest.mark.parametrize("error, reason", [
    (httpx.TimeoutException("x"), "error"),
    (groq.APITimeoutError(request=httpx.Request("POST", "https://example.invalid")), "timeout"),
    (groq.InternalServerError("boom", response=httpx.Response(500, request=httpx.Request("POST", "https://example.invalid")), body=None), "error"),
    (RuntimeError(f"sdk exploded with {KEY}"), "error"),
])
def test_provider_failures_give_the_standard_summary_and_never_leak_the_key(user, fake_groq, error, reason, caplog):
    caplog.set_level(logging.DEBUG)
    FakeGroq.error = error
    page = view(user)
    assert "Standard summary" in page and KEY not in page and KEY not in caplog.text
    assert summary_row().reason == reason


def test_a_busy_provider_gives_the_standard_summary_but_is_not_remembered(user, fake_groq):
    request = httpx.Request("POST", "https://example.invalid")
    FakeGroq.error = groq.RateLimitError("slow down", response=httpx.Response(429, request=request), body=None)
    page = view(user)
    assert "Standard summary" in page and "The AI service is busy right now" in page
    assert summary_row() is None                                     # not cached: the next view tries again
    FakeGroq.error = None
    FakeGroq.replies = [good_reply()]
    assert "AI-generated summary" in view(user)
    assert len(FakeGroq.calls()) == 2


def test_empty_reply_gives_the_standard_summary(user, fake_groq):
    FakeGroq.replies = ["   "]
    assert "Standard summary" in view(user) and summary_row().reason == "empty"


def test_missing_key_gives_the_standard_summary_without_calling_or_caching(user, monkeypatch):
    monkeypatch.setattr(ai_client, "Groq", FakeGroq)
    page = view(user)
    assert "Standard summary" in page and "not set up on this server" in page
    assert FakeGroq.instances == [] and summary_row() is None


@pytest.mark.parametrize("key, model", [("replace-with-your-groq-api-key", MODEL), (KEY, "replace-with-a-groq-model-id"), ("", MODEL), (KEY, "")])
def test_placeholder_or_blank_settings_count_as_missing(user, monkeypatch, key, model):
    monkeypatch.setenv("GROQ_API_KEY", key)
    monkeypatch.setenv("GROQ_MODEL", model)
    monkeypatch.setattr(ai_client, "Groq", FakeGroq)
    assert "Standard summary" in view(user) and FakeGroq.instances == []


def test_adding_the_key_later_works_immediately(user, monkeypatch):
    monkeypatch.setattr(ai_client, "Groq", FakeGroq)
    assert "Standard summary" in view(user)
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    monkeypatch.setenv("GROQ_MODEL", MODEL)
    FakeGroq.replies = [good_reply()]
    assert "AI-generated summary" in view(user)


def test_a_failed_generation_is_remembered_for_an_hour_then_retried(user, fake_groq):
    FakeGroq.error = groq.APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))
    view(user)
    view(user)
    assert len(FakeGroq.calls()) == 1                      # the second view did not call the provider again
    row = summary_row()
    row.created_at = row.created_at - timedelta(hours=2)
    db.session.commit()
    FakeGroq.error = None
    FakeGroq.replies = [good_reply()]
    assert "AI-generated summary" in view(user)
    assert len(FakeGroq.calls()) == 2 and summary_row().source == "ai"


def test_an_ai_summary_is_never_replaced_by_a_failure(user, fake_groq):
    FakeGroq.replies = [good_reply()]
    view(user)
    FakeGroq.error = RuntimeError("down")
    for _ in range(3):
        assert "AI-generated summary" in view(user)
    assert len(FakeGroq.calls()) == 1


def test_changing_the_plan_wording_or_premium_regenerates_the_summary(user, fake_groq):
    FakeGroq.replies = [good_reply()]
    view(user)
    clause(plan(), "4.4").text = clause(plan(), "4.4").text + " (updated wording)"
    db.session.commit()
    view(user)
    assert len(FakeGroq.calls()) == 2
    plan().monthly_premium_cents = 9000
    db.session.commit()
    FakeGroq.replies = [one_changed(2, text="The premium is S$90 a month and stays fixed for the whole policy term.")]
    view(user)
    assert len(FakeGroq.calls()) == 3 and "S$90" in json.dumps(summary_row().statements)


def test_summary_generation_is_rate_limited_per_user_and_the_fallback_is_not_cached(app, user, fake_groq):
    limiter = app.extensions["ai_summary_limiter"]
    clock = {"now": 100.0}
    limiter.limit, limiter.window, limiter.clock = 1, 60, lambda: clock["now"]
    FakeGroq.replies = [good_reply()]
    assert "AI-generated summary" in view(user)
    page = view(user, "PRUShield Premier")                       # uncached, and this user's allowance is used up
    assert "Standard summary" in page and "Too many summaries were requested just now" in page
    assert len(FakeGroq.calls()) == 1 and summary_row(name="PRUShield Premier") is None
    other = Browser(app.test_client())
    other.signup(email="limit2@example.com")
    other.onboard()
    shield = plan("PRUShield Premier")
    FakeGroq.replies = [reply([{"text": "Renews every year while premiums are paid.", "clause_ids": [clause(shield, "3.1").id]},
                               {"text": "Pays eligible hospital bills at a private hospital.", "clause_ids": [clause(shield, "4.1").id]},
                               {"text": "There is no death benefit.", "clause_ids": [clause(shield, "4.6").id]},
                               {"text": "Premiums are reviewed each year and may change.", "clause_ids": [clause(shield, "2.1").id]}])]
    assert "AI-generated summary" in view(other, "PRUShield Premier")          # a different user has their own allowance
    clock["now"] += 61
    assert "AI-generated summary" in view(user, "PRUShield Premier")            # cache hit now; no allowance needed


def test_cached_views_do_not_use_up_the_allowance(app, user, fake_groq):
    limiter = app.extensions["ai_summary_limiter"]
    limiter.limit, limiter.clock = 1, lambda: 100.0
    FakeGroq.replies = [good_reply()]
    for _ in range(5):
        assert "AI-generated summary" in view(user)
    assert len(FakeGroq.calls()) == 1


# ---------- languages ----------

def test_language_tabs_are_links_and_the_selected_one_is_marked(user, fake_groq):
    FakeGroq.replies = [good_reply()]
    page = view(user)
    tabs = re.findall(r'<a href="([^"]+)" lang="(\w+)"\s+class="lang-tab( is-active)?"[^>]*>([^<]+)</a>', page)
    assert [(lang, label) for _, lang, _, label in tabs] == [("en", "EN"), ("zh", "中文"), ("ms", "BM"), ("ta", "தமிழ்")]
    assert [bool(active) for _, _, active, _ in tabs] == [True, False, False, False]
    assert all("lang=" in href for href, lang, _, _ in tabs if lang != "en")


def test_a_valid_translation_is_accepted_cached_and_labelled(app, user, fake_groq):
    english = statements_for_term()
    FakeGroq.replies = [good_reply(), translated_reply(english)]
    page = view(user, lang="zh")
    assert "AI translation. Confirm details with your FC." in page and "译：Cover lasts until" in page
    assert 'lang="zh"' in page and "Translation unavailable" not in page
    assert [bool(m) for m in re.findall(r'class="lang-tab( is-active)?"', page)] == [False, True, False, False]
    assert len(FakeGroq.calls()) == 2
    row = summary_row("zh")
    assert row.source == "ai" and [s["clause_ids"] for s in row.statements] == [s["clause_ids"] for s in english]
    other = Browser(app.test_client())
    other.signup(email="zh2@example.com")
    other.onboard()
    assert "译：Cover lasts until" in view(other, lang="zh")
    assert len(FakeGroq.calls()) == 2                                     # cached for everyone
    sources = re.findall(r'<li id="src-\d+"><a href="/citations/demo/\d+\?plan=\d+">\[\d+\] p\.\d+, cl\.[\d.]+, line \d+</a>', page)
    assert len(sources) == 6                                              # the same cited clauses in every language


def test_the_translation_request_contains_only_the_validated_english_statements(user, fake_groq):
    english = statements_for_term()
    FakeGroq.replies = [good_reply(), translated_reply(english, "BM: ")]
    view(user, lang="ms")
    request = FakeGroq.calls()[1]
    sent = json.loads(request["messages"][1]["content"])
    assert sent == {"statements": english}
    assert "Malay" in request["messages"][0]["content"] and "digits stay as digits" in request["messages"][0]["content"].lower()
    for private in ("Taylor", "taylor@example.com", "Throwaway", KEY):
        assert private not in json.dumps(request)


@pytest.mark.parametrize("code, name", [("zh", "Chinese"), ("ms", "Malay"), ("ta", "Tamil")])
def test_each_language_is_translated_and_cached_separately(user, fake_groq, code, name):
    english = statements_for_term()
    FakeGroq.replies = [good_reply(), translated_reply(english, f"{code}: ")]
    page = view(user, lang=code)
    assert f"{code}: Cover lasts" in page and f'lang="{code}"' in page
    assert summary_row(code).source == "ai" and summary_row("en").source == "ai"
    assert name in FakeGroq.calls()[1]["messages"][0]["content"]


def break_digits(statements):
    changed = [dict(s) for s in statements]
    changed[0]["text"] = changed[0]["text"].replace("65", "56")
    return changed


def drop_digits(statements):
    changed = [dict(s) for s in statements]
    changed[2]["text"] = changed[2]["text"].replace("S$89", "S$")
    return changed


def add_digits(statements):
    changed = [dict(s) for s in statements]
    changed[1]["text"] += " 2"
    return changed


def drop_clause_id(statements):
    changed = [dict(s) for s in statements]
    changed[1]["clause_ids"] = []
    return changed


def swap_clause_id(statements):
    changed = [dict(s) for s in statements]
    changed[0]["clause_ids"] = [changed[1]["clause_ids"][0]]
    return changed


def add_clause_id(statements):
    changed = [dict(s) for s in statements]
    changed[0]["clause_ids"] = changed[0]["clause_ids"] + changed[1]["clause_ids"]
    return changed


def fewer_statements(statements):
    return [dict(s) for s in statements[:-1]]


def reordered(statements):
    return [dict(s) for s in reversed(statements)]


@pytest.mark.parametrize("mutate", [break_digits, drop_digits, add_digits, drop_clause_id, swap_clause_id,
                                    add_clause_id, fewer_statements, reordered])
def test_a_translation_that_changes_a_digit_or_a_clause_id_is_refused(user, fake_groq, mutate):
    english = statements_for_term()
    FakeGroq.replies = [good_reply(), reply(mutate(english))]
    page = view(user, lang="zh")
    assert "Translation unavailable, showing English" in page
    assert "Cover lasts until the Life Assured reaches age 65." in page and "AI translation" not in page
    assert 'lang="en"' in page and "AI-generated summary, checked against the policy wording" in page
    row = summary_row("zh")
    assert row.source == "standard" and row.statements == [] and row.reason == "invalid_output"
    view(user, lang="zh")
    assert len(FakeGroq.calls()) == 2                      # the refusal is remembered; no second attempt within the hour


@pytest.mark.parametrize("raw", ["not json", "{}", '{"statements": []}', None])
def test_unusable_translation_replies_show_english(user, fake_groq, raw):
    FakeGroq.replies = [good_reply(), raw]
    assert "Translation unavailable, showing English" in view(user, lang="ta")


def test_translation_failure_is_retried_after_an_hour_and_can_then_succeed(user, fake_groq):
    english = statements_for_term()
    FakeGroq.replies = [good_reply(), "not json"]
    view(user, lang="zh")
    row = summary_row("zh")
    row.created_at = row.created_at - timedelta(hours=2)
    db.session.commit()
    FakeGroq.replies = [translated_reply(english)]
    assert "AI translation. Confirm details with your FC." in view(user, lang="zh")


def test_translation_without_a_key_shows_english_with_the_note_and_is_not_cached(user, fake_groq, monkeypatch):
    FakeGroq.replies = [good_reply()]
    view(user)                                              # English cached while the key still exists
    monkeypatch.delenv("GROQ_API_KEY")
    page = view(user, lang="zh")
    assert "Translation unavailable, showing English" in page and summary_row("zh") is None
    assert len(FakeGroq.calls()) == 1


def test_translation_provider_failure_shows_english(user, fake_groq):
    FakeGroq.replies = [good_reply()]
    view(user)
    FakeGroq.error = groq.APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))
    assert "Translation unavailable, showing English" in view(user, lang="zh")
    assert summary_row("zh").reason == "timeout"


def test_a_translation_goes_stale_when_the_english_text_changes(user, fake_groq):
    english = statements_for_term()
    FakeGroq.replies = [good_reply(), translated_reply(english)]
    view(user, lang="zh")
    clause(plan(), "4.4").text += " (updated wording)"        # the plan changed: English and translation both regenerate
    db.session.commit()
    FakeGroq.replies = [good_reply(), translated_reply(english, "新：")]
    page = view(user, lang="zh")
    assert "新：Cover lasts" in page and len(FakeGroq.calls()) == 4


def test_a_standard_english_summary_can_still_be_translated(user, fake_groq):
    FakeGroq.replies = ["not json"]
    view(user)                                              # English falls back to the standard summary
    english = summary_row().statements
    FakeGroq.replies = [translated_reply(english, "译：")]
    page = view(user, lang="zh")
    assert "AI translation. Confirm details with your FC." in page and "译：Coverage duration" in page


def test_unknown_language_parameter_shows_english(user, fake_groq):
    FakeGroq.replies = [good_reply()]
    for lang in ("xx", "", "ZH", "zh-CN", "<script>"):
        page = view(user, lang=lang)
        assert 'lang="en"' in page and "Cover lasts until" in page
    assert len(FakeGroq.calls()) == 1


# ---------- Read Aloud ----------

def test_read_aloud_script_makes_no_network_requests():
    script = (Path(__file__).resolve().parent.parent / "app/static/js/plan-readaloud.js").read_text(encoding="utf-8")
    for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "navigator.", "import(", "http://", "https://", "<script"):
        assert forbidden not in script, forbidden
    assert "speechSynthesis" in script and "SpeechSynthesisUtterance" in script
    assert "No voice for this language is installed on your device." in script


def test_read_aloud_button_starts_disabled_and_carries_the_language(user, fake_groq):
    FakeGroq.replies = [good_reply(), translated_reply(statements_for_term())]
    page = view(user)
    assert re.search(r'<button type="button" class="btn btn-small btn-secondary" id="read-aloud" data-lang="en" disabled>Read aloud</button>', page)
    assert 'id="read-aloud-status" role="status"' in page
    assert 'data-lang="zh"' in view(user, lang="zh")
    FakeGroq.replies = ["not json"]
    unavailable = view(user, lang="ta")                               # translation refused: English text, so the English voice
    assert 'data-lang="en"' in unavailable and "Translation unavailable" in unavailable


def test_the_plan_page_keeps_the_strict_csp_and_has_no_inline_script_or_style(user, fake_groq):
    FakeGroq.replies = [good_reply()]
    response = user.get(f"/plans/{plan().id}")
    policy = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in policy and "unsafe-inline" not in policy and "unsafe-eval" not in policy
    page = response.get_data(as_text=True)
    assert not re.search(r"<script(?![^>]*\bsrc=)", page) and " style=" not in page
    assert re.findall(r'<script src="([^"]+)"', page) == ["/static/js/plan-readaloud.js"]
    assert "speechSynthesis" not in page                              # the server never speaks for the browser


# ---------- debug switch, logging and secrets ----------

def test_debug_logging_names_rejected_tokens_only_when_switched_on(user, fake_groq, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    FakeGroq.replies = [one_changed(2, text="The premium is S$95 a month, up 15% on last year.")]
    view(user)
    assert "AI_DEBUG" not in caplog.text
    PlanSummary.query.delete()
    db.session.commit()
    monkeypatch.setenv("AI_DEBUG", "1")
    view(user)
    assert "AI_DEBUG rejected plan summary" in caplog.text and "number_not_in_cited_clause" in caplog.text
    assert "'95'" in caplog.text and "'15%'" in caplog.text
    for secret in (KEY, MODEL, "Taylor", "taylor@example.com", "Do not add any number", "stays fixed"):
        assert secret not in caplog.text, secret


def test_logs_record_outcomes_only(user, fake_groq, caplog):
    caplog.set_level(logging.DEBUG)
    FakeGroq.replies = [good_reply(), translated_reply(statements_for_term())]
    view(user)
    view(user, lang="zh")
    assert "plan summary policy=" in caplog.text and "source=ai" in caplog.text
    for secret in (KEY, MODEL, "Taylor", "taylor@example.com", "Cover lasts", "premium", "clauses"):
        assert secret not in caplog.text, secret


def test_the_key_never_reaches_the_page_the_scripts_or_the_responses(user, fake_groq):
    FakeGroq.replies = [good_reply()]
    seen = [view(user), view(user, lang="zh"), view(user, "PRUShield Premier"), user.get("/plans").get_data(as_text=True)]
    seen.append(user.get("/static/js/plan-readaloud.js").get_data(as_text=True))
    for text in seen:
        assert KEY not in text and "GROQ_API_KEY" not in text and "gsk_" not in text


# ---------- generate-summaries ----------

def test_generate_summaries_fills_the_cache_for_every_plan_and_is_repeatable(app, seeded, fake_groq):
    # app/ai/cli.py still asks for "catalogue plans". Since the Prudential import that means plans without clauses,
    # so this test points it at the hidden demo plans (open item in tasks.md: summaries for catalogue plans, 6b).
    import app.ai.cli as ai_cli
    from app.catalogue import legacy_demo_policies
    ai_cli.catalogue_policies = legacy_demo_policies
    def reply_for(request_messages):
        sent = json.loads(request_messages[1]["content"])
        if "clauses" in sent:
            ids = [c["id"] for c in sent["clauses"]]
            texts = ["Cover is described in the policy.", "The plan has stated benefits.",
                     "The plan has stated limits.", "The plan has stated conditions."]
            return reply([{"text": t, "clause_ids": [ids[i]]} for i, t in enumerate(texts)])
        return reply([{"text": "译：" + s["text"], "clause_ids": s["clause_ids"]} for s in sent["statements"]])

    class Echo(FakeGroq):
        def _create(self, **kwargs):
            self.requests.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply_for(kwargs["messages"])))])

    ai_client.Groq = Echo
    result = app.test_cli_runner().invoke(args=["generate-summaries", "--language", "en", "--language", "zh"])
    assert result.exit_code == 0, result.output
    assert PlanSummary.query.filter_by(language="en", source="ai").count() == 6
    assert PlanSummary.query.filter_by(language="zh", source="ai").count() == 6
    calls = len(Echo.calls())
    assert calls == 12
    again = app.test_cli_runner().invoke(args=["generate-summaries", "--language", "en", "--language", "zh"])
    assert again.exit_code == 0 and len(Echo.calls()) == calls                          # nothing regenerated
    forced = app.test_cli_runner().invoke(args=["generate-summaries", "--language", "en", "--force"])
    assert forced.exit_code == 0 and len(Echo.calls()) == calls + 6


def test_generate_summaries_needs_a_key(app, seeded):
    result = app.test_cli_runner().invoke(args=["generate-summaries"])
    assert result.exit_code != 0 and "GROQ_API_KEY and GROQ_MODEL are not set" in result.output
    assert PlanSummary.query.count() == 0


def test_generate_summaries_rejects_an_unknown_language(app, seeded, fake_groq):
    assert app.test_cli_runner().invoke(args=["generate-summaries", "--language", "fr"]).exit_code != 0


# ---------- access ----------

def test_summaries_are_only_for_signed_in_finished_clients(app, seeded, fake_groq):
    anonymous = Browser(app.test_client())
    assert anonymous.get(f"/plans/{plan().id}?lang=zh").location.endswith("/login")
    assert FakeGroq.instances == []
    fresh = Browser(app.test_client())
    fresh.signup()
    assert fresh.get(f"/plans/{plan().id}").location.endswith("/onboarding")
    assert FakeGroq.instances == []
