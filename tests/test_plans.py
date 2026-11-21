"""/plans, the dashboard top 3 and the plan detail page (without the AI parts). _test database only."""

import re

import pytest
from conftest import Browser

from app.extensions import db
from app.models import ComparisonCell, Policy, PolicyClause, Scenario

TYPES = {
    "term_life": "Term life", "hospitalisation": "Hospitalisation", "critical_illness": "Critical illness",
    "whole_life": "Whole life", "retirement": "Retirement", "disability": "Disability",
}
EXPECTED = {
    "Term life": ["PRUActive Term"], "Hospitalisation": ["PRUShield Premier"], "Critical illness": ["PRUCritical Care"],
    "Whole life": ["PRULife Whole"], "Retirement": ["PRURetire Income"], "Disability": ["PRUDisability Guard"],
}


@pytest.fixture()
def user(browser, seeded):
    browser.signup()
    browser.onboard()
    return browser


def plan(name):
    return Policy.query.filter_by(name=name, is_demo=True).one()


def card_names(html):
    return re.findall(r'<h[23] class="plan-name">([^<]+)</h[23]>', html)


def dashboard_cards(html):
    section = html[html.index('id="plans-title"'):]
    return re.findall(r"<h3>([^<]+)</h3>", section)


def cards_html(html):
    """The plan cards on the dashboard, as {plan name: card html}."""
    section = html[html.index('id="plans-title"'):]
    cards = re.findall(r'<article class="card plan-card">.*?</article>', section, re.S)
    return {re.search(r"<h3>([^<]+)</h3>", c).group(1): c for c in cards}


def onboard_with(app, email, answers, other_text="Something else"):
    """A fresh client. answers: {question number: option key, None to skip, 'other' to type free text}."""
    b = Browser(app.test_client())
    b.signup(email=email)
    for number in range(1, 9):
        key = answers.get(number)
        if key is None:
            b.skip(number)
        else:
            b.answer(number, key, other_text if key == "other" else "")
    b.post("/onboarding/review", {})
    return b


# ---------- catalogue and seed ----------

def test_seed_adds_three_more_plans_and_keeps_the_original_three_unchanged(app, seeded):
    plans = Policy.query.order_by(Policy.id).all()
    assert [p.name for p in plans] == ["PRUActive Term", "PRUShield Premier", "PRUCritical Care",
                                       "PRULife Whole", "PRURetire Income", "PRUDisability Guard"]
    assert [(p.type, p.monthly_premium_cents) for p in plans[:3]] == [
        ("Term life", 8900), ("Hospitalisation", 14200), ("Critical illness", 21500)]
    assert {p.type for p in plans} == set(TYPES.values())
    assert all(p.is_demo and "fictional" in p.document_ref for p in plans)


def test_seed_is_idempotent_for_all_six_plans(app, seeded):
    counts = lambda: (Policy.query.count(), PolicyClause.query.count(), ComparisonCell.query.count())
    before = counts()
    assert app.test_cli_runner().invoke(args=["seed-demo"]).exit_code == 0
    assert counts() == before == (6, 43, 36)


def test_new_plans_have_clauses_and_cells_like_the_old_ones(app, seeded):
    for name in ("PRULife Whole", "PRURetire Income", "PRUDisability Guard"):
        policy = plan(name)
        assert PolicyClause.query.filter_by(policy_id=policy.id).count() >= 7
        cells = ComparisonCell.query.filter_by(policy_id=policy.id).all()
        assert len(cells) >= 5
        assert all(c.clause_id is not None for c in cells if c.state == "not_included")


def test_catalogue_helpers_split_the_hidden_demo_plans_from_the_catalogue(app, seeded):
    from app.catalogue import catalogue_policies, legacy_demo_policies
    app.config["SHOW_PRUDENTIAL_PLANS"] = True
    db.session.add(Policy(insurer="Real", name="Not a demo", type="Term life", monthly_premium_cents=None,
                          description="x", is_demo=False, slug="not-a-demo"))
    db.session.commit()
    demo = [p.name for p in legacy_demo_policies()]
    assert demo[:2] == ["PRUActive Term", "PRUShield Premier"] and len(demo) == 6
    assert all(p.is_hidden for p in legacy_demo_policies())
    assert [p.name for p in catalogue_policies()] == ["Not a demo"]  # demo plans are hidden from the catalogue

