# shotbox mcp

`shotbox mcp` gives an agent the sealed sessions shotbox gives a script: a
display, a D-Bus and an accessibility bus of their own, programs started
in them, and the tools to look at them, drive them and check them.

    shotbox mcp --allow PROGRAMS

It speaks the Model Context Protocol on stdin and stdout, so a client
starts it and talks to it for as long as it's connected.
As it connects, it gives the agent a short note on how the tools go
together (the protocol's `instructions`): start a session, spawn, read the
tree before clicking, wait rather than sleep, stop it after.

## What it's for

An agent working on a program with a window can't see it. With this, it
can:

- **check its own change.** Start the program, read the accessible tree,
  click through the change by name, and take a screenshot when it needs
  pixels.
- **audit.** Ask what a screen reader hears, lint for nodes with no name,
  measure contrast, walk the Tab order, and check that the focus shows
  where it goes.
- **reproduce a report.** Follow the steps in a session that is the same
  each time, and show the state it ends in.

The accessible tree is the cheap way to look. A thousand or two tokens
of text say what's on screen, what each thing is called and what can be
clicked, and the tree is what someone who can't see the screen gets. A
screenshot costs far more, and is for what only pixels say: colors,
layout, what's drawn.

## The shape

**Sessions are the server's.** `start_session` starts one and names it,
and every other tool names the session it acts on. The sessions are held
in the server's process, as `Session` objects, and all of them stop when
it does, however it ends. When the client closes its stdin, the server
stops them in turn. A client stopping a server doesn't wait for that:
Claude Code sends SIGINT, SIGTERM 100ms later and SIGKILL 400ms after
that, and one session can take longer to stop than that.

So every session lives in a directory of the server's, and a watchdog
(`shotbox mcp --watch DIR`, in a process session of its own, out of the
client's reach) holds a pipe from the server. However the server ends,
SIGKILL included, the pipe closes. The watchdog then stops every process
it knows for a session's by its environment (SIGTERM, so a recording
finishes its file if it can, and SIGKILL for what's left 5 seconds on), and removes
the directory.
A session's process has its scratch dir, runtime dir or home in the
directory, or the server's own `SHOTBOX_MCP` naming it, for what inherits
the server's environment (Xvfb). Nothing else is killed: not a shell
someone `cd`'d into a session's directory to `tail -f` a log, whose
`PWD` is there, nor anything naming it on its command line. A program
that clears or rewrites its environment (`env -i`, a rewritten process
title) or makes itself undumpable isn't found that way; it usually goes
when what's round it does, and otherwise it's left. On a signal the
server exits at once, leaving the sessions to the watchdog.

**Programs come from a list.** An agent can't run a command. It spawns a
program by its name in the allow file the person started the server
with:

    # NAME = COMMAND ARGS..., split as a shell would, run without one;
    # a # starting a word, outside quotes, starts a comment
    editor = gnome-text-editor
    gtkhx = ./build/src/gtkhx --server localhost
    demo = /usr/bin/python3 tests/demo.py

A command with a slash that isn't absolute is relative to the file, and
every program starts in the file's directory, with nothing on its stdin. Without `--allow`, nothing
can be spawned, and the tools say so. A program's output goes to a log in
the session, which `get_program_output` reads: the answer to a window that
never came up.

**Sealing is the safety.** A session reaches nothing of the desktop the
server runs on: not its display, its session bus, its home, its settings,
its sound, or its screen reader. The program is the person's, from their
list, and what it can reach of the machine is up to it. What the agent
adds is clicks and keys on a display no one else is looking at. The one
thing it writes outside a session is `record_video`'s clips, new .mp4 files
in the `--allow` file's directory, never over a file that's there.

**The allow file's directory is the agent's to read.** `wait_for` line
and `record_video`'s `until_line` read any text file under it and answer
with the line they find, so keep the allow file in a directory of its
own or the project's, not your home. And what's on the list is the
agent's to drive: allowing a terminal, a browser or a file manager hands
it arbitrary commands, typed with `type_text`.

## The tools

| | |
|---|---|
| `start_session`, `stop_session` | a session: X11 or Wayland, the accessibility bus (on by default), dark, high contrast, text scale, audio, screen size (up to 7680x4320), gamepads, a network of its own (`net: loopback`), who and where its programs are (`identity: USER@HOST`), what its programs used, said when it's stopped (`usage`), and a wall clock that starts at a time (`clock`, `tz`) |
| `spawn_program`, `get_program_output` | a program from the list, and how it's doing |
| `get_accessible_tree` | the tree, a node a line, with bounds if asked, up to 500 lines |
| `take_screenshot` | the screen or a window, as an image, cropped or zoomed if asked, or as someone with another kind of color vision sees it, with each named node's box numbered on it if asked (`labels`); or a burst of them, as one image; with its size |
| `record_video` | a clip of the screen or a window, up to 60 seconds and 100 MB, saved as an .mp4 in the session's scratch directory or in the `--allow` file's directory, answered with its path, its length and a sheet of frames from it, as one image, with what was heard if asked, in a session started with `audio`; or ended by a line in a log (`until_line`, `file`, `tail`), read as `wait_for`'s is |
| `click_element` | by a node's name (and role), once it's enabled, still and uncovered unless `force`, or with `action` its accessible action of that name, for a node a click won't reach, or a point, the button held a while or left down if asked; with `changes`, what it changed in the tree |
| `scroll_wheel` | the mouse wheel, clicks down and right, over a node by its name (and role), waited for as `click_element`'s is, or at a point; with `changes`, what it changed in the tree |
| `press_keys`, `type_text` | chords, held together a while or left down if asked, and ASCII text; with `changes`, what they changed in the tree |
| `use_gamepad` | buttons, sticks and triggers on a virtual Xbox 360 pad, in a session started with `gamepads` |
| `wait_for` | a window, stillness, an announcement, the focus, a node in the accessible tree by its name or text, looking like a picture (and where: found up to `margin` pixels from its crop's place), a change (each but for the boxes `ignore` leaves out: a clock, a cursor), or a line in a log, in the `--allow` file's directory |
| `check_accessible_names`, `check_contrast`, `check_focus_order` | the checks `shotbox a11y` makes; `check_contrast` with `passes` says what passes too, with its ratio, as `a11y contrast --passes` does, and with `strict` fails on what it couldn't measure, as `--strict` does; text whose ratio changed between pictures across what it needs it says apart, which fails only with `strict`, and what's behind a modal dialog, which doesn't fail; a web page's node with its DOM tag, id and text ([Web nodes](accessibility.md#web-nodes)); `check_focus_order` with `document` lists only a browser's page, as `tabwalk --document` does |
| `get_speech` | what Orca, a real screen reader started for the call, says after each press of Tab or of the keys given, with the focused node's tree line: [What a screen reader says](accessibility.md#what-a-screen-reader-says) |
| `list_announcements` | what's been announced to a screen reader |
| `record_audio` | in a session started with `audio`, record what plays between `start` and `stop`, and say how loud it was and where it dropped out, as `shotbox listen` does |

**An x,y with a window is the window's.** Given `window`,
`click_element`'s and `scroll_wheel`'s x,y, `crop`, `zoom`, and the
bounds `get_accessible_tree`, `check_accessible_names`, `check_contrast`
and a click by name answer with, and list when two nodes match, are
all from the window's top left corner, so a node's bounds go to
`click_element` as they are; without one, they're the screen's.

Each description says what the tool is for, what to read in its answer,
and what to use instead when it's the wrong one: an agent knows a tool
by nothing else.

**A picture is what the model sees.** Claude scales an image past a
long edge of 1568 px, or past 1568 patches of 28x28 px, so an x,y read
off a 1920x1080 screen would be off by a factor of 1.32 when given
to `click_element`. `take_screenshot` and `record_video` shrink a picture
past that themselves (1920x1080 to 1456x819) and say its size and the
scale. A screenshot's answer says where a point on it is for
`click_element` with the same window: `x/0.7583, y/0.7583`, or for a
part, `100 + x/0.8122, 50 + y/0.812` (each side is rounded on its own),
with no scale or corner left to undo; or click by name. `crop` takes a part,
shrunk only if it's past what's seen; `zoom` takes the corners
`[x0, y0, x1, y1]` of one, in `click_element`'s x,y (a picture's, mapped
as its answer says), and shows it as large as fits, up to 8 times, to
read small text. A part is cut to what's taken, and one with none of it
is refused. A burst's or a clip's pictures are laid out as many to a
row as shrinks them least: two 1920x1080 screens to a row, 728x410
each.

**What an action changed.** `click_element`, `scroll_wheel`,
`press_keys` and `type_text` with `changes` read the tree before they
act and again once the screen has held still for half a second (as
`wait_for` stable), and answer with what changed, and when it was read:
a tree line a node, `+` added, `-` gone (a subtree gone whole as its
root, `and 3 inside`), `~` renamed, described otherwise or in other
states, with `, was` and what it was, each indented under the line above
it that it's in, and with changes in more than one window, each window's
after a line naming it and its place, `in frame "Untitled" (window 2,
pid 1234):`, as two can be the same:

    Clicked check box "Wrap lines" [focusable, checked] at the middle of [5, 171, 350, 28].
    Changed in the tree, read 0.6 s after, once the screen had held still for 0.5 s:
    ~ button "Save" [focusable], was [focusable, focused]
    ~ check box "Wrap lines" [focusable, focused], was [focusable, checked]

What changes after it's read is missed: a program that moves the focus
a second after a key, or opens a window a second after a click, while
the screen holds still, is "Nothing changed". `wait_for` focus or
window waits for that. A screen that never holds still (a spinner, a
clock) is read after 10 seconds, saying so, which every such call
pays. It's the whole tree, every window's, since a click can open
another. Where nodes are isn't compared, since a scroll moves them all,
nor a field's text, which isn't in its line, so typing into one changes
none. Nodes are paired by role and name in tree order, and those left
by role, so a dialog that gives way to another of the same shape is its
nodes renamed. A program that doesn't answer the tree's read (busy, or
hung) is said, `! application "" (pid 1234) didn't answer`, and its
windows left out, not said to be gone; if the tree can't be read again
at all, the answer says why, after what was done. It's asked for rather
than always said: it costs the two reads, about a second, and a page
that changes whole could fill an answer the agent didn't need.

**Numbers on a picture.** `take_screenshot` with `labels` draws each
named node's box on it in magenta, with a number at its corner, and
answers with the number's tree line and the box on the picture, scaled
and cropped or zoomed as the picture is: what's seen is clicked by name. Not a
window, whose box is the picture, nor a node named as what it's in (a
button's label). A box is cut to those of what it's in, so a row
scrolled out of view, which a toolkit still says is showing, has none;
numbers at one corner go side by side. Without `window`, a window
behind another is labeled too, over the one in front. Named nodes whose
place can't be told (two windows the same size and title) are counted,
not labeled. The boxes are where the tree says, which on GTK 4.14 and
later is off, and the answer says so for such a program: [GTK 4's
positions](accessibility.md#gtk-4s-positions).

**Answers are bounded.** A screen is at most 7680x4320. The tree, what
an action changed in it, a picture's labels, the checks' answers and
`list_announcements` (its latest) stop at 500 lines,
saying how many more and how to narrow it, if it can be; GTK apps' whole windows
measured 63 to 314. A line a program wrote, and a tree line, stops at
500 characters, saying how many more.

**A program's text is marked.** What a program wrote, in
`get_program_output`, `wait_for` line's and `until_line`'s line and
`list_announcements`, comes a line each after `| `, and a title or a
node's name is quoted; the instructions say both are the programs', not
the person's: a log line or a web page can say anything.

**Without PyGObject**, as plain `pipx install` or uvx installs it,
`start_session` still starts, without the accessibility bus, and its
answer says which tools won't work and how to fix it
(`pipx install --force --system-site-packages shotbox`, or the system's
packages; uvx and uv's tools can't see them, so use pipx), as it says
what it couldn't seal.

A tool it doesn't have is a protocol error, -32602, naming a renamed
tool's new name. Any other failure is an answer, marked `isError`, in a
sentence saying what to do instead: a program not on the list says
what is, a session that isn't there names the ones that are, and an argument a tool doesn't take is
named, as is a choice that isn't one before what a call lacks. A node's
name, for `click_element` and `wait_for`'s focus, is taken as it is, and failing
that as a regex: `+` and `(Draft)` are labels, not regexes, as in
[Clicking by name](accessibility.md#clicking-by-name). Two nodes matching
are listed as `get_accessible_tree` lines with their bounds; none, the
nearest names. `wait_for` a window, given the name a program was spawned by,
waits for a window of that program's, whatever its title: of a process in
the process session of any spawned by that name, so a shell wrapper, what
it forks, and a second `gnome-text-editor` handing its window to the
first count too, by `_NET_WM_PID` on X11 and the pid sway has on
Wayland. The name wins over a window titled the same, which is likely
another program's (`^name$` asks for that); with no window of its own,
one titled exactly the name will do. Otherwise not found by the name:
a window of a process started some other way (by D-Bus, or by another
name), one that left its session (a daemon, with `setsid`), or one in a
PID namespace of its own, as Flatpak's are, whose `_NET_WM_PID` on X11
is its pid inside that (on Wayland, the pid sway has may still find it).
Wait by its title. A window wait that gives up lists the windows there
are, the nearest first. `check_accessible_names` and `check_contrast` answer in
tree lines too, with bounds, and an `ignore` regex leaves out a problem
it's found in, the tree line's or the node's path's, as `shotbox a11y lint --ignore` does.
Arguments are taken however the client sends them, since clients
don't pass them through as typed. A number can come as text, and a list
as text with commas or spaces: `"Tab Tab"` for `["Tab", "Tab"]`.

## Starting it

Claude Code:

    claude mcp add shotbox -- shotbox mcp --allow /path/to/programs

Claude Desktop, and other clients that take a config:

```json
{
  "mcpServers": {
    "shotbox": {
      "command": "shotbox",
      "args": ["mcp", "--allow", "/path/to/programs"]
    }
  }
}
```

From a checkout, the command is the checkout's `bin/shotbox`.

## How it's built

JSON-RPC 2.0 over newline-delimited stdin and stdout, by hand: the
methods MCP's tools need (`server/discover`, `initialize`, `ping`,
`tools/list`, `tools/call`), as liverykit does it. The SDK would be
shotbox's first Python dependency, for five methods. It speaks the MCP
revisions 2024-11-05, 2025-03-26, 2025-06-18 and 2025-11-25, which open
with `initialize`: it answers with the version the client asked for,
when it's one of those, and otherwise 2025-11-25. And it speaks
2026-07-28, which has no `initialize`: each request names its version in
`_meta`, and one it doesn't speak is refused with -32022 and the
versions it does. `server/discover` gives 2026-07-28, the tools
capability and the instructions.

Nothing but the protocol goes to stdout, since one stray line there ends
the connection, and nothing but the server reads stdin, where a program
that reads it would eat requests. The server takes both for itself, and
points fd 1 at stderr and fd 0 at /dev/null for everything else: the
sessions, their programs, and whatever a library prints.

Calls are answered one at a time, in turn, and the waits are bounded: a
timeout of at most 300 seconds, and a Tab walk of at most 200 presses. A
thread reads stdin as requests come, so `ping` is answered at once, even
during a long call, and `notifications/cancelled` calls off the request
it names: its waits stop, as do a click by name, `check_focus_order`, `get_speech`,
`check_accessible_names`, `check_contrast` or a recording it's in the
middle of (the clip is finished short), its answer isn't sent, and the session is left as it was. When
stdin closes, the call running is called off the same way, those sent
after it are answered, and then the sessions stop. A call given a `progressToken` (in `params._meta`) sends
`notifications/progress` every 5 seconds until it's answered, its
`progress` the seconds it has taken. A request it can't read is an error
for that request, not the end of the server.

Most calls take a second or two. Those that can run long:

- `wait_for`, up to its `timeout`, 300 seconds at most;
- `record_video`, up to 60 seconds and then a few for its frames;
- `check_focus_order`, a few seconds a press, up to 200 presses;
- `get_speech`, a few seconds for Orca to start, and a second a press;
- `click_element` by name, up to its `timeout`, while the node doesn't show or isn't
  ready to click.

Many clients give up on a call after about 60 seconds, and cancel it.
Some count from the last progress (the TypeScript SDK's
`resetTimeoutOnProgress`); for the rest, raise the client's timeout:
Claude Code's `MCP_TOOL_TIMEOUT` (milliseconds, in its environment), or
Cline's `timeout` (seconds) in the server's entry in its MCP settings.

The accessible tree is read in a `shotbox a11y` process for each call, as
the Python API reads it. libatspi keeps the first bus it finds for the
life of a process, and each session has a bus of its own.

## Testing

`test/mcp_test.py` sends requests to the handler in the test's own
process, with a real session behind it, as a client would send them:
- initialize, with the version asked for and with an unknown one;
- each tool on a libadwaita window;
- each refusal: a program not on the list, a session that isn't there,
  an argument a tool doesn't take, a screen past 8K;
- a 1920x1080 picture shrunk to 1456x819, with its scale; the tree cut
  short, with what's left; a program's output fenced, a long line cut.

It then runs the server over stdio, as a client runs it, and checks that
nothing but answers comes out on stdout, that a ping is answered during
a long call, which sends progress and, cancelled, no answer, and that
the session stops when stdin closes. From a virtualenv that can't see
PyGObject, a session still starts, saying what won't work.
