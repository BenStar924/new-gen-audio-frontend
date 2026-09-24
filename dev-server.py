#!/usr/bin/env python3
"""HUBS Audio console, mock backend, API v2.

The reference implementation of API.md. It serves www/ and implements the
whole /api contract against in-memory state, with no hardware, so the UI can
be developed and demoed on a laptop and the backend engineer can read what
every endpoint does.

    python3 dev-server.py                          # http://127.0.0.1:8000
    python3 dev-server.py 8000 --lanes 12          # up to 16 devices in total
    python3 dev-server.py --role operator          # this console is an Operator
    python3 dev-server.py --login-required         # start at the Locked screen
    python3 dev-server.py --demo                   # lanes ring on their own

A site is only fully observable with several consoles, a Master Controller and
customers pressing call buttons. The /api/sim/* endpoints produce those states
on demand: ringing, another console's call, faults, role changes, a missing USB
drive, and so on. They are for development only. The real backend has none of
them, and contract-test.py does not need them.

Mock credentials: developer password 9999. Users (name / PIN / role):
Alex Teller / 1111 / user, Sam Tech / 2222 / technician,
Pat Admin / 3333 / admin, Jo Owner / 4444 / owner,
Former Teller / 5555 / disabled.

This is a development tool. It is not deployed to the device.
"""

import argparse
import copy
import hashlib
import json
import os
import random
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler

WWW_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www")

API_VERSION = 2
MAX_DEVICES = 16                  # lanes + consoles combined, a hard limit

# Placeholders for Hamilton's 16 pneumatic LED colours (not yet supplied).
LANE_COLORS = ["red", "orange", "amber", "yellow", "lime", "green", "teal", "cyan",
               "sky", "blue", "indigo", "violet", "purple", "magenta", "pink", "white"]

USER_ROLES = ["user", "technician", "admin", "owner"]
ALL_ROLES = USER_ROLES + ["disabled"]
BUILTIN_USER = "Hamilton Audio"   # the MVP auto-login account
DEV_PASSWORD = "9999"

# The provisional capability matrix. It must match CAPABILITIES in app.js.
CAPABILITIES = {
    "troubleshooting":    (None,     USER_ROLES),
    "settings":           ("master", ["technician", "admin", "owner"]),
    "manage_users":       ("master", ["admin", "owner"]),
    "chain_diagram":      ("master", USER_ROLES),
    "restart_lane_audio": ("master", ["technician", "admin", "owner"]),
}

LOCK = threading.RLock()


# ---------------------------------------------------------------- errors

class ApiError(Exception):
    status = 400

    def __init__(self, text):
        super().__init__(text)
        self.text = text


class BadRequest(ApiError):
    status = 400


class Forbidden(ApiError):
    status = 403


class Conflict(ApiError):
    status = 409


def need(body, key, kind):
    """The field if present and of the right JSON type, else 400. bool is not
    an int here: JSON `true` is not a number."""
    if key not in body:
        raise BadRequest("%s is required" % key)
    value = body[key]
    ok = {"int": type(value) is int, "bool": type(value) is bool,
          "str": isinstance(value, str), "dict": isinstance(value, dict),
          "list": isinstance(value, list)}[kind]
    if not ok:
        raise BadRequest("%s has the wrong type" % key)
    return value


def need_pct(body, key):
    value = need(body, key, "int")
    if not 0 <= value <= 100:
        raise BadRequest("%s must be 0-100" % key)
    return value


def need_pin(body, key="pin"):
    pin = need(body, key, "str")
    if len(pin) != 4 or not pin.isdigit():
        raise BadRequest("PIN must be exactly 4 digits")
    return pin


# ---------------------------------------------------------------- state

state = {}
users = []
app_log = []
ops_log = []
backup = {"snapshot": None}


def hash_pin(pin, salt):
    return hashlib.sha256((salt + pin).encode("utf-8")).hexdigest()


def make_user(name, pin, role, builtin=False):
    salt = os.urandom(8).hex()
    return {"name": name, "role": role, "builtin": builtin,
            "salt": salt, "pin_hash": hash_pin(pin, salt) if pin else None}


