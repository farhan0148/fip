// Ask AI voice: record a question (then check the transcript), and read the written answer aloud.
// Typed questions and written answers work without any of this. Replaying uses the same answer text: no request is made.
(function () {
  "use strict";
  function $(id) { return document.getElementById(id); }

  // ---------- speak a question ----------
  var form = $("ask-form"), row = $("voice-row");
  if (form && row && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder) {
    row.hidden = false;
    var recorder = null, chunks = [], stream = null;
    var start = $("mic-start"), stop = $("mic-stop"), status = $("mic-status");
    start.addEventListener("click", function () {
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
        stream = s; chunks = [];
        recorder = new MediaRecorder(s);
        recorder.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
        recorder.onstop = send;
        recorder.start();
        start.hidden = true; stop.hidden = false;
        status.textContent = "Recording\u2026 press Stop when you have finished your question.";
      }).catch(function () { status.textContent = "The microphone is not available. You can type your question instead."; });
    });
    stop.addEventListener("click", function () { if (recorder && recorder.state !== "inactive") recorder.stop(); });
    function send() {
      stop.hidden = true; start.hidden = false;
      if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
      var blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      if (!blob.size) { status.textContent = "Nothing was recorded. Try again or type your question."; return; }
      status.textContent = "Turning your recording into text\u2026";
      var lang = $("ask-lang").value;
      fetch(form.dataset.transcribeUrl + "?lang=" + encodeURIComponent(lang), {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": blob.type, "X-CSRF-Token": form.dataset.csrf }, body: blob
      }).then(function (r) { return r.json().then(function (b) { return { ok: r.ok, body: b }; }); }).then(function (res) {
        if (res.ok && res.body.text) {
          $("ask-question").value = res.body.text;
          $("transcript-note").hidden = false;
          status.textContent = "Transcript ready. Correct it if needed, then press Ask.";
          $("ask-question").focus();
        } else {
          var why = { not_configured: "The AI service is not set up on this server.", busy: "The AI service is busy right now.",
                      timeout: "It took too long.", rate_limited: "Too many requests just now.", empty: "No speech was heard." }[res.body.error] || "It did not work.";
          status.textContent = why + " You can type your question instead.";
        }
      }).catch(function () { status.textContent = "It did not work. You can type your question instead."; });
    }
  }

  // ---------- hear the answer ----------
  var speakRow = $("speak-row");
  if (speakRow && "speechSynthesis" in window) {
    speakRow.hidden = false;
    var text = JSON.parse($("speak-text").textContent), lang = speakRow.dataset.lang, said = $("speak-status");
    var tags = { en: ["en"], zh: ["zh"], ms: ["ms", "id"], ta: ["ta"] };
    function voiceFor() {
      var wanted = tags[lang] || [lang], voices = window.speechSynthesis.getVoices();
      for (var i = 0; i < voices.length; i++) {
        var v = (voices[i].lang || "").toLowerCase().replace("_", "-");
        for (var j = 0; j < wanted.length; j++) if (v === wanted[j] || v.indexOf(wanted[j] + "-") === 0) return voices[i];
      }
      return null;
    }
    function speak() {
      window.speechSynthesis.cancel();
      var voice = voiceFor();
      if (!voice) { said.textContent = "No voice for this language on this device. Please read the answer above."; return; }
      var u = new SpeechSynthesisUtterance(text);
      u.voice = voice; u.lang = voice.lang;
      u.onend = function () { said.textContent = "Finished."; };
      u.onerror = function () { said.textContent = "The answer could not be read aloud. Please read it above."; };
      said.textContent = "Reading aloud\u2026";
      window.speechSynthesis.speak(u);
    }
    $("speak-play").addEventListener("click", speak);
    $("speak-replay").addEventListener("click", speak);
    $("speak-stop").addEventListener("click", function () { window.speechSynthesis.cancel(); said.textContent = "Stopped."; });
    if (window.speechSynthesis.onvoiceschanged === null) window.speechSynthesis.onvoiceschanged = function () { /* voices are read when you press Listen */ };
  }
})();
