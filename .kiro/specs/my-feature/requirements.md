# Insurance Navigator — Requirements

Product scope and expected behaviour. Read alongside [design.md](design.md) and [tasks.md](tasks.md).

## 1. Overview

Insurance Navigator is a web app for Prudential Singapore customers and their Financial Consultants (FCs). It helps a customer understand their protection gap, compare plans in plain language, and prepare for an advice conversation. It does not sell or advise on its own. Every estimate is labelled "to be confirmed with your FC".

Two audiences, two dashboards:

- **Client** – guided questionnaire, coverage map, plans, comparison, simulator, quiz, settings.
- **FC** – client list, client profile, gap analysis, video meeting with AI summary, Record of Advice (ROA) draft.

## 2. Principles

1. **Consent first.** Nothing reaches an FC until the client confirms what is shared. Sharing is off by default and reversible.
2. **Cited, not asserted.** Every AI statement about a policy links to the exact page, clause and line of the source document.
3. **Honest uncertainty.** Comparison cells use four states: Included (subject to conditions), Optional rider, Unconfirmed, Not included. "Not included" is used only when the wording supports it.
4. **Estimates are labelled.** Simulator and gap figures show the formula and an "estimate only" note.
5. **Plain language.** Summaries are available in English, 中文, Bahasa Melayu and தமிழ்.

## 3. Users and roles

- **Client**: can complete the questionnaire, browse plans, compare, simulate, take the comprehension quiz, share data with an FC, change FC, and clear all data.
- **FC**: sees only data a client has shared. Can accept or decline a new client, verify flagged items, run meetings, and draft and sign off an ROA.

## 4. Features

### Client

