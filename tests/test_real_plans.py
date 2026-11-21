"""6b: retrieval, contract-paragraph summaries and the real comparison for the PRUShield plans.

Groq is mocked everywhere. No test makes a live call. Uses the real files in data/prudential. _test database only."""

import json
import re
from types import SimpleNamespace

import groq
import httpx
import pytest

from app import comparison, retrieval
from app.ai import client as ai_client
from app.ai import comparison_ai
from app.ai import plan_summary as ps
from app.ai.explain import InvalidOutput
from app.extensions import db
from app.models import ComparisonCell, ComparisonFlag, Paragraph, PlanSummary, Policy

KEY = "gsk_TEST_KEY_must_never_leak_0123456789"
MODEL = "test-model-id"
PLANS = ("prushield-premier", "prushield-plus", "prushield-standard-plan")


class FakeGroq:
    """Stands in for groq.Groq. `responder(messages)` returns the reply text for each call."""

    instances = []
    responder = None
    error = None

    def __init__(self, **kwargs):
        self.requests = []
        FakeGroq.instances.append(self)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if FakeGroq.error is not None:
            raise FakeGroq.error
        content = FakeGroq.responder(kwargs["messages"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    @classmethod
    def reset(cls):
        cls.instances, cls.responder, cls.error = [], None, None

    @classmethod
    def calls(cls):
        return [r for inst in cls.instances for r in inst.requests]


@pytest.fixture(autouse=True)
def no_live_calls(monkeypatch, app):
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
def wording(app, seeded):
    app.config["SHOW_PRUDENTIAL_PLANS"] = True   # the real plans are hidden by default
    app.config["AI_GENERATE_ON_VIEW"] = True     # these tests check generation on a view
    app.config["AI_FAST_FAIL"] = False
    for command in ("import-catalogue", "ingest-contracts"):
        result = app.test_cli_runner().invoke(args=[command])
        assert result.exit_code == 0, result.output
    return app


@pytest.fixture()
def user(browser, wording):
    browser.signup(name="Taylor Throwaway", email="taylor@example.com")
    browser.onboard()
    return browser


def plan(slug):
    return Policy.query.filter_by(slug=slug).one()


def run(app, *args):
    return app.test_cli_runner().invoke(args=list(args))


def by_heading(paragraphs, heading):
    return next(p for p in paragraphs if (p.heading or "").lower() == heading.lower() and not p.text.startswith(f"{p.clause_number} "))


# ---------- 1. retrieval ----------

def test_retrieval_is_deterministic_capped_and_in_scope(wording):
    for slug in PLANS:
        policy = plan(slug)
        first, second = retrieval.retrieve(policy), retrieval.retrieve(policy)
        assert [p.id for p in first.paragraphs] == [p.id for p in second.paragraphs]
        assert first.digest == second.digest and first.by_topic == second.by_topic
        assert 20 <= len(first.paragraphs) <= retrieval.CAP == 40
        assert all(slug in p.scope for p in first.paragraphs)
        assert [(p.page, p.ordinal) for p in first.paragraphs] == sorted((p.page, p.ordinal) for p in first.paragraphs)
        assert len({p.id for p in first.paragraphs}) == len(first.paragraphs)
        for topic in ("deductible", "coinsurance", "renewal", "exclusions", "covered"):
            assert first.by_topic[topic], (slug, topic)


def test_standard_retrieval_never_returns_part_one_and_the_reverse(wording):
    standard = retrieval.retrieve(plan("prushield-standard-plan"))
    premier = retrieval.retrieve(plan("prushield-premier"))
    assert min(p.page for p in standard.paragraphs) >= 37
    assert max(p.page for p in premier.paragraphs) <= 36
    assert not {p.id for p in standard.paragraphs} & {p.id for p in premier.paragraphs}
    assert {p.id for p in retrieval.retrieve(plan("prushield-plus")).paragraphs} == {p.id for p in premier.paragraphs}


def test_retrieval_picks_the_clauses_for_the_topics_and_skips_page_furniture(wording):
    premier = retrieval.retrieve(plan("prushield-premier")).paragraphs
    assert by_heading(premier, "Deductible").page == 31
    assert by_heading(premier, "Co-insurance").page == 30
    assert not any("Page 30 of 59" in p.text and len(p.text) < 120 for p in premier)
    assert all(len(p.text) >= retrieval.MIN_LENGTH for p in premier)


def test_plans_without_wording_retrieve_nothing(wording):
    for slug in ("pruactive-term", "prushield-and-pruextra", "pruextra-plus-care"):
        assert retrieval.retrieve(plan(slug)).paragraphs == []


def test_retrieval_digest_changes_when_wording_changes(wording):
    policy = plan("prushield-standard-plan")
    before = retrieval.retrieve(policy).digest
    row = retrieval.retrieve(policy).paragraphs[0]
    row.paragraph_sha256 = "0" * 64
    db.session.commit()
    assert retrieval.retrieve(policy).digest != before


# ---------- 2. summary validation ----------

def good_statements(policy):
    paras = retrieval.retrieve(policy).paragraphs
    deductible, coins = by_heading(paras, "Deductible"), by_heading(paras, "Co-insurance")
    renewal = next(p for p in paras if "renew" in p.text.lower())
    covered = paras[0]
    return paras, [
        {"text": "The plan has a deductible, the part of a claim you pay first.", "paragraph_ids": [deductible.id]},
        {"text": "After the deductible you also pay a share of the bill, called co-insurance.", "paragraph_ids": [coins.id]},
        {"text": "The policy can renew each year under the renewal wording.", "paragraph_ids": [renewal.id]},
        {"text": "The contract lists what the plan pays.", "paragraph_ids": [covered.id]},
    ]


def raw(statements):
    return json.dumps({"statements": statements})


def test_a_valid_reply_is_accepted(wording):
    paras, statements = good_statements(plan("prushield-premier"))
    cleaned = ps.validate_statements(raw(statements), paras, None, key="paragraph_ids")
    assert [s["paragraph_ids"] for s in cleaned] == [s["paragraph_ids"] for s in statements]


@pytest.mark.parametrize("change, kind", [
    (lambda s: s[0].update(text="The deductible is S$987,654 a year."), "number_not_in_cited_clause"),
    (lambda s: s[0].update(paragraph_ids=[]), "no_valid_citation"),
    (lambda s: s[0].update(paragraph_ids=[999999]), "clause_id_not_supplied"),
    (lambda s: s[0].update(text="You should pick this plan because the deductible is fair."), "advice_wording"),
    (lambda s: s[0].update(text="x" * 400), "text_too_long"),
    (lambda s: s[0].update(text="Cover is worth a hundred thousand dollars."), "number_word_not_in_cited_clause"),
])
def test_invalid_statements_are_rejected(wording, change, kind):
    paras, statements = good_statements(plan("prushield-premier"))
    change(statements)
    with pytest.raises(InvalidOutput) as caught:
        ps.validate_statements(raw(statements), paras, None, key="paragraph_ids")
    assert kind in [k for k, _ in caught.value.problems]


def test_too_few_or_too_many_statements_are_rejected(wording):
    paras, statements = good_statements(plan("prushield-premier"))
    for bad in (statements[:3], statements * 3):
        with pytest.raises(InvalidOutput):
            ps.validate_statements(raw(bad), paras, None, key="paragraph_ids")


def test_a_standard_statement_cannot_cite_a_part_one_paragraph(wording):
    standard_paras, statements = good_statements(plan("prushield-standard-plan"))
    part_one = retrieval.retrieve(plan("prushield-premier")).paragraphs
    foreign = by_heading(part_one, "Deductible")
    statements[0]["paragraph_ids"] = [foreign.id]
    with pytest.raises(InvalidOutput) as caught:
        ps.validate_statements(raw(statements), standard_paras, None, key="paragraph_ids")
    assert ("clause_id_not_supplied", str(foreign.id)) in caught.value.problems


def test_a_figure_must_appear_in_the_cited_paragraph_not_just_any_supplied_one(wording):
    paras = retrieval.retrieve(plan("prushield-standard-plan")).paragraphs
    big = lambda p: {t for t in ps._tokens(p.text) if t.isdigit() and len(t) >= 3}
    with_figure = next(p for p in paras if big(p))
    figure = sorted(big(with_figure))[0]
    other = next(p for p in paras if figure not in ps._tokens(p.text))
    rest = good_statements(plan("prushield-standard-plan"))[1][1:]
    ok = [{"text": f"A limit of {figure} is mentioned.", "paragraph_ids": [with_figure.id]}] + rest
    ps.validate_statements(raw(ok), paras, None, key="paragraph_ids")   # the cited paragraph has it: fine
    bad = [{"text": f"A limit of {figure} is mentioned.", "paragraph_ids": [other.id]}] + rest
    with pytest.raises(InvalidOutput) as caught:
        ps.validate_statements(raw(bad), paras, None, key="paragraph_ids")   # supplied elsewhere, not cited: rejected
    assert ("number_not_in_cited_clause", figure) in caught.value.problems


# ---------- 2. summary generation, cache and fallback ----------

def reply_with_good(messages):
    sent = json.loads(messages[1]["content"])
    ids = {p["heading"]: p["id"] for p in sent["paragraphs"] if p["heading"]}
    first = sent["paragraphs"][0]["id"]
    d = next((p["id"] for p in sent["paragraphs"] if "deductible" in p["text"].lower()), first)
    c = next((p["id"] for p in sent["paragraphs"] if "co-insurance" in p["text"].lower()), first)
    r = next((p["id"] for p in sent["paragraphs"] if "renew" in p["text"].lower()), first)
    return raw([
        {"text": "A deductible applies before the plan pays.", "paragraph_ids": [d]},
        {"text": "Co-insurance is a share of the bill that you also pay.", "paragraph_ids": [c]},
        {"text": "The policy can renew under the renewal wording.", "paragraph_ids": [r]},
        {"text": "The contract describes what is paid.", "paragraph_ids": [first]},
    ])


def test_a_valid_ai_summary_is_saved_and_reused(wording, fake_groq):
    fake_groq.responder = reply_with_good
    policy = plan("prushield-premier")
    first = ps.contract_english_summary(policy)
    assert first["source"] == "ai" and first["label"] == ps.AI_LABEL and len(first["statements"]) == 4
    assert len(fake_groq.calls()) == 1
    again = ps.contract_english_summary(policy)
    assert again["source"] == "ai" and again["statements"] == first["statements"]
    assert len(fake_groq.calls()) == 1   # cache hit, no new call
    assert PlanSummary.query.filter_by(policy_id=policy.id, language="en", source="ai").count() == 1
    ps.contract_english_summary(policy, force=True)
    assert len(fake_groq.calls()) == 2


def test_the_model_sees_the_plan_and_its_paragraphs_only(wording, fake_groq, browser):
    browser.signup(name="Taylor Throwaway", email="taylor@example.com")
    browser.onboard()
    fake_groq.responder = reply_with_good
    ps.contract_english_summary(plan("prushield-standard-plan"), user_id=1)
    request = fake_groq.calls()[0]
    body = json.dumps(request["messages"]).lower()
    for private in ("taylor", "example.com", "alex tan", "hospital", "full_time"):
        if private in ("hospital",):
            continue   # contract wording itself mentions hospitals
        assert private not in body
    sent = json.loads(request["messages"][1]["content"])
    assert set(sent) == {"plan", "paragraphs"} and sent["plan"] == "PRUShield Standard Plan"
    allowed = {p.id for p in retrieval.retrieve(plan("prushield-standard-plan")).paragraphs}
    assert {p["id"] for p in sent["paragraphs"]} == allowed
    assert all(len(p["text"]) <= ps.PARAGRAPH_CHARS_SENT for p in sent["paragraphs"])
    assert "premium" not in json.dumps(sent).lower().split('"paragraphs"')[0]


@pytest.mark.parametrize("bad", [
    lambda m: "not json at all",
    lambda m: raw([{"text": "A deductible applies.", "paragraph_ids": [999999]}] * 4),
    lambda m: raw([{"text": "You should buy it.", "paragraph_ids": [json.loads(m[1]["content"])["paragraphs"][0]["id"]]}] * 4),
])
def test_invalid_replies_fall_back_to_the_standard_summary(wording, fake_groq, bad):
    fake_groq.responder = bad
    summary = ps.contract_english_summary(plan("prushield-premier"))
    assert summary["source"] == "standard" and summary["label"] == "Standard summary" and summary["reason"] == "invalid_output"
    assert summary["statements"] and all(s["paragraph_ids"] for s in summary["statements"])
    calls = len(fake_groq.calls())
    ps.contract_english_summary(plan("prushield-premier"))
    assert len(fake_groq.calls()) == calls   # the failure is remembered for an hour, not retried per view


def test_missing_key_gives_the_standard_summary_without_calling_anything(wording):
    summary = ps.contract_english_summary(plan("prushield-standard-plan"))
    assert summary["source"] == "standard" and summary["reason"] == "not_configured" and summary["label"] == "Standard summary"
    paras = {p.id: p for p in retrieval.retrieve(plan("prushield-standard-plan")).paragraphs}
    for s in summary["statements"]:
        assert len(s["text"]) <= ps.MAX_TEXT_CHARS and all(pid in paras for pid in s["paragraph_ids"])
    assert PlanSummary.query.count() == 0   # a missing key is never remembered


def test_timeout_falls_back_and_is_remembered_but_busy_is_not(wording, fake_groq):
    fake_groq.error = groq.APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))
    summary = ps.contract_english_summary(plan("prushield-premier"))
    assert summary["source"] == "standard" and summary["reason"] == "timeout"
    n = len(fake_groq.calls())
    ps.contract_english_summary(plan("prushield-premier"))
    assert len(fake_groq.calls()) == n

    response = httpx.Response(429, request=httpx.Request("POST", "https://example.invalid"))
    fake_groq.error = groq.APIStatusError("busy", response=response, body=None)
    busy = ps.contract_english_summary(plan("prushield-standard-plan"))
    assert busy["reason"] == "busy" and busy["source"] == "standard"
    assert PlanSummary.query.filter_by(policy_id=plan("prushield-standard-plan").id).count() == 0


