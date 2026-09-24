#!/usr/bin/env python3
"""HUBS Audio console API contract test, API v2.

Checks that a backend serves what the UI in www/ expects. When this passes,
the UI works against that backend. API.md describes the contract; this checks
it. Treat the test, not either codebase, as the interface.

    python3 contract-test.py                     # read-only, safe on a live console
    python3 contract-test.py --bench             # also places calls and changes settings
    python3 contract-test.py --bench --user "Pat Admin" --pin 3333
    python3 contract-test.py --bench --dev-password 9999
    python3 contract-test.py --url http://127.0.0.1:8000

Read-only mode sends GET requests only and changes nothing.

--bench PLACES A CALL on an idle lane, which on real hardware opens the audio
path to that lane. Run it on a bench system, never on a site in service. It
puts back every setting it changes, even when a check fails. It never creates
users (accounts can't be deleted), never restores a configuration, never
restarts lane audio and never reboots.

If the console is locked, pass --user/--pin of a technician, admin or owner so
the bench checks can log in. Settings checks need the Master Console.

Uses the standard library only, so it runs on the device's stock python3.
Exits 0 when every check passes, 1 when any check fails, and 2 when the
backend cannot be reached.
"""

import argparse
import http.client
import json
import sys
from urllib.parse import urlsplit

API_VERSION = 2
MAX_DEVICES = 16
BUILTIN_USER = "Hamilton Audio"

LANE_COLORS = ["red", "orange", "amber", "yellow", "lime", "green", "teal", "cyan",
               "sky", "blue", "indigo", "violet", "purple", "magenta", "pink", "white"]
CALL_STATES = {"idle", "ringing", "active", "busy", "held"}
HEALTH = {"ok", "warning", "error"}
SEVERITIES = {"warning", "error"}
USER_ROLES = ["user", "technician", "admin", "owner"]
ALL_ROLES = USER_ROLES + ["disabled"]
SHADES = {"light", "base", "dark"}

# Same table as CAPABILITIES in www/app.js and API.md.
CAPABILITIES = {
    "troubleshooting":    (None,     USER_ROLES),
    "settings":           ("master", ["technician", "admin", "owner"]),
    "manage_users":       ("master", ["admin", "owner"]),
    "chain_diagram":      ("master", USER_ROLES),
    "restart_lane_audio": ("master", ["technician", "admin", "owner"]),
}


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

    @staticmethod
    def _json(payload):
        try:
            return json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def get_json(self, path):
        status, headers, payload = self.request("GET", path)
        return status, headers, self._json(payload)

    def post(self, path, body=None, raw=None):
        status, _, payload = self.request("POST", path, body={} if body is None and raw is None else body, raw=raw)
        return status, self._json(payload)

    def status(self):
        return self.get_json("/api/status")[2] or {}


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


def is_num(v):
    return type(v) in (int, float)


def is_str(v):
    return isinstance(v, str)


def pct(v):
    return is_int(v) and 0 <= v <= 100


def has_error_text(body):
    return isinstance(body, dict) and is_str(body.get("error")) and body["error"].strip() != ""


def can(status, capability):
    """What the capability matrix says this session should be allowed."""
    user = (status.get("session") or {}).get("user")
    if not user or user.get("role") == "disabled":
        return False
    call = status.get("call")
    if call and call.get("restricted"):
        return False
    console_role, roles = CAPABILITIES[capability]
    if console_role and (status.get("console") or {}).get("role") != console_role:
        return False
    return user.get("role") in roles


def lanes_of(status):
    return [d for d in status.get("devices") or [] if isinstance(d, dict) and d.get("kind") == "lane"]


