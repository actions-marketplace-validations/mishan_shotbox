/*
 * Copyright (C) 2026 Misha Nasledov <misha@nasledov.com>
 *
 * SPDX-License-Identifier: MIT
 */

/*
 * The helpers that need a browser, in one: Playwright's Chromium, if it is
 * installed (`npm install && npx playwright install chromium`). Without it
 * they skip, saying so.
 */

import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { after, before, test } from 'node:test';

import { dress, film, frames, gif, pageErrors, sealed, steady } from '../node/index.mjs';

let chromium = null, why = false;

try
{
    ({ chromium } = await import('playwright'));
    await fs.access(chromium.executablePath());
}
catch (e)
{
    why = `no Playwright Chromium (${e.message.split('\n')[0]})`;
}

const has = (cmd) =>
{
    try { execFileSync(cmd, ['-version'], { stdio: 'ignore' }); return true; }
    catch { return false; }
};

let seal, browser;

before(async () =>
{
    if (why)
        return;
    seal = await sealed();
    browser = await chromium.launch({ env: seal.env, args: steady });
});

after(async () =>
{
    await browser?.close();
    await seal?.close();
});

const PAGE = 'data:text/html,<body style="margin:0;height:100vh"></body>';

test('dress draws a pointer that follows the mouse', { skip: why }, async () =>
{
    const page = await browser.newPage();

    try
    {
        await page.goto(PAGE);
        await dress(page);

        const pointer = page.locator('[data-shotbox=pointer]');

        await page.mouse.move(120, 80);
        assert.deepEqual(await pointer.evaluate((n) => [n.style.left, n.style.top]),
                         ['120px', '80px']);

        await page.mouse.down();
        assert.match(await pointer.evaluate((n) => n.style.transform), /0\.6/);
        await page.mouse.up();

        /* And it takes no events: a click lands on what is under it. */
        assert.equal(await page.evaluate(() =>
            document.elementFromPoint(120, 80).dataset.shotbox), undefined);

        /* Back after a reload, once there is a body to put it in. */
        await page.reload();
        assert.equal(await page.locator('[data-shotbox]').count(), 2);
    }
    finally
    {
        await page.close();
    }
});

test('dress captions fade on their own, and remove takes it all out',
     { skip: why }, async () =>
{
    const page = await browser.newPage();

    try
    {
        await page.goto(PAGE);

        const dressing = await dress(page, { hold: 200 });
        const shown = () => page.locator('[data-shotbox=caption]').evaluate(
            (n) => [n.textContent, n.style.opacity]);

        await dressing.caption('Alt  Enter');
        assert.deepEqual(await shown(), ['Alt  Enter', '1']);
        await page.waitForTimeout(400);
        assert.deepEqual(await shown(), ['Alt  Enter', '0']);

        await dressing.remove();
        assert.equal(await page.locator('[data-shotbox]').count(), 0);
    }
    finally
    {
        await page.close();
    }
});

test('dress is one set however often it is asked, and none in an iframe',
     { skip: why }, async () =>
{
    const page = await browser.newPage();
    const count = () => page.locator('[data-shotbox]').count();

    try
    {
        await page.goto(PAGE);
        await dress(page);

        const dressing = await dress(page);

        assert.equal(await count(), 2);
        await page.reload();
        assert.equal(await count(), 2);

        /* Out means out, and asked again, back. */
        await dressing.remove();
        assert.equal(await count(), 0);

        const again = await dress(page);

        assert.equal(await count(), 2);
        await again.caption('back');
        assert.equal(await page.locator('[data-shotbox=caption]').textContent(),
                     'back');

        /* An iframe loaded after, which the init script also runs in. */
        await page.evaluate(() => new Promise((ok) =>
        {
            const frame = document.createElement('iframe');

            frame.srcdoc = '<body>inside</body>';
            frame.onload = ok;
            document.body.append(frame);
        }));
        assert.equal(await page.frames()[1].locator('[data-shotbox]').count(), 0);
        await again.remove();
        assert.equal(await count(), 0);
    }
    finally
    {
        await page.close();
    }
});

test('pageErrors hears both a throw and a console.error', { skip: why }, async () =>
{
    const page = await browser.newPage();

    try
    {
        const errors = pageErrors(page);

        await page.goto(PAGE);
        await page.evaluate(() =>
        {
            console.error('said');
            setTimeout(() => { throw new Error('thrown'); });
        });
        await page.waitForTimeout(100);
        assert.equal(errors.length, 2, errors.join(' | '));
    }
    finally
    {
        await page.close();
    }
});

