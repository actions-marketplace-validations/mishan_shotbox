# Contributing

## Tests

`test/run.sh` checks the session, the terminal, the pictures, the Python
API (`test/api.py`, which needs GTK 3's PyGObject) and the Node helpers, and
takes about eight minutes; `SHOTBOX_PART=a11y test/run.sh` runs one of its
parts (`core`, `input`, `a11y`, `record`), as CI does, each on its own
runner. The helpers that need a browser use Playwright's
Chromium (`npm install && npx playwright install chromium`) and skip
without it, the Wayland sessions need sway, grim and Xwayland and skip
without them, the accessibility checks need GTK 4, libadwaita and
at-spi2-core and skip without them, `--audio` needs PipeWire,
WirePlumber and `pactl` and skips without them, `--gamepads` needs
bubblewrap and a writable `/dev/uinput` and skips without them, and
`--clock` needs libfaketime and skips without it.

## Help from AI

AI tools are welcome to help with shotbox, but the attribution they add
by default isn't. CI's `no-ai-marks` check fails on it in commit
messages and authors, the pull request's title and description, its
branch name, and the lines it adds: a `Co-authored-by:` trailer naming an
AI, a "Generated with …" footer, a 🤖 or `[AI]` tag, a link back to the
session, a branch named `claude/…`, `copilot/…` or the like, and hidden
characters such as zero-width spaces. Its
[README](https://github.com/mishan/no-ai-marks#what-it-checks) has the
full list.

If it fails, take the mark out: amend or reword the commits, rename the
branch, or edit the description, which runs the check again. Agents
working on shotbox: leave these out from the start.
