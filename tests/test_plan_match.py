"""Plan matching rules: ranking, budget trade-off and compatibility rows. Pure rules, no database."""

from types import SimpleNamespace

import pytest

from app import plan_match as pm

OTHER = SimpleNamespace(status="answered", selected=["other"])


def plan(plan_id, kind, premium_dollars):
    return SimpleNamespace(id=plan_id, type=kind, monthly_premium_cents=premium_dollars * 100)


def answer(key):
    return SimpleNamespace(status="answered", selected=[key])


SKIPPED = SimpleNamespace(status="skipped", selected=[])
CATALOGUE = [plan(1, "Term life", 89), plan(2, "Hospitalisation", 142), plan(3, "Critical illness", 215),
             plan(4, "Whole life", 176), plan(5, "Retirement", 250), plan(6, "Disability", 68)]
HOSPITAL = [("hospital", "Hospital and medical cover")]
DEPENDANTS = [("dependants", "Protecting the people who rely on your income")]


def names(ranked):
    return [item["policy"].id for item in ranked]


# ---------- budget bands ----------

@pytest.mark.parametrize("key, premium, fits", [
    ("under_100", 99, True), ("under_100", 100, False), ("under_100", 68, True),
    ("100_300", 300, True), ("100_300", 301, False), ("100_300", 50, True),
    ("300_500", 500, True), ("300_500", 501, False),
])
def test_budget_fit_boundaries(key, premium, fits):
    budget = pm.budget_for({"q4": answer(key)})
    assert budget.fits(premium * 100) is fits


@pytest.mark.parametrize("q4", [None, SKIPPED, answer("not_sure"), OTHER])
def test_unknown_budget_is_none(q4):
    assert pm.budget_for({"q4": q4} if q4 is not None else {}) is None


# ---------- ranking ----------

def test_type_match_beats_budget_fit_and_earlier_priority_beats_later():
    budget = pm.budget_for({"q4": answer("under_100")})
    ranked = pm.rank_plans(CATALOGUE, HOSPITAL + [("retirement", "Retirement planning")], budget)
    # Hospital plan (priority 1) first even though it does not fit; retirement (priority 2) second even though over budget;
    # then plans that only fit the budget (Term 89, Disability 68) in catalogue order; the rest last.
    assert names(ranked) == [2, 5, 1, 6, 3, 4]
    assert ranked[0]["reason"] == "Matches your priority: Hospital and medical cover."
    assert ranked[2]["reason"] == "Fits your stated budget."
    assert ranked[4]["reason"] == "" and not ranked[4]["matched"]


def test_a_plan_can_match_both_ways_and_says_so():
    budget = pm.budget_for({"q4": answer("100_300")})
    ranked = pm.rank_plans(CATALOGUE, HOSPITAL, budget)
    assert ranked[0]["policy"].id == 2
    assert ranked[0]["reason"] == "Matches your priority: Hospital and medical cover. Fits your stated budget."


def test_ties_use_the_fixed_catalogue_order():
    # Term (1) and Whole (4) both speak to dependants and both fit: catalogue order decides, every time.
    budget = pm.budget_for({"q4": answer("100_300")})
    first = names(pm.rank_plans(CATALOGUE, DEPENDANTS, budget))
    assert first[:2] == [1, 4] and names(pm.rank_plans(CATALOGUE, DEPENDANTS, budget)) == first
    reordered = [CATALOGUE[3]] + CATALOGUE[:3] + CATALOGUE[4:]
    assert names(pm.rank_plans(reordered, DEPENDANTS, budget))[:2] == [4, 1]


def test_budget_breaks_a_tie_between_same_type_matches():
    budget = pm.budget_for({"q4": answer("under_100")})        # Term fits (89), Whole does not (176)
    assert names(pm.rank_plans([CATALOGUE[3], CATALOGUE[0]], DEPENDANTS, budget)) == [1, 4]


def test_unknown_answers_never_match():
    assert not any(item["matched"] for item in pm.rank_plans(CATALOGUE, [], None))
    ranked = pm.rank_plans(CATALOGUE, [], None)
    assert names(ranked) == [1, 2, 3, 4, 5, 6]                  # plain catalogue order
    assert all(item["reason"] == "" for item in ranked)


def test_a_priority_with_no_plan_type_matches_nothing():
    ranked = pm.rank_plans(CATALOGUE, [("existing_cover", "Checking your existing cover")], None)
    assert not any(item["matched"] for item in ranked)


# ---------- budget trade-off ----------

def test_tradeoff_inside_the_band_shows_what_is_left():
    budget = pm.budget_for({"q4": answer("100_300")})
    result = pm.budget_tradeoff(14200, budget)
    assert result["known"] and result["fits"]
    assert result["headline"] == "Within your budget: S$142 a month. Your band is S$100–300 a month."
    assert result["detail"] == "Up to S$158 a month of your band is left."
    assert "Any other premiums" in result["note"]


