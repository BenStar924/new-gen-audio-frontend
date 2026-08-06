#!/usr/bin/env python3
"""Mock Operator Console backend for front-end development.

Stands in for the libmicrohttpd C app: serves www/ and implements the same
/api/* contract against in-memory state (see README.md). Lets the UI be
developed and demoed on a laptop, or on the bench card before the C backend
exists.

    python3 dev-server.py [port]        # default 8000

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

state = {
    "lanes": 8,
    "lane": 1,
    "mic": False,
    "mute": True,
    "volume": 60,
    "brightness": 80,
    "message": "Mock backend — no hardware attached",
}


def clamp(value, low, high):
    return max(low, min(high, value))


def simulated_level():
    """Fake an input level so the meter has something to show.

    Only moves while the mic is live and the speakers are un-muted, which is
    also the only time the real meter would read anything.
    """
    if state["mute"] or not state["mic"]:
        return 0

    # Two detuned sines so the movement doesn't look like a sawtooth.
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
            lane = int(body.get("lane", state["lane"]))
            state["lane"] = clamp(lane, 1, state["lanes"])
            state["message"] = "Lane %d active" % state["lane"]

        elif path == "/api/mic":
            state["mic"] = bool(body.get("on"))
            state["message"] = "Mic %s" % ("live" if state["mic"] else "off")

        elif path == "/api/mute":
            state["mute"] = bool(body.get("on"))
            state["message"] = "Speakers %s" % ("muted" if state["mute"] else "on")

        elif path == "/api/volume":
            state["volume"] = clamp(int(body.get("value", state["volume"])), 0, 100)

        elif path == "/api/brightness":
            state["brightness"] = clamp(int(body.get("value", state["brightness"])), 0, 100)

        else:
            return self.send_json({"error": "unknown endpoint"}, status=404)

        return self.send_json({"ok": True})

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
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = HTTPServer(("127.0.0.1", port), ConsoleHandler)
    print("Operator Console mock backend on http://127.0.0.1:%d" % port)
    print("Serving %s" % WWW_ROOT)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
