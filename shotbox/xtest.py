"""Pressing keys and moving the pointer on the session's display.

A few X11 requests spoken over the display's socket, and the XTEST
extension's FakeInput, which every X server shotbox runs (Xvfb) has. No
Python X binding and no xdotool: the standard library and `xauth`.
"""

import os
import re
import socket
import struct
import subprocess

# Keysyms for the names a chord can use. Printable ASCII is its own keysym.
KEYSYMS = {
    "Return": 0xFF0D, "Enter": 0xFF0D, "Escape": 0xFF1B, "Esc": 0xFF1B,
    "Tab": 0xFF09, "BackSpace": 0xFF08, "Delete": 0xFFFF, "Insert": 0xFF63,
    "Home": 0xFF50, "End": 0xFF57, "Page_Up": 0xFF55, "Page_Down": 0xFF56,
    "Left": 0xFF51, "Up": 0xFF52, "Right": 0xFF53, "Down": 0xFF54,
    "space": 0x20, "comma": 0x2C, "period": 0x2E, "slash": 0x2F,
    "minus": 0x2D, "equal": 0x3D, "plus": 0x2B, "Menu": 0xFF67,
    **{f"F{n}": 0xFFBE + n - 1 for n in range(1, 13)},
}
MODIFIERS = {
    "ctrl": 0xFFE3, "control": 0xFFE3, "shift": 0xFFE1,
    "alt": 0xFFE9, "super": 0xFFEB, "meta": 0xFFE7,
}
SHIFT = 0xFFE1

KEY_PRESS, KEY_RELEASE, BUTTON_PRESS, BUTTON_RELEASE, MOTION = 2, 3, 4, 5, 6


class XError(RuntimeError):
    pass


def parse_chord(spec):
    """A chord's (keysym, modifier keysyms): `ctrl+comma`, `Return`,
    `alt+shift+Tab`. A lone `+` is the plus key."""
    *mods, key = spec.split("+") if spec != "+" else ["+"]
    for m in mods:
        if m.lower() not in MODIFIERS:
            raise XError(f"unknown modifier {m!r} in {spec!r}")
    sym = KEYSYMS.get(key, ord(key) if len(key) == 1 else None)
    if sym is None:
        raise XError(f"unknown key {key!r} in {spec!r}")
    return sym, [MODIFIERS[m.lower()] for m in mods]


def _pad(n):
    return (4 - n % 4) % 4


