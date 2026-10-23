# Node

`node/` is a small package for browser checks with Playwright, from the
pieces several projects had copied between them:

```js
import { serve, checks, pageErrors, launch, until, gif, sealed,
         dress, film, frames, steady, accessible } from 'shotbox';
```

- `serve(dir, port = 0)`: a static server on localhost that serves ES
  modules with the right type; port 0 picks a free one.
  `shotbox-serve [DIR] [PORT] [PAGE]` runs it by hand (port 8080) and
  prints the address of PAGE, for a site whose page isn't at its top:
  `"demo": "shotbox-serve . 8080 demo/"` in a project's scripts.
- `checks()`: `check(cond, what)` and `skip(what)` print `ok`, `FAIL` or
  `skip` lines; `done()` prints the tally and returns the exit status.
- `pageErrors(page)`: every uncaught exception and `console.error`.
- `launch(cmd, args)`: a program in its own process group, with `kill()`
  that reaches its children and `why()` that says how it ended and what it
  printed. `until(test, ms)` polls.
- `gif(film, out, { width, fps, from, colors, dither })`: a Playwright
  recording, or a directory from `frames()`, as a GIF, via ffmpeg, with its own palette. `from` cuts that
  many seconds off the start, `colors` caps the palette, and `dither` is
  ffmpeg's (`none` for flat pages with something moving on them).
- `sealed({ pass, env })`: an environment for a headless browser that
  nothing of yours gets into, as a session's but with no display.
  Otherwise Chromium's fonts come through your `~/.config/fontconfig` and
  `~/.local/share/fonts`, and your pictures aren't anybody else's. Give
  its `env` to `chromium.launch({ env })` and `close()` it afterwards.
  `pass` (a list of names) and `env` (an object) do what `--pass` and
  `--env` do.
- `dress(page, { hold })`: a pointer and captions for a recording, since
  a browser records neither its cursor nor a key pressed. A dot follows
  the mouse and shrinks while a button is down; `caption(text)` shows a
  caption at the bottom for `hold` ms (1300). Both come back after a
  reload; `remove()` takes them out for a still.
- `film(page)`: where the loop starts in a recording. Call it right after
  `newPage()`, `start()` when the loop begins, and `end()` closes the
  context and resolves to the video, for `gif(video, out, { from:
  reel.from })`. Playwright writes a frame only when the page repaints.

- `frames(page, { dir, fps, epoch, seed })`: a recording made a frame
  at a time that comes out the same each run, where `recordVideo` races
  the page and never does. The page's time stands still and moves a
  frame's worth between screenshots: timers, `Date`,
  `requestAnimationFrame`, CSS transitions and animations, and a seeded
  `Math.random`. Launch Chromium with `args: steady`, and call it before
  the page's first navigation. `run(ms)` lets page time pass unrecorded,
  `start()` starts keeping frames, `hold(ms)` stands in for
  `waitForTimeout` and `move(x, y, ms)` for `mouse.move({ steps })`;
  other input lands between frames. `end()` says where they are, for
  `gif(dir, out, { fps })`. Runs match frame for frame, give or take a
  few pixels one level apart; what is real-time and not a timer (a
  fetch, a worker, an iframe loading) is not stood still, though a
  frame that navigates is let load and paint before time moves on.

- `accessible`: Chromium's launch options for `shotbox run --a11y`,
  `chromium.launch({ ...accessible })`: headed, since a headless browser
  isn't on the bus, and without LCD text, whose tinted glyphs `shotbox
  a11y contrast` would measure low. And its pages' options,
  `browser.newPage({ ...accessible })` (or `newContext`): with
  `prefers-reduced-motion: reduce`, so a page that honors it holds still
  to be measured. Playwright's pages emulate no preference unless told,
  which a launch option can't change. To check the motion,
  `{ ...accessible, reducedMotion: 'no-preference' }`.

```js
const context = await browser.newContext({ recordVideo: { dir } });
const page = await context.newPage();
const reel = film(page);
await page.goto(url);
const dressing = await dress(page);
reel.start();
await page.mouse.move(400, 300, { steps: 24 });
await dressing.caption('Alt  Enter');
await page.keyboard.press('Alt+Enter');
await gif(await reel.end(), 'demo.gif', { from: reel.from });
```

It has TypeScript declarations, written by hand beside each module and
checked by `npm run types`. `dress`, `film` and `pageErrors` take
Playwright's `Page`, so they want Playwright's own types installed.

`npm install --save-dev shotbox`. Its `package.json` is at the top of the
repository, since npm installs from the top of a git repository and
nowhere else, so `"shotbox": "github:mishan/shotbox"` in `devDependencies`
works too, or `"file:../shotbox"` for a checkout beside yours.