def find_user(name):
    name = (name or "").strip().lower()
    for u in users:
        if u["name"].lower() == name:
            return u
    return None


def log(text, level="info"):
    ops_log.append({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": level,
                    "device": state.get("console_id"), "text": text})
    del ops_log[:-1000]


def build_site(lanes, consoles, role):
    """Lay devices out along an A2B chain: the Master Console sits closest to
    the Master Controller (position 0), then lanes, with the other consoles
    spread between them."""
    lanes = max(1, min(lanes, MAX_DEVICES - consoles))
    devices = []
    console_devs = []
    for i in range(consoles):
        console_devs.append({
            "id": "console-%d" % (i + 1), "kind": "console",
            "role": "master" if i == 0 else "operator",
            "health": "ok", "codes": [],
            "outputs": [{"id": "speaker", "name": "Built-in speaker"}],
            "default_output": "speaker", "default_volume": 60,
            "noise_cancellation": True,
        })
    lane_devs = [{
        "id": "lane-%d" % (i + 1), "kind": "lane", "number": i + 1,
        "color": LANE_COLORS[i % len(LANE_COLORS)],
        "health": "ok", "codes": [], "call": {"state": "idle", "since": None},
        "restarting_until": None,
    } for i in range(lanes)]

    devices.append(console_devs[0])
    others = console_devs[1:]
    step = max(1, len(lane_devs) // (len(others) + 1)) if others else len(lane_devs)
    for i, lane in enumerate(lane_devs):
        devices.append(lane)
        if others and (i + 1) % step == 0:
            devices.append(others.pop(0))
    devices.extend(others)
    for pos, d in enumerate(devices):
        d["position"] = pos

    # A headset on this console, so the output switch has something to switch.
    me_index = 0 if role == "master" else 1 if consoles > 1 else 0
    me = console_devs[me_index]
    me["outputs"].append({"id": "headset", "name": "Headset"})
    if role == "operator" and consoles > 1:
        # This console is an operator; console-1 stays the Master.
        pass
    return devices, me["id"]


def reset_site(args):
    global users
    devices, me_id = build_site(args.lanes, args.consoles, args.role)
    state.clear()
    state.update({
        "devices": devices,
        "console_id": me_id,
        "i2c": True, "a2b": True, "master_link": True,
        "call": None,
        "login_required": bool(args.login_required),
        "autolock_minutes": 150,
        "session_user": None,
        "last_user": BUILTIN_USER,
        "features": {"answer_without_pin": False, "customer_branding": False},
        "branding": {"title": "", "theme": {}, "brand": {"base": "", "shade": "base"}},
        "service_panel": False,
        "usb": True,
        "sync_changed": None,
        "message": "",
    })
    if args.role == "operator" and args.consoles < 2:
        # An operator implies a Master somewhere else on the chain.
        me = find_device(me_id)
        me["role"] = "operator"
    users = [
        make_user(BUILTIN_USER, None, "owner", builtin=True),
        make_user("Alex Teller", "1111", "user"),
        make_user("Sam Tech", "2222", "technician"),
        make_user("Pat Admin", "3333", "admin"),
        make_user("Jo Owner", "4444", "owner"),
        make_user("Former Teller", "5555", "disabled"),
    ]
    if not state["login_required"]:
        state["session_user"] = find_user(BUILTIN_USER)
    del app_log[:]
    del ops_log[:]
    backup["snapshot"] = None
    log("Console started")


def find_device(dev_id):
    for d in state["devices"]:
        if d["id"] == dev_id:
            return d
    return None


def me():
    return find_device(state["console_id"])


def lanes():
    return [d for d in state["devices"] if d["kind"] == "lane"]


def consoles():
    return [d for d in state["devices"] if d["kind"] == "console"]


def lane_label(lane):
    return "Lane %d" % lane["number"]


def can(capability):
    device_role, roles = CAPABILITIES[capability]
    user = state["session_user"]
    if user is None or user["role"] == "disabled":
        return False
    if device_role and me()["role"] != device_role:
        return False
    return user["role"] in roles


def mark_sync():
    state["sync_changed"] = time.time()


def sync_status():
    total = max(0, len(consoles()) - 1)        # every console but the Master
    changed = state["sync_changed"]
    if changed is None or total == 0:
        return {"pending": 0, "total": total}
    acked = int((time.time() - changed) / 0.8)  # one acknowledgment per 0.8 s
    return {"pending": max(0, total - acked), "total": total}


def tick():
    """Advance time-based simulation: lane audio restarts finishing."""
    now = time.time()
    for lane in lanes():
        until = lane.get("restarting_until")
        if until and now >= until:
            lane["restarting_until"] = None
            lane["codes"] = [c for c in lane["codes"] if c["code"] != "AUDIO-RESTART"]
            lane["health"] = worst(lane["codes"])


def worst(codes):
    sev = [c.get("severity") for c in codes]
    return "error" if "error" in sev else "warning" if "warning" in sev else "ok"


def status_payload():
    tick()
    now = time.time()
    user = state["session_user"]
    out_devices = []
    for d in state["devices"]:
        item = {"id": d["id"], "kind": d["kind"], "position": d["position"],
                "health": d["health"], "codes": d["codes"]}
        if d["kind"] == "lane":
            item.update({"number": d["number"], "color": d["color"], "call": dict(d["call"])})
        else:
            item["role"] = d["role"]
        out_devices.append(item)
    out_devices.sort(key=lambda x: x["position"])
    mine = me()
    return {
        "api": API_VERSION,
        "now": now,
        "console": {"id": mine["id"], "role": mine["role"], "i2c": state["i2c"],
                    "a2b": state["a2b"],
                    "master_link": True if mine["role"] == "master" else state["master_link"]},
        "devices": out_devices,
        "call": dict(state["call"]) if state["call"] else None,
        "outputs": [dict(o) for o in mine["outputs"]],
        "session": {"user": {"name": user["name"], "role": user["role"]} if user else None,
                    "login_required": state["login_required"],
                    "last_user": state["last_user"], "pin_length": 4,
                    "autolock_minutes": state["autolock_minutes"]},
        "sync": sync_status(),
        "service_panel": state["service_panel"],
        "features": dict(state["features"]),
        "branding": copy.deepcopy(state["branding"]),
        "message": state["message"],
    }


def settings_payload():
    data = {
        "lanes": [{"id": l["id"], "number": l["number"], "color": l["color"]}
                  for l in sorted(lanes(), key=lambda l: l["number"])],
        "consoles": [{"id": c["id"], "position": c["position"], "role": c["role"],
                      "outputs": [dict(o) for o in c["outputs"]],
                      "default_output": c["default_output"],
                      "default_volume": c["default_volume"],
                      "noise_cancellation": c["noise_cancellation"]}
                     for c in sorted(consoles(), key=lambda c: c["position"])],
        "site": {"login_required": state["login_required"],
                 "autolock_minutes": state["autolock_minutes"], "pin_length": 4},
        "lane_colors": list(LANE_COLORS),
        "tier1": [],
    }
    if can("manage_users"):
        data["users"] = [{"name": u["name"], "role": u["role"], "builtin": u["builtin"]}
                         for u in users]
    return data


# ---------------------------------------------------------------- commands

def require_call():
    if not state["call"]:
        raise Conflict("No call in progress")
    return state["call"]


def require_settings(capability="settings"):
    if me()["role"] != "master":
        raise Forbidden("Settings are only available on the Master Console")
    if not can(capability):
        raise Forbidden("Your account can't change this")


def end_call(new_state):
    call = state["call"]
    lane = find_device(call["lane"])
    if lane:
        lane["call"] = {"state": new_state, "since": time.time() if new_state == "held" else None}
    state["call"] = None


def cmd_call(body):
    lane_id = need(body, "lane", "str")
    lane = find_device(lane_id)
    if not lane or lane["kind"] != "lane":
        raise Conflict("That lane is no longer on the system")
    if state["call"]:
        raise Conflict("Already in a call")
    st = lane["call"]["state"]
    restricted = False
    if state["session_user"] is None:
        if state["features"]["answer_without_pin"] and st == "ringing":
            restricted = True
        else:
            raise Forbidden("Log in to take calls")
    if lane["health"] == "error":
        raise Conflict("%s is out of service" % lane_label(lane))
    if st == "busy":
        raise Conflict("%s is in a call on another console" % lane_label(lane))
    if st == "active":
        raise Conflict("Already in a call")
    mine = me()
    outputs = [o["id"] for o in mine["outputs"]]
    lane["call"] = {"state": "active", "since": time.time()}
    state["call"] = {"lane": lane["id"], "since": time.time(), "mic_muted": False,
                     "volume": mine["default_volume"],
                     "output": mine["default_output"] if mine["default_output"] in outputs else "speaker",
                     "restricted": restricted}
    log("%s %s" % ("Answered" if st == "ringing" else "Called", lane_label(lane)))
    return {}


def dispatch(path, body):
    # ---- calls
    if path == "/api/call":
        return cmd_call(body)
    if path == "/api/call/hangup":
        require_call()
        lane = find_device(state["call"]["lane"])
        end_call("idle")
        log("Hung up %s" % (lane_label(lane) if lane else "call"))
        return {}
    if path == "/api/call/hold":
        call = require_call()
        if call["restricted"]:
            raise Forbidden("Log in to put a call on hold")
        lane = find_device(call["lane"])
        end_call("held")
        log("%s on hold" % (lane_label(lane) if lane else "Call"))
        return {}
    if path == "/api/call/mic":
        muted = need(body, "muted", "bool")
        require_call()["mic_muted"] = muted
        return {}
    if path == "/api/call/volume":
        value = need_pct(body, "value")
        require_call()["volume"] = value
        return {}
    if path == "/api/call/output":
        out = need(body, "id", "str")
        call = require_call()
        if out not in [o["id"] for o in me()["outputs"]]:
            raise BadRequest("That audio output isn't connected")
        call["output"] = out
        return {}

    # ---- session
    if path == "/api/login/check":
        u = find_user(need(body, "username", "str"))
        return {"valid": bool(u and u["role"] != "disabled" and not u["builtin"])}
    if path == "/api/login":
        name = need(body, "username", "str")
        pin = need_pin(body)
        u = find_user(name)
        if not u or u["builtin"] or u["role"] == "disabled" or u["pin_hash"] != hash_pin(pin, u["salt"]):
            log("Failed login for %r" % name, "warn")
            raise Forbidden("Incorrect username or PIN")
        state["session_user"] = u
        state["last_user"] = u["name"]
        log("%s logged in" % u["name"])
        return {"user": {"name": u["name"], "role": u["role"]}}
    if path == "/api/lock":
        if state["call"]:
            raise Conflict("Can't lock during a call")
        if state["login_required"] and state["session_user"]:
            log("%s locked (auto-lock)" % state["session_user"]["name"])
            state["session_user"] = None
        return {}

    # ---- this console
    if path == "/api/reboot":
        if state["call"]:
            raise Conflict("Hang up before restarting")
        log("Software restart requested")
        return {}
    if path == "/api/logs/export":
        if not state["usb"]:
            raise Conflict("No USB drive found. Insert one and try again.")
        name = "hubs-%s-log-%s.txt" % (state["console_id"], time.strftime("%Y%m%d-%H%M%S"))
        log("Log exported to %s" % name)
        return {"file": name}

    # ---- master only
    if path == "/api/lane/restart-audio":
        lane = find_device(need(body, "lane", "str"))
        if me()["role"] != "master":
            raise Forbidden("Only the Master Console can restart lane audio")
        if not can("restart_lane_audio"):
            raise Forbidden("Your account can't restart lane audio")
        if not lane or lane["kind"] != "lane":
            raise BadRequest("no such lane")
        if state["call"] and state["call"]["lane"] == lane["id"]:
            end_call("idle")
        lane["call"] = {"state": "idle", "since": None}
        lane["codes"] = [c for c in lane["codes"] if c["code"] != "AUDIO-RESTART"] + [
            {"code": "AUDIO-RESTART", "severity": "warning", "text": "Audio restarting"}]
        lane["health"] = worst(lane["codes"])
        lane["restarting_until"] = time.time() + 5
        log("Restarted audio on %s" % lane_label(lane))
        return {}

    # ---- developer
    if path == "/api/auth":
        scope = need(body, "scope", "str")
        password = need(body, "password", "str")
        if scope != "developer":
            raise BadRequest("unknown scope")
        if password != DEV_PASSWORD:
            log("Failed developer unlock", "warn")
            raise Forbidden("Incorrect password")
        log("Developer screen unlocked")
        return {}
    if path == "/api/app-log":
        entries = need(body, "entries", "list")
        for e in entries[:50]:
            if not isinstance(e, dict) or not isinstance(e.get("message"), str):
                raise BadRequest("each entry needs a message")
            app_log.append({k: e.get(k) for k in
                            ("t", "level", "message", "source", "line", "column", "stack")})
        del app_log[:-500]
        return {}

    # ---- settings (master only)
    if path == "/api/settings/lane":
        require_settings()
        if state["service_panel"]:
            raise Conflict("Lane numbers and colours come from the Service Panel")
        lane = find_device(need(body, "lane", "str"))
        if not lane or lane["kind"] != "lane":
            raise BadRequest("no such lane")
        number = need(body, "number", "int") if "number" in body else None
        color = need(body, "color", "str") if "color" in body else None
        if number is not None:
            if not 1 <= number <= 16:
                raise BadRequest("Lane numbers are 1 to 16")
            if any(l is not lane and l["number"] == number for l in lanes()):
                raise BadRequest("Lane %d is already used" % number)
        if color is not None and color not in LANE_COLORS:
            raise BadRequest("unknown lane colour")
        if number is not None:
            lane["number"] = number
        if color is not None:
            lane["color"] = color
        mark_sync()
        return {}
    if path == "/api/settings/console":
        require_settings()
        c = find_device(need(body, "console", "str"))
        if not c or c["kind"] != "console":
            raise BadRequest("no such console")
        out = need(body, "default_output", "str") if "default_output" in body else None
        vol = need_pct(body, "default_volume") if "default_volume" in body else None
        nc = need(body, "noise_cancellation", "bool") if "noise_cancellation" in body else None
        if out is not None and out not in [o["id"] for o in c["outputs"]]:
            raise BadRequest("That output isn't connected to this console")
        if out is not None:
            c["default_output"] = out
        if vol is not None:
            c["default_volume"] = vol
        if nc is not None:
            c["noise_cancellation"] = nc
        mark_sync()
        return {}
    if path == "/api/settings/site":
        require_settings()
        req = need(body, "login_required", "bool") if "login_required" in body else None
        mins = need(body, "autolock_minutes", "int") if "autolock_minutes" in body else None
        if mins is not None and not 15 <= mins <= 480:
            raise BadRequest("Auto-lock is 15 to 480 minutes")
        if req is True and not any(u["role"] in ("admin", "owner") and not u["builtin"] for u in users):
            # Otherwise turning login on locks every console with no way back in.
            raise Conflict("Create an admin or owner account before requiring login")
        if mins is not None:
            state["autolock_minutes"] = mins
        if req is not None:
            state["login_required"] = req
            if not req:
                state["session_user"] = find_user(BUILTIN_USER)
        mark_sync()
        return {}
    if path == "/api/users":
        require_settings("manage_users")
        name = need(body, "name", "str").strip()
        pin = need_pin(body)
        role = need(body, "role", "str")
        if not 1 <= len(name) <= 32:
            raise BadRequest("Names are 1 to 32 characters")
        if role not in USER_ROLES:
            raise BadRequest("unknown role")
        if find_user(name):
            raise Conflict("%s already exists" % name)
        users.append(make_user(name, pin, role))
        log("User %s created (%s)" % (name, role))
        mark_sync()
        return {}
    if path == "/api/users/update":
        require_settings("manage_users")
        u = find_user(need(body, "name", "str"))
        role = need(body, "role", "str") if "role" in body else None
        pin = need_pin(body) if "pin" in body else None
        if not u:
            raise BadRequest("no such user")
        if u["builtin"]:
            raise Conflict("The built-in account can't be changed")
        if role is not None and role not in ALL_ROLES:
            raise BadRequest("unknown role")
        if role is not None:
            u["role"] = role
            log("User %s is now %s" % (u["name"], role))
        if pin is not None:
            u["salt"] = os.urandom(8).hex()
            u["pin_hash"] = hash_pin(pin, u["salt"])
            log("PIN reset for %s" % u["name"])
        mark_sync()
        return {}
    if path == "/api/config/save":
        require_settings()
        backup["snapshot"] = {
            "lanes": [(l["id"], l["number"], l["color"]) for l in lanes()],
            "consoles": [(c["id"], c["default_output"], c["default_volume"], c["noise_cancellation"])
                         for c in consoles()],
            "users": copy.deepcopy(users),
            "site": (state["login_required"], state["autolock_minutes"]),
        }
        name = "hubs-config-%s.json" % time.strftime("%Y%m%d-%H%M%S")
        log("Configuration saved to %s" % name)
        return {"file": name}
    if path == "/api/config/restore":
        require_settings()
        snap = backup["snapshot"]
        if not snap:
            raise Conflict("No saved configuration found")
        for lid, number, color in snap["lanes"]:
            l = find_device(lid)
            if l:
                l["number"], l["color"] = number, color
        for cid, out, vol, nc in snap["consoles"]:
            c = find_device(cid)
            if c:
                c["default_output"], c["default_volume"], c["noise_cancellation"] = out, vol, nc
        users[:] = copy.deepcopy(snap["users"])
        state["login_required"], state["autolock_minutes"] = snap["site"]
        log("Configuration restored")
        mark_sync()
        return {}

    # ---- customer branding (later version; behind features.customer_branding)
    if path == "/api/title":
        state["branding"]["title"] = need(body, "text", "str")[:31]
        return {}
    if path == "/api/brand":
        shade = need(body, "shade", "str")
        if shade not in ("light", "base", "dark"):
            raise BadRequest("bad shade")
        theme = need(body, "theme", "dict")
        base = body.get("base", "")
        if not isinstance(base, str):
            raise BadRequest("base must be a string")
        state["branding"]["theme"] = theme
        state["branding"]["brand"] = {"base": base[:7], "shade": shade}
        return {}

    # ---- simulation (development only; not part of the contract)
    if path.startswith("/api/sim/"):
        return dispatch_sim(path[len("/api/sim/"):], body)

    return None


def dispatch_sim(name, body):
    if name == "lane":
        lane = find_device(need(body, "lane", "str"))
        if not lane or lane["kind"] != "lane":
            raise BadRequest("no such lane")
        if "state" in body:
            st = need(body, "state", "str")
            if st not in ("idle", "ringing", "busy", "held"):
                raise BadRequest("bad state")
            if state["call"] and state["call"]["lane"] == lane["id"]:
                state["call"] = None
            lane["call"] = {"state": st, "since": time.time() - body.get("ago", 0) if st != "idle" else None}
        if "codes" in body:
            lane["codes"] = need(body, "codes", "list")
            lane["health"] = worst(lane["codes"])
        return {}
    if name == "device":
        d = find_device(need(body, "device", "str"))
        if not d:
            raise BadRequest("no such device")
        d["codes"] = need(body, "codes", "list")
        d["health"] = worst(d["codes"])
        return {}
    if name == "console":
        for key in ("i2c", "a2b", "master_link"):
            if key in body:
                state[key] = need(body, key, "bool")
        if "role" in body:
            role = need(body, "role", "str")
            if role not in ("master", "operator"):
                raise BadRequest("bad role")
            mine = me()
            if role == "operator":
                others = [c for c in consoles() if c is not mine]
                if not others:
                    raise BadRequest("an operator needs another console to be the Master")
                for c in consoles():
                    c["role"] = "operator"
                others[0]["role"] = "master"
            else:
                for c in consoles():
                    c["role"] = "operator"
                mine["role"] = "master"
        return {}
    if name == "outputs":
        outs = need(body, "outputs", "list")
        mine = me()
        mine["outputs"] = [{"id": "speaker", "name": "Built-in speaker"}] + [
            o for o in outs if isinstance(o, dict) and o.get("id") != "speaker"]
        ids = [o["id"] for o in mine["outputs"]]
        if state["call"] and state["call"]["output"] not in ids:
            state["call"]["output"] = "speaker"
        if mine["default_output"] not in ids:
            mine["default_output"] = "speaker"
        return {}
    if name == "features":
        for key in ("answer_without_pin", "customer_branding"):
            if key in body:
                state["features"][key] = need(body, key, "bool")
        return {}
    if name == "usb":
        state["usb"] = need(body, "present", "bool")
        return {}
    if name == "service-panel":
        state["service_panel"] = need(body, "on", "bool")
        return {}
    if name == "sync":
        mark_sync()
        return {}
    if name == "theme":
        state["branding"]["theme"] = body.get("theme") or {}
        return {}
    if name == "login":
        # Put the console in a given session state without a PIN, for screenshots.
        if "required" in body:
            state["login_required"] = need(body, "required", "bool")
        if "user" in body:
            u = find_user(body["user"]) if body["user"] else None
            state["session_user"] = u
            if u:
                state["last_user"] = u["name"]
        return {}
    if name == "reset":
        reset_site(ARGS)
        return {}
    raise BadRequest("unknown simulation")


# ---------------------------------------------------------------- help pages

HELP_DOCS = [
    {"id": "quick-start", "title": "Quick start", "pages": 2},
    {"id": "troubleshooting", "title": "Troubleshooting guide", "pages": 3},
]


def help_index():
    return {"docs": [{"id": d["id"], "title": d["title"],
                      "pages": ["/help/%s-%d.svg" % (d["id"], n + 1) for n in range(d["pages"])]}
                     for d in HELP_DOCS]}


def help_page_svg(doc_id, page):
    doc = next((d for d in HELP_DOCS if d["id"] == doc_id), None)
    if not doc or not 1 <= page <= doc["pages"]:
        return None
    lines = "".join(
        '<rect x="70" y="%d" width="%d" height="10" rx="3" fill="#D5D9E6"/>' % (190 + i * 26, 460 - (i * 37) % 140)
        for i in range(9))
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 800" width="600" height="800">'
            '<rect width="600" height="800" fill="#fff"/>'
            '<rect x="70" y="70" width="44" height="44" rx="9" fill="#2B2A5E"/>'
            '<path d="M82 80h7v10h7V80h7v24h-7V96h-7v8h-7z" fill="#fff"/>'
            '<text x="130" y="100" font-family="sans-serif" font-size="26" font-weight="700" fill="#16162E">%s</text>'
            '<text x="70" y="160" font-family="sans-serif" font-size="16" fill="#6B6E8C">'
            'Placeholder page %d of %d. Real help is rendered from the PDF at install.</text>'
            '%s</svg>') % (doc["title"], page, doc["pages"], lines)


