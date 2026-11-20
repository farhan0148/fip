"""Consent-based sharing and the minimal FC portal. Uses the *_test database only."""

import re

import pytest
from conftest import Browser
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    ComparisonFlag,
    FCAssignment,
    FCProfile,
    Policy,
    PolicyClause,
    ShareEvent,
    ShareGrant,
    User,
)

FC_PASSWORD = "demo-fc-password-1"
DANIEL = ("fc.daniel@example.com", "FC-2048")
JANE = ("fc.jane@example.com", "FC-3117")
ALL = ("profile_answers", "simulator", "flagged_items")

SIM = {
    "fields": {"age": "30", "support_years": "5", "support_annual": "50,000", "housing_debt": "80,000",
               "other_debts": "0", "savings": "50,000", "existing_cover": "150,000"},
    "plans": [],
    "events": [{"type": "buy_home", "age": "31", "amount": "300,000"}],
}


def fresh():
    db.session.expire_all()


# ---------- helpers ----------

def new_browser(app):
    return Browser(app.test_client())


def make_client(app, email="client@example.com", name="Casey Client", answers=None):
    b = new_browser(app)
    b.signup(name=name, email=email)
    b.onboard(answers)
    return b


def make_fc_browser(app, who=DANIEL):
    b = new_browser(app)
    response = b.login(email=who[0], password=FC_PASSWORD)
    assert response.status_code == 302 and response.location.endswith("/fc"), response.location
    return b


def uid(email):
    fresh()
    return User.query.filter_by(email=email).one().id


def choose_fc(b, code=DANIEL[1], category=None):
    data = {"choice": "referral", "code": code}
    if category:
        data["category"] = category
    return b.post("/sharing/fc", data)


def confirm(b, categories):
    return b.post("/sharing/confirm", {"category": list(categories)})


def share(b, categories=ALL, code=DANIEL[1]):
    """Pick an FC by referral code (unless already assigned) and confirm the given categories."""
    if choose_fc(b, code).status_code not in (302, 400):
        raise AssertionError("choose_fc failed")
    return confirm(b, categories)


def save_scenario(b):
    return b.client.post("/simulator/save", json=SIM, headers={"X-CSRF-Token": b.token()})


def flag_a_cell(b):
    shield = Policy.query.filter_by(name="PRUShield Premier").one()
    return b.post("/compare/flag", {"policy_id": shield.id, "row_key": "cash_value", "a": shield.id, "b": shield.id})


def grants_for(client_email, active_only=True):
    fresh()
    q = ShareGrant.query.filter_by(client_id=uid(client_email))
    if active_only:
        q = q.filter(ShareGrant.revoked_at.is_(None))
    return sorted(g.category for g in q)


def events_for(client_email, action=None):
    fresh()
    q = ShareEvent.query.filter_by(client_id=uid(client_email))
    if action:
        q = q.filter_by(action=action)
    return q.order_by(ShareEvent.id).all()


@pytest.fixture()
def world(app, seeded):
    """Seeded demo plans and the two demo FCs."""
    return app


def checked_boxes(html):
    return sorted(re.findall(r'<input type="checkbox" name="category" value="(\w+)"[^>]*\bchecked\b', html))


# ---------- 1. roles and accounts ----------

def test_public_signup_always_creates_a_client(world):
    b = new_browser(world)
    response = b.post("/signup", {"full_name": "Mallory", "email": "mallory@example.com",
                                  "password": "correct-horse-9", "role": "fc", "is_demo": "1", "referral_code": "FC-9999"})
    assert response.status_code == 302
    user = User.query.filter_by(email="mallory@example.com").one()
    assert user.role == "client" and user.is_demo is False
    assert FCProfile.query.filter_by(user_id=user.id).count() == 0
    assert b.get("/fc").status_code == 404


def test_seed_demo_adds_two_demo_fcs_with_referral_codes_and_is_repeatable(world):
    fcs = User.query.filter_by(role="fc").order_by(User.id).all()
    assert [u.email for u in fcs] == [DANIEL[0], JANE[0]]
    assert all(u.is_demo for u in fcs)
    codes = {p.user_id: p.referral_code for p in FCProfile.query.all()}
    assert sorted(codes.values()) == ["FC-2048", "FC-3117"]
    assert world.test_cli_runner().invoke(args=["seed-demo"]).exit_code == 0
    assert User.query.filter_by(role="fc").count() == 2 and FCProfile.query.count() == 2


