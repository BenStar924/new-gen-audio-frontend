#!/usr/bin/env python3
"""Operator Console API contract test, API v1.

Checks that a backend serves what the front end in www/ expects. When this
passes, the UI works against that backend. This is the interface between the
front end and whoever builds the backend: the README tables describe the
contract, and this checks it.

    python3 contract-test.py                        # read-only, safe on a live console
    python3 contract-test.py --bench                # also drives lanes, audio and settings
    python3 contract-test.py --bench --config-password 1234
    python3 contract-test.py --url http://127.0.0.1:8000

Read-only mode sends GET requests only and changes nothing.

--bench selects lanes, which on real hardware OPENS THE MICROPHONE AND
UNMUTES THE AMPLIFIER. Run it on a bench unit, never on a console that is in
service. It puts back every setting it changes, even when a check fails.

Uses the standard library only, so it runs on the device's stock python3.
Exits 0 when every check passes, 1 when any check fails, and 2 when the
backend cannot be reached.
"""

import argparse
import http.client
import json
import sys
from urllib.parse import urlsplit

API_VERSION = 1
MAX_LANES = 12

LANE_STATUSES = {"idle", "self", "busy", "hold", "error"}
TAGS = {"none", "red", "blue", "green", "yellow", "purple"}
LAYOUTS = {"left", "right", "bottom"}
SHADES = {"light", "base", "dark"}
LOG_LEVELS = {"info", "warn", "error"}


# ---------------------------------------------------------------- transport

class Unreachable(Exception):
    pass


class Backend:
    def __init__(self, url, timeout):
        parts = urlsplit(url)
        if parts.scheme != "http" or not parts.hostname:
            raise SystemExit("--url must look like http://host:port")
        self.host = parts.hostname
        self.port = parts.port or 80
        self.timeout = timeout

    def request(self, method, path, body=None, raw=None):
        """Returns (status, headers, bytes). `path` is sent exactly as given,
        so traversal probes are not normalised away before they leave."""
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        headers = {}
        data = None
        if raw is not None:
            data = raw.encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            payload = resp.read()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, payload
        except (ConnectionError, OSError) as exc:
            raise Unreachable(str(exc))
        finally:
            conn.close()

    def get_json(self, path):
        status, headers, payload = self.request("GET", path)
        try:
            data = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            data = None
        return status, headers, data

    def post(self, path, body=None, raw=None):
        status, _, payload = self.request("POST", path, body=body, raw=raw)
        try:
            data = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            data = None
        return status, data

    def status(self):
        _, _, data = self.get_json("/api/status")
        return data


# ---------------------------------------------------------------- reporting

class Report:
    def __init__(self):
        self.passed = 0
        self.failed = []
        self.skipped = []

    def section(self, title):
        print("\n" + title)

    def check(self, name, ok, detail=""):
        if ok:
            self.passed += 1
            print("  PASS  " + name)
        else:
            self.failed.append(name)
            print("  FAIL  " + name + ("  -- " + detail if detail else ""))
        return ok

    def skip(self, name, why):
        self.skipped.append(name)
        print("  SKIP  " + name + "  -- " + why)


def is_bool(v):
    return type(v) is bool


def is_int(v):
    # bool is a subclass of int in Python; a JSON `true` is not a number here.
    return type(v) is int


def pct(v):
    return is_int(v) and 0 <= v <= 100


# ---------------------------------------------------------------- read-only

def check_static(b, r):
    r.section("Static files (the UI itself)")

    status, headers, body = b.request("GET", "/")
    r.check("GET / serves the page", status == 200 and "text/html" in headers.get("content-type", ""),
            "status %s, content-type %r" % (status, headers.get("content-type")))
    r.check("GET / is sent with Cache-Control: no-store",
            "no-store" in headers.get("cache-control", ""),
            "WPE caches aggressively; without no-store an updated UI may not appear "
            "(got %r)" % headers.get("cache-control"))
    r.check("the page is the Operator Console", b"operatorConsole" in body,
            "the served index.html does not mount the operatorConsole component")

    for path, kind in (("/app.js", "javascript"),
                       ("/vendor/app.css", "css"),
                       ("/vendor/alpine.min.js", "javascript")):
        status, headers, _ = b.request("GET", path)
        r.check("GET %s serves %s" % (path, kind),
                status == 200 and kind in headers.get("content-type", ""),
                "status %s, content-type %r" % (status, headers.get("content-type")))

    for path in ("/../README.md", "/vendor/../../kiosk.sh"):
        status, _, _ = b.request("GET", path)
        r.check("GET %s does not escape www/" % path, status != 200,
                "served a file from outside www/ (status 200)")


