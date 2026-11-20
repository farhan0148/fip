"""Coverage Needs Simulator, death cover only (design.md section 4, requirement R4).

Formulas, always shown in the UI:

    need = support + debts - savings
    gap  = need - existing death cover

    support = years x annual amount, plus support added by life events
    debts   = housing debt + other debts, adjusted by life events

This is plain arithmetic. No AI service is involved. The browser runs the same
rules in static/js/simulator-core.js so results update without a server call;
tests/simulator_cases.json keeps the two in step. Flask re-validates and
recalculates everything when a scenario is saved.

Rules
-----
* Whole dollars only. Every input must be filled in (enter 0 if none applies);
  a blank or invalid field blocks the results instead of being treated as 0.
* Life events apply from their age onwards. "After life events" means after the
  last event, at the evaluation age E = max(age today, latest event age).
* A plan's cover applies up to and including its last covered age. It drops at
  the next age (a plan covering up to 65 drops at 66).
* Gap today uses only the existing cover. "With added plans" adds the cover of
  each added plan that still applies at E.
* A pay-down cannot exceed the housing debt at that age, and an income change
  cannot take support below zero. Both are reported as errors.
* Retire at 55/65 and job break are listed in the spec but their effect on a
  death-cover need is not defined, so they are not modelled yet.
"""

import re

MAX_AMOUNT = 1_000_000_000
AGE_MIN, AGE_MAX = 18, 99
EVENT_AGE_MAX = 100
MAX_EVENTS = 12
MAX_PLANS = 3
MAX_SUPPORT_YEARS = 60

# key -> (label, kind). Kinds: age, years, money.
FIELDS = {
    "age": ("Your age today", "age"),
    "support_years": ("Years of support", "years"),
    "support_annual": ("Annual support amount", "money"),
    "housing_debt": ("Housing debt", "money"),
    "other_debts": ("Other debts", "money"),
    "savings": ("Savings available", "money"),
    "existing_cover": ("Existing death cover", "money"),
}

# key -> label, supported, target input, direction on that input, signed amount allowed
EVENT_TYPES = {
    "buy_home": dict(label="Buy a home", supported=True, target="housing_debt", direction=1, signed=False, input_label="Housing debt"),
    "new_child": dict(label="New child", supported=True, target="support", direction=1, signed=False, input_label="Support need"),
    "income_change": dict(label="Income change", supported=True, target="support", direction=1, signed=True, input_label="Support need"),
    "pay_down_mortgage": dict(label="Pay down mortgage", supported=True, target="housing_debt", direction=-1, signed=False, input_label="Housing debt"),
    "retire_55": dict(label="Retire at 55", supported=False),
    "retire_65": dict(label="Retire at 65", supported=False),
    "job_break": dict(label="Job break (12 months)", supported=False),
}

_DIGITS = re.compile(r"^-?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)$")


def parse_amount(raw):
    """Parse a whole-dollar string. Returns (value, error) with error in None, 'blank', 'format'."""
    text = re.sub(r"\s+", "", "" if raw is None else str(raw))
    text = re.sub(r"^(-?)(?:S\$|\$)", r"\1", text, flags=re.IGNORECASE)
    if text == "":
        return None, "blank"
    if not _DIGITS.match(text):
        return None, "format"
    return int(text.replace(",", "")), None


def catalogue_entry(policy):
    """Simulator view of a Policy row (whole dollars)."""
    cover = (policy.death_cover_cents or 0) // 100
    return {
        "id": policy.id,
        "name": policy.name,
        "type": policy.type,
        "monthly_premium": policy.monthly_premium_cents // 100,
        "death_cover": cover,
        "ends_at_age": policy.cover_ends_at_age,
        "addable": cover > 0,
    }


def _number(raw, label, low, high, whole_label):
    value, problem = parse_amount(raw)
    if problem == "blank":
        return None, f"Enter {label.lower()}. Use 0 if it does not apply."
    if problem == "format":
        return None, f"{label}: use {whole_label}, for example 50,000."
    if value < low or value > high:
        return None, f"{label}: enter a value from {low:,} to {high:,}."
    return value, None


