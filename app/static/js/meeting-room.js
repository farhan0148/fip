// Video meeting room. WebRTC media goes browser to browser; this page only exchanges small signalling messages with the
// server (polling). Audio is recorded in THIS browser, from this person's own microphone, only while both people have
// agreed, and the finished segment is uploaded once. No AI service is called from here.
(function () {
  "use strict";
  var cfgNode = document.getElementById("room-config");
  var room = document.getElementById("room");
  if (!cfgNode || !room) return;
  var cfg = JSON.parse(cfgNode.textContent);
  var csrf = room.dataset.csrf;
  var role = cfg.role;
  function $(id) { return document.getElementById(id); }

  var local = null, pc = null, epoch = null, lastId = 0, ended = cfg.ended;
  var pendingCandidates = [], remoteReady = false, offered = false, polling = true;
  var recorder = null, chunks = [], recStarted = 0, myConsent = false, lastState = null;
  var uploads = [];

  function post(url, body) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json().catch(function () { return {}; }); });
  }
  function setText(id, text) { var n = $(id); if (n) n.textContent = text; }

  // ---------- media ----------
  function getMedia() {
    var md = navigator.mediaDevices;
    if (!md || !md.getUserMedia) return Promise.reject(new Error("unsupported"));
    return md.getUserMedia({ audio: true, video: true }).catch(function () {
      return md.getUserMedia({ audio: true });
    });
  }

  // ---------- peer connection ----------
  function resetPc() {
    if (pc) { try { pc.close(); } catch (e) { /* already closed */ } }
    pc = null; remoteReady = false; offered = false; pendingCandidates = [];
    var remote = $("remote-video");
    if (remote) remote.srcObject = null;
  }
  function makePc() {
    resetPc();
    pc = new RTCPeerConnection({ iceServers: cfg.ice });
    var myEpoch = epoch;
    if (local) local.getTracks().forEach(function (t) { pc.addTrack(t, local); });
    pc.onicecandidate = function (e) {
      if (e.candidate) post(cfg.urls.signal, { epoch: myEpoch, kind: "candidate", payload: JSON.stringify(e.candidate) });
    };
    pc.ontrack = function (e) {
      var remote = $("remote-video");
      if (remote && remote.srcObject !== e.streams[0]) remote.srcObject = e.streams[0];
    };
    pc.onconnectionstatechange = function () {
      if (!pc) return;
      setText("call-status", pc.connectionState === "connected" ? "Connected" :
        pc.connectionState === "failed" ? "Connection failed. Reload this page to try again." : "Connecting\u2026");
    };
    return pc;
  }
  function flushCandidates() {
    var list = pendingCandidates; pendingCandidates = [];
    list.forEach(function (c) { pc.addIceCandidate(c).catch(function () { /* stale candidate */ }); });
  }
  function startOffer() {
    offered = true;
    var myEpoch = epoch, conn = makePc();
    conn.createOffer().then(function (offer) {
      return conn.setLocalDescription(offer).then(function () {
        return post(cfg.urls.signal, { epoch: myEpoch, kind: "offer", payload: JSON.stringify(conn.localDescription) });
      });
    });
  }
  function handleSignal(sig) {
    var data = JSON.parse(sig.payload);
    if (sig.kind === "offer" && role === "client") {
      var myEpoch = epoch, conn = makePc();
      conn.setRemoteDescription(data).then(function () {
        remoteReady = true; flushCandidates();
        return conn.createAnswer();
      }).then(function (answer) {
        return conn.setLocalDescription(answer).then(function () {
          return post(cfg.urls.signal, { epoch: myEpoch, kind: "answer", payload: JSON.stringify(conn.localDescription) });
        });
      });
    } else if (sig.kind === "answer" && role === "fc" && pc) {
      pc.setRemoteDescription(data).then(function () { remoteReady = true; flushCandidates(); });
    } else if (sig.kind === "candidate" && pc) {
      if (remoteReady) pc.addIceCandidate(data).catch(function () { /* stale candidate */ });
      else pendingCandidates.push(data);
    }
  }

  // ---------- recording (own microphone, only while both agree) ----------
  function recorderMime() {
    var options = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
    for (var i = 0; i < options.length; i++) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(options[i])) return options[i];
    }
    return "";
  }
  function startRecorder() {
    if (recorder || !local || !window.MediaRecorder || local.getAudioTracks().length === 0) return;
    var mime = recorderMime();
    chunks = []; recStarted = Date.now();
    recorder = new MediaRecorder(new MediaStream(local.getAudioTracks()), mime ? { mimeType: mime } : undefined);
    var active = recorder, started = recStarted;
    active.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
    active.onstop = function () {
      var blob = new Blob(chunks, { type: active.mimeType || mime || "audio/webm" });
      chunks = [];
      if (blob.size === 0) return;
      uploads.push(fetch(cfg.urls.recording + "?started=" + started, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": blob.type, "X-CSRF-Token": csrf }, body: blob
      }).then(function (r) { return r.json().catch(function () { return {}; }); }));
    };
    active.start();
  }
  function stopRecorder() {
    if (!recorder) return Promise.resolve();
    var active = recorder; recorder = null;
    if (active.state !== "inactive") active.stop();
    return new Promise(function (resolve) { setTimeout(function () { Promise.all(uploads).then(resolve, resolve); }, 150); });
  }

  // ---------- UI ----------
  function render(state) {
    lastState = state;
    var who = state.other_name || "the other person";
    if (!ended) {
      if (!pc || pc.connectionState === "new" || pc.connectionState === "connecting") {
        setText("call-status", state.other_present ? "Connecting\u2026" : "Waiting for " + who + " to join");
      }
    }
    var mine = role === "client" ? state.client_rec : state.fc_rec;
    var theirs = role === "client" ? state.fc_rec : state.client_rec;
    myConsent = mine;
    var btn = $("consent-btn");
    if (btn) {
      btn.textContent = mine ? "Withdraw recording consent" : "I agree to recording";
      btn.setAttribute("aria-pressed", mine ? "true" : "false");
      btn.disabled = ended;
    }
    var indicator = $("rec-indicator");
    if (indicator) indicator.hidden = !state.recording;
    setText("rec-note", state.recording ? "Recording. Audio is captured in each browser while you both agree, and stops if either of you withdraws or the meeting ends. You can keep talking without it." :
      mine ? "Waiting for " + who + " to agree before anything is recorded." :
      theirs ? who + " has agreed to recording. Nothing is recorded until you agree too." :
      "Not recording. Recording starts only if you both agree.");
  }
  function finishEnded() {
    if (ended && !polling) return;
    ended = true; polling = false;
    setText("call-status", "The meeting has ended.");
    var indicator = $("rec-indicator"); if (indicator) indicator.hidden = true;
    stopRecorder().then(function () {
      resetPc();
      if (local) local.getTracks().forEach(function (t) { t.stop(); });
      var done = $("after-end"); if (done) done.hidden = false;
    });
  }

  // ---------- polling ----------
  function poll() {
    if (!polling) return;
    fetch(cfg.urls.state + "?after=" + lastId + "&epoch=" + epoch, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (state) {
        if (!state) return;
        if (state.ended) { finishEnded(); return; }
        if (state.epoch !== epoch) { epoch = state.epoch; lastId = 0; resetPc(); }
        render(state);
        state.signals.forEach(function (s) { lastId = Math.max(lastId, s.id); handleSignal(s); });
        if (role === "fc" && state.other_present && !pc && !offered) startOffer();
        if (state.recording) startRecorder(); else stopRecorder();
      })
      .catch(function () { /* a missed poll is fine */ })
      .then(function () { if (polling) setTimeout(poll, 1000); });
  }

  // ---------- buttons ----------
  var consentBtn = $("consent-btn");
  if (consentBtn) consentBtn.addEventListener("click", function () {
    var agree = !myConsent;
    if (!agree) stopRecorder();            // withdrawing stops capture at once, before the server round trip
    post(cfg.urls.consent, { agree: agree });
  });
  var endBtn = $("end-btn");
  if (endBtn) endBtn.addEventListener("click", function () {
    endBtn.disabled = true;
    stopRecorder().then(function () { return post(cfg.urls.end, {}); }).then(function () {
      polling = false; window.location.href = cfg.afterEnd;
    });
  });
  var leaveBtn = $("leave-btn");
  if (leaveBtn) leaveBtn.addEventListener("click", function () {
    leaveBtn.disabled = true; polling = false;
    stopRecorder().then(function () { return post(cfg.urls.leave, {}); }).then(function () {
      if (local) local.getTracks().forEach(function (t) { t.stop(); });
      window.location.href = cfg.afterEnd;
    });
  });

  // ---------- for the walkthrough checks: what is actually arriving ----------
  window.__room = {
    stats: function () {
      var remote = $("remote-video");
      var out = { connection: pc ? pc.connectionState : "none", videoWidth: remote ? remote.videoWidth : 0,
                  remoteAudioTracks: 0, remoteVideoTracks: 0, audioBytes: 0, videoBytes: 0, audioEnergy: 0,
                  recording: !!recorder };
      if (remote && remote.srcObject) {
        out.remoteAudioTracks = remote.srcObject.getAudioTracks().length;
        out.remoteVideoTracks = remote.srcObject.getVideoTracks().length;
      }
      if (!pc) return Promise.resolve(out);
      return pc.getStats().then(function (report) {
        report.forEach(function (s) {
          if (s.type === "inbound-rtp" && s.kind === "audio") { out.audioBytes += s.bytesReceived || 0; out.audioEnergy += s.totalAudioEnergy || 0; }
          if (s.type === "inbound-rtp" && s.kind === "video") { out.videoBytes += s.bytesReceived || 0; }
        });
        return out;
      });
    }
  };

  // ---------- start ----------
  if (ended) { finishEnded(); return; }
  getMedia().then(function (stream) {
    local = stream;
    var lv = $("local-video"); if (lv) lv.srcObject = stream;
  }).catch(function () {
    setText("call-status", "Camera or microphone is not available. Allow access in your browser and reload.");
  }).then(function () {
    return post(cfg.urls.join, {});
  }).then(function (joined) {
    if (joined.error === "ended") { finishEnded(); return; }
    epoch = joined.epoch;
    setText("call-status", "Waiting for the other person to join");
    poll();
  });
})();
