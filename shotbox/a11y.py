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
import os
import re
import sys
import threading
import time
from functools import cache

from . import picture, x
from .screen import Screen, SessionError, wait_for

# The states worth a line in the tree: what a screen reader says about a
# node, not how it's laid out ("showing", "vertical"). Their AT-SPI names.
STATES = ("focusable", "focused", "selected", "checked", "indeterminate", "pressed",
          "expanded", "editable", "read-only", "modal", "required", "invalid-entry")

# Controls with nothing to say for themselves without a name. Atspi.Role
# names, since the strings change between releases ("push button" is
# "button" in 2.62); those a release lacks are skipped.
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

# Controls a box is drawn round, or should be, for --edges: not check boxes
# and the like, whose node holds their label too.
EDGED = ("PUSH_BUTTON", "BUTTON", "TOGGLE_BUTTON", "PUSH_BUTTON_MENU", "ENTRY",
         "PASSWORD_TEXT", "SPIN_BUTTON", "COMBO_BOX")

POLITENESS = {1: "polite", 2: "assertive"}


def atspi():
    """libatspi, on the session's accessibility bus and nobody else's."""
    if not os.environ.get("AT_SPI_BUS_ADDRESS") or not os.environ.get("SHOTBOX_SCRATCH"):
        raise SessionError("no accessibility bus: start the session with --a11y")
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
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
    """Where `top` is on the screen, and where its children's window
    coordinates start: ((x, y, width, height), (x, y)), or None if that
    can't be told. A node's extents are in its window's coordinates (GTK 4
    has no others), so they're put on the screen by the display's window
    of the same size, and name if it has one: `shown`, as the backend's
    windows() lists them. Chromium on Wayland counts its shadow into its
    accessible frame and not its window, and can't be placed. GTK 4 on X11
    counts its shadow into its window and places its frame at minus the
    shadow; until GTK's fix, its children at minus it again, and each
    child at its parent's corner, short of the parent's border and
    padding."""
    e = top.get_extents(Atspi.CoordType.WINDOW)
    name = top.get_name() or ""
    hits = [w for w in shown if (w[2], w[3]) == (e.width, e.height) and (not name or w[1] == name)]
    if len(hits) > 1 and isinstance(hits[0][0], str):   # X11's unmapped windows
        env = Screen().env
        hits = [w for w in hits if x.viewable(env, w[0])]
    if len(hits) != 1:
        return None
    w = hits[0]
    # The frame's first child fills it but for the shadow: at the frame's
    # corner, it's been put at minus the shadow again.
    first = top.get_child_at_index(0) if top.get_child_count() else None
    c = first and first.get_extents(Atspi.CoordType.WINDOW)
    again = bool(c and (e.x or e.y) and (c.x, c.y) == (e.x, e.y)
                 and (c.width, c.height) != (e.width, e.height))
    return [w[4], w[5], w[2], w[3]], (w[4] - e.x * (1 + again), w[5] - e.y * (1 + again))


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
    e = n._acc.get_extents(Atspi.CoordType.WINDOW)
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
        self.was = self.where(start) if start else None

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
        # A document takes the focus as a whole, and nobody draws round it.
        if box and n._role not in roles(self.Atspi, ("DOCUMENT_WEB", "DOCUMENT_FRAME")):
            # The ring the focus left can reach into this one's.
            skip = self.was and self.last.clip(self.was, RING)
            n["changed"] = self.last.changed(now, box, RING, skip)
            n["needed"] = shown_needs(box)
        n["bounds"] = box
        self.last, self.was = now, box


# How far outside its node a focus ring can be drawn.
RING = 4


def shown_needs(box):
    """How many pixels must change for the focus to show: a one-pixel
    outline round the node. WCAG 2.2's focus appearance asks for two."""
    return 2 * (box[2] + box[3])


def unseen(n):
    """Whether a walk with `visible` found the focus not shown on `n`."""
    return n.get("changed") is not None and n.changed < n.needed


