#!/usr/bin/env python3
"""A plain GTK 3 window to take pictures of: test/window.py TITLE [LOG]
[corner], or TITLE contrast. With LOG, it holds an entry instead, and
writes what's typed in it there; with corner too, a button in its top
left corner, and writes "clicked" there when it's clicked. With contrast,
a switch and a text view in a frame with an edge, which `a11y contrast
--edges 3` should pass; light text whose color is dark enough, which it
may measure short; and a label drawn see-through, which it shouldn't."""

import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

w = Gtk.Window(title=sys.argv[1])
w.set_default_size(200, 120)
if sys.argv[2:] == ["contrast"]:
    css = Gtk.CssProvider()
    css.load_from_data(b"window { background: #faf8fc; } label { color: #5f5670; }"
                       b" .dim { opacity: 0.55; } frame > border { border-color: #767676; }")
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
    light = Gtk.Label()
    light.set_markup('<span weight="light">Contact</span>')
    dim = Gtk.Label(label="Dimmed")
    dim.get_style_context().add_class("dim")
    switch = Gtk.Switch(halign=Gtk.Align.START)
    switch.get_accessible().set_name("Wi-Fi")
    notes = Gtk.TextView(margin=6)
    notes.set_size_request(-1, 60)
    notes.get_accessible().set_name("Notes")
    frame = Gtk.Frame()
    frame.add(notes)
    for widget in (light, dim, switch, frame):
        box.add(widget)
    w.add(box)
elif sys.argv[3:] == ["corner"]:
    button = Gtk.Button(label="Corner", halign=Gtk.Align.START, valign=Gtk.Align.START)
    button.connect("clicked", lambda b: open(sys.argv[2], "w").write("clicked"))
    w.add(button)
elif len(sys.argv) > 2:
    entry = Gtk.Entry()
    entry.connect("changed", lambda e: open(sys.argv[2], "w").write(e.get_text()))
    w.add(entry)
else:
    w.add(Gtk.Label(label="a window"))
w.connect("destroy", Gtk.main_quit)
w.show_all()
Gtk.main()
