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

/* ---------- brand colour derivation ----------
 *
 * The installer picks a hue and a shade; the console derives the whole palette
 * from it and — crucially — chooses the on-chrome text colour so labels are
 * never unreadable. That is what lets a customer have their brand colour
 * WITHOUT anyone producing an illegible screen: legibility is guaranteed by
 * construction, not by trusting whoever picked the colour.
 *
 * A light shade becomes a pale masthead with dark text and a strong active
 * lane; base and dark shades are darkened until white text clears WCAG 4.5:1. */

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

var BRAND_WHITE = [255, 255, 255], BRAND_BLACK = [0, 0, 0];

/* Darken a colour until white text on it reaches 4.5:1. */
function darkenForWhite(rgb) {
  var r = rgb, guard = 0;
  while (contrast(r, BRAND_WHITE) < 4.5 && guard < 40) { r = mix(r, BRAND_BLACK, 0.06); guard++; }
  return r;
}

/* A dark ink for text on a pale chrome. */
function inkFor(chrome) {
  var ink = mix(chrome, BRAND_BLACK, 0.82);
  if (contrast(ink, chrome) < 4.5) ink = [17, 17, 17];
  return ink;
}

/* base hex + shade -> the six chrome/lane tokens (everything else stays the
   default light field). Returns an object keyed WITHOUT the --color- prefix,
   which applyTheme adds. */
