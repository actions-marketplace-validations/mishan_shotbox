#!/bin/sh
# shotbox's own tests: the sealed session, the terminal, the pictures, and
# the Node helpers. Needs what shotbox needs: Xvfb, xauth, dbus-daemon,
# ImageMagick, and PyGObject with VTE for the terminal.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
sb=$here/../bin/shotbox
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fails=0
ok() { echo "ok    $1"; }
fail() { echo "FAIL  $1"; fails=$((fails + 1)); }

# One version: the Python package's, the Node package's and its lockfile's,
# and the changelog's newest release.
root=$here/..
py=$(python3 -c "import sys; sys.path.insert(0, '$root'); import shotbox; print(shotbox.__version__)")
js=$(node -p "require('$root/package.json').version")
lock=$(node -p "require('$root/package-lock.json').version")
log=$(grep -m1 -o '^## [0-9][0-9.]*' "$root/CHANGELOG.md" | cut -c4-)
[ "$py" = "$js" ] && [ "$js" = "$lock" ] && [ "$lock" = "$log" ] \
  && ok "one version, $py, everywhere" || fail "versions: python $py, npm $js, lock $lock, changelog $log"

# The session: none of ours gets in, and it cleans up after itself.
out=$(DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 SSH_AUTH_SOCK=/nope SECRET=x \
  "$sb" run -- sh -c 'echo "D=$DISPLAY W=${WAYLAND_DISPLAY-} S=${SSH_AUTH_SOCK-} X=${SECRET-} H=$HOME"')
case $out in
  *"W= S= X= H=/tmp/shotbox-"*) ok "the environment is rebuilt, not inherited" ;;
  *) fail "the environment leaked: $out" ;;
esac
case $out in D=:0*) fail "DISPLAY is ours" ;; *) ok "a display of its own" ;; esac
scratch=$("$sb" run -- sh -c 'echo $SHOTBOX_SCRATCH')
[ ! -e "$scratch" ] && ok "the scratch dir is removed" || fail "left $scratch behind"
set +e; "$sb" run -- sh -c 'exit 7'; st=$?; set -e
[ $st = 7 ] && ok "run exits with the command's status" || fail "run exited $st"
# A program that's gone, leaving a child behind: the child goes too.
"$sb" run -- sh -c "sleep 300 & echo \$! > '$tmp/orphan'"
pid=$(cat "$tmp/orphan")
kill -0 "$pid" 2>/dev/null && { fail "a child left behind outlived the session"; kill "$pid"; } \
  || ok "what a program leaves behind stops with the session"
names=$("$sb" run -- gdbus call --session --dest org.freedesktop.DBus \
  --object-path / --method org.freedesktop.DBus.ListActivatableNames)
[ "$names" = "(['org.freedesktop.DBus'],)" ] && ok "nothing activatable on the bus" \
  || fail "activatable: $names"
SHOTBOX_TEST=yes "$sb" run --pass SHOTBOX_TEST --env A=b -- \
  sh -c '[ "$A" = b ] && [ "$SHOTBOX_TEST" = yes ]' \
  && ok "--env sets one, --pass lets one through" || fail "--env and --pass"
# A session that lasts: started, used by later commands, stopped; what was
# spawned in it stops with it, and its scratch dir goes.
kept=keep$$
"$sb" start --name "$kept" >/dev/null && "$sb" sessions | grep -qx "$kept" \
  && ok "start: a session that lasts, listed" || fail "start"
"$sb" spawn --session "$kept" -- "$here/window.py" kept >/dev/null \
  && "$sb" --session "$kept" wait window kept --timeout 10 >/dev/null \
  && "$sb" --session "$kept" capture "$tmp/kept.png" --window kept \
  && [ "$(identify -format %wx%h "$tmp/kept.png")" = 200x120 ] \
  && ok "spawn, and --session: a program in it, waited for and taken" || fail "spawn, --session"
# A program with no display to lose: only the session stopping it stops it.
"$sb" spawn --session "$kept" --log "$tmp/kept.log" -- \
  sh -c 'echo "$SHOTBOX_SCRATCH"; exec sleep 600' "marker$$" >/dev/null
"$sb" --session "$kept" wait file "$tmp/kept.log" --timeout 10 || true
scratch=$(head -1 "$tmp/kept.log")
[ -n "$scratch" ] && ok "spawn --log: its output, there" || fail "spawn --log"
set +e
"$sb" spawn --session "$kept" --log /nonexistent/kept.log -- true 2> "$tmp/err"; st=$?
set -e
[ $st != 0 ] && "$sb" sessions | grep -qx "$kept" \
  && ok "spawn that can't: says why, and the session carries on" \
  || { fail "spawn that can't ($st)"; cat "$tmp/err"; }
"$sb" stop "$kept" && ! "$sb" sessions | grep -qx "$kept" && [ ! -e "$scratch" ] \
  && ok "stop: the session goes, and its scratch dir" || fail "stop: left $scratch"
pgrep -f "[m]arker$$" >/dev/null && fail "stop: what was spawned in it was left running" \
  || ok "stop: what was spawned in it goes too"
set +e
"$sb" --session "$kept" park 2> "$tmp/err"; st=$?
set -e
[ $st != 0 ] && grep -q "no session called '$kept'" "$tmp/err" \
  && ok "--session: a session that isn't there, said" || fail "--session, gone ($st)"
# Two started at once with one name: one starts, the other says so.
"$sb" start --name "$kept" > "$tmp/k1" 2>&1 & k1=$!
"$sb" start --name "$kept" > "$tmp/k2" 2>&1 & k2=$!
set +e; wait $k1; s1=$?; wait $k2; s2=$?; set -e
"$sb" stop "$kept" 2>/dev/null || true
[ $((s1 + s2)) = 1 ] && grep -q "already" "$tmp/k1" "$tmp/k2" \
  && ! pgrep -f "[s]tart --serve $kept" >/dev/null \
  && ok "start: two with one name, one starts" || fail "start, raced: $s1 $s2"
