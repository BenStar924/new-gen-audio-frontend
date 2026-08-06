/* Operator Console — Alpine component.
 *
 * Loaded before vendor/alpine.min.js (both deferred, so document order is
 * execution order) and registers the component on alpine:init.
 *
 * State model: the console owns one or more lanes. Mic and speaker state are
 * per-lane and the controls act on whichever lane is active. Volume is NOT
 * per-lane — there is a single master volume for the console, so it lives in
 * console state and is posted without a lane id.
 *
 * The backend is the authority. Controls are optimistic: a tap updates local
 * state, renders, then POSTs. GET /api/status once a second reconciles, so a
 * rejected or failed command self-corrects within a poll interval rather than
 * leaving the UI lying about the amplifier.
 */

document.addEventListener("alpine:init", function () {
  Alpine.data("operatorConsole", function () {
    return {
      uiVersion: "0.4.0",

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
      lanes: [],

      /* Where the mic/speakers/volume cluster sits: "left", "right" or
         "bottom". Operator-configurable, because the panel does not always sit
         square in front of them — mounted to one side, reach is not symmetric.
         Only the flex direction of <main> changes; the markup is identical in
         all three. */
      controlsPosition: "right",
      controlsPositions: ["left", "right", "bottom"],

      /* Master volume. One per console, not one per lane. */
      volume: 60,

      activeLaneId: 1,
      brightness: 80,
      message: "Starting…",
      settingsOpen: false,
      backendHost: window.location.host,

      init: function () {
        /* Render something coherent before the first poll lands. Muted matches
           the config.txt boot state (gpio=25=op,dh — GPIO25 is an active-high
           mute), so the UI agrees with the hardware at startup instead of
           contradicting it for the first second. */
        this.lanes = [this.blankLane(1)];

        this.poll();
        setInterval(this.poll.bind(this), this.pollMs);
      },

      blankLane: function (id) {
        return { id: id, mic: false, mute: true, level: 0 };
      },

      /* The lane the controls act on. Never undefined: a status response that
         drops the active lane (because the count shrank) would otherwise blank
         every binding in the audio card. */
      get active() {
        var found = this.lanes.find(function (lane) {
          return lane.id === this.activeLaneId;
        }, this);

        return found || this.lanes[0] || this.blankLane(this.activeLaneId);
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
        return byCount[this.lanes.length] || Math.ceil(Math.sqrt(this.lanes.length));
      },

      /* ---------- Meter ---------- */

      /* Small meter inside each lane tile. Same rule as the big meter, applied
         to any lane rather than the active one — that is what makes a live
         lane visible without selecting it. */
      tileSegmentClass: function (lane, index) {
        var level = lane.mute ? 0 : lane.level;
        var lit = Math.round((level / 100) * this.tileSegments);
        var isActive = lane.id === this.activeLaneId;

        if (index >= lit) return isActive ? "bg-navy-d" : "bg-line-d";
        if (index / this.tileSegments >= 0.75) {
          return isActive ? "bg-[#E4A034]" : "bg-warn";
        }
        return isActive ? "bg-[#3FBF77]" : "bg-on";
      },

      meterSegmentClass: function (index) {
        /* A muted amplifier shows no level whatever the input is doing — the
           meter reports what reaches the speakers, not what reaches the ADC. */
        var level = this.active.mute ? 0 : this.active.level;
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

        if (Array.isArray(data.lanes) && data.lanes.length) {
          this.lanes = data.lanes.map(function (lane, index) {
            var base = this.blankLane(typeof lane.id === "number" ? lane.id : index + 1);

            if (typeof lane.mic === "boolean") base.mic = lane.mic;
            if (typeof lane.mute === "boolean") base.mute = lane.mute;
            if (typeof lane.level === "number") base.level = clamp(lane.level, 0, 100);

            return base;
          }, this);
        }

        if (typeof data.lane === "number") this.activeLaneId = data.lane;
        if (typeof data.volume === "number") this.volume = clamp(data.volume, 0, 100);
        if (this.controlsPositions.indexOf(data.layout) !== -1) {
          this.controlsPosition = data.layout;
        }
        if (typeof data.brightness === "number") this.brightness = clamp(data.brightness, 0, 100);
        if (typeof data.message === "string") this.message = data.message;

        this.ensureActiveLaneExists();
      },

      /* The lane count is operator-editable, so the active lane can vanish from
         under the controls. Fall back to the highest remaining lane. */
      ensureActiveLaneExists: function () {
        var exists = this.lanes.some(function (lane) {
          return lane.id === this.activeLaneId;
        }, this);

        if (!exists && this.lanes.length) {
          this.activeLaneId = this.lanes[this.lanes.length - 1].id;
        }
      },

      /* ---------- Actions ---------- */

      selectLane: function (id) {
        this.activeLaneId = id;
        this.message = "Lane " + id + " selected";
        this.post("/api/lane", { lane: id });
      },

      setControlsPosition: function (position) {
        if (this.controlsPositions.indexOf(position) === -1) return;

        this.controlsPosition = position;
        this.message = "Controls moved to " + position;
        this.post("/api/layout", { position: position });
      },

      toggleMic: function () {
        var lane = this.active;
        lane.mic = !lane.mic;
        this.message = "Lane " + lane.id + " mic " + (lane.mic ? "live" : "off");
        this.post("/api/mic", { lane: lane.id, on: lane.mic });
      },

      toggleMute: function () {
        var lane = this.active;
        lane.mute = !lane.mute;
        this.message = "Lane " + lane.id + " speakers " + (lane.mute ? "muted" : "on");
        this.post("/api/mute", { lane: lane.id, on: lane.mute });
      },

      /* Master volume — console-wide, so no lane id goes with it. */
      nudgeVolume: function (delta) {
        this.volume = clamp(this.volume + delta, 0, 100);
        this.post("/api/volume", { value: this.volume });
      },

      nudgeBrightness: function (delta) {
        this.brightness = clamp(this.brightness + delta, 0, 100);
        this.post("/api/brightness", { value: this.brightness });
      },

      /* Changing the site's lane count. The backend owns the real list — it has
         to add or drop hardware state — so this posts the count and lets the
         next poll deliver the authoritative lanes. */
      setLaneCount: function (count) {
        count = clamp(count, this.minLanes, this.maxLanes);
        if (count === this.lanes.length) return;

        if (count < this.lanes.length) {
          this.lanes = this.lanes.slice(0, count);
        } else {
          while (this.lanes.length < count) {
            this.lanes.push(this.blankLane(this.lanes.length + 1));
          }
        }

        this.ensureActiveLaneExists();
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