function deriveBrandTheme(baseHex, shade) {
  var rgb = hexToRgb(baseHex);
  if (!rgb) return {};

  var chrome, onDark, self;
  if (shade === "light") {
    chrome = mix(rgb, BRAND_WHITE, 0.78);   /* pale wash of the hue */
    onDark = true;                          /* so text goes dark */
    self = darkenForWhite(rgb);             /* active lane keeps a strong hue */
  } else {
    chrome = darkenForWhite(rgb);
    if (shade === "dark") chrome = mix(chrome, BRAND_BLACK, 0.28);
    onDark = false;
    self = chrome;
  }

  return {
    navy: rgbToHex(chrome),
    "navy-d": rgbToHex(mix(chrome, BRAND_BLACK, 0.35)),
    "navy-tint": onDark ? rgbToHex(mix(rgb, BRAND_BLACK, 0.2))
                        : rgbToHex(mix(chrome, BRAND_WHITE, 0.6)),
    onchrome: onDark ? rgbToHex(inkFor(chrome)) : "#FFFFFF",
    "lane-self": rgbToHex(self),
    "lane-self-line": rgbToHex(mix(self, BRAND_BLACK, 0.35)),
  };
}

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

      /* Brand colour. `base` is the chosen hue (empty = Hamilton default),
         `shade` is light | base | dark. The palette is DERIVED from these, not
         painted by hand, so it is always legible. Marketing approves the hue
         set; the shade ramp and the contrast guarantee are the engineering. */
      brand: { base: "", shade: "base" },
      brandHue: "",            /* which hue's shade ramp is on screen */
      hues: [
        { name: "Navy",     hex: "#2B2A5E" }, { name: "Royal",  hex: "#1A4F9C" },
        { name: "Sky",      hex: "#0E6FB2" }, { name: "Teal",   hex: "#0E7A88" },
        { name: "Green",    hex: "#1F8A4C" }, { name: "Forest", hex: "#14562E" },
        { name: "Burgundy", hex: "#7A1F3D" }, { name: "Red",    hex: "#C0392B" },
        { name: "Orange",   hex: "#C15A12" }, { name: "Bronze", hex: "#8A6D2A" },
        { name: "Purple",   hex: "#5B3E9B" }, { name: "Graphite", hex: "#33343A" },
      ],
      shades: [
        { id: "light", label: "Light" },
        { id: "base",  label: "Base" },
        { id: "dark",  label: "Dark" },
      ],

      tab: 0,
      message: "Starting…",

      /* A refusal has to survive the next poll. The backend's own message is
         whatever is true right now ("No lane selected"), so without this a
         "lane is in use" notice is wiped inside a second and the tap reads as
         a broken screen — the exact thing saying why was meant to prevent. */
      notice: "",
      noticeUntil: 0,
      noticeMs: 4000,

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

      /* The status line. Re-evaluated on every poll, so an expired notice
         gives way to the backend's own message within a second. */
      get statusLine() {
        if (this.notice && Date.now() < this.noticeUntil) return this.notice;
        return this.message;
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
          /* Includes the Master Controller's reason for refusing a lane, which
             arrives as the body of a 409. */
          self.say(error.message || ("Command failed: " + path));
          throw error;
        });
      },

      /* Shows text in the status line for a few seconds, outranking whatever
         the backend reports in the meantime. */
      say: function (text) {
        this.notice = text;
        this.noticeUntil = Date.now() + this.noticeMs;
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
            /* Losing the backend outranks any notice still on screen — it is
               the more important thing to be telling the operator. */
            self.notice = "";
            self.message = "No response from backend";
          });
      },

      applyStatus: function (data) {
        if (!data || typeof data !== "object") return;

        /* Expiry has to be an assignment, not just a comparison in the getter:
           Alpine re-runs an effect when a value it read changes, and a clock
           passing a deadline changes nothing it is watching. */
        if (this.notice && Date.now() >= this.noticeUntil) this.notice = "";

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

        /* The stored brand selection, so the picker shows what is applied.
           Not overwritten while the keyboard is open, so an in-progress custom
           entry is not yanked out from under the installer by a poll. */
        if (data.brand && typeof data.brand === "object" && !this.kb.open) {
          this.brand = {
            base: typeof data.brand.base === "string" ? data.brand.base : "",
            shade: typeof data.brand.shade === "string" ? data.brand.shade : "base",
          };
          if (!this.brandHue) this.brandHue = this.brand.base;
        }

        /* Lane count can shrink under us, leaving the tab out of range. */
        if (this.tab >= this.tabCount) this.tab = this.tabCount - 1;
      },

      /* ---------- lane actions ---------- */

      selectLane: function (lane) {
        var self = this;

        if (lane.status === "busy" || lane.status === "error") {
          /* Say why rather than doing nothing — a dead tap reads as a broken
             screen. */
          this.say(lane.status === "busy"
            ? lane.label + " is in use by " + (lane.owner || "another console")
            : lane.label + " is out of service");
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

      /* ---------- brand colour ---------- */

      /* The hue whose shade ramp is shown: the last one tapped, else the
         applied brand, else Hamilton navy. */
      get activeHue() {
        return this.brandHue || this.brand.base || this.hues[0].hex;
      },

      isHue: function (hex) {
        return this.brand.base.toLowerCase() === hex.toLowerCase();
      },

      isShade: function (shade) {
        return this.isHue(this.activeHue) && this.brand.shade === shade;
      },

      /* The chrome colour a given hue+shade produces — used to paint the ramp
         swatches so they preview the real result, pale to deep. */
      shadeSwatch: function (baseHex, shade) {
        return deriveBrandTheme(baseHex, shade).navy || baseHex;
      },

      /* The text colour that swatch needs, so the label on it stays readable. */
      shadeInk: function (baseHex, shade) {
        return deriveBrandTheme(baseHex, shade).onchrome || "#FFFFFF";
      },

      /* Tapping a hue applies it, keeping the current shade, and reveals its
         ramp so the shade can be changed. */
      pickHue: function (hex) {
        this.brandHue = hex;
        this.setBrand(hex, this.brand.shade || "base");
      },

      /* Derive, apply immediately (so the change is instant), then tell the
         backend, which stores the selection and the derived palette. */
      setBrand: function (baseHex, shade) {
        var theme = deriveBrandTheme(baseHex, shade);
        if (!Object.keys(theme).length) return;   /* not a colour */

        this.applyTheme(theme);
        this.brand = { base: baseHex, shade: shade };
        this.brandHue = baseHex;
        this.post("/api/brand", { base: baseHex, shade: shade, theme: theme })
          .catch(function () {});
      },

      /* Exact brand match: type a hex. The same derivation runs, so a custom
         colour is as legible as a preset. */
      customBrand: function () {
        var self = this;
        this.promptKeyboard({
          mode: "hex",
          label: "Brand colour hex (e.g. 1F5FB0)",
          value: "",                       /* start fresh; typing 6 chars is quick */
          commit: function (value) {
            var hex = "#" + String(value).replace(/[^0-9a-fA-F]/g, "").slice(0, 6);
            if (!hexToRgb(hex)) { self.say("Enter a 6-digit hex colour"); return; }
            self.setBrand(hex, self.brand.shade || "base");
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
        if (this.kb.mode === "hex") {
          /* A colour needs digits AND A–F; neither of the other layouts has
             both. */
          return [
            ["0", "1", "2", "3", "4", "5", "6", "7"],
            ["8", "9", "A", "B", "C", "D", "E", "F"]
          ];
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
