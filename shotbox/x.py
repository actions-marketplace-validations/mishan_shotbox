"""Finding windows and taking pictures of them, on the session's display.

Uses the X tools every distro has (xwininfo, ImageMagick's import and
convert) rather than a Python X binding, so there's nothing to install.
"""

import ctypes
import ctypes.util
import functools
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


def pattern(spec):
    """How a name is matched, a window's or a node's: as it is, or as a
    regex matched in full; one that isn't a regex ("+", "Save (") only as
    it is. A test of a name, and what to add to saying what's looked for."""
    try:
        rx = re.compile(spec)
    except re.error as e:
        return (lambda name: name == spec), f" (exactly: it isn't a regex, {e})"
    return (lambda name: name == spec or rx.fullmatch(name) is not None), ""


def boxed(node):
    """Whether a node's bounds are a box with something in it."""
    b = node.get("bounds")
    return (isinstance(b, list) and len(b) == 4 and all(type(v) is int for v in b)
            and b[2] > 0 and b[3] > 0)


def picked(spec, items, name=lambda n: n.name):
    """Of `items`, those named `spec` exactly, if any, else those pattern()
    matches: a name that's also a regex, "a.c", not taken for "abc" too."""
    test = pattern(spec)[0]
    return [i for i in items if name(i) == spec] or [i for i in items if test(name(i))]


def matcher(spec):
    """What a window is asked for by: its name (its title), as pattern()
    matches one, or app=RE for its app id: a Wayland window's app_id,
    or either half of an X window's WM_CLASS, its instance or its class.
    A test of (name, app ids), and how to say what it's looking for."""
    if spec.startswith("app="):
        test, note = pattern(spec[4:])
        return (lambda name, apps: any(map(test, apps)),
                f"window with app id {spec[4:]!r}{note}")
    test, note = pattern(spec)
    return lambda name, apps: test(name), f"window named {spec!r}{note}"


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


def transient_for(env, wid):
    """The window `wid` is a dialog of, by its WM_TRANSIENT_FOR, or None."""
    out = subprocess.run(["xprop", "-id", wid, "WM_TRANSIENT_FOR"], env=env,
                         capture_output=True, text=True).stdout
    m = re.search(r"# (0x[0-9a-f]+)", out)
    return m and m.group(1)


def viewable(env, wid):
    out = subprocess.run(["xwininfo", "-id", wid], env=env,
                         capture_output=True, text=True).stdout
    return "Map State: IsViewable" in out


def in_sessions(pid, sids):
    """Whether process `pid` is in one of the process sessions `sids`: a
    spawned program's, which what it forks or execs, a shell wrapper's
    child or a toolkit's helper, stays in."""
    try:
        return bool(pid) and os.getsid(pid) in sids    # getsid(0) is our own
    except OSError:
        return False


def shown(env, spec=None, sids=()):
    """The viewable windows `spec` asks for (see matcher), or with none,
    every one, as windows() has them, less the 1x1 helpers toolkits often
    name after the app too; with `sids`, only those of a process in one of
    those sessions, by their _NET_WM_PID, which GTK 3 and 4 and Qt 5 and
    6 set with no window manager."""
    test = matcher(spec)[0] if spec else lambda name, apps: True
    hits = [w for w in windows(env) if test(w[1], w[6]) and w[2] > 1 and w[3] > 1]
    return [w for w in hits if viewable(env, w[0])
            and (not sids or in_sessions(window_pid(env, w[0]), sids))]


def find_window(env, spec=None, sids=()):
    """The biggest of the windows shown() finds, as (id, name, width,
    height, x, y), or None. Biggest, because of those helpers."""
    hits = [w[:6] for w in shown(env, spec, sids)]
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


@functools.cache
def font():
    """ImageMagick's `-font` for text it writes: 6 writes none where none of
    the fonts its own lists name is installed, as on Ubuntu's runners, and
    fontconfig has one."""
    found = shutil.which("fc-match") and subprocess.run(["fc-match", "-f", "%{file}", "sans"],
                                                         capture_output=True, text=True).stdout
    return ["-font", found] if found else []

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
SOUND = {".mp4": ["-c:a", "aac"], ".webm": ["-c:a", "libopus"]}
# With sound, the picture's encoder holds back no frames: ffmpeg holds the
# sound back as far as it does, and what's held back when a signal stops
# it is lost.
PROMPT = {".mp4": ["-tune", "zerolatency"], ".webm": ["-lag-in-frames", "0"]}
# For a picture read from a pipe: ffmpeg opens the sound only once it's
# probed it, which takes frames coming for a second or more, and what plays
# meanwhile is lost.
UNPROBED = ["-probesize", "32", "-analyzeduration", "0"]
# What plays at the session's sink, in its own format, so what plays in
# that format comes through as is.
SINK = ["-f", "pulse", "-sample_rate", "48000", "-channels", "2", "-i", "shotbox-sink.monitor"]