def check_status(b, r):
    r.section("GET /api/status")

    status, headers, s = b.get_json("/api/status")
    if not r.check("responds 200 with a JSON object", status == 200 and isinstance(s, dict),
                   "status %s" % status):
        return None
    r.check("Content-Type is application/json",
            "application/json" in headers.get("content-type", ""),
            repr(headers.get("content-type")))
    r.check("Cache-Control: no-store", "no-store" in headers.get("cache-control", ""),
            repr(headers.get("cache-control")))
    r.check("api == %d" % API_VERSION, s.get("api") == API_VERSION,
            "got %r. The UI checks this and warns on screen when it differs" % s.get("api"))

    fields = [
        ("online", is_bool), ("title", lambda v: isinstance(v, str)),
        ("lane", lambda v: is_int(v) and v >= 0), ("held", is_bool),
        ("mic", is_bool), ("mute", is_bool), ("level", pct), ("volume", pct),
        ("brightness", pct), ("audio_out", lambda v: isinstance(v, str)),
        ("modules", lambda v: isinstance(v, list)), ("layout", lambda v: v in LAYOUTS),
        ("theme", lambda v: isinstance(v, dict)), ("user_mgmt", is_bool),
        ("hardware", is_bool), ("message", lambda v: isinstance(v, str)),
    ]
    for name, valid in fields:
        r.check("field %-10s is present and well-typed" % name,
                name in s and valid(s[name]), "got %r" % s.get(name, "<missing>"))

    brand = s.get("brand")
    r.check("field brand      is {base: str, shade: light|base|dark}",
            isinstance(brand, dict) and isinstance(brand.get("base"), str)
            and brand.get("shade") in SHADES, "got %r" % brand)

    lanes = s.get("lanes")
    if not r.check("field lanes      is a list of 1-%d lanes" % MAX_LANES,
                   isinstance(lanes, list) and 1 <= len(lanes) <= MAX_LANES,
                   "got %r" % (lanes if not isinstance(lanes, list) else "%d lanes" % len(lanes))):
        return s

    bad = []
    for i, lane in enumerate(lanes):
        problems = []
        if not isinstance(lane, dict):
            bad.append("lane %d is not an object" % (i + 1))
            continue
        if lane.get("id") != i + 1:
            problems.append("id %r (expected %d, 1-based and contiguous)" % (lane.get("id"), i + 1))
        if not isinstance(lane.get("label"), str):
            problems.append("label %r" % lane.get("label"))
        if lane.get("tag") not in TAGS:
            problems.append("tag %r" % lane.get("tag"))
        if lane.get("status") not in LANE_STATUSES:
            problems.append("status %r" % lane.get("status"))
        if "owner" not in lane or not (lane["owner"] is None or isinstance(lane["owner"], str)):
            problems.append("owner %r (must be a string or null)" % lane.get("owner", "<missing>"))
        if not pct(lane.get("out_volume")):
            problems.append("out_volume %r" % lane.get("out_volume"))
        if problems:
            bad.append("lane %d: %s" % (i + 1, ", ".join(problems)))
    r.check("every lane has id, label, tag, status, owner, out_volume", not bad, "; ".join(bad))

    mods = s.get("modules") if isinstance(s.get("modules"), list) else []
    r.check("modules are {id, name}, at most two",
            len(mods) <= 2 and all(isinstance(m, dict) and isinstance(m.get("id"), str)
                                   and isinstance(m.get("name"), str) for m in mods),
            "got %r" % mods)
    r.check("audio_out is 'speaker' or a fitted module",
            s.get("audio_out") == "speaker" or s.get("audio_out") in [m.get("id") for m in mods],
            "got %r" % s.get("audio_out"))

    # Cross-field rules the UI relies on.
    held_lane = s.get("lane")
    selfs = [l["id"] for l in lanes if isinstance(l, dict) and l.get("status") == "self"]
    if held_lane:
        r.check("the held lane is the one marked 'self'", selfs == [held_lane],
                "lane=%r but lanes marked self: %r" % (held_lane, selfs))
    else:
        r.check("no lane held -> no lane marked 'self'", not selfs, "marked self: %r" % selfs)
        r.check("no lane held -> mic off, amplifier muted, meter at 0 (the boot state)",
                s.get("mic") is False and s.get("mute") is True and s.get("level") == 0,
                "mic=%r mute=%r level=%r" % (s.get("mic"), s.get("mute"), s.get("level")))
    return s