def find_dev(status, dev_id):
    for d in status.get("devices") or []:
        if isinstance(d, dict) and d.get("id") == dev_id:
            return d
    return None


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
    r.check("the page is the HUBS Audio UI", b"hubsConsole" in body,
            "the served index.html does not mount the hubsConsole component")

    for path, kind in (("/app.js", "javascript"),
                       ("/vendor/app.css", "css"),
                       ("/vendor/alpine.min.js", "javascript")):
        status, headers, _ = b.request("GET", path)
        r.check("GET %s serves %s" % (path, kind),
                status == 200 and kind in headers.get("content-type", ""),
                "status %s, content-type %r" % (status, headers.get("content-type")))

    for path in ("/../API.md", "/vendor/../../kiosk.sh"):
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
            "application/json" in headers.get("content-type", ""), repr(headers.get("content-type")))
    r.check("Cache-Control: no-store", "no-store" in headers.get("cache-control", ""),
            repr(headers.get("cache-control")))
    r.check("api == %d" % API_VERSION, s.get("api") == API_VERSION,
            "got %r. The UI checks this and warns on screen when it differs" % s.get("api"))
    r.check("now is Unix seconds", is_num(s.get("now")) and s["now"] > 1.6e9, "got %r" % s.get("now"))

    # ---- console
    c = s.get("console")
    ok = (isinstance(c, dict) and is_str(c.get("id")) and c.get("role") in ("master", "operator")
          and all(is_bool(c.get(k)) for k in ("i2c", "a2b", "master_link")))
    r.check("console is {id, role: master|operator, i2c, a2b, master_link}", ok, "got %r" % c)
    if ok and c["role"] == "master":
        r.check("master_link is true on the Master Console itself", c["master_link"] is True,
                "got %r" % c["master_link"])

    # ---- devices
    devices = s.get("devices")
    if not r.check("devices is a list of 1-%d devices" % MAX_DEVICES,
                   isinstance(devices, list) and 1 <= len(devices) <= MAX_DEVICES,
                   "got %r" % (devices if not isinstance(devices, list) else "%d devices" % len(devices))):
        return s

    bad = []
    ids, positions, numbers = set(), set(), set()
    for i, d in enumerate(devices):
        if not isinstance(d, dict):
            bad.append("device %d is not an object" % i)
            continue
        p = []
        name = d.get("id", "device %d" % i)
        if not is_str(d.get("id")) or d["id"] in ids:
            p.append("id %r (a unique string)" % d.get("id"))
        ids.add(d.get("id"))
        if d.get("kind") not in ("lane", "console"):
            p.append("kind %r" % d.get("kind"))
        if not is_int(d.get("position")) or d["position"] in positions:
            p.append("position %r (a unique integer)" % d.get("position"))
        positions.add(d.get("position"))
        if d.get("health") not in HEALTH:
            p.append("health %r" % d.get("health"))
        codes = d.get("codes")
        if not isinstance(codes, list) or not all(
                isinstance(x, dict) and is_str(x.get("code")) and x.get("severity") in SEVERITIES
                and is_str(x.get("text")) for x in codes):
            p.append("codes %r (list of {code, severity: warning|error, text})" % codes)
        elif any(x["severity"] == "error" for x in codes) and d.get("health") != "error":
            p.append("has an error code but health %r" % d.get("health"))
        elif any(x["severity"] == "warning" for x in codes) and d.get("health") == "ok":
            p.append("has a warning code but health 'ok'")
        if d.get("kind") == "lane":
            n = d.get("number")
            if not is_int(n) or not 1 <= n <= 16 or n in numbers:
                p.append("number %r (unique, 1-16)" % n)
            numbers.add(n)
            if d.get("color") not in LANE_COLORS:
                p.append("color %r" % d.get("color"))
            call = d.get("call")
            if not (isinstance(call, dict) and call.get("state") in CALL_STATES
                    and (call.get("since") is None or is_num(call.get("since")))):
                p.append("call %r ({state, since: number|null})" % call)
        elif d.get("kind") == "console":
            if d.get("role") not in ("master", "operator"):
                p.append("role %r" % d.get("role"))
        if p:
            bad.append("%s: %s" % (name, ", ".join(p)))
    r.check("every device is well-formed (id, kind, position, health, codes, lane/console fields)",
            not bad, "; ".join(bad))

    masters = [d for d in devices if isinstance(d, dict) and d.get("kind") == "console" and d.get("role") == "master"]
    r.check("at most one Master Console on the chain", len(masters) <= 1, "%d masters" % len(masters))
    mine = find_dev(s, c.get("id")) if isinstance(c, dict) else None
    r.check("this console appears in devices with the same role",
            isinstance(mine, dict) and mine.get("kind") == "console" and mine.get("role") == c.get("role"),
            "console.id %r -> %r" % (c.get("id") if isinstance(c, dict) else None, mine))
    order = [d.get("position") for d in devices if isinstance(d, dict)]
    r.check("devices are sent in chain order (position ascending)", order == sorted(order), "got %r" % order)

    # ---- outputs
    outputs = s.get("outputs")
    ok = isinstance(outputs, list) and all(isinstance(o, dict) and is_str(o.get("id")) and is_str(o.get("name"))
                                           for o in outputs)
    r.check("outputs is a list of {id, name}", ok, "got %r" % outputs)
    out_ids = [o.get("id") for o in outputs] if ok else []
    r.check("outputs include the built-in 'speaker'", "speaker" in out_ids, "got %r" % out_ids)

    # ---- call
    call = s.get("call")
    if call is None:
        active = [l.get("id") for l in lanes_of(s) if (l.get("call") or {}).get("state") == "active"]
        r.check("no call, so no lane is 'active'", not active, "active: %r" % active)
    else:
        ok = (isinstance(call, dict) and is_str(call.get("lane")) and is_num(call.get("since"))
              and is_bool(call.get("mic_muted")) and pct(call.get("volume"))
              and call.get("output") in out_ids and is_bool(call.get("restricted")))
        r.check("call is {lane, since, mic_muted, volume 0-100, output in outputs, restricted}", ok,
                "got %r" % call)
        lane = find_dev(s, call.get("lane")) if isinstance(call, dict) else None
        r.check("the call's lane is 'active'", isinstance(lane, dict) and (lane.get("call") or {}).get("state") == "active",
                "lane %r" % lane)

    # ---- session
    sess = s.get("session")
    ok = (isinstance(sess, dict) and is_bool(sess.get("login_required")) and is_str(sess.get("last_user"))
          and sess.get("pin_length") == 4 and is_int(sess.get("autolock_minutes"))
          and 15 <= sess["autolock_minutes"] <= 480)
    r.check("session is {user, login_required, last_user, pin_length: 4, autolock_minutes 15-480}", ok,
            "got %r" % sess)
    if isinstance(sess, dict):
        user = sess.get("user")
        r.check("session.user is null or {name, role} with an active role",
                user is None or (isinstance(user, dict) and is_str(user.get("name"))
                                 and user.get("role") in USER_ROLES), "got %r" % user)
        if sess.get("login_required") is False:
            r.check("with login off, the session is the built-in %s (owner)" % BUILTIN_USER,
                    isinstance(user, dict) and user.get("name") == BUILTIN_USER and user.get("role") == "owner",
                    "got %r" % user)

    # ---- the rest
    sync = s.get("sync")
    r.check("sync is {pending, total} with 0 <= pending <= total",
            isinstance(sync, dict) and is_int(sync.get("pending")) and is_int(sync.get("total"))
            and 0 <= sync["pending"] <= sync["total"], "got %r" % sync)
    r.check("service_panel is a boolean", is_bool(s.get("service_panel")), "got %r" % s.get("service_panel"))
    f = s.get("features")
    r.check("features is {answer_without_pin, customer_branding}",
            isinstance(f, dict) and is_bool(f.get("answer_without_pin")) and is_bool(f.get("customer_branding")),
            "got %r" % f)
    br = s.get("branding")
    r.check("branding is {title, theme: {}, brand: {base, shade}}",
            isinstance(br, dict) and is_str(br.get("title")) and isinstance(br.get("theme"), dict)
            and isinstance(br.get("brand"), dict) and is_str(br["brand"].get("base"))
            and br["brand"].get("shade") in SHADES, "got %r" % br)
    r.check("message is a string", is_str(s.get("message")), "got %r" % s.get("message"))
    return s


