"""shotbox: reproducible screenshots of real programs, in a sealed session.

    shotbox shoot OUT.png [options] -- COMMAND...   run it, wait, take the picture
    shotbox run [options] -- COMMAND...             run it in a sealed session
    shotbox exec [options] -- COMMAND...            the same, with no display
    shotbox start [--name NAME] [options]           a session that lasts, until stopped
    shotbox spawn --session NAME -- COMMAND...      run a program in a lasting session
    shotbox stop NAME | shotbox sessions            stop one; list them
    shotbox term [options] -- COMMAND...            a terminal to take pictures of
    shotbox mutate --target T [options] -- TEST...  a browser test against changes to its page
    shotbox wait window|port|file|ready|stable|announce|node|match|change [ARG]
    shotbox wait line RE FILE                       (inside a session) wait for it
    shotbox capture OUT.png [--window RE] [--burst N]  (inside a session) take a picture
    shotbox record OUT.mp4|OUT.wav [--seconds S]    (inside a session) take a video, or the sound
    shotbox key CHORD...                            (inside a session) press keys
    shotbox type TEXT                               (inside a session) type text
    shotbox click X Y [--window RE]                 (inside a session) click there
    shotbox move X Y [--window RE]                  (inside a session) point there
    shotbox drag X1 Y1 X2 Y2 [--window RE]          (inside a session) drag
    shotbox scroll X Y DY [DX] [--window RE]        (inside a session) turn the wheel there
    shotbox park                                    (inside a session) pointer out of the way
    shotbox focus RE                                (inside a session) give a window the keyboard
    shotbox play SCRIPT                             (inside a session) timed steps, on one clock
    shotbox latency move X1 Y1 X2 Y2 --watch G      (inside a session) how fast it follows
    shotbox flicker [--window RE] [--crop G]        (inside a session) find what flickers
    shotbox pad press|stick|trigger|neutral ...     (inside a session) work a gamepad
    shotbox a11y tree|lint|tabwalk|click|contrast   (inside a session) the accessible tree
    shotbox mcp [--allow FILE]                      sessions for an agent, over MCP (stdio)
    shotbox doctor                                  what's here of what each command needs
    shotbox --version                               which shotbox this is
    shotbox vision IN.png OUT.png --as KIND         a picture as colorblind eyes see it
    shotbox listen IN.wav                           its level, clipping and dropouts
    shotbox compare A.png B.png [--diff D.png]      how many pixels differ
    shotbox compare REFDIR NEWDIR [NAME...]         the same, picture by picture

`shotbox --session NAME COMMAND ...` runs any of the commands for inside a
session in a lasting one. `shotbox COMMAND --help` for each one's options.
"""

import argparse
import contextlib
import dataclasses
import io
import json
import math
import os
import re
import shlex
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

from . import __version__, a11y, kept, mcp, mutation, pad, sound, term, usage, x, xtest
from .screen import Screen, SessionError, wheel
from .session import NEEDS, Session, off_the_clock, unclocked


def scale(s):
    n = float(s)
    if not n > 0:
        raise argparse.ArgumentTypeError(f"more than 0, not {s}")
    return n


def seconds(s):
    n = float(s)
    if not (math.isfinite(n) and 0 <= n <= 86400):
        raise argparse.ArgumentTypeError(f"seconds, 0 to a day (86400), not {s}")
    return n


def run_seconds(s):
    n = float(s)
    if not (math.isfinite(n) and 0 < n <= 86400):
        raise argparse.ArgumentTypeError(f"seconds, more than 0, up to a day (86400), not {s}")
    return n


def size(s):
    w, h = s.lower().split("x")
    return int(w), int(h)


class Gone(argparse.Action):
    """An option that's gone: what to use instead, its const."""
    def __call__(self, p, *_):
        p.error(self.const)


def session_options(p, display=True):
    """The options `run`, `shoot` and `start` take; without `display`, as
    `exec` takes them, those that are about a display left out, and off."""
    g = p.add_argument_group("the session")
    if display:
        g.add_argument("--screen", type=size, default=(1280, 800), metavar="WxH",
                       help="the display's size (1280x800)")
    g.add_argument("--desktop", action="store_true",
                   help="real GSettings (dconf) and the system's D-Bus services, "
                        "for desktop shells; the default is settings of its own "
                        "and nothing activatable")
    g.add_argument("--machine-bus", action="store_true",
                   help="the machine's system bus and printers, for a program that "
                        "needs its services (NetworkManager, logind, CUPS); the "
                        "default is a stand-in bus and no printers. A desktop's "
                        "settings can show its Wi-Fi networks, Bluetooth devices, "
                        "user accounts and printers then: check pictures before "
                        "sharing them")
    g.add_argument("--system-bus", nargs=0, action=Gone, help=argparse.SUPPRESS,
                   const="--system-bus is gone: the stand-in system bus is the default "
                         "now; --machine-bus for the machine's")
    g.add_argument("--seed", metavar="DIR", help="start the scratch home as a copy of DIR")
    g.add_argument("--env", action="append", default=[], metavar="NAME=VALUE",
                   help="set a variable inside (repeatable)")
    g.add_argument("--pass", dest="passthrough", action="append", default=[], metavar="NAME",
                   help="let a variable through from outside (repeatable)")
    g.add_argument("--keep", action="store_true", help="keep the scratch dir, and say where")
    g.add_argument("--audio", action="store_true",
                   help="a PipeWire of the session's own, pulse protocol too, whose "
                        "one sink plays to nowhere")
    g.add_argument("--usage", nargs="?", const="-", metavar="FILE",
                   help="say, when the session stops, what its programs used, and its own "
                        "processes: peak memory and CPU time; with FILE, write it there as "
                        "JSON too (with `start`, only there)")
    g.add_argument("--net", choices=["loopback"],
                   help="loopback: a network of the session's own, which its programs "
                        "share, with nothing on it but them; needs bubblewrap, and not "
                        "--no-sandbox")
    g.add_argument("--identity", metavar="USER@HOST",
                   help="who and where programs are: whoami, ls -l's owner, hostname, "
                        "$USER and $HOSTNAME say USER and HOST, not yours; needs "
                        "bubblewrap, and not --no-sandbox")
    g.add_argument("--clock", metavar="TIME",
                   help="the wall clock starts at TIME (ISO 8601, 2026-09-26T18:40:00, in "
                        "--tz or UTC) and runs on, for every program; needs libfaketime")
    g.add_argument("--tz", metavar="ZONE", help="the time zone (Europe/Berlin; UTC by default)")
    g.add_argument("--no-sandbox", dest="sandbox", action="store_false", default=None,
                   help="run programs outside bubblewrap, for those that need setuid "
                        "helpers (sudo, AppImages); they can reach the machine's sound "
                        "devices then. SHOTBOX_SANDBOX=off is this for every session")
    p.set_defaults(display=display)
    if not display:
        p.set_defaults(screen=None, wayland=False, xwayland=False, a11y=False,
                       announce_log=None, dark=False, high_contrast=False, text_scale=None,
                       gamepads=0)
        return
    g.add_argument("--wayland", action="store_true",
                   help="a Wayland session, under a headless sway, rather than X11 on "
                        "Xvfb (docs/wayland.md)")
    g.add_argument("--xwayland", action="store_true",
                   help="a Wayland session with Xwayland in it too, for X clients")
    g.add_argument("--a11y", action="store_true",
                   help="an accessibility (AT-SPI) bus of the session's own, for "
                        "`shotbox a11y` and announcements")
    g.add_argument("--announce-log", metavar="FILE",
                   help="record what programs announce here (implies --a11y; "
                        "$SHOTBOX_SCRATCH/announcements by default)")
    g.add_argument("--dark", action="store_true", help="the dark style")
    g.add_argument("--high-contrast", action="store_true", help="the high-contrast style")
    g.add_argument("--text-scale", type=scale, metavar="N",
                   help="scale text by N (1.25), as the desktop's text scaling does")
    g.add_argument("--gamepads", type=int, default=0, metavar="N",
                   help="N virtual Xbox 360 pads (1 to 4), seen only by the session's "
                        "programs, for `shotbox pad`; needs write access to /dev/uinput, "
                        "and bubblewrap")


