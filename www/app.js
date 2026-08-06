/* Operator Console — Alpine component.
 *
 * Loaded before vendor/alpine.min.js (both deferred, so document order is
 * execution order) and registers the component on alpine:init.
 *
 * State model: audio is console-wide, because the hardware is — one amplifier
 * mute, one microphone enable, one master volume. A lane is a routing
 * destination. Selecting a lane opens the mic and speakers; releasing it (by
 * tapping it again) closes them. Nothing is per-lane.
 *
 * The backend is the authority. Controls are optimistic: a tap updates local
 * state, renders, then POSTs. GET /api/status once a second reconciles, so a
 * rejected or failed command self-corrects within a poll interval rather than
 * leaving the UI lying about the amplifier.
 */

document.addEventListener("alpine:init", function () {
  Alpine.data("operatorConsole", function () {
    return {
      uiVersion: "0.5.0",

      pollMs: 1000,
      requestTimeoutMs: 2000,
      meterSegments: 24,
      tileSegments: 12,
      volumeStep: 5,
      brightnessStep: 10,

      /* At least one lane per console; 8 is the panel's practical ceiling
         before lane buttons stop being comfortable finger targets. */
      minLanes: 1,
      maxLanes: 8,

      connected: false,

      /* Lanes are just a count — there is no per-lane state to hold. */
      laneCount: 4,
      activeLaneId: 0,   /* 0 = no lane selected */

      /* Console-wide audio. */
      mic: false,
      mute: true,        /* matches the config.txt boot state */
      level: 0,

      /* Where the mic/speakers/volume cluster sits: "left", "right" or
         "bottom". Operator-configurable, because the panel does not always sit
         square in front of them — mounted to one side, reach is not symmetric.
         Only the flex direction of <main> changes; the markup is identical in
         all three. */
      controlsPosition: "right",
      controlsPositions: ["left", "right", "bottom"],

      volume: 60,
      brightness: 80,
      message: "Starting…",
      settingsOpen: false,
      backendHost: window.location.host,

      init: function () {
        this.poll();
        setInterval(this.poll.bind(this), this.pollMs);
      },

      /* True while a lane is held. The mic and speaker controls do nothing
         without one — there is nothing routed to open a microphone into. */
      get hasLane() {
        return this.activeLaneId >= 1;
      },

      /* Column count that squares up best for the current lane count. Paired
         with the flex-grow rule in input.css, a final row holding fewer lanes
         than this stretches to fill the width, so odd counts read as
         deliberate instead of ragged.

         Two mappings, because the control position changes the shape of the
         lane area: with controls at the bottom it is wide and short, so lanes
         can spread out; with controls at a side it is narrower and taller, so
         fewer columns keeps the tiles from going skinny. */
      get laneColumns() {
        var wide = { 1: 1, 2: 2, 3: 3, 4: 2, 5: 3, 6: 3, 7: 4, 8: 4 };
        var narrow = { 1: 1, 2: 2, 3: 2, 4: 2, 5: 2, 6: 3, 7: 3, 8: 3 };

        var byCount = this.controlsPosition === "bottom" ? wide : narrow;
        return byCount[this.laneCount] || Math.ceil(Math.sqrt(this.laneCount));
      },

      /* True only when the control cluster is actually a bottom rail. At a
         single lane the lane grid is hidden, so there is nothing for the rail
         to sit below — it would strand itself at the top of an empty screen.
         The cluster falls back to the stacked, centred card instead. */
      get railMode() {
        return this.controlsPosition === "bottom" && this.laneCount > 1;
      },

      /* ---------- Meter ---------- */

      /* Meter inside a lane tile. Only the held lane can have a level: there
         is one microphone, so an unselected lane is not carrying anything. */
      tileSegmentClass: function (laneId, index) {
        var lit = (laneId === this.activeLaneId && !this.mute)
                    ? Math.round((this.level / 100) * this.tileSegments)
                    : 0;

        if (index >= lit) {
          return laneId === this.activeLaneId ? "bg-navy-d" : "bg-line-d";
        }
        return index / this.tileSegments >= 0.75 ? "bg-[#E4A034]" : "bg-[#3FBF77]";
      },

      meterSegmentClass: function (index) {
        /* A muted amplifier shows no level whatever the input is doing — the
           meter reports what reaches the speakers, not what reaches the ADC. */
        var level = (this.hasLane && !this.mute) ? this.level : 0;
        var lit = Math.round((level / 100) * this.meterSegments);

        if (index >= lit) return "bg-line-d";

        var position = index / this.meterSegments;
        if (position >= 0.85) return "bg-danger";
        if (position >= 0.7) return "bg-warn";
        return "bg-on";
      },

      /* ---------- Backend ---------- */

      /* Same-origin against 127.0.0.1, and must fail fast: a hung request would
         stack up behind the poll interval. */
      request: function (path, options) {
        var controller = new AbortController();
        var timer = setTimeout(function () {
          controller.abort();
        }, this.requestTimeoutMs);

        var init = Object.assign({ signal: controller.signal }, options || {});

        return fetch(path, init)
          .then(function (response) {
            if (!response.ok) throw new Error(path + " -> " + response.status);
            return response.json();
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
          /* Surface it rather than silently diverging from the hardware; the
             next poll re-syncs the control to its real state. */
          self.message = "Command failed: " + path;
          console.error(error);
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
            self.message = "No response from backend";
          });
      },

      /* Take only the fields the backend actually sent, so a partial response
         never blanks a control. */
      applyStatus: function (data) {
        if (!data || typeof data !== "object") return;

        if (typeof data.lanes === "number") {
          this.laneCount = clamp(data.lanes, this.minLanes, this.maxLanes);
        }
        if (typeof data.lane === "number")       this.activeLaneId = data.lane;
        if (typeof data.mic === "boolean")       this.mic = data.mic;
        if (typeof data.mute === "boolean")      this.mute = data.mute;
        if (typeof data.level === "number")      this.level = clamp(data.level, 0, 100);
        if (typeof data.volume === "number")     this.volume = clamp(data.volume, 0, 100);
        if (typeof data.brightness === "number") this.brightness = clamp(data.brightness, 0, 100);
        if (typeof data.message === "string")    this.message = data.message;

        if (this.controlsPositions.indexOf(data.layout) !== -1) {
          this.controlsPosition = data.layout;
        }
      },

      /* ---------- Actions ---------- */

      /* Selecting a lane opens the mic and speakers; tapping the lane already
         held releases it and closes them. The backend owns that rule — the UI
         posts the tap and lets the next poll tell it what happened. */
      selectLane: function (id) {
        var release = (id === this.activeLaneId);

        this.activeLaneId = release ? 0 : id;
        this.mic  = !release;
        this.mute = release;
        this.message = release ? "No lane selected" : "Lane " + id + " live";

        this.post("/api/lane", { lane: release ? 0 : id });
      },

      toggleMic: function () {
        if (!this.hasLane) return;

        this.mic = !this.mic;
        this.message = "Lane " + this.activeLaneId + " mic " +
                       (this.mic ? "live" : "off");
        this.post("/api/mic", { on: this.mic });
      },

      toggleMute: function () {
        if (!this.hasLane) return;

        this.mute = !this.mute;
        this.message = "Lane " + this.activeLaneId + " speakers " +
                       (this.mute ? "muted" : "on");
        this.post("/api/mute", { on: this.mute });
      },

      nudgeVolume: function (delta) {
        this.volume = clamp(this.volume + delta, 0, 100);
        this.post("/api/volume", { value: this.volume });
      },

      nudgeBrightness: function (delta) {
        this.brightness = clamp(this.brightness + delta, 0, 100);
        this.post("/api/brightness", { value: this.brightness });
      },

      /* Changing the site's lane count. The backend owns the real value and
         releases the held lane if it disappears. */
      setLaneCount: function (count) {
        count = clamp(count, this.minLanes, this.maxLanes);
        if (count === this.laneCount) return;

        this.laneCount = count;
        if (this.activeLaneId > count) {
          this.activeLaneId = 0;
          this.mic = false;
          this.mute = true;
        }

        this.message = count + (count === 1 ? " lane" : " lanes") + " configured";
        this.post("/api/lanes", { count: count });
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
 * mid-gesture. Kept out of Alpine because it's presentation only — routing it
 * through component state would re-render the tree on every press.
 *
 * Note this is only the visual press. Taps themselves bind to pointerup in the
 * markup (click is unreliable under WPE) with Alpine's .throttle modifier as
 * the duplicate-event guard, so one tap acts exactly once. */
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
