"""A session's display, from Python: waiting on it, driving it, and taking
pictures of it. What the `shotbox wait`, `capture`, `key`, `click` and
friends commands do, without a process for each step.

Inside `shotbox run`, the display is the one in the environment:

    import shotbox
    screen = shotbox.here()
    screen.wait_window("Text Editor")
    screen.click(40, 60, window="Text Editor")
    screen.wait_stable(window="Text Editor")
    screen.capture("editor.png", window="Text Editor", park=True)

A Session is a Screen too, for a script that starts its own.

Anything that fails raises SessionError, saying what. With `failed` set (or
$SHOTBOX_FAILED), a failure first leaves a picture of the screen there.
"""

import fcntl
import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import wl, wlinput, x, xtest


class SessionError(RuntimeError):
    pass


def wait_for(what, test, timeout, interval=0.1, alive=None):
    """Poll `test()` until it returns something true, or fail saying what."""
    end = time.monotonic() + timeout
    while True:
        got = test()
        if got:
            return got
        if alive is not None and alive.poll() is not None:
            raise SessionError(f"gave up waiting for {what}: the program exited "
                               f"(status {alive.returncode})")
        if time.monotonic() > end:
            raise SessionError(f"gave up waiting for {what} after {timeout:g}s")
        time.sleep(interval)