def test_create_fc_command_prompts_for_a_hidden_password(world):
    runner = world.test_cli_runner()
    result = runner.invoke(args=["create-fc", "--email", "New.FC@Example.com", "--name", "  Priya   Nair ",
                                 "--speciality", "Retirement"], input="long-password-1\nlong-password-1\n")
    assert result.exit_code == 0, result.output
    assert "long-password-1" not in result.output
    user = User.query.filter_by(email="new.fc@example.com").one()
    assert user.role == "fc" and user.full_name == "Priya Nair" and user.is_demo is False
    assert user.password_hash != "long-password-1" and "long-password-1" not in user.password_hash
    profile = FCProfile.query.filter_by(user_id=user.id).one()
    assert re.fullmatch(r"FC-\d{4}", profile.referral_code) and profile.speciality == "Retirement"
    assert profile.languages == ["English"]
    # The new FC can log in through the normal page and lands on /fc.
    b = new_browser(world)
    assert b.login(email="new.fc@example.com", password="long-password-1").location.endswith("/fc")


@pytest.mark.parametrize("args, password, message", [
    (["--email", "bad", "--name", "X"], "long-password-1\nlong-password-1\n", "valid email"),
    (["--email", "ok@example.com", "--name", "X"], "short\nshort\n", "8 to 128"),
    (["--email", "fc.daniel@example.com", "--name", "X"], "long-password-1\nlong-password-1\n", "already exists"),
    (["--email", "ok@example.com", "--name", "   "], "long-password-1\nlong-password-1\n", "Name"),
])
def test_create_fc_rejects_bad_input(world, args, password, message):
    before = User.query.count()
    result = world.test_cli_runner().invoke(args=["create-fc"] + args, input=password)
    assert result.exit_code != 0 and message in result.output
    assert User.query.count() == before


def test_create_fc_requires_email_and_name(world):
    assert world.test_cli_runner().invoke(args=["create-fc"]).exit_code != 0


def test_fc_logs_in_through_the_same_page_and_lands_on_the_portal(world):
    b = make_fc_browser(world)
    assert b.get("/").location.endswith("/fc")
    page = b.get("/fc").get_data(as_text=True)
    assert "FC portal" in page and "My clients" in page
    assert b.logout().location.endswith("/login")
    assert b.get("/fc").location.endswith("/login")


@pytest.mark.parametrize("path", ["/dashboard", "/simulator", "/compare", "/plans", "/settings", "/onboarding",
                                  "/onboarding/question/1", "/sharing", "/sharing/new"])
def test_fc_cannot_open_client_pages(world, path):
    b = make_fc_browser(world)
    response = b.get(path)
    assert response.status_code == 302 and response.location.endswith("/fc"), path


def test_fc_cannot_post_to_client_actions(world):
    fc = make_fc_browser(world)
    client = make_client(world)
    cid = uid("client@example.com")
    for path, data in [("/sharing/fc", {"choice": "random"}), ("/sharing/confirm", {"category": ["profile_answers"]}),
                       ("/sharing/revoke", {"category": "profile_answers"})]:
        assert fc.post(path, data).status_code == 302
    assert fc.client.post("/simulator/save", json=SIM, headers={"X-CSRF-Token": fc.token()}).status_code == 302
    assert fc.client.post("/api/simulator/explain", json=SIM, headers={"X-CSRF-Token": fc.token()}).status_code == 403
    assert ShareGrant.query.count() == 0 and FCAssignment.query.count() == 0
    assert client.get("/fc").status_code == 404 and cid


@pytest.mark.parametrize("path", ["/fc", "/fc/clients/1"])
def test_clients_never_see_the_fc_portal(world, path):
    client = make_client(world)
    assert client.get(path).status_code == 404
    assert new_browser(world).get(path).location.endswith("/login")


# ---------- 2. consent flow ----------

