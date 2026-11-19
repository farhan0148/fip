from app.extensions import db
from app.models import QuestionnaireAnswer, User

FULL_ANSWERS = {1: "30", 2: "full_time", 3: "spouse_child", 4: "100_300",
                5: "family_income", 6: "at_60", 7: "private", 8: "personal_employer"}


def finish_questions(browser, skip=()):
    for number, key in FULL_ANSWERS.items():
        if number in skip:
            browser.skip(number)
        else:
            browser.answer(number, key)


def confirm(browser):
    return browser.post("/onboarding/review", {})


# ---------- sign-up and login ----------

def test_signup_validation_errors(browser):
    response = browser.post("/signup", {"full_name": "", "email": "nope", "password": "short"})
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "Enter your full name." in body
    assert "Enter a valid email address." in body
    assert "Use at least 8 characters." in body
    assert User.query.count() == 0


def test_signup_hashes_password_and_starts_onboarding(browser):
    response = browser.signup()
    assert response.status_code == 302 and response.location.endswith("/onboarding")
    user = User.query.one()
    assert user.password_hash != "correct-horse-9" and "correct-horse-9" not in user.password_hash
    assert browser.get("/onboarding").location.endswith("/onboarding/question/1")


def test_duplicate_email_rejected(browser):
    browser.signup()
    browser.logout()
    response = browser.signup()
    assert response.status_code == 400
    assert "already exists" in response.get_data(as_text=True)
    assert User.query.count() == 1


def test_login_wrong_password_and_unknown_email(browser):
    browser.signup()
    browser.logout()
    for email, password in (("alex@example.com", "wrong-password"), ("nobody@example.com", "whatever-123")):
        response = browser.login(email, password)
        assert response.status_code == 400
        assert "The email or password is incorrect." in response.get_data(as_text=True)
    assert browser.get("/onboarding").status_code == 302  # still logged out


def test_post_without_csrf_token_is_rejected(browser):
    response = browser.client.post("/signup", data={"full_name": "A", "email": "a@example.com", "password": "password-1"})
    assert response.status_code == 400
    assert User.query.count() == 0


def test_login_resumes_unfinished_onboarding(browser):
    browser.signup()
    browser.answer(1, "30")
    browser.answer(2, "full_time")
    browser.logout()
    response = browser.login()
    assert response.location.endswith("/onboarding")
    assert browser.get("/onboarding").location.endswith("/onboarding/question/3")


def test_login_goes_to_dashboard_once_complete(browser):
    browser.signup()
    finish_questions(browser)
    confirm(browser)
    browser.logout()
    assert browser.login().location.endswith("/dashboard")


# ---------- gating ----------

def test_pages_locked_until_review_confirmed(browser):
    assert browser.get("/dashboard").location.endswith("/login")
    browser.signup()
    for path in ("/dashboard", "/plans", "/compare", "/simulator"):
        assert browser.get(path).location.endswith("/onboarding"), path
    finish_questions(browser)
    assert browser.get("/dashboard").location.endswith("/onboarding")  # reviewed but not confirmed
    confirm(browser)
    for path in ("/dashboard", "/plans", "/compare", "/simulator"):
        assert browser.get(path).status_code == 200, path


def test_cannot_jump_ahead_of_progress(browser):
    browser.signup()
    assert browser.get("/onboarding/question/5").location.endswith("/onboarding")
    assert browser.get("/onboarding/review").location.endswith("/onboarding/question/1")


# ---------- questionnaire ----------

def test_next_requires_a_valid_answer(browser):
    browser.signup()
    response = browser.post("/onboarding/question/1", {"action": "next"})
    assert response.status_code == 400 and "Choose an answer" in response.get_data(as_text=True)
    response = browser.answer(1, "not-an-option")
    assert response.status_code == 400
    assert QuestionnaireAnswer.query.count() == 0


def test_other_needs_text_and_replaces_selection(browser):
    browser.signup()
    response = browser.answer(1, "other", "   ")
    assert response.status_code == 400 and "Type your answer" in response.get_data(as_text=True)
    assert QuestionnaireAnswer.query.count() == 0

    browser.answer(1, "31_45")
    browser.answer(1, "other", "Thirty-two")
    row = QuestionnaireAnswer.query.one()
    assert row.selected == ["other"] and row.other_text == "Thirty-two"

    browser.answer(1, "30")
    row = QuestionnaireAnswer.query.one()
    assert row.selected == ["30"] and row.other_text is None


def test_other_text_too_long_rejected(browser):
    browser.signup()
    assert browser.answer(1, "other", "x" * 201).status_code == 400


def test_skip_records_unanswered_and_review_says_not_answered(browser):
    browser.signup()
    finish_questions(browser, skip=(4,))
    assert QuestionnaireAnswer.query.filter_by(question_id="q4").one().status == "skipped"
    page = browser.get("/onboarding/review").get_data(as_text=True)
    assert "Not answered" in page and page.count("Not answered") == 1


def test_answers_survive_back_navigation_and_reload(browser):
    browser.signup()
    browser.answer(1, "46_plus")
    browser.post("/onboarding/question/2", {"action": "back", "answer": "self_employed"})
    page = browser.get("/onboarding/question/1").get_data(as_text=True)
    assert 'value="46_plus" checked' in page
    # A valid pick made before pressing Back was saved too.
    assert QuestionnaireAnswer.query.filter_by(question_id="q2").one().selected == ["self_employed"]
    page = browser.get("/onboarding/question/2").get_data(as_text=True)
    assert 'value="self_employed" checked' in page


