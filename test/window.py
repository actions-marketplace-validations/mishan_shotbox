#!/usr/bin/env python3
"""A plain GTK 3 window to take pictures of: test/window.py TITLE [LOG]
[corner|list], or TITLE contrast, or TITLE slow MS, or TITLE flip MS, or TITLE flash MS, or
TITLE caret MS, or TITLE spinner, or TITLE moving, or
TITLE move, or TITLE scroll, or TITLE strip, or TITLE scene, or TITLE spaced,
or TITLE smooth, or TITLE band [WIDTH [DARK]], or TITLE fast, or TITLE noisy, or TITLE panel,
or TITLE text, pages, redraw, stair, poles, line, gradient, grating, passer, skip,
edge, gray, busy, icon, dropout, beep, fields, alert, popover or wide. With LOG, it holds an entry instead,
and writes what's typed in it there; with corner too, a button in its top
left corner, and writes "clicked" there when it's clicked; with list, rows
longer and more of them than fit, and writes where they're scrolled to,
down and across, there. With contrast,
a switch and a text view in a frame with an edge, which `a11y contrast
--edges 3` should pass; light text whose color is dark enough, which it
may measure short; a label drawn see-through, which it shouldn't; large
text, which needs only 3:1; and a dot, too small to measure. With slow, a square that
turns dark MS milliseconds after the pointer comes into the window, and
light again that long after it leaves: a known latency. With fields, an
entry named by its mnemonic label, and a search bar not revealed, in a
notebook with its tabs hidden. With alert, a Check button that shows an
error in an info bar and is renamed Checked, a Resend button that counts
down to 1s, and a Delete button that asks in a message dialog, its window
titled Delete. With dot,
a "." and nothing else. With popover, a faint label and a modal popover
open below it. With wide, a window 1000 pixels wide, a label
as wide, its text at the right, and below it a button at each end. With
moving, a faint label that's dark half a second in two, and dark words
sliding by; with blinking, dark words shown and hidden."""

import sys
import time

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
    big = Gtk.Label()
    big.set_markup('<span size="xx-large" weight="bold" foreground="#808080">Heading</span>')
    for widget in (light, dim, switch, frame, big, Gtk.Label(label=".")):
        box.add(widget)
    w.add(box)
elif sys.argv[2:] == ["wide"]:
    box = Gtk.Box()
    box.pack_start(Gtk.Button(label="Here"), False, False, 0)
    box.pack_end(Gtk.Button(label="There"), False, False, 0)
    # Spaced, so the buttons' shadow isn't in the label's box.
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    rows.add(Gtk.Label(label="Far", xalign=1))
    rows.add(box)
    w.set_size_request(1000, 120)
    w.add(rows)
elif sys.argv[2:] == ["popover"]:
    css = Gtk.CssProvider()
    css.load_from_data(b"label { color: #a0a0a0; }")
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    faint = Gtk.Label(label="Faint words")
    pop = Gtk.Popover(relative_to=faint, modal=True, position=Gtk.PositionType.BOTTOM)
    pop.add(Gtk.Button(label="Inside", visible=True))
    GLib.timeout_add(300, lambda: pop.popup() and False)
    w.add(faint)
elif sys.argv[2:] == ["dot"]:
    w.add(Gtk.Label(label="."))
