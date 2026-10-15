"""Flicker in a run of pictures: a part of the screen that keeps switching
between two looks, faster than anything meant to (a buffer drawn where it
shouldn't be, a strip of the wrong frame), or that flashes another look
once and comes back.

The screen is cut into tiles, and a tile's look is its mean color, so a
look that's the same in color but not pixel for pixel (a game's frame
behind a strip of it) is the same look. Each tile's looks over time make
states, a look and how long it held: in time, not pictures, since the
program draws at its own rate. A tile whose states go A, B, A, B... each
held no longer than `brief`, `times` changes or more, flickers; one that
goes A, B, A once, B as brief, flashed. A caret that blinks holds each
look half a second, and an animation doesn't come back to a look so
quickly, so neither is either.

Mean colors find what might be; the tile's pixels decide. Flicker's looks
come back as the same pictures, near enough, A as A and B as B, which a
line of text scrolling past, or anything moving through, doesn't; a flash
is to a color, all of the tile, as a frame drawn black, and back to the
same picture as before.
"""

from .x import differ


def means(pixels, width, height, tile):
    """Each tile's mean color, (r, g, b), a row of tiles at a time, of RGB
    `pixels`: from every other row of it, which is plenty for a mean."""
    across, down = -(-width // tile), -(-height // tile)
    sums = [[0, 0, 0, 0] for _ in range(across * down)]
    for y in range(0, height, 2):
        row = pixels[y * width * 3:(y + 1) * width * 3]
        base = (y // tile) * across
        for tx in range(across):
            part = row[tx * tile * 3:min(width, (tx + 1) * tile) * 3]
            s = sums[base + tx]
            s[0] += sum(part[0::3])
            s[1] += sum(part[1::3])
            s[2] += sum(part[2::3])
            s[3] += len(part) // 3
    return [(r / n, g / n, b / n) if n else (0, 0, 0) for r, g, b, n in sums]


def states(times, looks, end, fuzz):
    """[[look, start, seconds held, picture]]: `looks` at `times`, a look
    held while the next is within `fuzz` (of 255) of it in every channel,
    and the picture, by its place in `looks`, it first showed in."""
    out = []
    for k, (t, look) in enumerate(zip(times, looks)):
        if out and all(abs(a - b) <= fuzz for a, b in zip(out[-1][0], look)):
            continue
        out.append([look, t, None, k])
    for here, there in zip(out, out[1:]):
        here[2] = there[1] - here[1]
    if out:
        out[-1][2] = end - out[-1][1]
    return out


def alike(a, b, fuzz):
    """Whether two tiles' RGB pixels are the same picture, near enough: no
    more than a tenth of them differing by more than `fuzz` percent."""
    return differ(a, b, fuzz, len(a) // 30) <= len(a) // 30


def plain(pixels, fuzz):
    """Whether a tile's pixels are all near one color: a flash to black, or
    white, not something passing through it."""
    return all(max(pixels[c::3]) - min(pixels[c::3]) <= 2 * fuzz * 2.55 for c in range(3))


def find(held, brief, times, fuzz, pixels):
    """In one tile's states, its longest flicker, as (start, changes, mean
    seconds a brief state, look A, look B), or else a flash, as (start,
    seconds, look, then look), or None: see the module's docstring.
    `pixels(state)` is the tile's pixels in a state's first picture, for
    what mean colors can't tell: that the looks come back as the same
    pictures, which a line of text scrolling past, or anything else moving,
    doesn't."""
    near = fuzz * 2.55

    def same(a, b):
        return all(abs(x - y) <= near for x, y in zip(a, b))

    best, flash, i = None, None, 1
    while i + 1 < len(held):
        j = i
        # A run: each state held briefly, and the same look as two before.
        while (j + 1 < len(held) and held[j][2] <= brief and same(held[j - 1][0], held[j + 1][0])
               and not same(held[j][0], held[j + 1][0])):
            j += 1
        changes = j - i + 1
        if changes < 2:
            i += 1
            continue
        # Each look the same picture as two states before.
        recurs = all(alike(pixels(held[k]), pixels(held[k + 2]), fuzz) for k in range(i - 1, j - 1))
        if recurs and changes >= times:
            brief_ones = [st[2] for st in held[i:j + 1] if st[2] <= brief]
            run = (held[i][1], changes, sum(brief_ones) / len(brief_ones), held[i - 1][0],
                   held[i][0])
            best = run if best is None or changes > best[1] else best
        elif recurs and not flash and plain(pixels(held[i]), fuzz):
            flash = (held[i][1], held[i][2], held[i][0], held[i - 1][0])
        i = j + 1
    return best, (None if best else flash)


def boxes(tiles, across, tile):
    """Tiles, by index, that touch, merged: [(x, y, width, height, [tiles])]."""
    left, out = set(tiles), []
    while left:
        group, todo = [], [left.pop()]
        while todo:
            n = todo.pop()
            group.append(n)
            x, y = n % across, n // across
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    m = (y + dy) * across + (x + dx)
                    if 0 <= x + dx < across and m in left:
                        left.remove(m)
                        todo.append(m)
        xs, ys = [n % across for n in group], [n // across for n in group]
        out.append((min(xs) * tile, min(ys) * tile, (max(xs) - min(xs) + 1) * tile,
                    (max(ys) - min(ys) + 1) * tile, group))
    return sorted(out, key=lambda b: (b[1], b[0]))


def hex_of(look):
    return "#" + "".join(f"{round(c):02x}" for c in look)
