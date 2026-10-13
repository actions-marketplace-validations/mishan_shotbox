"""Measuring what's on the screen: contrast (WCAG 2's), and how much of a
place changed between two pictures. On the pixels themselves, as RGB
bytes, so it needs nothing beyond what takes the pictures.
"""

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

    def changed(self, other, box, pad=0, skip=None, keep=None):
        """How many pixels in `box` (grown by `pad`) differ in `other`, the
        same screen taken again; but for those in `skip`, a clipped area,
        that aren't in `keep`, another."""
        area = self.clip(box, pad)
        if not area:
            return 0
        x0, y0, x1, y1 = area
        n = 0
        for y in range(y0, y1):
            a, b = (y * self.width + x0) * 3, (y * self.width + x1) * 3
            if self.data[a:b] == other.data[a:b]:
                continue
            for x in range(x0, x1):
                if (skip and skip[0] <= x < skip[2] and skip[1] <= y < skip[3]
                        and not (keep and keep[0] <= x < keep[2] and keep[1] <= y < keep[3])):
                    continue
                i = (y * self.width + x) * 3
                n += self.data[i:i + 3] != other.data[i:i + 3]
        return n


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


def text_contrast(pic, box):
    """Text (or an icon) on its background, in `box`: the background is the
    color most of it is, the text the color with the most contrast against
    that, of at least 3 pixels, which an antialiased glyph's core is.
    (ratio, text, background), or None for a box of one color."""
    colors = pic.colors(box)
    if not colors:
        return None
    back = colors.most_common(1)[0][0]
    got = strongest(colors, back, least=3)
    return got and (got[0], got[1], back)


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
