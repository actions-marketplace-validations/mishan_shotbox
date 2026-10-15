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
# A TMPDIR too long for the session's sockets, said before anything starts.
long=$tmp/$(printf 'x%.0s' $(seq 45)); mkdir "$long"
set +e; TMPDIR=$long "$sb" run -- true 2> "$tmp/err"; st=$?; set -e
[ $st != 0 ] && grep -q "too long a path to make the session's sockets in" "$tmp/err" && ok "a TMPDIR too long, said" \
  || fail "long TMPDIR ($st): $(cat "$tmp/err")"
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
"$sb" run --desktop --seed "$tmp/seed" -- sh -c 'gdbus call --session --timeout 2 --dest org.shotbox.Probe \
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

# held LOG [SESSION OPTIONS]: keys and a button held for a while, into
# test/held.py, which logs each press and release with its time; prints
# what came in, pressed (+) or let go (-), a button said once.
held() {
  log=$1; shift
  : > "$log"
  "$sb" run "$@" -- sh -c "'$here/held.py' held '$log' &
    '$sb' wait window held && '$sb' wait stable --window held &&
    '$sb' click 50 50 --window held && '$sb' key --hold 0.4 a d &&
    '$sb' click 60 60 --window held --hold 0.3; st=\$?; sleep 0.3; exit \$st" || return 1
  awk '{ s = ($1 == "press" ? "+" : "-") $2; if (s != last) printf "%s ", s; last = s }' "$log"
}
# How long the key a was held in LOG, in hundredths of a second.
held_for() {
  awk '$2 == "a" { t[$1] = $3 } END { printf "%d", (t["release"] - t["press"]) * 100 }' "$1"
}
want_held="+button1 -button1 +a +d -d -a +button1 -button1 "

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
got=$(held "$tmp/held.log")
[ "$got" = "$want_held" ] && [ "$(held_for "$tmp/held.log")" -ge 35 ] \
  && ok "key and click --hold: held together, let go last first" || fail "hold: $got"
# --down and --up: a key left down between two commands, X11's server keeps it.
: > "$tmp/down.log"
"$sb" run -- sh -c "'$here/held.py' held '$tmp/down.log' &
  '$sb' wait window held && '$sb' wait stable --window held &&
  '$sb' click 50 50 --window held && '$sb' key --down w && sleep 0.3 && '$sb' key --up w &&
  sleep 0.3" && awk '$2 == "w" { t[$1] = $3 } END { exit !(t["release"] - t["press"] >= 0.25) }' \
  "$tmp/down.log" && ok "key --down, then --up: held between commands" || fail "--down, --up"
# Two held chords sharing Ctrl: letting go of one leaves Ctrl down for the
# other, and one is let go however it's spelled. A chord it can't press is said before anything goes down, a hold
# that isn't one is refused, and a hold stopped midway lets go.
: > "$tmp/mods.log"
"$sb" run -- sh -c "'$here/held.py' held '$tmp/mods.log' &
  '$sb' wait window held && '$sb' wait stable --window held && '$sb' click 50 50 --window held &&
  '$sb' key --down Control+c && '$sb' key --down ctrl+d && '$sb' key --up ctrl+d && echo mark >> '$tmp/mods.log' &&
  '$sb' key --up ctrl+c && ! '$sb' key --hold 0.2 b bogus 2>/dev/null &&
  ! '$sb' key --hold -1 b 2>/dev/null && { '$sb' key --hold 30 e & k=\$!; sleep 0.5; kill \$k; wait \$k;
  sleep 0.3; }"
