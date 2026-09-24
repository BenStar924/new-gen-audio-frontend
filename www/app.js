/* HUBS Audio console, UI.
 *
 * One Alpine component drives every screen; the screen shown is derived from
 * backend state (see `view`), never stored separately, so the UI cannot drift
 * from what the backend says: a call always takes the whole screen, a locked
 * console always shows the Locked screen.
 *
 * Built from the HUBS Audio UI/UX Design KPI. The API it talks to is API.md
 * (contract v2). Two rules from that document shape this file:
 *
 *   - Role gating goes through ONE check. Nothing here says `if master`;
 *     every screen and control asks can(capability), and the matrix lives in
 *     CAPABILITIES below.
 *   - Device status has ONE source. The lane grid, the status light, the A2B
 *     chain diagram and "this console's errors" all derive from `devices`.
 *
 * Loaded before vendor/alpine.min.js (both deferred, so document order is
 * execution order) and registers the component on alpine:init.
 */

/* ---------- application error capture ----------
 *
 * Feeds the Developer screen's application log. Uncaught errors, unhandled
 * promise rejections and the app's own console.error / console.warn calls are
 * batched to the local backend (there is no internet for a cloud tracker).
 * Failures to send are swallowed silently: reporting them would log another
 * error, which would try to send, and so on. */
(function () {
  var queue = [];
  var timer = null;
  var MAX_QUEUE = 50;

  function iso() { return new Date().toISOString(); }

  function push(entry) {
    if (queue.length >= MAX_QUEUE) return;          /* an error storm must not flood the Pi */
    entry.t = entry.t || iso();
    queue.push(entry);
    if (!timer) timer = setTimeout(flush, 1000);
  }

  function flush() {
    timer = null;
    if (!queue.length) return;
    var batch = queue.splice(0, queue.length);
    try {
      fetch("/api/app-log", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entries: batch })
      }).catch(function () {});
    } catch (e) { /* nowhere to report it */ }
  }

  window.addEventListener("error", function (e) {
    push({ level: "error", message: String(e.message || "Error"), source: e.filename || "",
           line: e.lineno || 0, column: e.colno || 0, stack: (e.error && e.error.stack) || "" });
  });

  window.addEventListener("unhandledrejection", function (e) {
    var r = e.reason;
    push({ level: "error", message: "Unhandled rejection: " + (r && r.message ? r.message : String(r)),
           source: "", line: 0, column: 0, stack: (r && r.stack) || "" });
  });

  ["error", "warn"].forEach(function (level) {
    var original = console[level];
    console[level] = function () {
      try {
        var text = Array.prototype.map.call(arguments, function (a) {
          return typeof a === "string" ? a : (a && a.message) || JSON.stringify(a);
        }).join(" ");
        push({ level: level, message: text, source: "", line: 0, column: 0,
               stack: (new Error().stack || "").split("\n").slice(2).join("\n") });
      } catch (e) { /* never let capture break logging */ }
      return original.apply(console, arguments);
    };
  });
})();

/* ---------- constants ---------- */

var API_VERSION = 2;
var BUILTIN_USER = "Hamilton Audio";

/* Every UI-side timer, together. Values marked (open) in the design doc are
   placeholders inside its stated ranges. */
var TIMERS = {
  screensaverMs:          5 * 60 * 1000,   /* fixed */
  settingsIdleMs:        10 * 60 * 1000,   /* open: 5-20 min */
  troubleshootingIdleMs: 10 * 60 * 1000,   /* open: 5-20 min */
  developerIdleMs:        3 * 60 * 1000,   /* open: 2-5 min */
  longPressMs:            2000,            /* hidden Developer entry */
  pollMs:                 1000,
  requestTimeoutMs:       3000,
  noticeMs:               4000
};

/* The provisional capability matrix. The design doc defers the full
   permission matrix, so this is the single place it changes, and the backend
   must enforce the same table (API.md). `console: null` means any console. */
var USER_ROLES = ["user", "technician", "admin", "owner"];
var CAPABILITIES = {
  troubleshooting:    { console: null,     roles: USER_ROLES },
  settings:           { console: "master", roles: ["technician", "admin", "owner"] },
  manage_users:       { console: "master", roles: ["admin", "owner"] },
  chain_diagram:      { console: "master", roles: USER_ROLES },
  restart_lane_audio: { console: "master", roles: ["technician", "admin", "owner"] }
};

var ROLE_LABELS = {
  user: "Teller", technician: "Technician", admin: "Admin", owner: "Owner", disabled: "Disabled"
};

/* PLACEHOLDERS for Hamilton's 16 pneumatic lane LED colours, which have not
   been supplied. A site has at most 16 devices, so every lane can have its own
   colour. These are identity, not theme: they match physical LEDs and do not
   follow the runtime palette. */