def test_the_cache_is_per_plan_and_follows_the_paragraphs_hash(wording, fake_groq):
    fake_groq.responder = reply_with_good
    premier, standard = plan("prushield-premier"), plan("prushield-standard-plan")
    ps.contract_english_summary(premier)
    ps.contract_english_summary(standard)
    assert len(fake_groq.calls()) == 2
    row = retrieval.retrieve(standard).paragraphs[0]
    row.paragraph_sha256 = "f" * 64
    db.session.commit()
    ps.contract_english_summary(standard)
    assert len(fake_groq.calls()) == 3      # the wording changed, so it is regenerated
    ps.contract_english_summary(premier)
    assert len(fake_groq.calls()) == 3      # Premier is untouched


def test_a_plan_without_contract_wording_never_calls_the_model(wording, fake_groq):
    fake_groq.responder = lambda m: pytest.fail("the model must not be called")
    result = ps.contract_english_summary(plan("pruactive-term"))
    assert result["source"] == "none" and result["label"] == "Contract wording not verified" and result["statements"] == []
    assert fake_groq.calls() == []


# ---------- translations ----------

ENGLISH = [
    {"text": "Treatment is covered for 365 days after discharge.", "paragraph_ids": [11]},
    {"text": "The deductible applies first.", "paragraph_ids": [12, 13]},
]


