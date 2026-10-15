# Commands

Every command, and the options sessions take. `shotbox COMMAND --help`
says each one's options.

## Needs

Every session needs `Xvfb`, `xauth`, `dbus-daemon`, ImageMagick, `xwininfo` and `xprop`; for
`shotbox term`, PyGObject with VTE (`gir1.2-vte-2.91`); for `--a11y`,
at-spi2-core, PyGObject's Atspi (`gir1.2-atspi-2.0`) and `gdbus`; for
`--desktop`, `gdbus`; for `--audio`, PipeWire and WirePlumber; for
`--gamepads`, write access to `/dev/uinput`; for `record`, ffmpeg; for
`--clock`, libfaketime; and
bubblewrap, which `--gamepads` needs, and which [seals](sealed.md)
more where it's there.

`shotbox doctor` says which of these are here, a line each: what
sessions need, and each of their options, `term` and `record`. A command
missing one fails before it starts anything, in a line saying what's
missing and how to get it, as `Session` and MCP's `start_session` do.
`doctor` fails only if what every session needs is missing.

## shoot

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
- `--twice` takes the picture in two fresh sessions, one after the other,
  and fails if they differ, saying how many pixels and the box they're in
  (`210 pixels differ, in 12x18 at 30,212`: a blinking cursor), with the
  difference marked in red beside OUT, as `OUT-diff.png`; if they're
  alike, one is kept as OUT. A picture is only worth committing if it
  comes out the same every time, and this proves it does. `--fuzz` and
  `--max` as `compare` takes them, for a program that dithers. It doubles
  the time, so it's for now and then: a new picture, or before a release.
  `term --shoot` takes it too.

## run, wait, capture and record

**`shotbox run [options] -- COMMAND...`** runs a command in a sealed session
and exits with its status. For scripts that take several pictures: inside,
`shotbox wait window|port|file|ready|stable ARG` and `shotbox capture OUT.png
[--window RE] [--crop G] [--park] [--vision KIND]` work on the session's
display. `shotbox wait stable [SECS] [--window RE]` is the one to use after a
click, in place of a sleep. `shotbox wait match REF.png [--window RE] [--crop
G]` waits until it looks like a picture, for a screen with no other sign
it's up (a boot, a login screen), and `shotbox wait change [--window RE]
[--crop G]` until it looks different from the first picture the wait takes
(a window still drawing is a picture too: `wait stable` first); each
counts pixels as `shotbox compare` does (`--fuzz PCT%`, `--max`), and prints how
long it took. `shotbox wait line RE FILE` waits for a line matching RE (found
anywhere in it) to be written to FILE, and prints it: for a program that logs
what happens, a line an event, where a sleep would go by the wall's clock
and a game's can stop. Lines written before the wait starts don't count
(`--from-start` for those too), nor one not yet ended; a file not there yet
is waited for, and one rotated or written again from the top is read from
its start (lines written to a rotated one just before it went may be
missed). `capture --burst N [--every SECS] [--sheet
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
over it is recorded too. It needs ffmpeg, and on Wayland wf-recorder;
sway draws no pointer, so there `--pointer` is refused. `shotbox record
OUT.wav` records what plays in a session with `--audio` instead: the
sink's mix, 48 kHz 16-bit stereo, so what plays in that format comes
through bit for bit, and `--seconds` is to the sample.
On X11 or Wayland, and it stops the same ways. `shotbox record OUT.mp4
--sound` (or .webm), in a session with `--audio`, records the picture and
what plays together, in one file, AAC in an MP4, Opus in a WebM: the
sound in step with the picture from its first frame, within a frame or
so, and both `--seconds` long. Then it says what `shotbox listen` would
of the sound, its level and where it clipped or dropped out, judged by
`listen`'s `--gap`, `--clipped` and `--quiet`; a silent stretch between
sounds is a gap there too. It doesn't fail for it unless `--strict`, which
fails as `listen` does, keeping the file. If the session's sound server
stops, the recording stops with it, and says so. The sound is heard from
a WAV copy, unnamed in TMPDIR, read before `record` returns: 11.5 MB a
minute, on disk and then in memory. Without `--audio`, or as a GIF or
WAV, it's refused. A wait that gives up takes a
picture of the screen with `--failed FILE`, or wherever `$SHOTBOX_FAILED`
says (`--env SHOTBOX_FAILED=...`).

## key, type, click, move, drag, park and focus

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
display directly, or on Wayland sway's virtual keyboard and pointer, so
they need nothing installed.

## Holding keys and buttons

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

## latency

`shotbox latency move X1 Y1 X2 Y2 --watch WxH+X+Y [--window RE]` measures
how long what's drawn takes to follow the pointer: it moves it from one
point to the other and back, `--times N` (10), and times each move from
sending it to the first picture in which the `--watch` box changed,
then prints the median and the 95th percentile. On X11 that's to within
a millisecond or so; on Wayland, grim takes its picture late in its run,
and moves read a few milliseconds long. Before each move the box holds
still for `--settle SECS` (0.25), or twice the longest move yet, and the
first move's own redraw is waited out, for up to a second (or four
times `--settle`): a program whose redraws come
later than that would have one taken for the next move's. The pointer itself isn't in the
pictures, so what's measured is what the program draws for it: a hover,
or a pointer of its own, as an emulator draws.

