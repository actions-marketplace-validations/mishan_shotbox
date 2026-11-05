# The README's demo

Six scenes, each a sealed session with a terminal, beside a small GTK 4
app in the first five, played by its `.play` script, then a closing card:

- `a11y.play`: `a11y lint` and `a11y contrast` find Sketchpad's
  (`sketchpad.py`) unnamed button and faint text, and `a11y speak` hears
  Orca say just "button" for it; `fix.diff` fixes both, both checks
  pass, and Orca says "Delete button".
- `pictures.play`: `shoot --tree` before and after the fix, and
  `compare` marks the pixels that changed and names the label they're in.
- `sound.play`: `record` what `player.py` plays, with a stall in it, and
  `listen` finds the gap. The MP4 keeps the sound.
- `flicker.play`: `flicker --save` finds `viewer.py`'s strip, and shows
  its frames.
- `pad.play`: `pad` drives `game.py` with a virtual gamepad.
- `mutate.play`: `mutate` runs `checkout.test.mjs` with its Pay button
  moved, which it fails (BRITTLE: it clicks where Pay was drawn), and
  with the price changed, which it passes (BLIND: it never checks it).

To record it again, with shotbox, PyGObject with GTK 4, libadwaita and
VTE, Orca, xdotool, ImageMagick, ffmpeg (with libass) and Node on PATH,
and Playwright's Chromium (`npm install && npx playwright install
chromium` in this checkout):

```
demo/record.sh OUT-DIR
```

which writes `OUT-DIR/demo.mp4` and `OUT-DIR/demo.gif`. `captions.srt`
is timed to the scenes' lengths (each `.play`'s `record --seconds`), so
a scene made longer moves the captions after it.
