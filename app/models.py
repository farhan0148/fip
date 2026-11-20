from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, UniqueConstraint

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(254), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    # Shared by the client app and the future FC portal (design.md section 5).
    role = db.Column(db.String(10), nullable=False, default="client", server_default="client")
    language = db.Column(db.String(5), nullable=False, default="en", server_default="en")
    # Set when the user confirms the review screen. Gates dashboard and other pages.
    review_confirmed_at = db.Column(db.DateTime(timezone=True))
    # Set the first time the dashboard is shown. Drives "Welcome" vs "Welcome back".
    dashboard_first_seen_at = db.Column(db.DateTime(timezone=True))
    is_demo = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    answers = db.relationship(
        "QuestionnaireAnswer", back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (CheckConstraint("role IN ('client', 'fc')", name="ck_users_role"),)

    @property
    def first_name(self):
        parts = self.full_name.split()
        return parts[0] if parts else self.full_name


class QuestionnaireAnswer(db.Model):
    """One row per user and question. A skipped question has status 'skipped'."""

    __tablename__ = "questionnaire_answers"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(10), nullable=False)
    # List of option keys. One item for now (single-select); a list keeps the
    # design.md model (selected[]) so multi-select can come later.
    selected = db.Column(db.JSON, nullable=False, default=list)
    other_text = db.Column(db.String(200))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    user = db.relationship("User", back_populates="answers")

    __table_args__ = (
        UniqueConstraint("user_id", "question_id", name="uq_answer_user_question"),
        CheckConstraint("status IN ('answered', 'skipped')", name="ck_answer_status"),
    )


class Policy(db.Model):
    """Plan catalogue. Only fictional demo rows exist at this checkpoint."""

    __tablename__ = "policies"

    id = db.Column(db.Integer, primary_key=True)
    insurer = db.Column(db.String(100), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), nullable=False)
    monthly_premium_cents = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(300), nullable=False)
    document_ref = db.Column(db.String(200))
    # Simulator inputs. death_cover_cents: 0 means no death benefit, NULL means not set.
    # cover_ends_at_age: last age the cover applies; NULL means it does not expire.
    death_cover_cents = db.Column(db.BigInteger)
    cover_ends_at_age = db.Column(db.Integer)
    is_demo = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())


CELL_STATES = ("included", "optional_rider", "unconfirmed", "not_included")


class PolicyClause(db.Model):
    """One numbered line of source wording on a page of a policy document."""

    __tablename__ = "policy_clauses"

    id = db.Column(db.Integer, primary_key=True)
    policy_id = db.Column(
        db.Integer, db.ForeignKey("policies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page = db.Column(db.Integer, nullable=False)
    clause = db.Column(db.String(20), nullable=False)
    line = db.Column(db.Integer, nullable=False)
    text = db.Column(db.String(500), nullable=False)

    policy = db.relationship("Policy")

    __table_args__ = (UniqueConstraint("policy_id", "page", "line", name="uq_clause_policy_page_line"),)


class ComparisonCell(db.Model):
    """One finding for one policy on one comparison row. A missing row means "not stated"."""

    __tablename__ = "comparison_cells"

    id = db.Column(db.Integer, primary_key=True)
    policy_id = db.Column(
        db.Integer, db.ForeignKey("policies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    row_key = db.Column(db.String(40), nullable=False)
    state = db.Column(db.String(20), nullable=False)
    finding = db.Column(db.String(300), nullable=False)
    clause_id = db.Column(db.Integer, db.ForeignKey("policy_clauses.id", ondelete="SET NULL"))

    policy = db.relationship("Policy")
    clause = db.relationship("PolicyClause")

    __table_args__ = (
        UniqueConstraint("policy_id", "row_key", name="uq_cell_policy_row"),
        CheckConstraint(
            "state IN ('included', 'optional_rider', 'unconfirmed', 'not_included')",
            name="ck_cell_state",
        ),
        # Requirements R3: "Not included" only when the wording supports it.
        CheckConstraint(
            "state <> 'not_included' OR clause_id IS NOT NULL", name="ck_cell_not_included_cited"
        ),
    )


class ComparisonFlag(db.Model):
    """A client asked for an FC to verify an Unconfirmed cell. Stays private until sharing exists."""

    __tablename__ = "comparison_flags"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_id = db.Column(db.Integer, db.ForeignKey("policies.id", ondelete="CASCADE"), nullable=False)
    row_key = db.Column(db.String(40), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "policy_id", "row_key", name="uq_flag_user_policy_row"),
    )


class Scenario(db.Model):
    """The signed-in user's saved simulator scenario (one per user for now)."""

    __tablename__ = "scenarios"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    name = db.Column(db.String(80), nullable=False, default="My scenario")
    # Validated, parsed whole-dollar inputs: {"fields": {...}, "plans": [policy ids], "events": [...]}
    inputs = db.Column(db.JSON, nullable=False)
    # Result recalculated by Flask at save time.
    result = db.Column(db.JSON, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
