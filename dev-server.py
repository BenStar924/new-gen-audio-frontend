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

state = {
    "lanes": [],
    "lane": 1,
    "layout": "right",
    # Master volume: one per console, not one per lane.
    "volume": 60,
    "brightness": 80,
    "message": "Mock backend — no hardware attached",
}


def clamp(value, low, high):
    return max(low, min(high, value))


def new_lane(lane_id):
    # Muted matches the config.txt boot state (gpio=25=op,dh — GPIO25 is an
    # active-high mute), which is what the real backend reports at startup.
    return {"id": lane_id, "mic": False, "mute": True, "level": 0}


def set_lane_count(count):
    count = clamp(count, MIN_LANES, MAX_LANES)
    lanes = state["lanes"]

    if count < len(lanes):
        del lanes[count:]
    else:
        while len(lanes) < count:
            lanes.append(new_lane(len(lanes) + 1))

    # The active lane can be the one that just disappeared.
    if state["lane"] > count:
        state["lane"] = count

    return count


def find_lane(lane_id):
    for lane in state["lanes"]:
        if lane["id"] == lane_id:
            return lane
    return None


def simulated_level(lane):
    """Fake an input level so the meter has something to show.

    Only moves while that lane's mic is live and its speakers are un-muted,
    which is also the only time the real meter would read anything. Lanes are
    detuned against each other so they don't move in lockstep.
    """
    if lane["mute"] or not lane["mic"]:
        return 0

    now = time.time() + lane["id"] * 3.7
    wave = math.sin(now * 2.3) + 0.6 * math.sin(now * 5.1)
    return int(clamp(45 + wave * 28, 0, 100))


class ConsoleHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WWW_ROOT, **kwargs)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/status":
            payload = dict(state)
            payload["lanes"] = [
                dict(lane, level=simulated_level(lane)) for lane in state["lanes"]
            ]
            return self.send_json(payload)

        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]

        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self.send_json({"error": "malformed body"}, status=400)

        # Console-wide commands.
        if path == "/api/lane":
            lane_id = int(body.get("lane", state["lane"]))
            if find_lane(lane_id) is None:
                return self.send_json({"error": "no such lane"}, status=400)
            state["lane"] = lane_id
            state["message"] = "Lane %d active" % lane_id
            return self.send_json({"ok": True})

        if path == "/api/lanes":
            count = set_lane_count(int(body.get("count", len(state["lanes"]))))
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

        # Per-lane commands. These act on the lane named in the body, not on
        # whatever happens to be selected — the UI is not the source of truth
        # for which lane a command targets.
        if path in ("/api/mic", "/api/mute"):
            lane = find_lane(int(body.get("lane", state["lane"])))
            if lane is None:
                return self.send_json({"error": "no such lane"}, status=400)

            if path == "/api/mic":
                lane["mic"] = bool(body.get("on"))
                state["message"] = "Lane %d mic %s" % (
                    lane["id"], "live" if lane["mic"] else "off")

            else:
                lane["mute"] = bool(body.get("on"))
                state["message"] = "Lane %d speakers %s" % (
                    lane["id"], "muted" if lane["mute"] else "on")

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
          % (WWW_ROOT, len(state["lanes"]), state["layout"]))

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
