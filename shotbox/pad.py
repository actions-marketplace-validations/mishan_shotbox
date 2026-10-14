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
import platform
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


# An ioctl's direction bits: asm-generic's, but for the few architectures
# that lay them out their own way.
_OWN = platform.machine().startswith(("ppc", "powerpc", "mips", "sparc", "alpha"))
_NONE, _WRITE, _READ = (1, 4, 2) if _OWN else (0, 1, 2)
_DIRSHIFT = 29 if _OWN else 30


def _ioc(direction, nr, size):
    return (direction << _DIRSHIFT) | (size << 16) | (ord("U") << 8) | nr


UI_DEV_CREATE = _ioc(_NONE, 1, 0)
UI_DEV_SETUP = _ioc(_WRITE, 3, 92)      # struct uinput_setup
UI_ABS_SETUP = _ioc(_WRITE, 4, 28)      # struct uinput_abs_setup
UI_SET_EVBIT = _ioc(_WRITE, 100, 4)
UI_SET_KEYBIT = _ioc(_WRITE, 101, 4)
UI_SET_ABSBIT = _ioc(_WRITE, 103, 4)
UI_GET_SYSNAME = _ioc(_READ, 44, 64)
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
        # No fuzz: the kernel would smooth small moves, and there's no noise
        # here to smooth. The dead zone is xpad's.
        axes = [(c, -STICK_MAX - 1, STICK_MAX, 0, 128) for pair in STICKS.values() for c in pair]
        axes += [(c, 0, TRIGGER_MAX, 0, 0) for c in TRIGGERS.values()]
        axes += [(c, -1, 1, 0, 0) for c in (0x10, 0x11)]
        for code, lo, hi, fuzz, flat in axes:
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
            fcntl.ioctl(self.fd, UI_ABS_SETUP, struct.pack("H2x6i", code, 0, lo, hi, fuzz, flat, 0))
        fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("HHHH80sI", BUS_USB, VENDOR, PRODUCT,
                                                       VERSION, name.encode()[:79], 0))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        sysname = fcntl.ioctl(self.fd, UI_GET_SYSNAME, bytes(64)).split(b"\0")[0].decode()
        self.nodes = self._nodes(sysname)
        self.state = {}         # (type, code): value, what isn't at rest
        self.dpad = set()       # d-pad directions down

    @staticmethod
    def _nodes(sysname):
        """The /dev/input nodes the kernel made for it, once udev lets us
        open them: its eventN, and the jsN that older games read, if joydev
        made one."""
        sysdir = Path("/sys/devices/virtual/input") / sysname
        end = time.monotonic() + 5
        while time.monotonic() < end:
            names = [p.name for p in sorted(sysdir.glob("event*")) + sorted(sysdir.glob("js*"))]
            nodes = [str(Path("/dev/input") / n) for n in names]
            if any(n.startswith("event") for n in names) and all(os.access(n, os.R_OK) for n in nodes):
                return nodes
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
                # Each direction goes up on its own: letting go of up while
                # down is held leaves the pad pointing down.
                (self.dpad.add if down else self.dpad.discard)(name)
                code, _ = DPAD[name]
                ways = [w for n, (c, w) in DPAD.items() if c == code and n in self.dpad]
                self._emit(EV_ABS, code, ways[-1] if len(ways) == 1 else 0)
            else:
                self._emit(EV_KEY, BUTTONS[name], int(down))
        self._sync()

    def stick(self, which, x, y):
        cx, cy = STICKS[which]
        self._emit(EV_ABS, cx, round(x * STICK_MAX))
        self._emit(EV_ABS, cy, round(y * STICK_MAX))
        self._sync()

    def trigger(self, which, value):
        self._emit(EV_ABS, TRIGGERS[which], round(value * TRIGGER_MAX))
        self._sync()

    def neutral(self):
        self.dpad.clear()
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


