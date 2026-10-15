# Python

A script that takes many pictures can drive the display itself rather than
start a `shotbox` process for every click. Inside `shotbox run`,
`shotbox.here()` is the session's display; outside, `shotbox.Session` starts
one. Both are a `Screen`:

```python
import shotbox

with shotbox.Session(size=(1600, 1000), failed="out/chat-failed.png") as s:
    s.spawn(["gtkhx"], log="gtkhx.log")
    s.wait_window("GtkHx.*")
    s.click(560, 660, window="GtkHx.*")
    s.type("/clear\n")
    s.wait_stable(window="GtkHx.*")
    s.capture("out/chat.png", window="GtkHx.*", park=True)
```

- Waits: `wait_window(re)` (a title, or `app=RE`; returns
  `(id, name, w, h, x, y)`),
  `wait_port(n)`, `wait_file(path)`, `wait_ready()`,
  `wait_line(re, path, from_start=False)` (returns the line),
  `wait_stable(secs=0.5, window=None)`, and `until(what, test)` for a test
  of your own; each takes `timeout` (30).
- Input: `key(*chords, hold=None, state=None)`, `type(text)`, `click(x, y,
  window=None, button=1, double=False, hold=None, state=None)`, `move`,
  `drag`, `park()`. One X connection, kept. In a session with `gamepads`:
  `pad_press(names, n=1, hold=None, state=None)`, `pad_stick(which, x, y,
  n=1, hold=None)`, `pad_trigger(which, value, n=1, hold=None)` and
  `pad_neutral(n=1)`.
- Pictures: `capture(out, window=None, crop=None, park=False,
  vision=None)`, `burst(out, count, every=0, ..., sheet=None)`, and
  `window(re)` for where one is. `record(out, seconds=None, window=None,
  ..., sound=False)` takes a video, or a `.wav` of the sound, and returns
  a Recording: its `path` and, with `sound`, `heard` (as
  `shotbox.sound.hear` says) and `problems()`.
- Scripts: `play(script, verbose=False)` plays one as `shotbox play`
  does: a string is its text, a `Path` its file. An error names the line.
- Anything that fails raises `SessionError`, saying what; with `failed` set
  (or `$SHOTBOX_FAILED` inside `shotbox run`) it leaves a picture of the
  screen there first, and says where.
- Accessibility, in a session with `a11y=True`: `a11y_tree(window=None)`,
  the nodes (dicts, and attributes: `role`, `name`, `description`,
  `states`, `path`, `depth`, and `bounds` on the screen); `find(role=None,
  name=None, window=None)`, `name` a regex; `focused()`; `wait_focus(re)`;
  `tab_walk(key="Tab", most=50, window=None, visible=False)`, the nodes
  the focus lands on, failing on a trap (and with `visible`, where the
  focus doesn't show); `a11y_lint(window=None, ignore=())` and
  `contrast(window=None, text=4.5, graphics=3, edges=None)`, the nodes
  with a `problem`; `click_node(name, role=None, window=None)`; and
  `wait_announce(re)`. `focus_window(re)` gives a window the keyboard.
- A `Session` also has `spawn(cmd, log=None)`, `run(cmd)`, `env` and
  `scratch`. It takes the session options as arguments: `a11y`,
  `announce_log`, `audio`, `clock`, `tz`, `dark`, `high_contrast`,
  `text_scale`, `wayland` and the rest.

Installed, `import shotbox` just works. With a checkout, put its root on
`PYTHONPATH` (`--pass PYTHONPATH` to let it
into a session).