def stop_once(signum, frame):
    """Stop as ctrl-C does, the first time: another signal (a kill sent
    twice, say) mustn't cut short the letting go that follows."""
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, signal.SIG_IGN)
    raise KeyboardInterrupt


def make_session(a):
    # Stopped by kill or timeout(1), stop the session too, as for ctrl-C,
    # rather than leave its display and programs running.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    env = dict(e.split("=", 1) for e in a.env)
    return Session(size=a.screen, desktop=a.desktop, machine_bus=a.machine_bus,
                   seed=a.seed, keep=a.keep, env=env, passthrough=a.passthrough,
                   wayland=a.wayland, xwayland=a.xwayland, a11y=a.a11y,
                   announce_log=a.announce_log, dark=a.dark,
                   high_contrast=a.high_contrast, text_scale=a.text_scale,
                   audio=a.audio, gamepads=a.gamepads, sandbox=a.sandbox,
                   display=a.display, net=a.net, usage=bool(a.usage),
                   identity=a.identity, clock=a.clock, tz=a.tz)


@contextlib.contextmanager
def session(a):
    """make_session's session, and what it used said when it's stopped."""
    s = make_session(a)
    try:
        with s:
            yield s
    finally:
        say_usage(a, s)


def say_usage(a, s):
    """What the session used, if --usage asked: on stderr, and to its FILE."""
    if not s.used:
        return
    sys.stderr.write(f"shotbox: {usage.line(s.used)}\n")
    if a.usage != "-":
        Path(a.usage).write_text(json.dumps(s.used, indent=2) + "\n")


def self_command():
    """How to run this shotbox again, from inside a session: this checkout's
    bin/shotbox, or else this Python's shotbox module, as installed."""
    here = Path(__file__).resolve().parent.parent / "bin" / "shotbox"
    return [sys.executable, str(here)] if here.exists() else [sys.executable, "-m", "shotbox"]


def split(argv):
    """Options, and the command after --."""
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1:]
    return argv, []


def command(cmd, p):
    if not cmd:
        p.error("no command given, after --")
    return cmd


def cmd_run(argv):
    p = argparse.ArgumentParser(prog="shotbox run",
                                description="Run a command in a sealed session: its own X display, "
                                            "D-Bus and home, and none of yours.")
    session_options(p)
    opts, cmd = split(argv)
    a = p.parse_args(opts)
    cmd = command(cmd, p)
    with session(a) as s:
        return s.run(cmd)


def cmd_exec(argv):
    p = argparse.ArgumentParser(prog="shotbox exec",
                                description="Run a command sealed as in a session, with "
                                            "no display: a headless browser, a test "
                                            "suite, anything that's to reach neither your "
                                            "desktop nor your speakers.")
    session_options(p, display=False)
    opts, cmd = split(argv)
    a = p.parse_args(opts)
    cmd = command(cmd, p)
    with session(a) as s:
        return s.run(cmd)


def cmd_mutate(argv):
    # No abbreviations, so the reproduce line can drop --save and the rest.
    p = argparse.ArgumentParser(prog="shotbox mutate", allow_abbrev=False,
                                description="Run a test against small changes to the page it "
                                            "tests, each in a session of its own, to find a "
                                            "test that fails on one that shouldn't matter "
                                            "(BRITTLE) or passes on one that should (BLIND). "
                                            "The test calls mutated() from shotbox's npm "
                                            "package, which makes each change.")
    session_options(p)
    p.add_argument("--target", action="append", default=[], metavar="T",
                   help=f"what to change: {mutation.FORMS} (repeatable)")
    p.add_argument("--kinds", default=",".join(mutation.MATTERS), metavar="K,K",
                   help="which changes (%(default)s)")
    p.add_argument("--only", metavar="RUN", help="only this run, as the report names it")
    p.add_argument("--confirm", type=int, default=1, metavar="N",
                   help="reruns a failure on a change that shouldn't matter needs, "
                        "to be BRITTLE (1)")
    p.add_argument("--timeout", type=run_seconds, metavar="S",
                   help="stop a run after S seconds (by default, a changed run after 3 "
                        "times the slower clean run, at least 30, and the first two clean "
                        "runs never)")
    p.add_argument("--save", metavar="DIR", help="keep each run's output and report in DIR")
    p.add_argument("--strict", action="store_true", help="fail on UNAPPLIED runs too")
    p.add_argument("--json", action="store_true", help="the report as JSON, on stdout")
    opts, cmd = split(argv)
    a = p.parse_args(opts)
    cmd = command(cmd, p)
    if a.confirm < 1:
        p.error("--confirm needs 1 or more")
    try:
        mutation.plan(a.target, a.kinds.split(","), a.only)
    except SessionError as e:
        p.error(str(e))
    try:
        rep = mutation.mutate(cmd, a.target, kinds=a.kinds.split(","), confirm=a.confirm,
                              timeout=a.timeout, only=a.only, save=a.save, strict=a.strict,
                              out=None if a.json else sys.stdout,
                              _new_session=lambda: session(a))
    except (SessionError, OSError) as e:
        # 1 is for what the runs found: this is that they couldn't all be made.
        sys.stderr.write(f"shotbox: {e}\n")
        return 2
    found = rep.brittle_runs + rep.blind_runs + (rep.unapplied_runs if a.strict else [])
    again = None
    if found:
        drop = {"--only": 2, "--save": 2, "--json": 1}
        rest, skip = [], 0
        for o in opts:
            name = o.split("=", 1)[0]
            if skip:
                skip -= 1
            elif name in drop:
                skip = drop[name] - 1 if "=" not in o else 0
            else:
                rest.append(o)
        again = "shotbox mutate " + shlex.join([*rest, "--only", found[0].label, "--", *cmd])
    if a.json:
        print(json.dumps({**dataclasses.asdict(rep), "reproduce": again}, indent=2))
    elif again:
        print(f"reproduce: {again}")
    return rep.status


def tail(path, n=30):
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return "(no output)"
    return "\n".join(lines[-n:]) or "(no output)"


def cmd_shoot(argv):
    p = argparse.ArgumentParser(
        prog="shotbox shoot",
        description="Start a command in a sealed session, wait until it's ready, "
                    "take a screenshot, and stop it.")
    p.add_argument("out", help="the PNG to write")
    p.add_argument("--window", metavar="RE",
                   help="take this window (its title, a regex matched in full, "
                        "or app=RE for its app id) "
                        "instead of the whole display; also waits for it")
    p.add_argument("--crop", metavar="WxH+X+Y",
                   help="then crop to this; without it, to what the program "
                        "wrote to $SHOTBOX_SCRATCH/crop, if it did (as "
                        "`shotbox term --crop` does)")
    p.add_argument("--wait", action="append", default=[], metavar="WHAT",
                   help="wait for window:RE, port:N, file:PATH, ready (the "
                        "program touched $SHOTBOX_SCRATCH/ready, as `shotbox "
                        "term` does after its steps), or stable[:SECS] (the "
                        "--window, or the display, unchanged for SECS, 0.5); "
                        "repeatable, in order")
    p.add_argument("--settle", type=float, default=0.3, metavar="SECS",
                   help="then wait this long for the last paint (0.3)")
    p.add_argument("--park", action="store_true",
                   help="move the pointer out of the way before the picture")
    vision_option(p)
    p.add_argument("--timeout", type=float, default=30, metavar="SECS",
                   help="give up on a wait after this long (30)")
    p.add_argument("--log", metavar="FILE", help="keep the command's output here")
    p.add_argument("--failed", metavar="FILE",
                   help="if it fails, a picture of the screen goes here "
                        "(OUT's name with -failed, beside it)")
    twice_options(p)
    session_options(p)
    opts, cmd = split(argv)
    a = p.parse_args(opts)
    cmd = command(cmd, p)
    waits = list(a.wait)
    if a.window and not any(w.startswith("window:") for w in waits):
        waits.insert(0, "window:" + a.window)
    if not waits and not a.settle:
        p.error("nothing to wait for: give --window, --wait or --settle")
    if not a.twice and (a.fuzz is not None or a.max is not None):
        p.error("--fuzz and --max go with --twice")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    if a.twice:
        return twice(a, cmd, waits)
    status = take(a, cmd, waits, a.out)
    if not status:
        print(a.out)
    return status


