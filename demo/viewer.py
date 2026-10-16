#!/usr/bin/env python3
"""A tiny GTK 4 viewer for the demo's flicker scene, with a bug: a strip
four pixels wide down its picture's right edge, left uncleared, flips
between the picture and black every 50 ms."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

black = [False]


def draw(area, cr, width, height):
    for y in range(0, height, 8):
        v = y / height
        cr.set_source_rgb(0.15 + 0.2 * v, 0.35 + 0.3 * v, 0.55 + 0.2 * v)
        cr.rectangle(0, y, width, 8)
        cr.fill()
    cr.set_source_rgb(0.95, 0.8, 0.3)
    cr.arc(width * 0.4, height * 0.45, 28, 0, 6.3)
    cr.fill()
    if black[0]:
        cr.set_source_rgb(0, 0, 0)
        cr.rectangle(width - 24, 40, 4, 120)
        cr.fill()


def flip(area):
    black[0] = not black[0]
    area.queue_draw()
    return True


def activate(app):
    area = Gtk.DrawingArea(vexpand=True)
    area.set_draw_func(draw)
    GLib.timeout_add(50, flip, area)
    Gtk.ApplicationWindow(application=app, title="Viewer",
                          titlebar=Adw.HeaderBar(decoration_layout=":close"), child=area,
                          default_width=224, default_height=240).present()


app = Adw.Application(application_id="org.example.Viewer")
app.connect("activate", activate)
app.run()
