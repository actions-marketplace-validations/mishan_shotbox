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
  `wait_stable(secs=0.5, window=None)`, `wait_match(ref, window=None,
  crop=None, fuzz="0%", most=0, ignore=(), margin=0)` and
  `wait_change(...)` (as `shotbox wait match` and `change`, `ignore` a
  list of `WxH+X+Y` boxes; `wait_match` returns a `Match(seconds, x, y)`,
  x, y where the picture matched: where a `margin` found it, else the
  crop's corner, or 0, 0; `wait_change` the seconds), and `until(what,
  test)` for a test of your own; each takes `timeout` (30).
- Input: `key(*chords, hold=None, state=None)`, `type(text)`, `click(x, y,
  window=None, button=1, double=False, hold=None, state=None)`, `move`,
  `scroll(x, y, dy, dx=0, window=None)` (wheel clicks down and right),
  `drag`, `park()`. One X connection, kept. In a session with `gamepads`:
  `pad_press(names, n=1, hold=None, state=None)`, `pad_stick(which, x, y,
  n=1, hold=None)`, `pad_trigger(which, value, n=1, hold=None)` and
  `pad_neutral(n=1)`.
- Pictures: `capture(out, window=None, crop=None, park=False,
  vision=None)`, `burst(out, count, every=0, ..., sheet=None)`, and
  `window(re)` for where one is. `record(out, seconds=None, window=None,
  ..., sound=False, until=None, tail=0)` takes a video, or a `.wav` of the
  sound, ended with `until=(re, path)` by a line, as `wait_line` waits for
  one, `tail` seconds after it, and returns a Recording: its `path`, the
  `line` that ended it (or None), whether it was `stopped`, and with
  `sound`, `heard` (as `shotbox.sound.hear` says) and `problems()`.
- Scripts: `play(script, vars=None, name=None, verbose=False)` plays one
  as `shotbox play` does: a string is its text, a `Path` its file. `vars`
  (a dict) is over the session's variables for `${NAME}`, so values
  needn't be put in the text and quoted; `name` is what an error calls it
  (`$EXPLORE:2: ...`). Text starting with a newline, as a triple-quoted
  string does, is numbered from the line after. What its steps print (a
  `wait line`'s line, a recording's path) goes to stdout.
- Anything that fails raises `SessionError`, saying what; with `failed` set
  (or `$SHOTBOX_FAILED` inside `shotbox run`) it leaves a picture of the
  screen there first, and says where.
- Accessibility, in a session with `a11y=True`: `a11y_tree(window=None)`,
  the nodes (dicts, and attributes: `role`, `name`, `description`,
  `states`, `path`, `depth`, and `bounds`: on the screen, or given a
  `window`, here and in the nodes below, from its corner, as `click` and
  `scroll` take an x,y with it; a window's
  with its program's `pid` and `toolkit`, and a program that didn't
  answer as a node with its `pid` and `quiet`); `find(role=None,
  name=None, window=None)`, `name` as `click_node` takes it (exactly,
  else a regex); `focused()`; `wait_focus(re)`; `wait_node(re,
  window=None)`, the node `wait node` waits for, with its `text`, cut to
  80 characters round the match, where that's what matched;
  `tab_walk(key="Tab", most=50, window=None, visible=False,
  reach_all=False, ignore=(), document=False)`, the nodes the focus
  lands on, failing as `tabwalk` does (with `visible`, where the focus
  doesn't show; with `reach_all`, on what it never reaches; with
  `document`, only the web page's, as `--document`); `a11y_lint(window=None,
  ignore=())`, the nodes with a `problem` (one in a web page with its
  `dom` and `text`: [Web nodes](accessibility.md#web-nodes));
  `contrast(window=None,
  text=4.5, graphics=3, edges=None, large=3, passes=False,
  strict=False)`, a dict of `problems`, the nodes with a `problem`, and
  `notes`, what wasn't measured, which doesn't fail, each with its
  `note` (GTK 4's with its `why`), or with `strict` fails, among the
  `problems`, but for what's scrolled out of view; `behind`, what's
  behind a modal dialog, which doesn't fail, each with its `behind`;
  and with `passes`, `passes`, what passes, each with its `contrast`,
  the ratio as `a11y contrast --passes` says it;
  `click_node(name, role=None, window=None, force=False)` and
  `scroll_node(name, dy, dx=0, role=None, window=None, force=False)`,
  waiting as `a11y click` does, unless `force`;
  `shotbox.a11y.changes(before, after)`, what changed from one
  `a11y_tree()` to another, as MCP's actions with `changes` say it;
  `speak(keys=None, window=None, most=50)`, what Orca says as `a11y
  speak` has it, with Orca installed: a dict, its `start` and each of
  its `presses` with the `focus` (a node, or None), whether it `moved`,
  and the `speech` (a list), and `text_view`, if Tab stopped at one,
  the key that leaves it; and
  `wait_announce(re)`. `focus_window(re)` gives a window the keyboard.
- A `Session` also has `spawn(cmd, log=None)`, `run(cmd)`, `env` and
  `scratch`. It takes the session options as arguments: `a11y`,
  `announce_log`, `audio`, `clock`, `tz`, `dark`, `high_contrast`,
  `text_scale`, `wayland` and the rest.

Installed, `import shotbox` just works. With a checkout, put its root on
`PYTHONPATH` (`--pass PYTHONPATH` to let it
into a session).
