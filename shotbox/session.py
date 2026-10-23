"""A sealed session: a private display, a private D-Bus, a scratch home.

Everything a screenshot needs to be reproducible and harmless. Nothing run
inside can reach your desktop: DISPLAY, WAYLAND_DISPLAY and the session bus
are gone, the environment is rebuilt from an allowlist rather than scrubbed
from a denylist, and HOME and every XDG directory point into a scratch dir
that is removed afterwards. Settings are the session's own and portals are
off, so apps come up with their own defaults instead of yours.

    with Session(size=(1280, 800)) as s:
        s.spawn(["gnome-text-editor"])
        s.wait_window("Text Editor")
        s.capture("editor.png", window="Text Editor")

A Session is a Screen (screen.py) once it's entered, so it waits, drives
the display and takes pictures.

The pieces, and why each one:

- Xvfb on a display number of our own, with an Xauthority cookie, started
  with -displayfd so we know it's listening instead of guessing with sleep.
- Or, with wayland=True, a headless sway, drawing in software, its sockets
  in the session's own XDG_RUNTIME_DIR, and with xwayland=True an Xwayland
  in it for X clients. See docs/wayland.md.
- dbus-daemon with a config of our own. By default its service directory is
  empty, so nothing gets activated on it behind your back (gvfs, dconf, the
  portals); desktop=True uses the system's service directories instead, for
  things like GNOME Shell that need dconf and friends.
- A stand-in system bus, and no printers, so nothing reaches the
  machine's services (NetworkManager, logind, CUPS...); machine_bus=True
  gives programs the machine's.
- Optionally, with a11y=True, an accessibility (AT-SPI) bus of its own,
  started by at-spi-bus-launcher on the private session bus, so the
  program's accessible tree can be read (a11y.py) and nothing of it
  reaches your desktop's screen reader.
- Optionally, with audio=True, a PipeWire of its own whose one sink is a
  null sink, speaking the pulse protocol too: sound goes nowhere, and a
  player has something to play to.
- Optionally, with clock=TIME, a wall clock that starts at TIME and runs
  on, for every program, by libfaketime.
- Every process in its own group, and the group killed on the way out:
  signalling a wrapper script alone leaves its children running.
"""

import glob
import json
import grp
import math
import os
import pwd
import re
import secrets
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import sysconfig
import tempfile
import time
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

from . import kept, pad, usage, wl, wlinput, xtest
from .x import CONVERT
from .screen import Screen, SessionError, wait_for  # noqa: F401 (wait_for, for callers)

# Passed through from the caller unless told otherwise. Everything else is
# rebuilt: nothing from your session leaks in by accident.
# SHOTBOX_SANDBOX, so one set for a session holds for a session started in it.
PASS = ("PATH", "USER", "LOGNAME", "SHELL", "TERM", "SHOTBOX_SANDBOX")

# Deterministic GTK: X11 on Xvfb, the software renderer, no portals, no
# accessibility bus, settings in the scratch home's keyfile, nothing mounted.
SEALED_ENV = {
    "LANG": "C.UTF-8",
    "TZ": "UTC",
    "GDK_BACKEND": "x11",
    "GSK_RENDERER": "cairo",
    # Cairo needs no GL, but GTK 4 opens EGL anyway, and Mesa warns on
    # Xvfb that it has no DRI3.
    "GDK_DISABLE": "gl",
    "ADW_DISABLE_PORTAL": "1",
    "GDK_DEBUG": "no-portals",
    "GTK_A11Y": "none",
    "NO_AT_BRIDGE": "1",
    "GIO_USE_VFS": "local",
    "GSETTINGS_BACKEND": "keyfile",
}

BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>{type}</type>
  <keep_umask/>
  <listen>{listen}</listen>
  <auth>EXTERNAL</auth>
  {services}
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


# sway, headless: one output the size of the screen, no borders or gaps,
# and every window floating at 0,0 at the size it asks for, the way an X
# client comes up on Xvfb with no window manager. No swaynag: a complaint
# about the config would be drawn into the pictures.
SWAY_CONFIG = """output * resolution {width}x{height} position 0 0
default_border none
default_floating_border none
focus_follows_mouse no
swaynag_command -
for_window [all] floating enable, move position 0 0
xwayland {xwayland}
seat * xcursor_theme {cursors}
"""


# The cursor theme of a Wayland session, sway's and every program's: one
# cursor, 1x1 and clear, under every name a program asks for, so the
# pointer, which stays where it was left, isn't in a picture, as on X11 it
# isn't. sway 1.9 puts the cursor in what grim takes. The names are the
# freedesktop cursor spec's, the X cursor font's and the older ones GTK,
# Chromium and X clients fall back to.
BLANK_CURSOR = (b"Xcur" + struct.pack("<6I", 16, 0x10000, 1, 0xFFFD0002, 24, 28)
                + struct.pack("<9I", 36, 0xFFFD0002, 24, 1, 1, 1, 0, 0, 0) + bytes(4))
CURSOR_THEME = "shotbox-blank"
CURSOR_NAMES = """
default context-menu help pointer progress wait cell crosshair text vertical-text alias
copy move no-drop not-allowed grab grabbing all-scroll all-resize col-resize row-resize
n-resize e-resize s-resize w-resize ne-resize nw-resize se-resize sw-resize ew-resize
ns-resize nesw-resize nwse-resize zoom-in zoom-out dnd-ask dnd-move dnd-copy dnd-link
dnd-none dnd-no-drop X_cursor arrow based_arrow_down based_arrow_up boat bogosity
bottom_left_corner bottom_right_corner bottom_side bottom_tee box_spiral center_ptr circle
clock coffee_mug cross cross_reverse diamond_cross dot dotbox double_arrow draft_large
draft_small draped_box exchange fleur gobbler gumby hand1 hand2 heart icon iron_cross
left_ptr left_side left_tee leftbutton ll_angle lr_angle man middlebutton mouse pencil
pirate plus question_arrow right_ptr right_side right_tee rightbutton rtl_logo sailboat
sb_down_arrow sb_h_double_arrow sb_left_arrow sb_right_arrow sb_up_arrow sb_v_double_arrow
shuttle sizing spider spraycan star target tcross top_left_arrow top_left_corner
top_right_corner top_side top_tee trek ul_angle umbrella ur_angle watch xterm
left_ptr_watch h_double_arrow v_double_arrow fd_double_arrow bd_double_arrow hand
pointing_hand openhand closedhand ibeam size_all size_hor size_ver size_bdiag size_fdiag
split_h split_v whats_this forbidden crossed_circle half-busy
""".split()


GTK_SETTINGS = """[Settings]
gtk-cursor-blink = false
gtk-enable-animations = false
"""


# The whole of PipeWire's config, not a fragment on the system's: that loads
# the ALSA monitor. One sink, a null one, which nothing else could be
# routed to, and drivers to run it with no device's clock.
PIPEWIRE_CONFIG = """context.properties = {
    core.daemon = true
    core.name = pipewire-0
    support.dbus = false
}
context.spa-libs = {
    audio.convert.* = audioconvert/libspa-audioconvert
    support.* = support/libspa-support
}
context.modules = [
    { name = libpipewire-module-protocol-native }
    { name = libpipewire-module-metadata }
    { name = libpipewire-module-spa-node-factory }
    { name = libpipewire-module-client-node }
    { name = libpipewire-module-access }
    { name = libpipewire-module-adapter }
    { name = libpipewire-module-link-factory }
    { name = libpipewire-module-protocol-pulse }
]
pulse.properties = {
    server.address = [ "unix:native" ]
}
context.objects = [
    { factory = metadata
        args = {
            metadata.name = default
            metadata.values = [
                { key = default.audio.sink value = { name = shotbox-sink } }
                { key = default.audio.source value = { name = shotbox-sink } }
            ]
        }
    }
    { factory = spa-node-factory
        args = {
            factory.name = support.node.driver
            node.name = Dummy-Driver
            node.group = pipewire.dummy
            priority.driver = 20000
        }
    }
    { factory = spa-node-factory
        args = {
            factory.name = support.node.driver
            node.name = Freewheel-Driver
            priority.driver = 19000
            node.group = pipewire.freewheel
            node.freewheel = true
        }
    }
    { factory = adapter
        args = {
            factory.name = support.null-audio-sink
            node.name = shotbox-sink
            node.description = "shotbox's null sink"
            media.class = Audio/Sink
            audio.position = [ FL FR ]
            object.linger = true
        }
    }
]
"""