class Recording:
    """What `record` made: its `path`, which it stands in for; with
    sound, `heard`, what `sound.hear` heard in its sound (else None); the
    `line` that ended it, if one did; and whether it was `stopped`."""

    def __init__(self, path, heard=None, line=None, stopped=False):
        self.path, self.heard, self.line, self.stopped = Path(path), heard, line, stopped

    def __fspath__(self):
        return str(self.path)

    def __str__(self):
        return str(self.path)

    def __repr__(self):
        return f"Recording({str(self.path)!r})"

    def problems(self, quiet=-60.0, clipped=0):
        """What's wrong with its sound, as `sound.problems` says: a line each."""
        from .sound import problems
        if not self.heard:
            return []
        cut = ["the sound server stopped, and the recording with it"] if self.heard["cut"] else []
        return cut + problems(self.heard, quiet=quiet, clipped=clipped)


def record(env, out, box, seconds=None, fps=None, pointer=False, sound=False, gap=0.001,
           size=None, until=None, tail=0):
    """As `recording`, and wait for it: a Recording, or RuntimeError.
    Ctrl-C stops it, and it's finished all the same. With `until`, a test
    as wait_for polls, giving a line in a list, it's stopped as Ctrl-C
    stops it `tail` seconds after the test passes, and that's its `line`.
    A cancelled wait (screen._CANCEL) stops it as Ctrl-C does."""
    from .screen import _CANCEL
    proc = recording(env, out, box, seconds=seconds, fps=fps, pointer=pointer, sound=sound,
                     gap=gap, size=size)
    line, stopped, end = None, False, None
    try:
        # Watched here, not by the keeper: here's where the line is wanted,
        # and however this goes, the keeper's stdin closes and it stops.
        while proc.poll() is None and (end is None or time.monotonic() < end):
            if _CANCEL.is_set():
                stopped = True
                break
            if until and not line and (line := until()):
                end = time.monotonic() + tail
            # Not time.sleep: under libfaketime 0.9.10, as the Python API is
            # in a --clock session's program, Python 3.11's fails (EINVAL).
            select.select([], [], [], 0.1)
    except KeyboardInterrupt:
        stopped = True
    except RuntimeError as e:      # the file couldn't be followed
        stop(proc)
        if not proc.returncode:
            e.args = (f"{e}; what was recorded is kept, at {out}",)
        raise
    finally:
        if proc.poll() is None:
            stop(proc)
    out = ended(proc, out)
    if sound:
        proc.said.seek(0)
    return Recording(out, json.load(proc.said) if sound else None, line and line[0], stopped)


