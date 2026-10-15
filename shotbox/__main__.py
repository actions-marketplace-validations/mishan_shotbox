"""shotbox: reproducible screenshots of real programs, in a sealed session.

    shotbox shoot OUT.png [options] -- COMMAND...   run it, wait, take the picture
    shotbox run [options] -- COMMAND...             run it in a sealed session
    shotbox start [--name NAME] [options]           a session that lasts, until stopped
    shotbox spawn --session NAME -- COMMAND...      run a program in a lasting session
    shotbox stop NAME | shotbox sessions            stop one; list them
    shotbox term [options] -- COMMAND...            a terminal to take pictures of
    shotbox wait window|port|file|ready|stable|announce ARG
                                                    (inside a session) wait for it
    shotbox capture OUT.png [--window RE] [--burst N]  (inside a session) take a picture
    shotbox record OUT.mp4 [--seconds S] [--window RE]  (inside a session) take a video
    shotbox key CHORD...                            (inside a session) press keys
    shotbox type TEXT                               (inside a session) type text
    shotbox click X Y [--window RE]                 (inside a session) click there
    shotbox move X Y [--window RE]                  (inside a session) point there
    shotbox drag X1 Y1 X2 Y2 [--window RE]          (inside a session) drag
    shotbox park                                    (inside a session) pointer out of the way
    shotbox focus RE                                (inside a session) give a window the keyboard
    shotbox pad press|stick|trigger|neutral ...     (inside a session) work a gamepad
    shotbox a11y tree|lint|tabwalk|click|contrast   (inside a session) the accessible tree
    shotbox mcp [--allow FILE]                      sessions for an agent, over MCP (stdio)
    shotbox vision IN.png OUT.png --as KIND         a picture as colorblind eyes see it
    shotbox compare A.png B.png [--diff D.png]      how many pixels differ
    shotbox compare REFDIR NEWDIR [NAME...]         the same, picture by picture

`shotbox --session NAME COMMAND ...` runs any of the commands for inside a
session in a lasting one. `shotbox COMMAND --help` for each one's options.
"""

import argparse
import math
import os
import signal
import sys
import time
from pathlib import Path

from . import a11y, kept, mcp, term, x, xtest
from .screen import Screen, SessionError
from .session import Session


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


def size(s):
    w, h = s.lower().split("x")
    return int(w), int(h)


def session_options(p):
    g = p.add_argument_group("the session")
    g.add_argument("--screen", type=size, default=(1280, 800), metavar="WxH",
                   help="the display's size (1280x800)")
    g.add_argument("--desktop", action="store_true",
                   help="real GSettings (dconf) and the system's D-Bus services, "
                        "for desktop shells; the default is in-memory settings "
                        "and nothing activatable")
    g.add_argument("--system-bus", action="store_true", help="a stand-in system bus too")
    g.add_argument("--seed", metavar="DIR", help="start the scratch home as a copy of DIR")
    g.add_argument("--env", action="append", default=[], metavar="NAME=VALUE",
                   help="set a variable inside (repeatable)")
    g.add_argument("--pass", dest="passthrough", action="append", default=[], metavar="NAME",
                   help="let a variable through from outside (repeatable)")
    g.add_argument("--keep", action="store_true", help="keep the scratch dir, and say where")
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
    g.add_argument("--audio", action="store_true",
                   help="a PipeWire of the session's own, pulse protocol too, whose "
                        "one sink plays to nowhere")
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
    return Session(size=a.screen, desktop=a.desktop, system_bus=a.system_bus,
                   seed=a.seed, keep=a.keep, env=env, passthrough=a.passthrough,
                   wayland=a.wayland, xwayland=a.xwayland, a11y=a.a11y,
                   announce_log=a.announce_log, dark=a.dark,
                   high_contrast=a.high_contrast, text_scale=a.text_scale,
                   audio=a.audio, gamepads=a.gamepads)


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
    with make_session(a) as s:
        return s.run(cmd)


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
    session_options(p)
    opts, cmd = split(argv)
    a = p.parse_args(opts)
    cmd = command(cmd, p)
    waits = list(a.wait)
    if a.window and not any(w.startswith("window:") for w in waits):
        waits.insert(0, "window:" + a.window)
    if not waits and not a.settle:
        p.error("nothing to wait for: give --window, --wait or --settle")

    with make_session(a) as s:
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
            s.capture(a.out, window=a.window, crop=crop, park=a.park, vision=a.vision)
        except SessionError as e:
            sys.stderr.write(f"shotbox: {e}\n--- {' '.join(cmd)} said ---\n{tail(log)}\n")
            return 1
    print(a.out)
    return 0


def inside():
    """The display of the session this runs in."""
    if not os.environ.get("SHOTBOX_SCRATCH"):
        raise SystemExit("shotbox: this only works inside `shotbox run`, or with "
                         "`shotbox --session NAME`")
    return Screen()


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


def cmd_sessions(argv):
    argparse.ArgumentParser(prog="shotbox sessions",
                            description="The lasting sessions there are.").parse_args(argv)
    for name in kept.names():
        print(name)
    return 0


