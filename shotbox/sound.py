"""What a recording of a session's sound says: how loud it is, whether it
clipped, and where it dropped out.

A stream that stops feeding the sink (an underrun: a player that falls
behind) leaves the sink's mix silent, and PipeWire fills that with exact
zeros: a gap is a run of frames that are all zero, between the first
sound and the last. A wave crosses zero a sample at a time; a millisecond
of zeros in every channel is no wave.
"""

import array
import math
import operator
import re
import sys
import wave


def decibels(level):
    """A level, of 32768 full scale, in dBFS."""
    return 20 * math.log10(level / 32768) if level else -math.inf


def hear(path, gap=0.001):
    """What the 16-bit WAV at `path` holds: a dict of seconds, rate,
    channels, peak and rms (dBFS), clipped (samples at full scale), and
    gaps, [(seconds in, seconds long)] for each run of silence `gap`
    seconds or longer between the first sound and the last."""
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path} is {8 * w.getsampwidth()}-bit: only 16-bit PCM is heard")
        channels, rate = w.getnchannels(), w.getframerate()
        data = w.readframes(w.getnframes())
    width, shortest = 2 * channels, max(1, round(gap * rate))
    data = data[:len(data) // width * width]    # a file cut off mid-frame
    samples = array.array("h", data)
    if sys.byteorder == "big":
        samples.byteswap()
    peak = max(max(samples, default=0), -min(samples, default=0))
    gaps = []
    # Each run of zero bytes, whole, from its first: the lookbehind fails
    # at once anywhere else in it, so this is linear however long the runs.
    for m in re.finditer(b"(?<!\0)\0{%d,}" % (shortest * width), data):
        # The frames wholly inside it: one may begin mid-frame.
        start, end = -(-m.start() // width) * width, m.end() // width * width
        if m.start() > 0 and m.end() < len(data) and (end - start) // width >= shortest:
            gaps.append((start // width / rate, (end - start) // width / rate))
    return {
        "seconds": len(samples) // channels / rate, "rate": rate, "channels": channels,
        "peak": decibels(peak),
        "rms": decibels(math.sqrt(sum(map(operator.mul, samples, samples)) / len(samples))
                        if samples else 0),
        "clipped": samples.count(32767) + samples.count(-32768),
        "gaps": gaps,
    }


def describe(heard):
    """What `hear` found, a line each: the whole, then each gap."""
    return ([f"{heard['seconds']:.3f}s, {heard['rate']} Hz, {heard['channels']} channels: "
             f"peak {heard['peak']:.1f} dBFS, rms {heard['rms']:.1f} dBFS, "
             f"{heard['clipped']} clipped"]
            + [f"gap at {at:.3f}s, {long * 1000:.1f} ms" for at, long in heard["gaps"]])


def problems(heard, quiet=-60.0, clipped=0):
    """What's wrong with what `hear` found: a line each."""
    out = []
    if heard["peak"] < quiet:
        out.append(f"silent: its peak is below {quiet:g} dBFS")
    if heard["clipped"] > clipped:
        n = heard["clipped"]
        out.append(f"clipped: {n} sample{'s' * (n > 1)} at full scale, {clipped} allowed")
    if heard["gaps"]:
        out.append(f"{len(heard['gaps'])} gap{'s' * (len(heard['gaps']) > 1)} of silence "
                   "in the sound")
    return out
