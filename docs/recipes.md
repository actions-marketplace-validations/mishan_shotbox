# Recipes

Worked examples, each run on Debian with the programs named. Where a
recipe needs a program of your own, it's `./my-app`, a window called
`My App`, or `./game`, whose window is `Game`.

## Checking a change in a GUI

Build what's committed beside your change, a `git worktree` of `main`
say, and take the same picture of each:

```
shotbox shoot before.png --window 'My App' -- ../app-main/my-app
shotbox shoot after.png --window 'My App' -- ./my-app
shotbox compare before.png after.png --diff diff.png
```

`compare` says how many pixels changed and the box they're in
(`23829 pixels differ, in 200x120 at 0,0`), and `diff.png` marks them in
red: a change that reaches further than it should shows there. Pictures
of different sizes aren't compared; it says both sizes.

Then the looks a user may have, each off unless asked for:

```
for look in dark high-contrast; do
  shotbox shoot "after-$look.png" --$look --window 'My App' -- ./my-app
done
shotbox shoot after-big.png --text-scale 1.5 --window 'My App' -- ./my-app
```

To click through it rather than look at one screen, keep a session and
work it by name, from the accessible tree:

```
shotbox start --name ed --a11y
shotbox spawn --session ed -- gnome-text-editor
shotbox --session ed wait window '.*Text Editor'
shotbox --session ed a11y tree --window '.*Text Editor' | grep button
shotbox --session ed a11y click 'Main Menu'
shotbox --session ed wait stable
shotbox --session ed capture menu.png
shotbox stop ed
```

## Visual regression in CI

A script takes every picture the project keeps, into a directory it's
given. `--clock` and `--identity` keep the date and who took it out of
them, so a picture changes when the program's look does, and not
because a day went by or someone else shot it:

```sh
#!/bin/sh -e
# tools/shoot.sh DIR: the pictures the docs show, taken into DIR
dir=${1:-docs/pictures}
mkdir -p "$dir"
set -- --clock 2026-03-02T09:00:00 --identity dev@example
shotbox shoot "$dir/month.png" "$@" --window '.*Calendar.*' --wait stable -- gnome-calendar
shotbox shoot "$dir/month-dark.png" "$@" --dark --window '.*Calendar.*' --wait stable -- gnome-calendar
shotbox term --shoot "$dir/files.png" "$@" --size 60x5 --when 'example' -- \
  sh -c 'cd && touch -d 2026-03-01T17:00 notes.md && ls -l && hostname'
```

Without `--clock`, the calendar marks today, and the month it opens on
is this one; without `--identity`, `ls -l` and `hostname` say yours. File
times are what's on disk, so the script sets the one it shows.

A new picture, or one that's been flaky, is worth taking `--twice`, to
check it comes out the same: two fresh sessions, failing where they
differ, with `OUT-diff.png` saying where.

```
shotbox shoot /tmp/month.png --twice --clock 2026-03-02T09:00:00 \
  --identity dev@example --window '.*Calendar.*' --wait stable -- gnome-calendar
```

In CI, take them again and compare:

```yaml
name: pictures
on: [push, pull_request]
jobs:
  pictures:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: mishan/shotbox@v0.10.0
        with:
          term: true
          clock: true
      - run: sudo apt-get install -y -qq gnome-calendar
      - run: tools/shoot.sh fresh
      - run: mkdir -p docs/pictures && shotbox compare docs/pictures fresh --diff fresh
      - if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: pictures
          path: fresh
```

A picture belongs to the machine it was taken on: its fonts, its GTK, its
theme. Commit the runner's, not your desktop's: the first run fails
(`no pictures in docs/pictures`), and the `pictures` artifact it uploads
is what to commit. When a change is meant to change a picture, commit
the new one from that run's artifact the same way; each failing one has
`NAME-diff.png` beside it there, which isn't to be committed, or the next
run fails on it as a picture it didn't take. A picture that differs by a few pixels
of dithering or antialiasing every run can be given room by name,
`--fuzz month=1%` or `--max files=20`, leaving the rest exact.

