#!/usr/bin/env python3
"""A tiny GTK 4 game for the demo's gamepad scene: the left stick moves a
ball, A makes it pulse, and touching the star scores. It reads the pads
from /dev/input as a game does."""
import glob
import math
import os
import select
import struct
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

EVENT = struct.Struct("@llHHi")
ABS_X, ABS_Y, BTN_A = 0, 1, 304
SPEED = 160
STARS = ((167, 92), (47, 212), (167, 272), (107, 340))
state = {"x": 107.0, "y": 212.0, "t": 0, "dx": 0.0, "dy": 0.0, "pulse": 0, "star": 0, "score": 0}


def read_pads():
    fds = [os.open(p, os.O_RDONLY) for p in sorted(glob.glob("/dev/input/event*"))]
    while fds:
        for fd in select.select(fds, [], [])[0]:
            data = os.read(fd, EVENT.size * 64)
            for i in range(0, len(data), EVENT.size):
                _, _, kind, code, value = EVENT.unpack_from(data, i)
                if kind == 3 and code in (ABS_X, ABS_Y):
                    state["dx" if code == ABS_X else "dy"] = value / 32767
                elif kind == 1 and code == BTN_A and value:
                    state["pulse"] = 40


def tick(area):
    s = state
    now = GLib.get_monotonic_time() / 1e6
    dt, s["t"] = (now - s["t"] if s["t"] else 0), now
    if not area.get_width():
        return True
    s["x"] = min(max(s["x"] + s["dx"] * SPEED * dt, 12), area.get_width() - 12)
    s["y"] = min(max(s["y"] + s["dy"] * SPEED * dt, 12), area.get_height() - 12)
    sx, sy = STARS[s["star"] % len(STARS)]
    if math.hypot(s["x"] - sx, s["y"] - sy) < 24:
        s["star"] += 1
        s["score"] += 1
    s["pulse"] = max(0, s["pulse"] - 1)
    area.queue_draw()
    return True


def draw(area, cr, width, height):
    s = state
    cr.set_source_rgb(0.12, 0.14, 0.2)
    cr.paint()
    sx, sy = STARS[s["star"] % len(STARS)]
    cr.set_source_rgb(0.98, 0.78, 0.2)
    for i in range(10):
        r = 12 if i % 2 == 0 else 5
        a = -math.pi / 2 + i * math.pi / 5
        cr.line_to(sx + r * math.cos(a), sy + r * math.sin(a))
    cr.close_path()
    cr.fill()
    if s["pulse"]:
        cr.set_source_rgba(0.55, 0.8, 1, s["pulse"] / 40)
        cr.arc(s["x"], s["y"], 14 + (40 - s["pulse"]) * 0.8, 0, 2 * math.pi)
        cr.set_line_width(3)
        cr.stroke()
    cr.set_source_rgb(0.55, 0.8, 1)
    cr.arc(s["x"], s["y"], 12, 0, 2 * math.pi)
    cr.fill()
    cr.set_source_rgb(1, 1, 1)
    cr.set_font_size(16)
    cr.move_to(12, height - 14)
    cr.show_text(f"Score {s['score']}")


def activate(app):
    area = Gtk.DrawingArea()
    area.set_draw_func(draw)
    GLib.timeout_add(16, tick, area)
    threading.Thread(target=read_pads, daemon=True).start()
    Gtk.ApplicationWindow(application=app, title="Pad game", child=area,
                          titlebar=Adw.HeaderBar(decoration_layout=":close"),
                          default_width=224, default_height=480).present()


app = Adw.Application(application_id="org.example.PadGame")
app.connect("activate", activate)
app.run()
