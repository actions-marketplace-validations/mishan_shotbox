"""Pressing keys and moving the pointer in a Wayland session under sway:
what xtest.py does for X11.

A few Wayland requests spoken over the compositor's socket, and two
protocols wlroots compositors have for exactly this: a virtual keyboard
(zwp_virtual_keyboard_v1) and a virtual pointer (zwlr_virtual_pointer_v1).
No Python Wayland binding and no wtype: the standard library, with the
keymap passed as a file descriptor.

A session's keyboard and pointer are made once, by a server it starts
(serve), and last as long as it does: headless sway has neither of its
own, and while there's no keyboard GTK has no keyboard focus, so it says
nothing has the focus and draws no caret; while there's no pointer sway
takes it away from programs, and a program slow to take up a new one
misses its first button. Every command presses keys and works the
pointer through that server, so a key one leaves down stays down for the
next, and the pointer stays where the last command left it, as on X11.
A button is let go when the command that pressed it goes, however it
goes.

The keyboard's keymap holds every key shotbox can press, laid out as a US
keyboard is: a and A, 1 and !, Tab and back-tab on one key, the second
with Shift. So a shifted symbol is typed with Shift held, and Shift in a
chord gives the key's shifted symbol, as on X11; and the keymap never
changes. A program meets a new keyboard's keymap along with its first key,
and wtype, which makes a keyboard every time it runs, was seen to drop a
first key. So a new keyboard first presses a key with no symbol, then
waits for that to go through and a moment more. In testing no key went
missing with or without that; it's cheap to keep.
"""

import json
import os
from functools import cached_property
import selectors
import socket
import struct
import threading
import time

from . import wl
from .pad import write_ready
from .xtest import KEYSYMS, SHIFT, XError, parse_chord