def twice_options(p):
    """--twice, and how alike its two pictures must be: as `shoot` and
    `term --shoot` take them."""
    p.add_argument("--twice", action="store_true",
                   help="take the picture in two fresh sessions, and fail if they "
                        "differ, writing the difference beside OUT, as OUT-diff.png")
    p.add_argument("--fuzz", metavar="PCT",
                   help="with --twice: colors this close count as the same (0%%)")
    p.add_argument("--max", type=int, metavar="N",
                   help="with --twice: pixels allowed to differ (0)")


def twice(a, cmd, waits):
    """The picture taken twice, each in a session of its own, so what varies
    from one run to the next (a cursor's blink, the clock, the order things
    start in) shows; kept as OUT if the two are alike."""
    out = Path(a.out)
    fuzz = "0%" if a.fuzz is None else a.fuzz
    diff = out.with_name(out.stem + "-diff.png")
    # Beside OUT, so the one kept is moved there whole, on one filesystem.
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        pics = [Path(tmp) / "1.png", Path(tmp) / "2.png"]
        for pic in pics:
            if take(a, cmd, waits, pic, watch=pic == pics[0]):
                return 1
        try:
            n, box = x.compare(*pics, fuzz=fuzz, box=True)
        except ValueError as e:
            sys.stderr.write(f"shotbox: taken twice, {e}\n")
            return 1
        if n > (a.max or 0):
            x.compare(*pics, diff=diff, fuzz=fuzz)
            sys.stderr.write(f"shotbox: taken twice, {differing(n, box)}; see {diff}\n")
            return 1
        diff.unlink(missing_ok=True)
        os.replace(pics[0], out)
    print(a.out)
    return 0


def take(a, cmd, waits, out, watch=False):
    """shoot's picture, to `out`, in a session of its own. With `watch`,
    then watched for something blinking, a caret drawn on a timer of its
    own, say, which two runs would catch in the same phase: each starts
    the program at the same time. 2.5 seconds is two blinks of GTK's,
    the slowest common one; after the picture, so it's taken when the
    other run's is."""
    with session(a) as s:
        s.failed = a.failed or str(Path(a.out).with_name(Path(a.out).stem + "-failed.png"))
        log = a.log or str(s.scratch / "command.log")
        proc = s.spawn(cmd, log=log)
        try:
            for spec in waits:
                kind, _, arg = spec.partition(":")
                s.until(*s.test(kind, arg, window=a.window), a.timeout, alive=proc)
            time.sleep(a.settle)
            if proc.poll() is not None:
                s.fail(f"the program exited (status {proc.returncode}) "
                       "before the picture was taken")
            if a.window and not s.backend.find_window(s.env, a.window):
                s.fail(f"the window {a.window!r} went away")
            crop = a.crop
            if not crop and (s.scratch / "crop").exists():
                crop = (s.scratch / "crop").read_text().strip()
            s.capture(out, window=a.window, crop=crop, park=a.park, vision=a.vision)
            # Tiles of 4, so a caret of 7 pixels fills one; a look held
            # up to a second, three times, is one GTK holds for 800 ms.
            blink = watch and next((f for f in s.flicker(2.5, window=a.window, crop=crop, tile=4,
                                                         brief=1, times=3)[0]
                                    if f["kind"] == "flicker"), None)
            if blink:
                sys.stderr.write(f"shotbox: taken twice, but a cursor (or something) blinks at "
                                 f"{blink['box']} every {blink['every'] * 1000:.0f} ms, so the "
                                 "picture depends on when it's taken\n")
                return 1
        except SessionError as e:
            sys.stderr.write(f"shotbox: {e}\n--- {' '.join(cmd)} said ---\n{tail(log)}\n")
            return 1
    return 0


class Checked(Exception):
    """A step of a script, its arguments taken, stopped where it would start."""


# What a step of `shotbox play` runs on, in the thread it runs in, and
# whether it's only being checked.
playing = threading.local()


def inside(display=True):
    """The display of the session this runs in, or with `display` off, the
    session, which may have none."""
    screen = getattr(playing, "screen", None) or Screen()
    if not screen.env.get("SHOTBOX_SCRATCH"):
        raise SystemExit("shotbox: this only works inside `shotbox run` or `exec`, or "
                         "with `shotbox --session NAME`")
    if display and not (screen.env.get("DISPLAY") or screen.env.get("WAYLAND_DISPLAY")):
        raise SystemExit("shotbox: this session has no display")
    if getattr(playing, "checking", False):
        raise Checked
    return screen


def cmd_spawn(argv):
    p = argparse.ArgumentParser(prog="shotbox spawn",
                                description="Start a program in a lasting session "
                                            "(`shotbox start`), which stops it when "
                                            "the session stops.")
    p.add_argument("--session", required=True, metavar="NAME")
    p.add_argument("--log", metavar="FILE", help="its output goes here (else nowhere)")
    opts, cmd = split(argv)
    a = p.parse_args(opts)
    got = kept.ask(kept.find(a.session), {
        "op": "spawn", "cmd": command(cmd, p), "cwd": os.getcwd(),
        "log": a.log and str(Path(a.log).resolve())})
    print(got["pid"])
    return 0


def cmd_stop(argv):
    p = argparse.ArgumentParser(prog="shotbox stop",
                                description="Stop a lasting session, and everything in it.")
    p.add_argument("name", nargs="+", metavar="NAME")
    for name in p.parse_args(argv).name:
        scratch = kept.stop(name)
        if scratch:
            print(f"shotbox: kept {scratch}")
    return 0


def cmd_doctor(argv):
    argparse.ArgumentParser(prog="shotbox doctor",
                            description="What's here of what sessions, each of their "
                                        "options and `term` and `record` need, a line "
                                        "each; fails if what every session needs "
                                        "is missing.").parse_args(argv)
    missing = []
    for part, check in NEEDS.items():
        why = check()
        print(f"no    {part}: {why}" if why else f"ok    {part}")
        missing += [part] * bool(why)
    gone, tmp = kept.stale(), tempfile.gettempdir()
    print(f"no    scratch dirs: {len(gone)} stale in {tmp}, "
          f"{megabytes(sum(kept.size(d) for d, _ in gone))} "
          "(`shotbox sessions --stale` lists them)" if gone
          else f"ok    scratch dirs: none stale in {tmp}")
    return 1 if {"sessions", "X11"} & set(missing) else 0


def megabytes(n):
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} kB"


def cmd_sessions(argv):
    p = argparse.ArgumentParser(prog="shotbox sessions",
                                description="The lasting sessions there are; or what's "
                                            "stale: scratch dirs whose sessions have gone, "
                                            "and lasting sessions idle a day or more.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--stale", action="store_true",
                   help="list the stale, each with its size and what made it")
    g.add_argument("--clean", action="store_true",
                   help="remove stale scratch dirs, and what's left running of their "
                        "sessions; idle lasting sessions are listed, not stopped")
    p.add_argument("--unmarked", action="store_true",
                   help="with --clean, remove dirs an older shotbox made too, which may "
                        "be ones it kept with --keep")
    a = p.parse_args(argv)
    if a.unmarked and not a.clean:
        p.error("--unmarked goes with --clean")
    if not (a.stale or a.clean):
        for name in kept.names():
            print(name)
        return 0
    for d, made in kept.stale():
        what = ("made by an older shotbox" if made is None else
                f"{shlex.join([Path(made['command'][0]).name, *made['command'][1:]])}, "
                f"pid {made['pid']}, "
                f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(made['time']))}")
        took = megabytes(kept.size(d))
        if a.clean and made is None and not a.unmarked:
            print(f"left {d}  {took}  {what}, which may have kept it: --unmarked removes it")
        elif a.clean:
            mcp.reap(d)
            print(f"removed {d}  {took}  {what}")
        else:
            print(f"{d}  {took}  {what}")
    for name, since in kept.idle():
        print(f"{name}  a lasting session, idle {since / 3600:.0f} hours: `shotbox stop {name}`")
    return 0


