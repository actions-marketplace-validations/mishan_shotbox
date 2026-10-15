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
line of text scrolling past, or anything moving through, doesn't; or one
of them does, and is a color all over, a frame drawn black between frames
of a scene that moves on. A flash is to a color, all of the tile, as a
frame drawn black, and back to the same picture as before.
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
    """Whether two tiles' RGB pixels are the same picture: no more than one
    in fifty differing by more than `fuzz` percent. Text scrolling past
    differs by more, in its strokes."""
    return differ(a, b, fuzz, len(a) // 150) <= len(a) // 150


def plain(pixels, other, fuzz):
    """Whether a tile's pixels, where they differ from `other`'s, are all
    near one color: a flash to black, or a band of the tile drawn black
    every other frame, not something passing through it. Where they're
    the same, the rest of the tile, doesn't count."""
    limit = fuzz * 2.55
    changed = [k for k in range(0, len(pixels), 3)
               if max(abs(pixels[k + c] - other[k + c]) for c in range(3)) > limit]
    return bool(changed) and all(
        max(pixels[k + c] for k in changed) - min(pixels[k + c] for k in changed) <= 2 * limit
        for c in range(3))


def colors(a, b, fuzz):
    """Two tiles' looks, as the mean color of each where they differ: a
    narrow band's own colors, not the tile's, most of which held still."""
    limit = fuzz * 2.55
    changed = [k for k in range(0, len(a), 3)
               if max(abs(a[k + c] - b[k + c]) for c in range(3)) > limit] or range(0, len(a), 3)
    return tuple(tuple(sum(p[k + c] for k in changed) / len(changed) for c in range(3))
                 for p in (a, b))


def find(held, brief, times, fuzz, pixels):
    """In one tile's states, its longest flicker, as (start, changes, mean
    seconds a brief state, look A, look B), or else a flash, as (start,
    seconds, look, then look), or None: see the module's docstring.
    `pixels(k)` is the tile's pixels in picture `k`, which decide what the
    mean colors found: brief states one after another."""
    best, flash, i = None, None, 1
    while i < len(held):
        if held[i][2] > brief:
            i += 1
            continue
        k = i
        while k < len(held) and held[k][2] <= brief:
            k += 1
        # The brief states, and the look either side of them: A, B, A, B...
        seq = held[i - 1:k + 1]
        changes = len(seq) - 1
        recur = [all(alike(pixels(seq[m][3]), pixels(seq[m + 2][3]), fuzz)
                     for m in range(side, len(seq) - 2, 2)) for side in (0, 1)]
        a, b = pixels(seq[0][3]), pixels(seq[1][3])
        plains = [plain(a, b, fuzz), plain(b, a, fuzz)]
        # Both looks the same pictures each time; or one, a color all over,
        # as a frame drawn black between frames of a scene that moves on.
        if changes >= times and (all(recur) or any(r and p for r, p in zip(recur, plains))):
            run = (seq[1][1], changes, sum(s[2] for s in held[i:k]) / (k - i),
                   *colors(a, b, fuzz))
            best = run if best is None or changes > best[1] else best
        # A flash: from a picture that held still, to a color, and back:
        # what's moving holds no picture still, though it come back.
        elif (changes == 2 and recur[0] and plains[1] and not flash
              and alike(pixels(seq[0][3]), pixels(seq[1][3] - 1), fuzz)):
            before, during = colors(a, b, fuzz)
            flash = (seq[1][1], seq[1][2], during, before)
        i = k
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
