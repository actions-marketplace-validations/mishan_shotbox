#!/usr/bin/env python3
"""A plain GTK 3 window to take pictures of: test/window.py TITLE [LOG]
[corner], or TITLE contrast, or TITLE slow MS, or TITLE flip MS, or TITLE flash MS, or
TITLE move, or TITLE scroll, or TITLE strip, or TITLE scene, or TITLE spaced,
or TITLE band. With LOG, it holds an entry instead, and
writes what's typed in it there; with corner too, a button in its top
left corner, and writes "clicked" there when it's clicked. With contrast,
a switch and a text view in a frame with an edge, which `a11y contrast
--edges 3` should pass; light text whose color is dark enough, which it
may measure short; and a label drawn see-through, which it shouldn't. With slow, a square that
turns dark MS milliseconds after the pointer comes into the window, and
light again that long after it leaves: a known latency."""

import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

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
elif sys.argv[2:3] == ["slow"]:
    late = int(sys.argv[3])
    square = Gtk.DrawingArea()
    dark = [False]
    square.connect("draw", lambda a, cr: (cr.set_source_rgb(*[0.1 if dark[0] else 0.9] * 3),
                                          cr.paint()))

    def turn(to):
        def now():
            dark[0] = to
            square.queue_draw()
        GLib.timeout_add(late, now)
    w.connect("enter-notify-event", lambda *_: turn(True))
    w.connect("leave-notify-event", lambda *_: turn(False))
    w.add(square)
elif sys.argv[2:3] == ["flip"]:
    # A square that turns green and black, and back, every MS milliseconds.
    square, green = Gtk.DrawingArea(), [False]
    square.connect("draw", lambda a, cr: (cr.set_source_rgb(0, 0.6 if green[0] else 0, 0),
                                          cr.paint()))

    def flip():
        green[0] = not green[0]
        square.queue_draw()
        return True
    GLib.timeout_add(int(sys.argv[3]), flip)
    w.add(square)
elif sys.argv[2:3] == ["flash"]:
    # A green square that goes black for MS milliseconds, every second.
    square, black = Gtk.DrawingArea(), [False]
    square.connect("draw", lambda a, cr: (cr.set_source_rgb(0, 0 if black[0] else 0.6, 0),
                                          cr.paint()))

    def turn(to):
        black[0] = to
        square.queue_draw()
        return False

    def flash():
        turn(True)
        GLib.timeout_add(int(sys.argv[3]), turn, False)
        return True
    GLib.timeout_add(1000, flash)
    w.add(square)
elif sys.argv[2:3] == ["scene"]:
    # bzflag's strip, as it was: black every other 30 ms, and between, a
    # scene that's moved on everywhere, as a turning tank's view has.
    area, n, black = Gtk.DrawingArea(), [0], [False]

    def draw(a, cr):
        cr.set_source_rgb(0, 0, 0)
        cr.paint()
        if not black[0]:
            for x in range(0, 200, 8):
                for y in range(0, 120, 8):
                    v = ((x * 7 + y * 13 + n[0] * 29) % 97) / 97
                    cr.set_source_rgb(0.1 * v, 0.4 + 0.5 * v, 0.1)
                    cr.rectangle(x, y, 8, 8)
                    cr.fill()
    area.connect("draw", draw)

    def swap():
        black[0] = not black[0]
        n[0] += 1
        area.queue_draw()
        return True
    GLib.timeout_add(30, swap)
    w.add(area)
elif sys.argv[2:3] == ["band"]:
    # bzflag's strip as narrow as it was: three columns at a tile's edge,
    # black every other 30 ms, and between, a scene that's moved on; text
    # that doesn't change beside it, in the rest of the tile.
    area, n, black = Gtk.DrawingArea(), [0], [False]

    def draw(a, cr):
        cr.set_source_rgb(0.95, 0.95, 0.95)
        cr.paint()
        cr.set_source_rgb(0.1, 0.1, 0.1)
        for line in range(8):
            cr.move_to(4, 14 + line * 14)
            cr.show_text("still text, beside the band")
        for y in range(0, 120, 4):
            v = ((y * 13 + n[0] * 29) % 97) / 97
            cr.set_source_rgb(*((0, 0, 0) if black[0] else (0.1 * v, 0.4 + 0.5 * v, 0.1)))
            cr.rectangle(189, y, 3, 4)
            cr.fill()
    area.connect("draw", draw)

    def swap():
        black[0] = not black[0]
        n[0] += 1
        area.queue_draw()
        return True
    GLib.timeout_add(30, swap)
    w.add(area)
elif sys.argv[2:3] == ["strip"]:
    # As bzflag's strip: black every other 30 ms, and between, a green
    # scene with a square crossing it, a little further on each time.
    area, began, black = Gtk.DrawingArea(), GLib.get_monotonic_time(), [False]

    def draw(a, cr):
        cr.set_source_rgb(0, 0, 0) if black[0] else cr.set_source_rgb(0, 0.6, 0)
        cr.paint()
        if not black[0]:
            cr.set_source_rgb(1, 1, 1)
            cr.rectangle((GLib.get_monotonic_time() - began) / 1e6 * 100 % 216 - 16, 50, 16, 16)
            cr.fill()
    area.connect("draw", draw)

    def swap():
        black[0] = not black[0]
        area.queue_draw()
        return True
    GLib.timeout_add(30, swap)
    w.add(area)
elif sys.argv[2:3] in (["move"], ["scroll"], ["spaced"]):
    # Motion, which is neither: a black square crossing white at 300
    # pixels a second, or lines of text scrolling up at 200.
    area, began = Gtk.DrawingArea(), GLib.get_monotonic_time()

    def draw(a, cr):
        t = (GLib.get_monotonic_time() - began) / 1e6
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.set_source_rgb(0, 0, 0)
        if sys.argv[2] == "move":
            cr.rectangle(t * 300 % 216 - 16, 50, 16, 16)
            cr.fill()
        else:
            # Lines 14 pixels apart, or 40, with white between.
            gap = 14 if sys.argv[2] == "scroll" else 40
            for line in range(120 // gap + 2):
                cr.move_to(4, 14 + line * gap - t * 200 % gap)
                cr.show_text(f"line {line + int(t * 200 / gap)}: the quick brown fox")
    area.connect("draw", draw)
    GLib.timeout_add(16, lambda: area.queue_draw() or True)
    w.add(area)
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
