#!/usr/bin/env python3
"""tabwalk --visible's count of what the focus changed, on made-up
pictures, so it needs no display: test/focus_test.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shotbox.a11y import focus_change, shown_needs  # noqa: E402
from shotbox.picture import Picture  # noqa: E402

W, H = 400, 200
BOX = (100, 50, 200, 34)   # where the node says it is


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


fails = 0


def check(ok, what):
    global fails
    print(("ok    " if ok else "FAIL  ") + what)
    fails += not ok


x, y, w, h = BOX
# Drawn 6px below its bounds, its top edge already the ring's color, as a
# current tab's rail is: only the ring's sides and foot change.
drawn = (x, y + 6, w, h)
before = picture(ring(*drawn)[0])
after = picture(*ring(*drawn))
check(focus_change(before, after, BOX, None) >= shown_needs(BOX),
      "a ring drawn a few pixels off the node's bounds counts as shown")
check(focus_change(before, before, BOX, None) == 0,
      "nothing changed counts as not shown")
# The ring the focus left, next to this node, isn't counted for it.
left = (x, y + h + 4, w, h)
check(focus_change(picture(*ring(*left)), picture(), BOX, left) == 0,
      "the ring the focus left isn't counted for the node it came to")
sys.exit(1 if fails else 0)
