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

import collections
import contextlib
import difflib
import fcntl
import functools
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import zlib
from pathlib import Path

from . import pad, wl, wlinput, x, xtest


class SessionError(RuntimeError):
    pass


def wheel(dy, dx):
    """Refuse a scroll of no clicks either way."""
    if not dy and not dx:
        raise SessionError("nothing to scroll: no clicks down or across")


# Set from another thread to call off every wait in the process, as shotbox
# mcp does for a request its client has cancelled; cleared by whoever set it.
_CANCEL = threading.Event()

# What wait_match gives: the seconds it took, and where the picture matched.
Match = collections.namedtuple("Match", "seconds x y")


def wait_for(what, test, timeout, interval=0.1, alive=None):
    """Poll `test()` until it returns something true, or fail saying what."""
    end = time.monotonic() + timeout
    while True:
        if _CANCEL.is_set():
            raise SessionError(f"stopped waiting for {what}: cancelled")
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
        process), fail sooner if it exits. A test with `there`, what to add
        to that: the windows there are, for one that waits on a window."""
        try:
            return wait_for(what, test, timeout, alive=alive)
        except SessionError as e:
            if _CANCEL.is_set():
                raise
            self.fail(str(e) + (test.there() if hasattr(test, "there") else ""))

    def there(self, name, sids=()):
        """What to add to giving up on the window wait_window(name, sids=sids)
        waits for, if it isn't there: the windows there are, their titles,
        and with app=RE or on Wayland, their app ids, the nearest to `name`
        first (by app id with app=RE), ten at most."""
        if self.find_window(name, sids):
            return ""
        app = name.startswith("app=") and not sids
        key = name[4:] if app else name
        seen = {}
        for w in self.backend.shown(self.env):
            line = json.dumps(w[1], ensure_ascii=False)
            apps = list(dict.fromkeys(w[6]))
            if apps and (app or self.wayland):
                line += f" ({'app id' if len(apps) == 1 else 'app ids'} {', '.join(apps)})"
            near = max(difflib.SequenceMatcher(None, key, k).ratio()
                       for k in (w[6] if app else (w[1],)) or ("",))
            seen[line] = max(near, seen.get(line, 0))
        if not seen:
            return "; there are none"
        lines = sorted(seen, key=seen.get, reverse=True)
        more = f", and {len(lines) - 10} more" if len(lines) > 10 else ""
        return "; there are: " + ", ".join(lines[:10]) + more

    # --- waiting --------------------------------------------------------------

    def test(self, kind, arg="", window=None):
        """What to say, and what to poll, for a wait: window (a regex for
        its title, or app=RE for its app id), port,
        file, ready, or stable (seconds; `window` to watch just that one)."""
        if kind == "window":
            def found():
                return self.backend.find_window(self.env, arg)
            found.there = lambda: self.there(arg)
            return f"a {x.matcher(arg)[1]}", found
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
            try:
                found = re.compile(arg)
            except re.error as e:
                raise SessionError(f"{arg!r} isn't a regex: {e}") from None

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
                        if i not in gone and found.search(line.partition(": ")[2]):
                            f.write(f"{i}\n")
                            return True
                return False
            return f"an announcement matching {arg!r}", said
        if kind == "stable":
            quiet = float(arg) if arg else 0.5
            where = f"the window {window!r}" if window else "the display"
            still = x.Still(self.env, window, quiet, self.backend)
            if window:
                still.there = lambda: self.there(window)
            return f"{where} to hold still for {quiet:g}s", still
        raise SessionError(f"don't know how to wait for {kind!r} "
                           "(window, port, file, ready, stable or announce)")

    def wait_window(self, name, timeout=30, sids=()):
        """Wait for a viewable window whose title matches `name` (a regex,
        matched in full), or, with app=RE, whose app id does; or with `sids`,
        as find_window has them, `name` a program's. Its (id, name, width,
        height, x, y)."""
        if not sids:
            return self.until(*self.test("window", name), timeout)

        def found():
            return self.find_window(name, sids)
        found.there = lambda: self.there(name, sids)
        return self.until(f"a window of {name!r}, or one titled that", found, timeout)

    def find_window(self, name, sids=()):
        """The window `name` asks for, as the backend's find_window; or with
        `sids`, process sessions, one of a process in one of them, else one
        titled exactly `name`: a program's, whose window a process it didn't
        start may make (D-Bus activation, a daemon)."""
        if not sids:
            return self.backend.find_window(self.env, name)
        return (self.backend.find_window(self.env, sids=sids)
                or self.backend.find_window(self.env, re.escape(name)))

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
        return self.until(f"a line matching {pattern!r} in {path}",
                          self._follow(pattern, path, from_start, within), timeout)[0]

    def _follow(self, pattern, path, from_start=False, within=None):
        """wait_line's test, from now: the line in a list once one comes,
        else None."""
        try:
            found, chunk = re.compile(pattern), 1 << 20
        except re.error as e:
            raise SessionError(f"{pattern!r} isn't a regex: {e}") from None
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
        return line

    def wait_ready(self, timeout=30):
        """Wait for $SHOTBOX_SCRATCH/ready, which `shotbox term` writes."""
        self.until(*self.test("ready"), timeout)

    def wait_stable(self, seconds=0.5, window=None, timeout=30):
        """Wait until the display, or the window named `window`, has looked
        the same for `seconds`: for the paint after a click, a panel sliding
        in, a toast going away."""
        self.until(*self.test("stable", seconds, window), timeout)

    def wait_match(self, ref, window=None, crop=None, fuzz="0%", most=0, timeout=30,
                   ignore=(), margin=0):
        """Wait until the display, or the window named `window`, cropped
        (WxH+X+Y), looks like the picture `ref`: `most` pixels or fewer
        differ by more than `fuzz`, as `shotbox compare` counts, but for
        those in the boxes (WxH+X+Y, in the picture) in `ignore`. For a
        screen with no other sign it's up: a boot, a login screen. With
        `margin`, the picture may be up to that many pixels from the crop's
        place each way, and is found where it matches best. A Match: the
        seconds it took, and x, y, its top left corner, as the crop's is
        (the crop's own without a margin, 0, 0 without a crop)."""
        # png: so a name like msl:FILE is a file, not ImageMagick's coder.
        try:
            x.size_of(f"png:{ref}")
        except subprocess.CalledProcessError:
            self.fail(f"can't read {ref} as a PNG")
        if not (isinstance(margin, int) and margin >= 0):
            self.fail(f"a margin is a whole number of pixels, not {margin!r}")
        if margin and not crop:
            self.fail("a margin goes with a crop: where the picture is looked for")
        return Match(*self._wait_looks(f"png:{ref}", ref, window, crop, fuzz, most, timeout,
                                       ignore, margin))

    def wait_change(self, window=None, crop=None, fuzz="0%", most=0, timeout=30, ignore=()):
        """Wait until the display, or the window named `window`, cropped,
        looks different from the first picture the wait takes: more than
        `most` pixels differ by more than `fuzz`, but for those in
        `ignore`, as wait_match's. The seconds it took."""
        return self._wait_looks(None, None, window, crop, fuzz, most, timeout, ignore)[0]

    def _wait_looks(self, ref, name, window, crop, fuzz, most, timeout, ignore, margin=0):
        """wait_match against `ref` (`name` to say), or with None,
        wait_change: the seconds from the start of the wait to the picture
        it passed on, and where it matched: x, y."""
        box = crop and re.fullmatch(r"(\d+)x(\d+)\+(\d+)\+(\d+)", crop)
        if crop and not (box and int(box[1]) and int(box[2])):
            self.fail(f"a crop is WIDTHxHEIGHT+X+Y, at least 1x1, not {crop}")
        box = box and [int(n) for n in box.groups()]
        if not (re.fullmatch(r"(\d+\.?\d*|\.\d+)%", fuzz) and float(fuzz[:-1]) <= 100):
            self.fail(f"fuzz is a percentage, 0% to 100%, not {fuzz!r}")
        if most < 0:
            self.fail(f"the pixels that may differ are 0 or more, not {most}")
        skip = [x.box_of(g) for g in ignore]
        if None in skip:
            self.fail("what to ignore is WIDTHxHEIGHT+X+Y, at least 1x1, not "
                      f"{ignore[skip.index(None)]}")
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

        start, at, sizes, small, rows = time.monotonic(), None, None, None, None
        place = tuple(box[2:]) if box else (0, 0)
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
            nonlocal ref, at, sizes, small, rows, place
            w = None
            if window:
                w = self.backend.find_window(self.env, window)
                if not w:
                    return False
                if box and (box[0] + box[2] > w[2] or box[1] + box[3] > w[3]):
                    small = f"{w[2]}x{w[3]}"
                    return False
            area = box
            if margin:
                # Grown by the margin, cut to what's taken.
                x0, y0 = max(0, box[2] - margin), max(0, box[3] - margin)
                area = (min(w[2] if w else wide, box[2] + box[0] + margin) - x0,
                        min(w[3] if w else high, box[3] + box[1] + margin) - y0, x0, y0)
            t = time.monotonic()
            got = picture(w, area)
            if got is None:
                return False
            if ref is None:
                ref = got
                return False
            if margin and ref[:2] == tuple(box[:2]):
                rows = rows or skip and x.kept(*ref[:2], skip)
                places = [(px - x0, py - y0) for px, py in x.nearest(box[2], box[3], margin)
                          if x0 <= px <= x0 + got[0] - ref[0] and y0 <= py <= y0 + got[1] - ref[1]]
                found = x.best_place(ref, got, places, fuzz, most, rows)
                n = found[0] if found else most + 1
                if found:
                    place = (found[1][0] + x0, found[1][1] + y0)
            elif margin:
                n, sizes = None, ("{}x{}".format(*ref[:2]), "{}x{}".format(*box[:2]))
            elif got[:2] == ref[:2]:
                rows = rows or skip and x.kept(*ref[:2], skip)
                n = x.differ(ref, got, fuzz, most, rows)
            else:
                n, sizes = None, ("{}x{}".format(*ref[:2]), "{}x{}".format(*got[:2]))
            if (n is not None and n <= most) == match:
                at = t - start
                return True
            return False

        where = f"the window {window!r}" if window else "the display"
        where += f", cropped to {crop}," if crop else ""
        where += f" or up to {margin} pixels off it," if margin else ""
        what = f"{where} to match {name}" if match else f"{where} to change"
        try:
            wait_for(what, looks, timeout, interval=0.01)
        except SessionError as e:
            if small:
                e = f"{e}; the crop isn't inside the window, {small}"
            elif match and sizes:
                e = f"{e}; {name} is {sizes[0]}, the picture taken {sizes[1]}"
            elif window:
                e = f"{e}{self.there(window)}"
            self.fail(str(e))
        finally:
            now.unlink(missing_ok=True)
        return at, *place

    def flicker(self, seconds=2.0, window=None, crop=None, tile=16, brief=0.1, times=8,
                fuzz=6.0, ignore=(), save=None):
        """Watch the display, or the window named `window`, cropped, for
        `seconds`, as fast as pictures come, for flicker and flashes (see
        flicker.py): tiles `tile` pixels square, a state no longer than
        `brief` seconds brief, `times` changes or more flicker, looks
        within `fuzz` percent the same, and boxes (WxH+X+Y) in `ignore`
        left out. [dict(kind="flicker" or "flash", box, at, changes,
        every, looks)], a box of tiles each, in order, how many pictures
        were taken, and how many of them changed. With `save`, a directory,
        each one's pictures there as a sheet (see `_sheet`), its path its
        "sheet", or "unsaved" if it couldn't be written."""
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
        if save is not None:
            if not str(save):
                self.fail("a directory to save to, not ''")
            try:
                Path(save).mkdir(parents=True, exist_ok=True)
                tempfile.TemporaryFile(dir=save).close()
            except (FileExistsError, NotADirectoryError):
                self.fail(f"can't save to {save}: it, or a directory it's in, is a file")
            except OSError as e:
                self.fail(f"can't save to {save}: {e.strerror}")
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
                size = w[2:4] if w else self.backend.root_size(self.env)
                self.fail(f"the crop {crop} isn't inside the {'window' if w else 'display'}, "
                          f"{size[0]}x{size[1]}")
            return got

        # Every picture that differs from the one before, compressed: the
        # program draws at its own rate, and what holds is said by time.
        shots, last, taken, start = [], None, 0, time.monotonic()
        try:
            while time.monotonic() - start < seconds:
                t0 = time.monotonic()
                got = picture()
                taken += 1
                if got[2] != last:
                    shots.append(((t0 + time.monotonic()) / 2 - start, zlib.compress(got[2], 1)))
                    last = got[2]
        finally:
            Path(png).unlink(missing_ok=True)
        width, height = got[:2]
        times_ = [t for t, _ in shots]
        across = -(-width // tile)
        looks, before = [], None
        for _, data in shots:
            now = zlib.decompress(data)
            looks.append(flicker.means(now, width, height, tile, before, looks and looks[-1]))
            before = now
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
            tx, ty = n % across * tile, n // across * tile
            flickering, flashed = flicker.find(held, brief, times, fuzz,
                                               lambda k, n=n: tiles_in[n, k],
                                               min(tile, width - tx))
            if flickering:
                found["flicker"][n] = flickering
            elif flashed:
                found["flash"][n] = flashed
        # A look that's in the picture before or after, up or down the
        # screen, is text scrolling: lines alike, one where another was, or
        # drawn again the same after a blank, aren't a look coming back,
        # nor a line shown once a flash; a run is scrolling if as many of its
        # looks are as aren't. One color all over says nothing; another look the same
        # down the tile, a gradient across, a border, a strip, is anywhere
        # up or down, and holds its place as scrolling text doesn't. A
        # flicker's look may be a near copy, moved, as text scrolled by
        # fractions of a pixel is; that's asked of about eight of its
        # pictures, by an odd step so both looks are, as finding it costs
        # more, and they're scrolling if as many of those are as aren't.
        few = {(n, k) for n, f in found["flicker"].items() for k in f[5][::len(f[5]) // 8 | 1]}
        ask = {}
        for n, k in ([(n, k) for n, f in found["flicker"].items() for k in f[5]]
                     + [(n, k) for n, fs in found["flash"].items() for f in fs for k in f[4:]]):
            for j in (k - 1, k + 1):
                ask.setdefault(j, []).append((n, k))
        scrolled, say = set(), set()
        for j in sorted(set(ask) & set(range(len(shots)))):
            f, columns = zlib.decompress(shots[j][1]), {}
            for n, k in ask[j]:
                tx, ty = n % across * tile, n // across * tile
                step = (min(tx + tile, width) - tx) * 3
                here = tiles_in[n, k]
                if (n, k) in scrolled or here == here[:3] * (len(here) // 3):
                    continue
                say.add((n, k))
                if here == here[:step] * (len(here) // step):
                    continue
                if tx not in columns:
                    columns[tx] = b"".join(f[(y * width + tx) * 3:(y * width + tx) * 3 + step]
                                           for y in range(height))
                at = columns[tx].find(here)
                while at >= 0 and (at % step or at == ty * step):
                    at = columns[tx].find(here, at + 1)
                if at >= 0 or (n, k) in few and flicker.moved(here, f, width, tx, ty,
                                                               step // 3, tile, fuzz):
                    scrolled.add((n, k))
        def scrolling(n, pictures):
            told = [(n, k) in scrolled for k in pictures if (n, k) in say]
            return bool(told) and 2 * sum(told) >= len(told)
        found["flicker"] = {n: f for n, f in found["flicker"].items() if not scrolling(n, f[5])
                            and not scrolling(n, [k for k in f[5] if (n, k) in few])}
        found["flash"] = {n: [f for f in fs if not scrolling(n, f[4:])]
                          for n, fs in found["flash"].items()}
        out, seen = [], []
        for bx, by, bw, bh, group in flicker.boxes(found["flicker"], across, tile):
            bw, bh = min(bw, width - bx), min(bh, height - by)    # tiles at the edge
            # The box said as its tile that changed most does.
            at, changes, every, a, b, ks = max((found["flicker"][n] for n in group),
                                               key=lambda f: f[1])
            out.append(dict(kind="flicker", box=f"{bw}x{bh}+{bx}+{by}", at=at, changes=changes,
                            every=every, looks=(flicker.hex_of(a), flicker.hex_of(b))))
            # A first look and eight changes: both looks, coming back.
            seen.append(((bx, by, bw, bh), [ks[0] - 1, *ks[:8]]))
        flashes = found["flash"]
        down = len(looks[0]) // across

        def beside(group):
            """The tiles round a group of tiles, not in it."""
            at = ((m % across, m // across) for m in group)
            return {(y + dy) * across + x + dx for x, y in at
                    for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                    if 0 <= x + dx < across and 0 <= y + dy < down} - set(group)

        def toward(k, n):
            """How tile n's mean color moved in picture k."""
            return [a - b for a, b in zip(looks[k][n], looks[k - 1][n])]

        def along(way, d):
            """Whether a tile's mean color moving `d` went `way`."""
            return (sum(a * b for a, b in zip(way, d))
                    > 0.8 * sum(c * c for c in way) ** 0.5 * sum(c * c for c in d) ** 0.5)

        def between(p, a, b):
            """Whether color p is within twice fuzz of the way from a to b."""
            ab = [y - x for x, y in zip(a, b)]
            t = max(0, min(1, sum((q - x) * d for q, x, d in zip(p, a, ab))
                           / (sum(d * d for d in ab) or 1)))
            return near(p, [x + t * d for x, d in zip(a, ab)])

        @functools.lru_cache(maxsize=None)
        def turned(m, i):
            """Tile m's colors where it changed in its state i, before and
            after, or None."""
            old, new = tiles_in.get((m, helds[m][i - 1][3])), tiles_in.get((m, helds[m][i][3]))
            return old and new and flicker.colors(old, new, flicker.changed(old, new, fuzz / 4))

        def near(p, q):
            """Whether two colors are within twice fuzz of each other."""
            return sum((x - y) ** 2 for x, y in zip(p, q)) ** 0.5 <= 2 * fuzz * 2.55

        def brings(m, i, flashed):
            """Whether tile m's state i took the colors of a flash's, each
            (look, then) in `flashed`, or a part of them, or they its. One
            whose pictures weren't kept, as no run of its own needed them,
            can't say, and is taken to, so motion isn't missed."""
            if not turned(m, i):
                return True
            was, took = turned(m, i)
            return any(between(look, then, took) or between(took, was, look)
                       for look, then in flashed)

        def swapped(n, m):
            """Whether tile m, as flashing tile n changed, went the other
            way, from n's flash look to the look n had: something thin
            stepping from one into the other, as an aliased line's step
            does, or a square leaving a tile as it enters the next."""
            k, look, then = now[n][4], now[n][2], now[n][3]
            if not along([-c for c in toward(k, n)], toward(k, m)):
                return False
            i = next((i for i, st in enumerate(helds[m]) if i and st[3] == k), None)
            return (bool(i and turned(m, i))
                    and near(turned(m, i)[0], look) and near(turned(m, i)[1], then))

        def took(m, i):
            """Whether tile m's state i is the color of one of the flash's
            looks, being under it."""
            return bool(turned(m, i)) and any(near(turned(m, i)[1], f[2]) for f in now.values())

        def still(n, i, t):
            """Whether tile n, before its state i, held a look longer than
            `brief`, or since watching began, up to within `brief` of `t`:
            one busy all along, a scene animating, a terminal scrolling,
            didn't."""
            for st in reversed(helds[n][:i]):
                if st[1] + st[2] < t - brief:
                    return False
                if st[2] > brief or st is helds[n][0]:
                    return True
            return False

        done = set()
        for k in sorted({f[4] for fs in flashes.values() for f in fs}):
            # Tiles that flashed in the same picture make a box. Something
            # moving through turns a tile beside it the same way, briefly,
            # as it passes, within `brief`, or a picture, before or after,
            # if pictures come slower; a flash doesn't, and what changes
            # beside it in the same picture, another way, or for longer, is
            # something else: a caret, a spinner, a word in the flash.
            now = {n: f for n, fs in flashes.items() for f in fs
                   if f[4] == k and (n, k) not in done}
            if not now:
                continue
            # What showed only while the flash did, one color or not, is one
            # thing with it, and what's beside that is what's beside it: a
            # square passing, a sliver of it in a tile at the window's edge,
            # or lines of text drawn once in a terminal scrolling.
            gone = max(f[5] for f in now.values())
            # A flash drawn torn, a part of it a picture later, is one: what
            # flashed before it was gone is with it, not something moving.
            now.update({n: f for n, fs in flashes.items() for f in fs if k < f[4] < gone})
            done |= {(n, f[4]) for n, f in now.items()}
            # Of what changed with it, a tile busy before isn't with it,
            # unless the flash covered it, taking its color.
            with_it = {m for m, held in helds.items()
                       for i, (st, after) in enumerate(zip(held, held[1:]))
                       if st[3] == k and after[3] <= gone
                       and (still(m, i, times_[k]) or took(m, i))}
            for bx, by, bw, bh, group in flicker.boxes(now.keys() | with_it, across, tile):
                if not now.keys() & set(group):
                    continue
                # Most of the box gone other ways than the flash did is a
                # region drawn anew, as a terminal scrolling a page at a time
                # does: its caret gone for the page.
                went = [sum(c) for c in zip(*(toward(now[n][4], n) for n in group if n in now))]
                if 2 * sum(not along(went, toward(k, n)) for n in group if n not in now) > len(group):
                    continue
                # Nor is it where a thin thing stepped out of, into a tile of
                # the box or beside it, for that picture.
                if any(swapped(n, m) for n in group if n in now
                       for m in set(group) | beside(group) if m != n and m in helds):
                    continue
                # And it passes through what held still, with its colors: a
                # tile beside busy all along, a scene animating, or going
                # darker as text scrolls in by a black flash, says nothing.
                shown = [now[n][2:4] for n in group if n in now]
                if any(along(went, toward(st[3], m)) for m in beside(group) if m in helds
                       for i, st in enumerate(helds[m]) if i and st[3] != k and st[2] <= brief
                       and (abs(st[3] - k) == 1 or abs(st[1] - times_[k]) <= brief)
                       and still(m, i, st[1]) and brings(m, i, shown)):
                    continue
                bw, bh = min(bw, width - bx), min(bh, height - by)
                at, held_for, look, then, first, _ = min(
                    (now[n] for n in group if n in now), key=lambda f: f[0])
                # Torn, till the last of it is gone.
                after = max(now[n][5] for n in group if n in now)
                out.append(dict(kind="flash", box=f"{bw}x{bh}+{bx}+{by}", at=at, changes=2,
                                every=held_for,
                                looks=(flicker.hex_of(then), flicker.hex_of(look))))
                seen.append(((bx, by, bw, bh), [*range(first - 1, after)][:8] + [after]))
        order = sorted(range(len(out)), key=lambda i: out[i]["at"])
        if save is not None:
            for number, i in enumerate(order, 1):
                f = out[i]
                path = Path(save) / f"{number:0{len(str(len(out)))}d}-{f['kind']}-{f['box']}.png"
                # One not written leaves the rest, and what was found, to be said.
                try:
                    f["sheet"] = str(self._sheet(path, shots, seen[i][1], seconds, (width, height),
                                                 seen[i][0], tile))
                except (OSError, subprocess.CalledProcessError):
                    f["unsaved"] = str(path)
        return [out[i] for i in order], taken, len(shots)

    def _sheet(self, out, shots, ks, end, size, box, margin):
        """Pictures `ks` of `shots`, (seconds, compressed RGB `size`) of a
        watch that ended at `end`, cut to `box` (x, y, width, height) and
        `margin` round it, the box outlined, each labeled with when it
        showed, in order, as one PNG at `out` no more than 1920 pixels each
        way: magnified, a pixel a square, up to 8 times, so a band a pixel
        wide is seen, or shrunk, its label not, to fit."""
        (width, height), (bx, by, bw, bh) = size, box
        x0, y0 = max(0, bx - margin), max(0, by - margin)
        x1, y1 = min(width, bx + bw + margin), min(height, by + bh + margin)
        cw, ch = x1 - x0, y1 - y0
        # As near square as it goes: a strip down the screen side by side,
        # one across it one above another.
        across = max(1, min(len(ks), round((len(ks) * ch / cw) ** 0.5)))
        down = -(-len(ks) // across)
        # A cell's border is 2 pixels, its label 17 high.
        fit = min((1920 // across - 2) / cw, (1920 // down - 20) / ch)
        scale = min(8, int(fit)) or fit
        grow = (["-scale", f"{scale * 100}%"] if scale >= 1
                else ["-resize", f"{int(cw * scale)}x{int(ch * scale)}!"])
        left, top = int((bx - x0) * scale) - 1, int((by - y0) * scale) - 1
        right, bottom = int((bx - x0 + bw) * scale), int((by - y0 + bh) * scale)
        with tempfile.TemporaryDirectory(dir=self.env["SHOTBOX_SCRATCH"]) as tmp:
            cells = []
            for n, k in enumerate(ks):
                t, data = shots[k]
                till = shots[k + 1][0] if k + 1 < len(shots) else max(end, t)
                data = zlib.decompress(data)
                cells.append(Path(tmp) / f"{n}.png")
                subprocess.run([*x.CONVERT, "-size", f"{cw}x{ch}", "-depth", "8", "rgb:-", *grow,
                                "-fill", "none", "-stroke", "magenta", "+antialias",
                                # Two wide, so a sheet seen smaller keeps it.
                                "-draw", f"rectangle {left},{top} {right},{bottom}",
                                "-draw", f"rectangle {left - 1},{top - 1} {right + 1},{bottom + 1}",
                                "-antialias", "-stroke", "none", "-fill", "white",
                                "-background", "black", "+size", "-pointsize", "14",
                                f"label:{t:.3f}\u2013{till:.3f}s", "-gravity", "center", "-append",
                                "-bordercolor", "gray50", "-border", "1", str(cells[-1])],
                               input=b"".join(data[(y * width + x0) * 3:(y * width + x1) * 3]
                                              for y in range(y0, y1)), check=True)
            return x.sheet(cells, out, across=across)

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
        # Options as --name=VALUE, and a name after --: an agent's regex can
        # start with a dash.
        proc = subprocess.Popen([*self_command(), "a11y", args[0], "--json", *args[1:]],
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
        end = time.monotonic() + timeout
        while True:
            try:
                out, err = proc.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if _CANCEL.is_set() or time.monotonic() > end:
                    proc.kill()
                    proc.communicate()
                    if _CANCEL.is_set():
                        raise SessionError(f"shotbox a11y {args[0]}: cancelled") from None
                    self.fail(f"shotbox a11y {args[0]} got no answer in {timeout}s")
        if proc.returncode:
            self.fail(err.strip().removeprefix("shotbox: ") or f"shotbox a11y {args[0]} failed")
        return json.loads(out)

    def _windowed(self, nodes, window, at=None):
        """`nodes` with their bounds in `window`'s x,y (its corner `at` on the
        screen, if that's been looked up), as click, scroll and capture take
        an x,y with it, so they're given to them as they are."""
        from .a11y import Node
        if not window or not any(n.get("bounds") for n in nodes):
            return nodes
        wx, wy = at or self.window(window)[4:6]
        return [Node({**n, "bounds": [n.bounds[0] - wx, n.bounds[1] - wy, *n.bounds[2:]]})
                if n.get("bounds") else n for n in nodes]

    def _by_name(self, window, timeout, act):
        """The node `act(timeout)` clicks or scrolls over, in `window`'s x,y, as
        are those listed when more than one matches. The window is waited for
        first, so nothing fails once it's acted."""
        began = time.monotonic()
        at = window and self.wait_window(window, timeout)[4:6]
        try:
            n = act(max(0, timeout - (time.monotonic() - began)))
        except SessionError as e:
            # The time it gave up after is the caller's, not what was left of it.
            said = re.sub(r"^(gave up .*?after )[\d.e-]+s", lambda m: f"{m[1]}{timeout:g}s", str(e))
            if not at or not re.match(r"\d+ nodes match", said):
                raise SessionError(said) from None
            raise SessionError(re.sub(r" @(-?\d+),(-?\d+)(?= \d+x\d+$)",
                                      lambda m: f" @{int(m[1]) - at[0]},{int(m[2]) - at[1]}",
                                      said, flags=re.M)) from None
        return self._windowed([n], window, at)[0]

    def a11y_tree(self, window=None):
        """Every node a screen reader sees, depth first: dicts (and
        attributes) of role, name, description, states, path, depth, and
        bounds, [x, y, width, height]: on the screen, or with `window`, in
        that window's x,y; or None where they can't be told."""
        from .a11y import Node
        return self._windowed([Node(n) for n in self._a11y(
            "tree", *([f"--window={window}"] if window else []))], window)

    def find(self, role=None, name=None, window=None):
        """The nodes with this role ("button") and named `name`: those
        named exactly that, if any, else those it matches as a regex, in
        full."""
        named = None if name is None else x.pattern(name)[0]
        found = [n for n in self.a11y_tree(window)
                 if (role is None or n.role == role) and (named is None or named(n.name))]
        return [n for n in found if n.name == name] or found

    def focused(self, window=None):
        """The node with the keyboard focus, or None."""
        return next((n for n in self.a11y_tree(window) if "focused" in n.states), None)

    def wait_focus(self, name, timeout=30, window=None):
        """Wait for the keyboard focus to be on a node named `name`, exactly
        or as a regex matched in full; that node."""
        named, note = x.pattern(name)

        def there():
            n = self.focused(window)
            return n if n and named(n.name) else None
        return self.until(f"the focus on {name!r}{note}", there, timeout)

    def wait_node(self, pattern, window=None, timeout=30):
        """Wait for a showing node with `pattern`, a regex, found in its name
        or text: a status line saying a page is drawn, say. That node, with
        its text round the match, about 80 characters, as `text` where
        that's what matched."""
        from .a11y import Node
        return Node(self._a11y("node", "--timeout", str(timeout),
                               *([f"--window={window}"] if window else []), "--", pattern,
                               timeout=90 + timeout))

    def a11y_lint(self, window=None, ignore=()):
        """The nodes a screen reader would have no name to say for, each
        with what's wrong as `problem`; but those whose "PATH: PROBLEM"
        line, or "LINE: PROBLEM" with its tree line, one of the regexes in
        `ignore` is found in."""
        from .a11y import Node
        args = [f"--ignore={i}" for i in ignore]
        return self._windowed([Node(n) for n in self._a11y(
            "lint", *args, *([f"--window={window}"] if window else []))], window)

    def contrast(self, window=None, text=4.5, graphics=3.0, edges=None, ignore=(), large=3.0,
                 passes=False, strict=False):
        """What's drawn with too little contrast, as `shotbox a11y contrast
        --json` has it: a dict of `problems`, the nodes that fail, each with
        what's wrong as `problem`: text, buttons and links against their
        background (at least `text`, or `large` for large text), images
        (`graphics`), and with `edges`, a button's or field's edge against
        what's round it; and `notes`, what wasn't measured and doesn't
        fail, each with its `note`: too small to, or GTK 4's, with `why`;
        with `strict`, only what's scrolled out of view, the rest being
        `problems` too; and `behind`, what's behind a modal dialog, not
        measured, which doesn't fail even with `strict`, each with its
        `behind`. With `passes`, `passes` too, what was measured
        and passes, each with its ratio as `contrast`. But not those whose "PATH: WHAT" line, or
        "LINE: WHAT" with its tree line, one of the regexes in `ignore` is
        found in. Measured on a picture of the screen, `window` raised.
        Fails, saying why, if nothing could be measured: of a GTK 4 window,
        its positions wrong, only text found near them is."""
        from .a11y import Node
        args = ["--text", str(text), "--large", str(large), "--graphics", str(graphics),
                *(["--edges", str(edges)] if edges else []),
                *[f"--ignore={i}" for i in ignore], *(["--passes"] if passes else []),
                *(["--strict"] if strict else []),
                *([f"--window={window}"] if window else [])]
        return {k: self._windowed([Node(n) for n in v], window)
                for k, v in self._a11y("contrast", *args).items()}

    def click_node(self, name, role=None, window=None, button=1, double=False, timeout=10,
                   force=False):
        """Click the middle of the node named `name`, exactly or else as a
        regex matched in full, and role `role` if given (the same), waiting
        up to `timeout` for it to show, and unless `force`, to be enabled,
        still and uncovered; that node."""
        from .a11y import Node
        return self._by_name(window, timeout, lambda left: Node(self._a11y(
            "click", "--button", str(button), "--timeout", str(left),
            *([f"--role={role}"] if role else []), *(["--double"] if double else []),
            *(["--force"] if force else []),
            *([f"--window={window}"] if window else []), "--", name, timeout=90 + left)))

    def scroll_node(self, name, dy, dx=0, role=None, window=None, timeout=10, force=False):
        """Turn the mouse wheel over the node `click_node` would click, `dy`
        clicks down and `dx` right, as `scroll`; that node."""
        from .a11y import Node
        return self._by_name(window, timeout, lambda left: Node(self._a11y(
            "scroll", "--timeout", str(left), *([f"--role={role}"] if role else []),
            *(["--force"] if force else []),
            *([f"--window={window}"] if window else []), "--", name, str(dy), str(dx),
            timeout=90 + left)))

    def tab_walk(self, key="Tab", most=50, window=None, visible=False, reach_all=False,
                 ignore=(), document=False):
        """Press `key` until the focus lands somewhere it has been: the node
        it lands on after each press, the last being that one. Fails, as
        `shotbox a11y tabwalk` says, unless it comes round: on a trap (but
        for the one thing that could take the focus, or a text view
        ctrl+Tab, shift+Tab, Escape or F6 leaves), where no node has the
        focus, or after `most` presses; with `visible`, if the focus
        doesn't show on a node it lands on; with `reach_all`, on a control
        it never reaches, but those whose "PATH: not reached by KEY", or
        with its tree line for the path, one of the regexes in `ignore` is
        found in. With `document`, only what's in the window's web page,
        as `tabwalk --document` has it."""
        from .a11y import Node, unseen, walk_end
        args = ["tabwalk", f"--key={key}", "--max", str(most), *(["--visible"] if visible else []),
                *(["--document"] if document else []), *[f"--ignore={i}" for i in ignore]]
        got = self._a11y(*args, *([f"--window={window}"] if window else []),
                         timeout=90 + most * 5)
        seen = [Node(n) for n in got["focus"]]
        if got["end"] != "cycle":
            where = f", on {seen[-1].line()}" if seen and got["end"] == "trap" else ""
            self.fail(walk_end(key, most, seen, got["end"], []) + where)
        hidden = [n for n in seen if unseen(n)]
        if hidden:
            self.fail("the focus doesn't show on " + "; ".join(n.line() for n in hidden))
        if reach_all and got["unreached"]:
            self.fail(f"{key} never reaches " + "; ".join(Node(n).line() for n in got["unreached"]))
        return seen

    def speak(self, keys=None, window=None, most=50):
        """What Orca, a screen reader, says as keys move the focus: with
        Orca running in the session, press `keys` in turn, or Tab until the
        focus lands where it has been, at most `most` times, but not in a
        text view, which takes Tab. A dict: `start`, what has the `focus`
        as Orca starts (a node, or None) and its `speech`, a list of what
        it said; `presses`, the same after each press, with its `key` and
        whether the focus `moved`; and `text_view`, if Tab stopped at
        one, the key that leaves it, else None. Needs Orca, which takes a few seconds to start."""
        from .a11y import Node
        args = ["speak", "--max", str(most), *([f"--window={window}"] if window else []),
                *(["--keys", *keys] if keys else [])]
        got = self._a11y(*args, timeout=180 + 15 * (len(keys) if keys else most))
        for p in [got["start"], *got["presses"]]:
            p["focus"] = p["focus"] and Node(p["focus"])
        return got

    # --- windows and pictures -------------------------------------------------

    def window(self, name):
        """The window `name` asks for (a title regex, or app=RE), or fail."""
        w = self.backend.find_window(self.env, name)
        if not w:
            self.fail(f"no {x.matcher(name)[1]}, matched in full{self.there(name)}")
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
               park=False, sound=False, gap=0.001, size=None, until=None, tail=0,
               within=None):
        """A video of the display, or of the window named `window`, cropped
        (WxH+X+Y, inside it), to `out`: an .mp4, .webm or .gif. For
        `seconds`, or until interrupted. Without `pointer`, the pointer
        isn't drawn, as in a picture; with `park`, it goes out of the way
        first. It needs ffmpeg, and on Wayland, wf-recorder; sway draws no
        pointer there, so `pointer` is refused. A .wav is what plays in
        a session with audio, on X11 or Wayland, and takes none of those.
        With `sound`, in a session with audio, an .mp4 or .webm has what
        plays in it too, heard with `gap` as `shotbox.sound.hear` takes it.
        With `size`, it ends when the file (for a .gif, the video it's made
        from) reaches that many bytes; a .webm's may be seconds late, as
        its muxer holds up to about 5 seconds before writing.
        With `until`, (pattern, path), it stops `tail` seconds after a line
        matching the pattern is written to the file at `path`, as
        `wait_line` waits for one (`within` as there), and `seconds` is
        the most it lasts.
        A Recording: its `path`, which it stands in for, its `heard` (with
        `sound`), its `line` (the line that ended it, or None), whether it
        was `stopped` (interrupted, not ended by itself), and `problems()`."""
        until = until and self._follow(*until, within=within)
        if Path(out).suffix.lower() == ".wav":
            if window or crop or fps or pointer or park:
                raise SessionError("a .wav is the session's sound: no window, crop, fps, "
                                   "pointer or park")
            try:
                return x.record(self.env, out, None, seconds=seconds, sound=sound, gap=gap,
                                size=size, until=until, tail=tail)
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
            return x.record(self.env, out, box, seconds=seconds, fps=fps, pointer=pointer,
                            sound=sound, gap=gap, size=size, until=until, tail=tail)
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

    def scroll(self, px, py, dy, dx=0, window=None):
        """Turn the mouse wheel at X, Y on the screen, or inside `window`:
        `dy` clicks down (up if negative), `dx` right (left)."""
        wheel(dy, dx)
        self.xt.scroll(*self._at(px, py, window), dy, dx)

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

    # --- scripts --------------------------------------------------------------

    def play(self, script, vars=None, name=None, verbose=False):
        """Play a script of timed steps, as `shotbox play` does: its text,
        or with a Path, the file's; with `vars` (a dict) over the session's
        variables for ${NAME}. Each step's options are checked before the
        first starts; SessionError, naming the line (in `name`), if one is
        wrong or fails, once what runs in the background is stopped. Text
        starting with a newline, as a triple-quoted string does, is
        numbered from the line after it."""
        from .__main__ import play
        if isinstance(script, str):
            script = script.removeprefix("\n")
        play(script, self, verbose, name, vars)

    def close(self):
        if self._x is not None:
            self._x.close()
            self._x = None


def here():
    """The display of the `shotbox run` this is inside."""
    if not os.environ.get("SHOTBOX_SCRATCH"):
        raise SessionError("not inside a shotbox session")
    return Screen()
