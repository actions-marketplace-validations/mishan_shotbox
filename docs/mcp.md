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
SIGKILL included, the pipe closes. The watchdog then kills every process
it knows for a session's by its environment, and removes the directory.
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
the session, which `program_output` reads: the answer to a window that
never came up.

**Sealing is the safety.** A session reaches nothing of the desktop the
server runs on: not its display, its session bus, its home, its settings,
its sound, or its screen reader. The program is the person's, from their
list, and what it can reach of the machine is up to it. What the agent
adds is clicks and keys on a display no one else is looking at. The one
thing it writes outside a session is `record`'s clips, new .mp4 files
in the `--allow` file's directory, never over a file that's there.

## The tools

| | |
|---|---|
| `start_session`, `stop_session` | a session: X11 or Wayland, the accessibility bus (on by default), dark, high contrast, text scale, audio, screen size, gamepads, a network of its own (`net: loopback`), who and where its programs are (`identity: USER@HOST`), what its programs used, said when it's stopped (`usage`), and a wall clock that starts at a time (`clock`, `tz`) |
| `spawn`, `program_output` | a program from the list, and how it's doing |
| `accessible_tree` | the tree, a node a line, with bounds if asked |
| `screenshot` | the screen or a window, as an image, cropped if asked, or as someone with another kind of color vision sees it; or a burst of them, as one image |
| `record` | a clip of the screen or a window, up to 60 seconds and 100 MB, saved as an .mp4 in the session's scratch directory or in the `--allow` file's directory, answered with its path, its length and a sheet of frames from it, as one image, with what was heard if asked, in a session started with `audio`; or ended by a line in a log (`until_line`, `file`, `tail`), read as `wait_for`'s is |
| `click` | by a node's name (and role), or a point, the button held a while or left down if asked |
| `scroll` | the mouse wheel, clicks down and right, over a node by its name (and role), or at a point |
| `press_keys`, `type_text` | chords, held together a while or left down if asked, and ASCII text |
| `gamepad` | buttons, sticks and triggers on a virtual Xbox 360 pad, in a session started with `gamepads` |
| `wait_for` | a window, stillness, an announcement, the focus, looking like a picture, a change, or a line in a log, in the `--allow` file's directory |
| `lint`, `contrast`, `tab_walk` | the checks `shotbox a11y` makes |
| `announcements` | what's been announced to a screen reader |
| `sound` | in a session started with `audio`, record what plays between `start` and `stop`, and say how loud it was and where it dropped out, as `shotbox listen` does |

Each description says what the tool is for, what to read in its answer,
and what to use instead when it's the wrong one: an agent knows a tool
by nothing else.

A failure is an answer, marked `isError`, in a sentence saying what to do
instead: a program not on the list says what is, a session that isn't
there names the ones that are, and an argument a tool doesn't take is
named. Arguments are taken however the client sends them, since clients
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
methods MCP's tools need (`initialize`, `ping`, `tools/list`,
`tools/call`), as liverykit does it. The SDK would be shotbox's first
Python dependency, for four methods. `initialize` answers with the
protocol version the client asked for, when it's one it knows, and
otherwise its newest.

Nothing but the protocol goes to stdout, since one stray line there ends
the connection, and nothing but the server reads stdin, where a program
that reads it would eat requests. The server takes both for itself, and
points fd 1 at stderr and fd 0 at /dev/null for everything else: the
sessions, their programs, and whatever a library prints.

A call holds the server until it's done, so the waits are bounded: a
timeout of at most 300 seconds, and a Tab walk of at most 200 presses. A
request it can't read is an error for that request, not the end of the
server.

The accessible tree is read in a `shotbox a11y` process for each call, as
the Python API reads it. libatspi keeps the first bus it finds for the
life of a process, and each session has a bus of its own.

## Testing

`test/mcp_test.py` sends requests to the handler in the test's own
process, with a real session behind it, as a client would send them:
- initialize, with the version asked for and with an unknown one;
- each tool on a libadwaita window;
- each refusal: a program not on the list, a session that isn't there,
  an argument a tool doesn't take.

It then runs the server over stdio, as a client runs it, and checks two
things: that nothing but answers comes out on stdout, and that the
session stops when stdin closes.