def cmd_wait(argv):
    p = argparse.ArgumentParser(prog="shotbox wait",
                                description="Inside `shotbox run`: wait for a window, "
                                            "a port, a file, the ready file, the "
                                            "screen to stop changing, an "
                                            "announcement, a node in the accessible "
                                            "tree, the screen to look like a "
                                            "picture, or to change, or a line in a "
                                            "file. match and change say how long they "
                                            "took; line prints the line.")
    p.add_argument("kind", choices=("window", "port", "file", "ready", "stable", "announce",
                                    "node", "match", "change", "line"))
    p.add_argument("arg", nargs="?", default="",
                   help="the window's title (a regex; app=RE for its app id), the "
                        "port, the file; for "
                        "stable, how long it must hold still (0.5); for announce, "
                        "a regex found in what was said (needs --a11y); for node, a "
                        "regex found in a showing node's name or text (needs --a11y); "
                        "for match, "
                        "the picture (a PNG) to look like; for line, a regex found in "
                        "the line")
    p.add_argument("file", nargs="?", help="for line, the file it's written to")
    p.add_argument("--from-start", action="store_true",
                   help="for line: from the top of the file, not from the end it has "
                        "when the wait starts")
    p.add_argument("--window", metavar="RE",
                   help="for stable, node, match and change: watch this window rather "
                        "than the display")
    p.add_argument("--crop", metavar="WxH+X+Y",
                   help="for match and change: watch just this part of it")
    p.add_argument("--fuzz", metavar="PCT",
                   help="for match and change: colors this close count as the same (0%%)")
    p.add_argument("--max", type=int, metavar="N",
                   help="for match and change: pixels that may differ and still count "
                        "as the same (0)")
    p.add_argument("--ignore", action="append", default=[], metavar="WxH+X+Y",
                   help="for match and change: leave out this box: a clock, a cursor "
                        "(repeatable)")
    p.add_argument("--margin", type=int, default=0, metavar="PX",
                   help="for match, with --crop: the picture may be up to PX pixels "
                        "from the crop's place each way; say where it matched (0)")
    p.add_argument("--timeout", type=float, default=30)
    p.add_argument("--failed", metavar="FILE",
                   help="if it gives up, a picture of the screen goes here "
                        "($SHOTBOX_FAILED)")
    # Intermixed, so `line --from-start RE FILE` reads RE and FILE on
    # Python 3.12.3 too, whose parse_args settles both before the option.
    a = p.parse_intermixed_args(argv)
    if a.kind == "match" and not a.arg:
        p.error("match needs the picture to look like")
    if a.kind == "node" and not a.arg:
        p.error("node needs a regex to look for: an empty one matches any node")
    if (a.kind == "line") != bool(a.file):
        p.error("line, and only line, takes a FILE after its pattern")
    if a.from_start and a.kind != "line":
        p.error("--from-start goes with line")
    if a.kind == "change" and a.arg:
        p.error("change takes no ARG: it waits for a change from how it looks now")
    looks = a.kind in ("match", "change")
    if not looks and (a.crop or a.fuzz is not None or a.max is not None or a.ignore):
        p.error("--crop, --fuzz, --max and --ignore go with match and change")
    if a.margin and a.kind != "match":
        p.error("--margin goes with match")
    screen = inside(display=a.kind not in ("port", "file", "ready", "line"))
    screen.failed = a.failed or screen.failed
    if a.kind == "line":
        print(screen.wait_line(a.arg, a.file, a.from_start, a.timeout))
        return 0
    if a.kind == "node":
        screen.wait_node(a.arg, a.window, a.timeout)
        return 0
    if not looks:
        screen.until(*screen.test(a.kind, a.arg, window=a.window), a.timeout)
        return 0
    how = dict(window=a.window, crop=a.crop, fuzz="0%" if a.fuzz is None else a.fuzz,
               most=a.max or 0, timeout=a.timeout, ignore=a.ignore)
    if a.kind == "change":
        print(f"{screen.wait_change(**how):.3f}s")
    else:
        got = screen.wait_match(a.arg, margin=a.margin, **how)
        print(f"{got.seconds:.3f}s" + (f" at {got.x},{got.y}" if a.margin else ""))
    return 0


def cmd_capture(argv):
    p = argparse.ArgumentParser(prog="shotbox capture",
                                description="Inside `shotbox run`: screenshot the display "
                                            "or one window.")
    p.add_argument("out")
    p.add_argument("--window", metavar="RE")
    p.add_argument("--crop", metavar="WxH+X+Y")
    p.add_argument("--park", action="store_true",
                   help="move the pointer out of the way first, as `shotbox park`")
    vision_option(p)
    p.add_argument("--burst", type=int, metavar="N",
                   help="take N pictures, to OUT-1.png, OUT-2.png...: for motion")
    p.add_argument("--every", type=seconds, default=0, metavar="SECS",
                   help="with --burst: start one every SECS (default: as fast as they come)")
    p.add_argument("--sheet", metavar="FILE",
                   help="with --burst: all the pictures in rows of four here too")
    a = p.parse_args(argv)
    if a.burst is None:
        if a.every or a.sheet:
            p.error("--every and --sheet go with --burst")
        inside().capture(a.out, window=a.window, crop=a.crop, park=a.park, vision=a.vision)
        return 0
    taken = inside().burst(a.out, a.burst, every=a.every, window=a.window, crop=a.crop,
                           park=a.park, vision=a.vision, sheet=a.sheet)
    for path, at in taken:
        print(f"{path}  {at:.3f}s")
    return 0


def cmd_record(argv):
    p = argparse.ArgumentParser(prog="shotbox record",
                                description="Inside `shotbox run`: record a video of the "
                                            "display or one window, or with --audio, a "
                                            ".wav of what plays, for --seconds, till "
                                            "a line in a file says (--until-line), or "
                                            "until stopped (Ctrl-C, or kill, which ends "
                                            "the file properly). Needs ffmpeg, and "
                                            "on Wayland, wf-recorder.")
    p.add_argument("out", metavar="OUT", help="an .mp4, .webm, .gif or .wav")
    p.add_argument("--seconds", type=seconds, metavar="SECS",
                   help="how long, or with --until-line, the most it lasts")
    p.add_argument("--until-line", nargs=2, metavar=("RE", "FILE"),
                   help="stop once a line matching RE is written to FILE, as `wait line` "
                        "waits for one, and print it")
    p.add_argument("--tail", type=seconds, default=0, metavar="SECS",
                   help="with --until-line: record this long after the line (0)")
    p.add_argument("--window", metavar="RE",
                   help="where this window is when it starts: what's drawn over it is "
                        "recorded too, and it isn't followed if it moves")
    p.add_argument("--crop", metavar="WxH+X+Y", help="inside the display or the window")
    p.add_argument("--fps", type=int, choices=range(1, 61), metavar="N",
                   help="frames a second, 1 to 60 (30, or 15 for a GIF)")
    p.add_argument("--pointer", action="store_true",
                   help="draw the pointer too (X11 only: sway draws none)")
    p.add_argument("--park", action="store_true",
                   help="move the pointer out of the way first, as `shotbox park`")
    p.add_argument("--sound", action="store_true",
                   help="with --audio, what plays too, in the same file (AAC in an MP4, "
                        "Opus in a WebM), and say what `shotbox listen` would of it")
    p.add_argument("--strict", action="store_true",
                   help="with --sound, fail if `listen` would (the file is kept)")
    a = listening(p).parse_args(argv)
    if a.seconds == 0:
        p.error("--seconds is more than 0")
    if a.tail and not a.until_line:
        p.error("--tail goes with --until-line")
    if not a.sound and (a.strict or {a.gap, a.clipped, a.quiet} != {None}):
        p.error("--strict, --gap, --clipped and --quiet go with --sound")
    judgement(p, a)
    if Path(a.out).suffix.lower() not in (*x.VIDEO, ".gif", ".wav"):
        p.error(f"OUT is an .mp4, .webm, .gif or .wav, not {a.out}")
    if a.crop and not re.fullmatch(r"\d+x\d+\+\d+\+\d+", a.crop):
        p.error(f"--crop is WIDTHxHEIGHT+X+Y, not {a.crop}")
    start = time.monotonic()
    wav = Path(a.out).suffix.lower() == ".wav"
    screen = inside(display=not wav)
    out = screen.record(a.out, seconds=a.seconds, window=a.window, crop=a.crop, fps=a.fps,
                          pointer=a.pointer, park=a.park, sound=a.sound, gap=a.gap / 1000,
                          until=a.until_line, tail=a.tail)
    bad = out.problems(quiet=a.quiet, clipped=a.clipped)
    try:
        if out.line is not None:
            print(out.line)
        print(f"{out.path}  {time.monotonic() - start:.1f}s" + (
            "" if not a.until_line or out.line is not None else
            "  stopped before the line" if out.stopped else
            "  no line in its --seconds" if a.seconds and x.source_up(screen.env, wav, a.sound)
            else "  no line: its session went"),
            flush=True)
        if out.heard:
            print("\n".join(sound.describe(out.heard) + bad), flush=True)
    except OSError:     # stopped by its terminal closing, with nowhere to say so
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 1 if a.strict and bad else 0


