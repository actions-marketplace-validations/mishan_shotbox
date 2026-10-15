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

import contextlib
import fcntl
import json
import os
import re
import subprocess
import tempfile
import time
import zlib
from pathlib import Path

from . import pad, wl, wlinput, x, xtest


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
        shown = self.env.get("DISPLAY") or self.env.get("WAYLAND_DISPLAY")
        if self.failed and shown and x.try_capture(self.env, self.failed, self.backend):
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
            return f"port {arg}", lambda: x.port_open(int(arg),
                                                      net_pid=self.env.get("SHOTBOX_NET_PID"))
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

    def wait_line(self, pattern, path, from_start=False, timeout=30, within=None):
        """Wait for a line of the file at `path` that matches `pattern` (a
        regex, found anywhere in it), written after the wait starts, or with
        `from_start`, anywhere in the file: for a program that logs what
        happens, a line an event, where its clock isn't the wall's. A file
        not there yet is waited for, and one replaced (rotated, or written
        again from the top) is read from its start; lines written to a
        rotated file just before it went may be missed. A line counts once
        it ends. With `within`, a directory, a file that's really outside it
        (by a symlink) is refused. The line."""
        found, chunk = re.compile(pattern), 1 << 20
        # `seen`: the bytes just before `pos`, to tell the file written again.
        ino, pos, seen, rest, old = None, 0, b"", b"", False
        if not from_start:
            with contextlib.suppress(OSError), open(path, "rb") as f:
                # From its last byte: what ends the line that's in is old.
                end = f.seek(0, os.SEEK_END)
                pos, old = max(end - 1, 0), end > 0
                f.seek(max(pos - 64, 0))
                ino, seen = os.fstat(f.fileno()).st_ino, f.read(pos - f.tell())

        def line():
            nonlocal ino, pos, seen, rest, old
            try:
                with open(path, "rb") as f:
                    real = os.readlink(f"/proc/self/fd/{f.fileno()}")
                    if within and not Path(real).is_relative_to(within):
                        raise SessionError(f"{path} leads outside {within}")
                    st = os.fstat(f.fileno())
                    f.seek(pos - len(seen))
                    got = f.read(len(seen) + chunk)
                    if st.st_ino != ino or not got.startswith(seen):
                        ino, pos, seen, rest, old = st.st_ino, 0, b"", b"", False
                        f.seek(0)
                        got = f.read(chunk)
                    else:
                        got = got[len(seen):]
            except FileNotFoundError:
                return None
            except OSError as e:
                raise SessionError(f"can't read {path}: {e.strerror}") from None
            pos += len(got)
            seen = (seen + got)[-64:]
            *lines, rest = (rest + got).split(b"\n")
            if lines:
                lines, old = lines[old:], False
            if len(rest) > chunk:
                # Not kept growing: what's left of a line this long is dropped.
                rest, old = b"", True
            for got in lines:
                got = got.decode(errors="replace").removesuffix("\r")
                if found.search(got):
                    return [got]   # in a list: an empty line can match too
            return None
        return self.until(f"a line matching {pattern!r} in {path}", line, timeout)[0]

    def wait_ready(self, timeout=30):
        """Wait for $SHOTBOX_SCRATCH/ready, which `shotbox term` writes."""
        self.until(*self.test("ready"), timeout)

    def wait_stable(self, seconds=0.5, window=None, timeout=30):
        """Wait until the display, or the window named `window`, has looked
        the same for `seconds`: for the paint after a click, a panel sliding
        in, a toast going away."""
        self.until(*self.test("stable", seconds, window), timeout)

    def wait_match(self, ref, window=None, crop=None, fuzz="0%", most=0, timeout=30):
        """Wait until the display, or the window named `window`, cropped
        (WxH+X+Y), looks like the picture `ref`: `most` pixels or fewer
        differ by more than `fuzz`, as `shotbox compare` counts. For a screen with
        no other sign it's up: a boot, a login screen. The seconds it took."""
        # png: so a name like msl:FILE is a file, not ImageMagick's coder.
        try:
            x.size_of(f"png:{ref}")
        except subprocess.CalledProcessError:
            self.fail(f"can't read {ref} as a PNG")
        return self._wait_looks(f"png:{ref}", ref, window, crop, fuzz, most, timeout)

    def wait_change(self, window=None, crop=None, fuzz="0%", most=0, timeout=30):
        """Wait until the display, or the window named `window`, cropped,
        looks different from the first picture the wait takes: more than
        `most` pixels differ by more than `fuzz`. The seconds it took."""
        return self._wait_looks(None, None, window, crop, fuzz, most, timeout)

    def _wait_looks(self, ref, name, window, crop, fuzz, most, timeout):
        """wait_match against `ref` (`name` to say), or with None,
        wait_change: the seconds from the start of the wait to the picture
        it passed on."""
        box = crop and re.fullmatch(r"(\d+)x(\d+)\+(\d+)\+(\d+)", crop)
        if crop and not (box and int(box[1]) and int(box[2])):
            self.fail(f"a crop is WIDTHxHEIGHT+X+Y, at least 1x1, not {crop}")
        box = box and [int(n) for n in box.groups()]
        if not (re.fullmatch(r"(\d+\.?\d*|\.\d+)%", fuzz) and float(fuzz[:-1]) <= 100):
            self.fail(f"fuzz is a percentage, 0% to 100%, not {fuzz!r}")
        if most < 0:
            self.fail(f"the pixels that may differ are 0 or more, not {most}")
        match = ref is not None
        fd, now = tempfile.mkstemp(".png", dir=self.env["SHOTBOX_SCRATCH"])
        os.close(fd)
        now = Path(now)

        def picture(w=None, area=None):
            """(width, height, RGB bytes), in memory where the backend can,
            else through a PNG; or None, a window gone between finding it
            and taking it."""
            got = self.backend.grab(self.env, w, area)
            if got:
                return got
            geometry = area and "{}x{}+{}+{}".format(*area)
            try:
                self.backend.capture(self.env, now, window=w, crop=geometry)
            except subprocess.CalledProcessError:
                return None
            return x.rgb(now)

        start, at, sizes, small = time.monotonic(), None, None, None
        ref = x.rgb(ref) if match else None
        fuzz = float(fuzz[:-1])
        # The display's size doesn't change: a crop off it never fits. A
        # window's may, still coming up, so one it doesn't fit is waited on.
        if box and not window:
            wide, high, _ = picture()
            if box[0] + box[2] > wide or box[1] + box[3] > high:
                now.unlink()
                self.fail(f"the crop {crop} isn't inside the {wide}x{high} display")

        def looks():
            nonlocal ref, at, sizes, small
            w = None
            if window:
                w = self.backend.find_window(self.env, window)
                if not w:
                    return False
                if box and (box[0] + box[2] > w[2] or box[1] + box[3] > w[3]):
                    small = f"{w[2]}x{w[3]}"
                    return False
            t = time.monotonic()
            got = picture(w, box)
            if got is None:
                return False
            if ref is None:
                ref = got
                return False
            if got[:2] == ref[:2]:
                n = x.differ(ref[2], got[2], fuzz, most)
            else:
                n, sizes = None, ("{}x{}".format(*ref[:2]), "{}x{}".format(*got[:2]))
            if (n is not None and n <= most) == match:
                at = t - start
                return True
            return False

        where = f"the window {window!r}" if window else "the display"
        where += f", cropped to {crop}," if crop else ""
        what = f"{where} to match {name}" if match else f"{where} to change"
        try:
            wait_for(what, looks, timeout, interval=0.01)
        except SessionError as e:
            if small:
                e = f"{e}; the crop isn't inside the window, {small}"
            elif match and sizes:
                e = f"{e}; {name} is {sizes[0]}, the picture taken {sizes[1]}"
            self.fail(str(e))
        finally:
            now.unlink(missing_ok=True)
        return at

    def flicker(self, seconds=2.0, window=None, crop=None, tile=16, brief=0.1, times=4,
                fuzz=6.0, ignore=()):
        """Watch the display, or the window named `window`, cropped, for
        `seconds`, as fast as pictures come, for flicker and flashes (see
        flicker.py): tiles `tile` pixels square, a state no longer than
        `brief` seconds brief, `times` changes or more flicker, looks
        within `fuzz` percent the same, and boxes (WxH+X+Y) in `ignore`
        left out. [dict(kind="flicker" or "flash", box, at, changes,
        every, looks)], a box of tiles each, in order."""
        from . import flicker
        w = self.window(window) if window else None
        geometry = r"(\d+)x(\d+)\+(\d+)\+(\d+)"
        area = None
        if crop:
            m = re.fullmatch(geometry, crop)
            if not (m and int(m[1]) and int(m[2])):
                self.fail(f"a crop is WIDTHxHEIGHT+X+Y, at least 1x1, not {crop}")
            area = tuple(int(n) for n in m.groups())
        skip = []
        for g in ignore:
            m = re.fullmatch(geometry, g)
            if not m:
                self.fail(f"what to ignore is WIDTHxHEIGHT+X+Y, not {g}")
            skip.append(tuple(int(n) for n in m.groups()))
        fd, png = tempfile.mkstemp(".png", dir=self.env["SHOTBOX_SCRATCH"])
        os.close(fd)

        def picture():
            """In memory where the backend can; else through a PNG, as a
            window partly off the screen is."""
            got = self.backend.grab(self.env, w, area)
            if got:
                return got
            try:
                self.backend.capture(self.env, png, window=w,
                                     crop=area and "{}x{}+{}+{}".format(*area))
                got = x.rgb(png)
            except subprocess.CalledProcessError:
                self.fail("can't take pictures of " + (f"the window {window!r}" if window
                                                       else "the display"))
            if area and got[:2] != area[:2]:
                self.fail(f"the crop {crop} isn't inside what's watched, {got[0]}x{got[1]}")
            return got

        # Every picture that differs from the one before, compressed: the
        # program draws at its own rate, and what holds is said by time.
        shots, last, start = [], None, time.monotonic()
        try:
            while time.monotonic() - start < seconds:
                t0 = time.monotonic()
                got = picture()
                if got[2] != last:
                    shots.append(((t0 + time.monotonic()) / 2 - start, zlib.compress(got[2], 1)))
                    last = got[2]
        finally:
            Path(png).unlink(missing_ok=True)
        width, height = got[:2]
        times_ = [t for t, _ in shots]
        across = -(-width // tile)
        looks = [flicker.means(zlib.decompress(data), width, height, tile) for _, data in shots]
        # A tile's states, by its mean colors, a change of a quarter of fuzz
        # a new one, so a band a pixel or two wide is seen; the pixels decide.
        helds, wanted = {}, {}
        for n in range(len(looks[0])):
            tx, ty = n % across * tile, n // across * tile
            if any(gx < tx + tile and tx < gx + gw and gy < ty + tile and ty < gy + gh
                   for gw, gh, gx, gy in skip):
                continue
            helds[n] = flicker.states(times_, [frame[n] for frame in looks], seconds,
                                      fuzz * 2.55 / 4)
            for k in flicker.needs(helds[n], brief):
                wanted.setdefault(k, []).append(n)
        # Each picture a tile is looked at in, decompressed once, for those.
        tiles_in = {}
        for k in sorted(wanted):
            f = zlib.decompress(shots[k][1])
            for n in wanted[k]:
                tx, ty = n % across * tile, n // across * tile
                x1, y1 = min(tx + tile, width), min(ty + tile, height)
                tiles_in[n, k] = b"".join(f[(y * width + tx) * 3:(y * width + x1) * 3]
                                          for y in range(ty, y1))
        found = {"flicker": {}, "flash": {}}
        for n, held in helds.items():
            flickering, flashed = flicker.find(held, brief, times, fuzz,
                                               lambda k, n=n: tiles_in[n, k])
            if flickering:
                found["flicker"][n] = flickering
            elif flashed:
                found["flash"][n] = flashed
        out = []
        for bx, by, bw, bh, group in flicker.boxes(found["flicker"], across, tile):
            bw, bh = min(bw, width - bx), min(bh, height - by)    # tiles at the edge
            # The box said as its tile that changed most does.
            at, changes, every, a, b = max((found["flicker"][n] for n in group),
                                           key=lambda f: f[1])
            out.append(dict(kind="flicker", box=f"{bw}x{bh}+{bx}+{by}", at=at, changes=changes,
                            every=every, looks=(flicker.hex_of(a), flicker.hex_of(b))))
        flashes = found["flash"]
        for k in sorted({f[4] for f in flashes.values()}):
            # Tiles that flashed in the same picture make a box. A flash
            # leaves the tiles round it as they were; something moving
            # through changes one beside it, a picture or three before or
            # after.
            now = {n: f for n, f in flashes.items() if f[4] == k}
            for bx, by, bw, bh, group in flicker.boxes(now, across, tile):
                near = {m + dx + dy * across for m in group for dx in (-1, 0, 1)
                        for dy in (-1, 0, 1)} - set(group)
                if any(abs(st[3] - k) <= 3 for m in near if m in helds
                       for st in helds[m][1:]):
                    continue
                bw, bh = min(bw, width - bx), min(bh, height - by)
                at, held_for, look, then, _ = now[group[0]]
                out.append(dict(kind="flash", box=f"{bw}x{bh}+{bx}+{by}", at=at, changes=2,
                                every=held_for,
                                looks=(flicker.hex_of(then), flicker.hex_of(look))))
        return sorted(out, key=lambda f: f["at"])

    def wait_announce(self, pattern, timeout=30):
        """Wait for a program to have announced something matching
        `pattern` (a regex, found anywhere in it) to a screen reader."""
        self.until(*self.test("announce", pattern), timeout)

    # --- the accessible tree ----------------------------------------------------

    def _a11y(self, *args, timeout=90):
        """`shotbox a11y WHAT --json ARGS` in the session, for its answer: libatspi
        keeps the first bus it finds for the life of a process, which may
        not be this session's. It has a watchdog of its own; `timeout` is
        for one that never even gets that far."""
        from .__main__ import self_command
        try:
            # Options as --name=VALUE, and a name after --: an agent's regex can
            # start with a dash.
            r = subprocess.run([*self_command(), "a11y", args[0], "--json", *args[1:]],
                               env=self.env, capture_output=True, text=True, timeout=timeout)
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
        return [Node(n) for n in self._a11y("tree", *([f"--window={window}"] if window else []))]

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
        args = [f"--ignore={i}" for i in ignore]
        return [Node(n) for n in self._a11y("lint", *args, *([f"--window={window}"] if window else []))]

    def contrast(self, window=None, text=4.5, graphics=3.0, edges=None, ignore=()):
        """The nodes drawn with too little contrast, each with what's wrong
        as `problem`: text, buttons and links against their background (at
        least `text`), images (`graphics`), and with `edges`, a button's or field's edge against
        what's round it; but those whose "PATH: PROBLEM" line one of the
        regexes in `ignore` is found in. Measured on a picture of the
        screen, `window` raised."""
        from .a11y import Node
        args = ["--text", str(text), "--graphics", str(graphics),
                *(["--edges", str(edges)] if edges else []),
                *[f"--ignore={i}" for i in ignore],
                *([f"--window={window}"] if window else [])]
        return [Node(n) for n in self._a11y("contrast", *args)]

    def click_node(self, name, role=None, window=None, button=1, double=False, timeout=10):
        """Click the middle of the node whose name matches `name` (a regex,
        matched in full), and role `role` if given (a regex too), waiting
        up to `timeout` for it to show; that node."""
        from .a11y import Node
        args = ["--button", str(button), "--timeout", str(timeout),
                *([f"--role={role}"] if role else []), *(["--double"] if double else []),
                *([f"--window={window}"] if window else []), "--", name]
        return Node(self._a11y("click", *args, timeout=90 + timeout))

    def tab_walk(self, key="Tab", most=50, window=None, visible=False):
        """Press `key` until the focus lands somewhere it has been: the node
        it lands on after each press, the last being that one. Fails if a
        press leaves it where it was (unless it's the one thing that could
        take it), or after `most` presses; with `visible`, if the focus
        doesn't show on a node it lands on."""
        from .a11y import Node, unseen
        args = ["tabwalk", f"--key={key}", "--max", str(most), *(["--visible"] if visible else [])]
        got = self._a11y(*args, *([f"--window={window}"] if window else []),
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

    def capture(self, out, window=None, crop=None, park=False, vision=None):
        """A picture of the display, or of the window named `window`, to
        `out` (a PNG), then cropped (WxH+X+Y). With `park`, the pointer
        goes out of the way first, so nothing shows its hover. With
        `vision` (protanopia, deuteranopia, tritanopia or gray), it's as
        someone with that color vision sees it."""
        if park:
            self.park()
        self.backend.capture(self.env, out, window=self.window(window) if window else None,
                             crop=crop)
        if vision:
            x.vision(out, out, vision)
        return Path(out)

    def record(self, out, seconds=None, window=None, crop=None, fps=None, pointer=False,
               park=False):
        """A video of the display, or of the window named `window`, cropped
        (WxH+X+Y, inside it), to `out`: an .mp4, .webm or .gif. For
        `seconds`, or until interrupted. Without `pointer`, the pointer
        isn't drawn, as in a picture; with `park`, it goes out of the way
        first. It needs ffmpeg, and on Wayland, wf-recorder; sway draws no
        pointer there, so `pointer` is refused. A .wav is what plays in
        a session with audio, on X11 or Wayland, and takes none of those."""
        if Path(out).suffix.lower() == ".wav":
            if window or crop or fps or pointer or park:
                raise SessionError("a .wav is the session's sound: no window, crop, fps, "
                                   "pointer or park")
            try:
                return x.record(self.env, out, None, seconds=seconds)
            except (ValueError, RuntimeError) as e:
                raise SessionError(str(e))
        if self.wayland and pointer:
            raise SessionError("sway draws no pointer to record")
        if park:
            self.park()
        try:
            screen_w, screen_h = self.backend.root_size(self.env)
        except RuntimeError as e:
            raise SessionError(str(e))
        box = self.window(window)[2:6] if window else (screen_w, screen_h, 0, 0)
        if crop:
            m = re.fullmatch(r"(\d+)x(\d+)\+(\d+)\+(\d+)", crop)
            if not m:
                raise SessionError(f"a crop is WIDTHxHEIGHT+X+Y, not {crop}")
            cw, ch, cx, cy = map(int, m.groups())
            if cx + cw > box[0] or cy + ch > box[1] or cw < 2 or ch < 2:
                raise SessionError(f"the crop {crop} isn't inside the {box[0]}x{box[1]} taken")
            box = (cw, ch, box[2] + cx, box[3] + cy)
        # Only what's on the screen can be recorded: a window partly off it
        # is recorded as far as it shows.
        x0, y0 = max(box[2], 0), max(box[3], 0)
        x1, y1 = min(box[2] + box[0], screen_w), min(box[3] + box[1], screen_h)
        if x1 - x0 < 2 or y1 - y0 < 2:
            raise SessionError("nothing of that is on the screen to record")
        box = (x1 - x0, y1 - y0, x0, y0)
        try:
            return x.record(self.env, out, box, seconds=seconds, fps=fps, pointer=pointer)
        except (ValueError, RuntimeError) as e:
            raise SessionError(str(e))

    def burst(self, out, count, every=0.0, window=None, crop=None, park=False, vision=None,
              sheet=None, sheet_size=None):
        """`count` pictures, as `capture` takes them, each starting `every`
        seconds or more after the last, or as fast as they come, to
        OUT-1.png, OUT-2.png...
        (numbered to the width of `count`); with `sheet`, all of them in rows
        of four there too. Motion a single picture can't show: an
        animation, a transition, something moving. [(path, seconds after
        the first)]."""
        if count < 1:
            raise SessionError(f"a burst is 1 picture or more, not {count}")
        if park:
            self.park()
        out = Path(out)
        width = len(str(count))
        taken, start, last = [], time.monotonic(), None
        for i in range(count):
            wait = 0 if last is None else last + every - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            last = time.monotonic()
            at = last - start
            path = out.with_name(f"{out.stem}-{i + 1:0{width}d}{out.suffix or '.png'}")
            # Found again each time: on Wayland it's taken by where it is,
            # and it may have moved.
            found = self.window(window) if window else None
            self.backend.capture(self.env, path, window=found, crop=crop)
            taken.append((path, at))
        for path, _ in taken:
            if vision:
                x.vision(path, path, vision)
        if sheet:
            x.sheet([p for p, _ in taken], sheet, size=sheet_size)
        return taken

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

    def key(self, *chords, hold=None, state=None):
        """Press and release each chord in turn: ctrl+comma, Return,
        alt+shift+Tab. With `hold`, press them all, keep them down that many
        seconds, then let go, last first: walking while running, say. With
        `state` "down" or "up", only press them or only let them go. A chord
        it can't press is said before any key goes down, and a chord already
        held down isn't pressed again, or let go by a press."""
        for c in chords:
            self.xt.chord_keys(c)
        if state == "up":
            self._let_go(chords)
            return
        if hold is None and state is None:
            keep = self._held()
            for c in chords:
                self.xt.chord(c, keep=keep)
            return
        pressed, done = [], False
        try:
            for c in chords:
                with self._held_keys() as held:
                    if any(self._same(h, c) for h in held):
                        continue
                    pressed.append(c)
                    self.xt.chord(c, "down", keep=held)
                    held.append(c)
            if hold is not None:
                time.sleep(hold)
            done = True
        finally:
            # After a hold, or if anything went wrong: nothing it pressed
            # may stay down.
            if hold is not None or not done:
                self._let_go(pressed[::-1])

    @contextlib.contextmanager
    def _held_keys(self):
        """The chords held down and not yet let go, to change in place: kept
        in the session's scratch dir, since each command is its own process,
        and locked while they change, since commands run side by side."""
        scratch = self.env.get("SHOTBOX_SCRATCH")
        if not scratch:
            yield self.__dict__.setdefault("_held_chords", [])
            return
        f = Path(scratch) / "held-keys"
        with open(Path(scratch) / "held-keys.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            held = f.read_text().split() if f.exists() else []
            yield held
            f.write_text("".join(f"{c}\n" for c in held))

    def _held(self):
        """The chords held down, whose keys a key pressed and let go
        meanwhile mustn't let go of."""
        with self._held_keys() as held:
            return list(held)

    def _same(self, a, b):
        """Whether two chords press the same keys, however spelled: Ctrl+w
        and control+w, Esc and Escape, W and shift+w."""
        return self.xt.chord_keys(a) == self.xt.chord_keys(b)

    def _let_go(self, chords):
        """Let go of each chord, leaving down what chords still held need."""
        with self._held_keys() as held:
            for c in chords:
                held[:] = [h for h in held if not self._same(h, c)]
                self.xt.chord(c, "up", keep=held)

    def type(self, text):
        """Type ASCII text into whatever has the keyboard focus."""
        self.xt.type(text, keep=self._held())

    def move(self, px, py, window=None):
        """Move the pointer to X, Y on the screen, or inside `window`."""
        self.xt.move(*self._at(px, py, window))

    def latency(self, a, b, watch, window=None, times=10, timeout=5.0, settle=0.25):
        """How long what's drawn takes to follow the pointer: it's moved
        from point `a` to `b` and back, `times` moves, and each is timed
        from sending it to the first picture in which the `watch` box
        (WxH+X+Y, on the screen or in `window`) differs from just before;
        then it's let settle: before each, the box holds still for
        `settle` seconds, or twice the longest move yet, so a redraw still
        to come isn't taken for the next move's. The seconds each move
        took, in order: to within half a picture's taking, a few
        milliseconds."""
        box = re.fullmatch(r"(\d+)x(\d+)\+(\d+)\+(\d+)", watch)
        if not (box and int(box[1]) and int(box[2])):
            self.fail(f"what to watch is WIDTHxHEIGHT+X+Y, at least 1x1, not {watch}")
        box = tuple(int(n) for n in box.groups())
        w = self.window(window) if window else None
        whole = None if w else self.backend.grab(self.env)
        if not (w or whole):
            self.fail("can't take a picture of the display")
        wide, high = (w[2], w[3]) if w else whole[:2]
        if box[0] + box[2] > wide or box[1] + box[3] > high:
            self.fail(f"{watch} isn't inside the {wide}x{high} "
                      + ("window" if window else "display"))
        # Where to, found now: finding a window is milliseconds of its own.
        # A window that moves after isn't followed.
        said = {self._at(*p, window): p for p in (a, b)}
        a, b = self._at(*a, window), self._at(*b, window)

        def look():
            got = self.backend.grab(self.env, w, box)
            if not got:
                self.fail(f"can't take a picture of {watch}"
                          + (f" in the window {window!r}" if window else ""))
            return got[2]

        def still():
            """Wait until the box has held still, and what it looks like."""
            quiet = max([settle] + [2 * t for t in took])
            last, since, end = look(), time.monotonic(), time.monotonic() + timeout + quiet
            while time.monotonic() - since < quiet:
                if time.monotonic() > end:
                    self.fail(f"{watch} doesn't hold still")
                now = look()
                if now != last:
                    last, since = now, time.monotonic()
            return last

        took = []
        # The first move's own redraw, waited out, up to a second, so it
        # isn't taken for the next's; a first point whose arrival shows
        # nothing (no hover) has none to wait for.
        before = look()
        self.xt.move(*a)
        end = time.monotonic() + min(timeout, max(1.0, 4 * settle))
        while look() == before and time.monotonic() < end:
            pass
        for i in range(times):
            to = b if i % 2 == 0 else a
            before = still()
            sent = time.monotonic()
            self.xt.move(*to)
            while True:
                # A picture's time is the middle of its taking: X's is taken
                # as asked, Wayland's from the frame drawn next.
                start = time.monotonic()
                changed = look() != before
                t = (start + time.monotonic()) / 2
                if changed:
                    took.append(t - sent)
                    break
                if t - sent > timeout:
                    self.fail(f"{watch} didn't change in {timeout:g}s after the pointer "
                              "moved to {},{}".format(*said[to]))
        return took

    def click(self, px, py, window=None, button=1, double=False, hold=None, state=None):
        """Click at X, Y on the screen, or inside `window`. With `hold`,
        keep the button down that many seconds; with `state` "down" or "up",
        only press it or only let it go (moving there first)."""
        at = self._at(px, py, window)
        if hold is not None:
            self.xt.button(*at, button=button, state="down")
            try:
                time.sleep(hold)
            finally:
                self.xt.button(*at, button=button, state="up")
            return
        if state:
            self.xt.button(*at, button=button, state=state)
        else:
            self.xt.click(*at, button=button, count=2 if double else 1)

    def drag(self, x1, y1, x2, y2, window=None, button=1):
        dx, dy = self._at(0, 0, window)
        self.xt.drag(x1 + dx, y1 + dy, x2 + dx, y2 + dy, button=button)

    # --- gamepads (a session started with gamepads; see pad.py) -------------

    TAP = 0.05      # long enough for a game polling every frame to see it

    def _pad(self, req):
        if not isinstance(req["pad"], int) or req["pad"] < 1:
            raise SessionError(f"pads are numbered from 1, not {req['pad']}")
        try:
            return pad.ask(self.env.get("SHOTBOX_SCRATCH", ""), req)
        except pad.PadError as e:
            raise SessionError(str(e))

    def pad_press(self, names, n=1, hold=None, state=None):
        """Press gamepad `n`'s buttons (a, b, x, y, lb, rb, back, start,
        guide, ls, rs, and the d-pad's up, down, left, right) together:
        tapped, held `hold` seconds, or with `state` only pressed (left down,
        across commands) or only let go. One left down stays down through taps
        and holds of it, holds that overlap let go when the last ends, and
        letting go with `state` "up" ends its holds too."""
        try:
            pad.check_names(names)
        except pad.PadError as e:
            raise SessionError(str(e))
        if state:
            self._pad({"op": "buttons", "pad": n, "names": names, "state": state})
            return
        token = self._pad({"op": "buttons", "pad": n, "names": names, "state": "press"})["token"]
        try:
            time.sleep(self.TAP if hold is None else hold)
        finally:
            self._pad({"op": "buttons", "pad": n, "names": names, "state": "release",
                       "token": token})

    def pad_stick(self, which, px, py, n=1, hold=None):
        """Push gamepad `n`'s left or right stick to px, py, each -1 to 1 (y
        down): left there, or for `hold` seconds and then centered, unless
        it's been moved since."""
        token = self._pad({"op": "stick", "pad": n, "which": which, "x": px, "y": py})["token"]
        if hold is not None:
            try:
                time.sleep(hold)
            finally:
                self._pad({"op": "stick", "pad": n, "which": which, "x": 0, "y": 0,
                           "token": token})

    def pad_trigger(self, which, value, n=1, hold=None):
        """Pull gamepad `n`'s left or right trigger to `value`, 0 to 1: left
        there, or for `hold` seconds and then let go, unless it's been pulled
        since."""
        token = self._pad({"op": "trigger", "pad": n, "which": which, "value": value})["token"]
        if hold is not None:
            try:
                time.sleep(hold)
            finally:
                self._pad({"op": "trigger", "pad": n, "which": which, "value": 0,
                           "token": token})

    def pad_neutral(self, n=1):
        """Let go of everything on gamepad `n`: buttons up, sticks centered."""
        self._pad({"op": "neutral", "pad": n})

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
