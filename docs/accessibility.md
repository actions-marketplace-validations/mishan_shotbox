# Accessibility

A sealed session has no accessibility bus: `GTK_A11Y=none` and
`NO_AT_BRIDGE=1` tell GTK 4 and GTK 3 not to look for one, so nothing a
program does reaches your desktop's screen reader. `--a11y` gives the
session a bus of its own instead, so what a screen reader would hear can
be read, linted and walked, headless, in CI.

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

This is for GTK. GTK 4 and GTK 3 talk to a bus whenever there is one;
Qt and Chromium also ask `org.a11y.Status`'s `IsEnabled` whether a screen
reader is running, which here says no (the launcher aborts when told
otherwise in a sealed session), so they need their own switches:
`QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`, `--force-renderer-accessibility`.
Neither is tested. Everything runs in the launcher's
process group, which goes with the session's other groups at the end.

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
button is `push button` before 2.62 and `button` from it. The lint
compares roles by their enum, not their names, so its rules hold either
way; a committed tree belongs to one at-spi2-core.

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
status is 1 if there are any.

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

Nodes are told apart by their program's name on the bus and their
object path, not by their names, so two buttons with the same name are
still two stops (GTK 3 numbers its objects per program, so a path alone
isn't enough).

Each `shotbox a11y` gives up, saying so, if libatspi gets no answer for
a minute (more, for a long walk): it waits on programs inside C, where
nothing else would get to stop it.

## Announcements

GTK 4.14's `gtk_accessible_announce()` emits AT-SPI's `Announcement`
signal (interface `org.a11y.atspi.Event.Object`, arguments: the
politeness, 1 polite or 2 assertive, and the text). GTK sends it whether
or not a reader has registered to hear it, so the recorder is a match
rule on the accessibility bus. It writes each one to
`$SHOTBOX_SCRATCH/announcements`, or `--announce-log FILE`, as
`polite: TEXT`, a line break in it as `\n`, and the session waits for
it to be listening before anything runs. `$SHOTBOX_ANNOUNCEMENTS` says
where the file is.

`wait announce` takes the first match that no wait has taken yet (it
notes the lines taken in `$SHOTBOX_SCRATCH/announcements-taken`, under a
lock), so an announcement that came before the wait began is still
found, the same thing said twice is waited for twice, and waits can be
in any order.

GTK has no live regions, so there is nothing else of GTK's to record;
another toolkit's live-region text changes aren't recorded.

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

Reduced motion has no preset: every session already sets
`gtk-enable-animations = false`.
