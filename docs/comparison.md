# How shotbox compares

Several tools give an agent or a test a Linux GUI to drive. Most are
about control: start a program, see it, click and type. shotbox does
that too, but what it's for is checking: a session sealed the same way
each time, so a picture of it comes out the same each run on the same
machine, with `--clock` and `--identity` for what moves; measurements of what it
finds (contrast, the Tab order, names a screen reader can't say, pixels
changed, latency, dropouts in its sound, what it used); and waits for
things rather than sleeps. Where a tool below does something shotbox
doesn't, or does it better, that's said.

What each says of itself is from its own README or docs, as of October
2026.

## linux-x11-harness

[Wangxiaoxiaoa/linux-x11-harness](https://github.com/Wangxiaoxiaoa/linux-x11-harness)
(MIT; Rust, packaged for Python; alpha) gives each agent display its own
Xvfb, Openbox and process group, drives it over XTest, and serves it over
MCP. It reads the accessible tree and clicks elements by it, reads text
with local OCR (Tesseract or RapidOCR), and has a live preview a person
can take over.

Like shotbox, the program is kept off your desktop. Unlike it, its docs
say nothing of the environment or home a program starts with, sound, a
network of its own, comparing pictures, or keeping a picture the same
between runs. For an agent that has no vision model and needs OCR, or a
person who wants to watch and step in, it's the better fit.

## agent-workspace-linux

[agent-sh/agent-workspace-linux](https://github.com/agent-sh/agent-workspace-linux)
(MIT; Rust) is a hidden Xvfb desktop with a window manager, driven with
XTest and xdotool, with about 86 MCP tools, and a Chrome of the workspace's own,
driven over DevTools. Under bubblewrap it confines files as well as the
network (`disabled`, `local_only` or `inherit_host`), and an agent's
permissions can be capped.

That's more sandbox than shotbox's, which leaves the filesystem as yours
and isn't a security boundary. It has no accessibility tree or checks,
no comparison of pictures and no sound. For an agent that should be
kept from your files, or that mostly works in a browser, it's the better
choice.

## kwin-mcp

[isac322/kwin-mcp](https://github.com/isac322/kwin-mcp) (MIT; Python)
runs a virtual KWin (`kwin_wayland --virtual`) with its own D-Bus for
each session, with a fresh home and XDG directories if asked
(`home="isolated"`), or attaches to a running KWin session; it sends input over KWin's EIS interface, reads the AT-SPI
tree, and takes pictures with KWin's own screenshot interface. It has
touch gestures and D-Bus calls as tools, and runs unprivileged in a
container for CI. It says itself that it isn't a sandbox: programs run
with your files and network.

It's Wayland on KDE Plasma 6, where shotbox's Wayland is a headless
sway, and its X11 Xvfb. For a KDE or Qt program that should be tested
under the compositor it ships with, or with touch, it's the better
choice. It has no checks of its own (pixels are compared only in its
own tests), no sound, and nothing to keep pictures the same.
[Samthesurf/kwin-mcp](https://github.com/Samthesurf/kwin-mcp), of the
same name, drives only your real Plasma desktop.

## agent-desktop

[lahfir/agent-desktop](https://github.com/lahfir/agent-desktop)
(Apache-2.0; Rust) is a command-line tool that drives programs by their
accessible tree, with references to nodes and a failure rather than a
guess when one is stale or ambiguous. It works on macOS; Windows and
Linux are planned, and it has no MCP server. It drives your own desktop,
not a session apart. On a Mac, it's what to use; on Linux, not yet.

## dogtail

[dogtail](https://gitlab.com/dogtail/dogtail) (GPL-2.0; Python) is the
long-standing AT-SPI test library for GNOME and GTK programs, used with
behave and qecore, and maintained at Red Hat, on Xorg and on GNOME's Wayland
(with gnome-ponytail-daemon). Its newer hermetic sessions start a private
D-Bus, accessibility bus and a bare mutter with a virtual monitor, for CI
with no desktop.

For GUI tests written against the tree, its API is much richer than
the few calls shotbox has (`find`, `click_node`, `wait_focus`), and its
hermetic sessions run under mutter, closer to a GNOME desktop than
sway. It has no MCP server, no pictures to compare, no sound, and
doesn't measure contrast or walk the Tab order. The two go together:
dogtail finds a session's tree in `shotbox run --a11y`.

## openQA

[openQA](https://open.qa/) and its runner
[os-autoinst](https://github.com/os-autoinst/os-autoinst) (GPL-2.0;
Perl) test whole operating systems in virtual machines, mostly QEMU, or
on real hardware. A test waits for the screen to match a needle (a
picture, with areas to match or leave out and a similarity to reach),
types and clicks, reads serial output, records video, and with QEMU
records and compares sound. It schedules jobs across a fleet and has a
web interface to review results and make needles.

It's the better choice for an installer, a boot, a whole desktop or a
distribution: a VM per job isolates far more than a session does. It's
much more to run than one command, and a VM is the wrong size for
checking one program. It has no accessibility tree and no MCP server.
shotbox's `wait match` and `compare` are its needles in small: a whole
picture, or a box at a fixed place in one (`--crop`), a count of pixels that may differ, and a
`--fuzz` for colors.

## Others

- [Yaskawa-Global/gui-user](https://github.com/Yaskawa-Global/gui-user)
  (MIT; Python): an MCP server on its own Xvfb, xdotool for input, the
  AT-SPI tree, and waits for idle and for elements. Control, no checks.
- [grymmjack/qa-harness](https://github.com/grymmjack/qa-harness) (MIT;
  Bash): GUI tests on Linux, macOS and Windows that assert on boxes of
  pixels, with JUnit output; on Linux, Xvfb, xdotool and scrot. No
  accessibility tree or MCP.
- [Anthropic's computer-use demo](https://github.com/anthropics/claude-quickstarts/tree/main/computer-use-demo):
  a container with Xvfb, a window manager, xdotool and VNC, a reference
  for an agent that works from screenshots, one session at a time.
- [SikuliX](https://github.com/oculix-org/SikuliX1), archived by its
  author in March 2026 and carried on as
  [Oculix](https://github.com/oculix-org/Oculix): finds
  what to click by matching pictures (OpenCV) and OCR, on any desktop.

## In short

None of these seals a program's environment and settings the same way
each run (kwin-mcp's isolated home comes closest), holds its clock and
its user and host names still for a picture, checks a picture comes out
the same twice (`--twice`), or measures
contrast, the Tab order, pointer latency or sound dropouts; openQA alone
listens to sound. shotbox doesn't confine files, run a VM, read text by
OCR, offer a live view to take over, drive KDE's compositor, or run on
macOS or Windows; the tools above do.
