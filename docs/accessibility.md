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
- **`shotbox a11y tabwalk [--key Tab] [--max 50] [--window RE]`** presses
  the key until the focus lands somewhere it has been, printing where it
  lands each time, and fails on a trap: [Walking the Tab
  order](#walking-the-tab-order).
- **`shotbox a11y click NAME [--role RE] [--window RE]`** clicks the
  node whose name matches NAME (a regex, matched in full): a button by
  its label rather than where it is. [Clicking by name](#clicking-by-name).
- **`shotbox a11y contrast [--window RE]`** measures the contrast of
  what each node draws against its background, and fails, a line each:
  `PATH: text 2.51:1, #a0a0a0 on #fafafb (at least 4.5)`.
  [Contrast](#contrast).
- **`shotbox wait announce RE`** waits for a program to announce
  something matching RE (found anywhere in it) to a screen reader:
  GTK 4.14's `gtk_accessible_announce()`, or text put in a browser's
  live region (`aria-live`, `role="status"`). [Announcements](#announcements).

```
shotbox run --a11y -- sh -c 'gnome-text-editor &
  shotbox a11y lint --window ".*Text Editor"'
```

finds that the editor's text area has no name.

`--window` picks a window by title, or `app=RE`, as everywhere, and
waits up to `--timeout` (10s) for it to appear in the tree. Without it,
every program's windows are there. It works on X11, Wayland and
Xwayland, for GTK 4, GTK 3, Qt 5 and AccessKit's toolkits (egui, Slint,
Bevy, GTK built with it), and for Chromium, headed, given
`--force-renderer-accessibility` (the Node package's `accessible`), on
X11.

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
yes, which the session sets; it still needs `--force-renderer-accessibility`
for a page's part of the tree, and is tested headed. Everything runs in
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

The tree leaves out nodes that aren't visible (a hidden widget, a stack
page not shown), and lists the states a screen reader says, not the ones
about layout: focusable, focused, selected, checked, indeterminate,
pressed, expanded or collapsed, editable, read-only, modal, required,
invalid-entry, and disabled for a node that isn't sensitive. Programs
come in the order of their names, their windows and children in the
order they give them, so a tree can be committed and diffed.

Role names are at-spi2-core's, and change between its releases: a push
button is `push button` before 2.54 and `button` from it. The lint
compares roles by their enum, not their names, so its rules hold either
way; a committed tree belongs to one at-spi2-core.

## Where a node is

A node's bounds (`--json`, and the Python API's `bounds`) are where it
is on the screen: `[x, y, width, height]`. Toolkits give a node's
extents in its window's coordinates, GTK 4 having no others (its screen
coordinates are all 0), so shotbox puts them on the screen by the
display's window that is the accessible frame's size, and name. Where
it can't tell, a node's bounds are `null`, and clicking it says so:

- Chromium on Wayland counts its shadow into its accessible frame and
  not its window, by amounts it doesn't say. On X11, its bounds are
  right.
- GTK 4 loses border and padding on the way down, and how depends on
  its version. On X11 with no compositor a window has a border and
  padding of its own (5 pixels, its `solid-csd` style), and every
  version loses that from the window's children; shotbox makes it up,
  telling it from the frame and its first child, which fills the frame
  but for it. Up to 4.14.5, GTK places each node at its own content
  area, past its border and padding: a button by about 17 pixels across.
  From 4.14.6, it places each node relative to its parent's corner,
  short of the parent's border and padding: a button's label at the
  button's corner. Neither can be told from the tree. A GTK with this
  fixed, each node relative to its parent's bounds and a window at
  zero, is placed right.

## Lint

Few rules, each one something a screen reader user would trip over:

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
status is 1 if there are any. `--ignore RE` (repeatable) leaves out each line the
regex is found in: an unnamed node that won't be fixed yet, or a
toolkit's own (libadwaita's tab bar scroller), so the rest can be held
to in CI.

## Walking the Tab order