# A record left by a session that died, its pid since someone else's.
sleep 600 & other=$!
d=${XDG_RUNTIME_DIR:+$XDG_RUNTIME_DIR/shotbox}; d=${d:-/tmp/shotbox-$(id -u)}
printf '{"pid": %s, "started": 1, "env": {}, "control": "/nonexistent"}' $other > "$d/$kept.json"
"$sb" stop "$kept" 2>/dev/null || true
kill -0 $other 2>/dev/null && [ ! -e "$d/$kept.json" ] \
  && ok "stop: a session that's gone, forgotten, and its pid left alone" \
  || fail "stop: killed $other, someone else's"
kill $other 2>/dev/null || true
# What a --desktop session's bus activates gets the session's environment,
# and its own service files, not the caller's.
mkdir -p "$tmp/seed/.local/share/dbus-1/services"
printf '[D-BUS Service]\nName=org.shotbox.Probe\nExec=/bin/sh -c '"'"'echo "$HOME $DISPLAY" > %s'"'"'\n' \
  "$tmp/activated" > "$tmp/seed/.local/share/dbus-1/services/org.shotbox.Probe.service"
"$sb" run --desktop --seed "$tmp/seed" -- sh -c 'gdbus call --session --dest org.shotbox.Probe \
  --object-path / --method org.shotbox.Probe.Nothing >/dev/null 2>&1
  [ "$(cat '"$tmp/activated"')" = "$HOME $DISPLAY" ]' \
  && ok "--desktop: what the bus activates is inside the session" \
  || fail "--desktop: activated with: $(cat "$tmp/activated" 2>/dev/null)"
# Audio: PipeWire with a null sink, or skip, unless SHOTBOX_AUDIO=required.
if command -v pipewire >/dev/null && command -v wireplumber >/dev/null && command -v pactl >/dev/null; then
  python3 -c "import wave; w = wave.open('$tmp/beep.wav', 'wb'); w.setnchannels(1)
w.setsampwidth(2); w.setframerate(48000); w.writeframes(bytes(9600)); w.close()"
  mkdir -p "$tmp/audio"
  TMPDIR=$tmp/audio "$sb" run --audio -- sh -c "pactl info | grep -qx 'Default Sink: shotbox-sink' &&
    timeout 10 paplay '$tmp/beep.wav'" \
    && ok "--audio: a sink of its own, which plays" || fail "--audio"
  left=$(for p in $(pgrep -u "$(id -u)" -x 'pipewire|wireplumber'); do
    { tr '\0' '\n' < "/proc/$p/environ"; } 2>/dev/null | grep -q "^XDG_RUNTIME_DIR=$tmp/audio/" && echo "$p"
  done; true)
  [ -z "$left" ] && ok "--audio: PipeWire and WirePlumber stop with the session" \
    || fail "--audio: left running: $left"
  "$sb" run -- sh -c '! pactl info >/dev/null 2>&1' && ok "no --audio, no sound server" \
    || fail "a sound server without --audio"
else
  [ "${SHOTBOX_AUDIO-}" = required ] && fail "audio skipped: no PipeWire, WirePlumber or pactl" \
    || echo "skip  audio: no PipeWire, WirePlumber or pactl"
fi

# The terminal: steps, the ready file, the log, and the same picture twice.
cat > "$tmp/scheme.json" <<'JSON'
{"foreground": "#ebe6f0", "background": "#0f0d14", "cursor": "#ff2d95",
 "palette": ["#2a2438", "#ff7b72", "#7ee787", "#e5c07b", "#8ca3ff", "#b48cff",
             "#7ed9e7", "#c4bcce", "#9c93ab", "#f7a6a4", "#aae7b1", "#e7cfaa",
             "#b2bef9", "#cab0f9", "#aadeeb", "#ebe6f0"]}
JSON
shot() {
  "$sb" shoot "$1" --window shotbox-term --wait ready -- \
    "$sb" term --scheme "$tmp/scheme.json" --size 40x6 --log "$tmp/pty.log" \
      --when 'name\?' --type 'doll\r' --when 'hi, doll' -- \
      bash --norc -c 'printf "\033[35mname?\033[0m "; read n; echo "hi, $n"; sleep 60' >/dev/null
}
shot "$tmp/a.png" && ok "shoot waits for the steps and takes the window" || fail "shoot"
shot "$tmp/b.png"
[ "$(identify -format %wx%h "$tmp/a.png")" != "" ] && cmp -s "$tmp/a.png" "$tmp/b.png" \
  && ok "two runs, the same bytes" || fail "two runs differ"
grep -q "$(printf '\033')\[35mname?" "$tmp/pty.log" && ok "the log keeps the escape codes" \
  || fail "no escape codes in the log"
set +e
"$sb" shoot "$tmp/c.png" --window shotbox-term --wait ready --timeout 3 -- \
  "$sb" term --timeout 1 --when 'never' -- sh -c 'echo nope; sleep 60' 2> "$tmp/err"
st=$?; set -e
[ $st != 0 ] && grep -q "exited\|gave up" "$tmp/err" && ok "a step that never comes fails, saying why" \
  || fail "a stuck step: status $st"
[ -s "$tmp/c-failed.png" ] && grep -q "c-failed.png" "$tmp/err" \
  && ok "a failed shoot leaves a picture of the screen" || fail "no -failed.png"

# term --shoot, --crop in cells, and a program that's done before the picture.
"$sb" term --shoot "$tmp/full.png" --size 40x10 --when there -- sh -c 'echo hi; echo there' >/dev/null \
  && ok "term --shoot, and the terminal outlives its program" || fail "term --shoot"