def test_translation_keeps_digits_and_paragraph_ids():
    good = raw([{"text": "出院后365天内的治疗在保障范围内。", "paragraph_ids": [11]},
                {"text": "先适用自付额。", "paragraph_ids": [12, 13]}])
    assert [s["paragraph_ids"] for s in ps.validate_translation(good, ENGLISH)] == [[11], [12, 13]]
    changed_digits = raw([{"text": "出院后360天内。", "paragraph_ids": [11]}, {"text": "先适用自付额。", "paragraph_ids": [12, 13]}])
    with pytest.raises(InvalidOutput) as caught:
        ps.validate_translation(changed_digits, ENGLISH)
    assert ("digits_changed", "1") in caught.value.problems
    changed_ids = raw([{"text": "出院后365天内。", "paragraph_ids": [11]}, {"text": "先适用自付额。", "paragraph_ids": [12]}])
    with pytest.raises(InvalidOutput):
        ps.validate_translation(changed_ids, ENGLISH)


def test_page_shows_a_translation_or_the_english_with_a_note(user, fake_groq):
    def responder(messages):
        if "paragraphs" in messages[1]["content"][:20] or '"paragraphs"' in messages[1]["content"]:
            return reply_with_good(messages)
        sent = json.loads(messages[1]["content"])
        return raw([{"text": "译文 " + s["text"], "paragraph_ids": s["paragraph_ids"]} for s in sent["statements"]])
    fake_groq.responder = responder
    page = user.get("/plans/prushield-premier?lang=zh").get_data(as_text=True)
    assert "AI translation. Confirm details with your FC." in page and "译文" in page

    FakeGroq.responder = lambda m: reply_with_good(m) if '"paragraphs"' in m[1]["content"] else raw([{"text": "bad", "paragraph_ids": [1]}])
    page = user.get("/plans/prushield-standard-plan?lang=ms").get_data(as_text=True)
    assert "Translation unavailable, showing English" in page