test('film says where the loop starts, and gif cuts there',
     { skip: why || (!has('ffmpeg') && 'no ffmpeg') }, async () =>
{
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'shotbox-test-'));
    const plain = await browser.newPage();

    try
    {
        assert.throws(() => film(plain), /not being recorded/);

        const context = await browser.newContext({
            viewport: { width: 160, height: 120 },
            recordVideo: { dir, size: { width: 160, height: 120 } },
        });
        const page = await context.newPage();
        const t0 = performance.now();
        const reel = film(page);
        const t1 = performance.now();

        /* Red for the part to cut, blue from the start of the loop. And
           something moving in the corner: Playwright writes a frame only
           when the page repaints, so a page that holds still leaves
           nothing after the cut. */
        await page.goto(PAGE);
        await page.evaluate(() =>
        {
            const tick = document.body.appendChild(document.createElement('i'));
            const on = () => { tick.textContent = performance.now(); requestAnimationFrame(on); };

            on();
            document.body.style.background = '#ff0000';
        });
        await page.waitForTimeout(1500);
        await page.evaluate(() => { document.body.style.background = '#0000ff'; });
        await page.waitForTimeout(100);
        const t2 = performance.now();

        reel.start();

        const t3 = performance.now();

        await page.waitForTimeout(1500);

        /* Seconds from film() to start(), not milliseconds, and not from
           anything else. */
        assert.ok(reel.from >= (t2 - t1) / 1000 && reel.from <= (t3 - t0) / 1000,
                  `from ${reel.from}`);

        const video = await reel.end();
        const first = async (name, from) =>
        {
            const out = await gif(video, path.join(dir, name),
                                  { width: 160, fps: 10, from });
            const px = execFileSync('convert', [`${out}[0]`, '-format',
                                                '%[fx:int(255*p{80,60}.r)],%[fx:int(255*p{80,60}.b)]',
                                                'info:']).toString();
            const [r, b] = px.split(',').map(Number);

            return r > 200 && b < 60 ? 'red' : r < 60 && b > 200 ? 'blue' : px;
        };

        /* Uncut, it starts before the loop; cut, at it. */
        assert.notEqual(await first('all.gif', 0), 'blue');
        assert.equal(await first('loop.gif', reel.from), 'blue');
    }
    finally
    {
        await plain.close();
        await fs.rm(dir, { recursive: true, force: true });
    }
});

/* A page with one of each thing frames() has to stand still: a CSS
   transition, a timer, Date, and Math.random. */
const BUSY = `<body style="margin:0">
<div id=box style="width:20px;height:20px;background:#f00;
                   transition:transform 1s linear"></div>
<p id=text></p>
<script>
setInterval(() => { text.textContent = Date.now() + ' ' + Math.random(); }, 100);
</script>
</body>`;

const record = async (dir) =>
{
    const context = await browser.newContext({ viewport: { width: 200, height: 100 } });
    const page = await context.newPage();

    try
    {
        const rec = await frames(page, { fps: 10, dir });

        await page.setContent(BUSY);
        await rec.run(200);
        rec.start();
        await rec.hold(300);
        await page.evaluate(() => { box.style.transform = 'translateX(150px)'; });
        await rec.hold(500);
        await rec.move(100, 50, 300);

        return { ...(await rec.end()),
                 text: await page.locator('#text').textContent() };
    }
    finally
    {
        await context.close();
    }
};

test('frames: the same frames each run, a frame per step of page time',
     { skip: why || (!has('ffmpeg') && 'no ffmpeg') }, async () =>
{
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'shotbox-test-'));
    const px = (file, y) => execFileSync('convert', [file, '-crop', `200x1+0+${y}`,
                                                     'txt:-']).toString();

    try
    {
        const a = await record(path.join(dir, 'a'));
        const b = await record(path.join(dir, 'b'));

        /* 300 + 500 + 300 ms at 10 fps; Date is the epoch and 1.3s. */
        assert.equal(a.count, 11);
        assert.equal(b.count, 11);
        assert.equal(a.text, b.text);
        assert.match(a.text, /^1767225601300 /);

        for (let i = 0; i < a.count; i++)
        {
            const name = `${String(i).padStart(5, '0')}.png`;
            /* compare says how many on stderr, and exits 1 for any. */
            const diff = spawnSync('compare', [
                '-metric', 'AE', '-fuzz', '1%',
                path.join(a.dir, name), path.join(b.dir, name), 'null:',
            ]).stderr.toString();

            assert.equal(Number(diff.split(' ')[0]), 0, `${name}: ${diff}`);
        }

        /* The transition runs on page time: 150px a second is 15 a frame. */
        const left = (i) =>
        {
            const row = px(path.join(a.dir, `${String(i).padStart(5, '0')}.png`), 10);
            const red = row.split('\n').filter((l) => /#FF0000/i.test(l));

            return Number(red[0]?.split(',')[0]);
        };

        assert.equal(left(6) - left(4), 30);

        /* And the gif has a frame for each, no more, no fewer. */
        const out = await gif(a.dir, path.join(dir, 'a.gif'), { fps: 10, width: 200 });

        assert.equal(execFileSync('identify', [out]).toString().trim()
                         .split('\n').length, 11);
    }
    finally
    {
        await fs.rm(dir, { recursive: true, force: true });
    }
});