def test_only_the_catalogue_helper_supplies_plans_to_the_plans_pages():
    from pathlib import Path
    for name in ("plans.py", "plan_match.py"):
        source = (Path(__file__).resolve().parent.parent / "app" / name).read_text(encoding="utf-8")
        assert "Policy.query" not in source and "is_demo" not in source, name


# ---------- /plans ----------

def test_nav_plans_link_points_to_all_plans(user):
    page = user.get("/dashboard").get_data(as_text=True)
    assert 'href="/plans"' in page
    assert user.get("/plans").status_code == 200


def test_plans_pages_need_a_finished_client_account(app, seeded):
    anonymous = Browser(app.test_client())
    assert anonymous.get("/plans").location.endswith("/login")
    assert anonymous.get("/plans/1").location.endswith("/login")
    fresh = Browser(app.test_client())
    fresh.signup()
    assert fresh.get("/plans").location.endswith("/onboarding")
    assert fresh.get("/plans/1").location.endswith("/onboarding")


def test_an_fc_is_sent_back_to_the_fc_portal(app, seeded):
    fc = Browser(app.test_client())
    fc.login(email="fc.daniel@example.com", password="demo-fc-password-1")
    assert fc.get("/plans").location.endswith("/fc")
    assert fc.get("/plans/1").location.endswith("/fc")


# ---------- dashboard top 3 ----------

def test_top_three_follows_priorities_then_budget(app, seeded):
    b = onboard_with(app, "hosp@example.com", {1: "30", 2: "full_time", 3: "no_one", 4: "under_100",
                                               5: "hospital", 6: "at_65", 7: "no_preference", 8: None})
    page = b.get("/dashboard").get_data(as_text=True)
    assert dashboard_cards(page) == ["PRUShield Premier", "PRUActive Term", "PRUDisability Guard"]
    assert page.count("Best match") == 1
    cards = cards_html(page)
    assert "Best match" in cards["PRUShield Premier"]
    assert "Matches your priority: Hospital and medical cover." in cards["PRUShield Premier"]
    for other in ("PRUActive Term", "PRUDisability Guard"):
        assert "Best match" not in cards[other] and "Fits your stated budget." in cards[other]
        assert "Matches your priority" not in cards[other]
    assert "Matched to your questionnaire answers. This is not advice to buy." in page
    assert "None of the sample plans matched" not in page


def test_top_three_with_no_match_shows_the_first_three_without_a_tag(app, seeded):
    b = onboard_with(app, "none@example.com", {n: None for n in range(1, 9)})
    page = b.get("/dashboard").get_data(as_text=True)
    assert dashboard_cards(page) == ["PRUActive Term", "PRUShield Premier", "PRUCritical Care"]
    assert "Best match" not in page and "Matches your priority" not in page and "Fits your stated budget" not in page
    assert "None of the sample plans matched your answers" in page and "without a match tag" in page


def test_typed_in_and_not_sure_answers_count_as_unknown_never_a_match(app, seeded):
    typed = onboard_with(app, "typed@example.com", {1: "other", 2: "other", 3: "other", 4: "other", 5: "other",
                                                   6: "other", 7: "other", 8: "other"})
    page = typed.get("/dashboard").get_data(as_text=True)
    assert "Best match" not in page and "None of the sample plans matched" in page
    unsure = onboard_with(app, "unsure@example.com", {1: "30", 2: "full_time", 3: "no_one", 4: "not_sure",
                                                     5: "not_sure", 6: "not_sure", 7: "no_preference", 8: None})
    page = unsure.get("/dashboard").get_data(as_text=True)
    assert "Best match" not in page and "Fits your stated budget" not in page


def test_budget_alone_can_match_but_a_typed_budget_cannot(app, seeded):
    answers = {1: "30", 2: "full_time", 3: "no_one", 5: "not_sure", 6: "not_sure", 7: "no_preference", 8: None}
    budget = onboard_with(app, "budget@example.com", {**answers, 4: "under_100"})
    page = budget.get("/dashboard").get_data(as_text=True)
    assert dashboard_cards(page)[:2] == ["PRUActive Term", "PRUDisability Guard"]        # the plans under S$100
    assert page.count("Best match") == 1 and "Fits your stated budget." in page
    assert "Matches your priority" not in page
    typed = onboard_with(app, "typedbudget@example.com", {**answers, 4: "other"})
    assert "Best match" not in typed.get("/dashboard").get_data(as_text=True)


