#!/usr/bin/env python3
"""Mock Operator Console backend for front-end development.

Stands in for the libmicrohttpd C app: serves www/ and implements the same
/api/* contract against in-memory state (see README.md). Lets the UI be
developed and demoed on a laptop, or on the bench card before the C backend
exists.

    python3 dev-server.py [port] [--lanes N] [--layout left|right|bottom]

Then open http://127.0.0.1:8000 — or point the kiosk at it:

    cog http://127.0.0.1:8000

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
MAX_LANES = 8
LAYOUTS = ("left", "right", "bottom")

# Audio is console-wide because the hardware is: one amplifier mute (GPIO25),
# one mic enable (GPIO24), one master volume. A lane is a routing destination.
state = {
    "lanes": 4,
    "lane": 0,        # 0 = no lane selected
    "mic": False,
    "mute": True,     # matches the config.txt boot state
    "layout": "right",
    "volume": 60,
    "brightness": 80,
    "message": "No lane selected",
}


def clamp(value, low, high):
    return max(low, min(high, value))


def set_lane_count(count):
    count = clamp(count, MIN_LANES, MAX_LANES)
    state["lanes"] = count

    # The selected lane can be the one that just disappeared. Release rather
    # than re-point: moving a live microphone somewhere the operator did not
    # ask for is the wrong recovery.
    if state["lane"] > count:
        release_lane()

    return count


def select_lane(lane):
    """Selecting routes a lane and opens the mic and speakers.

    Re-selecting the lane already held releases it, as does lane 0.
    """
    if lane == state["lane"] or lane == 0:
        release_lane()
        return True

    if lane < 1 or lane > state["lanes"]:
        return False

    state["lane"] = lane
    state["mic"] = True
    state["mute"] = False
    state["message"] = "Lane %d live" % lane
    return True


def release_lane():
    state["lane"] = 0
    state["mic"] = False
    state["mute"] = True
    state["message"] = "No lane selected"


def simulated_level():
    """Fake an input level so the meter has something to show.

    Only moves while a lane is held with the mic live and speakers un-muted —
    the only time the real meter would read anything.
    """
    if state["lane"] == 0 or state["mute"] or not state["mic"]:
        return 0

    now = time.time()
    wave = math.sin(now * 2.3) + 0.6 * math.sin(now * 5.1)
    return int(clamp(45 + wave * 28, 0, 100))


class ConsoleHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WWW_ROOT, **kwargs)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/status":
            payload = dict(state)
            payload["level"] = simulated_level()
            payload["hardware"] = False
            return self.send_json(payload)

        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]

        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self.send_json({"error": "malformed body"}, status=400)

        if path == "/api/lane":
            lane = int(body.get("lane", 0))
            if not select_lane(lane):
                return self.send_json({"error": "no such lane"}, status=400)
            return self.send_json({"ok": True})

        if path == "/api/lanes":
            count = set_lane_count(int(body.get("count", state["lanes"])))
            state["message"] = "%d lane%s configured" % (count, "" if count == 1 else "s")
            return self.send_json({"ok": True, "count": count})

        if path == "/api/layout":
            position = body.get("position")
            if position not in LAYOUTS:
                return self.send_json({"error": "bad position"}, status=400)
            state["layout"] = position
            state["message"] = "Controls moved to %s" % position
            return self.send_json({"ok": True})

        if path == "/api/volume":
            state["volume"] = clamp(int(body.get("value", state["volume"])), 0, 100)
            return self.send_json({"ok": True})

        if path == "/api/brightness":
            state["brightness"] = clamp(int(body.get("value", state["brightness"])), 0, 100)
            return self.send_json({"ok": True})

        # Manual overrides, only while a lane is held — there is nothing routed
        # to open a microphone into otherwise.
        if path in ("/api/mic", "/api/mute"):
            if state["lane"] == 0:
                return self.send_json({"error": "no lane selected"}, status=409)

            on = bool(body.get("on"))
            if path == "/api/mic":
                state["mic"] = on
                state["message"] = "Lane %d mic %s" % (
                    state["lane"], "live" if on else "off")
            else:
                state["mute"] = on
                state["message"] = "Lane %d speakers %s" % (
                    state["lane"], "muted" if on else "on")

            return self.send_json({"ok": True})

        return self.send_json({"error": "unknown endpoint"}, status=404)

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # The device is a kiosk showing one page; caching only ever serves stale
        # UI during development.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        # Quiet the per-request logging; poll traffic drowns everything else.
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
          % (WWW_ROOT, state["lanes"], state["layout"]))

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