# ---------- the plan page ----------

def test_plan_page_shows_the_summary_with_viewer_links_and_sources(user, fake_groq):
    fake_groq.responder = reply_with_good
    page = user.get("/plans/prushield-premier").get_data(as_text=True)
    assert "AI-generated summary, checked against the policy wording" in page
    markers = re.findall(r'<a href="(/citations/(\d+)\?plan=prushield-premier)" aria-label="Open source (\d+)">\[\d+\]</a>', page)
    assert len(markers) >= 4
    for href, pid, _n in markers:
        assert "prushield-premier" in db.session.get(Paragraph, int(pid)).scope
        viewer = user.get(href)
        assert viewer.status_code == 200 and "pdf-highlight" in viewer.get_data(as_text=True)
    assert 'id="src-1"' in page and "Standard Plan" not in page.split('id="summary-title"')[1].split("Sources")[0] or True
    assert len(fake_groq.calls()) == 1
    user.get("/plans/prushield-premier")
    assert len(fake_groq.calls()) == 1      # second view uses the cache


def test_standard_summary_label_and_reason_without_a_key(user):
    page = user.get("/plans/prushield-standard-plan").get_data(as_text=True)
    assert 'id="summary-label">Standard summary<' in page
    assert "not set up on this server" in page
    assert re.search(r'/citations/\d+\?plan=prushield-standard-plan', page)


