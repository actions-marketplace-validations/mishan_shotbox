#!/bin/sh
# shotbox's own tests: the sealed session, the terminal, the pictures, and
# the Node helpers. Needs what shotbox needs: Xvfb, xauth, dbus-daemon,
# ImageMagick, and PyGObject with VTE for the terminal.
set -eu
# SHOTBOX_PART=NAME runs only what follows each "#: part NAME", besides
# what's before the first part and after "#: end": CI runs each part at
# once, on runners of their own.
if [ -n "${SHOTBOX_PART-}" ]; then
  p=$SHOTBOX_PART; unset SHOTBOX_PART
  grep -qx "#: part $p" "$0" || { echo "no part $p in $0" >&2; exit 2; }
  exec sh -c "$(awk -v p="$p" '/^#: part / { parted = 1; on = $3 == p; next }
    /^#: end$/ { on = 1; next } !parted || on' "$0")" "$0"
fi
here=$(cd "$(dirname "$0")" && pwd)
sb=$here/../bin/shotbox
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fails=0
ok() { echo "ok    $1"; }
fail() { echo "FAIL  $1"; fails=$((fails + 1)); }
audio() { command -v pipewire >/dev/null && command -v wireplumber >/dev/null && command -v pactl >/dev/null; }
atspi() { python3 -c 'import gi; gi.require_version("Adw", "1"); gi.require_version("Atspi", "2.0")' 2>/dev/null \
  && { [ -x /usr/libexec/at-spi-bus-launcher ] || [ -x /usr/lib/at-spi2-core/at-spi-bus-launcher ]; }; }
app=$here/a11y_app.py

