"""Finding windows and taking pictures of them, on the session's display.

Uses the X tools every distro has (xwininfo, ImageMagick's import and
convert) rather than a Python X binding, so there's nothing to install.
"""

import hashlib
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

TREE_LINE = re.compile(r'^\s*(0x[0-9a-f]+) "(.*)": \((?:"(.*?)" "(.*?)")?\)'
                       r'\s+(\d+)x(\d+)\+-?\d+\+-?\d+\s+\+(-?\d+)\+(-?\d+)\s*$')


def matcher(spec):
    """What a window is asked for by: a regex for its name (its title),
    matched in full, or app=RE for its app id: a Wayland window's app_id,
    or either half of an X window's WM_CLASS, its instance or its class.
    A test of (name, app ids), and how to say what it's looking for."""
    if spec.startswith("app="):
        pattern = re.compile(spec[4:])
        return (lambda name, apps: any(pattern.fullmatch(a) for a in apps),
                f"window with app id {spec[4:]!r}")
    pattern = re.compile(spec)
    return lambda name, apps: bool(pattern.fullmatch(name)), f"window named {spec!r}"


def windows(env):
    """Every named window: (id, name, width, height, x, y, app ids), x and y
    absolute, the app ids its WM_CLASS."""
    out = subprocess.run(["xwininfo", "-root", "-tree"], env=env,
                         capture_output=True, text=True).stdout
    found = []
    for line in out.splitlines():
        m = TREE_LINE.match(line)
        if m:
            wid, name, instance, klass, w, h, x, y = m.groups()
            apps = tuple(a for a in (instance, klass) if a)
            found.append((wid, name, int(w), int(h), int(x), int(y), apps))
    return found


def window_pid(env, wid):
    """The process that owns window `wid`, by its _NET_WM_PID, or None."""
    out = subprocess.run(["xprop", "-id", wid, "_NET_WM_PID"], env=env,
                         capture_output=True, text=True).stdout
    m = re.search(r"= (\d+)", out)
    return int(m.group(1)) if m else None


def viewable(env, wid):
    out = subprocess.run(["xwininfo", "-id", wid], env=env,
                         capture_output=True, text=True).stdout
    return "Map State: IsViewable" in out


def find_window(env, spec):
    """The biggest viewable window `spec` asks for (see matcher), as (id,
    name, width, height, x, y), or None. Biggest, because toolkits often
    name a 1x1 helper window after the app too."""
    test, _ = matcher(spec)
    hits = [w[:6] for w in windows(env) if test(w[1], w[6]) and w[2] > 1 and w[3] > 1]
    hits = [w for w in hits if viewable(env, w[0])]
    return max(hits, key=lambda w: w[2] * w[3]) if hits else None


def port_open(port, host="127.0.0.1"):
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex((host, port)) == 0


# ImageMagick 7's name for what 6 calls convert, which 7 warns about.
CONVERT = ["magick"] if shutil.which("magick") else ["convert"]

# PNGs that come out the same byte for byte: no metadata, no timestamps.
QUIET_PNG = ["-strip", "-define", "png:exclude-chunks=date,time"]


def capture(env, out, window=None, crop=None):
    """Screenshot the whole display, or one window, to `out` (a PNG), then
    crop it (ImageMagick geometry, WxH+X+Y, relative to what was taken)."""
    target = window[0] if window else "root"
    cmd = ["import", "-window", target]
    if crop:
        cmd += ["-crop", crop, "+repage"]
    cmd += QUIET_PNG + [str(out)]
    subprocess.run(cmd, env=env, check=True)
    return Path(out)


def try_capture(env, out, backend=None):
    """Screenshot the whole display for a look at what went wrong: the path,
    or None if there was no display left to take. `backend` is the module
    that takes it: this one, or wl for a Wayland session."""
    try:
        return (backend or sys.modules[__name__]).capture(env, out)
    except (OSError, subprocess.CalledProcessError):
        return None


def fingerprint(env, window=None):
    """A digest of what's on the display, or in one window, or None if it
    couldn't be taken (a window gone between finding it and taking it)."""
    target = window[0] if window else "root"
    r = subprocess.run(["import", "-window", target, "-depth", "8", "rgb:-"],
                       env=env, capture_output=True)
    if r.returncode or not r.stdout:
        return None
    return hashlib.sha256(r.stdout).digest()


class Still:
    """A test for wait_for: true once the display (or the window named
    `name`) has looked the same for `quiet` seconds. For the paint after a
    click, a panel sliding in, a toast going away: anything that has no
    other sign it's done. `backend`, as for try_capture."""

    def __init__(self, env, name=None, quiet=0.5, backend=None):
        self.env, self.name, self.quiet = env, name, quiet
        self.backend = backend or sys.modules[__name__]
        self.last, self.since = None, 0.0

    def __call__(self):
        window = None
        if self.name:
            window = self.backend.find_window(self.env, self.name)
            if not window:
                self.last = None
                return False
        # The window's place and size too: one that moves hasn't settled.
        now = (window[2:] if window else None, self.backend.fingerprint(self.env, window))
        t = time.monotonic()
        if now[1] is None or now != self.last:
            self.last, self.since = now, t
            return False
        return t - self.since >= self.quiet


def montage(images, out, across=True):
    """Put images side by side (or one above another)."""
    subprocess.run([*CONVERT, *map(str, images), "+append" if across else "-append",
                    *QUIET_PNG, str(out)], check=True)
    return Path(out)


def size_of(path):
    out = subprocess.run(["identify", "-format", "%wx%h", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return out.strip()


def compare(a, b, diff=None, fuzz="0%"):
    """How many pixels differ between two images (0 means the same): pixels
    where any channel differs by more than `fuzz`. With `diff`, also writes
    an image with the differences marked in red."""
    if size_of(a) != size_of(b):
        raise ValueError(f"not the same size: {size_of(a)} and {size_of(b)}")
    # The per-pixel difference, its largest channel, then on or off. Counted
    # by hand: compare's own AE metric weighs pixels differently from one
    # ImageMagick release to the next.
    out = subprocess.run(
        [*CONVERT, str(a), str(b), "-alpha", "off", "-compose", "difference",
         "-composite", "-separate", "-evaluate-sequence", "max",
         "-threshold", fuzz, "-format", "%[fx:round(mean*w*h)]", "info:"],
        capture_output=True, text=True, check=True).stdout
    if diff:
        subprocess.run(["compare", "-fuzz", fuzz, "-define", "png:exclude-chunks=date,time",
                        str(a), str(b), str(diff)], capture_output=True)
    return int(out.strip())
