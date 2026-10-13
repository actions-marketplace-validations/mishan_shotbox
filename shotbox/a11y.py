"""The accessible (AT-SPI) tree of what runs in a session, as a screen
reader sees it, for a session started with --a11y.

    shotbox a11y tree [--window RE]     the tree, a node a line
    shotbox a11y lint [--window RE]     controls and images with no name
    shotbox a11y tabwalk [--key Tab]    where the keyboard focus goes

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

from . import x
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
                _id=f"{acc.app.bus_name if acc.app else ''}{acc.path}")


def walk(Atspi, window=None, timeout=10):
    """Every visible node under the toplevels `window` picks, depth first,
    as (depth, Node)."""
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
    return out


def tree(window=None, timeout=10):
    Atspi = atspi()
    return [Node(n, depth=d) for d, n in walk(Atspi, window, timeout=timeout)]


def focused(Atspi, window=None):
    """The node with the keyboard focus, or None."""
    for _, n in walk(Atspi, window):
        if "focused" in n.states:
            return n
    return None


def roles(Atspi, names):
    return {int(getattr(Atspi.Role, r)) for r in names if hasattr(Atspi.Role, r)}


def lint(Atspi, nodes):
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
    return problems


def tab_walk(key="Tab", most=50, window=None, settle=2.0, timeout=10):
    """Press `key` up to `most` times and note where the focus lands each
    time. (nodes, how it ended): "cycle" once it lands somewhere it has
    been (where it began, or a stop since), "trap" if a press leaves it
    where it was, "max" if neither. A press that leaves it on the one
    thing in the window that could take it is a cycle of one."""
    Atspi = atspi()
    screen = Screen()
    toplevels(Atspi, window, timeout)
    start = focused(Atspi, window)
    seen = []
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
            seen.append(now)
            if now._id in been:
                return seen, "cycle"
        return seen, "max"
    finally:
        screen.close()


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
    sub = p.add_subparsers(dest="what", required=True, metavar="{tree,lint,tabwalk}")
    t = sub.add_parser("tree", help="the tree, a node a line, indented")
    lt = sub.add_parser("lint", help="controls, images and focusable nodes with no name")
    w = sub.add_parser("tabwalk", help="press a key until the focus comes back round")
    w.add_argument("--key", default="Tab", metavar="CHORD", help="the key to press (Tab)")
    w.add_argument("--max", type=int, default=50, metavar="N", help="at most this many (50)")
    for s in (t, lt, w):
        s.add_argument("--window", metavar="RE",
                       help="just this window (its title, a regex matched in full, "
                            "or app=RE for its app id; one with no accessible name "
                            "goes by the display's, unless its program has a named "
                            "one picked), waiting for it")
        s.add_argument("--timeout", type=float, default=10, metavar="SECS",
                       help="how long to wait for the --window (10)")
    for s in (t, w):
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
        nodes = tree(a.window, a.timeout)
        if a.json:
            print(json.dumps([dict(public(n), depth=n.depth) for n in nodes]))
        else:
            for n in nodes:
                print("  " * n.depth + n.line())
        return 0
    if a.what == "lint":
        problems = lint(atspi(), tree(a.window, a.timeout))
        for n, what in problems:
            print(f"{n.path}: {what}")
        return 1 if problems else 0
    seen, how = tab_walk(a.key, a.max, a.window, timeout=a.timeout)
    if a.json:
        print(json.dumps({"focus": [public(n) for n in seen], "end": how}))
        return 0
    for n in seen:
        print(n.line())
    if how == "cycle":
        print(f"round again after {len(seen)}")
        return 0
    if how == "trap":
        print(f"stuck: {a.key} leaves the focus where it was")
    else:
        print(f"not round again after {a.max}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
