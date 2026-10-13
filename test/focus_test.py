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


def ring(x, y, w, h):
    """A 2px ring just inside (x, y, w, h)."""
    return [(x, y, x + w, y + 2), (x, y + h - 2, x + w, y + h),
            (x, y, x + 2, y + h), (x + w - 2, y, x + w, y + h)]


def off(box, dx, dy):
    return (box[0] + dx, box[1] + dy, box[2], box[3])


fails = 0


def check(ok, what):
    global fails
    print(("ok    " if ok else "FAIL  ") + what)
    fails += not ok


x, y, w, h = BOX
container = (x - 20, y - 20, w + 40, h + 80)
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
]
for what, before, after, box, was, shown in CASES:
    n = focus_change(before, after, box, was)
    check((n >= shown_needs(box)) == shown,
          f"{what}: {'shown' if shown else 'not shown'} ({n} changed, {shown_needs(box)} needed)")
sys.exit(1 if fails else 0)
