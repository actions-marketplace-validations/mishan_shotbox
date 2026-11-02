"""What a screen reader says, from a real Orca in the session, as keys
move the focus:

    shotbox a11y speak [--keys KEY...] [--window RE]

Orca is started in the session for the call and stopped after it, with
a home of its own in the session's scratch dir, so it starts from its
defaults and nothing of the person's is read or written. What it says is
read from its debug log, which has a line for each thing it says.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from . import a11y, x
from .screen import Screen, SessionError, wait_for

# An entry of Orca's debug log for each thing it says: 51's is
# "21:10:21.039 - SPEECH OUTPUT: 'Save button'" and its voice, a dict, and
# 46 to 50's put a space before the voice, or None. What's said can run
# over lines, as the voice can, up to the next entry's time, each line
# after the first indented to line up with the first's text. The log is
# Orca's business, not an interface, so this entry is all that's read of it.
ENTRY = re.compile(r"\n(?=\d\d:\d\d:\d\d\.\d+ - )")
SAID = re.compile(r"(?:[\d:.]+ - )?SPEECH OUTPUT: '(.*?)'(?: ?(?:\{.*\}|None))?\s*", re.S)

# What Orca runs with, in a home of its own. A pgrep that finds no other
# Orca: Orca won't start while any of this user's runs, the person's own
# included (and with --replace kills them all: it's never passed). Its
# name, shotbox-orca rather than orca, so the person's Orca, or GNOME's
# `orca --replace`, doesn't find it in turn: it's run by its interpreter,
# not as orca, and names itself through a stand-in setproctitle.
# speech-dispatcher's, Spiel's and BrlAPI's modules, taken out as Python
# starts (Orca's launcher puts the system's modules before PYTHONPATH's),
# so it says nothing out loud nor on a braille display, and logs what it
# would have said all the same. No key echo, which says each key before
# what it moved the focus to: in GSettings' keyfile from Orca 50, in its
# settings module before. And its log written a line at a time, as 51
# does: before, it's a block at a time.
FILES = {
    "bin/pgrep": "#!/bin/sh\nexit 1\n",
    "python/sitecustomize.py": """import ctypes, sys, types
sys.modules.update(dict.fromkeys(('speechd', 'brlapi', 'gi.repository.Spiel')))
title = sys.modules['setproctitle'] = types.ModuleType('setproctitle')
title.setproctitle = lambda _: ctypes.CDLL(None).prctl(15, b'shotbox-orca', 0, 0, 0)
""",
    "config/glib-2.0/settings/keyfile": "[org/gnome/orca/default/typing-echo]\nkey-echo=false\n",
    "data/orca/orca-customizations.py": """from orca import debug
debug.debugFile.reconfigure(line_buffering=True)
try:
    from orca import settings
    settings.enableKeyEcho = False
except ImportError:
    pass