# WirePlumber links streams to the sink: a stream nothing links is never
# driven, and a player waits on it forever. It's to look at no devices.
# From 0.5, a profile: "policy" has no device monitors; stateless, so it
# saves no defaults.
WIREPLUMBER_CONFIG = """wireplumber.profiles = {
    shotbox = {
        inherits = [ policy, mixin.systemwide-session, mixin.stateless ]
        support.dbus = disabled
        support.mpris = disabled
    }
}
"""

# Before 0.5 (Ubuntu 24.04's 0.4.17), Lua: after the system's 50-*-config
# fragments and before its 90-enable-all, which starts each monitor not
# set off by then.
WIREPLUMBER_LUA = {
    "main.lua.d": """alsa_monitor.enabled = false
v4l2_monitor.enabled = false
libcamera_monitor.enabled = false
""",
    "bluetooth.lua.d": "bluez_monitor.enabled = false\n",
}

# Where distributions put at-spi2-core's bus launcher.
A11Y_LAUNCHERS = ("/usr/libexec/at-spi-bus-launcher", "/usr/lib/at-spi2-core/at-spi-bus-launcher",
                  "/usr/lib/at-spi2/at-spi-bus-launcher")


def gtk4_minor():
    """The system's GTK 4 minor version, or None: asked of the library in a
    process of its own, so GTK isn't loaded into this one."""
    r = subprocess.run([sys.executable, "-c", "import ctypes; print(ctypes.CDLL("
                        "'libgtk-4.so.1').gtk_get_minor_version())"],
                       capture_output=True, text=True)
    return int(r.stdout) if r.returncode == 0 else None


def presets(toolkit, dark, high_contrast, text_scale, minor=None):
    """settings.ini keys for the display presets, for GTK 3 or 4 (GTK 4.minor).
    GTK 4.20 took over the interface's color scheme and contrast, and older
    GTK 4 warns of keys it doesn't know, so it gets its dark variant and no
    high contrast; GTK 3 takes a dark variant and its HighContrast theme.
    Text scaling is the font DPI, in 1024ths, which GTK 4 on Wayland takes
    from here."""
    keys = {}
    new = toolkit == "gtk-4.0" and (minor or 0) >= 20
    if dark and new:
        keys["gtk-interface-color-scheme"] = "dark"
    elif dark:
        keys["gtk-application-prefer-dark-theme"] = "true"
    if high_contrast and new:
        keys["gtk-interface-contrast"] = "more"
    elif high_contrast and toolkit == "gtk-3.0":
        keys["gtk-theme-name"] = "HighContrast"
    if text_scale:
        keys["gtk-xft-dpi"] = str(round(96 * 1024 * text_scale))
    return keys


def settings(text):
    """The keys `text`, a settings.ini, sets in its [Settings] group, the
    one GTK reads, with their values."""
    group, keys = None, {}
    for line in text.splitlines():
        bare = line.strip()
        if bare.startswith("["):
            group = bare
        elif group == "[Settings]" and "=" in bare and not bare.startswith(("#", ";")):
            key, value = bare.split("=", 1)
            keys[key.strip()] = value.strip()
    return keys


def merge(text, keys, into="[Settings]"):
    """`text`, a settings.ini (or GLib keyfile), with `keys` set in its
    `into` group, and its comments and everything else as they were."""
    kept, group, at = [], None, None
    for line in text.splitlines():
        bare = line.strip()
        if bare.startswith("["):
            group = bare
            if bare == into and at is None:
                kept.append(line)
                at = len(kept)
                continue
        elif group == into and bare.split("=", 1)[0].strip() in keys:
            continue
        kept.append(line)
    if at is None:
        kept.append(into)
        at = len(kept)
    kept[at:at] = [f"{k} = {v}" for k, v in keys.items()]
    return "\n".join(kept) + "\n"


def unfold(home, path):
    """Make each step from `home` down to `path` the session's own rather
    than a link out of it, so writing `path` can't reach what a seed's
    links (stow's dotfiles, say) point at: a linked directory becomes a
    real one of links to what's in it, a linked file a copy."""
    p = home
    for part in path.relative_to(home).parts:
        p = p / part
        if not p.is_symlink():
            continue
        target = p.resolve()
        p.unlink()
        if target.is_dir():
            p.mkdir()
            for child in target.iterdir():
                (p / child.name).symlink_to(child)
        elif target.exists():
            shutil.copyfile(target, p)


# The package each tool is in, on Debian, where it isn't named for it.
PACKAGES = {"Xvfb": "xvfb", "Xwayland": "xwayland", "pipewire": "pipewire-bin",
            "pw-dump": "pipewire-bin", "bwrap": "bubblewrap", "gdbus": "libglib2.0-bin",
            "nsenter": "util-linux", "xwininfo": "x11-utils", "xprop": "x11-utils",
            "import": "imagemagick", "magick": "imagemagick", "convert": "imagemagick"}


def lacks(*tools):
    """Which of these tools aren't on PATH, and the package each is in;
    nothing if they all are."""
    missing = [t + (f" ({PACKAGES[t]})" if t in PACKAGES else "")
               for t in tools if not shutil.which(t)]
    return "not found: " + ", ".join(missing) if missing else None


def need(*tools):
    why = lacks(*tools)
    if why:
        raise SessionError(why)


REINSTALL = "pipx install --force --system-site-packages shotbox"
ATSPI = ("PyGObject and AT-SPI", ("they're in python3-gi and gir1.2-atspi-2.0 on Debian, and "
         "python3-gobject, at-spi2-core and gobject-introspection on Fedora"), ("Atspi", "2.0"))
VTE = ("PyGObject and VTE", ("they're in python3-gi and gir1.2-vte-2.91 on Debian, and "
       "python3-gobject and vte291 on Fedora"), ("Vte", "2.91"))


def bindings(what, where, *libs):
    """Why this Python can't load GObject's `libs`, (namespace, version)
    pairs, which are `what`: `where` they are, or, from a virtualenv that
    can't see the system's packages (as plain `pipx install` makes), how
    to make one that can. Nothing if it can."""
    try:
        import gi
    except ImportError as e:
        cfg = Path(sys.prefix, "pyvenv.cfg")
        if cfg.exists() and re.search(r"include-system-site-packages\s*=\s*false",
                                      cfg.read_text()):
            if "pipx/venvs/" in Path(sys.prefix).as_posix():
                return (f"needs {what} from the system, which this install can't see; "
                        f"reinstall with: {REINSTALL}")
            # uv's tools and uvx's have no way to see them: pipx has.
            if re.search(r"/uv/(tools|archive-v\d+)/", Path(sys.prefix).as_posix() + "/"):
                return (f"needs {what} from the system, which uv's tool environments can't "
                        "see; install with: pipx install --system-site-packages shotbox")
            return (f"needs {what} from the system, which this virtualenv can't see: "
                    "make it with --system-site-packages")
        return f"needs {what}, which this Python can't load ({e}): {where}"
    try:
        for lib, version in libs:
            gi.require_version(lib, version)
    except ValueError as e:
        return f"needs {what}, which this Python can't load ({e}): {where}"
    return None


@cache
def pango_cairo():
    """Why text can't be drawn here to look for, as `a11y contrast` does
    for GTK 4.14 and later's, or None."""
    where = "they're in python3-gi-cairo and gir1.2-pango-1.0 on Debian"
    why = bindings("pycairo and PangoCairo", where, ("Pango", "1.0"), ("PangoCairo", "1.0"))
    if why:
        return why
    try:
        import cairo
        from gi.repository import PangoCairo
        PangoCairo.create_layout(cairo.Context(cairo.ImageSurface(cairo.FORMAT_A8, 1, 1)))
    except Exception as e:   # no pycairo, or PyGObject without its cairo part: a KeyError
        return f"needs pycairo and PangoCairo, which this Python can't use ({e!r}): {where}"
    return None


