"""Gamepads for a session: virtual Xbox 360 controllers, made with Linux's
uinput, that only the session's programs can see.

uinput makes a real input device, which is the point (a game reads it as
it reads a controller plugged in: SDL, evdev, anything), but it's made for
the whole machine. So a server in the session holds the devices, which
also keeps their buttons and sticks where they were left between commands;
and every program the session starts runs under bubblewrap with a
/dev/input of its own, holding only these devices: none of yours gets in.
Your desktop can still see them while the session runs, named as made by
shotbox; they go when it stops.

The standard library only: the ioctls and structs are Linux's, from
<linux/uinput.h> and <linux/input.h>.
"""

import fcntl
import json
import os
import socket
import struct
import time
from pathlib import Path

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3

# As Linux's xpad driver reports an Xbox 360 pad, so games' mappings for
# that pad apply. Its X and Y are BTN_NORTH and BTN_WEST, as xpad has them.
BUTTONS = {"a": 0x130, "b": 0x131, "x": 0x133, "y": 0x134, "lb": 0x136, "rb": 0x137,
           "back": 0x13A, "start": 0x13B, "guide": 0x13C, "ls": 0x13D, "rs": 0x13E}
DPAD = {"up": (0x11, -1), "down": (0x11, 1), "left": (0x10, -1), "right": (0x10, 1)}
STICKS = {"left": (0x00, 0x01), "right": (0x03, 0x04)}     # ABS_X/Y, ABS_RX/RY
TRIGGERS = {"left": 0x02, "right": 0x05}                   # ABS_Z, ABS_RZ
STICK_MAX, TRIGGER_MAX = 32767, 255

# struct input_id and the ids of a wired Xbox 360 pad.
BUS_USB, VENDOR, PRODUCT, VERSION = 0x03, 0x045E, 0x028E, 0x0110


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("U") << 8) | nr


UI_DEV_CREATE = _ioc(0, 1, 0)
UI_DEV_SETUP = _ioc(1, 3, 92)           # struct uinput_setup
UI_ABS_SETUP = _ioc(1, 4, 28)           # struct uinput_abs_setup
UI_SET_EVBIT = _ioc(1, 100, 4)
UI_SET_KEYBIT = _ioc(1, 101, 4)
UI_SET_ABSBIT = _ioc(1, 103, 4)
UI_GET_SYSNAME = _ioc(2, 44, 64)
EVENT = struct.Struct("@llHHi")         # struct input_event: a timeval, type, code, value


class PadError(RuntimeError):
    pass