def test_top_three_links_and_the_demo_values_stay_intact(user):
    page = user.get("/dashboard").get_data(as_text=True)
    assert 'href="/plans"' in page and "View all plans" in page
    first_plan = dashboard_cards(page)[0]
    assert f'href="/plans/{plan(first_plan).id}"' in page and ">View plan<" in page
    assert re.search(r'href="/compare\?a=%d&amp;b=\d+"' % plan(first_plan).id, page)
    assert "Fictional demo data" in page and "Illustrative demo values" in page
    assert re.findall(r'class="ring-number"[^>]*>(\d+)<', page) == []   # no saved scenario: the score card shows the setup state
    assert re.findall(r'class="bar-value">(\d+)%<', page) == ["72", "65", "58", "35", "48", "20"]


# ---------- plan detail (no AI) ----------

def test_detail_has_every_section_and_the_disclaimer(user):
    shield = plan("PRUShield Premier")
    page = user.get(f"/plans/{shield.id}").get_data(as_text=True)
    assert "PRUShield Premier" in page and "S$142" in page and "Hospitalisation" in page
    assert "← Back to dashboard" in page and f'href="/compare?a={shield.id}&amp;b=' in page
    assert 'href="/simulator"' in page and "Get this plan" in page
    for heading in ("Plan summary", "Budget trade-off", "Compatibility with you", "How it compares to similar plans"):
        assert heading in page, heading
    assert "estimates for discussion, to be confirmed with your Financial Consultant (FC)" in page
    assert "not advice to buy" in page


def test_unknown_and_non_catalogue_plans_are_404(user):
    assert user.get("/plans/99999").status_code == 404
    outsider = Policy(insurer="Real", name="Outside the catalogue", type="Term life", monthly_premium_cents=100,
                      description="x", is_demo=False)
    db.session.add(outsider)
    db.session.commit()
    assert user.get(f"/plans/{outsider.id}").status_code == 404
    assert user.get("/plans/abc").status_code == 404


def test_detail_budget_tradeoff_from_the_answer(app, seeded):
    shield = plan("PRUShield Premier")
    b = onboard_with(app, "band@example.com", {1: "30", 2: "full_time", 3: "no_one", 4: "100_300", 5: "not_sure",
                                               6: "not_sure", 7: "no_preference", 8: None})
    page = b.get(f"/plans/{shield.id}").get_data(as_text=True)
    assert "Within your budget: S$142 a month. Your band is S$100–300 a month." in page
    assert "Up to S$158 a month of your band is left." in page and "budget-bar" in page
    assert "Any other premiums you pay use up the same band." in page
    over = onboard_with(app, "tight@example.com", {1: "30", 2: "full_time", 3: "no_one", 4: "under_100", 5: "not_sure",
                                                   6: "not_sure", 7: "no_preference", 8: None})
    page = over.get(f"/plans/{shield.id}").get_data(as_text=True)
    assert "Above your budget" in page and "S$42 a month over the top of your band" in page


def test_detail_says_unknown_when_the_budget_is_unknown(app, seeded):
    b = onboard_with(app, "nobudget@example.com", {1: "30", 2: "full_time", 3: "no_one", 4: None, 5: "not_sure",
                                                  6: "not_sure", 7: "no_preference", 8: None})
    page = b.get(f"/plans/{plan('PRUShield Premier').id}").get_data(as_text=True)
    assert "Your monthly budget is unknown." in page and "budget-bar" not in page
    assert "Within your budget" not in page and "Above your budget" not in page


def test_detail_compatibility_rows_show_the_driving_answer_or_not_enough_information(app, seeded):
    term = plan("PRUActive Term")
    known = onboard_with(app, "known@example.com", {1: "30", 2: "full_time", 3: "spouse_child", 4: "100_300",
                                                    5: "family_income", 6: "at_60", 7: "private", 8: "none"})
    page = known.get(f"/plans/{term.id}").get_data(as_text=True)
    assert page.count('class="fit-row"') == 4
    assert "You said: My spouse and one child" in page and "Your budget: S$100–300 a month" in page
    assert "You said: Private hospital" in page and "You said: No existing cover" in page
    assert "Good fit" in page and "Weak fit" in page                       # Term pays no hospital bills
    unknown = onboard_with(app, "unknown@example.com", {n: None for n in range(1, 9)})
    page = unknown.get(f"/plans/{term.id}").get_data(as_text=True)
    assert page.count('chip chip-info">Not enough information') == 4        # one chip per row
    assert "You said:" not in page and "Your budget:" not in page          # no answer is invented as the driver


