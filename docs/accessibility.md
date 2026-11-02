# Accessibility

A sealed session has no accessibility bus: `GTK_A11Y=none` and
`NO_AT_BRIDGE=1` tell GTK 4 and GTK 3 not to look for one, so nothing a
program does reaches your desktop's screen reader. `--a11y` gives the
session a bus of its own instead, so what a screen reader would hear can
be read, linted and walked, headless, in CI.

## Commands

Inside a session with `--a11y`:

- **`shotbox a11y tree [--window RE]`** prints the tree, a node a line,
  indented: role, name, description if any, and the states a screen
  reader would say: [Reading the tree](#reading-the-tree).
- **`shotbox a11y lint [--window RE] [--ignore RE]`** fails, a line each,
  for what has no name to say: [Lint](#lint).
- **`shotbox a11y tabwalk [--key Tab] [--max 50] [--reach-all] [--ignore RE] [--document] [--window RE]`**
  presses the key until the focus lands somewhere it has been, printing
  where it lands each time and what it never reached, and fails on a
  trap: [Walking the Tab order](#walking-the-tab-order).
- **`shotbox a11y click NAME [--role RE] [--window RE] [--force] [--action NAME]`** clicks the
  node named NAME, exactly or as a regex matched in full: a button by
  its label rather than where it is, once it's enabled, still and
  uncovered; with `--action NAME`, does that AT-SPI action instead, for a
  node a click won't reach. [Clicking by name](#clicking-by-name).
- **`shotbox a11y scroll NAME DY [DX] [--role RE] [--window RE] [--force]`** turns
  the mouse wheel over that node, found as `click` finds it, DY clicks
  down and DX right (negative, up and left): a list by itself or by a
  row still in view (one scrolled out of it is refused, as `click`
  refuses it).
- **`shotbox a11y contrast [--window RE] [--passes] [--strict]`** measures the contrast of
  what each node draws against its background, and fails, a line each:
  `PATH: text 2.51:1, #a0a0a0 on #fafafb (at least 4.5)`; with `--passes`,
  what passes too: `PATH: text passes at 12.23:1, #323236 on #fafafb (at
  least 4.5)`; with `--strict`, what it couldn't measure fails too.
  What's behind a modal dialog is said apart, and doesn't fail.
  [Contrast](#contrast).
- **`shotbox a11y speak [--keys KEY...] [--max 50] [--window RE]`**
  starts Orca in the session, presses Tab until the focus comes round
  (or the keys, in turn), and prints what Orca said after each press,
  with what has the focus: [What a screen reader says](#what-a-screen-reader-says).
- **`shotbox wait announce RE`** waits for a program to announce
  something matching RE (found anywhere in it) to a screen reader:
  GTK 4.14's `gtk_accessible_announce()`, text put in a browser's
  live region (`aria-live`, `role="status"`), an alert or info bar
  appearing (GTK 3's message dialogs, Qt's `QMessageBox`, libadwaita's
  `AdwAlertDialog`), or the focus renamed. [Announcements](#announcements).
- **`shotbox wait node RE [--window RE]`** waits for a node showing in
  the tree whose name or text RE is found in: a status line a program
  sets once it's done, where the screen holding still says nothing (a
  WebGL canvas waiting on its server). Giving up, it says what came
  nearest: a match that isn't showing (scrolled out of view), or the
  nearest names and texts, or that nothing comes near. Python's
  `wait_node` and MCP's `wait_for` give the text that matched cut to 80
  characters round the match. A page's text is matched with each
  element in it taken out and the spaces beside it made one: `<p>Done
  <a>here</a> now</p>` matches "Done now"; with no space beside it, the
  text either side joins, `Join<button>X</button>Me` as "JoinMe".

```
shotbox run --a11y -- sh -c 'gnome-text-editor &
  shotbox a11y lint --window ".*Text Editor"'
```

finds that the editor's text area has no name.

`--window` picks a window by title, or `app=RE`, as everywhere, and
waits up to `--timeout` (10s) for it to appear in the tree. Without it,
every program's windows are there. It works on X11, Wayland and
Xwayland, for GTK 4, GTK 3, Qt 5 and AccessKit's toolkits (egui, Slint,
Bevy, GTK built with it), and for Chromium (Google Chrome, Playwright's
Chromium, and the browsers built on it), headed, on X11.

What `wait node` takes as showing is what the toolkit says is, which
isn't what can be seen: text drawn at opacity 0, or with something over
it, is showing, as is an element of no size in Chromium (Firefox says
it isn't), and a GTK 4 row scrolled out of view (GTK 3's isn't). Each
poll walks the tree, every window's, and reads the text of each node
whose name doesn't match: seconds on a large page, as many as the
machine takes, more in Chromium than in Firefox; one under way when the
timeout runs out is finished first. `--window` keeps it to that
window's tree.

## The bus

With `--a11y`, the session:

- runs `at-spi-bus-launcher --launch-immediately` (at-spi2-core's) on the
  session's private D-Bus. It starts the accessibility bus, a
  `dbus-daemon` with its socket in the session's `XDG_RUNTIME_DIR`, and
  answers `org.a11y.Bus.GetAddress` on the session bus, which is where
  toolkits without the variable below look;
- asks it for the address and sets `AT_SPI_BUS_ADDRESS` to it. GTK 4
  and libatspi (GTK 3's bridge, and every reader) take that over
  anything else, on X11 and Wayland alike, so nobody goes looking for the
  desktop's bus;
- drops `GTK_A11Y` and `NO_AT_BRIDGE`;
- starts a recorder for announcements (below).

The registry, `at-spi2-registryd`, is started by the accessibility bus
when the first program asks for it.

GTK 4, GTK 3 and Qt 5 (tested with VLC) talk to a bus whenever there is
one, and so do programs whose toolkit uses AccessKit (egui, Slint, Bevy,
GTK built with `-Daccesskit=enabled` and run with `GTK_A11Y=accesskit`):
its Unix adapter is an AT-SPI program like any other. It has no cache
object, which libatspi asks every program for and warns of, every read;
`shotbox a11y` leaves that warning out. Its window coordinates on
Wayland count the surface's shadow in, consistently, and are placed
right. Chromium also asks `org.a11y.Status`'s `IsEnabled` whether a screen
reader is running, which here says no (the launcher aborts when told
otherwise in a sealed session), and takes `ACCESSIBILITY_ENABLED=1` for a
yes, which the session sets; some Firefox builds (the Ubuntu PPA's, which
GitHub's runners have) ask too, and take `GNOME_ACCESSIBILITY=1`, which
the session sets as well. That puts its windows on the bus, and no
more: it builds the rest of its tree, a page's too, only once it takes a
screen reader to be at work, which it does when a node's attributes are
asked for, as Orca asks. The announcement recorder (see below)
asks each program's as it joins the session's bus, and `shotbox a11y`
asks a browser window's, the first time a command reads it, and waits
for its page, so no
`--force-renderer-accessibility` is needed: up to 2 seconds for the
page's document, then up to `--timeout` for its tree to be built (the
document is an empty stand-in till then; a page of thousands of
controls takes seconds). A page whose tree isn't built in time is noted
on stderr, and the window `--window` asks for fails. That turns on
Chromium's accessibility (its basic and extended-properties modes) for
the whole browser, from the start in an `--a11y` session, for as long
as it runs, which slows its pages a
little: a measure of a page's performance in the same session counts
that in. It's a little less than the switch turns on (no screen-reader
mode); the trees came out the same in testing. A window with no page
(a crashed tab, or a browser started with
`--disable-platform-accessibility-integration`, which builds no page's
tree for a reader) is noted on stderr, and the rest read; the window
`--window` asks for, with nothing in its tree at all, fails saying so,
rather than give the window alone. `--force-renderer-accessibility`
builds the tree anyway. Electron's programs (VS Code, Slack; not
tested) have windows of another kind: they're asked too, which turns
their accessibility on, but not waited for or noted, so the first read
can come before the page is there. Nothing tells the other programs in
the session a screen reader is running. Playwright, in a session, looks
for its browsers in the scratch home's cache; the session sets
`PLAYWRIGHT_BROWSERS_PATH` to yours ([What's sealed](sealed.md)) so it
finds them. Everything runs in
the launcher's process group, which goes with the session's other groups
at the end.

Turning it on doesn't change the pictures: a libadwaita window comes out
the same bytes with and without it.

## Reading the tree

`shotbox a11y` reads the tree with libatspi (PyGObject's `Atspi`), in its
own process. libatspi finds a bus once and keeps it for the life of the
process, so `Screen.a11y_tree()` and the rest run `shotbox a11y ... --json`
in the session rather than reading from the caller's process, which may
not be inside it, or may have read another session's tree before.

`--window` picks a window by its title, or `app=RE` by its app id. A
window with no accessible name of its own goes by the display's window:
the one `--window` picks there, matched to its program by process, unless
one of that program's named windows is picked. A pattern that matches an
empty name, like `.*`, picks every unnamed window.

The tree leaves out nodes that aren't showing (a hidden widget, what's
in a revealer not revealed, which the toolkit calls visible), and in a
web page those that aren't visible, since a page's nodes scrolled out of
view aren't showing, where a toolkit's still are. GTK 4 says a stack's
pages not shown are showing, so they're in it. GTK 4 before 4.10.3 says
only its windows are showing, so in it the tree takes what's visible,
all it presents, as later ones say it's showing; and before 4.10 it
names a node with no name by its class (`GtkButton`), which the tree
takes as no name. A field with no name of its own goes by what labels it
([Lint](#lint)). It lists the states a screen reader says, not the ones
about layout: focusable, focused, selected, checked, indeterminate,
pressed, expanded or collapsed, editable, read-only, modal, required,
invalid-entry, and disabled for a node that isn't sensitive. Programs
come in the order of their names, their windows and children in the
order they give them, so a tree can be committed and diffed.

A program that doesn't answer (busy, or hung) is a line of its own at
the end, `application "" (pid 1234) didn't answer: busy, or hung`, and
its windows aren't in the tree; it's given `--timeout` (10 seconds, 15
at most) to answer first. Every other `a11y` command, and `wait node`, adds
`; program 1234 didn't answer: busy, or hung` to the error it gives up
with, a second past its `--timeout` (0.8 s more for each other program
that doesn't answer). The bus drops a program that hasn't answered for
about half a minute, after which it's as one with no accessibility.
With `--json`, a window has its program's `pid` and `toolkit`
("GTK 4.14.5"), and a program that didn't answer is a node with its
`pid` and `quiet`.

Role names are at-spi2-core's, and change between its releases: a push
button is `push button` before 2.54 and `button` from it. The lint
compares roles by their enum, not their names, so its rules hold either
way; a committed tree belongs to one at-spi2-core.

## Where a node is

A node's bounds (`--json`, and the Python API's `bounds`) are where it
is on the screen: `[x, y, width, height]`. Toolkits give a node's
extents in its window's coordinates, GTK 4 having no others (its screen
coordinates are all 0), so shotbox puts them on the screen by the
display's window that is the accessible frame's size, and name. (MCP's
tools and the Python API, given a window, give bounds from its corner
instead, as their x,y with one are.) Where
it can't tell, a node's bounds are `null`, and clicking it says so:

- Chromium on Wayland counts its shadow into its accessible frame and
  not its window, by amounts it doesn't say. On X11, its bounds are
  right.
- GTK 4 loses border and padding on the way down, and how depends on
  its version. On X11 with no compositor a window has a border and
  padding of its own (5 pixels, its `solid-csd` style). Before 4.14,
  the frame is what's inside it, so the display's window is that much
  bigger all round, and shotbox places the frame inside it. From 4.14,
  the frame is all of it, and GTK loses it from the window's
  children; shotbox makes it up, telling it from the frame and its first
  child, which fills the frame but for it. Up to 4.14.5, GTK places each
  node at its own content area, past its border and padding: a button by
  about 17 pixels across. Before 4.14 that's right, for what's inside the
  border and padding ([GTK 4's positions](#gtk-4s-positions)).
  From 4.14.6, it places each node relative to its parent's corner,
  short of the parent's border and padding: a button's label at the
  button's corner. Neither can be told from the tree. A GTK with this
  fixed, each node relative to its parent's bounds and a window at
  zero, is placed right.
- A size below zero, or over 32767 (the most an X11 window can be), is
  none a node can be drawn at: GTK 3's -1 for what it doesn't draw, and
  GTK 4's for a widget it hasn't realized (a notebook's page never
  shown), which it sends unset, a different one each run
  (-1346821116 wide).

## Lint

Few rules, each one something a screen reader user would trip over. A
node that takes the focus and has no name of its own is named by what
labels it (a labelled-by relation, as GTK 3 has between a field and its
mnemonic label), as a screen reader says it, in the tree too. A node
that says it has no size (GTK 3's -1 by -1, for a notebook's tab with
its tabs hidden) isn't drawn, and isn't linted:

| A node with no name that's | Why it's a problem |
|---|---|
| a button, toggle button, check box, radio button, menu item (of any kind), link, entry, password entry, spin button, slider, combo box, tab or switch, or an editable text | it says only its role: "button" |
| an image, not inside a control that has a name | it says "image"; a decorative one should be role presentation, which GTK leaves out of the tree, and one inside a named button is that button's |
| anything else that takes the keyboard focus, but for what has a named node inside it, and a container (scroll pane, list, table, tree, grouping, tab list, tool bar...) with something focusable inside it | Tab lands on it and there is nothing to say |

The exceptions in the last row are for GTK 4: its scrolled windows,
lists and notebooks take the focus to pass it on to what's in them, and
a screen reader says a list row by its label. A panel is not a container
here: it's GTK 4's role for a widget of its own, a drawing area say,
which Tab can land on and stay. An editable text with no name is still a
problem inside a scrolled window: it's flagged as itself.

Each problem is a line: the node's path, every step a role and its index
among its siblings, from the window down, then what's wrong. The exit
status is 1 if there are any, and 2, `shotbox: not all checked: program
1234 didn't answer: busy, or hung`, if without `--window` a program
didn't answer. `--ignore RE` (repeatable) leaves out each
line the regex is found in, or would be in with the node's tree line in
place of its path (`button "": button with no name`): an unnamed node
that won't be fixed yet, or a toolkit's own (libadwaita's tab bar
scroller), so the rest can be held to in CI.

### Web nodes

A path into a web page, `.../document web[3]/section[0]/section[2]/static[0]`,
is hard to map back to its source, so a lint or contrast line on a node
in one also says where it is in the page's DOM, after `  <- `, as
Chromium and Firefox give it (their `tag`, `id` and `class` object
attributes): the element's tag, and its id if it has one, then the
node's text (or its name, an `aria-label`'s), cut at 40 characters,
then, if the element has no id, the nearest element round it that has
one:

```
PATH: button with no name  <- button#icon
PATH: text 2.84:1, #999999 on #ffffff (at least 4.5)  <- p "Faint words" in div#intro
```

A text node isn't an element: its element's tag is said. The elements
are those in the browser's tree, not the DOM's every one: Chromium
leaves out a generic element (a `span`, a `div`) with no id, so the
tag, class and nearest id said can be of one round the node's own
(`<div><span class="f">Deep</span></div>` is said `div "Deep"`), and
in a shadow root its `div`s, even with an id, so a node in one is said
in its host's id. An iframe's document ends the climb: a node in one
is said in an id in the iframe's page, or none. Classes are left out
of the line, where they can run long. Firefox's contrast doesn't
measure a paragraph's text, so there a contrast line is a control's, a
link's or a button's. With `--json`, such a node has `dom`, its
element's `tag`, `id` and `class` where it has them, and `in`, those
of the nearest with an id, `{"tag": "p", "in": {"tag": "div", "id":
"intro", "class": "card"}}`, and `text`, cut as the line's is.
`--ignore` is matched against the line with them too: `--ignore
'div#intro'`, `--ignore '#icon'`.

## Walking the Tab order

`tabwalk` notes where the focus is, presses the key (over XTEST, or
sway's virtual keyboard), and waits up to two seconds for the focus to
land somewhere new. GTK says where the focus is only while there's a
keyboard, which on Wayland is the session's: made as it starts and
kept till it stops, so the focus is there to read between commands, and
before any key is pressed, as on X11. If no node has the focus to begin
with, it presses nothing, since it can't tell what a key would do
there, and says so, exit 1: on X11 with no window manager a window can
lack the keyboard (`shotbox focus` gives it). It stops:

- when it lands on a node it has been on: where it began, or any stop
  since, since focus can settle into a round (through a list's rows,
  say) that never passes where it began. A cycle, exit 0, the last node
  printed being the stop it came back to;
- when a press leaves the focus where it was. Focus alone can't tell a
  trap from a window with one thing in it to focus, so it looks: if
  nothing else on screen is sensitive and focusable, but for containers,
  it's a cycle of one, exit 0; otherwise a trap, exit 1. A window whose
  only other focusable nodes are ones Tab can't reach (a header bar's
  buttons, say) counts as a trap too;
- when a press leaves no node with the focus, exit 1;
- after `--max` presses, exit 1.

A text view one writes in (editable, multi-line) takes Tab for itself
and types a tab, as it should: what WCAG asks is that something gets
the focus out. So the walk doesn't press Tab there, which would change
the document: it tries ctrl+Tab, which GTK's text views take to move
on, then in Qt shift+Tab, Qt's (GTK's text views type a tab for it),
Escape (not in a dialog or alert, which it would close, libadwaita's in
its window's frame too), and F6, a pane's key, last, since Kate and
KWrite show a view with it. A web page's `<textarea>` doesn't take
Tab, so in a web page it presses Tab first, and if that moves the
focus the walk goes on as from any other stop; only a web editor that
keeps Tab gets the rest (a browser's ctrl+Tab goes to its next tab,
and Chromium's F6 into its toolbar). If one of those leaves the page
and Tab, going round the browser's controls, never comes back to it,
the walk says so, exit 1: `left the page by F6, and Tab never came
back to it`, and presses Escape, which takes Chromium back to the
page. shift+Tab leaves backward, so the walk then gives the focus to
the first thing after the text view that takes it (AT-SPI's
`grab_focus`, which Qt does) and goes on from there. The first that moves the focus is said on the node, `a
text view: ctrl+Tab leaves it`, and the walk goes on; if none does,
it's a trap, exit 1, `stuck in a text view`. One that changes the
text is said on the node too, and if the text view is gone after
one, the walk stops there, exit 1. An editor's own shortcuts can take
ctrl+Tab: gedit's moves the focus somewhere no node says, and Tab goes
on from there; gnome-text-editor keeps it for all of them. A walk that
begins in a text view nothing leaves gives the focus to the first thing
outside it (AT-SPI's `grab_focus`; GTK 3 and Qt do that, GTK 4 doesn't),
so the rest of the window is walked before the walk comes back to it, a
trap.

Then it lists what Tab never reached: each showing, sensitive control
(those lint holds to a name) or field it never landed on, nor on
anything round it or in it (but a container's, as a window's frame,
or a web page's document, which Chromium's Tab lands on),
nor on another of its group beside it (a tab or radio button, one of
which takes a stop, the rest arrows'), but for a menu bar's (F10's),
`not reached by Tab: button "Play"`; and each table or list with an
item that takes the focus, the focus never in it, once. The last line
counts them: `round again after 6; 3 not reached by Tab`. They don't
fail the walk unless `--reach-all` is given: arrow keys or shortcuts
may reach them, and a window's own buttons (Minimize, Maximize, Close,
in GTK 4's and GTK 3's title bars alike) are out of the Tab order, with
nothing in the tree to tell them from a toolbar's that Tab should
reach (VLC's take no focus at all), so failing by default would fail
every stock GTK window. `--ignore RE` leaves out each one, as lint's
does, whose `PATH: not reached by Tab`, or `NODE: not reached by Tab`
with its tree line, the regex is found in: `--ignore 'button
"(Minimize|Maximize|Close)"'`.

In a browser's window, Tab goes through the browser's own controls
too (the address bar and the buttons by it), and the tab strip's and
Back's are listed as not reached. `--document` lists only what's in
the window's web page (in its `document web`, iframes' included), in
Chromium and Firefox alike: the walk goes through the browser's
controls as before, and their presses count towards `--max`, but they
aren't listed, nor said as not reached, nor held to by `--visible` and
`--reach-all`, and the count on the last line is of the page's stops.
Chromium's Tab lands on the document itself as it comes in from the
browser's controls, as it can on an iframe's; those aren't among the
page's stops. A walk that never gets into the page, though it has
controls, isn't a pass, even without `--reach-all`: `round again,
never in the page; 2 not reached by Tab`, exit 1, as in Firefox on a
page whose controls are all `tabindex="-1"`. Chromium's Tab keeps the
focus on such a page's document, a trap, exit 1, whatever `--ignore`
leaves out; on a page with nothing to focus, that passes. A window
with no web document in it is refused, exit 1: `no web document to
walk: the window isn't a browser's`.

With `--visible`, it also checks that the focus shows. It parks the
pointer, takes a picture of the screen before the walk, and after each
press, once the screen has held still (up to two seconds), another; and
counts the pixels that changed in and round the node's bounds, grown by
4 pixels on each side for a ring drawn outside it. GTK 4 places a node
short of the border and padding of the nodes it's in (above): from
4.14.6 it's drawn right of and below its bounds by as much as their
padding adds up to (a header bar's buttons 7px, a tab bar's tabs 6px and
their close buttons 10px), and before that, left of and above. So when
too little changed there, the box is tried at every offset up to 16
pixels each way, and counts at one where what changed looks like a focus
shown: change along three or four of its sides (a ring, a rail and a
ring, a background) and little just outside them, which a neighbor's
selection has. The ring the focus left isn't counted: where the walk
found it as the focus came, but for what's in this node's own box when
that one holds it (a list's row, after the list). Where the walk began,
it's not known, so it's the outline round the node the focus left that
lost the most, if as much as half a one-pixel outline did and what's
there now looks like what's round it. Two things it can't tell apart
from a focus shown, when the node is drawn off its bounds: something the
node's size changing within 4 pixels of it (a spinner beside a small
button with no ring of its own can pass for its ring), and a tooltip
within 2 pixels of a 1-pixel ring (which can hide it). And a ring drawn
inside a node's bounds, as GTK 3 draws it, counts for a node inside that
one if it goes as the focus comes there (a list's ring inside its edge,
for its first row). Fewer than half a one-pixel outline round the node
(its width and its height) is a focus that doesn't show: a drawing area
that draws no ring, a text view that shows only its caret. WCAG 2.2's
focus appearance asks for a two-pixel outline; this asks for a quarter
of that, since an old GTK 4 places a node past its padding, and part of
its ring outside what's counted. A web document, which takes the focus
as a whole and has nothing drawn round it, and a container, which shows
it on what it passes it to (a notebook, on its tab), aren't checked. It
needs the node's bounds, so what can't be placed isn't checked either,
nor what's outside the screen (a window bigger than it), which says
`focus not measured: outside the screen`.

Nodes are told apart by their program's name on the bus and their
object path, not by their names, so two buttons with the same name are
still two stops (GTK 3 numbers its objects per program, so a path alone
isn't enough).

Each `shotbox a11y` gives up, saying so, if libatspi gets no answer for
a minute (more, for a long walk): it waits on programs inside C, where
nothing else would get to stop it.

## Clicking by name

`click NAME` reads the tree, picks the showing nodes named NAME, and
with the role `--role`, waiting up to `--timeout` for one; drops a match
inside another (a GTK button's label, inside the button), and a label
whose field it names, which matches too (the field takes the focus);
fails if more than one is left, saying which, as tree lines with where
each is; raises the `--window`; and clicks the middle of its bounds,
cut to its window's (a node scrolled out of view is still "showing";
one wholly outside its window, or the screen, is waited for, as below),
over XTEST or sway's virtual pointer, as `shotbox click` does. A real
click, not AT-SPI's `DoAction`: what's clicked is what a pointer would
reach, and a click gives a window the keyboard where no window manager
would. `scroll NAME DY [DX]` finds the node the same way and turns the
wheel at the same place, as `shotbox scroll` does.

Before it clicks, it waits, within the same `--timeout`, until the node
is ready, as Playwright's actionability checks do, and when the time's
up fails saying which it isn't, rather than clicking where nothing
happens: `gave up after 10s: button "Save" [focusable, disabled] isn't
enabled`, `... is moving`, `... is covered by dialog "Preferences"`. It's
ready when it's:

- **placed**: it has bounds, some of them in its window and on the
  screen: `... has no position yet` (a browser puts a node in its tree
  a moment before it places it), `... is outside its window` (scrolled
  out of view), `... is off-screen` (a kiosk browser's own bar, above
  the screen's top);
- **enabled**: sensitive, not greyed out;
- **still**: its bounds the same twice, 50 ms apart, so it isn't
  clicked part way through a move (a list still laying out, a row
  sliding in). That wait is what a click by name costs, when it's ready:
  about 50 ms;
- **uncovered**: in a GTK 4 window, the node GTK says is at the point
  it clicks is it, inside it, or what it's in, not a node drawn over it
  (an overlay, a libadwaita dialog, which is in its window). GTK 4
  answers with what it would hand a click, the topmost; GTK 3 answers
  with the first child there, whatever's drawn over it, so only GTK 4
  is asked. On X11, no window of its own
  dialogs (one transient for its window, which a window manager keeps
  over it) is shown over that point. A click raises the window first,
  which with no window manager puts it over anything else; on Wayland
  sway does the same.

`--force` clicks without waiting for the rest, but still for the node
to show and be placed, since without a place there's nothing to click:
to check that a disabled button does nothing, say.

`--action NAME` does the node's AT-SPI action NAME in place of a click,
for a node a click by name won't reach: a control given no size, which
never gets a position, or a drop-down's option. A closed `<select>`'s
other options aren't in Chromium's tree, and aren't showing in
Firefox's, so open it first, then pick one (Firefox's, once open, are in
a window of their own over the page, which a click takes as covering
them):

```
shotbox a11y click Pick --action open
shotbox a11y click Beta --action select    # Chromium's; Firefox's is click
```

An action's name, and what it does, are the toolkit's, and none is a
click everywhere: Qt's `Toggle` on a list item is a ctrl+click, a GTK
entry's `activate` presses its dialog's default button, a GTK 4
window's actions include `window.close`, and GTK 4's check button has
no action at all, as of 4.24. So a test names the one it means; NAME is
matched as it is, and failing that in any case, if only one matches
so. Without NAME,
`--action` fails listing the node's actions: `button "Unplaced"
[focusable] takes an action by its name; its actions: click (--action
NAME does one)`. Put `--action NAME` after the node's name, or write
`--action=NAME`.

An action needs no place and no raised window, so it waits for
neither, nor for the node to be still or uncovered; it still waits for
it to be enabled, unless `--force`, since an action on a disabled
control does nothing and can answer that it worked, as Firefox's
`press` on a disabled button does. It prints the node and the action:
`button "Send" [focusable], by its action "press"`, and fails if the
program says it refused it, `... refused its action "click"`. A node
without it fails at once, saying what it has: `image "Saved" has no
action`, `label "Cover" has no "click"; its actions: clipboard.copy, ...
(--action NAME does one)`. A click that gives up on a node with no
position lists its actions, `... has no position yet; its actions:
click (--action NAME does one)`, and on a match that isn't showing says
that, `... matches, but isn't showing`, but doesn't do an action
(Python's and MCP's say `action`): a click and an action differ (an
action gives no window the keyboard, and reaches what's covered), and a
test should say which it means.

A name is taken as it is, and failing that as a regex matched in full:
nodes named exactly NAME are picked over those it matches as a regex, so
a calculator's `+`, `.` and `(Draft)` are its buttons, not a regex that
doesn't compile, any one character and `Draft`. That's among the nodes
showing each time it looks: while it waits, a node the regex matches
that shows before one named exactly NAME is taken, so wait for the
window to be up first. A NAME that isn't a regex
is taken only as it is, and when nothing is named that, the error says
it isn't a regex. A role, a `--window`'s title and `wait_focus` take a
name the same way, but don't prefer an exact match (the biggest window
is taken, and the focus is on one node). When nothing matches, the error
lists the nearest names showing.

`shotbox focus RE` gives a window the keyboard and raises it, for one a
pointer can't reach first: on X11 by SetInputFocus and a restack, on
Wayland by sway's `focus`.

## Contrast

`contrast` raises the `--window`, takes a picture of the screen, with
the pointer parked, and measures each node in its bounds, but GTK 4.14
and later's, whose text is measured where it's found
([below](#gtk-4s-positions)):

- **Text**: a node with a name whose role is text (a label, heading,
  link, static text, paragraph), or a button, link, tab, menu item or
  combo box with no such node inside it, or the text a web page's node
  holds of its own (Firefox keeps a page's text in what it's in, a
  paragraph, list item, table cell, link or the page itself; Chromium in
  a static node for each run): run by run, each in one look and on one
  line, between the objects in it (a link, a check box) and emoji and
  other pictographs, which are drawn in their own colors and left out,
  in the box from its first character drawn to its last, or `can't tell
  where it is` where the browser says none. Chromium's character boxes
  slip by one at each soft hyphen or zero-width space, so a run's text
  quoted after it can be a character off ("Extra", a soft hyphen, "o"),
  though its ratio isn't. A browser says where a node
  and its characters are a moment after it's in the tree, so up to a
  second is waited for that. Not a canvas's fallback text, which isn't
  what it draws. The color most of its bounds are is the background, and the
  third pixel in order of contrast against it is the text: an
  antialiased glyph's core reaches its color, though a thin stroke
  seldom draws three pixels of one color. At least 4.5:1 (`--text`).
  Large text, 18pt or 14pt bold, is found by its font, run by run, as
  GTK and Chromium say it, all of it large (GTK 4 says sizes in whole
  pixels, rounded down, so 14pt, 18.67px, said as 18, is taken as up to
  a pixel more), and is held to 3:1 (`--large` sets that ratio). A
  toolkit that doesn't say (Qt) has its text taken as normal size, and
  so does GTK 3's text sized by `<span font="18">`, which it says as
  its label's own size. Fewer
  than 16 pixels that aren't the background have no core of their color
  to measure, in any toolkit: a ".", "-", ":" or ",". That's said, "too
  small to measure", and passes, unless it's GTK 4 text found whose
  color its coverage says (below). Text found counts the pixels its
  shape covers, not a grid or texture drawn behind it.
  Light or small text covers no pixel whole, so its core is lighter than
  its color, by more on one font and renderer than another, and can fail
  though its color passes. It's still measured as drawn, since text
  drawn see-through looks the same; where GTK 3 says the text's color
  and what's drawn is a lighter shade of it, a failure says that color
  too, to tell which (GTK 4 doesn't say). GTK 4.14 and later's text,
  found where it's drawn (below), whose glyphs cover fewer than three
  pixels whole, as stems thinner than a pixel do on any GTK 4 release
  (DejaVu Sans's "l" at 9 to 13px), is measured at the color a pixel
  they covered whole would be, from how much of each they cover: its
  color, or what see-through text makes of it. That's only where the
  shape found accounts for every pixel, to a level and a half: not at a
  size between the thirds of a pixel shapes are drawn at, as
  `font-size: smaller` can make, nor under a shadow, either of which
  would measure darker than its color; those are measured as above.
  GTK 4.16 and later draw text with `gtk-font-rendering = automatic`
  unless told otherwise, which ignores fontconfig's hinting and places
  glyphs at fractions of a pixel: a stem thinner than a pixel covers
  none whole, so small or spaced text is drawn fainter than its color,
  by an amount that differs from one GTK release to the next (small
  monospace set at 6.6:1 measures 4.9 on GTK 4.24; another project's
  heading, set at 6.3:1, 5.7 there and 4.3 on 4.22). Sessions set
  `gtk-font-rendering = manual`, which draws it at its color, so a
  check passes or fails the same on any distribution. That's how the
  text is set, not how GTK draws it on a desktop left at automatic: to
  measure that, seed a home whose `.config/gtk-4.0/settings.ini` sets
  `gtk-font-rendering = automatic` (`--seed`), which a session keeps
  (text found where it's drawn, as above, measures at its color either
  way).
  A control may draw its name or an icon, and GTK 3's and Qt's buttons
  keep their text to themselves, so the tree can't tell which: it's held
  to text's ratio. GTK 4 puts a label in a control that draws text, so a
  GTK 4 control with none draws an icon, and is held to graphics'.
- **Graphics**: a named image: measured the same way, at least 3:1
  (`--graphics`).
- A switch, slider, check box, radio button or spin button draws no
  text, and what WCAG asks of it, its parts against what's round them,
  isn't anything in its own box: it's left out, and so is GTK 3's
  switch, which it calls a toggle button. Its label, if it has one, is
  measured as text.
- A node that can't be placed fails ("not measured"), and so does text
  whose bounds on the screen are one color ("not seen"): text the color
  of its background, and a control, text or icon, of one color. For a
  control, that says how it measures with the pointer over it too,
  since some are drawn only then (a tab's close button). An image of one
  color passes, and a node that says it has no size, which isn't drawn
  ([Lint](#lint)), is left out.
- **Edges**, with `--edges RATIO`: a button, entry, spin button or combo
  box, against what's round it. The color most of a band two pixels
  outside is, against the color, of those making up a fifth or more of a
  band three pixels inside, with the most contrast against it: a border,
  or a filled button's fill. libadwaita's buttons are about 1.2:1, so
  this is for themes that mean to draw an edge. A field (an entry, text
  view or combo box) with no edge of its own passes if what it's in
  draws one, at its bounds or just outside them, and it fills that all
  round, within 16 pixels: a text view in a frame. Not the window's
  edge, and not a box it fills only from side to side: a form's fields
  in a bordered box, or an entry row in a boxed list, look like its
  inside.

A node with something measured inside it (a button's label) is measured
by that. Each node's bounds are cut to its window's and to those of
every node it's inside, so a row scrolled out of view, which GTK still
calls showing, is cut to nothing by its scrolled window, and isn't
measured. Then to the screen: what's outside it (in a window bigger
than the screen) isn't measured either, and says so, `text not
measured: outside the screen`; nor is what the screen cuts and shows
one color of (right-aligned text past its edge), `partly outside the
screen`. Disabled nodes and what's in them (a
disabled button's label) are left out, as WCAG leaves out inactive controls. `--ignore RE`, as
lint has, leaves out each line the regex is found in. The colors are what's on the screen, after opacity and
antialiasing: a check of what someone sees, not of the stylesheet. Text
drawn with subpixel (LCD) antialiasing has every pixel of a thin glyph
tinted, none of its own color, and measures low: Chromium needs
`--disable-lcd-text` (in the Node package's `accessible`), and GTK on
shotbox's displays draws grayscale; Qt's does it too (a Qt label set at
3.00:1 measures 2.75).

What wasn't measured (too small, outside the screen, GTK 4's) is said
after the failures, a line each, and how many and why on stderr, so stdout is only
`PATH: WHAT` lines. `--passes` says what passes, with its ratio, between
the failures and those, to compare one version with another or check
where GTK 4's text was found. It doesn't list an image of one color, or
an edge with nothing round it to measure, which have no ratio. `--json`
gives an object of lists: `problems`, each node with its `problem`;
with `--passes`, `passes`, each with its `contrast`; and `notes`, what
wasn't measured, each with its `note` and, for GTK 4's, its `why`; a
node in a web page with its `dom` and `text`, as lint's
([Web nodes](#web-nodes)).
`--strict` fails on what wasn't measured too, for CI that wants every
node measured or ignored (`--ignore`): those lines are failures, under
`problems` in `--json`, each with its `why` if it has one. On GTK 4.14
and later that's every icon, window control and edge, which aren't
measured, so leave them out with `--ignore` (`graphics not measured`,
`edge not measured`). What's scrolled out of view, or hidden, isn't a
failure: GTK 3 and browsers leave such rows out without a word.
A web page with text, none of it measured (all in fields, say), is a
note on its document, `text not measured: none of the
page's text was`, rather than passing without a word.

Text that fades or blinks measures as it is the moment it's pictured,
and neither its best nor its worst is the answer: the dim end of a fade
may be meant, and the bright end may be all it is for a moment. So the
screen is pictured six times, 0.37 s apart, while measuring goes on:
the first whole, the rest only the part holding what's measured,
which takes a few milliseconds on X11 (where it can't be had that way,
off the screen, the whole screen is, which takes longer, and so do big
parts with grim on Wayland: where a picture takes more than 0.37 s,
the next is taken when it's done, further apart). Each node measured,
or not seen or found, whose pixels changed in any of them is measured
in each: GTK 4's text not found is looked for again. 0.37 s is no
fraction of a common blink's period (0.5, 1, 1.2 or 2 s), so over
1.85 s the pictures fall all round its phase, never more than 0.37 s
apart: a 1 s blink is seen both ways, and a flash of 0.5 s in 2 s is
caught. If its ratio changed and fails at its best, it fails, `text
3.10:1 at best, changing from 1.40:1, #8a8a8a on #ffffff (at least
4.5)`; if it passes at its worst, it passes, `text passes at 5.20:1 at
worst, changing to 9.80:1, …`. Across what it needs, it's a note,
`PATH: text changing: 2.50:1 to 11.24:1 (at least 4.5)`, said apart on
stderr (`1 changing, which doesn't fail.`), and in `--json` among
`notes` with its `why`; with `--strict` it fails, as any note does.
Each has its `range` in `--json`, `[2.5, 11.24]`, and counts as
measured, though the first picture caught it blinked off. A control or
image is judged the same way (`graphics changing: …`). Blinked off,
text is the color of its background, 1:1, but only while its node is
where it was: text that's moved, scrolling by, isn't blank where it
was first seen. A ratio that changes by less than 0.05 is a level's
flicker, and hasn't. A caret blinks only in what's being edited, which
isn't measured. A check with anything to measure takes at least 1.85
s, then: up to that much more where measuring took less, as on a GTK 3
window (2.3 s, not 0.5, for a still one) or a Chromium page (about
1 s); none on a GTK 4 window, which takes longer, but for its text not
found that changed, looked for again in each picture: 10 to 14 s more
for a label the first picture caught blinked off, within the 30 s
contrast looks for text in all.
Chromium's pages opened with the Node package's `accessible` emulate
`prefers-reduced-motion: reduce`, so a page that honors it holds still
([Node](node.md)).

While a modal dialog is open, what's behind it isn't measured: it's
shut off till the dialog closes, and dimmed on purpose or covered, so
its ratio says nothing of how it reads with the dialog closed, where
it's checked. It's said last, `PATH: text behind a modal dialog
"Settings"`, and under `behind` in `--json`, each with its `behind`,
and doesn't fail, with `--strict` either. A modal dialog is one that
says it is: a dialog or alert, on the screen and of some size, in a
window (aria-modal's, which shuts off the rest of its page), and a
modal window, which shuts off its program's other windows; and
libadwaita's, drawn in its window, which always are and don't say so:
a GTK 4 dialog or alert that is itself a layer over the window's
content (as big as it, after something as big, showing and not the
same node listed twice), as libadwaita 1.10 draws them and 1.5 its
alert dialog, or a dialog three levels down in one, as 1.5 draws the
rest; only what comes before it in the tree, drawn under it, is
behind. An alert anywhere else (an error label, a toast, a status
page) isn't one, nor is a box or popover given a dialog's role. A
modal window's program's other windows are all behind it, whether it
covers them or not, and on X11 they aren't dimmed: GTK shuts them all
off, and doesn't say which is its parent. A modal popover dims
nothing, and doesn't count. Nor does an overlay that isn't modal,
opaque or see-through: what's under it can still take the focus, so
text there that can't be read fails. libadwaita's modal bottom sheet
doesn't say it's there, and what's under it is measured, and fails as
dimmed; a program's own see-through layer over its whole window, given
a dialog's or alert's role, can't be told from libadwaita's, and
counts. libadwaita 1.5 names its dialogs nothing, so one is said by
the first name in it: its title, if it shows one.

The exit status is 1 if anything failed, 0 if what was measured passed, and 2 if
nothing was measured at all and GTK 4's was left out: a window on GTK
4.14 or later none of whose text was found, `shotbox: nothing measured:
...` (text too small to measure, alone, doesn't fail). With `--json` it's 0
with failures, which the lines say, and 2 when nothing was measured,
with `--strict` too. It's 2 as well, `shotbox: not all checked`, if
without `--window` a program didn't answer, as for `lint`.
`Screen.contrast()` raises then, and MCP's `check_contrast` says so as
an error.

### GTK 4's positions

GTK 4.14 and later don't say where their widgets are. What they get
wrong:

- a stack's child is placed with its place in the stack counted twice
  (12 pixels right and down for every key in gnome-calculator);
- up to 4.14.5 (Ubuntu 24.04's), a widget is placed where its content
  starts, inside its own border and padding, at its whole size, so its
  box runs past its right and bottom edges by those;
- from 4.14.6, to 4.24 at least, a widget is placed without its
  containers' padding and borders, so it's said to be further left and
  up than it is by all of them (a dialog's button's label by 44 pixels
  across and 22 down).

Measuring at those places measures what's there instead: a button's
fill, a neighbor's text. So only text is measured on them, where it's
found: shotbox draws the node's name in the font GTK says it has, at
each size its whole pixels may be rounded down from, hinted as 4.14
and as 4.16 and later draw, and looks for it where the box GTK says can
be. A stack's page is a node with no actions (not a widget) whose only
child has its extents and is placed from them, which comes off; then up
to 4.14.5 the text is inside the box, and from 4.14.6 inside it moved
right and down by up to the room each container has round what's in
it, since padding and borders can only put it further left and up.
It's taken where it matches what's drawn best (a correlation of 0.98 or
more of the drawn text's coverage with the picture's lightness, with two
blank columns each side, worked out for each size and hinting at every
place in the room, and on 4.16 and later, if none reaches 0.98, for
the best of them a quarter of a pixel up, or a quarter or half down:
those place text at fractions of a pixel, so a line set by its baseline
can fall between rows), and not where anything else in that room
matches 0.8 or more (the same text twice), unless no other node could
have drawn one of the two, and another with this same text could the
other: a node draws its text once, so the first is this one's, and it's
looked for again just there, to fail only (see below). Else either can
be this one's, and taking it that a node draws its text in one of them,
it's measured in both, and fails at the better if it fails in both:
`text 2.50:1 at best, #a0a0a0 on #fafafb (at least 4.5); it matches in
two places and fails in both`; if either would pass, it's a note. A
node could have drawn a place if its room holds it and its text (a label's own as
well as its name) holds that text, or, whatever its text, it has
nothing in it and isn't a label (an entry, a text view, a drawing area,
an image): a label is taken to draw only its text, and a container
nothing but what's in it. Faint text is little of what varies where
it's drawn, and a pattern behind it, the same with the text and
without, can be much of the rest: a theme's grid of
#1e1929 lines every 24 pixels on #0f0d14 is 8% of the variance under
#3a3446 text (1.61:1), which takes its best match to 0.92 to 0.96. So
where the best match falls short of 0.98, it's worked out again just
round there with what's behind taken off: in the text's box, a line
through it, the color its column is for 2 of the 3 pixels both above
and below the box (another line can cross it there), or else its row
both left and right, unless that's the color most of what's round the
box is. That takes those labels to 0.989 and 0.994, and a button's
label over the grid through its fill, 0.972, to 0.994; text over no
pattern is as it was. What else matches, in
the room or over what's found, is the more of with the pattern and
without, so nothing more is taken for another text. Other text can
match nearly as well: "Row 1" over the start of "Row 10" up to 0.98, "on" in
"Button" 0.96, a letter-spaced or upper-case label over an entry
holding its text 0.96. So every other node whose own room takes in
what's found, disabled, left out or not, is drawn there too, by its
name (not a name it takes from its label) and, for an entry, what's in
it or its placeholder: if any matches 0.8 or more, nothing is taken,
unless its text is a shorter piece of this one's, which can't match all
of it ("check" over "unchecked"), and is drawn apart from what's found
too, matching 0.8 or more in its own room: a hidden "Open File" isn't
taken for "Open" and "File" drawn side by side. Nor does text holding
this one's count where it matches 0.8 or more in its own room apart
from what's found, at a place no other node could have drawn (as
above): an entry holding "Neon Doll", whose room takes in a combo row
below with "Neon Doll" selected. Which text is a node's is told by
which nodes could have drawn it, not by its being inside the node's
box: GTK has boxes too far up and left, and one in a box padded above
can be over what's laid on that box's top. What isn't in the tree (a
label with the role presentation), or a container that draws text
round what's in it, can't be counted, so text told apart like this,
either way, is taken only if it fails; if it would pass, it's a note,
as it was.
Text drawn by what has no text of its own (a drawing area, a widget
drawing round its child) can't be, so from 4.14.6 the text GTK lays
out must also be as wide as the name drawn as found, within a pixel
and a fiftieth (GTK says how wide, from 4.22.3 a character short): a
label drawn upper case or letter-spaced isn't, and isn't taken for
its name drawn nearby. From 4.22.3 GTK gives a single character no
width, so a one-character name is looked for only if it has no case (a
digit, a "+"); a letter isn't, and its line says so.
Text that isn't all ASCII ("Über", "€ 5", "…", "÷", "×", an arrow, a
curly quote) isn't measured from 4.14.6, until a GTK that measures its
extents right ships, a line each saying why: GTK measures it short by
however many characters, so it can't be told apart. Up to 4.14.5 GTK
doesn't say, and the text is looked for in the label's own box alone. A label hidden under
something opaque, but drawn in its own font, can still be taken for
the same text drawn visibly in the room round it, or for labels side by
side there that spell it out, a space apart ("Open" and "File" for a
hidden "Open File"), where each is drawn again elsewhere too. The text is measured
in its own box, and a failure says how far its top-left corner is from
where GTK says the text is, which is how far GTK has it wrong (its
containers' padding and borders, right and down, less a stack's place,
counted twice), with `--passes` too: a left-aligned label in a
full-width row, under a theme on 4.24,

```
.../list item[0]/panel[0]/panel[0]/label[0]: text passes at 14.94:1, #ebe6f0 on #16131d (at least 4.5); found 2,2 px from where GTK 4.24.1 says its text is
```

Up to 4.14.5, and for one character from 4.22.3, GTK doesn't say where
its text is, and it's how far from the corner of GTK's box, which takes
in where the text sits in it (centered, or lower in a taller key):
gnome-calculator's "=" on 4.24, white on blue,

```
.../button[23]/label[0]: text 3.76:1, #ffffff on #3584e4 (at least 4.5); found 14,19 px from the corner of the box GTK 4.24.1 says
```

Text all outside its window, or outside a scrolled window it's in,
wherever GTK's box can put it, isn't looked for: a row scrolled out of
view, which GTK still calls showing, `text not measured: not shown
(scrolled out of view, or hidden)`. A row partly in view is looked for,
and so is what's in a popover, which can be drawn outside both.

What isn't found isn't measured, a line each, `text not measured: GTK 4
reports its position wrong`, and why once (in `--json`, each such line's
`why`), unless nothing where it can be could measure as text that
passes: then it fails, `text not found, but nothing where it can be
has the contrast it needs: the best anything there reaches is 1.65:1
(at least 4.5)`, and why once, as for what isn't measured. That's
the contrast of the lightest each channel is anywhere it can be on the
darkest, more than any text there can measure, whatever its background
(and, for text whose shapes cover no pixel whole, as far again as its
most covered pixel is short, as its color is worked out from its
coverage). Only with `--window`, which raises the window, since
another can be over it; and not for text that's in a popover, can be
past the screen's edge or a scrolled window's, is placed even partly
outside its window or what it's in (a carousel's page not shown), or
would be too small to measure. Readable text anywhere it can be
(another node's, say), or anything else with that contrast, keeps it a
note. Text not looked for says why
on its line too: `(a single letter: GTK gives it no width)`, `(over
more than one line)`, `(not all ASCII: GTK measures its width short)`;
and so does text looked for and not taken, with why (the why at the end
says what that means): `(no match good enough: 0.44 at best)`, `(it
matches elsewhere too, in "unchecked")`, naming the node whose room
starts nearest where the other match is, if its text holds this one's
(else how well it matches there, and no name), `(another node's text
matches there too: "Row 10")`, or where that's a node's text, not its
name, its role and name as the tree has them, `the text "Display name",
whose text is "Neon Doll"` (an entry's), `(what matches is drawn wider or narrower
than GTK lays it out)`; and where nothing could be looked for, why:
`(GTK says no font for it)`. What isn't found: text in more than one font (a superscript), letter-spaced (which GTK doesn't say), a
name that isn't what's drawn, text with its own background at its edge
or at the window's, what's drawn outside where GTK's box can be (a
popover's label), and text with more than 600 by 400 pixels to look in.
Looking takes up to 30 seconds in all; text not looked for by then is
`text not measured: no time left to look for it`. Finding it needs
pycairo and PangoCairo (`python3-gi-cairo` and `gir1.2-pango-1.0` on
Debian, which the Action's `a11y: true` installs, and `shotbox doctor`
checks): without them, no text is found, and the why says so. Images,
controls that draw an icon, and edges (`--edges`) aren't measured
either, a line each: nothing says where they are. Thin text found is
measured from how much of each pixel its glyphs cover (as above), at
its color; at GTK's own bounds, with `SHOTBOX_TRUST_GTK4_BOUNDS=1`,
it's measured as drawn, and can measure lighter: `#b0b0b0` text as
`#c1c1c0`.

On gnome-calculator, gnome-text-editor, Files and test windows (borders
and padding nested, stacks, libadwaita's view stack, keys in a grid,
twins and decoys), on GTK 4.24.1 (X11, Wayland and Xwayland) and
4.14.5, every text found was where GTK draws it, to the pixel, against
its true place read in the program, and no text was taken for another.
GTK 3, Qt and browsers say where their widgets are, and are measured.

Before 4.14, GTK 4 says where a widget's content is: inside its own
border and padding, placed right. On GTK 4.6.9 (Ubuntu 22.04) and 4.8.3
(Debian 12), each node's bounds were its content box to the pixel, in
nested boxes with borders and padding, a stack and a button inside it,
and in gtk4-widget-factory, gtk4-demo, gnome-text-editor and Debian's
gnome-calculator. So it's measured: a label's text is inside its
content, on what's round it, and a button's label or icon on the
button's fill (Debian 12's gnome-calculator "=", white on orange, fails
at 2.64:1). Its edges aren't (`--edges`), being outside that, a line
each, as for later GTK 4. Nor, a line each:

- what's in a popover, which GTK places as if the popover were in its
  window, not where it's drawn (a popover's label 34 pixels above it,
  over the button that opened it);
- a stack's page no longer shown, still said to be where it was: a node
  on a stack's page where GTK, asked what's at its middle, says another
  node of that stack (or one it's in), `not measured: GTK says another
  node is at its middle`. A shown page's own child that can't be
  clicked (`can_target` off) comes out the same.

Close popovers before measuring on these versions: what's under an
open one is measured against the popover's pixels, since GTK doesn't
say where the popover really is.

`SHOTBOX_TRUST_GTK4_BOUNDS=1` in the session's environment (`--env`)
measures GTK 4 at its bounds as said, edges and all, rather than
looking for its text: for a GTK whose bounds you've checked, which
needs the stack's and the padding's fixed (one with the padding alone
fixed still has gnome-calculator's keypad 12 pixels off). libadwaita's
view stack and tab view pages take their bounds from their child, as
GTK's stack pages do.

## What a screen reader says

The tree says what a screen reader finds, not what it makes of it: a
field's mnemonic, the label it's named by, a description, a state, all
come out as the reader puts them, and a node can have a name and still
say nothing. `shotbox a11y speak` asks a real one, Orca:

```
$ shotbox run --a11y -- sh -c 'test/a11y_app.py form &
    shotbox a11y speak --window a11y-test'
(start) button "Save" [focusable, focused]  says "Screen reader on.", "a11y-test", "Save button"
button "" [focusable, focused]  says "button"
button "Announce" [focusable, focused]  says "Announce button"
check box "Wrap lines" [focusable, focused, checked]  says "Wrap lines check box checked"
text "File name" [focusable, focused, editable]  says "File name text", "opens popup"
panel "" [focusable, focused]  says nothing
text "Name:" [focusable, focused, editable]  says "Name: text", "N opens popup"
button "Save" [focusable, focused]  says "Save button"
```

First, `(start)`: what has the focus as Orca starts, and what it says
then (a dialog's title and text, say). Then a line a press: the node
with the focus, as the tree has it, then each thing Orca said (a line
break in one as `\n`), or `says nothing` (the drawing area above, which
a person tabbing through hears nothing of); `(no node has the focus)`
where none has it, as on X11 with no window manager until the window is
given the keyboard (`shotbox focus`); `(the focus didn't move)` where
the press left it where it was. A press is done once the focus has left
where it was (or after two seconds, if it doesn't), and then once
Orca's log has been still for half a second, so a program slow to move
the focus is still heard.

Without `--keys` it presses Tab until the focus lands where it has
been, at most `--max` times, as `tabwalk` does, but never in a text view
one writes in, which would take the tab as typing: it stops before one,
saying which key leaves it (`ctrl+Tab`, or in Qt `shift+Tab`), and
nothing is typed. `--keys Tab Tab space alt+n` presses those, in turn,
a line each, whatever the focus does: what Orca says as a check box is
toggled, or a mnemonic used. `--json` gives `start` and each of
`presses` as `key`, `focus` (a node, as `tree --json` has it, or null),
`moved` and `speech` (a list), and `text_view`, if Tab stopped at one,
the key that leaves it. From Python,
`Screen.speak()`; from MCP, `get_speech`.

It needs Orca (`orca` on Debian and Fedora), which takes a few seconds
to start, and is stopped, with what it started, once the keys are
pressed. Orca is run so it stays in the session:

- with a home of its own in the session's scratch dir
  (`XDG_CONFIG_HOME` and `XDG_DATA_HOME`), so it starts from its
  defaults and never reads or writes your settings, and its GSettings
  in a keyfile there;
- with no speech or braille: speech-dispatcher's, Spiel's and BrlAPI's
  Python modules are taken out as it starts, so it says nothing out
  loud nor on a braille display, and its debug log has what it would
  have said, which is what's read;
- with no key echo, which would say each key before what it moved the
  focus to;
- past its check for another Orca: it won't start while any of your
  user's runs, your desktop's own included, so the `pgrep` it asks
  finds none. `--replace`, which kills every Orca of yours, isn't
  passed;
- named `shotbox-orca`, not `orca`, so your desktop's Orca, started by
  hand or by GNOME's `orca --replace`, doesn't find it in turn and
  refuse to start, or kill it: it's run by its interpreter
  (`python3 /usr/bin/orca`) and names itself through a stand-in for
  `setproctitle`.

All but the `pgrep` are a `sitecustomize.py` on Orca's `PYTHONPATH`, so
an Orca whose interpreter is told to leave those out (`-I`, `-E`, `-s`
or `-S` in its `#!` line) isn't started: it's refused, saying why.

What's read of Orca's debug log is its `SPEECH OUTPUT: 'TEXT'` entries and
nothing else, as the log isn't an interface and changes between
releases. Checked with Orca 51.1 (Debian's), and 50.3, 48.0 and 46.1
(Ubuntu's packages, run from the package's files): they say the same
things, but for a period at the end before 51 (`"Save button."`), a
mnemonic said (`"N opens popup"`) from 50, and a checked box said
checked twice by 46 here (not with Ubuntu 24.04's own libraries, as CI
runs it). A role is said as at-spi2-core names it, as the tree shows it:
before 2.54 (Ubuntu 24.04's 2.52), a button is a `push button`
(`"Save push button."`). Checked on X11 and Wayland, with GTK 4's
windows.

## Announcements

GTK 4.14's `gtk_accessible_announce()` emits AT-SPI's `Announcement`
signal (interface `org.a11y.atspi.Event.Object`, arguments: the
politeness, 1 polite or 2 assertive, and the text). GTK sends it whether
or not a reader has registered to hear it, so the recorder is a match
rule on the accessibility bus. It writes each one to
`$SHOTBOX_SCRATCH/announcements`, or `--announce-log FILE` (which
implies `--a11y`), as
`polite: TEXT`, a line break in it as `\n`, and the session waits for
it to be listening before anything runs. `$SHOTBOX_ANNOUNCEMENTS` says
where the file is.

`wait announce` takes the first match that no wait has taken yet (it
notes the lines taken in `$SHOTBOX_SCRATCH/announcements-taken`, under a
lock), so an announcement that came before the wait began is still
found, the same thing said twice is waited for twice, and waits can be
in any order.

A browser's live regions are recorded too: Chromium sends
`object:text-changed:insert` for text put in one, with `container-live`
(`polite` or `assertive`) among the node's attributes. Unlike an
announcement, it sends the event only once a listener has registered
for it, which the recorder does, every program's text insertions then
coming to it, and only once it takes a screen reader to be at work,
which the recorder has it do as it joins the bus, so nothing need read
its tree first. What's recorded is the text inserted: a region whose text
is replaced sends all of the new text, and an element put in one sends
its own. Text outside a live region, and a busy region's, isn't
recorded. GTK has no live regions.

What Orca says as it appears is recorded too: a node whose role is
alert, notification or info bar saying it's showing
(`object:state-changed:showing`), as GTK 3's message dialogs and
`GtkInfoBar`, and Qt's `QMessageBox`, do; or the focus going into one,
as into libadwaita's `AdwAlertDialog`, which GTK 4 doesn't say is
showing. That's once while the focus stays in it, and again each time it
comes back, as Orca reads it; and a window that comes up is looked in
for the focus, as Orca does, since a program just started can say where
it is before what's there can be asked about. Before libadwaita 1.6
(Ubuntu 24.04's 1.5), an alert dialog is a dialog with no name, of
which Orca 46 says only "dialog", and isn't recorded. It's written with
its name and the names of the labels in it, but not its buttons':
`assertive: Warning Delete notes.md? It can't be undone.`, and
`polite:` for a notification. Orca says less, and not
always: it leaves out a label of under three words, or one that takes
the focus, as Qt's do (51 says that `QMessageBox` as `alert Warning`);
it reads a dialog only once it's the active window; and 51 loses a GTK 3
info bar to its filter of events that come too close together (46 says
it). An info bar revealed (`gtk_info_bar_set_revealed()`) rather than
shown says nothing of showing, and is heard by neither. A browser's
`role="alert"` is a live region, recorded as one, and an alert that's
announced something, or whose parent has, in the 10 seconds before isn't
written: libadwaita (from 1.6) announces a toast (`A toast appeared:
Saved`) from the parent of its node, an alert.

And the focus renamed, as a button that says Copied once it's clicked:
it's written `polite: Copied`, where Orca says it. As Orca 51 decides,
that's not a name much like the last one said (a similarity of 0.85 or
more, so not a button counting down, `Resend in 9s` to `8s`), nor a list
item's that holds a progress bar; and as 46 does, not a combo box's,
whose name is what's picked in it, or a table cell's. A value changed (a
slider moved) isn't: Orca says it, but it's what the keys just did, not
something the program said.

## Display presets

What each preset sets, and what honors it. libadwaita takes its style
from the portal or GSettings, neither of which a sealed session has, so
it's told with its debug variables; GTK reads `settings.ini` from the
scratch home (merged into a seeded one's `[Settings]`, the rest of it,
comments too, as it was). A seed's `settings.ini`, or a directory on the
way to it, that links out of the seed (stow's dotfiles) is replaced by
the session's own copy first, so nothing outside is written.

GTK 4 before 4.20 warns of every `settings.ini` key it doesn't know, in
every program (fatal under `G_DEBUG=fatal-warnings`), so shotbox asks the
system's `libgtk-4.so.1` its version, in a process of its own, and
writes only keys that version knows.

| Preset | libadwaita | GTK 4 | GTK 3 |
|---|---|---|---|
| `--dark` | `ADW_DEBUG_COLOR_SCHEME=prefer-dark` | `gtk-interface-color-scheme = dark` (4.20+), `gtk-application-prefer-dark-theme` (older) | `gtk-application-prefer-dark-theme` |
| `--high-contrast` | `ADW_DEBUG_HIGH_CONTRAST=1` | `gtk-interface-contrast = more` (4.20+), nothing (older) | `gtk-theme-name = HighContrast` |
| `--text-scale N` | as GTK 4 | X11: `Xft.dpi` in the X server's resources; Wayland: `gtk-xft-dpi` | X11 only: `Xft.dpi` |

On X11 (and Xwayland), GTK takes the font DPI from the X server's
`RESOURCE_MANAGER` over `settings.ini`, so `--text-scale` sets
`Xft.dpi` there, at 96 times N. GTK 3 on Wayland reads text scaling from
GSettings alone, which a sealed session keeps in a keyfile of its own
with no text scaling in it unless a seed's keyfile has some, so it
doesn't scale.

GTK 3's dark variant is its theme's: Ubuntu's GTK 3 defaults to Yaru,
and where Yaru isn't installed (a CI image), it falls back to Adwaita
without the dark variant, so `--dark` leaves GTK 3 light. `--env GTK_THEME=Adwaita:dark`
or a seeded `gtk-theme-name = Adwaita` gets it back.

`--env GTK_THEME=...` wins over GTK 3's `--high-contrast`, which is a
theme name: GTK reads the variable over its settings, so a session with
a `GTK_THEME` of its own isn't high contrast in GTK 3.

Reduced motion has no preset: every session already sets
`gtk-enable-animations = false`.
