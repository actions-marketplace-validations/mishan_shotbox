#!/usr/bin/env python3
"""Reads every input device it can see, as a game would: test/pad_read.py
LOG. Writes each one's name ("name NAME"), then "ready", then its key and
axis events ("key CODE VALUE", "abs CODE VALUE") as they come."""

import fcntl
import glob
import os
import select
import struct
import sys

EVENT = struct.Struct("@llHHi")
EVIOCGNAME = (2 << 30) | (256 << 16) | (ord("E") << 8) | 0x06

log = open(sys.argv[1], "a", buffering=1)
fds = []
for path in sorted(glob.glob("/dev/input/event*")):
    fd = os.open(path, os.O_RDONLY)
    name = fcntl.ioctl(fd, EVIOCGNAME, bytes(256)).split(b"\0")[0].decode()
    log.write(f"name {name}\nnode {path}\n")
    fds.append(fd)
log.write("ready\n")
while fds:
    for fd in select.select(fds, [], [])[0]:
        data = os.read(fd, EVENT.size * 64)
        for i in range(0, len(data), EVENT.size):
            _, _, kind, code, value = EVENT.unpack_from(data, i)
            if kind in (1, 3):
                log.write(f"{'key' if kind == 1 else 'abs'} {code} {value}\n")