## flicker

`shotbox flicker [--window RE] [--crop G] [--seconds 2]` watches for a
part of the screen that keeps switching between two looks, or flashes
another once and comes back, and fails (1) if one does: a strip of the
buffer a game isn't showing, say, or a frame drawn black. Not being able
to watch exits 2. It takes pictures as fast as they come, cuts each into
16-pixel tiles (`--tile`), and times each tile's looks, not counted in
pictures, since a program draws at its own rate. A tile's mean color
finds what might be, a change in it of a quarter of `--fuzz` (6%), so a
band a pixel wide is seen if it's bright (a channel moving 60 or more of
255; `--tile 8` halves that); its pixels decide:

- Flicker: A, B, A, B..., each held no longer than `--brief` (100 ms),
  `--times` changes (8) or more, kept up, in one place: the pixels most
  changes change, by a quarter of `--fuzz` or more, are most of what
  most of them do, and at least a line across the tile, its shorter
  side, so something passing through the rest of it, or a scene moving
  on in a column or two beside, doesn't hide it. Both looks come back as
  the same pictures most times, nearer than A is to B, or one of them
  does, one color where it differs from the other: black between frames
  of a scene that moves on, as bzflag's strip was, the scene not one
  color, or a shape of it, as pages of text between blanks aren't. Said
  with its box (whole tiles, in what's watched: the window or the crop),
  how many changes, how often, and the colors of what changed.
- A flash: A, B, A, from a picture that held still, to one color where
  it differs (a frame drawn black), a shape of it, a band, a line, an
  icon, as scattered strokes of text drawn once aren't, and back; each
  said. A line no more than two pixels wide, apart from what's either
  side of it, gone for a picture to the color of one side isn't one, nor
  is something moving through, which turns the tiles beside it that
  held still the same way, briefly, with its colors, within `--brief`,
  or a picture, before or after: beside all that showed only while the
  flash did. Nor is one in a box most of which went other ways, a region
  drawn anew, as a terminal scrolling a page at a time is, or one a thin
  thing stepped out of into a tile beside, a line's step. A flash drawn
  torn, part of it a picture later, is one. A frame drawn black between
  two different views, A, black, C, isn't found.

Nor is a burst of fewer changes than `--times`: a double or triple
flash, or a strip flickering only an eighth of a second. `--times 4`
finds those, and with them some things passing through: a ground line's
step going back and forth, something crossing a tile in a fifth of a
second. A watch too short, at the rate pictures came, to hold `--times`
changes is said.

A caret's half-second blink is neither, nor is something moving through,
a thin line's steps crawling, an edge between two faces a little apart
sweeping past, text scrolling past (as many of its looks as not in the
picture before or after, up or down), or a frozen frame that isn't one
color between live ones. `--ignore G` leaves out a box that flickers on
purpose. Pictures are kept as they're taken and looked at after, a tile
again only where it changed: a busy screen, every picture different, is
pictured more slowly, kept at about 150 MB a second of watching, and
takes some seconds to look at, so watch a window or a crop where you can:

```
flicker: 64x16+1216+592, 66 changes, every 30 ms, #000000 and #009900, from 0.022s
watched 2 s, 104 pictures, 52 a second, 104 changed
```

The last line, how many pictures were taken, is printed whatever's found.
Pictures that came fewer than two to a `--brief` are said: a look that
brief may be missed, and something moving through taken for a flash.

## exec

**`shotbox exec [options] -- COMMAND...`** runs a command sealed the same
way, but with no display: for what draws nothing, a headless browser or a
test suite, which should reach neither your desktop nor your speakers. It
takes the session options that aren't about a display: `--env`, `--pass`,
`--seed`, `--keep`, `--desktop`, `--system-bus`, `--audio`, `--net`,
`--identity`, `--clock`, `--tz` and `--no-sandbox`.

## start, spawn, stop and sessions

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

## term

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

## compare

**`shotbox compare A.png B.png [--diff D.png] [--fuzz 1%] [--max N]`**
counts the pixels that differ, and says the box they're in, and fails if
more than `--max` (0) do. With a committed picture, that's a visual
regression test.

**`shotbox compare REFDIR NEWDIR [NAME...]`** does that for each picture in
REFDIR (or each NAME), against the one of the same name in NEWDIR, a line
each, and fails if any differs, is missing, or changed size. `--diff DIR`
writes `NAME-diff.png` there for each that fails. `--fuzz` and `--max`
take `NAME=VALUE` for one picture, beside a plain VALUE for the rest:

```
shotbox compare data/screenshots fresh --fuzz 0.5% --fuzz video=5% --diff fresh
```

## vision

**`shotbox vision IN.png OUT.png --as KIND`** writes a picture as someone
with another kind of color vision sees it: `protanopia` (no red cones),
`deuteranopia` (no green), `tritanopia` (no blue), or `gray` (no color).
It's Machado, Oliveira and Fernandes' model (2009) at full severity, on
linear RGB. Anything told apart only by color (a status, a series in a
chart, a team in a game) that looks the same in one of them needs another
cue. `capture` and `shoot` take `--vision KIND` to take a picture that way.

## listen

**`shotbox listen IN.wav`** says how loud a recording of a session's
sound is (`shotbox record OUT.wav`), its peak and RMS in dBFS, and where
it dropped out or clipped, and fails if it did, or is silent. A program
that falls behind feeding its sound leaves the sink silent, which
PipeWire fills with exact zeros: a run of them in every channel, `--gap
MS` (1) or longer, between the first sound and the last, is a gap, said
with where it is and how long. Silence before and after the sound isn't
one, and a program that means to be silent for a while needs a longer
`--gap`. `--clipped N` samples at full scale are allowed (0), and a peak
under `--quiet DB` (-60) is silence. Glitches that leave no silence, a
click or a jump in the wave, it doesn't hear. From Python,
`shotbox.sound.hear(path)` (`import shotbox.sound`) returns the same, as a
dict.

```
shotbox run --audio -- sh -c 'shotbox record soak.wav --seconds 60 & ./app; wait'
shotbox listen soak.wav
```

## a11y and wait announce

In a session with `--a11y`: [Accessibility](accessibility.md#commands).

## pad

In a session with `--gamepads N`: [Gamepads](gamepads.md).

## mcp

Sessions for an agent, over MCP: [mcp.md](mcp.md).

## Session options

`shoot`, `run` and `start` take the session options: `--screen WxH`
(1280x800); `--env NAME=VALUE` and `--pass NAME` to set or let through
variables; `--seed DIR` to start the home as a copy of a directory (a
config, a profile, sample files);
`--desktop` for real GSettings and the system's D-Bus services, which a
desktop shell needs; `--system-bus` for a stand-in system bus; `--keep` to
keep the scratch dir and say where it is; `--wayland` and `--xwayland`
for a [Wayland](wayland.md) session; `--a11y` for an
[accessibility bus](accessibility.md); `--audio` for a PipeWire of the
session's own, speaking the pulse protocol too, whose one sink plays to
nowhere, so a program that makes sound has somewhere to play it (without
it there's no sound server at all); `--gamepads N` for
[gamepads](gamepads.md); `--usage [FILE]` to say, when it stops, what its programs used, and
its own processes (see below); `--net loopback` for a network of the session's own
(see [What's sealed](sealed.md)); `--identity USER@HOST` for a user and host name
that aren't yours (see [What's sealed](sealed.md)); `--clock TIME` and `--tz ZONE` for
a wall clock that starts at TIME (see [The clock](#the-clock)); `--no-sandbox` for a program
that needs a setuid helper (see [What's sealed](sealed.md)); and the display presets:

- `--dark`: the dark style. libadwaita apps, GTK 4 and GTK 3.
- `--high-contrast`: the high-contrast style. libadwaita apps, GTK 4.20
  and later, and GTK 3.
- `--text-scale N`: text N times the size (`1.25`), as the desktop's
  text scaling does: GTK 3 and 4 on X11 and Xwayland, GTK 4 on Wayland.

Each is off unless asked for, so pictures without them don't change.
Reduced motion needs no preset: GTK's animations are always off.

## Resource use

`--usage` says, when the session stops, what its programs used, all of
them together, and, apart, what its own processes did (its display,
buses and sound server): peak memory and CPU time, over how long:

```
shotbox: used, over 41.0s: its programs peak memory 1412 MB, CPU 12.3s (cgroup, with page cache); the session's own peak memory 81 MB, CPU 0.4s (sampled, resident)
```

`--usage FILE` writes it as JSON too, for comparing runs; with `start`,
it's written there when the session stops. Where the user's systemd can
make scopes (`systemd-run --user`), each program runs in one, in a slice
of the session's, which counts every process it ever had, a spike that
lasts a moment included, and the memory charged to it: what its
processes hold, and the page cache and tmpfs files they made too. That's
`cgroup, with page cache`. Elsewhere, and for the session's own
processes, `/proc` is read five times a second for what the processes
hold in memory, theirs and their children's: a peak between reads is
missed, and so is the CPU time of a process that comes and goes between
them, or one left behind by a parent that's gone, which the user's
systemd takes in. That's `sampled, resident`. Compare runs measured the same way.
From Python, `Session(usage=True)` and its `usage()`; over MCP,
`start_session`'s `usage`, said when it's stopped.

## The clock

Pictures carry the wall clock: a panel's, a chat's timestamps, "Today" in
a mail list, a `git log`'s dates. So that a reshoot only differs where the
program does, `--clock TIME` starts the clock at TIME, ISO 8601
(`2026-09-26T18:40:00`), as the session starts, and lets it run on, so
animations and timeouts work as ever. TIME is in `--tz ZONE`
(`Europe/Berlin`) if it's given, else UTC, unless it has an offset of its
own (`+02:00`, `Z`), which it keeps, `--tz` then only the zone it's shown
in; and `--tz` is every program's `TZ`, so `date` and a panel clock read
the same. It's the session's clock,
not each program's: one spawned a minute in reads a minute past TIME, as
the first does by then.

It's libfaketime, preloaded into every program the session starts, and a
session with `--clock` fails without it. It covers what's dynamically
linked and asks the C library: GTK, Qt, Emacs, irssi, Python, git. Static
binaries and Go programs read the clock themselves, and setuid ones (with
`--no-sandbox`) drop the preload: they see the real time. Monotonic clocks
aren't moved, and file times are what's on disk: the dates of files a test
makes are its own to set (`touch -d`). From Python, `Session(clock=...,
tz=...)`; over MCP, `start_session`'s `clock` and `tz`.
