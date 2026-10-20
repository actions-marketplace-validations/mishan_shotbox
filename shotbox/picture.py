"""Measuring what's on the screen: contrast (WCAG 2's), and how much of a
place changed between two pictures. On the pixels themselves, as RGB
bytes, so it needs nothing beyond what takes the pictures.
"""

import sys
import time
from array import array
from collections import Counter
from itertools import repeat
from operator import mul, sub, truediv


class Picture:
    """A picture of the whole screen: width, height and RGB bytes, as
    x.pixels and wl.pixels give them."""

    def __init__(self, width, height, data):
        self.width, self.height, self.data = width, height, data

    def clip(self, box, pad=0):
        """`box` (x, y, width, height), grown by `pad` on each side, as
        (x0, y0, x1, y1) inside the picture; None if nothing's left."""
        x, y, w, h = box
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(self.width, x + w + pad), min(self.height, y + h + pad)
        return (x0, y0, x1, y1) if x0 < x1 and y0 < y1 else None

    def colors(self, box, pad=0, inside=None):
        """How many pixels of each color are in `box` (grown by `pad`), or
        only those of a band that far `inside` its edge."""
        c = Counter()
        area = self.clip(box, pad)
        if not area:
            return c
        x0, y0, x1, y1 = area
        d = self.data
        for y in range(y0, y1):
            row = (y * self.width) * 3
            if inside is not None and inside <= y - y0 < y1 - y0 - inside:
                spans = ((x0, min(x1, x0 + inside)), (max(x0, x1 - inside), x1))
            else:
                spans = ((x0, x1),)
            for a, b in spans:
                for i in range(row + a * 3, row + b * 3, 3):
                    c[d[i:i + 3]] += 1
        return c


class Changes:
    """Which pixels in `area`, a clipped area, differ between two pictures of
    the same screen, and which of those are also in `skip`, another, but
    for its `hole`: as summed-area tables, so the count in any box in
    `area` is four lookups."""

    def __init__(self, before, after, area, skip=None, hole=None):
        self.x0, self.y0, x1, y1 = area
        w = x1 - self.x0
        self.all, self.skipped = [[0] * (w + 1)], [[0] * (w + 1)]
        for y in range(self.y0, y1):
            a = (y * before.width + self.x0) * 3
            if before.data[a:a + w * 3] == after.data[a:a + w * 3]:
                # Nothing changed on this row: the sums above it, again.
                self.all.append(self.all[-1])
                self.skipped.append(self.skipped[-1])
                continue
            rows = self.all[-1], self.skipped[-1]
            row_all, row_skip, run_all, run_skip = [0], [0], 0, 0
            for i in range(w):
                if before.data[a + i * 3:a + i * 3 + 3] != after.data[a + i * 3:a + i * 3 + 3]:
                    run_all += 1
                    x = self.x0 + i
                    run_skip += bool(
                        skip and skip[0] <= x < skip[2] and skip[1] <= y < skip[3]
                        and not (hole and hole[0] <= x < hole[2] and hole[1] <= y < hole[3]))
                row_all.append(rows[0][i + 1] + run_all)
                row_skip.append(rows[1][i + 1] + run_skip)
            self.all.append(row_all)
            self.skipped.append(row_skip)

    def count(self, area, table=None):
        """How many pixels in `area` (x0, y0, x1, y1), cut to this one's,
        changed: of all, or of `table`."""
        t = table or self.all
        x0, y0 = max(area[0] - self.x0, 0), max(area[1] - self.y0, 0)
        x1 = min(area[2] - self.x0, len(t[0]) - 1)
        y1 = min(area[3] - self.y0, len(t) - 1)
        if x0 >= x1 or y0 >= y1:
            return 0
        return t[y1][x1] - t[y0][x1] - t[y1][x0] + t[y0][x0]


def luminance(rgb):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def hex_of(rgb):
    return "#" + bytes(rgb).hex()


def strongest(colors, against, least=1):
    """The color in `colors` (a Counter) with the most contrast against
    `against`, of those with at least `least` pixels: (ratio, color), or
    None if there are none but `against`."""
    best = None
    for color, n in colors.items():
        if n >= least and color != against:
            r = ratio(color, against)
            if best is None or r > best[0]:
                best = (r, color)
    return best


def between(color, a, b):
    """Whether `color` is a mix of `a` and `b`, within a few levels a
    channel: what antialiasing or opacity makes of `b` drawn over `a`."""
    way = [y - x for x, y in zip(a, b)]
    t = sum((c - x) * w for c, x, w in zip(color, a, way)) / (sum(w * w for w in way) or 1)
    return 0 <= t <= 1 and all(abs(c - (x + t * w)) <= 12 for c, x, w in zip(color, a, way))


