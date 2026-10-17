# Installing shotbox for an agent

These steps are for an agent (Cline, or another MCP client) setting up
shotbox's MCP server for a person. shotbox runs graphical programs in a
sealed X11 or Wayland session of their own, so the agent can see, hear
and drive them. It runs on Linux only.

The setup is done when the MCP server is in the client's settings and
answers (step 5), not when the command works: do all five steps. Kill
only processes you started, by their PIDs: other shotbox sessions (their
Xvfb, sway and PipeWire) may be someone else's.

## 1. The system's packages

On Debian or Ubuntu:

```
sudo apt install xvfb xauth dbus-daemon imagemagick x11-utils python3-gi gir1.2-vte-2.91 \
  at-spi2-core gir1.2-atspi-2.0 libglib2.0-bin pipewire wireplumber ffmpeg bubblewrap \
  libfaketime pipx
```

Elsewhere, install shotbox first (step 2), then run `shotbox doctor`: it
says what's here and what's missing, a line each, and how to get it.
Ask the person before running `sudo`.

## 2. shotbox

```
pipx install --system-site-packages shotbox
```

`--system-site-packages` matters: the accessibility tools (the tree,
clicking by name, lint, contrast) need the system's PyGObject, which an
isolated install (plain `uvx`, or pipx without it) can't see. Then:

```
shotbox doctor
```

Every line should say `ok`, or name an optional feature the person
doesn't need.

## 3. The programs the agent may run

The agent can't run a command, only a program named in an allow file,
which the person chooses. Ask them which programs they want it to work
on before writing the file (at least one, for step 5), and write one
`NAME = COMMAND` per line, for example to `~/.config/shotbox/programs`:

```
# NAME = COMMAND ARGS..., split as a shell would
editor = gnome-text-editor
app = ./build/myapp --debug
```

A command with a slash that isn't absolute is relative to the file, and
every program starts in the file's directory. Put the file in a
directory of its own or in the project's: the server reads log files
from the file's directory when asked to wait for a line in one.

## 4. The MCP server

Add it to Cline's MCP settings (`cline_mcp_settings.json`), with the allow
file's absolute path:

```json
{
  "mcpServers": {
    "shotbox": {
      "command": "shotbox",
      "args": ["mcp", "--allow", "/home/USER/.config/shotbox/programs"],
      "timeout": 300
    }
  }
}
```

`timeout` is in seconds: waits and recordings can run longer than the
default 60. The `command` is `shotbox` as pipx put it on PATH; if Cline
doesn't find it, use its full path (`command -v shotbox`, usually
`~/.local/bin/shotbox`).

## 5. Check it

If the client doesn't list shotbox's tools yet, ask the person to reload
it (in VS Code, Developer: Reload Window), then go on. Call
`start_session`, start one of the person's programs with
`spawn_program`, `wait_for` its window, then `get_accessible_tree` or
`take_screenshot`, then `stop_session`. The server's instructions, sent
as it connects, say how the tools fit together.

More: [docs/mcp.md](https://github.com/mishan/shotbox/blob/main/docs/mcp.md).
