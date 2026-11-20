"""Fictional demo data for plans, sample clauses and comparison cells.

Everything here is invented sample wording. It is not taken from any real
insurance document, and `flask seed-demo` is the only thing that loads it.
Clauses are (page, clause number, line number, text). Cells are
(row_key, state, plain-language finding, (page, line) of the cited clause or None).
A row with no cell for a plan means the documents do not mention it.
"""

DEMO_POLICIES = (
    dict(insurer="Demo Insurer", name="PRUActive Term", type="Term life", monthly_premium_cents=8900,
         description="Affordable term life coverage for working adults.",
         document_ref="PRUActive Term sample policy contract (fictional)",
         death_cover_cents=50_000_000, cover_ends_at_age=65),
    dict(insurer="Demo Insurer", name="PRUShield Premier", type="Hospitalisation", monthly_premium_cents=14200,
         description="Integrated shield plan covering private hospital stays.",
         document_ref="PRUShield Premier sample policy contract (fictional)",
         death_cover_cents=0, cover_ends_at_age=None),
    dict(insurer="Demo Insurer", name="PRUCritical Care", type="Critical illness", monthly_premium_cents=21500,
         description="Multi-pay critical illness cover for up to 120 conditions.",
         document_ref="PRUCritical Care sample policy contract (fictional)",
         death_cover_cents=10_000_000, cover_ends_at_age=75),
    dict(insurer="Demo Insurer", name="PRULife Whole", type="Whole life", monthly_premium_cents=17600,
         description="Whole life cover that also builds a cash value over time.",
         document_ref="PRULife Whole sample policy contract (fictional)",
         death_cover_cents=25_000_000, cover_ends_at_age=None),
    dict(insurer="Demo Insurer", name="PRURetire Income", type="Retirement", monthly_premium_cents=25000,
         description="Saves towards a guaranteed monthly income from age 65.",
         document_ref="PRURetire Income sample policy contract (fictional)",
         death_cover_cents=0, cover_ends_at_age=None),
    dict(insurer="Demo Insurer", name="PRUDisability Guard", type="Disability", monthly_premium_cents=6800,
         description="Pays a monthly income if illness or injury stops you working.",
         document_ref="PRUDisability Guard sample policy contract (fictional)",
         death_cover_cents=0, cover_ends_at_age=None),
)

