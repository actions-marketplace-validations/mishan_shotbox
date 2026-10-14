"""Pressing keys and moving the pointer in a Wayland session under sway:
what xtest.py does for X11.

A few Wayland requests spoken over the compositor's socket, and two
protocols wlroots compositors have for exactly this: a virtual keyboard
(zwp_virtual_keyboard_v1) and a virtual pointer (zwlr_virtual_pointer_v1).
No Python Wayland binding and no wtype: the standard library, with the
keymap passed as a file descriptor.

The keyboard is made once per connection, with a keymap holding every key
shotbox can press, laid out as a US keyboard is: a and A, 1 and !, Tab and
back-tab on one key, the second with Shift. So a shifted symbol is typed
with Shift held, and Shift in a chord gives the key's shifted symbol, as
on X11; and the keymap never changes. A program meets a new keyboard's keymap
along with its first key, and wtype, which makes a keyboard every time it
runs, was seen to drop a first key. So a new keyboard first presses a key
with no symbol, then waits for that to go through and a moment more. In
testing no key went missing with or without that; it's cheap to keep.
"""

import os
import socket
import struct
import time

from . import wl
from .xtest import KEYSYMS, SHIFT, XError, parse_chord

# Real modifier bits, as the keymap below maps them.
MOD_BITS = {0xFFE1: 0x01, 0xFFE3: 0x04, 0xFFE9: 0x08, 0xFFE7: 0x08, 0xFFEB: 0x40}
# Linux's input event codes for pointer buttons 1, 2 and 3.
BUTTONS = {1: 0x110, 2: 0x112, 3: 0x111}
PRESSED, RELEASED = 1, 0
KEYMAP_XKB_V1 = 1

# What the keymap holds, besides a key with no symbol for the first press
# and the modifiers: printable ASCII, a US keyboard's pairs on two levels,
# Tab with back-tab (ISO_Left_Tab) above it, and the other named keys.
PAIRS = ([(ord(c), ord(c.upper())) for c in "abcdefghijklmnopqrstuvwxyz"]
         + [(ord(a), ord(b)) for a, b in zip("1234567890-=[]\\;',./`",
                                            '!@#$%^&*()_+{}|:"<>?~')]
         + [(0x20, None), (0xFF09, 0xFE20)])
SINGLES = sorted(set(KEYSYMS.values()) - {k for pair in PAIRS for k in pair})


def keymap():
    """The keymap, as XKB text, and where each keysym is: (keycode,
    shifted)."""
    codes, symbols = [], []
    code = 9
    blank = code
    codes.append(f"<K{code}> = {code};")
    code += 1
    mods = {}
    for name, sym in (("Shift", 0xFFE1), ("Control", 0xFFE3), ("Mod1", 0xFFE9),
                      ("Mod4", 0xFFEB)):
        codes.append(f"<K{code}> = {code};")
        extra = ", 0xffe7" if sym == 0xFFE9 else ""   # Meta beside Alt, on Mod1
        symbols.append(f"key <K{code}> {{ [ 0x{sym:x}{extra} ] }};")
        symbols.append(f"modifier_map {name} {{ <K{code}> }};")
        mods[sym] = code
        code += 1
    keys = {}
    for low, high in PAIRS + [(sym, None) for sym in SINGLES]:
        codes.append(f"<K{code}> = {code};")
        levels = f"0x{low:x}" + (f", 0x{high:x}" if high else "")
        symbols.append(f"key <K{code}> {{ [ {levels} ] }};")
        keys[low] = (code, False)
        if high:
            keys[high] = (code, True)
        code += 1
    text = ("xkb_keymap {\n"
            f'xkb_keycodes "shotbox" {{ minimum = 8; maximum = {code}; '
            + " ".join(codes) + " };\n"
            'xkb_types "shotbox" { include "complete" };\n'
            'xkb_compat "shotbox" { include "complete" };\n'
            'xkb_symbols "shotbox" { ' + " ".join(symbols) + " };\n"
            "};\n")
    return text, blank, keys


def _string(s):
    data = s.encode() + b"\0"
    return struct.pack("<I", len(data)) + data + b"\0" * (-len(data) % 4)


