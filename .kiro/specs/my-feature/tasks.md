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
- [ ] 8. Coverage Needs Simulator (formulas, plans, events, chart, breakdown)
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
- Whether sample plan names should stay as the Figma names (shown as demo data) or be renamed.