def check_range(what, v, lo, hi):
    """`v` as a float, or a PadError if it isn't one between lo and hi."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        f = float("nan")
    if not lo <= f <= hi:
        raise PadError(f"{what} goes from {lo:g} to {hi:g}, not {v}")
    return f


def handle(pads, req):
    """One request to the server: a dict in, a dict out."""
    if not isinstance(req, dict):
        raise PadError("a request is a JSON object")
    if req.get("op") == "info":
        return {"pads": [p.nodes for p in pads]}
    n = req.get("pad", 1)
    if not isinstance(n, int) or not 1 <= n <= len(pads):
        raise PadError(f"no pad {n}: the session has {len(pads)}")
    pad = pads[n - 1]
    op = req.get("op")
    if op == "buttons":
        if not isinstance(req["names"], list) or not all(isinstance(n, str) for n in req["names"]):
            raise PadError("names is a list of buttons")
        check_names(req["names"])
        pad.buttons(req["names"], req["state"] == "down")
    elif op == "stick":
        pad.stick(req["which"], check_range("a stick", req["x"], -1, 1),
                  check_range("a stick", req["y"], -1, 1))
    elif op == "trigger":
        pad.trigger(req["which"], check_range("a trigger", req["value"], 0, 1))
    elif op == "neutral":
        pad.neutral()
    else:
        raise PadError(f"no such request: {op!r}")
    return {}


def die_with_parent(parent):
    """Stop when the session's process (`parent`) does, however it goes:
    killed, the pads would stay plugged into the machine. Closing their
    files at exit unplugs them."""
    import ctypes
    import signal
    PR_SET_PDEATHSIG = 1
    ctypes.CDLL(None, use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    if os.getppid() != int(parent):     # it went before the line above took
        os._exit(1)


def write_ready(path, what):
    """Written whole and then renamed, so a reader never sees half of it."""
    tmp = Path(f"{path}.tmp")
    tmp.write_text(json.dumps(what))
    tmp.rename(path)


# Requests are answered one at a time, so each gets this long to arrive:
# any program in the session can connect, and one that hangs up, stalls or
# trickles costs no one else more than this.
REQUEST_SECONDS = 1.0


def read_line(conn, seconds):
    """A line from `conn`, or what came of it in `seconds` in all."""
    end, data = time.monotonic() + seconds, b""
    while b"\n" not in data:
        left = end - time.monotonic()
        if left <= 0:
            break
        conn.settimeout(left)
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    return data.split(b"\n", 1)[0] if b"\n" in data else b""


def serve(sock_path, count, ready, parent):
    """Make `count` pads, write their nodes to `ready` (a JSON list), and
    answer requests on the Unix socket at `sock_path`, a JSON line each,
    until stopped, or until the process `parent` stops."""
    die_with_parent(parent)
    pads = []
    try:
        for i in range(int(count)):
            pads.append(Pad(f"shotbox virtual pad {i + 1} (Xbox 360)"))
    except PadError as e:
        write_ready(ready, {"error": str(e)})
        return 1
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(sock_path)
    srv.listen(8)
    write_ready(ready, [node for p in pads for node in p.nodes])
    try:
        while True:
            conn, _ = srv.accept()
            with conn:
                try:
                    line = read_line(conn, REQUEST_SECONDS)
                    if not line:
                        continue
                    try:
                        reply = handle(pads, json.loads(line))
                    except (PadError, KeyError, ValueError, TypeError) as e:
                        reply = {"error": str(e) if isinstance(e, PadError)
                                 else f"bad request: {e}"}
                    conn.sendall(json.dumps(reply).encode() + b"\n")
                except OSError:
                    pass
    finally:
        for p in pads:
            p.close()


def ask(scratch, req):
    """Send one request to the session's pad server; its answer."""
    path = Path(scratch) / "pads.sock"
    if not path.exists():
        raise PadError("this session has no gamepads: start it with some "
                       "(--gamepads N, or gamepads in MCP's start_session)")
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(10)
            s.connect(str(path))
            s.sendall(json.dumps(req).encode() + b"\n")
            reply = json.loads(s.makefile("rb").readline() or b"null")
    except TimeoutError:
        raise PadError("the session's gamepads didn't answer in time: busy, or stuck")
    except OSError as e:
        raise PadError(f"the session's gamepads have gone ({e.strerror or e})")
    if not isinstance(reply, dict):
        raise PadError("the session's gamepads have gone (no answer)")
    if "error" in reply:
        raise PadError(reply["error"])
    return reply