def check_reads(b, r, s):
    r.section("Other GET endpoints")

    status, _, h = b.get_json("/api/help")
    docs = h.get("docs") if isinstance(h, dict) else None
    ok = status == 200 and isinstance(docs, list) and all(
        isinstance(d, dict) and is_str(d.get("id")) and is_str(d.get("title"))
        and isinstance(d.get("pages"), list) and all(is_str(p) for p in d["pages"]) for d in docs)
    r.check("GET /api/help is {docs: [{id, title, pages: [url]}]}", ok, "status %s, got %r" % (status, h))
    if ok and docs and docs[0]["pages"]:
        page = docs[0]["pages"][0]
        st, headers, _ = b.request("GET", page)
        r.check("help page %s is served as an image" % page,
                st == 200 and headers.get("content-type", "").startswith("image/"),
                "status %s, content-type %r" % (st, headers.get("content-type")))

    status, _, a = b.get_json("/api/app-log")
    r.check("GET /api/app-log is {entries: [...]}",
            status == 200 and isinstance(a, dict) and isinstance(a.get("entries"), list),
            "status %s, got %r" % (status, a))

    status, _, cfg = b.get_json("/api/settings")
    if can(s, "settings"):
        ok = status == 200 and isinstance(cfg, dict)
        r.check("GET /api/settings answers 200 for this session", ok, "status %s" % status)
        if ok:
            want = sorted((l["id"], l["number"], l["color"]) for l in lanes_of(s))
            got = sorted((l.get("id"), l.get("number"), l.get("color"))
                         for l in cfg.get("lanes") or [] if isinstance(l, dict))
            r.check("settings.lanes match the lanes in status", want == got, "status %r, settings %r" % (want, got))
            cons = cfg.get("consoles")
            r.check("settings.consoles are {id, role, outputs, default_output, default_volume, noise_cancellation}",
                    isinstance(cons, list) and cons and all(
                        isinstance(x, dict) and is_str(x.get("id")) and x.get("role") in ("master", "operator")
                        and isinstance(x.get("outputs"), list)
                        and x.get("default_output") in [o.get("id") for o in x["outputs"] if isinstance(o, dict)]
                        and pct(x.get("default_volume")) and is_bool(x.get("noise_cancellation"))
                        for x in cons), "got %r" % cons)
            site = cfg.get("site")
            r.check("settings.site is {login_required, autolock_minutes, pin_length: 4}",
                    isinstance(site, dict) and is_bool(site.get("login_required"))
                    and is_int(site.get("autolock_minutes")) and site.get("pin_length") == 4, "got %r" % site)
            r.check("settings.lane_colors is the 16 lane colours", cfg.get("lane_colors") == LANE_COLORS,
                    "got %r" % cfg.get("lane_colors"))
            r.check("settings.tier1 is a list", isinstance(cfg.get("tier1"), list), "got %r" % cfg.get("tier1"))
            if can(s, "manage_users"):
                users = cfg.get("users")
                r.check("settings.users is [{name, role, builtin}] including %s" % BUILTIN_USER,
                        isinstance(users, list) and all(
                            isinstance(u, dict) and is_str(u.get("name")) and u.get("role") in ALL_ROLES
                            and is_bool(u.get("builtin")) for u in users)
                        and any(u.get("name") == BUILTIN_USER and u.get("builtin") for u in users),
                        "got %r" % users)
                r.check("no PIN or hash is ever sent", "pin" not in json.dumps(users).lower(),
                        "a user record contains a pin field")
            else:
                r.check("settings.users is left out without manage_users", "users" not in cfg,
                        "sent users to a session that can't manage them")
    else:
        r.check("GET /api/settings is 403 for this console/session", status == 403,
                "status %s (console %r, user %r)" % (status, (s.get("console") or {}).get("role"),
                                                     (s.get("session") or {}).get("user")))

    status, _, body = b.get_json("/api/no-such-endpoint")
    r.check("unknown GET /api/... answers 404", status == 404, "status %s" % status)