def check_logs(b, r, label="GET /api/logs"):
    r.section(label)
    status, _, data = b.get_json("/api/logs")
    if not r.check("responds 200 with {entries: [...]}",
                   status == 200 and isinstance(data, dict) and isinstance(data.get("entries"), list),
                   "status %s" % status):
        return []
    entries = data["entries"]
    r.check("at most 50 entries", len(entries) <= 50, "%d entries" % len(entries))
    bad = [e for e in entries if not (isinstance(e, dict) and isinstance(e.get("t"), str)
                                      and e.get("level") in LOG_LEVELS
                                      and isinstance(e.get("text"), str))]
    r.check("each entry is {t, level: info|warn|error, text}", not bad, "first bad: %r" % (bad[:1],))
    times = [e.get("t") for e in entries if isinstance(e, dict)]
    r.check("newest first", times == sorted(times, reverse=True),
            "timestamps are not in descending order")
    return entries


def check_unknown_get(b, r):
    r.section("Unknown endpoints")
    status, _, _ = b.request("GET", "/api/no-such-endpoint")
    r.check("GET /api/no-such-endpoint -> 404", status == 404, "got %s" % status)


# ---------------------------------------------------------------- bench

class Restorer:
    """Undo stack: every change made on the bench is registered here first and
    reversed at the end, whether or not the checks in between pass."""

    def __init__(self):
        self.steps = []

    def push(self, label, fn):
        self.steps.append((label, fn))

    def run(self):
        failures = []
        for label, fn in reversed(self.steps):
            try:
                fn()
            except Exception as exc:  # restoring must not stop at the first error
                failures.append("%s (%s)" % (label, exc))
        self.steps = []
        return failures


def expect(r, b, name, path, body, want, raw=None):
    status, data = b.post(path, body=body, raw=raw)
    detail = "got %s %s" % (status, json.dumps(data) if data is not None else "")
    ok = r.check("%s -> %d" % (name, want), status == want, detail)
    return ok, data


def check_lanes_and_audio(b, r, undo):
    r.section("Lanes and audio (drives the mic and amplifier on real hardware)")

    s = b.status()
    b.post("/api/lane", {"lane": 0})
    s = b.status()
    r.check("releasing leaves the boot state: lane 0, mic off, muted",
            s["lane"] == 0 and s["mic"] is False and s["mute"] is True,
            "lane=%r mic=%r mute=%r" % (s["lane"], s["mic"], s["mute"]))
    undo.push("release lane", lambda: b.post("/api/lane", {"lane": 0}))

    idle = [l["id"] for l in s["lanes"] if l["status"] == "idle"]
    if not idle:
        r.skip("select / hold / release", "no idle lane to use")
        return
    L = idle[0]

    ok, data = expect(r, b, "select lane %d" % L, "/api/lane", {"lane": L}, 200)
    r.check("a successful command answers {\"ok\": true}", isinstance(data, dict) and data.get("ok") is True,
            "got %r" % data)
    s = b.status()
    r.check("selecting opens the mic and speakers and marks the lane 'self'",
            s["lane"] == L and s["lanes"][L - 1]["status"] == "self"
            and s["mic"] is True and s["mute"] is False and s["held"] is False,
            "lane=%r status=%r mic=%r mute=%r held=%r"
            % (s["lane"], s["lanes"][L - 1]["status"], s["mic"], s["mute"], s["held"]))

    expect(r, b, "hold on", "/api/hold", {"on": True}, 200)
    s = b.status()
    r.check("hold parks the lane: still ours, mic off, muted, meter 0",
            s["lane"] == L and s["held"] is True and s["mic"] is False
            and s["mute"] is True and s["level"] == 0,
            "lane=%r held=%r mic=%r mute=%r level=%r"
            % (s["lane"], s["held"], s["mic"], s["mute"], s["level"]))

    expect(r, b, "hold off", "/api/hold", {"on": False}, 200)
    s = b.status()
    r.check("resuming reopens the mic and speakers",
            s["held"] is False and s["mic"] is True and s["mute"] is False,
            "held=%r mic=%r mute=%r" % (s["held"], s["mic"], s["mute"]))

    expect(r, b, "mic off while holding a lane", "/api/mic", {"on": False}, 200)
    r.check("mic can be cut without releasing the lane",
            b.status()["mic"] is False and b.status()["lane"] == L)
    expect(r, b, "mic on", "/api/mic", {"on": True}, 200)
    expect(r, b, "speakers muted by hand", "/api/mute", {"on": True}, 200)
    r.check("mute is reflected", b.status()["mute"] is True)
    expect(r, b, "speakers on", "/api/mute", {"on": False}, 200)

    expect(r, b, "select the held lane again (release)", "/api/lane", {"lane": L}, 200)
    s = b.status()
    r.check("re-selecting the held lane releases it back to the boot state",
            s["lane"] == 0 and s["lanes"][L - 1]["status"] == "idle"
            and s["mic"] is False and s["mute"] is True and s["held"] is False,
            "lane=%r status=%r mic=%r mute=%r"
            % (s["lane"], s["lanes"][L - 1]["status"], s["mic"], s["mute"]))

    r.section("Refusals: 409 when valid but not possible now")
    expect(r, b, "mic on with no lane held", "/api/mic", {"on": True}, 409)
    expect(r, b, "speakers on with no lane held", "/api/mute", {"on": False}, 409)
    expect(r, b, "hold with no lane held", "/api/hold", {"on": True}, 409)
    s = b.status()
    r.check("refused commands changed nothing", s["mic"] is False and s["mute"] is True,
            "mic=%r mute=%r" % (s["mic"], s["mute"]))
    ok, data = expect(r, b, "select a lane that does not exist", "/api/lane",
                      {"lane": len(s["lanes"]) + 1}, 409)
    r.check("a refusal carries its reason in `error`",
            isinstance(data, dict) and isinstance(data.get("error"), str) and data["error"],
            "got %r. The UI shows this reason to the operator" % data)

    r.section("Malformed commands: 400, and nothing changes")
    expect(r, b, "select with no lane field", "/api/lane", {}, 400)
    expect(r, b, "select with a string lane", "/api/lane", {"lane": "2"}, 400)
    expect(r, b, "select with a body that is not JSON", "/api/lane", None, 400, raw="{not json")
    expect(r, b, "hold with no on field", "/api/hold", {}, 400)
    expect(r, b, "mic with no on field", "/api/mic", {}, 400)
    r.check("malformed commands did not select anything", b.status()["lane"] == 0)


