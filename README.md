# shotbox

[![PyPI](https://img.shields.io/pypi/v/shotbox)](https://pypi.org/project/shotbox/)
[![npm](https://img.shields.io/npm/v/shotbox)](https://www.npmjs.com/package/shotbox)
[![CI](https://github.com/mishan/shotbox/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mishan/shotbox/actions/workflows/ci.yml)

Eyes and hands on a real graphical program, for an AI agent, a script or CI.

An agent working on a program with a window can't see it. shotbox runs the
program on a sealed display of its own, X11 or Wayland, and lets whatever
is driving it look and act: read the accessible tree, take screenshots,
click by a button's name, type, hold keys and gamepad buttons, and wait
until the program is actually ready. Then measure what it finds: names a
screen reader can't say, contrast, the Tab order, and pixels changed
against a reference. Nothing it runs reaches your desktop by accident.

```
claude mcp add shotbox -- shotbox mcp --allow ~/src/app/shotbox-programs
```

Without an agent, it's the same session from the command line:

```
shotbox shoot editor.png --window 'Text Editor' -- gnome-text-editor notes.md
```

## For agents

`shotbox mcp --allow PROGRAMS` gives an agent sealed sessions over the
Model Context Protocol (stdio). It can start a session, spawn programs
from the list in PROGRAMS (`NAME = COMMAND` a line, and nothing else),
and then:
- read the accessible tree, and take screenshots, which come back as
  images;
- click by a node's name, press keys, type and work a gamepad;
- wait for windows, stillness and announcements;
- lint, measure contrast and walk the Tab order.

That's enough for an agent to check its own change in a GUI, test a game
by playing it, audit what a screen reader hears, or reproduce a bug report
in a session that's the same each time. The accessible tree is the cheap
way to look, a few hundred tokens of text; a screenshot is for what only
pixels say. An agent can't run anything off the list, and its sessions stop
when the server does.

What it's for, the tools, and how it's built: [docs/mcp.md](docs/mcp.md).

An agent that runs a program itself, from its shell, runs it on your
desktop: a window opens on your screen, a sound plays through your
speakers, a browser run under `xvfb-run` finds your Wayland display all
the same. To keep it in a session, put this in the project's `CLAUDE.md`
or `AGENTS.md`, or your own:

```markdown
## Graphical programs, browsers and sound

Never run a graphical program, a browser (headless too) or anything that
makes sound straight on this machine: it reaches my desktop and speakers.
Run it in a shotbox session:

- a picture: `shotbox shoot OUT.png --window RE -- COMMAND`
- a script or a test suite: `shotbox run [--audio] -- COMMAND`
- a browser from Node: the script under `shotbox run`; the shotbox
  package's `sealed()` seals a browser's environment, but not the
  speakers
- a session to come back to: `shotbox start --name NAME`, then
  `shotbox spawn --session NAME -- COMMAND`, and `shotbox stop NAME`

Not `xvfb-run`: it lets `WAYLAND_DISPLAY` and the session bus through. A
server started for a test listens on 127.0.0.1, not 0.0.0.0.
```

## Why

An agent checking its change, a screenshot for a README, and a test in CI
all want the same things, and every project was growing its own copy:

- **Sealed.** The environment is rebuilt from an allowlist, not scrubbed from
  a denylist, so nothing leaks in by accident. `DISPLAY`, `WAYLAND_DISPLAY`
  and your session bus are gone; `HOME` and every XDG directory are a scratch
  dir. The bus has nothing activatable on it, so no gvfs, dconf or portals
  start behind your back. Settings are in-memory, so apps come up with their
  own defaults, not yours. Programs run under bubblewrap, with no sound
  devices and none of your desktop's sockets. What that does and doesn't
  cover: [What's sealed](#whats-sealed).
- **Ready, not slept.** Waits are for things: a window mapped, a port open, a
  file written, text on a terminal's screen, or the picture holding still.
  No `sleep 6` and hope.
- **Deterministic.** X11 on Xvfb, GTK's software renderer, `LANG=C.UTF-8`,
  `TZ=UTC`, a cursor that doesn't blink (GTK's text caret included, and GTK's
  animations off), and PNGs with no timestamps in them.
- **Cleaned up.** Every program runs in its own process group, and the group
  is killed on the way out: signalling a wrapper script alone leaves its
  children running.

## Install

Needs `Xvfb`, `xauth`, `dbus-daemon`, ImageMagick and `xwininfo`; for
`shotbox term`, PyGObject with VTE (`gir1.2-vte-2.91`); for `--a11y`,
at-spi2-core, PyGObject's Atspi (`gir1.2-atspi-2.0`) and `gdbus`; for
`--desktop`, `gdbus`; for `--audio`, PipeWire and WirePlumber; for
`--gamepads`, write access to `/dev/uinput`; for `record`, ffmpeg; and
bubblewrap, which `--gamepads` needs, and which [seals](#whats-sealed)
more where it's there. On Debian:

```
sudo apt install xvfb xauth dbus-daemon imagemagick x11-utils gir1.2-vte-2.91 \
  at-spi2-core gir1.2-atspi-2.0 libglib2.0-bin pipewire wireplumber ffmpeg bubblewrap
pipx install --system-site-packages shotbox
```

`--system-site-packages` lets it see the system's PyGObject, which
`shotbox term` uses; `pip install shotbox` works too. Or run it from a
checkout: `ln -s "$PWD/bin/shotbox" ~/.local/bin/shotbox`.

No Python packages: it's the standard library and those tools. Wayland
sessions need more; see [Wayland](#wayland).

## Commands

**`shotbox shoot OUT.png [options] -- COMMAND...`** starts the command in a
sealed session, waits, takes the picture and stops everything.

- `--window RE` takes that window (its title, a regex matched in full)
  rather than the whole display, and waits for it to appear. `app=RE`
  matches its app id instead: a Wayland window's `app_id`, or an X
  window's `WM_CLASS`, instance or class. Every `--window` and every
  `window` wait takes either; a title that really does start with `app=`
  can be written `[a]pp=...`.
- `--wait window:RE|port:N|file:PATH|ready|stable[:SECS]` waits for more
  (repeatable, in order). `ready` is `$SHOTBOX_SCRATCH/ready` existing;
  `shotbox term` writes it. `stable` is the `--window` (or the whole display)
  looking the same for SECS (0.5): for a program with no other sign it's
  done drawing.
- `--park` moves the pointer out of the way first (see `shotbox park`).
- `--crop WxH+X+Y`, `--settle SECS` (0.3), `--timeout SECS` (30),
  `--log FILE` for the program's output. If a wait fails or the program
  dies, it says which, shows the program's last output, and leaves a
  picture of the screen as it was beside OUT, as `OUT-failed.png`
  (`--failed FILE` for elsewhere).

**`shotbox run [options] -- COMMAND...`** runs a command in a sealed session
and exits with its status. For scripts that take several pictures: inside,
`shotbox wait window|port|file|ready|stable ARG` and `shotbox capture OUT.png
[--window RE] [--crop G] [--park] [--vision KIND]` work on the session's
display. `shotbox wait stable [SECS] [--window RE]` is the one to use after a
click, in place of a sleep. `capture --burst N [--every SECS] [--sheet
FILE]` takes N pictures, to OUT-1.png, OUT-2.png..., one starting every SECS
(or as fast as they come), and prints when each was taken; `--sheet` puts
them in rows of four in one picture too. For what one picture can't show:
an animation, a transition, something moving. `shotbox record OUT.mp4
[--seconds SECS] [--window RE] [--crop G] [--fps N]` records a video, as an
.mp4, .webm or .gif (30 frames a second, a GIF 15), for SECS, or until it's
stopped: Ctrl-C, `kill` when it runs in the background with `&`, its
session's display stopping. However it stops, even killed outright, the
file is finished, by a process of its own, which can still be making a
GIF after `shotbox run` returns. The
pointer isn't drawn unless `--pointer`, as in a picture (`--park` moves it
aside first). A window is recorded where it is when recording starts, as
far as it's on the screen: it isn't followed if it moves, and what's drawn
over it is recorded too. It needs ffmpeg, and works in X11 sessions only
for now. A wait that gives up takes a
picture of the screen with `--failed FILE`, or wherever `$SHOTBOX_FAILED`
says (`--env SHOTBOX_FAILED=...`).

Inside, `shotbox key CHORD...` presses keys (`ctrl+comma`, `Return`,
`alt+shift+Tab`), `shotbox type TEXT` types ASCII text, and `shotbox click
X Y`, `shotbox move X Y` and `shotbox drag X1 Y1 X2 Y2` work the pointer,
at a point on the screen or, with `--window RE`, inside a window (`click
--button 3`, `--double`). `shotbox park` moves the pointer to the screen's
bottom-right corner, off whatever it was hovering, and waits (up to
`--settle`, 2s) for the repaint; a screen that keeps changing is taken as it
is. `shotbox focus RE` gives a window the keyboard and raises it: on X11
there's no window manager to do that for a window that comes up after
another, a dialog say, and keys pressed go nowhere. They speak XTEST to the
display directly, so there's still nothing to install.

Keys and buttons can be held, for games and for anything that reacts to
how long they're down. `shotbox key --hold SECS CHORD...` presses the
chords together, keeps them down that long, and lets go, last first;
`shotbox click X Y --hold SECS` does that with a button. To hold one while
doing something else, run it in the background: `shotbox key --hold 2 w &
shotbox click 300 200 --hold 1`. On X11, `--down` presses and leaves it
down, across commands, until the same command with `--up`; on Wayland a
virtual keyboard's keys go up when the command ends, so `--down` is refused
there, saying to use `--hold` (a hold killed outright, which can't let go,
is cleared with `--up`). A chord already held isn't pressed again, or let
go by pressing it, and keys pressed or typed while one is held leave its
keys down. As
on a real keyboard, a shifted symbol held
with other keys shifts them too, and a key held past the repeat delay
(about half a second) repeats.

**`shotbox start [--name NAME] [options]`** starts a session that lasts
until `shotbox stop NAME`, and prints its name (`s1`, `s2`... without
`--name`). It takes the session options, as `run` does. Programs go in it
with `shotbox spawn --session NAME [--log FILE] -- COMMAND...`, and stop
with it; and `shotbox --session NAME COMMAND ...` runs any of the commands
above that work inside a session (`wait`, `capture`, `key`, `click`,
`a11y`...) in it, from outside. `shotbox sessions` lists them. For a
script, or someone at a terminal, that looks, clicks and looks again
without starting the program over each time:

```
shotbox start --name ed --a11y
shotbox spawn --session ed -- gnome-text-editor
shotbox --session ed wait window ".*Text Editor"
shotbox --session ed a11y click "Open"
shotbox --session ed capture open.png
shotbox stop ed
```

`shoot`, `run` and `start` take the session options: `--screen WxH`
(1280x800); `--env NAME=VALUE` and `--pass NAME` to set or let through
variables; `--seed DIR` to start the home as a copy of a directory (a
config, a profile, sample files);
`--desktop` for real GSettings and the system's D-Bus services, which a
desktop shell needs; `--system-bus` for a stand-in system bus; `--keep` to
keep the scratch dir and say where it is; `--wayland` and `--xwayland`
for a [Wayland](#wayland) session; `--a11y` for an
[accessibility bus](#accessibility); `--audio` for a PipeWire of the
session's own, speaking the pulse protocol too, whose one sink plays to
nowhere, so a program that makes sound has somewhere to play it (without
it there's no sound server at all); `--gamepads N` for
[gamepads](#gamepads); `--no-sandbox` for a program that needs a setuid
helper (see [What's sealed](#whats-sealed)); and the display presets:

- `--dark`: the dark style. libadwaita apps, GTK 4 and GTK 3.
- `--high-contrast`: the high-contrast style. libadwaita apps, GTK 4.20
  and later, and GTK 3.
- `--text-scale N`: text N times the size (`1.25`), as the desktop's
  text scaling does: GTK 3 and 4 on X11 and Xwayland, GTK 4 on Wayland.

Each is off unless asked for, so pictures without them don't change.
Reduced motion needs no preset: GTK's animations are always off.

### What's sealed

A session keeps a program from reaching your desktop by accident: from
opening a window on it, playing through your speakers, or picking up your
settings. It isn't a security boundary against a program that sets out to.

Sealed:
- The environment, rebuilt from an allowlist: no `DISPLAY`,
  `WAYLAND_DISPLAY`, session bus, SSH agent or anything else of yours but
  what `--pass` lets through (a socket it names in `/run/user/UID` isn't
  there under bubblewrap). `HOME` and every XDG directory are a scratch
  dir.
- A display, a session bus (nothing activatable on it), an accessibility
  bus with `--a11y`, and a sound server with `--audio` (its sink plays to
  nowhere), all the session's own.
- Under bubblewrap, which every program the session spawns runs in where it
  can: `/dev/snd` is empty, so nothing plays to the machine's sound
  devices, and `/run/user/UID` is (and `XDG_RUNTIME_DIR`, if that's
  elsewhere), so your desktop's Wayland, PulseAudio, PipeWire and bus
  sockets aren't there to find, and a program can't be run from inside
  it. With `--gamepads`, only the session's pads (see [Gamepads](#gamepads)).

Not sealed:
- The files: the filesystem is the machine's, readable and writable as
  you, outside the scratch home.
- The network: a server bound to `0.0.0.0` is on your network.
- The machine's system bus, unless `--system-bus`: logind,
  NetworkManager and the rest, which may let a program in your desktop's
  session suspend or power off the machine.
- Your X server: its sockets, in `/tmp/.X11-unix` and the abstract one,
  are reachable by a program that sets `DISPLAY` and finds your cookie
  itself.
- Other processes: a program can see them, and signal yours.
- What the session starts for itself, the display server, the buses and
  what they activate, and PipeWire, runs outside bubblewrap; and so does
  what a program in a Wayland session has sway start for it.

Without bubblewrap, or where it can't make a sandbox (user namespaces held
back, as Ubuntu's AppArmor does), the session says so and runs programs
without one: they can reach the sound devices and your desktop's sockets.
With a `TMPDIR` in `/run/user/UID`, which the session's scratch dir is
made in, your desktop's sockets there stay reachable; it says that too.
`--gamepads` needs it, and fails instead, and has it with `--no-sandbox`
too; and since `/dev/uinput` can't be opened under it, a session with
gamepads can't be started from inside another. Under bubblewrap a program can't
gain privileges (no setuid helpers: sudo, `fusermount`, so no AppImages),
its supplementary groups show as `nogroup`, and one killed by a signal
exits with 128 plus the signal's number; `--no-sandbox` runs programs
without it, for one that needs those. The pid `spawn` reports is the
program's own.

### Gamepads

`--gamepads N` gives the session N virtual Xbox 360 controllers (1 to 4),
made with Linux's uinput, so a game reads them as it reads a pad plugged
in: SDL names them "Xbox 360 Controller", and older games find them as
`/dev/input/jsN` too. Every program the session spawns (with `run`,
`shoot`, `term`, `spawn` and MCP's `spawn`) runs under bubblewrap with a
`/dev/input` of its own, holding only these. The machine's raw HID devices
(`/dev/hidraw*`, `/dev/usb/hiddev*`, `/dev/uhid`) and `/dev/uinput` are a
file no one can open inside, and `/dev/bus/usb` is empty, so no controller
of the machine's reaches a program by another way (SDL's HIDAPI drivers,
hidapi, libusb); the lists in `/sys` and `/run/udev` still name the
machine's devices, but none can be opened. That's settled as each program
starts: one plugged in while a program runs can reach it. What the session starts for
itself, the display server and what D-Bus activates, isn't sandboxed (on
Wayland, a program can have sway start one outside it), so this keeps a
game from finding your controllers, not one that sets out to; and
your desktop can see the virtual pads while the session runs (named
"shotbox virtual pad N"); they go when it stops, however it stops. They're
real devices on the machine, so what reads controllers on your desktop
meanwhile, Steam or a game, can act on what's pressed on them (Steam may
open its overlay for `guide`): close it, or use another machine. It needs
write access to `/dev/uinput` (which many desktops give the logged-in user,
and the `input` group has) and `bwrap` with user namespaces, which the
session checks for at the start.

Inside, `shotbox pad` works them. What's left down stays down between
commands, on X11 and Wayland alike, since a server in the session holds
the pads, and through taps and holds of the same button; `--up` lets go
of it, holds and all (a hold whose command was killed, say):

```
shotbox pad press a                     # tap A
shotbox pad press lb rb --hold 0.5      # together, for half a second
shotbox pad press b --down              # left down, until --up
shotbox pad stick left 1 0 --hold 1.5   # walk right for a second and a half
shotbox pad stick right -1 0            # aim left, and leave it there
shotbox pad --pad 2 trigger right 1     # the second pad's right trigger, all the way
shotbox pad neutral                     # let go of everything
```

Buttons are `a b x y lb rb back start guide ls rs` and the d-pad's `up
down left right`. A stick's x and y go from -1 to 1, with y down; a
trigger from 0 to 1.

**`shotbox term [options] -- COMMAND...`** is a terminal to take pictures of:
VTE, the engine behind GNOME Terminal, Ptyxis and Tilix, in a plain window
named `shotbox-term`.

- `--scheme FILE` colors it: a Tilix scheme as is, or
  `{"foreground", "background", "cursor", "palette": [16 colors]}`.
- `--size COLSxROWS`, `--font "Monospace 11"`, `--title NAME`.
- Steps, in order: `--when REGEX` waits for the screen's text to match,
  `--type TEXT` types (`\r` is Enter, `\e` Escape), `--sleep SECS`. After
  the last one it writes the ready file. The terminal stays open, its
  screen as the program left it, after the program exits: no `sleep 60` to
  keep it up.
- `--crop COLSxROWS` takes only the top-left cells: a wide terminal, so
  nothing wraps, and a picture of just the part that matters. It writes
  the size in pixels to `$SHOTBOX_SCRATCH/crop`, which `shoot` crops to
  when it has no `--crop` of its own.
- `--shoot OUT.png` starts the session and takes the picture too, taking
  `shoot`'s session options, `--park` and `--failed`.
- `--log FILE` keeps every byte the program wrote, escape codes and all
  (via `script`, whose first line is its own header). Colors are hard to
  judge in a picture and easy to grep in the log.

```
shotbox term --shoot irssi.png --scheme dark.json --size 88x24 \
  --when 'End of /NAMES' --type 'hello\r' -- irssi -c 127.0.0.1
```

which is short for `shotbox shoot irssi.png --window shotbox-term --wait
ready -- shotbox term ...`.

**`shotbox compare A.png B.png [--diff D.png] [--fuzz 1%] [--max N]`**
counts the pixels that differ and fails if more than `--max` (0) do. With a
committed picture, that's a visual regression test.

**`shotbox vision IN.png OUT.png --as KIND`** writes a picture as someone
with another kind of color vision sees it: `protanopia` (no red cones),
`deuteranopia` (no green), `tritanopia` (no blue), or `gray` (no color).
It's Machado, Oliveira and Fernandes' model (2009) at full severity, on
linear RGB. Anything told apart only by color (a status, a series in a
chart, a team in a game) that looks the same in one of them needs another
cue. `capture` and `shoot` take `--vision KIND` to take a picture that way.

**`shotbox compare REFDIR NEWDIR [NAME...]`** does that for each picture in
REFDIR (or each NAME), against the one of the same name in NEWDIR, a line
each, and fails if any differs, is missing, or changed size. `--diff DIR`
writes `NAME-diff.png` there for each that fails. `--fuzz` and `--max`
take `NAME=VALUE` for one picture, beside a plain VALUE for the rest:

```
shotbox compare data/screenshots fresh --fuzz 0.5% --fuzz video=5% --diff fresh
```

## Accessibility

`--a11y` starts an accessibility (AT-SPI) bus of the session's own, so
what a screen reader would hear can be read and checked. Without it, a
session has none: GTK is told not to look. Your desktop's bus is never
touched either way.

Inside `shotbox run --a11y`:

- **`shotbox a11y tree [--window RE]`** prints the tree, a node a line,
  indented: role, name, description if any, and the states a screen
  reader would say (focusable, focused, selected, checked, pressed,
  expanded or collapsed, editable, disabled, ...). Hidden nodes are left
  out. It comes out the same each run, to commit and diff.
- **`shotbox a11y lint [--window RE] [--ignore RE]`** fails, a line each, for what has
  no name to say: a button, toggle, check box, radio button, menu item,
  link, entry, slider, combo box or tab; an image not inside a named
  control (a decorative one should be role presentation, which leaves it
  out of the tree); and anything else that takes the focus, but for
  what has a named node inside it, which is said by that, and a
  container (a scrolled window, a list, a notebook) that passes the
  focus on to something inside it. Each line is
  the node's path, its roles and their places from the window down, then
  what's wrong; `--ignore RE` (repeatable) leaves out a line it's found in,
  for what can't be fixed yet.
- **`shotbox a11y tabwalk [--key Tab] [--max 50] [--window RE]`** presses
  the key until the focus lands somewhere it has been, printing where it
  lands each time. It fails if a press leaves the focus where it was (a
  trap: a text view keeps Tab), unless nothing else could take it, or
  after `--max`. With `--visible`, it also fails where the focus doesn't
  show: fewer pixels in and round a node changed as it took the focus
  than half a one-pixel outline round it has (a caret alone, or nothing).
- **`shotbox a11y click NAME [--role RE] [--window RE]`** clicks the
  middle of the node whose name matches NAME (a regex, matched in full),
  waiting for it to show: a button by its label rather than where it is.
  A match inside another (a button's label) is the outer one; two that
  aren't fail, saying which.
- **`shotbox a11y contrast [--window RE]`** measures, on a picture of the
  screen, with the `--window` raised, the contrast of what each node
  draws in its bounds: text, and buttons, links and tabs, which may draw
  text or an icon, against their background, at least 4.5:1 (`--text`),
  and images at least 3:1 (`--graphics`). `--edges 3` also measures a
  button's or field's edge against what's round it. It fails, a line
  each: `PATH: text 2.51:1, #a0a0a0 on #fafafb (at least 4.5)`.
  Disabled nodes, switches, sliders and check boxes, and what's scrolled
  out of view are left out; `--ignore RE` leaves out a line, as for lint.
- **`shotbox wait announce RE`** waits for a program to announce
  something matching RE (found anywhere in it) to a screen reader:
  GTK 4.14's `gtk_accessible_announce()`, or text put in a browser's
  live region (`aria-live`, `role="status"`). Each wait takes the first
  match no wait has taken yet, so the same thing said twice is waited
  for twice. Everything announced goes to
  `$SHOTBOX_SCRATCH/announcements`, or `--announce-log FILE` (which
  implies `--a11y`), a line each (a line break as `\n`): `polite: Saved
  to notes.md`.

```
shotbox run --a11y -- sh -c 'gnome-text-editor &
  shotbox a11y lint --window ".*Text Editor"'
```

finds that the editor's text area has no name.

`--window` picks a window by title, or `app=RE`, as everywhere, and
waits up to `--timeout` (10s) for it to appear in the tree. Without it,
every program's windows are there. It works on X11, Wayland and
Xwayland, for GTK 4, GTK 3, Qt 5 and AccessKit's toolkits (egui, Slint,
Bevy, GTK built with it), and for Chromium, headed, given
`--force-renderer-accessibility` (the Node package's `accessible`), on
X11. How it's put together, and what each check can and can't tell:
[docs/accessibility.md](docs/accessibility.md).

## Wayland

`--wayland` makes a session a Wayland one: the program runs under a
headless sway, drawing in software, instead of on Xvfb. `--xwayland` adds
an Xwayland to it, and `DISPLAY`, so an X client runs the way it does on a
Wayland desktop, GTK programs made to with `--env GDK_BACKEND=x11`.

```
shotbox shoot editor.png --wayland --window app=org.gnome.TextEditor -- gnome-text-editor
```

Everything else is the same: windows by title or `app=`, pictures, waits,
failure pictures, keys, text, the pointer, `park`, `term` and the Python
API (`shotbox.Session(wayland=True)`, or `here()` inside). Windows come
from sway's IPC socket and pictures from `grim`; keys and the pointer go
over sway's virtual keyboard and pointer, from a small client of
shotbox's own, as XTEST does on X11.

The pictures aren't X11's: GTK draws its own decorations on Wayland,
and under Xwayland, with no XSETTINGS, its text at 96 dpi. A committed
picture belongs to one backend.

Needs `sway` and `grim`, and `Xwayland` for `--xwayland`:
`sudo apt install sway grim xwayland`. Why sway, and how it fits:
[docs/wayland.md](docs/wayland.md).

## Python

A script that takes many pictures can drive the display itself rather than
start a `shotbox` process for every click. Inside `shotbox run`,
`shotbox.here()` is the session's display; outside, `shotbox.Session` starts
one. Both are a `Screen`:

```python
import shotbox

with shotbox.Session(size=(1600, 1000), failed="out/chat-failed.png") as s:
    s.spawn(["gtkhx"], log="gtkhx.log")
    s.wait_window("GtkHx.*")
    s.click(560, 660, window="GtkHx.*")
    s.type("/clear\n")
    s.wait_stable(window="GtkHx.*")
    s.capture("out/chat.png", window="GtkHx.*", park=True)
```

- Waits: `wait_window(re)` (a title, or `app=RE`; returns
  `(id, name, w, h, x, y)`),
  `wait_port(n)`, `wait_file(path)`, `wait_ready()`,
  `wait_stable(secs=0.5, window=None)`, and `until(what, test)` for a test
  of your own; each takes `timeout` (30).
- Input: `key(*chords, hold=None, state=None)`, `type(text)`, `click(x, y,
  window=None, button=1, double=False, hold=None, state=None)`, `move`,
  `drag`, `park()`. One X connection, kept. In a session with `gamepads`:
  `pad_press(names, n=1, hold=None, state=None)`, `pad_stick(which, x, y,
  n=1, hold=None)`, `pad_trigger(which, value, n=1, hold=None)` and
  `pad_neutral(n=1)`.
- Pictures: `capture(out, window=None, crop=None, park=False,
  vision=None)`, `burst(out, count, every=0, ..., sheet=None)`, and
  `window(re)` for where one is.
- Anything that fails raises `SessionError`, saying what; with `failed` set
  (or `$SHOTBOX_FAILED` inside `shotbox run`) it leaves a picture of the
  screen there first, and says where.
- Accessibility, in a session with `a11y=True`: `a11y_tree(window=None)`,
  the nodes (dicts, and attributes: `role`, `name`, `description`,
  `states`, `path`, `depth`, and `bounds` on the screen); `find(role=None,
  name=None, window=None)`, `name` a regex; `focused()`; `wait_focus(re)`;
  `tab_walk(key="Tab", most=50, window=None, visible=False)`, the nodes
  the focus lands on, failing on a trap (and with `visible`, where the
  focus doesn't show); `a11y_lint(window=None, ignore=())` and
  `contrast(window=None, text=4.5, graphics=3, edges=None)`, the nodes
  with a `problem`; `click_node(name, role=None, window=None)`; and
  `wait_announce(re)`. `focus_window(re)` gives a window the keyboard.
- A `Session` also has `spawn(cmd, log=None)`, `run(cmd)`, `env` and
  `scratch`. It takes the session options as arguments: `a11y`,
  `announce_log`, `audio`, `dark`, `high_contrast`, `text_scale`,
  `wayland` and the rest.

Installed, `import shotbox` just works. With a checkout, put its root on
`PYTHONPATH` (`--pass PYTHONPATH` to let it
into a session).

## Node

`node/` is a small package for browser checks with Playwright, from the
pieces several projects had copied between them:

```js
import { serve, checks, pageErrors, launch, until, gif, sealed,
         dress, film, frames, steady, accessible } from 'shotbox';
```

- `serve(dir, port = 0)`: a static server on localhost that serves ES
  modules with the right type; port 0 picks a free one.
  `shotbox-serve [DIR] [PORT] [PAGE]` runs it by hand (port 8080) and
  prints the address of PAGE, for a site whose page isn't at its top:
  `"demo": "shotbox-serve . 8080 demo/"` in a project's scripts.
- `checks()`: `check(cond, what)` and `skip(what)` print `ok`, `FAIL` or
  `skip` lines; `done()` prints the tally and returns the exit status.
- `pageErrors(page)`: every uncaught exception and `console.error`.
- `launch(cmd, args)`: a program in its own process group, with `kill()`
  that reaches its children and `why()` that says how it ended and what it
  printed. `until(test, ms)` polls.
- `gif(film, out, { width, fps, from, colors, dither })`: a Playwright
  recording, or a directory from `frames()`, as a GIF, via ffmpeg, with its own palette. `from` cuts that
  many seconds off the start, `colors` caps the palette, and `dither` is
  ffmpeg's (`none` for flat pages with something moving on them).
- `sealed({ pass, env })`: an environment for a headless browser that
  nothing of yours gets into, as a session's but with no display.
  Otherwise Chromium's fonts come through your `~/.config/fontconfig` and
  `~/.local/share/fonts`, and your pictures aren't anybody else's. Give
  its `env` to `chromium.launch({ env })` and `close()` it afterwards.
  `pass` (a list of names) and `env` (an object) do what `--pass` and
  `--env` do.
- `dress(page, { hold })`: a pointer and captions for a recording, since
  a browser records neither its cursor nor a key pressed. A dot follows
  the mouse and shrinks while a button is down; `caption(text)` shows a
  caption at the bottom for `hold` ms (1300). Both come back after a
  reload; `remove()` takes them out for a still.
- `film(page)`: where the loop starts in a recording. Call it right after
  `newPage()`, `start()` when the loop begins, and `end()` closes the
  context and resolves to the video, for `gif(video, out, { from:
  reel.from })`. Playwright writes a frame only when the page repaints.

- `frames(page, { dir, fps, epoch, seed })`: a recording made a frame
  at a time that comes out the same each run, where `recordVideo` races
  the page and never does. The page's time stands still and moves a
  frame's worth between screenshots: timers, `Date`,
  `requestAnimationFrame`, CSS transitions and animations, and a seeded
  `Math.random`. Launch Chromium with `args: steady`, and call it before
  the page's first navigation. `run(ms)` lets page time pass unrecorded,
  `start()` starts keeping frames, `hold(ms)` stands in for
  `waitForTimeout` and `move(x, y, ms)` for `mouse.move({ steps })`;
  other input lands between frames. `end()` says where they are, for
  `gif(dir, out, { fps })`. Runs match frame for frame, give or take a
  few pixels one level apart; what is real-time and not a timer (a
  fetch, a worker, an iframe loading) is not stood still, though a
  frame that navigates is let load and paint before time moves on.

- `accessible`: Chromium's launch options for `shotbox run --a11y`,
  `chromium.launch({ ...accessible })`: headed, with the switch that
  puts a page's part of the tree on the bus, and without LCD text, whose
  tinted glyphs `shotbox a11y contrast` would measure low.

```js
const context = await browser.newContext({ recordVideo: { dir } });
const page = await context.newPage();
const reel = film(page);
await page.goto(url);
const dressing = await dress(page);
reel.start();
await page.mouse.move(400, 300, { steps: 24 });
await dressing.caption('Alt  Enter');
await page.keyboard.press('Alt+Enter');
await gif(await reel.end(), 'demo.gif', { from: reel.from });
```

It has TypeScript declarations, written by hand beside each module and
checked by `npm run types`. `dress`, `film` and `pageErrors` take
Playwright's `Page`, so they want Playwright's own types installed.

`npm install --save-dev shotbox`. Its `package.json` is at the top of the
repository, since npm installs from the top of a git repository and
nowhere else, so `"shotbox": "github:mishan/shotbox"` in `devDependencies`
works too, or `"file:../shotbox"` for a checkout beside yours.

## Tests

`test/run.sh` checks the session, the terminal, the pictures, the Python
API (`test/api.py`, which needs GTK 3's PyGObject) and the Node helpers, and
takes a few seconds. The helpers that need a browser use Playwright's
Chromium (`npm install && npx playwright install chromium`) and skip
without it, the Wayland sessions need sway, grim and Xwayland and skip
without them, the accessibility checks need GTK 4, libadwaita and
at-spi2-core and skip without them, `--audio` needs PipeWire,
WirePlumber and `pactl` and skips without them, and `--gamepads` needs
bubblewrap and a writable `/dev/uinput` and skips without them.

## License

MIT. See [LICENSE](LICENSE).
