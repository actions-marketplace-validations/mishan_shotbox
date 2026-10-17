"""shotbox mcp: sealed sessions for an agent, over the Model Context
Protocol on stdin and stdout.

    shotbox mcp --allow PROGRAMS

An agent starts a session, starts programs in it from PROGRAMS, a list
of what it may run, by name, and then looks (the accessible tree, a
screenshot), acts (a click by a node's name, keys, text), waits, and
checks (lint, contrast, the Tab order) until it stops the session. The
sessions are this process's: they all stop when it does, which is when
the client goes.

JSON-RPC by hand, the few methods MCP's tools need, as liverykit does it:
the SDK would be shotbox's first Python dependency, for four methods.
The accessible tree is read in a `shotbox a11y` process for each call,
as the Python API reads it: libatspi keeps the first bus it finds, and
each session has its own.

Every session lives in a directory of the server's, and a watchdog
(`shotbox mcp --watch DIR`, in a process session of its own) holds a
pipe from the server: however the server ends, the pipe closes, and the
watchdog kills every process it knows for a session's by its environment
(see MARKS), and removes it.
Clients stop a server faster than a session stops (Claude Code: SIGINT,
SIGTERM 100ms on, SIGKILL 400ms after that), so a signal ends the server
at once and leaves the sessions to the watchdog.
"""

import argparse
import base64
import itertools
import json
import math
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import __version__, sound, usage
from .a11y import Node, walk_lines
from .screen import _CANCEL, SessionError, wheel
from .session import Session
from .sound import describe
from .x import VISION, ended, recording, sheet, stop
from .xtest import XError

# Newest first: a client asking for one of these gets it; any other, the newest.
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

# Given to the agent as it connects: how the tools go together, which
# each tool's own description can't say.
INSTRUCTIONS = """\
shotbox runs graphical programs in sealed sessions of their own, X11 or Wayland, \
where you can look at them, drive them and check them; nothing in one is meant to \
reach the person's desktop or speakers (start_session's answer says when it can't), \
and with net: loopback, not the network either.

Start with start_session (with audio, for sound), then start a program from the \
person's allow list with spawn_program (start_session's answer lists them) and wait_for \
its window. Read get_accessible_tree before clicking: \
it says what's on screen and what each thing is called, in far fewer tokens than \
a screenshot, and click_element and scroll_wheel take a name. Use take_screenshot only \
for what pixels say (colors, layout); record_video for motion and sound. After acting, \
wait_for stable rather than sleeping. check_accessible_names, check_contrast, \
check_focus_order, list_announcements and record_audio check what a person would \
meet. stop_session when done; every session stops with the server."""


class Refused(Exception):
    """A call that can't be done as asked, said as a sentence the agent can
    act on."""


# --- what it may run ------------------------------------------------------------


def uncomment(line):
    """`line` up to a `#` that starts a word outside quotes, as a shell
    reads a comment: `--accent=#3584e4`, `'#'` and `\\#` are no comment. A
    backslash escapes the next character but inside single quotes."""
    quote, starts, i = None, True, 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and quote != "'":
            i, starts = i + 2, False
            continue
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and starts:
            return line[:i]
        starts = not quote and ch.isspace()
        i += 1
    return line


def programs(path):
    """The allow file: a program a line, `NAME = COMMAND ARGS...`, split as
    a shell would but run without one, `#` for comments. A command with a
    slash in it that isn't absolute is relative to the file, and every
    program starts in the file's directory."""
    out = {}
    if not path:
        return out, None
    path = Path(path).resolve()
    for n, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, eq, command = line.partition("=")
        try:
            name, words = name.strip(), shlex.split(uncomment(command))
        except ValueError as e:
            raise SessionError(f"{path}:{n}: {e}: {line}")
        if not eq or not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or not words:
            raise SessionError(f"{path}:{n}: not NAME = COMMAND: {line}")
        if "/" in words[0] and not os.path.isabs(words[0]):
            words[0] = str(path.parent / words[0])
        out[name] = words
    return out, path.parent


# --- arguments as clients send them ---------------------------------------------
#
# Clients don't pass arguments through as typed: "14" can arrive as 14, and
# "Tab, Tab" as a list. Each is taken however it comes.


def text(v):
    return None if v is None else str(v)


def number(v, what, least=None, most=None):
    """A number, or None for none; refused if it isn't one, and kept between
    `least` and `most`: a timeout of "nan" never runs out."""
    if v is None or v == "":
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        n = math.nan
    if not math.isfinite(n):
        raise Refused(f"{what} is a number, not {v!r}")
    if least is not None:
        n = max(least, n)
    return n if most is None else min(most, n)


def program_pid(proc):
    """The program's pid, not that of the sandbox it's in."""
    return getattr(proc, "program_pid", proc.pid)


def within(v, what, least, most, whole=False):
    """A number between `least` and `most` (and a whole one if `whole`), or
    None for none; refused otherwise, rather than moved into range."""
    n = number(v, what)
    if n is not None and (not least <= n <= most or (whole and n != int(n))):
        kind = "a whole number from" if whole else "from"
        raise Refused(f"{what} is {kind} {least:g} to {most:g}, not {n:g}")
    return int(n) if whole and n is not None else n


def held(hold, state):
    """press_keys' and click_element's hold (seconds) and state (down or up). A hold
    holds up the server, so it's kept short."""
    hold = number(hold, "hold")
    if hold is not None and not 0 <= hold <= 60:
        raise Refused(f"hold is 0 to 60 seconds, not {hold:g}")
    state = text(state) or None
    if state not in (None, "down", "up"):
        raise Refused(f"state is down or up, not {state!r}")
    if hold is not None and state:
        raise Refused("hold or state, not both")
    return hold, state


def flag(v):
    return v in (True, 1) or str(v).lower() in ("true", "yes", "1")


def words(v):
    """A list, or text split at commas and spaces: `"Tab Tab"`, `["Tab", "Tab"]`."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(w) for w in v]
    return [w for w in re.split(r"[,\s]+", str(v)) if w]


# --- the tools ------------------------------------------------------------------


def tree_text(nodes, bounds=False):
    return "\n".join("  " * n.depth + n.line(bounds) for n in nodes) or "(no accessible windows)"


def problems(nodes):
    """check_accessible_names' and check_contrast's answer: a line a node, as the tree has it, and
    what's wrong."""
    return "\n".join(f"{n.line(bounds=True)}: {n.problem}" for n in nodes) or "No problems."