# ---------------------------------------------------------------- bench

def expect(r, name, got_status, body, want):
    ok = got_status == want
    if ok and want >= 400:
        ok = has_error_text(body)
        return r.check(name, ok, "no readable {\"error\": ...} text; the UI shows it to the operator")
    return r.check(name, ok, "status %s, want %s, body %r" % (got_status, want, body))


def check_malformed(b, r):
    r.section("Malformed requests are rejected with 400 (never clamped or guessed)")
    cases = [
        ("/api/call", {}, "missing lane"),
        ("/api/call", {"lane": 3}, "lane as a number"),
        ("/api/call/mic", {"muted": "yes"}, "muted as a string"),
        ("/api/call/mic", {}, "missing muted"),
        ("/api/call/volume", {"value": 101}, "volume 101"),
        ("/api/call/volume", {"value": -1}, "volume -1"),
        ("/api/call/volume", {"value": True}, "volume true"),
        ("/api/call/volume", {"value": 50.5}, "volume 50.5"),
        ("/api/call/output", {}, "missing output id"),
        ("/api/login/check", {"username": 5}, "username as a number"),
        ("/api/login", {"username": "x"}, "missing pin"),
        ("/api/login", {"username": "x", "pin": 1234}, "pin as a number"),
        ("/api/login", {"username": "x", "pin": "12345"}, "5-digit pin"),
        ("/api/auth", {"scope": "developer"}, "missing password"),
        ("/api/auth", {"scope": "root", "password": "x"}, "unknown scope"),
        ("/api/app-log", {"entries": "oops"}, "entries not a list"),
        ("/api/lane/restart-audio", {}, "missing lane"),
    ]
    for path, body, what in cases:
        st, res = b.post(path, body)
        expect(r, "POST %s with %s -> 400" % (path, what), st, res, 400)
    st, res = b.post("/api/call/mic", raw="{not json")
    expect(r, "POST with a body that isn't JSON -> 400", st, res, 400)
    st, res = b.post("/api/call/mic", raw="[true]")
    expect(r, "POST with a JSON body that isn't an object -> 400", st, res, 400)
    st, res = b.post("/api/no-such-endpoint", {})
    r.check("unknown POST /api/... answers 404", st == 404, "status %s" % st)