got=$(awk '$1 == "mark" { printf "| "; next }
  { s = ($1 == "press" ? "+" : "-") $2; if (s != last) printf "%s ", s; last = s }' "$tmp/mods.log")
[ "$got" = "+button1 -button1 +Control_L +c +d -d | -c -Control_L +e -e " ] \
  && ok "held chords share a modifier; a bad chord or hold presses nothing; stopped, it lets go" \
  || fail "held modifiers: $got"
# While Shift+W is held, a key pressed and let go (or typed) leaves Shift
# down for W; a chord held twice is pressed once, a hold or a tap over it
# doesn't let it go, and it's let go however it's spelled.
: > "$tmp/twice.log"
"$sb" run -- sh -c "'$here/held.py' held '$tmp/twice.log' &
  '$sb' wait window held && '$sb' wait stable --window held && '$sb' click 50 50 --window held &&
  '$sb' key --down shift+w && '$sb' key shift+a && '$sb' type B && '$sb' key shift+w && '$sb' key --down shift+w &&
  '$sb' key --hold 0.1 shift+w && echo mark >> '$tmp/twice.log' && '$sb' key --up W && sleep 0.3"
got=$(awk '$1 == "mark" { printf "| "; next }
  { s = ($1 == "press" ? "+" : "-") $2; if (s != last) printf "%s ", s; last = s }' "$tmp/twice.log")
[ "$got" = "+button1 -button1 +Shift_L +W +A -A +B -B | -W -Shift_L " ] \
  && ok "keys tapped while one's held keep its modifier; held twice, or tapped, it stays down" \
  || fail "tap while held: $got"
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
    && ! grep '/label\[0\]: ' "$tmp/contrast" | grep -qv 'drawn lighter than its color, #5f5670, which is 6\.5[0-9]:1' \
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
  got=$(held "$tmp/wl-held.log" --wayland)
  [ "$got" = "$want_held" ] && [ "$(held_for "$tmp/wl-held.log")" -ge 35 ] \
    && ok "wayland: key and click --hold" || fail "wayland: hold: $got"
  set +e; "$sb" run --wayland -- "$sb" key --down w 2> "$tmp/err"; st=$?; set -e
  [ $st != 0 ] && grep -q "use --hold" "$tmp/err" \
    && ok "wayland: key --down, which can't last, refused saying why" || fail "wayland: --down ($st)"
  # Shift+W held by one command: keys pressed, typed or held by others are
  # shifted too, and pressing W again doesn't let it go. And a hold killed
  # outright, which can't let go, is cleared with --up.
  : > "$tmp/wl-mods.log"
  "$sb" run --wayland -- sh -c "'$here/held.py' held '$tmp/wl-mods.log' &
    '$sb' wait window held && '$sb' wait stable --window held && '$sb' click 50 50 --window held &&
    { '$sb' key --hold 3 shift+w & h=\$!; sleep 0.4; '$sb' key a && '$sb' type b &&
      '$sb' key --hold 0.2 c && '$sb' key shift+w; wait \$h; } &&
    { '$sb' key --hold 30 e & k=\$!; sleep 0.4; kill -KILL \$k; } &&
    '$sb' key --up e && '$sb' key --hold 0.1 e && sleep 0.3"
  grep -q "press A" "$tmp/wl-mods.log" && grep -q "press B" "$tmp/wl-mods.log" \
    && grep -q "press C" "$tmp/wl-mods.log" && [ "$(grep -c "release [Ww]" "$tmp/wl-mods.log")" = 1 ] \
    && [ "$(grep -c "press e" "$tmp/wl-mods.log")" = 2 ] \
    && ok "wayland: a chord held by one command shifts another's and stays down; a killed hold is let go with --up" \
    || fail "wayland: held across commands: $(tr '\n' ' ' < "$tmp/wl-mods.log")"
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
  set +e; "$sb" run --wayland -- "$sb" record "$tmp/wl.mp4" --seconds 1 2> "$tmp/err"; st=$?; set -e
  [ $st != 0 ] && grep -q "X11 sessions only" "$tmp/err" \
    && ok "wayland: record, X11's for now, refused saying so" || fail "wayland: record ($st)"
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

# A burst: five pictures a tenth of a second apart, numbered, and a sheet
# of them in rows of four.
"$sb" run -- sh -c "'$here/window.py' bw & '$sb' wait window bw &&
  '$sb' capture '$tmp/burst.png' --window bw --burst 5 --every 0.1 --sheet '$tmp/sheet.png'" > "$tmp/burst.txt" \
  && [ "$(identify -format %wx%h "$tmp/burst-5.png")" = 200x120 ] \
  && [ "$(identify -format %wx%h "$tmp/sheet.png")" = 800x240 ] \
  && awk '{ sub("s", "", $2); if ($2 < (NR - 1) * 0.1 - 0.001) bad = 1 } END { exit bad || NR != 5 }' "$tmp/burst.txt" \
  && ok "capture --burst: numbered, spaced, and a sheet" || fail "burst: $(cat "$tmp/burst.txt")"

# Recording: a window for two seconds as an MP4; the screen as a WebM at
# 10 frames a second; a crop as a GIF, its odd sides evened; and one with
# no length, stopped by kill, finished properly.
if command -v ffmpeg >/dev/null; then
  probe() { ffprobe -v error -select_streams v -show_entries stream=codec_name,width,height \
    -show_entries format=duration -of csv=p=0 "$1" | tr '\n' ' '; }
  frames() { ffprobe -v error -count_frames -select_streams v -show_entries stream=nb_read_frames \
    -of csv=p=0 "$1"; }
  "$sb" run -- sh -c "'$here/window.py' rec & '$sb' wait window rec &&
    '$sb' record '$tmp/rec.mp4' --window rec --seconds 2 && '$sb' record '$tmp/rec.webm' --fps 10 --seconds 1 &&
    '$sb' record '$tmp/rec.gif' --window rec --crop 101x51+10+10 --seconds 1 &&
    { '$sb' record '$tmp/rec-k.mp4' --window rec & r=\$!; sleep 1.5; kill \$r; wait \$r; } &&
    ! '$sb' record '$tmp/rec.avi' --seconds 1 2> '$tmp/err' && ! '$sb' record '$tmp/rec0.mp4' --seconds 0 2>> '$tmp/err'" > /dev/null \
    && [ "$(probe "$tmp/rec.mp4")" = "h264,200,120 2.000000 " ] && [ "$(frames "$tmp/rec.mp4")" -ge 50 ] \
    && [ "$(probe "$tmp/rec.webm")" = "vp9,1280,800 1.000000 " ] && [ "$(frames "$tmp/rec.webm")" = 10 ] \
    && [ "$(probe "$tmp/rec.gif")" = "gif,100,50 1.000000 " ] \
    && probe "$tmp/rec-k.mp4" | grep -q "^h264,200,120 " && [ "$(frames "$tmp/rec-k.mp4")" -ge 15 ] \
    && grep -q "mp4, .webm or .gif" "$tmp/err" && grep -q "more than 0" "$tmp/err" && [ ! -e "$tmp/rec0.mp4" ] \
    && ok "record: a window, the screen, a crop, as MP4, WebM and GIF; stopped by kill, finished" \
    || fail "record: $(probe "$tmp/rec.mp4"); $(probe "$tmp/rec.webm"); $(probe "$tmp/rec.gif"); $(probe "$tmp/rec-k.mp4")"
  # A GIF whose session is stopped (run killed) is finished all the same,
  # leaving nothing behind in the temp dir.
  rm -rf "$tmp/rtmp"; mkdir "$tmp/rtmp"
  "$sb" run --env "TMPDIR=$tmp/rtmp" -- sh -c "'$here/window.py' rg & '$sb' wait window rg &&
    '$sb' record '$tmp/rec-run.gif'" > /dev/null & r=$!
  sleep 6; kill $r; wait $r || true
  n=0; while [ -n "$(ls "$tmp/rtmp")" ] && [ $n -lt 100 ]; do sleep 0.1; n=$((n + 1)); done
  [ "$(probe "$tmp/rec-run.gif" | cut -d' ' -f1)" = gif,1280,800 ] && [ -z "$(ls "$tmp/rtmp")" ] \
    && ok "record: a GIF in a session that's stopped, finished, nothing left behind" \
    || fail "record, stopped run: $(probe "$tmp/rec-run.gif"); $(ls "$tmp/rtmp")"
  # A window partly off the screen is recorded as far as it shows; one
  # entirely off it is refused.
  if command -v xdotool >/dev/null; then
    "$sb" run -- sh -c "'$here/window.py' off & '$sb' wait window off &&
      xdotool search --name '^off\$' windowmove -- -50 -30 && sleep 0.3 &&
      '$sb' record '$tmp/rec-off.mp4' --window off --seconds 0.5 &&
      xdotool search --name '^off\$' windowmove -- -500 -500 && sleep 0.3 &&
      ! '$sb' record '$tmp/rec-gone.mp4' --window off --seconds 0.5 2> '$tmp/err'" > /dev/null \
      && [ "$(probe "$tmp/rec-off.mp4" | cut -d' ' -f1)" = h264,150,90 ] && grep -q "nothing of that" "$tmp/err" \
      && ok "record: a window partly off the screen, as far as it shows; one off it, refused" \
      || fail "record off screen: $(probe "$tmp/rec-off.mp4"); $(cat "$tmp/err")"
  else
    echo "skip  record off the screen: no xdotool"
  fi
else
  echo "skip  record: no ffmpeg"
fi

# Gamepads: only the session's in its /dev/input, read as a game reads
# them; what's left down stays down between commands; and gone with the
# session. Needs write access to /dev/uinput and bubblewrap, or it skips.
if [ -w /dev/uinput ] && command -v bwrap >/dev/null && bwrap --dev-bind / / true 2>/dev/null; then
  : > "$tmp/pad.log"
  "$sb" run --gamepads 2 -- sh -c "'$here/pad_read.py' '$tmp/pad.log' &
    n=0; while ! grep -q ready '$tmp/pad.log' 2>/dev/null && [ \$n -lt 100 ]; do sleep 0.1; n=\$((n + 1)); done
    '$sb' pad press a && '$sb' pad stick left 1 -1 --hold 0.1 &&
    '$sb' pad --pad 2 trigger right 1 --hold 0.1 && '$sb' pad press b --down &&
    '$sb' pad press up && '$sb' pad neutral && sleep 0.3" \
    && [ "$(grep -c '^name shotbox virtual pad' "$tmp/pad.log")" = 2 ] \
    && [ "$(grep -c '^name' "$tmp/pad.log")" = 2 ] \
    && ok "gamepads: the session's, and only those, in its /dev/input" || fail "gamepads: $(grep name "$tmp/pad.log")"
  # Pad 2 only pulls its trigger (abs 5); each pad's events in order.
  got=$(grep -E '^(key|abs) ' "$tmp/pad.log" | grep -v '^abs 5 ' | tr '\n' ' ')
  got2=$(grep '^abs 5 ' "$tmp/pad.log" | tr '\n' ' ')
  [ "$got" = "key 304 1 key 304 0 abs 0 32767 abs 1 -32767 abs 0 0 abs 1 0 key 305 1 abs 17 -1 abs 17 0 key 305 0 " ] \
    && [ "$got2" = "abs 5 255 abs 5 0 " ] \
    && ok "gamepads: buttons, sticks, triggers and the d-pad, as an Xbox 360 pad's" || fail "gamepad input: $got"
  grep -q "shotbox virtual pad" /proc/bus/input/devices && fail "gamepads: left behind" \
    || ok "gamepads: gone with the session"
  # Nothing else of the machine's to read controllers from: raw HID devices
  # and uinput can't be opened inside, and there's no /dev/bus/usb to scan.
  "$sb" run --gamepads 1 -- sh -c 'for f in /dev/hidraw* /dev/uhid /dev/usb/hiddev* /dev/uinput; do
      [ -e "$f" ] && { [ -c "$f" ] || head -c0 "$f"; } 2>/dev/null && echo "$f"; done
    [ ! -d /dev/bus/usb ] || ls /dev/bus/usb' > "$tmp/seal.out" \
    && [ ! -s "$tmp/seal.out" ] && ok "gamepads: the machine's HID, USB and uinput, masked" \
    || fail "gamepads: reachable: $(cat "$tmp/seal.out")"
  # The pad server goes with the process that started it, even killed.
  python3 - "$root" <<'PY' && ok "gamepads: gone when what made them is killed" || fail "gamepads: outlived a kill"
import os, shutil, signal, subprocess, sys, tempfile, time
d = tempfile.mkdtemp()
parent = subprocess.Popen([sys.executable, "-c", f"""
import os, subprocess, sys, time
sys.path.insert(0, {sys.argv[1]!r})
subprocess.Popen([sys.executable, "-c", "import sys; sys.path.insert(0, {sys.argv[1]!r}); "
                  "from shotbox.pad import serve; serve(*sys.argv[1:])",
                  "{d}/pads.sock", "1", "{d}/ready", str(os.getpid())])
time.sleep(60)"""])
end = time.monotonic() + 10
while not os.path.exists(f"{d}/ready") and time.monotonic() < end:
    time.sleep(0.05)
there = lambda: "shotbox virtual pad" in open("/proc/bus/input/devices").read()
made = there()
parent.send_signal(signal.SIGKILL)
parent.wait()
end = time.monotonic() + 3
while there() and time.monotonic() < end:
    time.sleep(0.05)
shutil.rmtree(d)
sys.exit(0 if made and not there() else 1)
PY
  # The Python API's spawn takes a path, as subprocess does, sandboxed too;
  # and what a thread spawns outlives the thread.
  python3 - "$root" <<'PY' && ok "gamepads: spawn takes a Path, and from a thread that ends" \
    || fail "gamepads: spawn of a Path, or from a thread"
import sys, threading, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from shotbox.session import Session
with Session(gamepads=1) as s:
    got = []
    t = threading.Thread(target=lambda: got.append(s.spawn(["sleep", "30"])))
    t.start()
    t.join()
    time.sleep(0.5)
    sys.exit(s.spawn([Path("/bin/true")]).wait() or got[0].poll() is not None)
PY
  # A program that hangs up, says nothing, or sends nonsense doesn't take
  # the pads down; values out of range and pads that aren't there are
  # refused, not moved.
  : > "$tmp/pad2.log"
  "$sb" run --gamepads 1 -- sh -c "'$here/pad_read.py' '$tmp/pad2.log' &
    n=0; while ! grep -q ready '$tmp/pad2.log' 2>/dev/null && [ \$n -lt 100 ]; do sleep 0.1; n=\$((n + 1)); done
    python3 -c 'import os, socket; s = socket.socket(socket.AF_UNIX); s.connect(os.environ[\"SHOTBOX_SCRATCH\"] + \"/pads.sock\")' &&
    python3 -c 'import json, os, socket
for m in (b\"[]\", b\"[\" * 99999, json.dumps(dict(op=\"stick\", which=\"left\", x=10**400, y=0)).encode()):
    s = socket.socket(socket.AF_UNIX); s.connect(os.environ[\"SHOTBOX_SCRATCH\"] + \"/pads.sock\"); s.sendall(m + b\"\\n\"); s.recv(99)' &&
    python3 -c 'import os, socket, time; s = socket.socket(socket.AF_UNIX); s.connect(os.environ[\"SHOTBOX_SCRATCH\"] + \"/pads.sock\"); time.sleep(8)' &
    sleep 0.2; '$sb' pad press x && '$sb' pad press down --down && '$sb' pad press up &&
    '$sb' pad neutral && ! '$sb' pad stick left 5 0 2> '$tmp/range' &&
    ! '$sb' pad --pad 2 press a 2>> '$tmp/range' && ! '$sb' pad --pad 0 press a 2>> '$tmp/range' &&
    { '$sb' pad press b --hold 30 & k=\$!; sleep 0.5; kill \$k; wait \$k; sleep 0.3; } &&
    '$sb' pad press lb --down && '$sb' pad press lb --hold 0.1 && '$sb' pad press lb a &&
    '$sb' pad press lb --up &&
    { '$sb' pad press y --hold 30 & k=\$!; sleep 0.5; kill -KILL \$k; wait \$k; '$sb' pad press y --up; echo upmark >> '$tmp/pad2.log'; } &&
    { '$sb' pad press rs --hold 1 & h=\$!; sleep 0.3; '$sb' pad neutral && '$sb' pad press rs --hold 2 & h2=\$!;
      wait \$h; sleep 0.3; echo mark >> '$tmp/pad2.log'; wait \$h2; } &&
    { '$sb' pad stick left 1 0 --hold 1 & h=\$!; '$sb' pad trigger right 1 --hold 1 & h2=\$!; sleep 0.3;
      '$sb' pad neutral && '$sb' pad stick left 0 1 && '$sb' pad trigger right 0.5;
      wait \$h \$h2; sleep 0.3; echo smark >> '$tmp/pad2.log'; } && sleep 0.3" \
    && grep -q "^key 307 1" "$tmp/pad2.log" && grep -q "goes from -1 to 1, not 5" "$tmp/range" \
    && grep -q "^key 305 0" "$tmp/pad2.log" \
    && [ "$(grep '^key 310' "$tmp/pad2.log" | tr '\n' ' ')" = "key 310 1 key 310 0 " ] \
    && [ "$(grep -E '^(key 308|upmark)' "$tmp/pad2.log" | tr '\n' ' ')" = "key 308 1 key 308 0 upmark " ] \
    && [ "$(grep -E '^(key 318|mark$)' "$tmp/pad2.log" | tr '\n' ' ')" = "key 318 1 key 318 0 key 318 1 mark key 318 0 " ] \
    && [ "$(grep -E '^(abs 1 |smark)' "$tmp/pad2.log" | tr '\n' ' ')" = "abs 1 32767 smark " ] \
    && [ "$(grep -E '^(abs 5 |smark)' "$tmp/pad2.log" | tr '\n' ' ')" = "abs 5 255 abs 5 0 abs 5 128 smark " ] \
    && [ "$(grep '^abs 17' "$tmp/pad2.log" | tr '\n' ' ')" = "abs 17 1 abs 17 0 abs 17 1 abs 17 0 " ] \
    && grep -q "no pad 2: the session has 1" "$tmp/range" && grep -q "numbered from 1, not 0" "$tmp/range" \
    && ok "gamepads: a client that hangs up or stalls doesn't stop them; d-pad directions each let go; out of range, refused; a stopped hold lets go; one left down stays down; --up ends a killed hold; neutral, and what's set after, not undone by an older hold of a button, stick or trigger" \
    || fail "gamepads: bad clients and ranges: $(cat "$tmp/range")"
  # A program named by a path relative to where it's spawned from: found
  # there, not where the session runs; one on PATH past one of the same
  # name that can't run, or in a relative PATH entry, from where it's
  # spawned; one that can't run, said why. The session's PATH is the
  # program's: a bwrap on it isn't the one shotbox runs.
  mkdir -p "$tmp/rel/relbin" "$tmp/path1" "$tmp/path2"; printf '#!/bin/sh\ntouch ran\n' > "$tmp/rel/prog"; chmod +x "$tmp/rel/prog"
  printf '#!/bin/sh\n' > "$tmp/rel/noexec"
  printf '#!/bin/sh\n' > "$tmp/path1/onpath"; printf '#!/bin/sh\ntouch ran2\n' > "$tmp/path2/onpath"; chmod +x "$tmp/path2/onpath"
  printf '#!/bin/sh\ntouch ran3\n' > "$tmp/rel/relbin/inrel"; chmod +x "$tmp/rel/relbin/inrel"
  printf '#!/bin/sh\nexit 1\n' > "$tmp/path1/bwrap"; chmod +x "$tmp/path1/bwrap"
  "$sb" start --name "pads$$" --gamepads 1 --env "PATH=$tmp/path1:relbin:$tmp/path2:$PATH" >/dev/null
  (cd "$tmp/rel" && "$sb" spawn --session "pads$$" -- ./prog >/dev/null) \
    && "$sb" --session "pads$$" wait file "$tmp/rel/ran" --timeout 10 \
    && (cd "$tmp/rel" && "$sb" spawn --session "pads$$" -- onpath >/dev/null) \
    && "$sb" --session "pads$$" wait file "$tmp/rel/ran2" --timeout 10 \
    && (cd "$tmp/rel" && "$sb" spawn --session "pads$$" -- inrel >/dev/null) \
    && "$sb" --session "pads$$" wait file "$tmp/rel/ran3" --timeout 10 \
    && ! (cd "$tmp/rel" && "$sb" spawn --session "pads$$" -- ./noexec 2> "$tmp/err") \
    && grep -q "not executable" "$tmp/err" \
    && ok "gamepads: a program by a relative path or PATH entry, from where it's spawned, or on PATH past one that can't run; shotbox's bwrap" || fail "gamepads: relative path"
  "$sb" stop "pads$$"
  "$sb" term --shoot "$tmp/term-pad.png" --gamepads 1 --log "$tmp/term-pad.log" --size 40x4 -- \
    sh -c 'ls /dev/input' >/dev/null && grep -aq event "$tmp/term-pad.log" \
    && ok "term --shoot --gamepads: the terminal's program has the pad" || fail "term --shoot --gamepads"
else
  [ "${SHOTBOX_GAMEPAD-}" = required ] && fail "gamepads skipped: no writable /dev/uinput or no bwrap" \
    || echo "skip  gamepads: no writable /dev/uinput or no bwrap"
fi
# A pad server that's gone, said plainly.
python3 - "$root" <<'PY' && ok "pad: a server that's gone, said plainly" || fail "pad: server gone"
import sys, tempfile, os
sys.path.insert(0, sys.argv[1])
from shotbox import pad
with tempfile.TemporaryDirectory() as d:
    open(os.path.join(d, "pads.sock"), "w").close()
    try:
        pad.ask(d, {"op": "info"})
        sys.exit(1)
    except pad.PadError as e:
        sys.exit(0 if "have gone" in str(e) else 1)
PY
set +e; "$sb" run -- "$sb" pad press a 2> "$tmp/err"; st=$?; set -e
[ $st != 0 ] && grep -q -- "--gamepads" "$tmp/err" && ok "pad, in a session without gamepads: says how" \
  || fail "pad without gamepads ($st)"

# Vision: red, green, blue and gray, as each kind of color vision sees
# them, against the model worked out here (within 2 levels: ImageMagick
# rounds a little differently); and a capture taken with --vision is the
# capture, then that.
convert -size 4x1 xc:white -fill '#ff0000' -draw 'point 0,0' -fill '#00ff00' -draw 'point 1,0' \
  -fill '#0000ff' -draw 'point 2,0' -fill '#808080' -draw 'point 3,0' "$tmp/colors.png"
for kind in protanopia deuteranopia tritanopia gray; do
  "$sb" vision "$tmp/colors.png" "$tmp/v-$kind.png" --as $kind >/dev/null
  convert "$tmp/v-$kind.png" -depth 8 txt:- | grep -o '#[0-9A-F]\{6\}' | tr '\n' ' ' > "$tmp/v-$kind.txt"
done
python3 - "$tmp" <<'PY' && ok "vision: each kind, as modeled" || fail "vision: colors"
import sys
# Machado, Oliveira and Fernandes (2009), severity 1.0, written out here
# from the paper rather than taken from shotbox, so a typo there shows.
VISION = {
    "protanopia": (0.152286, 1.052583, -0.204868, 0.114503, 0.786281, 0.099216,
                   -0.003882, -0.048116, 1.051998),
    "deuteranopia": (0.367322, 0.860646, -0.227968, 0.280085, 0.672501, 0.047413,
                     -0.011820, 0.042940, 0.968881),
    "tritanopia": (1.255528, -0.076749, -0.178779, -0.078411, 0.930809, 0.147602,
                   0.004733, 0.691367, 0.303900),
    "gray": (0.2126, 0.7152, 0.0722) * 3,
}
def lin(c):
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
def enc(v):
    v = min(max(v, 0), 1)
    return round(255 * (12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055))
bad = 0
for kind, m in VISION.items():
    got = open(f"{sys.argv[1]}/v-{kind}.txt").read().split()
    for rgb, g in zip([(255, 0, 0), (0, 255, 0), (0, 0, 255), (128, 128, 128)], got):
        want = [enc(sum(m[r * 3 + i] * lin(c) for i, c in enumerate(rgb))) for r in range(3)]
        if any(abs(w - int(g[1 + 2 * r:3 + 2 * r], 16)) > 2 for r, w in enumerate(want)):
            print(f"  {kind}: {rgb} is {g}, not {want}")
            bad += 1
sys.exit(bad)
PY
"$sb" run -- sh -c "'$here/window.py' vis & '$sb' wait window vis && '$sb' wait stable --window vis &&
  '$sb' capture '$tmp/vis.png' --window vis && '$sb' capture '$tmp/vis-d.png' --window vis --vision deuteranopia" \
  && "$sb" vision "$tmp/vis.png" "$tmp/vis-d2.png" --as deuteranopia >/dev/null \
  && cmp -s "$tmp/vis-d.png" "$tmp/vis-d2.png" && ok "capture --vision: the picture, as seen" \
  || fail "capture --vision"

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