# Real modifier bits, as the keymap below maps them.
MOD_BITS = {0xFFE1: 0x01, 0xFFE3: 0x04, 0xFFE9: 0x08, 0xFFE7: 0x08, 0xFFEB: 0x40}
# Linux's input event codes for pointer buttons 1, 2 and 3.
BUTTONS = {1: 0x110, 2: 0x112, 3: 0x111}
PRESSED, RELEASED = 1, 0
KEYMAP_XKB_V1 = 1
KEYBOARD = "keyboard.sock"      # the server's, in the session's scratch dir

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
    keys = {sym: (c, False) for sym, c in mods.items()}
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
    WAYLAND_DISPLAY), working the keyboard and pointer of the session's
    server (its socket in SHOTBOX_SCRATCH); with no server, or with `own`,
    as the server's own, ones of its own. The screen's size, for the
    pointer, comes from sway's IPC ($SWAYSOCK)."""

    def __init__(self, env=None, own=False):
        env = os.environ if env is None else env
        path = os.path.join(env.get("XDG_RUNTIME_DIR", ""), env.get("WAYLAND_DISPLAY", ""))
        self.sock = socket.socket(socket.AF_UNIX)
        try:
            self.sock.connect(path)
            self._setup(env, own)
        except OSError as e:
            self.sock.close()
            raise XError(f"can't reach the Wayland compositor at {path}: {e.strerror or e}")
        except BaseException:
            self.sock.close()
            raise

    def _setup(self, env, own):
        self.buf = b""
        self.next_id = 2
        self.waiting, self.done = set(), set()
        self.globals = {}
        self.registry = self._new()
        self._send(1, 1, struct.pack("<I", self.registry))    # wl_display.get_registry
        self.sync()
        server = env.get("SHOTBOX_SCRATCH") and os.path.join(env["SHOTBOX_SCRATCH"], KEYBOARD)
        self.shared = server if not own and server and os.path.exists(server) else None
        missing = [i for i in ("wl_seat", "zwp_virtual_keyboard_manager_v1",
                               "zwlr_virtual_pointer_manager_v1")
                   if i not in self.globals and not (self.shared and i != "wl_seat")]
        if missing:
            raise XError("the compositor has no " + ", ".join(missing))
        self.env = env
        self._conn, self._lock = None, threading.Lock()
        self.seat = self._bind("wl_seat", 1)
        text, self.blank, self.keys = keymap()
        self.held = {}          # keysym: its modifier bits, while `down`
        if self.shared:
            return
        kbd_manager = self._bind("zwp_virtual_keyboard_manager_v1", 1)
        self.keyboard = self._new()
        self._send(kbd_manager, 0, struct.pack("<II", self.seat, self.keyboard))
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
        """The key's keycode, and the modifier bits to hold with it: its
        own, for a modifier, and Shift too if the key's symbol is on its
        shifted level."""
        if keysym not in self.keys:
            raise XError(f"no key for keysym 0x{keysym:x} in shotbox's keymap")
        code, shifted = self.keys[keysym]
        mask = MOD_BITS.get(keysym, 0) | (MOD_BITS[SHIFT] if shifted else 0)
        for m in modifiers:
            mask |= MOD_BITS[m]
        return code, mask

    def _held(self, keep):
        """The modifier bits held keys need, and the keycodes they hold: this
        keyboard's, and the chords in `keep`, held by other commands."""
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
        isn't pressed, as on X11: letting go of it would end their hold."""
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
        up when its connection closes: the session's server's, when the
        session stops."""
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
        if self.shared:
            return self._ask("chord", spec, state, list(keep))
        sym, mods = parse_chord(spec)
        if state == "down":
            self.down(sym, mods, keep)
        else:
            (self.up if state == "up" else self.press)(sym, mods, keep)

    def type(self, text, keep=()):
        """Type text, one key at a time. ASCII only, as on X11. What the
        chords in `keep` hold stays down."""
        if self.shared:
            return self._ask("type", text, list(keep))
        for ch in text:
            self.press({"\n": 0xFF0D, "\r": 0xFF0D, "\t": 0xFF09}.get(ch, ord(ch)), keep=keep)

    def _connect(self):
        """A connection to the session's server. Its backlog may be full,
        with many commands starting at once: then it's tried again."""
        end = time.monotonic() + 30
        while True:
            s = socket.socket(socket.AF_UNIX)
            s.settimeout(30)
            try:
                s.connect(self.shared)
                return s, s.makefile("rb")
            except BlockingIOError:
                s.close()
                if time.monotonic() > end:
                    raise
                time.sleep(0.01)
            except OSError:
                s.close()
                raise

    def _hang_up(self):
        if self._conn:
            for f in reversed(self._conn):
                f.close()
        self._conn = None

    def _ask(self, *call):
        """Have the session's server make `call`, over a connection kept
        while this lasts, so a button pressed over it is let go when it
        closes; one that fails is made again for the next call. Threads
        take turns."""
        with self._lock:
            try:
                if not self._conn:
                    self._conn = self._connect()
                s, replies = self._conn
                s.sendall(json.dumps(call).encode() + b"\n")
                reply = json.loads(replies.readline() or b"null")
            except TimeoutError:
                self._hang_up()
                raise XError("the session's keyboard and pointer didn't answer in time: "
                             "busy, or stuck")
            except (OSError, ValueError) as e:
                self._hang_up()
                raise XError("the session's keyboard and pointer have gone "
                             f"({getattr(e, 'strerror', None) or e})")
            if not isinstance(reply, dict):
                self._hang_up()
                raise XError("the session's keyboard and pointer have gone (no answer)")
        if "error" in reply:
            raise XError(reply["error"])

    # --- the pointer ----------------------------------------------------------

    # Made, and the screen's size asked of sway, only once it points.
    @cached_property
    def pointer(self):
        manager = self._bind("zwlr_virtual_pointer_manager_v1", 1)
        pointer = self._new()
        self._send(manager, 0, struct.pack("<II", self.seat, pointer))
        return pointer

    @cached_property
    def _size(self):
        return wl.root_size(self.env)

    @property
    def width(self):
        return self._size[0]

    @property
    def height(self):
        return self._size[1]

    def _frame(self):
        self._send(self.pointer, 4)

    def move(self, x, y):
        if self.shared:
            return self._ask("move", x, y)
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
        if self.shared:
            return self._ask("click", x, y, button, count)
        self.move(x, y)
        for _ in range(count):
            self._button(button, PRESSED)
            self._button(button, RELEASED)
        self.sync()

    def button(self, x, y, button=1, state="down"):
        """Move to x, y and press a button, leaving it down, or let it go."""
        if self.shared:
            return self._ask("button", x, y, button, state)
        self.move(x, y)
        self._button(button, PRESSED if state == "down" else RELEASED)
        self.sync()

    def scroll(self, x, y, dy, dx=0):
        """Move to x, y and turn the wheel dy clicks down (up if negative)
        and dx right (left), a frame a click. The virtual pointer has only
        axis_discrete for a click; wlroots passes it on as value120 to a
        program that takes that. 15 is a click in degrees, as libinput
        gives a mouse's."""
        if self.shared:
            return self._ask("scroll", x, y, dy, dx)
        self.move(x, y)
        for axis, n in ((0, dy), (1, dx)):
            step = 1 if n > 0 else -1
            for _ in range(abs(n)):
                self._send(self.pointer, 7, struct.pack("<IIii", self._ms(), axis,
                                                        step * 15 * 256, step))
                self._frame()
        self.sync()

    def drag(self, x1, y1, x2, y2, button=1, steps=10):
        """Press at one point, move to another in steps, release there."""
        if self.shared:
            return self._ask("drag", x1, y1, x2, y2, button, steps)
        self.move(x1, y1)
        self._button(button, PRESSED)
        self.sync()
        for i in range(1, steps + 1):
            self.move(x1 + (x2 - x1) * i // steps, y1 + (y2 - y1) * i // steps)
        self._button(button, RELEASED)
        self.sync()

    def close(self):
        with self._lock:
            self._hang_up()
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def serve(sock_path, ready):
    """The session's keyboard and pointer: make them, the pointer in the
    screen's bottom-right corner, where `park` puts it and fewer windows
    reach than X's start in the middle, write to `ready` that they're
    there (or why not), then do what's asked on the Unix socket at
    `sock_path`, a JSON line each, a Display method's name and its
    arguments, answering each in turn, until stopped. The buttons a
    connection pressed and didn't let go of go up when it closes."""
    try:
        d = Display(own=True)
        d.move(d.width - 1, d.height - 1)
    except (XError, RuntimeError, LookupError) as e:
        write_ready(ready, {"error": str(e)})
        return 1
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(sock_path)
    srv.listen(socket.SOMAXCONN)
    write_ready(ready, [])
    calls = {name: getattr(d, name)
             for name in ("chord", "type", "move", "click", "button", "scroll", "drag")}
    sel = selectors.DefaultSelector()
    sel.register(srv, selectors.EVENT_READ)
    clients = {}        # connection: [what's come of a line, the buttons it holds down]

    def hang_up(conn):
        """Let go of a connection, and of the buttons it pressed and didn't."""
        sel.unregister(conn)
        conn.close()
        try:
            for button in clients.pop(conn)[1]:
                d._button(button, RELEASED)
            d.sync()
        except (XError, OSError):
            pass

    while True:
        for key, _ in sel.select():
            conn = key.fileobj
            if conn is srv:
                conn, _ = srv.accept()
                # Answers that don't fit are dropped with the connection: one
                # that doesn't read them can't hold the server up.
                conn.setblocking(False)
                clients[conn] = [b"", set()]
                sel.register(conn, selectors.EVENT_READ)
                continue
            try:
                got = conn.recv(65536)
            except BlockingIOError:
                continue
            except OSError:
                got = b""
            if not got:
                hang_up(conn)
                continue
            held = clients[conn][1]
            *lines, clients[conn][0] = (clients[conn][0] + got).split(b"\n")
            for line in lines:
                try:
                    call, *args = json.loads(line)
                    calls[call](*args)
                    if call == "button":
                        (held.add if args[3] == "down" else held.discard)(args[2])
                    reply = {}
                except Exception as e:      # whatever a request is, the server stays
                    reply = {"error": str(e) if isinstance(e, XError) else f"bad request: {e}"}
                try:
                    conn.sendall(json.dumps(reply).encode() + b"\n")
                except OSError:
                    hang_up(conn)
                    break
