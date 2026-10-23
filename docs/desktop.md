# Differences from a desktop

A session is built to come out the same every time, which makes it differ
from the desktop a program usually runs on. Most of the time that doesn't
matter; when a picture, a timing or a check disagrees with what you see on
your own screen, this is the list to check.

## Drawing

GTK draws with its software renderer (`GSK_RENDERER=cairo`, and
`GDK_DISABLE=gl`), not with GL or Vulkan. Antialiasing, gradients,
shadows and glyph edges come out differently from a desktop's, by a few
levels here and there, and drawing costs the CPU what a GPU would have
done, which `latency` measures. Pictures are the same from run to run and
machine to machine; they aren't byte for byte what your desktop shows.

## No window manager on X11

The default X11 session runs Xvfb and nothing else: no window manager and
no compositor. So:

- GTK 4 draws its windows' frames itself with no shadow, as a desktop
  without a compositor would.
- Nothing gives a new window the keyboard or raises it; `shotbox focus`
  and clicking a window do. A dialog's modality isn't enforced either.
- Windows come up where they ask to, usually at 0,0.

`--wayland` runs sway, headless, set to float every window at 0,0 at the
size it asks for, as on X11: it isn't GNOME's Mutter or KDE's KWin. See
[Wayland](wayland.md).

## The machine's devices

A session has a stand-in system bus and no printers, so a desktop's
settings, run with `--desktop`, find no networks, Bluetooth devices or
user accounts (GNOME Settings says NetworkManager isn't running), and a
print dialog no printers. `--machine-bus` gives it the machine's, which
show then: check pictures before sharing them. See
[What's sealed](sealed.md).

## Animations

GTK's animations are off (`gtk-enable-animations = false`, and
`enable-animations` false in GSettings, where GTK 3 on Wayland reads
it), so pictures don't catch a transition halfway. That hides a bug in
an animation, and a program can't be tested for honoring reduced
motion, since nothing in a session asks for it; there's no option yet
to turn them back on.

## Blinking carets

A text caret doesn't blink where one setting reaches it, and toolkits
read it from different places:

| Program | X11 (and Xwayland) | Wayland |
|---|---|---|
| GTK 3, and VTE terminals left to GTK's blink | `settings.ini` | GSettings |
| GTK 4 | `settings.ini` | `settings.ini` |
| Chromium, Chrome, Firefox | GTK's | not checked |
| Qt 6.5 or later | XSETTINGS | blinks |
| Qt 5, Qt 6 before 6.5 | blinks | blinks |

So a session's `settings.ini` says `gtk-cursor-blink = false`; its
GSettings, a keyfile in the scratch home (`GSETTINGS_BACKEND=keyfile`)
over the system's defaults, merged into a seed's own, say `cursor-blink`
false; and on X11 it holds the XSETTINGS selection for
as long as it runs, saying `Net/CursorBlink` 0 and nothing else, so no
other XSETTINGS manager (xsettingsd, say) can take it. `--desktop` uses
the desktop's own GSettings and XSETTINGS manager, so it leaves
blinking to the desktop. Qt 5, and Qt 6 before 6.5, take no setting for
it: Qt 5's GTK platform theme takes GTK's blink time, not whether to
blink. Nor is there one for a caret on a timer of its own, a game's, or
a terminal's with a setting of its own (xterm's `cursorBlink`, off
unless set). `shoot --twice` finds what's left. A seeded home whose GTK
3 `settings.ini` turns blinking on, or leaves it out (GTK's default is
on), gets it in all of them.

## Language and direction

`LANG=C.UTF-8` and `TZ=UTC`: English messages, left to right. A program
in another language takes `--env LANG=de_DE.UTF-8` (and `LANGUAGE`), if
that locale is installed on the machine; a right-to-left one
(`--env LANG=ar_EG.UTF-8`, `he_IL.UTF-8`) draws right to left, which is
how to test it. `--tz` and `--clock` set the time zone and the clock.

## Scale

There's no HiDPI scaling: no `GDK_SCALE`, and sway's output at scale 1.
`--text-scale N` scales text, as the desktop's text scaling does, not
the whole interface; GTK 3 and 4 follow it (see
[Display presets](accessibility.md#display-presets)), and Qt on X11 likely does
too, through the same `Xft.dpi`, though that's untested.

## Input

- A US keyboard layout. `type` types ASCII only; a character with no key
  fails, after what came before it is typed.
- No input method: dead keys and composing, and CJK input, can't be
  tested.
- No keypad or media keys among the keys `shotbox key` can press.
- The pointer warps to where it's sent; `drag` moves in 10 steps. There's
  no touch, tablet or touchpad gesture, and `scroll` sends wheel clicks,
  not smooth scrolling.

## Measurements

- `latency` times a pointer move to the first changed picture. It leaves
  out what a desktop adds: the input stack, a compositor, vsync and the
  display. Compare it against a baseline on the same machine; don't read
  it as the input latency a person feels.
- `flicker` sees what the program draws. On X11 with no compositor that
  includes flashes as a window is exposed that a compositor would hide,
  and it can't see a compositor's own damage or buffer bugs on Wayland.
- Sound dropouts come from a null sink on PipeWire's dummy driver: they
  catch a program that stops feeding its sound (a busy main thread, a
  pause), not a real device's underruns.
