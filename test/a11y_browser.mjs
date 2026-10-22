/*
 * Copyright (C) 2026 Misha Nasledov <misha@nasledov.com>
 *
 * SPDX-License-Identifier: MIT
 */

/*
 * Inside `shotbox run --a11y` (browser.test.mjs): Chromium, launched with
 * `accessible`, on the session's accessibility bus, its page's tree read,
 * its live regions heard before that, a button clicked by its name, a
 * faint paragraph's contrast measured, a browser that builds no page's
 * tree for a reader told apart from an empty one, a big page read whole,
 * and a crashed tab noted. Prints what it found as JSON.
 */

import { execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';

import { chromium } from 'playwright';

import { accessible } from '../node/a11y.mjs';

const shotbox = process.argv[2];
const run = (...args) => execFileSync(shotbox, args, { encoding: 'utf8' });

const browser = await chromium.launch({ ...accessible });

try
{
    const page = await browser.newPage();

    await page.setContent(`<title>a11y-browser</title>
        <button onclick="this.dataset.clicked = 'yes'">Save</button>
        <p style="color: #999">Faint words</p>
        <p style="color: #8a8a8a; font-size: 18pt">Large words</p>
        <p style="color: #8a8a8a; font-size: 14pt; font-weight: bold">Bold words</p>
        <div role="status" id="polite"></div>
        <div aria-live="assertive" id="rude"></div>
        <div id="quiet"></div>
        <button style="margin-top: 3000px">Below</button>`);

    /* Heard before anything has read the tree. The one not live goes
       first: had it been heard, it would be in the file before the waits
       below are done. */
    const set = (id, text) => page.evaluate(([i, t]) =>
    {
        document.getElementById(i).textContent = t;
    }, [id, text]);

    await set('quiet', 'not live');
    await set('polite', 'Saved to notes');
    run('wait', 'announce', 'Saved to notes', '--timeout', '10');
    await set('rude', 'Connection lost');
    run('wait', 'announce', 'Connection lost', '--timeout', '10');

    /* An element put in a region: its text, and not the character that
       stands in for it in the region's. */
    await page.evaluate(() =>
    {
        document.getElementById('polite').innerHTML = '<p>Done</p>';
    });
    run('wait', 'announce', 'Done', '--timeout', '10');

    /* The page's part of the tree comes a moment after the window's. */
    let tree = '';

    for (let i = 0; i < 50 && !tree.includes('"Save"'); i++)
    {
        await page.waitForTimeout(200);
        tree = run('a11y', 'tree', '--window', 'a11y-browser.*');
    }

    run('a11y', 'click', 'Save', '--window', 'a11y-browser.*');
    const clicked = await page.evaluate(() => document.querySelector('button').dataset.clicked);
    const contrast = spawnSync(shotbox, ['a11y', 'contrast', '--window', 'a11y-browser.*'],
                               { encoding: 'utf8' }).stdout;

    /* One that won't build its page's tree for a reader. */
    const blind = await chromium.launch({ ...accessible,
        args: [...accessible.args, '--disable-platform-accessibility-integration'] });
    const unread = await blind.newPage();

    await unread.setContent('<title>a11y-unread</title><button>Hidden</button>');
    const hint = spawnSync(shotbox, ['a11y', 'tree', '--window', 'a11y-unread.*'],
                           { encoding: 'utf8' }).stderr;
    await blind.close();

    /* A big page, in a browser not yet asked for its tree: read whole,
       not as the empty document Chromium shows while it builds it. */
    const fresh = await chromium.launch({ ...accessible });
    const big = await fresh.newPage();

    await big.setContent('<title>a11y-big</title>' +
        Array.from({ length: 3000 }, (_, i) => `<button>b${i}</button>`).join(''));
    const whole = run('a11y', 'tree', '--window', 'a11y-big.*');
    await fresh.close();

    /* A crashed tab: noted, and the rest read. */
    const crashed = await browser.newPage();

    await crashed.setContent('<title>a11y-crashed</title>');
    const gone = new Promise((r) => crashed.once('crash', r));

    await crashed.goto('chrome://crash').catch(() => {});
    await gone;
    const all = spawnSync(shotbox, ['a11y', 'tree'], { encoding: 'utf8' });

    process.stdout.write(JSON.stringify({
        tree,
        clicked,
        contrast,
        said: fs.readFileSync(process.env.SHOTBOX_ANNOUNCEMENTS, 'utf8'),
        hint,
        whole,
        crash: { status: all.status, out: all.stdout, err: all.stderr },
    }));
}
finally
{
    await browser.close();
}
