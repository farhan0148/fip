# Insurance Navigator — Tasks

Implementation checklist for [requirements.md](requirements.md) and [design.md](design.md). The suggested order from the source specification is preserved.

Before coding, resolve the [known Figma gaps](design.md#6-known-gaps-from-the-figma-build-decide-before-coding). Flag disagreements between the specification and Figma rather than guessing. Future development items remain outside the main flow.

Setup and run instructions: [SETUP.md](../../../SETUP.md).

## Checkpoint 1 (built): sign-up, login, questionnaire, review, dashboard

Decisions applied for this checkpoint:

- "Other, let me type" is a required text field (max 200 characters) that replaces the current selection.
- No login mockup exists, so login reuses the sign-up card style.
- UI wording is "Financial Consultant (FC)" on first mention, "FC" in short labels. The spec's "FR" typo is not carried into the UI.
- All eight questions are single-select. New accounts start with nothing selected. Age options stay as in Figma (Under 30, 30, 31–45, 46 or above).
- Currency is S$. Sample plans are labelled fictional demo data.

## Checkpoint 2 (built): demo score and radar, side-by-side comparison

- Dashboard score, radar and six bars are a **temporary fixed fixture** (`app/fixtures/demo_coverage.json`). The whole section is labelled "Illustrative demo values" and says the numbers are sample data that do not depend on the user's answers. The values are the same for every user and every reload.
- Review priorities still come only from the questionnaire.
- Comparison compares any two of the three fictional sample plans, chosen with two selectors (or the Compare button on a dashboard plan card). Selection lives in the URL, so a reload keeps it.
- Sample clauses, comparison cells and citation pages are seeded by `flask seed-demo`. All wording is invented.

## Checkpoint 3 (built): Coverage Needs Simulator, death cover only

- `/simulator` (nav link and dashboard "Simulate a change"). Editing an input, adding or removing a plan, or changing a life event updates the three gaps, the chart and "What changed, and why" in the browser. There is no page load and no server request per edit.
- Formulas are shown on the page and used as written: `need = support + debts − savings`, `gap = need − existing death cover`. Support is years × annual amount plus support added by events. Arithmetic only: no Groq or other AI service.
- Blank or invalid fields show inline errors and "—" instead of totals. Every field must be filled in (0 where nothing applies).
- **Save scenario** posts to Flask, which validates and recalculates everything and stores one scenario per account (`scenarios` table). It is restored after reload.
- Simulator results are separate from the dashboard's illustrative demo values.
- The calculation exists twice (`app/simulator.py` and `app/static/js/simulator-core.js`). `tests/simulator_cases.json` holds 38 hand-worked cases that both must pass.

Definitions I chose where the spec is silent:

- "After life events" means after the latest event, at age E = max(age today, latest event age). "With added plans" adds the cover of each plan that still applies at E.
- A plan covers you up to and including its last covered age and drops at the next age (PRUActive Term covers up to 65 and drops at 66, as the Figma note says).
- Events apply from their age onwards. Same-age events apply in the order listed.
- Event effects: Buy a home adds housing debt. New child adds support. Income change adds or removes support (negative lowers it). Pay down mortgage lowers housing debt. A pay-down larger than the housing debt, or an income change that takes support below zero, is an error.

## Checkpoint 4 (built): AI explanation of simulator changes

- The calculation stays deterministic (`app/simulator.py`, `static/js/simulator-core.js`, unchanged). design.md section 4 and requirement R4 no longer say "no chatbot": they now say the AI only explains calculated changes, on request, beside the rule-based "What changed, and why" panel.
- `POST /api/simulator/explain`: login and onboarding required (JSON 401/403), CSRF header required, per-user rate limit (5 per 60 s, in memory), same payload as save, re-validated and recalculated by Flask, nothing stored.
- Grounding: only the three gaps plus each change's label, input and signed effect on the gap go to Groq. No name, email, account id, age or raw input.
- Validation: every number in the reply must be a supplied S$ figure (other currencies, abbreviations, bare numbers, percentages, number words and advice wording are rejected). Any failure, timeout, error or missing key gives the fixed "Standard explanation" template.
- UI: "Explain this change" button under the panel, on click only, disabled until the inputs are valid and something has changed, with a note that scenario figures are sent to the AI provider (Groq).
- Settings: `GROQ_API_KEY` and `GROQ_MODEL` in the server `.env` only. There is no default model; pick one from https://console.groq.com/docs/models.
- Safety: tests refuse to run unless `TEST_DATABASE_URL` names a database ending in `_test` that differs from `DATABASE_URL`. A Node test runs the browser calculator against `tests/simulator_cases.json`. Guards check the calculators contain no AI imports or network calls and that provider code lives only in `app/ai/`.

Open for the AI layer:

- Live Groq call not yet verified (needs the real key and model in `.env`; every test mocks Groq).
- The validator checks figures and advice wording, not whether the sentence's logic is right (for example "raises" versus "lowers"). The bullets are one per change and quote the supplied signed effect.
- Rate limit is per server process. Move it to the database or Redis if the app runs several workers.
- Groq model choice and data-handling terms are a decision for the project (the scenario figures leave the server on each click).

## Checkpoint 4b (built): fix for rejected live AI replies

Symptom: a live Groq reply was always rejected (`invalid_output`), so users only saw the Standard explanation.
Scenario: gap today S$402,000, PRUActive Term effect -S$500,000, final gap -S$98,000.

- **Diagnosis** (local `AI_DEBUG=1`, live `openai/gpt-oss-20b`): the reply was correct. Rejected tokens were `S$402,000,` and `S$98,000,` (kind `abbreviated_or_malformed`). The number pattern swallowed the comma that ends a clause, so any figure followed by a comma failed the grouping check. The model also wrote "lowers the gap by -S$500,000", a double negative.
- **Validator** (`app/ai/explain.py`): S$ amounts are compared by magnitude, so `S$98,000`, `-S$98,000`, `S$-98,000`, `S$ 98,000`, `SGD 98,000` and a bare `98,000` are all accepted when 98,000 is a supplied magnitude. Still rejected: unsupplied magnitudes, other currencies, abbreviations (k, m), decimals and bad grouping, percentages, number words, advice wording.
- **Direction:** in a clause that talks about "the gap", a change described as raising when the calculator lowers it (or the reverse) is rejected. Clauses that use both directions, or do not mention the gap (for example "increases your cover"), are not judged.
- **Negative gaps:** a negative final gap must be described as cover exceeding need; "gap", "shortfall", "deficit" or "short by" for it is rejected, and "need exceeding cover" for a negative figure is rejected.
- **Bullets** are now generated from the calculator, and the model writes only the summary (the model's own bullets are ignored). The model input also carries a plain reading of each gap (for example "no gap: cover exceeds need by S$98,000").
- **Debug switch:** `AI_DEBUG=1` logs rejected tokens and the allowed magnitudes. Off by default and ignored when `APP_ENV=production`.
- **Live check:** six varied scenarios against the real API are all accepted (one was wrongly rejected by an intermediate version of the negative-gap rule and fixed).

Known limits: the checker judges figures, direction in gap clauses and negative-gap wording. It cannot judge every possible sentence, so an unusual but wrong reply could still pass, and an unusual correct reply could fall back to the standard explanation.

## Implementation tasks (suggested order)

- [ ] 1. Set up project, design tokens and shared components (button, card, chip, cell state, nav)
  - Done: Flask app, PostgreSQL models and migration, tokens, Inter font, button, card, chip, nav, shared base template and stylesheet for client and future FC portal.
  - Remaining: comparison "cell state" component (needed by task 7); FC portal sidebar layout.
- [x] 2. Auth and sign up
  - Sign-up, login, logout, hashed passwords, server-side validation, session login, CSRF token on every form, resume onboarding or go to dashboard after login.
  - Not built: password reset, login rate limiting, email verification.
- [x] 3. Questionnaire (MCQ) and review, with saved answers
  - Eight single-select questions, Skip/Back/Next, saved on every step, review with Edit per answer, "Not answered" for skipped, confirm to unlock the app.
  - Dashboard, Plans, Compare and Simulator stay locked until the review is confirmed.
- [ ] 4. Coverage map, score and dashboard
  - Done: dashboard layout, "Welcome, {first name}", profile summary from answered questions only, "Review priorities" card (rules in `app/priorities.py`), fictional sample plan cards, unavailable actions marked "Soon".
  - **Open: the real coverage calculation.** The score, radar and six percentages now show a fixed demo fixture (labelled "Illustrative demo values"). Replace the fixture with an agreed calculation from answers (and later policies) to the score and the six areas, then remove the demo label.
  - Open: "Top 3 recommended plans" matching. Cards are sample data and are not matched to the user, so the "Best match" tag is not shown.
  - Open: **Send to my FC** (consent flow, task 9).
- [ ] 5. Plans list and plan detail with language tabs
- [ ] 6. Citation model and PDF viewer with line highlighting
  - Done (sample only): `PolicyClause` model, citation page that shows the cited line highlighted with its line number, the claim and "Matches: clause X, line Y", and paging across the seeded sample pages.
  - Remaining: real PDF documents and rendering, download and "Open on Prudential.com.sg", "Other citations" list, "Send to my FC" (needs consent, task 9), citations from plan-detail AI summaries (task 5).
- [ ] 7. Comparison table with four cell states and clause links
  - Done: two-plan side-by-side table, four states (Included (subject to conditions), Optional rider, Unconfirmed, Not included), clause link on every cell that has a source, shared rows versus auto-detected distinctive rows, "Show original wording" toggle, "Flag for my FC to verify" on Unconfirmed cells (stored privately per user), plan selection and fallback for invalid ids.
  - Missing information is shown as Unconfirmed with "Not stated in this policy." and "No source clause found". "Not included" always needs a cited clause, enforced by a database constraint.
  - Remaining: more than two policies, adding other insurers by upload or link, "Send flagged items to my FC" (consent, task 9), extraction accuracy, the "Try a scenario" preview (future iteration), real policy documents, an "Add to compare" path from a plan-detail page (task 5).
  - Open: the Figma table shows premium per year. The sample data shows monthly premiums to match the plan cards.
- [x] 8. Coverage Needs Simulator (formulas, plans, events, chart, breakdown)
  - Done for death cover: inputs, formulas, add/remove plans, four life events, chart with term-expiry note, rule-based "What changed, and why", save and restore. Worked example verified: S$130,000 / S$580,000 / S$80,000.
  - Not built: more than one saved scenario (the Figma "Today / Scenario A / + New scenario" tabs), Critical Illness and Disability modes (shown as "Coming next"), "Review with my FC" (needs consent, task 9), life-event prompts (future development).
  - Open: retire at 55/65 and job break are listed in the spec but their effect on a death-cover need is not defined. They show in the event list as disabled ("not modelled yet") and the server rejects them.
  - Open: existing death cover is typed in. The spec says it is pre-filled from the client's plans, but the app has no record of plans the client already holds.
  - Done (checkpoint 4): AI "Explain this change" beside the rule-based breakdown, with a standard-template fallback.
  - Open: the Figma mock-up lists three events (home, child, pay down mortgage at 40) next to a S$580,000 "after life events" card. S$580,000 is only reached with home and child, as in design.md, so the pay-down is not part of that figure.
- [ ] 9. Consent screen and ShareGrant
- [ ] 10. Buy or consult, comprehension quiz, payment
- [ ] 11. Settings, Change FC, Clear my data, trust banner
  - The trust banner and Clear Session (C16) are not on the Figma onboarding screens and are not built yet.
- [ ] 12. FC portal: client list, profile, gap, incoming client
- [ ] 13. Video meeting with AI summary
- [ ] 14. ROA draft, sign-off, locked version with audit log
- [ ] 15. Accessibility and multilingual pass
  - Started: labelled controls, keyboard focus styles, skip link, error messages announced. Contrast of the terracotta button text and the muted token needs a decision (see open questions). Language switcher not built.

## Open questions

- Score calculation and what feeds each of the six coverage areas (task 4).
- Single versus multi-select for questions beyond this checkpoint.
- Age bands: "30" overlaps "Under 30" and "31–45" in Figma.
- Contrast: white text on the terracotta accent is about 3.6:1, below the 4.5:1 AA target for small text. Small muted text and accent links use darker tones than the design tokens.
- "Ask AI" on the dashboard is not in the requirements and is disabled.
- Simulator: should the plan list come from plans the client already holds (needs a My Plans feature) and should cover amounts for Critical Care and the other sample plans be confirmed? Sample values: PRUActive Term S$500,000 to age 65, PRUCritical Care S$100,000 to age 75, PRUShield Premier has no death benefit.
- Whether sample plan names should stay as the Figma names (shown as demo data) or be renamed.
