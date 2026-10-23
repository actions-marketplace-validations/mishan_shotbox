"""The accessible (AT-SPI) tree of what runs in a session, as a screen
reader sees it, for a session started with --a11y.

    shotbox a11y tree [--window RE]     the tree, a node a line
    shotbox a11y lint [--window RE]     controls and images with no name
    shotbox a11y tabwalk [--key Tab]    where the keyboard focus goes
    shotbox a11y click NAME             click a node, by its name
    shotbox a11y scroll NAME DY [DX]    turn the wheel over a node, by its name
    shotbox a11y contrast               text and controls against their backgrounds
    shotbox a11y speak [--keys KEY...]  what Orca says as the focus moves (speak.py)

Reads the session's own accessibility bus, from AT_SPI_BUS_ADDRESS, and
refuses to run without it: otherwise libatspi would go looking for the
desktop's. `record` is the session's announcement recorder; `node` is what
`shotbox wait node` runs.
"""

import argparse
import difflib
import json
import math
import os
import re
import sys
import threading
import time
import unicodedata
from collections import Counter
from functools import cache

from . import picture, x
from .screen import Screen, SessionError, wait_for, wheel
from .session import ATSPI, bindings, pango_cairo

# The states worth a line in the tree: what a screen reader says about a
# node, not how it's laid out ("showing", "vertical"). Their AT-SPI names.
STATES = ("focusable", "focused", "selected", "checked", "indeterminate", "pressed",
          "expanded", "editable", "read-only", "modal", "required", "invalid-entry")

# Controls with nothing to say for themselves without a name. Atspi.Role
# names, since the strings change between releases ("push button" is
# "button" from 2.54); those a release lacks are skipped.
CONTROLS = ("PUSH_BUTTON", "BUTTON", "PUSH_BUTTON_MENU", "TOGGLE_BUTTON", "CHECK_BOX",
            "RADIO_BUTTON", "MENU_ITEM", "CHECK_MENU_ITEM", "RADIO_MENU_ITEM", "LINK",
            "ENTRY", "PASSWORD_TEXT", "SPIN_BUTTON", "SLIDER", "COMBO_BOX", "PAGE_TAB",
            "SWITCH")

# Nodes that take the focus only to hand it to what's in them, as GTK 4's
# scrolled windows, lists and notebooks do, and are said by what's in them.
# Not PANEL: that's GTK 4's role for a widget of its own (a drawing area).
CONTAINERS = ("SCROLL_PANE", "LIST", "LIST_BOX", "TREE", "TREE_TABLE", "TABLE",
              "GROUPING", "FILLER", "SECTION", "PAGE_TAB_LIST", "VIEWPORT",
              "SPLIT_PANE", "TOOL_BAR", "MENU_BAR", "FRAME", "WINDOW")

# What's read as text, for contrast: AT-SPI's roles for text a program
# draws as it is.
TEXTS = ("LABEL", "HEADING", "LINK", "STATIC", "CAPTION", "TEXT", "PARAGRAPH")

# Controls that draw no text of their own, whatever their name. What WCAG
# asks of them is their parts against what's round them, not anything in
# their own box, so contrast leaves them out.
TEXTLESS = ("SWITCH", "SLIDER", "CHECK_BOX", "RADIO_BUTTON", "SPIN_BUTTON")

# Controls a box is drawn round, or should be, for --edges: not check boxes
# and the like, whose node holds their label too.
EDGED = ("PUSH_BUTTON", "BUTTON", "TOGGLE_BUTTON", "PUSH_BUTTON_MENU", "ENTRY",
         "PASSWORD_TEXT", "SPIN_BUTTON", "COMBO_BOX")

POLITENESS = {1: "polite", 2: "assertive"}


def atspi():
    """libatspi, on the session's accessibility bus and nobody else's."""
    if not os.environ.get("AT_SPI_BUS_ADDRESS") or not os.environ.get("SHOTBOX_SCRATCH"):
        raise SessionError("no accessibility bus: start the session with --a11y")
    why = bindings(*ATSPI)
    if why:
        raise SessionError(why)
    from gi.repository import Atspi, GLib

    # AccessKit's adapter has no cache object, which libatspi asks every
    # program for, and says so on each read of a tree with one in it.
    def quiet(domain, level, message, *_):
        if "/org/a11y/atspi/cache" not in message:
            GLib.log_default_handler(domain, level, message)
    GLib.log_set_handler("atspi", GLib.LogLevelFlags.LEVEL_WARNING, quiet)
    Atspi.init()
    return Atspi


class Node(dict):
    """A node: role, name, description, states, and path (where it is:
    each step's role and its index among its siblings), as a dict, so it
    goes through JSON as it is."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def line(self, bounds=False):
        """As the tree has it, and with `bounds`, where it is, if that's known."""
        out = f'{self.role} {json.dumps(self.name, ensure_ascii=False)}'
        if self.description:
            out += f' description={json.dumps(self.description, ensure_ascii=False)}'
        out += f" [{', '.join(self.states)}]" if self.states else ""
        if self.get("quiet"):
            out += f" (pid {self.pid}) didn't answer: busy, or hung"
        return out + (" @{},{} {}x{}".format(*self["bounds"]) if bounds and self.get("bounds")
                      else "")


# Past this many pairs of nodes, what's left between the trees' ends is
# taken as gone and added: pairing it takes as long as that.
PAIRED = 1_000_000


def changes(before, after):
    """What changed from one tree to another, as a11y_tree has them: a line
    a node, in tree order, as the tree has it, after "+ " for one added,
    "- " one gone (a subtree gone whole as its root, with how many were in
    it), or "~ " one renamed, described otherwise or in other states, then
    ", was" and what it was; each under the line above it that it's in, if
    any; with changes in more than one window, each window's after an
    "in" line naming it. Nodes are paired by depth, role, name and
    description, and those left by depth and role, so a row added above
    others doesn't make them all renamed; a run added or gone is slid to
    start at its shallowest node, so a row reads as one. Where they are
    isn't compared: a scroll moves everything. A program that didn't
    answer either time is said, after "! ", and its windows left out:
    they're not gone."""
    quiet = {n.pid for n in before + after if n.get("quiet")}

    def kept(t):
        out, skip = [], False
        for n in t:
            skip = n.get("pid") in quiet if not n.depth else skip
            if not skip:
                out.append(n)
        return out

    a, b = kept(before), kept(after)
    runs = []   # (mark, node, was), a list a run

    def was(m, n):
        said = [json.dumps(m.name, ensure_ascii=False)] if m.name != n.name else []
        if m.description != n.description:
            said.append(f"description={json.dumps(m.description, ensure_ascii=False)}")
        return " ".join(said + [f"[{', '.join(m.states)}]"] * (m.states != n.states))

    def pair(i0, i1, j0, j1, key, rest):
        # Most of a tree is as it was: its ends are paired before the rest
        # is searched.
        lead = tail = 0
        while lead < min(i1 - i0, j1 - j0) and key(a[i0 + lead]) == key(b[j0 + lead]):
            lead += 1
        while (tail < min(i1 - i0, j1 - j0) - lead
               and key(a[i1 - 1 - tail]) == key(b[j1 - 1 - tail])):
            tail += 1
        mi, mj = (i0 + lead, i1 - tail), (j0 + lead, j1 - tail)
        if (mi[1] - mi[0]) * (mj[1] - mj[0]) > PAIRED:
            middle = [("replace", *mi, *mj)]
        else:
            middle = [(op, p + mi[0], q + mi[0], r + mj[0], t + mj[0])
                      for op, p, q, r, t in difflib.SequenceMatcher(
                          None, [key(n) for n in a[slice(*mi)]], [key(n) for n in b[slice(*mj)]],
                          autojunk=False).get_opcodes()]
        ops = [["equal", i0, mi[0], j0, mj[0]], *map(list, middle),
               ["equal", mi[1], i1, mj[1], j1]]
        for k, op in enumerate(ops[1:-1], 1):
            if op[0] in ("insert", "delete") and ops[k - 1][0] == ops[k + 1][0] == "equal":
                slide(ops[k - 1], op, ops[k + 1], key)
        for op, p, q, r, t in ops:
            if op == "equal":
                runs.extend([("~", n, w)] for m, n in zip(a[p:q], b[r:t]) if (w := was(m, n)))
            elif p < q or r < t:
                rest(p, q, r, t)

    def slide(before_it, op, after_it, key):
        """Move an added or gone run as far as it can go and still be the
        same change, to where it starts at its shallowest node."""
        seq, lo, hi = (b, op[3], op[4]) if op[0] == "insert" else (a, op[1], op[2])
        up = down = 0
        while up < before_it[2] - before_it[1] and key(seq[lo - 1 - up]) == key(seq[hi - 1 - up]):
            up += 1
        while down < after_it[2] - after_it[1] and key(seq[lo + down]) == key(seq[hi + down]):
            down += 1
        d = min(range(-up, down + 1), key=lambda d: (seq[lo + d].depth, abs(d)))
        before_it[2] += d
        before_it[4] += d
        op[1:] = [v + d for v in op[1:]]
        after_it[1] += d
        after_it[3] += d

    def unpaired(p, q, r, t):
        gone = []
        while p < q:
            # The subtree, if it's all gone.
            end = next((k for k in range(p + 1, len(a)) if a[k].depth <= a[p].depth), len(a))
            whole = end <= q
            gone.append(("-", a[p], f" and {end - p - 1} inside" if whole and end > p + 1
                         else ""))
            p = end if whole else p + 1
        runs.append(gone + [("+", n, "") for n in b[r:t]])

    pair(0, len(a), 0, len(b), lambda n: (n.depth, n.role, n.name, n.description),
         lambda p, q, r, t: pair(p, q, r, t, lambda n: (n.depth, n.role), unpaired))
    # Changes in more than one window say which, above each window's: by
    # its place too, as two can be the same.
    window = {}
    for t in (a, b):
        count = 0
        for n in t:
            if not n.depth:
                count += 1
                top = f"in {n.line()} (window {count}" + (f", pid {n.pid}" if "pid" in n else "")
            window[id(n)] = top + "):"
    many = len({window[id(n)] for run in runs for _, n, _ in run if n.depth}) > 1
    out, said = [], None
    for run in runs:
        above = []  # (depth, indent) of what's printed above that a node can be in
        for k, (mark, n, w) in enumerate(run):
            if many and window[id(n)] != said:
                said = window[id(n)]
                if n.depth:
                    out.append(said)
                    above = []
            elif k and mark != run[k - 1][0]:
                above = []
            while above and above[-1][0] >= n.depth:
                above.pop()
            above.append((n.depth, above[-1][1] + 1 if above else 0))
            out.append(f"{mark} {'  ' * above[-1][1]}{n.line()}"
                       + (f", was {w}" if mark == "~" else w))
    return [f"! {n.line()}, so its windows aren't compared"
            for n in after if n.get("quiet")] + out


def toplevels(Atspi, window=None, timeout=10):
    """Each (application, toplevel) on the bus, in a stable order:
    applications by name, their windows as they list them. `window`, as
    everywhere: a title regex, or app=RE, which goes through the display's
    own idea of app ids to a title; it's waited for, up to `timeout`."""
    if not window:
        return find_toplevels(Atspi)
    what = f"an accessible {x.matcher(window)[1]}"
    try:
        return wait_for(what, lambda: find_toplevels(Atspi, window), timeout)
    except SessionError as e:
        raise SessionError(f"{e}{Screen().there(window)}") from None


def find_toplevels(Atspi, window=None):
    @cache
    def shown():
        """The Screen, and the display's window `window` picks there, or None."""
        screen = Screen()
        return screen, screen.backend.find_window(screen.env, window)

    # Only an unnamed window needs this, so app=RE alone never asks for it.
    @cache
    def shown_pid():
        screen, found = shown()
        return screen.backend.window_pid(screen.env, found[0]) if found else None

    desktop = Atspi.get_desktop(0)
    apps = []
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None:
            continue
        apps.append((app.get_name() or "", i, app))

    def picks(got):
        if window.startswith("app="):
            return bool(got) and shown()[1] is not None and got == shown()[1][1]
        return x.pattern(window)[0](got)

    hits = []
    for name, _, app in sorted(apps, key=lambda a: a[:2]):
        count = app.get_child_count()
        if count < 0:   # no answer, after libatspi's timeout: busy, or hung
            QUIET.add(app.get_process_id())
        tops = [t for t in (app.get_child_at_index(j) for j in range(count))
                if t is not None]
        names = [t.get_name() or "" for t in tops]
        if window is None:
            hits += [(app, t) for t in tops]
            continue
        # A window with no name of its own (a toolkit's window with no title)
        # is picked by a pattern that matches an empty name; otherwise it goes
        # by the display's, picked when its program owns the window picked
        # there, unless one of the program's named windows is picked.
        unnamed = "" in names and (picks("") or (
            not any(picks(n) for n in names if n)
            and app.get_process_id() == shown_pid()))
        hits += [(app, t) for t, got in zip(tops, names) if (picks(got) if got else unnamed)]
    return hits


# The pids of the programs that didn't answer when their windows were asked for.
QUIET = set()