def recording(env, out, box, seconds=None, fps=None, pointer=False, sound=False, gap=0.001,
              size=None):
    """Record the display's box (width, height, x, y) to `out`, an .mp4,
    .webm or .gif, at `fps` (30, or 15 for a GIF), for `seconds`, or until
    interrupted (Ctrl-C, or SIGTERM as the command line turns it). A GIF
    is recorded as a video first, then given its own palette. A .wav is
    what plays to the session's sink (with --audio) instead, and takes no
    box. With `sound`, an .mp4 or .webm has what plays in it too, heard
    with `gap` as `sound.hear` takes it. With `size`, it ends when the
    file reaches that many bytes. Needs ffmpeg, and on Wayland, wf-recorder.

    A keeper process of its own (keep_recording) runs ffmpeg and finishes
    the file, so it's finished however this one goes: interrupted, killed,
    or its session's display, or sound server, stopped. Its process, for
    `stop` and `ended`."""
    out = Path(out)
    kind = out.suffix.lower()
    if kind not in (*VIDEO, ".gif", ".wav"):
        raise ValueError(f"a recording is .mp4, .webm, .gif or .wav, not {out.name}")
    if sound and kind not in VIDEO:
        raise ValueError(f"a recording with sound is an .mp4 or .webm, not {out.name}")
    # The session's own sound server, not one let in with --env or --pass.
    if (kind == ".wav" or sound) and not env.get("PULSE_SERVER", "").startswith(
            f"unix:{env.get('SHOTBOX_SCRATCH')}/"):
        raise ValueError("there's no sound to record: start the session with --audio")
    if (kind == ".wav" or sound) and seconds is not None and round(seconds * 48000) < 1:
        raise ValueError(f"a recording lasts a sample or more, not {seconds:g} seconds")
    from .session import NEEDS      # which imports this module
    wayland = kind != ".wav" and bool(env.get("SWAYSOCK"))
    why = NEEDS["record on Wayland" if wayland else "record"]()
    if why:
        raise RuntimeError(why)
    if seconds is not None and seconds <= 0:
        raise ValueError(f"a recording lasts more than 0 seconds, not {seconds:g}")
    cmd, grab, heard = ["ffmpeg", "-loglevel", "error", "-nostdin", "-y"], None, None
    if kind == ".wav":
        cmd += SINK
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
            # Stamped as they come, by the clock the sound is: wf-recorder's
            # own starts at its first frame.
            cmd += ["-use_wallclock_as_timestamps", "1", *UNPROBED] if sound else []
            cmd += ["-f", "nut", "-i", "pipe:0"]
        elif sound:
            # Frames of its own, stamped as ffmpeg reads them, by the clock
            # the sound is. rawvideo's -framerate is its time base too: at
            # FPS, each stamp would be rounded to a frame.
            grab = [sys.executable, "-c", "import sys; del sys.path[0]; from shotbox.x import feed; "
                    "sys.exit(feed(*sys.argv[1:]))", str(w), str(h), str(x), str(y), str(fps),
                    str(int(pointer))]
            cmd += ["-use_wallclock_as_timestamps", "1", *UNPROBED, "-f", "rawvideo", "-pix_fmt", "bgr0",
                    "-video_size", f"{w}x{h}", "-framerate", "1000", "-i", "pipe:0"]
        else:
            cmd += ["-f", "x11grab", "-draw_mouse", str(int(pointer)), "-framerate", str(fps),
                    "-video_size", f"{w}x{h}", "-i", f"{env['DISPLAY']}+{x},{y}"]
        # Each input's time starts at its own first frame, unless -isync
        # puts the sound's on the picture's: they go by the same clock. In
        # 10 ms pieces: ffmpeg stamps its default 50 ms ones 30 ms early.
        cmd += ["-isync", "0", "-fragment_size", "1920", *SINK] if sound else []
    if kind == ".wav":
        # By the sample: -t goes by the input's clock, tens of ms out.
        cmd += ["-af", f"atrim=end_sample={round(seconds * 48000)}"] if seconds is not None else []
        cmd += ["-c:a", "pcm_s16le"]
    else:
        cmd += ["-t", f"{seconds:g}"] if seconds is not None else []
        # Left to itself, ffmpeg guesses the rate from when frames came:
        # on a busy machine, 29.97 for 30, and a WebM a frame short.
        cmd += ["-r", str(fps)]
        cmd += VIDEO[".webm" if kind == ".webm" else ".mp4"]
        if sound:
            # Begun at the picture's first frame: silence before the sound's
            # first, the sound's before the picture's dropped.
            end = f",atrim=end_sample={round(seconds * 48000)}" if seconds is not None else ""
            cmd += ["-filter_complex", f"[1:a]aresample=async=1:first_pts=0{end},asplit[a][heard]",
                    "-map", "0:v", "-map", "[a]", *SOUND[kind]]
            # Ended by a signal, or by the sound server going, the picture
            # ends with the sound. Not when timed: atrim and -t end both (and
            # the keeper the picture, if the sound server goes), and a
            # picture a frame short of -t, as a busy machine's can be, would
            # cut the sound short with it.
            cmd += ["-shortest"] if seconds is None else []
            cmd += PROMPT[kind]
            heard = {"options": ["-map", "[heard]", "-c:a", "pcm_s16le", "-f", "wav"], "gap": gap}
    here = str(Path(__file__).resolve().parent.parent)     # this shotbox, wherever it is
    path = os.pathsep.join(filter(None, (here, env.get("PYTHONPATH"))))
    # -c puts the current directory first on the path, ahead of PYTHONPATH:
    # another shotbox there would be the one run.
    keeper = ("import sys; del sys.path[0]; from shotbox.x import keep_recording; "
              "sys.exit(keep_recording(*sys.argv[1:]))")
    # What it heard, in a file: a pipe would fill with a long list of gaps,
    # and wait while this waits for it.
    said = tempfile.TemporaryFile() if sound else subprocess.DEVNULL
    # Its own session: `shotbox run`, stopped, kills its command's group
    # two seconds on, which would cut the finishing short.
    proc = subprocess.Popen([sys.executable, "-c", keeper, json.dumps(cmd), str(out),
                             json.dumps(grab), json.dumps(heard), str(size or 0)],
                            env={**env, "PYTHONPATH": path, "SHOTBOX_FINISHES": "1"},
                            stdin=subprocess.PIPE,
                            stdout=said, stderr=subprocess.PIPE, start_new_session=True)
    proc.said = said
    return proc


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
    # Looked up here: after fork, with other threads, the loader may be locked.
    prctl = ctypes.CDLL(None, use_errno=True).prctl

    def tie():
        PR_SET_PDEATHSIG = 1
        prctl(PR_SET_PDEATHSIG, sig, 0, 0, 0)
        if os.getppid() != keeper:      # it went before the line above took
            os._exit(1)
    return tie