elif sys.argv[2:] == ["fields"]:
    # GTK 3 names a field by its label as labelled-by, not as its own name;
    # an unrevealed bar's unnamed button is visible, but not showing; a
    # notebook's hidden tab is showing, but has no size.
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    label = Gtk.Label.new_with_mnemonic("_Name:")
    entry = Gtk.Entry()
    label.set_mnemonic_widget(entry)
    bar = Gtk.Revealer()
    bar.add(Gtk.Button.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
    for widget in (label, entry, bar):
        box.add(widget)
    book = Gtk.Notebook(show_tabs=False)
    book.append_page(box)
    w.add(book)
elif sys.argv[2:] == ["alert"]:
    # The bar shown, not revealed: revealing one says nothing of it showing.
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    bar = Gtk.InfoBar(message_type=Gtk.MessageType.ERROR, no_show_all=True)
    bar.get_content_area().add(Gtk.Label(label="Name is taken", visible=True))
    check = Gtk.Button(label="Check")
    check.connect("clicked", lambda b: (bar.show(), b.set_label("Checked")))
    resend = Gtk.Button(label="Resend in 5s")

    def tick(left):
        resend.set_label(f"Resend in {left}s")
        if left > 1:
            GLib.timeout_add(200, tick, left - 1)
        return False
    resend.connect("clicked", lambda b: tick(4))
    delete = Gtk.Button(label="Delete")
    delete.connect("clicked", lambda b: Gtk.MessageDialog(
        transient_for=w, title="Delete", message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.OK_CANCEL, text="Delete notes.md?",
        secondary_text="It can't be undone.").show())
    for widget in (bar, check, resend, delete):
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
elif sys.argv[2:3] == ["caret"]:
    # A caret drawn on a timer of its own, as a game's is: a bar 2 by 16
    # after a word, shown and hidden every MS milliseconds.
    area, shown = Gtk.DrawingArea(), [True]

    def draw(a, cr):
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.set_source_rgb(0, 0, 0)
        cr.move_to(10, 30)
        cr.show_text("hello")
        if shown[0]:
            cr.rectangle(50, 18, 2, 16)
            cr.fill()
    area.connect("draw", draw)
    GLib.timeout_add(int(sys.argv[3]), lambda: shown.__setitem__(0, not shown[0])
                     or area.queue_draw() or True)
    w.add(area)
elif sys.argv[2:] == ["spinner"]:
    spinner = Gtk.Spinner()
    spinner.start()
    w.add(spinner)
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
elif sys.argv[2:] == ["blinking"]:
    # Dark words, shown and hidden half a second each.
    words, start = Gtk.Label(label="Blinking dark words"), time.monotonic()
    GLib.timeout_add(20, lambda: words.set_opacity(
        (time.monotonic() - start) % 1 < 0.5) or True)
    w.add(words)
elif sys.argv[2:] == ["moving"]:
    # A faint label that flashes dark for half a second every two, and
    # dark words sliding by, 80 pixels a second, round a 300 wide loop.
    css, start = Gtk.CssProvider(), time.monotonic()
    Gtk.StyleContext.add_provider_for_screen(w.get_screen(), css, 800)
    box, slide = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12), Gtk.Fixed()
    words = Gtk.Label(label="Breaking news scrolls by")
    box.add(Gtk.Label(label="Mostly faint words"))
    slide.put(words, 0, 0)
    box.add(slide)

    def tick():
        t = time.monotonic() - start
        css.load_from_data(b"window { background: #ffffff; } label { color: #000000; }"
                           b" box > label { color: %s; }" % (b"#000000" if t % 2 < 0.5
                                                               else b"#a0a0a0"))
        slide.move(words, int(250 - t * 80 % 300), 0)
        return True
    GLib.timeout_add(20, tick)
    w.add(box)
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
elif sys.argv[2:3] == ["beep"]:
    # Black, white for 100 ms every second, and a tone that long played
    # as the white is drawn: a picture and a sound at the same instant.
    import math
    import struct
    import subprocess
    area, white, played = Gtk.DrawingArea(), [False], [False]
    pacat = subprocess.Popen(["pacat", "--format=s16le", "--rate=48000", "--channels=2",
                              "--latency-msec=10"], stdin=subprocess.PIPE)
    tone = b"".join(struct.pack("<hh", s, s) for s in (
        int(8000 * math.sin(2 * math.pi * 1000 * i / 48000)) for i in range(4800)))

    def draw(a, cr):
        cr.set_source_rgb(*[1 if white[0] else 0] * 3)
        cr.paint()
        if white[0] and not played[0]:
            played[0] = True
            pacat.stdin.write(tone)
            pacat.stdin.flush()

    def turn(to):
        white[0], played[0] = to, False
        area.queue_draw()
        return False

    def flash():
        turn(True)
        GLib.timeout_add(100, turn, False)
        return True
    area.connect("draw", draw)
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
elif sys.argv[2:3] == ["pages"]:
    # A terminal jump-scrolling, as winterm's `ls -lR` does: a page of new
    # lines, long and short, in a font not smoothed, every other 40 ms, a
    # blank between.
    import random
    import cairo
    area, n, sharp = Gtk.DrawingArea(), [0], cairo.FontOptions()
    sharp.set_antialias(cairo.ANTIALIAS_NONE)

    def draw(a, cr):
        cr.set_font_options(sharp)
        cr.set_source_rgb(0, 0, 0.44)
        cr.paint()
        cr.set_source_rgb(0.9, 0.9, 0.9)
        for line in range(7 if n[0] % 2 else 0):
            r = random.Random(n[0] * 7 + line)
            cr.move_to(4, 12 + line * 14)
            cr.show_text("".join(r.choice("abcdefghijklmnopqrstuvwxyz-./ ")
                                 for _ in range(r.choice((0, 3, 8, 20, 32)))))
    area.connect("draw", draw)
    GLib.timeout_add(40, lambda: n.__setitem__(0, n[0] + 1) or area.queue_draw() or True)
    w.add(area)