def validate(payload, catalogue):
    """Return (clean, errors). clean is None when errors is not empty.

    payload: {"fields": {name: str}, "plans": [policy id], "events": [{"type","age","amount"}]}
    catalogue: {policy id: catalogue entry}
    Error keys: field names, "plans", "events", "events.N.type|age|amount".
    """
    errors = {}
    if not isinstance(payload, dict):
        return None, {"payload": "The scenario could not be read."}
    raw_fields = payload.get("fields")
    raw_fields = raw_fields if isinstance(raw_fields, dict) else {}

    fields = {}
    for key, (label, kind) in FIELDS.items():
        if kind == "age":
            low, high, what = AGE_MIN, AGE_MAX, "a whole number of years"
        elif kind == "years":
            low, high, what = 0, MAX_SUPPORT_YEARS, "a whole number of years"
        else:
            low, high, what = 0, MAX_AMOUNT, "whole dollars"
        value, problem = _number(raw_fields.get(key), label, low, high, what)
        if problem:
            errors[key] = problem
        else:
            fields[key] = value

    # Plans
    plans = []
    raw_plans = payload.get("plans")
    if raw_plans is None:
        raw_plans = []
    if not isinstance(raw_plans, list) or len(raw_plans) > MAX_PLANS:
        errors["plans"] = f"Choose up to {MAX_PLANS} plans."
    else:
        for item in raw_plans:
            if isinstance(item, bool) or not isinstance(item, int) or item not in catalogue:
                errors["plans"] = "One of the selected plans is not available."
                break
            if not catalogue[item]["addable"]:
                errors["plans"] = f"{catalogue[item]['name']} has no death benefit, so it cannot be added."
                break
            if item in plans:
                errors["plans"] = "Each plan can be added once."
                break
            plans.append(item)

    # Events
    events = []
    raw_events = payload.get("events")
    if raw_events is None:
        raw_events = []
    if not isinstance(raw_events, list) or len(raw_events) > MAX_EVENTS:
        errors["events"] = f"Use at most {MAX_EVENTS} life events."
        raw_events = []
    for index, item in enumerate(raw_events):
        prefix = f"events.{index}"
        item = item if isinstance(item, dict) else {}
        kind = item.get("type")
        info = EVENT_TYPES.get(kind)
        event_ok = True
        if info is None:
            errors[f"{prefix}.type"] = "Choose an event type."
            event_ok = False
        elif not info["supported"]:
            errors[f"{prefix}.type"] = f"{info['label']} is not modelled yet. Choose another event."
            event_ok = False
        low_age = fields.get("age", AGE_MIN)
        age, problem = _number(item.get("age"), "Event age", low_age, EVENT_AGE_MAX, "a whole number of years")
        if problem:
            errors[f"{prefix}.age"] = problem
            event_ok = False
        signed = bool(info and info.get("signed"))
        amount, problem = _number(
            item.get("amount"), "Event amount", -MAX_AMOUNT if signed else 0, MAX_AMOUNT, "whole dollars"
        )
        if problem:
            errors[f"{prefix}.amount"] = problem
            event_ok = False
        events.append({"type": kind, "age": age, "amount": amount, "ok": event_ok})

    # Timeline checks need valid numbers everywhere.
    if not errors:
        _check_timeline(fields, events, errors)

    if errors:
        return None, errors
    return {
        "fields": fields,
        "plans": plans,
        "events": [{"type": e["type"], "age": e["age"], "amount": e["amount"]} for e in events],
    }, {}


def _sorted_events(events):
    return sorted(enumerate(events), key=lambda pair: (pair[1]["age"], pair[0]))


