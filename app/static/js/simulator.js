// Coverage Needs Simulator UI. Every edit recalculates in the browser (SimulatorCore);
// nothing is sent to the server until the user presses Save, and no AI service is used.
(function () {
  "use strict";

  var root = document.getElementById("sim-root");
  if (!root || !window.SimulatorCore) return;
  var Core = window.SimulatorCore;
  var data = JSON.parse(document.getElementById("sim-data").textContent);
  var catalogue = data.catalogue;
  var FIELD_KEYS = ["age", "support_years", "support_annual", "housing_debt", "other_debts", "savings", "existing_cover"];

  var state = { fields: {}, plans: [], events: [], nextId: 1 };
  var touched = {};
  var forceErrors = false;
  var serverErrors = null;
  var savedSnapshot = null;
  var lastResult = null;
  var savedLabel = "";

  function $(id) { return document.getElementById(id); }
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  var SVG_NS = "http://www.w3.org/2000/svg";
  function svg(tag, attrs, text) {
    var node = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // ---------- formatting ----------
  function group(n) { return String(Math.abs(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ","); }
  function money(n) { return (n < 0 ? "\u2212" : "") + "S$" + group(n); }
  function delta(n) { return n === 0 ? "S$0" : (n > 0 ? "+" : "\u2212") + "S$" + group(n); }
  function short(n) {
    if (n >= 1000000) return "S$" + (Math.round(n / 100000) / 10) + "m";
    if (n >= 1000) return "S$" + Math.round(n / 1000) + "k";
    return "S$" + n;
  }
  // Show whole-dollar values with thousands separators (used when restoring a saved scenario).
  function pretty(raw) {
    var parsed = Core.parseAmount(raw);
    return parsed.error ? String(raw) : (parsed.value < 0 ? "-" : "") + group(parsed.value);
  }
  var MONEY_KEYS = ["support_annual", "housing_debt", "other_debts", "savings", "existing_cover"];
  function planById(id) {
    for (var i = 0; i < catalogue.length; i++) if (catalogue[i].id === id) return catalogue[i];
    return null;
  }

  // ---------- state <-> payload ----------
  function payload() {
    return {
      fields: JSON.parse(JSON.stringify(state.fields)),
      plans: state.plans.slice(),
      events: state.events.map(function (e) { return { type: e.type, age: e.age, amount: e.amount }; })
    };
  }
  function setState(p) {
    state.fields = {};
    FIELD_KEYS.forEach(function (k) {
      var raw = (p && p.fields && p.fields[k] !== undefined) ? String(p.fields[k]) : "";
      state.fields[k] = MONEY_KEYS.indexOf(k) !== -1 ? pretty(raw) : raw;
    });
    state.plans = ((p && p.plans) || []).slice();
    state.events = ((p && p.events) || []).map(function (e) {
      return { id: state.nextId++, type: e.type || "", age: String(e.age === undefined ? "" : e.age), amount: pretty(e.amount === undefined ? "" : e.amount) };
    });
    touched = {};
    forceErrors = false;
    serverErrors = null;
  }

  // ---------- inputs ----------
  function bindFields() {
    FIELD_KEYS.forEach(function (key) {
      var input = $("f-" + key);
      input.addEventListener("input", function () { state.fields[key] = input.value; serverErrors = null; update(); });
      input.addEventListener("blur", function () { touched[key] = true; update(); });
    });
  }
  function writeFields() {
    FIELD_KEYS.forEach(function (key) { $("f-" + key).value = state.fields[key]; });
  }

  // ---------- plans ----------
  function renderPlans() {
    var list = $("plan-list");
    while (list.children.length > 1) list.removeChild(list.lastChild);
    state.plans.forEach(function (id) {
      var plan = planById(id);
      if (!plan) return;
      var li = el("li", "plan-row is-added");
      var text = el("div");
      text.appendChild(el("strong", null, plan.name));
      var ends = plan.ends_at_age === null ? "whole life" : "covers up to age " + plan.ends_at_age;
      text.appendChild(el("span", "muted small", "+" + short(plan.death_cover) + " cover \u00b7 S$" + plan.monthly_premium + "/mo \u00b7 " + ends));
      li.appendChild(text);
      var side = el("div", "plan-side");
      side.appendChild(el("span", "tag tag-added", "Added"));
      var remove = el("button", "btn btn-small btn-secondary", "Remove");
      remove.type = "button";
      remove.setAttribute("aria-label", "Remove " + plan.name);
      remove.addEventListener("click", function () {
        state.plans = state.plans.filter(function (p) { return p !== id; });
        serverErrors = null;
        renderPlans();
        update();
      });
      side.appendChild(remove);
      li.appendChild(side);
      list.appendChild(li);
    });

    var select = $("plan-select");
    select.textContent = "";
    var placeholder = el("option", null, "Choose a plan to add");
    placeholder.value = "";
    select.appendChild(placeholder);
    catalogue.forEach(function (plan) {
      var option = el("option");
      option.value = String(plan.id);
      var taken = state.plans.indexOf(plan.id) !== -1;
      option.textContent = plan.addable
        ? plan.name + " (+" + short(plan.death_cover) + (taken ? ", added" : "") + ")"
        : plan.name + " (no death benefit)";
      option.disabled = !plan.addable || taken;
      select.appendChild(option);
    });
    $("plan-add-btn").disabled = state.plans.length >= Core.LIMITS.maxPlans;
  }

  // ---------- events ----------
  function renderEvents() {
    var list = $("event-list");
    list.textContent = "";
    state.events.forEach(function (ev, index) {
      var li = el("li", "event-row");
      li.dataset.id = String(ev.id);
      var n = index + 1;

      var typeWrap = el("div", "ev-type");
      var typeLabel = el("label", "sr-only", "Event " + n + " type");
      typeLabel.htmlFor = "ev-type-" + ev.id;
      var select = el("select");
      select.id = "ev-type-" + ev.id;
      var blank = el("option", null, "Choose an event");
      blank.value = "";
      select.appendChild(blank);
      data.event_types.forEach(function (t) {
        var o = el("option", null, t.supported ? t.label : t.label + " (not modelled yet)");
        o.value = t.key;
        o.disabled = !t.supported;
        select.appendChild(o);
      });
      select.value = ev.type;
      select.addEventListener("change", function () { ev.type = select.value; touched[ev.id + ".type"] = true; serverErrors = null; update(); });
      typeWrap.appendChild(typeLabel);
      typeWrap.appendChild(select);

      var ageWrap = el("div", "ev-age");
      var ageLabel = el("label", null, "Age");
      ageLabel.htmlFor = "ev-age-" + ev.id;
      var age = el("input", "plain-input");
      age.id = "ev-age-" + ev.id; age.type = "text"; age.inputMode = "numeric"; age.autocomplete = "off"; age.value = ev.age;
      age.addEventListener("input", function () { ev.age = age.value; serverErrors = null; update(); });
      age.addEventListener("blur", function () { touched[ev.id + ".age"] = true; update(); });
      ageWrap.appendChild(ageLabel);
      ageWrap.appendChild(age);

      var amtWrap = el("div", "ev-amount");
      var amtLabel = el("label", null, "Amount");
      amtLabel.htmlFor = "ev-amount-" + ev.id;
      var box = el("div", "money-input");
      box.appendChild(el("span", null, "S$"));
      var amount = el("input");
      amount.id = "ev-amount-" + ev.id; amount.type = "text"; amount.inputMode = "numeric"; amount.autocomplete = "off"; amount.value = ev.amount;
      amount.addEventListener("input", function () { ev.amount = amount.value; serverErrors = null; update(); });
      amount.addEventListener("blur", function () { touched[ev.id + ".amount"] = true; update(); });
      box.appendChild(amount);
      amtWrap.appendChild(amtLabel);
      amtWrap.appendChild(box);

      var remove = el("button", "btn btn-small btn-secondary", "Remove");
      remove.type = "button";
      remove.setAttribute("aria-label", "Remove event " + n);
      remove.addEventListener("click", function () {
        state.events = state.events.filter(function (e) { return e.id !== ev.id; });
        serverErrors = null;
        renderEvents();
        update();
      });

      var effect = el("p", "ev-effect muted small");
      effect.id = "ev-effect-" + ev.id;
      var errors = el("div", "ev-errors");
      ["type", "age", "amount"].forEach(function (part) {
        var p = el("p", "field-error");
        p.id = "ev-err-" + ev.id + "-" + part;
        p.hidden = true;
        errors.appendChild(p);
      });

      li.appendChild(typeWrap); li.appendChild(ageWrap); li.appendChild(amtWrap); li.appendChild(remove);
      li.appendChild(effect); li.appendChild(errors);
      list.appendChild(li);
    });
    $("event-add-btn").disabled = state.events.length >= Core.LIMITS.maxEvents;
  }

  // ---------- errors ----------
  function showError(node, input, message) {
    node.hidden = !message;
    node.textContent = message || "";
    if (input) {
      if (message) input.setAttribute("aria-invalid", "true"); else input.removeAttribute("aria-invalid");
    }
  }
  function renderErrors(errors) {
    FIELD_KEYS.forEach(function (key) {
      var show = (forceErrors || touched[key]) && errors[key];
      showError($("err-" + key), $("f-" + key), show ? errors[key] : "");
    });
    showError($("err-plans"), null, errors.plans || "");
    showError($("err-events"), null, errors.events || "");
    state.events.forEach(function (ev, index) {
      ["type", "age", "amount"].forEach(function (part) {
        var message = errors["events." + index + "." + part];
        var show = (forceErrors || touched[ev.id + "." + part]) && message;
        var input = part === "type" ? $("ev-type-" + ev.id) : $("ev-" + part + "-" + ev.id);
        showError($("ev-err-" + ev.id + "-" + part), input, show ? message : "");
      });
    });
  }

  // ---------- results ----------
  function setStat(id, value, sub, tone) {
    $(id).textContent = value;
    $(id + "-sub").textContent = sub || "";
    $(id).dataset.tone = tone || "";
  }
  function gapText(n) { return money(n); }
  function gapSub(n) { return n < 0 ? "Cover exceeds need" : ""; }

  function renderResults(result, errors, clean) {
    var hasErrors = Object.keys(errors).length > 0;
    var status = $("result-status");
    if (result) {
      status.textContent = "";
      status.hidden = true;
    } else {
      status.hidden = false;
      status.textContent = hasErrors
        ? "Fill in every field and fix any marked problems to see your estimate."
        : "";
    }

    if (!result) {
      setStat("stat-today", "\u2014", "");
      setStat("stat-after", "\u2014", "");
      setStat("stat-with", "\u2014", "");
      $("stat-with-label").textContent = "With added plans";
      $("calc-line").textContent = "";
      $("support-total").textContent = "Total support: enter both fields.";
      $("held-cover").textContent = "Enter your existing death cover";
      renderChart(null);
      renderChanged(null);
      return;
    }

    var f = clean.fields;
    setStat("stat-today", gapText(result.gap_today), gapSub(result.gap_today) || "Existing cover only");
    var anyEvents = state.events.length > 0;
    setStat("stat-after", gapText(result.gap_after_events),
      anyEvents ? "At age " + result.evaluation_age + (gapSub(result.gap_after_events) ? ". " + gapSub(result.gap_after_events) : "") : "No life events added");
    if (result.gap_with_plans === null) {
      setStat("stat-with", "\u2014", "No plan added");
      $("stat-with-label").textContent = "With added plans";
    } else {
      var names = state.plans.map(function (id) { return planById(id).name; }).join(" + ");
      $("stat-with-label").textContent = "With " + names;
      setStat("stat-with", gapText(result.gap_with_plans),
        gapSub(result.gap_with_plans) || (result.plan_cover_at_evaluation === 0 ? "Plan cover has ended by age " + result.evaluation_age : "At age " + result.evaluation_age));
    }

    $("calc-line").textContent =
      "Need = " + money(result.support_today) + " support + " + money(result.debts_today) + " debts \u2212 " +
      money(f.savings) + " savings = " + money(result.need_today) +
      ". Gap = " + money(result.need_today) + " \u2212 " + money(f.existing_cover) +
      " existing cover = " + money(result.gap_today) + ".";
    $("support-total").textContent = "Total support: " + money(result.support_today);
    $("held-cover").textContent = money(f.existing_cover) + " death cover";
    renderChart(result);
    renderChanged(result);
  }

  function niceMax(value) {
    var base = Math.pow(10, Math.floor(Math.log(value) / Math.LN10));
    var steps = [1, 2, 2.5, 5, 10];
    for (var i = 0; i < steps.length; i++) if (steps[i] * base >= value) return steps[i] * base;
    return 10 * base;
  }

  function renderChart(result) {
    var chart = $("chart");
    chart.textContent = "";
    var tbody = $("chart-table").tBodies[0];
    tbody.textContent = "";
    var notes = $("chart-notes");
    notes.textContent = "";
    $("chart-empty").hidden = !!result;
    chart.hidden = !result;
    if (!result) return;

    var W = 640, H = 280, left = 64, right = 12, top = 12, bottom = 34;
    var plotW = W - left - right, plotH = H - top - bottom;
    var peak = Math.max.apply(null, result.points.map(function (p) { return Math.max(p.need, p.cover, 0); }));
    var max = peak > 0 ? niceMax(peak) : 1;

    [0, 0.5, 1].forEach(function (frac) {
      var y = top + plotH - frac * plotH;
      chart.appendChild(svg("line", { x1: left, x2: W - right, y1: y, y2: y, class: "grid-line" }));
      chart.appendChild(svg("text", { x: left - 8, y: y + 4, "text-anchor": "end", class: "axis-label" }, peak > 0 ? short(max * frac) : "S$0"));
    });

    var n = result.points.length;
    var groupW = plotW / n;
    var barW = Math.min(30, groupW / 2.6);
    result.points.forEach(function (p, i) {
      var cx = left + groupW * i + groupW / 2;
      var needH = Math.max(0, p.need) / max * plotH;
      var coverH = Math.max(0, p.cover) / max * plotH;
      var need = svg("rect", { x: cx - barW - 1, y: top + plotH - needH, width: barW, height: needH, rx: 3, class: "bar-need" });
      need.appendChild(svg("title", {}, "Age " + p.age + " need " + money(p.need)));
      var cover = svg("rect", { x: cx + 1, y: top + plotH - coverH, width: barW, height: coverH, rx: 3, class: "bar-cover" });
      cover.appendChild(svg("title", {}, "Age " + p.age + " cover " + money(p.cover)));
      chart.appendChild(need);
      chart.appendChild(cover);
      chart.appendChild(svg("text", { x: cx, y: H - 12, "text-anchor": "middle", class: "axis-label" }, "Age " + p.age));

      var row = tbody.insertRow();
      row.insertCell().textContent = String(p.age);
      row.insertCell().textContent = money(p.need);
      row.insertCell().textContent = money(p.cover);
    });

    result.expiry_notes.forEach(function (note) {
      var text = note.status === "ended"
        ? note.name + " ended at age " + note.age + ", so it adds no cover."
        : note.name + " covers you up to age " + (note.age - 1) + ". Cover drops from " + money(note.before) + " to " + money(note.after) + " at age " + note.age + ".";
      notes.appendChild(el("li", "expiry-note", text));
    });
  }

  function renderChanged(result) {
    var list = $("changed-list");
    list.textContent = "";
    var chip = $("changed-chip");
    var empty = $("changed-empty");
    if (!result) {
      chip.hidden = true;
      empty.hidden = false;
      empty.textContent = "Complete the figures to see what moves your gap.";
      return;
    }
    if (result.contributions.length === 0) {
      chip.hidden = true;
      empty.hidden = false;
      empty.textContent = "Nothing has changed yet. Add a life event or a plan to see what moves your gap.";
      return;
    }
    empty.hidden = true;
    chip.hidden = false;
    chip.textContent = "Gap " + money(result.gap_today) + " \u2192 " + money(result.final_gap) + " (" + delta(result.total_change) + ")";

    result.contributions.forEach(function (c) {
      var li = el("li", "changed-row");
      var name = c.kind === "event" ? c.label + " (age " + c.age + ")" : "Add " + c.label;
      li.appendChild(el("span", "changed-name", name));
      var detail = c.kind === "plan" && !c.active
        ? "Death cover " + delta(0) + " (covers up to age " + c.ends_at_age + ", before age " + result.evaluation_age + ")"
        : c.input_label + " " + delta(c.input_effect);
      li.appendChild(el("span", "changed-detail", detail));
      var tone = c.gap_effect > 0 ? "up" : (c.gap_effect < 0 ? "down" : "flat");
      li.appendChild(el("span", "changed-gap is-" + tone, "Gap " + delta(c.gap_effect)));
      list.appendChild(li);
    });
  }

  function renderEventEffects(clean) {
    state.events.forEach(function (ev, index) {
      var node = $("ev-effect-" + ev.id);
      if (!node) return;
      var item = clean && clean.events[index];
      if (!item) { node.textContent = ""; return; }
      var info = Core.EVENT_TYPES[item.type];
      node.textContent = info.inputLabel + " " + delta(info.direction * item.amount) + " from age " + item.age + ".";
    });
  }

  // ---------- main update ----------
  function update() {
    var p = payload();
    var evaluation = Core.evaluate(p, catalogue);
    var errors = serverErrors || evaluation.errors;
    lastResult = evaluation.result;
    renderErrors(errors);
    renderResults(evaluation.result, errors, evaluation.clean);
    renderEventEffects(evaluation.clean);
    if (savedSnapshot !== null) {
      $("save-status").textContent = JSON.stringify(p) === savedSnapshot ? savedLabel : "Unsaved changes.";
    }
  }

  // ---------- save ----------
  function formatTime(iso) {
    var d = new Date(iso);
    return isNaN(d.getTime()) ? "" : d.toLocaleString();
  }
  function save() {
    forceErrors = true;
    var p = payload();
    var button = $("save-btn");
    button.disabled = true;
    $("save-status").textContent = "Saving\u2026";
    fetch(root.dataset.saveUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": root.dataset.csrf },
      credentials: "same-origin",
      body: JSON.stringify(p)
    }).then(function (response) {
      return response.json().then(function (body) { return { status: response.status, body: body }; },
        function () { return { status: response.status, body: null }; });
    }).then(function (res) {
      button.disabled = false;
      if (res.body && res.body.ok) {
        savedSnapshot = JSON.stringify(p);
        serverErrors = null;
        var mismatch = lastResult && (lastResult.gap_today !== res.body.result.gap_today ||
          lastResult.gap_after_events !== res.body.result.gap_after_events ||
          lastResult.gap_with_plans !== res.body.result.gap_with_plans);
        savedLabel = "Saved " + formatTime(res.body.saved_at) + ".";
        showNotice(mismatch ? "The saved calculation differs from this screen. Reload to see the saved figures." : "");
        update();
        $("save-status").textContent = savedLabel;
      } else if (res.body && res.body.errors) {
        serverErrors = res.body.errors;
        update();
        $("save-status").textContent = "Not saved. Fix the marked fields and try again.";
      } else {
        $("save-status").textContent = "Not saved. Reload the page and try again.";
      }
    }).catch(function () {
      button.disabled = false;
      $("save-status").textContent = "Not saved. Check your connection and try again.";
    });
  }
  function showNotice(message) {
    var node = $("notice");
    node.hidden = !message;
    node.textContent = message || "";
  }

  // ---------- controls ----------
  function useExample() {
    var term = catalogue.filter(function (c) { return c.name === "PRUActive Term" && c.addable; })[0];
    setState({
      fields: { age: "30", support_years: "5", support_annual: "50,000", housing_debt: "80,000", other_debts: "0", savings: "50,000", existing_cover: "150,000" },
      plans: term ? [term.id] : [],
      events: [
        { type: "buy_home", age: "31", amount: "300,000" },
        { type: "new_child", age: "32", amount: "150,000" }
      ]
    });
    writeFields(); renderPlans(); renderEvents(); update();
  }
  function clearAll() {
    setState(null);
    writeFields(); renderPlans(); renderEvents(); update();
  }

  $("save-btn").addEventListener("click", save);
  $("example-btn").addEventListener("click", useExample);
  $("clear-btn").addEventListener("click", clearAll);
  $("plan-add-btn").addEventListener("click", function () {
    var id = parseInt($("plan-select").value, 10);
    if (!id || state.plans.indexOf(id) !== -1) return;
    state.plans.push(id);
    serverErrors = null;
    renderPlans();
    update();
  });
  $("event-add-btn").addEventListener("click", function () {
    state.events.push({ id: state.nextId++, type: "", age: "", amount: "" });
    serverErrors = null;
    renderEvents();
    update();
    var last = state.events[state.events.length - 1];
    $("ev-type-" + last.id).focus();
  });

  // ---------- start ----------
  bindFields();
  setState(data.saved);
  writeFields();
  renderPlans();
  renderEvents();
  if (data.saved) {
    savedSnapshot = JSON.stringify(payload());
    savedLabel = "Saved " + formatTime(data.saved_at) + ".";
    $("save-status").textContent = savedLabel;
  }
  showNotice(data.notice);
  update();
})();