var LANE_COLORS = {
  red: "#E0302A", orange: "#F07A1A", amber: "#F5A300", yellow: "#F2D21B",
  lime: "#9BD62A", green: "#25A244", teal: "#12A08E", cyan: "#1BC3E0",
  sky: "#3B9BF0", blue: "#2257D6", indigo: "#4B3FC4", violet: "#7B4BD8",
  purple: "#9C3FB8", magenta: "#D62F9E", pink: "#F27AB0", white: "#F4F4F4"
};
var LANE_COLOR_ORDER = Object.keys(LANE_COLORS);

/* ---------- colour helpers ---------- */

function hexToRgb(hex) {
  if (typeof hex !== "string") return null;
  var m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  var n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function rgbToHex(rgb) {
  return "#" + rgb.map(function (c) {
    var v = Math.max(0, Math.min(255, Math.round(c))).toString(16);
    return v.length === 1 ? "0" + v : v;
  }).join("");
}

function relLum(rgb) {
  var a = rgb.map(function (c) {
    c /= 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
}

function contrast(a, b) {
  var la = relLum(a), lb = relLum(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

function mix(a, b, t) {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

var WHITE = [255, 255, 255], BLACK = [0, 0, 0], LED_BODY = [29, 31, 38];

/* CSS variables for one lane: its colour, a readable ink for the number on
   it, and the LED at ~10% (dim, "in a call on another console") and unlit.
   Computed here rather than with color-mix(), which older WPE builds lack. */
function laneVars(colorId) {
  var hex = LANE_COLORS[colorId] || LANE_COLORS.white;
  var rgb = hexToRgb(hex);
  var ink = contrast(rgb, BLACK) >= contrast(rgb, WHITE) ? "#111111" : "#FFFFFF";
  return "--lane:" + hex + ";--lane-ink:" + ink +
         ";--lane-dim:" + rgbToHex(mix(LED_BODY, rgb, 0.22)) +
         ";--lane-off:" + rgbToHex(mix(LED_BODY, rgb, 0.07));
}

/* ---- customer branding (later version; behind features.customer_branding) ----
   Kept so the capability is ready: the installer picks a hue and a shade and
   the palette is derived with legibility guaranteed. v1 ships Hamilton only. */

function darkenForWhite(rgb) {
  var r = rgb, guard = 0;
  while (contrast(r, WHITE) < 4.5 && guard < 40) { r = mix(r, BLACK, 0.06); guard++; }
  return r;
}

function inkFor(chrome) {
  var ink = mix(chrome, BLACK, 0.82);
  if (contrast(ink, chrome) < 4.5) ink = [17, 17, 17];
  return ink;
}

function deriveBrandTheme(baseHex, shade) {
  var rgb = hexToRgb(baseHex);
  if (!rgb) return {};
  var chrome, onDark, self;
  if (shade === "light") {
    chrome = mix(rgb, WHITE, 0.78);
    onDark = true;
    self = darkenForWhite(rgb);
  } else {
    chrome = darkenForWhite(rgb);
    if (shade === "dark") chrome = mix(chrome, BLACK, 0.28);
    onDark = false;
    self = chrome;
  }
  return {
    navy: rgbToHex(chrome),
    "navy-d": rgbToHex(mix(chrome, BLACK, 0.35)),
    "navy-tint": onDark ? rgbToHex(mix(rgb, BLACK, 0.2)) : rgbToHex(mix(chrome, WHITE, 0.6)),
    onchrome: onDark ? rgbToHex(inkFor(chrome)) : "#FFFFFF",
    ink: rgbToHex(self)
  };
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function pad2(n) { return (n < 10 ? "0" : "") + n; }

/* ---------- the component ---------- */

document.addEventListener("alpine:init", function () {
  Alpine.data("hubsConsole", function () {
    return {
      uiVersion: "1.0.0",
      apiVersion: API_VERSION,
      backendApi: null,
      builtinUser: BUILTIN_USER,
      roleLabels: ROLE_LABELS,
      laneColorOrder: LANE_COLOR_ORDER,

      /* ---- mirrored from GET /api/status ---- */
      connected: false,
      consoleInfo: { id: "", role: "operator", i2c: true, a2b: true, master_link: true },
      devices: [],
      call: null,
      outputs: [],
      session: { user: null, login_required: false, last_user: "", pin_length: 4, autolock_minutes: 150 },
      sync: { pending: 0, total: 0 },
      servicePanel: false,
      features: { answer_without_pin: false, customer_branding: false },
      branding: { title: "", theme: {}, brand: { base: "", shade: "base" } },
      message: "",

      /* Backend clock: the Master Controller pushes time to every console, so
         timers and the date use it rather than the browser's own clock. */
      serverOffset: 0,
      nowS: Date.now() / 1000,

      /* ---- local UI state ---- */
      screen: "main",              /* main | settings | troubleshooting | help */
      pendingLane: "",             /* a tap in flight */
      notice: "",
      noticeUntil: 0,
      lastActivity: Date.now(),
      screensaver: false,
      restarting: false,

      dev: { unlocked: false, mode: null, returnTo: "troubleshooting", log: [] },
      help: { docs: [], doc: null, page: 0 },
      login: { username: "", valid: false, pin: "", error: "", checking: false },
      confirmBox: { open: false, title: "", body: "", okLabel: "", danger: false, action: null },
      picker: { open: false, title: "", options: [], value: "", commit: null },
      chainSelected: "",

      settings: null,
      settingsTab: "lanes",
      settingsConsole: "",
      settingsResult: "",

      themeSig: "",
      themeKeys: [],
      longPressTimer: null,

      kb: { open: false, mode: "number", label: "", value: "", secret: false, commit: null, max: 32 },

      /* ================= derived ================= */

      get user() { return this.session.user; },

      get lanes() {
        return this.devices
          .filter(function (d) { return d.kind === "lane"; })
          .sort(function (a, b) { return a.number - b.number; });
      },

      get chain() {
        return this.devices.slice().sort(function (a, b) { return a.position - b.position; });
      },

      get me() {
        var id = this.consoleInfo.id;
        return this.devices.find(function (d) { return d.id === id; }) || null;
      },

      get isMaster() { return this.consoleInfo.role === "master"; },

      get locked() {
        return this.session.login_required && !this.session.user;
      },

      get ringingLanes() {
        return this.lanes.filter(function (l) { return l.call && l.call.state === "ringing"; });
      },

      get callLane() {
        if (!this.call) return null;
        var id = this.call.lane;
        return this.devices.find(function (d) { return d.id === id; }) || null;
      },

      /* What is on screen, in priority order. A call always wins: the design
         doc makes the in-call screen the only thing usable during a call. */
      get view() {
        if (this.restarting) return "restarting";
        if (this.call) return "call";
        if (this.dev.unlocked) return "developer";
        if (this.locked) return "locked";
        return this.screen;
      },

      /* The status light, one rule for every screen: red for any error
         anywhere or a failed heartbeat; yellow for warnings only; else green. */
      get statusLevel() {
        var c = this.consoleInfo;
        if (!this.connected || !c.i2c || !c.a2b || !c.master_link) return "error";
        var worst = "ok";
        this.devices.forEach(function (d) {
          if (d.health === "error") worst = "error";
          else if (d.health === "warning" && worst !== "error") worst = "warning";
        });
        return worst;
      },

      get statusLabel() {
        return { ok: "System OK", warning: "Warning", error: "Error" }[this.statusLevel];
      },

      get apiMismatch() {
        return this.backendApi !== null && this.backendApi !== this.apiVersion;
      },

      get statusLine() {
        if (this.apiMismatch) {
          return "Backend API v" + this.backendApi + ", this UI needs v" + this.apiVersion +
                 ", so update one side";
        }
        if (!this.connected) return "No response from the console backend";
        if (this.notice && Date.now() < this.noticeUntil) return this.notice;
        return this.message;
      },

      get title() {
        return (this.features.customer_branding && this.branding.title) || "HUBS Audio";
      },

      get laneCols() {
        var n = this.lanes.length;
        return n <= 8 ? Math.max(n, 1) : Math.ceil(n / 2);
      },

      get laneRows() { return this.lanes.length <= 8 ? 1 : 2; },

      /* Pixel sizes for the identity graphic and LED button. Fixed steps
         rather than percentages: the LED must stay round whichever of the
         tile's width or height is the tighter one. */
      get laneSize() {
        if (this.laneRows === 2) return { unit: 60, led: 46 };
        if (this.laneCols <= 4) return { unit: 132, led: 96 };
        return { unit: 96, led: 68 };
      },

      get heldLanes() {
        return this.lanes.filter(function (l) { return l.call && l.call.state === "held"; });
      },

      get otherLanes() {
        var id = this.call && this.call.lane;
        return this.lanes.filter(function (l) { return l.id !== id; });
      },

      get settingsTabs() {
        var tabs = [
          { id: "lanes", label: "Lanes" },
          { id: "consoles", label: "Consoles" }
        ];
        if (this.can("manage_users")) tabs.push({ id: "users", label: "Users" });
        tabs.push({ id: "site", label: "Site" }, { id: "audio", label: "Audio" },
                  { id: "backup", label: "Backup" });
        if (this.features.customer_branding) tabs.push({ id: "branding", label: "Branding" });
        return tabs;
      },

      get clockText() {
        var d = new Date(this.nowS * 1000);
        var days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
        var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
        var h = d.getHours(), ampm = h >= 12 ? "PM" : "AM";
        h = h % 12 || 12;
        return days[d.getDay()] + " " + d.getDate() + " " + months[d.getMonth()] +
               "  ·  " + h + ":" + pad2(d.getMinutes()) + " " + ampm;
      },

      get syncText() {
        if (!this.sync.total) return "";
        if (!this.sync.pending) return "All consoles up to date";
        return "Updating " + this.sync.pending + " of " + this.sync.total + " consoles…";
      },

      /* ================= role gating: the ONE check ================= */

      can: function (capability) {
        var rule = CAPABILITIES[capability];
        var user = this.session.user;
        if (!rule || !user || user.role === "disabled") return false;
        if (this.call && this.call.restricted) return false;
        if (rule.console && rule.console !== this.consoleInfo.role) return false;
        return rule.roles.indexOf(user.role) !== -1;
      },

      /* ================= lifecycle ================= */

      init: function () {
        var self = this;
        this.poll();
        setInterval(function () { self.poll(); }, TIMERS.pollMs);
        setInterval(function () { self.tick(); }, 250);

        /* Any touch is activity; it also dismisses the screensaver (the
           overlay swallows that touch so it cannot press what is under it). */
        document.addEventListener("pointerdown", function () {
          self.lastActivity = Date.now();
        }, true);
      },

      tick: function () {
        this.nowS = Date.now() / 1000 + this.serverOffset;
        var idle = Date.now() - this.lastActivity;
        var view = this.view;

        /* An incoming ring always clears the screensaver. */
        if (this.screensaver && this.ringingLanes.length) this.screensaver = false;
        if (!this.screensaver && idle > TIMERS.screensaverMs && !this.call &&
            !this.ringingLanes.length && view !== "developer" && !this.kb.open) {
          this.screensaver = true;
        }

        if ((view === "settings" && idle > TIMERS.settingsIdleMs) ||
            ((view === "troubleshooting" || view === "help") && idle > TIMERS.troubleshootingIdleMs)) {
          this.screen = "main";
        }
        if (view === "developer" && idle > TIMERS.developerIdleMs) {
          this.exitDeveloper();
        }

        /* Auto-lock. Suppressed by an active call: a long quiet call must not
           time the operator out mid-call. */
        if (this.session.login_required && this.session.user && !this.call &&
            idle > this.session.autolock_minutes * 60 * 1000 && !this.lockSent) {
          this.lockSent = true;
          var self = this;
          this.post("/api/lock", {}).then(function () { self.screen = "main"; self.poll(); })
            .catch(function () {}).then(function () { self.lockSent = false; });
        }
      },

      wake: function () {
        this.screensaver = false;
        this.lastActivity = Date.now();
      },

      /* ================= backend ================= */

      request: function (path, options) {
        var controller = new AbortController();
        var timer = setTimeout(function () { controller.abort(); }, TIMERS.requestTimeoutMs);
        var init = Object.assign({ signal: controller.signal }, options || {});
        return fetch(path, init)
          .then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (body) {
              if (!response.ok) {
                var error = new Error(body.error || path + " -> " + response.status);
                error.status = response.status;
                throw error;
              }
              return body;
            });
          })
          .finally(function () { clearTimeout(timer); });
      },

      /* POST a command. A refusal's reason comes from the backend, written for
         the operator, and is shown in the status line. */
      post: function (path, body) {
        var self = this;
        return this.request(path, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body || {})
        }).catch(function (error) {
          self.say(error.message || "That didn't work");
          throw error;
        });
      },

      say: function (text) {
        this.notice = text;
        this.noticeUntil = Date.now() + TIMERS.noticeMs;
      },

      poll: function () {
        var self = this;
        this.request("/api/status")
          .then(function (data) { self.connected = true; self.applyStatus(data); })
          .catch(function () { self.connected = false; });
      },

      applyStatus: function (data) {
        if (!data || typeof data !== "object") return;
        if (typeof data.api === "number") this.backendApi = data.api;
        if (typeof data.now === "number") this.serverOffset = data.now - Date.now() / 1000;
        if (data.console && typeof data.console === "object") this.consoleInfo = data.console;
        if (Array.isArray(data.devices)) this.devices = data.devices;
        if ("call" in data) this.call = data.call;
        if (Array.isArray(data.outputs)) this.outputs = data.outputs;
        if (data.session && typeof data.session === "object") {
          var wasLocked = this.locked;
          this.session = data.session;
          if (!wasLocked && this.locked) this.onLocked();
        }
        if (data.sync) this.sync = data.sync;
        if (typeof data.service_panel === "boolean") this.servicePanel = data.service_panel;
        if (data.features) this.features = data.features;
        if (data.branding) this.branding = data.branding;
        if (typeof data.message === "string") this.message = data.message;

        /* v1 is Hamilton-branded only; a customer theme applies only once the
           backend turns customer branding on. */
        this.applyTheme(this.features.customer_branding ? (this.branding.theme || {}) : {});

        if (this.pendingLane && (this.call || !this.pendingLaneAlive())) this.pendingLane = "";
      },

      pendingLaneAlive: function () {
        return Date.now() - this.pendingAt < TIMERS.requestTimeoutMs;
      },

      /* Sets what the theme sets and removes what it no longer sets, so
         returning to the default restores the compiled colours. */
      applyTheme: function (tokens) {
        if (!tokens || typeof tokens !== "object") return;
        var sig = JSON.stringify(tokens);
        if (sig === this.themeSig) return;
        this.themeSig = sig;
        var root = document.documentElement.style;
        var applied = [];
        Object.keys(tokens).forEach(function (name) {
          var value = tokens[name];
          if (typeof value === "string" && /^[#a-zA-Z0-9(),.\s%-]+$/.test(value)) {
            root.setProperty("--color-" + name, value);
            applied.push(name);
          }
        });
        this.themeKeys.forEach(function (name) {
          if (applied.indexOf(name) === -1) root.removeProperty("--color-" + name);
        });
        this.themeKeys = applied;
      },

      /* ================= lanes ================= */

      laneVars: function (lane) { return laneVars(lane && lane.color); },

      laneState: function (lane) {
        return (lane && lane.call && lane.call.state) || "idle";
      },

      /* LED behaviour only (on/off/blink speed/brightness), never colour, so
         it reads the same for colour-blind operators. */
      ledClass: function (lane) {
        if (lane.health === "error") return "led-fault";
        if (lane.id === this.pendingLane) return "led-pending";
        return {
          idle: "led-off", ringing: "led-fast", active: "led-on",
          busy: "led-dim", held: "led-slow"
        }[this.laneState(lane)] || "led-off";
      },

      ledLabel: function (lane) {
        var name = "Lane " + lane.number;
        if (lane.health === "error") return name + ", out of service";
        return name + ", " + ({
          idle: "no call. Tap to call",
          ringing: "ringing. Tap to answer",
          active: "in your call",
          busy: "in a call on another console",
          held: "on hold. Tap to call back"
        }[this.laneState(lane)] || "");
      },

      /* One touch: answer if ringing, otherwise place a call. Rejoining a held
         lane is simply a new call. A busy lane does nothing but say why. */
      tapLane: function (lane) {
        var self = this;
        var st = this.laneState(lane);
        if (lane.health === "error") return this.say("Lane " + lane.number + " is out of service");
        if (st === "busy") return this.say("Lane " + lane.number + " is in a call on another console");
        if (st === "active" || this.pendingLane) return;
        this.pendingLane = lane.id;
        this.pendingAt = Date.now();
        this.post("/api/call", { lane: lane.id })
          .then(function () { self.poll(); })
          .catch(function () { self.pendingLane = ""; });
      },

      elapsed: function (since) {
        if (!since) return "";
        var s = Math.max(0, Math.floor(this.nowS - since));
        var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
        return (h ? h + ":" + pad2(m) : m) + ":" + pad2(s % 60);
      },

      /* ================= active call ================= */

      hangUp: function () {
        var self = this;
        this.post("/api/call/hangup", {}).then(function () { self.poll(); }).catch(function () {});
      },

      holdCall: function () {
        var self = this;
        this.post("/api/call/hold", {}).then(function () { self.poll(); }).catch(function () {});
      },

      toggleMic: function () {
        if (!this.call) return;
        this.call.mic_muted = !this.call.mic_muted;
        this.post("/api/call/mic", { muted: this.call.mic_muted }).catch(function () {});
      },

      nudgeVolume: function (delta) {
        if (!this.call) return;
        this.call.volume = clamp(this.call.volume + delta, 0, 100);
        this.post("/api/call/volume", { value: this.call.volume }).catch(function () {});
      },

      setOutput: function (id) {
        if (!this.call || this.call.output === id) return;
        this.call.output = id;
        this.post("/api/call/output", { id: id }).catch(function () {});
      },

      outputName: function (id) {
        var o = this.outputs.find(function (x) { return x.id === id; });
        return o ? o.name : id;
      },

      /* ================= locked screen ================= */

      onLocked: function () {
        this.screen = "main";
        var last = this.session.last_user;
        this.login = { username: last && last !== BUILTIN_USER ? last : "", valid: false,
                       pin: "", error: "", checking: false };
        if (this.login.username) this.checkUser(this.login.username);
      },

      editUsername: function () {
        var self = this;
        this.promptKeyboard({
          mode: "text", label: "Your name", value: this.login.username, max: 32,
          commit: function (value) { self.checkUser(value); }
        });
      },

      /* An unknown name is flagged at once, checked against the Master's list. */
      checkUser: function (name) {
        var self = this;
        name = (name || "").trim();
        this.login.username = name;
        this.login.valid = false;
        this.login.pin = "";
        this.login.error = "";
        if (!name) return;
        this.login.checking = true;
        this.post("/api/login/check", { username: name })
          .then(function (res) {
            self.login.valid = !!res.valid;
            if (!res.valid) self.login.error = "No active user named “" + name + "”";
          })
          .catch(function () {})
          .then(function () { self.login.checking = false; });
      },

      pinKey: function (digit) {
        if (!this.login.valid || this.login.pin.length >= this.session.pin_length) return;
        this.login.error = "";
        this.login.pin += digit;
        if (this.login.pin.length === this.session.pin_length) this.submitLogin();
      },

      pinBack: function () { this.login.pin = this.login.pin.slice(0, -1); },

      /* Unlimited attempts, no lockout; a wrong PIN just clears the dots. */
      submitLogin: function () {
        var self = this;
        this.request("/api/login", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username: this.login.username, pin: this.login.pin })
        }).then(function () {
          self.login.pin = "";
          self.screen = "main";
          self.poll();
        }).catch(function (e) {
          self.login.pin = "";
          self.login.error = e.message || "Incorrect PIN";
        });
      },

      /* ================= navigation ================= */

      go: function (screen) {
        if (screen === "settings" && !this.can("settings")) return;
        this.screen = screen;
        this.lastActivity = Date.now();
        if (screen === "settings") this.loadSettings();
        if (screen === "help") this.loadHelp();
        if (screen === "troubleshooting") this.chainSelected = "";
      },

      /* Hidden Developer entry: a long press on the logo, on the Locked and
         Troubleshooting screens only (the two paths the doc confirms). */
      logoPressStart: function () {
        var self = this;
        if (this.view !== "locked" && this.view !== "troubleshooting") return;
        clearTimeout(this.longPressTimer);
        this.longPressTimer = setTimeout(function () { self.enterDeveloper(); }, TIMERS.longPressMs);
      },

      logoPressEnd: function () { clearTimeout(this.longPressTimer); },

      confirm: function (options) {
        this.confirmBox = {
          open: true, title: options.title, body: options.body || "",
          okLabel: options.okLabel || "OK", danger: !!options.danger, action: options.action
        };
      },

      confirmOk: function () {
        var action = this.confirmBox.action;
        this.confirmBox.open = false;
        if (action) action();
      },

      /* Restarts the UI/software only; the audio system is untouched. */
      askReboot: function () {
        var self = this;
        if (this.call) return this.say("Hang up before restarting");
        this.confirm({
          title: "Restart this console's software?",
          body: "The screen restarts. Calls and the audio system are not affected.",
          okLabel: "Restart", danger: true,
          action: function () {
            self.post("/api/reboot", {}).then(function () {
              self.restarting = true;
              setTimeout(function () { window.location.reload(); }, 4000);
            }).catch(function () {});
          }
        });
      },

      /* ================= troubleshooting ================= */

      healthIcon: function (health) {
        return { ok: "✓", warning: "!", error: "✕" }[health] || "?";
      },

      /* Where a chain problem starts: the first device in chain order that is
         in error. Everything after it may be failing only because of it. */
      get chainOrigin() {
        var first = this.chain.find(function (d) { return d.health === "error"; });
        return first ? first.id : "";
      },

      get chainSelectedDevice() {
        var id = this.chainSelected;
        return this.devices.find(function (d) { return d.id === id; }) || null;
      },

      deviceName: function (d) {
        if (!d) return "";
        if (d.kind === "lane") return "Lane " + d.number;
        var n = d.id.replace(/^\D+/, "");
        return "Console " + n + (d.role === "master" ? " (Master)" : "") +
               (d.id === this.consoleInfo.id ? " · this one" : "");
      },

      exportLog: function () {
        var self = this;
        this.post("/api/logs/export", {}).then(function (res) {
          self.say("Log saved: " + (res.file || "done"));
        }).catch(function () {});
      },

      restartLaneAudio: function (lane) {
        var self = this;
        this.confirm({
          title: "Restart audio on Lane " + lane.number + "?",
          body: "Restarts that lane's audio hardware over the A2B chain. Any call on it is dropped.",
          okLabel: "Restart lane audio", danger: true,
          action: function () {
            self.post("/api/lane/restart-audio", { lane: lane.id })
              .then(function () { self.say("Restarting audio on Lane " + lane.number); self.poll(); })
              .catch(function () {});
          }
        });
      },

      loadHelp: function () {
        var self = this;
        this.help.doc = null;
        this.help.page = 0;
        this.request("/api/help").then(function (res) { self.help.docs = res.docs || []; })
          .catch(function () { self.help.docs = []; });
      },

      openHelp: function (doc) { this.help.doc = doc; this.help.page = 0; },

      /* ================= developer ================= */

      enterDeveloper: function () {
        var self = this;
        var from = this.view;
        this.promptKeyboard({
          mode: "text", label: "Developer password", secret: true, max: 32,
          commit: function (value) {
            self.request("/api/auth", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ scope: "developer", password: value })
            }).then(function () {
              self.dev = { unlocked: true, mode: null, returnTo: from, log: [] };
              self.lastActivity = Date.now();
            }).catch(function () { self.say("Incorrect developer password"); });
          }
        });
      },

      /* One destination per visit: switching means leaving and re-entering
         the password, on purpose. */
      chooseDev: function (mode) {
        this.dev.mode = mode;
        if (mode === "applog") this.pollAppLog();
      },

      pollAppLog: function () {
        var self = this;
        if (!this.dev.unlocked || this.dev.mode !== "applog") return;
        this.request("/api/app-log").then(function (res) {
          self.dev.log = res.entries || [];
          self.$nextTick(function () {
            var box = self.$refs.applog;
            if (box) box.scrollTop = box.scrollHeight;
          });
        }).catch(function () {});
        setTimeout(function () { self.pollAppLog(); }, 2000);
      },

      exitDeveloper: function () {
        var back = this.dev.returnTo === "troubleshooting" ? "troubleshooting" : "main";
        this.dev = { unlocked: false, mode: null, returnTo: "troubleshooting", log: [] };
        this.screen = back;
      },

      /* ================= settings (Master only) ================= */

      loadSettings: function () {
        var self = this;
        this.settingsResult = "";
        this.request("/api/settings").then(function (res) {
          self.settings = res;
          if (!self.settingsConsole && res.consoles && res.consoles.length) {
            self.settingsConsole = res.consoles[0].id;
          }
          if (self.settingsTab === "users" && !self.can("manage_users")) self.settingsTab = "lanes";
        }).catch(function (e) { self.say(e.message); self.screen = "main"; });
      },

      saveSetting: function (path, body, done) {
        var self = this;
        return this.post(path, body).then(function (res) {
          self.loadSettings();
          self.poll();
          if (done) done(res);
        }).catch(function () {});
      },

      get settingsConsoleObj() {
        var id = this.settingsConsole;
        return (this.settings && this.settings.consoles || []).find(function (c) { return c.id === id; }) || null;
      },

      editLaneNumber: function (lane) {
        var self = this;
        this.promptKeyboard({
          mode: "number", label: "Number for this lane (1-16)", value: String(lane.number), max: 2,
          commit: function (value) {
            var n = parseInt(value, 10);
            if (!(n >= 1 && n <= 16)) return self.say("Lane numbers are 1 to 16");
            self.saveSetting("/api/settings/lane", { lane: lane.id, number: n });
          }
        });
      },

      pickLaneColor: function (lane) {
        var self = this;
        this.picker = {
          open: true, kind: "color", title: "Colour for Lane " + lane.number, value: lane.color,
          options: LANE_COLOR_ORDER.map(function (id) {
            var users = (self.settings.lanes || []).filter(function (l) { return l.color === id && l.id !== lane.id; })
              .map(function (l) { return "Lane " + l.number; });
            return { id: id, label: id, hint: users.join(", ") };
          }),
          commit: function (id) { self.saveSetting("/api/settings/lane", { lane: lane.id, color: id }); }
        };
      },

      pickerChoose: function (id) {
        var commit = this.picker.commit;
        this.picker.open = false;
        if (commit) commit(id);
      },

      setConsoleDefault: function (field, value) {
        var body = { console: this.settingsConsole };
        body[field] = value;
        this.saveSetting("/api/settings/console", body);
      },

      autolockText: function (mins) {
        var h = Math.floor(mins / 60), m = mins % 60;
        return (h ? h + " h" : "") + (h && m ? " " : "") + (m ? m + " min" : "");
      },

      nudgeAutolock: function (delta) {
        var v = clamp(this.settings.site.autolock_minutes + delta, 15, 480);
        this.saveSetting("/api/settings/site", { autolock_minutes: v });
      },

      setLoginRequired: function (on) {
        var self = this;
        if (!on) return this.saveSetting("/api/settings/site", { login_required: false });
        this.confirm({
          title: "Require login on every console?",
          body: "Tellers will sign in with their name and a 4-digit PIN. Consoles lock after " +
                this.autolockText(this.settings.site.autolock_minutes) + " without activity.",
          okLabel: "Require login",
          action: function () { self.saveSetting("/api/settings/site", { login_required: true }); }
        });
      },

      pickRole: function (title, current, includeDisabled, commit) {
        var roles = USER_ROLES.slice().reverse();
        if (includeDisabled) roles.push("disabled");
        this.picker = {
          open: true, kind: "role", title: title, value: current,
          options: roles.map(function (r) {
            return { id: r, label: ROLE_LABELS[r],
                     hint: r === "disabled" ? "No access to anything; the record is kept" : "" };
          }),
          commit: commit
        };
      },

      /* New user: name, then a 4-digit PIN, then a role. Admin/Owner only. */
      addUser: function () {
        var self = this;
        this.promptKeyboard({
          mode: "text", label: "New user's name", max: 32,
          commit: function (name) {
            name = (name || "").trim();
            if (!name) return;
            self.promptKeyboard({
              mode: "pin", label: "4-digit PIN for " + name, secret: true, max: 4,
              commit: function (pin) {
                if (!/^\d{4}$/.test(pin)) return self.say("A PIN is exactly 4 digits");
                self.pickRole("Role for " + name, "user", false, function (role) {
                  self.saveSetting("/api/users", { name: name, pin: pin, role: role }, function () {
                    self.say(name + " added as " + ROLE_LABELS[role]);
                  });
                });
              }
            });
          }
        });
      },

      /* Accounts are never deleted: removing access means Disabled. */
      changeRole: function (u) {
        var self = this;
        this.pickRole("Role for " + u.name, u.role, true, function (role) {
          self.saveSetting("/api/users/update", { name: u.name, role: role });
        });
      },

      resetPin: function (u) {
        var self = this;
        this.promptKeyboard({
          mode: "pin", label: "New 4-digit PIN for " + u.name, secret: true, max: 4,
          commit: function (pin) {
            if (!/^\d{4}$/.test(pin)) return self.say("A PIN is exactly 4 digits");
            self.saveSetting("/api/users/update", { name: u.name, pin: pin }, function () {
              self.say("PIN reset for " + u.name);
            });
          }
        });
      },

      saveConfig: function () {
        var self = this;
        this.saveSetting("/api/config/save", {}, function (res) {
          self.settingsResult = "Saved: " + (res.file || "configuration backed up");
        });
      },

      restoreConfig: function () {
        var self = this;
        this.confirm({
          title: "Restore the saved configuration?",
          body: "Lanes, console defaults, users and site settings are replaced with the backup and sent to every console.",
          okLabel: "Restore", danger: true,
          action: function () {
            self.saveSetting("/api/config/restore", {}, function () {
              self.settingsResult = "Configuration restored";
            });
          }
        });
      },

      /* ---- customer branding tab (later version) ---- */
      brandHues: [
        { name: "Navy", hex: "#2B2A5E" }, { name: "Royal", hex: "#1A4F9C" },
        { name: "Sky", hex: "#0E6FB2" }, { name: "Teal", hex: "#0E7A88" },
        { name: "Green", hex: "#1F8A4C" }, { name: "Forest", hex: "#14562E" },
        { name: "Burgundy", hex: "#7A1F3D" }, { name: "Red", hex: "#C0392B" },
        { name: "Orange", hex: "#C15A12" }, { name: "Bronze", hex: "#8A6D2A" },
        { name: "Purple", hex: "#5B3E9B" }, { name: "Graphite", hex: "#33343A" }
      ],
      brandShades: [{ id: "light", label: "Light" }, { id: "base", label: "Base" }, { id: "dark", label: "Dark" }],

      setBrand: function (baseHex, shade) {
        var theme = deriveBrandTheme(baseHex, shade);
        if (!Object.keys(theme).length) return;
        this.saveSetting("/api/brand", { base: baseHex, shade: shade, theme: theme });
      },

      shadeSwatch: function (baseHex, shade) { return deriveBrandTheme(baseHex, shade).navy || baseHex; },

      editTitle: function () {
        var self = this;
        this.promptKeyboard({
          mode: "text", label: "Screen title", value: this.branding.title, max: 31,
          commit: function (value) { self.saveSetting("/api/title", { text: value }); }
        });
      },

      /* ================= on-screen keyboard ================= */

      promptKeyboard: function (options) {
        this.kb = {
          open: true, mode: options.mode || "number", label: options.label || "",
          value: options.value || "", secret: !!options.secret,
          commit: options.commit || null, max: options.max || 32
        };
      },

      kbKey: function (key) {
        if (this.kb.value.length < this.kb.max) this.kb.value += key;
      },

      kbBackspace: function () { this.kb.value = this.kb.value.slice(0, -1); },

      kbCancel: function () { this.kb.open = false; this.kb.commit = null; },

      kbAccept: function () {
        var commit = this.kb.commit, value = this.kb.value;
        this.kb.open = false;
        this.kb.commit = null;
        if (commit) commit(value);
      },

      get kbDisplay() {
        return this.kb.secret ? "•".repeat(this.kb.value.length) : this.kb.value;
      },

      kbRows: function () {
        if (this.kb.mode === "number" || this.kb.mode === "pin") {
          return [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"], ["0"]];
        }
        if (this.kb.mode === "hex") {
          return [["0", "1", "2", "3", "4", "5", "6", "7"], ["8", "9", "A", "B", "C", "D", "E", "F"]];
        }
        return [
          ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
          ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
          ["A", "S", "D", "F", "G", "H", "J", "K", "L", "'"],
          ["Z", "X", "C", "V", "B", "N", "M", "-", ".", " "]
        ];
      }
    };
  });
});

/* Held-down feedback, delegated once for every button on the page. Not
 * :active, which sticks on some touch panels when a press is cancelled
 * mid-gesture. Taps bind to pointerup in the markup (click is unreliable under
 * WPE), with Alpine's .throttle modifier as the duplicate-event guard. */
document.addEventListener("pointerdown", function (event) {
  var button = event.target.closest("button");
  if (button && !button.disabled) button.classList.add("is-pressed");
});

["pointerup", "pointercancel", "pointerleave"].forEach(function (name) {
  document.addEventListener(name, function (event) {
    var button = event.target.closest && event.target.closest("button");
    if (button) button.classList.remove("is-pressed");
  });
});