def test_entry_points_open_the_consent_screen_with_the_right_item_ticked(world):
    b = make_client(world)
    save_scenario(b)
    flag_a_cell(b)
    shield = Policy.query.filter_by(name="PRUShield Premier").one()
    term = Policy.query.filter_by(name="PRUActive Term").one()
    clause_id = PolicyClause.query.filter_by(policy_id=term.id).first().id

    expected = {
        "dashboard": ("/dashboard", "Send to my FC", "profile_answers"),
        "simulator": ("/simulator", "Review with my FC", "simulator"),
        "compare": (f"/compare?a={shield.id}&b={term.id}", "Send flagged items to my FC", "flagged_items"),
        "citation": (f"/citations/demo/{clause_id}", "Send to my FC", "flagged_items"),
    }
    for name, (path, label, category) in expected.items():
        page = b.get(path).get_data(as_text=True)
        link = re.search(r'<a class="btn[^"]*" href="(/sharing/new\?category=\w+)"[^>]*>' + re.escape(label), page)
        assert link, f"{name}: '{label}' is not an enabled link"
        assert link.group(1) == f"/sharing/new?category={category}", name
        assert not re.search(r'<button[^>]*disabled[^>]*>\s*' + re.escape(label), page), name
        step1 = b.get(link.group(1))
        assert step1.status_code == 200 and "Referral code" in step1.get_data(as_text=True)
        b.post("/sharing/fc/reset", {})
        choose_fc(b, category=category)
        step2 = b.get(link.group(1)).get_data(as_text=True)
        assert checked_boxes(step2) == [category], name
        b.post("/sharing/fc/reset", {})


def test_nothing_is_ticked_by_default(world):
    b = make_client(world)
    choose_fc(b)
    for url in ("/sharing/new", "/sharing/new?category=nonsense", "/sharing/new?category=coverage_score"):
        page = b.get(url).get_data(as_text=True)
        assert checked_boxes(page) == [], url
        assert page.count('name="category"') == 3
    assert "Confirm and share" in page


def test_step_one_asks_for_a_referral_code_or_a_random_match(world):
    b = make_client(world)
    page = b.get("/sharing/new").get_data(as_text=True)
    assert "Referral code" in page and "Match me" in page
    assert "Nothing is shared and no FC is told about you yet" in page


def test_valid_referral_code_previews_the_fc_and_assigns_nothing_yet(world):
    b = make_client(world)
    response = choose_fc(b, code="  fc-2048 ")
    assert response.status_code == 302 and response.location.endswith("/sharing/new")
    page = b.get("/sharing/new").get_data(as_text=True)
    assert "Daniel Koh" in page and "Family protection" in page
    assert "fc.daniel@example.com" not in page and "FC-2048" not in page
    fresh()
    assert FCAssignment.query.count() == 0 and ShareGrant.query.count() == 0 and ShareEvent.query.count() == 0


@pytest.mark.parametrize("code", ["FC-0000", "FC-9999", "", "   ", "nonsense", "fc.daniel@example.com",
                                  "alexandra@example.com", "FC-2048; DROP TABLE users", "x" * 200, "FC-20 48x"])
def test_invalid_referral_codes_get_one_generic_answer(world, code):
    make_client(world, email="exists@example.com")
    b = make_client(world, email="asker@example.com")
    response = choose_fc(b, code=code)
    body = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "We could not find that referral code" in body
    assert "exists@example.com" not in body and "Daniel" not in body
    fresh()
    assert FCAssignment.query.count() == 0 and ShareGrant.query.count() == 0


def test_failed_codes_are_rate_limited_per_client_and_successes_do_not_count(world):
    clock = {"now": 5000.0}
    limiter = world.extensions["referral_limiter"]
    limiter.clock = lambda: clock["now"]
    a, other = make_client(world, email="a@example.com"), make_client(world, email="b@example.com")
    assert (limiter.limit, limiter.window) == (5, 600)
    for _ in range(5):
        assert choose_fc(a, code="FC-0000").status_code == 400
    blocked = choose_fc(a, code="FC-0000")
    assert blocked.status_code == 429 and "Too many tries" in blocked.get_data(as_text=True)
    assert choose_fc(a, code=DANIEL[1]).status_code == 429          # even a valid code waits
    assert choose_fc(other, code=DANIEL[1]).status_code == 302      # another client is unaffected
    clock["now"] += 601
    assert choose_fc(a, code=DANIEL[1]).status_code == 302          # window passed

    # Good codes never count against the limit.
    c = make_client(world, email="c@example.com")
    for _ in range(8):
        assert choose_fc(c, code=DANIEL[1]).status_code == 302


