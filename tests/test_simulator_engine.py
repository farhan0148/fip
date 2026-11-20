"""Unit tests for app/simulator.py. They need no database."""

import copy
import json
from pathlib import Path

import pytest

from app import simulator

CASES = json.loads((Path(__file__).parent / "simulator_cases.json").read_text(encoding="utf-8"))
CATALOGUE = {entry["id"]: entry for entry in CASES["catalogue"]}


def build_payload(case):
    payload = copy.deepcopy(CASES["base"])
    patch = case["patch"]
    payload["fields"].update(patch.get("fields", {}))
    if "plans" in patch:
        payload["plans"] = patch["plans"]
    if "events" in patch:
        payload["events"] = patch["events"]
    return payload


def comparable(result):
    return {
        "need_today": result["need_today"],
        "gap_today": result["gap_today"],
        "evaluation_age": result["evaluation_age"],
        "gap_after_events": result["gap_after_events"],
        "gap_with_plans": result["gap_with_plans"],
        "final_gap": result["final_gap"],
        "total_change": result["total_change"],
        "points": [[p["age"], p["need"], p["cover"]] for p in result["points"]],
        "gap_effects": [c["gap_effect"] for c in result["contributions"]],
        "expiry_notes": [
            {k: n[k] for k in ("plan_id", "status", "age", "before", "after")} for n in result["expiry_notes"]
        ],
    }


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["name"])
def test_shared_cases(case):
    clean, errors = simulator.validate(build_payload(case), CATALOGUE)
    expect = case["expect"]
    if "errors" in expect:
        assert clean is None
        assert sorted(errors) == expect["errors"]
        return
    assert errors == {}
    assert comparable(simulator.calculate(clean, CATALOGUE)) == expect


def run(case_name):
    case = next(c for c in CASES["cases"] if c["name"].startswith(case_name))
    clean, errors = simulator.validate(build_payload(case), CATALOGUE)
    assert not errors
    return simulator.calculate(clean, CATALOGUE)


def test_worked_example_gaps_130k_580k_80k():
    result = run("worked example")
    assert (result["gap_today"], result["gap_after_events"], result["gap_with_plans"]) == (130_000, 580_000, 80_000)
    assert result["need_today"] == 280_000


def test_contributions_always_add_up_to_the_total_change():
    for case in CASES["cases"]:
        if "errors" in case["expect"]:
            continue
        clean, _ = simulator.validate(build_payload(case), CATALOGUE)
        result = simulator.calculate(clean, CATALOGUE)
        assert sum(c["gap_effect"] for c in result["contributions"]) == result["total_change"], case["name"]


def test_term_plan_stops_counting_after_its_last_covered_age():
    result = run("plan only")
    by_age = {p["age"]: p["cover"] for p in result["points"]}
    assert by_age[65] == 650_000 and by_age[66] == 150_000


@pytest.mark.parametrize(
    "text, expected",
    [("1,000", (1000, None)), ("1000", (1000, None)), ("S$ 2,500", (2500, None)), ("$7", (7, None)),
     ("-5", (-5, None)), ("0", (0, None)), ("", (None, "blank")), ("   ", (None, "blank")),
     (None, (None, "blank")), ("1.5", (None, "format")), ("1,00", (None, "format")),
     ("abc", (None, "format")), ("+5", (None, "format")), ("١٢٣", (None, "format")), ("1e3", (None, "format"))],
)
def test_parse_amount(text, expected):
    assert simulator.parse_amount(text) == expected


def test_garbage_payloads_are_rejected_not_crashed():
    for payload in (None, [], "x", 5, {"fields": "x"}, {"fields": {}, "events": "x"},
                    {"fields": {}, "events": [None, 5, "x"]}, {"fields": {}, "plans": "x"},
                    {"fields": {}, "plans": [None]}, {"fields": {}, "plans": [True]}):
        clean, errors = simulator.validate(payload, CATALOGUE)
        assert clean is None and errors


def test_too_many_events_rejected():
    payload = copy.deepcopy(CASES["base"])
    payload["events"] = [{"type": "new_child", "age": "31", "amount": "1"}] * 13
    clean, errors = simulator.validate(payload, CATALOGUE)
    assert clean is None and "events" in errors