class Screen:
    """The display in `env` (DISPLAY, XAUTHORITY and SHOTBOX_SCRATCH; the
    process's own environment by default)."""

    def __init__(self, env=None, failed=None):
        self.env = dict(os.environ if env is None else env)
        self.failed = failed or self.env.get("SHOTBOX_FAILED")
        self._x = None

    # --- failing --------------------------------------------------------------

    def fail(self, message):
        """Raise SessionError(message), first taking a picture of the screen
        to `failed` if it's set, and saying where."""
        if self.failed and x.try_capture(self.env, self.failed, self.backend):
            message += f"; the screen then: {self.failed}"
        raise SessionError(message)

    def until(self, what, test, timeout=30, alive=None):
        """Poll `test()` until it returns something true, and return that;
        or fail, saying it gave up waiting for `what`. With `alive` (a
        process), fail sooner if it exits."""
        try:
            return wait_for(what, test, timeout, alive=alive)
        except SessionError as e:
            self.fail(str(e))

    # --- waiting --------------------------------------------------------------

    def test(self, kind, arg="", window=None):
        """What to say, and what to poll, for a wait: window (a regex for
        its title, or app=RE for its app id), port,
        file, ready, or stable (seconds; `window` to watch just that one)."""
        if kind == "window":
            return f"a {x.matcher(arg)[1]}", lambda: self.backend.find_window(self.env, arg)
        if kind == "port":
            return f"port {arg}", lambda: x.port_open(int(arg))
        if kind == "file":
            return f"{arg} to exist", lambda: Path(arg).exists()
        if kind == "ready":
            ready = Path(self.env["SHOTBOX_SCRATCH"]) / "ready"
            return "the program to say it's ready", ready.exists
        if kind == "announce":
            # Each wait takes the first match no wait has taken, so the same
            # thing said twice can be waited for twice. The lines taken, a
            # number a line, under a lock: waits may run side by side.
            taken = Path(self.env["SHOTBOX_SCRATCH"]) / "announcements-taken"

            def said():
                log = self.env.get("SHOTBOX_ANNOUNCEMENTS")
                if not log:
                    self.fail("no announcements: start the session with --a11y")
                if not Path(log).exists():
                    return False
                lines = Path(log).read_text().splitlines()
                with open(taken, "a+") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    f.seek(0)
                    gone = {int(n) for n in f.read().split()}
                    for i, line in enumerate(lines):
                        if i not in gone and re.search(arg, line.partition(": ")[2]):
                            f.write(f"{i}\n")
                            return True
                return False
            return f"an announcement matching {arg!r}", said
        if kind == "stable":
            quiet = float(arg) if arg else 0.5
            where = f"the window {window!r}" if window else "the display"
            return (f"{where} to hold still for {quiet:g}s",
                    x.Still(self.env, window, quiet, self.backend))
        raise SessionError(f"don't know how to wait for {kind!r} "
                           "(window, port, file, ready, stable or announce)")

    def wait_window(self, name, timeout=30):
        """Wait for a viewable window whose title matches `name` (a regex,
        matched in full), or, with app=RE, whose app id does; its (id, name,
        width, height, x, y)."""
        return self.until(*self.test("window", name), timeout)

    def wait_port(self, port, timeout=30):
        self.until(*self.test("port", port), timeout)

    def wait_file(self, path, timeout=30):
        self.until(*self.test("file", path), timeout)

    def wait_ready(self, timeout=30):
        """Wait for $SHOTBOX_SCRATCH/ready, which `shotbox term` writes."""
        self.until(*self.test("ready"), timeout)

    def wait_stable(self, seconds=0.5, window=None, timeout=30):
        """Wait until the display, or the window named `window`, has looked
        the same for `seconds`: for the paint after a click, a panel sliding
        in, a toast going away."""
        self.until(*self.test("stable", seconds, window), timeout)

    def wait_announce(self, pattern, timeout=30):
        """Wait for a program to have announced something matching
        `pattern` (a regex, found anywhere in it) to a screen reader."""
        self.until(*self.test("announce", pattern), timeout)

    # --- the accessible tree ----------------------------------------------------

    def _a11y(self, *args, timeout=90):
        """`shotbox a11y ARGS --json` in the session, for its answer: libatspi
        keeps the first bus it finds for the life of a process, which may
        not be this session's. It has a watchdog of its own; `timeout` is
        for one that never even gets that far."""
        from .__main__ import self_command
        try:
            r = subprocess.run([*self_command(), "a11y", *args, "--json"], env=self.env,
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            self.fail(f"shotbox a11y {args[0]} got no answer in {timeout}s")
        if r.returncode:
            self.fail(r.stderr.strip().removeprefix("shotbox: ")
                      or f"shotbox a11y {args[0]} failed")
        return json.loads(r.stdout)

    def a11y_tree(self, window=None):
        """Every node a screen reader sees, depth first: dicts (and
        attributes) of role, name, description, states, path, depth, and
        bounds on the screen, [x, y, width, height], or None where they
        can't be told."""
        from .a11y import Node
        return [Node(n) for n in self._a11y("tree", *(["--window", window] if window else []))]

    def find(self, role=None, name=None, window=None):
        """The nodes with this role ("button") and a name matching `name`
        (a regex, matched in full)."""
        return [n for n in self.a11y_tree(window)
                if (role is None or n.role == role)
                and (name is None or re.fullmatch(name, n.name))]

    def focused(self, window=None):
        """The node with the keyboard focus, or None."""
        return next((n for n in self.a11y_tree(window) if "focused" in n.states), None)

    def wait_focus(self, name, timeout=30, window=None):
        """Wait for the keyboard focus to be on a node whose name matches
        `name` (a regex, matched in full); that node."""
        def there():
            n = self.focused(window)
            return n if n and re.fullmatch(name, n.name) else None
        return self.until(f"the focus on {name!r}", there, timeout)

    def a11y_lint(self, window=None, ignore=()):
        """The nodes a screen reader would have no name to say for, each
        with what's wrong as `problem`; but those whose "PATH: PROBLEM"
        line one of the regexes in `ignore` is found in."""
        from .a11y import Node
        args = [a for i in ignore for a in ("--ignore", i)]
        return [Node(n) for n in self._a11y("lint", *args, *(["--window", window] if window else []))]

    def contrast(self, window=None, text=4.5, graphics=3.0, edges=None):
        """The nodes drawn with too little contrast, each with what's wrong
        as `problem`: text against its background (at least `text`), an
        icon (`graphics`), and with `edges`, a button's or field's edge
        against what's round it. Measured on a picture of the screen."""
        from .a11y import Node
        args = ["--text", str(text), "--graphics", str(graphics),
                *(["--edges", str(edges)] if edges else []),
                *(["--window", window] if window else [])]
        return [Node(n) for n in self._a11y("contrast", *args)]

    def click_node(self, name, role=None, window=None, button=1, double=False, timeout=10):
        """Click the middle of the node whose name matches `name` (a regex,
        matched in full), and role `role` if given (a regex too), waiting
        up to `timeout` for it to show; that node."""
        from .a11y import Node
        args = [name, "--button", str(button), "--timeout", str(timeout),
                *(["--role", role] if role else []), *(["--double"] if double else []),
                *(["--window", window] if window else [])]
        return Node(self._a11y("click", *args, timeout=90 + timeout))

    def tab_walk(self, key="Tab", most=50, window=None, visible=False):
        """Press `key` until the focus lands somewhere it has been: the node
        it lands on after each press, the last being that one. Fails if a
        press leaves it where it was (unless it's the one thing that could
        take it), or after `most` presses; with `visible`, if the focus
        doesn't show on a node it lands on."""
        from .a11y import Node, unseen
        args = ["tabwalk", "--key", key, "--max", str(most), *(["--visible"] if visible else [])]
        got = self._a11y(*args, *(["--window", window] if window else []),
                         timeout=90 + most * 5)
        seen = [Node(n) for n in got["focus"]]
        if got["end"] == "trap":
            where = seen[-1].line() if seen else "nothing"
            self.fail(f"{key} leaves the focus where it was, on {where}")
        if got["end"] == "max":
            self.fail(f"the focus isn't round again after {most} presses of {key}")
        hidden = [n for n in seen if unseen(n)]
        if hidden:
            self.fail("the focus doesn't show on " + "; ".join(n.line() for n in hidden))
        return seen

    # --- windows and pictures -------------------------------------------------

    def window(self, name):
        """The window `name` asks for (a title regex, or app=RE), or fail."""
        w = self.backend.find_window(self.env, name)
        if not w:
            self.fail(f"no {x.matcher(name)[1]}")
        return w

    def focus_window(self, name):
        """Give the window `name` asks for the keyboard, and raise it. With
        no window manager on X11, a window that comes up after another
        (a dialog) gets neither by itself."""
        self.backend.focus(self.env, self.window(name))

    def capture(self, out, window=None, crop=None, park=False):
        """A picture of the display, or of the window named `window`, to
        `out` (a PNG), then cropped (WxH+X+Y). With `park`, the pointer
        goes out of the way first, so nothing shows its hover."""
        if park:
            self.park()
        self.backend.capture(self.env, out, window=self.window(window) if window else None,
                             crop=crop)
        return Path(out)

    # --- input ----------------------------------------------------------------

    @property
    def wayland(self):
        """Whether this is a Wayland session (under sway) rather than X11."""
        return bool(self.env.get("SWAYSOCK"))

    @property
    def backend(self):
        """The module that finds windows and takes pictures: wl or x."""
        return wl if self.wayland else x

    @property
    def xt(self):
        """The input connection, opened on first use and kept: XTEST on
        X11, a virtual keyboard and pointer on Wayland."""
        if self._x is None:
            self._x = (wlinput if self.wayland else xtest).Display(self.env)
        return self._x

    def _at(self, px, py, window):
        if not window:
            return px, py
        w = self.window(window)
        return w[4] + px, w[5] + py

    def key(self, *chords):
        """Press and release each chord in turn: ctrl+comma, Return,
        alt+shift+Tab."""
        for c in chords:
            self.xt.chord(c)

    def type(self, text):
        """Type ASCII text into whatever has the keyboard focus."""
        self.xt.type(text)

    def move(self, px, py, window=None):
        """Move the pointer to X, Y on the screen, or inside `window`."""
        self.xt.move(*self._at(px, py, window))

    def click(self, px, py, window=None, button=1, double=False):
        self.xt.click(*self._at(px, py, window), button=button, count=2 if double else 1)

    def drag(self, x1, y1, x2, y2, window=None, button=1):
        dx, dy = self._at(0, 0, window)
        self.xt.drag(x1 + dx, y1 + dy, x2 + dx, y2 + dy, button=button)

    def park(self, settle=2.0):
        """Move the pointer to the screen's bottom-right corner, off whatever
        it was hovering, then wait up to `settle` seconds for the display to
        hold still, for the repaint. A display that keeps changing (a video,
        a spinner) isn't a failure here: it's taken as it is."""
        self.xt.move(self.xt.width - 1, self.xt.height - 1)
        if settle:
            try:
                wait_for("", x.Still(self.env, quiet=0.3, backend=self.backend), settle)
            except SessionError:
                pass

    def close(self):
        if self._x is not None:
            self._x.close()
            self._x = None


def here():
    """The display of the `shotbox run` this is inside."""
    if not os.environ.get("SHOTBOX_SCRATCH"):
        raise SessionError("not inside a shotbox session")
    return Screen()
