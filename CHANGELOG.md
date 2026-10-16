# Changelog

## Unreleased

### Added

- **MCP's server instructions**: as it connects, `shotbox mcp` tells the
  agent how its tools go together, which clients give the agent and
  directories show.
- `record --until-line RE FILE [--tail SECS]`: a recording that ends on
  an event, once a line matching RE is written to FILE, as `wait line`
  waits for one, and SECS after it; `--seconds` is then the most it
  lasts. It prints the line, or says it was stopped or ran its
  `--seconds` first. `Screen.record(until=(re, path), tail=...)`, whose
  Recording says the `line` and whether it was `stopped`; MCP's `record`
  takes `until_line`, `file` and `tail`, its file in the `--allow` file's
  directory, as `wait_for`'s line is.
- `SHOTBOX_SANDBOX=off` in the environment: `--no-sandbox` for every
  session, from Python and MCP too, and for one started inside a session,
  for a Dockerfile or CI job to set once (`false` and `0` too). `shotbox
  doctor` says whether sessions have a sandbox, and why not.

### Changed

- A session that can't make a sandbox says so only where it would have
  hidden something: not in a container with no sound, input, HID or USB
  devices and nothing in `/run/user/UID` or `XDG_RUNTIME_DIR`. Where it
  says so, it says how to say it's meant.

### Fixed

- `wait line` with a pattern that isn't a regex says so, rather than
  with a traceback.
- `record --sound --seconds N` whose session's sound server stops first,
  as a lasting session's does when it's stopped: the picture ends with
  the sound, rather than running on for up to a second past it.

## 0.6.0 — 2026-10-15

Sound, and video with it, on X11 and Wayland; flicker, latency and what a
session used, measured; a session's own clock, identity and network;
timed scripts of input; and checks that a picture comes out the same
twice. Pictures of GTK 4 programs change: see the first note below.

### Changes to check when upgrading

- **GTK 4 draws text with manual font rendering** (GTK 4.16 and later),
  so text is drawn at its color rather than fainter, by however much
  automatic rendering draws it on that GTK release. Every GTK 4 picture
  with text changes, its widths too, so layout can shift, and `a11y
  contrast` measures thin text higher: take committed GTK 4 pictures
  again. A seeded home whose GTK 4 `settings.ini` sets
  `gtk-font-rendering` keeps its own.
- Under bubblewrap, a program can't gain privileges, its supplementary
  groups show as `nogroup`, and one killed by a signal exits with 128
  plus the signal's number. `--no-sandbox` runs it as before.
- `compare` says where pixels differ, after how many: `N pixels differ,
  in WxH at X,Y`, on each picture's line with directories too.
- A session with a display wants ImageMagick, and one with X11 or
  Xwayland `xwininfo` and `xprop` (x11-utils), before it starts, rather
  than at its first wait or picture.
- `Screen.record` returns a Recording, not the path: its `path`, and
  with sound what was heard. It stands in for the path where one is
  opened (`os.fspath`) and in `str()`, but `.suffix` and the rest of a
  Path's are on `.path`.

### Changed

- **Waits look faster.** `wait stable`, `match` and `change` take their
  pictures straight from the X server (a few milliseconds, where `import`
  took tens) or from grim's raw output on Wayland, and compare them in
  memory, with no PNG between; `match` and `change` look every 10 ms. A
  change is seen within about a frame, and the time `change` says is that
  close.
- **Every program a session spawns runs under bubblewrap where it can,**
  not only with `--gamepads`: `/dev/snd` and `/run/user/UID` are empty
  for it, so it can't play through the machine's speakers or find the
  desktop's Wayland, PulseAudio, PipeWire or bus sockets. Without
  bubblewrap, the session says so and runs it as before.
- A window asked for and not there is said with the titles, or app ids,
  there are, and that it's matched in full.
- **The README is a front page**, the reference moved to docs/commands.md,
  sealed.md, gamepads.md, python.md and node.md, the a11y commands to
  docs/accessibility.md, the Wayland example to docs/wayland.md, and the
  tests to CONTRIBUTING.md.

### Added