def check_settings(b, r, undo, config_password):
    s0 = b.status()

    r.section("Volume and brightness")
    v0 = s0["volume"]
    undo.push("volume", lambda: b.post("/api/volume", {"value": v0}))
    v1 = 35 if v0 != 35 else 45
    expect(r, b, "volume %d" % v1, "/api/volume", {"value": v1}, 200)
    r.check("volume is reflected", b.status()["volume"] == v1)
    expect(r, b, "volume 101", "/api/volume", {"value": 101}, 400)
    expect(r, b, "volume -1", "/api/volume", {"value": -1}, 400)
    expect(r, b, "volume with no value", "/api/volume", {}, 400)
    r.check("out-of-range volume was rejected, not clamped", b.status()["volume"] == v1)

    br0 = s0["brightness"]
    undo.push("brightness", lambda: b.post("/api/brightness", {"value": br0}))
    br1 = 60 if br0 != 60 else 70
    expect(r, b, "brightness %d" % br1, "/api/brightness", {"value": br1}, 200)
    r.check("brightness is reflected", b.status()["brightness"] == br1)
    expect(r, b, "brightness 101", "/api/brightness", {"value": 101}, 400)
    expect(r, b, "brightness with no value", "/api/brightness", {}, 400)

    r.section("Control position")
    l0 = s0["layout"]
    undo.push("layout", lambda: b.post("/api/layout", {"position": l0}))
    for pos in ("left", "bottom", "right"):
        expect(r, b, "layout %s" % pos, "/api/layout", {"position": pos}, 200)
        r.check("layout %s is reflected" % pos, b.status()["layout"] == pos)
    expect(r, b, "layout diagonal", "/api/layout", {"position": "diagonal"}, 400)

    r.section("Lane count")
    n0 = len(s0["lanes"])
    if n0 < MAX_LANES:
        undo.push("lane count", lambda: b.post("/api/lanes", {"count": n0}))
        expect(r, b, "lane count %d" % (n0 + 1), "/api/lanes", {"count": n0 + 1}, 200)
        lanes = b.status()["lanes"]
        r.check("a lane is added, 1-based and idle",
                len(lanes) == n0 + 1 and lanes[-1]["id"] == n0 + 1 and lanes[-1]["status"] == "idle",
                "%d lanes, last %r" % (len(lanes), lanes[-1]))
        expect(r, b, "lane count back to %d" % n0, "/api/lanes", {"count": n0}, 200)
        r.check("the lane is removed", len(b.status()["lanes"]) == n0)
    else:
        r.skip("lane count round trip", "already at %d lanes" % MAX_LANES)
    expect(r, b, "lane count 0", "/api/lanes", {"count": 0}, 400)
    expect(r, b, "lane count %d" % (MAX_LANES + 1), "/api/lanes", {"count": MAX_LANES + 1}, 400)
    expect(r, b, "lane count with no count", "/api/lanes", {}, 400)
    r.check("out-of-range counts were rejected, not clamped", len(b.status()["lanes"]) == n0)

    r.section("Per-lane configuration")
    lane1 = s0["lanes"][0]
    undo.push("lane 1 config", lambda: b.post("/api/lane-config", {
        "lane": 1, "label": lane1["label"], "tag": lane1["tag"], "out_volume": lane1["out_volume"]}))
    expect(r, b, "rename lane 1", "/api/lane-config", {"lane": 1, "label": "Contract test"}, 200)
    r.check("the label is reflected", b.status()["lanes"][0]["label"] == "Contract test")
    tag = "purple" if lane1["tag"] != "purple" else "green"
    expect(r, b, "tag lane 1 %s" % tag, "/api/lane-config", {"lane": 1, "tag": tag}, 200)
    r.check("the tag is reflected", b.status()["lanes"][0]["tag"] == tag)
    expect(r, b, "tag magenta", "/api/lane-config", {"lane": 1, "tag": "magenta"}, 400)
    ov = 42 if lane1["out_volume"] != 42 else 43
    expect(r, b, "lane 1 outgoing volume %d" % ov, "/api/lane-config", {"lane": 1, "out_volume": ov}, 200)
    r.check("outgoing volume is reflected", b.status()["lanes"][0]["out_volume"] == ov)
    expect(r, b, "outgoing volume 101", "/api/lane-config", {"lane": 1, "out_volume": 101}, 400)
    expect(r, b, "configure lane 0", "/api/lane-config", {"lane": 0, "label": "x"}, 400)
    expect(r, b, "configure a lane that does not exist", "/api/lane-config",
           {"lane": n0 + 1, "label": "x"}, 400)

    r.section("Screen title")
    t0 = s0["title"]
    undo.push("title", lambda: b.post("/api/title", {"text": t0}))
    expect(r, b, "set title", "/api/title", {"text": "Contract test"}, 200)
    r.check("the title is reflected", b.status()["title"] == "Contract test")
    expect(r, b, "title with no text", "/api/title", {}, 400)
    r.check("a malformed title did not blank the title", b.status()["title"] == "Contract test")

    r.section("Audio output")
    a0 = s0["audio_out"]
    undo.push("audio out", lambda: b.post("/api/audio-out", {"id": a0}))
    expect(r, b, "audio out speaker", "/api/audio-out", {"id": "speaker"}, 200)
    expect(r, b, "audio out to a module that is not fitted", "/api/audio-out", {"id": "no-such-module"}, 400)
    expect(r, b, "audio out with no id", "/api/audio-out", {}, 400)
    mods = s0["modules"]
    if mods:
        expect(r, b, "audio out %s" % mods[0]["id"], "/api/audio-out", {"id": mods[0]["id"]}, 200)
        r.check("the module is reflected", b.status()["audio_out"] == mods[0]["id"])
    else:
        r.skip("audio out to a fitted module", "no add-on modules fitted")

    r.section("Passwords")
    expect(r, b, "wrong configuration password", "/api/auth",
           {"scope": "config", "password": "contract-test-wrong"}, 403)
    expect(r, b, "unknown scope", "/api/auth", {"scope": "nonsense", "password": "x"}, 400)
    expect(r, b, "auth with no fields", "/api/auth", {}, 400)
    if config_password:
        expect(r, b, "correct configuration password", "/api/auth",
               {"scope": "config", "password": config_password}, 200)
    else:
        r.skip("correct configuration password", "pass --config-password to check it")

    r.section("Brand colour")
    brand0 = s0["brand"]
    theme0 = s0["theme"]
    undo.push("brand", lambda: b.post("/api/brand", {
        "base": brand0["base"], "shade": brand0["shade"], "theme": theme0}))
    expect(r, b, "brand with an unknown shade", "/api/brand",
           {"base": "#1F5FB0", "shade": "weird", "theme": {"navy": "#123456"}}, 400)
    expect(r, b, "brand whose theme is not an object", "/api/brand",
           {"base": "#1F5FB0", "shade": "base", "theme": "nope"}, 400)
    expect(r, b, "brand with no theme", "/api/brand", {"base": "#1F5FB0", "shade": "base"}, 400)
    test_theme = {"navy": "#123456", "lane-self": "#123456"}
    expect(r, b, "set brand", "/api/brand", {"base": "#123456", "shade": "dark", "theme": test_theme}, 200)
    s = b.status()
    r.check("brand and theme are stored and served back",
            s["brand"] == {"base": "#123456", "shade": "dark"} and s["theme"].get("navy") == "#123456",
            "brand=%r theme=%r" % (s["brand"], s["theme"]))


