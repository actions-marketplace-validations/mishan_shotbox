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

Mean colors find what might be, a quarter of fuzz a change, so a band a
pixel wide is seen; the tile's pixels decide. Flicker keeps its place:
the pixels every change changes are most of what each does, and no
fewer than a line across the tile, which text, or a line's steps,
moving through aren't. Its looks come back as the same pictures most
times, nearer than A is to B, A as A and B as B, which anything moving
through doesn't; or one of them does, and is one color where they
differ, a band drawn black between frames of a scene that moves on. A
flash is to one color where it differs, from a picture that held still
and back to it; and the tiles round it don't change about then, as they
do when something moves through. A look that's in the picture before or
after, up or down the screen, is text scrolling, and neither.
"""

from .x import differ


def means(pixels, width, height, tile, before=None, then=None):
    """Each tile's mean color, (r, g, b), of RGB `pixels`: every row, so a
    band a row high is in it. A tile as it was in the picture `before`,
    whose means were `then`, keeps its mean: most of a screen holds still."""
    out = []
    for ty in range(0, height, tile):
        band = slice(ty * width * 3, min(height, ty + tile) * width * 3)
        if before is not None and pixels[band] == before[band]:
            out += then[len(out):len(out) + -(-width // tile)]
            continue
        for tx in range(0, width, tile):
            rows = [(y * width + tx) * 3 for y in range(ty, min(height, ty + tile))]
            span = (min(width, tx + tile) - tx) * 3
            if before is not None and all(pixels[r:r + span] == before[r:r + span] for r in rows):
                out.append(then[len(out)])
                continue
            r = g = b = 0
            for at in rows:
                part = pixels[at:at + span]
                r, g, b = r + sum(part[0::3]), g + sum(part[1::3]), b + sum(part[2::3])
            n = len(rows) * span // 3
            out.append((r / n, g / n, b / n))
    return out


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


def changed(a, b, fuzz):
    """The pixels, by offset, where two tiles' RGB pixels differ by more
    than `fuzz` percent in a channel."""
    limit = fuzz * 2.55
    return {k for k in range(0, len(a), 3)
            if max(abs(a[k + c] - b[k + c]) for c in range(3)) > limit}


def alike(a, b, fuzz):
    """Whether two tiles' RGB pixels are the same picture: no more than one
    in fifty differing by more than twice `fuzz` percent, which noise in a
    black that isn't clean is within. Text scrolling past differs by more,
    in its strokes."""
    return differ(a, b, 2 * fuzz, len(a) // 150) <= len(a) // 150


def recur(shown, fuzz):
    """Whether each look, A and B, of a tile's pictures `shown` a state
    after another, comes back as the same picture most times: something
    passing through the rest of the tile spoils a few."""
    return [2 * sum(same) > len(same) for same in
            ([alike(shown[m], shown[m + 2], fuzz) for m in range(side, len(shown) - 2, 2)]
             for side in (0, 1))]


def plain(pixels, where, fuzz):
    """Whether a tile's pixels at `where`, where it differs from another
    look, are all near one color: a flash to black, or a band of the tile
    drawn black every other frame, not something passing through it."""
    limit = fuzz * 2.55
    return all(max(pixels[k + c] for k in where) - min(pixels[k + c] for k in where) <= 2 * limit
               for c in range(3))


def colors(a, b, where):
    """Two tiles' looks, as the mean color of each at `where`, where they
    differ: a narrow band's own colors, not the tile's, most of which held
    still."""
    return tuple(tuple(sum(p[k + c] for k in where) / len(where) for c in range(3))
                 for p in (a, b))


def runs(held, brief):
    """Each stretch of brief states in a tile's, with the look either side:
    [A, B, A, B...], as states."""
    i = 1
    while i < len(held):
        if held[i][2] > brief:
            i += 1
            continue
        k = i
        while k < len(held) and held[k][2] <= brief:
            k += 1
        yield held[i - 1:k + 1]
        i = k


def needs(held, brief):
    """The pictures, by place, `find` looks at a tile in: each state's
    first, and the last of a flash's first look."""
    return {k for seq in runs(held, brief) for k in [s[3] for s in seq] + [seq[1][3] - 1]}


def find(held, brief, times, fuzz, pixels):
    """In one tile's states, its longest flicker, as (start, changes, mean
    seconds a brief state, look A, look B, the pictures it changed in), or
    else a flash, as (start, seconds, look, then look, picture, the
    picture it came back in), each, or None and []: see the module's
    docstring.
    `pixels(k)` is the tile's pixels in picture `k`, of those `needs` says,
    which decide what the mean colors found: brief states one after
    another. A flash is said with its picture too, to tell it from motion."""
    best, flashes = None, []
    for seq in runs(held, brief):
        changes = len(seq) - 1
        if changes < times and changes != 2:
            continue
        shown = [pixels(st[3]) for st in seq]
        a, b = shown[0], shown[1]
        # Neither coming back even at the widest is something moving,
        # every picture new: its pixels needn't be looked at further.
        if not any(recur(shown, fuzz)):
            continue
        # Where it keeps switching: the pixels every change changes, which
        # must be most of what each changes, and no fewer than a line
        # across the tile. A passer-by elsewhere in the tile is left out;
        # text or a line moving through changes other pixels each time,
        # and a line's steps moving, a few.
        each = [changed(p, q, fuzz) for p, q in zip(shown, shown[1:])]
        where = set.intersection(*each)
        if len(where) < (len(a) // 3) ** 0.5 or any(2 * len(where) < len(c) for c in each):
            continue
        looks = colors(a, b, where)
        # The same picture is nearer than the two looks are: two faces of
        # a scene a little apart, an edge between them sweeping past,
        # would come back at any wider.
        near = min(fuzz, max(abs(x - y) for x, y in zip(*looks)) / 2.55 / 4)
        back = recur(shown, near)
        plains = [plain(a, where, fuzz), plain(b, where, fuzz)]
        # Both looks coming back as the same pictures; or one, a color all over,
        # as a frame drawn black between frames of a scene that moves on.
        if changes >= times and (all(back) or any(r and p for r, p in zip(back, plains))):
            brief_ones = [s[2] for s in seq[1:] if s[2] <= brief]
            run = (seq[1][1], changes, sum(brief_ones) / len(brief_ones), *looks,
                   [st[3] for st in seq[1:]])
            best = run if best is None or changes > best[1] else best
        # A flash: from a picture that held still, to a color, and back:
        # what's moving holds no picture still, though it come back.
        elif (changes == 2 and back[0] and plains[1]
              and alike(a, pixels(seq[1][3] - 1), fuzz)):
            flashes.append((seq[1][1], seq[1][2], looks[1], looks[0], seq[1][3], seq[2][3]))
    return best, ([] if best else flashes)


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
