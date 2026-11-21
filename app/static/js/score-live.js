// Live death cover score on the simulator page. Same rule as app/score.py:
// score = existing death cover / estimated need x 100, bounded 0 to 100, and no percentage when need <= 0.
(function () {
  "use strict";
  var box = document.getElementById("score-live");
  var dataNode = document.getElementById("sim-data");
  if (!box || !dataNode || !window.SimulatorCore) return;
  var catalogue = JSON.parse(dataNode.textContent).catalogue;

  function score(cover, need) {
    if (!(need > 0)) return null;
    return Math.max(0, Math.min(100, Math.round(cover / need * 100)));
  }
  function money(n) { return (n < 0 ? "\u2212" : "") + "S$" + String(Math.abs(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ","); }
  function set(id, text) { document.getElementById(id).textContent = text; }

  function render() {
    var ui = window.SimulatorUI;
    if (!ui) return;
    var ev = window.SimulatorCore.evaluate(ui.payload(), catalogue);
    if (!ev.result) {
      box.dataset.state = "setup";
      set("score-today", "\u2014");
      set("score-today-note", "Fill in every field to see your estimate.");
      set("score-whatif", "\u2014");
      set("score-whatif-note", "");
      return;
    }
    var f = ev.clean.fields, r = ev.result;
    var today = score(f.existing_cover, r.need_today);
    var after = score(f.existing_cover + r.plan_cover_at_evaluation, r.need_after_events);
    box.dataset.state = today === null ? "no_need" : "score";
    set("score-today", today === null ? "\u2014" : today + " / 100");
    set("score-today-note", today === null
      ? "Your estimated need is zero, so no percentage is shown."
      : "Existing death cover " + money(f.existing_cover) + " of an estimated need of " + money(r.need_today) +
        ". Gap " + (r.gap_today > 0 ? money(r.gap_today) : "none") + ".");
    var hypothetical = state_has_changes(ev);
    set("score-whatif", after === null ? "\u2014" : after + " / 100");
    set("score-whatif-note", !hypothetical
      ? "Add a life event or a plan to see a what-if score."
      : (after === null
          ? "After your events the estimated need is zero, so no percentage is shown."
          : "After life events and added plans (age " + r.evaluation_age + "). Gap " +
            (r.final_gap > 0 ? money(r.final_gap) : "none") + "."));
  }
  function state_has_changes(ev) {
    return ev.clean.events.length > 0 || ev.clean.plans.length > 0;
  }

  document.addEventListener("simulator:updated", render);
  window.addEventListener("load", render);
})();