def test_detail_overlap_row_uses_the_saved_simulator_figure(user):
    sim = {"fields": {"age": "30", "support_years": "5", "support_annual": "50,000", "housing_debt": "80,000",
                      "other_debts": "0", "savings": "50,000", "existing_cover": "150,000"}, "plans": [], "events": []}
    user.client.post("/simulator/save", json=sim, headers={"X-CSRF-Token": user.token()})
    assert Scenario.query.count() == 1
    page = user.get(f"/plans/{plan('PRUActive Term').id}").get_data(as_text=True)
    assert "In the simulator you typed existing death cover of S$150,000" in page
    assert "adds S$500,000 on top of it" in page


def test_detail_simplified_comparison_prefers_the_same_type_then_catalogue_order(user):
    page = user.get(f"/plans/{plan('PRUActive Term').id}").get_data(as_text=True)
    table = page[page.index('class="cmp-table simple-table"'):]
    heads = re.findall(r'<th scope="col">([^<]+)<span', table)
    assert heads == ["PRUActive Term", "PRUShield Premier", "PRUCritical Care"]          # no other Term plan: id order
    assert "This plan" in table and "Open full comparison" in page
    assert f'href="/compare?a={plan("PRUActive Term").id}&amp;b={plan("PRUShield Premier").id}"' in page
    for label in ("Included (subject to conditions)", "Optional rider", "Not included", "Unconfirmed"):
        assert label in table, label
    assert "Coverage duration" in table and "Hospital bills" in table


def test_detail_compare_button_opens_the_comparison_with_this_plan_first(user):
    whole = plan("PRULife Whole")
    page = user.get(f"/plans/{whole.id}").get_data(as_text=True)
    link = re.search(r'href="(/compare\?a=%d&amp;b=\d+)"' % whole.id, page).group(1).replace("&amp;", "&")
    opened = user.get(link).get_data(as_text=True)
    assert 'option value="%d" selected' % whole.id in opened


def test_best_match_chip_only_on_the_top_ranked_plan(app, seeded):
    b = onboard_with(app, "bm@example.com", {1: "30", 2: "full_time", 3: "no_one", 4: "under_100", 5: "hospital",
                                             6: "at_65", 7: "no_preference", 8: None})
    assert "Best match" in b.get(f"/plans/{plan('PRUShield Premier').id}").get_data(as_text=True)
    assert "Best match" not in b.get(f"/plans/{plan('PRUActive Term').id}").get_data(as_text=True)


def test_plan_pages_never_show_other_peoples_data(app, seeded):
    a = onboard_with(app, "alice@example.com", {1: "30", 2: "full_time", 3: "spouse_child", 4: "100_300",
                                                5: "family_income", 6: "at_60", 7: "private", 8: "none"})
    b = onboard_with(app, "bob@example.com", {n: None for n in range(1, 9)})
    page = b.get(f"/plans/{plan('PRUActive Term').id}").get_data(as_text=True)
    assert "alice" not in page.lower() and "You said:" not in page
    assert a


def test_plan_page_styles_do_not_reuse_a_dashboard_class_name():
    """Regression: a plan-page rule once reused `.summary-text`, which stacked the dashboard's profile summary."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app"
    css = (root / "static/css/styles.css").read_text(encoding="utf-8")
    assert len(re.findall(r"^\.summary-text\s*\{", css, re.M)) == 1             # the dashboard's own rule, defined once
    assert "plan-summary-text" in css
    detail = (root / "templates/plans/detail.html").read_text(encoding="utf-8")
    assert 'class="summary-text"' not in detail and 'class="plan-summary-text"' in detail


def test_dashboard_profile_summary_stays_one_inline_paragraph(user):
    page = user.get("/dashboard").get_data(as_text=True)
    summary = re.search(r'<p class="summary-text">(.*?)</p>', page, re.S).group(1)
    assert summary.count('<span class="summary-item">') == 8 and " · " in summary
