from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, UniqueConstraint, event

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
    """Plan catalogue: imported Prudential plans (slug set) and the old fictional demo rows (is_demo)."""

    __tablename__ = "policies"

    id = db.Column(db.Integer, primary_key=True)
    insurer = db.Column(db.String(100), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), nullable=False)
    # NULL means the premium is not known. Never fill it with a guess.
    monthly_premium_cents = db.Column(db.Integer)
    description = db.Column(db.String(300), nullable=False)
    document_ref = db.Column(db.String(200))
    # Simulator inputs. death_cover_cents: 0 means no death benefit, NULL means not set.
    # cover_ends_at_age: last age the cover applies; NULL means it does not expire.
    death_cover_cents = db.Column(db.BigInteger)
    cover_ends_at_age = db.Column(db.Integer)
    is_demo = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    # Hidden rows stay in the database (flags and scenarios point at them) but are not in user-facing lists.
    is_hidden = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    # Prudential catalogue fields (prudential-catalogue.json). slug is NULL for the demo rows.
    slug = db.Column(db.String(100), unique=True)
    kind = db.Column(db.String(30))
    categories = db.Column(db.JSON)
    catalogue_status = db.Column(db.String(50))
    coverage_focus = db.Column(db.Text)
    contract_status = db.Column(db.String(50))
    product_url = db.Column(db.String(500))
    applicable_base_plans = db.Column(db.JSON)  # plan names, as written in the catalogue
    catalogue_as_of = db.Column(db.Date)


class Document(db.Model):
    """A source document listed in the catalogue (brochure, sample contract, rate-table page...)."""

    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    doc_id = db.Column(db.String(120), nullable=False, unique=True)
    type = db.Column(db.String(60), nullable=False)
    url = db.Column(db.String(600))
    language = db.Column(db.String(10))
    version = db.Column(db.String(200))
    page_count = db.Column(db.Integer)
    sha256 = db.Column(db.String(64))
    local_path = db.Column(db.String(300))


