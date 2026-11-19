# Insurance Navigator — Design

UI, flows, calculations and suggested data model for [requirements.md](requirements.md). Implementation checklist: [tasks.md](tasks.md).

## 1. UI source of truth

Source of truth for the UI: the Figma file "Insurance App – Client & FC Flows" (31 screens, five sections, one page). Build to match it. Where this file and Figma disagree, flag it rather than guessing.

## 2. Design tokens

| Token | Value |
|---|---|
| Background | `#F8F6F2` (cream) |
| Card | `#FFFFFF`, radius 16, 1px border `#EDE9E3` |
| Ink | `#1A1A1A` |
| Muted text | `#8A8780` |
| Accent | `#D1694A` (terracotta) |
| Accent soft | `#FBE9E2` |
| Success | text `#1F7A40`, fill `#E3F5E6` |
| Warning | text `#9E6B08`, fill `#FFF2D1` |
| Info | text `#335AA6`, fill `#E6EEFA` |
| Font | Inter (Regular, Medium, Semi Bold, Bold) |
| Button | radius 10; primary = accent fill, secondary = white with border |
| Frame width | 1440 desktop |

## 3. Screen inventory and flow

### Section 1: Onboarding (11 screens)
01 Sign up → 02 MCQ questions 1–8 → 02b Review → 03 Dashboard (coverage map, profile summary, top 3 plans)

### Section 2: Plans & Compare (4)
04 All plans → 05 Plan detail → 06 Compare policies → 14 Coverage Needs Simulator

### Section 3: Decide & Pay (3)
07 Buy or consult FC → 08 Comprehension quiz → 09 Payment

### Section 4: Trust & Settings (7)
10 Settings, 11 Citation PDF viewer, 12 Clear my data, FC sharing consent, 13a Review current FC, 13b Choose new FC, 13c Send my data

### Section 5: FC Portal (6)
F1 Clients → F2 Client profile → F3 Comparison gap → F4 Video meeting → F5 ROA draft → F6 Incoming client

### Key handoffs
- Dashboard **Send to my FC** → consent → FC **F6 Incoming client**
- Change FC step 3 **Confirm** → FC **F6 Incoming client**
- F6 **Accept** → F2 Client profile; **Start ROA draft** → F5; **Schedule intro call** → F4
- F4 **Add to client profile** → F2; F5 **Finalise ROA** → F1
- Every citation link → 11 Citation PDF viewer
- Starting points: Client = 01, FC = F1

## 4. Coverage Needs Simulator (death cover only)

Inputs: dependants' support (years × annual amount), housing debt, other debts, savings, existing death cover (pre-filled from the client's plans).

Formulas (always visible in the UI):

```
need = support + debts − savings
gap  = need − existing death cover
```

Controls:
- Add or remove a Prudential plan. A term plan's cover ends at its expiry age.
- Milestone events on a timeline: buy a home (adds debt), new child (adds support), income change, pay down mortgage, retire at 55/65, job break. Each has an amount and an age.

Outputs:
- Gap today, gap after life events, gap with the added plan.
- Chart of cover vs need over time, with a note when a term plan expires.
- "What changed, and why": a rule-based breakdown of which input moved the gap and by how much. No chatbot.

Worked example used in the design: need S$280k, existing cover S$150k, gap S$130k. After a home (+S$300k debt) and a child (+S$150k support) the gap is S$580k. Adding PRUActive Term (+S$500k) gives S$80k.

Critical Illness and Disability modes are shown as "coming next".

## 5. Suggested data model

- `User` (id, role: client | fc, language, accessibility prefs)
- `QuestionnaireAnswer` (userId, questionId, selected[], other text)
- `Policy` (id, insurer, name, type, document ref)
- `PolicyClause` (policyId, page, clause, line, text)
- `Citation` (statementId, policyClauseId)
- `ComparisonCell` (rowKey, policyId, state, finding, clauseId)
- `Scenario` (userId, inputs, plans[], events[], result)
- `ShareGrant` (clientId, fcId, categories[], grantedAt, revokedAt)
- `FcAssignment` (clientId, fcId, method: random | referral, status)
- `Meeting` (clientId, fcId, transcript ref, aiSummary)
- `Roa` (clientId, fcId, version, status, lockedAt, auditLog[])

## 6. Known gaps from the Figma build (decide before coding)

The Figma agent reported it skipped or only partly built:

- Free-text entry on "Other": the design shows an input area only.
- Missing destinations: login and the other five plan-detail pages.
- Controls without matching screens or states: plan filters, translations and Read Aloud, expanded summaries, accessibility settings, FC feedback and matching choices, simulator editing, PDF paging and downloads, meeting media controls, summary editing, exports.
- Placeholder content: policy clauses, premiums and FC names are sample data. Replace with real Prudential documents.