def _check_timeline(fields, events, errors):
    housing = fields["housing_debt"]
    support = fields["support_years"] * fields["support_annual"]
    for index, event in _sorted_events(events):
        info = EVENT_TYPES[event["type"]]
        change = info["direction"] * event["amount"]
        if info["target"] == "housing_debt":
            housing += change
            if housing < 0:
                errors[f"events.{index}.amount"] = "This pay-down is larger than the housing debt at that age."
                return
        else:
            support += change
            if support < 0:
                errors[f"events.{index}.amount"] = "This would take support below zero."
                return


def _state_at(fields, events, age):
    housing = fields["housing_debt"]
    support = fields["support_years"] * fields["support_annual"]
    for _, event in _sorted_events(events):
        if event["age"] > age:
            continue
        info = EVENT_TYPES[event["type"]]
        if info["target"] == "housing_debt":
            housing += info["direction"] * event["amount"]
        else:
            support += info["direction"] * event["amount"]
    return support, housing


def _plan_active(plan, age):
    return plan["ends_at_age"] is None or age <= plan["ends_at_age"]


def calculate(clean, catalogue):
    """clean comes from validate(). Returns the result dict (all whole dollars)."""
    f = clean["fields"]
    events = clean["events"]
    chosen = [catalogue[pid] for pid in clean["plans"]]

    def need_at(age):
        support, housing = _state_at(f, events, age)
        return support + housing + f["other_debts"] - f["savings"]

    def cover_at(age):
        return f["existing_cover"] + sum(p["death_cover"] for p in chosen if _plan_active(p, age))

    support_today = f["support_years"] * f["support_annual"]
    debts_today = f["housing_debt"] + f["other_debts"]
    need_today = need_at(f["age"])
    gap_today = need_today - f["existing_cover"]

    evaluation_age = max([f["age"]] + [e["age"] for e in events])
    need_after = need_at(evaluation_age)
    gap_after = need_after - f["existing_cover"]
    plan_cover = sum(p["death_cover"] for p in chosen if _plan_active(p, evaluation_age))
    gap_with = gap_after - plan_cover if chosen else None
    final_gap = gap_with if chosen else gap_after

    ages = {f["age"]} | {e["age"] for e in events}
    for p in chosen:
        if p["ends_at_age"] is not None and p["ends_at_age"] >= f["age"]:
            ages.add(p["ends_at_age"])
            if p["ends_at_age"] + 1 <= EVENT_AGE_MAX:
                ages.add(p["ends_at_age"] + 1)
    points = [{"age": a, "need": need_at(a), "cover": cover_at(a)} for a in sorted(ages)]

    contributions = []
    for _, e in _sorted_events(events):
        info = EVENT_TYPES[e["type"]]
        effect = info["direction"] * e["amount"]
        contributions.append({
            "kind": "event", "type": e["type"], "label": info["label"], "age": e["age"],
            "input_label": info["input_label"], "input_effect": effect, "gap_effect": effect,
        })
    for p in chosen:
        active = _plan_active(p, evaluation_age)
        contributions.append({
            "kind": "plan", "plan_id": p["id"], "label": p["name"], "active": active,
            "ends_at_age": p["ends_at_age"], "input_label": "Death cover",
            "input_effect": p["death_cover"] if active else 0,
            "gap_effect": -p["death_cover"] if active else 0,
        })

    notes = []
    for p in chosen:
        end = p["ends_at_age"]
        if end is None:
            continue
        if end < f["age"]:
            notes.append({"plan_id": p["id"], "name": p["name"], "status": "ended", "age": end,
                          "before": None, "after": None})
        else:
            notes.append({"plan_id": p["id"], "name": p["name"], "status": "ends", "age": end + 1,
                          "before": cover_at(end), "after": cover_at(end + 1)})

    return {
        "support_today": support_today,
        "debts_today": debts_today,
        "need_today": need_today,
        "gap_today": gap_today,
        "evaluation_age": evaluation_age,
        "need_after_events": need_after,
        "gap_after_events": gap_after,
        "plan_cover_at_evaluation": plan_cover,
        "gap_with_plans": gap_with,
        "final_gap": final_gap,
        "total_change": final_gap - gap_today,
        "points": points,
        "contributions": contributions,
        "expiry_notes": notes,
    }
