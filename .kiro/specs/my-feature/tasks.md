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

## Checkpoint 5 (built): consent-based sharing and a minimal FC portal

- **Roles:** public sign-up always creates `role="client"`. FCs are created only by `flask create-fc --email --name` (password prompted and hidden) or by `seed-demo` (two fictional demo FCs: Daniel Koh `FC-2048`, Jane Tan `FC-3117`). FCs sign in on the same page and land on `/fc`. Clients get a 404 on `/fc`. FCs are redirected to `/fc` from every client page and action. New tables: `fc_profiles`, `fc_assignments`, `share_grants`, `share_events` (additive migration).
- **Consent screen** `/sharing/new`: step 1 (only if no FC yet) is a referral code (previewed by name and speciality only) or a random match (least-loaded FC who speaks the client's language). Step 2 has three unticked categories (`profile_answers`, `simulator`, `flagged_items`), each showing exactly what the FC would see today. Only the item the client came from is pre-ticked. The assignment and the grants are created only on "Confirm and share", with at least one item ticked. The dashboard score and radar are never shareable.
- **Entry points enabled:** dashboard "Send to my FC" (profile), simulator "Review with my FC" (simulator), comparison "Send flagged items to my FC" (flagged items), citation "Send to my FC" (flagged items).
- **Sharing page** `/sharing`: active grants with Revoke, the trust banner, and an activity log (granted, revoked, viewed). `share_events` is append-only by design: the app only inserts, and an ORM guard blocks updates.
- **FC portal:** `/fc` lists only clients with an active grant, with what is shared and since when. `/fc/clients/<id>` shows only the granted categories. One function, `app/sharing.py: visible_categories(fc, client_id)`, decides access for every FC route. A grant counts only while it is active and the client is assigned to that FC. Anything else answers the same 404, so ids reveal nothing. Ungranted categories are never queried. A name is part of `profile_answers`, so it is shown only when that is granted ("Client #id" otherwise). Every view writes a `viewed` event. FC pages are read only.
- **Controls:** CSRF on every POST, wrong referral codes limited to 5 per 10 minutes per client (in memory, `app/ratelimit.py`), free text escaped, fixed error messages only.

Decisions I made where the spec was silent:

- A grant is a live permission, not a snapshot. The FC sees the latest saved data until the client revokes.
- Choosing an FC assigns nothing. The FC is told nothing until the client confirms at least one item.
- The citation page button has no "clause and question" category, so it opens consent with flagged items pre-ticked.
- Random match uses the least-loaded FC who speaks the client's language, with a random tie-break. Matching on needs is not built.
- Figma's consent screen shows more categories, some on by default. R5 says off by default and the demo score is a sample value, so only the three categories above exist, all unticked.

Remaining for FC work (not built): incoming-client Accept/Decline and AI pre-meeting summary (F6), client profile tabs (notes), gap analysis (F3), video meeting and summary (F4), ROA draft and sign-off (F5), Change FC with review, Clear my data, the trust banner on every main screen, an FC-initiated "request access".

## Checkpoint 6 (built): Plans, plan detail and top-3 matching

- **Catalogue:** `app/catalogue.py: catalogue_policies()` is the only source of plans for the Plans pages and the dashboard top 3 (today: demo plans). `seed-demo` now loads six fictional plans: the original three (unchanged) plus PRULife Whole (whole life), PRURetire Income (retirement) and PRUDisability Guard (disability), each with sample clauses and comparison cells.
- **/plans:** filters by type through the query string (works without JavaScript, stays on reload, unknown values show all), cards with premium, "View plan" and "Compare", an honest empty state, nav "Plans" now points here.
- **Dashboard top 3** (replaces "Sample plans"): rules in `app/plan_match.py`. A plan whose type matches a Review priority ranks first (earlier priority first), then plans that fit the Q4 budget band, then catalogue order. Skipped, Not sure and typed-in answers are unknown and never match. "Best match" only on #1, with a one-line reason. If nothing matches, the first three plans show with no tag and a note. The demo score section is untouched.
- **/plans/<id>:**
  - **Summary:** AI plan summary (all AI code in `app/ai/plan_summary.py`), grounded in that plan's clauses only. The model sees no client data, so one cached summary (`plan_summaries`) serves everyone. Generated on the first view that finds no usable cache, rate-limited per user. A failed generation is remembered for an hour, and a missing key, our rate limit, or the provider being busy (429) is never remembered.
  - **Validation:** every statement must cite supplied clause ids, every number must appear in a cited clause or be the plan's premium, and advice wording is rejected. Anything else gives the deterministic "Standard summary" built from the plan's comparison cells.
  - **Citations:** markers [n] link to the existing citation page and a Sources list sits under the text. The citation page got an optional `?plan=` so its back link can return to the plan.
  - **Translations:** EN | 中文 | BM | தமிழ் tabs. Translations come from the validated English statements and are accepted only if every digit sequence and clause id is kept exactly; otherwise English shows with "Translation unavailable, showing English".
  - **Read Aloud:** browser speech synthesis only, no network request, disabled with a message when no voice exists.
  - **Fit sections:** deterministic budget trade-off, four compatibility rows (family obligations, budget fit, hospital preference, overlap with cover you have), each with the answer that drove it or "Not enough information", and a simplified comparison against up to two similar plans.
- `flask generate-summaries [--language ...] [--force]` pre-fills the cache.

Decisions I made where the spec was silent:

- A plan counts as matched if its type matches a priority OR its premium fits the budget band. "Under S$100" excludes S$100; the other bands include their top.
- Whole life and term life both speak to the "dependants" priority. The "existing cover" priority matches no plan type.
- Overlap uses the saved simulator scenario first, then the Q8 answer.
- The summary module makes its own provider call (same SDK class and settings) because `app/ai/client.py` limits replies to 500 tokens, too small for Tamil or a full summary.

Remaining (not built): real policy document ingestion (the catalogue is still the fictional sample plans), other insurers, "My Plans" (plans the client already holds), a full multilingual review of the translations, "Get this plan" and the purchase flow (task 10), a summary regeneration schedule or admin view.

## Checkpoint 7 (built): Prudential catalogue, contract ingestion and PDF citation viewer

- **Source pack:** `data/prudential/` holds `prudential-catalogue.json`, the two CSVs, START-HERE.md and the two MOH sample contracts (committed).
- **`flask import-catalogue`** (idempotent): 49 plans and 70 documents with ids, types, versions, URLs, hashes and the catalogue date kept as given. `Policy.monthly_premium_cents` is now nullable; catalogue plans have no premium and the pages say "Premium varies. See Prudential's official rate tables" (linked to the rate-table document when the plan has one, otherwise to the plan's official product page). No premium is ever invented.
- **Selector rules** (`catalogue.selectable_policies`): product families and closed plans are never selectable, riders only with one of their applicable base plans. Closed plans stay viewable and are listed under "Closed to new members", not under "All kinds".
- **Contract status** on every card and plan page. "Contract wording verified (sample, April 2026)" is shown only when the plan is linked to a sample contract that was ingested with a matching hash. Everything else says "Contract wording not verified".
- **`flask ingest-contracts`** (idempotent, no AI): checks each PDF's SHA-256 against the catalogue before writing anything and refuses on a mismatch. Stores `DocumentVersion` and `Paragraph` rows (page, bbox in PDF points with top-left origin, whitespace-normalised text, paragraph SHA-256, clause number, heading, plan scope). Scope: pages 1-36 of the PRUShield V2 sample are Premier and Plus, pages 37-59 are Standard, the A/B sample is PRUShield A and B. The three catalogue anchors resolve to the right paragraph hash and scope.
- **Citation viewer** `/citations/<paragraph id>`: server-rendered PNG of the real PDF page, the paragraph drawn as an SVG rectangle in PDF points (no inline styles or scripts, CSP unchanged), page navigation limited to pages with the same scope, document version visible, "Open source PDF" (official URL at the page). It refuses with an error if the PDF's SHA-256 no longer matches the stored hash, and refuses a citation made for a plan the paragraph does not apply to.
- **Old demo citations** moved to `/citations/demo/<clause id>` (the number space is shared). Only the route line in `compare.py` changed.
- **/plans** filters by category (Health, Life, Wealth, Legacy) and kind, cards with category chips, coverage focus, contract status and "View plan". **/plans/<slug>** shows metadata, documents, riders or base plans, and for the PRUShield variants a Sources panel with real paragraphs and citation links. The numeric `/plans/<id>` page (with the AI summary) still serves the hidden demo plans only.
- **Demo plans** are hidden (`is_hidden`), not deleted, so flags and scenarios survive.

Open items from this checkpoint:

- **Compare, Simulator and the dashboard "Top recommended plans" still use only the hidden fictional demo plans.** They need a premium (the simulator does `monthly_premium_cents // 100`, compare renders it) and a cover amount (`death_cover_cents`, `cover_ends_at_age`), and comparison cells with cited clauses. Catalogue plans have none of these, so they are not offered there. Redesign needed: premium-less rows, "not enough information" for cover amounts, comparison cells built from ingested paragraphs.
- Plan AI summaries (6b) are not built for catalogue plans. The existing summary code works on the demo plans only. `flask generate-summaries` (in `app/ai/cli.py`, not touched) asks `catalogue_policies()`, which now returns catalogue plans that have no clauses, so it generates nothing until 6b.
- Contract wording exists only for PRUShield Premier, Plus, Standard (and closed A, B). PRUExtra rider wording is missing. All other plans are brochure-only.
- The A/B Sources panel shows table fragments (the contract has no clause headings for them).
- Uses PyMuPDF (AGPL-3.0 licence): confirm it is acceptable before any deployment.

## Checkpoint 8 (built): AI summaries and comparison for the real PRUShield plans (6b)

- **Scope:** PRUShield Premier, Plus (the catalogue calls it "PRUShield Plus") and Standard Plan, the three open plans with ingested wording. Closed A and B also have wording but are never summarised or compared. Every other plan says "Contract wording not verified" and never reaches the model.
- **Retrieval** (`app/retrieval.py`, no AI): 13 fixed topics (covered, ward, limits, deductible, co-insurance, pre/post hospitalisation, cancer, exclusions, waiting periods, claims, renewal, termination, riders) scored by weighted keywords (a keyword in the clause heading counts double). Only paragraphs whose scope includes the plan's slug. Round-robin across topics, capped at 40, document order, same result every time. Premier and Plus get the same paragraphs because they share Part One.
- **Summary** (`app/ai/plan_summary.py`): `{"statements":[{"text","paragraph_ids"}]}`, 4 to 8 statements, 1 to 4 supplied paragraph ids each, every figure must be in a cited paragraph, no advice wording, length caps. Any failure uses the deterministic "Standard summary" (clause heading plus first sentence of each topic's best paragraph). The model sees the plan name and paragraphs only (each cut to 400 characters), no client data and no premium. Cached per plan, language and paragraphs hash. Markers [n] open `/citations/<paragraph id>?plan=<slug>` in the PDF viewer. Translations keep the old rule (digits and cited ids unchanged, else English with a note).
- **`flask generate-summaries [--plan slug] [--language ..] [--force]`** skips plans with no paragraphs (counted in the last line), retries when the provider is busy, and reports a failing plan without stopping.
- **`flask generate-comparison [--plan slug] [--force]`** (offline, cached): eight fixed rows (ward class, annual claim limit, deductible, co-insurance, pre and post hospitalisation, cancer drug and treatment, key exclusions, renewal terms) plus a fixed "Premium varies" row. The model proposes `{state, finding, paragraph_id}` per row. A proposal is kept only if the paragraph was supplied (so it is in the plan's scope), figures appear in it, there is no advice wording, and "not_included" / "optional_rider" are backed by wording in that paragraph. Everything else becomes Unconfirmed, "Not stated in the available wording".
- **Migration `10cdcfd50f0b`** (additive): `comparison_cells.paragraph_id` (nullable FK) and `paragraphs_hash`; the "not included needs a citation" CHECK now accepts a clause or a paragraph. Demo cells, flags and scenarios are untouched.
- **/compare** lists the open plans that have contract wording. A plan with no findings yet shows a "Generate findings for <plan>" button (`POST /compare/generate`): one provider call, no client data, rate-limited per user, no retry loop inside the request, and the page says plainly when the service is busy or not set up. `flask generate-comparison` is still there for bulk runs. Summaries already generate on the first plan-page view. Cells link to the PDF viewer and "Show original wording" shows the paragraph text. The flag-for-FC flow works for Unconfirmed cells. The hidden demo plans are no longer in Compare.
- **Plan page** `/plans/<slug>` has the summary card with language tabs for the three plans.

Provider limits found in the live check (Groq free tier, `openai/gpt-oss-20b`): 8,000 tokens per minute and 200,000 tokens per day. A summary request is about 3,500 tokens, so the CLI waits and retries when the provider says "busy". The model's hidden reasoning could use the whole output budget on large inputs (HTTP 400 `json_validate_failed`), so the summary call now asks for low reasoning effort on gpt-oss models.

Open items:

- Reading the dashboard "Top recommended plans", the simulator and the "Send to my FC" flagged-items screen still use or show the fictional demo plans. A flagged real cell shows its finding but no source reference in the FC's view (that code is outside this checkpoint).
- The annual claim limit row is Unconfirmed for all three plans: the wording points to the Benefits Schedule, whose figures are in tables the paragraph extractor splits into fragments. Table-aware extraction is needed before figures can be compared.
- No Read Aloud on catalogue plan pages. No budget or compatibility sections on catalogue plans (they have no premium).
- Premier and Plus findings are identical because the contract wording is shared. Differences are in the Benefits Schedule.

## Decision: the real Prudential plans are hidden for now

Everything from checkpoints 7 and 8 is built and tested but switched off. `SHOW_PRUDENTIAL_PLANS` defaults to off, so Plans, Compare and the plan summary use the fictional demo plans again (cards with Compare, AI summary, translations, Read Aloud). With it off, `/plans/<slug>` is a 404, `/compare/generate` is refused, and the catalogue helpers return nothing. Set `SHOW_PRUDENTIAL_PLANS=1` in `.env` to bring the real plans back. Nothing was deleted, and the catalogue, paragraphs and cached summaries stay in the database. To be looked at again later: the Compare findings quality, the Benefits Schedule tables, and the provider's daily token limit.

## Week 7 prototype (built): clickable demo pass

Demo plans only (the real Prudential plans stay hidden behind `SHOW_PRUDENTIAL_PLANS`).

- **Death cover score:** dashboard shows existing death cover / estimated need x 100 (0 to 100) from the saved simulator scenario, a setup state before any save, and no percentage when need is zero. The simulator shows the live score and a what-if score (events and added plans) as you type. The six-area radar stays labelled illustrative.
- **Ask AI and summaries (demo clauses):** `/ask` (plan, question, language, "Summarise this plan") and a "Generate AI summary" button on plan pages. The provider is called only by those buttons: one short request per cache miss, 10 s timeout, no automatic retry, cached by plan, clause version, question and language. Page loads and language tabs make no call (`AI_GENERATE_ON_VIEW` is off). Failures show a fallback with clause links and the original wording.
- **Buy or consult:** Get this plan, Consult my FC (existing consent flow), Buy now: four scenario questions from the plan's cited findings, feedback with clause links, retries, 3 of 4 to pass, server-gated demo checkout with no payment details.
- **Settings:** text size, high contrast, read-aloud default and summary language persist per account. Change FC (choose, preview, confirm). Clear my data resets demo state only.
- **FC portal:** fictional incoming client (accept or decline, pre-meeting summary from a local template), simulated meeting (Join, End meeting, Add to client profile) and an editable sample ROA with missing items, Mark reviewed and Lock. Live video and actual sign-off wait for Week 14.

Simulated or not built: video, payment, the AI pre-meeting summary and meeting summary (fixed sample text), PDF export of the ROA, "Download my data", sharing categories beyond profile, scenario and flagged items.

## Week 8 (built): real meetings, Ask AI by voice, adaptable ROA

- **Meetings** (`app/meetings.py`, `static/js/meeting-room.js`): WebRTC video between a client and their assigned FC, signalled through this app by polling. Only those two can open a meeting. Recording needs both people to agree, runs in each person's own browser, stops on withdrawal or when the FC ends the meeting, and the call goes on without it.
- **Notes** (`app/meeting_notes.py`, `app/ai/meeting_ai.py`, `app/ai/transcribe.py`): the FC presses "Create AI draft" once per recording. Each audio segment is transcribed once, then one summary call writes discussion points, next steps and open questions, with "[unclear]" markers. Failure keeps manual notes. The FC saves privately, previews, then confirms to send. Later edits stay private until sent again. The customer's **Saved Notes** tab shows only what was sent to them.
- **Ask AI by voice:** record a question, check or correct the transcript, ask, read the cited answer, Listen / Replay / Stop with a matching browser voice (no request on replay).
- **ROA** (`app/roa.py`): private drafts from the client's shared profile, simulator scenario, the FC's meeting notes, the demo plans' cited findings and the FC's own approved recommendation. Plan comparisons list options and reasoning, coverage reviews list estimates and assumptions, undecided meetings list open questions and next steps. Missing facts say "To confirm". Edits, added and removed sections survive a refresh.
- Setup: `flask seed-meetings` (demo client + Daniel + three demo meetings). Zero provider calls on page loads, replay, reopening and polling. Zero automatic retries.

Not verified live: real Groq speech-to-text and notes quality, speaker audio over a real network (STUN/TURN), browser voices for Chinese, Malay and Tamil. Not built: live transcript, sending the ROA to the client, Week 14 sign-off.

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
  - Done (checkpoint 6): "Top 3 plans for you" is matched to the questionnaire (type matches a Review priority, then budget fit), "Best match" only on #1, honest "no match" state. Still sample data.
  - Done (checkpoint 5): **Send to my FC** opens the consent flow (task 9).
- [x] 5. Plans list and plan detail with language tabs
  - Done: see Checkpoint 6. Not built: real policy ingestion, other insurers, "My Plans", a full multilingual review of the translations, "Get this plan".
- [ ] 6. Citation model and PDF viewer with line highlighting
  - Done (checkpoint 7): real PDF page rendering with the paragraph highlighted for the PRUShield sample contracts, hash and scope checks. Still not built: "Other citations" list, citations from AI summaries on catalogue plans.
  - Done (sample only): `PolicyClause` model, citation page that shows the cited line highlighted with its line number, the claim and "Matches: clause X, line Y", and paging across the seeded sample pages.
  - Remaining: real PDF documents and rendering, download and "Open on Prudential.com.sg", "Other citations" list, citations from plan-detail AI summaries (task 5).
- [ ] 7. Comparison table with four cell states and clause links
  - Done: two-plan side-by-side table, four states (Included (subject to conditions), Optional rider, Unconfirmed, Not included), clause link on every cell that has a source, shared rows versus auto-detected distinctive rows, "Show original wording" toggle, "Flag for my FC to verify" on Unconfirmed cells (stored privately per user), plan selection and fallback for invalid ids.
  - Missing information is shown as Unconfirmed with "Not stated in this policy." and "No source clause found". "Not included" always needs a cited clause, enforced by a database constraint.
  - Remaining: more than two policies, adding other insurers by upload or link, extraction accuracy, the "Try a scenario" preview (future iteration), real policy documents, an "Add to compare" path from a plan-detail page (task 5).
  - Open: the Figma table shows premium per year. The sample data shows monthly premiums to match the plan cards.
- [x] 8. Coverage Needs Simulator (formulas, plans, events, chart, breakdown)
  - Done for death cover: inputs, formulas, add/remove plans, four life events, chart with term-expiry note, rule-based "What changed, and why", save and restore. Worked example verified: S$130,000 / S$580,000 / S$80,000.
  - Not built: more than one saved scenario (the Figma "Today / Scenario A / + New scenario" tabs), Critical Illness and Disability modes (shown as "Coming next"), life-event prompts (future development).
  - Open: retire at 55/65 and job break are listed in the spec but their effect on a death-cover need is not defined. They show in the event list as disabled ("not modelled yet") and the server rejects them.
  - Open: existing death cover is typed in. The spec says it is pre-filled from the client's plans, but the app has no record of plans the client already holds.
  - Done (checkpoint 4): AI "Explain this change" beside the rule-based breakdown, with a standard-template fallback.
  - Open: the Figma mock-up lists three events (home, child, pay down mortgage at 40) next to a S$580,000 "after life events" card. S$580,000 is only reached with home and child, as in design.md, so the pay-down is not part of that figure.
- [x] 9. Consent screen and ShareGrant
  - Done: consent screen with referral code or random match, three unticked categories with a live preview, `ShareGrant`, `ShareEvent`, revoke, the four entry points, the trust banner on `/sharing`. See Checkpoint 5.
  - Not built: Change FC (3 steps) and "Clear my data" (task 11), consent for the other categories in Figma (coverage map, quiz results, questions, meeting notes).
- [ ] 10. Buy or consult, comprehension quiz, payment
- [ ] 11. Settings, Change FC, Clear my data, trust banner
  - Not built in checkpoint 5: Change FC with review, Clear my data. A trust banner exists only on `/sharing`.
  - The trust banner and Clear Session (C16) are not on the Figma onboarding screens and are not built yet.
- [ ] 12. FC portal: client list, profile, gap, incoming client
  - Done: FC accounts and login, client list limited to granted clients, read-only client view limited to granted categories, view log. See Checkpoint 5.
  - Remaining: incoming client (Accept / Decline and the AI pre-meeting summary), notes, gap analysis, and the other FC tabs.
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
