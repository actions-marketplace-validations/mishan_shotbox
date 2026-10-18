# Security

## Reporting a vulnerability

Report it privately, through GitHub's
[private vulnerability reporting](https://github.com/mishan/shotbox/security/advisories/new),
not in a public issue. Say what you did, what happened, and the shotbox
version (`pip show shotbox` or the package's version). I aim to answer
within a week, and to fix and release before anything is made public.

## Supported versions

Fixes go into the newest release. shotbox is young and has no long-term
branches: upgrade to get a fix.

## What counts

shotbox's promise is that a session is sealed, and that an agent over MCP
can only run what the person allowed. A way around either is a
vulnerability:

- a program in a session reaching the person's display, session bus,
  home, settings, sound or screen reader, beyond what
  [docs/sealed.md](docs/sealed.md) says isn't sealed;
- the network reached from a session started with `--net loopback`;
- an MCP client starting a program not on the `--allow` list, running a
  command, or reading or writing files outside what
  [docs/mcp.md](docs/mcp.md) says the server reads and writes;
- a session's processes outliving it where shotbox says they stop.

## What doesn't

- What an allowed program does with the access it has: a program on the
  list is the person's, and a terminal or browser on it can do what a
  terminal or browser can.
- Sessions run with `--no-sandbox` or `SHOTBOX_SANDBOX=off`, which give up
  the sandbox by design.
- Text a program shows (titles, names, output) reaching the agent as it
  is: it's the program's, not the person's, and an agent should treat it
  so. Marking it as such in MCP's answers is tracked in #130.
