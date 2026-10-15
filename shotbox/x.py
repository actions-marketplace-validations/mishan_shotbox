"""Finding windows and taking pictures of them, on the session's display.

Uses the X tools every distro has (xwininfo, ImageMagick's import and
convert) rather than a Python X binding, so there's nothing to install.
"""

import hashlib
import json
import os
import re
import select
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
    w, h = re.search(r"Width: (\d+)", out), re.search(r"Height: (\d+)", out)
    if not (w and h):
        raise RuntimeError(f"can't reach the display {env.get('DISPLAY')}: is the session still up?")
    return int(w[1]), int(h[1])


# How each kind of video is encoded: fast enough to keep up as it records.
VIDEO = {
    ".mp4": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart"],
    ".webm": ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-crf", "32",
              "-b:v", "0", "-pix_fmt", "yuv420p"],
}


def record(env, out, box, seconds=None, fps=None, pointer=False):
    """Record the display's box (width, height, x, y) to `out`, an .mp4,
    .webm or .gif, at `fps` (30, or 15 for a GIF), for `seconds`, or until
    interrupted (Ctrl-C, or SIGTERM as the command line turns it). A GIF
    is recorded as a video first, then given its own palette. Needs
    ffmpeg.

    A keeper process of its own (keep_recording) runs ffmpeg and finishes
    the file, so it's finished however this one goes: interrupted, killed,
    or its session's display stopped."""
    out = Path(out)
    kind = out.suffix.lower()
    if kind not in (*VIDEO, ".gif"):
        raise ValueError(f"a recording is .mp4, .webm or .gif, not {out.name}")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("recording needs ffmpeg")
    if seconds is not None and seconds <= 0:
        raise ValueError(f"a recording lasts more than 0 seconds, not {seconds:g}")
    w, h, x, y = box
    w, h = w - w % 2, h - h % 2         # the encoders' color sampling halves each side
    fps = fps or (15 if kind == ".gif" else 30)
    cmd = ["ffmpeg", "-loglevel", "error", "-nostdin", "-y", "-f", "x11grab",
           "-draw_mouse", str(int(pointer)), "-framerate", str(fps), "-video_size", f"{w}x{h}",
           "-i", f"{env['DISPLAY']}+{x},{y}"]
    cmd += ["-t", f"{seconds:g}"] if seconds is not None else []
    cmd += VIDEO[".webm" if kind == ".webm" else ".mp4"]
    here = str(Path(__file__).resolve().parent.parent)     # this shotbox, wherever it is
    path = os.pathsep.join(filter(None, (here, env.get("PYTHONPATH"))))
    # -c puts the current directory first on the path, ahead of PYTHONPATH:
    # another shotbox there would be the one run.
    keeper = ("import sys; del sys.path[0]; from shotbox.x import keep_recording; "
              "sys.exit(keep_recording(*sys.argv[1:]))")
    # Its own session: `shotbox run`, stopped, kills its command's group
    # two seconds on, which would cut the finishing short.
    proc = subprocess.Popen([sys.executable, "-c", keeper, json.dumps(cmd), str(out)],
                            env={**env, "PYTHONPATH": path}, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            start_new_session=True)
    try:
        proc.wait()
    except KeyboardInterrupt:
        try:
            proc.stdin.write(b"stop\n")
            proc.stdin.flush()
        except OSError:
            pass
        while True:
            try:
                proc.wait()
                break
            except KeyboardInterrupt:
                pass        # it's finishing the file the first stop asked for
    if proc.returncode:
        raise RuntimeError(proc.stderr.read().decode(errors="replace").strip()
                           or f"the recording failed (status {proc.returncode})")
    return out


def keep_recording(cmd, out):
    """Run the ffmpeg command `cmd` (JSON, without its output file) to
    record to `out`, and stop it with one signal, which it finishes the
    file on (a second makes it quit unfinished): when a line comes on stdin
    or stdin closes, as when what started this goes however it goes, or
    when the display goes. Then make the GIF, for a .gif. Status 0, or 1
    and why on stderr."""
    cmd, out = json.loads(cmd), Path(out)
    tmp = tempfile.mkdtemp() if out.suffix.lower() == ".gif" else None
    video = Path(tmp) / "recording.mp4" if tmp else out
    try:
        # What started this may have had Ctrl-C ignored (a job started with
        # &): ffmpeg's own handlers mustn't start out that way.
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_DFL)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        proc = subprocess.Popen(cmd + [str(video)], stdin=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        display = os.environ["DISPLAY"]
        while proc.poll() is None:
            if select.select([sys.stdin], [], [], 0.5)[0] or not display_up(display):
                proc.send_signal(signal.SIGTERM)
                break
        _, err = proc.communicate()
        if proc.returncode not in (0, 255) or not video.exists():
            sys.stderr.write(f"ffmpeg couldn't record: {err.decode(errors='replace').strip()}\n")
            return 1
        # Stopped before the first frame, ffmpeg writes a file with no video.
        frames = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v",
                                 "-show_entries", "stream=index", "-of", "csv=p=0", str(video)],
                                capture_output=True, text=True).stdout.strip()
        if not frames:
            video.unlink()
            sys.stderr.write("nothing was recorded: it stopped before the first frame\n")
            return 1
        if tmp:
            gif = subprocess.run(["ffmpeg", "-loglevel", "error", "-nostdin", "-y", "-i",
                                  str(video), "-vf",
                                  "split[a][b];[a]palettegen[p];[b][p]paletteuse", str(out)],
                                 stderr=subprocess.PIPE)
            if gif.returncode:
                sys.stderr.write("ffmpeg couldn't make the GIF: "
                                 + gif.stderr.decode(errors="replace").strip() + "\n")
                return 1
        return 0
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def display_up(display):
    """Whether the local X display (`:N`) still takes connections."""
    m = re.fullmatch(r":(\d+)(\.\d+)?", display)
    if not m:
        return True
    with socket.socket(socket.AF_UNIX) as s:
        try:
            s.connect(f"/tmp/.X11-unix/X{m[1]}")
            return True
        except OSError:
            return False


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
