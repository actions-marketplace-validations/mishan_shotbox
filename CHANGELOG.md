# Changelog

## 0.10.0 — 2026-10-23

Contrast measures web pages' text run by run, in Firefox as in
Chromium, on lines that say where in the DOM each is. It pictures the
screen six times, so text that fades or blinks is said with its range;
it fails GTK 4 text that matches in two places and fails in both, and,
with `--window`, faint GTK 4 text it can't find where nothing there
could pass; and it says what's behind a modal dialog apart. A click by
name waits until its node is ready, and `--action` reaches one a click
can't; on Wayland the session holds one pointer, as on X11. `wait node`
waits on the accessible tree, and pictures match with boxes left out
and within a margin. `tabwalk --document` and a Firefox recipe help
with web apps; MCP gains zoom, numbered labels and tree changes; a
seeded home's GSettings are kept. Bounds given a window are now the
window's, in MCP and Python: see the changes to check.

### Changes to check when upgrading

- **`a11y contrast` pictures the screen six times, 0.37 s apart, and
  says text whose ratio changes across what it needs as changing, with
  its range**: `PATH: text changing: 2.50:1 to 11.24:1 (at least 4.5)`,
  a note, which fails only with `--strict`. A fading or blinking prompt
  passed or failed by when it was pictured. Changing text that fails at
  its best fails, `text 3.10:1 at best, changing from 1.40:1, …`, and
  that passes at its worst passes, `text passes at 5.20:1 at worst,
  changing to 9.80:1, …`; a control or image that changes is judged the
  same way. `--json`, Python's `contrast()` and MCP's `check_contrast`
  give each its `range`, and the note its `why`. The pictures are taken
  while measuring goes on, of only what's measured, so a check with
  anything to measure takes at least 1.85 s: up to that much more where
  measuring took less (a still GTK 3 window, 2.3 s, not 0.5; about 1 s
  more on a Chromium page; nothing on a GTK 4 window, which takes
  longer, but for its text not found that changed, looked for again in
  each picture: 10 to 14 s more for a label first caught blinked off).
- **On Wayland the pointer stays where a command leaves it, as on X11**,
  hovering there, until a command moves it; `park` and `--park` move it
  to the bottom-right corner, where it starts. Each command made a
  pointer of its own, which went when it ended, and the hover with it.
  The pointer is drawn as nothing, in pictures and recordings: the
  session gives sway and its programs a cursor theme whose every cursor
  is clear (`XCURSOR_THEME`, GTK's `gtk-cursor-theme-name` and GSettings'
  `cursor-theme`), since sway 1.9 (Ubuntu 24.04) puts the cursor in a
  picture.
