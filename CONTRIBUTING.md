# Contributing

## Tests

`test/run.sh` checks the session, the terminal, the pictures, the Python
API (`test/api.py`, which needs GTK 3's PyGObject) and the Node helpers, and
takes a few seconds. The helpers that need a browser use Playwright's
Chromium (`npm install && npx playwright install chromium`) and skip
without it, the Wayland sessions need sway, grim and Xwayland and skip
without them, the accessibility checks need GTK 4, libadwaita and
at-spi2-core and skip without them, `--audio` needs PipeWire,
WirePlumber and `pactl` and skips without them, `--gamepads` needs
bubblewrap and a writable `/dev/uinput` and skips without them, and
`--clock` needs libfaketime and skips without it.