/* What an --a11y session needs, as test/api.py asks it. */
const atspi = () =>
{
    const launcher = ['/usr/libexec', '/usr/lib/at-spi2-core', '/usr/lib/at-spi2']
        .some((d) => spawnSync('test', ['-x', `${d}/at-spi-bus-launcher`]).status === 0);
    const gir = spawnSync('python3', ['-c',
        'import gi; gi.require_version("Atspi", "2.0")']).status === 0;

    return launcher && gir ? false : 'no at-spi (at-spi2-core, gir1.2-atspi-2.0)';
};

test('Chromium on an --a11y session: its tree, a click by name, a node waited for, contrast, behind a modal too, the page\'s own Tab order, live regions, no page, a big page, a crashed tab',
     { skip: why || atspi() }, () =>
{
    const here = path.dirname(new URL(import.meta.url).pathname);
    const shotbox = path.join(here, '..', 'bin', 'shotbox');

    const out = execFileSync(shotbox, [
        'run', '--a11y', '--',
        'node', path.join(here, 'a11y_browser.mjs'), shotbox,
    ], { encoding: 'utf8' });
    const { tree, clicked, below, contrast, ignored, behind, walk, around, long, further, nearest, ends, said, hint, whole, crash } = JSON.parse(out);

    assert.match(tree, /button "Save"/);
    /* Scrolled out of view: not showing, but still in a page's tree. */
    assert.match(tree, /button "Below"/);
    assert.equal(clicked, 'yes');
    assert.match(below, /after 0\.5s: (push )?button "Below" \[focusable\] matches, but isn't showing$/m);
    assert.match(contrast, /^\S.*paragraph\[\d+\]\/static\[0\]: text 2\.8[45]:1, #999999 on #ffffff.*  <- p "Faint words" in div#intro$/m);
    /* Large text, 18pt or 14pt bold, passes at 3.45:1. */
    assert.equal(contrast.trim().split('\n').length, 1);
    assert.deepEqual(ignored, { status: 0, out: '' });
    assert.deepEqual(behind['a11y-modal'].problems, []);
    assert.deepEqual(behind['a11y-modal'].behind.map((n) => [n.name, n.behind, n.dom, n.text]),
                     [['Faint words', 'text behind a modal dialog "Settings"', { tag: 'p' },
                       'Faint words']]);
    assert.deepEqual(behind['a11y-unshown'].behind, []);
    assert.deepEqual(behind['a11y-unshown'].problems.map((n) => n.name), ['Faint words']);
    assert.deepEqual([walk.end, walk.focus.map((n) => n.name), walk.unreached.map((n) => n.name)],
                     ['cycle', ['Notes', 'Two', 'One'], ['Off']]);
    assert.ok(around.unreached.some((n) => n.name === 'Off'), JSON.stringify(around.unreached));
    assert.match(long.text, /^….+Drawn.+…$/);
    assert.equal(long.text.length, 82);
    assert.match(further, /: paragraph "", its text "Further down", matches, but isn't showing$/m);
    assert.match(nearest, /The nearest: "….*ipsum\. Drawn Lorem/);
    assert.deepEqual(ends, { editor: ['escaped', 'escaped'], empty: ['cycle'],
                             allneg: ['trap'], trapdoc: ['trap'] });
    assert.equal(said, 'polite: Saved to notes\nassertive: Connection lost\n' +
                       'polite: Done\n');
    assert.match(hint, /"a11y-unread.*": no page in the browser's accessible tree \(a crashed tab, .*--force-renderer-accessibility/);
    assert.match(whole, /button "b2999"/);
    assert.equal(crash.status, 0, crash.err);
    assert.match(crash.err, /"a11y-crashed.*": no page in the browser's accessible tree \(a crashed tab/);
    assert.match(crash.out, /button "Save"/);
});