"$sb" term --shoot "$tmp/cells.png" --size 40x10 --crop 12x3 --when there -- \
  sh -c 'echo hi; echo there' >/dev/null
full=$(identify -format %wx%h "$tmp/full.png"); cells=$(identify -format %wx%h "$tmp/cells.png")
[ "${cells%x*}" -lt $((${full%x*} / 3 + 2)) ] && [ "${cells#*x}" -lt $((${full#*x} / 3 + 2)) ] \
  && ok "term --crop takes the top-left cells ($cells of $full)" || fail "term --crop: $cells of $full"
set +e; "$sb" term --env A=b -- true 2>/dev/null; st=$?; set -e
[ $st = 2 ] && ok "term takes session options only with --shoot" || fail "term --env: status $st"

# A window by its app id: WM_CLASS, instance or class.
"$sb" shoot "$tmp/app1.png" --window app=window.py -- "$here/window.py" t1 >/dev/null
"$sb" shoot "$tmp/app2.png" --window app=Window.py -- "$here/window.py" t1 >/dev/null
[ "$(identify -format %wx%h "$tmp/app1.png")" = 200x120 ] && cmp -s "$tmp/app1.png" "$tmp/app2.png" \
  && ok "a window by its app id" || fail "app="
set +e; "$sb" shoot "$tmp/app3.png" --window app=nope --timeout 1 -- "$here/window.py" t1 2> "$tmp/err"
st=$?; set -e
[ $st != 0 ] && grep -q "window with app id 'nope'" "$tmp/err" && ok "app=: a miss says what it looked for" \
  || fail "app= miss: $st"

# Stable: waits out a burst of output, and gives up on output that never stops.
"$sb" run -- sh -c "
  '$sb' term --size 30x5 --log '$tmp/burst.log' -- \
    bash --norc -c 'for i in 1 2 3 4 5 6 7 8; do echo \$i; sleep 0.2; done; echo over; sleep 60' &
  '$sb' wait window shotbox-term && '$sb' wait stable --window shotbox-term" \
  && grep -aq '^over' "$tmp/burst.log" && ok "stable waits for the screen to stop changing" \
  || fail "stable came early"
set +e
"$sb" run -- sh -c "
  '$sb' term --size 30x5 -- bash --norc -c 'while :; do date +%N; sleep 0.1; done' &
  '$sb' wait window shotbox-term && '$sb' wait stable --timeout 2 --failed '$tmp/busy.png'" 2> "$tmp/err"
st=$?; set -e
[ $st != 0 ] && grep -q "hold still" "$tmp/err" && [ -s "$tmp/busy.png" ] \
  && ok "stable gives up on a busy screen, with a picture of it" || fail "busy: status $st"

# chords LOG [SESSION OPTIONS]: Shift in a chord, into cat in a terminal:
# the symbol on the key's shifted level, as on a keyboard (A, !, +), and
# Shift+Tab as the terminal's back-tab, ESC [ Z.
chords() {
  log=$1; shift
  "$sb" run "$@" -- sh -c "
    '$sb' term --size 40x6 --log '$log' -- sh -c cat & t=\$!
    '$sb' wait window shotbox-term && '$sb' wait stable --window shotbox-term &&
    '$sb' click 20 20 --window shotbox-term &&
    '$sb' key shift+a shift+1 plus shift+Tab Return
    st=\$?; sleep 0.5; kill \$t 2>/dev/null; exit \$st" \
    && grep -aq 'A!+' "$log" && grep -aq "$(printf '\033')\[Z" "$log"
}

# Input: a click for focus, typed text with shifted symbols, and chords.
"$sb" run -- sh -c "
  '$sb' term --size 40x6 --log '$tmp/input.log' -- sh -c cat & t=\$!
  '$sb' wait window shotbox-term && sleep 0.5 &&
  '$sb' click 20 20 --window shotbox-term &&
  '$sb' type 'Hi, you! 1+1=2 ~/a_b <x>' && '$sb' key Return ctrl+d
  st=\$?; sleep 0.5; kill \$t 2>/dev/null; exit \$st" \
  && grep -aq 'Hi, you! 1+1=2 ~/a_b <x>' "$tmp/input.log" \
  && ok "click, type and key reach the program" || fail "input"
chords "$tmp/chords.log" && ok "shift in a chord: the shifted symbol, and back-tab" \
  || fail "shift chords"
set +e; "$sb" key ctrl+comma 2>/dev/null; st=$?; set -e
[ $st != 0 ] && ok "input needs a session" || fail "key ran outside a session"

# The Python API, and parking the pointer.
set +e; python3 "$here/api.py" "$sb" > "$tmp/api.log" 2>&1; st=$?; set -e
cat "$tmp/api.log"
fails=$((fails + $(grep -c '^FAIL' "$tmp/api.log" || true)))
[ $st = 0 ] || grep -q "^FAIL" "$tmp/api.log" || fail "api.py: status $st"

# What tabwalk --visible counts as the focus shown, on made-up pictures.
set +e; python3 "$here/focus_test.py" > "$tmp/focus.log" 2>&1; st=$?; set -e
cat "$tmp/focus.log"
fails=$((fails + $(grep -c '^FAIL' "$tmp/focus.log" || true)))
[ $st = 0 ] || grep -q "^FAIL" "$tmp/focus.log" || fail "focus_test.py: status $st"

# Accessibility: a session's own AT-SPI bus, the tree, lint, the Tab
# order and announcements, against a libadwaita window. They need GTK 4,
# libadwaita and at-spi2-core, or they skip, unless SHOTBOX_A11Y=required.
app=$here/a11y_app.py
if python3 -c 'import gi; gi.require_version("Adw", "1"); gi.require_version("Atspi", "2.0")' 2>/dev/null \
    && { [ -x /usr/libexec/at-spi-bus-launcher ] || [ -x /usr/lib/at-spi2-core/at-spi-bus-launcher ]; }; then
  # Its sessions' scratch dirs under one of ours, to tell what they leave
  # running from the desktop's own accessibility bus: the launcher, its
  # bus and the registry have the session's runtime dir, not in their
  # command lines but in their environments.
  a11y=$tmp/a11y
  mkdir -p "$a11y"
  export TMPDIR=$a11y
  ours_a11y() {
    for p in $(pgrep -u "$(id -u)" .); do
      { tr '\0' '\n' < "/proc/$p/environ"; } 2>/dev/null | grep -q "^XDG_RUNTIME_DIR=$a11y/" && echo "$p"
    done
    true
  }
  a11y_tree() {
    "$sb" run --a11y -- sh -c "'$app' & '$sb' a11y tree --window a11y-test"
  }
  a11y_tree > "$tmp/tree1"; a11y_tree > "$tmp/tree2"
  grep -q '^ *[a-z ]*button "Save" \[focusable, focused\]$' "$tmp/tree1" \
    && grep -q '^ *check box "Wrap lines" \[focusable, checked\]$' "$tmp/tree1" \
    && grep -q '^ *[a-z ]*text "File name" \[focusable, editable\]$' "$tmp/tree1" \
    && ok "a11y: the tree, names and states" || { fail "a11y: the tree"; cat "$tmp/tree1"; }
  cmp -s "$tmp/tree1" "$tmp/tree2" && ok "a11y: two runs, the same tree" \
    || { fail "a11y: the tree differs"; diff "$tmp/tree1" "$tmp/tree2"; }
  out=$("$sb" run --a11y -- sh -c "'$here/window.py' g3 & '$sb' a11y tree --window g3")
  [ "$out" = "$(printf 'frame "g3"\n  label "a window"')" ] && ok "a11y: a GTK 3 window's tree" \
    || fail "a11y: GTK 3: $out"
  # With no title, GTK's X window is named after the program, python3.
  out=$("$sb" run --a11y -- sh -c "'$app' untitled & '$sb' a11y tree --window python3" | head -1)
  [ "$out" = 'frame ""' ] && ok "a11y: a window with no name goes by the display's" \
    || fail "a11y: no name: $out"
  out=$("$sb" run --a11y -- sh -c "'$app' second & '$sb' wait window python3 >/dev/null && '$sb' a11y tree --window a11y-test" | grep -c '^frame')
  [ "$out" = 1 ] && ok "a11y: a named window doesn't bring its program's unnamed ones" \
    || fail "a11y: named and unnamed: $out frames"
  out=$("$sb" run --a11y -- sh -c 'case $AT_SPI_BUS_ADDRESS in
    *"path=$SHOTBOX_SCRATCH/"*) echo ours ;; *) echo "$AT_SPI_BUS_ADDRESS" ;; esac')
  [ "$out" = ours ] && ok "a11y: the bus is the session's own" || fail "a11y: the bus: $out"
  set +e
  "$sb" run --a11y -- sh -c "'$app' & '$sb' a11y lint --window a11y-test" > "$tmp/lint"
  st=$?; set -e
  [ $st = 1 ] && [ "$(wc -l < "$tmp/lint")" = 3 ] && grep -q '/[a-z ]*button\[1\]: [a-z ]*button with no name$' "$tmp/lint" \
    && grep -q '/image\[5\]: image with no name' "$tmp/lint" \
    && grep -q '/panel\[6\]: focusable panel with no name$' "$tmp/lint" \
    && ok "a11y: lint finds the icon button, the image and the drawing area with no name" \
    || { fail "a11y: lint ($st)"; cat "$tmp/lint"; }
  "$sb" run --a11y -- sh -c "'$app' fixed & '$sb' a11y lint --window a11y-test" > "$tmp/lint" \
    && ok "a11y: lint passes them named" || { fail "a11y: lint, named"; cat "$tmp/lint"; }
  set +e
  "$sb" run --a11y -- sh -c "'$app' & '$sb' a11y lint --window a11y-test --ignore 'image\[' --ignore 'panel with'" > "$tmp/lint"
  st=$?; set -e
  [ $st = 1 ] && [ "$(wc -l < "$tmp/lint")" = 1 ] && grep -q 'button with no name$' "$tmp/lint" \
    && ok "a11y: lint --ignore leaves out what it's told to" || { fail "a11y: lint --ignore ($st)"; cat "$tmp/lint"; }
  "$sb" run --a11y -- sh -c "'$app' & '$sb' a11y click Announce --window a11y-test >/dev/null &&
    '$sb' wait announce 'notes\.md' --timeout 10" \
    && ok "a11y: click finds a button by its name, and clicks it" || fail "a11y: click"
  set +e
  "$sb" run --a11y -- sh -c "'$app' & '$sb' a11y tabwalk --visible --window a11y-test" > "$tmp/walk"
  st=$?; set -e
  [ $st = 1 ] && [ "$(grep -c 'focus not shown' "$tmp/walk")" = 2 ] \
    && grep -q '^panel "" \[focusable, focused\]  <- focus not shown' "$tmp/walk" \
    && ok "a11y: tabwalk --visible finds the drawing area that shows no focus" \
    || { fail "a11y: tabwalk --visible ($st)"; cat "$tmp/walk"; }
  "$sb" run --a11y -- sh -c "'$app' & '$sb' a11y contrast --window a11y-test" > "$tmp/contrast" \
    && ok "a11y: contrast passes libadwaita's own" || { fail "a11y: contrast"; cat "$tmp/contrast"; }
  # Rows scrolled out of view are showing, to GTK, and not measured.
  "$sb" run --a11y -- sh -c "'$app' long & '$sb' a11y contrast --window a11y-test" \
    > "$tmp/contrast" 2>&1 \
    && ok "a11y: contrast leaves out what's scrolled out of view" \
    || { fail "a11y: contrast, scrolled"; cat "$tmp/contrast"; }
  # GTK 3 places a window's first child where it is, even at the corner.
  rm -f "$tmp/corner"
  "$sb" run --a11y -- sh -c "'$here/window.py' corner '$tmp/corner' corner &
    '$sb' a11y click Corner --window corner >/dev/null && sleep 0.5" \
    && [ "$(cat "$tmp/corner" 2>/dev/null)" = clicked ] \
    && ok "a11y: click finds a GTK 3 button in its window's corner" || fail "a11y: click, GTK 3 corner"
  set +e
  "$sb" run --a11y -- sh -c "'$app' faint & '$sb' a11y contrast --window a11y-test" > "$tmp/contrast"
  st=$?; set -e
  [ $st = 1 ] && [ "$(wc -l < "$tmp/contrast")" = 1 ] \
    && grep -q '/label\[[0-9]*\]: text 2\.5[01]:1, #a0a0a0 on #fafaf[ab] (at least 4\.5)$' "$tmp/contrast" \
    && ok "a11y: contrast finds faint text, and its colors" || { fail "a11y: contrast, faint ($st)"; cat "$tmp/contrast"; }
  set +e
  "$sb" run --a11y -- sh -c "'$app' & '$sb' a11y contrast --edges 3 --window a11y-test" > "$tmp/contrast"
  st=$?; set -e
  [ $st = 1 ] && grep -q '/[a-z ]*button\[0\]: edge 1\.[0-9]*:1' "$tmp/contrast" \
    && ok "a11y: contrast --edges finds buttons with no edge to speak of" \
    || { fail "a11y: contrast --edges ($st)"; cat "$tmp/contrast"; }
  # GTK 3 calls a switch a toggle button; a frame draws a text view's
  # edge; light text, which can measure short of its color (by how much
  # depends on the font), is said to be that color.
  set +e
  "$sb" run --a11y -- sh -c "'$here/window.py' g3 contrast &
    '$sb' a11y contrast --edges 3 --window g3" > "$tmp/contrast"
  st=$?; set -e
  [ $st = 1 ] && grep -q '/label\[1\]: text ' "$tmp/contrast" \
    && ! grep -q 'toggle button\|/text\[0\]' "$tmp/contrast" \
    && ! grep '/label\[0\]: ' "$tmp/contrast" | grep -qv 'its color is #5f5670, 6\.5[0-9]:1' \
    && ok "a11y: contrast, GTK 3: not a switch, a framed text view, light text's color said" \
    || { fail "a11y: contrast, GTK 3 ($st)"; cat "$tmp/contrast"; }
  "$sb" run --a11y -- sh -c "'$app' & '$sb' a11y tabwalk --window a11y-test" > "$tmp/walk" \
    && sed -n 1p "$tmp/walk" | grep -qx '\(push \)\{0,1\}button "" \[focusable, focused\]' \
    && grep -q '"Save" \[focusable, focused\]$' "$tmp/walk" \
    && [ "$(tail -1 "$tmp/walk")" = "round again after 6" ] \
    && ok "a11y: tabwalk goes round and back" || { fail "a11y: tabwalk"; cat "$tmp/walk"; }
  # Scrolled windows, lists and notebooks take the focus with no name of
  # their own, and say what's in them; Tab passes into their rows.
  "$sb" run --a11y -- sh -c "'$app' lists & '$sb' a11y lint --window a11y-test &&
    '$sb' a11y tabwalk --window a11y-test" > "$tmp/walk" \
    && grep -q '^round again after' "$tmp/walk" \
    && ok "a11y: lists, scrolled, in a notebook: lint passes, tabwalk goes round" \
    || { fail "a11y: lists"; cat "$tmp/walk"; }
  "$sb" run --a11y -- sh -c "'$app' one & '$sb' a11y tabwalk --window a11y-test" > "$tmp/walk" \
    && [ "$(tail -1 "$tmp/walk")" = "round again after 1" ] \
    && ok "a11y: tabwalk, with one thing to take the focus, is round in one" \
    || { fail "a11y: one"; cat "$tmp/walk"; }
  set +e
  "$sb" run --a11y -- sh -c "'$app' trap & '$sb' a11y tabwalk --window a11y-test" > "$tmp/walk"
  st=$?; set -e
  [ $st = 1 ] && grep -q '^stuck' "$tmp/walk" && ok "a11y: tabwalk finds a focus trap" \
    || { fail "a11y: trap ($st)"; cat "$tmp/walk"; }
  # Said twice, waited for twice; a third wait has nothing left to take.
  "$sb" run --announce-log "$tmp/said" -- sh -c "'$app' & '$sb' wait window a11y-test &&
    '$sb' key Tab Tab space && '$sb' wait announce 'notes\.md' --timeout 10 &&
    '$sb' key space && '$sb' wait announce 'notes\.md' --timeout 10 &&
    ! '$sb' wait announce 'notes\.md' --timeout 1 2>/dev/null" \
    && [ "$(cat "$tmp/said")" = "$(printf 'polite: Saved to notes.md\npolite: Saved to notes.md')" ] \
    && ok "a11y: announcements, logged, and each waited for once" \
    || { fail "a11y: announcements"; cat "$tmp/said"; }
  set +e; "$sb" run -- "$sb" a11y tree 2> "$tmp/err"; st=$?; set -e
  [ $st != 0 ] && grep -q -- "--a11y" "$tmp/err" && ok "a11y: without --a11y, no tree, and says why" \
    || fail "a11y: no bus: $st"
  # shotbox mcp: the protocol, and its tools on a session like these.
  set +e; python3 "$here/mcp_test.py" "$sb" > "$tmp/mcp.log" 2>&1; st=$?; set -e
  grep -E '^(ok|FAIL) ' "$tmp/mcp.log" | sed 's/^\(....  \)/\1mcp: /'
  fails=$((fails + $(grep -c '^FAIL' "$tmp/mcp.log" || true)))
  [ $st = 0 ] || grep -q "^FAIL" "$tmp/mcp.log" || { fail "mcp_test.py: status $st"; tail -20 "$tmp/mcp.log"; }
  [ -z "$(ours_a11y)" ] && ok "a11y: the bus stops with the session" \
    || fail "a11y: left running: $(ours_a11y)"
  unset TMPDIR

  # The presets: darker, more contrast, bigger text.
  "$sb" shoot "$tmp/p.png" --window a11y-test -- "$app" >/dev/null
  "$sb" shoot "$tmp/p-dark.png" --window a11y-test --dark -- "$app" >/dev/null
  "$sb" shoot "$tmp/p-hc.png" --window a11y-test --high-contrast -- "$app" >/dev/null
  "$sb" shoot "$tmp/p-big.png" --window a11y-test --text-scale 1.5 -- "$app" >/dev/null
  mean() { identify -format '%[fx:int(mean*100)]' "$1"; }
  [ "$(mean "$tmp/p-dark.png")" -lt $(($(mean "$tmp/p.png") / 2)) ] \
    && ok "presets: --dark is dark" \
    || fail "presets: --dark: mean $(mean "$tmp/p-dark.png")%, without $(mean "$tmp/p.png")%"
  ! cmp -s "$tmp/p.png" "$tmp/p-hc.png" && [ "$(mean "$tmp/p-hc.png")" -gt "$(mean "$tmp/p-dark.png")" ] \
    && ok "presets: --high-contrast changes it" \
    || fail "presets: --high-contrast: mean $(mean "$tmp/p-hc.png")%, without $(mean "$tmp/p.png")%"
  [ "$(identify -format %h "$tmp/p-big.png")" -gt "$(identify -format %h "$tmp/p.png")" ] \
    && ok "presets: --text-scale makes the text, and the window, bigger" \
    || fail "presets: --text-scale: $(identify -format %h "$tmp/p-big.png") high, without $(identify -format %h "$tmp/p.png")"
  # A seeded settings.ini that ends in another group, with no last newline.
  # Its theme is GTK 3's own: Ubuntu's GTK 3 defaults to Yaru, which has no
  # dark variant where it isn't installed.
  mkdir -p "$tmp/seed/.config/gtk-3.0"
  printf '[Settings]\ngtk-theme-name = Adwaita\ngtk-cursor-blink = false\n[Other]\nx = 1' \
    > "$tmp/seed/.config/gtk-3.0/settings.ini"
  "$sb" shoot "$tmp/p-seed.png" --window g3 --dark --seed "$tmp/seed" -- "$here/window.py" g3 >/dev/null
  [ "$(mean "$tmp/p-seed.png")" -lt 50 ] && ok "presets: --dark, over a seeded settings.ini" \
    || { fail "presets: seeded: mean $(mean "$tmp/p-seed.png")%, settings.ini:"
         "$sb" run --dark --seed "$tmp/seed" -- sh -c 'cat ~/.config/gtk-3.0/settings.ini' 2>&1; }
  # Seeded dotfiles linked out of the seed, as stow makes them: a linked
  # settings.ini and a linked directory. What they point at stays as it was.
  mkdir -p "$tmp/linked/.config" "$tmp/out4"
  printf '[Settings]\n# mine\n' > "$tmp/out3.ini"; cp "$tmp/out3.ini" "$tmp/out3.orig"
  mkdir -p "$tmp/linked/.config/gtk-3.0"
  ln -s "$tmp/out3.ini" "$tmp/linked/.config/gtk-3.0/settings.ini"
  ln -s "$tmp/out4" "$tmp/linked/.config/gtk-4.0"
  out=$("$sb" run --dark --seed "$tmp/linked" -- sh -c 'cat ~/.config/gtk-3.0/settings.ini')
  cmp -s "$tmp/out3.ini" "$tmp/out3.orig" && [ -z "$(ls "$tmp/out4")" ] \
    && case $out in *gtk-application-prefer-dark-theme*"# mine"*) true ;; *) false ;; esac \
    && ok "presets: a seed's links out are read, not written" || fail "presets: links: $out"
else
  [ "${SHOTBOX_A11Y-}" = required ] && fail "a11y skipped: no GTK 4, libadwaita or at-spi2-core" \
    || echo "skip  a11y: no GTK 4, libadwaita or at-spi2-core"
fi

# Wayland: a headless sway, and Xwayland in it. They need sway, grim and
# Xwayland, or they skip, unless SHOTBOX_WAYLAND=required.
win=$here/window.py
# Giving a window the keyboard: the second one up has it on X11 only once
# it's told to, and typing reaches the one told.
focus() {
  rm -f "$tmp/f1" "$tmp/f2"
  "$sb" run "$@" -- sh -c "'$win' f1 '$tmp/f1' & '$sb' wait window f1 >/dev/null &&
    '$win' f2 '$tmp/f2' & '$sb' wait window f2 >/dev/null && sleep 0.5 &&
    '$sb' focus f1 && '$sb' type one && '$sb' focus f2 && '$sb' type two && sleep 0.5"
  [ "$(cat "$tmp/f1" 2>/dev/null)" = one ] && [ "$(cat "$tmp/f2" 2>/dev/null)" = two ]
}
focus && ok "focus gives a window the keyboard" || fail "focus: f1=$(cat "$tmp/f1" 2>/dev/null) f2=$(cat "$tmp/f2" 2>/dev/null)"
xwayland() { command -v Xwayland >/dev/null; }
xwayland_skip() {
  [ "${SHOTBOX_WAYLAND-}" = required ] && fail "$1 skipped: no Xwayland" || echo "skip  $1: no Xwayland"
}
# Their sessions' scratch dirs go under one of ours, so what's left of
# them can be told from any other sway or Xwayland on the machine.
wl=$tmp/wl
mkdir -p "$wl"
ours_xwayland() {
  for p in $(pgrep -x Xwayland); do
    { tr '\0' '\n' < "/proc/$p/environ"; } 2>/dev/null | grep -q "^XDG_RUNTIME_DIR=$wl/" && echo "$p"
  done
  true
}
if command -v sway >/dev/null && command -v grim >/dev/null; then
  focus --wayland && ok "wayland: focus gives a window the keyboard" || fail "wayland: focus"
  export TMPDIR=$wl
  out=$(WAYLAND_DISPLAY=outside DISPLAY=:0 "$sb" run --wayland -- \
    sh -c 'echo "W=$WAYLAND_DISPLAY D=${DISPLAY-} S=$SWAYSOCK R=$XDG_RUNTIME_DIR"')
  case $out in
    "W=wayland-"*" D= S=$wl/shotbox-"*/run/sway-ipc.*" R=$wl/shotbox-"*) ok "wayland: a sway of its own, and no X" ;;
    *) fail "wayland: the environment: $out" ;;
  esac
  "$sb" shoot "$tmp/w1.png" --wayland --window wl-test -- "$win" wl-test >/dev/null
  "$sb" shoot "$tmp/w2.png" --wayland --window wl-test -- "$win" wl-test >/dev/null
  [ "$(identify -format %wx%h "$tmp/w1.png")" = 200x120 ] && cmp -s "$tmp/w1.png" "$tmp/w2.png" \
    && ok "wayland: shoot takes the window, the same bytes twice" || fail "wayland: shoot"
  "$sb" shoot "$tmp/w3.png" --wayland --wait window:wl-test --wait stable -- "$win" wl-test >/dev/null \
    && [ "$(identify -format %wx%h "$tmp/w3.png")" = 1280x800 ] \
    && ok "wayland: a stable wait, and the whole output" || fail "wayland: stable"
  set +e
  "$sb" shoot "$tmp/w4.png" --wayland --window never --timeout 2 -- "$win" wl-test 2> "$tmp/err"
  st=$?; set -e
  [ $st != 0 ] && [ -s "$tmp/w4-failed.png" ] && ok "wayland: a failed wait leaves a picture" \
    || fail "wayland: no -failed.png ($st)"
  if xwayland; then
    "$sb" run --xwayland -- sh -c "[ -n \"\$DISPLAY\" ] && GDK_BACKEND=x11 '$win' wl-x11 &
      '$sb' wait window wl-x11 && '$sb' wait stable --window wl-x11 &&
      '$sb' capture '$tmp/w5.png' --window wl-x11" \
      && [ "$(identify -format %wx%h "$tmp/w5.png")" = 200x120 ] \
      && ok "xwayland: an X client found and taken" || fail "xwayland"
    # Taken by title and by app id in one session: the same window, the
    # same pixels, whenever it painted.
    "$sb" run --xwayland -- sh -c "GDK_BACKEND=x11 '$win' wl-x11 &
      '$sb' wait window app=Window.py && '$sb' wait stable --window app=Window.py &&
      '$sb' capture '$tmp/w8.png' --window app=Window.py &&
      '$sb' capture '$tmp/w9.png' --window wl-x11" \
      && cmp -s "$tmp/w8.png" "$tmp/w9.png" && ok "xwayland: an X window by its WM_CLASS" \
      || fail "xwayland: app="
  else
    xwayland_skip "xwayland"
  fi
  "$sb" shoot "$tmp/w7.png" --wayland --window app=window.py -- "$win" wl-test >/dev/null \
    && cmp -s "$tmp/w1.png" "$tmp/w7.png" && ok "wayland: a window by its app_id" \
    || fail "wayland: app="
  "$sb" term --shoot "$tmp/w6.png" --wayland --size 30x5 --crop 12x3 --when there -- \
    sh -c 'echo hi; echo there' >/dev/null \
    && ok "wayland: term --shoot, cropped ($(identify -format %wx%h "$tmp/w6.png"))" \
    || fail "wayland: term --shoot"
  # Input: the same as X11's, then a new keyboard for every key, none of
  # which may lose its first (wtype lost one in a spike; shotbox never
  # has), then an X client.
  "$sb" run --wayland -- sh -c "
    '$sb' term --size 40x6 --log '$tmp/wl-in.log' -- sh -c cat & t=\$!
    '$sb' wait window shotbox-term && '$sb' wait stable --window shotbox-term &&
    '$sb' click 20 20 --window shotbox-term &&
    '$sb' type 'Hi, you! 1+1=2 ~/a_b <x>' && '$sb' key Return ctrl+d
    st=\$?; sleep 0.5; kill \$t 2>/dev/null; exit \$st" \
    && grep -aq 'Hi, you! 1+1=2 ~/a_b <x>' "$tmp/wl-in.log" \
    && ok "wayland: click, type and key reach the program" || fail "wayland: input"
  chords "$tmp/wl-chords.log" --wayland \
    && ok "wayland: shift in a chord: the shifted symbol, and back-tab" || fail "wayland: shift chords"
  "$sb" run --wayland -- sh -c "
    '$sb' term --size 40x6 --log '$tmp/wl-keys.log' -- sh -c cat & t=\$!
    '$sb' wait window shotbox-term && '$sb' wait stable --window shotbox-term &&
    for c in a b c d e f g h i j k l m n o p q r s t; do '$sb' type \$c; done
    '$sb' key Return; sleep 0.5; kill \$t 2>/dev/null"
  tr -d '\r' < "$tmp/wl-keys.log" | grep -aqx abcdefghijklmnopqrst \
    && ok "wayland: twenty keyboards, no first key lost" || fail "wayland: a key went missing"
  if xwayland; then
    "$sb" run --xwayland -- sh -c "
      GDK_BACKEND=x11 '$sb' term --size 40x6 --log '$tmp/xw-in.log' -- sh -c cat & t=\$!
      '$sb' wait window shotbox-term && '$sb' wait stable --window shotbox-term &&
      '$sb' click 20 20 --window shotbox-term && '$sb' type 'over X' && '$sb' key Return
      st=\$?; sleep 0.5; kill \$t 2>/dev/null; exit \$st" \
      && grep -aq 'over X' "$tmp/xw-in.log" \
      && ok "xwayland: typing reaches an X client" || fail "xwayland: input"
  else
    xwayland_skip "xwayland: input"
  fi
  if [ -n "${a11y-}" ]; then
    set +e
    "$sb" run --wayland --a11y -- sh -c "'$app' & '$sb' a11y lint --window app=org.shotbox.A11yTest
      st=\$?; '$sb' click 100 140 --window a11y-test &&
      '$sb' wait announce 'notes\.md' --timeout 10 || exit 9; exit \$st" > "$tmp/wl-lint"
    st=$?; set -e
    [ $st = 1 ] && [ "$(wc -l < "$tmp/wl-lint")" = 3 ] \
      && ok "wayland: a11y: lint, by app id, and an announcement" \
      || { fail "wayland: a11y ($st)"; cat "$tmp/wl-lint"; }
    "$sb" run --wayland --a11y -- sh -c "'$app' & '$sb' a11y click Announce --window a11y-test >/dev/null &&
      '$sb' wait announce 'notes\.md' --timeout 10" \
      && ok "wayland: a11y: click by name" || fail "wayland: a11y: click"
  fi
  pgrep -f "$wl/shotbox-.*/sway.conf" >/dev/null && fail "wayland: sway left running" \
    || ok "wayland: sway stops with the session"
  [ -z "$(ours_xwayland)" ] \
    && ok "xwayland: Xwayland stops with the session" || fail "xwayland: Xwayland left running"
  unset TMPDIR
