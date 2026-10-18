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
desktop's. `record` is the session's announcement recorder.
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
from collections import Counter
from functools import cache

from . import picture, x
from .screen import Screen, SessionError, wait_for, wheel
from .session import ATSPI, bindings

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
        return out + (" @{},{} {}x{}".format(*self["bounds"]) if bounds and self.get("bounds")
                      else "")


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
    and not its window, and can't be placed. GTK 4 from 4.14, to 4.24 at
    least, loses the window's border (on X11 with no compositor, it has
    one) from its children: the frame's first child, which fills it but
    for that, is at the frame's corner rather than inside the border. The
    border and padding of every other node on the way down are lost too,
    and can't be told: docs/accessibility.md says how far off that is."""
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
        top.clear_cache()
        role = top.get_role()
        visit(top, role, 0, f"{role_name(Atspi, top, role)} {json.dumps(top.get_name() or '')}")
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
    out = walk(Atspi, window, until=lambda n: "focused" in n.states)
    return out[-1][1] if out and "focused" in out[-1][1].states else None


def roles(Atspi, names):
    return {int(getattr(Atspi.Role, r)) for r in names if hasattr(Atspi.Role, r)}


def ignored(ignore):
    """A test of a problem, (node, what), for the regexes in `ignore`: one
    found in its "PATH: WHAT" or in its tree line's "LINE: WHAT"."""
    try:
        found = [re.compile(i) for i in ignore]
    except re.error as e:
        raise SessionError(f"ignore takes regexes, and {e.pattern!r} isn't one: {e}") from None
    return lambda n, what: any(r.search(f"{n.path}: {what}") or r.search(f"{n.line()}: {what}")
                               for r in found)


def lint(Atspi, nodes, ignore=()):
    """(node, what's wrong) for each showing control, image or focusable node
    a screen reader would have no name to say for. An image inside a named
    control is part of it: the control's name is what gets said. Anything
    with a name inside it is said by what's in it, and a container with
    something focusable inside passes the focus on to it."""
    skip = ignored(ignore)
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


