"""Flask CLI commands. `flask seed-demo` loads fictional demo data only; `flask create-fc` makes a real FC."""

import re
import secrets

import click
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from .demo_data import CELLS, CLAUSES, DEMO_FCS, DEMO_POLICIES
from .extensions import db
from .models import ComparisonCell, ComparisonFlag, FCProfile, Policy, PolicyClause, User

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo-password-1"
DEMO_FC_PASSWORD = "demo-fc-password-1"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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


def _unique_referral_code():
    for _ in range(50):
        code = f"FC-{secrets.randbelow(9000) + 1000}"
        if not FCProfile.query.filter_by(referral_code=code).first():
            return code
    raise click.ClickException("Could not find a free referral code. Try again.")


def register_commands(app):
    @app.cli.command("import-catalogue")
    @click.option("--dir", "directory", type=click.Path(file_okay=False), default=None,
                  help="Folder with prudential-catalogue.json (default: data/prudential).")
    def import_catalogue_command(directory):
        """Import the Prudential catalogue (plans and documents). Safe to run again."""
        from pathlib import Path

        from . import catalogue_import

        base = Path(directory) if directory else catalogue_import.default_dir(app)
        try:
            counts = catalogue_import.import_catalogue(base / "prudential-catalogue.json")
        except catalogue_import.CatalogueError as error:
            db.session.rollback()
            raise click.ClickException(str(error)) from error
        click.echo(
            f"Catalogue imported: {counts['plans']} new plans, {counts['documents']} new documents "
            f"({Policy.query.filter(Policy.slug.isnot(None)).count()} plans in total)."
        )

    @app.cli.command("ingest-contracts")
    @click.option("--dir", "directory", type=click.Path(file_okay=False), default=None,
                  help="Folder with the contract PDFs (default: data/prudential).")
    def ingest_contracts_command(directory):
        """Check each sample contract's SHA-256, then store its paragraphs. Safe to run again."""
        from pathlib import Path

        from . import catalogue_import, contracts

        base = Path(directory) if directory else catalogue_import.default_dir(app)
        try:
            results = contracts.ingest_all(base)
        except contracts.IngestError as error:
            db.session.rollback()
            raise click.ClickException(str(error)) from error
        for doc_id, (count, created) in results.items():
            click.echo(f"{doc_id}: {count} paragraphs ({'new version' if created else 'already ingested, refreshed'}).")

    @app.cli.command("seed-meetings")
    @click.option("--client", "client_email", default=DEMO_EMAIL, show_default=True, help="Client account to set up.")
    @click.option("--fc", "fc_email", default="fc.daniel@example.com", show_default=True, help="Demo FC to assign.")
    def seed_meetings(client_email, fc_email):
        """Fictional demo setup for real meetings, notes and ROA drafts (adds only what is missing)."""
        from . import meeting_seed

        client = User.query.filter_by(email=client_email.strip().lower(), role="client").first()
        fc = User.query.filter_by(email=fc_email.strip().lower(), role="fc").first()
        if client is None or fc is None:
            raise click.ClickException("Client or FC not found. Run flask seed-demo first, or check the emails.")
        meetings = meeting_seed.seed_for(client, fc)
        click.echo(f"{client.email} is assigned to {fc.full_name} with three demo meetings:")
        for m in meetings:
            click.echo(f"  #{m.id} {m.title} ({m.status})  client: /meetings/{m.id}  FC: /fc/meetings/{m.id}")

    @app.cli.command("seed-demo")
    @click.option("--reset", is_flag=True, help="Delete existing demo plans first (accounts are kept).")
    def seed_demo(reset):
        """Load fictional demo plans, sample clauses, one demo client and two demo FCs."""
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
                policy = Policy(is_demo=True, is_hidden=True, **data)
                db.session.add(policy)
                db.session.flush()
                added += 1
            policy.is_hidden = True  # the fictional plans are never listed on the Plans pages
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

        for fc in DEMO_FCS:
            user = User.query.filter_by(email=fc["email"]).first()
            if user is None:
                user = User(
                    full_name=fc["name"], email=fc["email"], role="fc", is_demo=True,
                    password_hash=generate_password_hash(DEMO_FC_PASSWORD),
                )
                db.session.add(user)
                db.session.flush()
                added += 1
            if not FCProfile.query.filter_by(user_id=user.id).first():
                db.session.add(FCProfile(user_id=user.id, referral_code=fc["referral_code"],
                                         speciality=fc["speciality"], languages=fc["languages"]))
                added += 1
        db.session.commit()
        click.echo(f"Seeded {added} fictional demo row(s).")
        click.echo(f"Demo client login: {DEMO_EMAIL} / {DEMO_PASSWORD}")
        for fc in DEMO_FCS:
            click.echo(f"Demo FC login: {fc['email']} / {DEMO_FC_PASSWORD}  (referral code {fc['referral_code']})")

    @app.cli.command("create-fc")
    @click.option("--email", required=True, help="Login email for the new FC.")
    @click.option("--name", required=True, help="Full name shown to clients.")
    @click.option("--speciality", default="General protection", show_default=True)
    @click.option("--language", "languages", multiple=True, help="Repeat for each language, e.g. --language English.")
    @click.password_option(prompt="Password (hidden)", confirmation_prompt="Repeat password", help="Prompted, never echoed.")
    def create_fc(email, name, speciality, languages, password):
        """Create a Financial Consultant account. Public sign-up can only create clients."""
        email = email.strip().lower()
        name = " ".join(name.split())
        if not name or len(name) > 100:
            raise click.ClickException("Name must be 1 to 100 characters.")
        if not _EMAIL.match(email) or len(email) > 254:
            raise click.ClickException("Enter a valid email address.")
        if not 8 <= len(password) <= 128:
            raise click.ClickException("Password must be 8 to 128 characters.")
        if User.query.filter_by(email=email).first():
            raise click.ClickException("An account with that email already exists.")
        user = User(full_name=name, email=email, role="fc", password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.flush()
        code = _unique_referral_code()
        db.session.add(FCProfile(user_id=user.id, referral_code=code, speciality=speciality.strip(),
                                 languages=list(languages) or ["English"]))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise click.ClickException("Could not create the account. Try again.") from None
        click.echo(f"Created FC {name} <{email}>. Referral code: {code}")