def test_back_with_incomplete_other_never_blocks(browser):
    browser.signup()
    browser.answer(1, "30")
    response = browser.post("/onboarding/question/2", {"action": "back", "answer": "other", "other_text": ""})
    assert response.status_code == 302 and response.location.endswith("/onboarding/question/1")


def test_q8_label_and_destination(browser):
    browser.signup()
    for number in range(1, 8):
        browser.answer(number, FULL_ANSWERS[number])
    page = browser.get("/onboarding/question/8").get_data(as_text=True)
    assert "Review answers" in page
    assert browser.answer(8, "none").location.endswith("/onboarding/review")


# ---------- review ----------

def test_edit_from_review_returns_to_review(browser):
    browser.signup()
    finish_questions(browser)
    page = browser.get("/onboarding/question/1?from=review").get_data(as_text=True)
    assert ">Save<" in page
    response = browser.post(
        "/onboarding/question/1?from=review", {"answer": "46_plus", "action": "save"}
    )
    assert response.location.endswith("/onboarding/review")
    assert "46 or above" in browser.get("/onboarding/review").get_data(as_text=True)


def test_edit_a_skipped_answer_from_review(browser):
    browser.signup()
    finish_questions(browser, skip=(3,))
    browser.post("/onboarding/question/3?from=review", {"answer": "partner_only", "action": "save"})
    assert "Not answered" not in browser.get("/onboarding/review").get_data(as_text=True)


# ---------- dashboard ----------

def test_welcome_then_welcome_back_and_data_persists(browser):
    browser.signup(name="Alex Tan")
    finish_questions(browser)
    assert confirm(browser).location.endswith("/dashboard")
    first = browser.get("/dashboard").get_data(as_text=True)
    assert "Welcome, Alex" in first
    second = browser.get("/dashboard").get_data(as_text=True)
    assert "Welcome back, Alex" in second
    assert "Age:</span> 30" in second
    assert QuestionnaireAnswer.query.count() == 8


def test_dashboard_marks_placeholders_and_uses_fc_wording(browser):
    browser.signup()
    finish_questions(browser)
    confirm(browser)
    page = browser.get("/dashboard").get_data(as_text=True)
    assert "Financial Consultant (FC)" in page
    assert page.index("Financial Consultant (FC)") < page.index("Send to my FC")
    assert "Illustrative demo values" in page
    assert "$" not in page.replace("S$", "")  # S$ everywhere if any currency is shown


def test_dashboard_plans_are_labelled_demo(browser):
    from app.models import Policy
    db.session.add(Policy(insurer="Demo", name="Sample Plan", type="Term life",
                          monthly_premium_cents=8900, description="Demo.", is_demo=True))
    db.session.commit()
    browser.signup()
    finish_questions(browser)
    confirm(browser)
    page = browser.get("/dashboard").get_data(as_text=True)
    assert "Fictional demo data" in page and "Demo data" in page and "S$89" in page


def test_summary_only_uses_answered_questions(browser):
    browser.signup()
    finish_questions(browser, skip=(4, 7))
    confirm(browser)
    page = browser.get("/dashboard").get_data(as_text=True)
    assert "Monthly budget" not in page and "Hospital preference" not in page
    assert "Employment:</span> Full-time employee" in page


def test_priorities_respond_to_answers_and_skips_trigger_nothing(browser):
    # Q3 dependants answer drives the dependants priority.
    browser.signup(email="a@example.com")
    finish_questions(browser)
    confirm(browser)
    page = browser.get("/dashboard").get_data(as_text=True)
    assert "Protecting the people who rely on your income" in page
    assert "Dependants: My spouse and one child." in page
    browser.logout()

    # Same answers but Q3 and Q5 skipped: dependants must not appear.
    browser.signup(email="b@example.com")
    finish_questions(browser, skip=(3, 5, 8))
    confirm(browser)
    page = browser.get("/dashboard").get_data(as_text=True)
    assert "Protecting the people who rely on your income" not in page


def test_priorities_change_when_an_answer_is_edited(browser):
    browser.signup()
    finish_questions(browser)
    browser.post("/onboarding/question/3?from=review", {"answer": "no_one", "action": "save"})
    browser.post("/onboarding/question/5?from=review", {"answer": "hospital", "action": "save"})
    confirm(browser)
    page = browser.get("/dashboard").get_data(as_text=True)
    assert "Dependants: My spouse and one child." not in page
    assert "Hospital and medical cover" in page
    assert "You said hospital cover matters most." in page


def test_empty_state_when_nothing_triggers(browser):
    browser.signup()
    for number in range(1, 9):
        browser.skip(number)
    confirm(browser)
    page = browser.get("/dashboard").get_data(as_text=True)
    assert "don't point to a specific topic" in page or "don&#39;t point to a specific topic" in page
    assert "nothing to summarise" in page


def test_other_answers_do_not_trigger_priorities(browser):
    browser.signup()
    for number in range(1, 9):
        browser.answer(number, "other", "Something else")
    confirm(browser)
    page = browser.get("/dashboard").get_data(as_text=True)
    assert "point to a specific topic" in page


def test_user_text_is_escaped(browser):
    browser.signup(name="<b>Eve</b>")
    for number in range(1, 9):
        browser.answer(number, "other", "<script>alert(1)</script>")
    confirm(browser)
    page = browser.get("/dashboard").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