# ---------------------------------------------------------------- demo

def demo_loop():
    """Lanes ring on their own: roughly every 25 s an idle lane rings, and an
    unanswered ring gives up after 45 s. Lets someone watch the LEDs without
    driving the simulator by hand."""
    while True:
        time.sleep(1)
        with LOCK:
            now = time.time()
            for lane in lanes():
                c = lane["call"]
                if c["state"] == "ringing" and c["since"] and now - c["since"] > 45:
                    lane["call"] = {"state": "idle", "since": None}
                    log("Missed call on %s" % lane_label(lane), "warn")
            if random.random() < 1 / 25.0:
                idle = [l for l in lanes() if l["call"]["state"] == "idle" and l["health"] != "error"]
                if idle:
                    lane = random.choice(idle)
                    lane["call"] = {"state": "ringing", "since": now}


# ---------------------------------------------------------------- http

class ConsoleHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WWW_ROOT, **kwargs)

    def do_GET(self):
        path = self.path.split("?")[0]
        with LOCK:
            if path == "/api/status":
                return self.send_json(status_payload())
            if path == "/api/settings":
                try:
                    require_settings()
                except ApiError as exc:
                    return self.send_json({"error": exc.text}, status=exc.status)
                return self.send_json(settings_payload())
            if path == "/api/app-log":
                return self.send_json({"entries": list(app_log)})
            if path == "/api/help":
                return self.send_json(help_index())
            if path.startswith("/help/") and path.endswith(".svg"):
                stem = path[len("/help/"):-len(".svg")]
                doc_id, _, page = stem.rpartition("-")
                svg = help_page_svg(doc_id, int(page)) if page.isdigit() else None
                if svg is None:
                    return self.send_json({"error": "not found"}, status=404)
                return self.send_bytes(svg.encode("utf-8"), "image/svg+xml")
            if path.startswith("/api/"):
                return self.send_json({"error": "unknown endpoint"}, status=404)
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self.send_json({"error": "malformed body"}, status=400)
        if not isinstance(body, dict):
            return self.send_json({"error": "body must be a JSON object"}, status=400)

        with LOCK:
            try:
                result = dispatch(path, body)
            except ApiError as exc:
                return self.send_json({"error": exc.text}, status=exc.status)
        if result is None:
            return self.send_json({"error": "unknown endpoint"}, status=404)
        reply = {"ok": True}
        reply.update(result)
        return self.send_json(reply)

    def send_json(self, payload, status=200):
        self.send_bytes(json.dumps(payload).encode("utf-8"), "application/json", status)

    def send_bytes(self, data, ctype, status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        if not self.path.startswith("/api/") and not self.path.startswith("/help/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/status") or self.path.startswith("/api/app-log"):
            return
        super().log_message(fmt, *args)


ARGS = None


def main():
    global ARGS
    ap = argparse.ArgumentParser(description="HUBS Audio console mock backend (API v%d)" % API_VERSION)
    ap.add_argument("port", nargs="?", type=int, default=8000)
    ap.add_argument("--lanes", type=int, default=6, help="lanes on the chain (default 6)")
    ap.add_argument("--consoles", type=int, default=3, help="consoles on the chain (default 3)")
    ap.add_argument("--role", choices=["master", "operator"], default="master",
                    help="this console's role (default master)")
    ap.add_argument("--login-required", action="store_true", help="start at the Locked screen")
    ap.add_argument("--demo", action="store_true", help="lanes ring on their own")
    ARGS = ap.parse_args()
    if ARGS.lanes + ARGS.consoles > MAX_DEVICES:
        raise SystemExit("at most %d devices per site (lanes + consoles)" % MAX_DEVICES)
    if ARGS.consoles < 1 or ARGS.lanes < 1:
        raise SystemExit("need at least one lane and one console")

    reset_site(ARGS)
    if ARGS.demo:
        threading.Thread(target=demo_loop, daemon=True).start()

    server = HTTPServer(("127.0.0.1", ARGS.port), ConsoleHandler)
    print("HUBS Audio mock backend (API v%d) on http://127.0.0.1:%d" % (API_VERSION, ARGS.port))
    print("  %d lanes, %d consoles, this console is the %s%s%s" % (
        len(lanes()), len(consoles()), me()["role"],
        ", login required" if ARGS.login_required else "",
        ", demo ringing on" if ARGS.demo else ""))
    print("  developer password %s; users: Alex Teller 1111, Sam Tech 2222, "
          "Pat Admin 3333, Jo Owner 4444" % DEV_PASSWORD)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
