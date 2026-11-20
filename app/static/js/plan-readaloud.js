// Read aloud for the plan summary. Uses only the browser's built-in speech synthesis:
// no server call, no key and no network request of any kind.
(function () {
  "use strict";

  var button = document.getElementById("read-aloud");
  var status = document.getElementById("read-aloud-status");
  var text = document.getElementById("summary-text");
  if (!button || !text) return;

  var synth = window.speechSynthesis;
  var wanted = (button.getAttribute("data-lang") || "en").toLowerCase();
  var speaking = false;

  function voiceFor() {
    var voices = synth ? synth.getVoices() : [];
    for (var i = 0; i < voices.length; i++) {
      var lang = (voices[i].lang || "").toLowerCase().replace("_", "-");
      if (lang === wanted || lang.indexOf(wanted + "-") === 0) return voices[i];
    }
    return null;
  }

  function refresh() {
    if (!synth || typeof window.SpeechSynthesisUtterance !== "function") {
      button.disabled = true;
      status.textContent = "Read aloud is not supported in this browser.";
      return;
    }
    var voice = voiceFor();
    button.disabled = !voice && !speaking;
    status.textContent = voice || speaking ? "" : "No voice for this language is installed on your device.";
  }

  function stop() {
    synth.cancel();
    speaking = false;
    button.textContent = "Read aloud";
    refresh();
  }

  button.addEventListener("click", function () {
    if (speaking) { stop(); return; }
    var voice = voiceFor();
    if (!voice) { refresh(); return; }
    // Read the sentences only, not the [1] citation markers.
    var parts = Array.prototype.map.call(text.querySelectorAll(".stmt-text"), function (node) { return node.textContent; });
    var utterance = new window.SpeechSynthesisUtterance(parts.join(" "));
    utterance.voice = voice;
    utterance.lang = voice.lang;
    utterance.onend = utterance.onerror = function () { speaking = false; button.textContent = "Read aloud"; refresh(); };
    speaking = true;
    button.textContent = "Stop";
    synth.speak(utterance);
  });

  if (synth) {
    // Voices load after the page on some browsers.
    if (typeof synth.addEventListener === "function") synth.addEventListener("voiceschanged", refresh);
    window.addEventListener("pagehide", function () { synth.cancel(); });
  }
  refresh();
})();
