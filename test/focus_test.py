#!/usr/bin/env python3
"""tabwalk --visible's count of what the focus changed, on made-up
pictures, so it needs no display: test/focus_test.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shotbox.a11y import focus_change, shown_needs  # noqa: E402
from shotbox.picture import Picture  # noqa: E402

W, H = 400, 240
BOX = (100, 50, 200, 34)   # where the node says it is
SMALL = (100, 50, 24, 24)  # a tab's close button


def picture(*rects):
    """The page, with each (x0, y0, x1, y1) in the ring's color."""
    data = bytearray(b"\x10\x10\x10" * W * H)
    for x0, y0, x1, y1 in rects:
        for y in range(y0, y1):
            data[(y * W + x0) * 3:(y * W + x1) * 3] = b"\xe0\x1b\x24" * (x1 - x0)
    return Picture(W, H, bytes(data))


def ring(x, y, w, h, t=2):
    """A ring `t` pixels wide just inside (x, y, w, h)."""
    return [(x, y, x + w, y + t), (x, y + h - t, x + w, y + h),
            (x, y, x + t, y + h), (x + w - t, y, x + w, y + h)]


def moved(was, now, t=1):
    """The focus from one node to the next: one ring goes, the other comes."""
    return picture(*ring(*was, t=t)), picture(*ring(*now, t=t))


def off(box, dx, dy):
    return (box[0] + dx, box[1] + dy, box[2], box[3])


fails = 0


def check(ok, what):
    global fails
    print(("ok    " if ok else "FAIL  ") + what)
    fails += not ok


x, y, w, h = BOX
container = (x - 20, y - 20, w + 40, h + 80)
CHECK = (100, 50, 16, 16)
RADIO, RADIO2 = (100, 50, 80, 16), (100, 70, 80, 16)
ICON, ICON2 = (100, 50, 16, 16), (118, 50, 16, 16)
LINKED, LINKED2 = (100, 50, 24, 24), (124, 50, 24, 24)
TAB, CLOSE = (100, 50, 200, 34), (260, 55, 24, 24)
LIST, ROW = (100, 50, 200, 120), (100, 50, 200, 24)
# (what, before, after, the node's bounds, where the focus was, shown?)
CASES = [
    # GTK 4 places a node short of its ancestors' padding: drawn right of
    # and below its bounds. A current tab's rail is already the ring's color
    # along its top, so only the sides and foot change.
    ("a ring drawn 6px below the bounds, its top already that color",
     picture(ring(*off(BOX, 0, 6))[0]), picture(*ring(*off(BOX, 0, 6))), BOX, None, True),
    ("a close button's ring drawn 10px right and down",
     picture(), picture(*ring(*off(SMALL, 10, 10))), SMALL, None, True),
    ("a ring drawn 16px right and down", picture(), picture(*ring(*off(BOX, 16, 16))),
     BOX, None, True),
    ("a ring drawn 12px below, its top already that color, deeper in padding",
     picture(ring(*off(BOX, 0, 12))[0]), picture(*ring(*off(BOX, 0, 12))), BOX, None, True),
    ("a ring 60px below, past where the node could be, is someone else's",
     picture(), picture(*ring(*off(BOX, 0, 60))), BOX, None, False),
    ("a background that changes, and no ring", picture(), picture((x, y, x + w, y + h)),
     BOX, None, True),
    ("a caret alone", picture(), picture((x + 10, y + 8, x + 11, y + 26)), BOX, None, False),
    ("nothing changed", picture(), picture(), BOX, None, False),
    ("the ring the focus left, just below", picture(*ring(*off(BOX, 0, h + 4))), picture(),
     BOX, off(BOX, 0, h + 4), False),
    ("the ring the focus left, drawn 7px off its own bounds",
     picture(*ring(*off(BOX, 7, h + 11))), picture(), BOX, off(BOX, 0, h + 4), False),
    ("a node inside the one the focus left, its ring",
     picture(*ring(*container)), picture(*ring(*container), *ring(*BOX)), BOX, container, True),
    # The next sibling, a few pixels on, with a thin ring: the one the
    # focus left is right beside it.
    ("radio buttons 4px apart, 1px rings", *moved(RADIO, RADIO2), RADIO2, RADIO, True),
    ("toolbar icons 2px apart, 1px rings", *moved(ICON, ICON2), ICON2, ICON, True),
    ("linked buttons, 1px rings", *moved(LINKED, LINKED2), LINKED2, LINKED, True),
    ("a list's row flush with its edge, its background, after the list's ring",
     picture(*ring(*LIST)), picture((ROW[0], ROW[1], ROW[0] + ROW[2], ROW[1] + ROW[3])),
     ROW, LIST, True),
    ("a tab's close button, both drawn off their bounds",
     *moved(off(TAB, 0, 6), off(CLOSE, 10, 10), t=2), CLOSE, TAB, True),
    # GTK 4 up to 4.14.5 draws a node left of and above its bounds.
    ("a ring drawn 12px left and up", picture(), picture(*ring(*off(LINKED, -12, -12))),
     LINKED, None, True),
    # A neighbor's change isn't this node's focus.
    ("a row 6px below selected, beside a check box with no ring",
     picture(), picture((90, 72, 300, 92)), CHECK, None, False),
    ("a label 4px right recoloring, beside a check box with no ring",
     picture(), picture((120, 50, 200, 66)), CHECK, None, False),
]
for what, before, after, box, was, shown in CASES:
    n = focus_change(before, after, box, was)
    check((n >= shown_needs(box)) == shown,
          f"{what}: {'shown' if shown else 'not shown'} ({n} changed, {shown_needs(box)} needed)")
sys.exit(1 if fails else 0)