def check_session(b, r, args):
    r.section("Session")
    st, res = b.post("/api/login/check", {"username": "No Such Person Here"})
    r.check("login/check of an unknown name is {valid: false}",
            st == 200 and isinstance(res, dict) and res.get("valid") is False, "status %s, %r" % (st, res))
    st, res = b.post("/api/login/check", {"username": BUILTIN_USER})
    r.check("login/check answers {valid: bool}",
            st == 200 and isinstance(res, dict) and is_bool(res.get("valid")), "status %s, %r" % (st, res))
    st, res = b.post("/api/login", {"username": "No Such Person Here", "pin": "0000"})
    expect(r, "login with an unknown user -> 403", st, res, 403)
    st, res = b.post("/api/auth", {"scope": "developer", "password": "definitely-wrong"})
    expect(r, "developer auth with a wrong password -> 403", st, res, 403)
    if args.dev_password:
        st, res = b.post("/api/auth", {"scope": "developer", "password": args.dev_password})
        expect(r, "developer auth with the right password -> 200", st, res, 200)
    else:
        r.skip("developer auth with the right password", "pass --dev-password to check it")

    marker = "contract-test marker"
    st, res = b.post("/api/app-log", {"entries": [{"t": "2026-01-01T00:00:00Z", "level": "warn",
                                                   "message": marker, "source": "", "line": 0,
                                                   "column": 0, "stack": ""}]})
    expect(r, "POST /api/app-log accepts a batch", st, res, 200)
    _, _, log = b.get_json("/api/app-log")
    r.check("the entry comes back from GET /api/app-log",
            isinstance(log, dict) and any(isinstance(e, dict) and e.get("message") == marker
                                          for e in log.get("entries") or []), "not found")


