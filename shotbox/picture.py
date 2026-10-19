"""Measuring what's on the screen: contrast (WCAG 2's), and how much of a
place changed between two pictures. On the pixels themselves, as RGB
bytes, so it needs nothing beyond what takes the pictures.
"""

import time
from collections import Counter


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


def text_contrast(pic, box):
    """Text (or an icon) on its background, in `box`: the background is the
    color most of it is, the text the third pixel in order of contrast
    against that, which an antialiased glyph's core reaches, though a
    thin stroke seldom draws three of one color. (ratio, text,
    background, how many pixels aren't the background), or None for a
    box of one color."""
    colors = pic.colors(box)
    if not colors:
        return None
    back = colors.most_common(1)[0][0]
    ink = sum(colors.values()) - colors[back]
    seen = 0
    for r, color in sorted(((ratio(c, back), c) for c in colors if c != back), reverse=True):
        seen += colors[color]
        if seen >= 3:
            return r, color, back, ink
    return None


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


def lightness(pic, x0, y0, x1, y1):
    """The lightness of each pixel of the picture from x0, y0 up to x1, y1,
    cut to it: (a list, row by row, its width, x0, y0)."""
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(pic.width, x1), min(pic.height, y1)
    d, lum = pic.data, []
    for y in range(y0, y1):
        i = (y * pic.width + x0) * 3
        lum += [d[j] * 2 + d[j + 1] * 5 + d[j + 2] for j in range(i, i + (x1 - x0) * 3, 3)]
    return lum, max(0, x1 - x0), x0, y0


def score(lum, points, at):
    """How well the pixels from `at` in `lum` follow a shape's coverage,
    `points` (offset, coverage): their correlation, either way round."""
    n = len(points)
    sa = sl = saa = sll = sal = 0
    for off, a in points:
        v = lum[at + off]
        sa += a
        sl += v
        saa += a * a
        sll += v * v
        sal += a * v
    va, vl = saa - sa * sa / n, sll - sl * sl / n
    return abs(sal - sa * sl / n) / (va * vl) ** 0.5 if va > 0 and vl > 0 else 0.0


def points_of(shape, width):
    """A shape's pixels, as (offset in a picture `width` wide, coverage)."""
    w, h, cover = shape
    return [(i // w * width + i % w, a) for i, a in enumerate(cover)]


def find_text(pic, kinds, area, until=None):
    """Where text is drawn in `area` (x0, y0, x1, y1), as one of `kinds`
    of shapes may draw it, each shape (width, height, coverage), a byte a
    pixel, with blank columns at each side, and a kind's alike but for
    their size. A place matches as well as its pixels' lightness follows
    a shape's coverage (score): (match, x, y, width, height, the best
    match of a place apart from that one, and which kind and shape of it
    matched), its x and y those of the shape, or None if `area` can't
    hold one, or has more places than PLACES to look at. Raises
    TimeoutError past `until`, a monotonic time."""
    x0, y0, x1, y1 = area
    # The shapes' blank sides can lie outside `area`, not past the picture.
    if (x1 - x0 + 4) * (y1 - y0) > PLACES:
        return None
    lum, width, gx0, gy0 = lightness(pic, x0 - 2, y0, x1 + 2, y1)
    height = len(lum) // width if width else 0

    # Summed lightness and its square, so a place all one lightness, which
    # matches nothing, is passed over without a look: most of an area.
    stride = width + 1
    sums, squares = [0] * (stride * (height + 1)), [0] * (stride * (height + 1))
    for y in range(height):
        run = run2 = 0
        for x in range(width):
            v = lum[y * width + x]
            run += v
            run2 += v * v
            sums[(y + 1) * stride + x + 1] = sums[y * stride + x + 1] + run
            squares[(y + 1) * stride + x + 1] = squares[y * stride + x + 1] + run2

    def flat(x, y, w, h):
        a, c = y * stride + x, (y + h) * stride + x
        total = sums[c + w] - sums[a + w] - sums[c] + sums[a]
        return (squares[c + w] - squares[a + w] - squares[c] + squares[a]) * w * h == total * total

    # A first look with a few pixels of each kind's middle shape, then
    # each of its shapes, all of their pixels, at the places that look best.
    best, apart = None, None
    for k, kind in enumerate(kinds):
        shapes = [(w, h, points_of((w, h, cover), width)) for w, h, cover in kind]
        sw, sh, points = shapes[len(shapes) // 2]
        inked = [p for p in points if p[1]]
        few = inked[::max(1, len(inked) // 30)] + points[::max(1, len(points) // 30)]
        places = []
        for y in range(height - sh + 1):
            if until and time.monotonic() > until:
                raise TimeoutError
            places += [(score(lum, few, y * width + x), x, y) for x in range(width - sw + 1)
                       if not flat(x, y, sw, sh)]
        places.sort(reverse=True)
        top = max(((score(lum, p, y * width + x), x, y, w, h, k, i) for _, x, y in places[:40]
                   for i, (w, h, p) in enumerate(shapes) if x + w <= width and y + h <= height),
                  default=None)
        if top and (not best or top > best):
            best, apart = top, (shapes[top[6]][2], places)
    if not best:
        return None
    # The best match of a place the best one doesn't overlap.
    _, bx, by, bw, bh, k, i = best
    points, places = apart
    others = [p for p in places if (p[1] + bw <= bx or p[1] >= bx + bw or p[2] + bh <= by
                                    or p[2] >= by + bh) and p[1] + bw <= width
              and p[2] + bh <= height]
    return (best[0], bx + gx0, by + gy0, bw, bh,
            max((score(lum, points, y * width + x) for _, x, y in others[:20]), default=0.0),
            (k, i))


def match_over(pic, kinds, box):
    """How well one of `kinds` of shapes (find_text's) matches what's drawn
    over `box` (x, y, width, height): placed so the text, the shape but
    for its blank sides, is inside the box or holds it."""
    bx, by, bw, bh = box
    got = 0.0
    for kind in kinds:
        for w, h, cover in kind:
            tw = w - 4
            xs = range(min(bx, bx + bw - tw), max(bx, bx + bw - tw) + 1)
            ys = range(min(by, by + bh - h), max(by, by + bh - h) + 1)
            lum, width, gx0, gy0 = lightness(pic, xs[0] - 2, ys[0], xs[-1] + tw + 2, ys[-1] + h)
            if width < w or len(lum) < width * h:
                continue
            points = points_of((w, h, cover), width)
            height = len(lum) // width
            got = max([got] + [score(lum, points, (y - gy0) * width + x - 2 - gx0)
                               for y in ys for x in xs
                               if gx0 <= x - 2 and x - 2 - gx0 + w <= width
                               and gy0 <= y and y - gy0 + h <= height])
    return got


# The most places find_text looks at: as many as there are in 600 by 200
# pixels.
PLACES = 120000