CLAUSES = {
    "PRUActive Term": (
        (2, "2.1", 4, "Premiums are fixed at the amount in the Policy Schedule for the whole policy term."),
        (2, "2.2", 5, "Premiums must be paid on time. A grace period of 30 days applies."),
        (3, "3.1", 7, "The cover starts on the Policy Start Date and ends when the Life Assured reaches age 65."),
        (3, "3.2", 8, "If the Life Assured dies or is diagnosed with total permanent disability during the policy term, the Company will pay the Sum Assured in a single lump sum."),
        (3, "3.3", 9, "No benefit is payable once the policy term has ended."),
        (5, "4.4", 2, "This plan does not pay for hospital, surgical or other medical expenses."),
        (5, "4.5", 3, "Claims linked to pre-existing conditions are subject to the exclusions in Section 7."),
        (8, "6.3", 5, "This plan has no cash value. No amount is payable if the policy is surrendered or lapses."),
        (11, "9.1", 3, "Critical Illness Rider (optional): for an extra premium, a lump sum is paid on diagnosis of a listed critical illness. The rider is not part of the base plan."),
    ),
    "PRUShield Premier": (
        (2, "2.1", 3, "Premiums are reviewed each year and may change. The Company will tell you 30 days before a change."),
        (3, "3.1", 4, "The plan renews every year up to age 100 as long as premiums are paid."),
        (4, "4.1", 6, "This plan pays eligible hospital bills and approved outpatient treatment at a private hospital, up to the annual limit in the Policy Schedule."),
        (4, "4.2", 7, "Eligible bills are reimbursed after the deductible and co-insurance."),
        (4, "4.6", 9, "This plan provides no death benefit and no maturity benefit."),
        (12, "11.2", 4, "Amounts payable when the policy ends are set out in the Policy Schedule."),
        (14, "12.3", 2, "Premium waiver rider (optional): premiums are waived if the Life Assured becomes totally disabled. An extra premium applies."),
    ),
    "PRUCritical Care": (
        (2, "2.1", 5, "Premiums are fixed for the first 5 years and may be reviewed after that."),
        (3, "3.1", 4, "The cover ends when the Life Assured reaches age 75."),
        (3, "3.2", 6, "A lump sum is paid on first diagnosis of any listed critical illness, after a 90-day waiting period."),
        (3, "3.4", 8, "If death occurs, the Sum Assured less any critical illness payment already made is paid."),
        (6, "5.2", 3, "Hospital and medical expenses are not covered under this plan."),
        (9, "7.1", 4, "The plan may build a surrender value after year 10. The amount is shown in the Policy Schedule."),
    ),
    "PRULife Whole": (
        (2, "2.1", 3, "Premiums are fixed at the amount in the Policy Schedule and are payable for 20 years."),
        (3, "3.1", 4, "The cover lasts for the whole of the Life Assured's life, as long as premiums are paid for the premium term."),
        (3, "3.2", 6, "On death the Company will pay the Sum Assured of S$250,000 plus any bonuses declared."),
        (5, "4.4", 2, "This plan does not pay for hospital, surgical or other medical expenses."),
        (8, "6.3", 5, "After the third policy year the plan builds a cash value that can be withdrawn by surrendering the policy."),
        (11, "9.1", 3, "Critical Illness Rider (optional): for an extra premium, a lump sum is paid on diagnosis of a listed critical illness."),
        (14, "12.3", 2, "Premium waiver rider (optional): premiums are waived if the Life Assured becomes totally disabled. An extra premium applies."),
    ),
    "PRURetire Income": (
        (2, "2.1", 3, "Premiums are fixed for the first 10 years and may be reviewed after that."),
        (3, "3.1", 4, "The plan pays a guaranteed monthly income from the payout start age of 65 for 20 years."),
        (3, "3.3", 8, "If the Life Assured dies before age 65, the Company will return the premiums paid, without interest."),
        (5, "4.6", 3, "This plan does not pay for hospital, surgical or other medical expenses."),
        (7, "5.2", 5, "Any income above the guaranteed amount is not guaranteed and depends on how the plan performs."),
        (9, "7.1", 4, "The surrender value in the early years may be less than the premiums paid."),
        (12, "10.2", 2, "Premium waiver rider (optional): premiums are waived if the Life Assured becomes totally disabled. An extra premium applies."),
    ),
    "PRUDisability Guard": (
        (2, "2.1", 4, "Premiums are fixed for the first 5 years and may be reviewed after that."),
        (3, "3.1", 5, "The cover ends when the Life Assured reaches age 65."),
        (3, "3.2", 7, "If the Life Assured is totally disabled for more than 90 days, the Company will pay a monthly benefit of 75% of the insured income, up to the limit in the Policy Schedule."),
        (4, "3.5", 3, "This plan provides no death benefit."),
        (5, "4.2", 2, "This plan does not pay for hospital, surgical or other medical expenses."),
        (8, "6.1", 3, "This plan has no cash value."),
        (10, "8.1", 6, "Claims arising from a condition that existed before the policy start date are not covered."),
    ),
}