else
  [ "${SHOTBOX_WAYLAND-}" = required ] && fail "wayland skipped: no sway or grim" \
    || echo "skip  wayland: no sway or grim"
fi

# Compare.
"$sb" compare "$tmp/a.png" "$tmp/b.png" >/dev/null && ok "compare: the same" || fail "compare same"
convert "$tmp/a.png" -fill red -draw 'point 3,3' "$tmp/d.png"
set +e; n=$("$sb" compare "$tmp/a.png" "$tmp/d.png" --diff "$tmp/diff.png"); st=$?; set -e
[ $st = 1 ] && [ "$n" = "1 pixels differ" ] && [ -s "$tmp/diff.png" ] \
  && ok "compare: one pixel off, found" || fail "compare: $st $n"

mkdir -p "$tmp/ref" "$tmp/new"
cp "$tmp/a.png" "$tmp/ref/same.png"; cp "$tmp/a.png" "$tmp/new/same.png"
cp "$tmp/a.png" "$tmp/ref/off.png"; cp "$tmp/d.png" "$tmp/new/off.png"
cp "$tmp/a.png" "$tmp/ref/gone.png"
set +e; out=$("$sb" compare "$tmp/ref" "$tmp/new" --diff "$tmp/diffs"); st=$?; set -e
[ $st = 1 ] && echo "$out" | grep -q "^same: the same" && echo "$out" | grep -q "^off: 1 pixels" \
  && echo "$out" | grep -q "^gone: no" && [ -s "$tmp/diffs/off-diff.png" ] \
  && ok "compare: directories, picture by picture" || fail "compare dirs: $st $out"
