// "Explain this change": asks the server for a plain-language explanation of the calculated
// changes. It runs only when the button is pressed (never while typing), needs valid
// inputs, and never alters the calculator or the rule-based "What changed, and why" panel.
(function () {
  "use strict";

  var button = document.getElementById("explain-btn");
  var root = document.getElementById("sim-root");
  if (!button || !root || !window.SimulatorUI) return;

  var statusLine = document.getElementById("explain-status");
  var resultBox = document.getElementById("explain-result");
  var hint = document.getElementById("explain-hint");
  var busy = false;
  var asked = null;     // JSON of the payload the shown explanation was written for
  var ticket = 0;       // lets a late reply be ignored if the inputs changed meanwhile

  var REASONS = {
    not_configured: "The AI service is not set up on this server, so this is the standard explanation.",
    timeout: "The AI service took too long, so this is the standard explanation.",
    error: "The AI service could not be reached, so this is the standard explanation.",
    empty: "The AI service returned nothing, so this is the standard explanation.",
    invalid_output: "The AI reply did not match the calculated figures, so it was not used. This is the standard explanation.",
    no_changes: ""
  };

  function canExplain() {
    var result = window.SimulatorUI.result();
    return !!(result && result.contributions.length > 0);
  }

  function refresh() {
    button.disabled = busy || !canExplain();
    hint.hidden = canExplain();
  }

  function clearResult() {
    resultBox.hidden = true;
    asked = null;
    statusLine.textContent = "";
  }

  function render(data) {
    document.getElementById("explain-label").textContent = data.label;
    document.getElementById("explain-label").className = "chip " + (data.source === "ai" ? "chip-info" : "chip-muted");
    document.getElementById("explain-summary").textContent = data.summary;
    var list = document.getElementById("explain-bullets");
    list.textContent = "";
    (data.bullets || []).forEach(function (text) {
      var li = document.createElement("li");
      li.textContent = text;
      list.appendChild(li);
    });
    document.getElementById("explain-reason").textContent = data.source === "standard" ? (REASONS[data.reason] || "") : "";
    resultBox.hidden = false;
  }

  button.addEventListener("click", function () {
    if (busy || !canExplain()) return;
    var payload = window.SimulatorUI.payload();
    var snapshot = JSON.stringify(payload);
    var mine = ++ticket;
    busy = true;
    button.textContent = "Explaining\u2026";
    resultBox.hidden = true;
    statusLine.textContent = "Writing an explanation of the calculated changes\u2026";
    refresh();

    fetch(root.dataset.explainUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": root.dataset.csrf },
      credentials: "same-origin",
      body: snapshot
    }).then(function (response) {
      return response.json().then(function (body) { return { status: response.status, body: body }; },
        function () { return { status: response.status, body: null }; });
    }).then(function (res) {
      if (mine !== ticket) return;  // a newer request or edit superseded this one
      if (JSON.stringify(window.SimulatorUI.payload()) !== snapshot) {
        statusLine.textContent = "Your figures changed while the explanation was loading. Press the button again.";
      } else if (res.body && res.body.ok) {
        render(res.body);
        asked = snapshot;
        statusLine.textContent = "";
      } else if (res.status === 429) {
        var wait = res.body && res.body.retry_after ? res.body.retry_after : 30;
        statusLine.textContent = "That is a lot of explanations in a row. Try again in about " + wait + " seconds.";
      } else if (res.status === 401 || res.status === 403) {
        statusLine.textContent = "Please log in again to use this.";
      } else if (res.status === 400 && res.body && res.body.error === "invalid_input") {
        statusLine.textContent = "Fix the marked fields first.";
      } else {
        statusLine.textContent = "Could not get an explanation. Reload the page and try again.";
      }
    }).catch(function () {
      if (mine !== ticket) return;
      statusLine.textContent = "Could not reach the server. Check your connection and try again.";
    }).then(function () {
      if (mine !== ticket) return;
      busy = false;
      button.textContent = "Explain this change";
      refresh();
    });
  });

  // When any figure changes, the explanation no longer matches, so remove it. Nothing is sent.
  document.addEventListener("simulator:updated", function () {
    if (asked !== null && JSON.stringify(window.SimulatorUI.payload()) !== asked) clearResult();
    refresh();
  });

  refresh();
})();