- **`a11y lint` and `a11y contrast` lines on a web page's node end in
  where it is in the page's DOM**, the element's tag and id, its text
  and the nearest id round it, as Chromium and Firefox give them:
  `PATH: text 2.84:1, #999999 on #ffffff (at least 4.5)  <- p "Faint
  words" in div#intro`. A path alone, `.../document
  web[3]/section[0]/section[2]/static[0]`, was hard to find in the
  source. `--json`, Python's `a11y_lint()` and `contrast()` give each
  such node `dom` (with its element's `class` too) and `text`, and MCP's
  `check_accessible_names` and `check_contrast` lines end the same way.
  `--ignore` matches the ending too: `--ignore 'div#intro'`. A script
  that reads the lines as `PATH: WHAT` gets the end in WHAT.
- **`a11y tabwalk` says a web page's controls Tab never reaches in
  Chromium**, so `--reach-all` can fail where it passed: Tab lands on
  the page's document coming in from the toolbar, and that counted as
  reaching all that's in it, so a button with `tabindex="-1"` passed.
  Leave out what's meant to be off the Tab order with `--ignore`.
- **A click or scroll by name waits until the node is placed, enabled,
  still and uncovered**, and fails, saying which it isn't, when its
  timeout runs out: `gave up after 10s: button "Save" [focusable,
  disabled] isn't enabled`, `... has no position yet`, `... is
  off-screen`, `... is outside its window` (the next three failed at once before,
  the first as "can't tell where", which a browser's node just added
  was), `... is moving`, `... is covered by label "Cover"` (in a GTK 4
  window), `... is covered by window "Dialog"` (a dialog of its window,
  on X11). That's `a11y click` and `a11y scroll`, Python's `click_node`
  and `scroll_node`, and MCP's `click_element` and `scroll_wheel`. A
  click on a disabled button passed, doing nothing, and one under a
  dialog or mid-move went elsewhere; to click anyway, as a check that a
  disabled control does nothing, pass `--force` (`force=True`, MCP's
  `force`), which still waits for it to be placed. A ready node costs
  the click about 50 ms, to see it's still.
- **`Screen.wait_match` returns a `Match(seconds, x, y)`**, not the
  seconds as a float: x, y is where the picture matched, the crop's
  corner (0, 0 without one), or where `margin` found it. A caller that
  used the float takes `.seconds`. MCP's `wait_for` match says where
  too: `It matches ref.png, after 0.412 s, at 103,58.`
- **`a11y contrast --window` fails GTK 4's text it doesn't find, where
  nothing there could pass**: `text not found, but nothing where it can
  be has the contrast it needs: the best anything there reaches is
  1.65:1 (at least 4.5)`, with its `why`. It was a
  note, which doesn't fail, however faint, so a run that passed can
  fail now. Stock libadwaita has some: an empty `AdwEntryRow`'s title
  (1.98:1), Files' preferences group description (3.21:1), and Settings
  › Sound's "No Output Devices" (3.23:1). Leave them out with
  `--ignore` if they're not yours. Text dimmed behind an open dialog
  doesn't fail: see the next.
- **`a11y contrast` says what's behind a modal dialog apart, and
  doesn't fail it**, with `--strict` either: `PATH: text behind a modal
  dialog "Settings"`, after what wasn't measured, and under `behind` in
  `--json`, `Screen.contrast()` and MCP's `check_contrast`. That's
  what's outside an aria-modal dialog in its page (one on the screen),
  a modal window's program's other windows, covered or not, and what's
  under a libadwaita dialog in its window, which is always modal; not
  what's under a modal popover. It was measured where the dialog dims
  or covers it, and failed (an aria-modal dialog's backdrop took a
  `#999` paragraph to 2.14:1) or passed on what covered it (a
  libadwaita dialog's own text), so a run that failed can pass now,
  and its lines are new on stdout. Check what's behind with the dialog
  closed.
- **MCP's `take_screenshot` says where a point on a picture is for
  `click_element` as a sum, `100 + x/0.8122, 50 + y/0.812`**, in place
  of "divide an x,y on it by that, then add the crop's x,y": a client
  that reads the old sentence needs the new one. Its `crop`, in a burst
  too, is `WIDTHxHEIGHT+X+Y` only, as `record_video`'s and `wait_for`'s
  are, not any ImageMagick geometry (`100x100`, `50%x50%+0+0`).
- **MCP's bounds, given a window, are the window's**: from its top left
  corner, as `click_element`'s x,y, `crop` and `zoom` with it are, in
  `get_accessible_tree`, `check_accessible_names`, `check_contrast` and
  a click or scroll by name, and the nodes it lists when more than one
  matches. They were the screen's, so a node's bounds given to a click
  in the window were off by where the window is. An agent that added
  the window's place back no longer should.
- **The Python API's bounds, given a window, are the window's**, as MCP's
  are: from its top left corner, as `click(x, y, window=)`'s x,y,
  `scroll`'s and `capture`'s `crop` with it are, in `a11y_tree`, `find`,
  `focused`, `wait_focus`, `a11y_lint`, `contrast`, the node `click_node`
  and `scroll_node` give back, and the nodes they list when more than one
  matches. They were the screen's, so a node's bounds given to a click
  in the window were off by where the window is. Without a window
  they're the screen's, as `a11y --json`'s still are.
- **A seeded home's GSettings keyfile
  (`.config/glib-2.0/settings/keyfile`) is read, with the session's
  interface keys merged into it**, as `settings.ini`'s are; it no longer
  takes `--env GSETTINGS_BACKEND=keyfile`. Since 0.9.0 the session's
  replaced it, losing the settings of seeds that read it that way
  before.

### Added

- The Node package's `accessible` is a page's options too:
  `browser.newPage({ ...accessible })` emulates
  `prefers-reduced-motion: reduce`, so a page that honors it holds
  still for `a11y contrast`. `reducedMotion: 'no-preference'` after it
  checks the motion.
- `a11y click NAME --action ACTION` does the node's AT-SPI action ACTION
  (matched as it is, else in any case) in place of a pointer click, for
  a node a click by name won't reach: a control given no size, which
  never gets a position, or a drop-down's option, which isn't in the
  tree, or isn't showing, until the drop-down is opened: `a11y click
  Pick --action open`, then `a11y click Beta --action select`. An
  action's name and what it does are the toolkit's, so it's named;
  without one, it fails listing the node's actions. It waits for the
  node to be enabled, unless `--force`, but not placed, still or
  uncovered, and says the action: `button "Send" [focusable], by its
  action "press"`; a node without it fails, listing the actions it has,
  as does one whose program refuses it. A click by name that gives up on
  a node with no position lists its actions: `... has no position yet;
  its actions: click (--action NAME does one)`; on a match that isn't
  showing, it says that, as `wait node` does. Python's
  `click_node(action="NAME")` and MCP's `click_element` with `action`
  have it too.
- `wait node RE` waits for a node showing in the accessible tree whose
  name or text RE is found in, `--window RE` for one window's: a status
  line saying a page is drawn, where `wait stable` passes on a WebGL
  canvas still waiting on its server, in place of polling `a11y tree |
  grep`. Giving up, it says what came nearest: a match not showing,
  the nearest names and texts, or that nothing comes near.
  `Screen.wait_node(re, window=None)` and MCP's `wait_for` node have
  it too, and say the node's text where that matched, cut to 80
  characters round the match.
- A recipe, in docs/recipes.md: a web app in Firefox, served on the
  session's own network, in a new profile each run, its window sized
  to the screen, pictured once the page says it's drawn.
- `a11y tabwalk --document` lists only what's in a browser window's web
  page, and says only its controls Tab never reached: Tab still goes
  through the browser's own (the address bar, Back, the tab strip),
  which were listed as not reached on every page. A window with no web
  document is refused, and a walk that never gets into a page with
  controls fails, even without `--reach-all`: `round again, never in
  the page`.
  `Screen.tab_walk(document=True)` and MCP's `check_focus_order` with
  `document` have it too.
- `compare` and `wait match` and `change` take `--ignore WxH+X+Y`
  (repeatable), as `flicker` does, to leave out a box that changes on
  its own, a clock or a cursor, as openQA's exclude areas and
  Playwright's `mask` do, rather than a `--fuzz` loose enough to hide
  real changes. `compare`'s diff shows it magenta, and with directories
  `NAME=WxH+X+Y` is for one picture. `Screen.wait_match(ignore=...)`,
  `wait_change` and MCP's `wait_for` have it too.
- `wait match --crop G --margin PX` looks for the picture up to PX
  pixels from the crop's place each way, as openQA searches for a
  needle, and prints where it matched: `0.412s at 103,58`. For a
  canvas or a game, where what's drawn moves a little from run to run
  and there's no tree to find it by. `compare --margin PX` compares the
  second picture where it matches the first best, but for a band PX
  wide round the edge, and says how far it moved, whether it passes or
  fails. `Screen.wait_match` takes `margin`; MCP's `wait_for` match
  takes `margin` (up to 32).
- `a11y contrast` says on its line why GTK 4's text it looked for wasn't
  taken, and its why says what that means: `(no match good enough: 0.44
  at best)`, `(it matches elsewhere too, in "unchecked")`, `(another
  node's text matches there too: "Row 10")`, naming the node where it's
  its text, not its name, that matches, or `(what matches is drawn
  wider or narrower than GTK lays it out)`. All four read "wasn't found
  there alone" before.
- MCP's `take_screenshot` has `zoom`: a region's corners, `[x0, y0, x1,
  y1]` in `click_element`'s x,y, much as Anthropic's computer-use tool
  takes a zoom's, shown as large as fits (up to 8 times) to read small
  text. Its answer, and a crop's or a shrunk picture's, says where a
  point on it is for `click_element`: `100 + x/4.356, 39 + y/4.355`,
  rather than leaving the agent to undo a scale and a corner itself.
- `a11y contrast --strict` fails on its notes, too, what it couldn't
  measure and text whose contrast changes: for
  CI that wants every node measured, or ignored with `--ignore`. Those
  lines are failures, under `problems` in `--json`, each with its `why`
  if it has one; `Screen.contrast(strict=True)` and MCP's
  `check_contrast` with `strict` have it too.
- MCP's `click_element`, `scroll_wheel`, `press_keys` and `type_text`
  with `changes` say what they changed in the accessible tree once the
  screen holds still, and when it was read: nodes added, gone, renamed
  or in other states, a tree line each, up to 500, so an agent needn't
  read the whole tree again. `shotbox.a11y.changes(before, after)` says
  it of two `a11y_tree()`s.
- `a11y tree` says a program that didn't answer (busy, or hung), on a
  line of its own, where its windows were left out without a word; with
  `--json`, and in `Screen.a11y_tree()`, a window has its program's
  `pid` and `toolkit`.
- MCP's `take_screenshot` with `labels` draws each named node's box and
  a number on the picture, and answers with each number's tree line and
  box, scaled and cropped as the picture is and cut to what it's in, so
  what's seen can be clicked by name.

### Fixed

- On Wayland a click or scroll, or the start of a held click, sometimes
  didn't reach a busy program: between commands sway had no pointer,
  so programs had none, and one slow to take up the next command's
  missed what it sent first. The session's server holds the one
  pointer now, as it holds the keyboard, and every command points
  through it. A button pressed by a command killed outright goes up
  when it dies.
- `a11y contrast` looked for every toolkit's label text where it's
  drawn, as it does GTK 4.14 and later's, whose boxes are wrong; GTK 3's,
  Qt's and Chromium's aren't, so their text is measured in them again.
  A faint GTK 3 label was said as not found, "GTK None
  reports where its widgets are wrong".
- `a11y tabwalk` takes a web page's `<textarea>` as the browser does:
  Tab moves on from it. It was taken for a text view that keeps Tab
  and left with ctrl+Tab, which goes to the browser's next tab, Escape
  and F6, which in Chromium goes into the toolbar, where Tab then went
  round: the page's controls after it were said as not reached, failing
  `--reach-all`, and the browser was left there for the next walk. In a
  web page Tab is pressed first; the rest only if it keeps the focus,
  and where one of them leaves the page and Tab never comes back to
  it, the walk fails, `left the page by F6, and Tab never came back to
  it`, and presses Escape to take Chromium back to the page.
- Firefox from Ubuntu's PPA, which GitHub's runners have, never joined
  an `--a11y` session's bus, so `wait node` and `a11y` gave up on its
  window: it asks whether a screen reader is running, as Chromium does,
  and takes `GNOME_ACCESSIBILITY=1` for a yes, which the session now
  sets with `ACCESSIBILITY_ENABLED=1`.
- `a11y contrast` measured nothing on a page in Firefox, which keeps a
  page's text in what it's in (a paragraph, a list item, a table cell,
  a link, the page itself), where Chromium gives each run of it a
  static node named by it: a page of faint text passed. A web node's
  own text, a Chromium static node's too, is now measured run by run,
  each run in one look and on one line, between the objects in it (a
  link, a check box) and emoji, which are drawn in their own colors:
  never by a whole box, which would take a dark link's color for the
  faint words round it, or the dark words round a faint link that
  wraps for the link's. A run the browser can't place, after up to a
  second's wait, is `can't tell where it is`. A canvas's fallback text
  isn't measured, in Chromium either, since it isn't what the canvas
  draws. And a web page with text, none of it measured, is a note,
  `text not measured: none of the page's text was`.
- A read of the accessible tree (`a11y tree`, `lint`, a click by name
  and the rest) failed with `Object does not exist at path …` when a
  window closed while it was read. A window gone is left out, as a node
  gone already was, and so are its nodes when it goes while they're
  walked; one gone before its bounds are read leaves its nodes' bounds
  `null`, as a node gone then had.
- `compare` failed on two pictures with a million or more pixels
  differing, unable to read ImageMagick's count, `1.04e+06`; it counts
  them exactly now.
- `a11y contrast` didn't take GTK 4's text where another node's shorter
  text inside it matched there too: "unchecked", under a "check" whose
  room took it in, was a note. A shorter text can't match all of a
  longer one, so where it's drawn apart too it doesn't count against it
  now; "check" itself, matching in both places, isn't taken unless it
  fails in both (see below).
- MCP's `take_screenshot` said one scale for a thin picture, its
  width's, though each side is rounded on its own: a 10x4320 crop said
  0.4 and was 0.363 down. It says both when they differ. A crop with
  none of what's taken in it is refused, not shown as one pixel, or in
  a burst, as a sheet of nothing.
- `a11y contrast` left faint GTK 4.14 and later text over a theme's
  grid unmeasured, which doesn't fail, while text of the same color
  elsewhere failed: the fainter the text, the more of what varies where
  it's drawn are the grid's lines (8% of it under `#3a3446` text on
  `#0f0d14`), and the less it matched its shape. Where the best match
  falls short, it's worked out again there with the lines through the
  text's box taken off: those labels are found, and fail at 1.61:1.
- `a11y contrast` didn't take GTK 4's text where another node holding
  the same text has it in its room: a combo row's selected item under
  an entry holding it, or a label over one, was a note however faint.
  Where no other node could have drawn it there, it's measured, and
  only a failure is reported; if it would pass, it's still a note. Text
  matching in two places that either can be its (a faint "check" over a
  faint "unchecked") is measured in both, and fails at the better if it
  fails in both: `text 2.50:1 at best, …; it matches in two places and
  fails in both`.
- `record` on X11 went by a frame rate ffmpeg guessed from when the
  frames came, not `--fps`: on a busy machine a 30 frames a second MP4
  was 29.97 (2 seconds lasting 2.002), a WebM a frame or two short, and
  a GIF's frames timed wrong (1 second lasting 1.2). It's at `--fps`
  now, as on Wayland.
- `wait announce` heard nothing of a headed Chromium's live regions
  until `a11y tree` or another command had read its tree, which is what
  has Chromium take a screen reader to be at work. The recorder asks
  each program that joins the session's bus for its attributes, as Orca
  does, so in an `--a11y` session a browser's accessibility is on from
  the start, which slows its pages a little.

## 0.9.0 — 2026-10-19

GTK 4.14 and later's text measured for contrast again, found where it's
drawn rather than where GTK says, at its color even when thin, in a
theme's fonts, and with a reason for what isn't; `--passes` and JSON
that keeps notes apart from failures. Sessions sealed from the
machine's system bus and printers by default. `a11y speak` for what
Orca says; announcements of alerts as they appear; one keyboard for a
Wayland session, so the focus is always there to read; blinking carets
and smoothly scrolled text no longer taken for flicker.

### Changes to check when upgrading

- **`a11y contrast --json` keeps what wasn't measured apart**: it's an
  object, not a list: `problems`, each node with its `problem`, and
  `notes`, each with its `note` and `why`, for what wasn't measured and
  doesn't fail. They came in one list, told apart by `fails`, so a
  script counting entries counted the notes as failures. No `fails` key
  now. `Screen.contrast()` gives the same dict, its lists of nodes, and
  has no `notes` argument: the notes are always there. `a11y lint
  --json` is as it was.
- **A session has a stand-in system bus and no printers**, unless
  `--machine-bus` (`Session(machine_bus=True)`), which replaces
  `--system-bus` (`system_bus=True`), now refused. Programs reached the
  machine's system bus and CUPS, so GNOME Settings under `--desktop`
  showed its Wi-Fi networks, and a print dialog its printers. Under
  bubblewrap, the machine's bus and CUPS sockets aren't there either;
  CUPS on localhost:631 still answers a program that asks there, unless
  `--net loopback`. A program that needs the machine's services
  (NetworkManager, logind, polkit, CUPS) wants `--machine-bus`: check
  pictures taken with it before sharing them. MCP's sessions have the
  stand-in.

### Added

- `a11y contrast --passes` says what passes too, after what fails, a line
  each with its ratio and colors (`PATH: text passes at 10.84:1, #2e2e32
  on #e6e6e7 (at least 4.5)`), and for GTK 4's text, how far from where
  GTK says its text is it was found: to compare one version with
  another, or check where text was found, without setting each ratio to
  30. `--json` lists
  them as `passes`, each with its `contrast`; `Screen.contrast(passes=True)`
  and MCP's `check_contrast` with `passes` have them too.
- `shotbox a11y speak [--keys KEY...]`: what a screen reader says, from
  a real Orca started in the session for it, with a home of its own
  there and no speech or braille output. It presses Tab until the
  focus comes round (stopping before a text view, which would take it as
  typing), or the keys given, and prints what Orca said as it started,
  then a line a press: the node with the focus, then what Orca said
  (`button "Save" [focusable, focused]  says "Save button"`), or `says
  nothing`. `Screen.speak()`
  in Python, and MCP's `get_speech`. Needs Orca, which `shotbox doctor`
  lists; checked with Orca 46.1, 48.0, 50.3 and 51.1. The Action's
  `speak: true` installs Orca, and what `a11y: true` does.
- Announcements (`wait announce`, `announcements`, MCP's
  `list_announcements` and `wait_for` announcement) take in what Orca
  says as it appears, and not only GTK 4's announcements and live
  regions: an alert, notification or info bar shown, or first given the
  focus, with its name and its labels' (`assertive: Warning Delete
  notes.md? It can't be undone.`), as GTK 3's message dialogs and
  `GtkInfoBar`, Qt's `QMessageBox` and libadwaita's `AdwAlertDialog`
  (from libadwaita 1.6) are; and the focus renamed (`polite: Copied`), but not a name much
  like the last (a countdown), as Orca leaves out. A GTK 3, Qt or
  libadwaita program that says an error this way is heard.

### Changed

- On Wayland, a session has one keyboard, made as it starts and shared
  by every command until it stops, not one a command, so programs
  always have one, as on a desktop and on X11: GTK 4 and GTK 3 say
  where the focus is between commands (the tree, MCP's `wait_for`
  focus), libadwaita's alert dialog is heard as the focus goes in, and
  a field with the focus draws its caret in pictures. `key --down`
  works there too, and a key a hold killed outright left down stays
  down until `--up`, as on X11; `click --down` is still refused. `a11y
  tabwalk` and `a11y speak` no longer press Shift first.
- `shotbox a11y tabwalk` (and `Screen.tab_walk()`, MCP's
  `check_focus_order`) reads the tree after each press only as far as
  the node with the focus, not all of it: 50 presses through a GTK 4
  list of 300 rows take 5 seconds, not 90, and through a GTK 3 one of
  400, a second, not 11. Every read of the tree asks each node for its
  role once, not three times, and one that isn't showing for nothing
  more than its states: `a11y tree`, `lint` and `contrast` make a
  quarter fewer calls on the accessibility bus, and under half as many
  in a list mostly scrolled out of view. What they find is as before.
- `a11y contrast` measures GTK 4 before 4.14 (Debian 12's 4.8, Ubuntu
  22.04's 4.6), which says where a widget's content is, inside its
  border and padding, and says it right; not its edges (`--edges`),
  which are outside that, nor what's in a popover, or on a stack's page
  no longer shown, a line each. Such a window passed with
  nothing measured before (nothing in it was walked); now what fails,
  fails. docs/accessibility.md, "GTK 4's positions".
- `a11y contrast` measures the text of GTK 4.14 and later, which it left
  out, where it finds it: it draws the node's name in the font GTK says
  and looks for it where GTK's wrong box can be (a stack's place, which
  GTK counts twice, taken off; up to 4.14.5 inside the box, from 4.14.6
  as far right and down as its containers' padding and borders can
  have moved it), and takes it only where it matches and nothing else
  there does, nor another node's text (or placeholder), drawn there to
  compare, and from 4.14.6 where GTK lays it out as wide as found
  (text not all ASCII isn't measured there). A
  failure says how far from where GTK says its text is it was found,
  which is how far GTK has it wrong, or where GTK doesn't say, from its
  box's corner (gnome-calculator's "=", 3.76:1, 14,19 px). Text not
  found (over two lines, a name that isn't
  drawn), not looked for within 30 seconds, images, icons and edges are
  still not measured, a line each.
  Such a window exited 2, nothing measured, and `Screen.contrast()` and
  MCP's `check_contrast` failed on it; now they pass or fail on its
  text. Needs pycairo and PangoCairo (`python3-gi-cairo`,
  `gir1.2-pango-1.0`), which the Action's `a11y: true` installs and
  `shotbox doctor` checks for.
  docs/accessibility.md, "GTK 4's positions".
- `a11y contrast` finds more of GTK 4.14 and later's text, and faster:
  each size and hinting of a node's name is matched at every place in
  its room at once, exactly, not first by a few of its pixels at each,
  and rooms up to 600 by 400 pixels (a sidebar's) are looked in, not 600
  by 200. Under a theme with a grid drawn behind everything and a large
  heading, a sampler had 29 of 36 labels measured in 21 seconds, not 5
  in 34 with the rest out of time; gnome-calculator takes 1.2 seconds,
  not 2. A match elsewhere in the room for the best-matching shape,
  which keeps text from being taken, is always seen, and comparing
  other nodes' text keeps to the 30 seconds too.
- `a11y contrast` doesn't look for GTK 4.14 and later's text all outside
  its window or a scrolled window it's in (a row scrolled out of view,
  which GTK calls showing; not in a popover, which can be drawn outside
  both), and says so: `text not measured: not shown (scrolled out of
  view, or hidden)`, not that it wasn't found. Nor, from 4.22.3, a
  single letter, which GTK gives no width. The line for text not looked
  for says why (a single letter, over more than one line, not all
  ASCII), as its why does. On 4.16 and later, text drawn a quarter of a
  pixel off the rows (a line set by its baseline) is found: a theme's
  sampler's description, which matched 0.967, matches 0.995.
- Chromium's pages (Google Chrome's, Playwright's Chromium's) are read
  in an `--a11y` session without `--force-renderer-accessibility`:
  `shotbox a11y` asks a browser window for its attributes, as Orca does,
  which has Chromium build the page's tree for the rest of the
  browser's life, and waits for it, up to `--timeout` for a big page
  (one whose tree isn't built by then is noted, or fails with
  `--window`). A window with no page (a crashed
  tab, or a browser started with
  `--disable-platform-accessibility-integration`) is noted on stderr;
  the one `--window` asks for, with nothing in its tree, fails saying
  so and what turns it on, from `a11y tree` and the other commands and
  MCP's `get_accessible_tree`, rather than give the window alone.
  Electron's windows are asked too, but not waited for. The Node
  package's `accessible` no longer has the switch.
- Playwright finds its browsers in a session without
  `PLAYWRIGHT_BROWSERS_PATH` given: the session sets it to yours
  (`~/.cache/ms-playwright`, or what it is outside), not the scratch
  home's empty cache; not with `--identity`, whose sessions don't have
  your name. docs/sealed.md.

### Fixed

- `a11y contrast` crashed on GTK 4.14 and later text whose CSS weight
  isn't one Pango names (`font-weight: 450`), measuring nothing. Its
  text is looked for at the nearest named weight.
- `a11y contrast` measured thin GTK 4 text lighter than its color, and
  failed it: a stem thinner than a pixel covers none whole, so a "1" in
  `#b0b0b0` on `#353535` (5.66:1) measured 3.94:1, and DejaVu Sans's
  "l" at 9px is as thin on GTK 4.14 as on 4.24. Text found where it's
  drawn, whose glyphs cover fewer than three pixels whole, is now
  measured at the color a pixel they covered whole would be, from how
  much of each they cover, where the shape found accounts for every
  pixel (no shadow, a size it's drawn at); other text, as before. That
  measures a small "." or "," too, rather than calling it too small,
  and a "." on a theme's grid, whose lines counted as its pixels, is
  too small to measure, not failed at its antialiased edge.
- On Wayland, a modifier held down alone (`key --down shift`, or `key
  --hold 2 shift &`) didn't shift the keys pressed or typed meanwhile, nor
  a click: `shift` was pressed without its modifier bit. Now it does, as
  on X11.
- Announcements: an alert dialog that comes up with its program, the
  focus in it, could go unheard, the program saying where the focus is
  before what's there could be asked about; a window that comes up is
  now looked in for it.
- GTK 4 before 4.10.3: the tree, lint, `click` and the walks take
  what's in a window, not the window alone (GTK said only its windows
  were showing); before 4.10, a node GTK names by its class
  (`GtkButton`) has no name, so lint finds an icon button with none.
  Before 4.14, a window that draws its own frame (on X11 with no
  compositor) has its nodes placed, inside its border, not `null`.
- `a11y contrast` on a window bigger than the screen (Chromium's, at
  its own size) crashed on a node past the screen's edge; now that's
  not measured, a line saying it's outside the screen, and `click`,
  `scroll` and `tabwalk --visible` leave it out too, saying so. Text
  the edge cuts off (right-aligned, past it) is noted as partly
  outside the screen, not failed as "not seen".
- A width or height below zero or over 32767 is taken as bounds not
  known (`null`): GTK 4 sends an unset size for a widget it hasn't
  realized (a notebook's page never shown), which the tree and
  contrast took as a real size. GTK 3's -1 by -1, for what it doesn't
  draw, is `null` too, and still not linted, nor a failure in contrast.
  docs/accessibility.md, "Where a node is".
- `shoot --twice` (and `term --shoot --twice`) passed a picture with a
  blinking caret in it, both runs catching it in the same phase. Now
  the first run is watched for 2.5 seconds after its picture, and
  something blinking fails it, said with where and how often (`a cursor
  (or something) blinks at 4x16+48+16 every 530 ms`); it takes that much
  longer. A look held a second or more isn't caught. And fewer carets
  blink in a session: GTK 3's on Wayland, read from GSettings, and Qt
  6.5's or later on X11 and Xwayland, read from XSETTINGS, which a
  session now serves (but under `--desktop`, which leaves that to the
  desktop), are off with GTK's. Qt 5's, and Qt 6's before 6.5, have no
  setting to turn off. docs/desktop.md, "Blinking carets".
- GTK 3 on Wayland animated (a spinner spun), reading
  `enable-animations` from GSettings over `settings.ini`. A session's
  GSettings are now a keyfile in its scratch home, not memory, saying
  that and the caret's blink are off, over the system's defaults (its
  vendor's overrides included); what a program sets there is seen by
  the session's other programs too, and goes with the session.
- `flicker` doesn't call smoothly scrolled text flicker. Text scrolled
  by fractions of a pixel (GTK 4's or kinetic scrolling, a browser at a
  fractional scale) is never the same pixels moved, so where its lines
  came round to the same rows every other picture, a tile flipped
  between two looks that the check for scrolling, which wanted an exact
  copy, didn't take for it. A flicker's look is now also matched, as a
  video encoder does, against the picture before or after moved up or
  down by up to a tile, to half a pixel; a shift that leaves half or
  less of what differs in place is scrolling. Not across: stripes
  swapping are each other moved across, and still flicker. It's asked
  of about eight of a flicker's pictures; the exact copy, of all.
- `flicker` doesn't find a flash at the start or end of watching: a
  change whose look before or after the watch cut short, as a square
  moving off a tile in its first picture, or an edge sweeping past in
  its last, was taken for one (`flash: 24x16+176+48, #e47878 for 30 ms
  in #ffffff, at 0.005s`), the motion beside it unseen. A flash's look
  before and after must hold longer than `--brief`, as it always did
  in the middle of a watch.

## 0.8.0 — 2026-10-18

MCP tools named for what they do (verb_noun: renamed, see below), with
descriptions that say when to use each; MCP revisions 2025-11-25 and
2026-07-28; pictures sized for the model looking at them, bounded
answers, and a program's text marked as its own. Waiting for a spawned
program's window by its name, window waits that say what's there, and
sessions killed outright cleaned up, with `sessions --stale` and
`--clean` for what was left before.

### Changes to check when upgrading

- **An unknown MCP tool is a protocol error**: a tools/call of a tool
  shotbox doesn't have is a JSON-RPC error, -32602, as the spec has it,
  not an `isError` answer.
- **MCP's tools are named verb_noun**, so an agent can guess what each
  does from its name. A client's permission list that names them
  (Claude Code's `mcp__shotbox__click`, say) needs the new names; a call
  by an old one is refused, naming the new one. Their arguments and
  answers are as before.

  | Was | Now |
  |---|---|
  | `spawn` | `spawn_program` |
  | `program_output` | `get_program_output` |
  | `accessible_tree` | `get_accessible_tree` |
  | `screenshot` | `take_screenshot` |
  | `record` | `record_video` |
  | `click` | `click_element` |
  | `scroll` | `scroll_wheel` |
  | `gamepad` | `use_gamepad` |
  | `lint` | `check_accessible_names` |
  | `contrast` | `check_contrast` |
  | `tab_walk` | `check_focus_order` |
  | `announcements` | `list_announcements` |
  | `sound` | `record_audio` |

  `start_session`, `stop_session`, `press_keys`, `type_text` and
  `wait_for` keep theirs. The commands (`shotbox click`, `a11y lint`...)
  are as they were.
- **MCP marks a program's text**: `get_program_output`'s lines, the line
  `wait_for` line and `record_video`'s `until_line` end on, and
  `list_announcements` come a line each after `| `, and the instructions
  say such lines, and quoted names and titles, are the programs', not
  the person's. A client reading
  `wait_for` line's answer as the bare line needs to take the `| ` off.

### Added

- `shotbox --version`: which shotbox this is.
- `shotbox sessions --stale` lists scratch dirs whose sessions have gone,
  with each one's size and the command, pid and time that made it, which
  a `shotbox.json` in it now says, and lasting sessions unused for a day;
  `shotbox sessions --clean` removes those dirs, with what's left running
  of their sessions (an older shotbox's, which say nothing, only with
  `--unmarked`). `shotbox doctor` says how many there are, and their
  size.
- MCP's `wait_for` a window takes the name a program was spawned by, and
  waits for a window of that program's, whatever its title, on X11 and
  Wayland, over one titled the name (`^name$` still waits for that),
  which is taken if the program has none of its own, as a daemon's
  window is; `spawn_program`'s answer says so.
- MCP revisions 2025-11-25 and 2026-07-28, with `server/discover`.
- MCP's `take_screenshot` and `record_video` say a picture's size, and
  shrink one past what Claude sees unscaled (a 1568 px long edge or 1568
  patches of 28 px: 1920x1080 to 1456x819), saying the scale, so an x,y
  read off it can be mapped back for `click_element`. A burst's and a
  clip's sheet lays its pictures out as many to a row as shrinks them
  least, two 1920x1080 screens to a row, not always four.
- MCP's `start_session`, without the system's PyGObject in sight (plain
  `pipx install`, uvx), starts anyway and says which tools won't work
  and how to fix it, as it says what it couldn't seal.

### Changed

- A session killed outright (SIGKILL) no longer leaves its scratch dir
  in `$TMPDIR`, nor its X server, bus and programs running, nor those of
  a session started in it: a watchdog process of its own stops them,
  letting a recording finish its file
  within 5 seconds, and removes the dir (not one kept
  with `--keep`), as `shotbox mcp`'s does for its sessions, which it now
  stops with SIGTERM too, before SIGKILL 5 seconds on.
- MCP's answers are bounded: `start_session`'s screen is at most
  7680x4320; `get_accessible_tree`, the checks and `list_announcements`
  stop at 500 lines, saying how many more and to narrow it with window;
  and a program's line, or a tree line, stops at 500 characters.
- docs/mcp.md says that `wait_for` line reads any text file under the
  allow file's directory, so it belongs in its own or the project's, not
  your home, and that allowing a terminal, browser or file manager hands
  the agent arbitrary commands through `type_text`.
- A command that needs PyGObject, run from uvx or `uv tool install`,
  whose environments can't see the system's, says to install with
  `pipx install --system-site-packages shotbox`, not to make the
  virtualenv with `--system-site-packages`, which uv's tools can't.
- MCP's tool descriptions say when to use each tool and which to use
  instead, what its answer holds, and its defaults: `get_program_output`
  reads a program that has exited, `list_announcements` keeps what was
  said, oldest first, and `check_contrast` raises the window it measures.
- MCP's `check_focus_order`, when nothing has the focus to start from,
  says to click the window with `click_element`, not to run
  `shotbox focus`, which an agent can't.
- A window wait that gives up (`wait window`, `Screen.wait_window`, a
  `--window` that waits, MCP's `wait_for`) lists the windows there are,
  the nearest first, with their app ids on Wayland, as a click by name
  lists the nearest names; so does a `--window` with no such window.

## 0.7.1 — 2026-10-17

MCP tool definitions an agent or a directory can read in full: every
parameter described with its limits, the allowed programs listed, and
hints as the spec reads them.

### Changed

- MCP's tools/list describes every parameter, with the limits the server
  holds them to (`minimum`, `maximum`); `spawn`'s and `program_output`'s
  `program` is one of the `--allow` file's names. The tools give the
  hints clients look for: `wait_for` is read-only, `stop_session` alone
  destructive, and only `spawn` and the tools that drive a program
  reach an open world.
- MCP's `wait_for` takes a `timeout` of 0 as 0 seconds, not 10, and
  `start_session` refuses a `text_scale` under 0.1.

### Fixed

- `flicker` doesn't take text scrolling smoothly past, lines apart, for
  flicker: the background between lines comes back, but only every
  other look, and a look drawn plain between frames of a scene has to be
  plain each time it comes back.
- `flicker`'s docs say what it can't tell from flicker: text scrolled
  by fractions of a pixel (GTK 4's or kinetic scrolling, a browser at a
  fractional scale), whose rows repeat, as it's never the same picture
  shifted.

## 0.7.0 — 2026-10-16

Accessibility checks you can gate on: lint and tabwalk agree with Orca
and don't edit what they walk, and contrast says what it can't measure
(GTK 4) rather than guess. Scrolling; names clicked as they are; an MCP
server that tells agents how to use it, answers while it works and
stops what's cancelled; and `--clock` that works on Ubuntu 24.04.

### Changes to check when upgrading

- **GTK 4 apps' contrast isn't checked**: GTK 4 says where its widgets
  are without their containers' padding and borders, and a stack's
  child with its place in the stack counted twice, so what was measured
  was often a button's fill or a neighbor's text. `a11y contrast` says
  each GTK 4 node is "not measured", and why on stderr; with nothing
  measured at all, as in a GTK 4 window, it exits 2, not 0 (or 1), and
  `Screen.contrast()` raises. GTK 3, Qt and browsers are measured as
  before. `SHOTBOX_TRUST_GTK4_BOUNDS=1` measures GTK 4 at its bounds, for
  a GTK whose bounds you've checked. docs/accessibility.md, "GTK 4's
  positions".
- **A name is taken as it is before as a regex**: `a11y click`, MCP's
  `click`, `find()` and `click_node()` pick the nodes named exactly NAME
  over those NAME matches as a regex, among those showing when they
  look, so a calculator's `.` is its button rather than every
  one-character one, and `(Draft)` is itself. A window's title, a role
  and a focus wait match a name as it is too, or as a regex: `Foo (1)`
  now finds the window titled that.
- MCP's `lint` and `contrast` answer with each node as `accessible_tree`
  has it, with its bounds, rather than its path. An `--ignore` (`ignore`)
  regex is found in either.

### Added

- `a11y contrast` finds large text, 18pt or 14pt bold all through, by
  its font as GTK and Chromium say it, and holds it to WCAG's 3:1 rather
  than 4.5:1; `--large RATIO` sets that ratio. `Screen.contrast(large=...,
  notes=True)`, the second for what wasn't measured; MCP's `contrast`
  lists that after the failures, and why.
- **Scrolling**: `shotbox scroll X Y DY [DX] [--window RE]` turns the
  mouse wheel at a point, DY clicks down and DX right (negative, up and
  left), over XTEST's buttons 4 to 7 on X11 and the virtual pointer's
  `axis_discrete` on Wayland; `shotbox a11y scroll NAME DY [DX]` over a
  node found by its name, as `a11y click` finds it. A step in `play`
  scripts too; `Screen.scroll` and `Screen.scroll_node`, and MCP's
  `scroll`, by a node's name or a point.
- **MCP's server instructions**: as it connects, `shotbox mcp` tells the
  agent how its tools go together; clients pass it on, and directories
  show it.
- `record --until-line RE FILE [--tail SECS]`: a recording that ends on
  an event, once a line matching RE is written to FILE, as `wait line`
  waits for one, and SECS after it; `--seconds` is then the most it
  lasts. It prints the line, or says it was stopped or ran its
  `--seconds` first. `Screen.record(until=(re, path), tail=...)`, whose
  Recording says the `line` and whether it was `stopped`; MCP's `record`
  takes `until_line`, `file` and `tail`, its file in the `--allow` file's
  directory, as `wait_for`'s line is.
- `SHOTBOX_SANDBOX=off` in the environment: `--no-sandbox` for every
  session, from Python and MCP too, and for one started inside a session,
  for a Dockerfile or CI job to set once (`false` and `0` too). `shotbox
  doctor` says whether sessions have a sandbox, and why not.
- `shotbox key shift`: a modifier alone.
- `a11y tabwalk` says what Tab never reached (`not reached by Tab`): the
  showing, sensitive controls and fields, and tables and lists whose
  items take the focus; the last line counts them. `--reach-all` fails
  on them; without it they don't fail the walk, since arrows or
  shortcuts may reach them and a window's own buttons are out of the
  Tab order. `--ignore RE` leaves out those it's found in.
  `Screen.tab_walk(reach_all=True, ignore=...)`; MCP's `tab_walk` says
  them too, and takes `ignore`.

### Changed

- A session that can't make a sandbox says so only where it would have
  hidden something: not in a container with no sound, input, HID or USB
  devices and nothing in `/run/user/UID` or `XDG_RUNTIME_DIR`. Where it
  says so, it says how to say it's meant.

### Fixed

- shotbox's own commands inside a `--clock` session (`wait`, `key
  --hold`, `record`...) run on the real clock, rather than under the
  session's libfaketime, which in 0.9.10 (Ubuntu 24.04's) makes the
  `time.sleep` of Python 3.11 and later fail; the programs they start for the session, as
  `term`'s, keep the session's clock.
- `a11y contrast` leaves out a disabled control's label, as the control
  is; says text too small to have a core of its color (a ".") is too
  small to measure, and passes it; and takes text as the third pixel in
  order of contrast, not the strongest color three pixels share, which
  a thin stroke seldom draws.
- A name, role, window title, announcement or `--ignore` that isn't a
  regex (`+`) says so in a sentence, rather than with a traceback; a name
  that isn't one is taken only as it is.
- `a11y click` and MCP's `click`, when more than one node matches, list
  them as tree lines with where they are, and how to narrow it, rather
  than by their paths; when none does, the nearest names. MCP's `click`
  takes a `timeout`.
- MCP's `wait_for` with a `what` there isn't names the choices, rather
  than asking for a value.
- `shotbox mcp` answers `ping` at once during a long call, takes
  `notifications/cancelled`, calling off the request's waits, click,
  check or recording and sending no answer to it, calls off the call
  running when its stdin closes, rather than finishing it, and sends `notifications/progress` every 5 seconds to
  a call with a `progressToken`. docs/mcp.md says which calls run long,
  and how to raise a client's timeout.
- `a11y tabwalk` no longer presses Tab in a text view, where it typed a
  tab into the document: it tries ctrl+Tab, shift+Tab in Qt (GTK's
  types a tab), Escape (not in a dialog, which it would close) and F6 to
  leave it, and fails as a trap only if none does. One it begins in,
  nothing leaving, or leaves backward, it leaves by giving the focus to
  what's after it, so the rest is walked. It presses Shift first, as
  GTK 4 on Wayland says where the focus is only after a key; with no
  node focused even then, it presses nothing, and says so; focus gone
  after a press is said as that, not as Tab leaving it where it was.
  The docs no longer say ctrl+Tab always gets out: an editor's own
  shortcuts can take it. MCP's `tab_walk` answers in `tabwalk`'s lines
  (`round again after 6`, where it said `Round again after 6.`).
- `a11y lint` and `tree` take a field's label as its name, as a screen
  reader does: a GTK 3 field with a mnemonic label passes, and is
  `text "Name:"` in the tree; `a11y click Name:` (and `scroll`) takes the
  field, not its label. They leave out what isn't showing, as a
  revealer's unrevealed bar (gedit's search bar), not only what's
  hidden; and lint, what says it has no size (gedit's one document's
  hidden tab). Thanks to @jay23606, whose #141 found the same fix.
- `wait line` with a pattern that isn't a regex says so, rather than
  with a traceback.
- `record --sound --seconds N` whose session's sound server stops first,
  as a lasting session's does when it's stopped: the picture ends with
  the sound, rather than running on for up to a second past it.
- `--audio` with `--clock`: the sound server runs on the real clock, so
  sound plays and records. libfaketime moved the timer it plays by (late
  by the offset, or at once, as TIME was behind or ahead of now), and
  nothing in the session could read it.
- `record` whose ffmpeg is still opening what it records 10 seconds in
  kills it and fails saying so, rather than waiting on it for good,
  Ctrl-C and all.

## 0.6.0 — 2026-10-15

Sound, and video with it, on X11 and Wayland; flicker, latency and what a
session used, measured; a session's own clock, identity and network;
timed scripts of input; and checks that a picture comes out the same
twice. Pictures of GTK 4 programs change: see the first note below.

### Changes to check when upgrading

- **GTK 4 draws text with manual font rendering** (GTK 4.16 and later),
  so text is drawn at its color rather than fainter, by however much
  automatic rendering draws it on that GTK release. Every GTK 4 picture
  with text changes, its widths too, so layout can shift, and `a11y
  contrast` measures thin text higher: take committed GTK 4 pictures
  again. A seeded home whose GTK 4 `settings.ini` sets
  `gtk-font-rendering` keeps its own.
- Under bubblewrap, a program can't gain privileges, its supplementary
  groups show as `nogroup`, and one killed by a signal exits with 128
  plus the signal's number. `--no-sandbox` runs it as before.
- `compare` says where pixels differ, after how many: `N pixels differ,
  in WxH at X,Y`, on each picture's line with directories too.
- A session with a display wants ImageMagick, and one with X11 or
  Xwayland `xwininfo` and `xprop` (x11-utils), before it starts, rather
  than at its first wait or picture.
- `Screen.record` returns a Recording, not the path: its `path`, and
  with sound what was heard. It stands in for the path where one is
  opened (`os.fspath`) and in `str()`, but `.suffix` and the rest of a
  Path's are on `.path`.

### Changed

- **Waits look faster.** `wait stable`, `match` and `change` take their
  pictures straight from the X server (a few milliseconds, where `import`
  took tens) or from grim's raw output on Wayland, and compare them in
  memory, with no PNG between; `match` and `change` look every 10 ms. A
  change is seen within about a frame, and the time `change` says is that
  close.
- **Every program a session spawns runs under bubblewrap where it can,**
  not only with `--gamepads`: `/dev/snd` and `/run/user/UID` are empty
  for it, so it can't play through the machine's speakers or find the
  desktop's Wayland, PulseAudio, PipeWire or bus sockets. Without
  bubblewrap, the session says so and runs it as before.
- A window asked for and not there is said with the titles, or app ids,
  there are, and that it's matched in full.
- **The README is a front page**, the reference moved to docs/commands.md,
  sealed.md, gamepads.md, python.md and node.md, the a11y commands to
  docs/accessibility.md, the Wayland example to docs/wayland.md, and the
  tests to CONTRIBUTING.md.

### Added

- **`shotbox play SCRIPT`**: a script of timed input, played in a
  session on one clock, with no command started for each step, so a demo
  plays out the same way each time it's recorded. A line is a time
  (seconds from the start, or `+SECS` after the step before) and a
  command for inside a session; one ending in `&` runs on in the
  background, a recording say, and `${NAME}` is the session's variable.
  Each line's options are checked before the first runs, and a failure
  says its file and line. Also `Screen.play`, which takes the text, or a
  Path to the file, and `vars` for `${NAME}`.
- **`wait line RE FILE`**: wait for a line matching RE to be written to
  a file, and print it, for a script that follows a program's log of
  what happens rather than sleeping by a clock the program may not
  keep to. From the file's end when the wait starts (`--from-start` for
  the top); a file not there yet is waited for, and one rotated or
  rewritten is followed. Also `Screen.wait_line`, and MCP's `wait_for`,
  whose file is in the `--allow` file's directory.
- **`shoot --twice`** (and `term --shoot --twice`): the picture taken in
  two fresh sessions, and a failure if they differ, beyond `--fuzz` and
  `--max`, saying how many pixels and the box they're in, with
  `OUT-diff.png` beside OUT. If they're alike, one is kept as OUT.
- **`--identity USER@HOST`**: who and where a session's programs are,
  for `run`, `shoot`, `term`, `start`, `exec`, `Session(identity=...)`
  and MCP's `start_session`: `hostname`, `uname -n`, `whoami`, `id
  -un`, `getpwuid()`, `ls -l`'s owner, `$USER`, `$LOGNAME` and
  `$HOSTNAME` say USER and HOST, so a picture doesn't show who took it.
  It needs bubblewrap, and not `--no-sandbox`.
- **`--clock TIME` and `--tz ZONE`**: every program a session runs sees
  the wall clock start at TIME (ISO 8601, in ZONE or UTC) and run on, a
  program spawned later reading the same clock as the first, so dates in
  pictures don't change between shoots. For `run`, `shoot`, `term`,
  `start`, `exec`, `Session(clock=..., tz=...)` and MCP's
  `start_session`; by libfaketime, which doesn't reach static binaries or
  Go programs.
- **What a command needs, checked before it starts anything.** A tool
  not on PATH, `/dev/uinput` it can't open for `--gamepads`, or PyGObject
  with VTE (`term`) or AT-SPI (`--a11y`) that this Python can't load is
  said at once in a line, with the package to install, or, from a
  virtualenv that can't see the system's packages, how to make one that
  can (for pipx's, `pipx install --force --system-site-packages
  shotbox`); `Session` and MCP's `start_session` say the same.
  **`shotbox doctor`** goes through every check, a line each, and fails
  only if what every session needs is missing.
- **`--usage [FILE]`**: what a session's programs used, together, and
  apart, its own processes: peak memory and CPU time, from a systemd
  slice of the session's where the user's systemd can make one, else
  read from `/proc`; as JSON to FILE. `Session(usage=True)` and MCP's
  `start_session` take it too.
- **`shotbox latency move X1 Y1 X2 Y2 --watch G`**: how long what's
  drawn takes to follow the pointer, a move at a time, as the median and
  95th percentile; `Screen.latency` from Python.
- **`shotbox flicker`**: a part of the screen that keeps switching
  between two looks, or flashes another once, found from pictures taken
  as fast as they come, a tile at a time, by its mean color and in time:
  its box, how many changes, how often, and both colors; and how many
  pictures it took and how many changed, on stdout whatever's found, a
  rate too slow said. With `--save DIR`, a sheet of each one's pictures,
  its box outlined and a tile round it magnified, each labeled with when
  it showed.
  `Screen.flicker` from Python.
- **`--net loopback`**: a network of the session's own, which every
  program it runs shares and nothing else is on, for `run`, `shoot`,
  `start`, `exec` and MCP's `start_session`: a server a test starts is
  reached by the rest of the session, and from nowhere else. `wait port`
  looks in it.
- **`shotbox listen IN.wav`**: a recording's peak and RMS level, samples
  clipped, and gaps, where a program fell behind and left the sink
  silent; it fails on any, or on silence. `shotbox.sound.hear` from
  Python, and MCP's `sound` tool, which records a session's sound
  between `start` and `stop` and says the same of it.
- **A GitHub Action**, `uses: mishan/shotbox@vX.Y.Z`: installs shotbox
  at that version and what it runs on an Ubuntu runner, with each
  feature (`a11y`, `wayland`, `audio`, `record`, `term`, `gamepads`)
  asked for by name, and lets bubblewrap make its sandbox.
- **`shotbox exec -- COMMAND`**: the session's seal with no display, for
  a headless browser or a test suite: its own environment, home and bus,
  under bubblewrap, and `--audio` if it's wanted.
- **`--no-sandbox`**, for a program that needs a setuid helper (sudo, an
  AppImage), which can't run under bubblewrap.
- **What's sealed**, in docs/sealed.md: what a session keeps from a program,
  and what it doesn't. And, in the README, instructions to give an agent
  so it runs graphical programs, browsers and sound in a session.
- **`wait match REF.png` and `wait change`**, with `--window`, `--crop`,
  `--fuzz` and `--max`: wait until the screen looks like a picture, or
  looks different from when the wait started, and say how long it took.
  Also `Screen.wait_match` and `wait_change`, and MCP's `wait_for`,
  whose picture to match is a file in the `--allow` file's directory.
- **`shotbox record OUT.wav`**: what plays in a session with `--audio`,
  48 kHz 16-bit stereo, bit for bit for what plays in that format, on X11
  or Wayland, for `--seconds` (to the sample) or until stopped.
- **Recipes**, in docs/recipes.md: checking a change in a GUI, visual
  regression in CI, an accessibility audit, a game smoke test and an
  agent over MCP.
- **How shotbox compares**, in docs/comparison.md, with
  linux-x11-harness, agent-workspace-linux, kwin-mcp, agent-desktop,
  dogtail, openQA and others.
- **`shotbox record OUT.mp4` in Wayland sessions**, and .webm and .gif:
  the same options, the same length for `--seconds`, and the same ways
  to stop, the file finished however it stops. wf-recorder takes the
  frames over wlroots' screencopy and ffmpeg encodes them, as on X11.
  sway draws no pointer, so `--pointer` is refused there. The Action's
  `record` installs wf-recorder when `wayland` is on too.
- **`shotbox record OUT.mp4 --sound`**, and .webm: in a session with
  `--audio`, what plays too, in the same file, AAC in an MP4, Opus in a
  WebM, by one ffmpeg, the sound in step with the picture from its first
  frame, within a frame or so. Both are `--seconds` long, and it stops
  the ways `record` does, the file finished. Then it says what `listen`
  would of the sound: its level, clipping and dropouts, by `listen`'s
  `--gap`, `--clipped` and `--quiet`, failing for them with `--strict`.
  Also `Screen.record(..., sound=True)`, whose Recording has what
  `shotbox.sound.hear` heard, and its `problems()`.
- **MCP's `record` tool**: a clip of the screen or a window, up to 60
  seconds and 100 MB, saved as an .mp4 in the session's scratch
  directory or as a new file in the `--allow` file's directory. It
  answers with the path, the clip's length and a sheet of up to 8
  frames from it with their times, as an image, since a model can look
  at frames but not play a file; with `sound`, in a session with
  `audio`, what was heard, as the `sound` tool says it.
  `Screen.record(..., size=N)` ends a recording at N bytes.

### Fixed

- **`term --shoot` passes on `--net`, `--usage` and `--no-sandbox`,**
  which it took and then left out. It now gives shoot every session
  option as it was given, read back through its own options, so one
  added later reaches shoot too.
- **`shoot` makes OUT's directory** if it isn't there, where it failed
  with a traceback.
- **A recording's keeper killed outright** takes ffmpeg (and
  wf-recorder) with it, ffmpeg finishing the file, rather than leaving
  them recording after the session has gone.

## 0.5.0 — 2026-10-14

Holding keys and buttons, pictures as colorblind eyes see them, bursts, videos, and gamepads.

### Added

- **`key --hold SECS` and `click --hold SECS`** keep keys or a button down
  that long, the chords pressed together and let go last first: for games,
  and anything that reacts to how long something's held. In the background
  (`&`), one is held while other input goes on.
- **`key --down`/`--up` and `click --down`/`--up`** leave a key or button
  down between commands, on X11. A Wayland virtual keyboard's keys go up
  when its connection closes, so there `--down` is refused, saying to use
  `--hold`.
- **`shotbox vision IN OUT --as KIND`**, and `--vision KIND` on `capture`
  and `shoot`: a picture as someone with protanopia, deuteranopia or
  tritanopia sees it (Machado et al., 2009, at full severity), or in gray.
- **`shotbox record OUT`**: a video of the display or a window, as an MP4,
  WebM or GIF, for `--seconds` or until stopped (Ctrl-C, `kill`, its
  session's display going), and the file is finished however it stops,
  even killed outright. `--crop`, `--fps`, `--pointer` and `--park`; X11
  sessions only for now, and it needs ffmpeg.
- **`capture --burst N`**, with `--every SECS` and `--sheet FILE`: several
  pictures, numbered and timed, and a sheet of them, for motion.
- **`--gamepads N`** and **`shotbox pad`**: virtual Xbox 360 controllers,
  made with uinput, that only the session's programs see: each runs under
  bubblewrap with a `/dev/input` of its own, and the machine's raw HID and
  USB devices masked. Worked with `pad press`, `stick`, `trigger` and
  `neutral`; what's left down stays down between commands. MCP:
  `start_session` takes `gamepads`, and a `gamepad` tool.
- **`term --shoot`** passes `--audio` on to the session it starts.
- **MCP:** `press_keys` and `click` take `hold` (seconds) and `state` (down
  or up), which last across calls on Wayland too, since a session keeps its
  input connection; `screenshot` takes `vision`, and `burst` and `every` for
  a burst as one sheet.

### Fixed

- **A `TMPDIR` too long for a session's sockets** is said at the start
  (Linux allows a socket path 107 bytes), rather than D-Bus failing to
  start or sway crashing.

## 0.4.3 — 2026-10-13

`contrast` on GTK 3 and on GTK 4 icons and fields.

### Fixed

- **GTK 3's switch isn't measured as text.** It calls a switch a toggle
  button, so its off mark was held to text's 4.5:1.
- **`contrast --edges` takes a field's edge from what it fills all
  round:** a text view in a frame, which has no edge of its own.
- **A GTK 4 icon button is held to graphics' 3:1, not text's 4.5:1.**
  GTK 4 puts a label in a control that draws text, so one with none
  draws an icon. GTK 3's buttons keep their labels to themselves, and
  are still held to text's.
- **A missing GObject typelib is said in a line,** naming the packages,
  rather than with a traceback from the announcement recorder.

### Changed

- **A control with nothing drawn says how it measures with the pointer
  over it,** as a tab's close button is drawn only then: still "text not
  seen", but now one can tell that from text that's never seen.
- **Light GTK 3 text that fails says its color too.** Light or small
  text covers no pixel whole, so it measures short of its color, by more
  on one font and renderer than another, and text drawn see-through
  looks the same: it's still measured as drawn, but where GTK 3 says the
  color, and what's drawn is a lighter shade of it, the failure says it.

## 0.4.2 — 2026-10-13

`tabwalk --visible` on GTK 4's offset nodes.

### Fixed

- **`tabwalk --visible` finds a ring drawn off its node.** GTK 4 places a
  node short of the padding of what it's in, right of and below where
  it's drawn: a libadwaita tab 6 pixels, a header bar's buttons 7, a tab's
  close button 10. Only 4 pixels round a node's bounds were counted, and a
  tab whose top edge already had the ring's color, as a current tab's rail
  can, was reported as showing no focus. When too little changed at its
  bounds, the count is now taken at any offset up to 16 pixels each way
  where what changed looks like a focus shown: along three or four of the
  node's sides, and little just outside them. The ring the focus left
  isn't counted where the walk found it, so a sibling right beside it, a
  radio button or a toolbar icon with a thin ring, still is, and a
  sibling with no ring of its own isn't credited with its neighbor's.
- **No EGL warnings from GTK 4 programs on X11.** They opened EGL though
  they draw with cairo, and Mesa warned on stderr that Xvfb has no DRI3.
  A session now sets `GDK_DISABLE=gl` (GTK 4.16 and later); pictures are
  the same.

## 0.4.1 — 2026-10-13

A fix for sessions that last.

### Fixed

- **Two `shotbox start`s with one name at once both started**, and one
  session was left running with nothing to stop it by. The first wrote
  its record empty and filled it in later; in between, the second took
  the empty record for one whose session had gone. A record is now
  written whole and linked into place.

## 0.4.0 — 2026-10-13

A session can have an accessibility bus of its own, to read, lint and
walk what a screen reader would hear, to click by name, and to check
that the focus shows and text can be read; it can come up dark, in high
contrast or with bigger text, and have a sound server of its own. A
session can last, used by one command after another, and an agent can
have sessions of its own over MCP.

### Added

- **`shotbox mcp --allow PROGRAMS`**: sealed sessions for an agent, over
  the Model Context Protocol. It can start a session, spawn programs from
  the list, read the accessible tree, take screenshots (as images), click
  by name, press keys, wait, and lint, measure contrast and walk the Tab
  order. Its sessions stop when it does. See docs/mcp.md.
- **Sessions that last.** `shotbox start --name NAME` starts a session
  that lasts until `shotbox stop NAME`; `shotbox spawn --session NAME --
  COMMAND` runs a program in it, which stops with it; and `shotbox
  --session NAME COMMAND` runs `wait`, `capture`, `key`, `click`, `a11y`
  and the rest in it from outside. `shotbox sessions` lists them.
- **`--a11y`**, an AT-SPI bus of the session's own, on X11, Wayland
  and Xwayland, that never reaches your desktop's. Inside, `shotbox
  a11y tree` prints the accessible tree as text to commit and diff,
  `shotbox a11y lint` fails for controls, images and focusable nodes
  with no name, and `shotbox a11y tabwalk` presses Tab until the focus
  comes round again, failing on a trap. The Python API has
  `a11y_tree()`, `find()`, `focused()`, `wait_focus()` and
  `tab_walk()`. See docs/accessibility.md.
- **Announcements.** What programs announce to a screen reader (GTK
  4.14's `gtk_accessible_announce()`) is logged, to
  `$SHOTBOX_SCRATCH/announcements` or `--announce-log FILE`, and
  `shotbox wait announce RE` (`wait_announce()`) waits for one, each
  wait taking the next match. Text put in a browser's live region is
  logged the same way.
- **Chromium** is on the bus in an `--a11y` session (it is given
  `ACCESSIBILITY_ENABLED=1`), headed and with
  `--force-renderer-accessibility`, so its pages' trees can be read,
  linted and walked.
- **Where a node is.** The tree's JSON and the Python API give each
  node's bounds on the screen, and `shotbox a11y click NAME`
  (`click_node()`) clicks one by its name.
- **`shotbox a11y tabwalk --visible`** (`tab_walk(visible=True)`) fails
  where the focus doesn't show: too little in and round a node changed
  as it took it.
- **`shotbox a11y contrast`** (`contrast()`) measures, on the screen,
  text, buttons and links against their background (4.5:1), images (3:1), and with
  `--edges`, a button's or field's edge.
- **`--ignore RE`** for `shotbox a11y lint` and `contrast` (`ignore=`), for what
  can't be fixed yet.
- **`shotbox focus RE`** (`focus_window()`) gives a window the keyboard,
  which on X11 nothing else does for a window that comes up after
  another.
- **`--audio`**, a PipeWire of the session's own, pulse protocol too,
  whose one sink plays to nowhere.
- **`accessible`** in the Node package: Chromium's launch options for
  an `--a11y` session.
- **`--dark`, `--high-contrast` and `--text-scale N`**, for libadwaita,
  GTK 4 and GTK 3. Without them, pictures don't change.

### Fixed

- **What a `--desktop` session's bus starts is inside the session.**
  The bus ran what it activated (dconf, the portals) with the caller's
  environment, your `HOME`, `DISPLAY` and runtime dir among it, and read
  service files from your home: a setting changed in the session could
  be written to your own dconf. It now has the session's.
- **`shotbox run` and `shoot` stop their session when they're
  terminated** (`kill`, `timeout`), as they do on ctrl-C, rather than
  leave its display and programs running.

## 0.3.0 — 2026-09-27

A session can be a Wayland one, under a headless sway, with Xwayland in
it for X clients; windows, pictures, waits and input work there as they
do on X11. A Playwright recording can come out the same each run.

### Added

- **Published.** `pipx install --system-site-packages shotbox` (or `pip
  install shotbox`) for the command and the Python API, from PyPI, and
  `npm install shotbox` for the Node helpers. A tag publishes both, from
  CI; see docs/releasing.md.
- **Wayland sessions.** `--wayland` runs the program under a headless
  sway, drawing in software, instead of Xvfb, and `--xwayland` adds an
  Xwayland in it for X clients. Windows are found by title over sway's
  IPC and taken with grim, and `shoot`, `capture`, `stable` waits, failure
  pictures, `term --shoot` and the Python API work as they do on X11.
  `key`, `type`, `click`, `move`, `drag` and `park` speak sway's virtual
  keyboard and pointer protocols directly, reaching Xwayland's X clients
  too. See docs/wayland.md.
- **Windows by app id.** Wherever a window is named, `app=RE` matches its
  app id rather than its title: a Wayland window's `app_id`, or an X
  window's `WM_CLASS`, instance or class.
- **`frames(page)`**, a Playwright recording that comes out the same
  each run: the page's time stands still and moves a frame between
  screenshots, and `steady` is the Chromium flags that keep the frames
  alike. `gif()` takes the directory of frames it writes.

### Fixed

- **What a program leaves behind stops with the session.** A program
  that had already exited when the session ended had its process group
  skipped, so anything it left running in it, a shell script's
  background child say, outlived the session.

## 0.2.0 — 2026-09-27

Sessions can be driven, from the command line and from Python, and wait
for a picture to hold still. Terminal shots take one command. The Node
package grew from the pieces projects had copied into what a Playwright
recording needs: a sealed browser, a pointer and captions, a cut, and a
gif of it. The tests run in CI.

### Changes to check when upgrading

- **The Node package's `package.json` is at the top of the repository**,
  not in `node/`, since npm installs a git dependency from the top and
  nowhere else. Depend on `github:mishan/shotbox`, or on `file:../shotbox`
  for a checkout beside yours; `file:../shotbox/node` no longer works.

### Added

- **Input.** Inside a session, `shotbox key`, `type`, `click`, `move` and
  `drag` drive the display, at a point on the screen or inside a window,
  speaking XTEST directly. `shotbox park` moves the pointer off whatever
  it was hovering. GTK apps in a session no longer blink or animate.
- **`stable` waits** for the display or a window to look the same for a
  while, for what has no other sign it is done. A failed wait leaves a
  picture of the screen as it was.
- **A Python API.** `shotbox.Session` starts a session and
  `shotbox.here()` is the one a script runs inside; both wait, drive the
  display and take pictures over one X connection.
- **`shotbox term --shoot`** starts the session and takes the picture in
  one command, and `--crop COLSxROWS` crops by cells, not pixels.
- **`shotbox compare` on directories**, picture by picture, with `--max`
  and `--fuzz` for one picture by name.
- **`gif()` takes `from`, `colors` and `dither`**: a cut off the start, a
  smaller palette, and no dither for a flat page with something moving on
  it.
- **`sealed()`**, an environment for a headless browser that nothing of
  yours gets into: your fonts, fontconfig and time zone stay out of the
  pictures.
- **`dress(page)`** draws a pointer and captions into a recording, since
  a browser records neither its cursor nor a key pressed.
- **`film(page)`** says where a recording's loop starts, for `gif()`'s
  `from`.
- **`shotbox-serve [DIR] [PORT] [PAGE]`**, `serve()` as a command.
- **TypeScript declarations** for the Node package.

### Fixed

- `shotbox compare` with a missing picture says which, rather than
  failing with a traceback.

## 0.1.0 — 2026-09-25

The first version: `shotbox shoot`, `run`, `wait`, `capture` and `term`
in a sealed session, and a Node package for Playwright checks with a
static server, a check reporter, page errors, process-group launch and
webm to GIF.