class Server:
    def __init__(self, allowed=None, home=None):
        self.allowed, self.home = allowed or {}, home
        self.sessions = {}
        self.spawned = {}   # session name -> [(program, process, log)]
        self.sounds = {}    # session name -> (its recording's keeper, its file)

    # --- sessions ------------------------------------------------------------

    def session(self, name):
        name = str(name)
        if name in self.sessions:
            return self.sessions[name]
        there = ", ".join(self.sessions) or "none: start one with start_session"
        raise Refused(f"there's no session called {name!r}. Sessions: {there}")

    def start_session(self, name=None, a11y=True, wayland=False, xwayland=False,
                      dark=False, high_contrast=False, text_scale=None, audio=False,
                      screen="1280x800", gamepads=0, net=None, usage=False, identity=None,
                      clock=None, tz=None):
        name = text(name) or next(f"s{n}" for n in range(1, 10000)
                                  if f"s{n}" not in self.sessions)
        text_scale = number(text_scale, "text_scale")
        if text_scale is not None and text_scale < 0.1:
            raise Refused(f"text_scale is at least 0.1, not {text_scale:g}")
        if name in self.sessions:
            raise Refused(f"there's a session called {name!r} already")
        m = re.fullmatch(r"(\d+)x(\d+)", str(screen))
        if not m:
            raise Refused(f"screen is WIDTHxHEIGHT, like 1280x800, not {screen!r}")
        s = Session(size=(int(m[1]), int(m[2])), a11y=flag(a11y), wayland=flag(wayland),
                    xwayland=flag(xwayland), dark=flag(dark),
                    high_contrast=flag(high_contrast),
                    text_scale=text_scale, audio=flag(audio),
                    gamepads=within(gamepads, "gamepads", 0, 4, whole=True) or 0,
                    net=text(net), usage=flag(usage), identity=text(identity),
                    clock=text(clock), tz=text(tz))
        s.__enter__()
        self.sessions[name], self.spawned[name] = s, []
        kind = "Wayland (sway)" if s.use_wayland else "X11 (Xvfb)"
        progs = ", ".join(self.allowed) or "none (shotbox mcp was given no --allow file)"
        return (f"Session {name!r} is up: {kind}, {m[1]}x{m[2]}"
                + (", with an accessibility bus" if s.a11y else ", no accessibility bus")
                + (f", {s.gamepads} gamepad{'s' * (s.gamepads > 1)}" if s.gamepads else "")
                + (", a network of its own, loopback only" if s.net else "")
                + (", its programs running as {}@{}".format(*s.identity) if s.identity else "")
                + f". Programs it can spawn: {progs}."
                + (f" Note: {s.unsealed}." if s.unsealed else ""))

    def stop_session(self, session):
        session = str(session)
        s = self.session(session)
        try:
            s.__exit__(None, None, None)
        finally:
            del self.sessions[session], self.spawned[session]
            self.sounds.pop(session, None)  # its keeper ends with the sound server
        used = f" It {usage.line(s.used)}." if s.used else ""
        return f"Session {session!r} stopped, and everything in it.{used}"

    def spawn_program(self, session, program):
        session = str(session)
        s = self.session(session)
        if program not in self.allowed:
            listed = ", ".join(self.allowed) or "none"
            raise Refused(f"{program!r} isn't one of the programs shotbox mcp was allowed "
                          f"to run ({listed}). Only the person running it can add one, to "
                          "its --allow file.")
        n = len(self.spawned[session]) + 1
        log = s.scratch / f"{program}-{n}.log"
        proc = s.spawn(self.allowed[program], log=log, cwd=self.home,
                       stdin=subprocess.DEVNULL)
        self.spawned[session].append((program, proc, log))
        return f"Started {program} (pid {program_pid(proc)}) in {session!r}."

    def get_program_output(self, session, program, lines=40):
        session = str(session)
        self.session(session)
        runs = [r for r in self.spawned[session] if r[0] == program]
        if not runs:
            raise Refused(f"{program!r} hasn't been spawned in {session!r}")
        _, proc, log = runs[-1]
        status = ("still running" if proc.poll() is None
                  else f"exited with status {proc.returncode}")
        tail = log.read_text(errors="replace").splitlines()[
            -int(number(lines, "lines", 1, 2000) or 40):]
        return f"{program} (pid {program_pid(proc)}) is {status}. Its output:\n" + (
            "\n".join(tail) or "(nothing)")

    # --- looking -------------------------------------------------------------

    def take_screenshot(self, session, window=None, crop=None, park=False, vision=None,
                        burst=None, every=None):
        s = self.session(session)
        out = s.scratch / "screenshot.png"
        vision = text(vision) or None
        if vision and vision not in VISION:
            raise Refused(f"vision is one of {', '.join(sorted(VISION))}, not {vision!r}")
        burst = number(burst, "burst")
        if burst is not None and not (burst == int(burst) and 2 <= burst <= 12):
            raise Refused(f"burst is 2 to 12 pictures, not {burst:g}")
        every = number(every, "every")
        if every is not None and not 0 <= every <= 5:
            raise Refused(f"every is 0 to 5 seconds, not {every:g}")
        if every is not None and not burst:
            raise Refused("every goes with burst")
        what = f"the window {window!r}" if window else "the whole screen"
        what += f", as seen with {vision}" if vision else ""
        if burst:
            taken = s.burst(s.scratch / "burst.png", int(burst), every=every or 0,
                            window=text(window), crop=text(crop), park=flag(park),
                            vision=vision, sheet=out, sheet_size=SHEET_SIZE)
            times = ", ".join(f"{at:.2f}" for _, at in taken)
            said = (f"{len(taken)} pictures of {what} in rows of four, left to right, "
                    f"taken at {times} seconds.")
        else:
            s.capture(out, window=text(window), crop=text(crop), park=flag(park), vision=vision)
            said = f"A picture of {what}."
        return [{"type": "image", "data": base64.b64encode(out.read_bytes()).decode(),
                 "mimeType": "image/png"},
                {"type": "text", "text": said}]

    def clip(self, path):
        """Where record_video saves to: a new file in the allow file's directory,
        as a line wait reads one, and nowhere else an agent could have one
        written. Nothing of the person's is written over."""
        if self.home is None:
            raise Refused("record_video saves a path in the --allow file's directory, and shotbox "
                          "mcp was given no --allow file; without path, it saves in the "
                          "session's scratch directory")
        home = Path(self.home).resolve()
        out = (home / str(path)).resolve()
        if not out.is_relative_to(home) or out.suffix.lower() != ".mp4":
            raise Refused(f"record_video saves an .mp4 in {home}, by its path from there, "
                          f"not {path!r}")
        if out.exists():
            raise Refused(f"there's a {'directory' if out.is_dir() else 'file'} at {out} "
                          "already: record_video saves only a new file")
        above = next(p for p in out.parents if p.exists())
        if not above.is_dir():
            raise Refused(f"{above} is a file, not a directory to save in")
        return out

    def record_video(self, session, seconds=None, window=None, crop=None, sound=False,
                     path=None, until_line=None, file=None, tail=None):
        s = self.session(session)
        seconds = within(seconds, "seconds", 0.1, RECORD_SECONDS)
        if seconds is None and until_line is None:
            raise Refused(f"record_video needs seconds: how long to record, 0.1 to "
                          f"{RECORD_SECONDS}, or until_line")
        seconds = seconds or RECORD_SECONDS
        until = None if until_line is None else (str(until_line),
                                                 self.log(file, "record_video until_line"))
        tail = within(tail, "tail", 0, RECORD_SECONDS) or 0
        if tail and not until:
            raise Refused("tail goes with until_line: how long to record after its line")
        if flag(sound) and not s.audio:
            raise Refused(f"{str(session)!r} has no sound: start a session with audio")
        out = self.clip(path) if path is not None else next(
            p for n in itertools.count(1)
            if not (p := s.scratch / f"record-{n}.mp4").exists())
        # Recorded in the scratch dir, and only a whole clip saved at path.
        took = Path(tempfile.mkdtemp(prefix="record-", dir=s.scratch))
        rec = s.record(took / "clip.mp4", seconds=seconds, window=text(window),
                       crop=text(crop), sound=flag(sound), size=RECORD_SIZE, until=until,
                       tail=tail, within=until and Path(self.home).resolve())
        try:
            if path is None:
                rec.path.rename(out)
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                with open(rec.path, "rb") as src, open(out, "xb") as dst:
                    try:
                        shutil.copyfileobj(src, dst)
                    except OSError:
                        out.unlink()    # no part of a clip left in the person's tree
                        raise
        except OSError as e:
            raise Refused(f"couldn't save the clip at {out}: {e.strerror}. It's at "
                          f"{rec.path} until the session stops")
        if path is not None:
            rec.path.unlink()
        try:
            length = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                 "csv=p=0", str(out)], capture_output=True, text=True, check=True).stdout)
            packets = sorted(float(t) for t in subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                 "packet=pts_time", "-of", "csv=p=0", str(out)],
                capture_output=True, text=True, check=True).stdout.split())
            # Up to FRAMES of them, evenly by number: a short clip has fewer.
            picked = sorted({i * len(packets) // FRAMES for i in range(FRAMES)})
            # Numbered from 1 as they come, one a second: no frame repeated.
            subprocess.run(["ffmpeg", "-loglevel", "error", "-nostdin", "-i", str(out), "-vf",
                            "select=" + "+".join(f"eq(n\\,{i})" for i in picked)
                            + ",setpts=N/TB", "-r", "1", str(took / "frame-%d.png")],
                           capture_output=True, text=True, check=True)
            png = sheet([took / f"frame-{k + 1}.png" for k in range(len(picked))],
                        took / "sheet.png", size=SHEET_SIZE)
        except (subprocess.CalledProcessError, ValueError) as e:
            why = getattr(e, "stderr", None)
            raise Refused(f"{out} is saved, but its frames couldn't be taken: "
                          f"{why.strip() if why else e}")
        what = f"the window {window!r}" if window else "the whole screen"
        times = ", ".join(f"{packets[i]:.2f}" for i in picked)
        said = [f"Saved {out}, {length:.2f} seconds of {what}. {len(picked)} frames from it in "
                f"rows of four, left to right, at {times} seconds."]
        if rec.line is not None:
            said.append(f"It ended on the line: {rec.line}")
        elif length < seconds - 0.2:
            said.append(f"It stopped short of the {seconds:g} seconds asked for: "
                        + ("it reached 100 MB." if out.stat().st_size >= RECORD_SIZE
                           else "the session's display or sound server went."))
        elif until:
            said.append(f"No line matching {until_line!r} came in its {seconds:g} seconds.")
        if rec.heard:
            said += (describe(rec.heard)
                     + (rec.problems() or ["No gaps, clipping or silence."]))
        return [{"type": "image", "data": base64.b64encode(png.read_bytes()).decode(),
                 "mimeType": "image/png"},
                {"type": "text", "text": "\n".join(said)}]

    def get_accessible_tree(self, session, window=None, bounds=False):
        s = self.a11y_session(session)
        return tree_text(s.a11y_tree(text(window)), flag(bounds))

    def a11y_session(self, session):
        s = self.session(session)
        if not s.a11y:
            raise Refused(f"{session!r} has no accessibility bus: start a session with "
                          "a11y true (the default) to read the tree")
        return s

    # --- acting --------------------------------------------------------------

    def click_element(self, session, name=None, role=None, x=None, y=None, window=None,
                      button=1, double=False, hold=None, state=None, timeout=None):
        s = self.session(session)
        button, double = int(number(button, "button") or 1), flag(double)
        hold, state = held(hold, state)
        if double and (hold is not None or state):
            raise Refused("double, or hold and state: not together")
        if name is not None:
            if hold is not None or state:
                raise Refused("hold and state go with x and y, not a node's name")
            timeout = number(timeout, "timeout", 0, 300)
            n = self.a11y_session(session).click_node(
                str(name), role=text(role), window=text(window), button=button, double=double,
                timeout=10 if timeout is None else timeout)
            return f"Clicked {n.line()} at the middle of {n.bounds}."
        if x is None or y is None:
            raise Refused("click_element takes a node's name, or x and y")
        px, py = int(number(x, "x")), int(number(y, "y"))
        s.click(px, py, window=text(window), button=button, double=double, hold=hold,
                state=state)
        done = {"down": "Pressed and left down", "up": "Let go"}.get(state, "Clicked")
        return f"{done} at {px},{py}" + (f" in {window!r}." if window else " on the screen.")

    def scroll_wheel(self, session, dy=None, dx=None, name=None, role=None, x=None, y=None,
                     window=None):
        s = self.session(session)
        dy, dx = (within(v, k, -100, 100, whole=True) or 0 for v, k in ((dy, "dy"), (dx, "dx")))
        wheel(dy, dx)
        turned = "Scrolled " + " and ".join(f"{abs(n)} {ways[n < 0]}" for n, ways in (
            (dy, ("down", "up")), (dx, ("right", "left"))) if n)
        if name is not None:
            n = self.a11y_session(session).scroll_node(str(name), dy, dx, role=text(role),
                                                       window=text(window))
            return f"{turned} over {n.line()}, at the middle of {n.bounds}."
        if x is None or y is None:
            raise Refused("scroll_wheel takes a node's name, or x and y")
        px, py = int(number(x, "x")), int(number(y, "y"))
        s.scroll(px, py, dy, dx, window=text(window))
        return f"{turned} at {px},{py}" + (f" in {window!r}." if window else " on the screen.")

    def press_keys(self, session, keys, hold=None, state=None):
        chords = words(keys)
        if not chords:
            raise Refused("press_keys takes the keys to press, like [\"ctrl+s\"] or \"Tab Tab\"")
        hold, state = held(hold, state)
        self.session(session).key(*chords, hold=hold, state=state)
        done = {"down": "Pressed and left down", "up": "Let go of"}.get(
            state, f"Held for {hold:g} s" if hold is not None else "Pressed")
        return f"{done} " + ", ".join(chords) + "."

    def use_gamepad(self, session, action, pad=1, buttons=None, which=None, x=None, y=None,
                    value=None, hold=None, state=None):
        s = self.session(session)
        n = within(pad, "pad", 1, 4, whole=True) or 1
        hold, state = held(hold, state)
        if action == "press":
            names = words(buttons)
            if not names:
                raise Refused("press takes buttons, like [\"a\"] or \"lb rb\"")
            s.pad_press(names, n, hold=hold, state=state)
            done = {"down": "Pressed and left down", "up": "Let go of"}.get(
                state, f"Held for {hold:g} s" if hold is not None else "Pressed")
            return f"{done} {', '.join(names)} on pad {n}."
        if state:
            raise Refused("state goes with press; a stick or trigger stays where it's put")
        if action in ("stick", "trigger"):
            if which not in ("left", "right"):
                raise Refused(f"which is left or right, not {which!r}")
            if action == "stick":
                px, py = within(x, "x", -1, 1) or 0, within(y, "y", -1, 1) or 0
                s.pad_stick(which, px, py, n, hold=hold)
                put = f"Pushed the {which} stick to {px:g}, {py:g}"
            else:
                v = within(value, "value", 0, 1) or 0
                s.pad_trigger(which, v, n, hold=hold)
                put = f"Pulled the {which} trigger to {v:g}"
            let_go = f" for {hold:g} s, then let go" if hold is not None else ""
            return f"{put}{let_go}, on pad {n}."
        if action == "neutral":
            s.pad_neutral(n)
            return f"Let go of everything on pad {n}."
        raise Refused(f"action is press, stick, trigger or neutral, not {action!r}")

    def type_text(self, session, text):
        self.session(session).type(str(text))
        return f"Typed {len(str(text))} characters."

    def reference(self, value):
        """The picture a match wait names: a file in the allow file's
        directory, and nowhere else an agent could have it read."""
        if value is None:
            raise Refused("wait_for match needs a value: the picture (a PNG) to look like")
        if self.home is None:
            raise Refused("wait_for match takes a picture in the --allow file's directory, "
                          "and shotbox mcp was given no --allow file")
        home = Path(self.home).resolve()
        path = (home / str(value)).resolve()
        if not (path.is_relative_to(home) and path.is_file()):
            raise Refused(f"wait_for match takes a picture in {home}, by its path from "
                          f"there; there's no file {value!r} in it")
        return path

    def log(self, file, who="wait_for line"):
        """The file a line wait (`who`) reads: in the allow file's
        directory, where the programs start, and nowhere else an agent could
        have it read. It needn't be there yet."""
        if file is None:
            raise Refused(f"{who} needs a file: the one the line is written to")
        if self.home is None:
            raise Refused(f"{who} reads a file in the --allow file's directory, "
                          "and shotbox mcp was given no --allow file")
        home = Path(self.home).resolve()
        path = (home / str(file)).resolve()
        if not path.is_relative_to(home) or path.is_dir():
            raise Refused(f"{who} reads a file in {home}, by its path from there, "
                          f"not {file!r}")
        return path

    def wait_for(self, session, what, value=None, window=None, timeout=10, crop=None,
                 fuzz=None, most=None, file=None, from_start=False):
        s = self.session(session)
        if what not in WAITS:
            raise Refused(f"wait_for waits for {', '.join(WAITS[:-1])} or {WAITS[-1]}, "
                          f"not {what!r}")
        # Each call holds the server till it's done: not for long.
        timeout = number(timeout, "timeout", 0, 300)
        timeout = 10 if timeout is None else timeout
        if what in ("match", "change"):
            fuzz = f"{number(fuzz, 'fuzz', 0, 100) or 0:.6f}".rstrip("0").rstrip(".") + "%"
            most = int(number(most, "most", 0) or 0)
            if what == "match":
                at = s.wait_match(self.reference(value), text(window), text(crop), fuzz, most,
                                  timeout)
                return f"It matches {value}, after {at:.3f} s."
            at = s.wait_change(text(window), text(crop), fuzz, most, timeout)
            return f"It changed, after {at:.3f} s."
        if value is None and what != "stable":
            raise Refused(f"wait_for {what} needs a value: what to wait for")
        if what == "window":
            w = s.wait_window(str(value), timeout=timeout)
            return f"{w[1]!r} is up: {w[2]}x{w[3]} at {w[4]},{w[5]}."
        if what == "stable":
            s.wait_stable(number(value, "value", 0, 60) or 0.5, window=text(window),
                          timeout=timeout)
            return "It has held still."
        if what == "announcement":
            self.a11y_session(session).wait_announce(str(value), timeout=timeout)
            return f"Something matching {value!r} was announced."
        if what == "focus":
            n = self.a11y_session(session).wait_focus(str(value), timeout=timeout,
                                                      window=text(window))
            return f"The focus is on {n.line()}."
        return s.wait_line(str(value), self.log(file), flag(from_start), timeout,
                           within=Path(self.home).resolve())

    # --- checking ------------------------------------------------------------

    def check_accessible_names(self, session, window=None, ignore=None):
        return problems(self.a11y_session(session).a11y_lint(text(window), words(ignore)))

    def check_contrast(self, session, window=None, text_ratio=4.5, graphics=3.0,
                       edges=None, ignore=None):
        nodes = self.a11y_session(session).contrast(
            text(window), number(text_ratio, "text_ratio") or 4.5,
            number(graphics, "graphics") or 3.0, number(edges, "edges"), words(ignore),
            notes=True)
        notes = [n for n in nodes if not n.fails]
        whys = list(dict.fromkeys(n.why for n in notes if "why" in n))
        return "\n".join([problems([n for n in nodes if n.fails])]
                         + ([f"{len(notes)} not measured, which doesn't fail:", problems(notes)]
                            if notes else [])
                         + whys)

    def check_focus_order(self, session, key="Tab", window=None, visible=False, most=50,
                          ignore=None):
        s = self.a11y_session(session)
        most = int(number(most, "most", 1, 200) or 50)
        args = ["tabwalk", f"--key={key or 'Tab'}", "--max", str(most),
                *(["--visible"] if flag(visible) else []),
                *[f"--ignore={i}" for i in words(ignore)],
                *([f"--window={window}"] if window else [])]
        got = s._a11y(*args, timeout=90 + most * 5)
        return "\n".join(walk_lines(key or "Tab", most, [Node(n) for n in got["focus"]],
                                    got["end"], [Node(n) for n in got["unreached"]],
                                    got["began"] and Node(got["began"]),
                                    focus="click it with click_element"))

    def list_announcements(self, session):
        log = Path(self.a11y_session(session).env["SHOTBOX_ANNOUNCEMENTS"])
        said = log.read_text().strip() if log.exists() else ""
        return said or "Nothing announced yet."

    def record_audio(self, session, action, gap=None):
        s = self.session(session)
        name = str(session)
        if action == "start":
            if not s.audio:
                raise Refused(f"{name!r} has no sound: start a session with audio")
            if name in self.sounds:
                raise Refused(f"{name!r}'s sound is being recorded already")
            wav = s.scratch / "sound.wav"
            self.sounds[name] = recording(s.env, wav, None), wav
            return f"Recording {name!r}'s sound; action stop ends it and says what it heard."
        if action == "stop":
            if name not in self.sounds:
                raise Refused(f"{name!r}'s sound isn't being recorded: action start first")
            keeper, wav = self.sounds.pop(name)
            stop(keeper)
            gap = number(gap, "gap", 0.01, 60000) or 1
            try:
                heard = sound.hear(ended(keeper, wav), gap=gap / 1000)
            except RuntimeError as e:
                raise Refused(str(e))
            return "\n".join(sound.describe(heard)
                             + (sound.problems(heard) or ["No gaps, clipping or silence."]))
        raise Refused(f"action is start or stop, not {action!r}")

    def close(self):
        for name in list(self.sessions):
            try:
                self.stop_session(name)
            except BaseException:   # going anyway: the rest still need stopping
                pass


def schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


SESSION = {"type": "string", "description": "Its name, from start_session"}
# A burst's sheet is one image: shrunk to this many pixels each way, it's
# still readable and not a dozen screens' worth, however tall its pictures.
SHEET_SIZE = 1920
# A clip holds the server while it records, so it's about as long as the
# longest burst; and it stops at a size few clips of a screen reach, so the
# disk can't fill.
RECORD_SECONDS, RECORD_SIZE = 60, 100_000_000
# Clients time a call out at about 60 s, and some count from its last progress.
PROGRESS = 5
FRAMES = 8
HOLD = {"type": "number", "minimum": 0, "maximum": 60,
        "description": "Seconds to keep it down before letting go"}
STATE = {"type": "string", "enum": ["down", "up"],
         "description": "Only press, leaving it down until a call with up; or only let go"}
CROP = {"type": "string", "description": "WIDTHxHEIGHT+X+Y, inside what's taken"}
WINDOW = {"type": "string", "description":
          "A window's title, exactly or a regex matched in full, or app=RE for its app id"}
NAME = {"type": "string", "description": "A node's name, as get_accessible_tree gives it: "
        "exactly, or a regex matched in full"}
ROLE = {"type": "string", "description": "Narrows name: exactly, or a regex"}
X = {"type": "integer", "description": "Pixels from the left"}
Y = {"type": "integer", "description": "Pixels from the top"}
IGNORE = {"type": "array", "items": {"type": "string"},
          "description": "Regexes for lines that can't be fixed yet"}
PROGRAM = {"type": "string", "description": "Its name in the allow list"}
WAITS = ("window", "stable", "announcement", "focus", "match", "change", "line")

# What the agent knows of each tool is its description: what it's for, what
# to read in its answer, and what to do instead when it's the wrong one.
TOOLS = [
    ("start_session",
     "Start a sealed session: a display of its own (X11 under Xvfb, or Wayland under a "
     "headless sway), its own D-Bus and accessibility bus, and a scratch home. Nothing in it "
     "reaches the desktop of the person you're working for, and nothing of theirs reaches "
     "it. The answer gives its name, display and size, the programs spawn_program can start "
     "in it, and anything it couldn't seal. It lasts until stop_session.",
     schema({"name": {"type": "string", "description": "What to call it (s1, s2... by default)"},
             "a11y": {"type": "boolean", "description": "An accessibility bus (default true)"},
             "wayland": {"type": "boolean", "description": "Wayland, not X11"},
             "xwayland": {"type": "boolean", "description": "Wayland, with Xwayland for X "
                          "programs"},
             "dark": {"type": "boolean", "description": "GTK and libadwaita's dark style"},
             "high_contrast": {"type": "boolean", "description": "Their high contrast style"},
             "text_scale": {"type": "number", "minimum": 0.1,
                            "description": "Their text size, 1.25 for 125%"},
             "audio": {"type": "boolean", "description": "A sound server whose sink plays "
                       "to nowhere"},
             "gamepads": {"type": "integer", "minimum": 0, "maximum": 4,
                          "description": "Virtual Xbox 360 pads, for use_gamepad"},
             "screen": {"type": "string", "description": "WIDTHxHEIGHT (1280x800)"},
             "net": {"type": "string", "enum": ["loopback"], "description": "A network its "
                     "programs share and nothing else is on: nothing reaches in or out"},
             "identity": {"type": "string", "description": "USER@HOST: who and where its "
                          "programs are, for whoami, hostname, ls -l and $USER"},
             "usage": {"type": "boolean", "description": "Say, when it's stopped, what its "
                       "programs used: peak memory and CPU time"},
             "clock": {"type": "string", "description": "ISO 8601, 2026-09-26T18:40:00, in tz "
                       "or UTC: where its wall clock starts and runs on from, so dates in "
                       "pictures don't change (needs libfaketime)"},
             "tz": {"type": "string", "description": "Its time zone, Europe/Berlin (UTC)"}})),
    ("stop_session",
     "Stop a session start_session made, when you're done with it: every program in it is "
     "killed, unsaved work and all, and its scratch directory is deleted, with any clip "
     "record_video saved there (one saved at a path is kept). In a session started with "
     "usage, the answer says its programs' peak memory and CPU time. You needn't stop "
     "sessions before the server ends: they all stop with it.",
     schema({"session": SESSION}, ["session"])),
    ("spawn_program",
     "Start one of the programs the person allowed this server to run, by its name, in a "
     "session; nothing else can run. The answer gives its pid. Then wait_for its window "
     "before looking at it. If it doesn't come up, get_program_output says why.",
     schema({"session": SESSION, "program": PROGRAM}, ["session", "program"])),
    ("get_program_output",
     "Read the last lines (40, or lines) a spawned program wrote to stdout and stderr, after "
     "a line saying whether it's still running or exited, with its status. It works after "
     "the program has exited, until the session stops; for a program spawned more than once, "
     "it reads the latest. Use it when a window never comes up or a program seems to have "
     "crashed; to wait for a line in a log file, use wait_for line.",
     schema({"session": SESSION, "program": PROGRAM,
             "lines": {"type": "integer", "minimum": 1, "maximum": 2000,
                       "description": "How many lines from the end (40)"}},
            ["session", "program"])),
    ("get_accessible_tree",
     "Read what a screen reader would find: every visible node, a line each, indented, with "
     "its role, name, description and the states a screen reader says (focusable, focused, "
     "checked...). The cheapest way to see what's on screen and what can be clicked by name. "
     "A node with an empty name (\"\") is one a screen reader can say nothing for. Use "
     "take_screenshot for how it looks.",
     schema({"session": SESSION, "window": WINDOW,
             "bounds": {"type": "boolean", "description": "Add @x,y WIDTHxHEIGHT to each line"}},
            ["session"])),
    ("take_screenshot",
     "Take a picture of the screen, or of one window, RETURNED AS AN IMAGE for you to look "
     "at: the only way to see colors, layout and what's drawn. Pictures cost far more than "
     "get_accessible_tree; take the window rather than the screen, and crop when you need a "
     "part. vision checks nothing is told apart by color alone; burst, as one image in rows "
     "of four, is for motion, an animation or a transition.",
     schema({"session": SESSION, "window": WINDOW,
             "crop": CROP,
             "park": {"type": "boolean", "description": "Move the pointer away first, so "
                      "nothing shows its hover"},
             "burst": {"type": "integer", "minimum": 2, "maximum": 12,
                       "description": "Pictures to take"},
             "every": {"type": "number", "minimum": 0, "maximum": 5,
                       "description": "With burst, seconds between them"},
             "vision": {"type": "string", "enum": sorted(VISION),
                        "description": "protanopia (no red cones), deuteranopia (no green), "
                                       "tritanopia (no blue), or gray (no color)"}},
            ["session"])),
    ("record_video",
     "Record a video of the screen, or of one window, and save it as an .mp4: in the "
     "session's scratch directory, gone when the session stops, or at path. The answer is "
     "the saved path, how long the clip is, and up to 8 frames from it RETURNED AS ONE "
     "IMAGE in rows of four, with the times they show: you can't play the clip, but a "
     "person or another tool can. A clip stops at 100 MB. sound also says what it heard, "
     "as record_audio does. until_line ends it at an event instead; the answer gives the "
     "line. For a few moments of motion, take_screenshot's burst is cheaper.",
     schema({"session": SESSION,
             "seconds": {"type": "number", "minimum": 0.1, "maximum": RECORD_SECONDS,
                         "description": "How long; with until_line, the most (60)"},
             "window": WINDOW,
             "crop": CROP,
             "sound": {"type": "boolean", "description": "What plays too, in a session "
                       "started with audio"},
             "path": {"type": "string", "description": "Where to save it, a new .mp4, by its "
                      "path from the allow file's directory"},
             "until_line": {"type": "string", "description": "A regex: stop tail seconds "
                            "after a line matching it is written to file"},
             "file": {"type": "string", "description": "until_line's, by its path from the "
                      "allow file's directory"},
             "tail": {"type": "number", "minimum": 0, "maximum": RECORD_SECONDS,
                      "description": "Seconds to go on after the line (0)"}},
            ["session"])),
    ("click_element",
     "Click an element by its accessible name, or a point by x and y, on the screen or "
     "inside a window. By name is the one to use: it waits up to timeout seconds for the "
     "node, raises its window and clicks its middle, and the answer gives the node and its "
     "bounds. When two nodes match it lists them, with where they are; when none does, the "
     "nearest names. A click gives a window the keyboard, which nothing else does on X11. "
     "hold and state, by x and y, hold the button as press_keys holds keys.",
     schema({"session": SESSION, "name": NAME, "role": ROLE, "x": X, "y": Y,
             "window": WINDOW,
             "button": {"type": "integer", "description": "1 left, 2 middle, 3 right"},
             "double": {"type": "boolean", "description": "Click twice, as a double click"},
             "hold": HOLD, "state": STATE,
             "timeout": {"type": "number", "minimum": 0, "maximum": 300,
                         "description": "By name, seconds to wait for the node (10)"}},
            ["session"])),
    ("scroll_wheel",
     "Turn the mouse wheel over a node by its name, found as click_element finds it (waiting "
     "for it, its window raised), or at a point by x and y, leaving the pointer there. Use "
     "it for what's past a list's, a page's or a window's edge, then wait_for stable and "
     "look again; to move the keyboard focus, use press_keys.",
     schema({"session": SESSION,
             "dy": {"type": "integer", "minimum": -100, "maximum": 100,
                    "description": "Clicks down; negative, up"},
             "dx": {"type": "integer", "minimum": -100, "maximum": 100,
                    "description": "Clicks right; negative, left"},
             "name": NAME, "role": ROLE, "x": X, "y": Y, "window": WINDOW},
            ["session"])),
    ("press_keys",
     "Press keys in turn, each a chord: Tab, Return, Escape, ctrl+s, alt+shift+Tab, F6. They "
     "go to whatever has the keyboard; click a window first with click_element if nothing "
     "does. hold presses them all together and keeps them down that long (walking while "
     "running); state down leaves them down, across calls, until a call with state up (move "
     "or click while a key is held). The answer says what it pressed.",
     schema({"session": SESSION,
             "keys": {"type": "array", "items": {"type": "string"},
                      "description": "Chords, pressed one after another"},
             "hold": HOLD, "state": STATE}, ["session", "keys"])),
    ("use_gamepad",
     "Work a virtual gamepad, in a session started with gamepads: press buttons together, "
     "tapped, held for hold seconds, or with state down left down until a call with state "
     "up; push a stick to x, y; pull a trigger to value; or neutral, letting go of "
     "everything. A stick or trigger stays where it's put, unless hold says for how long. "
     "Programs in the session see a real Xbox 360 controller, and none of the machine's. "
     "For keys and the mouse, press_keys and click_element.",
     schema({"session": SESSION,
             "action": {"type": "string", "enum": ["press", "stick", "trigger", "neutral"],
                        "description": "Press buttons, push a stick, pull a trigger, "
                        "or let go of everything"},
             "pad": {"type": "integer", "minimum": 1, "maximum": 4,
                     "description": "Which of the session's pads (1)"},
             "buttons": {"type": "array", "items": {"type": "string"},
                         "description": "a b x y lb rb back start guide ls rs, and the "
                         "d-pad's up down left right"},
             "which": {"type": "string", "enum": ["left", "right"],
                       "description": "Which stick or trigger"},
             "x": {"type": "number", "minimum": -1, "maximum": 1,
                   "description": "The stick, left to right"},
             "y": {"type": "number", "minimum": -1, "maximum": 1,
                   "description": "The stick, up to down"},
             "value": {"type": "number", "minimum": 0, "maximum": 1,
                       "description": "The trigger, let go to pulled all the way"},
             "hold": HOLD, "state": STATE}, ["session", "action"])),
    ("type_text",
     "Type text into whatever has the keyboard focus, a key at a time. ASCII only: a "
     "character with no key fails the call, once what's before it is typed. For keys and "
     "shortcuts (Return, ctrl+s), use press_keys.",
     schema({"session": SESSION, "text": {"type": "string", "description": "The text to type"}},
            ["session", "text"])),
    ("wait_for",
     "Wait, instead of sleeping, for: a window (value: its title, exactly or a regex, or "
     "app=RE), stable (the screen, or the window, unchanged for value seconds, 0.5, up to "
     "60: after a click, for the repaint), an announcement (value: a regex in what was "
     "announced to a screen reader; each announcement is waited for once), focus (value: the name of the node with the keyboard focus, as "
     "click_element takes one), match (value: a PNG, by its path from the allow file's directory, "
     "that the screen, or the window, cropped to crop, should look like), "
     "change (from how it looked when the wait started), or "
     "line (value: a regex found in a line of file, written after the wait starts, or "
     "with from_start, anywhere in it: for a program that logs its events, rather than a "
     "sleep; a file not there yet is waited for, and one rotated or rewritten is read from "
     "the top). match and change say how long they took; line answers with the line. "
     "Fails after timeout seconds, saying so.",
     schema({"session": SESSION,
             "what": {"type": "string", "enum": list(WAITS), "description": "The kind of wait"},
             "value": {"type": "string", "description": "What to wait for, as what says"},
             "window": WINDOW,
             "timeout": {"type": "number", "minimum": 0, "maximum": 300,
                         "description": "Seconds before it fails (10)"},
             "crop": CROP,
             "fuzz": {"type": "number", "minimum": 0, "maximum": 100,
                      "description": "match, change: percent a pixel may differ by (0)"},
             "most": {"type": "integer", "minimum": 0,
                      "description": "match, change: pixels past fuzz allowed (0)"},
             "file": {"type": "string", "description": "line's, by its path from the allow "
                      "file's directory"},
             "from_start": {"type": "boolean", "description": "line: anywhere in file"}},
            ["session", "what"])),
    ("check_accessible_names",
     "Lint the accessibility tree for nodes a screen reader has no name to say for: "
     "controls, images (not inside a named control) and anything else that takes the focus. "
     "The answer: a tree line each, with bounds, then what's wrong; or \"No problems.\". Use "
     "it to check labels after a change; for colors, use check_contrast.",
     schema({"session": SESSION, "window": WINDOW, "ignore": IGNORE}, ["session"])),
    ("check_contrast",
     "Check that text, buttons, links and images stand out from their background: WCAG's "
     "4.5:1 for text (3 for large text) and 3:1 for images unless text_ratio and graphics "
     "say otherwise, and with edges, a button's or field's edge too. It raises the window "
     "and measures a picture of the screen in each node's bounds. The answer: each node that "
     "fails, a tree line with bounds, its ratio and colors, or \"No problems.\"; then what "
     "it couldn't measure, which doesn't fail (too small, or GTK 4's, whose positions are "
     "wrong), and why. A window where nothing can be measured, as GTK 4's, is an error, not "
     "a pass. Switches, sliders, check boxes, disabled nodes and what's scrolled out of view "
     "are skipped. For missing names, use check_accessible_names.",
     schema({"session": SESSION, "window": WINDOW,
             "text_ratio": {"type": "number", "description": "Least for text (4.5)"},
             "graphics": {"type": "number", "description": "Least for images (3)"},
             "edges": {"type": "number", "description": "Least for edges (unchecked)"},
             "ignore": IGNORE}, ["session"])),
    ("check_focus_order",
     "Check the keyboard focus order in one call: press Tab, or key, until the focus comes "
     "back round, and list where it went, a tree line each, after a \"began in\" line. Then "
     "the controls the key never reached, and how it ended: round again (fine), stuck (a "
     "trap, or a text view nothing leaves), not round after most presses, lost (no node has "
     "the focus), stopped (a text view closed as it was left), or nothing focused to begin "
     "with (click the window with click_element first). With visible, \"focus not shown on "
     "N\" counts stops that don't show it. A text view that takes Tab is left as a person "
     "would, with ctrl+Tab, shift+Tab, Escape or F6. The focus stays where it ended. To "
     "press a few keys yourself, use press_keys.",
     schema({"session": SESSION,
             "key": {"type": "string", "description": "The chord to press (Tab)"},
             "window": WINDOW,
             "visible": {"type": "boolean", "description": "Also check each place it lands "
                         "shows it has the focus"},
             "most": {"type": "integer", "minimum": 1, "maximum": 200,
                      "description": "Presses at most (50)"},
             "ignore": {**IGNORE, "description": "Regexes for unreached controls to leave "
                        "out"}}, ["session"])),
    ("list_announcements",
     "List what programs in the session have announced to a screen reader since it "
     "started, oldest first, a line each with its politeness: \"polite: Saved\", "
     "\"assertive: Error\". That's GTK's announcements and a browser's live regions. "
     "Reading doesn't clear them; \"Nothing announced yet.\" means none so far. Use it to "
     "check what a screen reader user heard after an action; to wait for one, use wait_for "
     "announcement.",
     schema({"session": SESSION}, ["session"])),
    ("record_audio",
     "Record what a session started with audio plays, from a call with action start to one "
     "with action stop, across the calls between that make the sound. stop's answer: the "
     "sound's length, peak and RMS level (dBFS), samples clipped, and each gap of exact "
     "silence between the first sound and the last, as a program that falls behind leaves; "
     "then what's wrong, if anything (gaps, clipping, silence). Sounds meant to be apart, "
     "two beeps say, need a gap longer than the silence between them. For a video of the "
     "screen with its sound, up to a minute, use record_video with sound.",
     schema({"session": SESSION,
             "action": {"type": "string", "enum": ["start", "stop"],
                        "description": "start, then stop"},
             "gap": {"type": "number", "minimum": 0.01, "maximum": 60000,
                     "description": "Silence, in milliseconds, said as a gap (1)"}},
            ["session", "action"])),
]

# Not take_screenshot (park moves the pointer) or check_contrast (it raises the window).
READ_ONLY = {"get_program_output", "get_accessible_tree", "wait_for", "check_accessible_names",
             "list_announcements"}
# Called again, these change nothing more: a stopped session's stop is refused.
IDEMPOTENT = {"stop_session", "take_screenshot", "check_contrast"}
# What the agent spawns and drives may be a browser on the host's network.
OPEN_WORLD = {"spawn_program", "click_element", "scroll_wheel", "press_keys", "use_gamepad",
              "type_text", "check_focus_order"}


def tool_list(allowed=()):
    """The tools, with spawn_program's and get_program_output's program one of `allowed`."""
    program = {**PROGRAM, "enum": list(allowed)} if allowed else PROGRAM
    return [{"name": name, "description": desc,
             "inputSchema": ({**params, "properties": {**params["properties"],
                                                       "program": program}}
                             if "program" in params["properties"] else params),
             "annotations": hints(name)}
            for name, desc, params in TOOLS]


def hints(name):
    """A tool's annotations. The destructive and idempotent hints mean
    nothing on a read-only tool, so it has neither."""
    got = {"readOnlyHint": name in READ_ONLY}
    if name not in READ_ONLY:
        # stop_session ends the session's programs, and their unsaved work.
        got["destructiveHint"] = name == "stop_session"
        if name in IDEMPOTENT:
            got["idempotentHint"] = True
    if name not in OPEN_WORLD:
        got["openWorldHint"] = False
    return got

# The names the tools had up to 0.7.1, for an agent or a client's settings that still uses one.
RENAMED = {"spawn": "spawn_program", "program_output": "get_program_output",
           "accessible_tree": "get_accessible_tree", "screenshot": "take_screenshot",
           "record": "record_video", "click": "click_element", "scroll": "scroll_wheel",
           "gamepad": "use_gamepad", "lint": "check_accessible_names",
           "contrast": "check_contrast", "tab_walk": "check_focus_order",
           "announcements": "list_announcements", "sound": "record_audio"}


def call(server, name, args):
    """A tool call's result: its content, and isError if it failed."""
    known = {t[0]: t[2] for t in TOOLS}
    if name not in known:
        now = f": it's {RENAMED[name]} now" if name in RENAMED else ""
        return {"content": [{"type": "text", "text": f"There's no tool called {name!r}{now}."}],
                "isError": True}
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return {"content": [{"type": "text", "text": "arguments are an object, by name"}],
                "isError": True}
    unknown = set(args) - set(known[name]["properties"])
    missing = [r for r in known[name]["required"] if args.get(r) is None]
    try:
        if unknown:
            raise Refused(f"{name} doesn't take {', '.join(sorted(unknown))}")
        if missing:
            raise Refused(f"{name} needs {', '.join(missing)}")
        got = getattr(server, name)(**args)
    except (Refused, SessionError, XError) as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except Exception as e:   # one call gone wrong isn't the server gone
        return {"content": [{"type": "text", "text": f"{name} failed: {e!r}"}],
                "isError": True}
    content = got if isinstance(got, list) else [{"type": "text", "text": got}]
    return {"content": content}


def handle(server, msg):
    """The answer to one JSON-RPC message, or None for a notification."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "not a JSON-RPC 2.0 request"}}
    if "id" not in msg:
        return None
    rid, method, params = msg["id"], msg.get("method"), msg.get("params") or {}
    if not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32602, "message": "params are an object"}}
    if method == "initialize":
        asked = params.get("protocolVersion")
        result = {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "shotbox", "version": __version__},
                  "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_list(server.allowed)}
    elif method == "tools/call":
        result = call(server, params.get("name"), params.get("arguments"))
    else:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"no method {method!r}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


# The variables a session's processes are known by: the server's own, for
# what inherits its environment (Xvfb), and a session's scratch, runtime
# dir and home, for the rest. Not any variable: a shell someone cd'd into
# a session's dir to tail its log has PWD there, and isn't the session's.
MARKS = (b"SHOTBOX_MCP", b"SHOTBOX_SCRATCH", b"XDG_RUNTIME_DIR", b"HOME")


def ours(environ, root):
    """Whether an environment (/proc's, NUL-separated) is a session's under
    `root`: one of MARKS is `root` or a path in it."""
    for entry in environ.split(b"\0"):
        key, _, value = entry.partition(b"=")
        if key in MARKS and (value == root or value.startswith(root + b"/")):
            return True
    return False


def reap(root):
    """Kill every process of ours that is a session's under `root`, until
    none is left; then remove it."""
    me, mark = os.getpid(), str(root).encode()
    for _ in range(50):
        found = []
        for d in Path("/proc").glob("[0-9]*"):
            if int(d.name) == me:
                continue
            try:
                if d.stat().st_uid == os.getuid() and ours((d / "environ").read_bytes(), mark):
                    found.append(int(d.name))
            except OSError:   # gone, or not ours to read
                continue
        if not found:
            break
        for pid in found:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        time.sleep(0.05)
    shutil.rmtree(root, ignore_errors=True)


def watch(root):
    """The watchdog: wait for the server's end of the pipe on stdin to
    close, then reap what's left of its sessions. Signals meant for the
    server aren't for it."""
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, signal.SIG_IGN)
    sys.stdin.buffer.read()
    reap(root)
    return 0


def main(argv):
    p = argparse.ArgumentParser(prog="shotbox mcp",
                                description="Sealed sessions for an agent, over the Model "
                                            "Context Protocol on stdin and stdout. Its "
                                            "sessions stop when it does.")
    p.add_argument("--allow", metavar="FILE",
                   help="the programs it may spawn: NAME = COMMAND a line (none without it)")
    p.add_argument("--watch", metavar="DIR", help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    if a.watch:
        return watch(a.watch)
    allowed, home = programs(a.allow)
    from .__main__ import self_command
    # Short: a session's sockets are under it, and a Unix socket's path has
    # 108 bytes.
    root = Path(tempfile.mkdtemp(prefix="sbm-"))
    tempfile.tempdir = str(root)   # where Session makes each scratch dir
    # What a session starts with the server's environment (Xvfb, its bus)
    # has this, for the watchdog to know it by.
    os.environ["SHOTBOX_MCP"] = str(root)
    # The server's end stays open, unused, for as long as the server lives:
    # its closing, however the server ends, is what the watchdog waits for.
    r, _ = os.pipe()
    subprocess.Popen([*self_command(), "mcp", "--watch", str(root)], stdin=r,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    os.close(r)
    # The protocol's alone on stdin and stdout: anything else printed to
    # stdout ends the connection, and a program reading stdin eats
    # requests. So the server keeps them, and fd 0 and 1 are /dev/null and
    # stderr for everything else, sessions and their programs.
    inp, out = os.fdopen(os.dup(0)), os.fdopen(os.dup(1), "w")
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    # A signal ends the server now: the watchdog stops the sessions, in the
    # time a client gives, which a session stopping in turn doesn't fit.
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: os._exit(0))
    server = Server(allowed, home)
    try:
        serve(server, inp, out)
    finally:
        server.close()
    return 0


def serve(server, inp, out):
    """Answer requests from `inp` on `out` in turn, while a thread reads
    `inp`: it answers ping at once, and notes a cancelled request, whose
    waits are called off and whose answer isn't sent. A call with a
    progressToken says how long it's been every PROGRESS seconds. When
    `inp` closes, the call running is called off as if cancelled, and
    those sent after it are answered."""
    lock, requests, cancelled, running = threading.RLock(), queue.Queue(), set(), [None]
    end = object()   # not None: a line can be JSON's null

    def key(rid):
        return json.dumps(rid)   # an id can be anything, as a client sends it

    def send(msg):
        with lock:
            out.write(json.dumps(msg) + "\n")
            out.flush()

    def read():
        try:
            for line in inp:
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except (ValueError, RecursionError):
                    send({"jsonrpc": "2.0", "id": None,
                          "error": {"code": -32700, "message": "not JSON"}})
                    continue
                method = msg.get("method") if isinstance(msg, dict) else None
                if method == "ping" and "id" in msg:
                    send(handle(server, msg))
                elif method == "notifications/cancelled":
                    params = msg.get("params")
                    rid = key(params.get("requestId") if isinstance(params, dict) else None)
                    with lock:
                        cancelled.add(rid)
                        if rid == running[0]:
                            _CANCEL.set()
                else:
                    requests.put(msg)
        except OSError:   # stdout gone: the server ends as it would at EOF
            pass
        finally:
            with lock:
                if running[0] is not None:
                    cancelled.add(running[0])
                    _CANCEL.set()
            requests.put(end)

    threading.Thread(target=read, daemon=True).start()
    while (msg := requests.get()) is not end:
        rid = msg.get("id") if isinstance(msg, dict) else None
        params = msg.get("params") if isinstance(msg, dict) else None
        meta = params.get("_meta") if isinstance(params, dict) else None
        token = meta.get("progressToken") if isinstance(meta, dict) else None
        done, start = threading.Event(), time.monotonic()
        with lock:
            skip = key(rid) in cancelled
            cancelled.discard(key(rid))
            running[0] = None if skip else key(rid)
            _CANCEL.clear()
        if skip:
            continue

        def tick():
            while not done.wait(PROGRESS):
                with lock:
                    if not (done.is_set() or key(rid) in cancelled):
                        send({"jsonrpc": "2.0", "method": "notifications/progress",
                              "params": {"progressToken": token,
                                         "progress": round(time.monotonic() - start)}})
        if token is not None:
            threading.Thread(target=tick, daemon=True).start()
        try:
            answer = handle(server, msg)
        except Exception as e:   # one request gone wrong isn't the server gone
            answer = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": repr(e)}}
        done.set()
        with lock:
            if answer is not None and key(rid) not in cancelled:
                send(answer)
            running[0] = None
            cancelled.discard(key(rid))
    _CANCEL.clear()   # for the sessions' stopping, which waits too
