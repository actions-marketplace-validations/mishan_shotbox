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
import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import __version__
from .a11y import Node, unseen
from .screen import SessionError
from .session import Session
from .xtest import XError

# Newest first: a client asking for one of these gets it; any other, the newest.
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")


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
    lines = []
    for n in nodes:
        line = "  " * n.depth + n.line()
        if bounds and n.get("bounds"):
            x, y, w, h = n.bounds
            line += f" @{x},{y} {w}x{h}"
        lines.append(line)
    return "\n".join(lines) or "(no accessible windows)"


class Server:
    def __init__(self, allowed=None, home=None):
        self.allowed, self.home = allowed or {}, home
        self.sessions = {}
        self.spawned = {}   # session name -> [(program, process, log)]

    # --- sessions ------------------------------------------------------------

    def session(self, name):
        name = str(name)
        if name in self.sessions:
            return self.sessions[name]
        there = ", ".join(self.sessions) or "none: start one with start_session"
        raise Refused(f"there's no session called {name!r}. Sessions: {there}")

    def start_session(self, name=None, a11y=True, wayland=False, xwayland=False,
                      dark=False, high_contrast=False, text_scale=None, audio=False,
                      screen="1280x800"):
        name = text(name) or next(f"s{n}" for n in range(1, 10000)
                                  if f"s{n}" not in self.sessions)
        if name in self.sessions:
            raise Refused(f"there's a session called {name!r} already")
        m = re.fullmatch(r"(\d+)x(\d+)", str(screen))
        if not m:
            raise Refused(f"screen is WIDTHxHEIGHT, like 1280x800, not {screen!r}")
        s = Session(size=(int(m[1]), int(m[2])), a11y=flag(a11y), wayland=flag(wayland),
                    xwayland=flag(xwayland), dark=flag(dark),
                    high_contrast=flag(high_contrast),
                    text_scale=number(text_scale, "text_scale"), audio=flag(audio))
        s.__enter__()
        self.sessions[name], self.spawned[name] = s, []
        kind = "Wayland (sway)" if s.use_wayland else "X11 (Xvfb)"
        progs = ", ".join(self.allowed) or "none (shotbox mcp was given no --allow file)"
        return (f"Session {name!r} is up: {kind}, {m[1]}x{m[2]}"
                + (", with an accessibility bus" if s.a11y else ", no accessibility bus")
                + f". Programs it can spawn: {progs}.")

    def stop_session(self, session):
        session = str(session)
        s = self.session(session)
        try:
            s.__exit__(None, None, None)
        finally:
            del self.sessions[session], self.spawned[session]
        return f"Session {session!r} stopped, and everything in it."

    def spawn(self, session, program):
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
        return f"Started {program} (pid {proc.pid}) in {session!r}."

    def program_output(self, session, program, lines=40):
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
        return f"{program} (pid {proc.pid}) is {status}. Its output:\n" + (
            "\n".join(tail) or "(nothing)")

    # --- looking -------------------------------------------------------------

    def screenshot(self, session, window=None, crop=None, park=False):
        s = self.session(session)
        out = s.scratch / "screenshot.png"
        s.capture(out, window=text(window), crop=text(crop), park=flag(park))
        what = f"the window {window!r}" if window else "the whole screen"
        return [{"type": "image", "data": base64.b64encode(out.read_bytes()).decode(),
                 "mimeType": "image/png"},
                {"type": "text", "text": f"A picture of {what}."}]

    def accessible_tree(self, session, window=None, bounds=False):
        s = self.a11y_session(session)
        return tree_text(s.a11y_tree(text(window)), flag(bounds))

    def a11y_session(self, session):
        s = self.session(session)
        if not s.a11y:
            raise Refused(f"{session!r} has no accessibility bus: start a session with "
                          "a11y true (the default) to read the tree")
        return s

    # --- acting --------------------------------------------------------------

    def click(self, session, name=None, role=None, x=None, y=None, window=None,
              button=1, double=False):
        s = self.session(session)
        button, double = int(number(button, "button") or 1), flag(double)
        if name is not None:
            n = self.a11y_session(session).click_node(
                str(name), role=text(role), window=text(window), button=button, double=double)
            return f"Clicked {n.line()} at the middle of {n.bounds}."
        if x is None or y is None:
            raise Refused("click takes a node's name, or x and y")
        px, py = int(number(x, "x")), int(number(y, "y"))
        s.click(px, py, window=text(window), button=button, double=double)
        return f"Clicked at {px},{py}" + (f" in {window!r}." if window else " on the screen.")

    def press_keys(self, session, keys):
        chords = words(keys)
        if not chords:
            raise Refused("press_keys takes the keys to press, like [\"ctrl+s\"] or \"Tab Tab\"")
        self.session(session).key(*chords)
        return "Pressed " + ", ".join(chords) + "."

    def type_text(self, session, text):
        self.session(session).type(str(text))
        return f"Typed {len(str(text))} characters."

    def wait_for(self, session, what, value=None, window=None, timeout=10):
        s = self.session(session)
        # Each call holds the server till it's done: not for long.
        timeout = number(timeout, "timeout", 0, 300) or 10
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
        raise Refused(f"wait_for waits for a window, stable, an announcement or focus, "
                      f"not {what!r}")

    # --- checking ------------------------------------------------------------

    def lint(self, session, window=None, ignore=None):
        nodes = self.a11y_session(session).a11y_lint(text(window), words(ignore))
        return "\n".join(f"{n.path}: {n.problem}" for n in nodes) or "No problems."

    def contrast(self, session, window=None, text_ratio=4.5, graphics=3.0, edges=None,
                 ignore=None):
        nodes = self.a11y_session(session).contrast(
            text(window), number(text_ratio, "text_ratio") or 4.5,
            number(graphics, "graphics") or 3.0, number(edges, "edges"), words(ignore))
        return "\n".join(f"{n.path}: {n.problem}" for n in nodes) or "No problems."

    def tab_walk(self, session, key="Tab", window=None, visible=False, most=50):
        s = self.a11y_session(session)
        most = int(number(most, "most", 1, 200) or 50)
        args = ["tabwalk", f"--key={key or 'Tab'}", "--max", str(most),
                *(["--visible"] if flag(visible) else []),
                *([f"--window={window}"] if window else [])]
        got = s._a11y(*args, timeout=90 + most * 5)
        seen = [Node(n) for n in got["focus"]]
        lines = [n.line() + (f"  <- focus not shown: {n.changed} pixels changed, "
                             f"{n.needed} needed" if unseen(n) else "") for n in seen]
        lines.append({"cycle": f"Round again after {len(seen)}.",
                      "trap": f"Stuck: {key} leaves the focus where it was.",
                      "max": f"Not round again after {most}."}[got["end"]])
        return "\n".join(lines)

    def announcements(self, session):
        log = Path(self.a11y_session(session).env["SHOTBOX_ANNOUNCEMENTS"])
        said = log.read_text().strip() if log.exists() else ""
        return said or "Nothing announced yet."

    def close(self):
        for name in list(self.sessions):
            try:
                self.stop_session(name)
            except BaseException:   # going anyway: the rest still need stopping
                pass


def schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


SESSION = {"type": "string", "description": "The session's name, as start_session gave it"}
WINDOW = {"type": "string", "description":
          "A window by its title (a regex, matched in full), or app=RE for its app id"}

# What the agent knows of each tool is its description: what it's for, what
# to read in its answer, and what to do instead when it's the wrong one.
TOOLS = [
    ("start_session",
     "Start a sealed session: a display of its own (X11 under Xvfb, or Wayland under a "
     "headless sway), its own D-Bus and accessibility bus, and a scratch home. Nothing in "
     "it reaches the desktop of the person you're working for, and nothing of theirs "
     "reaches it. Start one, spawn a program in it, then look with accessible_tree (cheap, "
     "text) before screenshot (pixels). It lasts until stop_session, or until this server "
     "stops. dark, high_contrast and text_scale (1.25) set the desktop's styles for GTK and "
     "libadwaita; audio gives it a sound server whose sink plays to nowhere.",
     schema({"name": {"type": "string", "description": "What to call it (s1, s2... by default)"},
             "a11y": {"type": "boolean", "description": "An accessibility bus (default true)"},
             "wayland": {"type": "boolean"}, "xwayland": {"type": "boolean"},
             "dark": {"type": "boolean"}, "high_contrast": {"type": "boolean"},
             "text_scale": {"type": "number"}, "audio": {"type": "boolean"},
             "screen": {"type": "string", "description": "WIDTHxHEIGHT (1280x800)"}})),
    ("stop_session", "Stop a session and everything running in it.",
     schema({"session": SESSION}, ["session"])),
    ("spawn",
     "Start one of the programs this server was allowed to run, by its name, in a "
     "session. You can't run anything else: the list is the person's, in the file "
     "shotbox mcp was started with, and start_session's answer says what's on it. Then "
     "wait_for the window before looking at it. If it doesn't come up, program_output "
     "says why.",
     schema({"session": SESSION, "program": {"type": "string"}}, ["session", "program"])),
    ("program_output",
     "Whether a spawned program is still running, and the end of what it printed: for a "
     "window that never came up, or a crash.",
     schema({"session": SESSION, "program": {"type": "string"},
             "lines": {"type": "integer", "description": "How many lines from the end (40)"}},
            ["session", "program"])),
    ("accessible_tree",
     "What a screen reader would find: every visible node, a line each, indented, with its "
     "role, name, description and the states a screen reader says (focusable, focused, "
     "checked...). The cheapest way to see what's on screen and what can be clicked by "
     "name, and exactly what someone who can't see the screen gets. A node with an empty "
     "name (\"\") is one a screen reader can say nothing for. bounds adds where each is on "
     "the screen. Use screenshot for how it looks.",
     schema({"session": SESSION, "window": WINDOW,
             "bounds": {"type": "boolean", "description": "Add @x,y WIDTHxHEIGHT to each line"}},
            ["session"])),
    ("screenshot",
     "A picture of the screen, or of one window, RETURNED AS AN IMAGE for you to look at: "
     "the only way to see colors, layout and what's drawn. Pictures cost far more than the "
     "accessible tree; take the window rather than the screen, and crop when you need a "
     "part. park moves the pointer out of the way first, so nothing shows its hover.",
     schema({"session": SESSION, "window": WINDOW,
             "crop": {"type": "string", "description": "WIDTHxHEIGHT+X+Y, inside what's taken"},
             "park": {"type": "boolean"}}, ["session"])),
    ("click",
     "Click a node by its name, as the accessible tree gives it (a regex, matched in "
     "full; role narrows it, like \"button\" or \"(push )?button\"), or a point by x and "
     "y, on the screen or inside a window. By name is the one to use: it finds the node "
     "wherever it is, raises its window and clicks its middle, and fails saying why when "
     "two nodes match. A click gives a window the keyboard, which nothing else does on "
     "X11.",
     schema({"session": SESSION, "name": {"type": "string"}, "role": {"type": "string"},
             "x": {"type": "integer"}, "y": {"type": "integer"}, "window": WINDOW,
             "button": {"type": "integer", "description": "1 left, 2 middle, 3 right"},
             "double": {"type": "boolean"}}, ["session"])),
    ("press_keys",
     "Press keys in turn, each a chord: Tab, Return, Escape, ctrl+s, alt+shift+Tab, F6. "
     "They go to whatever has the keyboard; click a window first if nothing does.",
     schema({"session": SESSION, "keys": {"type": "array", "items": {"type": "string"}}},
            ["session", "keys"])),
    ("type_text",
     "Type text into whatever has the keyboard focus. ASCII only.",
     schema({"session": SESSION, "text": {"type": "string"}}, ["session", "text"])),
    ("wait_for",
     "Wait, instead of sleeping, for: a window (value: its title, a regex, or app=RE), "
     "stable (the screen, or the window, unchanged for value seconds, 0.5: after a click, "
     "for the repaint), an announcement (value: a regex in what was announced to a screen "
     "reader), or focus (value: a regex for the name of the node with the keyboard focus). "
     "Fails after timeout seconds (10), saying so.",
     schema({"session": SESSION,
             "what": {"type": "string", "enum": ["window", "stable", "announcement", "focus"]},
             "value": {"type": "string"}, "window": WINDOW, "timeout": {"type": "number"}},
            ["session", "what"])),
    ("lint",
     "Nodes a screen reader has no name to say for: controls, images (not inside a named "
     "control) and anything else that takes the focus. A line each: the node's path, then "
     "what's wrong. ignore takes regexes for lines that can't be fixed yet.",
     schema({"session": SESSION, "window": WINDOW,
             "ignore": {"type": "array", "items": {"type": "string"}}}, ["session"])),
    ("contrast",
     "Contrast measured on the screen, in each node's bounds: text, buttons and links "
     "against their background (at least text_ratio, 4.5), images (graphics, 3), and with "
     "edges, a button's or field's edge against what's round it. A line each, with the "
     "colors. Switches, sliders and check boxes are left out, and so is what's scrolled "
     "out of view.",
     schema({"session": SESSION, "window": WINDOW, "text_ratio": {"type": "number"},
             "graphics": {"type": "number"}, "edges": {"type": "number"},
             "ignore": {"type": "array", "items": {"type": "string"}}}, ["session"])),
    ("tab_walk",
     "Press a key (Tab) until the keyboard focus comes back round, and say where it went "
     "each time: the Tab order. It ends in a cycle (fine), a trap (a press that leaves "
     "the focus where it was), or after most presses. visible also checks that each place "
     "it lands shows it has the focus. Leaves the focus where it ends.",
     schema({"session": SESSION, "key": {"type": "string"}, "window": WINDOW,
             "visible": {"type": "boolean"}, "most": {"type": "integer"}}, ["session"])),
    ("announcements",
     "Everything programs have announced to a screen reader in the session, a line each, "
     "\"polite: Saved\": GTK's announcements, a browser's live regions.",
     schema({"session": SESSION}, ["session"])),
]