- **`shotbox play SCRIPT`**: a script of timed input, played in a
  session on one clock, with no command started for each step, so a demo
  plays out the same way each time it's recorded. A line is a time
  (seconds from the start, or `+SECS` after the step before) and a
  command for inside a session; one ending in `&` runs on in the
  background, a recording say, and `${NAME}` is the session's variable.
  Each line's options are checked before the first runs, and a failure
  says its file and line. Also `Screen.play`, which takes the text, or a
  Path to the file, and `vars` for `${NAME}`.
- **`wait line RE FILE`**: wait for a line matching RE to be written to
  a file, and print it, for a script that follows a program's log of
  what happens rather than sleeping by a clock the program may not
  keep to. From the file's end when the wait starts (`--from-start` for
  the top); a file not there yet is waited for, and one rotated or
  rewritten is followed. Also `Screen.wait_line`, and MCP's `wait_for`,
  whose file is in the `--allow` file's directory.
- **`shoot --twice`** (and `term --shoot --twice`): the picture taken in
  two fresh sessions, and a failure if they differ, beyond `--fuzz` and
  `--max`, saying how many pixels and the box they're in, with
  `OUT-diff.png` beside OUT. If they're alike, one is kept as OUT.
- **`--identity USER@HOST`**: who and where a session's programs are,
  for `run`, `shoot`, `term`, `start`, `exec`, `Session(identity=...)`
  and MCP's `start_session`: `hostname`, `uname -n`, `whoami`, `id
  -un`, `getpwuid()`, `ls -l`'s owner, `$USER`, `$LOGNAME` and
  `$HOSTNAME` say USER and HOST, so a picture doesn't show who took it.
  It needs bubblewrap, and not `--no-sandbox`.
- **`--clock TIME` and `--tz ZONE`**: every program a session runs sees
  the wall clock start at TIME (ISO 8601, in ZONE or UTC) and run on, a
  program spawned later reading the same clock as the first, so dates in
  pictures don't change between shoots. For `run`, `shoot`, `term`,
  `start`, `exec`, `Session(clock=..., tz=...)` and MCP's
  `start_session`; by libfaketime, which doesn't reach static binaries or
  Go programs.
- **What a command needs, checked before it starts anything.** A tool
  not on PATH, `/dev/uinput` it can't open for `--gamepads`, or PyGObject
  with VTE (`term`) or AT-SPI (`--a11y`) that this Python can't load is
  said at once in a line, with the package to install, or, from a
  virtualenv that can't see the system's packages, how to make one that
  can (for pipx's, `pipx install --force --system-site-packages
  shotbox`); `Session` and MCP's `start_session` say the same.
  **`shotbox doctor`** goes through every check, a line each, and fails
  only if what every session needs is missing.
- **`--usage [FILE]`**: what a session's programs used, together, and
  apart, its own processes: peak memory and CPU time, from a systemd
  slice of the session's where the user's systemd can make one, else
  read from `/proc`; as JSON to FILE. `Session(usage=True)` and MCP's
  `start_session` take it too.
- **`shotbox latency move X1 Y1 X2 Y2 --watch G`**: how long what's
  drawn takes to follow the pointer, a move at a time, as the median and
  95th percentile; `Screen.latency` from Python.
- **`shotbox flicker`**: a part of the screen that keeps switching
  between two looks, or flashes another once, found from pictures taken
  as fast as they come, a tile at a time, by its mean color and in time:
  its box, how many changes, how often, and both colors; and how many
  pictures it took and how many changed, on stdout whatever's found, a
  rate too slow said. With `--save DIR`, a sheet of each one's pictures,
  its box outlined and a tile round it magnified, each labeled with when
  it showed.
  `Screen.flicker` from Python.
- **`--net loopback`**: a network of the session's own, which every
  program it runs shares and nothing else is on, for `run`, `shoot`,
  `start`, `exec` and MCP's `start_session`: a server a test starts is
  reached by the rest of the session, and from nowhere else. `wait port`
  looks in it.
- **`shotbox listen IN.wav`**: a recording's peak and RMS level, samples
  clipped, and gaps, where a program fell behind and left the sink
  silent; it fails on any, or on silence. `shotbox.sound.hear` from
  Python, and MCP's `sound` tool, which records a session's sound
  between `start` and `stop` and says the same of it.
- **A GitHub Action**, `uses: mishan/shotbox@vX.Y.Z`: installs shotbox
  at that version and what it runs on an Ubuntu runner, with each
  feature (`a11y`, `wayland`, `audio`, `record`, `term`, `gamepads`)
  asked for by name, and lets bubblewrap make its sandbox.
- **`shotbox exec -- COMMAND`**: the session's seal with no display, for
  a headless browser or a test suite: its own environment, home and bus,
  under bubblewrap, and `--audio` if it's wanted.
- **`--no-sandbox`**, for a program that needs a setuid helper (sudo, an
  AppImage), which can't run under bubblewrap.
- **What's sealed**, in docs/sealed.md: what a session keeps from a program,
  and what it doesn't. And, in the README, instructions to give an agent
  so it runs graphical programs, browsers and sound in a session.
- **`wait match REF.png` and `wait change`**, with `--window`, `--crop`,
  `--fuzz` and `--max`: wait until the screen looks like a picture, or
  looks different from when the wait started, and say how long it took.
  Also `Screen.wait_match` and `wait_change`, and MCP's `wait_for`,
  whose picture to match is a file in the `--allow` file's directory.
- **`shotbox record OUT.wav`**: what plays in a session with `--audio`,
  48 kHz 16-bit stereo, bit for bit for what plays in that format, on X11
  or Wayland, for `--seconds` (to the sample) or until stopped.
- **Recipes**, in docs/recipes.md: checking a change in a GUI, visual
  regression in CI, an accessibility audit, a game smoke test and an
  agent over MCP.
- **How shotbox compares**, in docs/comparison.md, with
  linux-x11-harness, agent-workspace-linux, kwin-mcp, agent-desktop,
  dogtail, openQA and others.
- **`shotbox record OUT.mp4` in Wayland sessions**, and .webm and .gif:
  the same options, the same length for `--seconds`, and the same ways
  to stop, the file finished however it stops. wf-recorder takes the
  frames over wlroots' screencopy and ffmpeg encodes them, as on X11.
  sway draws no pointer, so `--pointer` is refused there. The Action's
  `record` installs wf-recorder when `wayland` is on too.
- **`shotbox record OUT.mp4 --sound`**, and .webm: in a session with
  `--audio`, what plays too, in the same file, AAC in an MP4, Opus in a
  WebM, by one ffmpeg, the sound in step with the picture from its first
  frame, within a frame or so. Both are `--seconds` long, and it stops
  the ways `record` does, the file finished. Then it says what `listen`
  would of the sound: its level, clipping and dropouts, by `listen`'s
  `--gap`, `--clipped` and `--quiet`, failing for them with `--strict`.
  Also `Screen.record(..., sound=True)`, whose Recording has what
  `shotbox.sound.hear` heard, and its `problems()`.
- **MCP's `record` tool**: a clip of the screen or a window, up to 60
  seconds and 100 MB, saved as an .mp4 in the session's scratch
  directory or as a new file in the `--allow` file's directory. It
  answers with the path, the clip's length and a sheet of up to 8
  frames from it with their times, as an image, since a model can look
  at frames but not play a file; with `sound`, in a session with
  `audio`, what was heard, as the `sound` tool says it.
  `Screen.record(..., size=N)` ends a recording at N bytes.

### Fixed

- **`term --shoot` passes on `--net`, `--usage` and `--no-sandbox`,**
  which it took and then left out. It now gives shoot every session
  option as it was given, read back through its own options, so one
  added later reaches shoot too.
- **`shoot` makes OUT's directory** if it isn't there, where it failed
  with a traceback.
- **A recording's keeper killed outright** takes ffmpeg (and
  wf-recorder) with it, ffmpeg finishing the file, rather than leaving
  them recording after the session has gone.

## 0.5.0 — 2026-10-14

Holding keys and buttons, pictures as colorblind eyes see them, bursts, videos, and gamepads.

### Added

- **`key --hold SECS` and `click --hold SECS`** keep keys or a button down
  that long, the chords pressed together and let go last first: for games,
  and anything that reacts to how long something's held. In the background
  (`&`), one is held while other input goes on.
- **`key --down`/`--up` and `click --down`/`--up`** leave a key or button
  down between commands, on X11. A Wayland virtual keyboard's keys go up
  when its connection closes, so there `--down` is refused, saying to use
  `--hold`.
- **`shotbox vision IN OUT --as KIND`**, and `--vision KIND` on `capture`
  and `shoot`: a picture as someone with protanopia, deuteranopia or
  tritanopia sees it (Machado et al., 2009, at full severity), or in gray.
- **`shotbox record OUT`**: a video of the display or a window, as an MP4,
  WebM or GIF, for `--seconds` or until stopped (Ctrl-C, `kill`, its
  session's display going), and the file is finished however it stops,
  even killed outright. `--crop`, `--fps`, `--pointer` and `--park`; X11
  sessions only for now, and it needs ffmpeg.
- **`capture --burst N`**, with `--every SECS` and `--sheet FILE`: several
  pictures, numbered and timed, and a sheet of them, for motion.
- **`--gamepads N`** and **`shotbox pad`**: virtual Xbox 360 controllers,
  made with uinput, that only the session's programs see: each runs under
  bubblewrap with a `/dev/input` of its own, and the machine's raw HID and
  USB devices masked. Worked with `pad press`, `stick`, `trigger` and
  `neutral`; what's left down stays down between commands. MCP:
  `start_session` takes `gamepads`, and a `gamepad` tool.
- **`term --shoot`** passes `--audio` on to the session it starts.
- **MCP:** `press_keys` and `click` take `hold` (seconds) and `state` (down
  or up), which last across calls on Wayland too, since a session keeps its
  input connection; `screenshot` takes `vision`, and `burst` and `every` for
  a burst as one sheet.

### Fixed

- **A `TMPDIR` too long for a session's sockets** is said at the start
  (Linux allows a socket path 107 bytes), rather than D-Bus failing to
  start or sway crashing.

## 0.4.3 — 2026-10-13

`contrast` on GTK 3 and on GTK 4 icons and fields.

### Fixed

- **GTK 3's switch isn't measured as text.** It calls a switch a toggle
  button, so its off mark was held to text's 4.5:1.
- **`contrast --edges` takes a field's edge from what it fills all
  round:** a text view in a frame, which has no edge of its own.
- **A GTK 4 icon button is held to graphics' 3:1, not text's 4.5:1.**
  GTK 4 puts a label in a control that draws text, so one with none
  draws an icon. GTK 3's buttons keep their labels to themselves, and
  are still held to text's.
- **A missing GObject typelib is said in a line,** naming the packages,
  rather than with a traceback from the announcement recorder.

### Changed

- **A control with nothing drawn says how it measures with the pointer
  over it,** as a tab's close button is drawn only then: still "text not
  seen", but now one can tell that from text that's never seen.
- **Light GTK 3 text that fails says its color too.** Light or small
  text covers no pixel whole, so it measures short of its color, by more
  on one font and renderer than another, and text drawn see-through
  looks the same: it's still measured as drawn, but where GTK 3 says the
  color, and what's drawn is a lighter shade of it, the failure says it.

## 0.4.2 — 2026-10-13

`tabwalk --visible` on GTK 4's offset nodes.

### Fixed

- **`tabwalk --visible` finds a ring drawn off its node.** GTK 4 places a
  node short of the padding of what it's in, right of and below where
  it's drawn: a libadwaita tab 6 pixels, a header bar's buttons 7, a tab's
  close button 10. Only 4 pixels round a node's bounds were counted, and a
  tab whose top edge already had the ring's color, as a current tab's rail
  can, was reported as showing no focus. When too little changed at its
  bounds, the count is now taken at any offset up to 16 pixels each way
  where what changed looks like a focus shown: along three or four of the
  node's sides, and little just outside them. The ring the focus left
  isn't counted where the walk found it, so a sibling right beside it, a
  radio button or a toolbar icon with a thin ring, still is, and a
  sibling with no ring of its own isn't credited with its neighbor's.
- **No EGL warnings from GTK 4 programs on X11.** They opened EGL though
  they draw with cairo, and Mesa warned on stderr that Xvfb has no DRI3.
  A session now sets `GDK_DISABLE=gl` (GTK 4.16 and later); pictures are
  the same.

## 0.4.1 — 2026-10-13

A fix for sessions that last.

### Fixed

- **Two `shotbox start`s with one name at once both started**, and one
  session was left running with nothing to stop it by. The first wrote
  its record empty and filled it in later; in between, the second took
  the empty record for one whose session had gone. A record is now
  written whole and linked into place.

## 0.4.0 — 2026-10-13

A session can have an accessibility bus of its own, to read, lint and
walk what a screen reader would hear, to click by name, and to check
that the focus shows and text can be read; it can come up dark, in high
contrast or with bigger text, and have a sound server of its own. A
session can last, used by one command after another, and an agent can
have sessions of its own over MCP.

### Added

- **`shotbox mcp --allow PROGRAMS`**: sealed sessions for an agent, over
  the Model Context Protocol. It can start a session, spawn programs from
  the list, read the accessible tree, take screenshots (as images), click
  by name, press keys, wait, and lint, measure contrast and walk the Tab
  order. Its sessions stop when it does. See docs/mcp.md.
- **Sessions that last.** `shotbox start --name NAME` starts a session
  that lasts until `shotbox stop NAME`; `shotbox spawn --session NAME --
  COMMAND` runs a program in it, which stops with it; and `shotbox
  --session NAME COMMAND` runs `wait`, `capture`, `key`, `click`, `a11y`
  and the rest in it from outside. `shotbox sessions` lists them.
- **`--a11y`**, an AT-SPI bus of the session's own, on X11, Wayland
  and Xwayland, that never reaches your desktop's. Inside, `shotbox
  a11y tree` prints the accessible tree as text to commit and diff,
  `shotbox a11y lint` fails for controls, images and focusable nodes
  with no name, and `shotbox a11y tabwalk` presses Tab until the focus
  comes round again, failing on a trap. The Python API has
  `a11y_tree()`, `find()`, `focused()`, `wait_focus()` and
  `tab_walk()`. See docs/accessibility.md.
- **Announcements.** What programs announce to a screen reader (GTK
  4.14's `gtk_accessible_announce()`) is logged, to
  `$SHOTBOX_SCRATCH/announcements` or `--announce-log FILE`, and
  `shotbox wait announce RE` (`wait_announce()`) waits for one, each
  wait taking the next match. Text put in a browser's live region is
  logged the same way.
- **Chromium** is on the bus in an `--a11y` session (it is given
  `ACCESSIBILITY_ENABLED=1`), headed and with
  `--force-renderer-accessibility`, so its pages' trees can be read,
  linted and walked.
- **Where a node is.** The tree's JSON and the Python API give each
  node's bounds on the screen, and `shotbox a11y click NAME`
  (`click_node()`) clicks one by its name.
- **`shotbox a11y tabwalk --visible`** (`tab_walk(visible=True)`) fails
  where the focus doesn't show: too little in and round a node changed
  as it took it.
- **`shotbox a11y contrast`** (`contrast()`) measures, on the screen,
  text, buttons and links against their background (4.5:1), images (3:1), and with
  `--edges`, a button's or field's edge.
- **`--ignore RE`** for `shotbox a11y lint` and `contrast` (`ignore=`), for what
  can't be fixed yet.
- **`shotbox focus RE`** (`focus_window()`) gives a window the keyboard,
  which on X11 nothing else does for a window that comes up after
  another.
- **`--audio`**, a PipeWire of the session's own, pulse protocol too,
  whose one sink plays to nowhere.
- **`accessible`** in the Node package: Chromium's launch options for
  an `--a11y` session.
- **`--dark`, `--high-contrast` and `--text-scale N`**, for libadwaita,
  GTK 4 and GTK 3. Without them, pictures don't change.

### Fixed

- **What a `--desktop` session's bus starts is inside the session.**
  The bus ran what it activated (dconf, the portals) with the caller's
  environment, your `HOME`, `DISPLAY` and runtime dir among it, and read
  service files from your home: a setting changed in the session could
  be written to your own dconf. It now has the session's.
- **`shotbox run` and `shoot` stop their session when they're
  terminated** (`kill`, `timeout`), as they do on ctrl-C, rather than
  leave its display and programs running.

## 0.3.0 — 2026-09-27

A session can be a Wayland one, under a headless sway, with Xwayland in
it for X clients; windows, pictures, waits and input work there as they
do on X11. A Playwright recording can come out the same each run.

### Added

- **Published.** `pipx install --system-site-packages shotbox` (or `pip
  install shotbox`) for the command and the Python API, from PyPI, and
  `npm install shotbox` for the Node helpers. A tag publishes both, from
  CI; see docs/releasing.md.
- **Wayland sessions.** `--wayland` runs the program under a headless
  sway, drawing in software, instead of Xvfb, and `--xwayland` adds an
  Xwayland in it for X clients. Windows are found by title over sway's
  IPC and taken with grim, and `shoot`, `capture`, `stable` waits, failure
  pictures, `term --shoot` and the Python API work as they do on X11.
  `key`, `type`, `click`, `move`, `drag` and `park` speak sway's virtual
  keyboard and pointer protocols directly, reaching Xwayland's X clients
  too. See docs/wayland.md.
- **Windows by app id.** Wherever a window is named, `app=RE` matches its
  app id rather than its title: a Wayland window's `app_id`, or an X
  window's `WM_CLASS`, instance or class.
- **`frames(page)`**, a Playwright recording that comes out the same
  each run: the page's time stands still and moves a frame between
  screenshots, and `steady` is the Chromium flags that keep the frames
  alike. `gif()` takes the directory of frames it writes.

### Fixed

- **What a program leaves behind stops with the session.** A program
  that had already exited when the session ended had its process group
  skipped, so anything it left running in it, a shell script's
  background child say, outlived the session.

## 0.2.0 — 2026-09-27

Sessions can be driven, from the command line and from Python, and wait
for a picture to hold still. Terminal shots take one command. The Node
package grew from the pieces projects had copied into what a Playwright
recording needs: a sealed browser, a pointer and captions, a cut, and a
gif of it. The tests run in CI.

### Changes to check when upgrading

- **The Node package's `package.json` is at the top of the repository**,
  not in `node/`, since npm installs a git dependency from the top and
  nowhere else. Depend on `github:mishan/shotbox`, or on `file:../shotbox`
  for a checkout beside yours; `file:../shotbox/node` no longer works.

### Added

- **Input.** Inside a session, `shotbox key`, `type`, `click`, `move` and
  `drag` drive the display, at a point on the screen or inside a window,
  speaking XTEST directly. `shotbox park` moves the pointer off whatever
  it was hovering. GTK apps in a session no longer blink or animate.
- **`stable` waits** for the display or a window to look the same for a
  while, for what has no other sign it is done. A failed wait leaves a
  picture of the screen as it was.
- **A Python API.** `shotbox.Session` starts a session and
  `shotbox.here()` is the one a script runs inside; both wait, drive the
  display and take pictures over one X connection.
- **`shotbox term --shoot`** starts the session and takes the picture in
  one command, and `--crop COLSxROWS` crops by cells, not pixels.
- **`shotbox compare` on directories**, picture by picture, with `--max`
  and `--fuzz` for one picture by name.
- **`gif()` takes `from`, `colors` and `dither`**: a cut off the start, a
  smaller palette, and no dither for a flat page with something moving on
  it.
- **`sealed()`**, an environment for a headless browser that nothing of
  yours gets into: your fonts, fontconfig and time zone stay out of the
  pictures.
- **`dress(page)`** draws a pointer and captions into a recording, since
  a browser records neither its cursor nor a key pressed.
- **`film(page)`** says where a recording's loop starts, for `gif()`'s
  `from`.
- **`shotbox-serve [DIR] [PORT] [PAGE]`**, `serve()` as a command.
- **TypeScript declarations** for the Node package.

### Fixed

- `shotbox compare` with a missing picture says which, rather than
  failing with a traceback.

## 0.1.0 — 2026-09-25

The first version: `shotbox shoot`, `run`, `wait`, `capture` and `term`
in a sealed session, and a Node package for Playwright checks with a
static server, a check reporter, page errors, process-group launch and
webm to GIF.