def a11y_needs():
    if not any(os.access(p, os.X_OK) for p in A11Y_LAUNCHERS):
        return "not found: at-spi-bus-launcher (at-spi2-core)"
    return lacks("gdbus") or bindings(*ATSPI)


def sandbox_needs(sandbox=("bwrap", "--dev-bind", "/", "/")):
    """Why bubblewrap can't make a sandbox here, `sandbox` its command;
    nothing if it can."""
    if not shutil.which("bwrap"):
        return "there's no bwrap (bubblewrap)"
    probe = subprocess.run([*sandbox, "--", "true"], capture_output=True, text=True)
    if probe.returncode:
        return ("it can't make one here (are unprivileged user namespaces allowed?): "
                + ("; ".join(filter(None, probe.stderr.strip().splitlines()))
                   or f"status {probe.returncode}"))
    return None


def nothing_to_hide():
    """Whether a sandbox would hide nothing here, as in a container: no
    sound, input, raw HID or USB device, no uinput, and nothing in
    /run/user/UID or XDG_RUNTIME_DIR, whoever's they are. What can't be
    looked in counts as something."""
    dev = Path("/dev")
    for p in [dev / "snd", dev / "input", dev / "bus/usb", dev / "uhid", dev / "uinput",
              *dev.glob("hidraw*"), *dev.glob("usb/hiddev*"), Path(f"/run/user/{os.getuid()}"),
              *[Path(d) for d in [os.environ.get("XDG_RUNTIME_DIR")] if d]]:
        try:
            if p.exists() and (not p.is_dir() or any(p.iterdir())):
                return False
        except OSError:
            return False
    return True


def sandbox_env():
    """Whether SHOTBOX_SANDBOX leaves sessions a sandbox. YAML reads an
    unquoted off as false, which a CI job's env then sets."""
    on = os.environ.get("SHOTBOX_SANDBOX", "on")
    if on.lower() not in ("on", "true", "1", "off", "false", "0"):
        raise SessionError(f"SHOTBOX_SANDBOX is on or off, not {on!r}")
    return on.lower() in ("on", "true", "1")


def sandbox_off():
    try:
        if not sandbox_env():
            return "off, by SHOTBOX_SANDBOX"
    except SessionError as e:
        return str(e)
    why = sandbox_needs()
    if why and nothing_to_hide():
        why += "; nothing here for it to hide, so sessions don't say so"
    return why


def gamepad_needs():
    try:
        os.close(pad.uinput())
    except pad.PadError as e:
        return str(e)
    return lacks("bwrap")


# Why each part of a session, and each command that needs more, can't be
# had here; nothing if it can. A session checks its parts before it starts
# any, so what's missing is said at once in a line, rather than as a part
# that dies with the reason in its log; `shotbox doctor` goes through them
# all.
NEEDS = {
    "sessions": lambda: lacks("dbus-daemon"),
    "X11": lambda: lacks("Xvfb", "xauth", "xwininfo", "xprop", "import"),
    "--wayland": lambda: lacks("sway", "grim", CONVERT[0]),
    "--xwayland": lambda: lacks("Xwayland", "xprop"),
    "--desktop": lambda: lacks("gdbus"),
    "--a11y": a11y_needs,
    "a11y speak": lambda: a11y_needs() or lacks("orca"),
    "a11y contrast on GTK 4": lambda: a11y_needs() or pango_cairo(),
    "--audio": lambda: lacks("pipewire", "wireplumber", "pw-dump"),
    "--gamepads": gamepad_needs,
    "--net": lambda: lacks("bwrap", "nsenter"),
    "sandbox": sandbox_off,
    "term": lambda: bindings(*VTE),
    "record": lambda: lacks("ffmpeg"),
    "record on Wayland": lambda: lacks("ffmpeg", "wf-recorder"),
    "--clock": lambda: None if faketime() else ("the session's clock needs libfaketime, "
                                                "and there's no libfaketime.so.1 here"),
}


def faketime():
    """libfaketime's library, wherever the distribution put it: this
    machine's multiarch dir, guessed where Python wasn't told it; or None."""
    arch = sysconfig.get_config_var("MULTIARCH") or os.uname().machine + "-*"
    for d in (f"/usr/lib/{arch}", "/usr/lib64", "/usr/lib", "/usr/local/lib"):
        for lib in sorted(glob.glob(f"{d}/faketime/libfaketime.so.1")):
            return lib


def unclocked(env):
    """Take the session's libfaketime out of `env`'s LD_PRELOAD (the loader
    splits it at colons and spaces), keeping it in SHOTBOX_FAKETIME for
    on_the_clock(); whether there was one."""
    preload = re.split(r"[:\s]+", env.get("LD_PRELOAD", ""))
    fake = [p for p in preload if os.path.basename(p).startswith("libfaketime")]
    if fake:
        env["LD_PRELOAD"] = ":".join(p for p in preload if p and p not in fake)
        env["SHOTBOX_FAKETIME"] = ":".join(fake)
    return bool(fake)


def off_the_clock():
    """Run this shotbox again without the session's libfaketime, if it's
    preloaded: a --clock session's clock is for its programs, and under
    libfaketime 0.9.10 the time.sleep of Python 3.11 and later fails, and
    every wait with it. Before 3.11, sleep isn't hurt."""
    if sys.version_info >= (3, 11) and unclocked(os.environ):
        os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])


def on_the_clock(env):
    """`env`, for a program started for the session, with the libfaketime
    off_the_clock() took off this process put back."""
    if not env.get("SHOTBOX_FAKETIME"):
        return env
    return dict(env, LD_PRELOAD=":".join(filter(None, (env["SHOTBOX_FAKETIME"],
                                                       env.get("LD_PRELOAD")))))


def free_display(skip=()):
    """The lowest display number with neither a lock nor a socket."""
    for n in range(99, 1000):
        if n in skip:
            continue
        if not (Path(f"/tmp/.X{n}-lock").exists()
                or Path(f"/tmp/.X11-unix/X{n}").exists()):
            return n
    raise SessionError("no free X display number")


def group_alive(pgid):
    """Whether anything in the process group is still running. Zombies
    don't count: where nothing reaps orphans (a container whose first
    process is ours), they stay in the group without being alive."""
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            # pid (comm) state ppid pgrp ...; comm can hold spaces and parens.
            state, _, pgrp = stat.read_text().rsplit(")", 1)[1].split()[:3]
        except (OSError, IndexError):
            continue
        if int(pgrp) == pgid and state != "Z":
            return True
    return False


def kill_group(proc, grace=2.0):
    """TERM the process's group, then KILL what's left of it. The group,
    whether or not the process itself is still there: a program that has
    exited can leave children running in it."""
    if proc is None:
        return
    pgid = proc.pid
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        end = time.monotonic() + grace
        while True:
            proc.poll()   # reaps the process itself, once it's gone
            if not group_alive(pgid) or time.monotonic() > end:
                break
            time.sleep(0.05)
        if not group_alive(pgid):
            break
    proc.wait()


def sandboxed_pid(fd, proc, timeout=5):
    """The pid bwrap reports on its --info-fd, `fd` (closed after), or
    bwrap's own if it says nothing in time."""
    with os.fdopen(fd, "rb") as f:
        end, data = time.monotonic() + timeout, b""
        while time.monotonic() < end:
            if not select.select([f], [], [], 0.05)[0]:
                continue
            chunk = os.read(f.fileno(), 4096)
            if not chunk:
                break
            data += chunk
    try:
        return json.loads(data)["child-pid"]
    except (ValueError, KeyError):
        return proc.pid


