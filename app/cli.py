"""Flask CLI commands. `flask seed-demo` loads fictional demo data only."""

import click
from werkzeug.security import generate_password_hash

from .demo_data import CELLS, CLAUSES, DEMO_POLICIES
from .extensions import db
from .models import ComparisonCell, ComparisonFlag, Policy, PolicyClause, User

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo-password-1"


def _seed_policy_documents(policy):
    """Add sample clauses and comparison cells for a policy that has none yet."""
    if PolicyClause.query.filter_by(policy_id=policy.id).first():
        return 0
    by_location = {}
    for page, clause, line, text in CLAUSES.get(policy.name, ()):
        row = PolicyClause(policy_id=policy.id, page=page, clause=clause, line=line, text=text)
        db.session.add(row)
        by_location[(page, line)] = row
    db.session.flush()
    for row_key, state, finding, location in CELLS.get(policy.name, ()):
        db.session.add(
            ComparisonCell(
                policy_id=policy.id,
                row_key=row_key,
                state=state,
                finding=finding,
                clause_id=by_location[location].id if location else None,
            )
        )
    return len(by_location) + len(CELLS.get(policy.name, ()))


def register_commands(app):
    @app.cli.command("seed-demo")
    @click.option("--reset", is_flag=True, help="Delete existing demo plans first (accounts are kept).")
    def seed_demo(reset):
        """Load fictional demo plans, sample clauses and one demo account (no answers)."""
        if reset:
            demo_ids = [p.id for p in Policy.query.filter_by(is_demo=True)]
            if demo_ids:
                ComparisonFlag.query.filter(ComparisonFlag.policy_id.in_(demo_ids)).delete(synchronize_session=False)
                ComparisonCell.query.filter(ComparisonCell.policy_id.in_(demo_ids)).delete(synchronize_session=False)
                PolicyClause.query.filter(PolicyClause.policy_id.in_(demo_ids)).delete(synchronize_session=False)
                Policy.query.filter(Policy.id.in_(demo_ids)).delete(synchronize_session=False)
            db.session.commit()

        added = 0
        for data in DEMO_POLICIES:
            policy = Policy.query.filter_by(is_demo=True, name=data["name"]).first()
            if policy is None:
                policy = Policy(is_demo=True, **data)
                db.session.add(policy)
                db.session.flush()
                added += 1
            if not policy.document_ref:  # plans seeded by an earlier build have no document name
                policy.document_ref = data["document_ref"]
            if policy.death_cover_cents is None:  # plans seeded before the simulator existed
                policy.death_cover_cents = data["death_cover_cents"]
                policy.cover_ends_at_age = data["cover_ends_at_age"]
            added += _seed_policy_documents(policy)

        if not User.query.filter_by(email=DEMO_EMAIL).first():
            db.session.add(
                User(
                    full_name="Alex Demo",
                    email=DEMO_EMAIL,
                    password_hash=generate_password_hash(DEMO_PASSWORD),
                    is_demo=True,
                )
            )
            added += 1
        db.session.commit()
        click.echo(f"Seeded {added} fictional demo row(s).")
        click.echo(f"Demo login: {DEMO_EMAIL} / {DEMO_PASSWORD}")