def vision_option(p):
    p.add_argument("--vision", choices=sorted(x.VISION), metavar="KIND",
                   help="take it as someone with this color vision sees it: protanopia, "
                        "deuteranopia, tritanopia, or gray (none)")


def listening(p):
    """`p`, with what `listen` judges a sound by; `judgement` checks them."""
    p.add_argument("--gap", type=float, metavar="MS",
                   help="silence this long between sounds is a dropout: every channel at "
                        "exactly zero, as a player that falls behind leaves it (1)")
    p.add_argument("--clipped", type=int, metavar="N",
                   help="samples that may be at full scale (0)")
    p.add_argument("--quiet", type=float, metavar="DB",
                   help="a peak below this is silence (-60 dBFS)")
    return p


def judgement(p, a):
    """The options `listening` gave `p`, as parsed into `a`: checked, and
    those not given their defaults."""
    a.gap = 1 if a.gap is None else a.gap
    a.clipped = 0 if a.clipped is None else a.clipped
    a.quiet = -60 if a.quiet is None else a.quiet
    if not 0 < a.gap < math.inf:
        p.error("--gap is a number of milliseconds, more than 0")


def cmd_listen(argv):
    p = argparse.ArgumentParser(prog="shotbox listen",
                                description="Say how loud a recording of a session's "
                                            "sound is (`shotbox record OUT.wav`), and "
                                            "where it clipped or dropped out; fail if it "
                                            "did, or is silent.")
    p.add_argument("wav", metavar="IN.wav", help="a 16-bit PCM WAV")
    a = listening(p).parse_args(argv)
    judgement(p, a)
    try:
        heard = sound.hear(a.wav, gap=a.gap / 1000)
    except (OSError, EOFError, ValueError, wave.Error) as e:
        sys.stderr.write(f"shotbox: {a.wav}: {e}\n")
        return 2
    bad = sound.problems(heard, quiet=a.quiet, clipped=a.clipped)
    print("\n".join(sound.describe(heard) + bad))
    return 1 if bad else 0


def cmd_vision(argv):
    p = argparse.ArgumentParser(prog="shotbox vision",
                                description="Write a picture as someone with another "
                                            "kind of color vision sees it, to check "
                                            "that nothing is told apart by color alone.")
    p.add_argument("src", metavar="IN.png")
    p.add_argument("out", metavar="OUT.png")
    p.add_argument("--as", dest="kind", required=True, choices=sorted(x.VISION), metavar="KIND",
                   help="protanopia (no red cones), deuteranopia (no green), tritanopia "
                        "(no blue), or gray (no color)")
    a = p.parse_args(argv)
    if not Path(a.src).exists():
        sys.stderr.write(f"shotbox: no {a.src}\n")
        return 2
    x.vision(a.src, a.out, a.kind)
    print(a.out)
    return 0


def cmd_key(argv):
    p = argparse.ArgumentParser(prog="shotbox key",
                                description="Inside `shotbox run`: press and release each "
                                            "chord in turn, e.g. ctrl+comma, Return, "
                                            "alt+shift+Tab.")
    p.add_argument("chords", nargs="+", metavar="CHORD")
    held_options(p, "the chords", "them", where="")
    a = p.parse_args(argv)
    for c in a.chords:
        xtest.parse_chord(c)
    inside().key(*a.chords, hold=a.hold, state=a.state)
    return 0


def held_here(screen, a):
    """The screen, unless --down can't work on it: a Wayland virtual
    pointer's buttons go up when this command ends."""
    if a.state == "down" and screen.wayland:
        raise SessionError("on Wayland, what --down presses is let go when the command "
                           "ends: use --hold SECS instead (in the background, with `&`, "
                           "to hold it while doing something else)")
    return screen


def held_options(p, what, them, where=" (X11; on Wayland, use --hold)"):
    """--hold SECS, --down and --up, for keys and buttons; `where` --down
    works."""
    g = p.add_mutually_exclusive_group()
    g.add_argument("--hold", type=seconds, metavar="SECS",
                   help=f"press {what}, keep {them} down this long, then let go")
    g.add_argument("--down", dest="state", action="store_const", const="down",
                   help=f"press {what} and leave {them} down, until --up{where}")
    g.add_argument("--up", dest="state", action="store_const", const="up",
                   help=f"let go of {what}, pressed with --down")
    return g


def cmd_type(argv):
    p = argparse.ArgumentParser(prog="shotbox type",
                                description="Inside `shotbox run`: type text into whatever "
                                            "has the keyboard focus.")
    p.add_argument("text")
    a = p.parse_args(argv)
    inside().type(a.text)
    return 0


def pointer_command(name, what):
    def cmd(argv):
        p = argparse.ArgumentParser(prog=f"shotbox {name}",
                                    description=f"Inside `shotbox run`: {what}.")
        p.add_argument("x", type=int)
        p.add_argument("y", type=int)
        p.add_argument("--window", metavar="RE",
                       help="X and Y are inside this window rather than the screen")
        if name == "click":
            p.add_argument("--button", type=int, default=1, help="1 left, 2 middle, 3 right")
            g = held_options(p, "the button", "it")
            g.add_argument("--double", action="store_true", help="click twice")
        if name == "scroll":
            p.add_argument("dy", type=int, help="clicks down; up if negative")
            p.add_argument("dx", type=int, nargs="?", default=0,
                           help="clicks right; left if negative")
        a = p.parse_args(argv)
        if name == "click":
            held_here(inside(), a).click(a.x, a.y, window=a.window, button=a.button,
                                         double=a.double, hold=a.hold, state=a.state)
        elif name == "scroll":
            wheel(a.dy, a.dx)
            inside().scroll(a.x, a.y, a.dy, a.dx, window=a.window)
        else:
            inside().move(a.x, a.y, window=a.window)
        return 0
    return cmd


def cmd_drag(argv):
    p = argparse.ArgumentParser(prog="shotbox drag",
                                description="Inside `shotbox run`: press at X1 Y1, move to "
                                            "X2 Y2, release: a divider, a slider, a drop.")
    for n in ("x1", "y1", "x2", "y2"):
        p.add_argument(n, type=int)
    p.add_argument("--window", metavar="RE",
                   help="the points are inside this window rather than the screen")
    p.add_argument("--button", type=int, default=1)
    a = p.parse_args(argv)
    inside().drag(a.x1, a.y1, a.x2, a.y2, window=a.window, button=a.button)
    return 0