# Not screenshot (park moves the pointer) or contrast (it raises the window).
READ_ONLY = {"program_output", "accessible_tree", "lint", "announcements"}


def tool_list():
    return [{"name": name, "description": desc, "inputSchema": params,
             "annotations": {"readOnlyHint": name in READ_ONLY}}
            for name, desc, params in TOOLS]


def call(server, name, args):
    """A tool call's result: its content, and isError if it failed."""
    known = {t[0]: t[2] for t in TOOLS}
    if name not in known:
        return {"content": [{"type": "text", "text": f"There's no tool called {name!r}."}],
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
                  "serverInfo": {"name": "shotbox", "version": __version__}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_list()}
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
        for line in inp:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except (ValueError, RecursionError):
                answer = {"jsonrpc": "2.0", "id": None,
                          "error": {"code": -32700, "message": "not JSON"}}
            else:
                try:
                    answer = handle(server, msg)
                except Exception as e:   # one request gone wrong isn't the server gone
                    answer = {"jsonrpc": "2.0",
                              "id": msg.get("id") if isinstance(msg, dict) else None,
                              "error": {"code": -32603, "message": repr(e)}}
            if answer is not None:
                out.write(json.dumps(answer) + "\n")
                out.flush()
    finally:
        server.close()
    return 0