def keep_recording(cmd, out, grab, heard, size="0"):
    """Run the ffmpeg command `cmd` (JSON, without its output file) to
    record to `out`, reading what the command `grab` (JSON, or null)
    writes, and with `heard` (JSON: output options and a gap, or null), the
    sound to a WAV too, saying what `sound.hear` heard in it on stdout;
    and stop it with one signal, which it finishes the file on (a second
    makes it quit unfinished): when a line comes on stdin or stdin closes,
    as when what started this goes however it goes, or when the display
    goes (the sound server, for a .wav; sway, on Wayland), or when what
    it writes reaches `size` bytes (0 for no end); killed if it's still
    opening what it records 10 seconds in. Then make the GIF, for a .gif.
    Status 0, or 1 and why on stderr."""
    cmd, out, grab, heard = json.loads(cmd), Path(out), json.loads(grab), json.loads(heard)
    gif = out.suffix.lower() == ".gif"
    # Files with no names, which ffmpeg writes by their fds: nothing is left
    # behind however this goes, even killed outright.
    clip = tempfile.TemporaryFile() if gif else None
    sound_file = tempfile.TemporaryFile() if heard else None
    # Where ffmpeg reports its progress, which it does from when what it
    # records is open, whatever it holds back of the file.
    progress = tempfile.TemporaryFile()
    fds = [f.fileno() for f in (clip, sound_file, progress) if f]
    video = Path(f"/dev/fd/{clip.fileno()}") if gif else out
    wav = Path(f"/dev/fd/{sound_file.fileno()}") if heard else None
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
        proc = subprocess.Popen(cmd[:1] + ["-progress", f"/dev/fd/{progress.fileno()}"]
                                + cmd[1:] + (["-f", "mp4"] if gif else []) + [str(video)]
                                + (heard["options"] + [str(wav)] if heard else []),
                                stderr=subprocess.PIPE, pass_fds=fds,
                                stdin=feed.stdout if feed else subprocess.DEVNULL,
                                preexec_fn=tied(signal.SIGTERM))
        if feed:
            feed.stdout.close()
        sound = out.suffix.lower() == ".wav"
        pulse = os.environ["PULSE_SERVER"].removeprefix("unix:") if sound or heard else None
        wayland = bool(os.environ.get("SWAYSOCK"))
        sock = wayland and str(Path(os.environ["XDG_RUNTIME_DIR"], os.environ["WAYLAND_DISPLAY"]))
        # With no progress reported by then, ffmpeg's stuck opening what it
        # records (a sound server whose graph doesn't run, say), and deaf to
        # SIGTERM there.
        deadline = time.monotonic() + 10

        def begun():
            return os.fstat(progress.fileno()).st_size > 0
        while proc.poll() is None:
            up = source_up(os.environ, sound, heard)
            # Stopped, not cut off as ffmpeg's -fs would: the sound heard
            # ends with the picture.
            full = int(size) and video.exists() and video.stat().st_size >= int(size)
            late = time.monotonic() > deadline and not begun()
            if select.select([sys.stdin], [], [], 0.5)[0] or not up or full or late:
                proc.send_signal(signal.SIGTERM)
                break
        # Whether the sound server had gone when it ended, not later.
        cut = bool(heard) and not socket_up(pulse)
        if feed:
            # With sound, a moment for ffmpeg to stop both itself: ended by
            # the pipe, the picture stops while the sound runs on until
            # ffmpeg sees the signal, and timed, no -shortest ends it there.
            # Not with sway gone: ffmpeg can't stop the picture without it.
            try:
                proc.wait(timeout=2 if heard and (not wayland or socket_up(sock)) else 0)
            except subprocess.TimeoutExpired:
                pass
            # ffmpeg waits on its input, and with sway gone, wf-recorder
            # waits for good on a frame: an end to the pipe ends both.
            feed.kill()
            feed.wait()
        try:
            _, err = proc.communicate(
                timeout=None if begun() else max(deadline - time.monotonic(), 2))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            sys.stderr.write("ffmpeg couldn't record: it was still opening what it records "
                             "after 10 seconds\n")
            return 1
        if proc.returncode not in (0, 255) or not video.exists():
            tool, said = "ffmpeg", err.decode(errors="replace").strip()
            if feed and feed.returncode > 0:        # what ffmpeg says is only that it had nothing
                grab_err.seek(0)
                tool = "wf-recorder" if wayland else "shotbox's frames"
                said = grab_err.read().decode(errors="replace").strip()
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
                                     str(video)], capture_output=True, text=True,
                                    pass_fds=fds).stdout.strip()
        if not frames:
            if not gif:
                video.unlink()
            sys.stderr.write("nothing was recorded: it stopped before the first frame\n")
            return 1
        if heard:
            from .sound import hear
            said = hear(wav, gap=heard["gap"])
            if cut:
                # The sound ended first, and timed, nothing ended the picture
                # with it: cut to the sound's length now ffmpeg's done, where
                # a picture a frame short can't cut the sound as -shortest can.
                # Into a link's target, over what a cut killed partway left.
                out = out.resolve()
                part = out.with_name(f".{out.name}")
                part.unlink(missing_ok=True)
                made = subprocess.run(["ffmpeg", "-loglevel", "error", "-nostdin", "-y", "-i",
                                       str(out), "-c", "copy", "-t", str(said["seconds"]),
                                       "-movflags", "+faststart", str(part)],
                                      stderr=subprocess.PIPE)
                if made.returncode:
                    part.unlink(missing_ok=True)
                    sys.stderr.write("ffmpeg couldn't end the picture with the sound: "
                                     + made.stderr.decode(errors="replace").strip() + "\n")
                    return 1
                part.replace(out)
            print(json.dumps({**said, "cut": cut}), flush=True)
        if gif:
            made = subprocess.run(["ffmpeg", "-loglevel", "error", "-nostdin", "-y", "-i",
                                   str(video), "-vf",
                                   "split[a][b];[a]palettegen[p];[b][p]paletteuse", str(out)],
                                  stderr=subprocess.PIPE, pass_fds=fds)
            if made.returncode:
                sys.stderr.write("ffmpeg couldn't make the GIF: "
                                 + made.stderr.decode(errors="replace").strip() + "\n")
                return 1
        return 0
    finally:
        if feed:
            feed.kill()
            feed.wait()


