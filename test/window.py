#!/usr/bin/env python3
"""A plain GTK 3 window to take pictures of: test/window.py TITLE [LOG]
[corner]. With LOG, it holds an entry instead, and writes what's typed in
it there; with corner too, a button in its top left corner, and writes
"clicked" there when it's clicked."""

import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

w = Gtk.Window(title=sys.argv[1])
w.set_default_size(200, 120)
if sys.argv[3:] == ["corner"]:
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
