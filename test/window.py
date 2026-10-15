#!/usr/bin/env python3
"""A plain GTK 3 window to take pictures of: test/window.py TITLE [LOG]
[corner], or TITLE contrast, or TITLE slow MS, or TITLE flip MS, or TITLE flash MS, or
TITLE move, or TITLE scroll, or TITLE strip, or TITLE scene, or TITLE spaced,
or TITLE band [WIDTH [DARK]], or TITLE fast, or TITLE noisy, or TITLE panel,
or TITLE text, line, gradient or passer. With LOG, it holds an entry instead, and
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
elif sys.argv[2:3] in (["band"], ["noisy"]):
    # bzflag's strip as narrow as it was: WIDTH (3) columns at a tile's
    # edge, black every other 30 ms, and between, a scene that's moved on,
    # dimmer by DARK; text that doesn't change beside it. noisy: the whole
    # window, its black a dark gray with noise in it, as an uncleared
    # buffer's might be.
    area, n, black = Gtk.DrawingArea(), [0], [False]
    wide = int(sys.argv[3]) if sys.argv[2] == "band" and len(sys.argv) > 3 else 3
    dim = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0

    def draw(a, cr):
        cr.set_source_rgb(0.95, 0.95, 0.95)
        cr.paint()
        cr.set_source_rgb(0.1, 0.1, 0.1)
        for line in range(8):
            cr.move_to(4, 14 + line * 14)
            cr.show_text("still text, beside the band")
        if sys.argv[2] == "noisy":
            for x in range(0, 200, 4):
                for y in range(0, 120, 4):
                    v = ((x * 7 + y * 13 + n[0] * 29) % 97) / 97
                    if black[0]:
                        g = 0.12 + 0.04 * (((x * 31 + y * 17 + n[0] * 53) % 9) - 4) / 4
                        cr.set_source_rgb(g, g, g)
                    else:
                        cr.set_source_rgb(0.1 * v, 0.4 + 0.5 * v, 0.1)
                    cr.rectangle(x, y, 4, 4)
                    cr.fill()
            return
        for y in range(0, 120, 4):
            v = ((y * 13 + n[0] * 29) % 97) / 97
            cr.set_source_rgb(*((0, 0, 0) if black[0] else
                                (0.1 * v * dim, (0.4 + 0.5 * v) * dim, 0.1 * dim)))
            cr.rectangle(192 - wide, y, wide, 4)
            cr.fill()
    area.connect("draw", draw)

    def swap():
        black[0] = not black[0]
        n[0] += 1
        area.queue_draw()
        return True
    GLib.timeout_add(30, swap)
    w.add(area)
elif sys.argv[2:3] == ["panel"]:
    # A gray panel with a word in it, white for 40 ms every second, beside
    # a caret that blinks every half second and a spinner that turns: a
    # flash, with what changes beside it something else.
    area, n, white = Gtk.DrawingArea(), [0], [False]

    def draw(a, cr):
        cr.set_source_rgb(0.5, 0.5, 0.5)
        cr.paint()
        cr.set_source_rgb(1, 1, 1) if white[0] else cr.set_source_rgb(0.3, 0.3, 0.3)
        cr.rectangle(32, 32, 64, 64)
        cr.fill()
        cr.set_source_rgb(0, 0, 0)
        cr.move_to(36, 46)
        cr.show_text("Hi")
        if n[0] // 17 % 2:
            cr.rectangle(98, 40, 2, 14)
            cr.fill()
        cr.arc(124, 64, 8, n[0] * 0.5, n[0] * 0.5 + 4)
        cr.stroke()
    area.connect("draw", draw)

    def tick():
        n[0] += 1
        area.queue_draw()
        return True

    def turn(to):
        white[0] = to
        area.queue_draw()
        return False

    def flash():
        turn(True)
        GLib.timeout_add(40, turn, False)
        return True
    GLib.timeout_add(30, tick)
    GLib.timeout_add(1000, flash)
    w.add(area)
elif sys.argv[2:3] == ["text"]:
    # A terminal's listing scrolling a line every 30 ms, as winterm's
    # `ls -lR` does: lines of other text, long and short and empty, over
    # a plain background, so a tile goes blank and back to text that
    # isn't the text it had.
    import random
    area, n = Gtk.DrawingArea(), [0]

    def draw(a, cr):
        cr.set_source_rgb(0.9, 0.9, 0.9)
        cr.paint()
        cr.set_source_rgb(0, 0, 0.44)
        for line in range(9):
            r = random.Random(n[0] + line)
            cr.move_to(4, 12 + line * 14)
            cr.show_text("".join(r.choice("abcdefghijklmnopqrstuvwxyz-./ ")
                                 for _ in range(r.choice((0, 3, 8, 20, 32)))))
    area.connect("draw", draw)
    GLib.timeout_add(30, lambda: n.__setitem__(0, n[0] + 1) or area.queue_draw() or True)
    w.add(area)
elif sys.argv[2:3] == ["line"]:
    # A line a pixel wide, not smoothed, as bzflag's ground lines while the
    # tank turns: drifting, and a little back and on each frame, so its
    # steps move a few pixels along it, and back.
    area, n = Gtk.DrawingArea(), [0]

    def draw(a, cr):
        cr.set_antialias(1)    # cairo.ANTIALIAS_NONE
        cr.set_source_rgb(0, 0.3, 0)
        cr.paint()
        cr.set_source_rgb(0, 0.83, 0.67)
        cr.set_line_width(1)
        y = 58 + 0.02 * n[0] + (0.25 if n[0] % 2 else -0.25)
        cr.move_to(0, y)
        cr.line_to(200, y + 10)
        cr.stroke()
    area.connect("draw", draw)
    GLib.timeout_add(30, lambda: n.__setitem__(0, n[0] + 1) or area.queue_draw() or True)
    w.add(area)
elif sys.argv[2:3] == ["passer"]:
    # bzflag's strip as it was at the screen's edge: six columns black and
    # green every other frame of 30 ms, in a tile with two of the scene's
    # beside it, through which something passes for a third of each
    # second, and the page's background after.
    area, n, began = Gtk.DrawingArea(), [0], GLib.get_monotonic_time()

    def draw(a, cr):
        t = (GLib.get_monotonic_time() - began) / 1e6
        cr.set_source_rgb(0.9, 0.9, 0.9)
        cr.paint()
        cr.set_source_rgb(0, 0.4, 0)
        cr.rectangle(0, 0, 178, 120)
        cr.fill()
        if t % 1 < 0.33:
            cr.set_source_rgb(1, 1, 1)
            cr.rectangle(166, t % 1 * 400 - 12, 12, 12)
            cr.fill()
        cr.set_source_rgb(*((0, 0, 0) if n[0] % 2 else (0.53, 0.79, 0)))
        cr.rectangle(178, 0, 6, 120)
        cr.fill()
    area.connect("draw", draw)
    GLib.timeout_add(30, lambda: n.__setitem__(0, n[0] + 1) or area.queue_draw() or True)
    w.add(area)
elif sys.argv[2:3] == ["gradient"]:
    # Two faces 8% apart, the edge between them sweeping past unevenly,
    # forward and back a little each frame, as a turning view's do.
    area, n = Gtk.DrawingArea(), [0]

    def draw(a, cr):
        cr.set_source_rgb(0.84, 0.44, 0.44)
        cr.paint()
        cr.set_source_rgb(0.76, 0.44, 0.44)
        cr.rectangle(0, 0, n[0] * 3 % 240 + (6 if n[0] % 2 else -6), 120)
        cr.fill()
    area.connect("draw", draw)
    GLib.timeout_add(30, lambda: n.__setitem__(0, n[0] + 1) or area.queue_draw() or True)
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
elif sys.argv[2:3] in (["move"], ["scroll"], ["spaced"], ["fast"]):
    # Motion, which is neither: a black square crossing white at 300
    # pixels a second, or lines of text scrolling up at 200.
    area, began = Gtk.DrawingArea(), GLib.get_monotonic_time()

    def draw(a, cr):
        t = (GLib.get_monotonic_time() - began) / 1e6
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.set_source_rgb(0, 0, 0)
        if sys.argv[2] in ("move", "fast"):
            # fast: a red square of 12, through a tile in a picture or two.
            if sys.argv[2] == "fast":
                cr.set_source_rgb(0.8, 0, 0)
            side, speed = (12, 1200) if sys.argv[2] == "fast" else (16, 300)
            cr.rectangle(t * speed % 216 - 16, 50, side, side)
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
