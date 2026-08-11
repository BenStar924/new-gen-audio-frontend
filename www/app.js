/* Operator Console — Alpine component.
 *
 * Loaded before vendor/alpine.min.js (both deferred, so document order is
 * execution order) and registers the component on alpine:init.
 *
 * State model: audio is console-wide, because the hardware is — one amplifier
 * mute, one microphone enable, one master volume. A lane is a routing
 * destination. Selecting a lane opens the mic and speakers; releasing it closes
 * them.
 *
 * Lane OWNERSHIP is not ours to decide. Multiple consoles compete for lanes and
 * the Master Controller arbitrates, so a tap is a *request*: the UI marks the
 * lane pending and waits for the backend to confirm. It must never paint a lane
 * as won before it has been granted, or a denied grab flashes as success.
 *
 * The backend is the authority for everything else too. Controls other than
 * lane selection are optimistic — a tap updates local state, renders, then
 * POSTs — and the once-a-second poll reconciles.
 */

document.addEventListener("alpine:init", function () {
  Alpine.data("operatorConsole", function () {
    return {
      uiVersion: "0.6.0",

      pollMs: 1000,
      requestTimeoutMs: 2000,
      meterSegments: 24,
      volumeStep: 5,
      brightnessStep: 10,

      minLanes: 1,
      maxLanes: 12,
      lanesPerTab: 6,

      /* ---------- screens ---------- */

      /* One document, no page loads: a kiosk never navigates, and a reload on
         this board costs seconds of WPE startup. */
      screen: "main",
      unlocked: {},          /* scope -> true, cleared on leaving the screen */

      connected: false,      /* HTTP link to our own backend */
      online: false,         /* backend's link to the Master Controller */
      hardware: false,

      title: "Operator Console",
      lanes: [],
      activeLaneId: 0,
      pendingLaneId: 0,      /* requested, not yet granted */
      held: false,

      mic: false,
      mute: true,
      level: 0,

      volume: 60,
      brightness: 80,
      audioOut: "speaker",
      modules: [],
      userMgmt: false,

      controlsPosition: "right",
      controlsPositions: ["left", "right", "bottom"],

      tab: 0,
      message: "Starting…",
      settingsOpen: false,
      backendHost: window.location.host,

      /* Screens that need a password, and the prompt state for them. */
      authScope: null,
      authError: "",
      logs: [],

      /* On-screen keyboard. No physical keyboard exists on a deployed console,
         so every text and number entry goes through this. */
      kb: { open: false, mode: "number", value: "", label: "", secret: false,
            commit: null },

      init: function () {
        this.poll();
        setInterval(this.poll.bind(this), this.pollMs);
      },

      /* ---------- derived ---------- */

      get hasLane() {
        return this.activeLaneId >= 1;
      },

      get tabCount() {
        return Math.ceil(this.lanes.length / this.lanesPerTab) || 1;
      },

      get tabbed() {
        return this.lanes.length > this.lanesPerTab;
      },

      /* Lanes on the current tab only. */
      get visibleLanes() {
        if (!this.tabbed) return this.lanes;
        var start = this.tab * this.lanesPerTab;
        return this.lanes.slice(start, start + this.lanesPerTab);
      },

      /* A lane on another tab can be live, busy or faulted and the operator
         would never see it. Each tab header carries the worst state among its
         lanes so nothing hides behind a tab. */
      tabStatus: function (index) {
        var start = index * this.lanesPerTab;
        var lanes = this.lanes.slice(start, start + this.lanesPerTab);

        if (lanes.some(function (l) { return l.status === "self"; }))  return "self";
        if (lanes.some(function (l) { return l.status === "error"; })) return "error";
        if (lanes.some(function (l) { return l.status === "busy"; }))  return "busy";
        if (lanes.some(function (l) { return l.status === "hold"; }))  return "hold";
        return "idle";
      },

      get laneColumns() {
        var n = this.visibleLanes.length;
        var wide = { 1: 1, 2: 2, 3: 3, 4: 2, 5: 3, 6: 3 };
        var narrow = { 1: 1, 2: 2, 3: 2, 4: 2, 5: 2, 6: 3 };

        var byCount = this.controlsPosition === "bottom" ? wide : narrow;
        return byCount[n] || Math.ceil(Math.sqrt(n)) || 1;
      },

      get railMode() {
        return this.controlsPosition === "bottom" && this.lanes.length > 1;
      },

      /* ---------- lane appearance ----------
         Every colour is a token so a theme can restyle it without a rebuild.
         Nothing here may return a literal colour. */

      laneClass: function (lane) {
        if (lane.id === this.pendingLaneId) {
          return "bg-lane-idle border-lane-self-line opacity-60";
        }

        switch (lane.status) {
        case "self":
          return "bg-lane-self border-lane-self-line text-white shadow-[0_4px_0_var(--color-lane-self-line)]";
        case "busy":
          return "bg-lane-busy border-lane-busy-line text-txt lane-locked";
        case "hold":
          return "bg-lane-hold border-lane-hold-line text-txt";
        case "error":
          return "bg-lane-error border-lane-error-line text-mute lane-locked";
        default:
          return "bg-lane-idle border-lane-idle-line text-txt";
        }
      },

      /* Short word under the number. The operator must be able to tell "I have
         this" from "someone else has this" without reading a legend. */
      laneCaption: function (lane) {
        if (lane.id === this.pendingLaneId) return "…";
        if (lane.status === "self")  return this.held ? "ON HOLD" : "RELEASE";
        if (lane.status === "busy")  return "IN USE";
        if (lane.status === "hold")  return "PARKED";
        if (lane.status === "error") return "FAULT";
        return lane.label;
      },

      /* Spelled out rather than built by concatenation: Tailwind only emits
         classes it can literally see, and "bg-tag-" + tag is invisible to it. */
      tagClass: function (lane) {
        var map = {
          red: "bg-tag-red", blue: "bg-tag-blue", green: "bg-tag-green",
          yellow: "bg-tag-yellow", purple: "bg-tag-purple", none: "bg-tag-none"
        };
        return map[lane.tag] || "bg-tag-none";
      },

      meterSegmentClass: function (index) {
        var level = (this.hasLane && !this.mute && !this.held) ? this.level : 0;
        var lit = Math.round((level / 100) * this.meterSegments);

        if (index >= lit) return "bg-line-d";

        var position = index / this.meterSegments;
        if (position >= 0.85) return "bg-danger";
        if (position >= 0.7) return "bg-warn";
        return "bg-on";
      },

      /* ---------- theme ----------
         Marketing has not approved a palette and production is late 2027, so no
         colour is compiled in. Tailwind v4 theme tokens ARE custom properties,
         which is what makes a runtime restyle free. */

      applyTheme: function (tokens) {
        if (!tokens || typeof tokens !== "object") return;

        Object.keys(tokens).forEach(function (name) {
          var value = tokens[name];
          if (typeof value === "string" && /^[#a-zA-Z0-9(),.\s%-]+$/.test(value)) {
            document.documentElement.style.setProperty("--color-" + name, value);
          }
        });
      },

      /* ---------- backend ---------- */

      request: function (path, options) {
        var controller = new AbortController();
        var timer = setTimeout(function () {
          controller.abort();
        }, this.requestTimeoutMs);

        var init = Object.assign({ signal: controller.signal }, options || {});

        return fetch(path, init)
          .then(function (response) {
            return response.json().then(function (body) {
              if (!response.ok) {
                var error = new Error(body.error || path + " -> " + response.status);
                error.status = response.status;
                error.body = body;
                throw error;
              }
              return body;
            });
          })
          .finally(function () {
            clearTimeout(timer);
          });
      },

      post: function (path, body) {
        var self = this;

        return this.request(path, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body)
        }).catch(function (error) {
          self.message = error.message || ("Command failed: " + path);
          throw error;
        });
      },

      poll: function () {
        var self = this;

        this.request("/api/status")
          .then(function (data) {
            self.connected = true;
            self.applyStatus(data);
          })
          .catch(function () {
            self.connected = false;
            self.online = false;
            self.message = "No response from backend";
          });
      },

      applyStatus: function (data) {
        if (!data || typeof data !== "object") return;

        if (Array.isArray(data.lanes)) this.lanes = data.lanes;
        if (typeof data.lane === "number") {
          this.activeLaneId = data.lane;
          /* The request resolved one way or the other — stop showing pending. */
          if (this.pendingLaneId && this.pendingLaneId === data.lane) {
            this.pendingLaneId = 0;
          }
        }

        if (typeof data.online === "boolean")     this.online = data.online;
        if (typeof data.hardware === "boolean")   this.hardware = data.hardware;
        if (typeof data.title === "string")       this.title = data.title;
        if (typeof data.held === "boolean")       this.held = data.held;
        if (typeof data.mic === "boolean")        this.mic = data.mic;
        if (typeof data.mute === "boolean")       this.mute = data.mute;
        if (typeof data.level === "number")       this.level = clamp(data.level, 0, 100);
        if (typeof data.volume === "number")      this.volume = clamp(data.volume, 0, 100);
        if (typeof data.brightness === "number")  this.brightness = clamp(data.brightness, 0, 100);
        if (typeof data.audio_out === "string")   this.audioOut = data.audio_out;
        if (typeof data.user_mgmt === "boolean")  this.userMgmt = data.user_mgmt;
        if (Array.isArray(data.modules))          this.modules = data.modules;
        if (typeof data.message === "string")     this.message = data.message;

        if (this.controlsPositions.indexOf(data.layout) !== -1) {
          this.controlsPosition = data.layout;
        }

        this.applyTheme(data.theme);

        /* Lane count can shrink under us, leaving the tab out of range. */
        if (this.tab >= this.tabCount) this.tab = this.tabCount - 1;
      },

      /* ---------- lane actions ---------- */

      selectLane: function (lane) {
        var self = this;

        if (lane.status === "busy" || lane.status === "error") {
          /* Say why rather than doing nothing — a dead tap reads as a broken
             screen. */
          this.message = lane.status === "busy"
            ? lane.label + " is in use by " + (lane.owner || "another console")
            : lane.label + " is out of service";
          return;
        }

        var release = (lane.id === this.activeLaneId);

        if (release) {
          this.activeLaneId = 0;
          this.held = false;
          this.mic = false;
          this.mute = true;
          this.post("/api/lane", { lane: 0 }).catch(function () {});
          return;
        }

        /* A request, not a decision — the Master Controller grants it. */
        this.pendingLaneId = lane.id;
        this.post("/api/lane", { lane: lane.id })
          .catch(function () {
            self.pendingLaneId = 0;   /* denied; the poll keeps the true state */
          });
      },

      toggleHold: function () {
        if (!this.hasLane) return;

        this.held = !this.held;
        this.post("/api/hold", { on: this.held }).catch(function () {});
      },

      toggleMic: function () {
        if (!this.hasLane) return;
        this.mic = !this.mic;
        this.post("/api/mic", { on: this.mic }).catch(function () {});
      },

      toggleMute: function () {
        if (!this.hasLane) return;
        this.mute = !this.mute;
        this.post("/api/mute", { on: this.mute }).catch(function () {});
      },

      setAudioOut: function (id) {
        this.audioOut = id;
        this.post("/api/audio-out", { id: id }).catch(function () {});
      },

      nudgeVolume: function (delta) {
        this.volume = clamp(this.volume + delta, 0, 100);
        this.post("/api/volume", { value: this.volume }).catch(function () {});
      },

      nudgeBrightness: function (delta) {
        this.brightness = clamp(this.brightness + delta, 0, 100);
        this.post("/api/brightness", { value: this.brightness }).catch(function () {});
      },

      setControlsPosition: function (position) {
        if (this.controlsPositions.indexOf(position) === -1) return;
        this.controlsPosition = position;
        this.post("/api/layout", { position: position }).catch(function () {});
      },

      /* ---------- screens and auth ---------- */

      go: function (screen) {
        this.screen = screen;
        this.settingsOpen = false;
        if (screen === "troubleshooting") this.loadLogs();
      },

      /* Leaving a protected screen re-locks it: an installer who walks away
         should not leave the configuration open behind them. */
      leave: function () {
        this.unlocked = {};
        this.screen = "main";
      },

      /* The password is checked by the backend and never held here. */
      unlock: function (scope) {
        var self = this;
        this.authError = "";

        this.promptKeyboard({
          mode: "number",
          label: scope === "developer" ? "Developer password" : "Configuration password",
          secret: true,
          commit: function (value) {
            self.request("/api/auth", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ scope: scope, password: value })
            }).then(function () {
              self.unlocked[scope] = true;
              self.go(scope === "developer" ? "developer" : "config");
            }).catch(function () {
              self.authError = "Incorrect password";
            });
          }
        });
      },

      loadLogs: function () {
        var self = this;
        this.request("/api/logs")
          .then(function (data) { self.logs = data.entries || []; })
          .catch(function () { self.logs = []; });
      },

      /* ---------- installer configuration ---------- */

      setLaneCount: function (count) {
        count = clamp(count, this.minLanes, this.maxLanes);
        this.post("/api/lanes", { count: count }).catch(function () {});
      },

      setLaneField: function (lane, field, value) {
        var body = { lane: lane.id };
        body[field] = value;
        lane[field] = value;
        this.post("/api/lane-config", body).catch(function () {});
      },

      editLaneLabel: function (lane) {
        var self = this;
        this.promptKeyboard({
          mode: "text",
          label: "Name for lane " + lane.id,
          value: lane.label,
          commit: function (value) { self.setLaneField(lane, "label", value); }
        });
      },

      editTitle: function () {
        var self = this;
        this.promptKeyboard({
          mode: "text",
          label: "Screen title",
          value: this.title,
          commit: function (value) {
            self.title = value;
            self.post("/api/title", { text: value }).catch(function () {});
          }
        });
      },

      /* ---------- on-screen keyboard ---------- */

      promptKeyboard: function (options) {
        this.kb.open = true;
        this.kb.mode = options.mode || "number";
        this.kb.label = options.label || "";
        this.kb.value = options.value || "";
        this.kb.secret = !!options.secret;
        this.kb.commit = options.commit || null;
      },

      kbKey: function (key) {
        if (this.kb.value.length < 32) this.kb.value += key;
      },

      kbBackspace: function () {
        this.kb.value = this.kb.value.slice(0, -1);
      },

      kbCancel: function () {
        this.kb.open = false;
        this.kb.commit = null;
      },

      kbAccept: function () {
        var commit = this.kb.commit;
        var value = this.kb.value;

        this.kb.open = false;
        this.kb.commit = null;

        if (commit) commit(value);
      },

      get kbDisplay() {
        return this.kb.secret ? "•".repeat(this.kb.value.length) : this.kb.value;
      },

      kbRows: function () {
        if (this.kb.mode === "number") {
          return [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"], ["0"]];
        }
        return [
          ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
          ["A", "S", "D", "F", "G", "H", "J", "K", "L"],
          ["Z", "X", "C", "V", "B", "N", "M", "-", " "]
        ];
      }
    };
  });
});

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

/* Held-down feedback, delegated once for every button on the page.
 *
 * Not :active, which sticks on some touch panels when a press is cancelled
 * mid-gesture. Kept out of Alpine because it is presentation only — routing it
 * through component state would re-render the tree on every press.
 *
 * Taps themselves bind to pointerup in the markup (click is unreliable under
 * WPE) with Alpine's .throttle modifier as the duplicate-event guard. */
document.addEventListener("pointerdown", function (event) {
  var button = event.target.closest("button");
  if (button && !button.disabled) button.classList.add("is-pressed");
});

["pointerup", "pointercancel", "pointerleave"].forEach(function (name) {
  document.addEventListener(name, function (event) {
    var button = event.target.closest("button");
    if (button) button.classList.remove("is-pressed");
  });
});