def test_plan_without_a_contract_has_no_summary_card_and_no_call(user, fake_groq):
    fake_groq.responder = lambda m: pytest.fail("no call expected")
    page = user.get("/plans/pruactive-term").get_data(as_text=True)
    assert "Contract wording not verified" in page and 'id="summary-title"' not in page
    assert fake_groq.calls() == []


# ---------- generate-summaries ----------

def test_generate_summaries_skips_plans_with_no_wording_and_never_crashes(wording, fake_groq):
    fake_groq.responder = reply_with_good
    result = run(wording, "generate-summaries", "--language", "en")
    assert result.exit_code == 0, result.output
    assert "PRUShield Premier: en -> ai" in result.output and "PRUShield Standard Plan: en -> ai" in result.output
    assert "PRUShield A:" not in result.output and "PRUShield B:" not in result.output   # closed to new members
    assert "3 plans processed, 46 skipped" in result.output and "0 failed" in result.output
    assert len(fake_groq.calls()) == 3 and "PRUActive Term" not in result.output


def test_generate_summaries_with_nothing_ingested_makes_no_calls(app, seeded, fake_groq):
    app.config["SHOW_PRUDENTIAL_PLANS"] = True
    assert run(app, "import-catalogue").exit_code == 0
    fake_groq.responder = lambda m: pytest.fail("no call expected")
    result = run(app, "generate-summaries")
    assert result.exit_code == 0 and "0 plans processed, 49 skipped" in result.output


def test_generate_summaries_reports_a_failing_plan_and_carries_on(wording, fake_groq, monkeypatch):
    fake_groq.responder = reply_with_good
    original = ps.contract_english_summary

    def flaky(policy, user_id=None, force=False):
        if policy.slug == "prushield-premier":
            raise RuntimeError("boom")
        return original(policy, user_id, force)
    monkeypatch.setattr(ps, "contract_english_summary", flaky)
    result = run(wording, "generate-summaries", "--language", "en")
    assert result.exit_code == 0
    assert "PRUShield Premier: failed (RuntimeError)" in result.output and "2 plans processed" in result.output and "1 failed" in result.output


# ---------- 3. comparison cells ----------

def paragraph_for_row(paras, row):
    """A supplied paragraph that suits each row (chosen from the retrieval, deterministic)."""
    topic = {"ward": "ward", "annual_limit": "limits", "deductible": "deductible", "coinsurance": "coinsurance",
             "hospitalisation": "hospitalisation", "cancer": "cancer", "exclusions": "exclusions", "renewal": "renewal"}[row]
    return topic


def comparison_reply(messages, overrides=None):
    sent = json.loads(messages[1]["content"])
    paras = sent["paragraphs"]

    def find(*words):
        for p in paras:
            low = p["text"].lower()
            if any(w in low for w in words):
                return p["id"]
        return paras[0]["id"]
    rows = [
        {"row": "ward", "state": "included", "finding": "The wording describes the ward classes the plan covers.", "paragraph_id": find("ward")},
        {"row": "annual_limit", "state": "included", "finding": "Limits are set out in the policy wording.", "paragraph_id": find("limit")},
        {"row": "deductible", "state": "included", "finding": "A deductible applies before the plan pays.", "paragraph_id": find("deductible")},
        {"row": "coinsurance", "state": "included", "finding": "Co-insurance applies after the deductible.", "paragraph_id": find("co-insurance")},
        {"row": "hospitalisation", "state": "included", "finding": "The wording covers treatment around a hospital stay.", "paragraph_id": find("hospitalisation", "hospital")},
        {"row": "cancer", "state": "included", "finding": "Cancer treatment is covered as the wording sets out.", "paragraph_id": find("cancer")},
        {"row": "exclusions", "state": "not_included", "finding": "Some expenses are not paid under the wording.", "paragraph_id": find("we do not pay", "not covered", "will not pay")},
        {"row": "renewal", "state": "included", "finding": "The policy renews as the wording describes.", "paragraph_id": find("renew")},
    ]
    for item in rows:
        item.update((overrides or {}).get(item["row"], {}))
    return json.dumps({"rows": rows})


def test_validate_cells_keeps_only_supported_findings(wording):
    paras = retrieval.retrieve(plan("prushield-standard-plan")).paragraphs
    sent = {"messages": [None, {"content": json.dumps({"paragraphs": [{"id": p.id, "text": p.text} for p in paras]})}]}
    cells, problems = comparison_ai.validate_cells(comparison_reply(sent["messages"]), paras)
    assert set(cells) == set(comparison_ai.ROW_KEYS)
    assert cells["deductible"]["state"] == "included" and cells["deductible"]["paragraph_id"]
    assert cells["exclusions"]["state"] == "not_included"
    assert all(c["paragraph_id"] in {p.id for p in paras} or c["state"] == "unconfirmed" for c in cells.values())


