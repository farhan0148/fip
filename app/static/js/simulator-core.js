// Coverage Needs Simulator: the calculation, with no DOM access.
// Mirrors app/simulator.py rule for rule. tests/simulator_cases.json runs the same cases
// against both, so a change to one must be made in the other.
//
//   need = support + debts - savings
//   gap  = need - existing death cover
(function (root) {
  "use strict";

  var MAX_AMOUNT = 1000000000;
  var AGE_MIN = 18, AGE_MAX = 99, EVENT_AGE_MAX = 100;
  var MAX_EVENTS = 12, MAX_PLANS = 3, MAX_SUPPORT_YEARS = 60;

  var FIELDS = {
    age: { label: "Your age today", kind: "age" },
    support_years: { label: "Years of support", kind: "years" },
    support_annual: { label: "Annual support amount", kind: "money" },
    housing_debt: { label: "Housing debt", kind: "money" },
    other_debts: { label: "Other debts", kind: "money" },
    savings: { label: "Savings available", kind: "money" },
    existing_cover: { label: "Existing death cover", kind: "money" }
  };

  var EVENT_TYPES = {
    buy_home: { label: "Buy a home", supported: true, target: "housing_debt", direction: 1, signed: false, inputLabel: "Housing debt" },
    new_child: { label: "New child", supported: true, target: "support", direction: 1, signed: false, inputLabel: "Support need" },
    income_change: { label: "Income change", supported: true, target: "support", direction: 1, signed: true, inputLabel: "Support need" },
    pay_down_mortgage: { label: "Pay down mortgage", supported: true, target: "housing_debt", direction: -1, signed: false, inputLabel: "Housing debt" },
    retire_55: { label: "Retire at 55", supported: false },
    retire_65: { label: "Retire at 65", supported: false },
    job_break: { label: "Job break (12 months)", supported: false }
  };

  var DIGITS = /^-?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)$/;

  // Returns {value, error} with error null, "blank" or "format".
  function parseAmount(raw) {
    var text = String(raw === null || raw === undefined ? "" : raw).replace(/\s+/g, "");
    text = text.replace(/^(-?)(?:S\$|\$)/i, "$1");
    if (text === "") return { value: null, error: "blank" };
    if (!DIGITS.test(text)) return { value: null, error: "format" };
    return { value: parseInt(text.replace(/,/g, ""), 10), error: null };
  }

  function group(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function readNumber(raw, label, low, high, what) {
    var p = parseAmount(raw);
    if (p.error === "blank") return { error: "Enter " + label.toLowerCase() + ". Use 0 if it does not apply." };
    if (p.error === "format") return { error: label + ": use " + what + ", for example 50,000." };
    if (p.value < low || p.value > high) {
      return { error: label + ": enter a value from " + (low < 0 ? "-" + group(-low) : group(low)) + " to " + group(high) + "." };
    }
    return { value: p.value };
  }

  function sortedEvents(events) {
    return events
      .map(function (e, i) { return { index: i, event: e }; })
      .sort(function (a, b) { return a.event.age - b.event.age || a.index - b.index; });
  }

  function stateAt(fields, events, age) {
    var housing = fields.housing_debt;
    var support = fields.support_years * fields.support_annual;
    sortedEvents(events).forEach(function (item) {
      if (item.event.age > age) return;
      var info = EVENT_TYPES[item.event.type];
      if (info.target === "housing_debt") housing += info.direction * item.event.amount;
      else support += info.direction * item.event.amount;
    });
    return { support: support, housing: housing };
  }

  function planActive(plan, age) {
    return plan.ends_at_age === null || plan.ends_at_age === undefined || age <= plan.ends_at_age;
  }

  // payload: {fields: {name: string}, plans: [id], events: [{type, age, amount}]}
  // catalogue: [{id, name, death_cover, ends_at_age, addable}]
  // Returns {errors: {key: message}, clean, result}. result is null when there are errors.
  function evaluate(payload, catalogue) {
    var errors = {};
    var byId = {};
    catalogue.forEach(function (p) { byId[p.id] = p; });
    var rawFields = (payload && payload.fields) || {};
    var fields = {};

    Object.keys(FIELDS).forEach(function (key) {
      var def = FIELDS[key], low = 0, high = MAX_AMOUNT, what = "whole dollars";
      if (def.kind === "age") { low = AGE_MIN; high = AGE_MAX; what = "a whole number of years"; }
      if (def.kind === "years") { high = MAX_SUPPORT_YEARS; what = "a whole number of years"; }
      var r = readNumber(rawFields[key], def.label, low, high, what);
      if (r.error) errors[key] = r.error; else fields[key] = r.value;
    });

    var plans = [];
    var rawPlans = (payload && payload.plans) || [];
    if (rawPlans.length > MAX_PLANS) {
      errors.plans = "Choose up to " + MAX_PLANS + " plans.";
    } else {
      for (var i = 0; i < rawPlans.length; i++) {
        var id = rawPlans[i];
        if (typeof id !== "number" || !byId[id]) { errors.plans = "One of the selected plans is not available."; break; }
        if (!byId[id].addable) { errors.plans = byId[id].name + " has no death benefit, so it cannot be added."; break; }
        if (plans.indexOf(id) !== -1) { errors.plans = "Each plan can be added once."; break; }
        plans.push(id);
      }
    }

    var events = [];
    var rawEvents = (payload && payload.events) || [];
    if (rawEvents.length > MAX_EVENTS) {
      errors.events = "Use at most " + MAX_EVENTS + " life events.";
      rawEvents = [];
    }
    rawEvents.forEach(function (item, index) {
      var prefix = "events." + index;
      item = item || {};
      var info = EVENT_TYPES[item.type];
      if (!info) errors[prefix + ".type"] = "Choose an event type.";
      else if (!info.supported) errors[prefix + ".type"] = info.label + " is not modelled yet. Choose another event.";
      var lowAge = fields.age !== undefined ? fields.age : AGE_MIN;
      var age = readNumber(item.age, "Event age", lowAge, EVENT_AGE_MAX, "a whole number of years");
      if (age.error) errors[prefix + ".age"] = age.error;
      var signed = !!(info && info.signed);
      var amount = readNumber(item.amount, "Event amount", signed ? -MAX_AMOUNT : 0, MAX_AMOUNT, "whole dollars");
      if (amount.error) errors[prefix + ".amount"] = amount.error;
      events.push({ type: item.type, age: age.value, amount: amount.value });
    });

    if (Object.keys(errors).length === 0) checkTimeline(fields, events, errors);
    if (Object.keys(errors).length > 0) return { errors: errors, clean: null, result: null };

    var clean = { fields: fields, plans: plans, events: events };
    return { errors: {}, clean: clean, result: calculate(clean, byId) };
  }

  function checkTimeline(fields, events, errors) {
    var housing = fields.housing_debt;
    var support = fields.support_years * fields.support_annual;
    var ordered = sortedEvents(events);
    for (var i = 0; i < ordered.length; i++) {
      var info = EVENT_TYPES[ordered[i].event.type];
      var change = info.direction * ordered[i].event.amount;
      if (info.target === "housing_debt") {
        housing += change;
        if (housing < 0) { errors["events." + ordered[i].index + ".amount"] = "This pay-down is larger than the housing debt at that age."; return; }
      } else {
        support += change;
        if (support < 0) { errors["events." + ordered[i].index + ".amount"] = "This would take support below zero."; return; }
      }
    }
  }

  function calculate(clean, byId) {
    var f = clean.fields, events = clean.events;
    var chosen = clean.plans.map(function (id) { return byId[id]; });

    function needAt(age) {
      var s = stateAt(f, events, age);
      return s.support + s.housing + f.other_debts - f.savings;
    }
    function coverAt(age) {
      return chosen.reduce(function (sum, p) { return sum + (planActive(p, age) ? p.death_cover : 0); }, f.existing_cover);
    }

    var needToday = needAt(f.age);
    var gapToday = needToday - f.existing_cover;
    var evaluationAge = events.reduce(function (m, e) { return Math.max(m, e.age); }, f.age);
    var needAfter = needAt(evaluationAge);
    var gapAfter = needAfter - f.existing_cover;
    var planCover = chosen.reduce(function (sum, p) { return sum + (planActive(p, evaluationAge) ? p.death_cover : 0); }, 0);
    var gapWith = chosen.length ? gapAfter - planCover : null;
    var finalGap = chosen.length ? gapWith : gapAfter;

    var ageSet = {};
    ageSet[f.age] = true;
    events.forEach(function (e) { ageSet[e.age] = true; });
    chosen.forEach(function (p) {
      if (p.ends_at_age !== null && p.ends_at_age !== undefined && p.ends_at_age >= f.age) {
        ageSet[p.ends_at_age] = true;
        if (p.ends_at_age + 1 <= EVENT_AGE_MAX) ageSet[p.ends_at_age + 1] = true;
      }
    });
    var points = Object.keys(ageSet).map(Number).sort(function (a, b) { return a - b; }).map(function (a) {
      return { age: a, need: needAt(a), cover: coverAt(a) };
    });

    var contributions = [];
    sortedEvents(events).forEach(function (item) {
      var e = item.event, info = EVENT_TYPES[e.type], effect = info.direction * e.amount;
      contributions.push({ kind: "event", type: e.type, label: info.label, age: e.age, input_label: info.inputLabel, input_effect: effect, gap_effect: effect });
    });
    chosen.forEach(function (p) {
      var active = planActive(p, evaluationAge);
      contributions.push({
        kind: "plan", plan_id: p.id, label: p.name, active: active, ends_at_age: p.ends_at_age === undefined ? null : p.ends_at_age,
        input_label: "Death cover", input_effect: active ? p.death_cover : 0, gap_effect: active ? -p.death_cover : 0
      });
    });

    var notes = [];
    chosen.forEach(function (p) {
      var end = p.ends_at_age;
      if (end === null || end === undefined) return;
      if (end < f.age) notes.push({ plan_id: p.id, name: p.name, status: "ended", age: end, before: null, after: null });
      else notes.push({ plan_id: p.id, name: p.name, status: "ends", age: end + 1, before: coverAt(end), after: coverAt(end + 1) });
    });

    return {
      support_today: f.support_years * f.support_annual,
      debts_today: f.housing_debt + f.other_debts,
      need_today: needToday,
      gap_today: gapToday,
      evaluation_age: evaluationAge,
      need_after_events: needAfter,
      gap_after_events: gapAfter,
      plan_cover_at_evaluation: planCover,
      gap_with_plans: gapWith,
      final_gap: finalGap,
      total_change: finalGap - gapToday,
      points: points,
      contributions: contributions,
      expiry_notes: notes
    };
  }

  root.SimulatorCore = {
    parseAmount: parseAmount,
    evaluate: evaluate,
    EVENT_TYPES: EVENT_TYPES,
    LIMITS: { maxEvents: MAX_EVENTS, maxPlans: MAX_PLANS }
  };
})(window);