class Display:
    """A connection to the compositor in `env` (XDG_RUNTIME_DIR and
    WAYLAND_DISPLAY), with a keyboard and a pointer of its own. The screen's
    size, for the pointer, comes from sway's IPC ($SWAYSOCK)."""

    def __init__(self, env=None):
        env = os.environ if env is None else env
        path = os.path.join(env.get("XDG_RUNTIME_DIR", ""), env.get("WAYLAND_DISPLAY", ""))
        self.sock = socket.socket(socket.AF_UNIX)
        try:
            self.sock.connect(path)
            self._setup(env)
        except OSError as e:
            self.sock.close()
            raise XError(f"can't reach the Wayland compositor at {path}: {e.strerror or e}")
        except BaseException:
            self.sock.close()
            raise

    def _setup(self, env):
        self.buf = b""
        self.next_id = 2
        self.waiting, self.done = set(), set()
        self.globals = {}
        self.registry = self._new()
        self._send(1, 1, struct.pack("<I", self.registry))    # wl_display.get_registry
        self.sync()
        missing = [i for i in ("wl_seat", "zwp_virtual_keyboard_manager_v1",
                               "zwlr_virtual_pointer_manager_v1") if i not in self.globals]
        if missing:
            raise XError("the compositor has no " + ", ".join(missing))
        seat = self._bind("wl_seat", 1)
        kbd_manager = self._bind("zwp_virtual_keyboard_manager_v1", 1)
        ptr_manager = self._bind("zwlr_virtual_pointer_manager_v1", 1)
        self.keyboard = self._new()
        self._send(kbd_manager, 0, struct.pack("<II", seat, self.keyboard))
        self.pointer = self._new()
        self._send(ptr_manager, 0, struct.pack("<II", seat, self.pointer))

        text, self.blank, self.keys = keymap()
        data = text.encode() + b"\0"
        fd = os.memfd_create("shotbox-keymap")
        try:
            os.write(fd, data)
            self._send(self.keyboard, 0, struct.pack("<II", KEYMAP_XKB_V1, len(data)), fd)
        finally:
            os.close(fd)
        # The first key goes to a program along with the keymap; let it be
        # one that means nothing, and give the program a moment with it.
        self._key(self.blank)
        self.sync()
        time.sleep(0.05)
        self.width, self.height = self._size(env)
        self.held = {}          # keysym: its modifier bits, while `down`

    # --- the wire -------------------------------------------------------------

    def _new(self):
        self.next_id += 1
        return self.next_id - 1

    def _send(self, obj, opcode, args=b"", fd=None):
        msg = struct.pack("<II", obj, ((8 + len(args)) << 16) | opcode) + args
        if fd is None:
            self.sock.sendall(msg)
        else:
            socket.send_fds(self.sock, [msg], [fd])

    def _bind(self, interface, version):
        name, _ = self.globals[interface]
        new = self._new()
        self._send(self.registry, 0, struct.pack("<I", name) + _string(interface)
                   + struct.pack("<II", version, new))
        return new

    def _event(self):
        while len(self.buf) < 8:
            got = self.sock.recv(4096)
            if not got:
                raise XError("the compositor closed the connection")
            self.buf += got
        obj, word = struct.unpack_from("<II", self.buf)
        size, opcode = word >> 16, word & 0xFFFF
        while len(self.buf) < size:
            got = self.sock.recv(4096)
            if not got:
                raise XError("the compositor closed the connection")
            self.buf += got
        body, self.buf = self.buf[8:size], self.buf[size:]
        if obj == 1 and opcode == 0:                          # wl_display.error
            _, code, n = struct.unpack_from("<III", body)
            raise XError(f"Wayland error {code}: {body[12:12 + n - 1].decode(errors='replace')}")
        if obj == self.registry and opcode == 0:              # wl_registry.global
            name, n = struct.unpack_from("<II", body)
            interface = body[8:8 + n - 1].decode()
            version, = struct.unpack_from("<I", body, 8 + n + (-n % 4))
            self.globals[interface] = (name, version)
        elif opcode == 0 and obj in self.waiting:             # wl_callback.done
            self.waiting.discard(obj)
            self.done.add(obj)

    def sync(self):
        """Wait until the compositor has handled everything sent so far."""
        callback = self._new()
        self.waiting.add(callback)
        self._send(1, 0, struct.pack("<I", callback))         # wl_display.sync
        while callback not in self.done:
            self._event()
        self.done.discard(callback)

    def _size(self, env):
        out = wl.ipc(env, wl.GET_OUTPUTS)[0]["rect"]
        return out["width"], out["height"]

    # --- the keyboard ---------------------------------------------------------

    @staticmethod
    def _ms():
        return int(time.monotonic() * 1000) & 0xFFFFFFFF

    def _key(self, code, state=None):
        for s in ((PRESSED, RELEASED) if state is None else (state,)):
            self._send(self.keyboard, 1, struct.pack("<III", self._ms(), code - 8, s))

    def _mods(self, mask):
        self._send(self.keyboard, 2, struct.pack("<IIII", mask, 0, 0, 0))

    def _code(self, keysym, modifiers):
        """The key's keycode, and the modifier bits to hold with it: Shift
        too if the key's symbol is on its shifted level."""
        if keysym not in self.keys:
            raise XError(f"no key for keysym 0x{keysym:x} in shotbox's keymap")
        code, shifted = self.keys[keysym]
        mask = MOD_BITS[SHIFT] if shifted else 0
        for m in modifiers:
            mask |= MOD_BITS[m]
        return code, mask

    def _held(self, keep):
        """The modifier bits held keys need, and the keycodes they hold: this
        keyboard's, and the chords in `keep`, held by other commands, whose
        keyboards a program doesn't tell apart from this one."""
        mask, codes = 0, set()
        for m in self.held.values():
            mask |= m
        for spec in keep:
            code, m = self._code(*parse_chord(spec))
            mask |= m
            codes.add(code)
        return mask, codes

    def press(self, keysym, modifiers=(), keep=()):
        """Press and release one key, holding the modifier keysyms down, and
        leaving down what the chords in `keep`, held, hold. A key they hold
        isn't pressed, as on X11: this keyboard would let go of it when its
        connection closes, though the other's still holds it."""
        code, mask = self._code(keysym, modifiers)
        held, codes = self._held(keep)
        if code in codes:
            return
        self._mods(held | mask)
        self._key(code)
        if mask:
            self._mods(held)
        self.sync()

    def down(self, keysym, modifiers=(), keep=()):
        """Press a key with its modifiers and leave it down, with the
        modifiers the chords in `keep` hold too. A virtual keyboard's keys go
        up when its connection closes, so on Wayland a key stays down only
        as long as this Display does."""
        code, mask = self._code(keysym, modifiers)
        self.held[keysym] = mask
        self._mods(self._held(keep)[0])
        self._key(code, PRESSED)
        self.sync()

    def up(self, keysym, modifiers=(), keep=()):
        """Let go of a key pressed with `down`, and its modifiers, but for
        what the chords in `keep` hold."""
        code, _ = self._code(keysym, modifiers)
        self.held.pop(keysym, None)
        held, codes = self._held(keep)
        if code not in codes:
            self._key(code, RELEASED)
        self._mods(held)
        self.sync()

    def chord_keys(self, spec):
        """What a chord presses, however it's spelled: its keycode and
        modifier bits; an XError if shotbox's keymap can't press it."""
        return self._code(*parse_chord(spec))

    def chord(self, spec, state=None, keep=()):
        """Press and release a chord: `ctrl+comma`, `Return`, `alt+shift+Tab`;
        or with `state` "down" or "up", only press it or only let it go
        (leaving down what the chords in `keep` hold)."""
        sym, mods = parse_chord(spec)
        if state == "down":
            self.down(sym, mods, keep)
        else:
            (self.up if state == "up" else self.press)(sym, mods, keep)

    def type(self, text, keep=()):
        """Type text, one key at a time. ASCII only, as on X11. What the
        chords in `keep` hold stays down."""
        for ch in text:
            self.press({"\n": 0xFF0D, "\r": 0xFF0D, "\t": 0xFF09}.get(ch, ord(ch)), keep=keep)

    # --- the pointer ----------------------------------------------------------

    def _frame(self):
        self._send(self.pointer, 4)

    def move(self, x, y):
        # Absolute motion is unsigned: off the screen is its edge, as on X11.
        x = min(max(x, 0), self.width - 1)
        y = min(max(y, 0), self.height - 1)
        self._send(self.pointer, 1, struct.pack("<IIIII", self._ms(), x, y,
                                                self.width, self.height))
        self._frame()
        self.sync()

    def _button(self, button, state):
        self._send(self.pointer, 2, struct.pack("<III", self._ms(), BUTTONS[button], state))
        self._frame()

    def click(self, x, y, button=1, count=1):
        self.move(x, y)
        for _ in range(count):
            self._button(button, PRESSED)
            self._button(button, RELEASED)
        self.sync()

    def button(self, x, y, button=1, state="down"):
        """Move to x, y and press a button, leaving it down, or let it go."""
        self.move(x, y)
        self._button(button, PRESSED if state == "down" else RELEASED)
        self.sync()

    def drag(self, x1, y1, x2, y2, button=1, steps=10):
        """Press at one point, move to another in steps, release there."""
        self.move(x1, y1)
        self._button(button, PRESSED)
        self.sync()
        for i in range(1, steps + 1):
            self.move(x1 + (x2 - x1) * i // steps, y1 + (y2 - y1) * i // steps)
        self._button(button, RELEASED)
        self.sync()

    def close(self):
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
