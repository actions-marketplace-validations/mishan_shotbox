#!/usr/bin/env python3
"""A plain GTK 3 window to take pictures of: test/window.py TITLE [LOG].
With LOG, it holds an entry instead, and writes what's typed in it there."""

import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

w = Gtk.Window(title=sys.argv[1])
w.set_default_size(200, 120)
if len(sys.argv) > 2:
    entry = Gtk.Entry()
    entry.connect("changed", lambda e: open(sys.argv[2], "w").write(e.get_text()))
    w.add(entry)
else:
    w.add(Gtk.Label(label="a window"))
w.connect("destroy", Gtk.main_quit)
w.show_all()
Gtk.main()
