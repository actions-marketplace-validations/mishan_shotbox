"""Finding windows and taking pictures of them, on the session's display.

Uses the X tools every distro has (xwininfo, ImageMagick's import and
convert) rather than a Python X binding, so there's nothing to install.
"""

import ctypes
import hashlib
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
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


def root_size(env):
    """The display's width and height."""
    out = subprocess.run(["xwininfo", "-root"], env=env, capture_output=True, text=True).stdout
    return (int(re.search(r"Width: (\d+)", out)[1]), int(re.search(r"Height: (\d+)", out)[1]))


# How each kind of video is encoded: fast enough to keep up as it records.
VIDEO = {
    ".mp4": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart"],
    ".webm": ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-crf", "32",
              "-b:v", "0", "-pix_fmt", "yuv420p"],
}


def record(env, out, box, seconds=None, fps=30, pointer=False):
    """Record the display's box (width, height, x, y) to `out`, an .mp4,
    .webm or .gif, for `seconds`, or until interrupted (Ctrl-C, or SIGTERM
    as the command line turns it), which finishes the file properly. A
    GIF is recorded as a video first, then given its own palette. Needs
    ffmpeg."""
    out = Path(out)
    kind = out.suffix.lower()
    if kind not in (*VIDEO, ".gif"):
        raise ValueError(f"a recording is .mp4, .webm or .gif, not {out.name}")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("recording needs ffmpeg")
    if seconds is not None and seconds <= 0:
        raise ValueError(f"a recording lasts more than 0 seconds, not {seconds:g}")
    tmp = tempfile.mkdtemp() if kind == ".gif" else None
    video = Path(tmp) / "recording.mp4" if tmp else out
    try:
        _record(env, video, box, seconds, fps, pointer)
    except BaseException:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        raise
    if tmp:
        # The GIF is made, and the recording removed, in a session of its
        # own: `shotbox run`, stopped, kills what's left of its command two
        # seconds on, which would cut both short.
        proc = subprocess.Popen(["sh", "-c", 'ffmpeg -loglevel error -y -i "$1" -vf '
                                 '"split[a][b];[a]palettegen[p];[b][p]paletteuse" "$2"; '
                                 's=$?; rm -rf "$3"; exit $s', "sh", str(video), str(out), tmp],
                                stdin=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                start_new_session=True)
        while True:
            try:
                _, err = proc.communicate()
                break
            except KeyboardInterrupt:
                pass        # it's the file the first stop asked for
        if proc.returncode:
            raise RuntimeError(f"ffmpeg couldn't make the GIF: {err.decode(errors='replace').strip()}")
    return out


def stop_with(parent):
    """For preexec_fn: a SIGTERM for the child when `parent` (this process)
    goes. Linux sends it when the thread that started the child ends, and
    record() waits on that thread for as long as the child runs."""
    def prctl():
        PR_SET_PDEATHSIG = 1
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
        if os.getppid() != parent:      # it went before the line above took
            os._exit(1)
    return prctl


def _record(env, video, box, seconds, fps, pointer):
    w, h, x, y = box
    w, h = w - w % 2, h - h % 2         # the encoders' color sampling halves each side
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-draw_mouse", str(int(pointer)),
           "-framerate", str(fps), "-video_size", f"{w}x{h}", "-i", f"{env['DISPLAY']}+{x},{y}"]
    cmd += ["-t", f"{seconds:g}"] if seconds is not None else []
    cmd += VIDEO[".webm" if video.suffix.lower() == ".webm" else ".mp4"] + [str(video)]
    # Its own session, so a Ctrl-C reaches it only as passed on here; and
    # stopped (finishing the file) if this process goes without passing
    # one on, killed: in its own session, nothing else would stop it.
    proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            start_new_session=True, preexec_fn=stop_with(os.getpid()))
    try:
        _, err = proc.communicate()
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)     # ffmpeg finishes the file and stops
        while True:
            try:
                _, err = proc.communicate()
                break
            except KeyboardInterrupt:
                pass        # it's finishing the file the first stop asked for
    if proc.returncode not in (0, 255) or not video.exists():
        raise RuntimeError(f"ffmpeg couldn't record: {err.decode(errors='replace').strip()}")
    # Stopped before the first frame, ffmpeg writes a file with no video in it.
    frames = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                             "stream=index", "-of", "csv=p=0", str(video)],
                            capture_output=True, text=True).stdout.strip()
    if not frames:
        video.unlink()
        raise RuntimeError("nothing was recorded: it stopped before the first frame")


# How colors look without one kind of cone, as Machado, Oliveira and
# Fernandes (2009) model it at full severity: a 3x3 matrix on linear RGB.
# Gray is no color at all, by luminance.
VISION = {
    "protanopia": (0.152286, 1.052583, -0.204868, 0.114503, 0.786281, 0.099216,
                   -0.003882, -0.048116, 1.051998),
    "deuteranopia": (0.367322, 0.860646, -0.227968, 0.280085, 0.672501, 0.047413,
                     -0.011820, 0.042940, 0.968881),
    "tritanopia": (1.255528, -0.076749, -0.178779, -0.078411, 0.930809, 0.147602,
                   0.004733, 0.691367, 0.303900),
    "gray": (0.2126, 0.7152, 0.0722) * 3,
}


def vision(src, out, kind):
    """`src` as someone with `kind` of color vision (a VISION key) sees it,
    to `out`. The two may be the same file."""
    matrix = " ".join(f"{v:.6f}" for v in VISION[kind])
    subprocess.run(CONVERT + [str(src), "-colorspace", "RGB", "-color-matrix", f"3x3: {matrix}",
                              "-colorspace", "sRGB"] + QUIET_PNG + [str(out)], check=True)
    return Path(out)


def try_capture(env, out, backend=None):
    """Screenshot the whole display for a look at what went wrong: the path,
    or None if there was no display left to take. `backend` is the module
    that takes it: this one, or wl for a Wayland session."""
    try:
        return (backend or sys.modules[__name__]).capture(env, out)
    except (OSError, subprocess.CalledProcessError):
        return None


def ppm(data):
    """A binary PPM's (width, height, RGB bytes), as grim and import write it."""
    m = re.match(rb"P6\s+(\d+)\s+(\d+)\s+(\d+)\s", data)
    if not m or m.group(3) != b"255":
        raise ValueError("not an 8-bit PPM")
    return int(m.group(1)), int(m.group(2)), data[m.end():]


def pixels(env):
    """The whole display as (width, height, RGB bytes)."""
    return ppm(subprocess.run(["import", "-window", "root", "-depth", "8", "ppm:-"],
                              env=env, capture_output=True, check=True).stdout)


def focus(env, window):
    """Give `window` (as find_window has it) the keyboard, and raise it:
    with no window manager, nothing else would."""
    from .xtest import Display
    with Display(env) as d:
        d.focus(int(window[0], 16))


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


def sheet(images, out, across=4, size=None):
    """Put images in rows of `across`, left to right, top to bottom: a
    contact sheet, shrunk to fit `size` pixels each way if it doesn't."""
    rows = [images[i:i + across] for i in range(0, len(images), across)]
    cmd = list(CONVERT)
    for row in rows:
        cmd += ["(", *map(str, row), "+append", ")"]
    cmd += ["-background", "black", "-append"]
    if size:
        cmd += ["-resize", f"{size}x{size}>"]
    subprocess.run(cmd + [*QUIET_PNG, str(out)], check=True)
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