def origin(Atspi, top, shown):
    """Where `top` is on the screen, where its children's window
    coordinates start, and the display's window it is: ((x, y, width,
    height), (x, y), window), or None if that can't be told. A node's
    extents are in its window's coordinates (GTK 4 has no others), so
    they're put on the screen by the display's window of the same size,
    and name if it has one: `shown`, as the backend's windows() lists
    them. Chromium on Wayland counts its shadow into its accessible frame
    and not its window, and can't be placed. GTK 4 from 4.14, to 4.24 at
    least, loses the window's border (on X11 with no compositor, it has
    one) from its children: the frame's first child, which fills it but
    for that, is at the frame's corner rather than inside the border. From
    4.14.6 the border and padding of every other node on the way down are
    lost too, and can't be told: docs/accessibility.md says how far off
    that is."""
    e = top.get_extents(Atspi.CoordType.WINDOW)
    name = top.get_name() or ""
    app = top.get_application()
    version = (app.get_toolkit_version() or "") if app and app.get_toolkit_name() == "GTK" else ""
    gtk4 = version.startswith("4.")
    # GTK 4 before 4.14 says where its window's content is: inside the
    # window's own border, as wide all round (5 pixels, its solid-csd).
    inside = gtk4 and int(version.split(".")[1]) < 14

    def fits(w):
        grown = w[2] - e.width
        return ((grown == 0 or inside and 0 < grown <= 20 and grown % 2 == 0)
                and grown == w[3] - e.height)

    hits = [w for w in shown if fits(w) and (not name or w[1] == name)]
    if len(hits) > 1 and isinstance(hits[0][0], str):   # X11's unmapped windows
        env = Screen().env
        hits = [w for w in hits if x.viewable(env, w[0])]
    if len(hits) != 1:
        return None
    w = hits[0]
    lost = ((w[2] - e.width) // 2, (w[3] - e.height) // 2)
    # GTK 4 alone: anywhere else a first child at the corner and smaller
    # (a GTK 3 button aligned to the start) is where it says.
    first = top.get_child_at_index(0) if gtk4 and not inside and top.get_child_count() else None
    c = first and first.get_extents(Atspi.CoordType.WINDOW)
    if c and sized(c) and (c.x, c.y) == (e.x, e.y) and c.width < e.width and c.height < e.height:
        lost = ((e.width - c.width) // 2, (e.height - c.height) // 2)
    return [w[4], w[5], w[2], w[3]], (w[4] - e.x + lost[0], w[5] - e.y + lost[1]), w


# The most an X11 window can be. A size past it, or below zero, is none a
# node can have: GTK 4 sends uninitialized memory for a widget it hasn't
# realized. The width is what catches it (-1346821116, 744334356): the
# height can be the other half of a pointer, about 22000, which fits.
LARGEST = 32767


def sized(e):
    """Whether extents `e` have a size a node can be drawn at."""
    return 0 <= e.width <= LARGEST and 0 <= e.height <= LARGEST


def place(Atspi, n, shown, origins):
    """`n`'s bounds on the screen, [x, y, width, height], or None. `origins`
    caches origin() for each toplevel."""
    if n._top not in origins:
        try:
            origins[n._top] = origin(Atspi, n._top, shown)
        except Exception:   # a window closed since the walk
            origins[n._top] = None
    o = origins[n._top]
    if o is None:
        return None
    if n._acc == n._top:
        return o[0]
    try:
        e = n._acc.get_extents(Atspi.CoordType.WINDOW)
    except Exception:   # gone since the walk: a closing popover, a toast
        return None
    return [o[1][0] + e.x, o[1][1] + e.y, e.width, e.height] if sized(e) else None


def role_name(Atspi, acc, role):
    """`acc.get_role_name()` for its `role`, without asking for that again:
    libatspi names the roles it knows itself."""
    if 0 <= role <= Atspi.Role.LAST_DEFINED and role != Atspi.Role.EXTENDED:
        return Atspi.role_get_name(role)
    return acc.get_role_name()


def node(Atspi, acc, states, role, path):
    shown = [s for s in STATES
             if states.contains(getattr(Atspi.StateType, s.upper().replace("-", "_")))]
    if states.contains(Atspi.StateType.EXPANDABLE) and not states.contains(
            Atspi.StateType.EXPANDED):
        shown.append("collapsed")
    if not states.contains(Atspi.StateType.SENSITIVE):
        shown.append("disabled")
    # GTK 4 before 4.10 names what has no name by its class.
    name = acc.get_name() or ""
    if re.fullmatch(r"Gtk[A-Z]\w*", name) and gtk4_release(Node(_acc=acc)) < (4, 10):
        name = ""
    # A field GTK 3 names by its mnemonic label: a screen reader says that.
    if not name and "focusable" in shown:
        name = " ".join(t.get_name() for t in related(Atspi, acc, "LABELLED_BY") if t.get_name())
    return Node(role=role_name(Atspi, acc, role), name=name,
                description=acc.get_description() or "", states=shown, path=path,
                _role=int(role),
                _showing=states.contains(Atspi.StateType.SHOWING),
                # Paths alone collide: GTK 3 numbers its objects per program.
                _id=f"{acc.app.bus_name if acc.app else ''}{acc.path}",
                _acc=acc)


def walk(Atspi, window=None, timeout=10, bounds=False, until=None):
    """Every showing node (in a page, visible) under the toplevels `window`
    picks, depth first, as (depth, Node); with `until`, up to the first
    it's true of. With `bounds`, each has its bounds on the screen, or
    None where they can't be told."""
    out = []

    def done():
        return until and out and until(out[-1][1])

    def visit(acc, role, depth, path, web=False):
        if len(out) > 20000 or depth > 200:
            return
        # A toolkit's widget can be visible and not on the screen (in a
        # revealer not revealed); a page's is not showing only scrolled
        # out of view, as a toolkit's list row is still showing. Each
        # call is a round trip, so what isn't shown isn't asked more. GTK 4
        # before 4.10.3 says only its windows are showing; it presents
        # only what's visible, as later ones say is showing.
        web = web or role == Atspi.Role.DOCUMENT_WEB
        try:
            states = acc.get_state_set()
            old = not web and gtk4_release(Node(_acc=acc)) < (4, 10, 3)
            if not states.contains(Atspi.StateType.VISIBLE if web or old
                                   else Atspi.StateType.SHOWING):
                return
            n = node(Atspi, acc, states, role, path)
            if old:
                n["_showing"] = True
        except Exception:   # gone while we looked: a closing popover, a toast
            return
        n["_top"] = top
        out.append((depth, n))
        for i in range(acc.get_child_count()):
            if done():
                return
            child = acc.get_child_at_index(i)
            if child is not None:
                role = child.get_role()
                visit(child, role, depth + 1, f"{path}/{role_name(Atspi, child, role)}[{i}]", web)

    for _, top in toplevels(Atspi, window, timeout):
        if done():
            break
        # libatspi caches states, and hears they've changed only from
        # events, which nothing here dispatches.
        try:
            top.clear_cache()
            paged(Atspi, top, window, timeout)
            role = top.get_role()
            path = f"{role_name(Atspi, top, role)} {json.dumps(top.get_name() or '')}"
        except SessionError:   # paged()'s, about the window asked for
            raise
        except Exception:   # gone while we looked: a window closing
            continue
        visit(top, role, 0, path)
    if bounds:
        screen, origins = Screen(), {}
        shown = screen.backend.windows(screen.env)
        for _, n in out:
            n["bounds"] = place(Atspi, n, shown, origins)
            n["_window"] = origins[n._top] and origins[n._top][0]
    return out


# The browser windows paged() has looked at: Chromium's mode, once on,
# stays on for the browser's life.
PAGED = set()


def paged(Atspi, top, window, timeout):
    """Have a Chromium browser window build its page's part of the tree,
    and wait for it. Chromium builds its tree past the window only for a
    screen reader, and takes a node's attributes being asked for as one
    at work: Orca asks, and few else do. A window with no page (a crashed
    tab), or whose page isn't built in `timeout`, is noted; the one
    `window` asks for fails, if it has nothing at all or its page isn't
    built."""
    key = f"{top.app.bus_name if top.app else ''}{top.path}"
    app = top.get_application()
    if key in PAGED or not app or app.get_toolkit_name() != "Chromium":
        return
    PAGED.add(key)
    if (top.get_attributes() or {}).get("class") != "BrowserRootView":
        return

    def page():
        top.clear_cache()
        level = [top]
        for _ in range(10):
            level = [c for n in level for c in (n.get_child_at_index(i)
                                                 for i in range(n.get_child_count())) if c]
            docs = [c for c in level if c.get_role() == Atspi.Role.DOCUMENT_WEB]
            if docs:
                return docs[0]

    def built():
        doc = page()
        return doc and doc.get_attributes()

    name = json.dumps(top.get_name() or "")
    try:
        wait_for("", page, 2, interval=0.2)
        # A page's document is first an empty stand-in, with no attributes,
        # until its tree is built: for a big page, a while. about:blank's
        # stays one.
        if not (top.get_name() or "").startswith("about:blank"):
            wait_for("", built, timeout, interval=0.2)
    except SessionError:
        if page():
            why = (f"{name}: the page's accessible tree wasn't built in {timeout:g}s; "
                   "a longer --timeout gives it longer")
            fail = window
        else:
            why = (f"{name}: no page in the browser's accessible tree (a crashed tab, or a "
                   "browser started with --disable-platform-accessibility-integration, "
                   "which builds none for a reader; --force-renderer-accessibility builds "
                   "it anyway)")
            fail = window and not top.get_child_count()
        if fail:
            raise SessionError(why) from None
        sys.stderr.write(f"shotbox: {why}\n")


def tree(window=None, timeout=10, bounds=False):
    Atspi = atspi()
    return [Node(n, depth=d) for d, n in walk(Atspi, window, timeout=timeout, bounds=bounds)]


def focused(Atspi, window=None):
    """The node with the keyboard focus, or None."""
    out = walk(Atspi, window, until=lambda n: "focused" in n.states)
    return out[-1][1] if out and "focused" in out[-1][1].states else None


def roles(Atspi, names):
    return {int(getattr(Atspi.Role, r)) for r in names if hasattr(Atspi.Role, r)}


def ignored(ignore, web=False):
    """A test of a problem, (node, what), for the regexes in `ignore`: one
    found in its "PATH: WHAT" or in its tree line's "LINE: WHAT", and
    with `web`, in either with the DOM ending its line has (placed())."""
    try:
        found = [re.compile(i) for i in ignore]
    except re.error as e:
        raise SessionError(f"ignore takes regexes, and {e.pattern!r} isn't one: {e}") from None
    Atspi = web and found and atspi()

    def skip(n, what):
        if Atspi:
            dom(Atspi, n)
        return any(r.search(f"{head}: {what}{end}") for r in found
                   for head in (n.path, n.line()) for end in {"", placed(n)})
    return skip


def lint(Atspi, nodes, ignore=()):
    """(node, what's wrong) for each showing control, image or focusable node
    a screen reader would have no name to say for. An image inside a named
    control is part of it: the control's name is what gets said. Anything
    with a name inside it is said by what's in it, and a container with
    something focusable inside passes the focus on to it."""
    skip = ignored(ignore, web=True)
    controls = roles(Atspi, CONTROLS)
    containers = roles(Atspi, CONTAINERS)
    by_path = {n.path: n for n in nodes}
    said_inside, focus_inside = set(), set()
    for n in nodes:
        for inside, holds in ((said_inside, n.name), (focus_inside, "focusable" in n.states)):
            path = n.path
            while holds and "/" in path:
                path = path.rsplit("/", 1)[0]
                inside.add(path)

    def in_named_control(n):
        path = n.path
        while "/" in path:
            path = path.rsplit("/", 1)[0]
            up = by_path.get(path)
            if up and up._role in controls and up.name:
                return True
        return False

    problems = []
    for n in nodes:
        if n.name or undrawn(Atspi, n):
            continue
        if n._role in controls or (n._role == Atspi.Role.TEXT and "editable" in n.states):
            problems.append((n, f"{n.role} with no name"))
        elif n._role == Atspi.Role.IMAGE and not in_named_control(n):
            problems.append((n, "image with no name (a decorative one should be "
                                "role presentation, which leaves it out)"))
        elif ("focusable" in n.states and n.path not in said_inside
              and not (n._role in containers and n.path in focus_inside)):
            problems.append((n, f"focusable {n.role} with no name"))
    return [(n, what) for n, what in problems if not skip(n, what)]


def related(Atspi, acc, kind):
    """The nodes `acc` has a relation of `kind` ("LABELLED_BY") to."""
    try:
        return [t for r in acc.get_relation_set()
                if r.get_relation_type() == getattr(Atspi.RelationType, kind)
                for t in (r.get_target(i) for i in range(r.get_n_targets())) if t]
    except Exception:   # gone while we looked
        return []


def undrawn(Atspi, n):
    """Whether `n` says it has no size: GTK 3's -1 by -1 for what it
    doesn't draw (a notebook's tab, with its tabs hidden), though it's
    showing."""
    try:
        e = n._acc.get_extents(Atspi.CoordType.WINDOW)
    except Exception:   # gone while we looked
        return True
    return (e.width, e.height) == (-1, -1)


# The ways out of a text view, which takes Tab for itself: GTK's key
# (forward, so the walk goes on), Qt's (back: GTK's types a tab), Escape
# (an editor's search bar), and a pane's, last: Kate and KWrite show a
# view with it.
ESCAPES = ("ctrl+Tab", "shift+Tab", "Escape", "F6")


def tab_walk(key="Tab", most=50, window=None, settle=2.0, timeout=10, visible=False,
             document=False):
    """Press `key` up to `most` times and note where the focus lands each
    time. (nodes, how it ended, what it never reached, the text view it
    began in or None): "cycle" once it lands somewhere it has been
    (where it began, or a stop since), "trap" if a press leaves it where
    it was, "lost" if no node has it after one, "max" if none of those;
    "unfocused" if no node has it to begin with, after `settle` (X11 with no
    window manager), and nothing is pressed into that. A press that leaves
    it on the one thing in the window that could take it is a cycle of one.
    `key` isn't pressed in a text view one writes in, which takes Tab for
    itself, but first in a web page's, as a textarea doesn't: ESCAPES are,
    noted on it as `tried`, the one that moves the focus as `left_by`, one
    after which its text changed as `edited`; none is a trap, and one after
    which the text view isn't there ends the walk, "closed". Escape isn't
    tried in a dialog, which it would close, nor shift+Tab but in Qt; once
    that leaves, backward, the walk goes on from the first thing after the
    text view that takes the focus (grab_focus). A cycle with no stop in a
    web page after one of a web page's was left so is "escaped", and
    Escape is pressed, back to the page.
    Where the walk began in one nothing leaves, it gives the focus to the
    first thing outside it that takes it (AT-SPI's grab_focus, which GTK 4
    doesn't do), so the rest of the window is walked before the trap. With
    `visible`, each node says how much of the screen round it changed as it
    took the focus (see shown()). With `document`, the nodes, what it never
    reached and the text view are only those in the window's web documents
    (but the documents), though the walk goes through the browser's own
    controls too, and a trap on a web document with nothing in the page it
    never reached is a cycle; a window with no web document is refused."""
    Atspi = atspi()
    screen = Screen()

    def press(k, before):
        """Press `k`, if any, and once the focus has left `before` for a
        node, or after `settle`, the node it's on, or None."""
        if k:
            screen.key(k)
        end = time.monotonic() + settle
        while True:
            now = focused(Atspi, window)
            if (now is not None and (before is None or now._id != before._id)
                    or time.monotonic() > end):
                return now
            time.sleep(0.05)

    toplevels(Atspi, window, timeout)
    docs = document and [n.path + "/" for _, n in walk(Atspi, window, timeout)
                         if n._role == Atspi.Role.DOCUMENT_WEB]
    if document and not docs:
        screen.close()
        raise SessionError("no web document to walk: the window isn't a browser's")

    def mine(n):
        return not document or (n.path.startswith(tuple(docs))
                                and n._role != Atspi.Role.DOCUMENT_WEB)

    start = press(None, None)
    if start is None:
        screen.close()
        return [], "unfocused", [], None
    began = start if writing(Atspi, start) else None
    seen = []
    if visible:
        screen.park()
        look = Look(Atspi, screen, start)

    def leave(n):
        """Try ESCAPES on `n`, a text view: where the focus went, if it
        left (None for nowhere a node says), and None, "trap" or
        "closed"."""
        n["tried"], n["left_by"] = [], None
        dialog, qt = in_dialog(Atspi, n), toolkit(n).startswith("Qt")
        text = contents(Atspi, n)
        # A web page's textarea doesn't take Tab, and a browser's ctrl+Tab
        # and F6 go to its other tabs and its toolbar: they're for a web
        # editor that does.
        if "/document web[" in n.path:
            now = press(key, n)
            if now is None or now._id != n._id:
                del n["tried"], n["left_by"]
                return now, None
            n["tried"].append(key)
            if contents(Atspi, n) != text:
                n["edited"], text = key, contents(Atspi, n)
        for k in ESCAPES:
            if k == key or (dialog and k == "Escape") or (k == "shift+Tab" and not qt):
                continue
            n["tried"].append(k)
            now = press(k, n)
            if not showing(Atspi, n):
                return now, "closed"
            if contents(Atspi, n) != text:
                n["edited"], text = k, contents(Atspi, n)
            if now is None or now._id != n._id:
                n["left_by"] = k
                if k == "shift+Tab":
                    after = focus_outside(Atspi, window, n, settle, after=True)
                    if after is not None:
                        n["walked_from"], now = public(after), after
                return now, None
        return None, "trap"

    def go():
        nonlocal start
        # Left a text view for what no node says has the focus (gedit's
        # ctrl+Tab), from where the next press goes on.
        lost = False
        for _ in range(most):
            before = seen[-1] if seen else start
            if not lost and writing(Atspi, before):
                now, end = leave(before)
                if end == "trap" and before is began and not seen:
                    # Walked from there, it's a trap when the walk comes back.
                    start = now = focus_outside(Atspi, window, before, settle)
                    if now is not None:
                        before["walked_from"] = public(now)
                        continue
                if end:
                    return seen or [before], end
                if now is None:
                    lost = True
                    continue
            else:
                now = press(key, None if lost else before)
                if lost and now is None:
                    continue
                if not lost and (now is None or now._id == before._id):
                    if now is None:
                        return seen, "lost"
                    return seen or [now], "cycle" if alone(Atspi, window, now) else "trap"
            lost = False
            been = [n._id for n in ([start] if start else []) + seen]
            if visible:
                look.took(now)
            seen.append(now)
            if now._id in been:
                return seen, "cycle"
        return seen, "max"

    try:
        seen, how = go()
        # Left a web page's text view by a browser's key (Chromium's F6, to
        # its toolbar's pane), and Tab went round the browser's controls,
        # never back in the page. Escape takes Chromium back to it (F6 does
        # too, but leaves Tab stuck there).
        walked = [n for n in (start, *seen) if n]
        out = next((i for i in range(len(walked) - 1, -1, -1)
                    if "/document web[" in walked[i].path and walked[i].get("left_by")), None)
        if how == "cycle" and out is not None and not any(
                "/document web[" in n.path for n in walked[out + 1:]):
            how = "escaped"
            screen.key("Escape")
    finally:
        screen.close()
    # Said only where the walk left it; else it's the trap.
    began = began if began and mine(began) and (began.get("left_by")
                                                 or began.get("walked_from")) else None
    missed = [n for n in unreached(Atspi, window, [n for n in (start, *seen) if n and mine(n)])
              if mine(n)]
    # A page's document keeping the focus, with nothing in the page to walk.
    if document and how == "trap" and seen[-1]._role == Atspi.Role.DOCUMENT_WEB and not missed:
        how = "cycle"
    return [n for n in seen if mine(n)], how, missed, began


def writing(Atspi, n):
    """Whether `n` is a text view one writes in, which takes Tab."""
    try:
        return n is not None and "editable" in n.states and n._acc.get_state_set().contains(
            Atspi.StateType.MULTI_LINE)
    except Exception:   # gone since
        return False


def toolkit(n):
    try:
        return n._acc.get_application().get_toolkit_name() or ""
    except Exception:   # gone
        return ""


def contents(Atspi, n):
    # Not asked of what has none: GTK 3 logs a CRITICAL each time.
    try:
        return (Atspi.Text.get_text(n._acc, 0, -1) if "Text" in n._acc.get_interfaces()
                else None)
    except Exception:   # gone since
        return None


def showing(Atspi, n):
    try:
        n._acc.clear_cache()
        return n._acc.get_state_set().contains(Atspi.StateType.SHOWING)
    except Exception:   # gone
        return False


def in_dialog(Atspi, n):
    """Whether `n` is in a dialog or alert (libadwaita's, in its window's
    frame, too)."""
    dialogs = roles(Atspi, ("DIALOG", "ALERT", "FILE_CHOOSER"))
    acc = n._acc
    try:
        while acc is not None:
            if int(acc.get_role()) in dialogs:
                return True
            acc = acc.get_parent()
    except Exception:   # gone
        return True
    return False


def focus_outside(Atspi, window, n, settle, after=False):
    """Give the focus to the first node outside `n` (`after` it) that takes
    it (AT-SPI's grab_focus), and the node it's then on, or None."""
    containers = roles(Atspi, CONTAINERS)
    nodes = [o for _, o in walk(Atspi, window)]
    if after:
        nodes = nodes[next((i for i, o in enumerate(nodes) if o._id == n._id), len(nodes)):]
    for o in nodes:
        if ("focusable" in o.states and "disabled" not in o.states and o._role not in containers
                and o._id != n._id and not o.path.startswith(n.path + "/")):
            try:
                Atspi.Component.grab_focus(o._acc)
            except Exception:   # gone, or it won't
                continue
            end = time.monotonic() + settle
            while time.monotonic() < end:
                now = focused(Atspi, window)
                if now is not None and now._id != n._id:
                    return now
                time.sleep(0.05)
    return None


# Roles a group of which takes one Tab stop, the rest reached by arrows.
GROUPED = ("RADIO_BUTTON", "PAGE_TAB", "MENU_ITEM", "CHECK_MENU_ITEM", "RADIO_MENU_ITEM")

# A table or list's focusable items, and what they're in, which takes a
# Tab stop for them all.
ITEMS = ("TABLE_CELL", "LIST_ITEM", "TREE_ITEM")
LISTS = ("TABLE", "TREE_TABLE", "TREE", "LIST", "LIST_BOX")


def unreached(Atspi, window, seen):
    """The showing, sensitive controls and fields the focus never landed
    on, nor on anything round them, nor in them but for a container's,
    nor on another of their group beside them (a tab, a radio button),
    but for a menu bar's (F10's), nor in a web document it landed on
    (Chromium's Tab does, coming in from the toolbar); and each table or list with an item
    that takes the focus, the focus never in it."""
    controls, grouped = roles(Atspi, CONTROLS), roles(Atspi, GROUPED)
    containers, items, lists = roles(Atspi, CONTAINERS), roles(Atspi, ITEMS), roles(Atspi, LISTS)
    menu_bar, docs = roles(Atspi, ("MENU_BAR",)), roles(Atspi, ("DOCUMENT_WEB",))
    nodes = [n for _, n in walk(Atspi, window)]
    by_path = {n.path: n for n in nodes}
    ids = {n._id for n in seen}

    def ups(n):
        path = n.path
        while "/" in path:
            path = path.rsplit("/", 1)[0]
            if path in by_path:
                yield by_path[path]

    def near(n, o):
        return ((n.path.startswith(o.path + "/") and o._role not in containers | docs)
                or o.path.startswith(n.path + "/")
                or (n._role in grouped and n._role == o._role
                    and n.path.rsplit("/", 1)[0] == o.path.rsplit("/", 1)[0]))

    def entered(n):
        return n._id in ids or any(o.path.startswith(n.path + "/") for o in seen)

    out = []
    for n in nodes:
        if "disabled" in n.states or n._id in ids:
            continue
        if n._role in items and "focusable" in n.states:
            box = next((u for u in ups(n) if u._role in lists), None)
            if box and box not in out and not entered(box):
                out.append(box)
        elif ((n._role in controls or (n._role == Atspi.Role.TEXT and "editable" in n.states))
              and not any(near(n, o) for o in seen)
              and not any(u._role in menu_bar for u in ups(n))):
            out.append(n)
    return out


class Look:
    """Pictures of the screen as the focus moves, to tell whether it shows
    where it went."""

    def __init__(self, Atspi, screen, start):
        self.Atspi, self.screen = Atspi, screen
        self.shown, self.origins = screen.backend.windows(screen.env), {}
        self.last = self.grab()
        self.was = (self.where(start), None) if start else (None, None)

    def grab(self):
        return picture.Picture(*self.screen.backend.pixels(self.screen.env))

    def where(self, n):
        return place(self.Atspi, n, self.shown, self.origins)

    def took(self, n):
        """Note on `n`, which has just taken the focus, how many pixels
        changed in and round it ("changed") and how many it takes to show
        it ("needed"), or that it's outside the screen ("off_screen")."""
        try:
            wait_for("", x.Still(self.screen.env, quiet=0.3, backend=self.screen.backend), 2)
        except SessionError:
            pass
        now = self.grab()
        box = self.where(n)
        if box and not now.clip(box):
            n["off_screen"] = True
        # A document takes the focus as a whole, and nobody draws round it;
        # a container shows it on what it passes it to (a notebook's tab).
        elif box and n._role not in roles(self.Atspi, ("DOCUMENT_WEB", "DOCUMENT_FRAME",
                                                        *CONTAINERS)):
            n["changed"], drawn = focus_change(self.last, now, box, *self.was)
            n["needed"] = shown_needs(box)
        n["bounds"] = box
        self.last = now
        self.was = (box, drawn) if not unseen(n) and "changed" in n else (None, None)


# How far outside its node a focus ring can be drawn.
RING = 4

# How far from its bounds a node can be drawn. GTK 4 places a node short of
# the border and padding of the nodes it's in (docs/accessibility.md, "Where
# a node is"): from 4.14.6 right of and below them (a header bar's buttons
# 7px, a tab bar's close buttons 10px), before that left of and above.
REACH = 16

# How wide a band just outside a found ring is looked at for change that
# isn't the ring's: a neighbor's selection, a spinner.
MARGIN = 4


def offsets():
    """Every offset up to REACH each way, the nearest first."""
    out = [(dx, dy) for dy in range(-REACH, REACH + 1) for dx in range(-REACH, REACH + 1)]
    return sorted(out, key=lambda d: abs(d[0]) + abs(d[1]))


def grow(box, by):
    x, y, w, h = box
    return (x - by, y - by, x + w + by, y + h + by)


def meet(a, b):
    """Where two (x0, y0, x1, y1) areas meet; empty (x0 >= x1) if they don't."""
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def focus_change(before, after, box, was=None, drawn=None):
    """How many pixels in and round `box` changed from `before` to `after`
    as the focus came to it, and where what's counted is: (x0, y0, x1, y1),
    or None if nothing. First at its bounds; GTK 4 can draw a node up to
    REACH from them, so then at any offset where what changed looks like a
    focus shown: change along three or four of the node's sides (a ring, or
    a rail and a ring, or a fill) and little just outside them (not a
    neighbor's selection). The focus the node it left showed isn't counted:
    `was` is where that node is, if its focus showed, and `drawn` where,
    or None where the walk began, not knowing it. What's in this node's
    own box counts if `was` holds it (a list's row, inside the list)."""
    reach = grow(box, REACH + RING + MARGIN)
    area = before.clip((reach[0], reach[1], reach[2] - reach[0], reach[3] - reach[1]))
    if not area:
        return 0, None
    plain = picture.Changes(before, after, area)
    if was and not drawn:
        drawn = left_drawn(before, after, area, was, plain)
    if drawn:
        # Not what's well inside the ring: a node there drew that.
        inner = (drawn[0] + RING, drawn[1] + RING, drawn[2] - RING, drawn[3] - RING)
        changes = picture.Changes(before, after, area, drawn, inner)
        holds = meet(grow(was, 0), grow(box, 0)) == grow(box, 0)
    else:
        changes, holds = plain, False
    n, (dx, dy) = shown_at(changes, box, holds)
    return n, tight(changes, grow((box[0] + dx, box[1] + dy, box[2], box[3]), RING))


def tight(changes, rect):
    """The least (x0, y0, x1, y1) in `rect` that holds all that changed in
    it, or None."""
    x0, y0, x1, y1 = rect
    if not changes.count(rect):
        return None
    while not changes.count((x0, y0, x0 + 1, y1)):
        x0 += 1
    while not changes.count((x1 - 1, y0, x1, y1)):
        x1 -= 1
    while not changes.count((x0, y0, x1, y0 + 1)):
        y0 += 1
    while not changes.count((x0, y1 - 1, x1, y1)):
        y1 -= 1
    return x0, y0, x1, y1


def left_drawn(before, after, area, was, changes):
    """Where the ring round `was`, which the focus has just left, was drawn:
    what changed in `area` round the outline the size of `was` that lost
    the most, if as much as half a one-pixel outline did and what's there
    now looks like what's round it; else None. A node that showed no ring
    loses nothing, but the next one's ring can be where its outline is."""

    def outline(dx, dy):
        moved = (was[0] + dx, was[1] + dy, was[2], was[3])
        inner = grow(moved, -RING)
        n = changes.count(grow(moved, RING))
        return n - changes.count(inner) if inner[0] < inner[2] and inner[1] < inner[3] else n

    dx, dy = max(offsets(), key=lambda d: outline(*d))
    moved = (was[0] + dx, was[1] + dy, was[2], was[3])
    if outline(dx, dy) * 2 < outline_length(grow(moved, 0), area):
        return None
    return went(before, after, area, moved)


def outline_length(ring, area):
    """How long the one-pixel outline of `ring` is in `area`, both (x0, y0,
    x1, y1)."""
    across = max(0, min(ring[2], area[2]) - max(ring[0], area[0]))
    down = max(0, min(ring[3], area[3]) - max(ring[1], area[1]))
    return (across * sum(area[1] <= y < area[3] for y in (ring[1], ring[3] - 1))
            + down * sum(area[0] <= x < area[2] for x in (ring[0], ring[2] - 1)))


def went(before, after, area, ring):
    """Where what changed in a band round the edge of `ring`, a box, went
    rather than came, as (x0, y0, x1, y1): what's now more like what didn't
    change just round the band than it was, by how much of that has its
    color. None if less went than came."""
    def band(outer, inner):
        x0, y0, x1, y1 = meet(outer, area)
        for y in range(y0, y1):
            for x in range(x0, x1):
                if not (inner[0] <= x < inner[2] and inner[1] <= y < inner[3]):
                    i = (y * before.width + x) * 3
                    yield x, y, before.data[i:i + 3], after.data[i:i + 3]

    # By how much, since the ring's color can be round it too (a checked box).
    near = Counter(b for _, _, b, a in [*band(grow(ring, RING + 2), grow(ring, RING)),
                                        *band(grow(ring, -RING), grow(ring, -RING - 2))]
                   if b == a)
    changed = [(x, y, near[b], near[a])
               for x, y, b, a in band(grow(ring, RING), grow(ring, -RING)) if b != a]
    gone = [(x, y) for x, y, was, now in changed if now > was]
    if len(gone) <= sum(was > now for _, _, was, now in changed):
        return None
    xs, ys = [x for x, _ in gone], [y for _, y in gone]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def shown_at(changes, box, holds):
    """The count for `box` in `changes`, and the offset it's at: at its
    bounds, or the best offset where the change looks like a focus shown,
    if more; even when its bounds have enough, the next node needs to know
    where its ring really was. What `changes` skips counts only inside the
    box, and only if `holds`."""
    x, y, w, h = box

    def counted(rect, inside):
        n = changes.count(rect) - changes.count(rect, changes.skipped)
        return n + (changes.count(meet(rect, inside), changes.skipped) if holds else 0)

    def at(dx, dy):
        inside = grow((x + dx, y + dy, w, h), 0)
        return counted(grow((x + dx, y + dy, w, h), RING), inside), inside

    found, _ = at(0, 0)
    best = (0, 0)
    for dx, dy in offsets()[1:]:
        n, inside = at(dx, dy)
        if n <= found:
            continue
        bx, by, bx1, by1 = inside
        sides = [((bx - RING, by - RING, bx1 + RING, by + RING), w),
                 ((bx - RING, by1 - RING, bx1 + RING, by1 + RING), w),
                 ((bx - RING, by - RING, bx + RING, by1 + RING), h),
                 ((bx1 - RING, by - RING, bx1 + RING, by1 + RING), h)]
        if sum(counted(band, inside) * 2 >= length for band, length in sides) < 3:
            continue
        outside = counted(grow((bx, by, w, h), RING + MARGIN), inside) - n
        if outside * 2 > w + h:
            continue
        found, best = n, (dx, dy)
    return found, best


def shown_needs(box):
    """How many pixels must change for the focus to show: half a one-pixel
    outline round the node, where WCAG 2.2's focus appearance asks for two
    pixels all round. Half, since GTK 4 before 4.14.6 places a node past
    its padding, and part of its ring outside what's counted."""
    return box[2] + box[3]


def unseen(n):
    """Whether a walk with `visible` found the focus not shown on `n`."""
    return n.get("changed") is not None and n.changed < n.needed


def raise_window(screen, window):
    """Raise the display's window `window` picks, if it can be found, so
    nothing covers what's clicked or measured in it."""
    if window and screen.backend.find_window(screen.env, window):
        screen.focus_window(window)


def ratio_text(r):
    """`r` to two places, rounded down, so a ratio that fails never prints
    as one that passes."""
    return f"{math.floor(r * 100) / 100:.2f}"


def click(name, role=None, window=None, button=1, double=False, timeout=10, force=False):
    """Click the node `at_node` finds; that node."""
    return at_node(name, role, window, timeout,
                   lambda s, x, y: s.click(x, y, button=button, double=double), force)


def scroll(name, dy, dx=0, role=None, window=None, timeout=10, force=False):
    """Turn the wheel over the node `at_node` finds; that node."""
    wheel(dy, dx)
    return at_node(name, role, window, timeout, lambda s, x, y: s.scroll(x, y, dy, dx), force)


def at_node(name, role, window, timeout, act, force=False):
    """Do `act` (a Screen, x, y) at the middle of the node named `name`, and
    with role `role` if given, each as x.pattern() matches a name, waiting
    up to `timeout` for it to show, placed in its window and on the
    screen, and unless `force`, to be ready (see unready), its window
    raised. Nodes named exactly
    `name` are taken over those it matches as a regex: a calculator's "."
    is its button, not any one character. A match inside another (a
    button's label) is the outer one. The node."""
    Atspi = atspi()
    (named, note), (as_role, role_note) = x.pattern(name), x.pattern(role or "")
    showing, late = [], []
    screen = Screen()

    def matches():
        showing[:] = [n for _, n in walk(Atspi, window, timeout) if n._showing]
        nodes = [n for n in showing if named(n.name) and (role is None or as_role(n.role))]
        nodes = [n for n in nodes if n.name == name] or nodes
        # A field named by its label is the one of the two that's taken.
        ids = {n._id for n in nodes}
        return [n for n in nodes if not any(n.path.startswith(o.path + "/") for o in nodes)
                and not ids & {f"{t.app.bus_name if t.app else ''}{t.path}"
                               for t in related(Atspi, n._acc, "LABEL_FOR")}]

    def found():
        """The node, where it's clicked and its window's origin(), once it's ready."""
        late.clear()
        hits = matches()
        if not hits:
            return None
        origins = {}
        shown = screen.backend.windows(screen.env)
        for n in hits:
            n["bounds"] = place(Atspi, n, shown, origins)
        if len(hits) > 1:
            raise SessionError(f"{len(hits)} nodes match {name!r}:\n  "
                               + "\n  ".join(n.line(bounds=True) for n in hits)
                               + "\nNarrow it with a role or a window, or click one where it is.")
        n = hits[0]
        box = n.bounds
        # A browser puts a node in its tree a moment before it places it.
        why = None
        if not box or box[2] <= 0 or box[3] <= 0:
            why = "has no position yet"
        else:
            # Inside its window: a node scrolled out of view is still showing.
            inside = overlap(box, origins[n._top][0])
            px, py, w, h = overlap(inside, [0, 0, screen.xt.width, screen.xt.height])
            if not inside[2] or not inside[3]:
                why = "is outside its window"
            elif not w or not h:
                why = "is off-screen"
            point = (px + w // 2, py + h // 2)
        why = why or not force and unready(Atspi, screen, n, point, origins[n._top], shown)
        if why:
            late[:] = [n, why]
            return None
        return n, point, origins[n._top]

    what = f"a node named {name!r}{note}" + (f" with role {role!r}{role_note}" if role else "")
    try:
        n, (px, py), at = wait_for(what, found, timeout)
    except SessionError as e:
        if late and str(e).startswith("gave up"):
            raise SessionError(f"gave up after {timeout:g}s: {late[0].line()} {late[1]}"
                               ) from None
        near = not any(named(n.name) for n in showing) and difflib.get_close_matches(
            name, {n.name for n in showing if n.name}, 5)
        if not near:
            raise
        raise SessionError(f"{e}. The nearest names: "
                           + ", ".join(json.dumps(n, ensure_ascii=False) for n in near)) from None
    try:
        screen.backend.focus(screen.env, at[2])
        act(screen, px, py)
    finally:
        screen.close()
    return n


def around(text, start, end, most=80):
    """`text` cut to `most` characters round its `start`:`end`, with … where
    it's cut: a page's text can run to megabytes."""
    a = max(0, min((start + end - most) // 2, len(text) - most, start))
    return "…" * (a > 0) + text[a:a + most] + "…" * (a + most < len(text))


def unembedded(text):
    """`text` without the U+FFFC that stands in a page's text for each
    element in it, and the space round one made one, so `Done <a>here</a>
    now` reads "Done now". Not with the element's text in its place: that
    takes round trips an element, and those of each in it, each poll; and
    the element's text is matched on its own."""
    return re.sub(r"\s*\ufffc[\s\ufffc]*", lambda m: " " if (
        m.start() and m.end() < len(text) and m[0].strip("\ufffc")) else "", text)


def wait_node(pattern, window=None, timeout=10):
    """Wait for a showing node with `pattern`, a regex, found in its name
    or text; that node, with its text round the match as `text` where
    that's what matched. Giving up, say what came nearest: a match that
    isn't showing, else the nearest names and texts, or that there were
    none."""
    if not pattern:
        raise SessionError("wait node needs a regex to look for: an empty one matches any node")
    Atspi = atspi()
    try:
        found = re.compile(pattern)
    except re.error as e:
        raise SessionError(f"{pattern!r} isn't a regex: {e}") from None
    seen = []

    def matches(n):
        if found.search(n.name):
            return True
        n["_text"] = unembedded(contents(Atspi, n) or "")
        m = found.search(n._text)
        if m:
            n["text"] = around(n._text, *m.span())
            return True
        return False

    def there():
        seen[:] = [n for _, n in walk(Atspi, window, timeout)]
        return next((n for n in seen if n._showing and matches(n)), None)

    what = f"a node matching {pattern!r}"
    try:
        return wait_for(what, there, timeout)
    except SessionError as e:
        if not str(e).startswith(f"gave up waiting for {what}"):
            raise
        hidden = next((n for n in seen if not n._showing and matches(n)), None)
        if hidden:
            text = (f", its text {json.dumps(hidden.text, ensure_ascii=False)},"
                    if "text" in hidden else "")
            raise SessionError(f"{e}: {hidden.line()}{text} matches, but isn't showing") from None
        # Shorter, anything is near.
        if len(pattern) < 3:
            raise SessionError(str(e)) from None
        # A long text in pieces of whole words, about twice the pattern's
        # length, each compared and said on its own: whole, it's never near.
        width, said = 2 * len(pattern), {}
        for t in {t for n in seen for t in (n.name, n.get("_text")) if t}:
            words = t.split() if len(t) > 80 else [t]
            for i in range(len(words)):
                j, length = i + 1, len(words[i])
                while j < len(words) and length < width:
                    length, j = length + 1 + len(words[j]), j + 1
                piece = " ".join(words[i:j])
                said.setdefault(piece, "…" * (i > 0) + around(piece, 0, 0)
                                + "…" * (j < len(words)))
        near = difflib.get_close_matches(pattern, said, 5)
        raise SessionError(f"{e}. The nearest: " + ", ".join(
            json.dumps(said[t], ensure_ascii=False) for t in near) if near
            else f"{e}: nothing in the tree comes near it") from None


# How long a node's bounds are to hold before it's taken as still: a few
# frames, so one part way through a move is seen moving.
STILL = 0.05


def unready(Atspi, screen, n, point, at, shown):
    """Why `n` can't be clicked at `point` yet, or None: it isn't enabled,
    its bounds change in STILL, or something is over it there. In GTK 4's
    windows, what's there is what GTK would pick, which is what's drawn on
    top: another node (an overlay, libadwaita's dialogs) covers it. GTK 3
    answers with the first child there, whatever's drawn over it, so
    only GTK 4 is asked. On X11, a shown window over its own, `at[2]`,
    that's transient for it (a dialog, kept over it where there's a
    window manager) covers it; `shown` is in X's stacking order, top
    first. Sway raises the window it focuses over the rest."""
    if "disabled" in n.states:
        return "isn't enabled"
    time.sleep(STILL)
    if place(Atspi, n, shown, {n._top: at}) != n.bounds:
        return "is moving"
    top = ident(n._top)
    if gtk4(n):
        try:
            got = n._top.get_accessible_at_point(point[0] - at[1][0], point[1] - at[1][1],
                                                 Atspi.CoordType.WINDOW)
            up, acc = [], got
            while acc is not None and ident(acc) not in (n._id, top):
                up.append(acc)
                acc = acc.get_parent()
            if up and acc is not None and ident(acc) == top and ident(got) not in ancestors(n):
                over = next((a for a in up if a.get_name()), got)
                return (f"is covered by {over.get_role_name()} "
                        f"{json.dumps(over.get_name() or '', ensure_ascii=False)}")
        except Exception:   # gone since
            pass
    own = at[2][0]
    if isinstance(own, str):
        for w in shown:
            if w[0] == own:
                break
            if (0 <= point[0] - w[4] < w[2] and 0 <= point[1] - w[5] < w[3]
                    and x.transient_for(screen.env, w[0]) == own and x.viewable(screen.env, w[0])):
                return f"is covered by window {json.dumps(w[1], ensure_ascii=False)}"
    return None


def ident(acc):
    """What tells `acc` from the rest, as a Node's `_id` does."""
    return f"{acc.app.bus_name if acc.app else ''}{acc.path}"


def ancestors(n):
    """The idents of the nodes `n` is in, up to its toplevel."""
    out, acc = set(), n._acc.get_parent()
    while acc is not None and ident(acc) != ident(n._top):
        out.add(ident(acc))
        acc = acc.get_parent()
    return out


def modals(Atspi, nodes):
    """Each of `nodes` behind a modal dialog, by its path, and that dialog,
    a Node. A modal window shuts off every other window of its program,
    covered or not (GTK's don't say which is their parent); a modal dialog
    or alert in a window, on the screen (aria-modal's, which shuts off its
    page alone), everything else there, but not a modal popover, which
    dims nothing. libadwaita's dialogs, drawn in their window, are always
    modal and don't say so: a GTK 4 dialog or alert there is one in the
    shapes libadwaita draws them in (layered()), and only what's before
    it, drawn under it, is behind. An alert anywhere else (an error
    label, a toast, a status page) isn't one, nor is a box or popover
    given a dialog's role. Of dialogs over dialogs, the last, outermost,
    is in front."""
    def inside(path, outer):
        return path == outer or path.startswith(outer + "/")

    tops = {ident(n._top): n.path for n in nodes if n._acc == n._top}
    content = {}
    for n in nodes:
        if n._acc != n._top and n.path.rsplit("/", 1)[0] == tops.get(ident(n._top)):
            content.setdefault(ident(n._top), n.bounds)
    paths = {n.path: n for n in nodes}
    siblings = {}
    for n in nodes:
        up, step = n.path.rsplit("/", 1) if "/" in n.path else ("", n.path)
        at = re.search(r"\[(\d+)\]$", step)
        if at:
            siblings[up, int(at[1])] = n

    def layer(o):
        """Whether `o` is a layer over its window's content: as big as it,
        and after something as big, showing and another object (not a
        stack's page GTK lists twice, nor a banner folded away)."""
        size = (content.get(ident(o._top)) or [])[2:] if o else None
        if not size or not o.bounds or o.bounds[2:] != size:
            return False
        up, step = o.path.rsplit("/", 1)
        at = int(re.search(r"\[(\d+)\]$", step)[1])
        before = at and siblings.get((up, at - 1))
        return bool(before and before._showing and before.bounds
                    and before.bounds[2:] == size and before._id != o._id)

    def layered(n):
        """Whether `n` is libadwaita's dialog: a layer itself (1.10's, and
        1.5's alert dialog), or 1.5's sheet, three down from its layer."""
        return layer(n) or (n._role == Atspi.Role.DIALOG
                            and n.path.endswith("/panel[0]/panel[0]/dialog[1]")
                            and layer(paths.get(n.path.rsplit("/", 3)[0])))

    dialogs = roles(Atspi, ("DIALOG", "ALERT"))
    fronts = {}
    for i, n in enumerate(nodes):
        if (n._acc == n._top or n._role not in dialogs or not n._showing or not n.bounds
                or not all(n.bounds[2:])):
            continue
        under = "modal" not in n.states and gtk4(n) and layered(n)
        if "modal" not in n.states and not under:
            continue
        scope = next((o.path for o in reversed(nodes[:i]) if o._role == Atspi.Role.DOCUMENT_WEB
                      and inside(n.path, o.path)), tops.get(ident(n._top)))
        if scope not in fronts or not inside(n.path, fronts[scope][0].path):
            # libadwaita 1.5 names its dialog's node nothing: said by the first
            # name in it, which is its title if it shows one.
            name = n.name or next((o.name for o in nodes[i:] if inside(o.path, n.path)
                                   and o.name), "")
            fronts[scope] = Node(n, name=name), i if under else len(nodes)
    behind = {n.path: m for i, n in enumerate(nodes) for scope, (m, last) in fronts.items()
              if i < last and inside(n.path, scope) and not inside(n.path, m.path)
              and not inside(m.path, n.path)}
    apps = {}
    for n in nodes:
        if n._top.app:
            apps.setdefault(n._top.app.bus_name, n._top.get_application())
    for bus, app in apps.items():
        front = None
        try:
            for t in (app.get_child_at_index(j) for j in range(max(app.get_child_count(), 0))):
                states = t and t.get_state_set()
                if states and states.contains(Atspi.StateType.MODAL) and states.contains(
                        Atspi.StateType.SHOWING):
                    role = t.get_role()
                    front = node(Atspi, t, states, role,
                                 f"{role_name(Atspi, t, role)} {json.dumps(t.get_name() or '')}")
        except Exception:   # gone while we looked: a window closing
            continue
        behind.update({n.path: front for n in nodes if front and n._top.app
                       and n._top.app.bus_name == bus and ident(n._top) != front._id})
    return behind


def contrast(window=None, text=4.5, graphics=3.0, edges=None, timeout=10, ignore=(),
             large=3.0, strict=False):
    """What's drawn with too little contrast, as (node, what), measured on a
    picture of the screen, `window` raised, in each node's bounds: text (a
    web node's own, run by run, runs()), and a button, link, tab
    or menu item, which may draw text or an icon and can't be told apart,
    against its background (at least `text`, or `large` for large text);
    an image against its own (`graphics`); and
    with `edges`, a control's edge against what's round it. Disabled nodes
    and what's in them are left out, as WCAG leaves them, and so are a
    switch, slider, check box or the like, and what's outside its window
    (scrolled out of view). A node that can't be placed, and text with
    nothing drawn on its background, are problems too: nothing says they
    can be read, and so is GTK 4's text not found where nothing has the
    contrast it needs. But for those ignored() leaves out. (problems, what
    passes, what wasn't measured but doesn't fail, as (node, what): text too small
    to, what's outside the screen, and GTK 4's, see GTK4_BOUNDS, or with
    `strict` only what's scrolled out of view, the rest being problems
    too; what's behind a modal dialog (modals()), not measured, as (node,
    what), which doesn't fail even with `strict`: shut off till it's
    closed, and dimmed on purpose or covered by it; why GTK 4's weren't,
    a line each; how many were measured). A web page with text, none of it
    measured, is a note."""
    skip = ignored(ignore, web=True)
    Atspi = atspi()
    toplevels(Atspi, window, timeout)
    screen = Screen()
    try:
        raise_window(screen, window)
        screen.park()
    finally:
        screen.close()
    nodes = [n for _, n in walk(Atspi, window, timeout, bounds=True)]
    fronts = modals(Atspi, nodes)
    texts, controls = roles(Atspi, TEXTS), roles(Atspi, CONTROLS)
    named = roles(Atspi, ("LABEL", "HEADING", "LINK", "STATIC", "CAPTION"))
    images, textless, edged = (roles(Atspi, ("IMAGE", "ICON")), roles(Atspi, TEXTLESS),
                               roles(Atspi, EDGED))
    # A link draws its text, as other controls may not.
    unworded = (controls - {int(Atspi.Role.LINK)}) | images
    parents = {n.path: n for n in nodes}
    ids = {n._id: n for n in nodes}
    layers = {}

    def within(path, outer):
        return path == outer or path.startswith(outer + "/")

    def disabled(n):
        path = n.path
        while "/" in path:
            path = path.rsplit("/", 1)[0]
            if path in parents and "disabled" in parents[path].states:
                return True
        return "disabled" in n.states

    kids, worded, kits = {}, {}, {}
    for n in nodes:
        kids.setdefault(n.path.rsplit("/", 1)[0], []).append(n)

    def wording(n):
        """Whether `n` is a web node measured by the runs of its own text
        (runs()): Firefox keeps a page's text in what it's in (a
        paragraph, a list item, a link), Chromium in a static node for each
        run, and says where else its text is wrong (an option's). Not one
        whose text is its static children's, nor a control or image,
        measured as one."""
        if n.path not in worded:
            if n._top not in kits:
                kits[n._top] = toolkit(n)
            text = ("/document web[" in n.path and n._role not in unworded and (
                n._role == Atspi.Role.STATIC or kits[n._top] != "Chromium")
                and contents(Atspi, n) or "").replace("\ufffc", "")
            worded[n.path] = any(c.isalnum() for c in text) and not any(
                o._role == Atspi.Role.STATIC and o.name.strip() and o.name.strip() in text
                for o in kids.get(n.path, ()))
        return worded[n.path]

    since = []   # when a character was first asked of that had no box yet

    def runs(n):
        """A Node for each line of each run of `n`'s own text in one look,
        each with its `_run`, its text, its `_look`, its attributes, and
        `bounds`, where its first and last characters drawn are, or None
        if that can't be told. Not what's between runs: the objects in it
        (U+FFFC), and pictographs (emoji), drawn in their own colors. A
        line is where those characters are at one height, found by
        halving; not by a range's extents, which in Firefox takes in an
        object it ends at, nor its lines, which in Firefox are the whole
        paragraph. Only characters drawn are asked of: whitespace, a line
        break and zero-width ones have no box of their own. Both browsers
        say nothing of some till a moment after they're first asked, so
        up to a second, in all, is waited for that; and Chromium's window
        coordinates for text start elsewhere than for its nodes, so it's
        asked in the screen's, taken to the walk's bounds. Right to left
        text, which may be drawn in another order, is asked of whole."""
        acc, text, out, i, boxes = n._acc, contents(Atspi, n) or "", [], 0, {}
        try:
            e = acc.get_extents(Atspi.CoordType.SCREEN)
        except Exception:   # gone since the walk
            return out
        at = n.bounds and (n.bounds[0] - e.x, n.bounds[1] - e.y)

        def box(j):
            if j not in boxes:
                r = Atspi.Text.get_character_extents(acc, j, Atspi.CoordType.SCREEN)
                if r.height <= 0 and not since:
                    since.append(time.monotonic())
                while r.height <= 0 and time.monotonic() < since[0] + 1:
                    time.sleep(0.05)
                    r = Atspi.Text.get_character_extents(acc, j, Atspi.CoordType.SCREEN)
                boxes[j] = [r.x, r.y, r.width, r.height] if r.height > 0 else None
            return boxes[j]

        def halves(drawn):
            # Firefox gives no box to some characters near a zero-width one:
            # they're left out, but for a run of none, which can't be placed.
            boxed = [j for j in (drawn[0], drawn[-1]) if box(j)]
            while len(boxed) < 2 and len(drawn) > 2:
                drawn = drawn[not box(drawn[0]):len(drawn) - (not box(drawn[-1]))]
                boxed = [j for j in (drawn[0], drawn[-1]) if box(j)]
            if len(boxed) < 2 or box(drawn[0])[1] == box(drawn[-1])[1]:
                return [drawn]
            return halves(drawn[:len(drawn) // 2]) + halves(drawn[len(drawn) // 2:])

        def lines(drawn):
            """`drawn`, split where its characters change height."""
            got = []
            for half in halves(drawn):
                if got and box(got[-1][0]) and box(half[0]) and box(got[-1][0])[1] == box(
                        half[0])[1]:
                    got[-1] = got[-1] + half
                else:
                    got.append(half)
            return got

        def apart(j, k):
            """Whether an object or a pictograph is between `j` and `k`."""
            return any(text[m] == "\ufffc" or unicodedata.category(text[m]) == "So"
                       for m in range(j + 1, k))

        while i < len(text):
            try:
                look, _, end = Atspi.Text.get_attribute_run(acc, i, True)
                end = max(end, i + 1)
                parts = [[]]
                for j in range(i, end):
                    if text[j].isspace() or unicodedata.category(text[j]) in (
                            "Cc", "Cf", "Mn", "Me", "So") or text[j] == "\ufffc":
                        continue
                    if parts[-1] and apart(parts[-1][-1], j):
                        parts.append([])
                    parts[-1].append(j)
                found = [line for part in parts if part for line in lines(part)]
            except Exception:   # gone since the walk
                return out
            for line in found:
                span = text[line[0]:line[-1] + 1]
                if not any(c.isalnum() for c in span):
                    continue
                rtl = any(unicodedata.bidirectional(c) in ("R", "AL") for c in span)
                got = [box(j) for j in (line if rtl else (line[0], line[-1])) if box(j)]
                x0 = min(b[0] for b in got) if got else None
                y0 = min(b[1] for b in got) if got else None
                out.append(Node(n, _run=span, _look=look or {}, bounds=[
                    x0 + at[0], y0 + at[1], max(b[0] + b[2] for b in got) - x0,
                    max(b[1] + b[3] for b in got) - y0] if at and x0 is not None else None))
            i = end
        return out

    def kind(n):
        # A canvas's fallback text isn't what it draws.
        if (not n._showing or not (n.name or wording(n)) or "editable" in n.states
                or re.search(r"/document web\[\d+\]/(.*/)?canvas\[\d+\](/|$)", n.path)
                or disabled(n)
                or n._role in textless or gtk3_switch(Atspi, n)):
            return None
        if n._role in images:
            return "graphics"
        return "text" if n._role in texts or n._role in controls or wording(n) else None

    def shown(n):
        """`n`'s bounds cut to its window's and every node's it's inside, or
        None if nothing of them is left to measure: a row scrolled out of
        view, which GTK still calls showing, is cut to nothing by its
        scrolled window. GTK 4 places a child as if its parent had no
        padding, which can put its box past the parent's edge, and that
        is cut off too."""
        if not n.bounds:
            return None
        box, path = n.bounds, n.path
        outers = [n._window]
        while "/" in path:
            path = path.rsplit("/", 1)[0]
            up = parents.get(path)
            outers.append(up and up.bounds)
        for outer in outers:
            if outer:
                box = overlap(box, outer)
        return box if box[2] > 0 and box[3] > 0 else None

    def paged_out(n):
        """Whether `n` is on a stack's page that isn't shown, which GTK 4
        before 4.14 still says is where it was: asked what's at its
        middle, GTK says a node of that stack, or one it's in, but not
        on `n`'s page (a stack's pages are the nodes with no bounds). A
        shown page's own child GTK can't pick answers the stack, so it's
        left out too, with its line."""
        page = n.path
        while "/" in page:
            page = page.rsplit("/", 1)[0]
            if page in parents and parents[page].bounds is None:
                break
        else:
            return False
        try:
            e = n._acc.get_extents(Atspi.CoordType.WINDOW)
            got = n._top.get_accessible_at_point(e.x + e.width // 2, e.y + e.height // 2,
                                                 Atspi.CoordType.WINDOW)
        except Exception:   # gone since the walk
            return False
        o = got and ids.get(f"{got.app.bus_name if got.app else ''}{got.path}")
        stack = page.rsplit("/", 1)[0]
        return bool(o) and not within(o.path, page) and (
            within(o.path, stack) or within(stack, o.path))

    def popped(n):
        """Whether `n` is in a popover, or is one."""
        path = n.path
        while "/" in path:
            if path not in layers:
                try:
                    layers[path] = parents[path]._acc.get_layer() == Atspi.ComponentLayer.POPUP
                except Exception:   # no bounds: a stack's page
                    layers[path] = False
            if layers[path]:
                return True
            path = path.rsplit("/", 1)[0]
        return False

    def hovered(box):
        """The screen with the pointer over the middle of `box`, once the
        sampler's done, whose pictures it would change."""
        sampler.join()
        s = Screen()
        try:
            s.move(box[0] + box[2] // 2, box[1] + box[3] // 2)
            try:
                wait_for("", x.Still(s.env, quiet=0.3, backend=s.backend), 2)
            except SessionError:
                pass
            return picture.Picture(*s.backend.pixels(s.env))
        finally:
            s.park()
            s.close()

    def framed(n, box):
        """Whether what `n` is in draws its edge for it, as a frame does a
        text view's: the nearest node round it with an edge, of those it
        fills all round, but not one at its window's edge, whose edge is
        the window's. Filling it only side to side isn't enough: a form's
        fields do that in a bordered box, each looking like its inside."""
        win, path = n._window, n.path
        while "/" in path:
            path = path.rsplit("/", 1)[0]
            outer = parents.get(path) and shown(parents[path])
            if not outer:
                continue
            if not all(abs(box[i] - outer[i]) <= REACH
                       and abs(box[i] + box[i + 2] - outer[i] - outer[i + 2]) <= REACH
                       for i in (0, 1)):
                return False
            if win and overlap(grow_box(outer, 3), list(win)) != grow_box(outer, 3):
                return False
            # Or drawn just outside it, as a box shadow is.
            if any(got and got[0] >= edges for got in (
                    picture.edge_contrast(pic, outer), picture.edge_contrast(pic, grow_box(outer, 2)))):
                return True
        return False

    # Firefox says where a page's node is a moment after it's in the tree.
    unplaced = [n for n in nodes if "/document web[" in n.path and kind(n) and not n.bounds]
    deadline = time.monotonic() + 1
    if unplaced:
        s, origins = Screen(), {}
        try:
            shown_ = s.backend.windows(s.env)
            while unplaced and time.monotonic() < deadline:
                time.sleep(0.1)
                for n in unplaced:
                    n["bounds"] = place(Atspi, n, shown_, origins)
                unplaced = [n for n in unplaced if not n.bounds]
        finally:
            s.close()
    measured = [r for n in nodes if kind(n) for r in (runs(n) if wording(n) else [n])]
    problems, passes, notes, behind, whys, count, taken = [], [], [], [], [], 0, []
    uncounted = set()   # what's measured only by its pictures, if at all
    trusted = os.environ.get("SHOTBOX_TRUST_GTK4_BOUNDS") == "1"
    rel = {}

    def relative(n):
        """`n`'s extents in its parent's, as GTK says them."""
        if n.path not in rel:
            try:
                e = n._acc.get_extents(Atspi.CoordType.PARENT)
                rel[n.path] = (e.x, e.y, e.width, e.height)
            except Exception:   # gone since the walk
                rel[n.path] = None
        return rel[n.path]

    areas, fonts, drawings, lays, refused, aparts, doubt = {}, {}, {}, {}, {}, {}, {}

    def room(n):
        """Where `n`'s text can be on GTK 4.14 and later, which says where
        it is wrong, [x, y, width, height], cut to its window; or None. GTK
        can count a stack's page's place twice, the page's and its
        child's, which is taken off where it does. Up to
        4.14.5 GTK says where a widget's content starts, and its text is
        inside that box; from 4.14.6, where its edge is less every
        container's border and padding, which can only put it further left
        and up than it is, by no more than the room each container has
        round what's in it."""
        if n.path in areas:
            return areas[n.path]
        b, win = n.bounds, n._window
        x, y, more = (b[0], b[1], [0, 0]) if b and win else (0, 0, None)
        path, child = n.path, n
        while more and "/" in path:
            path = path.rsplit("/", 1)[0]
            up = parents.get(path)
            if not up or up._acc == n._top:
                break
            mine, its = relative(child), relative(up)
            if not mine or not its:
                more = None
            # A page: not a widget, so no actions, with its child's extents.
            # Its child is placed from it, counted twice, unless GTK sums
            # past the page, as for libadwaita 1.5's view stack.
            elif (mine == its and up._acc.get_child_count() == 1
                    and "Action" not in up._acc.get_interfaces() and child.bounds and up.bounds
                    and child.bounds[:2] == [up.bounds[0] + its[0], up.bounds[1] + its[1]]):
                x, y = x - its[0], y - its[1]
            elif gtk4_release(n) >= (4, 14, 6):
                for i in (0, 1):
                    reach = max([o[i] + o[i + 2] for o in map(relative, kids[path]) if o],
                                default=0)
                    more[i] += its[i + 2] - min(max(reach, 0), its[i + 2])
            child = up
        areas[n.path] = more and overlap([x, y, b[2] + more[0], b[3] + more[1]], list(win))
        return areas[n.path]

    def out_of_view(n):
        """Whether where `n`'s text can be is all outside its window, or a
        scrolled window it's in, which shows nothing outside it (a row
        scrolled out of view, which GTK calls showing); not in a popover,
        which can be drawn outside both."""
        if popped(n):
            return False
        area, path = room(n), n.path
        while area and all(area[2:]) and "/" in path:
            path = path.rsplit("/", 1)[0]
            up = parents.get(path)
            if up and up._role == Atspi.Role.SCROLL_PANE:
                area = overlap(area, room(up) or area)
        return bool(area) and not all(area[2:])

    def font(n):
        if n.path not in fonts:
            try:
                fonts[n.path] = Atspi.Text.get_attribute_run(n._acc, 0, True)[0] or {}
            except Exception:   # no text, or gone since the walk
                fonts[n.path] = None
        return fonts[n.path]

    def drawing(text, attrs, dy=0):
        key = (text, tuple(sorted(attrs.items())), dy)
        if key not in drawings:
            drawings[key] = text_shapes(text, attrs, dy)
        return drawings[key]

    def texts_of(o):
        """What `o` may draw: its name, unless it's a label's it's named by,
        and what's in it, if it's edited or a label, whose name can be set
        to other than what it draws."""
        got = []
        if o.name and not any(t.get_name() == o.name
                              for t in related(Atspi, o._acc, "LABELLED_BY")):
            got.append(o.name)
        if "editable" in o.states or o._role in named:
            try:
                got.append(Atspi.Text.get_text(o._acc, 0, -1) or "")
            except Exception:   # no text, or gone since the walk
                pass
        try:
            got.append((o._acc.get_attributes() or {}).get("placeholder-text", ""))
        except Exception:   # gone since the walk
            pass
        return list(dict.fromkeys(t for t in got if t))

    def as_wide(n, shape):
        """Whether the text GTK lays out for `n` is as wide as its name drawn
        as `shape` (find_text's), within a pixel and a fiftieth (the sizes
        shapes are drawn at, a third of a pixel apart, drift from GTK's
        over a long text): not when GTK draws it otherwise (upper case,
        letter-spaced), and what's found is the name drawn by something
        else. GTK says how wide from 4.14.6 (before, the text is looked
        for in its own box alone), from 4.22.3 a character short; text not
        all ASCII isn't looked for there (see unsure)."""
        if gtk4_release(n) < (4, 14, 6):
            return True
        end = len(n.name) - (gtk4_release(n) >= (4, 22, 3))
        if not end:
            return True   # one with no case: see lone
        wide = laid(n) and laid(n)[2]
        kinds = wide and drawing(n.name[:end], font(n))
        return bool(kinds) and abs(kinds[shape[0]][shape[1]][0] - 4 - wide) <= 1 + wide / 50

    def laid(n):
        """Where GTK lays out `n`'s text in its own box, (x, y, width), from
        4.14.6 (before, it doesn't say); or None, as for one character
        from 4.22.3, whose range is empty, at the box's corner."""
        if n.path not in lays:
            lays[n.path] = None
            if gtk4_release(n) >= (4, 14, 6):
                try:
                    r = Atspi.Text.get_range_extents(n._acc, 0, Atspi.Text.get_character_count(
                        n._acc), Atspi.CoordType.WINDOW)
                    e = n._acc.get_extents(Atspi.CoordType.WINDOW)
                    if r.width > 0:
                        lays[n.path] = (r.x - e.x, r.y - e.y, r.width)
                except Exception:   # no extents, or gone since the walk
                    pass
        return lays[n.path]

    def lone(n):
        """Whether `n`'s text is one letter, which GTK from 4.22.3 gives no
        width (a character short): casing alone can turn one into another,
        not spacing, so only one with no case (a digit, a "+") is looked
        for."""
        return (gtk4_release(n) >= (4, 22, 3) and len(n.name) == 1
                and n.name.upper() != n.name.lower())

    def unsure(n):
        """Whether how wide GTK lays out `n`'s text can't be told: from
        4.14.6, for text not all ASCII, GTK turns characters into the
        layout's bytes backwards, so the range it measures ends short by
        however many, and nearly any width would do."""
        return gtk4_release(n) >= (4, 14, 6) and not n.name.isascii()

    def seek(n, until, near=None):
        """Where `n`'s text is drawn, for GTK 4.14 and later: (its box, how
        far its corner is from where GTK says its text is, or up to 4.14.5,
        which doesn't say, its box, and where and as what shape it's drawn,
        for picture.text_contrast), or None if it isn't found, alone, where
        it can be (room, or `near`, part of it), or another node's text
        there could be what's found."""
        where, attrs = near or room(n), font(n)
        area = where and overlap(where, [0, 0, pic.width, pic.height])
        kinds = (area and area[2] > 0 and area[3] > 0 and attrs is not None
                 and drawing(n.name, attrs))
        seen_area = area and area[2] > 0 and area[3] > 0
        area = kinds and (area[0], area[1], area[0] + area[2], area[1] + area[3])

        def apart(a, b):
            """The better of two searches' runners-up, and where it is."""
            return max((a[5], a[7]), (b[5], b[7]))

        def find(area, under=None):
            got = picture.find_text(pic, kinds, area, until, under)
            shape = got and kinds[got[6][0]][got[6][1]]
            if got and got[0] < MATCH and gtk4_release(n) >= (4, 16):
                # GTK 4.16 and later place text at fractions of a pixel, so a
                # line set by its baseline can fall between rows.
                k, i = got[6]
                phased = [drawing(n.name, attrs, dy)[k][i] for dy in (-0.25, 0.25, 0.5)]
                again = picture.find_text(pic, [phased], area, until, under)
                if again and again[0] > got[0]:
                    shape = phased[again[6][1]]
                    r, at = apart(got, again)
                    got = again[:5] + (r, got[6], at)
            return got, shape
        got, shape = find(area) if kinds else (None, None)
        under = None
        if got and got[0] < MATCH:
            # Faint text can be so little of what varies where it is that a
            # grid's lines drawn through it are much of the rest: looked
            # for again just round there, less them. What else matches,
            # here or anywhere, is the more of with them and without.
            x, y, w, h = got[1:5]
            under = picture.backdrop(pic, (x, y, x + w, y + h))
            again, drawn = find((max(area[0], x - 1), max(area[1], y - 3),
                                 min(area[2], x + w + 1), min(area[3], y + h + 3)), under)
            if again and again[0] > got[0]:
                r, at = apart(got, again)
                got, shape = again[:5] + (r, again[6], at), drawn
        if not got or got[0] < MATCH:
            refused[n.path] = ("unmatched", f"no match good enough: {got[0]:.2f} at best" if got
                               else "where it can be is too small or too big to look in"
                               if kinds else "GTK says no font for it" if attrs is None
                               else "where it can be is off the screen" if not seen_area
                               else "it can't be drawn to look for")
            return None
        if got[5] >= MATCH_ELSEWHERE:
            places = [got[1:3], got[7]]
            boxes = [[x + 2, y, got[3] - 4, got[4]] for x, y in places]
            others = [drawers(b, n.name, n) for b in boxes]
            like = others[1]
            why = ("twice", "it matches elsewhere too" + (
                f", in {called(*like[0])}" if like
                else f", {got[5]:.2f}: the same text twice, or like it"))
            # A node draws its text once, so where no other node could have
            # drawn one place (drawers, blank too) and another with the same
            # text could the other, this one's is the first, looked for
            # again just there, but taken only if it fails (doubt). Text
            # inside a longer one ("check" in "unchecked") is left as
            # matching twice.
            for i in (0, 1):
                if not near and not drawers(boxes[i], n.name, n, blank=True) and any(
                        s.lower() == n.name.lower() for _, s in others[1 - i]):
                    x, y = places[i]
                    doubt[n.path] = why
                    seen = seek(n, until, overlap([x - 1, y - 3, got[3] + 2, got[4] + 6], where))
                    if not seen:
                        refused[n.path] = why
                    return seen
            refused[n.path] = why
            return None
        if not as_wide(n, got[6]):
            refused[n.path] = ("wide", "what matches is drawn wider or narrower than GTK "
                                       "lays it out")
            return None
        box = [got[1] + 2, got[2], got[3] - 4, got[4]]
        for o in nodes:
            if o is n or within(o.path, n.path) or within(n.path, o.path):
                continue
            there = room(o)
            if not there or not all(overlap(there, box)[2:]):
                continue
            for t in texts_of(o):
                if time.monotonic() > until:
                    raise TimeoutError
                other = drawing(t, font(o) or attrs)
                # A shorter text inside this one can't match all of it, if
                # it's drawn where it is too.
                if (other and t.lower() != n.name.lower() and t.lower() in n.name.lower()
                        and drawn_apart(o, t, other, box, until)):
                    continue
                if (max(picture.match_over(pic, other, box, until, u) for u in {None, under})
                        >= MATCH_ELSEWHERE if other
                        else n.name.lower() in t.lower()):
                    why = ("other", f"another node's text matches there too: {called(o, t)}")
                    # Nor does text holding this one's drawn apart where no
                    # other node could have, but then it's taken only if it
                    # fails (doubt).
                    if (other and n.name.lower() in t.lower()
                            and drawn_apart(o, t, other, box, until, alone=True)):
                        doubt.setdefault(n.path, why)
                        continue
                    refused[n.path] = why
                    return None
        at = laid(n) or (0, 0)
        return box, tuple(box[i] - n.bounds[i] - at[i] for i in (0, 1)), (got[1], got[2], shape)

    def drawers(box, t, of, blank=False):
        """The nodes but `of`, and what it's in or holds, whose room holds
        the middle of `box` and whose text holds `t`, (node, that text): the
        one whose room starts nearest it first, as GTK places each further
        left and up than it is. With `blank`, any with nothing in it that
        isn't a label too, whatever its text (a drawing area, an image, a
        text view), which can draw anything: a label draws its name."""
        mid = [box[0] + box[2] // 2, box[1] + box[3] // 2, 1, 1]
        return [(nodes[i], s) for _, i, s in sorted(
            ((room(o)[1], room(o)[0]), i, s) for i, o in enumerate(nodes)
            if not (within(o.path, of.path) or within(of.path, o.path))
            and room(o) and overlap(room(o), mid) == mid
            for s in texts_of(o) + ([""] if blank and o.path not in kids
                                    and o._role not in named else [])
            if not s or t.lower() in s.lower())[::-1]]

    def drawn_apart(o, t, kinds, box, until, alone=False):
        """Whether `o`'s text `t`, drawn as `kinds`, matches MATCH_ELSEWHERE
        or more where `o`'s can be (room), at a place apart from `box`; with
        `alone`, one no other node could have drawn (drawers)."""
        key = o.path, id(kinds)   # drawing() keeps each kinds
        if key not in aparts:
            area = room(o) and overlap(room(o), [0, 0, pic.width, pic.height])
            aparts[key] = area and all(area[2:]) and picture.find_text(
                pic, kinds, (area[0], area[1], area[0] + area[2], area[1] + area[3]), until)
        got = aparts[key]
        if not got:
            return False
        for match, (x, y) in ((got[0], got[1:3]), (got[5], got[7])):
            there = [x + 2, y, got[3] - 4, got[4]]
            if all(overlap(there, box)[2:]):
                continue
            return match >= MATCH_ELSEWHERE and not (alone and drawers(there, t, o, blank=True))
        return False

    def called(o, t):
        """`o`'s text `t`, as a line says it: by `o`'s name where that's
        not it."""
        return f'"{t}"' if t == o.name else f'the {o.role} "{o.name}", whose text is "{t}"'

    def faint(n, least):
        """For `n`'s text, not found or not taken: the most contrast any text
        where it can be (room) can measure (picture.most_contrast), if
        that's less than `least`, so whatever text is there is fainter; or
        None. Not for text too small to measure, as some shape it can be
        found as is, nor where it can be outside what's shown: in a
        popover, past the screen's edge, or past a scrolled window's, or
        where GTK places it even partly outside its window or what it's
        in (a carousel's page not shown)."""
        area, attrs = room(n), font(n)
        dys = (0, -0.25, 0.25, 0.5) if gtk4_release(n) >= (4, 16) else (0,)
        kinds = [drawing(n.name, attrs, dy) for dy in dys] if area and attrs is not None else None
        covers = kinds and None not in kinds and [c for k in kinds for kind in k for _, _, c in kind]
        if (not covers or popped(n) or not all(area[2:]) or shown(n) != n.bounds
                or overlap(area, [0, 0, pic.width, pic.height]) != area
                or min(len(c) - c.count(0) for c in covers) < SMALLEST):
            return None
        path = n.path
        while "/" in path:
            path = path.rsplit("/", 1)[0]
            up = parents.get(path)
            if up and up._role == Atspi.Role.SCROLL_PANE and overlap(area, room(up) or area) != area:
                return None
        # Covering no pixel whole, text measures as the color its coverage
        # steps toward, as far again as its most covered pixel is short.
        most = picture.most_contrast(pic, area, 255 / min(max(c) for c in covers))
        return most if most < least else None

    def leaf(n):
        return not any(o.path.startswith(n.path + "/") for o in measured)

    pic = picture.Picture(*screen.backend.pixels(screen.env))
    shot, samples, stop = time.monotonic(), [], threading.Event()

    def searched(n):
        """Whether `n`'s text is looked for where it's drawn: GTK 4's, from
        4.14."""
        return (n._role in texts and n._role not in controls and gtk4(n)
                and gtk4_release(n) >= (4, 14) and not trusted)

    # Where each node measured is, or GTK 4's text can be, cut to the
    # screen: all a sample keeps of what it pictures.
    regions = {}
    for n in measured:
        region = pic.clip((searched(n) and room(n)) or shown(n) or [0, 0, 0, 0])
        if region:
            regions[id(n)] = region

    def crops_of(p, r, x=0, y=0):
        """The rows of `r` (x0, y0, x1, y1) in `p`, a picture of the screen
        from `x`, `y`."""
        return b"".join(p.data[(j * p.width + r[0] - x) * 3:(j * p.width + r[2] - x) * 3]
                        for j in range(r[1] - y, r[3] - y))

    def lay(data, width, r, rows):
        wide = (r[2] - r[0]) * 3
        for i, y in enumerate(range(r[1], r[3])):
            data[(y * width + r[0]) * 3:(y * width + r[2]) * 3] = rows[i * wide:(i + 1) * wide]
        return data

    def sample():
        """Each picture, of only what holds the regions where it can be had
        that way, fast (x.grab), as the whole screen is too slow for."""
        rs = set(regions.values())
        x0, y0 = min((r[0] for r in rs), default=0), min((r[1] for r in rs), default=0)
        x1, y1 = max((r[2] for r in rs), default=1), max((r[3] for r in rs), default=1)
        for i in range(1, SAMPLES):
            stop.wait(max(0, shot + i * SAMPLE - time.monotonic()))
            if stop.is_set():
                return
            got = screen.backend.grab(screen.env, box=(x1 - x0, y1 - y0, x0, y0))
            p = picture.Picture(*(got or screen.backend.pixels(screen.env)))
            samples.append({r: crops_of(p, r, *((x0, y0) if got else (0, 0))) for r in rs})
    # Pictured again at set times while it's measured, so when a blink is
    # caught doesn't hang on how long measuring takes.
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    found, late, hidden = {}, set(), set()
    cannot = None
    until = time.monotonic() + SEARCH
    for n in measured:
        if searched(n) and leaf(n) and n.path not in fronts:
            cannot = cannot or pango_cairo()
            if not cannot and out_of_view(n):
                hidden.add(n.path)
            if cannot or n.path in hidden or unsure(n) or lone(n):
                continue
            try:
                found[n.path] = seek(n, until)
            except TimeoutError:
                late.add(n.path)

    def covered(n, k):
        """Whether `n` is behind a modal dialog: said apart if so."""
        m = fronts.get(n.path)
        if m:
            name = json.dumps(m.name, ensure_ascii=False)
            behind.append((n, f"{k} behind a modal dialog {name}"))
        return bool(m)

    def wrong(n, k):
        """Whether `n` is GTK 4's, whose positions are wrong: noted if so."""
        version = gtk4(n)
        inside = version and gtk4_release(n) < (4, 14)
        popover = inside and k != "edge" and popped(n)
        if (not version or version in GTK4_BOUNDS or inside and k != "edge" and not popover
                or trusted):
            return False
        text = n._role in texts and k == "text" and not (cannot or inside)
        short = (" (a single letter: GTK gives it no width)" if text and lone(n) else
                 " (over more than one line)" if text and "\n" in n.name else
                 " (not all ASCII: GTK measures its width short)" if text and unsure(n) else
                 f" ({refused[n.path][1]})" if text and n.path in refused else "")
        case = text and n.path in refused and refused[n.path][0]
        notes.append((n, f"{k} not measured: GTK 4 reports its position wrong{short}"))
        why = (f"GTK {version} reports where what's in a popover is as if the popover were "
               "in its window, not where it's drawn; see docs/accessibility.md." if popover else
               f"GTK {version} reports where a widget's content is, inside its border and "
               "padding, so its edge can't be measured from what it reports; see "
               "docs/accessibility.md." if inside else
               f"GTK {version} reports where its widgets are wrong, so text is measured where "
               f"it's found drawn near there, by its name in its font, but shotbox {cannot}; "
               "see docs/accessibility.md." if n._role in texts and k == "text" and cannot else
               f"GTK {version} reports where its widgets are wrong, so text is measured where "
               "it's found drawn near there, by its name in its font, but a single letter "
               "isn't: GTK gives it no width to check; see docs/accessibility.md."
               if n._role in texts and k == "text" and lone(n) else
               f"GTK {version} reports where its widgets are wrong, so text is measured where "
               "it's found drawn near there, by its name in its font, but not text over more "
               "than one line; see docs/accessibility.md."
               if n._role in texts and k == "text" and "\n" in n.name else
               f"GTK {version} reports where its widgets are wrong, so text is measured where "
               "it's found drawn near there, by its name in its font, and nothing there matched "
               "it well enough: text drawn otherwise than its name and font say (in another "
               "case or size, letter-spaced, cut short) isn't found; see docs/accessibility.md."
               if case == "unmatched" else
               f"GTK {version} reports where its widgets are wrong, so text is measured where "
               "it's found drawn near there, by its name in its font, and this matched in two "
               "places where it can be (the same text twice, or inside a longer one), so "
               "neither is taken; see docs/accessibility.md." if case == "twice" else
               f"GTK {version} reports where its widgets are wrong, so text is measured where "
               "it's found drawn near there, by its name in its font, and another node's text "
               "matches what was found as well, so it isn't "
               "taken for this one's; see docs/accessibility.md." if case == "other" else
               f"GTK {version} reports where its widgets are wrong, so text is measured where "
               "it's found drawn near there, by its name in its font, and what matched isn't as "
               "wide as GTK lays this out, as when it's drawn letter-spaced or in another case "
               "and something nearby draws the name as written; see docs/accessibility.md."
               if case == "wide" else
               f"GTK {version} reports where its widgets are wrong, so text is measured where "
               "it's found drawn near there, by its name in its font, and this wasn't found "
               "there; see docs/accessibility.md." if n._role in texts and k == "text"
               and not unsure(n) else
               f"GTK {version} reports where its widgets are wrong, and how wide text that "
               "isn't all ASCII is, so this can't be told from its name drawn by something "
               "else nearby; see docs/accessibility.md." if n._role in texts and k == "text" else
               f"GTK {version} reports where a widget's content starts, not its edge, and a "
               "stack's child with its place in the stack counted twice, so what isn't text "
               "can't be measured from what it reports; see docs/accessibility.md."
               if gtk4_release(n) < (4, 14, 6) else
               f"GTK {version} reports where its widgets are without their containers' "
               "padding and borders, and a stack's child with its place in the stack counted "
               "twice, so what isn't text can't be measured from what it reports; see "
               "docs/accessibility.md.")
        n["_why"] = why
        if why not in whys:
            whys.append(why)
        return True

    for n in measured:
        # A button with a label in it: the label's text is what's measured.
        if "_run" not in n and not leaf(n):
            continue
        k = kind(n)
        # GTK 4 puts a label in a control that draws text, so one with none
        # draws an icon: a graphic, held to graphics' ratio.
        if n._role in controls and gtk4(n):
            k = "graphics"
        if covered(n, k):
            continue
        seen = found.get(n.path)
        if n.path in hidden:
            notes.append((n, f"{k} not measured: not shown (scrolled out of view, or hidden)"))
            continue
        if n.path in late:
            notes.append((n, f"{k} not measured: no time left to look for it"))
            why = (f"Text GTK {gtk4(n)} places wrong is looked for for {SEARCH:g} seconds in "
                   "all; see docs/accessibility.md.")
            n["_why"] = why
            if why not in whys:
                whys.append(why)
            continue
        big = k == "text" and (large_run(n._look, False) if "_run" in n
                               else large_text(Atspi, n))
        least = large if big else text if k == "text" else graphics
        at = f"at least {least:g}" + (", large text" if big else "")
        if seen and n.path in doubt:
            # Told apart from another's text by which nodes could have drawn
            # it, which can't count one left out of the tree: taken only to
            # fail, else as if not found.
            got = picture.text_contrast(pic, overlap(seen[0], [0, 0, pic.width, pic.height]),
                                        seen[2])
            if not got or got[0] >= least:
                seen, refused[n.path] = None, doubt[n.path]
        # Only in a window raised: another can be over it, its text faint.
        dim = window and n.path in found and not seen and faint(n, least)
        if dim:
            uncounted.add(id(n))
            taken.append((n, k, least, at, overlap(room(n), [0, 0, pic.width, pic.height]),
                          False, ""))
            problems.append((n, f"{k} not found, but nothing where it can be has the contrast "
                                f"it needs: the best anything there reaches is "
                                f"{ratio_text(dim)}:1 ({at})"))
            n["_why"] = why = (
                f"GTK {gtk4(n)} reports where its widgets are wrong, so text is measured where "
                "it's found drawn near there; this wasn't, and nothing where it can be reaches "
                "the contrast text needs, so whatever text is there is fainter; see "
                "docs/accessibility.md.")
            if why not in whys:
                whys.append(why)
            continue
        if not seen and wrong(n, k):
            continue
        if not n.bounds:
            if "_run" in n or not undrawn(Atspi, n):
                problems.append((n, f"{k} not measured: can't tell where it is"))
            continue
        box = seen[0] if seen else shown(n)
        if not box:
            continue
        # A window can be bigger than the screen, and its nodes past it.
        on = overlap(box, [0, 0, pic.width, pic.height])
        if not on[2] or not on[3]:
            notes.append((n, f"{k} not measured: outside the screen"))
            continue
        cut, box = on != box, on
        if gtk4_release(n) < (4, 14) and paged_out(n):
            notes.append((n, f"{k} not measured: GTK says another node is at its middle"))
            continue
        got = picture.text_contrast(pic, box, seen and seen[2])
        # Its coverage says a small glyph's color, which its core is too
        # small to.
        small = got and got[3] < SMALLEST and not got[4]
        count += bool(got and not small)
        if small:
            notes.append((n, f"{k} too small to measure, {got[3]} pixels"))
        elif got is None and (k == "text" or n._role in controls):
            # What it draws can be past the screen's edge: right-aligned text.
            if cut:
                notes.append((n, f"{k} not measured: partly outside the screen"))
                continue
            back = picture.hex_of(pic.colors(box).most_common(1)[0][0])
            # A control can be drawn only when the pointer's over it: a tab's
            # close button. Say how it is then, to tell.
            over = n._role in controls and picture.text_contrast(hovered(box), box)
            then = (f"; with the pointer over it, {ratio_text(over[0])}:1, "
                    f"{picture.hex_of(over[1])} on {picture.hex_of(over[2])} ({at})"
                    if over else "")
            problems.append((n, f"{k} not seen: nothing drawn on {back}{then}"))
            uncounted.add(id(n))
            taken.append((n, k, least, at, box, None, ""))
        elif got:
            moved = (f"; found {seen[1][0]},{seen[1][1]} px from " + (
                f"where GTK {gtk4(n)} says its text is" if laid(n)
                else f"the corner of the box GTK {gtk4(n)} says") if seen else "")
            colors = f"{picture.hex_of(got[1])} on {picture.hex_of(got[2])} ({at})"
            if got[0] >= least:
                passes.append((n, f"{k} passes at {ratio_text(got[0])}:1, {colors}{moved}"))
                taken.append((n, k, least, at, box, seen and seen[2], moved))
                continue
            said = said_color(Atspi, n)
            # Light or small text covers no pixel whole, so measures short of
            # its color, as text drawn see-through does: say both.
            meant = (f"; drawn lighter than its color, {picture.hex_of(said)}, which is "
                     f"{ratio_text(picture.ratio(said, got[2]))}:1 (light text, or opacity)"
                     if said and picture.ratio(said, got[2]) > got[0]
                     and picture.between(got[1], got[2], said) else "")
            problems.append((n, f"{k} {ratio_text(got[0])}:1, {colors}{meant}{moved}"))
            taken.append((n, k, least, at, box, seen and seen[2], moved))
    # Text that fades or blinks measures as it is the moment it's pictured.
    # What changed in a sample is measured in each, its sample's part laid
    # over the first picture: GTK 4's text not found (`drawn` False) looked
    # for again, or bounded by faint() where it still isn't, and what's
    # blinked off the color of its background, while it's where it was
    # (what's moved can't be told from blank). One that fails at its best
    # fails, and one that passes at its worst passes; but between, the dim
    # end of a fade can be meant, and the bright end be all it is for a
    # moment, so it's said with its range, failing only with `strict`. A
    # change of less than CHANGE is a level's flicker, none.
    if not taken:
        stop.set()
    sampler.join()
    first, where = pic, None
    for n, k, least, at, box, drawn, rest in taken:
        region = regions.get(id(n))
        base = region and crops_of(first, region)
        if not region or all(s[region] == base for s in samples):
            continue
        x0, y0, wide, high = region[0], region[1], region[2] - region[0], region[3] - region[1]
        got, still = [], None
        try:
            for rows in [base, *(s[region] for s in samples)]:
                if drawn is False:
                    # Looked for in the whole picture, the sample's part
                    # laid over the first's.
                    pic = picture.Picture(first.width, first.height, bytes(lay(
                        bytearray(first.data), first.width, region, rows)))
                    aparts.clear()
                    try:
                        seen = seek(n, until)
                    except TimeoutError:
                        continue
                    dim = not seen and faint(n, least)
                    # Not found, it's measured where it is only where GTK says
                    # that right.
                    if not seen and not dim and gtk4(n) not in GTK4_BOUNDS:
                        continue
                    r = (picture.text_contrast(pic, overlap(seen[0], [
                        0, 0, pic.width, pic.height]), seen[2]) if seen else (dim, None) if dim
                        else picture.text_contrast(pic, box))
                else:
                    r = picture.text_contrast(
                        picture.Picture(wide, high, rows), [box[0] - x0, box[1] - y0, *box[2:]],
                        drawn and (drawn[0] - x0, drawn[1] - y0, drawn[2]))
                if not r:
                    if still is None:
                        where = where or (Screen(), {})
                        try:
                            still = place(Atspi, n, where[0].backend.windows(where[0].env),
                                          where[1]) == parents.get(n.path, n).bounds
                        except Exception:   # gone since
                            still = False
                    if not still:
                        continue
                got.append(r or (1.0, None))
        finally:
            pic = first
            if where:
                where[0].close()
                where = None
        got.sort(key=lambda r: r[0])
        if not got or got[-1][0] - got[0][0] < CHANGE:
            continue
        lo, hi = ratio_text(got[0][0]), ratio_text(got[-1][0])
        passes = [p for p in passes if p[0] is not n]
        problems = [p for p in problems if p[0] is not n]
        n["range"] = [float(lo), float(hi)]
        count += id(n) in uncounted
        end = got[-1] if got[-1][0] < least else got[0]
        colors = f", {picture.hex_of(end[1])} on {picture.hex_of(end[2])}" if end[1] else ""
        if got[0][0] >= least:
            passes.append((n, f"{k} passes at {lo}:1 at worst, changing to {hi}:1{colors} "
                              f"({at}){rest}"))
        elif got[-1][0] < least:
            problems.append((n, f"{k} {hi}:1 at best, changing from {lo}:1{colors} ({at})"
                                f"{rest}"))
        else:
            notes.append((n, f"{k} changing: {lo}:1 to {hi}:1 ({at}){rest}"))
            n["_why"] = why = (
                f"Its contrast changed over {SAMPLES} pictures {SAMPLE:g} s apart, as a fade's "
                "or a blink's does, so it's a note, which fails only with --strict; see "
                "docs/accessibility.md.")
            if why not in whys:
                whys.append(why)
    if edges:
        for n in nodes:
            if not (n._showing and not disabled(n) and (
                    n._role in edged or (n._role == Atspi.Role.TEXT and "editable" in n.states))
                    ) or gtk3_switch(Atspi, n) or covered(n, "edge") or wrong(n, "edge"):
                continue
            box = shown(n)
            if box and not pic.clip(box):
                notes.append((n, "edge not measured: outside the screen"))
                continue
            got = box and picture.edge_contrast(pic, box)
            count += bool(got)
            # A field's edge can be what it's in: a frame's.
            field = n._role in (Atspi.Role.TEXT, Atspi.Role.COMBO_BOX)
            if not got:
                continue
            colors = (f"{ratio_text(got[0])}:1, {picture.hex_of(got[1])} by "
                      f"{picture.hex_of(got[2])} (at least {edges:g})")
            if got[0] >= edges:
                passes.append((n, f"edge passes at {colors}"))
            elif field and framed(n, box):
                passes.append((n, f"edge passes, drawn by what it's in; its own {colors}"))
            else:
                problems.append((n, f"edge {colors}"))
    said = problems + passes + notes + behind
    for d in nodes:
        if (d._role == Atspi.Role.DOCUMENT_WEB and d._showing
                and not any(within(n.path, d.path) for n, _ in said)
                and any(o._showing and (contents(Atspi, o) or "").replace("\ufffc", "").strip()
                        for o in nodes if within(o.path, d.path))):
            notes.append((d, "text not measured: none of the page's text was"))
            d["_why"] = why = (
                "A web page has text, but none of it was measured: text in a field, or in "
                "something disabled, isn't; see docs/accessibility.md.")
            if why not in whys:
                whys.append(why)
    notes = [(n, what) for n, what in notes if not skip(n, what)]
    # What's scrolled out of view isn't there to measure, as GTK 3's and
    # browsers' rows aren't, which aren't said at all.
    failing = [Noted(note) for note in notes if strict and note[0].path not in hidden]
    problems = [(n, what) for n, what in problems if not skip(n, what)] + failing
    notes = [note for note in notes if note not in failing]
    return (problems, [(n, what) for n, what in passes if not skip(n, what)], notes,
            [(n, what) for n, what in behind if not skip(n, what)],
            [w for w in whys if any(n.get("_why") == w for n, _ in problems + notes)], count)


class Noted(tuple):
    """A note, (node, what), that --strict made a problem."""


# How many pictures contrast takes, this many seconds apart: 0.37 s is
# no fraction of a common blink's period (0.5, 1, 1.2, 2 s), so over 1.85 s
# they fall all round each one's phase, never more than 0.37 s apart: a
# 1 s blink is seen on and off, and a flash of 0.5 s in 2 s is caught.
SAMPLE, SAMPLES = 0.37, 6

# A ratio that changes by less than this between pictures hasn't: a
# level's flicker in what's drawn.
CHANGE = 0.05

# Fewer pixels than this, a glyph (a ".") has no core of its own color.
SMALLEST = 16

# How well text drawn by its name and font must match the place it's found
# (picture.find_text), and how well nothing apart from that may.
MATCH, MATCH_ELSEWHERE = 0.98, 0.8

# The most seconds contrast looks for text, in all: well inside the
# watchdog's minute.
SEARCH = 30

# The GTK 4 releases from 4.14 that say where their widgets are: none yet.
# Others count a stack's child's place twice, and say where a widget's
# content starts (to 4.14.5) or leave out each container's padding and
# border (from 4.14.6), which can't be undone from what they say, so
# only their text is measured, where it's found (docs/accessibility.md,
# "GTK 4's positions"). Before 4.14, GTK 4 says where a widget's content
# is, which is measured, but for its edge and what's in a popover.
GTK4_BOUNDS = ()


def large_text(Atspi, n):
    """Whether all of `n`'s text is large, as WCAG has it: 18pt, or 14pt
    bold, run by run of its font. GTK says each run's size and scale (GTK
    4 in whole pixels, rounded down, so 14pt, 18.67px, is said as 18 and
    taken as up to a pixel more; GTK 3 in points) and weight; Chromium
    its size with a unit ("18pt") and weight; Qt no size."""
    acc = n._acc
    runs, i = [], 0
    try:
        count = Atspi.Text.get_character_count(acc) if "Text" in acc.get_interfaces() else 0
        while i < count and len(runs) < 50:
            attrs, _, end = Atspi.Text.get_attribute_run(acc, i, True)
            runs.append(attrs or {})
            i = max(end, i + 1)
    except Exception:   # gone since the walk
        return False
    return bool(runs) and all(large_run(attrs, bool(gtk4(n))) for attrs in runs)


def text_shapes(text, attrs, dy=0):
    """The kinds of shapes GTK 4 may draw `text` in, in the font its first
    run's `attrs` say, for picture.find_text: hinted as GTK 4.16 and later
    may (slightly, or not) and as earlier ones do (fully, its metrics
    too), each at the sizes its whole pixels may be rounded down from,
    moved down `dy` pixels. None without pycairo and PangoCairo, or for
    text on more than a line."""
    if "\n" in text or pango_cairo():
        return None
    import cairo
    from gi.repository import Pango, PangoCairo
    font = Pango.FontDescription()
    font.set_family(attrs.get("family-name", "Sans"))
    weight = attrs.get("weight", "")
    # CSS takes any weight (450), which GTK says, and PyGObject only Pango's
    # named ones: the nearest.
    font.set_weight(min(Pango.Weight.__enum_values__.values(), key=lambda w: abs(w - int(weight)))
                    if weight.isdigit() else Pango.Weight.NORMAL)
    font.set_style(Pango.Style.ITALIC if attrs.get("style") == "italic" else Pango.Style.NORMAL)
    try:
        size = float(attrs.get("size", "")) * float(attrs.get("scale", 1))
    except ValueError:
        return None
    kinds = []
    for hint, metrics in ((cairo.HINT_STYLE_NONE, cairo.HINT_METRICS_OFF),
                          (cairo.HINT_STYLE_SLIGHT, cairo.HINT_METRICS_OFF),
                          (cairo.HINT_STYLE_FULL, cairo.HINT_METRICS_ON)):
        kinds.append([])
        for more in (0, 1 / 3, 2 / 3):
            font.set_absolute_size((size + more) * Pango.SCALE)
            options = cairo.FontOptions()
            options.set_antialias(cairo.ANTIALIAS_GRAY)
            options.set_hint_style(hint)
            options.set_hint_metrics(metrics)

            def layout(cr):
                got = PangoCairo.create_layout(cr)
                PangoCairo.context_set_font_options(got.get_context(), options)
                got.set_font_description(font)
                got.set_text(text, -1)
                return got
            blank = cairo.Context(cairo.ImageSurface(cairo.FORMAT_A8, 1, 1))
            _, logical = layout(blank).get_pixel_extents()
            # Two blank columns each side: see find_text.
            w, h = logical.width + 4, logical.height
            if logical.width <= 0 or h <= 0:
                return None
            surface = cairo.ImageSurface(cairo.FORMAT_A8, w, h)
            cr = cairo.Context(surface)
            cr.move_to(2 - logical.x, dy - logical.y)
            PangoCairo.show_layout(cr, layout(cr))
            surface.flush()
            data, stride = bytes(surface.get_data()), surface.get_stride()
            kinds[-1].append((w, h, b"".join(data[y * stride:y * stride + w] for y in range(h))))
    return kinds


def large_run(attrs, pixels):
    """Whether a run, by its attributes, is large text. A size with no unit
    is GTK's: in pixels if `pixels` (GTK 4's), else points."""
    size = re.fullmatch(r"([\d.]+)(px|pt)?", attrs.get("size", attrs.get("font-size", "")))
    try:
        n = size and float(size[1]) * float(attrs.get("scale", 1))
    except ValueError:
        return False
    if not n:
        return False
    px = size[2] == "px" or (pixels and not size[2])
    pt, most = (n * 0.75, (n + (not size[2])) * 0.75) if px else (n, n)
    weight = attrs.get("weight", attrs.get("font-weight", ""))
    bold = weight == "bold" or (weight.isdigit() and int(weight) >= 700)
    return pt >= 18 or (most >= 14 and bold)


def grow_box(box, by):
    return [box[0] - by, box[1] - by, box[2] + 2 * by, box[3] + 2 * by]


def made_with(top):
    """The toolkit `top`'s program is, and its version: "GTK 4.14.5"."""
    app = top.get_application()
    return " ".join(filter(None, (app.get_toolkit_name(), app.get_toolkit_version()))
                    ) if app else ""


def gtk4(n):
    """The GTK 4 version `n`'s program is, or None. Asked once a program:
    get_application() asks its way up the tree."""
    bus = n._acc.app.bus_name if n._acc.app else None
    if bus not in GTK4:
        app = n._acc.get_application()
        version = app and app.get_toolkit_name() == "GTK" and app.get_toolkit_version() or ""
        GTK4[bus] = version if version.startswith("4.") else None
    return GTK4[bus]


GTK4 = {}


def gtk4_release(n):
    """The GTK 4 version `n`'s program is, as (4, minor, micro), or (99,)
    if it isn't GTK 4."""
    version = gtk4(n)
    return tuple(int(v) for v in version.split(".") if v.isdigit()) if version else (99,)


def gtk3_switch(Atspi, n):
    """GTK 3 calls a switch a toggle button; unlike its buttons, it has no
    image, which they all say they have."""
    acc = n._acc
    return (n._role == Atspi.Role.TOGGLE_BUTTON and (acc.get_toolkit_version() or "").startswith("3.")
            and "Image" not in acc.get_interfaces())


def said_color(Atspi, n):
    """The color GTK 3 says `n`'s text is, as (r, g, b), or None. Before
    opacity and a link's own color, so it's only said, not measured. GTK 4
    doesn't say; Chromium does, but not for its own window's controls."""
    acc = n._acc
    if not (acc.get_toolkit_version() or "").startswith("3.") or "Text" not in acc.get_interfaces():
        return None
    said = (Atspi.Text.get_default_attributes(acc) or {}).get("fg-color", "")
    channels = [int(c) for c in re.findall(r"\d+", said)]
    return tuple(c // 257 for c in channels) if len(channels) == 3 else None


def overlap(a, b):
    """The part of box `a` inside box `b`, both [x, y, width, height]."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return [x0, y0, max(0, x1 - x0), max(0, y1 - y0)]


def alone(Atspi, window, node):
    """Whether `node` is the one thing on screen that could take the focus:
    nothing else showing, sensitive and focusable but for containers."""
    containers = roles(Atspi, CONTAINERS)
    return not any("focusable" in n.states and n._showing and "disabled" not in n.states
                   and n._role not in containers and n._id != node._id
                   for _, n in walk(Atspi, window))


def watchdog(seconds, what):
    """End the process if `what` takes longer than `seconds`: libatspi can
    wait on a program that never answers, inside C, where nothing in
    Python gets to run."""
    def gone():
        sys.stderr.write(f"shotbox: {what} got no answer in {seconds:g}s\n")
        os._exit(1)
    timer = threading.Timer(seconds, gone)
    timer.daemon = True
    timer.start()


def public(n):
    return {k: v for k, v in n.items() if not k.startswith("_")}


# What dom() has read of each node it climbed through, in this run: a
# page's findings share the elements round them.
CLIMBED = {}


def dom(Atspi, n):
    """Note on `n`, if it's a node in a web page, where it is in its DOM,
    as Chromium's and Firefox's object attributes say, once: "dom", the
    tag, id and class of the element it is or is in (a text node has
    none), and "in", the same of the nearest one round that with an id,
    if it has none; and "text", its own, cut at 40 characters."""
    if "/document web[" not in n.path or "_placed" in n:
        return
    n["_placed"] = True
    el, acc = None, n._acc
    try:
        while acc is not None:
            key = ident(acc)
            if key not in CLIMBED:
                got = acc.get_attributes() or {}
                CLIMBED[key] = (acc.get_role() == Atspi.Role.DOCUMENT_WEB, got.get("tag") and {
                    k: got[k] for k in ("tag", "id", "class") if got.get(k)}, acc.get_parent())
            top, this, up = CLIMBED[key]
            if top:
                break
            if this:
                el = el or dict(this)
                if "id" in this:
                    if this != el:
                        el["in"] = this
                    break
            acc = up
    except Exception:   # gone while we looked
        pass
    text = (n.get("_run") or n.name or contents(Atspi, n) or "").replace("\ufffc", "").strip()
    n.update({**({"dom": el} if el else {}),
              **({"text": text[:40] + "…" * (len(text) > 40)} if text else {})})


def placed(n, named=False):
    """Where dom() puts `n`, said short, after "  <- ": `p "Faint words" in
    section#intro`; its text but where it's its name and `named`."""
    def css(el):
        return el["tag"] + (f"#{el['id']}" if "id" in el else "")
    el, text = n.get("dom"), n.get("text")
    text = text if text and not (named and n.name) else None
    said = ([css(el)] if el else []) + ([json.dumps(text, ensure_ascii=False)] if text else [])
    if el and "in" in el:
        said.append(f"in {css(el['in'])}")
    return "  <- " + " ".join(said) if said else ""


def record(out):
    """Write what programs announce on the session's accessibility bus to
    `out`, a line each, "polite: Saved", as they say it. GTK 4.14's
    gtk_accessible_announce() is AT-SPI's Announcement event, which goes to
    the bus whether or not anyone has asked to hear it, so a match rule is
    all it takes. A browser's live region is text inserted into a node
    whose container-live attribute is polite or assertive, sent only once
    a listener has registered for it. And what Orca says unasked: an
    alert or info bar as it shows or the focus goes into it, and the
    focus's new name."""
    from gi.repository import Gio, GLib
    bus = Gio.DBusConnection.new_for_address_sync(
        os.environ["AT_SPI_BUS_ADDRESS"],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)

    def write(politeness, text):
        # A line each, so a line break in what's said is written \n.
        with open(out, "a") as f:
            one = "\\n".join(text.splitlines())
            f.write(f"{politeness}: {one}\n")

    def said(conn, sender, path, iface, member, params):
        _, politeness, _, text, _ = params.unpack()
        now = time.monotonic()
        for gone in [k for k, at in announcers.items() if now - at > 10]:
            del announcers[gone]
        announcers[sender + path] = now
        if isinstance(text, str):
            write(POLITENESS.get(politeness, "polite"), text)

    def inserted(event):
        try:
            attrs = event.source.get_attributes() or {}
        except Exception:   # gone while we looked
            return
        politeness = attrs.get("container-live")
        # U+FFFC stands in for a child element, whose own text comes as its
        # own event.
        text = event.any_data.replace("\ufffc", "") if isinstance(event.any_data, str) else ""
        if (politeness in ("polite", "assertive") and attrs.get("container-busy") != "true"
                and text.strip()):
            write(politeness, text)

    def labels(acc, depth=0):
        # As Orca reads an alert: what its labels say, but not its buttons'.
        # Visible, not showing: a dialog says it's showing before its labels do.
        out = []
        for i in range(acc.get_child_count()):
            c = acc.get_child_at_index(i)
            role = int(c.get_role())
            if role in controls or not c.get_state_set().contains(Atspi.StateType.VISIBLE):
                continue
            if role in texts:
                out.append(c.get_name())
            elif depth < 20:
                out += labels(c, depth + 1)
        return out

    told = set()    # the alerts heard, so the focus going into one isn't
    announcers = {}     # who has announced something, and when

    def ident(acc):
        return f"{acc.app.bus_name if acc.app else ''}{acc.path}"

    def alert(acc, role):
        text = " ".join(dict.fromkeys(filter(None, [acc.get_name(), *labels(acc)])))
        if not text.strip():
            return
        told.add(ident(acc))
        # libadwaita announces a toast ("A toast appeared: Saved") from the
        # parent of its node, which is an alert too.
        now, parent = time.monotonic(), acc.get_parent()
        if not any(now - announcers.get(ident(a), -10) < 10 for a in (acc, parent) if a):
            write("polite" if role == Atspi.Role.NOTIFICATION else "assertive", text)

    def shown(event):
        try:
            role = int(event.source.get_role())
            if role in alerts and event.detail1:
                alert(event.source, role)
            elif role in alerts:    # gone, so heard again once it's back
                told.discard(ident(event.source))
        except Exception:   # gone while we looked
            pass

    # Orca says the focus again when its name changes ("Copy" to "Copied"),
    # as 51 decides it: not a name much like the last one it said (a
    # countdown), nor a list item's that holds a progress bar; and as 46
    # does, not a combo box's, which is what's picked in it, or a cell's.
    focus = {}

    def focused(event):
        if event.detail1:
            went(event.source)

    def went(acc):
        try:
            left = focus.get("alert")
            focus.update(acc=acc, name=acc.get_name())
            # GTK 4 says nothing of an alert dialog showing: Orca reads it
            # as the focus goes into it, as it does any dialog, and again
            # each time it comes back. A page's alerts are its live regions.
            up, here = acc.get_parent(), None
            for _ in range(50):
                role = int(up.get_role()) if up else None
                if role in alerts:
                    here = ident(up)
                    if here not in told:
                        alert(up, role)
                    break
                if role is None or role in tops:
                    break
                up = up.get_parent()
            focus["alert"] = here
            if left != here:
                told.discard(left)
        except Exception:   # gone
            pass

    def activated(event):
        # A program just started can say where its focus is before it can
        # be asked about what's there, which then can't be heard; so once
        # it can be, a window that comes up is looked in for its focus, as
        # Orca reads it.
        tries = [20]

        def look():
            try:
                acc = holding_focus(event.source)
            except Exception:   # not there yet, or gone
                tries[0] -= 1
                return tries[0] > 0
            if acc:
                went(acc)
            return False
        GLib.timeout_add(100, look)

    def holding_focus(acc, depth=0):
        if acc.get_state_set().contains(Atspi.StateType.FOCUSED):
            return acc
        for c in (acc.get_child_at_index(i) for i in range(acc.get_child_count())):
            if c and depth < 50 and c.get_state_set().contains(Atspi.StateType.SHOWING):
                found = holding_focus(c, depth + 1)
                if found:
                    return found
        return None

    def renamed(event):
        name = event.any_data
        if not (focus and event.source == focus["acc"] and isinstance(name, str)
                and name.strip() and name != focus["name"]):
            return
        old = (focus["name"] or "").lower()
        if round(difflib.SequenceMatcher(None, old, name.lower()).ratio(), 2) >= 0.85:
            return
        focus["name"] = name
        try:
            role = event.source.get_role()
            if role in (Atspi.Role.COMBO_BOX, Atspi.Role.TABLE_CELL) or (
                    role == Atspi.Role.LIST_ITEM and holds(event.source, Atspi.Role.PROGRESS_BAR)):
                return
        except Exception:   # gone
            return
        write("polite", name)

    def holds(acc, role, depth=0):
        return depth < 20 and any(
            c.get_role() == role or holds(c, role, depth + 1)
            for c in (acc.get_child_at_index(i) for i in range(acc.get_child_count())) if c)

    # A program joining the bus is asked for its attributes, as Orca asks,
    # which Chromium takes as a reader at work (see paged()): until then
    # it says nothing of its live regions.
    def joined(conn, sender, path, iface, member, params):
        kind, _, _, child, _ = params.unpack()
        if kind == "add":
            name, root = child
            bus.call(name, root, "org.a11y.atspi.Accessible", "GetAttributes",
                     None, None, Gio.DBusCallFlags.NONE, -1, None, None)

    bus.signal_subscribe(None, "org.a11y.atspi.Event.Object", "Announcement", None, None,
                         Gio.DBusSignalFlags.NONE, said)
    bus.signal_subscribe(None, "org.a11y.atspi.Event.Object", "ChildrenChanged",
                         "/org/a11y/atspi/accessible/root", None,
                         Gio.DBusSignalFlags.NONE, joined)
    Atspi = atspi()
    alerts = roles(Atspi, ("ALERT", "NOTIFICATION", "INFO_BAR"))
    tops = roles(Atspi, ("APPLICATION", "FRAME", "DOCUMENT_WEB", "DOCUMENT_FRAME"))
    controls, texts = roles(Atspi, CONTROLS), roles(Atspi, ("LABEL", "STATIC"))
    live = Atspi.EventListener.new(inserted)
    live.register("object:text-changed:insert")
    listeners = [Atspi.EventListener.new(f) for f in (shown, focused, renamed, activated)]
    for listener, kind in zip(listeners, ("object:state-changed:showing",
                                          "object:state-changed:focused",
                                          "object:property-change:accessible-name",
                                          "window:activate")):
        listener.register(kind)
    # A round-trip on libatspi's own connection, after the registration it
    # sent without waiting, so programs started next are told of it.
    Atspi.get_desktop(0).get_child_count()
    # Round-trip, so the match rule is in place before we say we're ready.
    bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                  "GetId", None, None, Gio.DBusCallFlags.NONE, -1, None)
    open(out, "a").close()
    GLib.MainLoop().run()


def main(argv):
    if argv[:1] == ["speak"]:
        from . import speak
        return speak.main(argv[1:])
    p = argparse.ArgumentParser(prog="shotbox a11y",
                                description="Inside `shotbox run --a11y`: read the "
                                            "accessible tree, as a screen reader does.")
    sub = p.add_subparsers(dest="what", required=True,
                           metavar="{tree,lint,tabwalk,click,scroll,contrast,speak}")
    t = sub.add_parser("tree", help="the tree, a node a line, indented")
    lt = sub.add_parser("lint", help="controls, images and focusable nodes with no name")
    ignore = dict(action="append", default=[], metavar="RE",
                  help="not a problem whose line (PATH: WHAT, or the node's tree line: "
                       "WHAT) this regex is found in (repeatable)")
    lt.add_argument("--ignore", **ignore)
    w = sub.add_parser("tabwalk", help="press a key until the focus comes back round")
    w.add_argument("--key", default="Tab", metavar="CHORD", help="the key to press (Tab)")
    w.add_argument("--max", type=int, default=50, metavar="N", help="at most this many (50)")
    w.add_argument("--reach-all", action="store_true",
                   help="fail where a showing, sensitive control is never reached; "
                        "without it, they're said, as arrows or shortcuts may reach them "
                        "(but a --document walk that never lands in the page fails)")
    w.add_argument("--ignore", **ignore)
    w.add_argument("--document", action="store_true",
                   help="only what's in the window's web page: the walk goes through the "
                        "browser's own controls, but doesn't list them")
    w.add_argument("--visible", action="store_true",
                   help="fail where the focus doesn't show: too little of the screen "
                        "round a node changed as it took it")
    c = sub.add_parser("click", help="click a node, found by its name")
    c.add_argument("name", metavar="NAME", help="its name: exactly, or failing that, a regex "
                                                "matched in full")
    c.add_argument("--role", metavar="RE", help="and its role, exactly or a regex")
    c.add_argument("--button", type=int, default=1, help="1 left, 2 middle, 3 right")
    c.add_argument("--double", action="store_true", help="click twice")
    sc = sub.add_parser("scroll", help="turn the mouse wheel over a node, found by its name")
    sc.add_argument("name", metavar="NAME", help="its name: exactly, or failing that, a regex "
                                                 "matched in full")
    sc.add_argument("dy", type=int, metavar="DY", help="clicks down; up if negative")
    sc.add_argument("dx", type=int, nargs="?", default=0, metavar="DX",
                    help="clicks right; left if negative")
    sc.add_argument("--role", metavar="RE", help="and its role, exactly or a regex")
    nd = sub.add_parser("node")
    nd.add_argument("pattern")
    for s in (c, sc):
        s.add_argument("--force", action="store_true",
                       help="don't wait for it to be enabled, still and uncovered")
    ct = sub.add_parser("contrast", help="text, controls and images against their backgrounds")
    ct.add_argument("--text", type=float, default=4.5, metavar="RATIO",
                    help="text, buttons, links and tabs against their background, at least (4.5)")
    ct.add_argument("--large", type=float, default=3.0, metavar="RATIO",
                    help="large text (18pt, or 14pt bold), at least (3)")
    ct.add_argument("--graphics", type=float, default=3.0, metavar="RATIO",
                    help="images against their background, at least (3)")
    ct.add_argument("--edges", type=float, metavar="RATIO",
                    help="also a button's or field's edge against what's round it, "
                         "at least this (3 is WCAG's)")
    ct.add_argument("--ignore", **ignore)
    ct.add_argument("--passes", action="store_true",
                    help="also what passes, with its ratio, after what fails")
    ct.add_argument("--strict", action="store_true",
                    help="what isn't measured fails too")
    for s in (t, lt, w, c, sc, ct, nd):
        s.add_argument("--window", metavar="RE",
                       help="just this window (its title, a regex matched in full, "
                            "or app=RE for its app id; one with no accessible name "
                            "goes by the display's, unless its program has a named "
                            "one picked), waiting for it")
        s.add_argument("--timeout", type=float, default=10, metavar="SECS",
                       help="how long to wait for the --window (10), or the node")
    for s in (t, lt, w, c, sc, ct, nd):
        s.add_argument("--json", action="store_true", help="as JSON, for scripts")
    sub.add_parser("speak", help="what Orca, a screen reader, says as keys move the focus")
    rec = sub.add_parser("record")
    rec.add_argument("out")
    a = p.parse_args(argv)

    if a.what == "record":
        record(a.out)
        return 0
    # A tree takes a second or two; a press, its settling time and a tree.
    watchdog(60 + a.timeout + (a.max * 5 if a.what == "tabwalk" else 0),
             "the accessible tree")
    if a.what == "tree":
        # Each window with its program's pid and toolkit, then a node for
        # each program that didn't answer, whose windows aren't there.
        nodes = [Node(n, **({"pid": n._top.get_process_id(), "toolkit": made_with(n._top)}
                            if not n.depth else {}))
                 for n in tree(a.window, a.timeout, bounds=a.json)]
        nodes += [Node(role="application", name="", description="", states=[],
                       path="application", depth=0, pid=pid, quiet=True, bounds=None)
                  for pid in sorted(QUIET) if not a.window]
        if a.json:
            print(json.dumps([dict(public(n), depth=n.depth) for n in nodes]))
        else:
            for n in nodes:
                print("  " * n.depth + n.line())
        return 0
    if a.what in ("lint", "contrast"):
        passes, notes, behind, whys, count = [], [], [], [], 1
        if a.what == "lint":
            problems = lint(atspi(), tree(a.window, a.timeout, bounds=a.json), a.ignore)
        else:
            problems, passes, notes, behind, whys, count = contrast(
                a.window, a.text, a.graphics, a.edges, a.timeout, a.ignore, a.large, a.strict)
            passes = passes if a.passes else []
        # Said apart from the lines, on stderr, so stdout is PATH: WHAT.
        # Under --strict what wasn't measured is among the problems, but
        # a failure isn't nothing.
        nothing = not count and whys and all(isinstance(p, Noted) for p in problems)
        Atspi = atspi()
        for n, _ in problems + passes + notes + behind:
            dom(Atspi, n)
        changing = sum("range" in n for n, _ in notes)
        said = " ".join([f"{len(notes) - changing} not measured"
                         + ("." if nothing else ", which doesn't fail.")]
                        * bool(len(notes) - changing)
                        + [f"{changing} changing, which doesn't fail."] * bool(changing)
                        + [f"{len(behind)} behind a modal dialog, which doesn't fail."]
                        * bool(behind) + whys)
        if a.json and a.what == "lint":
            print(json.dumps([dict(public(n), problem=what, fails=True) for n, what in problems]))
        elif a.json:
            print(json.dumps({"problems": [dict(public(n), problem=what,
                                                **({"why": n._why} if "_why" in n else {}))
                                           for n, what in problems],
                              **({"passes": [dict(public(n), contrast=what) for n, what in passes]}
                                 if a.passes else {}),
                              "notes": [dict(public(n), note=what,
                                             **({"why": n._why} if "_why" in n else {}))
                                        for n, what in notes],
                              "behind": [dict(public(n), behind=what) for n, what in behind]}))
        else:
            for n, what in problems + passes + notes + behind:
                print(f"{n.path}: {what}{placed(n)}")
        if nothing:
            sys.stderr.write(f"shotbox: nothing measured: {said}\n")
            return 2
        if said and not a.json:
            sys.stderr.write(said + "\n")
        return 1 if problems and not a.json else 0
    if a.what in ("click", "scroll"):
        n = (click(a.name, a.role, a.window, a.button, a.double, a.timeout, a.force)
             if a.what == "click" else scroll(a.name, a.dy, a.dx, a.role, a.window, a.timeout,
                                              a.force))
        print(json.dumps(public(n)) if a.json else n.line())
        return 0
    if a.what == "node":
        n = wait_node(a.pattern, a.window, a.timeout)
        print(json.dumps(public(n)) if a.json else n.line())
        return 0
    skip = ignored(a.ignore)
    seen, how, missed, began = tab_walk(a.key, a.max, a.window, timeout=a.timeout,
                                        visible=a.visible, document=a.document)
    missed = [n for n in missed if not skip(n, f"not reached by {a.key}")]
    if a.document and how == "cycle" and missed and not seen:
        how = "outside"
    if a.json:
        print(json.dumps({"focus": [public(n) for n in seen], "end": how,
                          "unreached": [public(n) for n in missed],
                          "began": began and public(began)}))
        return 0
    for line in walk_lines(a.key, a.max, seen, how, missed, began):
        print(line)
    hidden = [n for n in seen if unseen(n)]
    return 0 if how == "cycle" and not hidden and not (a.reach_all and missed) else 1


def walk_lines(key, most, seen, how, missed, began, focus="shotbox focus"):
    """What a walk found, a line each, as `shotbox a11y tabwalk` says it;
    `focus`, how to give a window the keyboard, for whoever reads it."""
    def marked(n):
        said = []
        if unseen(n):
            said.append(f"focus not shown: {n.changed} pixels changed, {n.needed} needed")
        if n.get("off_screen"):
            said.append("focus not measured: outside the screen")
        on = n.get("walked_from") and Node(n.walked_from).line()
        if n.get("left_by"):
            said.append(f"a text view: {n.left_by} leaves it"
                        + (f", backward; walked on from {on}" if on else ""))
        elif on:
            said.append(f"a text view nothing leaves; walked from {on}")
        if n.get("edited"):
            said.append(f"{n.edited} changed its text")
        return n.line() + "".join(f"  <- {s}" for s in said)

    lines = [f"began in {marked(began)}"] if began else []
    lines += [marked(n) for n in seen]
    lines += [f"not reached by {key}: {n.line()}" for n in missed]
    lines.append(walk_end(key, most, seen, how, missed, focus, began))
    hidden = [n for n in seen if unseen(n)]
    if hidden:
        lines.append(f"focus not shown on {len(hidden)}")
    return lines


def walk_end(key, most, seen, how, missed, focus="shotbox focus", began=None):
    """How a walk ended, as a line."""
    tried = seen[-1].get("tried") if seen else None
    # The key itself is tried first in a web page's, and is the one it takes.
    others = [k for k in tried or [] if k != key]
    left = next((n.left_by for n in reversed([began, *seen]) if n and n.get("left_by")), None)
    unreached = f"; {len(missed)} not reached by {key}" if missed else ""
    return {
        "cycle": f"round again after {len(seen)}{unreached}",
        "outside": f"round again, never in the page{unreached}",
        "escaped": f"left the page by {left}, and {key} never came back to it{unreached}",
        "trap": (f"stuck in a text view, which takes {key}: "
                 + " and ".join(filter(None, [", ".join(others[:-1]), others[-1]]))
                 + (" leave" if len(others) > 1 else " leaves") + " the focus there" if others
                 else f"stuck: {key} leaves the focus where it was"),
        "lost": f"lost: after {key}, no node has the focus",
        "closed": f"stopped: after {tried and tried[-1]}, the text view isn't there",
        "unfocused": "no node has the focus to begin with: give the window the keyboard "
                     f"({focus})",
        "max": f"not round again after {most}{unreached}",
    }[how]

if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SessionError as e:
        sys.exit(f"shotbox: {e}")
