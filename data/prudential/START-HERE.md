# Prudential Singapore policy source pack

Checked 20 November 2026. This pack covers the four public consumer catalogue categories for Prudential Assurance Company Singapore. It contains 41 distinct catalogue entries and eight additional PRUShield and PRUExtra variants, including two closed PRUShield plans. A catalogue listing does not establish eligibility or availability through every sales channel. Group plans, unlisted distributor offers and every legacy policy version are outside this inventory.

| File | Purpose |
|---|---|
| prudential-catalogue.json | Importable plan records, document metadata, relationships and three verified paragraph coordinates |
| prudential-plans.csv | Spreadsheet view of the catalogue, coverage focus and document IDs |
| prudential-documents.csv | Official URLs, document types, languages and local PDF paths where included |
| contracts/ | Two original Prudential sample contracts published by Singapore’s Ministry of Health |

The 70 document records comprise 66 brochures, two sample contracts, one product summary, and one premium reference page. Brochures and the product summary are linked to their original publishers. Blank fields indicate that an individual link was not verified. Most plans in this pack have marketing or summary materials only. Ask a Prudential representative for the applicable wording by plan, rider and version before enabling contract-level explanations for those plans.

## Start the prototype with PRUShield

The April 2026 MOH sample has Premier and Plus in Part One and Standard Plan in Part Two. Use those three variants to replace the fictional comparison pair first. The second sample covers PRUShield A and B, both closed to new members. PRUExtra riders have brochure references in this pack. Get the separate rider wording before enabling contract-backed claims about the riders.

Keep the product name, variant, document version and effective date visible. A dated sample may differ from a customer’s issued policy and policy certificate. Premiums vary with the applicable plan and inputs. The official rate reference has dated tables and this pack supplies no personalised quotes.

The JSON includes three paragraph anchors checked against the bundled April 2026 sample PDF. PDF pages are one-based. Coordinates are PDF points with the origin at the top left. Convert them to the viewer’s coordinate system and use the document hash to tie highlights to the correct version. One example jumps to page 30 and highlights the co-insurance paragraph for Premier and Plus. Another goes to page 55 for the separate Standard Plan wording. Scope retrieval to the selected plan and rider.

## Paste into Claude or Kiro

```text
Use this source pack to replace the fictional policy catalogue in Insurance
Navigator. Import prudential-catalogue.json into the existing plan catalogue
and keep document types, plan variants, dates, and source URLs intact.

Start the contract-backed pilot with PRUShield Premier, Plus and Standard.
Retrieve passages from the corresponding section of the April 2026 sample
contract. Explain selected plans in plain language in the user's chosen
supported language. Keep cited policy wording in the original language.
For each material coverage claim, return an existing citation ID. Clicking
it should open the source PDF at the right page and highlight the exact
paragraph. Use the three included anchors to verify the viewer first.

Keep brochures and summaries explicitly labelled. For a plan with only a
brochure or summary, attribute explanations to that document and show
“Contract wording not verified” for unsupported contract-level claims.
Keep closed products out of the new-plan selector. Treat PRUExtra as
separate riders and enforce the applicable base-plan relationships.

Use the existing Flask app, PostgreSQL and Groq server-side. Keep API keys
out of the repo. Use the catalogue metadata and sample documents only.
Preserve existing users and questionnaire data. Before changing the
comparison flow, verify that citations open and highlight the correct
paragraph for the right version and selected plan.
```

## Primary sources

- [Prudential Singapore product catalogue](https://www.prudential.com.sg/en/products/)
- [Health Protection](https://www.prudential.com.sg/en/products/health/)
- [Life Protection](https://www.prudential.com.sg/en/products/life/)
- [Wealth Accumulation](https://www.prudential.com.sg/en/products/wealth/)
- [Legacy Planning](https://www.prudential.com.sg/en/products/legacy-planning/)
- [Prudential PRUShield and PRUExtra](https://www.prudential.com.sg/en/products/health/medical/prushield/)
- [Singapore MOH Integrated Shield Plan comparison and sample contracts](https://www.moh.gov.sg/managing-expenses/schemes-and-subsidies/integrated-shield-plans/comparision-of-integrated-shield-plans/)
- [CompareFIRST, Prudential DIRECT – PRUProtect Life II product summary](https://www.comparefirst.sg/wap/prodSummaryPdf/199002477Z/PRUProtect_Life_II_CC_Product_Summary.pdf)