""",
}

# How long Orca's log must stay still for it to be done with a press: it
# writes a line for each event it handles.
QUIET = 0.5


class Orca:
    """Orca, running in the session with its own home, until close()."""

    def __init__(self, timeout=60):
        orca = shutil.which("orca")
        if not orca:
            raise SessionError("a11y speak needs Orca: it's in orca on Debian and Fedora")
        with open(orca, "rb") as f:
            first = f.readline()
        run = first[2:].decode().split() if first.startswith(b"#!") else []
        args = run[1:]
        if run and Path(run[0]).name == "env":
            args = [a for a in args if a != "-S"][1:]
        # -I, -E, -s and -S leave out PYTHONPATH or site, and with them
        # sitecustomize: Orca would speak, and be named orca.
        if any(a.startswith("-") and not a.startswith("--") and set(a[1:]) & set("IEsS")
               for a in args):
            raise SessionError(f"a11y speak won't start Orca: its interpreter, {' '.join(run)}, "
                               "ignores PYTHONPATH or site, which keep it from speaking out "
                               "loud and from being taken for the desktop's Orca")
        scratch = os.environ["SHOTBOX_SCRATCH"]
        if not Path(os.environ.get("HOME", "/")).resolve().is_relative_to(Path(scratch).resolve()):
            raise SessionError("a11y speak runs only in a session's own home")
        self.home = Path(tempfile.mkdtemp(prefix="orca-", dir=scratch))
        for name, text in FILES.items():
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        (self.home / "bin/pgrep").chmod(0o755)
        self.log = self.home / "debug.out"
        env = dict(os.environ, XDG_CONFIG_HOME=str(self.home / "config"),
                   XDG_DATA_HOME=str(self.home / "data"), GSETTINGS_BACKEND="keyfile",
                   PATH=f"{self.home / 'bin'}:{os.environ.get('PATH', '')}",
                   PYTHONPATH=":".join(filter(None, (str(self.home / "python"),
                                                     os.environ.get("PYTHONPATH")))))
        self.proc = subprocess.Popen([*run, orca, "--debug-file", str(self.log)], env=env,
                                     cwd=self.home, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL,
                                     stderr=open(self.home / "orca.err", "wb"),
                                     start_new_session=True, preexec_fn=x.tied(signal.SIGKILL))
        try:
            # It's ready once it says it's on, and done saying what's focused.
            wait_for("Orca to start", lambda: self.said(0), timeout, alive=self.proc)
            self.started = self.settle(0)
        except SessionError as e:
            err = (self.home / "orca.err").read_text(errors="replace").strip().splitlines()
            self.close()
            raise SessionError(f"{e}{': ' + err[-1] if err else ''}") from None

    def size(self):
        return self.log.stat().st_size if self.log.exists() else 0

    def said(self, start):
        """What Orca has said since `start`, an offset in its log."""
        if not self.log.exists():
            return []
        with open(self.log, "rb") as f:
            f.seek(start)
            text = f.read().decode(errors="replace")
        return [m[1].replace("\n" + " " * 18, "\n")
                for m in map(SAID.fullmatch, ENTRY.split(text)) if m]

    def settle(self, start, most=10):
        """What Orca has said since `start`, once its log has been still
        for QUIET, or after `most` seconds."""
        end, last, still = time.monotonic() + most, self.size(), time.monotonic()
        while time.monotonic() < end and time.monotonic() - still < QUIET:
            time.sleep(0.05)
            if self.proc.poll() is not None:
                raise SessionError(f"Orca exited ({self.proc.returncode})")
            if self.size() != last:
                last, still = self.size(), time.monotonic()
        return self.said(start)

    def close(self):
        # Orca shuts down on SIGTERM; killing its group takes what it started too.
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                pass
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.proc.wait()
        shutil.rmtree(self.home, ignore_errors=True)


def speak(keys=None, most=50, window=None, timeout=10, settle=2.0):
    """With Orca running, press `keys` in turn, or Tab until the focus lands
    where it has been (where it began, or a stop since) or `most` times,
    but not in a text view one writes in, which takes Tab. {"start": what
    has the focus as Orca starts and what it says, "presses": after each
    press, the key, where the focus is once it has left where it was (or
    after `settle`), whether it moved, and what Orca said, "text_view":
    if Tab stopped at one, the key that leaves it, else None}; the focus a
    Node, or None."""
    Atspi = a11y.atspi()
    screen = Screen()
    for k in keys or ():
        screen.xt.chord_keys(k)
    a11y.toplevels(Atspi, window, timeout)
    if not window and a11y.QUIET and a11y.focused(Atspi) is None:
        screen.close()
        raise SessionError("no node has the focus to begin with")
    orca = Orca()
    try:
        before = a11y.focused(Atspi, window)
        got = {"start": {"focus": before, "speech": orca.started}, "presses": [],
               "text_view": None}
        seen = {before._id} if before else set()
        for k in keys or ["Tab"] * most:
            if not keys and a11y.writing(Atspi, before):
                # As tabwalk tries them: GTK's way out, and Qt's (GTK's types a tab).
                qt = a11y.toolkit(before).startswith("Qt")
                got["text_view"] = "shift+Tab" if qt else "ctrl+Tab"
                break
            at = orca.size()
            screen.key(k)
            now = left(Atspi, window, before, settle)
            moved = now is not None and (before is None or now._id != before._id)
            got["presses"].append({"key": k, "focus": now, "moved": moved,
                                   "speech": orca.settle(at)})
            if not keys and (now is None or now._id in seen):
                break
            if now:
                seen.add(now._id)
            before = now
    finally:
        orca.close()
        screen.close()
    return got


def left(Atspi, window, before, settle):
    """The node with the focus once it's on one other than `before`, or
    after `settle` seconds, wherever it is: a slow program says where it
    went late, and Orca only once it does."""
    end = time.monotonic() + settle
    while True:
        now = a11y.focused(Atspi, window)
        if (now is not None and (before is None or now._id != before._id)
                or time.monotonic() > end):
            return now
        time.sleep(0.05)


def lines(got):
    """What speak() found, a line each, as `shotbox a11y speak` says it, the
    start first: the focus's tree line, then what was said, each quoted."""
    def said(p):
        where = p["focus"].line() if p["focus"] else "(no node has the focus)"
        stayed = "  (the focus didn't move)" if p.get("moved") is False else ""
        return (where + stayed + "  says "
                + (", ".join(json.dumps(s, ensure_ascii=False) for s in p["speech"])
                   or "nothing"))
    out = ["(start) " + said(got["start"])] + [said(p) for p in got["presses"]]
    if got["text_view"]:
        out.append("stopped before Tab in a text view, which would type it: "
                   f"--keys {got['text_view']} leaves it")
    return out


def main(argv):
    p = argparse.ArgumentParser(prog="shotbox a11y speak",
                                description="Inside `shotbox run --a11y`: start Orca, press "
                                            "keys, and say what it said after each, with "
                                            "what has the focus, a line each.")
    p.add_argument("--keys", nargs="+", metavar="CHORD",
                   help="press these, in turn (Tab until the focus comes round)")
    p.add_argument("--max", type=int, default=50, metavar="N",
                   help="without --keys, Tab at most this many times (50)")
    p.add_argument("--window", metavar="RE",
                   help="the focus in just this window (its title, a regex matched in full, "
                        "or app=RE for its app id), waiting for it")
    p.add_argument("--timeout", type=float, default=10, metavar="SECS",
                   help="how long to wait for the --window (10), and for a program to answer")
    p.add_argument("--json", action="store_true", help="as JSON, for scripts")
    a = p.parse_args(argv)
    a11y.patient(a.timeout)
    presses = len(a.keys) if a.keys else a.max
    # Orca's start, then a press: its settling time, and a tree.
    a11y.watchdog(120 + a.timeout + presses * 15, "a11y speak")
    got = speak(a.keys, a.max, a.window, a.timeout)
    if a.json:
        for p in [got["start"], *got["presses"]]:
            p["focus"] = p["focus"] and a11y.public(p["focus"])
        print(json.dumps(got, ensure_ascii=False))
    else:
        print("\n".join(lines(got)))
    return 0