`tabwalk` notes where the focus is, presses the key (over XTEST, or
sway's virtual keyboard), and waits up to two seconds for the focus to
land somewhere new. It stops:

- when it lands on a node it has been on: where it began, or any stop
  since, since focus can settle into a round (through a list's rows,
  say) that never passes where it began. A cycle, exit 0, the last line
  printed being the stop it came back to;
- when a press leaves the focus where it was. Focus alone can't tell a
  trap from a window with one thing in it to focus, so it looks: if
  nothing else on screen is sensitive and focusable, but for containers,
  it's a cycle of one, exit 0; otherwise a trap, exit 1. A text view that
  takes Tab for itself is one; `--key ctrl+Tab` is how GTK gets out of
  it. A window whose only other focusable nodes are ones Tab can't reach
  (a header bar's buttons, say) counts as a trap too;
- after `--max` presses, exit 1.

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
needs the node's bounds, so what can't be placed isn't checked either.

Nodes are told apart by their program's name on the bus and their
object path, not by their names, so two buttons with the same name are
still two stops (GTK 3 numbers its objects per program, so a path alone
isn't enough).

Each `shotbox a11y` gives up, saying so, if libatspi gets no answer for
a minute (more, for a long walk): it waits on programs inside C, where
nothing else would get to stop it.

## Clicking by name

`click NAME` reads the tree, picks the showing nodes whose name matches
NAME, and `--role` too, waiting up to `--timeout` for one; drops a match
inside another (a GTK button's label, inside the button), fails if more
than one is left, saying which; raises the `--window`; and clicks the
middle of its bounds, cut to its window's (a node scrolled out of view
is still "showing"; one wholly outside its window is refused), over
XTEST or sway's virtual pointer, as `shotbox click` does. A real
click, not AT-SPI's `DoAction`: what's clicked is what a pointer would
reach, and a click gives a window the keyboard where no window manager
would.

`shotbox focus RE` gives a window the keyboard and raises it, for one a
pointer can't reach first: on X11 by SetInputFocus and a restack, on
Wayland by sway's `focus`.

## Contrast

`contrast` raises the `--window`, takes a picture of the screen, with
the pointer parked, and measures each node in its bounds:

- **Text**: a node with a name whose role is text (a label, heading,
  link, static text, paragraph), or a button, link, tab, menu item or
  combo box with no such node inside it: the color most of its bounds are is the background, and the color
  with the most contrast against it, of at least three pixels, is the
  text, which an antialiased glyph's core is. At least 4.5:1 (`--text`).
  Light or small text covers no pixel whole, so its core is lighter than
  its color, by more on one font and renderer than another, and can fail
  though its color passes. It's still measured as drawn, since text
  drawn see-through looks the same; where GTK 3 says the text's color
  and what's drawn is a lighter shade of it, a failure says that color
  too, to tell which (GTK 4 doesn't say).
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
  `gtk-font-rendering = automatic` (`--seed`), which a session keeps.
  A control may draw its name or an icon, and GTK 3's and Qt's buttons
  keep their text to themselves, so the tree can't tell which: it's held
  to text's ratio. GTK 4 puts a label in a control that draws text, so a
  GTK 4 control with none draws an icon, and is held to graphics'. Text is taken as normal size, so large text, which WCAG holds
  to 3:1, has to be given its own run.
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
  color passes.
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
measured. Disabled nodes are left out, as WCAG leaves out inactive
controls. `--ignore RE`, as lint has, leaves out each line the regex is
found in. The colors are what's on the screen, after opacity and
antialiasing: a check of what someone sees, not of the stylesheet. Text
drawn with subpixel (LCD) antialiasing has every pixel of a thin glyph
tinted, none of its own color, and measures low: Chromium needs
`--disable-lcd-text` (in the Node package's `accessible`), and GTK on
shotbox's displays draws grayscale. Where
a node's bounds are off (GTK 4's padding, above), what's measured is
what's there instead; a child's bounds are cut to its parent's, which
keeps that inside what it's in.

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
coming to it. What's recorded is the text inserted: a region whose text
is replaced sends all of the new text, and an element put in one sends
its own. Text outside a live region, and a busy region's, isn't
recorded. GTK has no live regions.

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
GSettings alone, which a sealed session keeps in memory, so it doesn't
scale.

GTK 3's dark variant is its theme's: Ubuntu's GTK 3 defaults to Yaru,
and where Yaru isn't installed (a CI image), it falls back to Adwaita
without the dark variant, so `--dark` leaves GTK 3 light. `--env GTK_THEME=Adwaita:dark`
or a seeded `gtk-theme-name = Adwaita` gets it back.

`--env GTK_THEME=...` wins over GTK 3's `--high-contrast`, which is a
theme name: GTK reads the variable over its settings, so a session with
a `GTK_THEME` of its own isn't high contrast in GTK 3.

Reduced motion has no preset: every session already sets
`gtk-enable-animations = false`.
