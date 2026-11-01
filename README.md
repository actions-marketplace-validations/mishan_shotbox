# shotbox

<!-- mcp-name: io.github.mishan/shotbox -->

[![PyPI](https://img.shields.io/pypi/v/shotbox)](https://pypi.org/project/shotbox/)
[![npm](https://img.shields.io/npm/v/shotbox)](https://www.npmjs.com/package/shotbox)
[![CI](https://github.com/mishan/shotbox/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mishan/shotbox/actions/workflows/ci.yml)
[![Glama](https://glama.ai/mcp/servers/mishan/shotbox/badges/score.svg)](https://glama.ai/mcp/servers/mishan/shotbox)

<img align="right" width="112" alt="" src="https://raw.githubusercontent.com/mishan/shotbox/main/docs/logo.png">

Eyes, ears and hands on a real graphical program, for an AI agent, a
script or CI.

An agent working on a program with a window can't see it or hear it.
shotbox runs the program in a sealed session of its own, X11 or Wayland,
and lets whatever is driving it look, listen and act: read the accessible
tree, take screenshots, record what it plays, click by a button's name,
type, hold keys and gamepad buttons, and wait until the program is
actually ready. Then measure what it finds: names a screen reader can't
say, contrast, the Tab order, pixels changed against a reference, where
it flickers, how fast it follows the pointer, where its sound drops out,
and what it used.
Nothing it runs reaches your desktop or speakers by accident, and with
`--net loopback`, not your network either.

![shotbox finding an unnamed button and faint text, comparing pictures, hearing a dropout, catching flicker and driving a game with a gamepad](https://raw.githubusercontent.com/mishan/shotbox/main/docs/demo.gif)

Made with shotbox: each scene is a [`play` script](https://github.com/mishan/shotbox/tree/main/demo) in a sealed
session, recorded by `shotbox record`. [With sound](https://github.com/mishan/shotbox/blob/main/docs/demo.mp4).

```
claude mcp add shotbox -- shotbox mcp --allow ~/src/app/shotbox-programs
```

Without an agent, it's the same session from the command line:

```
shotbox shoot editor.png --window '.*Text Editor' -- gnome-text-editor notes.md
```

Worked examples, from checking a change to a game's smoke test:
[docs/recipes.md](https://github.com/mishan/shotbox/blob/main/docs/recipes.md). How it differs from other tools that
drive a GUI: [docs/comparison.md](https://github.com/mishan/shotbox/blob/main/docs/comparison.md).

## For agents

`shotbox mcp --allow PROGRAMS` gives an agent sealed sessions over the
Model Context Protocol (stdio). It can start a session, spawn programs
from the list in PROGRAMS (`NAME = COMMAND` a line, and nothing else),
and then:
- read the accessible tree, and take screenshots, which come back as
  images;
- click by a node's name, press keys, type and work a gamepad;
- wait for windows, stillness and announcements;
- lint, measure contrast and walk the Tab order;
- record what a session plays, and hear where it dropped out.

That's enough for an agent to check its own change in a GUI, test a game
by playing it, audit what a screen reader hears, or reproduce a bug report
in a session that's the same each time. The accessible tree is the cheap
way to look, a thousand or two tokens of text; a screenshot is for what only
pixels say. An agent can't run anything off the list, and its sessions stop
when the server does.

What it's for, the tools, and how it's built: [docs/mcp.md](https://github.com/mishan/shotbox/blob/main/docs/mcp.md).

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
- a script or a test suite with windows: `shotbox run [--audio] -- COMMAND`
- with none, a headless browser say: `shotbox exec [--audio] -- COMMAND`;
  the shotbox package's `sealed()` seals a browser's environment, but
  not the speakers
- a session to come back to: `shotbox start --name NAME`, then
  `shotbox spawn --session NAME -- COMMAND`, and `shotbox stop NAME`

Not `xvfb-run`: it lets `WAYLAND_DISPLAY` and the session bus through. A
server started for a test listens on 127.0.0.1, not 0.0.0.0.
```

## Why

An agent checking its change, a screenshot for a README, and a test in CI
all want the same things, and every project was growing its own copy:

- **Sealed.** Nothing of yours leaks in by accident, and nothing the
  program does reaches your desktop or speakers, where bubblewrap can make
  a sandbox (it says when it can't): [What's sealed](https://github.com/mishan/shotbox/blob/main/docs/sealed.md).
- **Ready, not slept.** Waits are for things: a window mapped, a port open, a
  file written, text on a terminal's screen, or the picture holding still.
  No `sleep 6` and hope.
- **Deterministic.** X11 on Xvfb, GTK's software renderer, `LANG=C.UTF-8`,
  `TZ=UTC`, text carets that don't blink (GTK's, Qt 6.5's, browsers'; and GTK's
  animations off), GTK 4's text drawn at its color, as its manual font
  rendering does, not fainter by however much a GTK release's automatic
  one draws it ([more](https://github.com/mishan/shotbox/blob/main/docs/accessibility.md#contrast)), and PNGs with no
  timestamps in them. It's the same every time, not the same as a desktop:
  [differences from a desktop](https://github.com/mishan/shotbox/blob/main/docs/desktop.md).
- **Cleaned up.** Every program runs in its own process group, and the group
  is killed on the way out: signalling a wrapper script alone leaves its
  children running.

## Install

On Debian, with what every command and option can use:

```
sudo apt install xvfb xauth dbus-daemon imagemagick x11-utils python3-gi gir1.2-vte-2.91 \
  at-spi2-core gir1.2-atspi-2.0 python3-gi-cairo gir1.2-pango-1.0 libglib2.0-bin pipewire \
  wireplumber ffmpeg bubblewrap libfaketime
pipx install --system-site-packages shotbox
```

`--system-site-packages` lets it see the system's PyGObject, which
`shotbox term` and `--a11y` use; `pip install shotbox` works too. Or run
it from a checkout: `ln -s "$PWD/bin/shotbox" ~/.local/bin/shotbox`.

`shotbox doctor` says what's here and what's missing, a line each, and
how to get it. What each command and option needs:
[Needs](https://github.com/mishan/shotbox/blob/main/docs/commands.md#needs).

No Python packages: it's the standard library and those tools. Wayland
sessions need more; see [Wayland](https://github.com/mishan/shotbox#wayland).

In a GitHub Actions job on Ubuntu, the Action installs it and what it
runs, as of the release (or branch) it's used at, with what each
feature needs asked for by name (all off unless set), and lets
bubblewrap make its sandbox, which Ubuntu's AppArmor holds back:

```yaml
- uses: mishan/shotbox@v0.10.0
  with:
    a11y: true        # --a11y and `shotbox a11y`
    speak: true       # `shotbox a11y speak`: Orca, and what a11y installs
    term: true        # `shotbox term`
    # wayland, audio, record, gamepads and clock too
- run: shotbox shoot app.png --window 'My App' -- ./my-app
```

## A quick tour

```
shotbox shoot app.png --window 'My App' -- ./my-app          # a picture, once it's up
shotbox shoot app.png --twice --window 'My App' -- ./my-app  # the same every time?
shotbox compare ref.png app.png --diff diff.png              # what changed, and where
shotbox run -- ./take-pictures.sh       # a script that waits, clicks and captures inside
shotbox exec --audio -- npx playwright test        # sealed, with no display
shotbox start --name ed --a11y          # a session to come back to
shotbox spawn --session ed -- gnome-text-editor
shotbox --session ed a11y click Open
shotbox --session ed capture open.png
shotbox stop ed
```

## Commands

| Command | What it does |
|---|---|
| [`shoot`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#shoot) | runs a program, waits, takes the picture; `--twice` proves it comes out the same |
| [`run`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#run-wait-capture-and-record) | runs a command in a sealed session and exits with its status |
| [`exec`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#exec) | the same, with no display: a headless browser, a test suite |
| [`start`, `spawn`, `stop`, `sessions`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#start-spawn-stop-and-sessions) | a session that lasts, the programs in it, stopping one, listing them |
| [`term`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#term) | a terminal to take pictures of |
| [`wait`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#run-wait-capture-and-record) | waits for a window, a port, a file, stillness, a picture, a change, a log line |
| [`capture`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#run-wait-capture-and-record) | takes a picture, or a burst of them |
| [`record`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#run-wait-capture-and-record) | records a video, the sound, or both |
| [`key`, `type`, `click`, `move`, `scroll`, `drag`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#key-type-click-move-scroll-drag-park-and-focus) | presses keys, types, works the pointer; [held](https://github.com/mishan/shotbox/blob/main/docs/commands.md#holding-keys-and-buttons) too |
| [`park`, `focus`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#key-type-click-move-scroll-drag-park-and-focus) | moves the pointer out of the way; gives a window the keyboard |
| [`play`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#play) | plays a script of timed input, on one clock |
| [`latency`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#latency) | how fast what's drawn follows the pointer |
| [`flicker`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#flicker) | finds a part of the screen that flickers or flashes |
| [`pad`](https://github.com/mishan/shotbox/blob/main/docs/gamepads.md) | works a virtual gamepad |
| [`a11y`, `wait announce`, `wait node`](https://github.com/mishan/shotbox/blob/main/docs/accessibility.md#commands) | the accessible tree: read, lint, Tab order, click by name, contrast, announcements, a node to show |
| [`compare`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#compare) | counts the pixels that differ, picture by picture or directory by directory |
| [`vision`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#vision) | a picture as colorblind eyes see it |
| [`listen`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#listen) | a recording's level, clipping and dropouts |
| [`mutate`](https://github.com/mishan/shotbox/blob/main/docs/mutate.md) | runs a browser test against small changes to its page: is it brittle, or blind? |
| [`mcp`](https://github.com/mishan/shotbox/blob/main/docs/mcp.md) | sessions for an agent, over MCP |
| [`doctor`](https://github.com/mishan/shotbox/blob/main/docs/commands.md#needs) | what's here of what each command needs |

The options sessions take, `--wayland`, `--a11y`, `--audio`,
`--gamepads`, `--net loopback`, `--identity`, `--clock`, `--dark` and
the rest: [Session options](https://github.com/mishan/shotbox/blob/main/docs/commands.md#session-options).

## What's sealed

A session keeps a program from reaching your desktop by accident, not one
that sets out to:

- Its environment is rebuilt from an allowlist; `HOME` and every XDG
  directory are a scratch dir.
- Its display, session bus and system bus, and with `--a11y` and
  `--audio` its accessibility bus and sound server, are its own; the
  sound server plays to nowhere. It has no printers (but CUPS on
  localhost:631, unless `--net loopback`). `--machine-bus` gives it the
  machine's system bus and printers.
- Where bubblewrap can make a sandbox (it says when it can't), nothing
  reaches the machine's sound devices or your desktop's Wayland,
  PulseAudio, PipeWire and bus sockets.
- Your network is reachable unless `--net loopback`, your user and host
  name show unless `--identity`, and the real time unless `--clock`.
- Not sealed: the files outside the scratch home, your X server, and
  other processes.

All of it, and what each option covers and doesn't:
[docs/sealed.md](https://github.com/mishan/shotbox/blob/main/docs/sealed.md).

## Accessibility

`--a11y` starts an accessibility (AT-SPI) bus of the session's own, so
what a screen reader would hear can be read and checked. Without it, a
session has none: GTK is told not to look. Your desktop's bus is never
touched either way.

`shotbox a11y` prints the tree, lints it for what has no name to say,
walks the Tab order, clicks by name and measures contrast, `shotbox
wait announce` waits for what's announced, and `shotbox wait node` for a
node to show; for GTK, Qt, AccessKit's
toolkits and Chromium. The commands, how it's put together, and what
each check can and can't tell: [docs/accessibility.md](https://github.com/mishan/shotbox/blob/main/docs/accessibility.md).

## Wayland

`--wayland` makes a session a Wayland one: the program runs under a
headless sway, drawing in software, instead of on Xvfb. `--xwayland` adds
an Xwayland to it, and `DISPLAY`, so an X client runs the way it does on a
Wayland desktop, GTK programs made to with `--env GDK_BACKEND=x11`.

Needs `sway` and `grim`, `Xwayland` for `--xwayland`, and wf-recorder
for `record`: `sudo apt install sway grim xwayland wf-recorder`. Why
sway, how it fits, and what differs from X11:
[docs/wayland.md](https://github.com/mishan/shotbox/blob/main/docs/wayland.md).

## Docs

- [Commands](https://github.com/mishan/shotbox/blob/main/docs/commands.md): every command and session option, what each needs,
  resource use and the clock
- [What's sealed](https://github.com/mishan/shotbox/blob/main/docs/sealed.md)
- [Recipes](https://github.com/mishan/shotbox/blob/main/docs/recipes.md): worked examples
- [How it compares](https://github.com/mishan/shotbox/blob/main/docs/comparison.md) with other tools that drive a GUI
- [MCP](https://github.com/mishan/shotbox/blob/main/docs/mcp.md): the tools an agent gets, and how they're built
- [Accessibility](https://github.com/mishan/shotbox/blob/main/docs/accessibility.md)
- [Wayland](https://github.com/mishan/shotbox/blob/main/docs/wayland.md)
- [Differences from a desktop](https://github.com/mishan/shotbox/blob/main/docs/desktop.md): drawing, window manager, animations, language, scale, input, measurements
- [Gamepads](https://github.com/mishan/shotbox/blob/main/docs/gamepads.md)
- [Python](https://github.com/mishan/shotbox/blob/main/docs/python.md): `shotbox.Session` and `here()`, for a script
  that drives the display itself
- [Node](https://github.com/mishan/shotbox/blob/main/docs/node.md): helpers for browser checks with Playwright
- [Mutation testing](https://github.com/mishan/shotbox/blob/main/docs/mutate.md): `shotbox mutate`, its changes,
  verdicts and limits
- [Contributing](https://github.com/mishan/shotbox/blob/main/CONTRIBUTING.md): the tests
- [Code of conduct](https://github.com/mishan/shotbox/blob/main/CODE_OF_CONDUCT.md),
  [security](https://github.com/mishan/shotbox/blob/main/SECURITY.md) and
  [accessibility](https://github.com/mishan/shotbox/blob/main/ACCESSIBILITY.md)
- [Releasing](https://github.com/mishan/shotbox/blob/main/docs/releasing.md)
- [Changelog](https://github.com/mishan/shotbox/blob/main/CHANGELOG.md)

## License

MIT. See [LICENSE](https://github.com/mishan/shotbox/blob/main/LICENSE).
