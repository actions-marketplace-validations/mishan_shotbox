#!/usr/bin/env python3
"""A libadwaita window to read the accessible tree of: test/a11y_app.py
[fixed] [notes] [trap] [dialog] [lists] [one] [unfocusable] [untitled] [second] [faint]
[thin] [long] [keys]. Its icon-only button,
its status icon and its drawing area have no accessible name unless
`fixed`, nor has the window with `untitled`, and `second` opens an
untitled second window; `notes` puts a text view first, which keeps Tab
(and prints its text as it changes), `trap` one that keeps ctrl+Tab,
shift+Tab, Escape and F6 too, and `dialog` that one in a dialog; `lists`
is lists and a notebook instead, `one` a single button, and
`unfocusable` that, and a button Tab can't reach;
`faint` adds a label too light to read, `thin` one in small monospace
spaced by a fraction of a pixel, whose stems are thinner than one, `long` is a scrolled
list running far past the window, and `keys` buttons named as a calculator's are, which
aren't regexes or match more than themselves as one.
The Announce button says something to a screen reader (GTK 4.14+)."""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402


def lists():
    """Containers that take the focus with no name of their own, which
    Tab passes through to their rows: a list box and a list view, each
    scrolled, in a notebook's page."""
    rows = Gtk.ListBox()
    for text in ("One", "Two"):
        rows.append(Gtk.Label(label=text))
    factory = Gtk.SignalListItemFactory()
    factory.connect("setup", lambda f, item: item.set_child(Gtk.Label()))
    factory.connect("bind", lambda f, item: item.get_child().set_label(item.get_item().get_string()))
    colors = Gtk.ListView(model=Gtk.SingleSelection(model=Gtk.StringList.new(["Red", "Green"])),
                          factory=factory)
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    for child in (rows, colors):
        page.append(Gtk.ScrolledWindow(child=child, vexpand=True))
    tabs = Gtk.Notebook()
    tabs.append_page(page, Gtk.Label(label="Lists"))
    tabs.append_page(Gtk.Label(label="Nothing"), Gtk.Label(label="Other"))
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(Gtk.Button(label="Save"))
    box.append(tabs)
    return box


def activate(app):
    w = Adw.ApplicationWindow(application=app, title=None if "untitled" in sys.argv else "a11y-test")
    w.set_default_size(320, 240)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    save = Gtk.Button(label="Save")
    delete = Gtk.Button(icon_name="edit-delete-symbolic")
    status = Gtk.Image(icon_name="network-offline-symbolic")
    # A widget of its own, which Tab lands on: role generic (AT-SPI's
    # panel), which GTK gives no name, unless it's given a role that has one.
    fixed = "fixed" in sys.argv
    sketch = Gtk.DrawingArea(focusable=True, content_height=20, accessible_role=(
        Gtk.AccessibleRole.IMG if fixed else Gtk.AccessibleRole.GENERIC))
    if fixed:
        delete.set_tooltip_text("Delete")
        status.update_property([Gtk.AccessibleProperty.LABEL], ["Offline"])
        sketch.update_property([Gtk.AccessibleProperty.LABEL], ["Sketch"])
    announce = Gtk.Button(label="Announce")
    announce.connect("clicked", lambda b: b.announce(
        "Saved to notes.md", Gtk.AccessibleAnnouncementPriority.MEDIUM))
    wrap = Gtk.CheckButton(label="Wrap lines", active=True)
    # On its own, not in a control: every GTK 4 places that right.
    faint = Gtk.Label(label="Faint words", visible="faint" in sys.argv)
    thin = Gtk.Label(label="CONTACT", visible="thin" in sys.argv)
    css = Gtk.CssProvider()
    css.load_from_string("label.faint { color: #a0a0a0; } label.thin { color: #5f5670;"
                         " font-family: monospace; font-size: 9.4px; letter-spacing: 0.37px; }")
    faint.add_css_class("faint")
    thin.add_css_class("thin")
    Gtk.StyleContext.add_provider_for_display(w.get_display(), css,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    entry = Gtk.Entry()
    entry.update_property([Gtk.AccessibleProperty.LABEL], ["File name"])
    # Decorative: no name, and left out of the tree.
    mark = Gtk.Image(icon_name="emblem-ok-symbolic",
                     accessible_role=Gtk.AccessibleRole.PRESENTATION)
    for widget in (save, delete, announce, wrap, entry, status, mark, sketch, faint, thin):
        box.append(widget)
    notes = Gtk.TextView()
    notes.get_buffer().set_text("notes")
    notes.get_buffer().connect("changed", lambda b: print(repr(b.props.text), flush=True))
    notes.update_property([Gtk.AccessibleProperty.LABEL], ["Notes"])
    if "trap" in sys.argv or "dialog" in sys.argv:
        # As an editor's own shortcuts can.
        keep = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keep.connect("key-pressed", lambda c, key, code, state: key in (
            Gdk.KEY_Escape, Gdk.KEY_F6, Gdk.KEY_ISO_Left_Tab) or (key == Gdk.KEY_Tab and bool(
                state & Gdk.ModifierType.CONTROL_MASK)))
        notes.add_controller(keep)
    if "notes" in sys.argv or "trap" in sys.argv:
        # A text view takes Tab for itself; GTK's ctrl+Tab leaves it.
        box.prepend(notes)
        notes.grab_focus()
    if "lists" in sys.argv:
        box = lists()
    if "long" in sys.argv:
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for i in range(200):
            rows.append(Gtk.Label(label=f"Row {i}"))
        box = Gtk.ScrolledWindow(child=rows, vexpand=True)
    if "keys" in sys.argv:
        box = Gtk.Box()
        for label in ("1", "2", ".", "+", "(Draft)"):
            box.append(Gtk.Button(label=label))
    view = Adw.ToolbarView(content=box)
    view.add_top_bar(Adw.HeaderBar())
    # One thing to take the focus: no header bar and its buttons either.
    if "one" in sys.argv or "unfocusable" in sys.argv:
        view = Gtk.Box()
        view.append(Gtk.Button(label="Save"))
        if "unfocusable" in sys.argv:
            view.append(Gtk.Button(label="Play", focusable=False))
    w.set_content(view)
    w.present()
    if "dialog" in sys.argv:
        # Escape, a way out of a text view elsewhere, closes a dialog.
        inside = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inside.append(notes)
        inside.append(Gtk.Button(label="Send"))
        Adw.Dialog(child=inside, title="Reply").present(w)
        notes.grab_focus()
    if "second" in sys.argv:
        other = Adw.ApplicationWindow(application=app)
        other.set_content(Gtk.Label(label="second"))
        other.present()


app = Adw.Application(application_id="org.shotbox.A11yTest")
app.connect("activate", activate)
app.run([])
