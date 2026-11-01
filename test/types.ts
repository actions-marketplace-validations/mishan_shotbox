/*
 * Copyright (C) 2026 Misha Nasledov <misha@nasledov.com>
 *
 * SPDX-License-Identifier: MIT
 */

/*
 * types.ts -- the declarations, used the way a consumer would use them.
 *
 * node/*.d.mts are hand-written, so nothing but this notices when one
 * drifts from the module beside it. Every name is reached through the
 * package's own exports map, not by relative path, which is how
 * TypeScript finds each declaration: beside the file the map names.
 * Compiled and never run: `npm run types'.
 */

import { chromium } from 'playwright';

import { accessible, checks, dress, film, frames, gif, launch, mutated, pageErrors, sealed,
         serve, steady, until } from 'shotbox';
import type { Checks, Dressing, GifOptions, Launched, Recorder, Reel,
              Seal } from 'shotbox';
import { serve as serveToo } from 'shotbox/serve';
import { accessible as accessibleToo } from 'shotbox/a11y';
import { checks as checksToo } from 'shotbox/check';
import { dress as dressToo } from 'shotbox/dress';
import { pageErrors as pageErrorsToo } from 'shotbox/errors';
import { film as filmToo } from 'shotbox/film';
import { frames as framesToo } from 'shotbox/frames';
import { gif as gifToo } from 'shotbox/gif';
import { launch as launchToo } from 'shotbox/launch';
import { mutated as mutatedToo } from 'shotbox/mutate';
import { sealed as sealedToo } from 'shotbox/sealed';

void [serveToo, accessibleToo, checksToo, dressToo, pageErrorsToo, filmToo, framesToo, gifToo,
      launchToo, mutatedToo, sealedToo];

const t: Checks = checks(process.stderr);
const held: boolean = t.check(1 + 1 === 2, 'arithmetic');
const failed: number = t.failures;
t.skip('nothing to skip');

const site = await serve('.', 0, '127.0.0.1');
const port = (site.address() as { port: number }).port;

const seal: Seal = await sealed({ pass: ['DISPLAY'], env: { A: 'b' } });
const home: string | undefined = seal.env.HOME;
const browser = await chromium.launch({ env: seal.env });
const context = await browser.newContext({ recordVideo: { dir: seal.dir } });
await mutated(context);
const page = await context.newPage();
const reel: Reel = film(page);
const errors: string[] = pageErrors(page);
const dressing: Dressing = await dress(page, { hold: 800 });

await page.goto(`http://127.0.0.1:${port}/`);
reel.start();
await dressing.caption('Alt  Enter');
await dressing.remove();

const options: GifOptions = { width: 840, fps: 8, from: reel.from, colors: 64,
                              dither: 'none' };
const out: string = await gif(await reel.end(), 'demo.gif', options);

const steadyPage = await (await chromium.launch({ args: [...steady] })).newPage();
void (await chromium.launch({ ...accessible })).newPage({ ...accessible });
void chromium.launch({ ...accessible, args: [...accessible.args, ...steady] });
const rec: Recorder = await frames(steadyPage, { dir: 'frames', fps: 8, seed: 1 });
await rec.run(1000);
rec.start();
await rec.hold(500);
await rec.move(10, 20, 300);
const took: { dir: string; count: number; fps: number } = await rec.end();
const sofar: number = rec.time + rec.count;
await gif(took.dir, 'frames.gif', { fps: took.fps });

const app: Launched = launch('true', [], { keep: 100 });
const came: boolean = await until(() => app.done, 1000, 50);
const why: string = app.why(1000);
app.kill();

await browser.close();
await seal.close();
site.close();

void [held, failed, home, errors, out, came, why, sofar];
process.exitCode = t.done();

/* And what it should refuse. */

// @ts-expect-error: the palette is a number of colors
void gif('a.webm', 'a.gif', { colors: '64' });
// @ts-expect-error: film() takes a page, not a context
void film(context);
// @ts-expect-error: a seal's env is set once
seal.env = {};
// @ts-expect-error: `from' is read, and start() is how it is set
reel.from = 2;
// @ts-expect-error: pass is a list of names
void sealed({ pass: 'DISPLAY' });
// @ts-expect-error: frames needs somewhere to put them
void frames(steadyPage, { fps: 8 });
// @ts-expect-error: steady is Chromium's flags, not to be changed
steady.push('--enable-gpu');
