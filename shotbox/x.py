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
import wave
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


def port_open(port, host="127.0.0.1", net_pid=None):
    """Whether something takes connections on `port`: here, or with
    `net_pid`, in that process's network namespace, as its TCP tables say,
    which needs no way into it."""
    if net_pid:
        tables = [Path(f"/proc/{net_pid}/net/{t}") for t in ("tcp", "tcp6")]
        return any(line.split()[1].rsplit(":", 1)[1] == f"{port:04X}"
                   and line.split()[3] == "0A"     # listening
                   for t in tables if t.exists() for line in t.read_text().splitlines()[1:])
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
    """As `recording`, and wait for it: its file, or RuntimeError. Ctrl-C
    stops it, and it's finished all the same."""
    proc = recording(env, out, box, seconds=seconds, fps=fps, pointer=pointer)
    try:
        proc.wait()
    except KeyboardInterrupt:
        stop(proc)
    return ended(proc, out)


def recording(env, out, box, seconds=None, fps=None, pointer=False):
    """Record the display's box (width, height, x, y) to `out`, an .mp4,
    .webm or .gif, at `fps` (30, or 15 for a GIF), for `seconds`, or until
    interrupted (Ctrl-C, or SIGTERM as the command line turns it). A GIF
    is recorded as a video first, then given its own palette. A .wav is
    what plays to the session's sink (with --audio) instead, and takes no
    box. Needs ffmpeg, and on Wayland, wf-recorder.

    A keeper process of its own (keep_recording) runs ffmpeg and finishes
    the file, so it's finished however this one goes: interrupted, killed,
    or its session's display, or sound server, stopped. Its process, for
    `stop` and `ended`."""
    out = Path(out)
    kind = out.suffix.lower()
    if kind not in (*VIDEO, ".gif", ".wav"):
        raise ValueError(f"a recording is .mp4, .webm, .gif or .wav, not {out.name}")
    # The session's own sound server, not one let in with --env or --pass.
    if kind == ".wav" and not env.get("PULSE_SERVER", "").startswith(
            f"unix:{env.get('SHOTBOX_SCRATCH')}/"):
        raise ValueError("there's no sound to record: start the session with --audio")
    if kind == ".wav" and seconds is not None and round(seconds * 48000) < 1:
        raise ValueError(f"a recording lasts a sample or more, not {seconds:g} seconds")
    from .session import NEEDS      # which imports this module
    wayland = kind != ".wav" and bool(env.get("SWAYSOCK"))
    why = NEEDS["record on Wayland" if wayland else "record"]()
    if why:
        raise RuntimeError(why)
    if seconds is not None and seconds <= 0:
        raise ValueError(f"a recording lasts more than 0 seconds, not {seconds:g}")
    cmd, grab = ["ffmpeg", "-loglevel", "error", "-nostdin", "-y"], None
    if kind == ".wav":
        # The sink's own format, so what plays at it comes through as is.
        cmd += ["-f", "pulse", "-sample_rate", "48000", "-channels", "2",
                "-i", "shotbox-sink.monitor"]
    else:
        w, h, x, y = box
        w, h = w - w % 2, h - h % 2     # the encoders' color sampling halves each side
        fps = fps or (15 if kind == ".gif" else 30)
        if wayland:
            # ffmpeg can't read wlroots' screencopy: wf-recorder takes the
            # frames and hands them on raw. -D takes one every refresh, not
            # only when something's drawn, or a still screen gives -r
            # nothing to make its frames from.
            grab = ["wf-recorder", "-D", "-r", str(fps), "-g", f"{x},{y} {w}x{h}",
                    "-c", "rawvideo", "-m", "nut", "-f", "pipe:1"]
            cmd += ["-f", "nut", "-i", "pipe:0", "-r", str(fps)]
        else:
            cmd += ["-f", "x11grab", "-draw_mouse", str(int(pointer)), "-framerate", str(fps),
                    "-video_size", f"{w}x{h}", "-i", f"{env['DISPLAY']}+{x},{y}"]
    if kind == ".wav":
        # By the sample: -t goes by the input's clock, tens of ms out.
        cmd += ["-af", f"atrim=end_sample={round(seconds * 48000)}"] if seconds is not None else []
        cmd += ["-c:a", "pcm_s16le"]
    else:
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
    return subprocess.Popen([sys.executable, "-c", keeper, json.dumps(cmd), str(out),
                             json.dumps(grab)],
                            env={**env, "PYTHONPATH": path}, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            start_new_session=True)