def test_random_match_picks_the_least_loaded_demo_fc_and_assigns_only_on_confirm(world):
    first = make_client(world, email="one@example.com")
    first.post("/sharing/fc", {"choice": "random"})
    assert FCAssignment.query.count() == 0
    confirm(first, ["profile_answers"])
    fresh()
    first_fc = FCAssignment.query.one()
    assert first_fc.method == "random" and db.session.get(User, first_fc.fc_id).is_demo and db.session.get(User, first_fc.fc_id).role == "fc"

    second = make_client(world, email="two@example.com")
    second.post("/sharing/fc", {"choice": "random"})
    confirm(second, ["profile_answers"])
    fresh()
    assignments = FCAssignment.query.order_by(FCAssignment.id).all()
    assert assignments[1].fc_id != assignments[0].fc_id        # least-loaded FC gets the next client


def test_random_match_with_no_fc_available_says_so(app, seeded):
    FCProfile.query.delete()
    User.query.filter_by(role="fc").delete()
    db.session.commit()
    b = make_client(app)
    response = b.post("/sharing/fc", {"choice": "random"})
    assert "No FC is available" in response.get_data(as_text=True)


def test_confirm_with_nothing_ticked_creates_nothing(world):
    b = make_client(world)
    choose_fc(b)
    response = confirm(b, [])
    assert response.status_code == 302 and "error=" in response.location
    only_unknown = confirm(b, ["coverage_score", "demo_radar", "everything"])
    assert "error=" in only_unknown.location
    fresh()
    assert FCAssignment.query.count() == 0 and ShareGrant.query.count() == 0 and ShareEvent.query.count() == 0
    assert "Tick at least one" in b.get(response.location).get_data(as_text=True)


def test_confirm_without_choosing_an_fc_goes_back_to_step_one(world):
    b = make_client(world)
    response = confirm(b, ["profile_answers"])
    assert response.status_code == 302 and response.location.endswith("/sharing/new")
    fresh()
    assert ShareGrant.query.count() == 0


def test_demo_score_and_radar_are_never_a_shareable_category(world):
    b = make_client(world)
    choose_fc(b)
    page = b.get("/sharing/new").get_data(as_text=True)
    assert re.findall(r'name="category" value="(\w+)"', page) == list(ALL)
    assert "can never be shared" in page and "dashboard score and radar" in page
    confirm(b, ["profile_answers", "coverage_map", "score", "demo_values"])
    assert grants_for("client@example.com") == ["profile_answers"]


def test_consent_screen_shows_exactly_what_each_item_contains(world):
    b = make_client(world, name="Casey Client")
    save_scenario(b)
    flag_a_cell(b)
    choose_fc(b)
    page = b.get("/sharing/new").get_data(as_text=True)
    assert "Casey Client" in page and "Full-time employee" in page and "Not included: your email address" in page
    assert "Buy a home at age 31: S$300,000" in page and "Gap today" in page and "S$130,000" in page
    assert "PRUShield Premier · Cash value" in page and "Unconfirmed" in page
    assert "client@example.com" not in page


def test_confirm_assigns_grants_only_what_was_ticked_and_logs_events(world):
    b = make_client(world)
    choose_fc(b)
    response = confirm(b, ["profile_answers", "flagged_items"])
    assert response.status_code == 302 and "shared=1" in response.location
    fresh()
    assignment = FCAssignment.query.one()
    assert assignment.method == "referral" and db.session.get(User, assignment.fc_id).email == DANIEL[0]
    assert grants_for("client@example.com") == ["flagged_items", "profile_answers"]
    events = events_for("client@example.com")
    assert [(e.action, e.category) for e in events] == [("granted", "profile_answers"), ("granted", "flagged_items")]
    assert all(e.fc_id == assignment.fc_id for e in events)


def test_resubmitting_the_same_choice_does_not_duplicate_grants(world):
    b = make_client(world)
    choose_fc(b)
    confirm(b, ["profile_answers"])
    again = confirm(b, ["profile_answers"])
    assert "error=" in again.location
    assert len(grants_for("client@example.com")) == 1 and len(events_for("client@example.com", "granted")) == 1


