# What's sealed

A session keeps a program from reaching your desktop by accident: from
opening a window on it, playing through your speakers, or picking up your
settings. It isn't a security boundary against a program that sets out to.

Sealed:
- The environment, rebuilt from an allowlist: no `DISPLAY`,
  `WAYLAND_DISPLAY`, session bus, SSH agent or anything else of yours but
  what `--pass` lets through (a socket it names in `/run/user/UID` isn't
  there under bubblewrap). `HOME` and every XDG directory are a scratch
  dir.
- A display, a session bus (nothing activatable on it, so no gvfs, dconf
  or portals start behind your back), an accessibility bus with `--a11y`,
  and a sound server with `--audio` (its sink plays to nowhere), all the
  session's own. Settings are in-memory, so apps come up with their own
  defaults, not yours.
- Under bubblewrap, which every program the session spawns runs in where it
  can: `/dev/snd` is empty, so nothing plays to the machine's sound
  devices, and `/run/user/UID` is (and `XDG_RUNTIME_DIR`, if that's
  elsewhere), so your desktop's Wayland, PulseAudio, PipeWire and bus
  sockets aren't there to find, and a program can't be run from inside
  it. With `--gamepads`, only the session's pads (see [Gamepads](gamepads.md)).

Not sealed:
- The files: the filesystem is the machine's, readable and writable as
  you, outside the scratch home.
- The network, unless `--net loopback`: a server bound to `0.0.0.0` is
  on your network, and a program reaches whatever you can. With `--net
  loopback`, the session has a network of its own, which every program
  it runs shares and nothing else is on: a dev server and the browser
  testing it reach each other on `127.0.0.1`, and nothing outside, nor
  anything outside them. `wait port` looks there, from inside the
  session or out. It needs bubblewrap, and `nsenter`, and fails without;
  and not `--no-sandbox`, since setuid helpers can't work in it either.
- Who and where you are, unless `--identity USER@HOST`: a shell's
  prompt, `ls -l` and a title bar show your user and host name. With
  it, programs see USER and HOST in: `hostname`, `hostname -f`, `uname
  -n` and `gethostname()` (each program in a UTS namespace of its own,
  all with the one name), `/etc/hostname` and `/etc/hosts`; `whoami`,
  `id -un`, `getpwuid()` and `ls -l`'s owner (an `/etc/passwd` and
  `/etc/group` of the session's, the machine's with your uid and gid
  called USER, the scratch home theirs, and your name in no group); and
  `$USER`, `$LOGNAME` and `$HOSTNAME`. `who` and `users` find no one.
  Your paths still show: the directory a program starts in, and `PATH`,
  are yours, so a prompt that shows where it is wants a `cd` into the
  session's home first. USER can't be a user or group the machine has
  already. It needs bubblewrap, and fails without, rather than take a
  picture with your names in it; and not `--no-sandbox`.
  `Session(identity="USER@HOST")` from Python, and `start_session`'s
  `identity` over MCP.
- The wall clock, unless `--clock TIME`: what it moves and what it
  doesn't, [The clock](commands.md#the-clock).
- The machine's system bus, unless `--system-bus`: logind,
  NetworkManager and the rest, which may let a program in your desktop's
  session suspend or power off the machine.
- Your X server: its sockets, in `/tmp/.X11-unix` and the abstract one,
  are reachable by a program that sets `DISPLAY` and finds your cookie
  itself.
- Other processes: a program can see them, and signal yours.
- What the session starts for itself, the display server, the buses and
  what they activate, and PipeWire, runs outside bubblewrap; and so does
  what a program in a Wayland session has sway start for it.

Without bubblewrap, or where it can't make a sandbox (user namespaces held
back, as Ubuntu's AppArmor and Docker's default profile do), the session
says so and runs programs without one: they can reach the sound devices
and your desktop's sockets. Where there's nothing for it to hide, as in a
container (no sound, input, raw HID or USB devices or uinput in `/dev`,
and nothing in `/run/user/UID` or `XDG_RUNTIME_DIR`), it doesn't say so;
`shotbox doctor` still says why there's no sandbox. `SHOTBOX_SANDBOX=off`
in the environment is `--no-sandbox` for every session, from Python and
MCP too, and for one started inside a session: for a Dockerfile or a CI
job to set once. `false` and `0` are off too (YAML makes an unquoted
`off` false), and `on`, `true` and `1` on.
With a `TMPDIR` in `/run/user/UID`, which the session's scratch dir is
made in, your desktop's sockets there stay reachable; it says that too.
`--gamepads` needs it, and fails instead, and has it with `--no-sandbox`
too; and since `/dev/uinput` can't be opened under it, a session with
gamepads can't be started from inside another. Under bubblewrap a program can't
gain privileges (no setuid helpers: sudo, `fusermount`, so no AppImages),
its supplementary groups show as `nogroup`, and one killed by a signal
exits with 128 plus the signal's number; `--no-sandbox` runs programs
without it, for one that needs those. The pid `spawn` reports is the
program's own.