def tab_walk(key="Tab", most=50, window=None, settle=2.0, timeout=10, visible=False):
    """Press `key` up to `most` times and note where the focus lands each
    time. (nodes, how it ended, what it never reached, the text view it
    began in or None): "cycle" once it lands somewhere it has been
    (where it began, or a stop since), "trap" if a press leaves it where
    it was, "lost" if no node has it after one, "max" if none of those;
    "unfocused" if no node has it to begin with (X11 with no window
    manager), and nothing is pressed into that. Shift is pressed first:
    GTK 4 on Wayland says where the focus is only once a key has been (an
    input method may switch modes on it). A press that leaves it on the one thing in the
    window that could take it is a cycle of one. `key` isn't pressed in a
    text view one writes in, which takes Tab for itself: ESCAPES are,
    noted on it as `tried`, the one that moves the focus as `left_by`,
    one after which its text changed as `edited`; none is a trap, and
    one after which the text view isn't there ends the walk, "closed".
    Escape isn't tried in a dialog, which it would close, nor shift+Tab
    but in Qt; once that leaves, backward, the walk goes on from the first
    thing after the text view that takes the focus (grab_focus). Where the walk
    began in one nothing leaves, it gives the focus to the first thing
    outside it that takes it (AT-SPI's grab_focus, which GTK 4 doesn't
    do), so the rest of the window is walked before the trap. With
    `visible`, each node says how much of the screen round it changed as
    it took the focus (see shown())."""
    Atspi = atspi()
    screen = Screen()

    def press(k, before):
        """Press `k`, and once the focus has left `before` for a node, or
        after `settle`, the node it's on, or None."""
        screen.key(k)
        end = time.monotonic() + settle
        while True:
            now = focused(Atspi, window)
            if (now is not None and (before is None or now._id != before._id)
                    or time.monotonic() > end):
                return now
            time.sleep(0.05)

    toplevels(Atspi, window, timeout)
    start = press("shift", None)
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
    finally:
        screen.close()
    # Said only where the walk left it; else it's the trap.
    began = began if began and (began.get("left_by") or began.get("walked_from")) else None
    return seen, how, unreached(Atspi, window, [n for n in (start, *seen) if n]), began


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
    try:
        return Atspi.Text.get_text(n._acc, 0, -1)
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
    but for a menu bar's (F10's); and each table or list with an item
    that takes the focus, the focus never in it."""
    controls, grouped = roles(Atspi, CONTROLS), roles(Atspi, GROUPED)
    containers, items, lists = roles(Atspi, CONTAINERS), roles(Atspi, ITEMS), roles(Atspi, LISTS)
    menu_bar = roles(Atspi, ("MENU_BAR",))
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
        return ((n.path.startswith(o.path + "/") and o._role not in containers)
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


def click(name, role=None, window=None, button=1, double=False, timeout=10):
    """Click the node `at_node` finds; that node."""
    return at_node(name, role, window, timeout,
                   lambda s, x, y: s.click(x, y, button=button, double=double))


def scroll(name, dy, dx=0, role=None, window=None, timeout=10):
    """Turn the wheel over the node `at_node` finds; that node."""
    wheel(dy, dx)
    return at_node(name, role, window, timeout, lambda s, x, y: s.scroll(x, y, dy, dx))


def at_node(name, role, window, timeout, act):
    """Do `act` (a Screen, x, y) at the middle of the node named `name`, and
    with role `role` if given, each as x.pattern() matches a name, waiting
    up to `timeout` for it to show, its window raised. Nodes named exactly
    `name` are taken over those it matches as a regex: a calculator's "."
    is its button, not any one character. A match inside another (a
    button's label) is the outer one. The node."""
    Atspi = atspi()
    (named, note), (as_role, role_note) = x.pattern(name), x.pattern(role or "")
    showing = []

    def matches():
        showing[:] = [n for _, n in walk(Atspi, window, timeout) if n._showing]
        nodes = [n for n in showing if named(n.name) and (role is None or as_role(n.role))]
        nodes = [n for n in nodes if n.name == name] or nodes
        # A field named by its label is the one of the two that's taken.
        ids = {n._id for n in nodes}
        return [n for n in nodes if not any(n.path.startswith(o.path + "/") for o in nodes)
                and not ids & {f"{t.app.bus_name if t.app else ''}{t.path}"
                               for t in related(Atspi, n._acc, "LABEL_FOR")}]

    what = f"a node named {name!r}{note}" + (f" with role {role!r}{role_note}" if role else "")
    try:
        hits = wait_for(what, matches, timeout)
    except SessionError as e:
        near = not any(named(n.name) for n in showing) and difflib.get_close_matches(
            name, {n.name for n in showing if n.name}, 5)
        if not near:
            raise
        raise SessionError(f"{e}. The nearest names: "
                           + ", ".join(json.dumps(n, ensure_ascii=False) for n in near)) from None
    screen = Screen()
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
    if not box or box[2] <= 0 or box[3] <= 0:
        raise SessionError(f"can't tell where {n.line()} is on the screen")
    # Inside its window: a node scrolled out of view is still showing.
    px, py, w, h = overlap(box, origins[n._top][0])
    if not w or not h:
        raise SessionError(f"{n.line()} is outside its window")
    px, py, w, h = overlap([px, py, w, h], [0, 0, screen.xt.width, screen.xt.height])
    if not w or not h:
        raise SessionError(f"{n.line()} is outside the screen")
    try:
        screen.backend.focus(screen.env, origins[n._top][2])
        act(screen, px + w // 2, py + h // 2)
    finally:
        screen.close()
    return n


def contrast(window=None, text=4.5, graphics=3.0, edges=None, timeout=10, ignore=(),
             large=3.0):
    """What's drawn with too little contrast, as (node, what), measured on a
    picture of the screen, `window` raised, in each node's bounds: text, and
    a button, link, tab or menu item, which may draw text or an icon and
    can't be told apart, against its background (at least `text`, or
    `large` for large text); an image against its own (`graphics`); and
    with `edges`, a control's edge against what's round it. Disabled nodes
    and what's in them are left out, as WCAG leaves them, and so are a
    switch, slider, check box or the like, and what's outside its window
    (scrolled out of view). A node that can't be placed, and text with
    nothing drawn on its background, are problems too: nothing says they
    can be read. But for those ignored() leaves out. (problems, what
    wasn't measured but doesn't fail, as (node, what): text too small
    to, what's outside the screen, and GTK 4's, see GTK4_BOUNDS; why GTK
    4's weren't, a line each; how many were measured)."""
    skip = ignored(ignore)
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

    def kind(n):
        if (not n._showing or not n.name or "editable" in n.states
                or disabled(n) or n._role in textless or gtk3_switch(Atspi, n)):
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
    problems, notes, whys, count = [], [], [], 0

    def wrong(n, k):
        """Whether `n` is GTK 4's, whose positions are wrong: noted if so."""
        version = gtk4(n)
        inside = version and gtk4_release(n) < (4, 14)
        popover = inside and k != "edge" and popped(n)
        if (not version or version in GTK4_BOUNDS or inside and k != "edge" and not popover
                or os.environ.get("SHOTBOX_TRUST_GTK4_BOUNDS") == "1"):
            return False
        notes.append((n, f"{k} not measured: GTK 4 reports its position wrong"))
        why = (f"GTK {version} reports where what's in a popover is as if the popover were "
               "in its window, not where it's drawn; see docs/accessibility.md." if popover else
               f"GTK {version} reports where a widget's content is, inside its border and "
               "padding, so its edge can't be measured from what it reports; see "
               "docs/accessibility.md." if inside else
               f"GTK {version} reports where its widgets are without their containers' "
               "padding and borders, and a stack's child with its place in the stack counted "
               "twice, so their contrast can't be measured from what it reports; see "
               "docs/accessibility.md.")
        n["_why"] = why
        if why not in whys:
            whys.append(why)
        return True

    for n in measured:
        # A button with a label in it: the label's text is what's measured.
        if any(o.path.startswith(n.path + "/") for o in measured):
            continue
        k = kind(n)
        # GTK 4 puts a label in a control that draws text, so one with none
        # draws an icon: a graphic, held to graphics' ratio.
        if n._role in controls and gtk4(n):
            k = "graphics"
        if wrong(n, k):
            continue
        if not n.bounds:
            if not undrawn(Atspi, n):
                problems.append((n, f"{k} not measured: can't tell where it is"))
            continue
        box = shown(n)
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
        big = k == "text" and large_text(Atspi, n)
        least = large if big else text if k == "text" else graphics
        at = f"at least {least:g}" + (", large text" if big else "")
        got = picture.text_contrast(pic, box)
        count += bool(got and got[3] >= SMALLEST)
        if got and got[3] < SMALLEST:
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
        elif got and got[0] < least:
            said = said_color(Atspi, n)
            # Light or small text covers no pixel whole, so measures short of
            # its color, as text drawn see-through does: say both.
            meant = (f"; drawn lighter than its color, {picture.hex_of(said)}, which is "
                     f"{ratio_text(picture.ratio(said, got[2]))}:1 (light text, or opacity)"
                     if said and picture.ratio(said, got[2]) > got[0]
                     and picture.between(got[1], got[2], said) else "")
            problems.append((n, f"{k} {ratio_text(got[0])}:1, {picture.hex_of(got[1])} on "
                                f"{picture.hex_of(got[2])} ({at}){meant}"))
    if edges:
        for n in nodes:
            if not (n._showing and not disabled(n) and (
                    n._role in edged or (n._role == Atspi.Role.TEXT and "editable" in n.states))
                    ) or gtk3_switch(Atspi, n) or wrong(n, "edge"):
                continue
            box = shown(n)
            if box and not pic.clip(box):
                notes.append((n, "edge not measured: outside the screen"))
                continue
            got = box and picture.edge_contrast(pic, box)
            count += bool(got)
            # A field's edge can be what it's in: a frame's.
            field = n._role in (Atspi.Role.TEXT, Atspi.Role.COMBO_BOX)
            if got and got[0] < edges and not (field and framed(n, box)):
                problems.append((n, f"edge {ratio_text(got[0])}:1, {picture.hex_of(got[1])} by "
                                    f"{picture.hex_of(got[2])} (at least {edges:g})"))
    notes = [(n, what) for n, what in notes if not skip(n, what)]
    return ([(n, what) for n, what in problems if not skip(n, what)], notes,
            [w for w in whys if any(n.get("_why") == w for n, _ in notes)], count)


# Fewer pixels than this, a glyph (a ".") has no core of its own color.
SMALLEST = 16

# The GTK 4 releases from 4.14 that say where their widgets are: none yet.
# Others leave out each container's padding and border, count a stack's
# child twice, and set a label's text a character short, none of which
# can be undone from what they say (docs/accessibility.md, "GTK 4's
# positions"). Before 4.14, GTK 4 says where a widget's content is, which
# is measured, but for its edge and what's in a popover.
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
                        "without it, they're said, as arrows or shortcuts may reach them")
    w.add_argument("--ignore", **ignore)
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
    for s in (t, lt, w, c, sc, ct):
        s.add_argument("--window", metavar="RE",
                       help="just this window (its title, a regex matched in full, "
                            "or app=RE for its app id; one with no accessible name "
                            "goes by the display's, unless its program has a named "
                            "one picked), waiting for it")
        s.add_argument("--timeout", type=float, default=10, metavar="SECS",
                       help="how long to wait for the --window (10), or the node")
    for s in (t, lt, w, c, sc, ct):
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
        nodes = tree(a.window, a.timeout, bounds=a.json)
        if a.json:
            print(json.dumps([dict(public(n), depth=n.depth) for n in nodes]))
        else:
            for n in nodes:
                print("  " * n.depth + n.line())
        return 0
    if a.what in ("lint", "contrast"):
        notes, whys, count = [], [], 1
        if a.what == "lint":
            problems = lint(atspi(), tree(a.window, a.timeout, bounds=a.json), a.ignore)
        else:
            problems, notes, whys, count = contrast(a.window, a.text, a.graphics, a.edges,
                                                    a.timeout, a.ignore, a.large)
        # Said apart from the lines, on stderr, so stdout is PATH: PROBLEM.
        nothing = not problems and not count and whys
        said = " ".join([f"{len(notes)} not measured"
                         + ("." if nothing else ", which doesn't fail.")] + whys) if notes else ""
        if a.json:
            print(json.dumps([dict(public(n), problem=what, fails=fails,
                                   **({"why": n._why} if "_why" in n else {}))
                              for found, fails in ((problems, True), (notes, False))
                              for n, what in found]))
        else:
            for n, what in problems + notes:
                print(f"{n.path}: {what}")
        if nothing:
            sys.stderr.write(f"shotbox: nothing measured: {said}\n")
            return 2
        if said and not a.json:
            sys.stderr.write(said + "\n")
        return 1 if problems and not a.json else 0
    if a.what in ("click", "scroll"):
        n = (click(a.name, a.role, a.window, a.button, a.double, a.timeout) if a.what == "click"
             else scroll(a.name, a.dy, a.dx, a.role, a.window, a.timeout))
        print(json.dumps(public(n)) if a.json else n.line())
        return 0
    skip = ignored(a.ignore)
    seen, how, missed, began = tab_walk(a.key, a.max, a.window, timeout=a.timeout,
                                        visible=a.visible)
    missed = [n for n in missed if not skip(n, f"not reached by {a.key}")]
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
    lines.append(walk_end(key, most, seen, how, missed, focus))
    hidden = [n for n in seen if unseen(n)]
    if hidden:
        lines.append(f"focus not shown on {len(hidden)}")
    return lines


def walk_end(key, most, seen, how, missed, focus="shotbox focus"):
    """How a walk ended, as a line."""
    tried = seen[-1].get("tried") if seen else None
    unreached = f"; {len(missed)} not reached by {key}" if missed else ""
    return {
        "cycle": f"round again after {len(seen)}{unreached}",
        "trap": (f"stuck in a text view, which takes {key}: {', '.join(tried[:-1])} and "
                 f"{tried[-1]} leave the focus there" if tried
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
