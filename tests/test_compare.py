import re

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import ComparisonCell, ComparisonFlag, Policy, PolicyClause, QuestionnaireAnswer, User

STATE_LABELS = ["Included (subject to conditions)", "Optional rider", "Unconfirmed", "Not included"]


def plan(name):
    return Policy.query.filter_by(name=name).one()


def cells_html(page):
    """Return the <td> blocks of the comparison table."""
    return re.findall(r'<td class="cmp-cell".*?</td>', page, flags=re.S)


@pytest.fixture()
def user(browser, seeded):
    browser.signup()
    browser.onboard()
    return browser


# ---------- access ----------

def test_compare_and_citations_locked_until_onboarding_confirmed(browser, seeded):
    assert browser.get("/compare").location.endswith("/login")
    browser.signup()
    assert browser.get("/compare").location.endswith("/onboarding")
    clause = PolicyClause.query.first()
    assert browser.get(f"/citations/demo/{clause.id}").location.endswith("/onboarding")


def test_compare_without_demo_data_shows_empty_state(browser, app):
    browser.signup()
    browser.onboard()
    page = browser.get("/compare").get_data(as_text=True)
    assert "seed-demo" in page


# ---------- the four states ----------

def test_default_pair_shows_all_four_states(user):
    page = user.get("/compare").get_data(as_text=True)
    table = page[page.index("<table"):]
    for label in STATE_LABELS:
        assert f'>{label}</span>' in table, label


def test_each_cell_has_one_state_and_a_clause_or_a_reason(user):
    page = user.get("/compare").get_data(as_text=True)
    for cell in cells_html(page):
        states = re.findall(r'class="state state-(\w+)"', cell)
        assert len(states) == 1
        assert "clause-link" in cell or "No source clause found" in cell
        if states[0] == "not_included":
            assert "clause-link" in cell  # R3: "Not included" is always cited


def test_missing_information_is_unconfirmed_never_not_included(user):
    shield, critical = plan("PRUShield Premier"), plan("PRUCritical Care")
    page = user.get(f"/compare?a={shield.id}&b={critical.id}").get_data(as_text=True)
    assert "Distinctive benefits" in page
    # Critical illness: only PRUCritical Care has a finding; Shield is "not stated".
    row = re.search(r"<tr>\s*<th scope=\"row\">Critical illness.*?</tr>", page, flags=re.S).group(0)
    shield_cell, critical_cell = cells_html(row)
    assert "Not stated in this policy." in shield_cell
    assert "state-unconfirmed" in shield_cell and "state-not_included" not in shield_cell
    assert "No source clause found" in shield_cell
    assert "Flag for my FC to verify" in shield_cell
    assert "state-included" in critical_cell


def test_rows_nobody_mentions_are_hidden(user):
    term, critical = plan("PRUActive Term"), plan("PRUCritical Care")
    page = user.get(f"/compare?a={term.id}&b={critical.id}").get_data(as_text=True)
    assert "Premium waiver" not in page
    assert "Distinctive benefits" not in page


