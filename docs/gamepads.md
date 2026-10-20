# Gamepads

`--gamepads N` gives the session N virtual Xbox 360 controllers (1 to 4),
made with Linux's uinput, so a game reads them as it reads a pad plugged
in: SDL names them "Xbox 360 Controller", and older games find them as
`/dev/input/jsN` too. Every program the session spawns (with `run`,
`shoot`, `term`, `spawn` and MCP's `spawn_program`) runs under bubblewrap with a
`/dev/input` of its own, holding only these. The machine's raw HID devices
(`/dev/hidraw*`, `/dev/usb/hiddev*`, `/dev/uhid`) and `/dev/uinput` are a
file no one can open inside, and `/dev/bus/usb` is empty, so no controller
of the machine's reaches a program by another way (SDL's HIDAPI drivers,
hidapi, libusb); the lists in `/sys` and `/run/udev` still name the
machine's devices, but none can be opened. That's settled as each program
starts: one plugged in while a program runs can reach it. What the session starts for
itself, the display server and what D-Bus activates, isn't sandboxed (on
Wayland, a program can have sway start one outside it), so this keeps a
game from finding your controllers, not one that sets out to; and
your desktop can see the virtual pads while the session runs (named
"shotbox virtual pad N"); they go when it stops, however it stops. They're
real devices on the machine, so what reads controllers on your desktop
meanwhile, Steam or a game, can act on what's pressed on them (Steam may
open its overlay for `guide`): close it, or use another machine. It needs
write access to `/dev/uinput` (which many desktops give the logged-in user,
and the `input` group has) and `bwrap` with user namespaces, which the
session checks for at the start.

Inside, `shotbox pad` works them. What's left down stays down between
commands, on X11 and Wayland alike, since a server in the session holds
the pads, and through taps and holds of the same button; `--up` lets go
of it, holds and all (a hold whose command was killed, say):

```
shotbox pad press a                     # tap A
shotbox pad press lb rb --hold 0.5      # together, for half a second
shotbox pad press b --down              # left down, until --up
shotbox pad stick left 1 0 --hold 1.5   # walk right for a second and a half
shotbox pad stick right -1 0            # aim left, and leave it there
shotbox pad --pad 2 trigger right 1     # the second pad's right trigger, all the way
shotbox pad neutral                     # let go of everything
```

Buttons are `a b x y lb rb back start guide ls rs` and the d-pad's `up
down left right`. A stick's x and y go from -1 to 1, with y down; a
trigger from 0 to 1.