def cmd_wait(argv):
    p = argparse.ArgumentParser(prog="shotbox wait",
                                description="Inside `shotbox run`: wait for a window, "
                                            "a port, a file, the ready file, the "
                                            "screen to stop changing, or an "
                                            "announcement.")
    p.add_argument("kind", choices=("window", "port", "file", "ready", "stable", "announce"))
    p.add_argument("arg", nargs="?", default="",
                   help="the window's title (a regex; app=RE for its app id), the "
                        "port, the file; for "
                        "stable, how long it must hold still (0.5); for announce, "
                        "a regex found in what was said (needs --a11y)")
    p.add_argument("--window", metavar="RE",
                   help="for stable: watch this window rather than the display")
    p.add_argument("--timeout", type=float, default=30)
    p.add_argument("--failed", metavar="FILE",
                   help="if it gives up, a picture of the screen goes here "
                        "($SHOTBOX_FAILED)")
    a = p.parse_args(argv)
    screen = inside()
    screen.failed = a.failed or screen.failed
    screen.until(*screen.test(a.kind, a.arg, window=a.window), a.timeout)
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
                                            "display or one window, for --seconds, or "
                                            "until stopped (Ctrl-C, or kill, which ends "
                                            "the file properly). X11 sessions; needs ffmpeg.")
    p.add_argument("out", metavar="OUT", help="an .mp4, .webm or .gif")
    p.add_argument("--seconds", type=seconds, metavar="SECS")
    p.add_argument("--window", metavar="RE")
    p.add_argument("--crop", metavar="WxH+X+Y", help="inside the display or the window")
    p.add_argument("--fps", type=int, default=30, choices=range(1, 61), metavar="N",
                   help="frames a second, 1 to 60 (30)")
    p.add_argument("--pointer", action="store_true", help="draw the pointer too")
    p.add_argument("--park", action="store_true",
                   help="move the pointer out of the way first, as `shotbox park`")
    a = p.parse_args(argv)
    if a.seconds == 0:
        p.error("--seconds is more than 0")
    start = time.monotonic()
    out = inside().record(a.out, seconds=a.seconds, window=a.window, crop=a.crop, fps=a.fps,
                          pointer=a.pointer, park=a.park)
    print(f"{out}  {time.monotonic() - start:.1f}s")
    return 0


def vision_option(p):
    p.add_argument("--vision", choices=sorted(x.VISION), metavar="KIND",
                   help="take it as someone with this color vision sees it: protanopia, "
                        "deuteranopia, tritanopia, or gray (none)")


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
    held_options(p, "the chords", "them")
    a = p.parse_args(argv)
    held_here(inside(), a).key(*a.chords, hold=a.hold, state=a.state)
    return 0


def held_here(screen, a):
    """The screen, unless --down can't work on it: a Wayland virtual
    keyboard's keys and pointer's buttons go up when this command ends."""
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
        a = p.parse_args(argv)
        if name == "click":
            held_here(inside(), a).click(a.x, a.y, window=a.window, button=a.button,
                                         double=a.double, hold=a.hold, state=a.state)
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


def per_name(values, name, default):
    """--fuzz and --max: the last NAME=VALUE for this picture, else the last
    plain VALUE, else the default."""
    named = [v.split("=", 1)[1] for v in values if v.split("=", 1)[0] == name and "=" in v]
    plain = [v for v in values if "=" not in v]
    return (named or plain or [default])[-1]


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
    a = p.parse_args(argv)
    def fuzz(name):
        return per_name(a.fuzz, name, "0%")

    def most(name):
        return int(per_name(a.max, name, 0))

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
            n = x.compare(a.a, a.b, diff=a.diff, fuzz=fuzz(None))
        except ValueError as e:
            sys.stderr.write(f"shotbox: {e}\n")
            return 2
        print(f"{n} pixels differ")
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
            n = x.compare(ref, new, fuzz=fuzz(name))
        except ValueError as e:
            print(f"{name}: {e}")
            bad.append(name)
            continue
        if n > most(name):
            if diff:
                x.compare(ref, new, diff=diff, fuzz=fuzz(name))
            print(f"{name}: {n} pixels differ" + (f"; see {diff}" if diff else ""))
            bad.append(name)
        else:
            print(f"{name}: {'the same' if n == 0 else f'{n} pixels differ, allowed'}")
    if bad:
        print(f"differ: {', '.join(bad)}")
        return 1
    return 0


COMMANDS = {"run": cmd_run, "shoot": cmd_shoot, "term": term.main, "wait": cmd_wait,
            "capture": cmd_capture, "record": cmd_record, "compare": cmd_compare, "vision": cmd_vision, "key": cmd_key,
            "type": cmd_type, "click": pointer_command("click", "move the pointer and click"),
            "move": pointer_command("move", "move the pointer, for a hover"),
            "drag": cmd_drag, "park": cmd_park, "focus": cmd_focus, "pad": cmd_pad,
            "a11y": a11y.main,
            "mcp": mcp.main,
            "start": kept.main, "spawn": cmd_spawn, "stop": cmd_stop,
            "sessions": cmd_sessions}

# What --session puts inside a lasting session.
INSIDE = ("wait", "capture", "record", "key", "type", "click", "move", "drag", "park", "focus", "pad",
          "a11y")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    session = None
    if argv[:1] == ["--session"]:
        if len(argv) < 2:
            sys.stderr.write("shotbox: --session needs a session's name\n")
            return 2
        session, argv = argv[1], argv[2:]
    elif argv and argv[0].startswith("--session="):
        session, argv = argv[0].split("=", 1)[1], argv[1:]
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in COMMANDS:
        sys.stdout.write(__doc__.lstrip())
        return 0 if not argv or argv[0] in ("-h", "--help") else 2
    try:
        if argv[0] in ("key", "click", "pad", "record"):
            # Stopped mid-hold (by kill, timeout(1) or a closed terminal),
            # let go on the way out; a recording finishes its file.
            for sig in (signal.SIGTERM, signal.SIGHUP):
                signal.signal(sig, stop_once)
        if session:
            if argv[0] not in INSIDE:
                raise SessionError(f"--session is for the commands used inside a "
                                   f"session ({', '.join(INSIDE)}), not {argv[0]}")
            kept.enter(session)
        return COMMANDS[argv[0]](argv[1:])
    except (SessionError, xtest.XError) as e:
        sys.stderr.write(f"shotbox: {e}\n")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