def stop(proc):
    """Stop the recording `proc`, and wait while it finishes its file."""
    try:
        proc.stdin.write(b"stop\n")
        proc.stdin.close()
    except OSError:
        pass                # gone already
    while True:
        try:
            proc.wait()
            break
        except KeyboardInterrupt:
            pass        # it's finishing the file the first stop asked for


def ended(proc, out):
    """`out`, the file the ended recording `proc` made, or RuntimeError."""
    if proc.returncode:
        raise RuntimeError(proc.stderr.read().decode(errors="replace").strip()
                           or f"the recording failed (status {proc.returncode})")
    return out


def tied(sig):
    """For preexec_fn: the child gets `sig` when this process goes, however
    it goes, rather than recording on for good."""
    import ctypes
    keeper = os.getpid()

    def tie():
        PR_SET_PDEATHSIG = 1
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_PDEATHSIG, sig, 0, 0, 0)
        if os.getppid() != keeper:      # it went before the line above took
            os._exit(1)
    return tie


def keep_recording(cmd, out, grab):
    """Run the ffmpeg command `cmd` (JSON, without its output file) to
    record to `out`, reading what the command `grab` (JSON, or null)
    writes, and stop it with one signal, which it finishes the file on (a
    second makes it quit unfinished): when a line comes on stdin or stdin
    closes, as when what started this goes however it goes, or when the
    display goes (the sound server, for a .wav; sway, on Wayland). Then
    make the GIF, for a .gif. Status 0, or 1 and why on stderr."""
    cmd, out, grab = json.loads(cmd), Path(out), json.loads(grab)
    tmp = tempfile.mkdtemp() if out.suffix.lower() == ".gif" else None
    video = Path(tmp) / "recording.mp4" if tmp else out
    feed = None
    try:
        # What started this may have had Ctrl-C ignored (a job started with
        # &): ffmpeg's own handlers mustn't start out that way.
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_DFL)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        if grab:
            grab_err = tempfile.TemporaryFile()
            feed = subprocess.Popen(grab, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=grab_err, preexec_fn=tied(signal.SIGKILL))
        # SIGTERM, so ffmpeg finishes the file.
        proc = subprocess.Popen(cmd + [str(video)], stderr=subprocess.PIPE,
                                stdin=feed.stdout if feed else subprocess.DEVNULL,
                                preexec_fn=tied(signal.SIGTERM))
        if feed:
            feed.stdout.close()
        sound = out.suffix.lower() == ".wav"
        if sound:
            sock = os.environ["PULSE_SERVER"].removeprefix("unix:")
        elif feed:
            sock = str(Path(os.environ["XDG_RUNTIME_DIR"], os.environ["WAYLAND_DISPLAY"]))
        else:
            sock = None
        while proc.poll() is None:
            up = socket_up(sock) if sock else display_up(os.environ["DISPLAY"])
            if select.select([sys.stdin], [], [], 0.5)[0] or not up:
                proc.send_signal(signal.SIGTERM)
                break
        if feed:
            # ffmpeg waits on its input, and with sway gone, wf-recorder
            # waits for good on a frame: an end to the pipe ends both.
            feed.kill()
            feed.wait()
        _, err = proc.communicate()
        if proc.returncode not in (0, 255) or not video.exists():
            tool, said = "ffmpeg", err.decode(errors="replace").strip()
            if feed and feed.returncode > 0:        # what ffmpeg says is only that it had nothing
                grab_err.seek(0)
                tool, said = "wf-recorder", grab_err.read().decode(errors="replace").strip()
                said = said.rpartition("\n")[2]
            sys.stderr.write(f"{tool} couldn't record: "
                             f"{said or 'it was stopped before it began'}\n")
            return 1
        # Stopped before the first frame, ffmpeg writes a file with none.
        if sound:
            try:
                with wave.open(str(out)) as w:
                    frames = w.getnframes()
            except (wave.Error, EOFError):
                frames = 0
        else:
            frames = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v",
                                     "-show_entries", "stream=index", "-of", "csv=p=0",
                                     str(video)], capture_output=True, text=True).stdout.strip()
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
        if feed:
            feed.kill()
            feed.wait()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def display_up(display):
    """Whether the local X display (`:N`) still takes connections."""
    m = re.fullmatch(r":(\d+)(\.\d+)?", display)
    return socket_up(f"/tmp/.X11-unix/X{m[1]}") if m else True