#: part core
# One version: the Python package's, the Node package's and its lockfile's,
# the MCP registry's entry, and the changelog's newest release.
root=$here/..
py=$(python3 -c "import sys; sys.path.insert(0, '$root'); import shotbox; print(shotbox.__version__)")
js=$(node -p "require('$root/package.json').version")
lock=$(node -p "require('$root/package-lock.json').version")
log=$(grep -m1 -o '^## [0-9][0-9.]*' "$root/CHANGELOG.md" | cut -c4-)
mcp=$(python3 -c "import json; s = json.load(open('$root/server.json')); print(*{s['version'], *(p['version'] for p in s['packages'])})")
cli=$("$sb" --version)
# The Action's uses: lines in the docs name the release they'd get.
action=$(grep -ho 'mishan/shotbox@v[0-9][0-9.]*' "$root/README.md" "$root"/docs/*.md | sort -u | cut -d@ -f2 | tr '\n' ' ')
[ "$py" = "$js" ] && [ "$js" = "$lock" ] && [ "$lock" = "$log" ] && [ "$log" = "$mcp" ] \
  && [ "$cli" = "shotbox $py" ] && [ "$action" = "v$py " ] \
  && ok "one version, $py, everywhere" || fail "versions: python $py, npm $js, lock $lock, changelog $log, MCP registry $mcp, --version $cli, Action docs $action"

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
# What a command needs and isn't here, said in a line before anything
# starts (nothing made in TMPDIR): a tool not on PATH, and the system's
# PyGObject, from a virtualenv that can't see it: plain `pipx install
# shotbox`'s, which can be reinstalled so it can, or another.
mkdir "$tmp/empty" "$tmp/path"; ln -s "$(command -v python3)" "$tmp/path/python3"
venv=$tmp/pipx/venvs/shotbox; python3 -m venv --without-pip "$venv"
python3 -m venv --without-pip "$tmp/venv"
at_once() { [ $1 != 0 ] && [ "$(wc -l < "$tmp/err")" = 1 ] && grep -q "$2" "$tmp/err" \
  && [ -z "$(ls -A "$tmp/empty")" ]; }
set +e; TMPDIR=$tmp/empty PATH=$tmp/path python3 "$sb" run -- true 2> "$tmp/err"; st=$?; set -e
at_once $st "^shotbox: not found: dbus-daemon" && ok "a tool not found, said before the session starts" \
  || fail "no dbus-daemon ($st): $(cat "$tmp/err")"
reinstall="from the system, which this install can't see; reinstall with: pipx install --force --system-site-packages shotbox"
set +e; TMPDIR=$tmp/empty "$venv/bin/python" "$sb" term --shoot "$tmp/t.png" -- true 2> "$tmp/err"; st=$?; set -e
at_once $st "^shotbox term: needs PyGObject and VTE $reinstall\$" \
  && ok "term, with no PyGObject in sight: how to reinstall, before the session starts" \
  || fail "term with no PyGObject ($st): $(cat "$tmp/err")"
set +e; TMPDIR=$tmp/empty "$tmp/venv/bin/python" "$sb" run --a11y -- true 2> "$tmp/err"; st=$?; set -e
at_once $st "^shotbox: needs PyGObject and AT-SPI from the system, which this virtualenv can't see: make it with --system-site-packages\$" \
  && ok "--a11y, with no PyGObject in sight: a virtualenv that can see it, before the session starts" \
  || fail "--a11y with no PyGObject ($st): $(cat "$tmp/err")"
# uv's tool environments can't be made to see it: pipx's can.
python3 -m venv --without-pip "$tmp/uv/tools/shotbox"
set +e; TMPDIR=$tmp/empty "$tmp/uv/tools/shotbox/bin/python" "$sb" run --a11y -- true 2> "$tmp/err"; st=$?; set -e
at_once $st "^shotbox: needs PyGObject and AT-SPI from the system, which uv's tool environments can't see; install with: pipx install --system-site-packages shotbox\$" \
  && ok "--a11y from uv's tool environment, with no PyGObject in sight: pipx, which can see it" \
  || fail "--a11y from uv with no PyGObject ($st): $(cat "$tmp/err")"
set +e; "$venv/bin/python" "$sb" doctor > "$tmp/out"; st=$?; set -e
[ $st = 0 ] && grep -q "^ok    sessions$" "$tmp/out" && grep -q "^no    term: needs PyGObject and VTE $reinstall\$" "$tmp/out" \
  && ok "doctor: what's here, and what isn't" || fail "doctor ($st): $(cat "$tmp/out")"
# Under bubblewrap: none of the machine's sound devices, and none of the
# desktop's sockets; all of them with --no-sandbox, or SHOTBOX_SANDBOX=off.
look='find /dev/snd "/run/user/$(id -u)" -mindepth 1 -maxdepth 1 2>/dev/null; true'
if bwrap --dev-bind / / true 2>/dev/null && [ -n "$(sh -c "$look")" ]; then
  in=$("$sb" run -- sh -c "$look"); out=$("$sb" run --no-sandbox -- sh -c "$look")
  off=$(SHOTBOX_SANDBOX=off "$sb" run -- sh -c "$look")
  [ -z "$in" ] && [ -n "$out" ] && [ "$off" = "$out" ] \
    && ok "no sound devices or desktop sockets inside, but with --no-sandbox or SHOTBOX_SANDBOX=off" \
    || fail "inside: [$in], with --no-sandbox: [$out], SHOTBOX_SANDBOX=off: [$off]"
  set +e; SHOTBOX_SANDBOX=bogus "$sb" run -- true 2> "$tmp/err"; st=$?; set -e
  [ $st != 0 ] && grep -qx "shotbox: SHOTBOX_SANDBOX is on or off, not 'bogus'" "$tmp/err" \
    && ok "SHOTBOX_SANDBOX=bogus, refused" || fail "SHOTBOX_SANDBOX=bogus ($st): $(cat "$tmp/err")"
  # Run from inside it, where the program wouldn't be: refused, saying so.
  hid="/run/user/$(id -u)/shotbox-test-$$"
  if mkdir "$hid" 2>/dev/null; then
    set +e; (cd "$hid" && "$sb" run -- true) 2> "$tmp/err"; st=$?; set -e
    rmdir "$hid"
    [ $st != 0 ] && grep -q "programs in the session don't see" "$tmp/err" \
      && ok "a program run from where it's hidden, refused" || fail "hidden cwd: $st $(cat "$tmp/err")"
  fi
fi
# The machine's system bus and printers only with --machine-bus: by default
# a bus of the session's own, with nothing on it but itself, no scheduler,
# and under bubblewrap none of the machine's sockets for them.
names='gdbus call --system --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
  --method org.freedesktop.DBus.ListNames | tr -d "[]() '"'"'" | tr , "\n" | grep "^[^:]"'
theirs=$(sh -c "$names" 2>/dev/null | grep -vxm1 org.freedesktop.DBus || true)
if [ -n "$theirs" ]; then
  in=$("$sb" run -- sh -c "$names"); out=$("$sb" run --machine-bus -- sh -c "$names")
  [ "$in" = org.freedesktop.DBus ] && echo "$out" | grep -qx "$theirs" \
    && ok "a system bus of its own, and the machine's ($theirs) with --machine-bus" \
    || fail "system bus names inside: [$in]; with --machine-bus, no $theirs in [$out]"
fi
if command -v lpstat >/dev/null && [ "$(lpstat -r)" = "scheduler is running" ]; then
  in=$("$sb" run -- sh -c 'lpstat -r; lpstat -e'); out=$("$sb" run --machine-bus -- lpstat -r)
  [ "$in" = "scheduler is not running" ] && [ "$out" = "scheduler is running" ] \
    && ok "no printers, and the machine's with --machine-bus" \
    || fail "printers inside: [$in]; with --machine-bus: [$out]"
fi
socks=$(ls -d /run/dbus/system_bus_socket /run/cups/cups.sock 2>/dev/null || true)
if [ -n "$socks" ] && bwrap --dev-bind / / true 2>/dev/null; then
  listed='ls -d "$@" 2>/dev/null; true'
  in=$("$sb" run -- sh -c "$listed" sh $socks); out=$("$sb" run --machine-bus -- sh -c "$listed" sh $socks)
  [ -z "$in" ] && [ "$out" = "$socks" ] \
    && ok "none of the machine's bus and printer sockets inside, but with --machine-bus" \
    || fail "sockets inside: [$in]; with --machine-bus: [$out] of [$socks]"
fi
# --net loopback: one network for all the session's programs, a server one
# starts reached from another and seen by a wait from outside, with only
# loopback on it, and not reached from the machine.
if bwrap --dev-bind / / --unshare-user --unshare-net true 2>/dev/null && command -v nsenter >/dev/null; then
  port=$((20000 + $$ % 10000)); net=net$$
  "$sb" start --name "$net" --net loopback >/dev/null
  "$sb" spawn --session "$net" -- python3 -m http.server $port --bind 127.0.0.1 >/dev/null
  "$sb" --session "$net" wait port $port --timeout 10 >/dev/null \
    && "$sb" spawn --session "$net" --log "$tmp/net.log" -- python3 -c "import urllib.request
print(urllib.request.urlopen('http://127.0.0.1:$port/').status, open('/proc/net/dev').read().count(':'))" >/dev/null
  n=0; until [ -s "$tmp/net.log" ] || [ $n = 50 ]; do sleep 0.1; n=$((n + 1)); done
  python3 -c "import socket, sys; sys.exit(socket.socket().connect_ex(('127.0.0.1', $port)) == 0)" \
    && [ "$(cat "$tmp/net.log")" = "200 1" ] \
    && ok "--net loopback: the session's programs share a network of their own" \
    || fail "--net loopback: $(cat "$tmp/net.log")"
  "$sb" stop "$net"
fi
# --identity: every way of asking who and where says USER and HOST, and no
# group names the real user, for a program run and one spawned later in a
# started session; a bad one, or a user the machine has, refused.
if bwrap --dev-bind / / --unshare-uts --hostname x true 2>/dev/null; then
  ask='echo $(hostname) $(hostname -f) $(cat /etc/hostname) $(uname -n) $(whoami) $(id -un)
echo $(ls -ld "$HOME" | cut -d" " -f3-4) $USER $LOGNAME $HOSTNAME
cut -d: -f4 /etc/group | tr , "\n" | grep -xF "$REAL" | wc -l'
  want=$(printf 'neon neon neon neon doll doll\ndoll doll doll doll neon\n0')
  real="--env REAL=$(id -un)"
  out=$("$sb" run --identity doll@neon $real -- sh -c "$ask")
  idn=id$$; "$sb" start --name "$idn" --identity doll@neon $real >/dev/null
  "$sb" spawn --session "$idn" --log "$tmp/id.log" -- sh -c "$ask" >/dev/null
  n=0; until grep -qx '[0-9]*' "$tmp/id.log" 2>/dev/null || [ $n = 50 ]; do sleep 0.1; n=$((n + 1)); done
  "$sb" stop "$idn"
  set +e; "$sb" run --identity 'doll@neon;x' -- true 2> "$tmp/err"; st=$?
  "$sb" run --identity root@x -- true 2>> "$tmp/err"; st2=$?; set -e
  [ "$out" = "$want" ] && [ "$(cat "$tmp/id.log")" = "$want" ] && [ $st != 0 ] && [ $st2 != 0 ] \
    && grep -q "identity is USER@HOST" "$tmp/err" && grep -q "root is the machine's user 0" "$tmp/err" \
    && ok "--identity: who and where, for every program, and bad ones refused" \
    || fail "--identity: run [$out], spawned [$(cat "$tmp/id.log")], bad ($st $st2) $(cat "$tmp/err")"
fi
# --usage: what a program used, its 300 MB at once among it, and what the
# session's own processes did, said and written as JSON.
"$sb" run --usage "$tmp/used.json" -- python3 -c "import time; x = bytearray(300 * 2**20)
time.sleep(0.5)" 2> "$tmp/err"
python3 -c "import json, sys; u = json.load(open('$tmp/used.json'))
sys.exit(not (u['programs']['peak'] > 300 * 2**20 and u['session']['peak'] > 0
              and u['programs']['how'].split(',')[0] in ('cgroup', 'sampled')))" \
  && grep -q "^shotbox: used, over " "$tmp/err" && ok "--usage: a program's peak memory, and the session's own" \
  || fail "--usage: $(cat "$tmp/err" "$tmp/used.json")"
scratch=$("$sb" run -- sh -c 'echo $SHOTBOX_SCRATCH')
[ ! -e "$scratch" ] && ok "the scratch dir is removed" || fail "left $scratch behind"
set +e; "$sb" run -- sh -c 'exit 7'; st=$?; set -e
[ $st = 7 ] && ok "run exits with the command's status" || fail "run exited $st"
# exec: the same seal with no display, its status, and no display's options.
out=$(DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 SECRET=x \
  "$sb" exec -- sh -c 'echo "D=${DISPLAY-} W=${WAYLAND_DISPLAY-} X=${SECRET-} H=$HOME"')
set +e; "$sb" exec -- sh -c 'exit 5'; st=$?; "$sb" exec --wayland -- true 2>/dev/null; st2=$?
"$sb" exec -- "$sb" capture "$tmp/none.png" 2> "$tmp/err"; st3=$?; set -e
case $out in
  "D= W= X= H=/tmp/shotbox-"*) [ $st = 5 ] && [ $st2 = 2 ] && [ $st3 != 0 ] \
    && grep -q "this session has no display" "$tmp/err" \
    && "$sb" exec -- "$sb" wait file /etc/hostname --timeout 5 \
    && ok "exec: sealed with no display, its command's status; display options and commands refused, a file waited for" \
    || fail "exec: status $st, --wayland $st2, capture $st3: $(cat "$tmp/err")" ;;
  *) fail "exec: $out" ;;
esac
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
# Killed outright, a run's watchdog stops what's left of its session and
# removes its scratch dir: a session's started in it too, and a recording
# is finished first; with --keep, the dir stays. Killed with it, as a whole
# process tree can be, the dir is left for `sessions --stale` to find and
# `--clean` to remove, with what's left running, and a live session's, a
# kept one's and, without --unmarked, an older shotbox's are left be.
st=$tmp/st; mkdir "$st"
up() { i=0; while [ ! -s "$1" ] && [ $i -lt 200 ]; do sleep 0.1; i=$((i + 1)); done; cat "$1"; }
left() {
  for p in $(pgrep -u "$(id -u)" .); do
    { tr '\0' '\n' < "/proc/$p/environ"; } 2>/dev/null | grep -qx "SHOTBOX_SCRATCH=$1" && echo "$p"
  done
  true
}
scratch_to='echo "$SHOTBOX_SCRATCH" > "$1"; exec sleep 600'
until_none() { i=0; while [ -n "$(left "$1")" ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i + 1)); done; }
TMPDIR=$st "$sb" run -- sh -c "$scratch_to" sh "$tmp/killed" & run=$!
d1=$(up "$tmp/killed")
TMPDIR=$st "$sb" run -- "$sb" run -- sh -c "$scratch_to" sh "$tmp/inner" & outer=$!
inner=$(up "$tmp/inner")
TMPDIR=$st "$sb" run --keep -- sh -c "$scratch_to" sh "$tmp/kept" > /dev/null & keeping=$!
dk=$(up "$tmp/kept")
kill -9 $run $outer $keeping; wait $run $outer $keeping 2>/dev/null || true
for d in "$d1" "$inner" "$dk"; do until_none "$d"; done
i=0; while [ -e "$d1" ] || [ -e "$inner" ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i + 1)); done
for d in "$d1" "$inner"; do
  what=$([ "$d" = "$d1" ] && echo "its scratch dir" || echo "a session's started in it")
  [ -n "$d" ] && [ ! -e "$d" ] && [ -z "$(left "$d")" ] \
    && ok "a run killed outright: its watchdog removes $what, and what ran" \
    || fail "a run killed outright left $d: $(left "$d")"
done
[ -n "$dk" ] && [ -d "$dk/home" ] && [ -z "$(left "$dk")" ] \
  && ok "a run with --keep killed outright: what ran stops, the dir stays" \
  || fail "--keep killed outright: $dk, $(left "$dk")"
if command -v ffmpeg >/dev/null; then
  TMPDIR=$st "$sb" run -- sh -c 'echo "$SHOTBOX_SCRATCH" > "$1"; exec "$2" record "$3" --seconds 20' \
    sh "$tmp/rec" "$sb" "$tmp/cut.mp4" > /dev/null & run=$!
  dr=$(up "$tmp/rec"); sleep 3; kill -9 $run; wait $run 2>/dev/null || true
  until_none "$dr"
  case $(ffprobe -v error -show_entries format=duration -of csv=p=0 "$tmp/cut.mp4" 2>&1) in
    [1-9]*) ok "a run killed outright while it records: the clip is finished" ;;
    *) fail "a run killed while it records: $(ffprobe -v error "$tmp/cut.mp4" 2>&1)" ;;
  esac
fi
TMPDIR=$st "$sb" run -- sh -c "$scratch_to" sh "$tmp/killed2" & run=$!
d2=$(up "$tmp/killed2"); watch=$(pgrep -u "$(id -u)" -fx ".* mcp --watch $d2")
kill -9 $run $watch; wait $run 2>/dev/null || true
TMPDIR=$st "$sb" run -- sh -c "$scratch_to" sh "$tmp/live" & live=$!
d3=$(up "$tmp/live")
TMPDIR=$st "$sb" sessions --stale > "$tmp/stale"
TMPDIR=$st "$sb" doctor > "$tmp/doctor1" || true
TMPDIR=$st "$sb" sessions --clean > "$tmp/clean"
TMPDIR=$st "$sb" doctor > "$tmp/doctor2" || true
[ "$(cut -d' ' -f1 "$tmp/stale")" = "$d2" ] && grep -q "  shotbox run -- sh -c .*, pid $run, " "$tmp/stale" \
  && ok "sessions --stale: a run killed with its watchdog, what made it, and not a live one" \
  || fail "sessions --stale: $(cat "$tmp/stale")"
[ ! -e "$d2" ] && [ -z "$(left "$d2")" ] && grep -q "^removed $d2 " "$tmp/clean" \
  && [ -e "$d3" ] && kill -0 $live && [ -e "$dk" ] \
  && ok "sessions --clean: it goes, with what it left running; a live and a kept one stay" \
  || fail "sessions --clean: $(cat "$tmp/clean"); left $(left "$d2")"
grep -q "^no    scratch dirs: 1 stale in $st, [0-9.]* [kM]B (" "$tmp/doctor1" \
  && grep -qx "ok    scratch dirs: none stale in $st" "$tmp/doctor2" \
  && ok "doctor: stale scratch dirs, and their size" \
  || fail "doctor: $(grep 'scratch dirs' "$tmp/doctor1" "$tmp/doctor2")"
kill $live; wait $live 2>/dev/null || true
old=$st/shotbox-0lder123; mkdir "$old"; touch -d '1 hour ago' "$old"
TMPDIR=$st "$sb" sessions --stale > "$tmp/stale"
TMPDIR=$st "$sb" sessions --clean > "$tmp/clean"
TMPDIR=$st "$sb" sessions --clean --unmarked > "$tmp/clean2"
grep -qx "$old  0 kB  made by an older shotbox" "$tmp/stale" && grep -q "^left $old " "$tmp/clean" \
  && grep -q "^removed $old " "$tmp/clean2" && [ ! -e "$old" ] \
  && ok "sessions: an older shotbox's dir, listed, and removed only with --unmarked" \
  || fail "sessions, an unmarked dir: $(cat "$tmp/stale" "$tmp/clean" "$tmp/clean2")"
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
if audio; then
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
  # record OUT.wav: what plays in the sink's format, bit for bit, for
  # --seconds to the sample; none without --audio.
  python3 -c "import random, wave; w = wave.open('$tmp/noise.wav', 'wb')
w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000)
w.writeframes(random.Random(1).randbytes(4 * 24000)); w.close()"
  # Played a second in, half a second long: well inside the three, however
  # slow the recording is to start.
  "$sb" run --audio -- sh -c "'$sb' record '$tmp/heard.wav' --seconds 3 >/dev/null & r=\$!
    sleep 1; timeout 10 paplay '$tmp/noise.wav'; wait \$r" \
    && got=$(python3 -c "import wave; w, n = wave.open('$tmp/heard.wav'), wave.open('$tmp/noise.wav')
print(w.getnframes(), n.readframes(24000) in w.readframes(w.getnframes()))") \
    && [ "$got" = "144000 True" ] \
    && ok "record OUT.wav: the session's sound, bit for bit, for --seconds" || fail "record wav: ${got-}"
  set +e; "$sb" run -- "$sb" record "$tmp/none.wav" --seconds 1 2> "$tmp/err"; st=$?; set -e
  [ $st != 0 ] && grep -q "start the session with --audio" "$tmp/err" \
    && ok "record OUT.wav without --audio: refused, saying so" || fail "wav without audio: $st"
  # listen: a stream that falls behind, heard as the gaps it leaves.
  # A tone in 10 ms blocks, held up a quarter of a second, twice.
  cat > "$tmp/stutter.py" <<'PY'
import math, struct, sys, time
out = sys.stdout.buffer
for block in range(150):
    if block in (50, 100):
        out.flush(); time.sleep(0.25)
    out.write(b"".join(struct.pack("<hh", s, s) for s in (int(8000 * math.sin(2 * math.pi * 440
              * (block * 480 + i) / 48000)) for i in range(480))))
    out.flush(); time.sleep(0.0095)
PY
  "$sb" run --audio -- sh -c "'$sb' record '$tmp/stutter.wav' --seconds 4 >/dev/null & r=\$!
    sleep 0.7; python3 '$tmp/stutter.py' | pacat --format=s16le --rate=48000 --channels=2 \
      --latency-msec=10; wait \$r"
  set +e; "$sb" listen "$tmp/stutter.wav" > "$tmp/heard"; st=$?; set -e
  # A loaded machine may add short gaps of its own: the stalls are long.
  [ $st = 1 ] && [ "$(awk '/^gap at / && $4 >= 150' "$tmp/heard" | wc -l)" = 2 ] \
    && ok "listen: a player that stalls twice, heard as two gaps" || { fail "listen: stalls ($st)"; cat "$tmp/heard"; }
else
  [ "${SHOTBOX_AUDIO-}" = required ] && fail "audio skipped: no PipeWire, WirePlumber or pactl" \
    || echo "skip  audio: no PipeWire, WirePlumber or pactl"
fi
# --clock: the wall clock starts at TIME, in --tz, and runs on, for a
# program started later in a session as for the first. With no libfaketime,
# said in a line, and the rest skipped, unless SHOTBOX_CLOCK=required.
if python3 -c "import sys; sys.path.insert(0, '$root')
from shotbox.session import faketime; faketime()" 2>/dev/null; then
  clk=clock$$; t=$(date -d 2026-09-26T16:40:00Z +%s); s1=0 s2=0 hm=
  "$sb" start --name "$clk" --clock 2026-09-26T18:40:00 --tz Europe/Berlin >/dev/null
  for n in 1 2; do
    [ $n = 1 ] || sleep 3
    "$sb" spawn --session "$clk" --log "$tmp/clock$n" -- date '+%s %H:%M %Z' >/dev/null
    i=0; until [ -s "$tmp/clock$n" ] || [ $i = 50 ]; do sleep 0.1; i=$((i + 1)); done
  done
  "$sb" stop "$clk"
  read -r s1 hm < "$tmp/clock1" || true; read -r s2 _ < "$tmp/clock2" || true
  [ "$hm" = "18:40 CEST" ] && [ $((s1 - t)) -ge 0 ] && [ $((s1 - t)) -le 5 ] \
    && [ $((s2 - s1)) -ge 3 ] && [ $((s2 - s1)) -le 8 ] \
    && ok "--clock: starts at TIME in its zone, and runs on for a program started later" \
    || fail "--clock: $(cat "$tmp/clock1" "$tmp/clock2"), from $t"
  "$sb" term --shoot "$tmp/berlin.png" --size 30x4 --timeout 10 --when '18:40 CEST' \
    --clock 2026-09-26T18:40:00 --tz Europe/Berlin -- date '+%H:%M %Z' >/dev/null \
    && ok "term --shoot --clock: the terminal's program reads it" || fail "term --shoot --clock"
  if audio; then
    timeout 60 "$sb" run --audio --clock 2026-09-26T18:40:00 -- "$sb" record "$tmp/clocked.wav" --seconds 1 >/dev/null \
      && ok "--audio --clock: sound recorded" || fail "--audio --clock: no sound recorded"
  fi
  # shotbox's own waits and holds run off the session's clock, which
  # libfaketime 0.9.10 breaks Python 3.11's sleep on; its programs keep it.
  out=$(timeout 60 "$sb" run --clock 2026-09-26T18:40:00 -- sh -c "'$sb' wait stable --timeout 10 &&
    '$sb' key --hold 0.1 a && date +%F" 2>&1) \
    && [ "$out" = 2026-09-26 ] && ok "--clock: shotbox's own wait and key --hold inside, its program on the clock" \
    || fail "--clock, shotbox inside: $out"
else
  set +e; "$sb" exec --clock 2026-09-26T18:40:00 -- true 2> "$tmp/err"; st=$?; set -e
  [ $st != 0 ] && [ "$(cat "$tmp/err")" = "shotbox: the session's clock needs libfaketime, and there's no libfaketime.so.1 here" ] \
    && ok "--clock with no libfaketime, said in a line" || fail "--clock, no libfaketime ($st): $(cat "$tmp/err")"
  [ "${SHOTBOX_CLOCK-}" = required ] && fail "clock skipped: no libfaketime" \
    || echo "skip  clock: no libfaketime"
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
# The session options reach the session term --shoot starts: only loopback
# on its network, what it used written, and the machine's devices seen with
# --no-sandbox.
if bwrap --dev-bind / / --unshare-user --unshare-net true 2>/dev/null && [ -n "$(sh -c "$look")" ]; then
  "$sb" term --shoot "$tmp/tnet.png" --net loopback --usage "$tmp/tused.json" --log "$tmp/tnet.log" \
    --size 40x4 --when n= -- sh -c 'echo n=$(grep -c : /proc/net/dev).' >/dev/null 2>&1
  "$sb" term --shoot "$tmp/tbox.png" --no-sandbox --log "$tmp/tbox.log" --size 40x4 -- \
    sh -c "$look" >/dev/null
  grep -aq 'n=1\.' "$tmp/tnet.log" && grep -q '"peak"' "$tmp/tused.json" \
    && grep -aqE '/dev/snd|/run/user' "$tmp/tbox.log" \
    && ok "term --shoot passes on --net, --usage and --no-sandbox" \
    || fail "term --shoot: net [$(cat "$tmp/tnet.log")], usage [$(cat "$tmp/tused.json")], no sandbox [$(cat "$tmp/tbox.log")]"
fi

# A window by its app id: WM_CLASS, instance or class.
"$sb" shoot "$tmp/app1.png" --window app=window.py -- "$here/window.py" t1 >/dev/null
"$sb" shoot "$tmp/app2.png" --window app=Window.py -- "$here/window.py" t1 >/dev/null
[ "$(identify -format %wx%h "$tmp/app1.png")" = 200x120 ] && cmp -s "$tmp/app1.png" "$tmp/app2.png" \
  && ok "a window by its app id" || fail "app="
set +e; "$sb" shoot "$tmp/app3.png" --window app=nope --timeout 1 -- "$here/window.py" t1 2> "$tmp/err"
st=$?; set -e
[ $st != 0 ] && grep -q "window with app id 'nope'" "$tmp/err" && ok "app=: a miss says what it looked for" \
  || fail "app= miss: $st"

# --twice: a window drawn the same each time, kept; a clock, refused,
# saying where it differs, with the diff beside OUT and no OUT.
"$sb" shoot "$tmp/new/twice.png" --twice --window t1 -- "$here/window.py" t1 >/dev/null \
  && [ -s "$tmp/new/twice.png" ] && ok "shoot --twice: the same twice, kept, its directory made" \
  || fail "shoot --twice"
set +e; "$sb" term --shoot "$tmp/clock.png" --twice --size 30x5 --when . -- date +%N 2> "$tmp/err"
st=$?; set -e
[ $st = 1 ] && grep -q "pixels differ, in [0-9]*x[0-9]* at [0-9]*,[0-9]*; see .*clock-diff.png" "$tmp/err" \
  && [ -s "$tmp/clock-diff.png" ] && [ ! -e "$tmp/clock.png" ] \
  && ok "term --shoot --twice: a clock differs, and where" || fail "--twice, a clock: $st $(cat "$tmp/err")"
# --twice and carets, each given the keyboard before the picture: GTK's
# doesn't blink in a session, and passes; one drawn on a timer of its own,
# every 530 ms, blinks, said with where and how often, and so does Qt 5's,
# or Qt 6's before 6.5, which nothing a session sets reaches; a later Qt
# 6's doesn't.
cat > "$tmp/qt.py" <<'EOF'
import importlib, sys
qt = importlib.import_module(sys.argv[1] + ".QtWidgets")
app = qt.QApplication(sys.argv)
edit = qt.QLineEdit()
edit.setWindowTitle("qt")
edit.show()
app.exec()
EOF
caret() {
  w=$1; shift
  set +e; "$sb" shoot "$tmp/caret-$w.png" --twice --window "$w" --wait ready -- sh -c "$* &
    '$sb' wait window '$w' >/dev/null && '$sb' focus '$w' && touch \"\$SHOTBOX_SCRATCH/ready\"; wait" \
    > /dev/null 2> "$tmp/err"; st=$?; set -e
}
blinks="but a cursor (or something) blinks at [0-9]*x[0-9]*[+][0-9]*[+][0-9]* every [3-6][0-9][0-9] ms"
caret t1 "'$here/window.py' t1 '$tmp/typed'"
[ $st = 0 ] && ok "--twice: GTK's caret doesn't blink" || fail "--twice, GTK's caret: $st $(cat "$tmp/err")"
caret drawn "'$here/window.py' drawn caret 530"
[ $st = 1 ] && grep -q "$blinks" "$tmp/err" && grep -q " at [0-9]*x[0-9]*[+]48[+]" "$tmp/err" \
  && [ ! -e "$tmp/caret-drawn.png" ] && ok "--twice: a caret blinking on its own timer, said" \
  || fail "--twice, a drawn caret: $st $(cat "$tmp/err")"
for qt in PyQt5 PyQt6; do
  if ! python3 -c "import $qt.QtWidgets" 2>/dev/null; then echo "skip  --twice, $qt's caret: no $qt"; continue; fi
  caret qt "python3 '$tmp/qt.py' $qt"
  v=$(python3 -c "from $qt.QtCore import QT_VERSION_STR; print(QT_VERSION_STR)")
  case $v in 5.*|6.[0-4].*) want=1 ;; *) want=0 ;; esac
  case $want$st in
    11) grep -q "$blinks" "$tmp/err" && ok "--twice: Qt $v's caret blinks, said" || fail "--twice, Qt $v: $(cat "$tmp/err")" ;;
    00) ok "--twice: Qt $v's caret doesn't blink" ;;
    *) fail "--twice, Qt $v's caret: $st $(cat "$tmp/err")" ;;
  esac
done
# GSettings: the system's defaults, its vendor's overrides included, but
# for the caret's blink and animations, off; a seed's keyfile merged in,
# its other groups kept, but linked out, never written.
dump="gsettings list-recursively org.gnome.desktop.interface | sort"
if command -v gsettings >/dev/null && GSETTINGS_BACKEND=memory sh -c "$dump" 2>/dev/null | grep -q .; then
  mkdir -p "$tmp/gs/.config/glib-2.0/settings"
  printf "[org/gnome/TextEditor]\nspellcheck=false\n\n[org/gnome/desktop/interface]\nfont-name='Mine 9'\ncursor-blink=true\n" > "$tmp/gs.keyfile"
  cp "$tmp/gs.keyfile" "$tmp/gs.orig"
  ln -s "$tmp/gs.keyfile" "$tmp/gs/.config/glib-2.0/settings/keyfile"
  GSETTINGS_BACKEND=memory LC_ALL=C.UTF-8 sh -c "$dump" \
    | sed -E -e 's/^(org.gnome.desktop.interface (cursor-blink|enable-animations)) true$/\1 false/' \
        -e "s/^(org.gnome.desktop.interface font-name) .*/\\1 'Mine 9'/" \
    | sort > "$tmp/gs.want"
  "$sb" run --seed "$tmp/gs" -- sh -c "$dump; sed -n '/TextEditor/,+1p' ~/.config/glib-2.0/settings/keyfile" > "$tmp/gs.got"
  printf '[org/gnome/TextEditor]\nspellcheck=false\n' >> "$tmp/gs.want"
  cmp -s "$tmp/gs.want" "$tmp/gs.got" && cmp -s "$tmp/gs.keyfile" "$tmp/gs.orig" \
    && ok "GSettings: the system's defaults, a seed's keyfile over them, but for the caret's blink and animations" \
    || fail "GSettings: $(diff "$tmp/gs.want" "$tmp/gs.got"; diff "$tmp/gs.orig" "$tmp/gs.keyfile")"
fi

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

# match: the window as it was pictured, how long that took; one the same
# size that looks different, given up on; another size, said; and options
# it can't use, refused. change: from when the wait starts.
"$sb" run -- sh -c "'$here/window.py' t1 &
  '$sb' wait match '$tmp/app1.png' --window t1" > "$tmp/out" \
  && grep -qx '[0-9.]*s' "$tmp/out" && ok "match waits for a window to look like a picture" \
  || fail "match: $(cat "$tmp/out")"
set +e
"$sb" run -- sh -c "'$here/window.py' t1 '$tmp/entry.log' &
  '$sb' wait match '$tmp/app1.png' --window t1 --timeout 2" 2> "$tmp/err"
st=$?
"$sb" run -- sh -c "'$here/window.py' t1 &
  '$sb' wait match '$tmp/app1.png' --window t1 --crop 10x10+0+0 --timeout 1" 2> "$tmp/err2"
st2=$?
"$sb" run -- sh -c "'$here/window.py' t1 &
  '$sb' wait change --window t1 --crop 300x10+0+0 --timeout 1" 2> "$tmp/err3"
st3=$?
bad=$("$sb" run -- sh -c "for o in '--fuzz 5' '--fuzz 150%' '--max -1' '--crop 10x10' '--crop 10x10+5000+0' '--ignore 0x5+0+0'; do
  '$sb' wait change \$o --timeout 1 2>&1 && echo took; done")
set -e
[ $st != 0 ] && grep -q "window 't1' to match" "$tmp/err" && ! grep -q "picture taken" "$tmp/err" \
  && [ $st2 != 0 ] && grep -q "app1.png is 200x120, the picture taken 10x10" "$tmp/err2" \
  && [ $st3 != 0 ] && grep -q "the crop isn't inside the window, 200x120" "$tmp/err3" \
  && ! echo "$bad" | grep -q took && [ "$(echo "$bad" | grep -c "percentage\|0 or more\|WIDTHxHEIGHT\|isn't inside")" = 6 ] \
  && ok "match gives up on a picture it doesn't look like, saying sizes that differ; bad options refused" \
  || fail "match miss: $st $(cat "$tmp/err") $st2 $(cat "$tmp/err2") $st3 $(cat "$tmp/err3") $bad"
"$sb" run -- sh -c "
  '$sb' term --size 30x5 -- bash --norc -c 'until [ -e $tmp/go ]; do sleep 0.1; done; echo go; sleep 60' &
  '$sb' wait window shotbox-term && '$sb' wait stable --window shotbox-term || exit 1
  { sleep 1; touch '$tmp/go'; } &
  '$sb' wait change --window shotbox-term" > "$tmp/out" \
  && awk '{ exit !($1 + 0 >= 0.5) }' "$tmp/out" && ok "change waits for the screen to change, and says when" \
  || fail "change: $(cat "$tmp/out")"
# --ignore: a patch on the picture the window lacks, matched once each
# pixel of it is left out, by one box or two, or one past the edge.
convert "$tmp/app1.png" -fill red -draw 'rectangle 5,5 14,14' "$tmp/patched.png"
"$sb" run -- sh -c "'$here/window.py' t1 & '$sb' wait window t1 >/dev/null
  for e in 'yes --ignore 10x10+5+5' 'yes --ignore 6x10+5+5 --ignore 4x10+11+5' \
           'yes --ignore 300x300+0+0' 'no --ignore 9x10+5+5'; do
    set -- \$e; want=\$1; shift
    '$sb' wait match '$tmp/patched.png' --window t1 --timeout 1 \"\$@\" >/dev/null 2>&1 && got=yes || got=no
    [ \$got = \$want ] || echo \"\$e: \$got\"
  done" > "$tmp/out" 2>&1 && [ ! -s "$tmp/out" ] \
  && ok "match leaves out what --ignore says" || fail "match --ignore: $(cat "$tmp/out")"
# --margin: a part of the window, looked for that far from the crop's
# place, at the window's edge too; with a patch on it left out.
convert "$tmp/app1.png" -crop 60x40+20+30 +repage "$tmp/part.png"
convert "$tmp/part.png" -fill red -draw 'point 5,5' "$tmp/part-patched.png"
"$sb" run -- sh -c "'$here/window.py' t1 & '$sb' wait window t1 >/dev/null
  for e in 'at 20,30:part 60x40+20+30 3' 'at 20,30:part 60x40+23+28 3' 'no:part 60x40+23+28 2' \
           'at 20,30:part 60x40+0+0 30' 'no:part-patched 60x40+21+31 1' \
           'at 20,30:part-patched 60x40+21+31 1 --ignore 1x1+5+5'; do
    want=\${e%%:*}; set -- \${e#*:}; ref=\$1 crop=\$2 margin=\$3; shift 3
    got=\$('$sb' wait match '$tmp/'\$ref.png --window t1 --timeout 1 --crop \$crop --margin \$margin \"\$@\" 2>/dev/null) \\
      && got=\${got#* } || got=no
    [ \"\$got\" = \"\$want\" ] || echo \"\$e: \$got\"
  done" > "$tmp/out" 2>&1 && [ ! -s "$tmp/out" ] \
  && ok "match finds a picture up to --margin from its place, and says where" \
  || fail "match --margin: $(cat "$tmp/out")"

# line: one written after the wait starts, not one begun before, and only
# once it ends; from the top with --from-start; in a file that comes later,
# one rotated, and one written again from the top, shorter or longer. Given
# up on, with a picture; a directory, said.
printf 'beacon_claimed 0\nold ' > "$tmp/game.log"
set +e
out=$("$sb" exec -- sh -c "
  { sleep 1; printf 'beacon_claimed\nbeacon_cl' >> '$tmp/game.log'; sleep 0.5; echo 'aimed 1' >> '$tmp/game.log'; } &
  '$sb' wait line beacon_cl '$tmp/game.log'
  '$sb' wait line 'beacon_\w+ 0' '$tmp/game.log' --from-start
  { sleep 1; echo defense_started > '$tmp/new.log'; } &
  '$sb' wait line defense '$tmp/new.log'
  { sleep 1; mv '$tmp/new.log' '$tmp/old.log'; echo defense_ended > '$tmp/new.log'; } &
  '$sb' wait line defense '$tmp/new.log'
  { sleep 1; echo wave_1 > '$tmp/new.log'; } &
  '$sb' wait line wave '$tmp/new.log'
  { sleep 1; printf 'wave_2\nlonger than what was there before\n' > '$tmp/new.log'; } &
  '$sb' wait line wave '$tmp/new.log'
  '$sb' wait line x '$tmp' --timeout 1 2>&1")
"$sb" run -- "$sb" wait line nothing "$tmp/game.log" --timeout 1 --failed "$tmp/line.png" 2> "$tmp/err"
st=$?; set -e
[ "$(echo $out)" = "beacon_claimed 1 beacon_claimed 0 defense_started defense_ended wave_1 wave_2 shotbox: can't read $tmp: Is a directory" ] \
  && [ $st != 0 ] && grep -q "a line matching 'nothing'" "$tmp/err" && [ -s "$tmp/line.png" ] \
  && ok "line: a new one, whole, in a file that comes later or is replaced; given up on, with a picture; a directory, said" \
  || fail "line: $out; $st $(cat "$tmp/err")"

# latency: a window that redraws 100 ms after the pointer comes or goes,
# measured at that, and no less.
"$sb" run -- sh -c "'$here/window.py' slow slow 100 & '$sb' wait window slow >/dev/null &&
  '$sb' wait stable >/dev/null &&
  '$sb' latency move 100 60 300 300 --watch 40x40+20+20 --window slow --times 4" > "$tmp/out" \
  && awk '{ exit !($2 >= 100 && $2 < 200) }' "$tmp/out" \
  && ok "latency: a window 100 ms slow to follow the pointer, measured so" || fail "latency: $(cat "$tmp/out")"

#: part input
# flicker: a square switching green and black every 30 ms flickers, at
# that; one that blinks every half second, as a caret does, doesn't; one
# black for 40 ms, once a second, flashed; one black between frames of a
# scene, the same or moved on each time (bzflag's strip), or a band of
# three columns at a tile's edge doing that beside still text, or one,
# flickers, said in the band's own colors, and so does a strip whose black
# is a dark gray with noise in it, one with something passing through most
# of its tile, one beside a scene moving on all along, and one over plain
# gray beside a square changing, two grays 6% apart, and stripes
# swapping, each the other moved across, not scrolling; an X flashing
# white flashes; and a still window, a moving square, slowly or through
# a tile in a picture or stepping past tiles to a sliver of it at the
# edge, text scrolling past, close, by halves of a pixel, or spaced, a
# listing's lines, or pages of them between blanks, a line's steps
# crawling, an edge between two
# faces 8% apart sweeping past, two poles alike crossing, a line missing
# for a picture or a step of it a row lower, and a terminal's page drawn
# anew, its caret gone with it, none; and a panel with a word in it flashing
# white, beside a caret and a spinner, flashes. Each one's sheet is in
# --save's directory, named in its line by what it is, of a size to see,
# and nothing else is; the flash's three pictures, one above another, a
# border above and below each; and without --save, none is said.
sheets() {
  [ -d "$1" ] || { echo "$2" | grep -q '^fl.*, from [0-9.]*s$' && ! echo "$2" | grep -q "shown in"; return; }
  echo "$2" | sed -n 's/^\(fl[a-z]*\): \([^,]*\),.*, shown in \(.*\)/\1 \2 \3/p' > "$tmp/sheets"
  [ "$(ls "$1" | wc -l)" = "$(wc -l < "$tmp/sheets")" ] || return 1
  while read -r kind box sheet; do
    case $sheet in "$1"/[1-9]-$kind-$box.png) ;; *) return 1 ;; esac
    identify -format "%w %h" "$sheet" | awk '{ exit !($1 >= 100 && $2 >= 100 && $1 <= 1920 && $2 <= 1920) }' \
      || return 1
  done < "$tmp/sheets"
}
flickered=""
for c in "flip 30 1 ^flicker: 200x120[+]0[+]0, .* every [1-5][0-9] ms, (#000000 and #009900|#009900 and #000000)" "flip 500 0" \
         "flash 40 1 ^flash: 200x120[+]0[+]0, #000000 for [3-6][0-9] ms in #009900" "still - 0" \
         "move - 0" "scroll - 0" "smooth - 0" "spaced - 0" "strip - 1 ^flicker: 200x120[+]0[+]0, .* ms, #00.* and #00" \
         "scene - 1 ^flicker: 200x120[+]0[+]0, .* every [1-5][0-9] ms" \
         "band 3 1 ^flicker: 16x120[+]176[+]0, .* every [1-5][0-9] ms, .*#000000" \
         "band 1 1 ^flicker: 16x120[+]176[+]0, " "noisy - 1 ^flicker: 200x120[+]0[+]0, .* every [1-5][0-9] ms" \
         "fast - 0" "panel - 1 ^flash: .*, #ffffff for [2-6][0-9] ms in " "text - 0" "pages - 0" \
         "line - 0" "gradient - 0" "passer - 1 ^flicker: 16x120[+]176[+]0, " "skip - 0" \
         "grating - 1 ^flicker: 16x120[+]0[+]0, .* every [1-5][0-9] ms" \
         "edge - 1 ^flicker: 16x120[+]176[+]0, " "gray - 1 ^flicker: 200x120[+]0[+]0, " "poles - 0" \
         "busy - 1 ^flicker: 16x120[+]176[+]0, " "icon - 1 ^flash: 16x16[+]64[+]48, #ffffff" \
         "dropout - 0" "redraw - 0" "stair - 0"; do
  set -f; set -- $c; set +f
  pat=; [ $# -le 3 ] || pat=${c#"$1 $2 $3 "}
  save="--save '$tmp/fl-$1-$2'"; [ $1 != gray ] || save=
  set +e; out=$("$sb" run -- sh -c "'$here/window.py' fl $1 $2 & '$sb' wait window fl >/dev/null &&
    '$sb' flicker --window fl --seconds 1.6 $save"); st=$?; set -e
  [ $st = "$3" ] && { [ -z "$pat" ] || echo "$out" | grep -Eq "$pat"; } && sheets "$tmp/fl-$1-$2" "$out" \
    && { [ "$1" != flash ] || [ "$(convert "$tmp"/fl-flash-40/*.png -crop 1x+1+0 -depth 8 txt:- \
                                   | grep -c '#7F7F7F')" = 6 ]; } \
    || flickered="$flickered $1 $2 ($st: $out)"
done
[ -z "$flickered" ] && ok "flicker: switching every 30 ms, not every 500; a flash; a strip; not a still window, motion, scrolling, a line or an edge; a sheet of each" \
  || fail "flicker:$flickered"

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
# How long the key a was held in LOG, in hundredths of a second, from its
# first press: one held past the repeat delay, on a busy machine, repeats.
held_for() {
  awk '$2 == "a" && !($1 in t) { t[$1] = $3 } END { printf "%d", (t["release"] - t["press"]) * 100 }' "$1"
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
  && ok "key and click --hold: held together, let go last first" \
  || fail "hold: $got(a for $(held_for "$tmp/held.log")0 ms)"
# down LOG [SESSION OPTIONS]: --down and --up, a key left down between two
# commands: X11's server keeps it, and on Wayland the session's keyboard;
# and Shift left down alone shifts the next command's key.
down() {
  log=$1; shift
  : > "$log"
  "$sb" run "$@" -- sh -c "'$here/held.py' held '$log' &
    '$sb' wait window held && '$sb' wait stable --window held &&
    '$sb' click 50 50 --window held && '$sb' key --down w && sleep 0.3 && '$sb' key --up w &&
    '$sb' key --down shift && '$sb' key a && '$sb' key --up shift && sleep 0.3" &&
    awk '$2 == "w" && !($1 in t) { t[$1] = $3 } END { exit !(t["release"] - t["press"] >= 0.25) }' "$log" &&
    grep -q '^press A ' "$log"
}
down "$tmp/down.log" && ok "key --down, then --up: held between commands, Shift alone too" || fail "--down, --up: $(tr '\n' ' ' < "$tmp/down.log")"
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

# play: a script typed into an entry, ${NAME} from the session's --env,
# its words split as the shell splits them; every line checked before the
# first starts, so one that's wrong is said by its number with nothing
# done; a step that fails says its line, leaves its picture, and stops
# what runs behind it, at once; and one behind that fails stops the script.
cat > "$tmp/play" <<'EOF'
wait window entry --timeout 10   # a comment

focus entry
click 100 60 --window entry
type ${WORD}#'&'
key BackSpace
type '${WORD}'
EOF
printf 'key a\ntype ${NOPE}\n' > "$tmp/play-unset"
printf 'click 1 1 --hold 10 &\n0.2 wait window nope --timeout 0.3 --failed %s\n' \
  "$tmp/play-failed.png" > "$tmp/play-fails"
printf 'wait window nope --timeout 0.3 &\n10 park\n' > "$tmp/play-behind"
"$sb" run --env WORD=hello -- sh -c "'$here/window.py' entry '$tmp/entry.log' &
  '$sb' play '$tmp/play' && sleep 0.3 && ! '$sb' play '$tmp/play-unset' 2> '$tmp/play.err' &&
  for f in fails behind; do s=\$(date +%s%N) && ! '$sb' play '$tmp/play-'\$f 2>> '$tmp/play.err' &&
    echo \$(((\$(date +%s%N) - s) / 1000000)) >> '$tmp/play.ms' || exit 1; done" \
  && [ "$(cat "$tmp/entry.log")" = 'hello#${WORD}' ] \
  && grep -qx "shotbox: $tmp/play-unset:2: \${NOPE} isn't set" "$tmp/play.err" \
  && grep -q "^shotbox: $tmp/play-fails:2: gave up waiting .*; the screen then: $tmp/play-failed.png$" "$tmp/play.err" \
  && grep -qx "shotbox: $tmp/play-behind:1: gave up waiting for a window named 'nope' after 0.3s; there are: \"entry\"" "$tmp/play.err" \
  && [ -s "$tmp/play-failed.png" ] && [ "$(sort -n "$tmp/play.ms" | tail -1)" -lt 3000 ] \
  && ok "play: a script typed in, \${NAME} set; checked first; a step failing, by its line, stops what's behind it, or behind, the script, saying the windows there are" \
  || fail "play: $(cat "$tmp/entry.log" "$tmp/play.err" "$tmp/play.ms" 2>/dev/null)"
# Its clock, in a lasting session, the script from stdin: a key at 1 s,
# the next 0.5 s after it; a wait (for a key pressed behind it at 2.5 s)
# holds back the + step after it, but not the step at 3.5 s; and a
# recording behind them all, longer than they are, waited for.
pl=play$$
"$sb" start --name "$pl" --env KEY=c >/dev/null
"$sb" spawn --session "$pl" -- "$here/held.py" held "$tmp/play-held.log" >/dev/null
"$sb" --session "$pl" wait window held --timeout 10
command -v ffmpeg >/dev/null && rec="record '$tmp/play.mp4' --seconds 4 &" || rec=
s=$(date +%s%N)
printf '%s\n' "click 50 50 --window held" "$rec" "1 key a" "+0.5 key b" "2.5 key e &" \
  "wait line --from-start '^press e ' '$tmp/play-held.log'" "+0.5 key \${KEY}" "3.5 key d" \
  | "$sb" --session "$pl" play - > /dev/null && ms=$((($(date +%s%N) - s) / 1000000)) || ms=
"$sb" stop "$pl" >/dev/null
got=$(awk '$1 == "press" { t[$2] = $3 } END { b = t["b"] - t["a"]; c = t["c"] - t["e"]
  d = t["d"] - t["a"]; printf "%.3f %.3f %.3f", b, c, d
  exit !(b > 0.4 && b < 0.6 && c > 0.45 && c < 0.75 && d > 2.4 && d < 2.6) }' "$tmp/play-held.log") \
  && [ -n "$ms" ] && { [ -z "$rec" ] || { [ $ms -ge 4000 ] && [ "$(ffprobe -v error \
    -show_entries format=duration -of csv=p=0 "$tmp/play.mp4" | cut -c1)" = 4 ]; }; } \
  && ok "play: on one clock, from stdin in a lasting session; a wait holds back + steps; a recording behind them, waited for" \
  || fail "play's clock: b-a, c-e, d-a $got, ${ms:-failed} ms"

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

#: part a11y
# a11y contrast's search for GTK 4's text, on made-up pictures.
set +e; python3 "$here/text_test.py" > "$tmp/text.log" 2>&1; st=$?; set -e
cat "$tmp/text.log"
fails=$((fails + $(grep -c '^FAIL' "$tmp/text.log" || true)))
[ $st = 0 ] || grep -q "^FAIL" "$tmp/text.log" || fail "text_test.py: status $st"

# Accessibility: a session's own AT-SPI bus, the tree, lint, the Tab
# order and announcements, against a libadwaita window. They need GTK 4,
# libadwaita and at-spi2-core, or they skip, unless SHOTBOX_A11Y=required.
if atspi; then
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
  "$sb" run --a11y -- sh -c "'$here/window.py' fields fields & '$sb' a11y lint --window fields &&
    '$sb' a11y click Name: --window fields >/dev/null && sleep 0.5 &&
    '$sb' a11y tree --window fields" > "$tmp/lint" && ! grep -q button "$tmp/lint" \
    && grep -q '^ *text "Name:" \[focusable, focused, editable\]$' "$tmp/lint" \
    && ok "a11y: lint, tree and click, GTK 3: a field named by its label, a bar not revealed left out" \
    || { fail "a11y: lint, GTK 3 fields"; cat "$tmp/lint"; }
  set +e
  "$sb" run --a11y -- sh -c "'$app' & '$sb' a11y lint --window a11y-test --ignore 'image\[' --ignore 'panel with'" > "$tmp/lint"
  st=$?; set -e
  [ $st = 1 ] && [ "$(wc -l < "$tmp/lint")" = 1 ] && grep -q 'button with no name$' "$tmp/lint" \
    && ok "a11y: lint --ignore leaves out what it's told to" || { fail "a11y: lint --ignore ($st)"; cat "$tmp/lint"; }
  "$sb" run --a11y -- sh -c "'$app' & '$sb' a11y click Announce --window a11y-test >/dev/null &&
    '$sb' wait announce 'notes\.md' --timeout 10" \
    && ok "a11y: click finds a button by its name, and clicks it" || fail "a11y: click"
  # A label shown three seconds in is waited for; before it is, a wait
  # gives up saying nothing comes near, or what does. A text view by its
  # text, not its name.
  "$sb" run --a11y -- sh -c "'$app' notes drawing & '$sb' wait window a11y-test >/dev/null &&
    ! '$sb' wait node Drawn --timeout 0.5 && ! '$sb' wait node Saev --window a11y-test --timeout 0.5 &&
    '$sb' wait node '^notes\$' --window a11y-test && '$sb' wait node 'Drawn.*tiles\$' --timeout 10" \
    > "$tmp/node" 2>&1 \
    && grep -qx "shotbox: gave up waiting for a node matching 'Drawn' after 0.5s: nothing in the tree comes near it" "$tmp/node" \
    && grep -qx "shotbox: gave up waiting for a node matching 'Saev' after 0.5s. The nearest: \"Save\"" "$tmp/node" \
    && ok "a11y: wait node waits for a node to show, by its name or text, or says what's near" \
    || { fail "a11y: wait node"; cat "$tmp/node"; }
  # Asked for the text of what has none, the frame, GTK 3 logs a CRITICAL.
  "$sb" run --a11y -- sh -c "'$here/window.py' g3 & '$sb' wait window g3 >/dev/null &&
    '$sb' wait node '^a window\$' --window g3" > "$tmp/node" 2>&1 && ! grep -q CRITICAL "$tmp/node" \
    && ok "a11y: wait node, GTK 3: no CRITICAL for what has no text" \
    || { fail "a11y: wait node, GTK 3"; cat "$tmp/node"; }
  set +e; "$sb" wait node 2>/dev/null; st=$?; "$sb" wait node '' 2>/dev/null; st2=$?; set -e
  [ $st = 2 ] && [ $st2 = 2 ] && ok "a11y: wait node with no regex, or an empty one, is refused" \
    || fail "a11y: wait node with no regex: status $st, $st2"
  # Under a dialog, disabled, moving and under a label, each is said and not
  # clicked (--force clicks the disabled one, which does nothing), then
  # waited for until "Ready" puts each right, and clicked.
  "$sb" run --a11y -- sh -c "set -e; '$app' ready & '$sb' wait window Dialog >/dev/null
    '$sb' a11y tree --window a11y-test >/dev/null
    for b in Behind Soon Moving Covered; do
      ! '$sb' a11y click \$b --window a11y-test --timeout 0.5 >/dev/null || exit 1; done
    '$sb' a11y click Soon --window a11y-test --force >/dev/null
    (sleep 1; '$sb' a11y click Ready --window a11y-test >/dev/null) & ready=\$!
    '$sb' a11y click Soon --window a11y-test >/dev/null; wait \$ready
    for b in Behind Moving Covered; do '$sb' a11y click \$b --window a11y-test >/dev/null; done
    sleep 0.5" > "$tmp/ready" 2>&1 \
    && grep -q '"Behind" \[focusable\] is covered by window "Dialog"$' "$tmp/ready" \
    && grep -q 'gave up after 0.5s: [a-z ]*button "Soon" \[focusable, disabled\] isn.t enabled$' "$tmp/ready" \
    && grep -q '"Moving" \[focusable\] is moving$' "$tmp/ready" \
    && grep -q '"Covered" \[focusable\] is covered by label "Cover"$' "$tmp/ready" \
    && [ "$(grep -v ' ' "$tmp/ready" | tr '\n' ' ')" = "Ready Soon Behind Moving Covered " ] \
    && ok "a11y: click waits for a node enabled, still and uncovered, or says which it isn't" \
    || { fail "a11y: click, not ready"; cat "$tmp/ready"; }
  # A node with no position yet, its window not told from a twin, is waited
  # for, with --force too, and said when it isn't placed in time.
  "$sb" run --a11y -- sh -c "'$app' ready late & '$sb' wait window Dialog >/dev/null &&
    '$sb' a11y tree --window a11y-test >/dev/null &&
    ! '$sb' a11y click Ready --window a11y-test --timeout 0.3 >/dev/null &&
    '$sb' a11y click Ready --window a11y-test --force >/dev/null && sleep 0.5" > "$tmp/late" 2>&1 \
    && grep -q '"Ready" \[focusable\] has no position yet; its actions: click (--action NAME does one)$' "$tmp/late" \
    && grep -qx Ready "$tmp/late" \
    && ok "a11y: click waits for a node to have a position, --force too" \
    || { fail "a11y: click, no position yet"; cat "$tmp/late"; }
  # One that never gets a position is done by the action --action names,
  # in any case, which says so, and still waits for it to be enabled; one
  # with no action says so, and --action without a name lists them.
  "$sb" run --a11y -- sh -c "'$app' ready & '$sb' wait window Dialog >/dev/null &&
    '$sb' a11y tree --window a11y-test >/dev/null &&
    ! '$sb' a11y click Unplaced --window a11y-test --timeout 0.3 >/dev/null &&
    ! '$sb' a11y click Unplaced --window a11y-test --action >/dev/null &&
    '$sb' a11y click Unplaced --window a11y-test --action Click &&
    ! '$sb' a11y click Soon --window a11y-test --action click --timeout 0.3 >/dev/null &&
    ! '$sb' a11y click Saved --window a11y-test --action click >/dev/null &&
    ! '$sb' a11y click Cover --window a11y-test --action click >/dev/null &&
    '$sb' a11y click Cover --window a11y-test --action clipboard.copy && sleep 0.5" > "$tmp/action" 2>&1 \
    && grep -q '"Unplaced" \[focusable\] has no position yet; its actions: click (--action NAME does one)$' "$tmp/action" \
    && grep -q '"Unplaced" \[focusable\] takes an action by its name; its actions: click (--action NAME does one)$' "$tmp/action" \
    && grep -qx '[a-z ]*button "Unplaced" \[focusable\], by its action "click"' "$tmp/action" \
    && grep -qx Unplaced "$tmp/action" \
    && grep -q 'gave up after 0.3s: [a-z ]*button "Soon" \[focusable, disabled\] isn.t enabled$' "$tmp/action" \
    && grep -qx 'shotbox: image "Saved" has no action' "$tmp/action" \
    && grep -q '^shotbox: label "Cover" has no "click"; its actions: clipboard.copy, .* (--action NAME does one)$' "$tmp/action" \
    && grep -qx 'label "Cover", by its action "clipboard.copy"' "$tmp/action" \
    && ok "a11y: click --action NAME does a node's action, with no position, once it's enabled" \
    || { fail "a11y: click --action"; cat "$tmp/action"; }
  set +e
  "$sb" run --a11y -- sh -c "'$app' & '$sb' a11y tabwalk --visible --window a11y-test" > "$tmp/walk"
  st=$?; set -e
  [ $st = 1 ] && [ "$(grep -c 'focus not shown' "$tmp/walk")" = 2 ] \
    && grep -q '^panel "" \[focusable, focused\]  <- focus not shown' "$tmp/walk" \
    && ok "a11y: tabwalk --visible finds the drawing area that shows no focus" \
    || { fail "a11y: tabwalk --visible ($st)"; cat "$tmp/walk"; }
  "$sb" run --a11y --env SHOTBOX_TRUST_GTK4_BOUNDS=1 -- sh -c "'$app' &
    '$sb' a11y contrast --window a11y-test" > "$tmp/contrast" \
    && ok "a11y: contrast passes libadwaita's own" || { fail "a11y: contrast"; cat "$tmp/contrast"; }
  # Rows scrolled out of view are showing, to GTK, and not measured.
  "$sb" run --a11y --env SHOTBOX_TRUST_GTK4_BOUNDS=1 -- sh -c "'$app' long &
    '$sb' a11y contrast --window a11y-test" \
    > "$tmp/contrast" 2>&1 \
    && ok "a11y: contrast leaves out what's scrolled out of view" \
    || { fail "a11y: contrast, scrolled"; cat "$tmp/contrast"; }
  # Scrolled over a row, by its name, the last row comes into the window,
  # then the first again; on Wayland too, where GTK 4 crashes on a scroll
  # whose pointer is gone before its next frame.
  by_name() {
    "$sb" run --a11y "$@" -- sh -c "'$app' long & '$sb' wait window a11y-test >/dev/null &&
      ! '$sb' a11y click 'Row 199' --window a11y-test --timeout 1 2>/dev/null &&
      '$sb' a11y scroll 'Row 0' 150 --window a11y-test >/dev/null &&
      '$sb' wait stable --window a11y-test && '$sb' a11y click 'Row 199' --window a11y-test >/dev/null &&
      '$sb' a11y scroll 'Row 199' -150 --window a11y-test >/dev/null &&
      '$sb' wait stable --window a11y-test && '$sb' a11y click 'Row 0' --window a11y-test >/dev/null"
  }
  by_name && ok "a11y: scroll by a node's name, to the last row and back" || fail "a11y: scroll"
  if command -v sway >/dev/null && command -v grim >/dev/null; then
    by_name --wayland && ok "wayland: a11y: scroll by a node's name, to the last row and back" \
      || fail "wayland: a11y: scroll"
  fi
  # GTK 3 places a window's first child where it is, even at the corner.
  rm -f "$tmp/corner"
  "$sb" run --a11y -- sh -c "'$here/window.py' corner '$tmp/corner' corner &
    '$sb' a11y click Corner --window corner >/dev/null && sleep 0.5" \
    && [ "$(cat "$tmp/corner" 2>/dev/null)" = clicked ] \
    && ok "a11y: click finds a GTK 3 button in its window's corner" || fail "a11y: click, GTK 3 corner"
  # GTK 4 says where its widgets are wrong: their text is measured where
  # it's found drawn near there, at its colors, and what isn't found (a
  # label over two lines, labels drawn letter-spaced or in upper case
  # over an entry holding or showing their text, which isn't taken for
  # theirs; faint, they fail where nothing they can be in could pass, as
  # up to 4.14.5, which has each in its own box), or
  # isn't text, is said not to be, and why. --passes says what passes too,
  # and --json keeps what isn't measured apart from what fails.
  # Told to trust it (a GTK whose bounds are right), it finds faint text
  # and its colors, but not a disabled button's label.
  unfound="not found, but nothing where it can be has the contrast it needs: the best anything there reaches is 2\.5[0-9]:1"
  set +e
  "$sb" run --a11y -- sh -c "'$app' faint lines decoys &
    '$sb' a11y contrast --passes --window a11y-test; s=\$?
    '$sb' a11y contrast --passes --json --window a11y-test > '$tmp/contrast.json' && exit \$s" \
    > "$tmp/contrast" 2> "$tmp/err"
  st=$?; set -e
  [ $st = 1 ] && [ "$(grep -v " not measured: GTK 4 reports its position wrong\|$unfound" "$tmp/contrast" | grep -cv ': text passes at ')" = 1 ] \
    && grep -q ': text passes at [0-9]*\.[0-9][0-9]:1, #[0-9a-f]\{6\} on #[0-9a-f]\{6\} (at least 4\.5)' "$tmp/contrast" \
    && python3 -c "import json, sys; j = json.load(open(sys.argv[1]))
assert [p['problem'][:8] for p in j['problems'] if 'not found' not in p['problem']] == ['text 2.5'], j['problems']
assert all(p['why'] for p in j['problems'] if 'not found' in p['problem'])
assert j['notes'] and all(' not measured: ' in n['note'] and n['why'] for n in j['notes'])
assert j['passes'] and all(p['contrast'].startswith('text passes at ') for p in j['passes'])" "$tmp/contrast.json" \
    && grep -q '/label\[[0-9]*\]: text 2\.5[01]:1, #a0a0a0 on #fafaf[ab] (at least 4\.5); found -\?[0-9]*,-\?[0-9]* px from \(where GTK 4\.[0-9.]* says its text is\|the corner of the box GTK 4\.[0-9.]* says\)$' "$tmp/contrast" \
    && grep -q 'button\[[0-9]*\]/label\[0\]: text not measured: GTK 4 reports its position wrong' "$tmp/contrast" \
    && grep -q 'check box\[[0-9]*\]/label\[[0-9]*\]: text passes at .*; found [0-9],[0-9] px from ' "$tmp/contrast" \
    && [ "$(grep -c "/panel\[[0-9]*\]/label\[0\]: text \(not measured: GTK 4 reports its position wrong\|$unfound\)" "$tmp/contrast")" = 4 ] \
    && grep -q "GTK 4\.[0-9.]* reports where its widgets are wrong, so text is measured where it's found" "$tmp/err" \
    && ok "a11y: contrast measures GTK 4's text where it's found, at its colors, a full-width label's near where GTK says, but not text it can't find or tell from another's; --passes and --json say what passes, and keep notes apart" \
    || { fail "a11y: contrast, GTK 4 text found ($st)"; cat "$tmp/contrast" "$tmp/contrast.json" "$tmp/err"; }
  # A theme's font (a seeded gtk.css), which GTK says, over a grid, so
  # nothing is all one lightness, in a window so big that from 4.14.6
  # each label is looked for in more than 600 by 200 pixels.
  mkdir -p "$tmp/theme/.config/gtk-4.0"
  printf '%s\n' 'label { font-family: monospace; font-weight: bold; font-size: 15px; }' \
    'window { min-width: 640px; min-height: 600px; background-image:' \
    '  repeating-linear-gradient(90deg, rgba(0, 0, 0, 0.05), rgba(0, 0, 0, 0.05) 1px, transparent 1px, transparent 24px),' \
    '  repeating-linear-gradient(0deg, rgba(0, 0, 0, 0.05), rgba(0, 0, 0, 0.05) 1px, transparent 1px, transparent 24px); }' \
    > "$tmp/theme/.config/gtk-4.0/gtk.css"
  set +e
  "$sb" run --a11y --seed "$tmp/theme" -- sh -c "'$app' faint &
    '$sb' a11y contrast --window a11y-test" > "$tmp/contrast" 2>/dev/null
  st=$?; set -e
  [ $st = 1 ] && ! grep -q 'no time left' "$tmp/contrast" \
    && grep -q '/label\[[0-9]*\]: text 2\.5[01]:1, #a0a0a0 on #fafaf[ab] (at least 4\.5); found -\?[0-9]*,-\?[0-9]* px from \(where GTK 4\.[0-9.]* says its text is\|the corner of the box GTK 4\.[0-9.]* says\)$' "$tmp/contrast" \
    && ok "a11y: contrast finds GTK 4's text in a theme's font, over a grid, in a big window" \
    || { fail "a11y: contrast, GTK 4 themed ($st)"; cat "$tmp/contrast"; }
  set +e
  "$sb" run --a11y --env SHOTBOX_TRUST_GTK4_BOUNDS=1 -- sh -c "'$app' faint &
    '$sb' a11y contrast --window a11y-test" > "$tmp/contrast"
  st=$?; set -e
  [ $st = 1 ] && [ "$(wc -l < "$tmp/contrast")" = 1 ] \
    && grep -q '/label\[[0-9]*\]: text 2\.5[01]:1, #a0a0a0 on #fafaf[ab] (at least 4\.5)$' "$tmp/contrast" \
    && ok "a11y: contrast finds faint text, and its colors, but not a disabled button's" \
    || { fail "a11y: contrast, faint ($st)"; cat "$tmp/contrast"; }
  # Behind a modal dialog, libadwaita's in its window or a modal window,
  # the faint text is said apart, and doesn't fail, even with --strict;
  # not beside an error label (an alert), a box with a dialog's role or a
  # popover with one, under a GTK 3 modal popover, or over an alert
  # (a status page under a toast, a label under an overlay), nor for an
  # alert in a layer-like box: after a banner folded away, on a view
  # stack's second page (GTK lists it twice), or laid over the rest.
  # libadwaita 1.5 names its dialog nothing, and shows no title for this
  # one, so it's said by the first name in it, its text view's.
  for over in "$app faint dialog;a11y-test;dialog \"(Reply|Notes)\"" \
              "$app faint modal;a11y-test;dialog \"Over\"" \
              "$app faint warned;a11y-test;" "$app faint inline;a11y-test;" \
              "$app faint popped;a11y-test;" "$app toasted;a11y-test;" \
              "$app overlaid;a11y-test;" "$app faint warned folded;a11y-test;" \
              "$app faint warned paged;a11y-test;" "$app faint laid;a11y-test;" \
              "$here/window.py popover popover;popover;"; do
    cmd=${over%%;*} win=${over#*;} want=${over##*;}
    "$sb" run --a11y -- sh -c "$cmd & '$sb' a11y contrast --strict --json --window ${win%%;*}" \
      > "$tmp/contrast.json" \
      && python3 -c "import json, re, sys; j = json.load(open(sys.argv[1])); want = sys.argv[2]
faint = [n['behind'] for n in j['behind'] if n['name'] == 'Faint words']
assert (len(faint) == 1 and re.fullmatch('text behind a modal ' + want, faint[0])
        and not j['problems'] if want else not j['behind']), j" "$tmp/contrast.json" "$want" \
      && ok "a11y: contrast says what's behind a modal dialog apart, and fails what isn't (${cmd##*/})" \
      || { fail "a11y: contrast, behind a modal dialog (${cmd##*/})"; cat "$tmp/contrast.json"; }
  done
  set +e
  "$sb" run --a11y --env SHOTBOX_TRUST_GTK4_BOUNDS=1 -- sh -c "'$app' &
    '$sb' a11y contrast --edges 3 --window a11y-test" > "$tmp/contrast"
  st=$?; set -e
  [ $st = 1 ] && grep -q '/[a-z ]*button\[0\]: edge 1\.[0-9]*:1' "$tmp/contrast" \
    && ok "a11y: contrast --edges finds buttons with no edge to speak of" \
    || { fail "a11y: contrast --edges ($st)"; cat "$tmp/contrast"; }
  # GTK 3 calls a switch a toggle button; a frame draws a text view's
  # edge; light text, which can measure short of its color (by how much
  # depends on the font), is said to be that color; a large heading needs
  # 3:1; a dot is too small to measure, which passes.
  set +e
  "$sb" run --a11y -- sh -c "'$here/window.py' g3 contrast &
    '$sb' a11y contrast --edges 3 --window g3" > "$tmp/contrast" 2> "$tmp/err"
  st=$?; set -e
  [ $st = 1 ] && grep -q '/label\[1\]: text [0-9]' "$tmp/contrast" \
    && ! grep -q 'toggle button\|/text\[0\]\|/label\[4\]' "$tmp/contrast" \
    && ! grep -q 'GTK None' "$tmp/contrast" "$tmp/err" \
    && grep -q '/label\[5\]: text too small to measure' "$tmp/contrast" \
    && ! grep '/label\[0\]: ' "$tmp/contrast" | grep -qv 'drawn lighter than its color, #5f5670, which is 6\.5[0-9]:1' \
    && "$sb" run --a11y -- sh -c "'$here/window.py' dot dot & '$sb' a11y contrast --window dot" \
      2>/dev/null | grep -q '/label\[0\]: text too small to measure' \
    && ok "a11y: contrast, GTK 3: not a switch, a framed text view, light text, large text, a dot, which passes alone" \
    || { fail "a11y: contrast, GTK 3 ($st)"; cat "$tmp/contrast"; }
  # A faint label dark half a second in two is changing, whenever the
  # first picture's taken, and with --strict fails; dark words sliding by
  # pass, though they've moved out of where they were first seen.
  "$sb" run --a11y -- sh -c "'$here/window.py' mv moving & '$sb' wait window mv >/dev/null
    for i in 1 2 3 4 5; do '$sb' a11y contrast --strict --passes --json --window mv; sleep 0.\$i
    done" > "$tmp/moving.json" \
    && python3 -c "import json, re, sys
runs = [json.loads(line) for line in open(sys.argv[1])]
assert len(runs) == 5, runs
for j in runs:
    said = {n['name']: (k, n.get('problem') or n.get('contrast')) for k in ('problems', 'passes')
            for n in j[k]}
    assert re.fullmatch(r'text changing: 2\.\d\d:1 to 2\d\.\d\d:1 \(at least 4\.5\)',
                        said['Mostly faint words'][1]) and said['Mostly faint words'][0] == 'problems', j
    assert said['Breaking news scrolls by'][0] == 'passes', j" "$tmp/moving.json" \
    && ok "a11y: contrast, GTK 3: a flash said as changing each time, failing with --strict; text moving passes" \
    || { fail "a11y: contrast, GTK 3, a flash and text moving"; cat "$tmp/moving.json"; }
  # Alone and blinking, it's measured however the first picture caught
  # it: changing, so --strict fails each time, never "nothing measured".
  "$sb" run --a11y -- sh -c "'$here/window.py' blink blinking & '$sb' wait window blink >/dev/null
    for i in 1 2 3 4 5; do '$sb' a11y contrast --strict --window blink >/dev/null 2>&1
      printf '%s ' \$?; sleep 0.\$i; done" > "$tmp/blinking" \
    && [ "$(cat "$tmp/blinking")" = "1 1 1 1 1 " ] \
    && ok "a11y: contrast, GTK 3: text blinking alone fails --strict each time" \
    || { fail "a11y: contrast, GTK 3, text blinking alone: $(cat "$tmp/blinking")"; }
  # A window wider than the screen: what's past the screen's edge isn't
  # measured or clicked, and says so; nor is a label whose text is past it.
  "$sb" run --a11y --screen 800x600 -- sh -c "'$here/window.py' wide wide &
    '$sb' a11y contrast --edges 1 --window wide && ! '$sb' a11y click There --window wide --timeout 1 &&
    '$sb' a11y tabwalk --visible --window wide" > "$tmp/contrast" 2>&1 \
    && grep -q '/label\[0\]: text not measured: partly outside the screen$' "$tmp/contrast" \
    && grep -q '[/ ]button\[1\]: text not measured: outside the screen$' "$tmp/contrast" \
    && grep -q '[/ ]button\[1\]: edge not measured: outside the screen$' "$tmp/contrast" \
    && grep -q '"There" \[focusable\] is off-screen$' "$tmp/contrast" \
    && grep -q '"There" \[focusable, focused\]  <- focus not measured: outside the screen$' "$tmp/contrast" \
    && ok "a11y: contrast, click and tabwalk --visible leave out what's past the screen's edge" \
    || { fail "a11y: past the screen's edge"; cat "$tmp/contrast"; }
  # GTK 4.16 and later: thin text, spaced by a fraction of a pixel, drawn
  # at its color, which GTK's automatic font rendering doesn't; and a
  # seeded home that asks for that has it.
  minor=$(python3 -c "import ctypes; print(ctypes.CDLL('libgtk-4.so.1').gtk_get_minor_version())")
  if [ "$minor" -ge 16 ]; then
    mkdir -p "$tmp/auto/.config/gtk-4.0"
    printf '[Settings]\ngtk-font-rendering = automatic\n' > "$tmp/auto/.config/gtk-4.0/settings.ini"
    "$sb" run --a11y --env SHOTBOX_TRUST_GTK4_BOUNDS=1 -- sh -c "'$app' thin &
      '$sb' a11y contrast --text 99 --window a11y-test" > "$tmp/contrast" || true
    "$sb" run --a11y --env SHOTBOX_TRUST_GTK4_BOUNDS=1 --seed "$tmp/auto" -- sh -c "'$app' thin &
      '$sb' a11y contrast --text 99 --window a11y-test" > "$tmp/contrast-auto" || true
    grep -q '/label\[7\]: text 6\.59:1, #5f5670 ' "$tmp/contrast" \
      && grep '/label\[7\]: text ' "$tmp/contrast-auto" | grep -qv '#5f5670 ' \
      && ok "a11y: contrast, GTK 4: thin text at its color, but where a seeded home asks for automatic" \
      || { fail "a11y: contrast, GTK 4 thin text"; grep 'label\[7\]' "$tmp/contrast" "$tmp/contrast-auto"; }
  fi
  # Text found from 4.14: stems thinner than a pixel, which cover none
  # whole on any GTK 4 release, measure at their color, from how much of
  # each pixel they cover, and bold text as its third pixel; light and
  # dark, passing and failing. Not where the shape found doesn't account
  # for every pixel: a size between those shapes are drawn at, or a
  # shadow, which would measure darker than the color (#777777, 4.48:1,
  # as 4.60 and 5.17). A "." and a ",", too few pixels to have a core of
  # their color, at their color too. And a weight CSS gives that Pango
  # doesn't name (450) is looked for, not a crash. A case a line: the
  # label; what's said.
  if [ "$minor" -ge 14 ]; then
    glyphs="Ill|DejaVu Sans|9|400|#5f5670|#ffffff; passes at 6\.88:1, #5f5670 on #ffffff
lIl|DejaVu Sans|9|400|#b0b0b0|#353535; passes at 5\.65:1, #b0b0b0 on #353535
IlI|DejaVu Sans|9|400|#c8c8c8|#ffffff; 1\.67:1, #c8c8c8 on #ffffff
llI|DejaVu Sans|9|400|#5a5a5a|#353535; 1\.7[5-8]:1, #5[9a]5[9a]5[9a] on #353535
Hello|DejaVu Sans|20|700|#767676|#ffffff; passes at 4\.54:1, #767676 on #ffffff
World|DejaVu Sans|20|700|#c8c8c8|#ffffff; 1\.67:1, #c8c8c8 on #ffffff
Hold|DejaVu Sans|20|700|#b0b0b0|#353535; passes at 5\.65:1, #b0b0b0 on #353535
Help|DejaVu Sans|20|700|#5a5a5a|#353535; 1\.77:1, #5a5a5a on #353535
Network|Cantarell|9.5|400|#777777|#ffffff; [1-4]\.[0-9]*:1, #[0-9a-f]* on #ffffff
Settings|Cantarell|9.5|400|#777777|#ffffff|0 1px rgba(0,0,0,0.1); [1-4]\.[0-9]*:1, #[0-9a-f]* on #ffffff
.|DejaVu Sans|13|400|#333333|#ffffff; passes at 12\.63:1, #333333 on #ffffff
,|DejaVu Sans|13|400|#aaaaaa|#ffffff; 2\.32:1, #aaaaaa on #ffffff
Book|DejaVu Sans|13|450|#5f5670|#ffffff; passes at 6\.88:1, #5f5670 on #ffffff"
    labels=$(echo "$glyphs" | sed "s/; .*//; s/.*/'&'/" | tr '\n' ' ')
    "$sb" run --a11y -- sh -c "'$here/gtk4_window.py' glyphs $labels & '$sb' wait window gtk4-glyphs \
      >/dev/null && sleep 1 && '$sb' a11y contrast --passes --window gtk4-glyphs" > "$tmp/contrast" || true
    i=0 wrong=""
    while IFS=';' read -r label said; do
      grep -q "/label\[$i\]: text$said (" "$tmp/contrast" || wrong="$wrong '$label'"
      i=$((i + 1))
    done <<EOF
$glyphs
EOF
    [ -z "$wrong" ] && ok "a11y: contrast, GTK 4: thin stems at their color, but not past what the shape accounts for; bold text as before" \
      || { fail "a11y: contrast, GTK 4 thin and bold text:$wrong"; cat "$tmp/contrast"; }
  fi
  # From 4.14, text that isn't looked for says why: rows scrolled out of
  # view, which GTK calls showing, above the list but in the window, and
  # below the window; and from 4.22.3 a single letter, which GTK gives no
  # width.
  micro=$(python3 -c "import ctypes; print(ctypes.CDLL('libgtk-4.so.1').gtk_get_micro_version())")
  while IFS='|' read -r least args lines want; do
    [ $((minor * 100 + micro)) -ge "$least" ] || continue
    "$sb" run --a11y -- sh -c "'$app' $args & '$sb' a11y contrast --window a11y-test" \
      < /dev/null > "$tmp/contrast" 2>&1 || true
    [ "$(grep -c "$want" "$tmp/contrast")" = "$lines" ] \
      && ok "a11y: contrast, GTK 4: says why it didn't look for text: $args" \
      || { fail "a11y: contrast, GTK 4, why not: $args"; cat "$tmp/contrast"; }
  done <<'CASES'
1400|long down|2|/label\[\(2\|199\)\]: text not measured: not shown (scrolled out of view, or hidden)$
2203|letter|1|: text not measured: GTK 4 reports its position wrong (a single letter: GTK gives it no width)$
CASES
  # From 4.14, faint text over a theme's grid (a seeded gtk.css), whose
  # lines vary as much as the text does: found, and failing. Spaced, not
  # found, but nothing where it can be has the contrast text needs, which
  # fails too; readable and spaced, not found, which doesn't, but for
  # --strict. A case a line: its color, the options, the exit status,
  # what's said.
  if [ "$minor" -ge 14 ]; then
    while IFS='|' read -r look args status want; do
      mkdir -p "$tmp/dim/.config/gtk-4.0"
      printf '%s\n' 'window { background-color: #0f0d14; background-image:' \
        '  repeating-linear-gradient(90deg, #1e1929, #1e1929 1px, transparent 1px, transparent 24px),' \
        '  repeating-linear-gradient(0deg, #1e1929, #1e1929 1px, transparent 1px, transparent 24px); }' \
        "label.faint { $look }" > "$tmp/dim/.config/gtk-4.0/gtk.css"
      set +e
      "$sb" run --a11y --dark --seed "$tmp/dim" -- sh -c "'$app' faint &
        '$sb' a11y contrast $args --window a11y-test" < /dev/null > "$tmp/contrast" 2>/dev/null
      st=$?; set -e
      [ $st = "$status" ] && grep -q "/label\[7\]: text $want" "$tmp/contrast" \
        && ok "a11y: contrast, GTK 4, faint over a grid: $look $args" \
        || { fail "a11y: contrast, GTK 4, faint over a grid: $look $args ($st)"; cat "$tmp/contrast"; }
    done <<'CASES'
color: #3a3446;||1|1\.6[01]:1, #3a3446 on #0f0d14 (at least 4\.5); found -\?[0-9]*,
color: #3a3446; letter-spacing: 3px;||1|not found, but nothing where it can be has the contrast it needs: the best anything there reaches is 1\.[0-9]*:1 (at least 4\.5)$
color: #ebe6f0; letter-spacing: 3px;||0|not measured: GTK 4 reports its position wrong (no match good enough: 0\.[0-9]* at best)$
color: #ebe6f0; letter-spacing: 3px;|--strict|1|not measured: GTK 4 reports its position wrong (no match good enough: 0\.[0-9]* at best)$
CASES
    # Nor does a carousel's page not shown, its label outside the window.
    "$sb" run --a11y -- sh -c "'$app' carousel & '$sb' wait window a11y-test >/dev/null &&
      sleep 1 && '$sb' a11y contrast --window a11y-test" < /dev/null > "$tmp/contrast" 2>/dev/null \
      && ok "a11y: contrast, GTK 4: a carousel's page not shown doesn't fail" \
      || { fail "a11y: contrast, GTK 4, carousel"; cat "$tmp/contrast"; }
    # From 4.14.6, where "check" can be takes in "unchecked" under it:
    # "unchecked" is found, "check" inside it being no match for all of
    # it, and "check", matching in both, isn't, which its line says.
    if [ $((minor * 100 + micro)) -ge 1406 ]; then
      "$sb" run --a11y -- sh -c "'$app' nested & '$sb' a11y contrast --passes --window a11y-test" \
        < /dev/null > "$tmp/contrast" 2>/dev/null
      grep -q '/label\[1\]: text passes at .*; found ' "$tmp/contrast" \
        && grep -q '/label\[0\]: text not measured: GTK 4 reports its position wrong (it matches elsewhere too, in "unchecked")$' "$tmp/contrast" \
        && ok "a11y: contrast, GTK 4: text found though shorter text inside it matches there; the shorter not, said" \
        || { fail "a11y: contrast, GTK 4, check over unchecked"; cat "$tmp/contrast"; }
      # Under "Recheck", whose room takes in both, "check" names the node
      # where its other match is. A hidden "Open File" isn't taken for
      # "Open" and "File" drawn side by side under it: "Open" is drawn
      # only there.
      for args in "nested recheck" split; do
        "$sb" run --a11y -- sh -c "'$app' $args & '$sb' a11y contrast --passes --window a11y-test" \
          < /dev/null 2>/dev/null || true
      done > "$tmp/contrast"
      grep -q '/label\[1\]: text not measured: GTK 4 reports its position wrong (it matches elsewhere too, in "unchecked")$' "$tmp/contrast" \
        && grep -q '/label\[0\]: text not measured: GTK 4 reports its position wrong (another node.s text matches there too: "Open")$' "$tmp/contrast" \
        && ! grep -q '/panel\[7\]/label\[0\]: text passes' "$tmp/contrast" \
        && ok "a11y: contrast, GTK 4: the other match's own node named; text not taken for pieces of it drawn only there" \
        || { fail "a11y: contrast, GTK 4, Recheck and Open File"; cat "$tmp/contrast"; }
      # A faint "Twin" over an entry holding "Twin", or under it, each
      # in the other's room, the entry's text where nothing else can draw
      # it: found where it's drawn, and failing. Nothing else is measured:
      # not a hidden "Twin" over the label, nor, where GTK says what's in
      # a box padded above is over what's laid on its top (over), a label
      # there or a hidden one, with a label, a drawing area or a label
      # named otherwise on top.
      for args in twin "twin under" "twin hidden" over "over hidden" "over hidden drawn" \
          "over drawn" "over named"; do
        "$sb" run --a11y -- sh -c "'$app' $args & '$sb' a11y contrast --passes --window a11y-test" \
          < /dev/null 2>/dev/null || true
      done > "$tmp/contrast"
      grep -q '/panel\[7\]/panel\[0\]/label\[0\]: text 2\.[0-9]*:1, #a0a0a0 on .*; found 0,0 px' "$tmp/contrast" \
        && grep -q '/panel\[7\]/panel\[1\]/label\[0\]: text 2\.[0-9]*:1, #a0a0a0 on .*; found 0,0 px' "$tmp/contrast" \
        && [ "$(grep -c '/panel\[7\]/.*: text \(passes\|[0-9]\)' "$tmp/contrast")" = 2 ] \
        && ok "a11y: contrast, GTK 4: text matching where only another node's text can be is found where it's drawn; not a hidden twin, nor where GTK's box is over it" \
        || { fail "a11y: contrast, GTK 4, Twin"; cat "$tmp/contrast"; }
    fi
    # --strict: a window where nothing's measured but what fails, exit 1
    # with the failure, not 2, "nothing measured".
    set +e
    "$sb" run --a11y -- sh -c "'$app' lone & '$sb' a11y contrast --strict --window a11y-test" \
      < /dev/null > "$tmp/contrast" 2>/dev/null
    st=$?; set -e
    [ $st = 1 ] && grep -q ': text not found, but nothing where it can be' "$tmp/contrast" \
      && ok "a11y: contrast --strict fails what fails, where nothing else is measured" \
      || { fail "a11y: contrast --strict, lone ($st)"; cat "$tmp/contrast"; }
    # --strict fails what isn't measured, but rows scrolled out of view
    # stay notes, as GTK 3 and browsers say nothing of theirs.
    "$sb" run --a11y -- sh -c "'$app' long down & '$sb' a11y contrast --strict --json --window a11y-test" \
      < /dev/null > "$tmp/contrast.json" 2>/dev/null
    python3 -c "import json, sys; j = json.load(open(sys.argv[1]))
assert j['problems'] and not any('not shown' in p['problem'] for p in j['problems']), j
assert j['notes'] and all('not shown' in n['note'] for n in j['notes']), j" "$tmp/contrast.json" \
      && ok "a11y: contrast --strict fails what isn't measured, not rows scrolled out of view" \
      || { fail "a11y: contrast --strict, scrolled"; cat "$tmp/contrast.json"; }
  fi
  # Before 4.14, GTK 4 says where a widget's content is: measured there,
  # faint labels in frames, one GTK can't pick, one under an overlay's
  # clear child and one on a stack's shown page too. Not measured, and
  # said: a stack's page no longer shown, which it still says is where it
  # was, a popover's label, which it places in its window, and a button's
  # edge, outside its content. From 4.14, the same labels are found, the
  # stack's though GTK counts its place in the stack twice (drawn a level
  # lighter, unhinted, on 4.16 and later), and the popover's isn't, drawn
  # outside the window.
  set +e
  "$sb" run --a11y -- sh -c "'$here/gtk4_window.py' & '$sb' wait window gtk4-old >/dev/null &&
    sleep 1 && '$sb' a11y contrast --edges 3 --window gtk4-old" > "$tmp/contrast" 2>/dev/null
  st=$?; set -e
  if [ "$minor" -lt 14 ]; then
    [ $st = 1 ] && [ "$(grep -c ': text 1\.99:1, #b0b0b0 on ' "$tmp/contrast")" = 4 ] \
      && grep -q '/filler\[2\]/label\[0\]: text 1\.99:1' "$tmp/contrast" \
      && grep -q '/panel\[3\]/panel\[0\]/label\[0\]: text not measured: GTK says another node is at its middle$' "$tmp/contrast" \
      && grep -q '/filler\[0\]/label\[0\]: text not measured: GTK 4 reports its position wrong$' "$tmp/contrast" \
      && grep -q '/push button\[4\]: edge not measured: GTK 4 reports its position wrong$' "$tmp/contrast" \
      && [ "$(wc -l < "$tmp/contrast")" = 8 ] \
      && ok "a11y: contrast, GTK 4 before 4.14: in a widget's content; not a stack's page gone, a popover or an edge, said" \
      || { fail "a11y: contrast, GTK 4 before 4.14 ($st)"; cat "$tmp/contrast"; }
  else
    [ $st = 1 ] && [ "$(grep -c ': text 1\.9[0-9]:1, #b[01]b[01]b[01] on #[0-9a-f]* (at least 4\.5); found -\?[0-9]*,-\?[0-9]* px from \(where GTK 4\.[0-9.]* says its text is\|the corner of the box GTK 4\.[0-9.]* says\)$' "$tmp/contrast")" = 4 ] \
      && grep -q '\[3\]/panel\[[01]\]/label\[0\]: text 1\.9' "$tmp/contrast" \
      && grep -q 'button\[4\]/panel\[1\]/panel\[0\]/label\[0\]: text not measured: GTK 4 reports its position wrong\( (.*)\)\?$' "$tmp/contrast" \
      && [ "$(grep -c ': edge not measured: GTK 4 reports its position wrong$' "$tmp/contrast")" = 2 ] \
      && [ "$(wc -l < "$tmp/contrast")" = 7 ] \
      && ok "a11y: contrast, GTK 4 from 4.14: text where it's found, a stack's too; not a popover or an edge, said" \
      || { fail "a11y: contrast, GTK 4 from 4.14 ($st)"; cat "$tmp/contrast"; }
  fi
  # The window's own buttons, out of the Tab order, left out by name.
  "$sb" run --a11y -- sh -c "'$app' & '$sb' a11y tabwalk --window a11y-test \
    --ignore 'button \"(Minimize|Maximize|Close)\"'" > "$tmp/walk" \
    && sed -n 1p "$tmp/walk" | grep -qx '\(push \)\{0,1\}button "" \[focusable, focused\]' \
    && grep -q '"Save" \[focusable, focused\]$' "$tmp/walk" \
    && [ "$(tail -1 "$tmp/walk")" = "round again after 6" ] \
    && ok "a11y: tabwalk goes round and back" || { fail "a11y: tabwalk"; cat "$tmp/walk"; }
  # Scrolled windows, lists and notebooks take the focus with no name of
  # their own, and say what's in them; Tab passes into their rows. The
  # page never shown is a widget GTK hasn't realized, whose size it sends
  # unset, a different one each run.
  "$sb" run --a11y -- sh -c "'$app' lists & '$sb' a11y lint --window a11y-test &&
    '$sb' a11y tree --json --window a11y-test > '$tmp/tree' &&
    '$sb' a11y tabwalk --window a11y-test" > "$tmp/walk" \
    && grep -q '^round again after' "$tmp/walk" \
    && python3 -c "import json, sys; t = json.load(open(sys.argv[1]))
sys.exit(not any(n['name'] == 'Page 2' for n in t)
         or any(not 0 <= s <= 32767 for n in t if n['bounds'] for s in n['bounds'][2:]))" "$tmp/tree" \
    && ok "a11y: lists, scrolled, in a notebook: lint passes, tabwalk goes round, no size past what a node can have" \
    || { fail "a11y: lists"; cat "$tmp/walk" "$tmp/tree"; }
  "$sb" run --a11y -- sh -c "'$app' one & '$sb' a11y tabwalk --window a11y-test" > "$tmp/walk" \
    && [ "$(tail -1 "$tmp/walk")" = "round again after 1" ] \
    && ok "a11y: tabwalk, with one thing to take the focus, is round in one" \
    || { fail "a11y: one"; cat "$tmp/walk"; }
  # A button Tab can't reach is said, and fails only when asked.
  set +e
  "$sb" run --a11y -- sh -c "'$app' unfocusable & '$sb' a11y tabwalk --window a11y-test &&
    ! '$sb' a11y tabwalk --reach-all --window a11y-test >/dev/null" > "$tmp/walk"
  st=$?; set -e
  [ $st = 0 ] && grep -Eqx 'not reached by Tab: (push )?button "Play"' "$tmp/walk" \
    && [ "$(tail -1 "$tmp/walk")" = "round again after 1; 1 not reached by Tab" ] \
    && ok "a11y: tabwalk says what Tab never reaches, and --reach-all fails on it" \
    || { fail "a11y: unreached ($st)"; cat "$tmp/walk"; }
  # A text view takes Tab: the walk doesn't press it there, nothing is
  # typed, and GTK's ctrl+Tab leaves it.
  "$sb" run --a11y -- sh -c "'$app' notes > '$tmp/typed' & '$sb' a11y tabwalk --window a11y-test" \
    > "$tmp/walk" \
    && grep -q '^began in text "Notes" .*  <- a text view: ctrl+Tab leaves it$' "$tmp/walk" \
    && grep -q '^round again after' "$tmp/walk" && [ ! -s "$tmp/typed" ] \
    && ok "a11y: tabwalk leaves a text view by ctrl+Tab, typing nothing into it" \
    || { fail "a11y: notes"; cat "$tmp/walk" "$tmp/typed"; }
  # One nothing gets out of is a trap; in a dialog, Escape, which would
  # close it, isn't tried, nor GTK's shift+Tab, which types a tab. A
  # field Tab never reached is said.
  set +e
  "$sb" run --a11y -- sh -c "'$app' trap > '$tmp/typed' & '$sb' a11y tabwalk --window a11y-test" \
    > "$tmp/walk"
  st=$?
  "$sb" run --a11y -- sh -c "'$app' dialog >> '$tmp/typed' & '$sb' a11y tabwalk --window a11y-test" \
    > "$tmp/dialog"
  st2=$?; set -e
  [ $st = 1 ] && grep -qx 'stuck in a text view, which takes Tab: ctrl+Tab, Escape and F6 leave the focus there' "$tmp/walk" \
    && grep -q '^not reached by Tab: text "File name"' "$tmp/walk" \
    && [ $st2 = 1 ] && grep -qx 'stuck in a text view, which takes Tab: ctrl+Tab and F6 leave the focus there' "$tmp/dialog" \
    && [ ! -s "$tmp/typed" ] \
    && ok "a11y: tabwalk finds a focus trap: a text view nothing gets out of, in a dialog too" \
    || { fail "a11y: trap ($st, $st2)"; cat "$tmp/walk" "$tmp/dialog" "$tmp/typed"; }
  # An Orca whose Python leaves out PYTHONPATH or site would run as it
  # is, out loud: it isn't started.
  mkdir -p "$tmp/isolated"
  for flags in -I -Es; do
    printf '#!/usr/bin/python3 %s\nopen("%s/ran", "w")\n' "$flags" "$tmp" > "$tmp/isolated/orca"
    chmod +x "$tmp/isolated/orca"
    set +e; "$sb" run --a11y -- sh -c "PATH='$tmp/isolated':\$PATH '$sb' a11y speak" 2> "$tmp/err"; st=$?; set -e
    [ $st != 0 ] && grep -q "won't start Orca: its interpreter, /usr/bin/python3 $flags, ignores" "$tmp/err" \
      && [ ! -e "$tmp/ran" ] && ok "a11y: speak refuses an Orca run with $flags, which would leave out its stubs" \
      || fail "a11y: speak with $flags ($st): $(cat "$tmp/err")"
  done
  # What a real Orca says: as it starts, and at each stop, a button by its
  # name and role, one with no name by its role alone, a field by its
  # mnemonic label, a label over two lines; named so a desktop's Orca
  # doesn't take it for another; in a window slow to move the focus, each
  # stop still said; and nothing typed into a text view, which takes Tab.
  # As releases say it: a period at the end before Orca 51, and "push
  # button" before at-spi2-core 2.54.
  if command -v orca >/dev/null; then
    "$sb" run --a11y -- sh -c "'$app' form lines & '$sb' a11y speak --window a11y-test > '$tmp/speak' & s=\$!
      until grep -qs 'SPEECH OUTPUT' \$SHOTBOX_SCRATCH/orca-*/debug.out; do sleep 0.1; done
      for p in \$(pgrep -f -- \"--debug-file \$SHOTBOX_SCRATCH\"); do cat /proc/\$p/comm; done
      wait \$s" > "$tmp/named"
    "$sb" run --a11y -- sh -c "'$app' slow & '$sb' a11y speak --window a11y-test" > "$tmp/slow"
    "$sb" run --a11y -- sh -c "'$app' notes > '$tmp/typed' & '$sb' a11y speak --window a11y-test" \
      > "$tmp/notes"
    grep -qx '(start) \(push \)\{0,1\}button "Save" \[focusable, focused\]  says .*"Save \(push \)\{0,1\}button\.\{0,1\}"' "$tmp/speak" \
      && grep -qx '\(push \)\{0,1\}button "" \[focusable, focused\]  says "\(push \)\{0,1\}button\.\{0,1\}"' "$tmp/speak" \
      && grep -q '^text "Name:" .*  says "Name: text' "$tmp/speak" \
      && grep -q '  says "Line one\\nLine two \(push \)\{0,1\}button\.\{0,1\}"$' "$tmp/speak" \
      && [ "$(cat "$tmp/named")" = shotbox-orca ] \
      && [ "$(grep -c '  says "[^"]*button' "$tmp/slow")" -ge 3 ] && ! grep -q "didn't move" "$tmp/slow" \
      && grep -q '^stopped before Tab in a text view' "$tmp/notes" && [ ! -s "$tmp/typed" ] \
      && ok "a11y: speak: what Orca says, of buttons named and not, a mnemonic, two lines; slow, and not in a text view" \
      || { fail "a11y: speak"; cat "$tmp/speak" "$tmp/named" "$tmp/slow" "$tmp/notes" "$tmp/typed"; }
  else
    [ "${SHOTBOX_ORCA-}" = required ] && fail "a11y: speak skipped: no Orca" \
      || echo "skip  a11y: speak: no Orca"
  fi
  # Said twice, waited for twice; a third wait has nothing left to take.
  "$sb" run --announce-log "$tmp/said" -- sh -c "'$app' & '$sb' wait window a11y-test &&
    '$sb' key Tab Tab space && '$sb' wait announce 'notes\.md' --timeout 10 &&
    '$sb' key space && '$sb' wait announce 'notes\.md' --timeout 10 &&
    ! '$sb' wait announce 'notes\.md' --timeout 1 2>/dev/null" \
    && [ "$(cat "$tmp/said")" = "$(printf 'polite: Saved to notes.md\npolite: Saved to notes.md')" ] \
    && ok "a11y: announcements, logged, and each waited for once" \
    || { fail "a11y: announcements"; cat "$tmp/said"; }
  # What Orca says as it comes: GTK 3's info bar and message dialog, the
  # dialog again once the focus has left it and come back, the focus
  # renamed but not counting down; libadwaita's alert dialog, which says
  # what was just announced, and again, but not its toast's alert; and a
  # Qt message box.
  "$sb" run --announce-log "$tmp/said" -- sh -c "'$here/window.py' alerts alert &
    '$sb' wait window alerts >/dev/null && '$sb' focus alerts && '$sb' key space &&
    '$sb' wait announce '^Error Name is taken$' --timeout 10 && '$sb' wait announce '^Checked$' --timeout 10 &&
    '$sb' key Tab space && sleep 1.5 &&
    '$sb' key Tab space && '$sb' wait announce 'notes\.md\? It can.t be undone\.$' --timeout 10 &&
    '$sb' focus Delete && sleep 0.5 && '$sb' focus alerts && '$sb' focus Delete &&
    '$sb' wait announce 'notes\.md' --timeout 10" \
    && [ "$(cat "$tmp/said")" = "$(printf '%s\n' 'assertive: Error Name is taken' 'polite: Checked' \
         "assertive: Warning Delete notes.md? It can't be undone." \
         "assertive: Warning Delete notes.md? It can't be undone.")" ] \
    && ok "a11y: announcements: GTK 3's info bar and message dialog, again, the focus renamed, not counting down" \
    || { fail "a11y: announcements, GTK 3"; cat "$tmp/said"; }
  # libadwaita before 1.6 (Ubuntu 24.04's 1.5) makes an alert dialog a
  # dialog with no name, and a toast a panel it doesn't announce: Orca 46
  # says "dialog", and none of them is heard.
  if python3 -c 'import gi, sys; gi.require_version("Adw", "1")
from gi.repository import Adw; sys.exit(Adw.get_minor_version() < 6)'; then
    asked="'$sb' wait announce '^Name is taken\$' --timeout 10" toast="'$sb' wait announce toast --timeout 10"
    heard="$(printf '%s\n' 'polite: Error: Name is taken, pick another' 'assertive: Name is taken' \
             'assertive: Name is taken' 'polite: A toast appeared: Saved to notes.md')"
  else
    asked="sleep 1" toast="sleep 1" heard='polite: Error: Name is taken, pick another'
  fi
  "$sb" run --announce-log "$tmp/said" -- sh -c "'$app' alert & '$sb' wait window a11y-test >/dev/null &&
    '$sb' wait announce '^Error' --timeout 10 && $asked && '$sb' key Escape && $asked &&
    '$sb' key Escape && sleep 0.5 && '$sb' key space && $toast && '$sb' key Tab && sleep 1" 2>/dev/null \
    && [ "$(cat "$tmp/said")" = "$heard" ] \
    && ok "a11y: announcements: libadwaita's alert dialog, as the focus goes in, each time; its toast once" \
    || { fail "a11y: announcements, libadwaita"; cat "$tmp/said"; }
  if python3 -c 'import PyQt5.QtWidgets' 2>/dev/null; then
    "$sb" run --announce-log "$tmp/said" -- sh -c "'$here/qt_window.py' & '$sb' wait window qt-alert >/dev/null &&
      '$sb' a11y click Delete >/dev/null && '$sb' wait announce 'notes\.md' --timeout 10" 2>/dev/null \
      && [ "$(cat "$tmp/said")" = 'assertive: Warning Delete notes.md?' ] \
      && ok "a11y: announcements: a Qt message box" || { fail "a11y: announcements, Qt"; cat "$tmp/said"; }
  else
    echo "skip  a11y: announcements, Qt: no PyQt5"
  fi
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

#: part input
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
# The wheel: down and right, then (a step in play) back up and left to
# the start. What a list is scrolled to, down and across, is in LOG.
scrolled() {
  rm -f "$tmp/list" "$tmp/list-there"
  "$sb" run "$@" -- sh -c "'$win' list '$tmp/list' list & '$sb' wait window list >/dev/null &&
    '$sb' wait stable --window list && '$sb' scroll 50 50 5 3 --window list &&
    '$sb' wait stable --window list && cp '$tmp/list' '$tmp/list-there' &&
    echo 'scroll 50 50 -5 -3 --window list' | '$sb' play - && '$sb' wait stable --window list"
  read -r down across < "$tmp/list-there" && [ "$down" -gt 0 ] && [ "$across" -gt 0 ] \
    && [ "$(cat "$tmp/list")" = "0 0" ]
}
scrolled && ok "scroll: down and right, then back" \
  || fail "scroll: $(cat "$tmp/list-there" "$tmp/list" 2>/dev/null)"
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
  scrolled --wayland && ok "wayland: scroll: down and right, then back" \
    || fail "wayland: scroll: $(cat "$tmp/list-there" "$tmp/list" 2>/dev/null)"
  export TMPDIR=$wl
  out=$(WAYLAND_DISPLAY=outside DISPLAY=:0 "$sb" run --wayland -- \
    sh -c 'echo "W=$WAYLAND_DISPLAY D=${DISPLAY-} S=$SWAYSOCK R=$XDG_RUNTIME_DIR"')
  case $out in
    "W=wayland-"*" D= S=$wl/shotbox-"*/run/sway-ipc.*" R=$wl/shotbox-"*) ok "wayland: a sway of its own, and no X" ;;
    *) fail "wayland: the environment: $out" ;;
  esac
  # The session's cursor theme, written into its home, over a seed's links
  # out of it: a linked ~/.icons, and a linked cursor. What they point at
  # stays as it was.
  mkdir -p "$tmp/icons-out" "$tmp/icons-seed1" "$tmp/icons-seed2/.icons/shotbox-blank/cursors"
  echo mine > "$tmp/icons-cursor"
  ln -s "$tmp/icons-out" "$tmp/icons-seed1/.icons"
  ln -s "$tmp/icons-cursor" "$tmp/icons-seed2/.icons/shotbox-blank/cursors/default"
  "$sb" run --wayland --seed "$tmp/icons-seed1" -- true && "$sb" run --wayland --seed "$tmp/icons-seed2" -- true \
    && [ -z "$(ls -A "$tmp/icons-out")" ] && [ "$(cat "$tmp/icons-cursor")" = mine ] \
    && ok "wayland: the cursor theme doesn't follow a seed's links out" \
    || fail "wayland: cursor theme over a seed's links: $(ls -A "$tmp/icons-out") $(head -c 4 "$tmp/icons-cursor")"
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
  [ $st != 0 ] && [ -s "$tmp/w4-failed.png" ] && grep -q '; there are: "wl-test" (app id window.py);' "$tmp/err" \
    && ok "wayland: a failed wait leaves a picture, and says the windows there are" \
    || fail "wayland: no -failed.png, or no windows said ($st): $(cat "$tmp/err")"
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
  # GTK 3 on Wayland reads its caret's blink and its animations from
  # GSettings, over settings.ini: an entry's caret, given the keyboard,
  # blinks with a seeded settings.ini that turns it on, and not without; a
  # spinner holds still.
  mkdir -p "$tmp/blinky/.config/gtk-3.0"
  printf '[Settings]\ngtk-cursor-blink = true\n' > "$tmp/blinky/.config/gtk-3.0/settings.ini"
  wl_blink() {
    set +e; "$sb" run --wayland "$@" -- sh -c "'$win' wb '$tmp/wb' & '$sb' wait window wb >/dev/null &&
      '$sb' focus wb &&
      '$sb' flicker --window wb --seconds 2.5 --brief 1000 --times 3 --tile 4" > "$tmp/out" 2>&1
    echo $?; set -e
  }
  steady=$(wl_blink); blinky=$(wl_blink --seed "$tmp/blinky")
  [ "$steady $blinky" = "0 1" ] && ok "wayland: GTK 3's caret doesn't blink, but for a seed's settings" \
    || fail "wayland: GTK 3's caret: $steady $blinky $(cat "$tmp/out")"
  "$sb" run --wayland -- sh -c "'$win' sp spinner & '$sb' wait window sp >/dev/null &&
    '$sb' wait --timeout 3 --window sp stable 1" >/dev/null 2>&1 \
    && ok "wayland: GTK 3's spinner holds still" || fail "wayland: GTK 3's spinner moves"
  # Input: the same as X11's, then an X client.
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
    && ok "wayland: key and click --hold" || fail "wayland: hold: $got(a for $(held_for "$tmp/wl-held.log")0 ms)"
  down "$tmp/wl-down.log" --wayland && ok "wayland: key --down, then --up: held between commands, Shift alone too" \
    || fail "wayland: --down, --up: $(tr '\n' ' ' < "$tmp/wl-down.log")"
  set +e; "$sb" run --wayland -- "$sb" click 1 1 --down 2> "$tmp/err"; st=$?; set -e
  [ $st != 0 ] && grep -q "use --hold" "$tmp/err" \
    && ok "wayland: click --down, which can't last, refused saying why" || fail "wayland: --down ($st)"
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
    && [ "$(grep -c "release e" "$tmp/wl-mods.log")" = 2 ] \
    && ok "wayland: a chord held by one command shifts another's and stays down; a killed hold is let go with --up" \
    || fail "wayland: held across commands: $(tr '\n' ' ' < "$tmp/wl-mods.log")"
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
  if atspi; then
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
    # The focus, read by a command of its own after another's key, and with
    # no key ever pressed, in GTK 4 and GTK 3: they say where it is only
    # while there's a keyboard, and the session's lasts.
    "$sb" run --wayland --a11y -- sh -c "'$app' & '$sb' wait window a11y-test >/dev/null &&
      '$sb' key Tab && sleep 0.5 && '$sb' a11y tree --window a11y-test > '$tmp/wl-f4'
      '$win' g3 '$tmp/g3' & '$sb' wait window g3 >/dev/null && sleep 0.5 &&
      '$sb' a11y tree --window g3 > '$tmp/wl-f3'" \
      && grep -q 'focusable, focused' "$tmp/wl-f4" && grep -q 'text .*focusable, focused' "$tmp/wl-f3" \
      && ok "wayland: a11y: the focus, GTK 4's and GTK 3's, read between commands" \
      || { fail "wayland: a11y: the focus between commands"; cat "$tmp/wl-f4" "$tmp/wl-f3"; }
    # So libadwaita's alert dialog is heard as the focus goes in, as the
    # program starts and again once answered (1.6 on: see part a11y).
    if python3 -c 'import gi, sys; gi.require_version("Adw", "1")
from gi.repository import Adw; sys.exit(Adw.get_minor_version() < 6)'; then
      "$sb" run --wayland --announce-log "$tmp/wl-said" -- sh -c "'$app' alert &
        '$sb' wait window a11y-test >/dev/null && '$sb' wait announce '^Name is taken\$' --timeout 10 &&
        '$sb' key Escape && '$sb' wait announce '^Name is taken\$' --timeout 10" 2>/dev/null \
        && ok "wayland: a11y: libadwaita's alert dialog, as the focus goes in" \
        || { fail "wayland: a11y: libadwaita's alert dialog"; cat "$tmp/wl-said"; }
    else
      echo "skip  wayland: a11y: libadwaita's alert dialog: libadwaita before 1.6"
    fi
    "$sb" run --wayland --a11y -- sh -c "'$app' notes > '$tmp/wl-typed' & '$sb' wait window a11y-test >/dev/null &&
      '$sb' a11y tabwalk --window a11y-test" > "$tmp/wl-walk" \
      && grep -q '^round again after' "$tmp/wl-walk" && [ ! -s "$tmp/wl-typed" ] \
      && ok "wayland: a11y: tabwalk, from a text view, typing nothing" \
      || { fail "wayland: a11y: tabwalk"; cat "$tmp/wl-walk" "$tmp/wl-typed"; }
  fi
  pgrep -f "$wl/shotbox-.*/(sway\.conf|keyboard\.sock)" >/dev/null && fail "wayland: sway or its keyboard left running" \
    || ok "wayland: sway and its keyboard stop with the session"
  [ -z "$(ours_xwayland)" ] \
    && ok "xwayland: Xwayland stops with the session" || fail "xwayland: Xwayland left running"
  unset TMPDIR
else
  [ "${SHOTBOX_WAYLAND-}" = required ] && fail "wayland skipped: no sway or grim" \
    || echo "skip  wayland: no sway or grim"
fi

#: part core
# Compare.
"$sb" compare "$tmp/a.png" "$tmp/b.png" >/dev/null && ok "compare: the same" || fail "compare same"
convert "$tmp/a.png" -fill red -draw 'point 0,0' "$tmp/d.png"
set +e; n=$("$sb" compare "$tmp/a.png" "$tmp/d.png" --diff "$tmp/diff.png"); st=$?; set -e
[ $st = 1 ] && [ "$n" = "1 pixels differ, in 1x1 at 0,0" ] && [ -s "$tmp/diff.png" ] \
  && ok "compare: one pixel off, found, and where" || fail "compare: $st $n"
convert -size 1300x800 xc:red "$tmp/red.png"; convert -size 1300x800 xc:blue "$tmp/blue.png"
set +e; n=$("$sb" compare "$tmp/red.png" "$tmp/blue.png" 2>&1); st=$?; set -e
[ $st = 1 ] && [ "$n" = "1040000 pixels differ, in 1300x800 at 0,0" ] \
  && ok "compare: a million pixels off, counted exactly" || fail "compare a million: $st $n"
# --ignore: two pixels off, either left out, both, a NAME's for another
# picture, or one that's no box.
convert "$tmp/d.png" -fill red -draw 'point 3,2' "$tmp/d2.png"
out=$(for e in '1:1 pixels differ, in 1x1 at 3,2:--ignore 1x1+0+0' \
               '1:1 pixels differ, in 1x1 at 0,0:--ignore 2x2+2+1' \
               '0:0 pixels differ:--ignore 1x1+0+0 --ignore 1x1+3+2' \
               '0:0 pixels differ:--ignore 5x3+0+0' \
               '1:2 pixels differ, in 4x3 at 0,0:--ignore other=5x3+0+0' \
               '2::--ignore 0x1+0+0'; do
  want=${e%%:*}; rest=${e#*:}; said=${rest%%:*}
  set +e; got=$("$sb" compare "$tmp/a.png" "$tmp/d2.png" ${rest#*:} 2>/dev/null); st=$?; set -e
  [ "$st:$got" = "$want:$said" ] || echo "${rest#*:}: $st $got"
done)
[ -z "$out" ] && ok "compare: --ignore leaves boxes out" || fail "compare --ignore: $out"
# --margin: the picture moved 2 right and 1 up, found within 2 or more,
# not 1; with a pixel changed, failing where it matches best; a box left
# out goes with the first picture.
convert "$tmp/a.png" -roll +2-1 "$tmp/moved.png"
convert "$tmp/moved.png" -fill red -draw 'point 12,9' "$tmp/moved2.png"
out=$(for e in '0:moved:0 pixels differ, moved 2,-1:--margin 2' '0:moved:0 pixels differ, moved 2,-1:--margin 5' \
               '1:moved::--margin 1' '1:moved2:1 pixels differ, in 1x1 at 10,10, moved 2,-1:--margin 2' \
               '0:moved2:0 pixels differ, moved 2,-1:--margin 2 --ignore 1x1+10+10' '2:moved::--margin x'; do
  want=${e%%:*}; rest=${e#*:}; b=${rest%%:*}; rest=${rest#*:}; said=${rest%%:*}
  set +e; got=$("$sb" compare "$tmp/a.png" "$tmp/$b.png" ${rest#*:} 2>/dev/null); st=$?; set -e
  [ "$st" = "$want" ] && { [ -z "$said" ] || [ "$got" = "$said" ]; } || echo "$b ${rest#*:}: $st $got"
done)
[ -z "$out" ] && ok "compare: --margin finds where the picture moved" || fail "compare --margin: $out"
"$sb" compare "$tmp/a.png" "$tmp/d2.png" --diff "$tmp/diff2.png" >/dev/null || :
"$sb" compare "$tmp/a.png" "$tmp/d2.png" --ignore 1x1+0+0 --diff "$tmp/xdiff.png" >/dev/null || :
set +e; n=$("$sb" compare "$tmp/diff2.png" "$tmp/xdiff.png"); set -e
[ "$n" = "1 pixels differ, in 1x1 at 0,0" ] && [ "$(convert "$tmp/xdiff.png" -format '%[hex:p{0,0}]' info:)" = FF00FF ] \
  && ok "compare: the diff shows what's left out, magenta" || fail "compare: diff: $n"

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

#: part record
# Recording: a window for two seconds as an MP4; the screen as a WebM at
# 10 frames a second; a crop as a GIF, its odd sides evened; and one with
# no length, stopped by kill, finished properly. On X11, and on Wayland,
# which needs wf-recorder too, or skips as the Wayland tests do.
if command -v ffmpeg >/dev/null; then
  probe() { ffprobe -v error -select_streams v -show_entries stream=codec_name,width,height \
    -show_entries format=duration -of csv=p=0 "$1" | tr '\n' ' '; }
  frames() { ffprobe -v error -count_frames -select_streams v -show_entries stream=nb_read_frames \
    -of csv=p=0 "$1"; }
  # FILE's streams' codecs and seconds, as decoded, on a line; then how
  # far each tone starts from its flash's first frame (window.py's beep).
  cat > "$tmp/av.py" <<'PY'
import array, subprocess, sys
f = sys.argv[1]


def run(*cmd):
    return subprocess.run(cmd, capture_output=True, check=True).stdout.decode("latin-1")


frames = [(float(t), float(y) > 128) for t, y in (line.split(",")[:2] for line in run(
    "ffprobe", "-v", "error", "-f", "lavfi", "-show_entries",
    "frame=pts_time:frame_tags=lavfi.signalstats.YAVG", "-of", "csv=p=0",
    f"movie={f},signalstats").split())]
sound = array.array("h", run("ffmpeg", "-v", "error", "-i", f, "-map", "0:a", "-ac", "1",
                             "-f", "s16le", "-").encode("latin-1"))
codecs = run("ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", f).split()
print(codecs[0], f"{frames[-1][0] + 1 / 30:.3f}", codecs[1], f"{len(sound) / 48000:.3f}")
onsets, last = [], -48000
for i, s in enumerate(sound):
    if abs(s) > 2000:
        if i - last > 4800:
            onsets.append(i / 48000)
        last = i
flashes = [t for (t, white), (_, was) in zip(frames[1:], frames) if white and not was]
print(" ".join(f"{a - v:.3f}" for v in flashes for a in onsets if abs(a - v) < 0.5))
PY
  # The same on X11 and on Wayland: records [--wayland] PREFIX LABEL.
  records() {
    opt=$1 pre=$2 say=$3
    # Each check that fails is said, with what it got; the session's steps
    # traced, so a step that failed is the last one there.
    why=; no() { why="$why; $*"; }
    "$sb" run $opt -- sh -xc "'$here/window.py' rec & '$sb' wait window rec && '$sb' capture '$tmp/$pre-win.png' --window rec &&
      '$sb' record '$tmp/$pre.mp4' --window rec --seconds 2 && '$sb' record '$tmp/$pre.webm' --fps 10 --seconds 1 &&
      '$sb' record '$tmp/$pre.gif' --window rec --crop 101x51+10+10 --seconds 1 &&
      { '$sb' record '$tmp/$pre-k.mp4' --window rec & r=\$!; sleep 1.5; kill \$r; wait \$r; } &&
      ! '$sb' record '$tmp/$pre.avi' --seconds 1 2> '$tmp/err' && ! '$sb' record '$tmp/${pre}0.mp4' --seconds 0 2>> '$tmp/err' &&
      ! '$sb' record '$tmp/${pre}s.mp4' --sound --seconds 1 2>> '$tmp/err' &&
      ! '$sb' record '$tmp/${pre}s.gif' --sound --seconds 1 2>> '$tmp/err' &&
      ! '$sb' record '$tmp/${pre}s.mp4' --strict --seconds 1 2>> '$tmp/err'" > "$tmp/$pre-steps" 2>&1 \
      || no "a step failed ($?)"
    p=$(probe "$tmp/$pre.mp4"); [ "$p" = "h264,200,120 2.000000 " ] || no "MP4 $p"
    n=$(frames "$tmp/$pre.mp4") || :; [ "$n" -ge 50 ] 2>/dev/null || no "MP4 $n frames, not 50 or more"
    c=$(ffmpeg -loglevel error -y -ss 1 -i "$tmp/$pre.mp4" -frames:v 1 "$tmp/$pre-frame.png" 2>&1 &&
      "$sb" compare "$tmp/$pre-win.png" "$tmp/$pre-frame.png" --fuzz 20% 2>&1) \
      || no "MP4's frame at 1 s, not the window's picture: $c"
    p=$(probe "$tmp/$pre.webm"); [ "$p" = "vp9,1280,800 1.000000 " ] || no "WebM $p"
    n=$(frames "$tmp/$pre.webm") || :; [ "$n" = 10 ] || no "WebM $n frames, not 10"
    p=$(probe "$tmp/$pre.gif"); [ "$p" = "gif,100,50 1.000000 " ] || no "GIF $p"
    p=$(probe "$tmp/$pre-k.mp4"); case $p in "h264,200,120 "*) ;; *) no "killed MP4 $p" ;; esac
    n=$(frames "$tmp/$pre-k.mp4") || :; [ "$n" -ge 15 ] 2>/dev/null || no "killed MP4 $n frames, not 15 or more"
    [ ! -e "$tmp/${pre}0.mp4" ] || no "a 0 s recording made"
    for e in "mp4, .webm, .gif or .wav" "more than 0" "start the session with --audio" \
      "with sound is an .mp4 or .webm" "go with --sound"; do grep -qs "$e" "$tmp/err" || no "not said: $e"; done
    [ -z "$why" ] && ok "$say: a window as it looks, the screen, a crop, as MP4, WebM and GIF; stopped by kill, finished" \
      || { fail "$say: ${why#; }"; cat "$tmp/$pre-steps" "$tmp/err"; }
    # --until-line: ended by a line written 1.5 s in, and --tail after it,
    # not by the one already there; with no line, by --seconds. Each said.
    echo run_end 0 > "$tmp/$pre-ev"
    "$sb" run $opt -- sh -c "{ sleep 1.5; echo run_end 1 >> '$tmp/$pre-ev'; } &
      '$sb' record '$tmp/$pre-u.mp4' --until-line 'run_\w+' '$tmp/$pre-ev' --tail 0.5 --seconds 10 &&
      '$sb' record '$tmp/$pre-u1.mp4' --until-line run_end '$tmp/$pre-ev' --seconds 1" > "$tmp/$pre-u" \
      && d=$(probe "$tmp/$pre-u.mp4" | cut -d' ' -f2) && awk "BEGIN { exit !($d >= 1.2 && $d < 3) }" \
      && [ "$(probe "$tmp/$pre-u1.mp4" | cut -d' ' -f2)" = 1.000000 ] \
      && sed -n 1p "$tmp/$pre-u" | grep -qx "run_end 1" && sed -n 3p "$tmp/$pre-u" | grep -q "  no line in its --seconds$" \
      && ok "$say --until-line: ended by a new line, and --tail after it; or by --seconds; said" \
      || { fail "$say --until-line: ${d-}"; cat "$tmp/$pre-u"; }
    # A GIF whose session is stopped (run killed) is finished all the same,
    # leaving nothing behind in the temp dir.
    rm -rf "$tmp/$pre-tmp"; mkdir "$tmp/$pre-tmp"
    "$sb" run $opt --env "TMPDIR=$tmp/$pre-tmp" -- sh -c "'$here/window.py' rg & '$sb' wait window rg &&
      '$sb' record '$tmp/$pre-run.gif'" > /dev/null & r=$!
    sleep 6; kill $r; wait $r || true
    n=0; while { pgrep -f "keep_recording.*$tmp/$pre-run.gif" || ls -A "$tmp/$pre-tmp" | grep -q .; } >/dev/null &&
      [ $n -lt 100 ]; do sleep 0.1; n=$((n + 1)); done
    [ "$(probe "$tmp/$pre-run.gif" | cut -d' ' -f1)" = gif,1280,800 ] && [ -z "$(ls "$tmp/$pre-tmp")" ] \
      && ok "$say: a GIF in a session that's stopped, finished, nothing left behind" \
      || fail "$say, stopped run: $(probe "$tmp/$pre-run.gif"); $(ls "$tmp/$pre-tmp")"
    # Killed outright, record takes its ffmpeg with it, which otherwise goes
    # on recording a display that's gone; the file is finished all the same.
    "$sb" run $opt -- sh -c "'$here/window.py' rk & '$sb' wait window rk &&
      { '$sb' record '$tmp/$pre-9.mp4' --window rk & r=\$!; sleep 1.5; kill -9 \$r; }
      n=0; while pgrep -ax ffmpeg | grep -q $pre-9 && [ \$n -lt 50 ]; do sleep 0.1; n=\$((n + 1)); done
      ! pgrep -ax ffmpeg | grep -q $pre-9" > /dev/null \
      && probe "$tmp/$pre-9.mp4" | grep -q "^h264,200,120 " \
      && ok "$say: killed outright, its ffmpeg stops and finishes the file" \
      || { fail "$say, killed: $(probe "$tmp/$pre-9.mp4")"
           pgrep -ax ffmpeg | grep $pre-9 | cut -d' ' -f1 | xargs -r kill; }
    # Stopped a moment after it starts, in the background, where a job's
    # Ctrl-C is ignored: it ends rather than recording for good. Killed, then
    # killed outright while it finishes: the file is still whole. A GIF
    # killed outright, once it has frames (ffmpeg can take a second or more
    # to start), is made all the same, and nothing left in TMPDIR.
    rm -rf "$tmp/$pre-tmp2"; mkdir "$tmp/$pre-tmp2"
    "$sb" run $opt --env "TMPDIR=$tmp/$pre-tmp2" -- sh -c "'$here/window.py' re & '$sb' wait window re || exit 1
      for d in 0.05 0.08 0.1 0.15 0.2 0.3 0.5; do
        '$sb' record '$tmp/$pre-e.mp4' --window re 2>/dev/null & r=\$!; sleep \$d; kill \$r
        n=0; while kill -0 \$r 2>/dev/null; do [ \$n -lt 100 ] || { echo \"hung, killed at \$d\"; kill -9 \$r; exit 1; }
          sleep 0.1; n=\$((n + 1)); done
      done
      '$sb' record '$tmp/$pre-2.mp4' --window re & r=\$!; sleep 1.5; kill \$r; sleep 0.1; kill -9 \$r
      '$sb' record '$tmp/$pre-9.gif' --window re & r=\$!
      n=0; until pgrep -ax python3 | grep keep_recording | grep -q $pre-9.gif; do
        [ \$n -lt 100 ] || { echo 'no keeper'; pgrep -ax python3; exit 1; }; sleep 0.1; n=\$((n + 1)); done
      sleep 3; kill -9 \$r
      n=0; while [ ! -s '$tmp/$pre-9.gif' ] || [ -n \"\$(ls '$tmp/$pre-tmp2')\" ]; do
        [ \$n -lt 300 ] || { echo 'no GIF'; pgrep -ax python3; pgrep -ax ffmpeg; exit 1; }
        sleep 0.1; n=\$((n + 1)); done" > "$tmp/$pre-steps" 2>&1 \
      && probe "$tmp/$pre-2.mp4" | grep -q "^h264,200,120 " && probe "$tmp/$pre-9.gif" | grep -q "^gif,200,120 " \
      && ok "$say: stopped as it starts, ends; killed while finishing, and a GIF killed, still whole" \
      || { fail "$say, stopped early or killed: $(probe "$tmp/$pre-2.mp4"); $(probe "$tmp/$pre-9.gif"); $(ls "$tmp/$pre-tmp2")"
           cat "$tmp/$pre-steps"; }
    # A lasting session stopped while it records: the recording ends with it.
    # With sound and timed, its PipeWire stops first, and the picture ends with it.
    snd=; ! audio || snd="--sound --seconds 30"
    "$sb" start --name "$pre$$" $opt ${snd:+--audio} >/dev/null
    "$sb" --session "$pre$$" record "$tmp/$pre-stop.mp4" $snd > /dev/null & r=$!
    sleep 2; "$sb" stop "$pre$$" >/dev/null
    n=0; while kill -0 $r 2>/dev/null && [ $n -lt 100 ]; do sleep 0.1; n=$((n + 1)); done
    if kill -0 $r 2>/dev/null; then kill -9 $r; fail "$say: outlived its session's stop"
    else probe "$tmp/$pre-stop.mp4" | grep -q "^h264,1280,800 " \
      && { [ -z "$snd" ] || python3 "$tmp/av.py" "$tmp/$pre-stop.mp4" | awk 'NR == 1 { exit ($2 - $4) ^ 2 >= 0.0025 }'; } \
      && ok "$say: ends, finished, when its lasting session stops" \
      || fail "$say, session stopped: $(probe "$tmp/$pre-stop.mp4"); $([ -z "$snd" ] || python3 "$tmp/av.py" "$tmp/$pre-stop.mp4")"; fi
    # --sound: what plays too, AAC in an MP4, Opus in a WebM, both streams
    # --seconds long (AAC, as an older ffmpeg decodes it, to its 1024-sample
    # frame), each tone starting within 0.1 s of its flash, and
    # what `listen` would say of it said: with --strict, its gaps fail it,
    # unless --gap is longer than they are. Stopped by kill, before its
    # --seconds or with none, by --until-line's line (a file not there
    # yet), or by the session's PipeWire going, before its --seconds (said)
    # or with none, the sound as long as the picture, within a frame of
    # each for those (33 ms of picture and 21 of AAC: 0.055 s), and as
    # long as it ran.
    if audio; then
      rm -rf "$tmp/$pre-pw"; mkdir "$tmp/$pre-pw"
      TMPDIR=$tmp/$pre-pw "$sb" run $opt --audio -- sh -c "'$here/window.py' beep beep & '$sb' wait window beep &&
        { '$sb' record '$tmp/$pre-s.mp4' --window beep --seconds 3 --sound --strict > '$tmp/$pre-heard'
          [ \$? = 1 ]; } &&
        '$sb' record '$tmp/$pre-s.webm' --window beep --seconds 2 --sound --strict --gap 1000 > '$tmp/$pre-heard2' &&
        { '$sb' record '$tmp/$pre-sk.mp4' --window beep --sound & r=\$!; sleep 2; kill \$r; wait \$r; } &&
        { '$sb' record '$tmp/$pre-st.mp4' --window beep --sound --seconds 10 & r=\$!; sleep 2; kill \$r; wait \$r; } &&
        { { sleep 2; echo go > '$tmp/$pre-go-ev'; } &
          '$sb' record '$tmp/$pre-su.mp4' --window beep --sound --seconds 10 --until-line go '$tmp/$pre-go-ev'; } &&
        : > '$tmp/$pre-go' && { '$sb' record '$tmp/$pre-sp0.mp4' --window beep --sound > /dev/null & p=\$!
          '$sb' record '$tmp/$pre-sp.mp4' --window beep --sound --seconds 30 > '$tmp/$pre-heard3' && wait \$p; }" \
        > /dev/null 2>&1 & r=$!
      n=0; until [ -e "$tmp/$pre-go" ] || [ $n -ge 600 ]; do sleep 0.1; n=$((n + 1)); done; sleep 2
      for p in $(pgrep -u "$(id -u)" -x pipewire); do
        { tr '\0' '\n' < "/proc/$p/environ"; } 2>/dev/null | grep -q "^XDG_RUNTIME_DIR=$tmp/$pre-pw/" && kill "$p"
      done
      wait $r \
        && python3 "$tmp/av.py" "$tmp/$pre-s.mp4" > "$tmp/$pre-av" && python3 "$tmp/av.py" "$tmp/$pre-s.webm" >> "$tmp/$pre-av" \
        && python3 "$tmp/av.py" "$tmp/$pre-sk.mp4" >> "$tmp/$pre-av" && python3 "$tmp/av.py" "$tmp/$pre-sp.mp4" >> "$tmp/$pre-av" \
        && python3 "$tmp/av.py" "$tmp/$pre-st.mp4" >> "$tmp/$pre-av" && python3 "$tmp/av.py" "$tmp/$pre-su.mp4" >> "$tmp/$pre-av" \
        && python3 "$tmp/av.py" "$tmp/$pre-sp0.mp4" >> "$tmp/$pre-av" \
        && sed -n '1p;3p' "$tmp/$pre-av" | tr '\n' ' ' | awk '$1 == "h264" && $2 == 3 && $3 == "aac" &&
             $4 >= 3 && $4 < 3.022 && $5 " " $6 " " $7 " " $8 == "vp9 2.000 opus 2.000" { ok = 1 } END { exit !ok }' \
        && [ "$(sed -n '5p;7p;9p;11p;13p' "$tmp/$pre-av" | awk '$1 == "h264" && $3 == "aac" && $2 > 1.4 && ($2 - $4) ^ 2 < (NR == 2 || NR == 5 ? 0.003 : 0.01)' | wc -l)" = 5 ] \
        && sed -n '2p;4p;6p' "$tmp/$pre-av" | tr ' ' '\n' | awk 'NF { n++ } $1 ^ 2 >= 0.01 { bad = 1 } END { exit bad || n < 3 }' \
        && grep -q "^3.000s, 48000 Hz, 2 channels: peak " "$tmp/$pre-heard" && grep -Eq "gaps? of silence" "$tmp/$pre-heard" \
        && ! grep -q gap "$tmp/$pre-heard2" && grep -q "^the sound server stopped" "$tmp/$pre-heard3" \
        && ok "$say --sound: picture and sound in one file, each as long, in step; listened to" \
        || { fail "$say --sound"; cat "$tmp/$pre-av" "$tmp/$pre-heard" "$tmp/$pre-heard2" "$tmp/$pre-heard3"; }
    fi
  }
  records "" rec record
  # Run from where another shotbox is, the keeper is still this one.
  mkdir -p "$tmp/other/shotbox"; : > "$tmp/other/shotbox/__init__.py"
  echo 'raise ImportError("the other shotbox")' > "$tmp/other/shotbox/x.py"
  (cd "$tmp/other" && "$sb" run -- "$sb" record "$tmp/rec-cwd.mp4" --seconds 1 > /dev/null) \
    && probe "$tmp/rec-cwd.mp4" | grep -q "^h264,1280,800 " \
    && ok "record: from where another shotbox is, still this one's" || fail "record, from another shotbox's dir"
  # An ffmpeg stuck opening what it records, where SIGTERM doesn't reach
  # it: given up on, timed or stopped, saying so.
  mkdir -p "$tmp/stuck"; printf '#!/bin/sh\ntrap "" TERM\nexec sleep 60\n' > "$tmp/stuck/ffmpeg"
  chmod +x "$tmp/stuck/ffmpeg"
  "$sb" run --env "PATH=$tmp/stuck:$PATH" -- sh -c "! '$sb' record '$tmp/stuck.mp4' --seconds 1 2> '$tmp/err' &&
    ! timeout -s INT 2 '$sb' record '$tmp/stuck.mp4' 2>> '$tmp/err'" \
    && [ "$(sort -u "$tmp/err")" = "shotbox: ffmpeg couldn't record: it was still opening what it records after 10 seconds" ] \
    && [ "$(wc -l < "$tmp/err")" = 2 ] \
    && ok "record: an ffmpeg that never begins, given up on, timed or stopped, saying so" \
    || fail "record, ffmpeg stuck: $(cat "$tmp/err")"
  # A still screen as a WebM, of which ffmpeg writes nothing for seconds,
  # holding it back, lasts its --seconds all the same.
  "$sb" run -- "$sb" record "$tmp/still.webm" --seconds 12 >/dev/null \
    && [ "$(probe "$tmp/still.webm")" = "vp9,1280,800 12.000000 " ] \
    && ok "record: a still screen as a WebM, its --seconds long" || fail "record, still WebM: $(probe "$tmp/still.webm")"
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
  if command -v sway >/dev/null && command -v grim >/dev/null && command -v wf-recorder >/dev/null; then
    records --wayland wrec "wayland: record"
    set +e; "$sb" run --wayland -- "$sb" record "$tmp/wrec-p.mp4" --pointer --seconds 1 2> "$tmp/err"; st=$?; set -e
    [ $st != 0 ] && grep -q "drawn as nothing" "$tmp/err" \
      && ok "wayland: record --pointer, refused saying why" || fail "wayland: record --pointer ($st)"
  else
    [ "${SHOTBOX_WAYLAND-}" = required ] && fail "wayland: record skipped: no sway, grim or wf-recorder" \
      || echo "skip  wayland: record: no sway, grim or wf-recorder"
  fi
else
  echo "skip  record: no ffmpeg"
fi

#: part core
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
  # Its own pads, by their nodes: another session's may be up meanwhile.
  nodes=$(sed -n 's|^node /dev/input/||p' "$tmp/pad.log")
  left=$(for n in $nodes; do cat "/sys/class/input/$n/device/name" 2>/dev/null || true; done)
  [ -n "$nodes" ] && ! echo "$left" | grep -q "shotbox virtual pad" \
    && ok "gamepads: gone with the session" || fail "gamepads: left behind ($nodes)"
  # Nothing else of the machine's to read controllers from: raw HID devices
  # and uinput can't be opened inside, and there's no /dev/bus/usb to scan.
  "$sb" run --gamepads 1 -- sh -c 'for f in /dev/hidraw* /dev/uhid /dev/usb/hiddev* /dev/uinput; do
      [ -e "$f" ] && { [ -c "$f" ] || head -c0 "$f"; } 2>/dev/null && echo "$f"; done
    [ ! -d /dev/bus/usb ] || ls /dev/bus/usb' > "$tmp/seal.out" \
    && [ ! -s "$tmp/seal.out" ] && ok "gamepads: the machine's HID, USB and uinput, masked" \
    || fail "gamepads: reachable: $(cat "$tmp/seal.out")"
  # The pad server goes with the process that started it, even killed.
  python3 - "$root" <<'PY' && ok "gamepads: gone when what made them is killed" || fail "gamepads: outlived a kill"
import json, os, shutil, signal, subprocess, sys, tempfile, time
from pathlib import Path
d = tempfile.mkdtemp()
parent = subprocess.Popen([sys.executable, "-c", f"""
import os, subprocess, sys, time
sys.path.insert(0, {sys.argv[1]!r})
subprocess.Popen([sys.executable, "-c", "import sys; sys.path.insert(0, {sys.argv[1]!r}); "
                  "from shotbox.pad import serve; serve(*sys.argv[1:])",
                  "{d}/pads.sock", "{d}/ready", "1", str(os.getpid())])
time.sleep(60)"""])
end = time.monotonic() + 10
while not os.path.exists(f"{d}/ready") and time.monotonic() < end:
    time.sleep(0.05)
nodes = json.load(open(f"{d}/ready")) if os.path.exists(f"{d}/ready") else []


def there():
    names = [Path(f"/sys/class/input/{Path(n).name}/device/name") for n in nodes]
    return any(p.exists() and p.read_text().startswith("shotbox virtual pad") for p in names)


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
    { '$sb' pad press y --hold 30 & k=\$!; sleep 0.5; kill -KILL \$k; wait \$k 2>/dev/null; '$sb' pad press y --up; echo upmark >> '$tmp/pad2.log'; } &&
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

# listen: a tone heard clean, its silence before and after not gaps; one
# with 5 ms of silence in it, a gap, but not with --gap 10; clipped;
# silent; and silence after a sample of 1, at the end none, and in the
# middle, a millisecond of it, one.
python3 - "$tmp" <<'PY'
import math, struct, sys, wave
def write(name, values):
    with wave.open(f"{sys.argv[1]}/{name}.wav", "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes(b"".join(struct.pack("<hh", v, v) for v in values))
tone = [int(8000 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(48000)]
write("clean", [0] * 4800 + tone + [0] * 4800)
write("gap", tone[:24000] + [0] * 240 + tone[24000:])
write("loud", [max(-32768, min(32767, v * 5)) for v in tone])
write("silent", [0] * 48000)
# Silence after a sample whose high byte is zero starts mid-frame.
write("fade", tone + [1] + [0] * 4800)
write("short", tone[:24001] + [1] + [0] * 48 + tone[24001:])
PY
listened=""
for c in "clean 0" "gap 1 gap at 0.500s, 5.0 ms" "gap --gap 10 0" "loud 1 clipped: " "silent 1 silent: " \
         "fade 0" "short 1 , 1.0 ms"; do
  set -- $c; name=$1; shift; opts=""
  [ "$1" = --gap ] && { opts="--gap $2"; shift 2; }
  want=$1; shift
  set +e; out=$("$sb" listen "$tmp/$name.wav" $opts); st=$?; set -e
  [ $st = "$want" ] && { [ $# = 0 ] || echo "$out" | grep -qF "$*"; } || listened="$listened $name($st: $out)"
done
[ -z "$listened" ] && ok "listen: clean, a gap (not with --gap 10), clipped, silent; silence after a sample of 1" \
  || fail "listen:$listened"

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

#: end
echo
[ $fails = 0 ] && echo "all passed" || { echo "$fails failed"; exit 1; }
