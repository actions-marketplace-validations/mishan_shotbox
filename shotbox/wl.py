"""Finding windows and taking pictures of them, in a Wayland session under
sway: what x.py does for X11.

Windows come from sway's IPC socket ($SWAYSOCK), which speaks JSON over a
Unix socket, and pictures from grim, which speaks wlr-screencopy. Nothing
to install beyond those two.
"""

import hashlib
import json
import socket
import struct
import subprocess
from pathlib import Path

from .x import CONVERT, QUIET_PNG, in_sessions, matcher, ppm

MAGIC = b"i3-ipc"
RUN_COMMAND, GET_OUTPUTS, GET_TREE, GET_VERSION = 0, 3, 4, 7


def ipc(env, kind, payload=""):
    """Ask sway one thing over its IPC socket; the reply, decoded."""
    data = payload.encode()
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(5)
        s.connect(env["SWAYSOCK"])
        s.sendall(MAGIC + struct.pack("<II", len(data), kind) + data)
        head = _read(s, len(MAGIC) + 8)
        size, _ = struct.unpack("<II", head[len(MAGIC):])
        return json.loads(_read(s, size))


def _read(s, n):
    buf = b""
    while len(buf) < n:
        got = s.recv(n - len(buf))
        if not got:
            raise OSError("sway closed its IPC socket")
        buf += got
    return buf


def windows(env):
    """Every window, Wayland and X alike: (id, name, width, height, x, y,
    app ids, visible, pid), x and y absolute. A Wayland window's app id is its
    app_id; an X one's, under Xwayland, its WM_CLASS instance and class."""
    found = []

    def walk(node):
        if node.get("pid") is not None:
            r = node["rect"]
            props = node.get("window_properties") or {}
            apps = tuple(a for a in (node.get("app_id"), props.get("instance"),
                                     props.get("class")) if a)
            found.append((node["id"], node.get("name") or "", r["width"], r["height"],
                          r["x"], r["y"], apps, node.get("visible", False), node["pid"]))
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            walk(child)

    try:
        tree = ipc(env, GET_TREE)
    except OSError as e:
        from .screen import SessionError
        raise SessionError(f"sway has gone away ({e.strerror or e})")
    walk(tree)
    return found


def window_pid(env, wid):
    """The process that owns window `wid`, as sway has it, or None."""
    return next((w[8] for w in windows(env) if w[0] == wid), None)


def shown(env, spec=None, sids=()):
    """As x.shown: the visible windows, with `sids` by the pid sway has
    for each, a Wayland client's or an Xwayland window's."""
    test = matcher(spec)[0] if spec else lambda name, apps: True
    return [w[:7] for w in windows(env) if w[7] and test(w[1], w[6])
            and (not sids or in_sessions(w[8], sids))]


def find_window(env, spec=None, sids=()):
    """The biggest of the windows shown() finds, or None: the same rule as
    x.find_window, less the 1x1 helpers, which Wayland doesn't have."""
    hits = [w[:6] for w in shown(env, spec, sids)]
    return max(hits, key=lambda w: w[2] * w[3]) if hits else None


def _region(window):
    return ["-g", f"{window[4]},{window[5]} {window[2]}x{window[3]}"] if window else []


def grab(env, window=None, box=None):
    """As x.grab: the output, or `window`, or the `box` (width, height, x,
    y) of it, as (width, height, RGB bytes), from grim with no PNG to
    make; or None where grim can't take it."""
    if box:
        ox, oy = (window[4], window[5]) if window else (0, 0)
        region = ["-g", f"{ox + box[2]},{oy + box[3]} {box[0]}x{box[1]}"]
    else:
        region = _region(window)
    r = subprocess.run(["grim", *region, "-t", "ppm", "-"], env=env, capture_output=True)
    try:
        return None if r.returncode or not r.stdout else ppm(r.stdout)
    except ValueError:      # not a PPM it can read: the slower way's
        return None


def capture(env, out, window=None, crop=None):
    """Screenshot the whole output, or one window, to `out` (a PNG), then
    crop it (WxH+X+Y, relative to what was taken). grim takes it and
    ImageMagick writes it, so the PNG has no timestamps."""
    shot = subprocess.run(["grim", *_region(window), "-t", "ppm", "-"], env=env,
                          capture_output=True, check=True).stdout
    cmd = [*CONVERT, "ppm:-"]
    if crop:
        cmd += ["-crop", crop, "+repage"]
    subprocess.run(cmd + QUIET_PNG + [str(out)], input=shot, check=True)
    return Path(out)


def pixels(env):
    """The whole output as (width, height, RGB bytes)."""
    return ppm(subprocess.run(["grim", "-t", "ppm", "-"], env=env, capture_output=True,
                              check=True).stdout)


def root_size(env):
    """The output's width and height."""
    try:
        r = ipc(env, GET_OUTPUTS)[0]["rect"]
    except OSError:
        raise RuntimeError("can't reach sway: is the session still up?")
    return r["width"], r["height"]


def focus(env, window):
    """Give `window` (as find_window has it) the keyboard, and raise it."""
    r = ipc(env, RUN_COMMAND, f"[con_id={window[0]}] focus")
    if not r or not r[0].get("success"):
        from .screen import SessionError
        raise SessionError(f"sway didn't focus {window[1]!r}: "
                           f"{(r and r[0].get('error')) or 'no answer'}")


def fingerprint(env, window=None):
    """A digest of what's on the output, or in one window, or None if it
    couldn't be taken."""
    r = subprocess.run(["grim", *_region(window), "-t", "ppm", "-"], env=env,
                       capture_output=True)
    if r.returncode or not r.stdout:
        return None
    return hashlib.sha256(r.stdout).digest()