In a container (a job's `container:`), which holds back the user
namespaces bubblewrap makes its sandbox in, as Docker's default profile
does, `--identity`, `--net` and `--gamepads` can't be had. Sessions run
without a sandbox there, and say so only where it would have hidden
something, a sound device or a runtime dir mounted in, say;
`SHOTBOX_SANDBOX: off` in the job's `env` says that's meant ([What's
sealed](sealed.md)).

## An accessibility audit

What a screen reader hears, what can't be read, and where Tab goes:

```sh
#!/bin/sh
# audit.sh: shotbox run --a11y -- ./audit.sh
w='Calculator'
gnome-calculator 2> /dev/null &
shotbox wait window "$w"
shotbox a11y lint --window "$w"; lint=$?
shotbox a11y contrast --window "$w"; contrast=$?
shotbox a11y tabwalk --visible --window "$w"; tabs=$?
[ $lint = 0 ] && [ $contrast = 0 ] && [ $tabs = 0 ]
```

Each check runs whether the last failed or not, so one run says
everything. On GNOME Calculator 51, `lint` finds a list and two text
fields with no name, `contrast` text it measures under 4.5:1, and
`tabwalk --visible` a field whose focus doesn't show. Where a check is
wrong about one node, or it can't be fixed yet, `--ignore RE` leaves its
line out. What each check measures, and what it can't tell:
[accessibility.md](accessibility.md).

The looks a user may pick change contrast too. From Python, each in a
session of its own:

```python
import shotbox

for preset in ({}, {"dark": True}, {"high_contrast": True}, {"text_scale": 1.5}):
    with shotbox.Session(a11y=True, **preset) as s:
        s.spawn(["gnome-calculator"], log="calculator.log")
        s.wait_window("Calculator")
        for node in s.contrast(window="Calculator")["problems"]:
            print(preset, node.name or node.role, node.problem)
```

And to see it as someone with other color vision does:
`shotbox shoot calc-gray.png --vision gray --window Calculator --
gnome-calculator`.

## A web app in Firefox

A page served from the session, in a Firefox of its own, pictured once
it says it's drawn. `site/` holds the app; its page sets a status line,
`<p role="status">`, to `Drawn` once it is:

```sh
#!/bin/sh -e
# web.sh: shotbox run --net loopback --a11y --screen 1600x1000 -- ./web.sh
python3 -m http.server 8000 --bind 127.0.0.1 --directory site 2>/dev/null &
shotbox wait port 8000
profile=$HOME/firefox
mkdir -p "$profile"
echo 'user_pref("datareporting.policy.firstRunURL", "");' > "$profile/user.js"
firefox --kiosk --new-instance --profile "$profile" --width 1600 --height 1000 \
  http://127.0.0.1:8000/ 2>/dev/null &
shotbox wait window '.*Mozilla Firefox'
shotbox wait node '^Drawn$' --window '.*Mozilla Firefox' --timeout 60
shotbox capture app.png
```

`--net loopback` gives the session a network of its own: the server
and the browser reach each other on `127.0.0.1`, and nothing else does.
`wait port` looks for the server there. The profile is made in the
session's home, new each run and gone with the session, so nothing an
earlier run left in it, a cached page or tabs to restore, changes
what's pictured. One made outside the session works too, since the
sandbox shows the machine's files, hiding only the desktop's
`/run/user/UID`: a profile there isn't found, and Firefox opens a
"Profile Missing" dialog in place of the page.

A new profile's first run opens a second tab, Firefox's privacy notice
from www.mozilla.org, which on the session's network doesn't load: out
of sight in kiosk mode, but there in the accessible tree as a page tab
"Problem loading page", with its page and its "Try Again" button.
`user.js` turns it off.

In a lasting session, worked from outside:

```
shotbox start --name web --net loopback --a11y --screen 1600x1000
shotbox spawn --session web -- python3 -m http.server 8000 --bind 127.0.0.1 --directory site
shotbox --session web wait port 8000
shotbox spawn --session web -- sh -c 'p=$HOME/firefox; mkdir -p "$p"
  echo "user_pref(\"datareporting.policy.firstRunURL\", \"\");" > "$p/user.js"
  exec firefox --kiosk --new-instance --profile "$p" --width 1600 --height 1000 http://127.0.0.1:8000/'
shotbox --session web wait node '^Drawn$' --window '.*Mozilla Firefox' --timeout 60
shotbox --session web capture app.png
shotbox stop web
```

With no window manager, `--kiosk` doesn't fill the screen: the window
is 1280x900, the rest of a 1600x1000 screen black. `--width` and
`--height` set to `--screen`'s size have it fill it.

`wait stable` passes once the screen holds still, which a page can do
while it's still loading: a WebGL canvas waiting seconds on its server.
What says it's done is the page, so wait for the page to say it. `wait
node` waits for a node in the accessible tree whose name or text
matches, here the status line's text; `wait announce '^Drawn$'` does
too, since a `role="status"` region is a live one, whose text is
announced as it changes. Either needs `--a11y`.

A page that takes long to draw a frame takes input late, or not at
all: with software GL at about a second a frame, the first drag was
dropped each time, and the same key pressed again moved the view by
different amounts, while `wait stable` passed on the frozen screen.
Drive what it shows from its state where it can be (the URL's hash,
say) rather than by input, or wait for it to say it's drawn before
sending more. Run with Firefox 158.

## A game smoke test

Start it, press start, walk, jump, and check it got each and played
sound without dropping out. A game that logs what happens is waited on
by its log, not by a clock it may not keep to. This one writes `ready`,
`level 1`, `walk right` and `jump` to the file it's given:

```sh
#!/bin/sh -e
# smoke.sh: shotbox run --gamepads 1 --audio -- ./smoke.sh
rm -f game.log
./game --log game.log &
shotbox wait line ready game.log --from-start
shotbox wait window Game
shotbox record play.wav --seconds 5 & rec=$!

shotbox pad press start
shotbox wait line 'level 1' game.log --from-start
shotbox pad stick left 1 0 --hold 1
shotbox wait line 'walk right' game.log --from-start
shotbox pad press a
shotbox wait line jump game.log --from-start
shotbox capture play.png --window Game

wait $rec
shotbox listen play.wav
```

`wait line` counts only lines written after it starts, unless
`--from-start`; a game can log a line before a wait started after the
press does, so each wait here reads the whole log, which is new each run
and has each line once. `listen` fails if the sound dropped out, clipped
or was silent:

```
5.000s, 48000 Hz, 2 channels: peak -12.2 dBFS, rms -17.5 dBFS, 0 clipped
```

The pad is a virtual Xbox 360 controller, which SDL names that; your
desktop's controllers aren't in the session. `--gamepads` needs write
access to `/dev/uinput`; see [Gamepads](gamepads.md).

## An agent over MCP

The programs the agent may start, a line each, in a file in the
project:

```
# shotbox-programs
editor = gnome-text-editor
calendar = gnome-calendar
```

```
claude mcp add shotbox -- shotbox mcp --allow ~/src/app/shotbox-programs
```

Then ask it for what you'd check yourself: "Start a session with the
clock at 2026-03-02 09:00, open the calendar, open its main menu, show
me, and lint it." It calls:

- `start_session` with `clock: "2026-03-02T09:00:00"`;
- `spawn_program` with `program: "calendar"`, and `wait_for` the window;
- `click_element` with `name: "Main Menu"`, then `wait_for` `stable`;
- `take_screenshot` of the window, which comes back as an image;
- `check_accessible_names`, which finds the menu's items have no names;
- `stop_session`.

The tree (`get_accessible_tree`) is the cheap way for it to look, and a
screenshot is for what only pixels say. Each tool and what it takes:
[mcp.md](mcp.md).
