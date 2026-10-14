"""The accessible (AT-SPI) tree of what runs in a session, as a screen
reader sees it, for a session started with --a11y.

    shotbox a11y tree [--window RE]     the tree, a node a line
    shotbox a11y lint [--window RE]     controls and images with no name
    shotbox a11y tabwalk [--key Tab]    where the keyboard focus goes
    shotbox a11y click NAME             click a node, by its name
    shotbox a11y contrast               text and controls against their backgrounds

Reads the session's own accessibility bus, from AT_SPI_BUS_ADDRESS, and
refuses to run without it: otherwise libatspi would go looking for the
desktop's. `record` is the session's announcement recorder.
"""

import argparse
import json
import math
import os
import re
import sys
import threading
import time
from collections import Counter
from functools import cache

from . import picture, x
from .screen import Screen, SessionError, wait_for

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
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi, GLib
    except (ImportError, ValueError) as e:
        raise SessionError(f"AT-SPI's Python bindings won't load ({e}): they're in python3-gi and "
                           "gir1.2-atspi-2.0 on Debian, and python3-gobject, at-spi2-core and "
                           "gobject-introspection on Fedora")

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

    def line(self):
        out = f'{self.role} {json.dumps(self.name, ensure_ascii=False)}'
        if self.description:
            out += f' description={json.dumps(self.description, ensure_ascii=False)}'
        return out + (f" [{', '.join(self.states)}]" if self.states else "")