def text_contrast(pic, box, drawn=None):
    """Text (or an icon) on its background, in `box`: the background is the
    color most of it is, the text the third pixel in order of contrast
    against that, which an antialiased glyph's core reaches, though a
    thin stroke seldom draws three of one color. (ratio, text,
    background, how many pixels aren't the background, whether the text
    is its coverage's color), or None for a box of one color. With
    `drawn`, where the text was found and as what (x, y, find_text's
    shape), the pixels are those the shape covers, not a grid or texture
    drawn behind it, and a shape that covers fewer than three pixels
    whole, as a stem thinner than a pixel or a small "." does, is the
    color its coverage steps toward (covered), where that's further than
    the third pixel on the same line."""
    colors = pic.colors(box)
    if not colors:
        return None
    back = colors.most_common(1)[0][0]
    ink = sum(map(bool, drawn[2][2])) if drawn else sum(colors.values()) - colors[back]
    seen = 0
    for r, color in sorted(((ratio(c, back), c) for c in colors if c != back), reverse=True):
        seen += colors[color]
        if seen >= 3:
            full = drawn and drawn[2][2].count(255) < 3 and covered(pic, drawn, back)
            if full and ratio(full, back) > r and between(color, back, full):
                return ratio(full, back), full, back, ink, True
            return r, color, back, ink, False
    return None


def covered(pic, drawn, back):
    """The color a pixel covered whole by `drawn` (x, y, find_text's shape)
    would be, on `back`: each channel's step from `back` over the shape's
    coverage, by least squares. None if it covers nothing, or any pixel
    of the shape's box, blank or not, is more than a level and a half off
    what that color at its coverage would draw."""
    x0, y0, (w, _, cover) = drawn
    steps, total = [0, 0, 0], 0
    for i, c in enumerate(cover):
        x, y = x0 + i % w, y0 + i // w
        if c and 0 <= x < pic.width and 0 <= y < pic.height:
            at = (y * pic.width + x) * 3
            for k in range(3):
                steps[k] += c * (pic.data[at + k] - back[k])
            total += c * c
    if not total:
        return None
    full = bytes(max(0, min(255, round(b + 255 * s / total))) for b, s in zip(back, steps))
    # Only a shape drawn exactly as matched: one a fraction of a pixel
    # larger, or a shadow under it, steps further than its color.
    for i, c in enumerate(cover):
        x, y = x0 + i % w, y0 + i // w
        at = (y * pic.width + x) * 3
        if 0 <= x < pic.width and 0 <= y < pic.height and any(
                abs(pic.data[at + k] - b - c * (f - b) / 255) > 1.5
                for k, (b, f) in enumerate(zip(back, full))):
            return None
    return full