def check_arbitration(b, r, undo):
    r.section("Arbitration between consoles")
    status, _ = b.post("/api/sim", {})
    if status == 404:
        r.skip("busy / parked / faulted lanes",
               "needs another console on a Master Controller, or dev-server.py's simulator")
        return

    s = b.status()
    idle = [l["id"] for l in s["lanes"] if l["status"] == "idle"]
    if not idle:
        r.skip("busy / parked / faulted lanes", "no idle lane to use")
        return
    L = idle[0]
    undo.push("simulated lane state", lambda: b.post("/api/sim", {"lane": L, "status": "idle"}))

    b.post("/api/sim", {"lane": L, "status": "busy", "owner": "Contract Console"})
    lane = b.status()["lanes"][L - 1]
    r.check("a lane another console is talking to reads 'busy' with its owner",
            lane["status"] == "busy" and lane["owner"] == "Contract Console", "got %r" % lane)
    ok, data = expect(r, b, "select a busy lane", "/api/lane", {"lane": L}, 409)
    r.check("the busy lane was not taken", b.status()["lane"] == 0)

    b.post("/api/sim", {"lane": L, "status": "hold", "owner": "Contract Console"})
    expect(r, b, "select a lane another console parked", "/api/lane", {"lane": L}, 200)
    r.check("a parked lane CAN be taken", b.status()["lane"] == L)
    b.post("/api/lane", {"lane": 0})

    b.post("/api/sim", {"lane": L, "status": "error"})
    expect(r, b, "select a faulted lane", "/api/lane", {"lane": L}, 409)


