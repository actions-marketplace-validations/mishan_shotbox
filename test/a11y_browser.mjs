/*
 * Copyright (C) 2026 Misha Nasledov <misha@nasledov.com>
 *
 * SPDX-License-Identifier: MIT
 */

/*
 * Inside `shotbox run --a11y` (browser.test.mjs): Chromium, headed, on the
 * session's accessibility bus with only its own switch added to what the
 * session sets, its page's tree read and its live regions heard. Prints
 * what it found as JSON.
 */

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';

import { chromium } from 'playwright';

const shotbox = process.argv[2];
const run = (...args) => execFileSync(shotbox, args, { encoding: 'utf8' });

const browser = await chromium.launch({
    headless: false, args: ['--force-renderer-accessibility'] });

try
{
    const page = await browser.newPage();

    await page.setContent(`<title>a11y-browser</title>
        <button>Save</button>
        <div role="status" id="polite"></div>
        <div aria-live="assertive" id="rude"></div>
        <div id="quiet"></div>`);

    /* The page's part of the tree comes a moment after the window's. */
    let tree = '';

    for (let i = 0; i < 50 && !tree.includes('"Save"'); i++)
    {
        await page.waitForTimeout(200);
        tree = run('a11y', 'tree', '--window', 'a11y-browser.*');
    }

    /* The one not live goes first: had it been heard, it would be in the
       file before the waits below are done. */
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

    process.stdout.write(JSON.stringify({
        tree,
        said: fs.readFileSync(process.env.SHOTBOX_ANNOUNCEMENTS, 'utf8'),
    }));
}
finally
{
    await browser.close();
}