def source_up(env, wav=False, sound=False):
    """Whether what a recording records is still there: the display (on
    Wayland, sway), or for a .wav the sound server; with `sound`, the
    sound server too, whose going ends the picture as well."""
    pulse = env.get("PULSE_SERVER", "").removeprefix("unix:")
    if wav:
        return socket_up(pulse)
    up = (socket_up(str(Path(env["XDG_RUNTIME_DIR"], env["WAYLAND_DISPLAY"])))
          if env.get("SWAYSOCK") else display_up(env["DISPLAY"]))
    return up and (not sound or socket_up(pulse))


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


def blackout(path, boxes):
    """Each box, (width, height, x, y), solid black in the picture at
    `path`: its pixels replaced, so nothing of what was there is left in
    the file. Unsmoothed, so an edge's pixels are black or untouched."""
    draws = [a for bw, bh, bx, by in boxes
             for a in ("-draw", f"rectangle {bx},{by} {bx + bw - 1},{by + bh - 1}")]
    subprocess.run(CONVERT + [str(path), "+antialias", "-fill", "black", "-stroke", "none",
                              *draws] + QUIET_PNG + [str(path)], check=True)
    return Path(path)


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


def draw_pointer(frame, width, height, x, y, cursor):
    """Draw `cursor`, as Display.cursor has it, on `frame`, a bytearray of
    the display's box (width, height, x, y) four bytes a pixel, blue first:
    blended as ffmpeg's x11grab blends it, for the same picture."""
    cx, cy, xhot, yhot, w, h, pixels = cursor
    left, top = cx - xhot - x, cy - yhot - y
    for row in range(max(0, -top), min(h, height - top)):
        for col in range(max(0, -left), min(w, width - left)):
            i = (row * w + col) * 4
            a = pixels[i + 3]
            if a:
                j = ((top + row) * width + left + col) * 4
                for k in range(3):
                    # A cursor whose colors aren't premultiplied goes past 255:
                    # wrapped, as x11grab's bytes wrap.
                    frame[j + k] = (pixels[i + k] + (frame[j + k] * (255 - a) + 127) // 255) & 255


def feed(width, height, x, y, fps, pointer):
    """Write the display's box (width, height, x, y) to stdout `fps` times
    a second, four bytes a pixel, blue first, with the pointer drawn on if
    `pointer` is "1", for ffmpeg to stamp each frame as it reads it: under
    load, x11grab's own frames, taken inside ffmpeg, drift from the sound.
    Late, the next is taken at once, and the schedule kept from there: a
    frame waiting in the pipe would be stamped late. Until the display or
    the pipe goes, status 0; 1 if the display can't be opened or its box
    read, saying why."""
    from .xtest import Closed, Display, XError
    width, height, x, y, fps = map(int, (width, height, x, y, fps))
    try:
        d = Display()
    except (OSError, XError) as e:
        sys.stderr.write(f"can't take the display's frames: {e}\n")
        return 1
    first, due = True, time.monotonic()
    while True:
        try:
            frame = d.pixels(d.root, x, y, width, height)
            if pointer == "1":
                frame = bytearray(frame)
                draw_pointer(frame, width, height, x, y, d.cursor())
        except (Closed, OSError):
            return 0
        except XError as e:
            if first:
                sys.stderr.write(f"can't take the display's {width}x{height}+{x}+{y}: {e}\n")
                return 1
            frame = None        # skipped: the next is taken on time
        first = False
        view = memoryview(frame or b"")
        try:
            while view:
                view = view[os.write(1, view):]
        except BrokenPipeError:
            return 0
        due += 1 / fps
        wait = due - time.monotonic()
        if wait > 0:
            # Not time.sleep, which fails under libfaketime 0.9.10 from
            # Python 3.11, as a --clock session's keeper may run.
            select.select([], [], [], wait)
        else:
            due = time.monotonic()


def regions(a, b, fuzz="0%", ignore=(), margin=0, at=(0, 0), gap=8):
    """Where two images differ, as compare counts it, in regions: pixels
    fewer than `gap` apart are one, and some up to twice that. Each one's
    count and box, (width, height, x, y), the most first."""
    differs, shifted, _, _ = _differs(a, b, fuzz, ignore, margin, at)
    width = int(size_of(a).split("x")[0])
    mask = subprocess.run([*differs, "-depth", "8", "gray:-"], input=shifted,
                          capture_output=True, check=True).stdout
    # Each cell gap on a side that something differs in: its count and box.
    cells = {}
    for y in range(len(mask) // width):
        row = mask[y * width:(y + 1) * width]
        x = row.find(255)
        while x >= 0:
            c = cells.setdefault((x // gap, y // gap), [0, x, y, x, y])
            c[0] += 1
            c[1:] = min(c[1], x), min(c[2], y), max(c[3], x), max(c[4], y)
            x = row.find(255, x + 1)
    # Cells touching, corners too, are one region.
    found, seen = [], set()
    for start in cells:
        if start in seen:
            continue
        seen.add(start)
        todo, n, x0, y0, x1, y1 = [start], 0, *cells[start][1:]
        while todo:
            cx, cy = todo.pop()
            c = cells[cx, cy]
            n += c[0]
            x0, y0, x1, y1 = min(x0, c[1]), min(y0, c[2]), max(x1, c[3]), max(y1, c[4])
            for nb in ((cx + i, cy + j) for i in (-1, 0, 1) for j in (-1, 0, 1)):
                if nb in cells and nb not in seen:
                    seen.add(nb)
                    todo.append(nb)
        found.append((n, (x1 - x0 + 1, y1 - y0 + 1, x0, y0)))
    return sorted(found, key=lambda r: -r[0])


def rgb(path):
    """A picture's (width, height, RGB bytes), as grab has them."""
    w, h = map(int, size_of(path).split("x"))
    data = subprocess.run([*CONVERT, str(path), "-alpha", "off", "-depth", "8", "rgb:-"],
                          capture_output=True, check=True).stdout
    return w, h, data


def box_of(geometry):
    """A WIDTHxHEIGHT+X+Y box, at least 1x1, as (width, height, x, y); None
    if it isn't one."""
    m = re.fullmatch(r"(\d+)x(\d+)\+(\d+)\+(\d+)", geometry)
    got = m and tuple(int(n) for n in m.groups())
    return got if got and got[0] and got[1] else None


def kept(width, height, ignore=()):
    """Each row of a picture `width` by `height`, as the spans (x0, x1) of
    it that the boxes in `ignore`, (width, height, x, y), leave."""
    rows = []
    for y in range(height):
        row, at = [], 0
        for x0, x1 in sorted((min(width, bx), min(width, bx + bw))
                             for bw, bh, bx, by in ignore if by <= y < by + bh):
            if x0 > at:
                row.append((at, x0))
            at = max(at, x1)
        rows.append(row + [(at, width)] if at < width else row)
    return rows


def differ(a, b, fuzz=0.0, most=0, rows=None, at=(0, 0)):
    """How many pixels of a picture `a`, (width, height, RGB bytes),
    differ from those of `b`, another, under them with a's top left
    corner at `at` in it, as compare counts them, where any channel
    differs by more than `fuzz` percent; only in the spans of `rows`
    (kept's) if given; up to `most` + 1, where it stops counting. Runs
    that are the same are passed over whole, so a picture that's mostly
    so is quick."""
    (w, h, a), (bw, _, b) = a, b
    limit, n, run = fuzz / 100 * 255, 0, 512 * 3
    for y, spans in enumerate(rows or [[(0, w)]] * h):
        for x0, x1 in spans:
            i0, j0 = (y * w + x0) * 3, ((y + at[1]) * bw + x0 + at[0]) * 3
            for start in range(0, (x1 - x0) * 3, run):
                end = min(start + run, (x1 - x0) * 3)
                if a[i0 + start:i0 + end] == b[j0 + start:j0 + end]:
                    continue
                for i, j in zip(range(i0 + start, i0 + end, 3), range(j0 + start, j0 + end, 3)):
                    if (abs(a[i] - b[j]) > limit or abs(a[i + 1] - b[j + 1]) > limit
                            or abs(a[i + 2] - b[j + 2]) > limit):
                        n += 1
                        if n > most:
                            return n
    return n


def nearest(x, y, margin):
    """The places up to `margin` pixels from x, y either way, nearest
    first."""
    steps = range(-margin, margin + 1)
    return sorted(((x + dx, y + dy) for dx in steps for dy in steps),
                  key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)


def best_place(a, b, places, fuzz=0.0, most=0, rows=None):
    """Of `places` for picture `a`'s top left corner in `b` (differ's),
    the first where fewest of its pixels differ, if that's `most` or
    fewer: (how many, (x, y)), else None."""
    best = None
    for at in places:
        n = differ(a, b, fuzz, best[0] - 1 if best else most, rows, at)
        if n <= (best[0] - 1 if best else most):
            best = (n, at)
            if not n:
                break
    return best


def band(width, height, margin):
    """The band `margin` pixels wide round the edge of a picture, as boxes."""
    return [(width, margin, 0, 0), (width, margin, 0, height - margin),
            (margin, height, 0, 0), (margin, height, width - margin, 0)] if margin else []


def moved(a, b, fuzz="0%", most=0, ignore=(), margin=0):
    """How far, up to `margin` pixels either way, picture `b` moved from
    `a`, two images of one size: where its pixels, but for `ignore`'s
    and the band that wide round the edge, match a's best, as many
    differing by more than `fuzz` as `most`, or a hundredth of them,
    or fewer; else not at all. (x, y). ValueError if they're not one
    size, or the band leaves nothing."""
    a, b = rgb(a), rgb(b)
    if a[:2] != b[:2]:
        raise ValueError("not the same size: {}x{} and {}x{}".format(*a[:2], *b[:2]))
    if 2 * margin >= min(a[:2]):
        raise ValueError("a margin of {} leaves nothing of {}x{}".format(margin, *a[:2]))
    rows = kept(*a[:2], [*ignore, *band(*a[:2], margin)])
    # Counted no further than that at each place: two pictures that are
    # nothing alike would take seconds to count over and over.
    found = best_place(a, b, nearest(0, 0, margin), float(fuzz[:-1]),
                       max(most, a[0] * a[1] // 100), rows)
    return found[1] if found else (0, 0)


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
    other sign it's done. Only the `box` (width, height, x, y) of it, if
    given, and not the boxes in `ignore`, in what's taken: a clock.
    `backend`, as for try_capture."""

    def __init__(self, env, name=None, quiet=0.5, backend=None, box=None, ignore=()):
        self.env, self.name, self.quiet = env, name, quiet
        self.backend = backend or sys.modules[__name__]
        self.last, self.since = None, 0.0
        self.box, self.ignore, self.rows = box, ignore, {}
        self.small = None   # the window's size, where the box isn't inside it

    def digest(self, window):
        if not (self.box or self.ignore):
            return self.backend.fingerprint(self.env, window)
        got = self.backend.grab(self.env, window, self.box)
        if not got:     # the slower way
            fd, path = tempfile.mkstemp(".png", dir=self.env["SHOTBOX_SCRATCH"])
            os.close(fd)
            try:
                self.backend.capture(self.env, path, window=window,
                                     crop=self.box and "{}x{}+{}+{}".format(*self.box))
                got = rgb(path)
            except subprocess.CalledProcessError:   # a window gone meanwhile
                return None
            finally:
                os.unlink(path)
        w, h, data = got
        rows = self.rows.get((w, h)) or self.rows.setdefault((w, h), kept(w, h, self.ignore))
        d = hashlib.sha256()
        for y, spans in enumerate(rows):
            for x0, x1 in spans:
                d.update(data[(y * w + x0) * 3:(y * w + x1) * 3])
        return d.digest()

    def __call__(self):
        window = None
        if self.name:
            window = self.backend.find_window(self.env, self.name)
            if not window:
                self.last = None
                return False
            box = self.box
            fits = not box or (box[0] + box[2] <= window[2] and box[1] + box[3] <= window[3])
            self.small = None if fits else f"{window[2]}x{window[3]}"
            if not fits:
                self.last = None
                return False
        # The window's place and size too: one that moves hasn't settled.
        now = (window[2:] if window else None, self.digest(window))
        t = time.monotonic()
        if now[1] is None or now != self.last:
            self.last, self.since = now, t
            return False
        return t - self.since >= self.quiet


class _ClientMessage(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong),
                ("message_type", ctypes.c_ulong), ("format", ctypes.c_int),
                ("l", ctypes.c_long * 5)]


class _Event(ctypes.Union):
    _fields_ = [("xclient", _ClientMessage), ("pad", ctypes.c_long * 24)]


class _Error(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("display", ctypes.c_void_p),
                ("resourceid", ctypes.c_ulong), ("serial", ctypes.c_ulong),
                ("error_code", ctypes.c_ubyte), ("request_code", ctypes.c_ubyte),
                ("minor_code", ctypes.c_ubyte)]


# Windows an X error was about: one closed while it's pinged, which is no
# reason to stop. Kept here, as is the handler, which libX11 calls from C.
_BAD = set()
_ON_ERROR = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_Error))(
    lambda d, e: _BAD.add(e.contents.resourceid) or 0)


class Pinger:
    """Asking windows on the display in `env` whether their programs answer,
    by _NET_WM_PING, which a toolkit answers from its main loop: one that's
    busy or hung doesn't. libX11 through ctypes, which reads XAUTHORITY's
    entries as the toolkits do."""

    STRUCTURE, SUBSTRUCTURE, CLIENT_MESSAGE = 1 << 17, 1 << 19, 33

    def __init__(self, env):
        x11 = self.x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XInternAtom.restype = x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        for f in ("XDefaultRootWindow", "XConnectionNumber", "XPending", "XFlush", "XFree"):
            getattr(x11, f).argtypes = [ctypes.c_void_p]
        x11.XSelectInput.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_long]
        x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long,
                                   ctypes.POINTER(_Event)]
        x11.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Event)]
        x11.XGetWMProtocols.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                        ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
                                        ctypes.POINTER(ctypes.c_int)]
        x11.XSetErrorHandler(_ON_ERROR)
        self.d = x11.XOpenDisplay(env.get("DISPLAY", "").encode())
        if not self.d:
            raise RuntimeError(f"can't reach the display {env.get('DISPLAY')}: "
                               "is the session still up?")
        self.protocols = x11.XInternAtom(self.d, b"WM_PROTOCOLS", False)
        self.ping_atom = x11.XInternAtom(self.d, b"_NET_WM_PING", False)
        self.root = x11.XDefaultRootWindow(self.d)
        # Qt 5 sends its answer for StructureNotify only, GTK for Substructure.
        x11.XSelectInput(self.d, self.root, self.STRUCTURE | self.SUBSTRUCTURE)
        # Stamps unique to this process: another wait's answers aren't ours.
        self.salt, self.count = (os.getpid() & 0x7FFF) << 16, 0
        self.sent, self.last = set(), {}

    def takes(self, wid):
        """Whether window `wid` (a number) lists _NET_WM_PING in its
        WM_PROTOCOLS; None if it's gone."""
        got, n = ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_int()
        _BAD.discard(wid)
        ok = self.x11.XGetWMProtocols(self.d, wid, ctypes.byref(got), ctypes.byref(n))
        if wid in _BAD:
            return None
        listed = ok and self.ping_atom in got[:n.value]
        if ok:
            self.x11.XFree(got)
        return bool(listed)

    def ping(self, wids):
        """Ping each of `wids` with this round's stamp, and return it."""
        self.count = (self.count + 1) & 0xFFFF
        stamp = self.salt | self.count
        self.sent.add(stamp)
        for wid in wids:
            e = _Event()
            e.xclient.type, e.xclient.window = self.CLIENT_MESSAGE, wid
            e.xclient.message_type, e.xclient.format = self.protocols, 32
            e.xclient.l[:3] = self.ping_atom, stamp, wid
            # No event mask: it goes to the window's owner.
            self.x11.XSendEvent(self.d, wid, False, 0, ctypes.byref(e))
        self.x11.XFlush(self.d)
        return stamp

    def read(self, until):
        """Take the answers that come in until `until` (time.monotonic());
        `last` holds each window's latest: (its stamp, when it came)."""
        e, fd = _Event(), self.x11.XConnectionNumber(self.d)
        while True:
            while self.x11.XPending(self.d):
                self.x11.XNextEvent(self.d, ctypes.byref(e))
                c = e.xclient
                stamp = c.l[1] & 0xFFFFFFFF
                if (c.type == self.CLIENT_MESSAGE and c.message_type == self.protocols
                        and c.l[0] == self.ping_atom and stamp in self.sent):
                    self.last[c.l[2]] = stamp, time.monotonic()
            left = until - time.monotonic()
            if left <= 0:
                return
            select.select([fd], [], [], left)


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


def _differs(a, b, fuzz, ignore, margin, at):
    """convert's arguments for where a and b differ, as compare counts it,
    white there and black elsewhere, and its input, with the boxes left
    out filled; or ValueError if they aren't the same size."""
    size = size_of(a)
    if size != size_of(b):
        raise ValueError(f"not the same size: {size} and {size_of(b)}")
    shifted = None
    if at != (0, 0):
        # Rolled round: what comes in at the edge is in the band.
        shifted = subprocess.run([*CONVERT, str(b), "-roll", f"{-at[0]:+d}{-at[1]:+d}", "miff:-"],
                                 capture_output=True, check=True).stdout
        b = "miff:-"
    ignore = [*ignore, *band(*map(int, size.split("x")), margin)]
    # Each box filled in each picture alike, so nothing in it differs.
    boxes = [arg for bw, bh, bx, by in ignore
             for arg in ("-draw", f"rectangle {bx},{by} {bx + bw - 1},{by + bh - 1}")]
    # The per-pixel difference, its largest channel, then on or off. Counted
    # by hand: compare's own AE metric weighs pixels differently from one
    # ImageMagick release to the next.
    # sRGB: ImageMagick reads a gray PNG, as it writes any picture with no
    # color in it, as one channel, and would count only that.
    return ([*CONVERT, str(a), str(b), "-colorspace", "sRGB", "-alpha", "off",
             "-fill", "black", *boxes, "-compose", "difference",
             "-composite", "-separate", "-evaluate-sequence", "max", "-threshold", fuzz],
            shifted, b, boxes)


def compare(a, b, diff=None, fuzz="0%", box=False, ignore=(), margin=0, at=(0, 0)):
    """How many pixels differ between two images (0 means the same): pixels
    where any channel differs by more than `fuzz`, but for those in the
    boxes in `ignore`, (width, height, x, y), and in a band `margin`
    pixels wide round the edge, with b's moved by -`at` (moved's). With
    `diff`, also writes an image with the differences marked in red, and
    what's left out magenta. With `box`, the count and the box they're
    in, (width, height, x, y), or None."""
    differs, shifted, b, boxes = _differs(a, b, fuzz, ignore, margin, at)
    out = subprocess.run(
        # A black border so the box (%@, what -trim would keep) is found
        # around what differs even when a corner does; -quiet, since with
        # nothing in the box it warns.
        [*differs, "-bordercolor", "black", "-border", "1", "-quiet",
         # -precision: else a million or more is said rounded, 1.04e+06.
         "-precision", "16", "-format", "%[fx:round(mean*w*h)] %@", "info:"],
        input=shifted, capture_output=True, check=True).stdout.decode().split()
    if diff:
        png = ["-define", "png:exclude-chunks=date,time"]
        shown = subprocess.run(["compare", "-fuzz", fuzz, *png, str(a), str(b),
                                # PNG: ImageMagick 6's MIFF of it is gray.
                                "png:-" if boxes else str(diff)], input=shifted,
                               capture_output=True).stdout
        if boxes:
            # Magenta, as Playwright's mask is.
            subprocess.run([*CONVERT, "png:-", "-fill", "#ff00ff", *boxes, *png, str(diff)],
                           input=shown, capture_output=True)
    n = int(out[0])
    w, h, x, y = map(int, re.split("[x+]", out[1]))
    return (n, (w, h, x - 1, y - 1) if n else None) if box else n
