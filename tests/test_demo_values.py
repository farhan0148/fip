import re

from app import demo_fixture



def numbers(page):
    """The demo score, radar points and bar percentages as rendered."""
    section = page[page.index('class="demo-section"'): page.index('class="card priorities-card"')]
    return {
        "score": re.search(r'class="ring-number">(\d+)<', section).group(1),
        "radar": re.search(r'<polygon points="([^"]+)" class="radar-shape"', section).group(1),
        "bars": re.findall(r'class="bar-value">(\d+)%<', section),
    }


def test_fixture_values_are_fixed_and_valid():
    data = demo_fixture.load()
    assert data["score"] == 62
    assert [(a["name"], a["value"]) for a in data["axes"]] == [
        ("Life cover", 72), ("Hospitalisation", 65), ("Critical illness", 58),
        ("Disability", 35), ("Retirement", 48), ("Liability", 20),
    ]
    assert demo_fixture.load() is demo_fixture.load()


def test_section_is_labelled_as_illustrative_demo_data(browser):
    browser.signup()
    browser.onboard()
    page = browser.get("/dashboard").get_data(as_text=True)
    assert "Illustrative demo values" in page
    assert "do not depend on any answers" in page
    assert "Coverage score (demo)" in page and "Coverage breakdown (demo)" in page
    assert numbers(page)["score"] == "62"
    assert numbers(page)["bars"] == ["72", "65", "58", "35", "48", "20"]


def test_values_do_not_change_on_reload_or_between_users(browser):
    browser.signup(email="a@example.com")
    browser.onboard()
    first = numbers(browser.get("/dashboard").get_data(as_text=True))
    assert numbers(browser.get("/dashboard").get_data(as_text=True)) == first
    browser.logout()

    # A different user with different answers sees identical demo values.
    browser.signup(email="b@example.com")
    browser.onboard({1: "46_plus", 2: "self_employed", 3: "no_one", 4: "under_100",
                     5: "hospital", 6: "before_60", 7: "public_b1", 8: "none"})
    assert numbers(browser.get("/dashboard").get_data(as_text=True)) == first


def test_priorities_still_follow_answers_while_demo_values_stay_put(browser):
    browser.signup()
    browser.onboard()
    before = browser.get("/dashboard").get_data(as_text=True)
    assert "Protecting the people who rely on your income" in before
    browser.logout()
    browser.signup(email="c@example.com")
    # Nothing here points to a topic (Q8 skipped), so the priorities are empty.
    browser.onboard({1: "30", 2: "full_time", 3: "no_one", 4: "not_sure", 5: "not_sure",
                     6: "at_65", 7: "no_preference", 8: None})
    after = browser.get("/dashboard").get_data(as_text=True)
    assert "Protecting the people who rely on your income" not in after
    assert numbers(after) == numbers(before)
