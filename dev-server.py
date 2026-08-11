#!/usr/bin/env python3
"""Mock Operator Console backend for front-end development.

Stands in for the libmicrohttpd C app: serves www/ and implements the same
/api contract against in-memory state (see README.md). Lets the UI be developed
and demoed on a laptop, or on the bench card before the C backend exists.

    python3 dev-server.py [port] [--lanes N] [--layout left|right|bottom]

Then open http://127.0.0.1:8000 — or point the kiosk at it:

    cog http://127.0.0.1:8000

It also simulates what only a multi-console site can really produce: other
consoles holding and parking lanes, and a lane in a fault state. Those are
driven by /api/sim so the UI can be tested against every lane state without a
Master Controller present.

This is a development tool. It is not deployed to the device.
"""

import json
import math
import os
import sys
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler

WWW_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www")

MIN_LANES = 1
MAX_LANES = 12
LAYOUTS = ("left", "right", "bottom")
TAGS = ("none", "red", "blue", "green", "yellow", "purple")

# Lane states. "self" is this console holding it; "busy" is another console
# talking and cannot be taken; "hold" is another console parking it and CAN be
# taken; "error" is unusable.
LANE_IDLE, LANE_SELF, LANE_BUSY, LANE_HOLD, LANE_ERROR = (
    "idle", "self", "busy", "hold", "error")

# Passwords live in the backend, never in the UI.
PASSWORDS = {"config": "1234", "developer": "9999"}

state = {
    "online": True,          # link to the Master Controller
    "title": "Drive-Thru",
    "lanes": [],
    "lane": 0,               # lane held by THIS console, 0 = none
    "held": False,           # our lane parked on hold
    "mic": False,
    "mute": True,            # matches the config.txt boot state
    "volume": 60,
    "brightness": 80,
    "layout": "right",
    "audio_out": "speaker",
    "message": "No lane selected",
}

# Optional add-on modules. The UI only draws buttons for what is present, so
# emptying this list must make them disappear.
modules = [
    {"id": "headset", "name": "Headset"},
    {"id": "handset", "name": "Handset"},
]

# Provisional theme. Marketing has not approved a palette; this is here to prove
# the UI restyles at runtime with no rebuild.
theme = {}

logs = [
    {"t": "2027-01-01 09:14:02", "level": "info", "text": "Console started"},
    {"t": "2027-01-01 09:14:03", "level": "info", "text": "Master Controller link up"},
]


def clamp(value, low, high):
    return max(low, min(high, value))


def log(level, text):
    logs.append({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "level": level, "text": text})
    del logs[:-50]          # last 50 only; the SD card is not a log server


def new_lane(lane_id):
    return {"id": lane_id, "label": "Lane %d" % lane_id, "tag": "none",
            "status": LANE_IDLE, "owner": None, "out_volume": 60}


def find_lane(lane_id):
    for lane in state["lanes"]:
        if lane["id"] == lane_id:
            return lane
    return None


def set_lane_count(count):
    count = clamp(count, MIN_LANES, MAX_LANES)
    lanes = state["lanes"]

    if count < len(lanes):
        del lanes[count:]
    else:
        while len(lanes) < count:
            lanes.append(new_lane(len(lanes) + 1))

    if state["lane"] > count:
        release_lane()

    return count


def release_lane():
    lane = find_lane(state["lane"])
    if lane and lane["status"] == LANE_SELF:
        lane["status"] = LANE_IDLE
        lane["owner"] = None

    state["lane"] = 0
    state["held"] = False
    state["mic"] = False
    state["mute"] = True
    state["message"] = "No lane selected"


def select_lane(lane_id):
    """Request a lane. The Master Controller arbitrates, so this can refuse.

    Returns (ok, reason). Re-selecting the held lane releases it.
    """
    if lane_id == 0 or lane_id == state["lane"]:
        release_lane()
        return True, None

    lane = find_lane(lane_id)
    if lane is None:
        return False, "no such lane"

    # A lane another console is talking to cannot be taken. A lane another
    # console has parked on hold CAN be.
    if lane["status"] == LANE_BUSY:
        return False, "lane in use by %s" % (lane["owner"] or "another console")
    if lane["status"] == LANE_ERROR:
        return False, "lane out of service"

    release_lane()

    lane["status"] = LANE_SELF
    lane["owner"] = "this console"
    state["lane"] = lane_id
    state["held"] = False
    state["mic"] = True
    state["mute"] = False
    state["message"] = "%s live" % lane["label"]
    log("info", "Connected to %s" % lane["label"])
    return True, None


def simulated_level():
    if state["lane"] == 0 or state["held"] or state["mute"] or not state["mic"]:
        return 0

    now = time.time()
    wave = math.sin(now * 2.3) + 0.6 * math.sin(now * 5.1)
    return int(clamp(45 + wave * 28, 0, 100))


class ConsoleHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WWW_ROOT, **kwargs)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/status":
            payload = dict(state)
            payload["level"] = simulated_level()
            payload["hardware"] = False
            payload["modules"] = modules
            payload["theme"] = theme
            payload["user_mgmt"] = False   # out of v1; the UI reserves the slot
            return self.send_json(payload)

        if path == "/api/logs":
            return self.send_json({"entries": list(reversed(logs))})

        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]

        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self.send_json({"error": "malformed body"}, status=400)

        if path == "/api/lane":
            ok, reason = select_lane(int(body.get("lane", 0)))
            if not ok:
                state["message"] = reason
                return self.send_json({"error": reason}, status=409)
            return self.send_json({"ok": True})

        if path == "/api/hold":
            if state["lane"] == 0:
                return self.send_json({"error": "no lane selected"}, status=409)

            state["held"] = bool(body.get("on"))
            lane = find_lane(state["lane"])
            # Parking drops audio but keeps the lane ours until someone takes it.
            state["mic"] = not state["held"]
            state["mute"] = state["held"]
            state["message"] = "%s %s" % (
                lane["label"], "on hold" if state["held"] else "live")
            return self.send_json({"ok": True})

        if path in ("/api/mic", "/api/mute"):
            if state["lane"] == 0:
                return self.send_json({"error": "no lane selected"}, status=409)

            on = bool(body.get("on"))
            lane = find_lane(state["lane"])
            if path == "/api/mic":
                state["mic"] = on
                state["message"] = "%s mic %s" % (lane["label"], "live" if on else "off")
            else:
                state["mute"] = on
                state["message"] = "%s speakers %s" % (
                    lane["label"], "muted" if on else "on")
            return self.send_json({"ok": True})

        if path == "/api/volume":
            state["volume"] = clamp(int(body.get("value", state["volume"])), 0, 100)
            return self.send_json({"ok": True})

        if path == "/api/brightness":
            state["brightness"] = clamp(int(body.get("value", state["brightness"])), 0, 100)
            return self.send_json({"ok": True})

        if path == "/api/audio-out":
            target = body.get("id", "speaker")
            if target != "speaker" and not any(m["id"] == target for m in modules):
                return self.send_json({"error": "no such module"}, status=400)
            state["audio_out"] = target
            state["message"] = "Audio via %s" % target
            return self.send_json({"ok": True})

        if path == "/api/layout":
            position = body.get("position")
            if position not in LAYOUTS:
                return self.send_json({"error": "bad position"}, status=400)
            state["layout"] = position
            return self.send_json({"ok": True})

        # ---- installer configuration ----

        if path == "/api/lanes":
            count = set_lane_count(int(body.get("count", len(state["lanes"]))))
            log("info", "Lane count set to %d" % count)
            return self.send_json({"ok": True, "count": count})

        if path == "/api/lane-config":
            lane = find_lane(int(body.get("lane", 0)))
            if lane is None:
                return self.send_json({"error": "no such lane"}, status=400)

            if "label" in body:
                lane["label"] = str(body["label"])[:24]
            if "tag" in body:
                if body["tag"] not in TAGS:
                    return self.send_json({"error": "bad tag"}, status=400)
                lane["tag"] = body["tag"]
            if "out_volume" in body:
                lane["out_volume"] = clamp(int(body["out_volume"]), 0, 100)

            return self.send_json({"ok": True})

        if path == "/api/title":
            state["title"] = str(body.get("text", ""))[:32]
            return self.send_json({"ok": True})

        if path == "/api/auth":
            scope = body.get("scope")
            if scope not in PASSWORDS:
                return self.send_json({"error": "bad scope"}, status=400)
            if str(body.get("password", "")) != PASSWORDS[scope]:
                log("warn", "Failed %s unlock" % scope)
                return self.send_json({"error": "incorrect"}, status=403)
            log("info", "%s screen unlocked" % scope.title())
            return self.send_json({"ok": True})

        # ---- simulation, so every lane state is reachable without a site ----

        if path == "/api/sim":
            lane = find_lane(int(body.get("lane", 0)))
            status = body.get("status")
            if lane is None or status not in (
                    LANE_IDLE, LANE_BUSY, LANE_HOLD, LANE_ERROR):
                return self.send_json({"error": "bad sim"}, status=400)

            if lane["id"] == state["lane"]:
                release_lane()

            lane["status"] = status
            lane["owner"] = body.get("owner", "Console 2") if status in (
                LANE_BUSY, LANE_HOLD) else None
            return self.send_json({"ok": True})

        if path == "/api/sim-online":
            state["online"] = bool(body.get("on", True))
            log("warn" if not state["online"] else "info",
                "Master Controller link %s" % ("down" if not state["online"] else "up"))
            return self.send_json({"ok": True})

        if path == "/api/sim-theme":
            theme.clear()
            theme.update(body.get("theme") or {})
            return self.send_json({"ok": True})

        if path == "/api/sim-modules":
            del modules[:]
            modules.extend(body.get("modules") or [])
            if not any(m["id"] == state["audio_out"] for m in modules):
                state["audio_out"] = "speaker"
            return self.send_json({"ok": True})

        return self.send_json({"error": "unknown endpoint"}, status=404)

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/status"):
            return
        super().log_message(fmt, *args)


def main():
    args = sys.argv[1:]
    port = 8000
    lanes = 4

    if "--layout" in args:
        index = args.index("--layout")
        layout = args[index + 1]
        if layout not in LAYOUTS:
            raise SystemExit("--layout must be one of %s" % ", ".join(LAYOUTS))
        state["layout"] = layout
        del args[index:index + 2]

    if "--lanes" in args:
        index = args.index("--lanes")
        lanes = int(args[index + 1])
        del args[index:index + 2]

    if args:
        port = int(args[0])

    set_lane_count(lanes)

    server = HTTPServer(("127.0.0.1", port), ConsoleHandler)
    print("Operator Console mock backend on http://127.0.0.1:%d" % port)
    print("Serving %s with %d lane(s), controls %s"
          % (WWW_ROOT, len(state["lanes"]), state["layout"]))
    print("Config password %s, developer password %s"
          % (PASSWORDS["config"], PASSWORDS["developer"]))

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
