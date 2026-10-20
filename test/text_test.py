#!/usr/bin/env python3
"""a11y contrast's search for GTK 4's text, on made-up pictures, so it
needs no display, but pycairo and PangoCairo: test/text_test.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shotbox import picture  # noqa: E402
from shotbox.a11y import MATCH, text_shapes  # noqa: E402
from shotbox.session import pango_cairo  # noqa: E402

if pango_cairo():
    print(f"skip  text_test.py: {pango_cairo()}")
    sys.exit(0)

W, H = 160, 60
FILL, LINE, INK = b"\x18\x14\x22", b"\x26\x20\x36", b"\x3a\x34\x46"
KINDS = text_shapes("Cancel", {"family-name": "Sans", "size": "13", "weight": "400"})
TW, TH, COVER = KINDS[2][1]
X, Y = 20, 16
fails = 0


def check(ok, what):
    global fails
    print(("ok    " if ok else "FAIL  ") + what)
    fails += not ok


def drawn(crossing):
    """Faint text on a button's fill a grid shows through, its lines every
    24 pixels and one along the row under the text's box, where they
    cross in `crossing`."""
    data = bytearray(FILL * W * H)
    for y in range(H):
        for x in range(W):
            if x % 24 == 6 or y == Y + TH:
                data[(y * W + x) * 3:(y * W + x) * 3 + 3] = (
                    crossing if x % 24 == 6 and y == Y + TH else LINE)
    for i, c in enumerate(COVER):
        at = ((Y + i // TW) * W + X + i % TW) * 3
        data[at:at + 3] = bytes(round(b + (t - b) * c / 255) for b, t in zip(data[at:at + 3], INK))
    return picture.Picture(W, H, bytes(data))


# A case a line: what's drawn, where lines cross.
for what, crossing in (("lines crossing in their own color", LINE),
                       ("lines crossing lighter, as two see-through ones do", b"\x32\x2a\x47")):
    pic = drawn(crossing)
    got = picture.find_text(pic, KINDS, (2, 0, W - 2, H))
    x, y, w, h = got[1:5]
    under = picture.backdrop(pic, (x, y, x + w, y + h))
    again = picture.find_text(pic, KINDS, (x - 1, y - 3, x + w + 1, y + h + 3), under=under)
    check(got[0] < MATCH <= again[0] and again[1:3] == (X, Y),
          f"faint text over a grid, {what}: found less the grid ({got[0]:.3f}, then "
          f"{again[0]:.3f} at {again[1:3]})")
sys.exit(1 if fails else 0)
