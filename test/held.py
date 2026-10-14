#!/usr/bin/env python3
"""A GTK 3 window that writes each key and button it gets, pressed or let
go, to LOG, one per line with the time: test/held.py TITLE LOG."""

import sys
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

log = open(sys.argv[2], "a", buffering=1)


def note(what, name):
    log.write(f"{what} {name} {time.monotonic():.3f}\n")


w = Gtk.Window(title=sys.argv[1])
w.set_default_size(200, 120)
w.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK)
w.connect("key-press-event", lambda w, e: note("press", Gdk.keyval_name(e.keyval)))
w.connect("key-release-event", lambda w, e: note("release", Gdk.keyval_name(e.keyval)))
w.connect("button-press-event", lambda w, e: e.type == Gdk.EventType.BUTTON_PRESS
          and note("press", f"button{e.button}"))
w.connect("button-release-event", lambda w, e: note("release", f"button{e.button}"))
w.connect("destroy", Gtk.main_quit)
w.show_all()
Gtk.main()