class Pad:
    """One virtual pad, there for as long as this object's file is open."""

    def __init__(self, name):
        try:
            self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            raise PadError(f"can't open /dev/uinput ({e.strerror}): gamepads need write "
                           "access to it, as the input group or a udev rule gives")
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for code in BUTTONS.values():
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
        axes = [(c, -STICK_MAX - 1, STICK_MAX, 16, 128) for pair in STICKS.values() for c in pair]
        axes += [(c, 0, TRIGGER_MAX, 0, 0) for c in TRIGGERS.values()]
        axes += [(c, -1, 1, 0, 0) for c in (0x10, 0x11)]
        for code, lo, hi, fuzz, flat in axes:
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
            fcntl.ioctl(self.fd, UI_ABS_SETUP, struct.pack("H2x6i", code, 0, lo, hi, fuzz, flat, 0))
        fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("HHHH80sI", BUS_USB, VENDOR, PRODUCT,
                                                       VERSION, name.encode()[:79], 0))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        sysname = fcntl.ioctl(self.fd, UI_GET_SYSNAME, bytes(64)).split(b"\0")[0].decode()
        self.node = self._node(sysname)
        self.state = {}         # (type, code): value, what isn't at rest

    @staticmethod
    def _node(sysname):
        """The /dev/input/eventN the kernel made for it, once udev lets us
        open it."""
        sysdir = Path("/sys/devices/virtual/input") / sysname
        end = time.monotonic() + 5
        while time.monotonic() < end:
            events = sorted(sysdir.glob("event*"))
            if events:
                node = Path("/dev/input") / events[0].name
                if os.access(node, os.R_OK):
                    return str(node)
            time.sleep(0.02)
        raise PadError(f"the pad {sysname} got no /dev/input node this user can read")

    def _emit(self, kind, code, value):
        os.write(self.fd, EVENT.pack(0, 0, kind, code, value))
        if value:
            self.state[(kind, code)] = value
        else:
            self.state.pop((kind, code), None)

    def _sync(self):
        os.write(self.fd, EVENT.pack(0, 0, EV_SYN, 0, 0))

    def buttons(self, names, down):
        for name in names:
            if name in DPAD:
                code, way = DPAD[name]
                self._emit(EV_ABS, code, way if down else 0)
            else:
                self._emit(EV_KEY, BUTTONS[name], int(down))
        self._sync()

    def stick(self, which, x, y):
        cx, cy = STICKS[which]
        self._emit(EV_ABS, cx, round(max(-1.0, min(1.0, x)) * STICK_MAX))
        self._emit(EV_ABS, cy, round(max(-1.0, min(1.0, y)) * STICK_MAX))
        self._sync()

    def trigger(self, which, value):
        self._emit(EV_ABS, TRIGGERS[which], round(max(0.0, min(1.0, value)) * TRIGGER_MAX))
        self._sync()

    def neutral(self):
        for kind, code in list(self.state):
            self._emit(kind, code, 0)
        self._sync()

    def close(self):
        os.close(self.fd)


def check_names(names):
    bad = [n for n in names if n not in BUTTONS and n not in DPAD]
    if bad:
        raise PadError(f"no button {', '.join(bad)}: the buttons are "
                       + ", ".join([*BUTTONS, *DPAD]))


def handle(pads, req):
    """One request to the server: a dict in, a dict out."""
    if req.get("op") == "info":
        return {"pads": [p.node for p in pads]}
    n = int(req.get("pad", 1))
    if not 1 <= n <= len(pads):
        raise PadError(f"no pad {n}: the session has {len(pads)}")
    pad = pads[n - 1]
    op = req.get("op")
    if op == "buttons":
        check_names(req["names"])
        pad.buttons(req["names"], req["state"] == "down")
    elif op == "stick":
        pad.stick(req["which"], float(req["x"]), float(req["y"]))
    elif op == "trigger":
        pad.trigger(req["which"], float(req["value"]))
    elif op == "neutral":
        pad.neutral()
    else:
        raise PadError(f"no such request: {op!r}")
    return {}


def serve(sock_path, count, ready):
    """Make `count` pads, write their nodes to `ready` (a JSON list), and
    answer requests on the Unix socket at `sock_path`, a JSON line each,
    until stopped."""
    pads = []
    try:
        for i in range(int(count)):
            pads.append(Pad(f"shotbox virtual pad {i + 1} (Xbox 360)"))
    except PadError as e:
        Path(ready).write_text(json.dumps({"error": str(e)}))
        return 1
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(sock_path)
    srv.listen(8)
    Path(ready).write_text(json.dumps([p.node for p in pads]))
    try:
        while True:
            conn, _ = srv.accept()
            with conn:
                line = conn.makefile("rb").readline()
                try:
                    reply = handle(pads, json.loads(line))
                except (PadError, KeyError, ValueError, TypeError) as e:
                    reply = {"error": str(e) if isinstance(e, PadError) else f"bad request: {e}"}
                conn.sendall(json.dumps(reply).encode() + b"\n")
    finally:
        for p in pads:
            p.close()


def ask(scratch, req):
    """Send one request to the session's pad server; its answer."""
    path = Path(scratch) / "pads.sock"
    if not path.exists():
        raise PadError("this session has no gamepads: start it with --gamepads N")
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(path))
        s.sendall(json.dumps(req).encode() + b"\n")
        reply = json.loads(s.makefile("rb").readline())
    if "error" in reply:
        raise PadError(reply["error"])
    return reply