def test_tradeoff_over_the_band():
    budget = pm.budget_for({"q4": answer("under_100")})
    result = pm.budget_tradeoff(14200, budget)
    assert not result["fits"] and result["headline"] == "Above your budget: S$142 a month. Your band is Under S$100 a month."
    assert result["detail"] == "It is S$42 a month over the top of your band."


def test_tradeoff_at_the_top_of_a_band_leaves_nothing():
    budget = pm.budget_for({"q4": answer("100_300")})
    result = pm.budget_tradeoff(30000, budget)
    assert result["fits"] and result["detail"] == "None of your band is left."


@pytest.mark.parametrize("q4", [None, SKIPPED, answer("not_sure"), OTHER])
def test_tradeoff_with_an_unknown_budget_says_so_and_guesses_nothing(q4):
    result = pm.budget_tradeoff(14200, pm.budget_for({"q4": q4} if q4 is not None else {}))
    assert result["known"] is False
    assert result["headline"] == "Your monthly budget is unknown."
    assert "fits" not in result and "left" not in result and "S$" not in result["note"]


def test_tradeoff_shows_cents_when_the_premium_has_them():
    budget = pm.budget_for({"q4": answer("100_300")})
    assert "S$89.50 a month" in pm.budget_tradeoff(8950, budget)["headline"]


# ---------- compatibility rows ----------

def test_family_row():
    assert pm.family_row(("spouse_child", "My spouse and one child"), 500000)["status"] == pm.GOOD
    weak = pm.family_row(("parents_family", "My parents or other family"), 0)
    assert weak["status"] == pm.WEAK and "no death benefit" in weak["text"]
    assert pm.family_row(("no_one", "No one right now"), 500000)["status"] == pm.PARTIAL
    unknown = pm.family_row(None, 500000)
    assert unknown["status"] == pm.UNKNOWN and unknown["label"] == "Not enough information" and unknown["driver"] is None


def test_family_row_names_the_answer_that_drove_it():
    row = pm.family_row(("spouse_child", "My spouse and one child"), 500000)
    assert row["driver"] == "You said: My spouse and one child" and "S$500,000" in row["text"]


def test_budget_row():
    band = pm.budget_for({"q4": answer("100_300")})
    assert pm.budget_row(band, 14200, None)["status"] == pm.GOOD
    assert pm.budget_row(band, 31000, None)["status"] == pm.WEAK
    unknown = pm.budget_row(None, 14200, None)
    assert unknown["status"] == pm.UNKNOWN and unknown["driver"] is None


@pytest.mark.parametrize("ward, state, status", [
    (("private", "Private hospital"), "included", pm.GOOD),
    (("public_a", "Public hospital · A ward"), "optional_rider", pm.GOOD),
    (("private", "Private hospital"), "not_included", pm.WEAK),
    (("no_preference", "No preference yet"), "included", pm.PARTIAL),
    (("no_preference", "No preference yet"), "not_included", pm.PARTIAL),
    (("private", "Private hospital"), None, pm.UNKNOWN),
    (None, "included", pm.UNKNOWN),
    (None, None, pm.UNKNOWN),
])
def test_hospital_row(ward, state, status):
    assert pm.hospital_row(ward, state)["status"] == status


def test_overlap_row_uses_the_saved_simulator_figure_first():
    row = pm.overlap_row(150000, ("none", "No existing cover"), 500000)
    assert row["status"] == pm.PARTIAL and "S$150,000" in row["driver"] and "S$500,000" in row["text"]
    assert pm.overlap_row(150000, None, 0)["status"] == pm.GOOD
    assert pm.overlap_row(0, None, 500000)["status"] == pm.GOOD
    assert "S$0" in pm.overlap_row(0, None, 500000)["driver"]


def test_overlap_row_falls_back_to_the_existing_cover_answer():
    assert pm.overlap_row(None, ("none", "No existing cover"), 500000)["status"] == pm.GOOD
    partial = pm.overlap_row(None, ("personal_employer", "Personal life + employer plan"), 500000)
    assert partial["status"] == pm.PARTIAL and partial["driver"] == "You said: Personal life + employer plan"
    unknown = pm.overlap_row(None, None, 500000)
    assert unknown["status"] == pm.UNKNOWN and unknown["driver"] is None


def test_every_status_has_a_label_and_a_chip():
    assert set(pm.STATUS_LABELS) == set(pm.STATUS_CHIPS) == {pm.GOOD, pm.PARTIAL, pm.WEAK, pm.UNKNOWN}
    assert pm.STATUS_LABELS[pm.UNKNOWN] == "Not enough information"