def cmd_park(argv):
    p = argparse.ArgumentParser(prog="shotbox park",
                                description="Inside `shotbox run`: move the pointer to the "
                                            "screen's bottom-right corner, off whatever it "
                                            "was hovering, and wait (up to --settle) for "
                                            "the repaint.")
    p.add_argument("--settle", type=float, default=2.0, metavar="SECS",
                   help="wait at most this long for the display to hold still (2)")
    a = p.parse_args(argv)
    inside().park(settle=a.settle)
    return 0


def cmd_latency(argv):
    p = argparse.ArgumentParser(prog="shotbox latency",
                                description="Inside `shotbox run`: how long what's drawn takes "
                                            "to follow the pointer. It's moved between two "
                                            "points, and each move timed from sending it to "
                                            "the first picture in which the --watch box "
                                            "changed; then the median and the 95th "
                                            "percentile.")
    p.add_argument("action", choices=["move"])
    p.add_argument("points", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
    p.add_argument("--watch", required=True, metavar="WxH+X+Y",
                   help="the box that shows the pointer's move, on the screen or in --window")
    p.add_argument("--window", metavar="RE", help="points and box inside this window")
    p.add_argument("--times", type=int, default=10, metavar="N", help="moves to time (10)")
    p.add_argument("--timeout", type=float, default=5, metavar="SECS",
                   help="give up on a move that changes nothing after this long (5)")
    p.add_argument("--settle", type=float, default=0.25, metavar="SECS",
                   help="before each move, the box holds still this long, or twice the "
                        "longest move yet, more than its latency (0.25)")
    a = p.parse_args(argv)
    if a.times < 1:
        p.error("--times is 1 or more")
    took = sorted(inside().latency(tuple(a.points[:2]), tuple(a.points[2:]), a.watch,
                                   window=a.window, times=a.times, timeout=a.timeout,
                                   settle=a.settle))
    median = statistics.median(took)
    # Below 20 moves, the 95th percentile is the slowest: said as that.
    high = (f"95th percentile {took[math.ceil(0.95 * len(took)) - 1] * 1000:.1f} ms"
            if len(took) >= 20 else f"slowest {took[-1] * 1000:.1f} ms")
    print(f"median {median * 1000:.1f} ms, {high}, of {len(took)} moves, "
          f"fastest {took[0] * 1000:.1f} ms")
    return 0


def cmd_flicker(argv):
    p = argparse.ArgumentParser(prog="shotbox flicker",
                                description="Inside `shotbox run`: watch the screen, or a "
                                            "window, for a part that keeps switching between "
                                            "two looks (flicker), or flashes another look "
                                            "once and comes back; fail (1) if one does, "
                                            "or can't watch (2). Boxes are in what's "
                                            "watched: the window, or the crop.")
    p.add_argument("--window", metavar="RE")
    p.add_argument("--crop", metavar="WxH+X+Y")
    p.add_argument("--seconds", type=float, default=2, metavar="SECS",
                   help="how long to watch (2)")
    p.add_argument("--brief", type=float, default=100, metavar="MS",
                   help="a look held no longer than this is brief (100): a caret's blink is "
                        "longer")
    p.add_argument("--times", type=int, default=8, metavar="N",
                   help="brief changes between two looks, in a row, that are flicker (8): "
                        "kept up that long, each no longer than --brief")
    p.add_argument("--fuzz", type=float, default=6, metavar="PCT",
                   help="how near two colors are to count as one (6): a tile's mean "
                        "color moving a quarter of this is a new look, and its pixels "
                        "within twice this, or half what's between two looks, the same "
                        "picture")
    p.add_argument("--tile", type=int, default=16, metavar="PX", help="tile size (16)")
    p.add_argument("--ignore", action="append", default=[], metavar="WxH+X+Y",
                   help="leave out tiles in this box: a video, a caret (repeatable)")
    p.add_argument("--save", metavar="DIR",
                   help="a sheet of each one's pictures here, made if it isn't: its box and "
                        "a tile round it, magnified, each labeled with its time")
    a = p.parse_args(argv)
    if not (a.seconds > 0 and a.brief > 0 and a.times >= 2 and a.tile >= 1 and a.fuzz >= 0):
        p.error("--seconds, --brief and --tile are more than 0, --times 2 or more, --fuzz 0 "
                "or more")
    try:
        found, taken, changed = inside().flicker(
            a.seconds, window=a.window, crop=a.crop, tile=a.tile, brief=a.brief / 1000,
            times=a.times, fuzz=a.fuzz, ignore=a.ignore, save=a.save)
    except SessionError as e:
        sys.stderr.write(f"shotbox: {e}\n")
        return 2
    if taken < 2 * a.seconds / (a.brief / 1000):
        sys.stderr.write(f"shotbox: {taken / a.seconds:.0f} pictures a second, fewer than two "
                         f"to {a.brief:g} ms: a look that brief may be missed, and motion taken "
                         "for a flash\n")
    if taken <= a.times:
        sys.stderr.write(f"shotbox: {taken} pictures in {a.seconds:g} s, too few to hold "
                         f"{a.times} changes: flicker can't be seen\n")
    for f in found:
        if "unsaved" in f:
            sys.stderr.write(f"shotbox: couldn't write {f['unsaved']}\n")
        shown = f", shown in {f['sheet']}" if "sheet" in f else ""
        if f["kind"] == "flicker":
            print(f"flicker: {f['box']}, {f['changes']} changes, every {f['every'] * 1000:.0f} "
                  f"ms, {f['looks'][0]} and {f['looks'][1]}, from {f['at']:.3f}s{shown}")
        else:
            print(f"flash: {f['box']}, {f['looks'][1]} for {f['every'] * 1000:.0f} ms in "
                  f"{f['looks'][0]}, at {f['at']:.3f}s{shown}")
    print(f"watched {a.seconds:g} s, {taken} pictures, {taken / a.seconds:.0f} a second, "
          f"{changed} changed")
    return 1 if found else 0


def cmd_focus(argv):
    p = argparse.ArgumentParser(prog="shotbox focus",
                                description="Inside `shotbox run`: give a window the "
                                            "keyboard, and raise it. On X11 there's no "
                                            "window manager to do it for a window that "
                                            "comes up after another, a dialog say.")
    p.add_argument("window", metavar="RE",
                   help="its title, a regex matched in full, or app=RE for its app id")
    a = p.parse_args(argv)
    inside().focus_window(a.window)
    return 0


def cmd_pad(argv):
    p = argparse.ArgumentParser(prog="shotbox pad",
                                description="Inside a session started with --gamepads: "
                                            "work a gamepad. What's left down stays down "
                                            "between commands, on X11 and Wayland alike.")
    p.add_argument("--pad", type=int, default=1, metavar="N", help="which pad (1)")
    sub = p.add_subparsers(dest="what", required=True)
    press = sub.add_parser("press", help="press buttons together: a b x y lb rb back start "
                                         "guide ls rs, and up down left right")
    press.add_argument("buttons", nargs="+", metavar="BUTTON")
    held_options(press, "the buttons", "them", where="")
    for name, args, what in (("stick", ("x", "y"), "push a stick to X Y, each -1 to 1, y down"),
                             ("trigger", ("value",), "pull a trigger, 0 to 1")):
        q = sub.add_parser(name, help=what)
        q.add_argument("which", choices=("left", "right"))
        for arg in args:
            q.add_argument(arg, type=float)
        q.add_argument("--hold", type=seconds, metavar="SECS",
                       help="for this long, then let go (else it stays there)")
    sub.add_parser("neutral", help="let go of everything: buttons up, sticks centered")
    a = p.parse_args(argv)
    if a.what == "press":
        try:
            pad.check_names(a.buttons)
        except pad.PadError as e:
            p.error(str(e))
    s = inside()
    if a.what == "press":
        s.pad_press(a.buttons, a.pad, hold=a.hold, state=a.state)
    elif a.what == "stick":
        s.pad_stick(a.which, a.x, a.y, a.pad, hold=a.hold)
    elif a.what == "trigger":
        s.pad_trigger(a.which, a.value, a.pad, hold=a.hold)
    else:
        s.pad_neutral(a.pad)
    return 0


def cmd_play(argv):
    p = argparse.ArgumentParser(prog="shotbox play",
                                description="Inside `shotbox run`: play a script, a step a "
                                            "line, all on one clock in this one process "
                                            "(but for those in the background). "
                                            "A step is an optional time, then a command "
                                            "for inside a session as it's given after "
                                            "`shotbox`. A time is seconds from the start, "
                                            "or +SECS after the step before ended; with "
                                            "none, right after it. A step ending in & "
                                            "runs on behind those after it, and the "
                                            "script waits for it at its end. Words are "
                                            "quoted as in the shell; ${NAME} is the "
                                            "session's variable; # starts a comment.")
    p.add_argument("script", metavar="SCRIPT", help="the file, or - to read it from stdin")
    p.add_argument("--verbose", action="store_true",
                   help="say each step on stderr as it starts, and when")
    a = p.parse_args(argv)
    stdin = a.script == "-"
    play(sys.stdin.read() if stdin else Path(a.script), inside(display=False), a.verbose,
         source="stdin" if stdin else None)
    return 0


# A part of a word: single-quoted, double-quoted, or neither.
PART = re.compile(r"""'([^']*)'|"((?:[^"\\]|\\.)*)"|((?:\\.|[^\s'"\\])+|\\)""")


def words_of(line, env):
    """A script line's words, each with whether any of it was quoted, split
    as the shell splits them: quotes, backslashes, `#` starting a comment
    at the start of a word, and ${NAME} from `env`, but not in single
    quotes. ValueError for a quote not closed, KeyError for a name not set."""
    def expand(escaped, text):
        return re.sub(escaped + r"|\$\{(\w+)\}",
                      lambda m: str(env[m[2]]) if m[1] is None else m[1], text)
    words, at = [], 0
    while True:
        at = len(line) - len(line[at:].lstrip())
        if at == len(line) or line[at] == "#":
            return words
        word, quoted = "", False
        while at < len(line) and not line[at].isspace():
            m = PART.match(line, at)
            if not m:
                raise ValueError(f"no closing quote: {line[at:]}")
            if m[1] is not None:
                word += m[1]
            elif m[2] is not None:
                word += expand(r'\\([\\"$`])', m[2])
            else:
                word += expand(r"\\(.)", m[3])
            quoted, at = quoted or m[3] is None or "\\" in m[3], m.end()
        words.append((word, quoted))


def play(script, screen, verbose=False, source=None, vars=None):
    """Play `script`, its text, or with a path, the file, on `screen`, with
    `vars` over the session's variables for ${NAME}. Each step's options are
    checked before the first starts; SessionError, naming the line (in
    `source`), if one is wrong or fails, once what runs in the background
    is stopped."""
    if isinstance(script, os.PathLike):
        source = str(script)
        try:
            script = Path(script).read_text()
        except OSError as e:
            raise SessionError(f"can't read {source}: {e.strerror}") from None
    # a11y isn't one: it starts before it can be stopped to be checked.
    played = [c for c in INSIDE if c not in ("a11y", "play")]
    env = {**screen.env, **(vars or {})}
    steps = []
    for n, line in enumerate(script.splitlines(), 1):
        where = f"{source}:{n}" if source else f"line {n}"
        try:
            words = words_of(line, env)
        except ValueError as e:
            raise SessionError(f"{where}: {e}") from None
        except KeyError as e:
            raise SessionError(f"{where}: ${{{e.args[0]}}} isn't set") from None
        at = (words.pop(0)[0] if words and not words[0][1]
              and re.fullmatch(r"\+?(\d+\.?\d*|\.\d+)", words[0][0]) else None)
        back = words[-1:] == [("&", False)]
        words = [w for w, _ in words[:len(words) - back]]
        if words and words[0] not in played:
            raise SessionError(f"{where}: {words[0]} isn't a step: {', '.join(played)}")
        if {"-h", "--help"} & set(words):
            raise SessionError(f"{where}: --help isn't a step")
        if back and not words:
            raise SessionError(f"{where}: & with nothing to run")
        if at or words:
            steps.append((where, at, words, back))

    def said(where, why):
        return f"{where}: {str(why).removeprefix('shotbox: ')}"

    playing.screen, playing.checking = screen, True
    try:
        for where, _, words, _ in steps:
            heard = io.StringIO()
            try:
                if words:
                    with contextlib.redirect_stderr(heard):
                        COMMANDS[words[0]](words[1:])
            except Checked:
                pass
            except SystemExit as e:
                raise SessionError(said(where, heard.getvalue().strip().rpartition("\n")[2]
                                        or e.code)) from None
            except (SessionError, xtest.XError) as e:
                raise SessionError(said(where, e)) from None
    finally:
        playing.checking = False

    errors, behind, woke = [], [], threading.Event()
    # This shotbox, wherever it is, run for a step in the background.
    here = str(Path(__file__).resolve().parent.parent)
    path = os.pathsep.join(filter(None, (here, screen.env.get("PYTHONPATH"))))
    again = "import sys; del sys.path[0]; from shotbox.__main__ import main; sys.exit(main())"

    def watch(where, proc):
        err = proc.communicate()[1].decode(errors="replace").strip()
        if proc.returncode and not stopping:
            errors.append(said(where, err.rpartition("\n")[2] or f"status {proc.returncode}"))
        woke.set()

    def stop():
        """Stop what runs in the background as SIGTERM stops a command (a
        recording finishes its file, a hold lets go), and wait while it does."""
        nonlocal stopping
        stopping = True
        for proc, _ in behind:
            if proc.poll() is None:
                proc.terminate()
        for _, t in behind:
            t.join()

    def until(due):
        """Wait till `due`, or a step in the background failing, if sooner."""
        while not errors and (left := due - time.monotonic()) > 0:
            woke.wait(left)
            woke.clear()

    start = last = time.monotonic()
    stopping = False
    try:
        for where, at, words, back in steps:
            if at:
                due = (last if at[0] == "+" else start) + float(at)
                if at[0] != "+" and time.monotonic() - due > 0.1:
                    sys.stderr.write(f"shotbox: {where}: {time.monotonic() - due:.2f}s late\n")
                until(due)
            if errors:
                break
            if verbose and words:
                sys.stderr.write(f"{time.monotonic() - start:.3f}s  {shlex.join(words)}"
                                 f"{' &' * back}\n")
            if back:
                # Its own process, in its own session: Ctrl-C at a terminal
                # is for this one, which stops it; and gone if this goes.
                proc = subprocess.Popen([sys.executable, "-c", again, *words],
                                        env={**screen.env, "PYTHONPATH": path, **(
                                            {"SHOTBOX_FAILED": str(screen.failed)}
                                            if screen.failed else {})},
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE, start_new_session=True,
                                        preexec_fn=x.tied(signal.SIGTERM))
                behind.append((proc, threading.Thread(target=watch, args=(where, proc))))
                behind[-1][1].start()
            elif words:
                failed = screen.failed      # a wait's --failed is its own
                try:
                    if COMMANDS[words[0]](words[1:]):
                        errors.append(f"{where}: {words[0]} failed")
                except (SessionError, xtest.XError) as e:
                    errors.append(said(where, e))
                except SystemExit as e:
                    errors.append(said(where, e.code))
                finally:
                    screen.failed = failed
            last = time.monotonic()
        while not errors and any(proc.returncode is None for proc, _ in behind):
            woke.wait()
            woke.clear()
    except KeyboardInterrupt:
        stop()
        raise
    finally:
        playing.screen = None
    stop()
    if errors:
        raise SessionError("; ".join(errors))


def per_name(values, name, default):
    """--fuzz and --max: the last NAME=VALUE for this picture, else the last
    plain VALUE, else the default."""
    named = [v.split("=", 1)[1] for v in values if v.split("=", 1)[0] == name and "=" in v]
    plain = [v for v in values if "=" not in v]
    return (named or plain or [default])[-1]


def differing(n, box):
    """How many pixels differ, and the box they're in: "210 pixels differ,
    in 12x18 at 30,212", which places them without a look at the diff."""
    return f"{n} pixels differ" + (f", in {box[0]}x{box[1]} at {box[2]},{box[3]}" if box else "")


def shifted(at):
    """How far a picture moved to match, if it did: ", moved 2,-1"."""
    return f", moved {at[0]},{at[1]}" if any(at) else ""


def cmd_compare(argv):
    p = argparse.ArgumentParser(prog="shotbox compare",
                                description="Count the pixels that differ between two "
                                            "images; fail if more than --max do. Given "
                                            "two directories, compare each picture in "
                                            "the first (or each NAME) with the one of "
                                            "the same name in the second.")
    p.add_argument("a", help="an image, or a directory of the pictures as they should be")
    p.add_argument("b", help="an image, or a directory of fresh pictures")
    p.add_argument("names", nargs="*", metavar="NAME",
                   help="with directories: just these (NAME or NAME.png)")
    p.add_argument("--diff", metavar="OUT",
                   help="write the differences, marked in red; with directories, "
                        "OUT is a directory, and each picture that fails gets NAME-diff.png")
    p.add_argument("--fuzz", action="append", default=[], metavar="[NAME=]PCT",
                   help="colors this close count as the same (0%%); NAME=PCT for one "
                        "picture (repeatable)")
    p.add_argument("--max", action="append", default=[], metavar="[NAME=]N",
                   help="pixels allowed to differ (0); NAME=N for one picture (repeatable)")
    p.add_argument("--ignore", action="append", default=[], metavar="[NAME=]WxH+X+Y",
                   help="leave out this box: a clock, a cursor; NAME=WxH+X+Y for one "
                        "picture (repeatable)")
    p.add_argument("--margin", action="append", default=[], metavar="[NAME=]PX",
                   help="B may have moved up to PX pixels each way: compare it where it "
                        "matches best, but for a band PX wide round the edge (0); NAME=PX "
                        "for one picture (repeatable)")
    a = p.parse_args(argv)
    for g in a.ignore:
        if not x.box_of(g.split("=", 1)[-1]):
            p.error(f"--ignore is WIDTHxHEIGHT+X+Y, at least 1x1, not {g}")
    for m in a.margin:
        if not m.split("=", 1)[-1].isdigit():
            p.error(f"--margin is a number of pixels, not {m}")

    def fuzz(name):
        return per_name(a.fuzz, name, "0%")

    def ignore(name):
        return [x.box_of(v.split("=", 1)[-1]) for v in a.ignore
                if "=" not in v or v.split("=", 1)[0] == name]

    def most(name):
        return int(per_name(a.max, name, 0))

    def compare(ref, new, name, diff=None, at=None):
        """compare's count and box where `new` matches best within the
        margin, and how far it moved to (`at`, if that's known)."""
        how = dict(fuzz=fuzz(name), ignore=ignore(name),
                   margin=int(per_name(a.margin, name, 0)))
        if at is None:
            at = x.moved(ref, new, most=most(name), **how) if how["margin"] else (0, 0)
        return *x.compare(ref, new, diff=diff, box=True, at=at, **how), at

    dirs = Path(a.a).is_dir(), Path(a.b).is_dir()
    if dirs[0] != dirs[1]:
        p.error("compare two images, or two directories")
    if not dirs[0]:
        if a.names:
            p.error("NAMEs go with two directories")
        missing = [f for f in (a.a, a.b) if not Path(f).exists()]
        if missing:
            sys.stderr.write(f"shotbox: no {' or '.join(missing)}\n")
            return 2
        try:
            n, box, at = compare(a.a, a.b, None, a.diff)
        except ValueError as e:
            sys.stderr.write(f"shotbox: {e}\n")
            return 2
        print(differing(n, box) + shifted(at))
        return 0 if n <= most(None) else 1

    names = [n.removesuffix(".png") for n in a.names] or sorted(
        f.stem for f in Path(a.a).glob("*.png"))
    if not names:
        sys.stderr.write(f"shotbox: no pictures in {a.a}\n")
        return 2
    if a.diff:
        Path(a.diff).mkdir(parents=True, exist_ok=True)
    bad = []
    for name in names:
        ref, new = Path(a.a) / f"{name}.png", Path(a.b) / f"{name}.png"
        missing = [str(f) for f in (ref, new) if not f.exists()]
        if missing:
            print(f"{name}: no {' or '.join(missing)}")
            bad.append(name)
            continue
        diff = Path(a.diff) / f"{name}-diff.png" if a.diff else None
        try:
            n, box, at = compare(ref, new, name)
        except ValueError as e:
            print(f"{name}: {e}")
            bad.append(name)
            continue
        if n > most(name):
            if diff:
                compare(ref, new, name, diff, at)
            print(f"{name}: {differing(n, box)}{shifted(at)}" + (f"; see {diff}" if diff else ""))
            bad.append(name)
        else:
            print(f"{name}: {'the same' if n == 0 else f'{differing(n, box)}; allowed'}"
                  + shifted(at))
    if bad:
        print(f"differ: {', '.join(bad)}")
        return 1
    return 0


COMMANDS = {"run": cmd_run, "exec": cmd_exec, "shoot": cmd_shoot, "term": term.main, "wait": cmd_wait,
            "capture": cmd_capture, "record": cmd_record, "compare": cmd_compare,
            "vision": cmd_vision, "listen": cmd_listen, "key": cmd_key,
            "type": cmd_type, "click": pointer_command("click", "move the pointer and click"),
            "move": pointer_command("move", "move the pointer, for a hover"),
            "scroll": pointer_command("scroll", "turn the mouse wheel there"),
            "drag": cmd_drag, "park": cmd_park, "focus": cmd_focus, "latency": cmd_latency,
            "flicker": cmd_flicker, "pad": cmd_pad, "play": cmd_play,
            "a11y": a11y.main,
            "mcp": mcp.main,
            "mutate": cmd_mutate,
            "start": kept.main, "spawn": cmd_spawn, "stop": cmd_stop,
            "sessions": cmd_sessions, "doctor": cmd_doctor}

# What --session puts inside a lasting session.
INSIDE = ("wait", "capture", "record", "key", "type", "click", "move", "scroll", "drag", "park",
          "focus", "pad", "a11y", "latency", "flicker", "play")


def main(argv=None):
    if argv is None:
        off_the_clock()
    argv = sys.argv[1:] if argv is None else argv
    session = None
    if argv[:1] == ["--session"]:
        if len(argv) < 2:
            sys.stderr.write("shotbox: --session needs a session's name\n")
            return 2
        session, argv = argv[1], argv[2:]
    elif argv and argv[0].startswith("--session="):
        session, argv = argv[0].split("=", 1)[1], argv[1:]
    if argv[:1] == ["--version"]:
        print(f"shotbox {__version__}")
        return 0
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in COMMANDS:
        sys.stdout.write(__doc__.lstrip())
        return 0 if not argv or argv[0] in ("-h", "--help") else 2
    try:
        if argv[0] in ("key", "click", "pad", "record", "play"):
            # Stopped mid-hold (by kill, timeout(1) or a closed terminal),
            # let go on the way out; a recording finishes its file.
            for sig in (signal.SIGTERM, signal.SIGHUP):
                signal.signal(sig, stop_once)
        if session:
            if argv[0] not in INSIDE:
                raise SessionError(f"--session is for the commands used inside a "
                                   f"session ({', '.join(INSIDE)}), not {argv[0]}")
            kept.enter(session)
            unclocked(os.environ)   # its env has the preload, for its programs
        return COMMANDS[argv[0]](argv[1:])
    except (SessionError, xtest.XError) as e:
        sys.stderr.write(f"shotbox: {e}\n")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
