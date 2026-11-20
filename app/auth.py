import re

from flask import Blueprint, g, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .models import User
from .security import check_csrf, home_for, session_required

bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8
MAX_PASSWORD = 128
# Used so a login for an unknown email takes about as long as a wrong password.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def _start_session(user):
    session.clear()
    session["user_id"] = user.id
    session.permanent = True


@bp.get("/")
def index():
    if g.user:
        return redirect(home_for(g.user))
    return redirect(url_for("auth.signup"))


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if g.user:
        return redirect(home_for(g.user))
    values = {"full_name": "", "email": ""}
    errors = {}
    if request.method == "POST":
        check_csrf()
        values["full_name"] = " ".join((request.form.get("full_name") or "").split())
        values["email"] = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        if not values["full_name"]:
            errors["full_name"] = "Enter your full name."
        elif len(values["full_name"]) > 100:
            errors["full_name"] = "Use 100 characters or fewer."
        if not EMAIL_RE.match(values["email"]) or len(values["email"]) > 254:
            errors["email"] = "Enter a valid email address."
        if len(password) < MIN_PASSWORD:
            errors["password"] = f"Use at least {MIN_PASSWORD} characters."
        elif len(password) > MAX_PASSWORD:
            errors["password"] = f"Use {MAX_PASSWORD} characters or fewer."

        if not errors and User.query.filter_by(email=values["email"]).first():
            errors["email"] = "An account with this email already exists. Log in instead."

        if not errors:
            user = User(
                full_name=values["full_name"],
                email=values["email"],
                password_hash=generate_password_hash(password),
                role="client",  # public sign-up can only ever create clients; FCs come from `flask create-fc`
            )
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                errors["email"] = "An account with this email already exists. Log in instead."
            else:
                _start_session(user)
                return redirect(url_for("onboarding.resume"))
    return render_template(
        "auth/signup.html", values=values, errors=errors, page_title="Create your account"
    ), (400 if errors else 200)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(home_for(g.user))
    values = {"email": ""}
    error = None
    if request.method == "POST":
        check_csrf()
        values["email"] = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=values["email"]).first() if values["email"] else None
        stored = user.password_hash if user else _DUMMY_HASH
        valid = check_password_hash(stored, password[:MAX_PASSWORD]) and user is not None
        if valid:
            _start_session(user)
            return redirect(home_for(user))
        error = "The email or password is incorrect."
    return render_template(
        "auth/login.html", values=values, error=error, page_title="Log in"
    ), (400 if error else 200)


@bp.post("/logout")
@session_required
def logout():
    check_csrf()
    session.clear()
    return redirect(url_for("auth.login"))
