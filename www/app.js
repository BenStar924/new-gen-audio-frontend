/* Operator Console — UI logic.
 *
 * Vanilla ES2020, no framework, no build step. Served by libmicrohttpd from
 * www/ and rendered by Cog/WPE.
 *
 * Shape of the code:
 *   state      — the single source of truth, mirrored from the backend
 *   render()   — writes state to the DOM; never reads from it
 *   bindTap()  — pointerup + debounce (click is unreliable under WPE)
 *   poll()     — GET /api/status on an interval, reconciles state
 *
 * Every control is optimistic: it updates state locally, renders, then POSTs.
 * The next poll reconciles against the hardware's real state, so a rejected
 * command corrects itself within one poll interval rather than leaving the UI
 * lying about the amplifier.
 */

(function () {
  "use strict";

  var UI_VERSION = "0.1.0";
  var POLL_MS = 1000;
  var TAP_DEBOUNCE_MS = 250;
  var METER_SEGMENTS = 24;
  var VOLUME_STEP = 5;
  var BRIGHTNESS_STEP = 10;

  var state = {
    connected: false,
    lanes: 8,
    lane: 1,
    mic: false,
    mute: true,        // matches the config.txt boot state: GPIO25 high = muted
    volume: 60,
    brightness: 80,
    level: 0,          // 0..100, most recent input level
    message: "Starting…",
    settingsOpen: false
  };

  var el = {};

  /* ---------- DOM helpers ---------- */

  function byId(id) {
    return document.getElementById(id);
  }

  function cacheElements() {
    [
      "status-pill", "status-text", "btn-settings",
      "lane-grid",
      "btn-mic", "btn-mute",
      "btn-vol-down", "btn-vol-up", "volume-value", "level-meter",
      "footer-msg", "footer-version",
      "settings-overlay", "btn-settings-close", "btn-reload",
      "btn-bright-down", "btn-bright-up", "brightness-value",
      "settings-backend", "settings-version"
    ].forEach(function (id) {
      el[id] = byId(id);
    });
  }

  /* ---------- Input ---------- */

  /* Bind a tap handler.
   *
   * pointerup fires for both mouse (bench, under cage) and touch (production)
   * under WPE, where click does not fire reliably. The debounce guards against
   * a panel that reports a single tap as several pointer events — without it a
   * latching control can toggle twice and appear not to respond at all. */
  function bindTap(node, handler) {
    if (!node) return;
    var last = 0;

    node.addEventListener("pointerup", function (event) {
      var now = Date.now();
      if (now - last < TAP_DEBOUNCE_MS) return;
      last = now;

      event.preventDefault();
      handler(event);
    });

    /* Held-down feedback without :active, which sticks on some touch panels
       when the pointer is cancelled mid-press. */
    node.addEventListener("pointerdown", function () {
      node.classList.add("is-pressed");
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach(function (name) {
      node.addEventListener(name, function () {
        node.classList.remove("is-pressed");
      });
    });
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  /* ---------- Backend ---------- */

  /* All requests are same-origin against 127.0.0.1 and must fail fast: a hung
     request would stack up behind the poll interval. */
  function request(path, options) {
    var controller = new AbortController();
    var timer = setTimeout(function () {
      controller.abort();
    }, 2000);

    var init = Object.assign({ signal: controller.signal }, options || {});

    return fetch(path, init)
      .then(function (response) {
        if (!response.ok) throw new Error(path + " -> " + response.status);
        return response.json();
      })
      .finally(function () {
        clearTimeout(timer);
      });
  }

  function post(path, body) {
    return request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).catch(function (error) {
      /* Surface the failure rather than silently diverging from the hardware;
         the next poll re-syncs the control to its real state. */
      setMessage("Command failed: " + path);
      console.error(error);
    });
  }

  function poll() {
    request("/api/status")
      .then(function (data) {
        state.connected = true;
        applyStatus(data);
        render();
      })
      .catch(function () {
        state.connected = false;
        setMessage("No response from backend");
        render();
      });
  }

  /* Copy only the fields the backend actually sent, so a partial response
     never blanks a control. */
  function applyStatus(data) {
    if (!data || typeof data !== "object") return;

    if (typeof data.lanes === "number") state.lanes = data.lanes;
    if (typeof data.lane === "number") state.lane = data.lane;
    if (typeof data.mic === "boolean") state.mic = data.mic;
    if (typeof data.mute === "boolean") state.mute = data.mute;
    if (typeof data.volume === "number") state.volume = clamp(data.volume, 0, 100);
    if (typeof data.brightness === "number") state.brightness = clamp(data.brightness, 0, 100);
    if (typeof data.level === "number") state.level = clamp(data.level, 0, 100);
    if (typeof data.message === "string") state.message = data.message;
  }

  function setMessage(text) {
    state.message = text;
  }

  /* ---------- Actions ---------- */

  function selectLane(lane) {
    state.lane = lane;
    setMessage("Lane " + lane + " selected");
    render();
    post("/api/lane", { lane: lane });
  }

  function toggleMic() {
    state.mic = !state.mic;
    setMessage("Mic " + (state.mic ? "live" : "off"));
    render();
    post("/api/mic", { on: state.mic });
  }

  function toggleMute() {
    state.mute = !state.mute;
    setMessage(state.mute ? "Speakers muted" : "Speakers on");
    render();
    post("/api/mute", { on: state.mute });
  }

  function nudgeVolume(delta) {
    state.volume = clamp(state.volume + delta, 0, 100);
    render();
    post("/api/volume", { value: state.volume });
  }

  function nudgeBrightness(delta) {
    state.brightness = clamp(state.brightness + delta, 0, 100);
    render();
    post("/api/brightness", { value: state.brightness });
  }

  function setSettingsOpen(open) {
    state.settingsOpen = open;
    render();
  }

  /* ---------- Static DOM ---------- */

  /* Lane buttons and meter segments are built once. Re-rendering them every
     frame would churn the DOM on a board with no headroom for it. */
  function buildLanes() {
    el["lane-grid"].textContent = "";

    for (var i = 1; i <= state.lanes; i++) {
      var button = document.createElement("button");
      button.className = "btn lane-btn";
      button.dataset.lane = String(i);
      button.textContent = String(i);
      button.setAttribute("aria-label", "Lane " + i);
      el["lane-grid"].appendChild(button);

      bindTap(button, (function (lane) {
        return function () { selectLane(lane); };
      })(i));
    }
  }

  function buildMeter() {
    el["level-meter"].textContent = "";

    for (var i = 0; i < METER_SEGMENTS; i++) {
      var segment = document.createElement("span");
      segment.className = "meter-seg";

      /* Top ~15% reads as peak, the band below it as warning. */
      var position = i / METER_SEGMENTS;
      if (position >= 0.85) segment.classList.add("peak");
      else if (position >= 0.7) segment.classList.add("warn");

      el["level-meter"].appendChild(segment);
    }
  }

  /* ---------- Render ---------- */

  function render() {
    renderStatus();
    renderLanes();
    renderLatch(el["btn-mic"], state.mic);
    renderLatch(el["btn-mute"], state.mute);
    renderVolume();
    renderMeter();
    renderSettings();

    el["footer-msg"].textContent = state.message;
  }

  function renderStatus() {
    var pill = el["status-pill"];
    pill.classList.toggle("status-online", state.connected);
    pill.classList.toggle("status-offline", !state.connected);
    el["status-text"].textContent = state.connected ? "READY" : "NO LINK";
  }

  function renderLanes() {
    var buttons = el["lane-grid"].children;
    for (var i = 0; i < buttons.length; i++) {
      var button = buttons[i];
      button.classList.toggle("is-active", Number(button.dataset.lane) === state.lane);
    }
  }

  function renderLatch(button, on) {
    button.setAttribute("aria-pressed", on ? "true" : "false");
    var label = button.querySelector(".latch-state");
    label.textContent = on ? label.dataset.on : label.dataset.off;
  }

  function renderVolume() {
    el["volume-value"].textContent = String(state.volume);
    el["btn-vol-down"].disabled = state.volume <= 0;
    el["btn-vol-up"].disabled = state.volume >= 100;
  }

  function renderMeter() {
    /* A muted amplifier shows no level regardless of what the input is doing —
       the meter reports what reaches the speakers, not what reaches the ADC. */
    var level = state.mute ? 0 : state.level;
    var lit = Math.round((level / 100) * METER_SEGMENTS);
    var segments = el["level-meter"].children;

    for (var i = 0; i < segments.length; i++) {
      segments[i].classList.toggle("lit", i < lit);
    }
  }

  function renderSettings() {
    el["settings-overlay"].classList.toggle("hidden", !state.settingsOpen);
    el["brightness-value"].textContent = String(state.brightness);
    el["btn-bright-down"].disabled = state.brightness <= 10;
    el["btn-bright-up"].disabled = state.brightness >= 100;
    el["settings-backend"].textContent = state.connected ? window.location.host : "unreachable";
  }

  /* ---------- Start ---------- */

  function init() {
    cacheElements();
    buildLanes();
    buildMeter();

    bindTap(el["btn-mic"], toggleMic);
    bindTap(el["btn-mute"], toggleMute);
    bindTap(el["btn-vol-down"], function () { nudgeVolume(-VOLUME_STEP); });
    bindTap(el["btn-vol-up"], function () { nudgeVolume(VOLUME_STEP); });

    bindTap(el["btn-settings"], function () { setSettingsOpen(true); });
    bindTap(el["btn-settings-close"], function () { setSettingsOpen(false); });
    bindTap(el["btn-reload"], function () { window.location.reload(); });
    bindTap(el["btn-bright-down"], function () { nudgeBrightness(-BRIGHTNESS_STEP); });
    bindTap(el["btn-bright-up"], function () { nudgeBrightness(BRIGHTNESS_STEP); });

    el["footer-version"].textContent = "v" + UI_VERSION;
    el["settings-version"].textContent = UI_VERSION;

    render();
    poll();
    setInterval(poll, POLL_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
