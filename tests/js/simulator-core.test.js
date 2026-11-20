// Runs static/js/simulator-core.js (in a bare VM, no browser) against the same
// hand-worked cases as the Python calculator, so the two cannot drift apart.
//   node --test tests/js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..", "..");
// SIMULATOR_CORE_PATH lets the drift check point at a modified copy; the default is the real file.
const corePath = process.env.SIMULATOR_CORE_PATH || path.join(root, "app", "static", "js", "simulator-core.js");
const source = fs.readFileSync(corePath, "utf8");
const cases = JSON.parse(fs.readFileSync(path.join(root, "tests", "simulator_cases.json"), "utf8"));

// The script attaches itself to `window`. A context with only `window` proves it needs nothing else.
const sandbox = { window: {} };
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: "simulator-core.js" });
const Core = sandbox.window.SimulatorCore;

// Results made inside the VM have a different realm (prototype), so round-trip them through JSON.
function evaluate(payload) {
  return JSON.parse(JSON.stringify(Core.evaluate(payload, cases.catalogue)));
}

function buildPayload(patch) {
  const payload = JSON.parse(JSON.stringify(cases.base));
  Object.assign(payload.fields, patch.fields || {});
  if ("plans" in patch) payload.plans = patch.plans;
  if ("events" in patch) payload.events = patch.events;
  return payload;
}

function comparable(result) {
  return {
    need_today: result.need_today,
    gap_today: result.gap_today,
    evaluation_age: result.evaluation_age,
    gap_after_events: result.gap_after_events,
    gap_with_plans: result.gap_with_plans,
    final_gap: result.final_gap,
    total_change: result.total_change,
    points: result.points.map((p) => [p.age, p.need, p.cover]),
    gap_effects: result.contributions.map((c) => c.gap_effect),
    expiry_notes: result.expiry_notes.map((n) => ({
      plan_id: n.plan_id, status: n.status, age: n.age, before: n.before, after: n.after,
    })),
  };
}

test("the calculator loads with no browser, network or module system", () => {
  assert.equal(typeof Core.evaluate, "function");
  assert.equal(typeof Core.parseAmount, "function");
});

for (const c of cases.cases) {
  test(c.name, () => {
    const out = evaluate(buildPayload(c.patch));
    if (c.expect.errors) {
      assert.equal(out.result, null, "invalid input must not produce a result");
      assert.deepEqual(Object.keys(out.errors).sort(), c.expect.errors);
    } else {
      assert.deepEqual(Object.keys(out.errors), []);
      assert.deepEqual(comparable(out.result), c.expect);
    }
  });
}

test("worked example gives S$130,000, S$580,000 and S$80,000", () => {
  const c = cases.cases.find((x) => x.name.startsWith("worked example"));
  const r = evaluate(buildPayload(c.patch)).result;
  assert.deepEqual([r.gap_today, r.gap_after_events, r.gap_with_plans], [130000, 580000, 80000]);
});

test("contributions add up to the total change in every valid case", () => {
  for (const c of cases.cases.filter((x) => !x.expect.errors)) {
    const r = evaluate(buildPayload(c.patch)).result;
    const sum = r.contributions.reduce((s, x) => s + x.gap_effect, 0);
    assert.equal(sum, r.total_change, c.name);
  }
});

const parseCases = [
  ["1,000", 1000, null], ["1000", 1000, null], ["S$ 2,500", 2500, null], ["$7", 7, null],
  ["-5", -5, null], ["0", 0, null], ["", null, "blank"], ["   ", null, "blank"],
  [null, null, "blank"], ["1.5", null, "format"], ["1,00", null, "format"],
  ["abc", null, "format"], ["+5", null, "format"], ["\u0661\u0662\u0663", null, "format"], ["1e3", null, "format"],
];
for (const [text, value, error] of parseCases) {
  test(`parseAmount(${JSON.stringify(text)})`, () => {
    // JSON round trip: objects made inside the VM have a different prototype.
    assert.deepEqual(JSON.parse(JSON.stringify(Core.parseAmount(text))), { value, error });
  });
}