def click(name, role=None, window=None, button=1, double=False, timeout=10):
    """Click the middle of the node whose name matches `name` (a regex,
    matched in full), and role `role` (a regex too) if given, waiting up to
    `timeout` for it to show. A match inside another (a button's label)
    is the outer one. The node clicked."""
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
    box = place(Atspi, n, screen.backend.windows(screen.env), {})
    if not box or box[2] <= 0 or box[3] <= 0:
        raise SessionError(f"can't tell where {n.line()} is on the screen")
    try:
        screen.click(box[0] + box[2] // 2, box[1] + box[3] // 2, button=button,
                     double=double)
    finally:
        screen.close()
    n["bounds"] = box
    return n


def contrast(window=None, text=4.5, graphics=3.0, edges=None, timeout=10):
    """What's drawn with too little contrast, as (node, what), measured on a
    picture of the screen in each node's bounds: text against its
    background (at least `text`), a control or image with no text in it
    (an icon) against its own (`graphics`), and with `edges`, a control's
    edge against what's round it. Disabled nodes are left out, as WCAG
    leaves them; text is taken as normal size."""
    Atspi = atspi()
    screen = Screen()
    try:
        screen.park()
    finally:
        screen.close()
    nodes = [n for _, n in walk(Atspi, window, timeout, bounds=True)]
    pic = picture.Picture(*screen.backend.pixels(screen.env))
    texts, controls = roles(Atspi, TEXTS), roles(Atspi, CONTROLS)
    images, edged = roles(Atspi, ("IMAGE", "ICON")), roles(Atspi, EDGED)

    def drawn(n):
        return (n._showing and n.bounds and n.bounds[2] > 0 and n.bounds[3] > 0
                and "disabled" not in n.states)

    def kind(n):
        if not drawn(n) or not n.name or "editable" in n.states:
            return None
        if n._role in texts:
            return "text"
        if n._role in controls or n._role in images:
            # Chromium's buttons hold their text themselves; GTK's, in a label.
            text = n._acc.get_text_iface()
            return "text" if text and text.get_character_count() else "graphics"
        return None

    measured = [n for n in nodes if kind(n)]
    problems = []
    for n in measured:
        # A button with a label in it: the label's text is what's measured.
        if any(o.path.startswith(n.path + "/") for o in measured):
            continue
        k = kind(n)
        least = text if k == "text" else graphics
        got = picture.text_contrast(pic, clip_to_parent(n, nodes))
        if got and got[0] < least:
            problems.append((n, f"{k} {got[0]:.2f}:1, {picture.hex_of(got[1])} on "
                                f"{picture.hex_of(got[2])} (at least {least:g})"))
    if edges:
        for n in nodes:
            if drawn(n) and (n._role in edged
                             or (n._role == Atspi.Role.TEXT and "editable" in n.states)):
                got = picture.edge_contrast(pic, n.bounds)
                if got and got[0] < edges:
                    problems.append((n, f"edge {got[0]:.2f}:1, {picture.hex_of(got[1])} by "
                                        f"{picture.hex_of(got[2])} (at least {edges:g})"))
    return problems


def clip_to_parent(n, nodes):
    """`n`'s bounds, inside its parent's: GTK 4 places a child as if its
    parent had no padding, which can put its box past the parent's edge."""
    up = next((o for o in nodes if o.path == n.path.rsplit("/", 1)[0]), None)
    if not up or not up.bounds:
        return n.bounds
    x0, y0 = max(n.bounds[0], up.bounds[0]), max(n.bounds[1], up.bounds[1])
    x1 = min(n.bounds[0] + n.bounds[2], up.bounds[0] + up.bounds[2])
    y1 = min(n.bounds[1] + n.bounds[3], up.bounds[1] + up.bounds[3])
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
    lt.add_argument("--ignore", action="append", default=[], metavar="RE",
                    help="not a problem whose line (PATH: WHAT) this regex is found "
                         "in (repeatable)")
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
    ct = sub.add_parser("contrast", help="text, icons and controls against their backgrounds")
    ct.add_argument("--text", type=float, default=4.5, metavar="RATIO",
                    help="text against its background, at least (4.5)")
    ct.add_argument("--graphics", type=float, default=3.0, metavar="RATIO",
                    help="an icon, a control or image with no text, at least (3)")
    ct.add_argument("--edges", type=float, metavar="RATIO",
                    help="also a button's or field's edge against what's round it, "
                         "at least this (3 is WCAG's)")
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
            problems = contrast(a.window, a.text, a.graphics, a.edges, a.timeout)
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
    sys.exit(main(sys.argv[1:]))