"$sb" compare "$tmp/ref" "$tmp/new" same off --max 0 --max off=1 >/dev/null \
  && ok "compare: NAMEs, and --max for one" || fail "compare NAME=N"
set +e; "$sb" compare "$tmp/a.png" "$tmp/nope.png" 2> "$tmp/err"; st=$?; set -e
[ $st = 2 ] && grep -q "no .*nope.png" "$tmp/err" && ok "compare: a missing picture, said plainly" \
  || fail "compare missing: $st $(cat "$tmp/err")"

# Node.
node --test --test-reporter=spec "$here"/node.test.mjs >"$tmp/node.log" 2>&1 && ok "node helpers" \
  || { fail "node helpers"; cat "$tmp/node.log"; }
# The declarations, which are hand-written, against a file that uses them.
tsc=$here/../node_modules/.bin/tsc
if [ -x "$tsc" ]; then
  (cd "$here/.." && "$tsc") >"$tmp/tsc.log" 2>&1 && ok "the declarations" \
    || { fail "the declarations"; cat "$tmp/tsc.log"; }
else
  echo "skip  the declarations: no TypeScript (npm install)"
fi

# And the ones that need a browser: Playwright's Chromium, from
# `npm install && npx playwright install chromium`, or they skip, unless
# SHOTBOX_BROWSER=required.
node --test --test-reporter=spec "$here"/browser.test.mjs >"$tmp/browser.log" 2>&1 || {
  fail "browser helpers"; cat "$tmp/browser.log"; }
if grep -q '^ℹ skipped [1-9]' "$tmp/browser.log"; then
  # CI says it has to run, so there a skip is a failure.
  why=$(grep -m1 -o 'no Playwright[^)]*)\|no ffmpeg\|no at-spi[^)]*)' "$tmp/browser.log")
  [ "${SHOTBOX_BROWSER-}" = required ] && fail "browser helpers skipped: $why" \
    || echo "skip  browser helpers: $why"
elif ! grep -q '^ℹ fail [1-9]' "$tmp/browser.log"; then
  ok "browser helpers"
fi

echo
[ $fails = 0 ] && echo "all passed" || { echo "$fails failed"; exit 1; }