| ID | Feature | Summary |
|---|---|---|
| C1 | Guided discovery (MCQ) | Eight questions, one per screen, selectable answer cards, "Why we ask" line, "Other, let me type" option, Skip, Back, Next. Ends with a review screen ("Here's what we understood") where every answer can be edited. |
| C2 | Coverage map and score | Radar of needs against current cover, overall score, short profile summary, **Send to my FC**. |
| C3 | Top 3 recommended plans | Cards with plan type, premium and best-match tag. "View all plans" opens the full list with filters. |
| C4 | Plan detail | English AI summary with language tabs and Read Aloud, citation markers [1] [2] [3], budget trade-off, compatibility fit, simplified comparison. |
| C5 | Citation PDF viewer | Clicking a citation opens the source page with the cited line highlighted and its line number, plus the exact AI claim and "Matches: clause X, line Y". |
| C6 | Comparison table | Two or more policies in columns (Prudential plan, or other insurer by upload or link). Shared rows, auto-detected distinctive rows, four cell states, clause link on every cell, plain-language finding with original-wording toggle, "Flag for my FR to verify" on Unconfirmed cells. |
| C7 | Scenario-based comparison (preview) | Marked "Future iteration". Pick a life event and see pays out / partly / doesn't pay / unconfirmed per policy. Not part of the main flow. |
| C8 | Coverage Needs Simulator | Real calculator, not a chat. The gap is calculated deterministically from the figures; an AI explains the calculated changes on request. See [Coverage Needs Simulator in design.md](design.md#4-coverage-needs-simulator-death-cover-only). |
| C9 | Buy or consult FC | Client chooses to consult an FC or buy. Buying requires the comprehension quiz before payment. |
| C10 | Comprehension quiz | Scenario questions built from the chosen policy, feedback linked to the exact clause. Unclear points go to the FC only with consent. |
| C11 | Payment | Order summary and pay. |
| C12 | Send to my FC (consent) | Toggles for what the FC can see; nothing shared until confirmed. |
| C13 | Settings | Accessibility, Change FC, Clear my data. |
| C14 | Change FC (3 steps) | Review current FC (rating plus "What wasn't good?" chips), choose new FC (random match or referral code like FC-2048), choose what to send and whether to remove old FC's access. |
| C15 | Clear my data | Confirmation modal listing what is removed. Data already shared with the FC is an opt-in checkbox, off by default. |
| C16 | Trust banner and Clear Session | Persistent banner on main screens with a one-click Clear Session. |

### FC portal

| ID | Feature | Summary |
|---|---|---|
| F1 | Client dashboard | List of clients with needs and coverage detail. |
| F2 | Client profile | Needs, coverage detail, existing policies, meetings, notes, ROA tab. |
| F3 | Comparison gap | Needs vs coverage, gaps and overlaps. Each flag cites the policy wording and is verified by the FC. |
| F4 | Video meeting | Live call with an AI summary. "Add to client profile" saves the summary. |
| F5 | ROA draft | Built from profile, gap analysis, meeting summary and quiz record. Missing-items checklist, FC edits and signs off, locked version with tamper-evident log. |
| F6 | Incoming client | New client request (via referral code or random match) with Accept / Decline and an AI pre-meeting summary: biggest gaps, plans compared, questions to cover. |

## 5. Acceptance requirements (EARS)

### R1 Guided discovery
- WHEN a new client signs up, THE SYSTEM SHALL start the questionnaire at question 1 of 8.
- WHEN the client selects answers and presses Next, THE SYSTEM SHALL save them and show the next question.
- WHEN the client reaches the end, THE SYSTEM SHALL show a review screen with an Edit action per answer.
- WHEN the client confirms the review, THE SYSTEM SHALL generate the coverage map and score.

### R2 Citations
- WHEN the AI summary states a fact about a policy, THE SYSTEM SHALL attach a citation with document, page, clause and line.
- WHEN the client selects a citation, THE SYSTEM SHALL open the source with that line highlighted.
- IF no source supports a statement, THE SYSTEM SHALL answer "not stated in this policy" instead of inferring.

### R3 Comparison
- THE SYSTEM SHALL assign each cell exactly one state: Included (subject to conditions), Optional rider, Unconfirmed, or Not included.
- IF the wording does not clearly exclude a benefit, THE SYSTEM SHALL NOT mark it Not included.
- WHEN a cell is Unconfirmed, THE SYSTEM SHALL offer "Flag for my FR to verify".

### R4 Simulator
- WHEN an input, plan or event changes, THE SYSTEM SHALL recalculate need, gap and the chart.
- THE SYSTEM SHALL calculate need, gap and each contribution with fixed formulas and SHALL NOT use an AI model to produce any figure.
- THE SYSTEM SHALL list the inputs that changed the gap and the amount each contributed (rule-based breakdown).
- WHEN the client asks for an explanation, THE SYSTEM SHALL recalculate on the server and show an AI-generated plain-language explanation of the calculated changes beside the rule-based breakdown, labelled as AI-generated.
- THE SYSTEM SHALL send the AI provider only the calculated figures and change labels, and SHALL tell the client that the scenario figures are sent.
- THE SYSTEM SHALL use only the supplied figures in the explanation and SHALL give no advice or product recommendation.
- IF the explanation contains a figure that was not supplied, or the AI provider fails, times out or is not configured, THE SYSTEM SHALL show a standard template explanation labelled "Standard explanation".
- THE SYSTEM SHALL label results as estimates to be confirmed with the FC.

### R5 Consent and sharing
- THE SYSTEM SHALL NOT share any client data with an FC before the client confirms.
- WHEN the client confirms, THE SYSTEM SHALL share only the categories toggled on.
- WHEN the client changes FC, THE SYSTEM SHALL offer to remove the old FC's access.
- WHEN the client selects Clear my data, THE SYSTEM SHALL delete their data after confirmation and include shared data only if the opt-in box is ticked.

### R6 Change FC
- WHEN the client picks "match me randomly", THE SYSTEM SHALL assign an available FC by language and needs.
- WHEN the client enters a referral code, THE SYSTEM SHALL validate it and preview the FC before confirming.
- WHEN a new FC receives a request, THE SYSTEM SHALL show Accept and Decline with an AI pre-meeting summary.

### R7 Purchase
- WHEN the client chooses Buy, THE SYSTEM SHALL require the comprehension quiz before payment.
- THE SYSTEM SHALL record quiz results and, with consent, share unclear points with the FC.

### R8 ROA
- WHEN an FC adds a meeting summary to the client profile, THE SYSTEM SHALL make it available to the ROA draft.
- THE SYSTEM SHALL list missing ROA items and block sign-off until resolved or acknowledged.
- WHEN the FC signs off, THE SYSTEM SHALL lock the version and write a tamper-evident log entry.

## 6. Future development (not in the main flow)

- Scenario-based comparison explanations: test against a normal table.
- Adaptive follow-up questions in guided discovery: test against a standard form.
- Life-event prompts in the simulator.
- Critical Illness and Disability gap modes.
- Comparing other insurers' policies at scale and testing extraction accuracy and the Unconfirmed state.