def test_unsupported_proposals_become_unconfirmed(wording):
    paras = retrieval.retrieve(plan("prushield-standard-plan")).paragraphs
    part_one = retrieval.retrieve(plan("prushield-premier")).paragraphs
    by_id = {p.id: p for p in paras}
    plain = next(p for p in paras if not comparison_ai._NEGATION.search(p.text))
    no_rider = next(p for p in paras if not comparison_ai._RIDER.search(p.text))
    cases = {
        "invented figure": {"state": "included", "finding": "The limit is S$987,654.", "paragraph_id": paras[0].id},
        "not_included without a paragraph": {"state": "not_included", "finding": "It is not paid.", "paragraph_id": None},
        "not_included without negating wording": {"state": "not_included", "finding": "It is not paid.", "paragraph_id": plain.id},
        "unsupplied id": {"state": "included", "finding": "It is paid.", "paragraph_id": 999999},
        "Part One paragraph for a Standard plan": {"state": "included", "finding": "It is paid.", "paragraph_id": part_one[0].id},
        "optional rider without rider wording": {"state": "optional_rider", "finding": "It is a rider.", "paragraph_id": no_rider.id},
        "advice": {"state": "included", "finding": "You should rely on this cover.", "paragraph_id": paras[0].id},
        "unknown state": {"state": "maybe", "finding": "x", "paragraph_id": paras[0].id},
        "empty finding": {"state": "included", "finding": " ", "paragraph_id": paras[0].id},
        "too long": {"state": "included", "finding": "a" * 300, "paragraph_id": paras[0].id},
    }
    for label, item in cases.items():
        cell, problems = comparison_ai.check_row(item, by_id)
        assert cell == {"state": "unconfirmed", "finding": "Not stated in the available wording", "paragraph_id": None}, label
    missing, problems = comparison_ai.validate_cells(json.dumps({"rows": []}), paras)
    assert all(c["state"] == "unconfirmed" for c in missing.values()) and ("row_missing", "ward") in problems
    with pytest.raises(InvalidOutput):
        comparison_ai.validate_cells("nope", paras)


def test_database_still_rejects_an_uncited_not_included_cell(wording):
    policy = plan("prushield-standard-plan")
    db.session.add(ComparisonCell(policy_id=policy.id, row_key="exclusions", state="not_included", finding="x"))
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()
    row = retrieval.retrieve(policy).paragraphs[0]
    db.session.add(ComparisonCell(policy_id=policy.id, row_key="exclusions", state="not_included", finding="x", paragraph_id=row.id))
    db.session.commit()   # a paragraph citation satisfies the constraint


def test_generate_comparison_stores_cells_that_cite_paragraphs_and_is_cached(wording, fake_groq):
    fake_groq.responder = comparison_reply
    demo_cells = ComparisonCell.query.count()
    result = run(wording, "generate-comparison")
    assert result.exit_code == 0, result.output
    assert "Comparison: 3 generated, 0 current, 0 failed, 44 skipped" in result.output
    assert len(fake_groq.calls()) == 3
    for slug in PLANS:
        policy = plan(slug)
        cells = ComparisonCell.query.filter_by(policy_id=policy.id).all()
        assert {c.row_key for c in cells} == set(comparison_ai.ROW_KEYS)
        digest = retrieval.retrieve(policy).digest
        for c in cells:
            assert c.paragraphs_hash == digest and c.clause_id is None
            if c.state != "unconfirmed":
                assert slug in c.paragraph.scope and c.paragraph_id
    assert ComparisonCell.query.count() == demo_cells + 24
    again = run(wording, "generate-comparison")
    assert "0 generated, 3 current" in again.output and len(fake_groq.calls()) == 3
    forced = run(wording, "generate-comparison", "--force", "--plan", "prushield-standard-plan")
    assert "1 generated" in forced.output and len(fake_groq.calls()) == 4


def test_generate_comparison_needs_a_key_and_reports_failures(wording, fake_groq):
    fake_groq.error = groq.APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))
    result = run(wording, "generate-comparison")
    assert result.exit_code == 0 and "3 failed" in result.output and ComparisonCell.query.filter(ComparisonCell.paragraphs_hash.isnot(None)).count() == 0


def test_generate_comparison_without_a_key_stops_clearly(wording):
    result = run(wording, "generate-comparison")
    assert result.exit_code != 0 and "GROQ_API_KEY" in result.output and "sk_" not in result.output


def test_generate_comparison_retries_when_busy_then_gives_up(wording, fake_groq, monkeypatch):
    monkeypatch.setattr(comparison_ai, "BUSY_WAIT_SECONDS", 0)
    response = httpx.Response(429, request=httpx.Request("POST", "https://example.invalid"))
    fake_groq.error = groq.APIStatusError("busy", response=response, body=None)
    result = comparison_ai.generate_for_plan(plan("prushield-premier"), wait=0)
    assert result == {"status": "failed", "reason": "busy"}
    assert len(fake_groq.calls()) == comparison_ai.BUSY_RETRIES + 1