def test_database_allows_only_one_active_grant_per_client_fc_and_category(world):
    b = make_client(world)
    share(b, ["simulator"])
    cid, fcid = uid("client@example.com"), uid(DANIEL[0])
    db.session.add(ShareGrant(client_id=cid, fc_id=fcid, category="simulator"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    db.session.add(ShareGrant(client_id=cid, fc_id=fcid, category="not_a_category"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_already_assigned_client_skips_step_one_and_cannot_switch_fc_here(world):
    b = make_client(world)
    share(b, ["profile_answers"])
    page = b.get("/sharing/new").get_data(as_text=True)
    assert "Referral code" not in page and "Daniel Koh" in page and "Already shared" in page
    response = b.post("/sharing/fc", {"choice": "referral", "code": JANE[1]})
    assert response.status_code == 400
    fresh()
    assert FCAssignment.query.one().fc_id == uid(DANIEL[0])


def test_sharing_page_lists_active_grants_with_a_revoke_button_and_the_trust_banner(world):
    b = make_client(world)
    empty = b.get("/sharing").get_data(as_text=True)
    assert "Your FC only sees what you choose to share. You can stop sharing any time." in empty
    assert "You are not sharing anything" in empty
    share(b, ["profile_answers", "simulator"])
    page = b.get("/sharing").get_data(as_text=True)
    assert "Your FC only sees what you choose to share. You can stop sharing any time." in page
    assert page.count(">Revoke</button>") == 2 and "Daniel Koh" in page
    assert "Your name and questionnaire answers" in page and "Your saved simulator scenario" in page
    assert "Comparison items you flagged" not in page.split("What you are sharing now")[1].split("Activity")[0]


def test_regranting_after_a_revoke_creates_a_new_row(world):
    b = make_client(world)
    share(b, ["simulator"])
    b.post("/sharing/revoke", {"category": "simulator"})
    confirm(b, ["simulator"])
    fresh()
    rows = ShareGrant.query.filter_by(category="simulator").order_by(ShareGrant.id).all()
    assert len(rows) == 2 and rows[0].revoked_at is not None and rows[1].revoked_at is None
    assert [e.action for e in events_for("client@example.com")] == ["granted", "revoked", "granted"]


def test_revoking_something_not_shared_does_nothing(world):
    b = make_client(world)
    share(b, ["simulator"])
    before = len(events_for("client@example.com"))
    assert b.post("/sharing/revoke", {"category": "flagged_items"}).status_code == 302
    assert len(events_for("client@example.com")) == before
    assert b.post("/sharing/revoke", {"category": "bogus"}).status_code == 400


# ---------- 3. FC portal and 4. access control ----------

def setup_two_clients(app):
    """alice shares profile+simulator with Daniel; bob shares only flagged items with Daniel; carol shares with Jane."""
    alice = make_client(app, email="alice@example.com", name="Alice Tan")
    save_scenario(alice)
    share(alice, ["profile_answers", "simulator"])
    bob = make_client(app, email="bob@example.com", name="Bob Lim")
    flag_a_cell(bob)
    share(bob, ["flagged_items"])
    carol = make_client(app, email="carol@example.com", name="Carol Ng")
    share(carol, ["profile_answers"], code=JANE[1])
    return alice, bob, carol


def test_fc_sees_only_the_one_category_that_was_granted(world):
    client = make_client(world, name="Casey Client")
    save_scenario(client)
    flag_a_cell(client)
    share(client, ["simulator"])
    fc = make_fc_browser(world)
    cid = uid("client@example.com")
    page = fc.get(f"/fc/clients/{cid}").get_data(as_text=True)
    assert "Simulator scenario" in page and "Buy a home at age 31: S$300,000" in page and "S$130,000" in page
    for hidden in ("Casey Client", "Full-time employee", "Profile and answers", "Flagged items", "PRUShield Premier",
                   "Cash value", "client@example.com"):
        assert hidden not in page, hidden
    assert page.count('class="card fc-section"') == 1


def test_fc_sees_each_category_only_when_granted(world):
    client = make_client(world, name="Casey Client")
    save_scenario(client)
    flag_a_cell(client)
    share(client, ["profile_answers", "flagged_items"])
    fc = make_fc_browser(world)
    page = fc.get(f"/fc/clients/{uid('client@example.com')}").get_data(as_text=True)
    assert "Casey Client" in page and "Full-time employee" in page and "PRUShield Premier" in page
    assert "Simulator scenario" not in page and "S$300,000" not in page and "Gap today" not in page
    assert "client@example.com" not in page and "Illustrative demo values" not in page


def test_fc_list_empty_state_when_nobody_shares(world):
    make_client(world)
    page = make_fc_browser(world).get("/fc").get_data(as_text=True)
    assert "No clients are sharing with you yet" in page and "<table" not in page


def test_fc_list_shows_only_clients_with_an_active_grant_and_what_is_shared(world):
    setup_two_clients(world)
    nobody = make_client(world, email="silent@example.com", name="Silent Sam")
    choose_fc(nobody)                       # picked Daniel but never confirmed: nothing assigned
    page = make_fc_browser(world).get("/fc").get_data(as_text=True)
    assert "Alice Tan" in page and "Profile and answers" in page and "Simulator scenario" in page
    assert "Client #%d" % uid("bob@example.com") in page and "Flagged items" in page   # no name: profile not shared
    assert "Bob Lim" not in page and "Carol Ng" not in page and "Silent Sam" not in page
    assert re.search(r"\d{2} \w{3} \d{4}, \d{2}:\d{2}", page)
    jane_page = make_fc_browser(world, JANE).get("/fc").get_data(as_text=True)
    assert "Carol Ng" in jane_page and "Alice Tan" not in jane_page


def test_wrong_fc_gets_404_for_a_client_assigned_to_someone_else(world):
    setup_two_clients(world)
    jane = make_fc_browser(world, JANE)
    for email in ("alice@example.com", "bob@example.com"):
        assert jane.get(f"/fc/clients/{uid(email)}").status_code == 404
    daniel = make_fc_browser(world, DANIEL)
    assert daniel.get(f"/fc/clients/{uid('carol@example.com')}").status_code == 404
    assert daniel.get(f"/fc/clients/{uid('alice@example.com')}").status_code == 200


def test_guessing_ids_reveals_nothing(world):
    setup_two_clients(world)
    make_client(world, email="ungranted@example.com")
    daniel = make_fc_browser(world, DANIEL)
    allowed = {uid("alice@example.com"), uid("bob@example.com")}
    answers = {}
    for guess in range(0, 60):
        response = daniel.get(f"/fc/clients/{guess}")
        answers[guess] = (response.status_code, response.get_data(as_text=True))
        assert (response.status_code == 200) == (guess in allowed), guess
    refused = {body for status, body in answers.values() if status == 404}
    assert len(refused) == 1                                   # the same page whether it exists or not
    assert daniel.get(f"/fc/clients/{uid('fc.jane@example.com')}").status_code == 404   # an FC id is not a client
    assert daniel.get(f"/fc/clients/{uid('fc.daniel@example.com')}").status_code == 404


def test_grant_without_a_matching_assignment_shows_nothing(world):
    b = make_client(world)
    cid = uid("client@example.com")
    db.session.add(ShareGrant(client_id=cid, fc_id=uid(DANIEL[0]), category="profile_answers"))
    db.session.commit()
    daniel = make_fc_browser(world)
    assert daniel.get(f"/fc/clients/{cid}").status_code == 404
    assert "No clients are sharing with you yet" in daniel.get("/fc").get_data(as_text=True)
    assert b


def test_grant_to_a_different_fc_than_the_assigned_one_shows_nothing(world):
    b = make_client(world)
    share(b, ["profile_answers"])                              # assigned to Daniel
    cid = uid("client@example.com")
    db.session.add(ShareGrant(client_id=cid, fc_id=uid(JANE[0]), category="profile_answers"))
    db.session.commit()
    assert make_fc_browser(world, JANE).get(f"/fc/clients/{cid}").status_code == 404


def test_revocation_takes_effect_on_the_very_next_fc_request(world):
    client = make_client(world, name="Casey Client")
    save_scenario(client)
    share(client, ["profile_answers", "simulator"])
    fc = make_fc_browser(world)
    cid = uid("client@example.com")
    assert "Full-time employee" in fc.get(f"/fc/clients/{cid}").get_data(as_text=True)
    client.post("/sharing/revoke", {"category": "profile_answers"})
    page = fc.get(f"/fc/clients/{cid}").get_data(as_text=True)
    assert "Full-time employee" not in page and "Casey Client" not in page and "Buy a home" in page
    assert "Client #%d" % cid in page
    client.post("/sharing/revoke", {"category": "simulator"})
    assert fc.get(f"/fc/clients/{cid}").status_code == 404
    assert "No clients are sharing with you yet" in fc.get("/fc").get_data(as_text=True)


def test_grants_are_live_not_snapshots(world):
    client = make_client(world)
    share(client, ["simulator"])
    fc = make_fc_browser(world)
    cid = uid("client@example.com")
    assert "No scenario has been saved yet" in fc.get(f"/fc/clients/{cid}").get_data(as_text=True)
    save_scenario(client)
    assert "S$130,000" in fc.get(f"/fc/clients/{cid}").get_data(as_text=True)


def test_ungranted_categories_are_never_queried(world):
    client = make_client(world)
    save_scenario(client)
    flag_a_cell(client)
    share(client, ["simulator"])
    fc = make_fc_browser(world)
    cid = uid("client@example.com")
    statements = []

    def spy(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", spy)
    try:
        assert fc.get(f"/fc/clients/{cid}").status_code == 200
    finally:
        event.remove(db.engine, "before_cursor_execute", spy)
    sql = "\n".join(statements).lower()
    assert "from scenarios" in sql                                         # the granted category is read
    for table in ("questionnaire_answers", "comparison_flags", "comparison_cells", "policy_clauses"):
        assert f"from {table}" not in sql, table
    assert not any(s.lower().startswith("select users.full_name from users") for s in statements)  # name not read


def test_fc_list_does_not_query_names_or_data_it_may_not_show(world):
    client = make_client(world)
    share(client, ["flagged_items"])
    fc = make_fc_browser(world)
    statements = []
    spy = lambda conn, cursor, statement, parameters, context, executemany: statements.append(statement)
    event.listen(db.engine, "before_cursor_execute", spy)
    try:
        fc.get("/fc")
    finally:
        event.remove(db.engine, "before_cursor_execute", spy)
    sql = "\n".join(statements).lower()
    for table in ("questionnaire_answers", "scenarios", "comparison_flags"):
        assert f"from {table}" not in sql
    assert not any("users.full_name from users" in s.lower() and "users.id in" in s.lower() for s in statements)


def test_share_events_record_views_for_each_category_shown(world):
    client = make_client(world)
    save_scenario(client)
    share(client, ["profile_answers", "simulator"])
    fc = make_fc_browser(world)
    cid = uid("client@example.com")
    fc.get(f"/fc/clients/{cid}")
    fc.get(f"/fc/clients/{cid}")
    views = events_for("client@example.com", "viewed")
    assert len(views) == 4 and sorted({e.category for e in views}) == ["profile_answers", "simulator"]
    assert all(e.fc_id == uid(DANIEL[0]) for e in views)
    client.post("/sharing/revoke", {"category": "simulator"})
    fc.get(f"/fc/clients/{cid}")
    assert len(events_for("client@example.com", "viewed")) == 5          # only profile_answers this time
    assert [e.action for e in events_for("client@example.com") if e.action != "viewed"] == ["granted", "granted", "revoked"]
    assert fc.get("/fc").status_code == 200 and len(events_for("client@example.com", "viewed")) == 5   # the list is not a view
    page = client.get("/sharing").get_data(as_text=True)
    assert "Daniel Koh viewed" in page and "You stopped sharing with Daniel Koh" in page


def test_refused_requests_write_no_view_events(world):
    setup_two_clients(world)
    before = ShareEvent.query.count()
    make_fc_browser(world, JANE).get(f"/fc/clients/{uid('alice@example.com')}")
    fresh()
    assert ShareEvent.query.count() == before


def test_share_events_are_append_only(world):
    b = make_client(world)
    share(b, ["simulator"])
    row = ShareEvent.query.first()
    row.category = "profile_answers"
    with pytest.raises(RuntimeError, match="append-only"):
        db.session.commit()
    db.session.rollback()


def test_fc_portal_is_read_only(world):
    setup_two_clients(world)
    fc = make_fc_browser(world)
    cid = uid("alice@example.com")
    for method in ("post", "put", "delete", "patch"):
        assert getattr(fc.client, method)(f"/fc/clients/{cid}").status_code == 405
        assert getattr(fc.client, method)("/fc").status_code == 405
    page = fc.get(f"/fc/clients/{cid}").get_data(as_text=True)
    assert "<form" not in page.split("</header>")[1]                                    # no form on the page body


# ---------- CSRF ----------

@pytest.mark.parametrize("path, data", [
    ("/sharing/fc", {"choice": "random"}),
    ("/sharing/fc", {"choice": "referral", "code": DANIEL[1]}),
    ("/sharing/fc/reset", {}),
    ("/sharing/confirm", {"category": ["profile_answers"]}),
    ("/sharing/revoke", {"category": "profile_answers"}),
])
def test_every_sharing_post_needs_a_csrf_token(world, path, data):
    b = make_client(world)
    share(b, ["simulator"])
    before = (ShareGrant.query.count(), ShareEvent.query.count(), FCAssignment.query.count())
    assert b.client.post(path, data=data).status_code == 400
    assert b.client.post(path, data={**data, "_csrf": "wrong"}).status_code == 400
    fresh()
    assert (ShareGrant.query.count(), ShareEvent.query.count(), FCAssignment.query.count()) == before


# ---------- role gate on every route ----------

SHARING_GETS = ["/sharing", "/sharing/new"]
SHARING_POSTS = ["/sharing/fc", "/sharing/fc/reset", "/sharing/confirm", "/sharing/revoke"]


@pytest.mark.parametrize("path", SHARING_GETS + ["/fc", "/fc/clients/1"])
def test_anonymous_users_are_sent_to_log_in(world, path):
    assert new_browser(world).get(path).location.endswith("/login")


@pytest.mark.parametrize("path", SHARING_POSTS)
def test_anonymous_users_cannot_post_to_sharing(world, path):
    b = new_browser(world)
    assert b.post(path, {"choice": "random", "category": "simulator"}).location.endswith("/login")
    assert ShareGrant.query.count() == 0


def test_clients_who_have_not_finished_onboarding_cannot_share(world):
    b = new_browser(world)
    b.signup()
    assert b.get("/sharing").location.endswith("/onboarding")
    assert b.get("/sharing/new").location.endswith("/onboarding")


# ---------- text is escaped ----------

def test_free_text_and_names_are_escaped_everywhere(world):
    evil = "<script>alert(1)</script>"
    b = new_browser(world)
    b.signup(name="<b>Eve</b>", email="eve@example.com")
    for number in range(1, 9):
        b.answer(number, "other", evil)
    b.post("/onboarding/review", {})
    choose_fc(b)
    consent = b.get("/sharing/new").get_data(as_text=True)
    assert evil not in consent and "&lt;script&gt;" in consent and "<b>Eve</b>" not in consent
    confirm(b, ["profile_answers"])
    page = make_fc_browser(world).get(f"/fc/clients/{uid('eve@example.com')}").get_data(as_text=True)
    listing = make_fc_browser(world).get("/fc").get_data(as_text=True)
    for html in (page, listing):
        assert evil not in html and "<b>Eve</b>" not in html
    assert "&lt;script&gt;" in page and "&lt;b&gt;Eve&lt;/b&gt;" in page


# ---------- nothing else changed ----------

def test_dashboard_demo_values_are_not_shared_or_changed(world):
    b = make_client(world)
    before = re.findall(r'class="ring-number">(\d+)<|class="bar-value">(\d+)%<', b.get("/dashboard").get_data(as_text=True))
    share(b, list(ALL))
    after = re.findall(r'class="ring-number">(\d+)<|class="bar-value">(\d+)%<', b.get("/dashboard").get_data(as_text=True))
    assert before == after and before
    page = make_fc_browser(world).get(f"/fc/clients/{uid('client@example.com')}").get_data(as_text=True)
    assert "Illustrative demo values" not in page and "Coverage score" not in page


def test_error_text_on_the_consent_page_cannot_be_injected_through_the_url(world):
    b = make_client(world)
    choose_fc(b)
    page = b.get("/sharing/new?error=Call+this+number+now").get_data(as_text=True)
    assert "Call this number now" not in page and 'role="alert"' not in page
    assert "Tick at least one" in b.get("/sharing/new?error=none_ticked").get_data(as_text=True)