elif sys.argv[2:3] in (["redraw"], ["stair"]):
    # redraw: a terminal's page of text held 200 ms, then drawn anew, a
    # blank between for 40 ms, its green caret gone with it. stair: a
    # line a pixel high on a tile's last row, above a wall's edge, part of
    # it a row lower for 30 ms once a second, as an aliased line's step in
    # a turning view.
    import random
    import cairo
    area, n, sharp = Gtk.DrawingArea(), [0], cairo.FontOptions()
    sharp.set_antialias(cairo.ANTIALIAS_NONE)

    def draw(a, cr):
        if sys.argv[2] == "stair":
            cr.set_source_rgb(0, 0.3, 0)
            cr.paint()
            cr.set_source_rgb(0.85, 0.45, 0.45)
            cr.rectangle(0, 70, 200, 3)
            cr.fill()
            cr.set_source_rgb(0.67, 0.85, 0)
            cr.rectangle(100, 63, 100, 1)
            cr.rectangle(0, 64 if n[0] % 33 == 0 else 63, 100, 1)
            cr.fill()
            return
        cr.set_font_options(sharp)
        cr.set_source_rgb(0, 0, 0.44)
        cr.paint()
        if n[0] % 6 == 5:
            return
        cr.set_source_rgb(0.9, 0.9, 0.9)
        r = random.Random(n[0] // 6)
        for line in range(7):
            cr.move_to(4, 12 + line * 14)
            cr.show_text("".join(r.choice("abcdefghijklmnopqrstuvwxyz-./ ")
                                 for _ in range(r.choice((0, 3, 8, 20, 32)))))
        cr.set_source_rgb(0, 1, 0)
        cr.rectangle(4, 102, 8, 12)
        cr.fill()
    area.connect("draw", draw)
    GLib.timeout_add(30 if sys.argv[2] == "stair" else 40,
                     lambda: n.__setitem__(0, n[0] + 1) or area.queue_draw() or True)
    w.add(area)
elif sys.argv[2:3] == ["poles"]:
    # Two yellow poles alike on a horizon, as bzflag's are while the tank
    # turns, a step of 8 a frame toward each other from either side,
    # crossing, and out the other.
    area, n = Gtk.DrawingArea(), [0]

    def draw(a, cr):
        cr.set_source_rgb(0.4, 0.6, 0.9)
        cr.paint()
        cr.set_source_rgb(0, 0.4, 0)
        cr.rectangle(0, 60, 200, 60)
        cr.fill()
        cr.set_source_rgb(0.9, 0.9, 0)
        for x in (n[0] % 30 * 8 - 16, 208 - n[0] % 30 * 8):
            cr.rectangle(x, 20, 2, 40)
            cr.fill()
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
    # beside it and the page's background after, through ten columns of
    # which something passes for a third of each second.
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
            cr.rectangle(166, t % 1 * 400 - 12, 26, 12)
            cr.fill()
        cr.set_source_rgb(*((0, 0, 0) if n[0] % 2 else (0.53, 0.79, 0)))
        cr.rectangle(178, 0, 6, 120)
        cr.fill()
    area.connect("draw", draw)
    GLib.timeout_add(30, lambda: n.__setitem__(0, n[0] + 1) or area.queue_draw() or True)
    w.add(area)
elif sys.argv[2:3] in (["icon"], ["dropout"]):
    # icon: an X of strokes two wide flashing white for 40 ms once a
    # second on gray. dropout: a line a pixel high, light on dark ground
    # under a wall, missing for 30 ms once a second, as depth-fighting
    # drops one in a turning view.
    area, n = Gtk.DrawingArea(), [0]

    def draw(a, cr):
        if sys.argv[2] == "icon":
            cr.set_source_rgb(0.3, 0.3, 0.3)
            cr.paint()
            if n[0] % 100 < 4:
                cr.set_source_rgb(1, 1, 1)
                for i in range(14):
                    cr.rectangle(65 + i, 49 + i, 2, 1)
                    cr.rectangle(78 - i, 49 + i, 2, 1)
                cr.fill()
            return
        cr.set_source_rgb(0, 0.3, 0)
        cr.paint()
        cr.set_source_rgb(0.85, 0.45, 0.45)
        cr.rectangle(0, 20, 200, 41)
        cr.fill()
        if n[0] % 100 > 2:
            cr.set_source_rgb(0.67, 0.85, 0)
            cr.rectangle(0, 62, 200, 1)
            cr.fill()
    area.connect("draw", draw)
    GLib.timeout_add(10, lambda: n.__setitem__(0, n[0] + 1) or area.queue_draw() or True)
    w.add(area)
elif sys.argv[2:3] == ["busy"]:
    # bzflag's strip, six columns black and green every other frame of
    # 30 ms, in a tile with two of a scene moving on every frame, never
    # black, ground lines at a wall's foot, and the page's background.
    area, n = Gtk.DrawingArea(), [0]

    def draw(a, cr):
        cr.set_source_rgb(0.9, 0.9, 0.9)
        cr.paint()
        for x in range(0, 178, 2):
            for y in range(0, 120, 4):
                v = ((x * 7 + y * 13 + n[0] * 29) % 97) / 97
                cr.set_source_rgb(0.1 * v, 0.4 + 0.5 * v, 0.1)
                cr.rectangle(x, y, 2, 4)
                cr.fill()
        cr.set_source_rgb(*((0, 0, 0) if n[0] % 2 else (0.53, 0.79, 0)))
        cr.rectangle(178, 0, 6, 120)
        cr.fill()
    area.connect("draw", draw)
    GLib.timeout_add(30, lambda: n.__setitem__(0, n[0] + 1) or area.queue_draw() or True)
    w.add(area)
elif sys.argv[2:3] in (["edge"], ["gray"]):
    # edge: a strip six columns wide, partway into a tile, black and green
    # every other 30 ms over plain gray, and a square in a corner turning
    # red and blue every 150 ms, so other pictures change too. gray: the
    # whole window two grays 6% apart, every other 30 ms.
    area, n, began = Gtk.DrawingArea(), [0], GLib.get_monotonic_time()

    def draw(a, cr):
        if sys.argv[2] == "gray":
            cr.set_source_rgb(*[0.5 if n[0] % 2 else 0.56] * 3)
            cr.paint()
            return
        cr.set_source_rgb(0.7, 0.7, 0.7)
        cr.paint()
        cr.set_source_rgb(*((0, 0, 0) if n[0] % 2 else (0.2, 0.6, 0.2)))
        cr.rectangle(180, 0, 6, 120)
        cr.fill()
        turn = (GLib.get_monotonic_time() - began) // 150000 % 2
        cr.set_source_rgb(*((1, 0, 0) if turn else (0, 0, 1)))
        cr.rectangle(8, 8, 10, 10)
        cr.fill()
    area.connect("draw", draw)
    GLib.timeout_add(30, lambda: n.__setitem__(0, n[0] + 1) or area.queue_draw() or True)
    GLib.timeout_add(150, lambda: area.queue_draw() or True)
    w.add(area)
elif sys.argv[2:3] == ["grating"]:
    # Black and white stripes 6 pixels wide swapping every 30 ms, a white
    # rule every 10 rows: each look is the other moved a stripe across,
    # which isn't scrolling.
    area, n = Gtk.DrawingArea(), [0]

    def draw(a, cr):
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.set_source_rgb(0, 0, 0)
        for x in range(n[0] % 2 * 6, 200, 12):
            for y in range(0, 120, 10):
                cr.rectangle(x, y + 1, 6, 9)
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
elif sys.argv[2:3] == ["skip"]:
    # Motion faster than the tiles: a red square of 12, between pixels so
    # its edges are blended, a step of 24 a frame along a row, and gone a
    # frame at the end, so that last, at the window's edge, a column of it
    # is in a tile alone, and where it was before isn't beside that.
    area, n = Gtk.DrawingArea(), [0]

    def draw(a, cr):
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        if n[0] % 9 < 8:
            cr.set_source_rgb(0.8, 0, 0)
            cr.rectangle(12.5 + 24 * (n[0] % 9), 50, 12, 12)
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
elif sys.argv[2:3] in (["move"], ["scroll"], ["smooth"], ["spaced"], ["fast"]):
    # Motion, which is neither: a black square crossing white at 300
    # pixels a second, or lines of text scrolling up at 200, or at 250.
    area, began, n = Gtk.DrawingArea(), GLib.get_monotonic_time(), [0]

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
            # Lines 14 pixels apart, scrolled by whole pixels as a terminal
            # or a GTK 3 list is, or 40, smoothly, with white between; or 15,
            # by half a line a frame, as GTK 4 scrolls by fractions of a
            # pixel: rows the same every other frame, never shifted whole.
            gap = {"scroll": 14, "smooth": 15}.get(sys.argv[2], 40)
            dy = {"scroll": int(t * 200), "smooth": n[0] * 7.5}.get(sys.argv[2], t * 200)
            for line in range(120 // gap + 2):
                cr.move_to(4, 14 + line * gap - dy % gap)
                cr.show_text(f"line {line + int(dy / gap)}: the quick brown fox")
    area.connect("draw", draw)
    GLib.timeout_add(30 if sys.argv[2] == "smooth" else 16,
                     lambda: n.__setitem__(0, n[0] + 1) or area.queue_draw() or True)
    w.add(area)
elif sys.argv[3:] == ["corner"]:
    button = Gtk.Button(label="Corner", halign=Gtk.Align.START, valign=Gtk.Align.START)
    button.connect("clicked", lambda b: open(sys.argv[2], "w").write("clicked"))
    w.add(button)
elif sys.argv[3:] == ["list"]:
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    for i in range(100):
        rows.add(Gtk.Label(label=f"Row {i} " + "and on " * 30, xalign=0))
    pane = Gtk.ScrolledWindow()
    pane.add(rows)
    down, across = pane.get_vadjustment(), pane.get_hadjustment()
    for adjustment in (down, across):
        adjustment.connect("value-changed", lambda a: open(sys.argv[2], "w").write(
            f"{down.get_value():.0f} {across.get_value():.0f}\n"))
    w.add(pane)
elif len(sys.argv) > 2:
    entry = Gtk.Entry()
    entry.connect("changed", lambda e: open(sys.argv[2], "w").write(e.get_text()))
    w.add(entry)
else:
    w.add(Gtk.Label(label="a window"))
w.connect("destroy", Gtk.main_quit)
w.show_all()
Gtk.main()
