import json
import re

import pytest

from app.extensions import db
from app.models import Policy, QuestionnaireAnswer, Scenario, User

WORKED_EXAMPLE = {
    "fields": {"age": "30", "support_years": "5", "support_annual": "50,000", "housing_debt": "80,000",
               "other_debts": "0", "savings": "50,000", "existing_cover": "150,000"},
    "plans": [],
    "events": [{"type": "buy_home", "age": "31", "amount": "300,000"},
               {"type": "new_child", "age": "32", "amount": "150,000"}],
}


@pytest.fixture()
def user(browser, seeded):
    browser.signup()
    browser.onboard()
    return browser


def plan(name):
    return Policy.query.filter_by(name=name).one()


def worked_example():
    payload = json.loads(json.dumps(WORKED_EXAMPLE))
    payload["plans"] = [plan("PRUActive Term").id]
    return payload


def save(browser, payload, token=True):
    headers = {"X-CSRF-Token": browser.token()} if token else {}
    return browser.client.post("/simulator/save", json=payload, headers=headers)


def page_data(browser):
    html = browser.get("/simulator").get_data(as_text=True)
    raw = re.search(r'<script type="application/json" id="sim-data">(.*?)</script>', html, flags=re.S).group(1)
    return json.loads(raw.replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&").replace("\\u0027", "'"))


# ---------- access ----------

def test_locked_until_onboarding_is_confirmed(browser, seeded):
    assert browser.get("/simulator").location.endswith("/login")
    browser.signup()
    assert browser.get("/simulator").location.endswith("/onboarding")
    assert save(browser, WORKED_EXAMPLE).status_code == 302


def test_nav_and_dashboard_link_to_the_simulator(user):
    dashboard = user.get("/dashboard").get_data(as_text=True)
    assert 'href="/simulator"' in dashboard
    assert re.search(r'<a class="btn btn-secondary" href="/simulator">Simulate a change</a>', dashboard)
    page = user.get("/simulator").get_data(as_text=True)
    assert page.count('href="/simulator"') >= 1 and "Coverage Needs Simulator" in page


def test_page_shows_formulas_and_estimate_label(user):
    page = user.get("/simulator").get_data(as_text=True)
    assert "Need = support + debts − savings" in page
    assert "Gap = need − existing death cover" in page
    assert "Estimate only" in page and "Financial Consultant (FC)" in page
    assert "separate from the demo score" in page


def test_catalogue_comes_from_seeded_plans(user):
    data = page_data(user)
    by_name = {c["name"]: c for c in data["catalogue"]}
    assert by_name["PRUActive Term"]["death_cover"] == 500000 and by_name["PRUActive Term"]["ends_at_age"] == 65
    assert by_name["PRUShield Premier"]["addable"] is False
    assert by_name["PRUCritical Care"]["death_cover"] == 100000 and by_name["PRUCritical Care"]["ends_at_age"] == 75
    assert data["saved"] is None


# ---------- save and restore ----------

def test_save_validates_recalculates_and_stores(user):
    response = save(user, worked_example())
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert (body["result"]["gap_today"], body["result"]["gap_after_events"], body["result"]["gap_with_plans"]) == (130000, 580000, 80000)
    row = Scenario.query.one()
    assert row.result["gap_with_plans"] == 80000
    assert row.inputs["fields"]["support_annual"] == 50000  # stored as parsed whole dollars
    assert row.inputs["events"][0] == {"type": "buy_home", "age": 31, "amount": 300000}


def test_server_ignores_any_result_the_client_sends(user):
    payload = worked_example()
    payload["result"] = {"gap_today": 1}
    payload["gap_with_plans"] = 1
    assert save(user, payload).get_json()["result"]["gap_with_plans"] == 80000


def test_save_rejects_invalid_input_and_stores_nothing(user):
    payload = worked_example()
    payload["fields"]["other_debts"] = ""
    payload["fields"]["savings"] = "1.5"
    payload["events"][0]["amount"] = "-5"
    response = save(user, payload)
    assert response.status_code == 400
    body = response.get_json()
    assert body["ok"] is False
    assert sorted(body["errors"]) == ["events.0.amount", "other_debts", "savings"]
    assert Scenario.query.count() == 0


@pytest.mark.parametrize("body", ["not json", "[]", "null", '{"fields": 5}'])
def test_save_rejects_unreadable_payloads(user, body):
    response = user.client.post("/simulator/save", data=body, content_type="application/json",
                                headers={"X-CSRF-Token": user.token()})
    assert response.status_code == 400 and Scenario.query.count() == 0


def test_save_requires_csrf_token(user):
    assert save(user, worked_example(), token=False).status_code == 400
    response = user.client.post("/simulator/save", json=worked_example(), headers={"X-CSRF-Token": "wrong"})
    assert response.status_code == 400
    assert Scenario.query.count() == 0


def test_saved_scenario_is_restored_after_reload(user):
    save(user, worked_example())
    data = page_data(user)
    assert data["saved"]["fields"]["support_annual"] == "50000"
    assert [e["type"] for e in data["saved"]["events"]] == ["buy_home", "new_child"]
    assert data["saved"]["plans"] == [plan("PRUActive Term").id]
    assert data["saved_at"]
    assert page_data(user)["saved"] == data["saved"]  # a second reload gives the same thing


def test_saving_again_updates_the_same_row(user):
    save(user, worked_example())
    changed = worked_example()
    changed["fields"]["savings"] = "60,000"
    save(user, changed)
    assert Scenario.query.count() == 1
    assert Scenario.query.one().inputs["fields"]["savings"] == 60000
    assert Scenario.query.one().result["gap_today"] == 120000


def test_scenarios_are_private_to_each_account(browser, seeded):
    browser.signup(email="one@example.com")
    browser.onboard()
    save(browser, worked_example())
    browser.logout()
    browser.signup(email="two@example.com")
    browser.onboard()
    assert page_data(browser)["saved"] is None
    save(browser, {**worked_example(), "events": []})
    assert Scenario.query.count() == 2
    browser.logout()
    browser.login(email="one@example.com")
    assert len(page_data(browser)["saved"]["events"]) == 2


def test_removed_plans_are_dropped_with_a_notice(user):
    save(user, worked_example())
    row = Scenario.query.one()
    row.inputs = {**row.inputs, "plans": [row.inputs["plans"][0], 99999]}
    db.session.commit()
    data = page_data(user)
    assert data["saved"]["plans"] == [plan("PRUActive Term").id]
    assert "no longer available" in data["notice"]


def test_plan_without_death_benefit_cannot_be_saved(user):
    payload = worked_example()
    payload["plans"] = [plan("PRUShield Premier").id]
    response = save(user, payload)
    assert response.status_code == 400 and "plans" in response.get_json()["errors"]


def test_saving_does_not_touch_answers_or_the_dashboard_demo_values(user):
    answers = QuestionnaireAnswer.query.count()
    users = User.query.count()
    before = user.get("/dashboard").get_data(as_text=True)
    save(user, worked_example())
    after = user.get("/dashboard").get_data(as_text=True)
    assert QuestionnaireAnswer.query.count() == answers and User.query.count() == users
    pick = lambda html: re.findall(r'class="ring-number">(\d+)<|class="bar-value">(\d+)%<', html)
    assert pick(before) == pick(after) and pick(after)
    assert "580,000" not in after  # simulator figures never appear on the dashboard


def test_seed_backfills_cover_columns_on_older_rows(user, seeded):
    term = plan("PRUActive Term")
    term.death_cover_cents = None
    term.cover_ends_at_age = None
    db.session.commit()
    assert seeded.test_cli_runner().invoke(args=["seed-demo"]).exit_code == 0
    db.session.refresh(term)
    assert (term.death_cover_cents, term.cover_ends_at_age) == (50_000_000, 65)
