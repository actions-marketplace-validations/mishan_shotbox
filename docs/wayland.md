# Wayland

A session can be a Wayland one: `--wayland` runs the program under a
headless sway instead of Xvfb, and `--xwayland` adds an X server inside
it, for X clients running on a Wayland desktop the way they do on a real
one. Everything else stays: the scratch home, the private bus, windows
by title or `app=`, pictures, waits, failure pictures, keys, text, the
pointer, `park`, `term`, `record`, the Python API
(`shotbox.Session(wayland=True)`, or `here()` inside) and the cleanup.

```
shotbox shoot editor.png --wayland --window app=org.gnome.TextEditor -- gnome-text-editor
```

This is the plan for it, and where it has got to.

## Why sway

Everything shotbox does to a display, it has to be able to do from a
client, with no GPU and no seat:

| Needs | Xvfb (now) | headless sway | headless Mutter |
|---|---|---|---|
| A private display, no GPU, no seat | yes | yes (`WLR_BACKENDS=headless`) | yes |
| The same pixels every run | yes | yes, with the pixman renderer | not checked |
| Pictures | `import` | wlr-screencopy (`grim`) | none for clients: D-Bus and PipeWire |
| Windows, by name, and where | `xwininfo` | its IPC socket: title, app id, rect | none for clients |
| Keys and pointer | XTEST | the virtual keyboard and pointer protocols | D-Bus remote desktop, over PipeWire |

In a spike (Debian testing, sway 1.12, a GTK 4 window), sway came up
headless with the pixman renderer, reported the window's title, app id,
geometry and focus over IPC, `grim` took the same bytes twice, whole and
by region, and text typed with `wtype` reached the window, less its first
character (see phase 2).

Mutter, headless, offers clients none of the three: no capture, no input
and no window list, only D-Bus sessions that need PipeWire. GNOME Shell
itself is better shot the way neon-doll's `shoot-shell.sh` does it, with
an extension inside. Other wlroots compositors (cage, labwc) would fit
later behind the same code, since the protocols are the same.

## How it fits

`Screen` talks to the display through a backend, picked from the
session's environment:

- **X11** is today's code: `xwininfo` and `import` (x.py), XTEST (xtest.py).
- **Wayland** is new (wl.py):
  - windows come from sway's IPC socket, which speaks JSON over a Unix
    socket: a few lines of the standard library, no `swaymsg` or `jq`;
  - pictures come from `grim`, run through ImageMagick like the X ones,
    so the PNGs have no timestamps;
  - a `stable` wait's fingerprint is `grim` writing a PPM to a pipe;
  - videos come from `wf-recorder`, over the same screencopy, piped raw
    to the ffmpeg X11's recordings use, so the options and the ways to
    stop are the same;
- **Wayland input** is wlinput.py, a small Wayland client of our own for
  sway's virtual keyboard and pointer, the way xtest.py is for X: the
  keymap goes over as text in a memfd, passed with `sendmsg`.

The session, with `--wayland`:

- starts `sway -c CONFIG` with `WLR_BACKENDS=headless`,
  `WLR_RENDERER=pixman` and `WLR_LIBINPUT_NO_DEVICES=1`, its sockets in
  the session's own `XDG_RUNTIME_DIR`;
- writes the config: the output at `--screen`'s size, no borders, no
  gaps, and every window floating at 0,0 at the size it asks for, the
  way an X client comes up on Xvfb with no window manager;
- waits for sway to answer on its IPC socket (it has nothing like Xvfb's
  `-displayfd`);
- sets `WAYLAND_DISPLAY`, `SWAYSOCK`, `XDG_SESSION_TYPE=wayland` and
  `GDK_BACKEND=wayland`, and no `DISPLAY`;
- starts its keyboard and pointer, kept till it stops: headless sway
  has neither of its own; while there's no keyboard GTK has no keyboard
  focus, saying nothing has the focus and drawing no caret, and while
  there's no pointer a program has none, and is slow to take up a new
  one, missing its first button;