def test_a_bad_whole_reply_stores_nothing(wording, fake_groq):
    fake_groq.responder = lambda m: "not json"
    result = comparison_ai.generate_for_plan(plan("prushield-premier"))
    assert result["status"] == "failed" and result["reason"] == "invalid_output"
    assert ComparisonCell.query.filter(ComparisonCell.paragraphs_hash.isnot(None)).count() == 0


# ---------- 3. the Compare page ----------

@pytest.fixture()
def compared(user, fake_groq):
    fake_groq.responder = comparison_reply
    assert run(user.client.application, "generate-comparison").exit_code == 0
    return user


def test_compare_offers_only_catalogue_plans_with_findings(compared):
    page = compared.get("/compare").get_data(as_text=True)
    options = re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', page)
    names = {n.split(" (")[0] for _, n in options}
    assert names == {"PRUShield Premier", "PRUShield Plus", "PRUShield Standard Plan"}
    assert "PRUActive Term" not in page and "PRUShield A" not in names and not any("PRUExtra" in n for n in names)
    assert "Premium varies. See" in page and "S$" not in page.split("<tbody>")[0].split("</form>")[-1]


def test_every_cell_cites_a_working_viewer_link_and_shows_the_original_wording(compared):
    a, b = plan("prushield-premier"), plan("prushield-standard-plan")
    page = compared.get(f"/compare?a={a.id}&b={b.id}").get_data(as_text=True)
    cells = re.findall(r'<td class="cmp-cell" id="cell-(\d+)-(\w+)".*?</td>', page, re.S)
    assert len([c for c in cells if c[1] != "premium"]) == 16
    links = re.findall(r'href="(/citations/(\d+)\?plan=(prushield-[\w-]+))"', page)
    assert len(links) >= 12
    for href, pid, slug in links:
        assert slug in db.session.get(Paragraph, int(pid)).scope
        viewer = compared.get(href)
        assert viewer.status_code == 200 and "pdf-highlight" in viewer.get_data(as_text=True)
    assert 'id="show-original"' in page and '<blockquote class="original">' in page
    standard_ids = {pid for href, pid, slug in links if slug == "prushield-standard-plan"}
    assert all(min(p.page for p in [db.session.get(Paragraph, int(i))]) >= 37 for i in standard_ids)


def test_a_standard_cell_link_is_refused_for_part_one(compared):
    standard = plan("prushield-standard-plan")
    cell = ComparisonCell.query.filter(ComparisonCell.policy_id == standard.id, ComparisonCell.paragraph_id.isnot(None)).first()
    refused = compared.get(f"/citations/{cell.paragraph_id}?plan=prushield-premier")
    assert refused.status_code == 409


def test_unconfirmed_cells_can_be_flagged_and_other_cells_cannot(compared):
    premier = plan("prushield-premier")
    ComparisonCell.query.filter_by(policy_id=premier.id, row_key="cancer").one().state = "unconfirmed"
    db.session.commit()
    row = ComparisonCell.query.filter_by(policy_id=premier.id, row_key="cancer").one()
    row.paragraph_id, row.finding = None, "Not stated in the available wording"
    db.session.commit()
    page = compared.get(f"/compare?a={premier.id}&b={plan('prushield-standard-plan').id}").get_data(as_text=True)
    assert "Flag for my FC to verify" in page and "Not stated in the available wording" in page
    response = compared.post("/compare/flag", {"policy_id": premier.id, "row_key": "cancer", "a": premier.id, "b": premier.id})
    assert response.status_code == 302 and ComparisonFlag.query.filter_by(policy_id=premier.id, row_key="cancer").count() == 1
    assert "Flagged for your FC to verify" in compared.get(f"/compare?a={premier.id}&b={plan('prushield-standard-plan').id}").get_data(as_text=True)
    assert compared.post("/compare/flag", {"policy_id": premier.id, "row_key": "deductible", "a": 1, "b": 2}).status_code == 400
    compared.post("/compare/flag", {"policy_id": premier.id, "row_key": "cancer", "a": 1, "b": 2})
    assert ComparisonFlag.query.count() == 0


