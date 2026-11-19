# Insurance Navigator: local setup (Windows PowerShell)

Checkpoint 1: sign-up, login, questionnaire, editable review, coverage dashboard.
Stack: Python 3.11+, Flask, Jinja, SQLAlchemy, Flask-Migrate (Alembic), PostgreSQL.

Run every command from the repository root (`C:\Farhan\FIP`). The commands call the
virtual environment's programs directly, so you do not need to activate it or change
the PowerShell execution policy.

## 1. Python environment

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
```

## 2. PostgreSQL

Use either option.

**Option A: you already have PostgreSQL.** Create an empty database, for example:

```powershell
createdb -U postgres insurance_navigator
```

**Option B: no PostgreSQL installed.** This downloads a portable PostgreSQL 16 (about 315 MB,
first run only) into `.devpg\` (git-ignored). It does not install anything system-wide and
listens on `127.0.0.1:5433` only.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev-postgres.ps1
# later, to stop it:
powershell -ExecutionPolicy Bypass -File .\scripts\dev-postgres.ps1 -Stop
```

## 3. Configuration

```powershell
Copy-Item .env.example .env
notepad .env
```

Set `DATABASE_URL` and `SECRET_KEY`. For Option B use:

```
DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5433/insurance_navigator
```

Generate a secret key with:

```powershell
.\.venv\Scripts\python -c "import secrets; print(secrets.token_hex(32))"
```

The app refuses to start if either value is missing. `.env` is git-ignored.

## 4. Database and demo data

```powershell
.\.venv\Scripts\flask --app wsgi db upgrade
.\.venv\Scripts\flask --app wsgi seed-demo
```

`seed-demo` loads fictional demo data only: three sample plans with sample policy clauses and comparison findings, and one account,
`demo@example.com` / `demo-password-1`, with no answers. It is safe to run again: it only adds what is missing. Add `--reset` to rebuild the demo plans,
clauses and findings (accounts and questionnaire answers are kept). The command is separate from migrations; skip it and the dashboard shows an
empty plans state.

## 5. Run

```powershell
.\.venv\Scripts\flask --app wsgi run --port 5000
```

Open <http://127.0.0.1:5000>. Stop with Ctrl+C.

## 6. Tests

Tests need a PostgreSQL database that can be wiped (tables are dropped and recreated).
Do not point `TEST_DATABASE_URL` at your real database.

```powershell
# Option B example
.\.devpg\pgsql\bin\createdb.exe -h 127.0.0.1 -p 5433 -U postgres insurance_navigator_test
$env:TEST_DATABASE_URL = "postgresql+psycopg://postgres@127.0.0.1:5433/insurance_navigator_test"
.\.venv\Scripts\python -m pytest -q
```

## How the review priorities work

Rules live in `app/priorities.py` (one table, each rule has a plain-language reason).

- An answer can add points to a topic, for example "My spouse and one child" adds 3 points to
  "Protecting the people who rely on your income".
- Skipped questions, unanswered questions and typed-in "Other" answers are unknown and add nothing.
- Topics are ranked by points. Ties keep a fixed order. At most three are shown.
- Budget (Q4) and the single age value "30" have no rules.
- These points only rank discussion topics. They are not the coverage score, the radar or the
  coverage percentages, which stay "Pending" until a calculation is agreed.

## Demo values and comparison

- The coverage score, radar and six percentages on the dashboard come from `app/fixtures/demo_coverage.json`.
  They are fixed sample numbers, labelled "Illustrative demo values", and do not depend on any answers.
- **Compare** (nav bar or a plan card on the dashboard) compares two of the three sample plans. Citation links open
  `/citations/<clause id>` with the cited line highlighted. All plan names, prices and wording are fictional.

## Notes

- Inter font files in `app/static/fonts/` are under the SIL Open Font License (`LICENSE-Inter.txt`).
- Passwords are hashed with Werkzeug (scrypt). Sessions are signed cookies (HttpOnly, SameSite=Lax).
  Set `SESSION_COOKIE_SECURE=1` when serving over HTTPS.
- The Flask development server is for local use only.