def check_unknown_post(b, r):
    status, _ = b.post("/api/no-such-endpoint", {})
    r.check("POST /api/no-such-endpoint -> 404", status == 404, "got %s" % status)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Operator Console API contract test (API v%d)." % API_VERSION)
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="backend base URL")
    ap.add_argument("--bench", action="store_true",
                    help="also run checks that change state. OPENS THE MIC on real hardware")
    ap.add_argument("--config-password", help="also check that this configuration password is accepted")
    ap.add_argument("--timeout", type=float, default=5.0, help="seconds per request")
    args = ap.parse_args()

    b = Backend(args.url, args.timeout)
    r = Report()

    print("Operator Console contract test, API v%d, against %s" % (API_VERSION, args.url))
    print("Mode: %s" % ("BENCH (state-changing, restored afterwards)" if args.bench else "read-only"))

    undo = Restorer()
    try:
        check_static(b, r)
        s = check_status(b, r)
        check_logs(b, r)
        check_unknown_get(b, r)

        if args.bench and s is not None:
            try:
                check_lanes_and_audio(b, r, undo)
                check_settings(b, r, undo, args.config_password)
                check_arbitration(b, r, undo)
                r.section("Unknown endpoints (POST)")
                check_unknown_post(b, r)
            finally:
                problems = undo.run()
                r.section("Restore")
                r.check("every changed setting was put back", not problems, "; ".join(problems))
            entries = check_logs(b, r, "GET /api/logs after the bench run")
            r.check("the bench run was logged", len(entries) > 0, "no log entries at all")
        elif not args.bench:
            print("\n(read-only run. Add --bench on a bench unit to check lanes, audio and settings)")
    except Unreachable as exc:
        print("\nCannot reach the backend at %s: %s" % (args.url, exc))
        print("Is it running? The UI expects it on 127.0.0.1:8000, serving www/ and /api.")
        return 2

    print("\n%d passed, %d failed, %d skipped" % (r.passed, len(r.failed), len(r.skipped)))
    if r.failed:
        print("\nFailed:")
        for name in r.failed:
            print("  - " + name)
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