- gives sway and its programs a cursor theme whose every cursor is
  clear, so the pointer, which stays where it was left, isn't seen: sway
  1.9 puts the cursor in what grim takes.

With `--xwayland`, sway starts Xwayland straight away (`xwayland force`)
and the session sets `DISPLAY` to it too, which sway reports to a command
it runs. GTK still picks Wayland; a program that only speaks X, or one
told to (`--env GDK_BACKEND=x11`), is an X client of Xwayland, as on a
desktop. sway's IPC lists X windows beside Wayland ones, so finding,
waiting on and taking a picture of one is the same.

Xwayland is asked for rather than always there: it's a second server to
start and stop, and a session without it can't have a program quietly
fall back to X. Unlike the Xvfb sessions, it has no Xauthority cookie:
wlroots doesn't give it one, so any local program that finds its socket
can connect for as long as the session lasts.

## What differs from X11

- **The pictures aren't X11's.** GTK draws its own decorations on
  Wayland, with rounded corners and, where there's room, a shadow. A
  committed picture belongs to one backend.
- **Window geometry** is sway's rect for the window, which leaves the
  shadow out, so a point inside a window is where it would be on X11.
- **Xwayland isn't Xvfb either.** An X client there has no XSETTINGS DPI to
  read, so GTK falls back to 96 dpi and draws its text smaller than the
  same program does as a Wayland client, as it would on a desktop.
- **A window's name** is its title, as on X11, and `app=RE` matches its
  app id: a Wayland window's `app_id`, or an X one's `WM_CLASS` under
  Xwayland, the same as on Xvfb.

## Phases

1. **Sessions, pictures and waits.** `--wayland` and `--xwayland` for
   `run`, `shoot` and `term` (VTE is at home on Wayland, so `term` came
   along); windows by name; `capture`, `shoot --window/--crop`, `stable`
   waits and failure pictures; the Python API choosing its backend;
   tests that skip without sway, `grim` and Xwayland, and CI that
   installs them. *Done.*
2. **Input.** Keys, text, clicks, moves, scrolls, drags and `park` over the
   virtual keyboard and pointer protocols, reaching Xwayland's X clients
   too: one keyboard and pointer for the session, which a server it
   starts holds for every command. *Done.*

   The spike lost the first key `wtype` sent, so the keyboard is made
   with one fixed keymap holding every key shotbox can press, laid out
   as a US keyboard is (a and A, 1 and !, Tab and back-tab on one key,
   so Shift in a chord gives the shifted symbol, as on X11; a first
   version put each symbol on a key of its own, and `shift+a` typed
   `a`), and a new
   keyboard presses a key with no symbol first and waits a moment. Over
   about 150 runs of typing into a GTK 4 entry, cold and under load, the
   shipped keyboard lost nothing, and nor did one without the blank key
   or the wait; one variant (the blank key without the wait) lost text
   twice early on and never again. What wtype hit wasn't pinned down.
3. **The rest.** Matching on app id (`app=RE`, on X11 too), and a
   README section of its own. *Done.*
4. **Using it.** neon-doll's `cosmic-shoot.sh` ran COSMIC's compositor
   nested in a headless sway of its own, with sleeps between steps; it
   is now a `shotbox run --wayland` with waits for each step, twice as
   fast, the same pictures (mishan/neon-doll#6). *Done.*

   It showed one thing worth knowing: what runs inside a nested
   compositor isn't a window sway can see, so the sign it has come up is
   the screen changing, and a clock on the screen changes by itself.
   Watch a part of the screen without one.

## Needs

`sway` and `grim` for `--wayland`, `Xwayland` for `--xwayland`, and
`wf-recorder` for `record`. On Debian and Ubuntu: `sudo apt install sway
grim xwayland wf-recorder`. Nothing else;
X11 sessions need none of them.