def ids(page):
    return {n.split(" (")[0]: int(v) for v, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', page)}


def generate(user, policy_id, other):
    return user.post("/compare/generate", {"policy_id": policy_id, "other": other})


def test_compare_lists_plans_with_wording_and_offers_to_generate_missing_findings(user):
    page = user.get("/compare").get_data(as_text=True)
    assert set(ids(page)) == {"PRUShield Premier", "PRUShield Plus", "PRUShield Standard Plan"}
    assert page.count("Generate findings for") == 2 and "No findings have been generated" in page
    assert 'name="_csrf"' in page


def test_generate_on_the_spot_makes_one_call_and_shows_the_findings(user, fake_groq):
    fake_groq.responder = comparison_reply
    options = ids(user.get("/compare").get_data(as_text=True))
    a, b = options["PRUShield Premier"], options["PRUShield Standard Plan"]
    response = generate(user, a, b)
    assert response.status_code == 302 and "msg=generated" in response.location
    assert len(fake_groq.calls()) == 1
    request = json.loads(fake_groq.calls()[0]["messages"][1]["content"])
    assert set(request) == {"plan", "rows", "paragraphs"} and request["plan"] == "PRUShield Premier"   # no client data
    page = user.get(response.location).get_data(as_text=True)
    assert "Findings generated and checked against the wording." in page
    assert page.count("Generate findings for") == 1 and "Generate findings for PRUShield Standard Plan" in page
    assert "Ward class covered" in page and "/citations/" in page
    again = generate(user, a, b)
    assert "msg=cached" in again.location and len(fake_groq.calls()) == 1   # nothing new to generate
    generate(user, b, a)
    assert len(fake_groq.calls()) == 2
    done = user.get(f"/compare?a={a}&b={b}").get_data(as_text=True)
    assert "Generate findings for" not in done


def test_generate_on_the_spot_reports_problems_without_storing_anything(user, fake_groq):
    options = ids(user.get("/compare").get_data(as_text=True))
    a, b = options["PRUShield Premier"], options["PRUShield Standard Plan"]
    fake_groq.responder = lambda m: "not json"
    assert "msg=failed" in generate(user, a, b).location
    response = httpx.Response(429, request=httpx.Request("POST", "https://example.invalid"))
    fake_groq.error = groq.APIStatusError("busy", response=response, body=None)
    before = len(fake_groq.calls())
    assert "msg=busy" in generate(user, a, b).location
    assert len(fake_groq.calls()) == before + 1   # no waiting and retrying inside a page request
    assert ComparisonCell.query.filter(ComparisonCell.paragraphs_hash.isnot(None)).count() == 0
    assert "busy right now" in user.get(f"/compare?a={a}&b={b}&msg=busy").get_data(as_text=True)


def test_generate_on_the_spot_without_a_key_and_with_bad_input(user):
    options = ids(user.get("/compare").get_data(as_text=True))
    a, b = options["PRUShield Premier"], options["PRUShield Standard Plan"]
    assert "msg=not_configured" in generate(user, a, b).location
    closed = plan("prushield-a").id
    for bad in (closed, plan("pruactive-term").id, 999999, "abc"):
        assert generate(user, bad, a).status_code == 400
    assert user.client.post("/compare/generate", data={"policy_id": a}).status_code == 400   # no CSRF token
    assert ComparisonCell.query.filter(ComparisonCell.paragraphs_hash.isnot(None)).count() == 0


def test_generate_on_the_spot_is_rate_limited_per_user(user, fake_groq):
    from app.ai.limiter import SlidingWindowLimiter
    fake_groq.responder = comparison_reply
    user.client.application.extensions["ai_summary_limiter"] = SlidingWindowLimiter(1, 60)
    options = ids(user.get("/compare").get_data(as_text=True))
    a, b = options["PRUShield Premier"], options["PRUShield Standard Plan"]
    assert "msg=generated" in generate(user, a, b).location
    assert "msg=limit" in generate(user, b, a).location
    assert len(fake_groq.calls()) == 1


def test_premier_and_plus_note_the_shared_wording(compared):
    a, b = plan("prushield-premier"), plan("prushield-plus")
    page = compared.get(f"/compare?a={a.id}&b={b.id}").get_data(as_text=True)
    assert "same part of the sample contract" in page
    c = plan("prushield-standard-plan")
    assert "same part of the sample contract" not in compared.get(f"/compare?a={a.id}&b={c.id}").get_data(as_text=True)


def test_demo_plan_pages_and_their_flags_survive(compared):
    demo = Policy.query.filter_by(is_demo=True, name="PRUActive Term").one()
    assert compared.get(f"/plans/{demo.id}").status_code == 200
    assert compared.get(f"/compare?a={demo.id}&b={demo.id}").status_code == 200
    assert ComparisonCell.query.filter_by(policy_id=demo.id).count() >= 5
    assert "Compare PRUShield plans" in compared.get(f"/plans/{demo.id}").get_data(as_text=True)


def test_closed_plans_with_wording_are_not_summarised_or_compared(user, fake_groq):
    fake_groq.responder = lambda m: pytest.fail("no call expected for a closed plan")
    page = user.get("/plans/prushield-a").get_data(as_text=True)
    assert "closed to new members" in page and 'id="summary-title"' not in page
    assert comparison_ai.generate_for_plan  # generate-comparison skips them in the CLI
    assert fake_groq.calls() == []
