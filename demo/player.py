#!/usr/bin/env python3
"""A tiny GTK 4 player for the demo's sound scene: it plays a few soft
notes through pacat, feeding them in time, and falls behind once, for
STALL seconds (0.35), as a player whose loop stalls does: the sound drops
out, and its progress bar stops."""
import math
import struct
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

RATE, CHUNK = 48000, 480
NOTES = (523.25, 659.25, 783.99, 1046.5, 783.99, 659.25)
NOTE = 0.4
STALL_AT, STALL = 1.25, float(sys.argv[1]) if len(sys.argv) > 1 else 0.35


def note(freq):
    n = int(RATE * NOTE)
    out = bytearray()
    for i in range(n):
        env = min(1, i / 480) * math.exp(-4 * i / n)
        s = int(6000 * env * (math.sin(2 * math.pi * freq * i / RATE)
                              + 0.3 * math.sin(4 * math.pi * freq * i / RATE)))
        out += struct.pack("<hh", s, s)
    return bytes(out)


def play(bar):
    pcm = b"".join(note(f) for f in NOTES)
    pacat = subprocess.Popen(["pacat", "--format=s16le", f"--rate={RATE}", "--channels=2",
                              "--latency-msec=30"], stdin=subprocess.PIPE)
    step, sent, start, stalled = CHUNK * 4, 0, time.monotonic(), False
    while sent < len(pcm):
        if not stalled and sent >= STALL_AT * RATE * 4:
            stalled = True
            time.sleep(STALL)
            start += STALL - 0.1
        ahead = sent / (RATE * 4) - (time.monotonic() - start)
        if ahead > 0.1:
            time.sleep(ahead - 0.1)
        pacat.stdin.write(pcm[sent:sent + step])
        pacat.stdin.flush()
        sent += step
        GLib.idle_add(bar.set_fraction, sent / len(pcm))
    pacat.stdin.close()
    pacat.wait()


def activate(app):
    bar = Gtk.ProgressBar(margin_start=16, margin_end=16, valign=Gtk.Align.CENTER)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
    box.append(Gtk.Label(label="♪  Chime", css_classes=["title-2"]))
    box.append(bar)
    Gtk.ApplicationWindow(application=app, title="Player",
                          titlebar=Adw.HeaderBar(decoration_layout=":close"), child=box,
                          default_width=224, default_height=480).present()
    threading.Thread(target=play, args=(bar,), daemon=True).start()


app = Adw.Application(application_id="org.example.Player")
app.connect("activate", activate)
app.run()
