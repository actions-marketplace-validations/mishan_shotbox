/*
 * Copyright (C) 2026 Misha Nasledov <misha@nasledov.com>
 *
 * SPDX-License-Identifier: MIT
 */

/*
 * Inside `shotbox run --a11y` (browser.test.mjs): Chromium, launched with
 * `accessible`, on the session's accessibility bus, its page's tree read,
 * its live regions heard before that, a button clicked by its name, a
 * node waited for by its name, one scrolled out of view not, a
 * faint paragraph's contrast measured, said with its DOM tag, text and
 * id, and ignored by them, but not a canvas's fallback text, and one
 * behind an aria-modal dialog said apart, but not behind one off the
 * screen or of no size, a page whose only text is in a field said to
 * have had none measured, a faint link wrapped in dark words and faint
 * words round emoji measured a line and a run at a time, text fading
 * in said as changing, with its range, and held still by reduced
 * motion in a page opened with `accessible`, its page's Tab order
 * walked without the browser's own controls, and how such a walk ends,
 * a node found by its text, a browser that builds no page's tree for a
 * reader told apart from an empty one, a big page read whole, and a
 * crashed tab noted. Prints what it found as JSON.
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
        <div id="intro"><p style="color: #999">Faint words</p></div>
        <canvas width="60" height="20">Canvas words</canvas>
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
    run('wait', 'node', '^Done$', '--window', 'a11y-browser.*', '--timeout', '10');
    const below = spawnSync(shotbox, ['wait', 'node', '^Below$', '--timeout', '0.5'],
                            { encoding: 'utf8' }).stderr;
    const clicked = await page.evaluate(() => document.querySelector('button').dataset.clicked);
    const contrast = spawnSync(shotbox, ['a11y', 'contrast', '--window', 'a11y-browser.*'],
                               { encoding: 'utf8' }).stdout;
    const ignored = spawnSync(shotbox, ['a11y', 'contrast', '--ignore', 'div#intro',
                                        '--window', 'a11y-browser.*'], { encoding: 'utf8' });

    /* Behind an aria-modal dialog, on its dimmed backdrop: said apart;
       not behind one translated off the screen, or of no size. */
    const dialog = (style) => `<div role="dialog" aria-modal="true" aria-label="Settings"
        style="position: fixed; top: 100px; left: 100px; background: #fff; ${style}">
        <p>Inside words</p></div>`;
    const modals = {
        'a11y-modal': `<div style="position: fixed; inset: 0; background: rgb(0 0 0 / 50%)"></div>
            ${dialog('padding: 20px')}`,
        'a11y-unshown': dialog('transform: translateX(-3000px)') +
            dialog('width: 0; height: 0; overflow: hidden'),
    };
    const behind = {};

    for (const [title, html] of Object.entries(modals))
    {
        const modal = await browser.newPage();

        await modal.setContent(`<title>${title}</title>
            <p style="color: #999">Faint words</p>${html}`);

        for (let i = 0; i < 50 && !run('a11y', 'tree', '--window', `${title}.*`)
            .includes('"Inside words"'); i++)
        {
            await modal.waitForTimeout(200);
        }

        behind[title] = JSON.parse(spawnSync(shotbox, ['a11y', 'contrast', '--json', '--window',
                                                       `${title}.*`], { encoding: 'utf8' }).stdout);
        await modal.close();
    }

    const lines = await browser.newPage();

    await lines.setContent(`<title>a11y-runs</title>
        <p style="width: 300px">Dark words dark words dark <a href="#" style="color: #ccc">a
          faint link that wraps across lines</a> dark words.</p>
        <p style="color: #bbb">Faint \u{1F600}\u{1F600} emoji words</p>`);
    for (let i = 0; i < 50 && !run('a11y', 'tree', '--window', 'a11y-runs.*')
        .includes('emoji words'); i++)
    {
        await lines.waitForTimeout(200);
    }
    const runs = JSON.parse(spawnSync(shotbox, ['a11y', 'contrast', '--json', '--window',
                                                'a11y-runs.*'], { encoding: 'utf8' }).stdout);
    await lines.close();

    const typed = await browser.newPage();

    await typed.setContent('<title>a11y-typed</title><input value="Typed words">');
    for (let i = 0; i < 50 && !run('a11y', 'tree', '--window', 'a11y-typed.*')
        .includes('entry'); i++)
    {
        await typed.waitForTimeout(200);
    }
    const field = spawnSync(shotbox, ['a11y', 'contrast', '--window', 'a11y-typed.*'],
                            { encoding: 'utf8' });
    await typed.close();

    /* Fading below what text needs throughout, above it, and across it;
       blinking each second; faint but for a flash, half a second in two;
       and faint text being typed in, its caret blinking black. */
    const motion = `.fails, .flash { color: #c8c8c8 } .passes { color: #595959 }
        p, div { color: #a0a0a0 }
        @keyframes fails { to { color: #a8a8a8 } } @keyframes passes { to { color: #000 } }
        @keyframes words { to { color: #202020 } }
        @keyframes blink { 0%, 49.9% { color: #a0a0a0 } 50%, 100% { color: #202020 } }
        @keyframes flash { 0%, 74.9% { color: #c8c8c8 } 75%, 100% { color: #000 } }
        @media (prefers-reduced-motion: no-preference) {
            p { animation: words 1.6s linear infinite } .fails { animation-name: fails }
            .passes { animation-name: passes } .blink { animation: blink 1s linear infinite }
            .flash { animation: flash 2s linear infinite } }`;
    const fading = {};
    const pages = { 'a11y-fading': { ...accessible, reducedMotion: 'no-preference' },
                    'a11y-still': { ...accessible } };

    for (const [title, options] of Object.entries(pages))
    {
        const moving = await browser.newPage(options);

        await moving.setContent(`<title>${title}</title><style>${motion}</style>
            <p class="fails">Fading fails</p><p class="passes">Fading passes</p>
            <p>Fading words</p><p class="blink">Blinking words</p>
            <p class="flash">Flashing words</p>
            <div contenteditable style="caret-color: #000">Typed words</div>`);
        await moving.focus('div');
        for (let i = 0; i < 50 && !run('a11y', 'tree', '--window', `${title}.*`)
            .includes('"Fading words"'); i++)
        {
            await moving.waitForTimeout(200);
        }
        /* The same each time, whenever the first picture is taken. */
        fading[title] = Array.from({ length: title === 'a11y-fading' ? 10 : 1 }, () => JSON.parse(
            spawnSync(shotbox, ['a11y', 'contrast', '--json', '--passes', '--window',
                                `${title}.*`], { encoding: 'utf8' }).stdout));
        await moving.close();
    }

    /* The page's Tab order: through the browser's controls, but only
       the page's said, Tab going on from a textarea; and without
       --document, the page's button Tab skips said too, though Tab
       lands on the document it's in. */
    const tabbed = await browser.newPage();

    await tabbed.setContent(`<title>a11y-tabdoc</title><button>One</button>
        <textarea aria-label="Notes">hi</textarea><a href="#two">Two</a><button tabindex="-1">Off</button>`);
    await tabbed.focus('button');
    const walk = JSON.parse(run('a11y', 'tabwalk', '--document', '--json',
                                '--window', 'a11y-tabdoc.*'));
    const around = JSON.parse(run('a11y', 'tabwalk', '--json', '--window', 'a11y-tabdoc.*'));
    await tabbed.close();

    /* A node by its text, which comes back cut round what matched, and
       by its text round an element in it; one scrolled out of view said
       by its text, having no name; and the part of a long text near a
       pattern that isn't in it. */
    const texts = await browser.newPage();

    await texts.setContent(`<title>a11y-text</title>
        <p>${'Lorem ipsum. '.repeat(100)}Drawn${' Lorem ipsum.'.repeat(100)}</p>
        <p>Done <a href="#">here</a> now</p>
        <p style="margin-top: 3000px">Further down</p>`);
    const long = JSON.parse(run('a11y', 'node', 'Drawn', '--json', '--window', 'a11y-text.*'));
    run('wait', 'node', '^Done now$', '--window', 'a11y-text.*', '--timeout', '0');
    const further = spawnSync(shotbox, ['wait', 'node', 'Further', '--window', 'a11y-text.*',
                                        '--timeout', '0'], { encoding: 'utf8' }).stderr;
    const nearest = spawnSync(shotbox, ['wait', 'node', 'ipsum. Drwan Lorem', '--window',
                                        'a11y-text.*', '--timeout', '0'], { encoding: 'utf8' }).stderr;
    await texts.close();

    /* How a walk ends: on a web editor that keeps Tab (F6 takes
       Chromium into its toolbar, and Tab never comes back), twice in a
       row, the second beginning in it; and with --document, on a page
       with nothing to focus, one whose controls are all out of the Tab
       order, and one that keeps Tab for itself. */
    const ends = {};

    for (const [name, html, walks] of [
        ['editor', `<button>A</button><textarea aria-label="Code">x</textarea><button>B</button>
            <script>document.querySelector('textarea').onkeydown = (e) =>
            { if (e.key === 'Tab') e.preventDefault(); };</script>`, [[], []]],
        ['empty', '<p>Nothing here</p>', [['--document']]],
        ['allneg', '<button tabindex="-1">A</button><button tabindex="-1">B</button>',
         [['--document']]],
        ['trapdoc', `<button>A</button><button>B</button><script>document.addEventListener(
            'keydown', (e) => { if (e.key === 'Tab') e.preventDefault(); }, true);</script>`,
         [['--document']]],
    ])
    {
        const p = await browser.newPage();

        await p.setContent(`<title>a11y-${name}</title>${html}`);
        if (name === 'editor')
            await p.focus('button');
        ends[name] = walks.map((args) => JSON.parse(run('a11y', 'tabwalk', ...args, '--json',
                                                        '--window', `a11y-${name}.*`)).end);
        await p.close();
    }

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
        below,
        contrast,
        ignored: { status: ignored.status, out: ignored.stdout },
        behind,
        field: { status: field.status, out: field.stdout },
        runs,
        fading,
        walk,
        around,
        long,
        further,
        nearest,
        ends,
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