def test_database_rejects_uncited_not_included(app, seeded):
    policy = plan("PRUActive Term")
    db.session.add(ComparisonCell(policy_id=policy.id, row_key="made_up", state="not_included",
                                  finding="x", clause_id=None))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_database_rejects_unknown_state(app, seeded):
    policy = plan("PRUActive Term")
    db.session.add(ComparisonCell(policy_id=policy.id, row_key="made_up", state="maybe", finding="x"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


# ---------- choosing plans ----------

def test_changing_plans_changes_columns(user):
    term, shield, critical = plan("PRUActive Term"), plan("PRUShield Premier"), plan("PRUCritical Care")
    first = user.get(f"/compare?a={term.id}&b={shield.id}").get_data(as_text=True)
    second = user.get(f"/compare?a={term.id}&b={critical.id}").get_data(as_text=True)
    head = lambda page: re.search(r"<thead>.*?</thead>", page, flags=re.S).group(0)
    assert "PRUShield Premier" in head(first) and "PRUCritical Care" not in head(first)
    assert "PRUCritical Care" in head(second) and "PRUShield Premier" not in head(second)


def test_same_plan_twice_is_replaced_with_notice(user):
    term = plan("PRUActive Term")
    page = user.get(f"/compare?a={term.id}&b={term.id}").get_data(as_text=True)
    assert "Pick two different plans" in page
    head = re.search(r"<thead>.*?</thead>", page, flags=re.S).group(0)
    assert head.count("PRUActive Term") == 1


@pytest.mark.parametrize("query", ["", "?a=abc&b=", "?a=99999&b=-1", "?a=1;drop&b=%00"])
def test_invalid_selection_falls_back_to_two_plans(user, query):
    response = user.get("/compare" + query)
    assert response.status_code == 200
    head = re.search(r"<thead>.*?</thead>", response.get_data(as_text=True), flags=re.S).group(0)
    assert head.count("<th scope=\"col\">") == 3


def test_dashboard_compare_button_opens_that_plan(user):
    page = user.get("/dashboard").get_data(as_text=True)
    shield = plan("PRUShield Premier")
    match = re.search(r'href="(/compare\?a=%d&amp;b=\d+)"' % shield.id, page)
    assert match, "dashboard should link Compare for PRUShield Premier"
    target = match.group(1).replace("&amp;", "&")
    opened = user.get(target).get_data(as_text=True)
    assert 'option value="%d" selected' % shield.id in opened


# ---------- citations ----------

def test_citation_link_opens_the_cited_line(user):
    term = plan("PRUActive Term")
    cell = ComparisonCell.query.filter_by(policy_id=term.id, row_key="duration").one()
    page = user.get("/compare").get_data(as_text=True)
    href = re.search(r'href="(/citations/demo/%d\?[^"]*)"' % cell.clause_id, page).group(1).replace("&amp;", "&")
    viewer = user.get(href).get_data(as_text=True)
    assert 'id="cited-line"' in viewer
    cited = re.search(r'<li class="source-line is-cited".*?</li>', viewer, flags=re.S).group(0)
    assert "ends when the Life Assured reaches age 65" in cited
    assert "Matches: clause 3.1, line 7" in viewer
    assert cell.finding.split(".")[0] in viewer  # the AI claim is shown


def test_citation_ignores_a_cell_that_does_not_cite_it(user):
    term = plan("PRUActive Term")
    other_cell = ComparisonCell.query.filter_by(policy_id=term.id, row_key="premium").one()
    duration = ComparisonCell.query.filter_by(policy_id=term.id, row_key="duration").one()
    page = user.get(f"/citations/demo/{duration.clause_id}?cell={other_cell.id}").get_data(as_text=True)
    assert other_cell.finding not in page
    assert "Source clause" in page


def test_citation_paging_and_unknown_clause(user):
    term = plan("PRUActive Term")
    clause = PolicyClause.query.filter_by(policy_id=term.id, page=3, line=7).one()
    other_page = user.get(f"/citations/demo/{clause.id}?page=5").get_data(as_text=True)
    assert "does not pay for hospital" in other_page and 'id="cited-line"' not in other_page
    assert "Back to cited line" in other_page
    assert user.get(f"/citations/demo/{clause.id}?page=999").status_code == 200  # falls back to cited page
    assert user.get("/citations/demo/999999").status_code == 404
    assert user.get("/citations/demo/not-a-number").status_code == 404


def test_original_wording_text_is_present_for_toggle(user):
    page = user.get("/compare").get_data(as_text=True)
    assert 'id="show-original"' in page
    assert "“The cover starts on the Policy Start Date" in page


# ---------- flag for FC to verify ----------

def flag_form(browser, policy, row_key):
    return browser.post("/compare/flag", {"policy_id": policy.id, "row_key": row_key, "a": policy.id, "b": policy.id})


def test_flag_toggle_on_unconfirmed_cell(user):
    shield = plan("PRUShield Premier")
    response = flag_form(user, shield, "cash_value")
    assert response.status_code == 302
    assert ComparisonFlag.query.count() == 1
    critical = plan("PRUCritical Care")
    page = user.get(f"/compare?a={shield.id}&b={critical.id}").get_data(as_text=True)
    assert "Flagged for your FC to verify" in page and "Remove flag" in page
    assert "Send flagged items to my FC (1)" in page
    flag_form(user, shield, "cash_value")
    assert ComparisonFlag.query.count() == 0


def test_flag_works_on_missing_information_cells(user):
    shield = plan("PRUShield Premier")
    assert flag_form(user, shield, "critical_illness").status_code == 302
    assert ComparisonFlag.query.one().row_key == "critical_illness"


def test_cannot_flag_cells_that_are_not_unconfirmed(user):
    term = plan("PRUActive Term")
    assert flag_form(user, term, "duration").status_code == 400
    assert flag_form(user, term, "not_a_row").status_code == 400
    assert ComparisonFlag.query.count() == 0


def test_flags_are_private_to_each_user(browser, seeded):
    browser.signup(email="one@example.com")
    browser.onboard()
    shield = plan("PRUShield Premier")
    flag_form(browser, shield, "cash_value")
    browser.logout()
    browser.signup(email="two@example.com")
    browser.onboard()
    page = browser.get(f"/compare?a={shield.id}&b={plan('PRUCritical Care').id}").get_data(as_text=True)
    assert "Flagged for your FC to verify" not in page


def test_flag_requires_csrf(user):
    shield = plan("PRUShield Premier")
    response = user.client.post("/compare/flag", data={"policy_id": shield.id, "row_key": "cash_value"})
    assert response.status_code == 400 and ComparisonFlag.query.count() == 0


# ---------- seed command ----------

def test_seed_is_idempotent_and_reset_keeps_accounts(browser, seeded):
    browser.signup()
    browser.onboard()
    counts = lambda: (Policy.query.count(), PolicyClause.query.count(), ComparisonCell.query.count())
    before = counts()
    assert seeded.test_cli_runner().invoke(args=["seed-demo"]).exit_code == 0
    assert counts() == before
    result = seeded.test_cli_runner().invoke(args=["seed-demo", "--reset"])
    assert result.exit_code == 0, result.output
    assert counts() == before
    assert User.query.filter_by(email="alex@example.com").count() == 1
    assert QuestionnaireAnswer.query.count() == 8


def test_citation_title_never_shows_none_and_seed_backfills_document_name(user, seeded):
    term = plan("PRUActive Term")
    term.document_ref = None  # a plan seeded by an earlier build
    db.session.commit()
    clause = PolicyClause.query.filter_by(policy_id=term.id, page=3, line=7).one()
    assert "Source: None" not in user.get(f"/citations/demo/{clause.id}").get_data(as_text=True)
    assert seeded.test_cli_runner().invoke(args=["seed-demo"]).exit_code == 0
    db.session.refresh(term)
    assert term.document_ref == "PRUActive Term sample policy contract (fictional)"
    assert "Source: PRUActive Term sample policy contract (fictional)" in user.get(f"/citations/demo/{clause.id}").get_data(as_text=True)
