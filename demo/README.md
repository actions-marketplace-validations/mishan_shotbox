# The README's demo

Five scenes, each a sealed session with a terminal beside a small GTK 4
app, played by its `.play` script, then a closing card:

- `a11y.play`: `a11y lint` and `a11y contrast` find Sketchpad's
  (`sketchpad.py`) unnamed button and faint text; `fix.diff` fixes both,
  and both checks pass.
- `pictures.play`: `shoot` before and after the fix, and `compare`
  marks the pixels that changed.
- `sound.play`: `record` what `player.py` plays, with a stall in it, and
  `listen` finds the gap. The MP4 keeps the sound.
- `flicker.play`: `flicker --save` finds `viewer.py`'s strip, and shows
  its frames.
- `pad.play`: `pad` drives `game.py` with a virtual gamepad.

To record it again, with shotbox, PyGObject with GTK 4, libadwaita and
VTE, xdotool, ImageMagick and ffmpeg (with libass) on PATH:

```
demo/record.sh OUT-DIR
```

which writes `OUT-DIR/demo.mp4` and `OUT-DIR/demo.gif`. `captions.srt`
is timed to the scenes' lengths (each `.play`'s `record --seconds`), so
a scene made longer moves the captions after it.