class Session(Screen):
    """A sealed session, and its display: everything a Screen does works on
    it once it's entered. `failed`, as for a Screen. With `display` off, it
    has none, as `shotbox exec`'s: what it runs is sealed the same, but
    nothing a Screen does to a display works on it."""

    def __init__(self, size=(1280, 800), desktop=False, machine_bus=False,
                 seed=None, keep=False, env=None, passthrough=(), failed=None,
                 wayland=False, xwayland=False, a11y=False, announce_log=None,
                 dark=False, high_contrast=False, text_scale=None, audio=False, gamepads=0,
                 sandbox=None, display=True, net=None, usage=False, identity=None,
                 clock=None, tz=None):
        self.size = size
        self.measure = usage
        self._sampler = self._slice = None
        self.used = None        # what usage() said as the session stopped
        self._up = False
        unsandboxed = "--no-sandbox"
        if sandbox is None:
            sandbox, unsandboxed = sandbox_env(), "SHOTBOX_SANDBOX=off"
        if net not in (None, "loopback"):
            raise SessionError(f"net is loopback or nothing, not {net!r}")
        if net and not sandbox:
            # Programs join its namespace as a user namespace's, where a
            # setuid helper can't work: what --no-sandbox is for.
            raise SessionError(f"--net loopback and {unsandboxed} don't go together: in the "
                               "session's network, setuid helpers can't work either")
        self.net = net
        try:
            zone = ZoneInfo(tz) if tz else timezone.utc
        except (KeyError, ValueError):
            raise SessionError(f"there's no time zone called {tz!r}")
        try:
            # Python reads a Z for UTC only from 3.11.
            t = clock if isinstance(clock, datetime) else clock and datetime.fromisoformat(
                re.sub(r"[Zz]$", "+00:00", clock))
        except ValueError:
            raise SessionError(f"the clock is an ISO 8601 time, like 2026-09-26T18:40:00, "
                               f"not {clock!r}")
        # In the zone, unless it names its own.
        self.clock = (t if t.tzinfo else t.replace(tzinfo=zone)).timestamp() if t else None
        if self.clock is not None and self.clock < 0:
            # The session's bus, for one, takes a time before it for no time.
            raise SessionError(f"the clock starts in 1970 or after, not {clock}")
        self._faketime = None
        self._time_env = {"TZ": tz} if tz else {}
        self._net_pid = None    # a process in the session's network namespace, with net
        m = re.fullmatch(r"([A-Za-z_][\w.-]{0,31})@([A-Za-z0-9][A-Za-z0-9.-]{0,63})",
                         identity or "", re.ASCII)
        if identity and not m:
            raise SessionError("identity is USER@HOST, of letters, digits, '.', '-' and '_', "
                               f"not {identity!r}")
        if identity and not sandbox:
            # Only bubblewrap can fake them: going on without, as for an
            # unsealed session, would put the real names in the very
            # pictures this is for.
            raise SessionError(f"--identity and {unsandboxed} don't go together: only "
                               "bubblewrap can fake who and where programs are")
        self.identity = m and m.groups()   # (user, host)
        self.display = display
        if not display and (wayland or xwayland or a11y or announce_log or dark or high_contrast
                            or text_scale or gamepads):
            raise SessionError("a session with no display has no Wayland, accessibility "
                               "bus, style, text scale or gamepads")
        self.sandbox = sandbox
        self._sandboxed = False
        self.unsealed = None    # what programs can reach that they shouldn't, to say
        if not 0 <= gamepads <= 4:
            raise SessionError(f"gamepads is 0 to 4, not {gamepads}")
        self.gamepads = gamepads
        self._pad_nodes = []    # the pads' /dev/input nodes, with gamepads
        self.a11y = a11y or bool(announce_log)
        self.audio = audio
        self.announce_log = announce_log
        if text_scale is not None and not text_scale > 0:
            raise SessionError(f"text_scale must be more than 0, not {text_scale}")
        self.dark, self.high_contrast, self.text_scale = dark, high_contrast, text_scale
        self.xwayland = xwayland
        self.use_wayland = wayland or xwayland
        self.desktop = desktop
        self.machine_bus = machine_bus
        self.seed = seed
        self.keep = keep
        self.extra_env = dict(env or {})
        self.passthrough = tuple(PASS) + tuple(passthrough)
        self.scratch = None
        self.env = None
        self.failed = failed
        self._x = self._xsettings = None
        self._blink = True
        self._procs = []
        self._watch = None

    # --- setup ----------------------------------------------------------------

    def __enter__(self):
        parts = ["sessions"]
        if self.display:
            parts.append("--wayland" if self.use_wayland else "X11")
        parts += [p for p, on in (("--xwayland", self.xwayland), ("--desktop", self.desktop),
                                  ("--a11y", self.a11y), ("--audio", self.audio),
                                  ("--gamepads", self.gamepads), ("--net", self.net),
                                  ("--clock", self.clock is not None)) if on]
        why = next(filter(None, (NEEDS[p]() for p in parts)), None)
        if why:
            raise SessionError(why)
        if self.clock is not None:
            # An offset, fixed as the session starts, so a program started
            # later reads the clock those before it do: a start-at time
            # (FAKETIME="@TIME") starts over in each program, and
            # FAKETIME_DONT_RESET carries it on only to a program's own
            # children, not to the next one the session starts. Monotonic
            # clocks are left be, so timeouts aren't thrown out by it, and
            # file times read as they are on disk. A fixed offset needs none
            # of the shared memory libfaketime would leave in /dev/shm for
            # each killed program.
            self._faketime = faketime()
            self._time_env.update(FAKETIME=f"{math.ceil(self.clock - time.time()):+d}",
                                  FAKETIME_DONT_FAKE_MONOTONIC="1", NO_FAKE_STAT="1",
                                  FAKETIME_DISABLE_SHM="1")
        # Sockets are made in the scratch dir, the longest sway's (run/
        # sway-ipc.UID.PID.sock), and Linux allows a socket path 107 bytes.
        tmp = tempfile.gettempdir()
        if len(os.fsencode(tmp)) > 59:
            raise SessionError(f"{tmp} is too long a path to make the session's sockets in "
                               f"({len(os.fsencode(tmp))} bytes, 59 at most): set TMPDIR to "
                               "a shorter one")
        self.scratch = Path(tempfile.mkdtemp(prefix="shotbox-"))
        try:
            kept.mark(self.scratch, **({"kept": True} if self.keep else {}))
            self._start_watchdog()
            if self.measure:
                self._start_usage()
            if self.identity:
                self._make_identity()
            if self.sandbox or self.gamepads:
                self._probe_sandbox()
            if self.net:
                self._start_net()
            self._make_home()
            # What the buses run what they activate with, until there's a
            # display and a bus to add: never the caller's.
            self.env = self._make_env({}, None, None)
            bus = self._start_bus("session")
            sysbus = None if self.machine_bus else self._start_bus("system")
            if not self.display:
                graphics = {}
            elif self.use_wayland:
                graphics = self._start_sway(bus, sysbus)
            else:
                display, xauth = self._start_x()
                graphics = {"DISPLAY": display, "XAUTHORITY": str(xauth)}
            self.env = self._make_env(graphics, bus, sysbus)
            if self.use_wayland:
                # A keyboard and pointer for the whole session (see wlinput.py).
                self._serve("the keyboard and pointer", "wlinput", self.env, wlinput.KEYBOARD)
            if not self._blink and "DISPLAY" in self.env and not self.desktop:
                self._xsettings = xtest.Display(self.env)
                self._xsettings.own_settings({"Net/CursorBlink": 0})
            if self.desktop:
                self._activation_env()
            if self.text_scale and "DISPLAY" in self.env:
                # X clients take the font DPI from the X server's
                # resources over settings.ini; only GTK 4 on Wayland reads
                # it there.
                dpi = round(96 * self.text_scale)
                subprocess.run(["xprop", "-root", "-f", "RESOURCE_MANAGER", "8s", "-set",
                                "RESOURCE_MANAGER", f"Xft.dpi:\t{dpi}\n"],
                               env=self.env, check=True)
            if self.a11y:
                self._start_a11y()
            if self.audio:
                self._start_audio()
            if self.gamepads:
                self._start_pads()
            if self._sampler:
                for proc in self._procs:
                    self._sampler.add("session", proc.pid)
            self._up = True
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def _start_watchdog(self):
        """A process of its own that, if this one goes without stopping the
        session (killed outright, say), stops what's left of it and, but with
        keep, removes the scratch dir, as `shotbox mcp`'s watchdog does for its server's."""
        from .__main__ import self_command
        from .mcp import MARKS
        r, self._watch = os.pipe()
        # Not marked as a session's itself, as it would be started in one,
        # so that one's watchdog leaves it to clean up after this one.
        env = {k: v for k, v in os.environ.items() if os.fsencode(k) not in MARKS}
        subprocess.Popen([*self_command(), "mcp", "--watch", str(self.scratch),
                          *["--keep"] * self.keep], stdin=r, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        os.close(r)

    def _start_usage(self):
        """Count what the session's programs use, in a systemd slice of
        theirs where the user's systemd can make one, and what its own
        processes do, read from /proc."""
        self._started = time.monotonic()
        if shutil.which("systemd-run"):
            try:
                self._slice = usage.Slice(self.scratch.name)
            except OSError:
                pass
        # With a slice for the programs, the sampler has only the session's
        # own processes to read, steadier than they: less often.
        self._sampler = usage.Sampler(every=0.5 if self._slice else 0.2)

    def usage(self):
        """With usage, what the session's programs and its own processes
        have used so far: {"seconds": since it started, "programs" and
        "session": {"peak": bytes of memory, all at once, at most, "cpu":
        seconds, "how": "cgroup, with page cache" or "sampled, resident"}};
        else None."""
        if not self._sampler:
            return None
        programs = self._slice.read() if self._slice else self._sampler.read("programs")
        return {"seconds": time.monotonic() - self._started, "programs": programs,
                "session": self._sampler.read("session")}

    def _make_identity(self):
        """The machine's /etc/passwd and /etc/group, for programs to see,
        with the session's uid and gid USER's, home the session's, and
        nothing else called that, nor the real name in a group's members:
        whoami, ls -l and getpwuid all say USER. /etc/hostname, and
        /etc/hosts with HOST in it, so hostname -f finds it without asking
        DNS; and no logins, which who and users would name."""
        (user, host), uid, gid = self.identity, os.getuid(), os.getgid()
        for kind, look, ours in (("user", pwd.getpwnam, uid), ("group", grp.getgrnam, gid)):
            try:
                theirs = look(user)[2]
            except KeyError:
                continue
            if theirs != ours:
                # Its line would go, and its files show as a number.
                raise SessionError(f"identity's {user} is the machine's {kind} {theirs} "
                                   "already: pick another name")
        try:
            me = pwd.getpwuid(uid)
            real, shell = me.pw_name, me.pw_shell
        except KeyError:
            real, shell = None, "/bin/sh"
        home = self.scratch / "home"
        files = {}
        for name, id_, ours in (("passwd", uid, f"{user}:x:{uid}:{gid}:{user}:{home}:{shell}"),
                                ("group", gid, f"{user}:x:{gid}:")):
            kept = [line for line in Path("/etc", name).read_text().splitlines()
                    if line.split(":")[0] != user and line.split(":")[2:3] != [str(id_)]]
            if name == "group":
                kept = [":".join([*f[:3], ",".join(m for m in f[3].split(",") if m != real)])
                        if len(f := line.split(":")) == 4 else line for line in kept]
            files[f"/etc/{name}"] = [*kept, ours]
        machine = socket.gethostname().split(".")[0]
        files["/etc/hosts"] = [
            line for line in Path("/etc/hosts").read_text().splitlines()
            if not any(n.split(".")[0] == machine for n in line.split("#")[0].split()[1:])
        ] + [f"127.0.1.1\t{host}"]
        files["/etc/hostname"] = [host]
        files["/run/utmp"] = []
        self._faked = {}
        for i, (where, lines) in enumerate(files.items()):
            if Path(where).exists():
                self._faked[where] = self.scratch / f"identity-{i}"
                self._faked[where].write_text("".join(f"{line}\n" for line in lines))

    def _make_home(self):
        home = self.scratch / "home"
        if self.seed:
            shutil.copytree(self.seed, home, symlinks=True)
        home.mkdir(exist_ok=True)
        for d in (".config", ".local/share", ".local/state", ".cache"):
            (home / d).mkdir(parents=True, exist_ok=True)
        # GTK apps: no blinking text caret, which a picture would catch on
        # or off at random, and no animations, which it would catch mid-way.
        # A seeded home's own settings win, but for the presets asked for;
        # GTK 3's caret is said again where GTK 3 on Wayland and Qt 6 look.
        minor = gtk4_minor()
        for gtk in ("gtk-3.0", "gtk-4.0"):
            ini = home / ".config" / gtk / "settings.ini"
            unfold(home, ini)
            if not ini.exists():
                ini.parent.mkdir(parents=True, exist_ok=True)
                ini.write_text(GTK_SETTINGS)
            more = presets(gtk, self.dark, self.high_contrast, self.text_scale, minor)
            # GTK 4.16's automatic font rendering places glyphs at fractions
            # of a pixel, so thin text is drawn fainter than its color, by
            # how much depending on the GTK: manual draws it at its color,
            # the same on every distro. Older GTK 4 warns of the key.
            if (gtk == "gtk-4.0" and (minor or 0) >= 16
                    and "gtk-font-rendering" not in settings(ini.read_text())):
                more["gtk-font-rendering"] = "manual"
            if self.use_wayland:
                more["gtk-cursor-theme-name"] = CURSOR_THEME     # for X clients on Xwayland
            if more:
                ini.write_text(merge(ini.read_text(), more))
            if gtk == "gtk-3.0":
                keys = settings(ini.read_text())
                self._blink = keys.get("gtk-cursor-blink") in (None, "true", "1")
                still = keys.get("gtk-enable-animations") in ("false", "0")
        # GTK 3 on Wayland reads these from GSettings, over settings.ini: the
        # session's own keyfile, a seed's merged into it as settings.ini is.
        keyfile = home / ".config" / "glib-2.0" / "settings" / "keyfile"
        unfold(home, keyfile)
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        more = {k: "false" for k, off in (("cursor-blink", not self._blink),
                                          ("enable-animations", still)) if off}
        if self.use_wayland:
            more["cursor-theme"] = f"'{CURSOR_THEME}'"
        keyfile.write_text(merge(keyfile.read_text() if keyfile.exists() else "", more,
                                 "[org/gnome/desktop/interface]"))
        run = self.scratch / "run"
        run.mkdir(mode=0o700)

    def _known_env(self):
        """The caller's environment, with the scratch dir in it: for a process
        that has no other, to be known as the session's by the watchdog."""
        return {**os.environ, "SHOTBOX_SCRATCH": str(self.scratch)}

    def _start_x(self):
        # Two sessions starting at once can pick the same free number; the
        # second Xvfb then fails to take it. Move on to the next one.
        xauth = self.scratch / "Xauthority"
        xauth.touch(mode=0o600)
        cookie = secrets.token_hex(16)
        taken = set()
        for _ in range(20):
            n = free_display(skip=taken)
            taken.add(n)
            # For any hostname (xauth add's is the machine's): a program
            # with an --identity looks for its own.
            num = str(n).encode().hex()
            subprocess.run(["xauth", "-q", "-f", str(xauth), "nmerge", "-"], check=True, text=True,
                           input=f"ffff 0000  {len(num) // 2:04x} {num} 0012 "
                                 f"{b'MIT-MAGIC-COOKIE-1'.hex()} 0010 {cookie}\n")
            r, w = os.pipe()
            w_, h = self.size
            proc = subprocess.Popen(
                ["Xvfb", f":{n}", "-auth", str(xauth), "-displayfd", str(w),
                 "-screen", "0", f"{w_}x{h}x24", "-nolisten", "tcp", "-noreset"],
                pass_fds=(w,), stdout=subprocess.DEVNULL,
                stderr=open(self.scratch / "Xvfb.log", "wb"),
                env=self._known_env(), start_new_session=True)
            os.close(w)
            with os.fdopen(r) as f:
                got = f.readline().strip()   # blocks until Xvfb listens or exits
            if got:
                self._procs.append(proc)
                return f":{got}", xauth
            proc.wait()
        raise SessionError("Xvfb didn't start; see " + str(self.scratch / "Xvfb.log"))

    def _start_sway(self, bus, sysbus):
        """Start sway, headless, and wait until it answers; the variables
        that point a program at it."""
        run = self.scratch / "run"
        conf = self.scratch / "sway.conf"
        conf.write_text(SWAY_CONFIG.format(width=self.size[0], height=self.size[1],
                                           xwayland="force" if self.xwayland else "disable",
                                           cursors=CURSOR_THEME))
        home = self.scratch / "home"
        # In the home's ~/.icons, which GTK 4 looks in, not in XCURSOR_PATH.
        theme = home / ".icons" / CURSOR_THEME / "cursors"
        unfold(home, theme / "default")
        theme.mkdir(parents=True, exist_ok=True)
        for name in CURSOR_NAMES:
            if (theme / name).is_dir() and not (theme / name).is_symlink():
                raise SessionError(f"the seed's ~/{(theme / name).relative_to(home)} is a "
                                   "directory, in the way of the session's cursor theme")
        (theme / "default").write_bytes(BLANK_CURSOR)
        for name in CURSOR_NAMES[1:]:
            (theme / name).unlink(missing_ok=True)
            (theme / name).symlink_to("default")
        blank = {"XCURSOR_THEME": CURSOR_THEME}
        env = {k: os.environ[k] for k in ("PATH",) if k in os.environ}
        env.update(HOME=str(home), XDG_RUNTIME_DIR=str(run), **blank,
                   XDG_CONFIG_HOME=str(home / ".config"), DBUS_SESSION_BUS_ADDRESS=bus,
                   LANG="C.UTF-8", TZ="UTC",
                   # No GPU, no seat, no input devices: an output in memory,
                   # drawn in software, the same pixels every run.
                   WLR_BACKENDS="headless", WLR_RENDERER="pixman",
                   WLR_LIBINPUT_NO_DEVICES="1", WLR_HEADLESS_OUTPUTS="1",
                   **self._system_env(sysbus))
        log = self.scratch / "sway.log"
        proc = subprocess.Popen(["sway", "-c", str(conf)], env=env,
                                stdout=subprocess.DEVNULL, stderr=open(log, "wb"),
                                start_new_session=True)
        self._procs.append(proc)

        # sway has nothing like Xvfb's -displayfd: its sockets appear in
        # our runtime dir, then it answers on the IPC one.
        def answering():
            socks = sorted(run.glob("sway-ipc.*.sock"))
            ways = sorted(p for p in run.glob("wayland-*") if not p.name.endswith(".lock"))
            if not socks or not ways:
                return None
            got = {"SWAYSOCK": str(socks[0]), "WAYLAND_DISPLAY": ways[0].name}
            try:
                wl.ipc(got, wl.GET_VERSION)
            except OSError:
                return None
            return got

        try:
            got = wait_for("sway to start", answering, 15, alive=proc)
        except SessionError as e:
            raise SessionError(f"{e}; see {log}")
        if self.xwayland:
            # sway sets DISPLAY for what it runs once it has made Xwayland's
            # display: ask it to write it down, in the runtime dir, so no
            # path of ours goes into a sway command.
            where = run / "xwayland-display"

            def xdisplay():
                wl.ipc(got, wl.RUN_COMMAND,
                       'exec printf %s "$DISPLAY" > "$XDG_RUNTIME_DIR/xwayland-display"')
                return where.exists() and where.read_text().strip()

            try:
                got["DISPLAY"] = wait_for("sway to give Xwayland a display", xdisplay, 15,
                                          interval=0.2, alive=proc)
            except SessionError as e:
                raise SessionError(f"{e}; see {log}")
        got.update(XDG_SESSION_TYPE="wayland", GDK_BACKEND="wayland", **blank)
        return got

    def _start_bus(self, kind):
        services = self.scratch / f"{kind}-services"
        services.mkdir()
        if kind == "session" and self.desktop:
            dirs = "<standard_session_servicedirs/>"
        else:
            dirs = f"<servicedir>{services}</servicedir>"
        listen = (f"unix:tmpdir={self.scratch}" if kind == "session"
                  else f"unix:path={self.scratch / 'run' / 'system_bus_socket'}")
        conf = self.scratch / f"{kind}-bus.conf"
        conf.write_text(BUS_CONFIG.format(type=kind, listen=listen, services=dirs))
        proc = subprocess.Popen(
            ["dbus-daemon", "--config-file", str(conf), "--nofork", "--print-address"],
            env=self.env, stdout=subprocess.PIPE, stderr=open(self.scratch / f"{kind}-bus.log", "wb"),
            text=True, start_new_session=True)
        self._procs.append(proc)
        address = proc.stdout.readline().strip()
        if not address:
            raise SessionError(f"dbus-daemon ({kind}) didn't start")
        return address

    def _make_env(self, graphics, bus, sysbus):
        home = self.scratch / "home"
        env = {k: os.environ[k] for k in self.passthrough if k in os.environ}
        env.update(SEALED_ENV, **self._time_env)
        # Playwright looks for its browsers in HOME's cache, and the
        # session's HOME is the scratch home. Not with --identity: the path
        # has the user's name in it.
        browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.join(
            os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "ms-playwright")
        if not self.identity and ("PLAYWRIGHT_BROWSERS_PATH" in os.environ
                                  or os.path.isdir(browsers)):
            env["PLAYWRIGHT_BROWSERS_PATH"] = browsers
        if self.desktop:
            del env["GSETTINGS_BACKEND"]
        if self.a11y:
            del env["GTK_A11Y"], env["NO_AT_BRIDGE"]
            # Chromium joins a bus only when told a reader is running, and
            # the bus's IsEnabled says none is; some Firefox builds (Ubuntu's
            # PPA's) too, told so their own way.
            env["ACCESSIBILITY_ENABLED"] = env["GNOME_ACCESSIBILITY"] = "1"
        # libadwaita takes its style from the portal or GSettings, not from
        # GTK's settings.ini, and a sealed session has neither to tell it.
        if self.dark:
            env["ADW_DEBUG_COLOR_SCHEME"] = "prefer-dark"
        if self.high_contrast:
            env["ADW_DEBUG_HIGH_CONTRAST"] = "1"
        env.update(
            HOME=str(home),
            XDG_CONFIG_HOME=str(home / ".config"),
            XDG_DATA_HOME=str(home / ".local/share"),
            XDG_STATE_HOME=str(home / ".local/state"),
            XDG_CACHE_HOME=str(home / ".cache"),
            XDG_RUNTIME_DIR=str(self.scratch / "run"),
            SHOTBOX="1",
            SHOTBOX_SCRATCH=str(self.scratch),
        )
        env.update(graphics)
        if self._net_pid:
            # For `wait port` from outside, to look in the session's network.
            env["SHOTBOX_NET_PID"] = str(self._net_pid)
        if self.gamepads:
            # SDL finds the pads in /dev/input itself; asked, udev would name
            # the machine's, which aren't there. Nor are the raw HID devices
            # its HIDAPI drivers would read the machine's controllers from.
            env["SDL_JOYSTICK_DISABLE_UDEV"] = "1"
            env["SDL_JOYSTICK_HIDAPI"] = "0"
        if self.identity:
            user, host = self.identity
            env.update(USER=user, LOGNAME=user, HOSTNAME=host)
        if bus:
            env["DBUS_SESSION_BUS_ADDRESS"] = bus
        env.update(self._system_env(sysbus))
        env.update(self.extra_env)
        if self._faketime:
            # First, so the clock is libfaketime's whatever else is preloaded.
            env["LD_PRELOAD"] = ":".join(filter(None, (self._faketime, env.get("LD_PRELOAD"))))
        return env

    def _system_env(self, sysbus):
        """The stand-in system bus, and no printers, with `sysbus`."""
        if not sysbus:
            return {}
        # Without its socket, libcups tries localhost:631; told of one
        # that isn't there, it finds no scheduler.
        return {"DBUS_SYSTEM_BUS_ADDRESS": sysbus,
                "CUPS_SERVER": str(self.scratch / "run" / "cups.sock")}

    def _activation_env(self):
        """Give what the session bus activates the session's environment,
        display and all, as the programs run in it have."""
        # JSON's strings are GVariant's too, escapes and all; repr's \x aren't.
        pairs = ", ".join(f"{json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}"
                          for k, v in self.env.items() if k != "DBUS_SESSION_BUS_ADDRESS")
        r = subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
                            "--object-path", "/org/freedesktop/DBus", "--method",
                            "org.freedesktop.DBus.UpdateActivationEnvironment",
                            "{" + pairs + "}"], env=self.env, capture_output=True, text=True)
        if r.returncode:
            raise SessionError("the session bus refused its activation environment: "
                               + r.stderr.strip())

    def _start_audio(self):
        """PipeWire, with a null sink and the pulse protocol, its sockets in
        the session's runtime dir, where clients look; and WirePlumber, on
        a config and state of the session's own."""
        conf = self.scratch / "pipewire.conf"
        conf.write_text(PIPEWIRE_CONFIG)
        log = self.scratch / "pipewire.log"
        # Not on the session's clock: libfaketime moves a timer set to a
        # time on the monotonic clock by the offset too, and PipeWire's
        # driver sets one each cycle, so its graph would never run.
        env = dict(self.env)
        if self._faketime:
            env["LD_PRELOAD"] = ":".join(p for p in env["LD_PRELOAD"].split(":")
                                         if p != self._faketime)
        proc = subprocess.Popen(["pipewire", "-c", str(conf)], env=env,
                                stdout=subprocess.DEVNULL, stderr=open(log, "wb"),
                                start_new_session=True)
        self._procs.append(proc)
        pulse = self.scratch / "run" / "pulse" / "native"
        try:
            wait_for("PipeWire", pulse.exists, 15, alive=proc)
        except SessionError as e:
            raise SessionError(f"{e}; see {log}")
        version = subprocess.run(["wireplumber", "--version"], capture_output=True,
                                 text=True).stdout
        m = re.search(r"libwireplumber (\d+)\.(\d+)", version)
        new = bool(m) and (int(m[1]), int(m[2])) >= (0, 5)
        wp = self.scratch / "wireplumber"
        conf = wp / "config" / "wireplumber"
        files = ({"wireplumber.conf.d/shotbox.conf": WIREPLUMBER_CONFIG} if new else
                 {f"{d}/51-shotbox.lua": text for d, text in WIREPLUMBER_LUA.items()})
        for name, text in files.items():
            (conf / name).parent.mkdir(parents=True)
            (conf / name).write_text(text)
        env = dict(self.env, XDG_CONFIG_HOME=str(wp / "config"),
                   XDG_STATE_HOME=str(wp / "state"))
        profile = ["--profile", "shotbox"] if new else []
        log = self.scratch / "wireplumber.log"
        proc = subprocess.Popen(["wireplumber", *profile], env=env, stdout=subprocess.DEVNULL,
                                stderr=open(log, "wb"), start_new_session=True)
        self._procs.append(proc)

        # It's linking streams once it's a client of PipeWire's.
        def linking():
            r = subprocess.run(["pw-dump"], env=self.env, capture_output=True, text=True)
            return '"application.process.binary": "wireplumber"' in r.stdout

        try:
            wait_for("WirePlumber", linking, 15, interval=0.2, alive=proc)
        except SessionError as e:
            raise SessionError(f"{e}; see {log}")
        self.env["PULSE_SERVER"] = f"unix:{pulse}"

    def _start_a11y(self):
        """An accessibility bus of the session's own: at-spi-bus-launcher on
        the private session bus starts it, and AT_SPI_BUS_ADDRESS points
        every toolkit and reader in the session at it, on X11 and Wayland
        alike. Then a recorder of what programs announce on it."""
        launcher = next(p for p in A11Y_LAUNCHERS if os.access(p, os.X_OK))
        log = self.scratch / "a11y-bus.log"
        proc = subprocess.Popen([launcher, "--launch-immediately"], env=self.env,
                                stdout=subprocess.DEVNULL, stderr=open(log, "wb"),
                                start_new_session=True)
        self._procs.append(proc)

        def address():
            r = subprocess.run(["gdbus", "call", "--session", "--dest", "org.a11y.Bus",
                                "--object-path", "/org/a11y/bus",
                                "--method", "org.a11y.Bus.GetAddress"],
                               env=self.env, capture_output=True, text=True)
            m = re.fullmatch(r"\('(.+)',\)", r.stdout.strip())
            return m and m.group(1)

        try:
            self.env["AT_SPI_BUS_ADDRESS"] = wait_for("the accessibility bus", address, 15,
                                                      interval=0.2, alive=proc)
        except SessionError as e:
            raise SessionError(f"{e}; see {log}")
        out = Path(self.announce_log or self.scratch / "announcements").resolve()
        self.env["SHOTBOX_ANNOUNCEMENTS"] = str(out)
        out.unlink(missing_ok=True)
        # Its own copy of the package's path: the session's PYTHONPATH is
        # whatever the caller let through, if anything.
        env = dict(self.env, PYTHONPATH=str(Path(__file__).resolve().parent.parent))
        rec = subprocess.Popen([sys.executable, "-m", "shotbox.a11y", "record", str(out)],
                               env=env, stdout=subprocess.DEVNULL,
                               stderr=open(self.scratch / "announce.log", "wb"),
                               start_new_session=True)
        self._procs.append(rec)
        # It makes the file once it's listening, so nothing said is missed.
        try:
            wait_for("the announcement recorder", out.exists, 15, alive=rec)
        except SessionError as e:
            said = (self.scratch / "announce.log").read_text(errors="replace").strip()
            raise SessionError(f"{e}; it said: {said or '(nothing)'}")

    def _start_pads(self):
        """The session's gamepads (see pad.py): a server holding them, and
        their /dev/input nodes for spawn to give programs. The server stops
        when the thread that made it does (Linux's parent-death signal goes
        by thread), so a Session with gamepads wants a thread that lasts as
        long as it does."""
        self._pad_nodes = self._serve("the gamepads", "pad", os.environ, "pads.sock",
                                      self.gamepads, os.getpid())

    def _serve(self, what, module, env, sock, *args):
        """Start shotbox.`module`'s serve(`sock`'s path in the scratch dir,
        the file it says it's ready in, *args), in a process of its own
        the session stops, with `env`; what it says, once it's ready."""
        ready = self.scratch / f"{Path(sock).stem}.json"
        here = str(Path(__file__).resolve().parent.parent)     # this shotbox, wherever it is
        path = os.pathsep.join(filter(None, (here, os.environ.get("PYTHONPATH"))))
        # del: -c puts the current directory first, ahead of PYTHONPATH.
        serve = (f"import sys; del sys.path[0]; from shotbox.{module} import serve; "
                 "sys.exit(serve(*sys.argv[1:]))")
        proc = subprocess.Popen([sys.executable, "-c", serve, str(self.scratch / sock), str(ready),
                                 *map(str, args)],
                                env={**env, "PYTHONPATH": path},
                                stdout=subprocess.DEVNULL, start_new_session=True)
        self._procs.append(proc)
        wait_for(what, ready.exists, 10, alive=proc)
        got = json.loads(ready.read_text())
        if isinstance(got, dict):
            raise SessionError(got["error"])
        return got

    def _start_net(self):
        """A network namespace of the session's own, for every program it
        runs to join, so they reach each other and nothing else: a process
        sleeping in it, which bwrap makes, bringing its loopback up. A
        namespace of each program's own would keep them apart too."""
        r, w = os.pipe()
        proc = subprocess.Popen(["bwrap", "--dev-bind", "/", "/", "--unshare-user",
                                 "--unshare-net", "--info-fd", str(w), "--", "sleep", "infinity"],
                                pass_fds=(w,), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                env=self._known_env(), start_new_session=True)
        os.close(w)
        self._procs.append(proc)
        pid = sandboxed_pid(r, proc)
        if proc.poll() is not None or pid == proc.pid:
            slow = proc.poll() is None
            if slow:
                proc.kill()
                proc.wait()
            said = proc.stderr.read().decode(errors="replace").strip()
            raise SessionError("--net loopback needs bubblewrap to make a network namespace, "
                               "and it can't here (are unprivileged user namespaces "
                               "allowed?): " + (said or "it didn't say it had one in 5s"
                                                if slow else said or f"status {proc.returncode}"))
        self._net_pid = pid

    def _probe_sandbox(self):
        """Run programs under bubblewrap if it can make a sandbox here. Gamepads
        need one; without them, the session goes on without, saying so where
        it would have hidden something."""
        (self.scratch / "masked").touch(mode=0)
        why = sandbox_needs(self._sandbox())
        if why and self.gamepads:
            raise SessionError(f"gamepads need bubblewrap to make a sandbox, and {why}")
        if why and self.identity:
            raise SessionError(f"--identity needs bubblewrap to make a sandbox, and {why}")
        kept = [d for d in self._runtime_dirs() if self.scratch.resolve().is_relative_to(d)]
        if why and not nothing_to_hide():
            self.unsealed = ("programs run without a sandbox, so they can reach the "
                             f"machine's sound devices and the desktop's sockets: {why}; "
                             "to run them so on purpose, and not be told, set "
                             "SHOTBOX_SANDBOX=off in shotbox's environment, or give "
                             "--no-sandbox on the command line")
        elif kept:
            self.unsealed = (f"the session's scratch dir is in {kept[0]}, so programs can "
                             "reach the desktop's sockets there: set TMPDIR elsewhere")
        if self.unsealed:
            sys.stderr.write(f"shotbox: {self.unsealed}\n")
        self._sandboxed = not why

    def _runtime_dirs(self):
        """The desktop's runtime dirs, where its own sockets are (Wayland,
        PulseAudio, PipeWire, its bus), for the sandbox to hide: /run/user/UID
        and XDG_RUNTIME_DIR, where it's one, the user's own and no one
        else's. Not one with the scratch dir in it, which _probe_sandbox
        says."""
        dirs = []
        for d in filter(None, (f"/run/user/{os.getuid()}", os.environ.get("XDG_RUNTIME_DIR"))):
            d = Path(d).resolve()
            try:
                st = d.stat()
            except OSError:
                continue
            if (d.is_dir() and st.st_uid == os.getuid() and not st.st_mode & 0o077
                    and d not in dirs):
                dirs.append(d)
        return dirs

    def _sandbox(self):
        """bwrap and its options, made for each program, since devices come
        and go. Programs get no sound devices and none of the desktop's
        sockets, so nothing plays through the machine's speakers or opens
        on its screen, and the session's pads in a /dev/input of their
        own, and none of the machine's: the raw HID and USB devices SDL,
        hidapi and libusb read controllers through are a file no one can
        open, and so is uinput, so nothing inside can plug a device into the
        machine. A device plugged in while a program runs can still reach
        it: /dev is the machine's, live."""
        args = ["--dev-bind", "/", "/", "--tmpfs", "/dev/input"]
        if Path("/dev/snd").is_dir():
            args += ["--tmpfs", "/dev/snd"]
        for run in self._runtime_dirs():
            if not self.scratch.resolve().is_relative_to(run):
                args += ["--tmpfs", str(run)]
        for node in self._pad_nodes:
            args += ["--dev-bind", node, node]
        masked = str(self.scratch / "masked")
        dev = Path("/dev")
        for node in [*dev.glob("hidraw*"), *dev.glob("usb/hiddev*"), dev / "uhid", dev / "uinput"]:
            if node.exists():
                args += ["--ro-bind", masked, str(node)]
        if (dev / "bus/usb").is_dir():
            args += ["--tmpfs", "/dev/bus/usb"]
        if not self.machine_bus:
            # For a program that looks for the machine's own sockets.
            # Not Avahi's: nss-mdns looks up .local names through it.
            for run in ("/run/dbus", "/run/cups"):
                if Path(run).is_dir():
                    args += ["--tmpfs", run]
        if self.identity:
            # A UTS namespace each, every one with the same name in it.
            args += ["--unshare-uts", "--hostname", self.identity[1]]
            for where, made in self._faked.items():
                args += ["--ro-bind", str(made), where]
            if Path("/run/systemd/sessions").is_dir():
                args += ["--tmpfs", "/run/systemd/sessions"]
        # Found on shotbox's PATH, where the session checked for it: the
        # session's PATH is the program's. Not --die-with-parent: Linux
        # sends that when the thread that started bwrap ends, which can be
        # long before the session does; the session stops bwrap's process
        # group, and the program in it, itself.
        return [shutil.which("bwrap") or "bwrap", *args]

    def _runnable(self, program, cwd):
        """Why `program` can't be run from `cwd` (bwrap would only say so in
        the program's log), or None if it can."""
        program, cwd = os.fspath(program), cwd or os.getcwd()
        if os.sep in program:
            path = os.path.join(cwd, program)
            found = os.path.isfile(path) and path
            runs = found and os.access(found, os.X_OK)
        else:
            # As exec does from `cwd`: the first that runs, past any on PATH
            # that don't, a relative or empty entry being from `cwd`.
            dirs = os.pathsep.join(os.path.join(cwd, d)
                                   for d in self.env.get("PATH", os.defpath).split(os.pathsep))
            runs = shutil.which(program, path=dirs)
            found = runs or shutil.which(program, mode=os.F_OK, path=dirs)
        if not found:
            return "No such file or directory"
        if not runs:
            return "Permission denied (not executable)"
        return None

    # --- running things -----------------------------------------------------------

    def spawn(self, cmd, log=None, **kw):
        """Start `cmd` inside the session, in its own process group. Output
        goes to `log` (a path) if given, else through. It runs under
        bubblewrap where it can, with no sound devices, and a /dev/input
        that has only the session's gamepads."""
        if self._sandboxed:
            cwd = Path(kw.get("cwd") or os.getcwd()).resolve()
            hidden = [d for d in self._runtime_dirs() if cwd.is_relative_to(d)
                      and not self.scratch.resolve().is_relative_to(d)]
            if hidden:
                raise SessionError(f"can't run {cmd[0]} from {cwd}: programs in the "
                                   f"session don't see {hidden[0]}")
            why = self._runnable(cmd[0], kw.get("cwd"))
            if why:
                raise SessionError(f"can't run {cmd[0]}: {why}")
        out = open(log, "ab") if log else None
        info = None
        try:
            if self._sandboxed:
                # bwrap says the program's own pid down this pipe.
                info, w = os.pipe()
                kw["pass_fds"] = (*kw.get("pass_fds", ()), w)
                cmd = [*self._sandbox(), "--info-fd", str(w), "--", *cmd]
            if self._net_pid:
                cmd = [shutil.which("nsenter") or "nsenter", "-t", str(self._net_pid), "-U", "-n",
                       "--preserve-credentials", "--", *cmd]
            if self._slice:
                cmd = self._slice.wrap(cmd, self.env)
            proc = subprocess.Popen(cmd, env=self.env, stdout=out, stderr=out,
                                    start_new_session=True, **kw)
            if self._sampler and not self._slice:
                self._sampler.add("programs", proc.pid)
        except OSError as e:
            if info is not None:
                os.close(info)
            raise SessionError(f"can't run {cmd[0]}: {e.strerror}")
        finally:
            if out:
                out.close()
            if info is not None:
                os.close(w)
        if info is not None:
            proc.program_pid = sandboxed_pid(info, proc)
        self._procs.append(proc)
        return proc

    def run(self, cmd, **kw):
        """Run `cmd` to the end inside the session; its exit status."""
        proc = self.spawn(cmd, **kw)
        try:
            return proc.wait()
        finally:
            kill_group(proc)

    # --- teardown -----------------------------------------------------------------

    def __exit__(self, *exc):
        self.close()
        if self._sampler and self._up:
            self.used = self.usage()
        for proc in reversed(self._procs):
            kill_group(proc)
        if self._xsettings:
            self._xsettings.close()
        if self._watch is not None:
            # Its processes stopped, the session needs no watchdog, and a
            # Ctrl-C cutting what's left short mustn't set it on a
            # recording that's finishing.
            os.write(self._watch, b"stopped")
            os.close(self._watch)
            self._watch = None
        for meter in (self._sampler, self._slice):
            if meter:
                meter.close()
        if self.scratch and self.scratch.exists():
            if self.keep:
                print(f"shotbox: kept {self.scratch}")
            else:
                shutil.rmtree(self.scratch, ignore_errors=True)
        return False