def edge_contrast(pic, box):
    """A control's edge against what's around it: the color most of a band
    2 pixels outside is, against the color, of those making up a fifth or
    more of a band 3 pixels inside, with the most contrast against it.
    (ratio, edge, around), or None if there's nothing around it."""
    around = pic.colors(box, pad=2, inside=2)
    if not around:
        return None
    out = around.most_common(1)[0][0]
    band = pic.colors(box, inside=3)
    got = strongest(band, out, least=max(1, sum(band.values()) // 5))
    return (got[0], got[1], out) if got else (1.0, out, out)


class Lightness:
    """The lightness of each pixel of the picture from x0, y0 up to x1, y1,
    cut to it, less what's behind it if `under` (backdrop's) says (`lum`,
    row by row, `width` wide, from `x0`, `y0`), and it and its square
    summed from the corner (`sums` and `squares`, a row and a column
    more), so any box's are four look-ups."""

    def __init__(self, pic, x0, y0, x1, y1, under=None):
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(pic.width, x1), min(pic.height, y1)
        d, lum = pic.data, []
        for y in range(y0, y1):
            i = (y * pic.width + x0) * 3
            row = [d[j] * 2 + d[j + 1] * 5 + d[j + 2] for j in range(i, i + (x1 - x0) * 3, 3)]
            # Less what's behind, kept above zero: matches multiplies unsigned.
            lum += [v - under(x, y) + 8 * 255 for x, v in zip(range(x0, x1), row)] if under else row
        self.lum, self.x0, self.y0 = lum, x0, y0
        self.width = width = max(0, x1 - x0)
        self.height = height = len(lum) // width if width else 0
        stride = width + 1
        self.sums, self.squares = [0] * (stride * (height + 1)), [0] * (stride * (height + 1))
        for y in range(height):
            run = run2 = 0
            for x in range(width):
                v = lum[y * width + x]
                run += v
                run2 += v * v
                self.sums[(y + 1) * stride + x + 1] = self.sums[y * stride + x + 1] + run
                self.squares[(y + 1) * stride + x + 1] = self.squares[y * stride + x + 1] + run2


def matches(lit, shape, x0, y0, nx, ny):
    """How well the lightness in `lit` follows `shape`'s coverage (width,
    height, a byte a pixel) at each of nx by ny places from x0, y0 in it:
    their correlation, either way round, squared, a list a row. Its
    coverage times the lightness, summed at every place, is the digits of
    one product of two whole numbers, a pixel to each 4 or 8 bytes, which
    never carry: exact, and multiplied in C."""
    w, h, cover = shape
    width, n, sa = lit.width, w * h, sum(cover)
    va = n * sum(a * a for a in cover) - sa * sa
    if va <= 0:
        return [[0.0] * nx for _ in range(ny)]
    code = "I" if sa * max(lit.lum) < 1 << 32 else "Q"
    flipped = array(code, bytes(array(code).itemsize * ((h - 1) * width + w)))
    for i, a in enumerate(cover):
        flipped[(h - 1 - i // w) * width + w - 1 - i % w] = a
    product = (int.from_bytes(array(code, lit.lum).tobytes(), sys.byteorder)
               * int.from_bytes(flipped.tobytes(), sys.byteorder))
    sal = memoryview(product.to_bytes((len(lit.lum) + len(flipped)) * flipped.itemsize,
                                      sys.byteorder)).cast(code)
    s, q, stride, out = lit.sums, lit.squares, width + 1, []
    for y in range(y0, y0 + ny):
        a, c, at = y * stride + x0, (y + h) * stride + x0, y * width + x0 + len(flipped) - 1
        sl = list(map(sub, map(sub, s[c + w:c + w + nx], s[a + w:a + w + nx]),
                      map(sub, s[c:c + nx], s[a:a + nx])))
        sll = map(sub, map(sub, q[c + w:c + w + nx], q[a + w:a + w + nx]),
                  map(sub, q[c:c + nx], q[a:a + nx]))
        cov = list(map(sub, map(n.__mul__, sal[at:at + nx]), map(sa.__mul__, sl)))
        # A place all one lightness has no variance, and matches nothing.
        vl = map(max, map(sub, map(n.__mul__, sll), map(mul, sl, sl)), repeat(1))
        out.append(list(map(truediv, map(mul, cov, cov), map(va.__mul__, vl))))
    return out


def find_text(pic, kinds, area, until=None, under=None):
    """Where text is drawn in `area` (x0, y0, x1, y1), as one of `kinds`
    of shapes may draw it, each shape (width, height, coverage), a byte a
    pixel, with blank columns at each side, and a kind's alike but for
    their size. A place matches as well as its pixels' lightness follows
    a shape's coverage (matches), each shape looked at at every place:
    (match, x, y, width, height, the best match of that shape at a place
    apart from that one, which kind and shape of it matched, and where
    that other match is, (x, y)), its x and y those of the shape, or None if `area` can't hold one, or has
    more places than PLACES to look at. Raises TimeoutError past
    `until`, a monotonic time. With `under` (backdrop's), less what's
    behind the text."""
    x0, y0, x1, y1 = area
    # The shapes' blank sides can lie outside `area`, not past the picture.
    if (x1 - x0 + 4) * (y1 - y0) > PLACES:
        return None
    lit = Lightness(pic, x0 - 2, y0, x1 + 2, y1, under)
    best, grid = None, None
    for k, kind in enumerate(kinds):
        for i, (w, h, cover) in enumerate(kind):
            if until and time.monotonic() > until:
                raise TimeoutError
            if w <= lit.width and h <= lit.height:
                got = matches(lit, (w, h, cover), 0, 0, lit.width - w + 1, lit.height - h + 1)
                top = max((max(row), row.index(max(row)), y) for y, row in enumerate(got))
                if not best or top + (w, h, k, i) > best:
                    best, grid = top + (w, h, k, i), got
    if not best:
        return None
    # The best match of a place the best one doesn't overlap.
    match, bx, by, bw, bh, k, i = best
    apart = (0.0, 0, 0)
    for y, row in enumerate(grid):
        spans = ((0, max(0, bx - bw + 1)), (bx + bw, len(row))) if by - bh < y < by + bh else (
            (0, len(row)),)
        for a, b in spans:
            if a < b:
                top = max(row[a:b])
                if top > apart[0]:
                    apart = (top, row.index(top, a, b), y)
    return (match ** 0.5, bx + lit.x0, by + lit.y0, bw, bh, apart[0] ** 0.5, (k, i),
            (apart[1] + lit.x0, apart[2] + lit.y0))


def match_over(pic, kinds, box, until=None, under=None):
    """How well one of `kinds` of shapes (find_text's) matches what's drawn
    over `box` (x, y, width, height): placed so the text, the shape but
    for its blank sides, is inside the box or holds it; with `under`, as
    find_text. Raises TimeoutError past `until`, a monotonic time."""
    bx, by, bw, bh = box
    shapes = [shape for kind in kinds for shape in kind]
    # Where each shape's corner can be: x0, y0, x1, y1, inclusive.
    spans = [(min(bx, bx + bw - w + 4) - 2, min(by, by + bh - h),
              max(bx, bx + bw - w + 4) - 2, max(by, by + bh - h)) for w, h, _ in shapes]
    lit = Lightness(pic, min(s[0] for s in spans), min(s[1] for s in spans),
                    max(s[2] + w for s, (w, _, _) in zip(spans, shapes)),
                    max(s[3] + h for s, (_, h, _) in zip(spans, shapes)), under)
    got = 0.0
    for (sx0, sy0, sx1, sy1), shape in zip(spans, shapes):
        if until and time.monotonic() > until:
            raise TimeoutError
        # Cut to the picture.
        x0, y0 = max(sx0 - lit.x0, 0), max(sy0 - lit.y0, 0)
        nx = min(sx1 - lit.x0, lit.width - shape[0]) - x0 + 1
        ny = min(sy1 - lit.y0, lit.height - shape[1]) - y0 + 1
        if nx > 0 and ny > 0:
            got = max([got] + [max(row) for row in matches(lit, shape, x0, y0, nx, ny)])
    return got ** 0.5


def backdrop(pic, box):
    """What's behind text found in `box` (x0, y0, x1, y1), the same with it
    and without it, as a function of x and y giving its lightness
    (Lightness'): in the box, a line through it, as a grid draws, the
    color its column is for 2 of the 3 pixels both above and below the box
    (another line can cross it there), or failing that, its row both left
    and right, where that isn't the color most of what's round the box
    is; elsewhere, that color."""
    x0, y0, x1, y1 = box
    rect = (x0, y0, x1 - x0, y1 - y0)
    most = (pic.colors(rect, y1 - y0) - pic.colors(rect)).most_common(1)[0][0]

    def line(one, other):
        sides = [c.most_common(1)[0] for c in (one, other) if c]
        colors = {c for c, _ in sides}
        return colors.pop() if len(colors) == 1 and all(n >= 2 for _, n in sides) else None
    cols = [line(pic.colors((x, y0 - 3, 1, 3)), pic.colors((x, y1, 1, 3))) for x in range(x0, x1)]
    rows = [line(pic.colors((x0 - 3, y, 3, 1)), pic.colors((x1, y, 3, 1))) for y in range(y0, y1)]
    lit = {c: c[0] * 2 + c[1] * 5 + c[2] for c in cols + rows + [most] if c}

    def under(x, y):
        if not (x0 <= x < x1 and y0 <= y < y1):
            return lit[most]
        c, r = cols[x - x0], rows[y - y0]
        return lit[c if c not in (None, most) else r if r not in (None, most) else most]
    return under


def most_contrast(pic, box, stretch=1):
    """The most contrast text drawn in `box` (x, y, width, height) can
    measure (text_contrast): that of the lightest each channel is there on
    the darkest, or the darkest on the lightest, either taken `stretch`
    times as far from the other, and a level and a half more, as
    covered() can take a color from coverage."""
    colors = pic.colors(box)
    hi, lo = ([f(c[k] for c in colors) for k in range(3)] for f in (max, min))
    lighter = [min(255, x + (y - x + 1.5) * stretch) for x, y in zip(lo, hi)]
    darker = [max(0, y - (y - x + 1.5) * stretch) for x, y in zip(lo, hi)]
    return max(ratio(lighter, lo), ratio(hi, darker))


# The most places find_text looks at: as many as there are in 600 by 400
# pixels, a sidebar's height.
PLACES = 240000