def toplevels(Atspi, window=None, timeout=10):
    """Each (application, toplevel) on the bus, in a stable order:
    applications by name, their windows as they list them. `window`, as
    everywhere: a title regex, or app=RE, which goes through the display's
    own idea of app ids to a title; it's waited for, up to `timeout`."""
    if not window:
        return find_toplevels(Atspi)
    what = f"an accessible {x.matcher(window)[1]}"
    return wait_for(what, lambda: find_toplevels(Atspi, window), timeout)


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
        return re.fullmatch(window, got) is not None

    hits = []
    for name, _, app in sorted(apps, key=lambda a: a[:2]):
        tops = [t for t in (app.get_child_at_index(j) for j in range(app.get_child_count()))
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


def origin(Atspi, top, shown):
    """Where `top` is on the screen, where its children's window
    coordinates start, and the display's window it is: ((x, y, width,
    height), (x, y), window), or None if that can't be told. A node's
    extents are in its window's coordinates (GTK 4 has no others), so
    they're put on the screen by the display's window of the same size,
    and name if it has one: `shown`, as the backend's windows() lists
    them. Chromium on Wayland counts its shadow into its accessible frame
    and not its window, and can't be placed. GTK 4, to 4.24 at least,
    loses the window's border (on X11 with no compositor, it has one)
    from its children: the frame's first child, which fills it but
    for that, is at the frame's corner rather than inside the border. The
    border and padding of every other node on the way down are lost too,
    and can't be told: docs/accessibility.md says how far off that is."""
    e = top.get_extents(Atspi.CoordType.WINDOW)
    name = top.get_name() or ""
    hits = [w for w in shown if (w[2], w[3]) == (e.width, e.height) and (not name or w[1] == name)]
    if len(hits) > 1 and isinstance(hits[0][0], str):   # X11's unmapped windows
        env = Screen().env
        hits = [w for w in hits if x.viewable(env, w[0])]
    if len(hits) != 1:
        return None
    w = hits[0]
    # GTK 4 alone: anywhere else a first child at the corner and smaller
    # (a GTK 3 button aligned to the start) is where it says.
    app = top.get_application()
    gtk4 = bool(app) and app.get_toolkit_name() == "GTK" and (
        app.get_toolkit_version() or "").startswith("4.")
    first = top.get_child_at_index(0) if gtk4 and top.get_child_count() else None
    c = first and first.get_extents(Atspi.CoordType.WINDOW)
    lost = (0, 0)
    if c and (c.x, c.y) == (e.x, e.y) and c.width < e.width and c.height < e.height:
        lost = ((e.width - c.width) // 2, (e.height - c.height) // 2)
    return [w[4], w[5], w[2], w[3]], (w[4] - e.x + lost[0], w[5] - e.y + lost[1]), w


def place(Atspi, n, shown, origins):
    """`n`'s bounds on the screen, [x, y, width, height], or None. `origins`
    caches origin() for each toplevel."""
    if n._top not in origins:
        origins[n._top] = origin(Atspi, n._top, shown)
    o = origins[n._top]
    if o is None:
        return None
    if n._acc == n._top:
        return o[0]
    try:
        e = n._acc.get_extents(Atspi.CoordType.WINDOW)
    except Exception:   # gone since the walk: a closing popover, a toast
        return None
    return [o[1][0] + e.x, o[1][1] + e.y, e.width, e.height]


def node(Atspi, acc, states, path):
    shown = [s for s in STATES
             if states.contains(getattr(Atspi.StateType, s.upper().replace("-", "_")))]
    if states.contains(Atspi.StateType.EXPANDABLE) and not states.contains(
            Atspi.StateType.EXPANDED):
        shown.append("collapsed")
    if not states.contains(Atspi.StateType.SENSITIVE):
        shown.append("disabled")
    return Node(role=acc.get_role_name(), name=acc.get_name() or "",
                description=acc.get_description() or "", states=shown, path=path,
                _role=int(acc.get_role()),
                _showing=states.contains(Atspi.StateType.SHOWING),
                # Paths alone collide: GTK 3 numbers its objects per program.
                _id=f"{acc.app.bus_name if acc.app else ''}{acc.path}",
                _acc=acc)


def walk(Atspi, window=None, timeout=10, bounds=False):
    """Every visible node under the toplevels `window` picks, depth first,
    as (depth, Node). With `bounds`, each has its bounds on the screen, or
    None where they can't be told."""
    out = []

    def visit(acc, depth, path):
        if len(out) > 20000 or depth > 200:
            return
        try:
            states = acc.get_state_set()
            n = node(Atspi, acc, states, path)
        except Exception:   # gone while we looked: a closing popover, a toast
            return
        if not states.contains(Atspi.StateType.VISIBLE):
            return
        n["_top"] = top
        out.append((depth, n))
        for i in range(acc.get_child_count()):
            child = acc.get_child_at_index(i)
            if child is not None:
                visit(child, depth + 1, f"{path}/{child.get_role_name()}[{i}]")

    for _, top in toplevels(Atspi, window, timeout):
        # libatspi caches states, and hears they've changed only from
        # events, which nothing here dispatches.
        top.clear_cache()
        visit(top, 0, f"{top.get_role_name()} {json.dumps(top.get_name() or '')}")
    if bounds:
        screen, origins = Screen(), {}
        shown = screen.backend.windows(screen.env)
        for _, n in out:
            n["bounds"] = place(Atspi, n, shown, origins)
            n["_window"] = origins[n._top] and origins[n._top][0]
    return out


def tree(window=None, timeout=10, bounds=False):
    Atspi = atspi()
    return [Node(n, depth=d) for d, n in walk(Atspi, window, timeout=timeout, bounds=bounds)]


def focused(Atspi, window=None):
    """The node with the keyboard focus, or None."""
    for _, n in walk(Atspi, window):
        if "focused" in n.states:
            return n
    return None


def roles(Atspi, names):
    return {int(getattr(Atspi.Role, r)) for r in names if hasattr(Atspi.Role, r)}


def lint(Atspi, nodes, ignore=()):
    """(node, what's wrong) for each control, image or focusable node a
    screen reader would have no name to say for. An image inside a named
    control is part of it: the control's name is what gets said. Anything
    with a name inside it is said by what's in it, and a container with
    something focusable inside passes the focus on to it."""
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
        if n.name:
            continue
        if n._role in controls or (n._role == Atspi.Role.TEXT and "editable" in n.states):
            problems.append((n, f"{n.role} with no name"))
        elif n._role == Atspi.Role.IMAGE and not in_named_control(n):
            problems.append((n, "image with no name (a decorative one should be "
                                "role presentation, which leaves it out)"))
        elif ("focusable" in n.states and n.path not in said_inside
              and not (n._role in containers and n.path in focus_inside)):
            problems.append((n, f"focusable {n.role} with no name"))
    return [(n, what) for n, what in problems
            if not any(re.search(i, f"{n.path}: {what}") for i in ignore)]


def tab_walk(key="Tab", most=50, window=None, settle=2.0, timeout=10, visible=False):
    """Press `key` up to `most` times and note where the focus lands each
    time. (nodes, how it ended): "cycle" once it lands somewhere it has
    been (where it began, or a stop since), "trap" if a press leaves it
    where it was, "max" if neither. A press that leaves it on the one
    thing in the window that could take it is a cycle of one. With
    `visible`, each node says how much of the screen round it changed as
    it took the focus (see shown())."""
    Atspi = atspi()
    screen = Screen()
    toplevels(Atspi, window, timeout)
    start = focused(Atspi, window)
    seen = []
    if visible:
        screen.park()
        look = Look(Atspi, screen, start)
    try:
        for _ in range(most):
            before = seen[-1] if seen else start
            screen.key(key)
            end = time.monotonic() + settle
            while True:
                now = focused(Atspi, window)
                moved = now is not None and (before is None or now._id != before._id)
                if moved or time.monotonic() > end:
                    break
                time.sleep(0.05)
            if not moved:
                if now is not None and alone(Atspi, window, now):
                    return seen or [now], "cycle"
                return seen + ([now] if now else []), "trap"
            been = [n._id for n in ([start] if start else []) + seen]
            if visible:
                look.took(now)
            seen.append(now)
            if now._id in been:
                return seen, "cycle"
        return seen, "max"
    finally:
        screen.close()


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
        it ("needed")."""
        try:
            wait_for("", x.Still(self.screen.env, quiet=0.3, backend=self.screen.backend), 2)
        except SessionError:
            pass
        now = self.grab()
        box = self.where(n)
        # A document takes the focus as a whole, and nobody draws round it;
        # a container shows it on what it passes it to (a notebook's tab).
        if box and n._role not in roles(self.Atspi, ("DOCUMENT_WEB", "DOCUMENT_FRAME",
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


def click(name, role=None, window=None, button=1, double=False, timeout=10):
    """Click the middle of the node whose name matches `name` (a regex,
    matched in full), and role `role` (a regex too) if given, waiting up to
    `timeout` for it to show, its window raised. A match inside
    another (a button's label) is the outer one. The node clicked."""
    Atspi = atspi()

    def matches():
        nodes = [n for _, n in walk(Atspi, window, timeout)
                 if n._showing and re.fullmatch(name, n.name)
                 and (role is None or re.fullmatch(role, n.role))]
        return [n for n in nodes if not any(n.path.startswith(o.path + "/") for o in nodes)]

    what = f"a node named {name!r}" + (f" with role {role!r}" if role else "")
    hits = wait_for(what, matches, timeout)
    if len(hits) > 1:
        raise SessionError(f"{len(hits)} nodes match: " + "; ".join(n.path for n in hits))
    n = hits[0]
    screen = Screen()
    origins = {}
    box = place(Atspi, n, screen.backend.windows(screen.env), origins)
    if not box or box[2] <= 0 or box[3] <= 0:
        raise SessionError(f"can't tell where {n.line()} is on the screen")
    # Inside its window: a node scrolled out of view is still showing.
    x, y, w, h = overlap(box, origins[n._top][0])
    if not w or not h:
        raise SessionError(f"{n.line()} is outside its window")
    try:
        screen.backend.focus(screen.env, origins[n._top][2])
        screen.click(x + w // 2, y + h // 2, button=button, double=double)
    finally:
        screen.close()
    n["bounds"] = box
    return n


def contrast(window=None, text=4.5, graphics=3.0, edges=None, timeout=10, ignore=()):
    """What's drawn with too little contrast, as (node, what), measured on a
    picture of the screen, `window` raised, in each node's bounds: text, and
    a button, link, tab or menu item, which may draw text or an icon and
    can't be told apart, against its background (at least `text`); an
    image against its own (`graphics`); and with `edges`, a control's
    edge against what's round it. Disabled nodes are left out, as WCAG
    leaves them, and so are a switch, slider, check box or the like, and
    what's outside its window (scrolled out of view); text is taken as
    normal size. A node that can't be placed, and
    text with nothing drawn on its background, are problems too: nothing
    says they can be read. But for those whose "PATH: PROBLEM" line one of
    the regexes in `ignore` is found in."""
    Atspi = atspi()
    toplevels(Atspi, window, timeout)
    screen = Screen()
    try:
        raise_window(screen, window)
        screen.park()
    finally:
        screen.close()
    nodes = [n for _, n in walk(Atspi, window, timeout, bounds=True)]
    pic = picture.Picture(*screen.backend.pixels(screen.env))
    texts, controls = roles(Atspi, TEXTS), roles(Atspi, CONTROLS)
    images, textless, edged = (roles(Atspi, ("IMAGE", "ICON")), roles(Atspi, TEXTLESS),
                               roles(Atspi, EDGED))
    parents = {n.path: n for n in nodes}

    def kind(n):
        if (not n._showing or not n.name or "editable" in n.states
                or "disabled" in n.states or n._role in textless or gtk3_switch(Atspi, n)):
            return None
        if n._role in images:
            return "graphics"
        return "text" if n._role in texts or n._role in controls else None

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

    def hovered(box):
        """The screen with the pointer over the middle of `box`."""
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

    measured = [n for n in nodes if kind(n)]
    problems = []
    for n in measured:
        # A button with a label in it: the label's text is what's measured.
        if any(o.path.startswith(n.path + "/") for o in measured):
            continue
        k = kind(n)
        if not n.bounds:
            problems.append((n, f"{k} not measured: can't tell where it is"))
            continue
        box = shown(n)
        if not box:
            continue
        # GTK 4 puts a label in a control that draws text, so one with none
        # draws an icon: a graphic, held to graphics' ratio.
        if n._role in controls and gtk4(n):
            k = "graphics"
        least = text if k == "text" else graphics
        got = picture.text_contrast(pic, box)
        if got is None and (k == "text" or n._role in controls):
            back = picture.hex_of(pic.colors(box).most_common(1)[0][0])
            # A control can be drawn only when the pointer's over it: a tab's
            # close button. Say how it is then, to tell.
            over = n._role in controls and picture.text_contrast(hovered(box), box)
            then = (f"; with the pointer over it, {ratio_text(over[0])}:1, "
                    f"{picture.hex_of(over[1])} on {picture.hex_of(over[2])} (at least {least:g})"
                    if over else "")
            problems.append((n, f"{k} not seen: nothing drawn on {back}{then}"))
        elif got and got[0] < least:
            said = said_color(Atspi, n)
            # Light or small text covers no pixel whole, so measures short of
            # its color, as text drawn see-through does: say both.
            meant = (f"; drawn lighter than its color, {picture.hex_of(said)}, which is "
                     f"{ratio_text(picture.ratio(said, got[2]))}:1 (light text, or opacity)"
                     if said and picture.ratio(said, got[2]) > got[0]
                     and picture.between(got[1], got[2], said) else "")
            problems.append((n, f"{k} {ratio_text(got[0])}:1, {picture.hex_of(got[1])} on "
                                f"{picture.hex_of(got[2])} (at least {least:g}){meant}"))
    if edges:
        for n in nodes:
            if not (n._showing and "disabled" not in n.states and (
                    n._role in edged or (n._role == Atspi.Role.TEXT and "editable" in n.states))
                    ) or gtk3_switch(Atspi, n):
                continue
            box = shown(n)
            got = box and picture.edge_contrast(pic, box)
            # A field's edge can be what it's in: a frame's.
            field = n._role in (Atspi.Role.TEXT, Atspi.Role.COMBO_BOX)
            if got and got[0] < edges and not (field and framed(n, box)):
                problems.append((n, f"edge {ratio_text(got[0])}:1, {picture.hex_of(got[1])} by "
                                    f"{picture.hex_of(got[2])} (at least {edges:g})"))
    return [(n, what) for n, what in problems
            if not any(re.search(i, f"{n.path}: {what}") for i in ignore)]


def grow_box(box, by):
    return [box[0] - by, box[1] - by, box[2] + 2 * by, box[3] + 2 * by]


def gtk4(n):
    app = n._acc.get_application()
    return bool(app) and app.get_toolkit_name() == "GTK" and (
        app.get_toolkit_version() or "").startswith("4.")


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


def record(out):
    """Write what programs announce on the session's accessibility bus to
    `out`, a line each, "polite: Saved", as they say it. GTK 4.14's
    gtk_accessible_announce() is AT-SPI's Announcement event, which goes to
    the bus whether or not anyone has asked to hear it, so a match rule is
    all it takes. A browser's live region is text inserted into a node
    whose container-live attribute is polite or assertive, sent only once
    a listener has registered for it."""
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

    bus.signal_subscribe(None, "org.a11y.atspi.Event.Object", "Announcement", None, None,
                         Gio.DBusSignalFlags.NONE, said)
    Atspi = atspi()
    live = Atspi.EventListener.new(inserted)
    live.register("object:text-changed:insert")
    # A round-trip on libatspi's own connection, after the registration it
    # sent without waiting, so programs started next are told of it.
    Atspi.get_desktop(0).get_child_count()
    # Round-trip, so the match rule is in place before we say we're ready.
    bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                  "GetId", None, None, Gio.DBusCallFlags.NONE, -1, None)
    open(out, "a").close()
    GLib.MainLoop().run()


def main(argv):
    p = argparse.ArgumentParser(prog="shotbox a11y",
                                description="Inside `shotbox run --a11y`: read the "
                                            "accessible tree, as a screen reader does.")
    sub = p.add_subparsers(dest="what", required=True,
                           metavar="{tree,lint,tabwalk,click,contrast}")
    t = sub.add_parser("tree", help="the tree, a node a line, indented")
    lt = sub.add_parser("lint", help="controls, images and focusable nodes with no name")
    ignore = dict(action="append", default=[], metavar="RE",
                  help="not a problem whose line (PATH: WHAT) this regex is found in "
                       "(repeatable)")
    lt.add_argument("--ignore", **ignore)
    w = sub.add_parser("tabwalk", help="press a key until the focus comes back round")
    w.add_argument("--key", default="Tab", metavar="CHORD", help="the key to press (Tab)")
    w.add_argument("--max", type=int, default=50, metavar="N", help="at most this many (50)")
    w.add_argument("--visible", action="store_true",
                   help="fail where the focus doesn't show: too little of the screen "
                        "round a node changed as it took it")
    c = sub.add_parser("click", help="click a node, found by its name")
    c.add_argument("name", metavar="NAME", help="its name, a regex matched in full")
    c.add_argument("--role", metavar="RE", help="and its role, a regex matched in full")
    c.add_argument("--button", type=int, default=1, help="1 left, 2 middle, 3 right")
    c.add_argument("--double", action="store_true", help="click twice")
    ct = sub.add_parser("contrast", help="text, controls and images against their backgrounds")
    ct.add_argument("--text", type=float, default=4.5, metavar="RATIO",
                    help="text, buttons, links and tabs against their background, at least (4.5)")
    ct.add_argument("--graphics", type=float, default=3.0, metavar="RATIO",
                    help="images against their background, at least (3)")
    ct.add_argument("--edges", type=float, metavar="RATIO",
                    help="also a button's or field's edge against what's round it, "
                         "at least this (3 is WCAG's)")
    ct.add_argument("--ignore", **ignore)
    for s in (t, lt, w, c, ct):
        s.add_argument("--window", metavar="RE",
                       help="just this window (its title, a regex matched in full, "
                            "or app=RE for its app id; one with no accessible name "
                            "goes by the display's, unless its program has a named "
                            "one picked), waiting for it")
        s.add_argument("--timeout", type=float, default=10, metavar="SECS",
                       help="how long to wait for the --window (10), or the node")
    for s in (t, lt, w, c, ct):
        s.add_argument("--json", action="store_true", help="as JSON, for scripts")
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
        nodes = tree(a.window, a.timeout, bounds=a.json)
        if a.json:
            print(json.dumps([dict(public(n), depth=n.depth) for n in nodes]))
        else:
            for n in nodes:
                print("  " * n.depth + n.line())
        return 0
    if a.what in ("lint", "contrast"):
        if a.what == "lint":
            problems = lint(atspi(), tree(a.window, a.timeout), a.ignore)
        else:
            problems = contrast(a.window, a.text, a.graphics, a.edges, a.timeout, a.ignore)
        if a.json:
            print(json.dumps([dict(public(n), problem=what) for n, what in problems]))
            return 0
        for n, what in problems:
            print(f"{n.path}: {what}")
        return 1 if problems else 0
    if a.what == "click":
        n = click(a.name, a.role, a.window, a.button, a.double, a.timeout)
        print(json.dumps(public(n)) if a.json else n.line())
        return 0
    seen, how = tab_walk(a.key, a.max, a.window, timeout=a.timeout, visible=a.visible)
    if a.json:
        print(json.dumps({"focus": [public(n) for n in seen], "end": how}))
        return 0
    hidden = [n for n in seen if unseen(n)]
    for n in seen:
        print(n.line() + (f"  <- focus not shown: {n.changed} pixels changed, "
                          f"{n.needed} needed" if unseen(n) else ""))
    if how == "cycle":
        print(f"round again after {len(seen)}")
    elif how == "trap":
        print(f"stuck: {a.key} leaves the focus where it was")
    else:
        print(f"not round again after {a.max}")
    if hidden:
        print(f"focus not shown on {len(hidden)}")
    return 0 if how == "cycle" and not hidden else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SessionError as e:
        sys.exit(f"shotbox: {e}")
