import os
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, g, render_template

from .extensions import db, migrate
from .ratelimit import FailureLimiter


def _database_url(raw):
    """Accept common Postgres URL spellings and use the psycopg 3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg://" + raw[len(prefix):]
    return raw


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__)

    secret = os.environ.get("SECRET_KEY")
    database_url = os.environ.get("DATABASE_URL")
    app.config.update(
        SECRET_KEY=secret,
        SQLALCHEMY_DATABASE_URI=_database_url(database_url) if database_url else None,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    if not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY is not set. Copy .env.example to .env and fill it in.")
    if not app.config["SQLALCHEMY_DATABASE_URI"]:
        raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")

    db.init_app(app)
    migrate.init_app(app, db)

    from . import ai, auth, compare, dashboard, fc_portal, onboarding, plans, sharing_routes, simulator_routes
    from .cli import register_commands
    from .security import add_security_headers, csrf_token, load_current_user

    app.register_blueprint(auth.bp)
    app.register_blueprint(onboarding.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(compare.bp)
    app.register_blueprint(plans.bp)
    app.register_blueprint(simulator_routes.bp)
    register_commands(app)
    ai.register(app)
    app.register_blueprint(sharing_routes.bp)
    app.register_blueprint(fc_portal.bp)
    app.config.setdefault("REFERRAL_FAILURE_LIMIT", 5)      # wrong referral codes ...
    app.config.setdefault("REFERRAL_FAILURE_WINDOW", 600)   # ... per client, per this many seconds
    app.extensions["referral_limiter"] = FailureLimiter(
        app.config["REFERRAL_FAILURE_LIMIT"], app.config["REFERRAL_FAILURE_WINDOW"]
    )

    app.before_request(load_current_user)
    app.after_request(add_security_headers)

    # Nav labels in one place. Disabled until the review is confirmed.
    nav_items = (
        ("Dashboard", "dashboard.index"),
        ("Plans", "plans.index"),
        ("Compare", "compare.index"),
        ("Simulator", "simulator.index"),
        ("Settings", "dashboard.settings"),
    )

    @app.context_processor
    def inject_globals():
        return {"csrf_token": csrf_token, "current_user": g.get("user"), "nav_items": nav_items}

    @app.template_filter("money")
    def money(cents):
        dollars, remainder = divmod(int(cents), 100)
        return f"S${dollars:,}" if remainder == 0 else f"S${dollars:,}.{remainder:02d}"

    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", title="Something went wrong", message=error.description), 400

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", title="Page not found", message="That page does not exist."), 404

    return app


from . import models  # noqa: E402,F401  (so Alembic sees the models)