class PlanDocument(db.Model):
    __tablename__ = "plan_documents"

    policy_id = db.Column(db.Integer, db.ForeignKey("policies.id", ondelete="CASCADE"), primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    position = db.Column(db.Integer, nullable=False, default=0)


class DocumentVersion(db.Model):
    """One ingested file: the exact bytes (sha256) that the stored paragraphs came from."""

    __tablename__ = "document_versions"

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    version = db.Column(db.String(200))
    page_count = db.Column(db.Integer, nullable=False)
    ingested_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    document = db.relationship("Document")

    __table_args__ = (UniqueConstraint("document_id", "sha256", name="uq_docversion_document_sha"),)


class Paragraph(db.Model):
    """A paragraph of a source PDF. bbox is in PDF points, origin top-left, pages are 1-based."""

    __tablename__ = "paragraphs"

    id = db.Column(db.Integer, primary_key=True)
    document_version_id = db.Column(
        db.Integer, db.ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page = db.Column(db.Integer, nullable=False)
    ordinal = db.Column(db.Integer, nullable=False)  # position on the page
    x0 = db.Column(db.Float, nullable=False)
    y0 = db.Column(db.Float, nullable=False)
    x1 = db.Column(db.Float, nullable=False)
    y1 = db.Column(db.Float, nullable=False)
    text = db.Column(db.Text, nullable=False)  # whitespace-normalised
    paragraph_sha256 = db.Column(db.String(64), nullable=False)
    clause_number = db.Column(db.String(30))
    heading = db.Column(db.String(200))
    scope = db.Column(db.JSON, nullable=False)  # plan slugs this wording applies to

    document_version = db.relationship("DocumentVersion")

    __table_args__ = (UniqueConstraint("document_version_id", "page", "ordinal", name="uq_paragraph_position"),)

    @property
    def bbox(self):
        return (self.x0, self.y0, self.x1, self.y1)


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
    # Catalogue plans cite a paragraph of the ingested contract PDF instead of a demo clause.
    paragraph_id = db.Column(db.Integer, db.ForeignKey("paragraphs.id", ondelete="SET NULL"))
    # Hash of the retrieved paragraphs this finding was made from (so the cache knows when it is stale).
    paragraphs_hash = db.Column(db.String(64))

    policy = db.relationship("Policy")
    clause = db.relationship("PolicyClause")
    paragraph = db.relationship("Paragraph")

    __table_args__ = (
        UniqueConstraint("policy_id", "row_key", name="uq_cell_policy_row"),
        CheckConstraint(
            "state IN ('included', 'optional_rider', 'unconfirmed', 'not_included')",
            name="ck_cell_state",
        ),
        # Requirements R3: "Not included" only when the wording supports it.
        CheckConstraint(
            "state <> 'not_included' OR clause_id IS NOT NULL OR paragraph_id IS NOT NULL",
            name="ck_cell_not_included_cited",
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


# ---------- FC accounts, assignment and consent-based sharing ----------

SHARE_CATEGORIES = ("profile_answers", "simulator", "flagged_items")
SHARE_ACTIONS = ("granted", "revoked", "viewed")


class FCProfile(db.Model):
    """Extra details for a user whose role is 'fc'. Created by `flask create-fc` or `flask seed-demo`."""

    __tablename__ = "fc_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    referral_code = db.Column(db.String(20), nullable=False, unique=True)
    speciality = db.Column(db.String(120), nullable=False, default="")
    languages = db.Column(db.JSON, nullable=False, default=list)

    user = db.relationship("User")


class FCAssignment(db.Model):
    """The one FC a client has chosen. Created only when the client confirms sharing."""

    __tablename__ = "fc_assignments"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    fc_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    method = db.Column(db.String(10), nullable=False)
    assigned_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (CheckConstraint("method IN ('referral', 'random')", name="ck_assignment_method"),)


class ShareGrant(db.Model):
    """Permission for one FC to see one category of one client's data. revoked_at NULL means active."""

    __tablename__ = "share_grants"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fc_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    category = db.Column(db.String(30), nullable=False)
    granted_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    revoked_at = db.Column(db.DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "category IN ('profile_answers', 'simulator', 'flagged_items')", name="ck_grant_category"
        ),
        # One active grant per client, FC and category. A revoked row does not block a new grant.
        db.Index(
            "uq_active_grant", "client_id", "fc_id", "category",
            unique=True, postgresql_where=db.text("revoked_at IS NULL"),
        ),
    )


class ShareEvent(db.Model):
    """Append-only log of consent changes and FC views. The app only ever inserts rows here."""

    __tablename__ = "share_events"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fc_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    category = db.Column(db.String(30), nullable=False)
    action = db.Column(db.String(10), nullable=False)
    at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("action IN ('granted', 'revoked', 'viewed')", name="ck_event_action"),
        CheckConstraint(
            "category IN ('profile_answers', 'simulator', 'flagged_items')", name="ck_event_category"
        ),
    )


@event.listens_for(ShareEvent, "before_update")
def _share_events_are_append_only(mapper, connection, target):
    raise RuntimeError("share_events is append-only: rows are never updated")


class PlanSummary(db.Model):
    """Cached plan summary, the same for every user (it is built from the plan's own clauses only).

    language "en" holds the source summary. Other languages are translations of the validated
    English statements. source "standard" with no statements means a translation was not available.
    """

    __tablename__ = "plan_summaries"

    id = db.Column(db.Integer, primary_key=True)
    policy_id = db.Column(
        db.Integer, db.ForeignKey("policies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    language = db.Column(db.String(5), nullable=False)
    # [{"text": str, "clause_ids": [int, ...]}, ...]
    statements = db.Column(db.JSON, nullable=False, default=list)
    source = db.Column(db.String(10), nullable=False)
    # Why a standard summary was used (timeout, error, invalid_output, ...). NULL for AI summaries.
    reason = db.Column(db.String(30))
    # Hash of the plan, its clauses (and, for translations, the English text they came from).
    clauses_hash = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("policy_id", "language", name="uq_summary_policy_language"),
        CheckConstraint("source IN ('ai', 'standard')", name="ck_summary_source"),
    )
