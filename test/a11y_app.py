#!/usr/bin/env python3
"""A libadwaita window to read the accessible tree of: test/a11y_app.py
[fixed] [trap] [lists] [one] [untitled] [second] [faint] [thin] [long]. Its icon-only button,
its status icon and its drawing area have no accessible name unless
`fixed`, nor has the window with `untitled`, and `second` opens an
untitled second window; `trap` puts a text view first, which keeps Tab;
`lists` is lists and a notebook instead, and `one` a single button;
`faint` adds a label too light to read, `thin` one in small monospace
spaced by a fraction of a pixel, whose stems are thinner than one, and `long` is a scrolled
list running far past the window.
The Announce button says something to a screen reader (GTK 4.14+)."""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


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
    if "trap" in sys.argv:
        # A text view takes Tab for itself: Tab can't leave it.
        notes = Gtk.TextView()
        notes.update_property([Gtk.AccessibleProperty.LABEL], ["Notes"])
        box.prepend(notes)
        notes.grab_focus()
    if "lists" in sys.argv:
        box = lists()
    if "long" in sys.argv:
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for i in range(200):
            rows.append(Gtk.Label(label=f"Row {i}"))
        box = Gtk.ScrolledWindow(child=rows, vexpand=True)
    view = Adw.ToolbarView(content=box)
    view.add_top_bar(Adw.HeaderBar())
    # One thing to take the focus: no header bar and its buttons either.
    w.set_content(Gtk.Button(label="Save") if "one" in sys.argv else view)
    w.present()
    if "second" in sys.argv:
        other = Adw.ApplicationWindow(application=app)
        other.set_content(Gtk.Label(label="second"))
        other.present()


app = Adw.Application(application_id="org.shotbox.A11yTest")
app.connect("activate", activate)
app.run([])