def socket_up(path):
    """Whether the Unix socket at `path` still takes connections."""
    with socket.socket(socket.AF_UNIX) as s:
        try:
            s.connect(path)
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


# Connections to take pictures over, a display each, kept: a wait polls.
_grabbers = {}


def grab(env, window=None, box=None):
    """What's on the display, or in `window` (as find_window has it), or
    the `box` (width, height, x, y) of it, straight from the X server, as
    (width, height, RGB bytes): a few milliseconds where `import` takes
    tens. None where it can't be had that way, some of it off the screen
    say, for the slower way."""
    from .xtest import Closed, Display, XError
    key = env.get("DISPLAY"), env.get("XAUTHORITY")
    try:
        d = _grabbers.get(key) or _grabbers.setdefault(key, Display(env))
        # import takes what of a window is on the screen; GetImage would
        # take a backing store's all, or fail, so a window partly off it
        # is import's.
        if window and not (0 <= window[4] and 0 <= window[5] and window[4] + window[2] <= d.width
                           and window[5] + window[3] <= d.height):
            return None
        drawable = int(window[0], 16) if window else d.root
        w, h, x, y = box or ((*window[2:4], 0, 0) if window else (d.width, d.height, 0, 0))
        return w, h, d.image(drawable, x, y, w, h)
    except (Closed, OSError):
        _grabbers.pop(key, None)
        return None
    except XError:          # a window gone, say: the connection's fine
        return None


def rgb(path):
    """A picture's (width, height, RGB bytes), as grab has them."""
    w, h = map(int, size_of(path).split("x"))
    data = subprocess.run([*CONVERT, str(path), "-alpha", "off", "-depth", "8", "rgb:-"],
                          capture_output=True, check=True).stdout
    return w, h, data


def differ(a, b, fuzz=0.0, most=0):
    """How many pixels of two RGB buffers of one size differ, as compare
    counts them, where any channel differs by more than `fuzz` percent;
    up to `most` + 1, where it stops counting. Runs that are the same are
    passed over whole, so a picture that's mostly so is quick."""
    limit, n, run = fuzz / 100 * 255, 0, 3 * 512
    for start in range(0, len(a), run):
        end = start + run
        if a[start:end] == b[start:end]:
            continue
        for i in range(start, min(end, len(a)), 3):
            if (abs(a[i] - b[i]) > limit or abs(a[i + 1] - b[i + 1]) > limit
                    or abs(a[i + 2] - b[i + 2]) > limit):
                n += 1
                if n > most:
                    return n
    return n


def fingerprint(env, window=None):
    """A digest of what's on the display, or in one window, or None if it
    couldn't be taken (a window gone between finding it and taking it)."""
    got = grab(env, window)
    if got:
        return hashlib.sha256(got[2]).digest()
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


def compare(a, b, diff=None, fuzz="0%", box=False):
    """How many pixels differ between two images (0 means the same): pixels
    where any channel differs by more than `fuzz`. With `diff`, also writes
    an image with the differences marked in red. With `box`, the count and
    the box they're in, (width, height, x, y), or None."""
    if size_of(a) != size_of(b):
        raise ValueError(f"not the same size: {size_of(a)} and {size_of(b)}")
    # The per-pixel difference, its largest channel, then on or off. Counted
    # by hand: compare's own AE metric weighs pixels differently from one
    # ImageMagick release to the next.
    out = subprocess.run(
        # sRGB: ImageMagick reads a gray PNG, as it writes any picture with
        # no color in it, as one channel, and would count only that.
        [*CONVERT, str(a), str(b), "-colorspace", "sRGB", "-alpha", "off",
         "-compose", "difference",
         "-composite", "-separate", "-evaluate-sequence", "max",
         # A black border so the box (%@, what -trim would keep) is found
         # around what differs even when a corner does; -quiet, since with
         # nothing in the box it warns.
         "-threshold", fuzz, "-bordercolor", "black", "-border", "1", "-quiet",
         "-format", "%[fx:round(mean*w*h)] %@", "info:"],
        capture_output=True, text=True, check=True).stdout.split()
    if diff:
        subprocess.run(["compare", "-fuzz", fuzz, "-define", "png:exclude-chunks=date,time",
                        str(a), str(b), str(diff)], capture_output=True)
    n = int(out[0])
    w, h, x, y = map(int, re.split("[x+]", out[1]))
    return (n, (w, h, x - 1, y - 1) if n else None) if box else n