def check_calls(b, r):
    r.section("Calls (bench: this places a real call)")
    s = b.status()

    for path, body in (("/api/call/hangup", {}), ("/api/call/hold", {}),
                       ("/api/call/mic", {"muted": True}), ("/api/call/volume", {"value": 50}),
                       ("/api/call/output", {"id": "speaker"})):
        if s.get("call"):
            break
        st, res = b.post(path, body)
        expect(r, "%s with no call -> 409" % path, st, res, 409)

    if s.get("call"):
        r.skip("call checks", "this console is already in a call")
        return
    if not (s.get("session") or {}).get("user"):
        r.skip("call checks", "the console is locked; pass --user and --pin")
        return
    lane = next((l for l in lanes_of(s) if l.get("health") != "error"
                 and (l.get("call") or {}).get("state") == "idle"), None)
    if not lane:
        r.skip("call checks", "no idle lane in service")
        return

    try:
        st, res = b.post("/api/call", {"lane": lane["id"]})
        if not expect(r, "POST /api/call to idle %s -> 200" % lane["id"], st, res, 200):
            return
        s = b.status()
        call = s.get("call") or {}
        r.check("status.call is now that lane", call.get("lane") == lane["id"], "got %r" % call)
        r.check("the lane reports 'active'",
                ((find_dev(s, lane["id"]) or {}).get("call") or {}).get("state") == "active",
                "got %r" % find_dev(s, lane["id"]))
        r.check("a call placed while logged in is not restricted", call.get("restricted") is False,
                "got %r" % call.get("restricted"))

        other = next((l for l in lanes_of(s) if l["id"] != lane["id"]), None)
        if other:
            st, res = b.post("/api/call", {"lane": other["id"]})
            expect(r, "a second call while in a call -> 409", st, res, 409)
        st, res = b.post("/api/reboot", {})
        expect(r, "reboot during a call -> 409", st, res, 409)
        st, res = b.post("/api/lock", {})
        r.check("lock during a call does not end or lock the call",
                (b.status().get("call") or {}).get("lane") == lane["id"], "call was lost")

        st, res = b.post("/api/call/mic", {"muted": True})
        expect(r, "mute the mic -> 200", st, res, 200)
        r.check("call.mic_muted follows", (b.status().get("call") or {}).get("mic_muted") is True, "")
        st, res = b.post("/api/call/volume", {"value": 65})
        expect(r, "set call volume 65 -> 200", st, res, 200)
        r.check("call.volume follows", (b.status().get("call") or {}).get("volume") == 65, "")
        st, res = b.post("/api/call/output", {"id": "no-such-output"})
        expect(r, "an output that isn't connected -> 400", st, res, 400)
        st, res = b.post("/api/call/output", {"id": "speaker"})
        expect(r, "output 'speaker' -> 200", st, res, 200)

        st, res = b.post("/api/call/hold", {})
        expect(r, "hold -> 200", st, res, 200)
        s = b.status()
        r.check("hold hangs up: call is null", s.get("call") is None, "got %r" % s.get("call"))
        r.check("hold leaves the lane 'held'",
                ((find_dev(s, lane["id"]) or {}).get("call") or {}).get("state") == "held",
                "got %r" % find_dev(s, lane["id"]))

        st, res = b.post("/api/call", {"lane": lane["id"]})
        expect(r, "calling a held lane rejoins it -> 200", st, res, 200)
        call = b.status().get("call") or {}
        r.check("a new call starts with the mic live", call.get("mic_muted") is False, "got %r" % call)

        st, res = b.post("/api/call/hangup", {})
        expect(r, "hang up -> 200", st, res, 200)
        s = b.status()
        r.check("after hang-up: call is null and the lane is idle",
                s.get("call") is None and ((find_dev(s, lane["id"]) or {}).get("call") or {}).get("state") == "idle",
                "call %r, lane %r" % (s.get("call"), find_dev(s, lane["id"])))
    finally:
        if b.status().get("call"):
            b.post("/api/call/hangup", {})


