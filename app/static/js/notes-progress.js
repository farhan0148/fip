// Shows progress while the AI draft is being prepared. Reads status only; it never starts the job.
(function () {
  "use strict";
  var box = document.getElementById("progress");
  if (!box) return;
  var text = document.getElementById("progress-text");
  function tick() {
    fetch(box.dataset.statusUrl, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (s) {
      if (s.status === "processing") { text.textContent = s.progress || "Working"; setTimeout(tick, 1500); }
      else { window.location.reload(); }
    }).catch(function () { setTimeout(tick, 3000); });
  }
  setTimeout(tick, 1000);
})();