def _cookie(display, xauthority):
    out = subprocess.run(["xauth", "-f", xauthority, "list", display],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == "MIT-MAGIC-COOKIE-1":
            return bytes.fromhex(parts[2])
    return b""


class Display:
    """A connection to the display in `env`: DISPLAY and XAUTHORITY."""

    def __init__(self, env=None):
        env = os.environ if env is None else env
        display = env.get("DISPLAY", "")
        m = re.fullmatch(r":(\d+)(\.\d+)?", display)
        if not m:
            raise XError(f"can't use DISPLAY={display!r}: only a local :N display")
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(f"/tmp/.X11-unix/X{m.group(1)}")
        self.seq = 0
        cookie = _cookie(display, env.get("XAUTHORITY", ""))
        name = b"MIT-MAGIC-COOKIE-1" if cookie else b""
        setup = struct.pack("<BxHHHHxx", 0x6C, 11, 0, len(name), len(cookie))
        setup += name + b"\0" * _pad(len(name)) + cookie + b"\0" * _pad(len(cookie))
        self.sock.sendall(setup)
        head = self._read(8)
        more = self._read(struct.unpack_from("<H", head, 6)[0] * 4)
        if head[0] != 1:
            reason = more[: head[1]].decode(errors="replace")
            raise XError(f"the X server refused the connection: {reason}")
        vendor_len, = struct.unpack_from("<H", more, 16)
        screens, formats = more[20], more[21]
        self.min_keycode, self.max_keycode = more[26], more[27]
        at = 32 + vendor_len + _pad(vendor_len) + 8 * formats
        self.root, = struct.unpack_from("<I", more, at) if screens else (0,)
        # The root's size: after it, a colormap, two pixels and an event mask.
        self.width, self.height = struct.unpack_from("<HH", more, at + 20) if screens else (0, 0)
        self.xtest = self._extension(b"XTEST")
        self.keymap = self._keymap()

    # --- the wire -------------------------------------------------------------

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            got = self.sock.recv(n - len(buf))
            if not got:
                raise XError("the X server closed the connection")
            buf += got
        return buf

    def _request(self, data):
        self.sock.sendall(data)
        self.seq = (self.seq + 1) & 0xFFFF

    def _reply(self):
        """The reply to the last request, skipping events on the way."""
        while True:
            head = self._read(32)
            kind = head[0]
            if kind == 0:
                raise XError(f"X error {head[1]} (request {head[10]}.{struct.unpack_from('<H', head, 8)[0]})")
            if kind == 1:
                extra = struct.unpack_from("<I", head, 4)[0] * 4
                body = head + (self._read(extra) if extra else b"")
                if struct.unpack_from("<H", head, 2)[0] == self.seq:
                    return body
            # anything else is an event; nobody asked for it

    def sync(self):
        """Wait until the server has handled everything sent so far."""
        self._request(struct.pack("<BxH", 43, 1))   # GetInputFocus
        self._reply()

    def _extension(self, name):
        req = struct.pack("<BxHHxx", 98, 2 + (len(name) + _pad(len(name))) // 4, len(name))
        self._request(req + name + b"\0" * _pad(len(name)))
        reply = self._reply()
        if not reply[8]:
            raise XError(f"the X server has no {name.decode()} extension")
        return reply[9]

    def _keymap(self):
        count = self.max_keycode - self.min_keycode + 1
        self._request(struct.pack("<BxHBBxx", 101, 2, self.min_keycode, count))
        reply = self._reply()
        per = reply[1]
        syms = struct.unpack_from(f"<{count * per}I", reply, 32)
        keymap = {}
        for i in range(count):
            for col in (0, 1):
                sym = syms[i * per + col] if col < per else 0
                if sym and sym not in keymap:
                    keymap[sym] = (self.min_keycode + i, col == 1)
        return keymap

    def _fake(self, kind, detail, x=0, y=0):
        self._request(struct.pack("<BBHBBxxIIIIhhIHBB", self.xtest, 2, 9, kind, detail,
                                  0, self.root if kind == MOTION else 0, 0, 0,
                                  x, y, 0, 0, 0, 0))

    # --- what callers use -----------------------------------------------------

    def keycode(self, keysym):
        if keysym not in self.keymap:
            raise XError(f"no key for keysym 0x{keysym:x} in this keymap")
        return self.keymap[keysym]

    def _codes(self, keysym, modifiers):
        """The key's keycode, and the modifiers' to hold with it: Shift too
        if the key's symbol is on its shifted level."""
        code, shifted = self.keycode(keysym)
        held = [self.keycode(m)[0] for m in modifiers]
        if shifted and SHIFT not in modifiers:
            held.append(self.keycode(SHIFT)[0])
        return code, held

    def press(self, keysym, modifiers=()):
        """Press and release one key, holding the modifier keysyms down."""
        self.down(keysym, modifiers, sync=False)
        self.up(keysym, modifiers)

    def down(self, keysym, modifiers=(), sync=True):
        """Press a key, modifiers first, and leave them all down. The X
        server keeps them down after this connection closes, until `up`."""
        code, held = self._codes(keysym, modifiers)
        for c in held:
            self._fake(KEY_PRESS, c)
        self._fake(KEY_PRESS, code)
        if sync:
            self.sync()

    def up(self, keysym, modifiers=()):
        """Let go of a key pressed with `down`, then its modifiers."""
        code, held = self._codes(keysym, modifiers)
        self._fake(KEY_RELEASE, code)
        for c in reversed(held):
            self._fake(KEY_RELEASE, c)
        self.sync()

    def chord(self, spec, state=None):
        """Press and release a chord: `ctrl+comma`, `Return`, `alt+shift+Tab`;
        or with `state` "down" or "up", only press it or only let it go."""
        sym, mods = parse_chord(spec)
        {None: self.press, "down": self.down, "up": self.up}[state](sym, mods)

    def type(self, text):
        """Type text, one key at a time. ASCII only: a character is typed by
        the key its symbol is on."""
        for ch in text:
            sym = {"\n": 0xFF0D, "\r": 0xFF0D, "\t": 0xFF09}.get(ch, ord(ch))
            self.press(sym)

    def move(self, x, y):
        self._fake(MOTION, 0, x, y)
        self.sync()

    def click(self, x, y, button=1, count=1):
        self.move(x, y)
        for _ in range(count):
            self._fake(BUTTON_PRESS, button)
            self._fake(BUTTON_RELEASE, button)
        self.sync()

    def button(self, x, y, button=1, state="down"):
        """Move to x, y and press a button, leaving it down, or let it go."""
        self.move(x, y)
        self._fake(BUTTON_PRESS if state == "down" else BUTTON_RELEASE, button)
        self.sync()

    def drag(self, x1, y1, x2, y2, button=1, steps=10):
        """Press at one point, move to another in steps, release there: a
        divider, a slider, a drag and drop. Each step is synced, so the
        toolkit sees the motion rather than a jump."""
        self.move(x1, y1)
        self._fake(BUTTON_PRESS, button)
        self.sync()
        for i in range(1, steps + 1):
            self.move(x1 + (x2 - x1) * i // steps, y1 + (y2 - y1) * i // steps)
        self._fake(BUTTON_RELEASE, button)
        self.sync()

    def focus(self, wid):
        """Raise window `wid` and give it the keyboard (until the pointer's
        root takes it back, should the window go)."""
        self._request(struct.pack("<BxHIHxxI", 12, 4, wid, 0x40, 0))   # ConfigureWindow: Above
        self._request(struct.pack("<BBHII", 42, 1, 3, wid, 0))         # SetInputFocus
        self.sync()

    def close(self):
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