CELLS = {
    "PRUActive Term": (
        ("duration", "included", "Cover runs to age 65 and pays out only if the term has not ended.", (3, 7)),
        ("premium", "included", "S$89 a month, fixed for the whole term, with a 30-day grace period.", (2, 4)),
        ("death_cover", "included", "Pays the Sum Assured as a lump sum on death or total permanent disability during the term.", (3, 8)),
        ("cash_value", "not_included", "No cash value. Nothing is paid if you surrender the policy or stop paying.", (8, 5)),
        ("hospital", "not_included", "Does not pay hospital or medical bills.", (5, 2)),
        ("critical_illness", "optional_rider", "Available as an optional rider at extra cost. It is not part of the base plan.", (11, 3)),
    ),
    "PRUShield Premier": (
        ("duration", "included", "Renews each year up to age 100 while premiums are paid.", (3, 4)),
        ("premium", "included", "S$142 a month today. Reviewed every year, so it may change.", (2, 3)),
        ("death_cover", "not_included", "No death benefit. This plan pays hospital bills only.", (4, 9)),
        ("cash_value", "unconfirmed", "The wording points to the Policy Schedule and does not say if any value is paid when the policy ends.", (12, 4)),
        ("hospital", "included", "Pays eligible hospital bills after the deductible and co-insurance, up to the annual limit.", (4, 6)),
        ("premium_waiver", "optional_rider", "Available as an optional rider at extra cost. Premiums are waived if you become totally disabled.", (14, 2)),
    ),
    "PRUCritical Care": (
        ("duration", "included", "Cover runs to age 75.", (3, 4)),
        ("premium", "included", "S$215 a month. Fixed for the first 5 years, then it may be reviewed.", (2, 5)),
        ("death_cover", "included", "Pays the Sum Assured on death, less any critical illness amount already paid.", (3, 8)),
        ("cash_value", "unconfirmed", "The plan may build a value after year 10, but this wording gives no amount or conditions.", (9, 4)),
        ("hospital", "not_included", "Does not pay hospital or medical bills.", (6, 3)),
        ("critical_illness", "included", "Pays a lump sum on first diagnosis of a listed condition, after a 90-day waiting period.", (3, 6)),
    ),
    "PRULife Whole": (
        ("duration", "included", "Covers you for life while premiums are paid for the premium term.", (3, 4)),
        ("premium", "included", "S$176 a month, fixed, payable for 20 years.", (2, 3)),
        ("death_cover", "included", "Pays the S$250,000 Sum Assured plus any declared bonuses on death.", (3, 6)),
        ("cash_value", "included", "Builds a cash value after the third policy year that you can withdraw by surrendering the policy.", (8, 5)),
        ("hospital", "not_included", "Does not pay hospital or medical bills.", (5, 2)),
        ("critical_illness", "optional_rider", "Available as an optional rider at extra cost.", (11, 3)),
        ("premium_waiver", "optional_rider", "Available as an optional rider at extra cost. Premiums are waived if you become totally disabled.", (14, 2)),
    ),
    "PRURetire Income": (
        ("duration", "included", "Pays a guaranteed monthly income from age 65 for 20 years.", (3, 4)),
        ("premium", "included", "S$250 a month, fixed for the first 10 years and then it may be reviewed.", (2, 3)),
        ("death_cover", "included", "Returns the premiums paid, without interest, if you die before age 65.", (3, 8)),
        ("cash_value", "unconfirmed", "The surrender value in the early years may be less than the premiums paid, but the wording gives no amounts.", (9, 4)),
        ("hospital", "not_included", "Does not pay hospital or medical bills.", (5, 3)),
        ("premium_waiver", "optional_rider", "Available as an optional rider at extra cost. Premiums are waived if you become totally disabled.", (12, 2)),
    ),
    "PRUDisability Guard": (
        ("duration", "included", "Cover runs to age 65.", (3, 5)),
        ("premium", "included", "S$68 a month, fixed for the first 5 years and then it may be reviewed.", (2, 4)),
        ("death_cover", "included", "Pays a monthly benefit of 75% of insured income if you are totally disabled for more than 90 days. There is no death benefit.", (3, 7)),
        ("cash_value", "not_included", "No cash value.", (8, 3)),
        ("hospital", "not_included", "Does not pay hospital or medical bills.", (5, 2)),
    ),
}

# Two fictional demo FCs (is_demo). Names follow the Figma sample screens.
DEMO_FCS = (
    dict(email="fc.daniel@example.com", name="Daniel Koh", referral_code="FC-2048",
         speciality="Family protection", languages=["English", "中文"]),
    dict(email="fc.jane@example.com", name="Jane Tan", referral_code="FC-3117",
         speciality="Hospitalisation and retirement", languages=["English", "Bahasa Melayu"]),
)