def check_settings(b, r):
    r.section("Settings (bench: changes are put back)")
    s = b.status()
    if not can(s, "settings"):
        for path in ("/api/settings/lane", "/api/settings/site", "/api/config/save"):
            st, res = b.post(path, {})
            expect(r, "%s without the settings capability -> 403" % path, st, res, 403)
        return

    st, _, cfg = b.get_json("/api/settings")
    if st != 200 or not isinstance(cfg, dict):
        r.skip("settings checks", "GET /api/settings answered %s" % st)
        return
    lanes = cfg.get("lanes") or []
    site = cfg.get("site") or {}
    if not lanes:
        r.skip("settings checks", "no lanes")
        return
    lane = lanes[0]

    if s.get("service_panel"):
        st, res = b.post("/api/settings/lane", {"lane": lane["id"], "color": lane["color"]})
        expect(r, "lane settings with a Service Panel -> 409", st, res, 409)
    else:
        for body, what in (({"lane": lane["id"], "number": 0}, "number 0"),
                           ({"lane": lane["id"], "number": 17}, "number 17"),
                           ({"lane": lane["id"], "number": "3"}, "number as a string"),
                           ({"lane": lane["id"], "color": "chartreuse"}, "an unknown colour")):
            st, res = b.post("/api/settings/lane", body)
            expect(r, "lane setting with %s -> 400" % what, st, res, 400)
        if len(lanes) > 1:
            st, res = b.post("/api/settings/lane", {"lane": lane["id"], "number": lanes[1]["number"]})
            expect(r, "lane number already used by another lane -> 400", st, res, 400)
        new_color = next(c for c in LANE_COLORS if c != lane["color"])
        try:
            st, res = b.post("/api/settings/lane", {"lane": lane["id"], "color": new_color})
            expect(r, "change a lane colour -> 200", st, res, 200)
            r.check("the new colour shows in status",
                    (find_dev(b.status(), lane["id"]) or {}).get("color") == new_color, "")
            free = next(n for n in range(16, 0, -1) if n not in [l["number"] for l in lanes])
            st, res = b.post("/api/settings/lane", {"lane": lane["id"], "number": free})
            expect(r, "renumber a lane to an unused number (%d) -> 200" % free, st, res, 200)
            r.check("the new number shows in status",
                    (find_dev(b.status(), lane["id"]) or {}).get("number") == free, "")
            sync = b.status().get("sync") or {}
            r.check("status.sync reports the change propagating (or already done)",
                    is_int(sync.get("pending")) and is_int(sync.get("total")), "got %r" % sync)
        finally:
            b.post("/api/settings/lane", {"lane": lane["id"], "number": lane["number"], "color": lane["color"]})

    for body, what in (({"autolock_minutes": 14}, "auto-lock 14 min"),
                       ({"autolock_minutes": 481}, "auto-lock 481 min"),
                       ({"login_required": "no"}, "login_required as a string")):
        st, res = b.post("/api/settings/site", body)
        expect(r, "site setting with %s -> 400" % what, st, res, 400)
    try:
        st, res = b.post("/api/settings/site", {"autolock_minutes": 120})
        expect(r, "set auto-lock to 120 min -> 200", st, res, 200)
        r.check("session.autolock_minutes follows",
                (b.status().get("session") or {}).get("autolock_minutes") == 120, "")
    finally:
        b.post("/api/settings/site", {"autolock_minutes": site.get("autolock_minutes", 150)})

    cons = (cfg.get("consoles") or [None])[0]
    if cons:
        for body, what in (({"console": cons["id"], "default_volume": 101}, "default volume 101"),
                           ({"console": cons["id"], "default_output": "no-such-output"}, "an unconnected output"),
                           ({"console": cons["id"], "noise_cancellation": 1}, "noise_cancellation as a number")):
            st, res = b.post("/api/settings/console", body)
            expect(r, "console setting with %s -> 400" % what, st, res, 400)
        try:
            st, res = b.post("/api/settings/console", {"console": cons["id"], "default_volume": 45})
            expect(r, "set a console default volume -> 200", st, res, 200)
        finally:
            b.post("/api/settings/console", {"console": cons["id"], "default_volume": cons["default_volume"]})

    if can(s, "manage_users"):
        for body, what in (({"name": "Contract Test", "pin": "12a4", "role": "user"}, "a non-digit PIN"),
                           ({"name": "Contract Test", "pin": "123", "role": "user"}, "a 3-digit PIN"),
                           ({"name": "Contract Test", "pin": "1234", "role": "god"}, "an unknown role"),
                           ({"name": "", "pin": "1234", "role": "user"}, "an empty name")):
            st, res = b.post("/api/users", body)
            expect(r, "create user with %s -> 400" % what, st, res, 400)
        st, res = b.post("/api/users", {"name": BUILTIN_USER.upper(), "pin": "1234", "role": "user"})
        expect(r, "create a user whose name exists (any case) -> 409", st, res, 409)
        st, res = b.post("/api/users/update", {"name": BUILTIN_USER, "role": "user"})
        expect(r, "change the built-in user -> 409", st, res, 409)
    else:
        st, res = b.post("/api/users", {"name": "Contract Test", "pin": "1234", "role": "user"})
        expect(r, "create a user without manage_users -> 403", st, res, 403)

    st, res = b.post("/api/lane/restart-audio", {"lane": "no-such-lane"})
    if can(s, "restart_lane_audio"):
        expect(r, "restart audio on a lane that doesn't exist -> 400", st, res, 400)
    else:
        expect(r, "restart lane audio without the capability -> 403", st, res, 403)

    st, res = b.post("/api/config/save", {})
    if st == 409:
        r.check("config save: nowhere to write it -> 409 with a reason", has_error_text(res), "")
    else:
        r.check("config save -> 200 with a file name",
                st == 200 and isinstance(res, dict) and is_str(res.get("file")), "status %s, %r" % (st, res))

    st, res = b.post("/api/logs/export", {})
    if st == 409:
        r.check("log export: nowhere to write it -> 409 with a reason", has_error_text(res), "")
    else:
        r.check("log export -> 200 with a file name",
                st == 200 and isinstance(res, dict) and is_str(res.get("file")), "status %s, %r" % (st, res))


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--bench", action="store_true",
                    help="also place a call and change settings (bench systems only)")
    ap.add_argument("--user", help="log in as this user first, if the console is locked")
    ap.add_argument("--pin", help="that user's PIN")
    ap.add_argument("--dev-password", help="check that the Developer password is accepted")
    args = ap.parse_args()

    b = Backend(args.url, args.timeout)
    r = Report()
    try:
        check_static(b, r)
        s = check_status(b, r)
        if s is None:
            raise SystemExit(1)
        check_reads(b, r, s)
        if args.bench:
            if args.user:
                st, res = b.post("/api/login", {"username": args.user, "pin": args.pin or ""})
                if st != 200:
                    print("\nlogin as %r failed: %s %r" % (args.user, st, res))
                    return 1
            check_malformed(b, r)
            check_session(b, r, args)
            check_calls(b, r)
            check_settings(b, r)
    except Unreachable as exc:
        print("\ncannot reach the backend at %s: %s" % (args.url, exc))
        return 2

    print("\n%d passed, %d failed, %d skipped" % (r.passed, len(r.failed), len(r.skipped)))
    if not args.bench:
        print("(read-only run; --bench also places a call and changes settings, on a bench system)")
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
