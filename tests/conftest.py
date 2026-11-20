import os

import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app import create_app
from app.extensions import db

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _database_target(url):
    """(host, port, database) for a URL, or None if it cannot be parsed."""
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError):
        return None
    return (parsed.host or "localhost", parsed.port or 5432, parsed.database)


def pytest_configure(config):
    """Stop the whole run unless the test database is clearly separate from the real one.

    The test fixtures drop every table. The target database name must end in "_test"
    and must not be the DATABASE_URL database (from the environment or .env).
    """
    if not TEST_DATABASE_URL:
        pytest.exit(
            "Refusing to run: TEST_DATABASE_URL is not set. Point it at a database whose name "
            "ends in _test, for example postgresql+psycopg://postgres@127.0.0.1:5433/insurance_navigator_test.",
            returncode=4,
        )
    target = _database_target(TEST_DATABASE_URL)
    if target is None or not target[2] or not target[2].endswith("_test"):
        pytest.exit(
            "Refusing to run: TEST_DATABASE_URL must name a database ending in _test "
            "(the tests drop all tables).",
            returncode=4,
        )
    real_url = os.environ.get("DATABASE_URL") or dotenv_values().get("DATABASE_URL")
    if real_url and _database_target(real_url) == target:
        pytest.exit("Refusing to run: TEST_DATABASE_URL is the same database as DATABASE_URL.", returncode=4)


@pytest.fixture()
def app():
    app = create_app(
        {"TESTING": True, "SECRET_KEY": "test-secret", "SQLALCHEMY_DATABASE_URI": TEST_DATABASE_URL}
    )
    with app.app_context():
        # Second line of defence: never wipe anything but the checked test database.
        assert db.engine.url.database.endswith("_test"), "refusing to drop tables outside a *_test database"
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


class Browser:
    """Small helper around the Flask test client that adds the CSRF token."""

    def __init__(self, client):
        self.client = client

    def token(self):
        # Pages that render a form create the token; the helper just ensures one exists.
        with self.client.session_transaction() as session:
            return session.setdefault("_csrf", "test-csrf-token")

    def get(self, url, **kwargs):
        return self.client.get(url, **kwargs)

    def post(self, url, data=None, **kwargs):
        data = dict(data or {})
        data.setdefault("_csrf", self.token())
        return self.client.post(url, data=data, **kwargs)

    def signup(self, name="Alex Tan", email="alex@example.com", password="correct-horse-9"):
        return self.post("/signup", {"full_name": name, "email": email, "password": password})

    def login(self, email="alex@example.com", password="correct-horse-9"):
        return self.post("/login", {"email": email, "password": password})

    def answer(self, number, key, other_text=""):
        return self.post(
            f"/onboarding/question/{number}",
            {"answer": key, "other_text": other_text, "action": "next"},
        )

    def skip(self, number):
        return self.post(f"/onboarding/question/{number}", {"action": "skip"})

    def onboard(self, answers=None):
        """Answer all eight questions and confirm the review."""
        answers = answers or {1: "30", 2: "full_time", 3: "spouse_child", 4: "100_300",
                              5: "family_income", 6: "at_60", 7: "private", 8: "personal_employer"}
        for number, key in answers.items():
            if key is None:
                self.skip(number)
            else:
                self.answer(number, key)
        return self.post("/onboarding/review", {})

    def logout(self):
        return self.post("/logout")


@pytest.fixture()
def browser(app):
    return Browser(app.test_client())


@pytest.fixture()
def seeded(app):
    """Load the fictional demo plans, clauses and comparison cells."""
    result = app.test_cli_runner().invoke(args=["seed-demo"])
    assert result.exit_code == 0, result.output
    return app