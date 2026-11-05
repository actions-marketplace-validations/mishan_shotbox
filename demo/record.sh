#!/bin/sh
# Records the README's demo into OUT-DIR: demo.mp4 and demo.gif, a scene
# each from a11y.play, pictures.play, sound.play, flicker.play, pad.play
# and mutate.play, then a closing card with LOGO (docs/logo.png). With
# SCENE, just that one, as OUT-DIR/SCENE.mp4, to check it before a take.
set -eu
demo=$(cd "$(dirname "$0")" && pwd)
out=$(realpath "$1")
only=${2-}
[ -z "$only" ] || [ -f "$demo/$only.play" ] || { echo "record.sh: no $only.play" >&2; exit 2; }
logo=${LOGO:-$demo/../docs/logo.png}
[ -d "$demo/../node_modules/playwright" ] || { echo "record.sh: npm install first" >&2; exit 2; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# scene NAME [SESSION-OPTION...]: plays NAME.play into $work/NAME.mp4,
# its terminal COLS columns wide (83) at x AT (224, beside the app), with
# MODULES as its node_modules if given.
scene() {
  name=$1
  shift
  [ -z "$only" ] || [ "$only" = "$name" ] || return 0
  shotbox run --screen 890x480 --seed "$demo" --identity demo@sketchpad \
    --env OUT="$work/$name.mp4" --env SCENE="$name" "$@" -- sh -c '
    cd
    [ -z "${MODULES-}" ] || ln -s "$MODULES" node_modules
    shotbox term --size ${COLS:-83}x28 --font "Monospace 10" --scheme scheme.json --log term.log \
      -- bash --rcfile "$HOME/bashrc" &
    shotbox wait window shotbox-term
    # No window manager: put the terminal beside the app, which maps at 0,0.
    xdotool search --name "^shotbox-term$" windowmove ${AT:-224} 0
    shotbox play "$SCENE.play" >/dev/null'
}
clock=--clock=2026-10-01T09:00:00
scene a11y --a11y $clock
scene pictures $clock
# No --clock: under it, record's ffmpeg can't read the sound server.
scene sound --audio
scene flicker --env SHOW_AT=+0+240 --env SHOW_SIZE=224x240 $clock
scene pad --gamepads 1 $clock
# The test's imports: this checkout as shotbox, and its Playwright (npm
# install). Its browsers' path is passed in: --identity leaves it out, as
# it has the user's name in it.
mkdir "$work/node_modules"
ln -s "$demo/.." "$work/node_modules/shotbox"
ln -s "$demo/../node_modules/playwright" "$work/node_modules"
scene mutate --env MODULES="$work/node_modules" --env COLS=110 --env AT=0 $clock \
  --env PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-${XDG_CACHE_HOME:-$HOME/.cache}/ms-playwright}"

if [ -n "$only" ]; then cp "$work/$only.mp4" "$out/"; exit; fi

magick -size 890x520 xc:'#1f2430' \( "$logo" -resize 170x170 \) -geometry +80+150 -composite \
  -font Cantarell-Bold -pointsize 30 -fill white -annotate +290+200 'shotbox' \
  -font Cantarell-Regular -pointsize 19 -fill '#d8dee9' \
  -annotate +290+236 'Eyes, ears and hands for agents on real GUI programs' \
  -font DejaVu-Sans-Mono -pointsize 14 -fill '#a3d97a' \
  -annotate +290+284 'claude mcp add shotbox -- shotbox mcp --allow PROGRAMS' \
  -fill '#81a1c1' -annotate +290+318 'start_session  spawn_program  get_accessible_tree  take_screenshot' \
  -annotate +290+340 'click_element  type_text  press_keys  wait_for  record_video' \
  -annotate +290+362 'check_accessible_names  check_contrast  check_focus_order  ...' "$work/card.png"

# Each scene padded to 890x520 for its caption, with a silent track where
# it has no sound of its own, then one after another.
set --
filters= n=0
for name in a11y pictures sound flicker pad mutate; do
  set -- "$@" -i "$work/$name.mp4"
  filters="$filters[$n:v]pad=890:520:0:0:0x11141b,fps=30,setsar=1[v$n];"
  if [ "$name" = sound ]; then
    filters="$filters[$n:a]aresample=48000,aformat=channel_layouts=stereo[a$n];"
  else
    filters="$filters anullsrc=r=48000:cl=stereo,atrim=0:$(ffprobe -v error -show_entries \
      format=duration -of csv=p=0 "$work/$name.mp4")[a$n];"
  fi
  n=$((n + 1))
done
set -- "$@" -loop 1 -t 4.5 -framerate 30 -i "$work/card.png"
filters="$filters[$n:v]fps=30,setsar=1[v$n]; anullsrc=r=48000:cl=stereo,atrim=0:4.5[a$n];"
concat=
for i in $(seq 0 $n); do concat="$concat[v$i][a$i]"; done
style="FontName=Cantarell,Bold=1,FontSize=10,PrimaryColour=&HFFFFFF&,BorderStyle=1,Outline=0,Shadow=0,Alignment=2,MarginV=7"
ffmpeg -v error -y "$@" -filter_complex "$filters${concat}concat=n=$((n + 1)):v=1:a=1[v][a];\
[v]subtitles=$demo/captions.srt:force_style='$style'[vc]" -map '[vc]' -map '[a]' \
  -c:v libx264 -preset veryslow -crf 26 -pix_fmt yuv420p -c:a aac -b:a 96k \
  -movflags +faststart "$out/demo.mp4"
ffmpeg -v error -y -i "$out/demo.mp4" -lavfi "fps=8,split[a][b];\
[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" \
  "$out/demo.gif"
