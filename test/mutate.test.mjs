/*
 * Copyright (C) 2026 Misha Nasledov <misha@nasledov.com>
 *
 * SPDX-License-Identifier: MIT
 */

/*
 * `mutated`, in a browser: Playwright's Chromium, if it is installed
 * (`npm install && npx playwright install chromium`). Without it they
 * skip, saying so.
 */

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { after, before, test } from 'node:test';

import { frames, mutated, pageErrors, sealed, steady, until } from '../node/index.mjs';

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

/* Pages for `mutated`, a context each, on a host of their own under a
   strict CSP and Trusted Types, their scripts let in by nonce. Each but
   the last is closed as soon as it has loaded, as a test that is quickly
   done would; on the last, what `act` does, what the run's report says
   once `done` is, or a second and a half on, and what `look` sees of it
   then. */
const CSP = "default-src 'none'; script-src 'nonce-n'; style-src 'none'; " +
            "frame-src http://b.test; require-trusted-types-for 'script'; trusted-types 'none'";

async function mutation (spec, pages, { frame = '', act = () => null, done = (r) => r.proven,
                                        look = () => null, on = browser } = {})
{
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'shotbox-test-'));
    const read = async () => JSON.parse(await fs.readFile(path.join(dir, `${process.pid}.json`), 'utf8')
                                                .catch(() => 'null'));
    const errors = [];

    Object.assign(process.env, { SHOTBOX_MUTATION: JSON.stringify(spec),
                                 SHOTBOX_MUTATION_REPORT: dir });
    try
    {
        for (const [i, html] of [pages].flat().entries())
        {
            const context = await on.newContext();

            try
            {
                await context.route(/^http:\/\/[ab]\.test\//, (r) =>
                    r.request().url().startsWith('http://a')
                        ? r.fulfill({ contentType: 'text/html', body: html,
                                      headers: { 'Content-Security-Policy': CSP } })
                        : r.fulfill({ contentType: 'text/html', body: frame }));
                await mutated(context);

                const page = await context.newPage();

                pageErrors(page, errors);
                await page.goto('http://a.test/');
                if (i < [pages].flat().length - 1)
                {
                    await page.close();
                    continue;
                }
                await act(page);
                await until(async () => done(await read() ?? {}), 1500);
                const saw = await look(page);

                /* Closed, as the report is only done then. */
                await context.close();
                return { report: await read(), errors, saw };
            }
            finally
            {
                await context.close();
            }
        }
    }
    finally
    {
        delete process.env.SHOTBOX_MUTATION;
        delete process.env.SHOTBOX_MUTATION_REPORT;
        await fs.rm(dir, { recursive: true, force: true });
    }
}

/* How often, sampled every 20 ms for a second, the test could find a
   button "Pay" showing, as a test would look for it. */
const seenBy = async (page) =>
{
    let seen = 0;

    for (let i = 0; i < 50; i++, await page.waitForTimeout(20))
        seen += await page.getByRole('button', { name: 'Pay', exact: true }).filter({ visible: true }).count();
    return seen;
};

/* How many of the elements `css` finds are showing, in every frame. */
const showing = async (page, css) =>
    (await Promise.all(page.frames().map((f) => f.locator(`${css} >> visible=true`).count())))
        .reduce((a, b) => a + b);

const PAY = '<button>Pay</button>';
const TOTAL = '<div><p>Total: <b>42.50</b></p><button data-kept aria-label="Other"></button></div>';
const FRAME = '<iframe src="http://b.test/"></iframe>';
/* A button named "Pay" by its style, which the page side can't read. */
const BEFORE = `<button id="p"></button><script nonce="n">
    const sheet = new CSSStyleSheet(); sheet.replaceSync('#p::before { content: "Pay" }');
    document.adoptedStyleSheets = [sheet]</script>`;
const pay = { by: 'role', role: 'button', name: 'Pay' };
/* A button "Pay" 40 by 20 px at 200, 200, over another, "Other", at each
   of `offsets` from it. */
const around = (offsets) => '<body>' + script(`for (const [x, y, name] of [...${JSON.stringify(offsets)}, [0, 0, 'Pay']])
    { const b = document.createElement('button'); b.textContent = name ?? 'Other';
      Object.assign(b.style, { position: 'absolute', left: 200 + x + 'px', top: 200 + y + 'px',
                               width: '40px', height: '20px', padding: '0', border: '0' });
      document.body.append(b); }`);
const script = (js) => `<script nonce="n">${js}</script>`;
const later = (js) => script(`setTimeout(() => { ${js} }, 100)`);
const shadow = (js) => '<div id="host"></div>' + script(`const fill = () =>
    { const b = document.createElement('button'); b.textContent = 'Pay';
      document.getElementById('host').attachShadow({ mode: 'open' }).append(b); }; ${js}`);

test('mutated hides the target, proven by the locator the test would use: by role, name, regex, text and selector, not its hidden twin, before the page\'s own scripts, as it remounts, in a shadow root and a cross-origin frame, for a test that closes it at once',
     { skip: why }, async () =>
{
    const cases = [
        ['a role and its name, not its hidden twin', pay, `<button hidden>Pay</button>${PAY}`],
        ['a role and a regex', { by: 'role', role: 'button', re: 'P.y' }, PAY],
        ['a name that is only part of another\'s', pay, `${PAY}<button data-kept>Pay later</button>`],
        ['text, the nearest element all of it is in', { by: 'text', name: 'Total: 42.50' }, TOTAL],
        ['text by regex', { by: 'text', re: 'Total: .*' }, TOTAL],
        ['a selector', { by: 'css', css: '#pay' }, '<button id="pay">Go</button>'],
        ['a name from aria-label', { by: 'role', role: 'link', name: 'Pay' },
         '<a href="#" aria-label="Pay">$</a>'],
        ['a name from aria-labelledby', { by: 'role', role: 'textbox', name: 'Card number' },
         '<span id="l">Card <i>number</i></span><input aria-labelledby="l">'],
        ['a name from its label', { by: 'role', role: 'checkbox', name: 'Save it' },
         '<label for="c">Save it</label><input type="checkbox" id="c">'],
        ['a name from alt', { by: 'role', role: 'img', name: 'Logo' },
         '<img alt="Logo" width="10" height="10">'],
        ['a name from what is in it, less what is hidden', pay,
         '<button><img alt="Pay" width="10" height="10"><span hidden>now</span></button>'],
        ['a name given later to what is in it', pay, '<button><span></span></button>' + script(`
             setTimeout(() => document.querySelector('span').setAttribute('aria-label', 'Pay'), 100)`)],
        ['a name from its value', { by: 'role', role: 'button', name: 'Send' },
         '<input type="submit" value="Send">'],
        ['a name from title', { by: 'role', role: 'textbox', name: 'Email' },
         '<input title="Email">'],
        ['a name from placeholder', { by: 'role', role: 'textbox', name: 'Email' },
         '<input placeholder="Email">'],
        ['a name a submit button has by default', { by: 'role', role: 'button', name: 'Submit' },
         '<input type="submit">'],
        ['a name less text hidden by its style', pay, '<p>Due</p>' + script(`const b = document.createElement('button');
             const s = document.createElement('span'); s.textContent = '!'; s.style.display = 'none';
             b.append('Pay', s); document.body.append(b)`)],
        ['a region', { by: 'role', role: 'region', name: 'Cart' }, '<section aria-label="Cart">x</section>'],
        ['a row header', { by: 'role', role: 'rowheader', name: 'Total' },
         '<table><tr><th scope="row">Total</th><td>1</td></tr></table>'],
        ['a selector that matches once a box is ticked', { by: 'css', css: '#c:checked + #pay' },
         `<input type="checkbox" id="c"><button id="pay">Go</button>${
             later("document.getElementById('c').checked = true")}`],
        ['before the page\'s scripts run', pay,
         PAY + script('document.title = getComputedStyle(document.querySelector("button")).display')],
        ['as it remounts every 20 ms', pay, PAY + script(`setInterval(() =>
             { const b = document.createElement('button'); b.textContent = 'Pay';
               document.querySelector('button').replaceWith(b); }, 20)`)],
        ['in an open shadow root', pay, shadow('fill()')],
        ['in an open shadow root filled after it has loaded', pay, shadow('setTimeout(fill, 100)')],
        ['in a shadow root filled before its host is added', pay, later(`
             const h = document.createElement('div'), b = document.createElement('button');
             b.textContent = 'Pay'; h.attachShadow({ mode: 'open' }).append(b);
             setTimeout(() => document.body.append(h), 100)`)],
        ['in a declarative shadow root', pay, `<div><template shadowrootmode="open">${PAY}</template></div>`],
        ['shown by a popover again and again, never where the test could find it', pay,
         script(`setInterval(() => { document.querySelectorAll('[popover]').forEach((p) => p.remove());
             const d = document.createElement('div'), b = document.createElement('button');
             d.popover = 'manual'; b.textContent = 'Pay'; d.append(b); document.body.append(d);
             setTimeout(() => d.isConnected && d.showPopover(), 30); }, 300)`)],
        ['shown by a popover, with nothing in the page changing', pay,
         `<div popover id="p">${PAY}</div>${later("document.getElementById('p').showPopover()")}`],
        ['that would fade out', pay, later(`const b = document.createElement('button');
             b.textContent = 'Pay'; b.style.transition = 'display 1s allow-discrete';
             document.body.append(b)`)],
        ['drawn again each frame', pay, PAY + script(`const b = document.querySelector('button');
             const f = () => { b.style.removeProperty('display'); requestAnimationFrame(f); }; f()`)],
        ['drawn again every 200 ms, out of sight in a frame', pay,
         FRAME + script("document.querySelector('iframe').style.marginTop = '3000px'"),
         PAY + `<script>const b = document.querySelector('button');
             setInterval(() => b.style.removeProperty('display'), 200)</script>`],
        ['drawn again each frame, many of it', pay, PAY.repeat(12) + script(`
             const f = () => { for (const b of document.querySelectorAll('button'))
                 b.style.removeProperty('display'); requestAnimationFrame(f); }; f()`)],
        ['shown later in what held it hidden', pay, `<div hidden><button><span>Pay</span></button></div>${
             later("document.querySelector('div').hidden = false")}`],
        ['a selector that comes to match what holds a match', { by: 'css', css: '.t' },
         `<div id="o"><span class="t">Pay</span> more</div>${
             later("document.getElementById('o').className = 't'")}`],
        ['text that is only part of other text', { by: 'text', name: 'Pay' },
         '<button>Pay</button><p>Pay later</p>'],
        ['text that is also an option not shown', { by: 'text', name: 'Monthly' },
         '<button>Monthly</button><select><option>Yearly</option><option>Monthly</option></select>'],
        ['text under aria-hidden, which a test finds by text all the same', { by: 'text', name: 'Due' },
         '<div aria-hidden="true"><p>Due</p></div>'],
        ['the page removes it after', pay, PAY + script('document.querySelector("button").remove()')],
        ['the page hides it from the accessible tree after', pay,
         `<div>${PAY}</div>` + script("document.querySelector('div').setAttribute('aria-hidden', 'true')")],
        ['touched many times at once', pay, PAY + script(`const b = document.querySelector('button');
             (async () => { for (let i = 0; i < 20; i++)
                 { b.dataset.n = i; await new Promise((ok) => queueMicrotask(ok)); } })()`)],
        ['a selector that matches once what holds it changes', { by: 'css', css: '.open #pay' },
         `<div><button id="pay">Go</button></div>${later("document.querySelector('div').className = 'open'")}`],
        ['a name its label is given later', { by: 'role', role: 'checkbox', name: 'Save it' },
         `<label for="c">Keep</label><input type="checkbox" id="c">${
             later("document.querySelector('label').textContent = 'Save it'")}`],
        ['a name its aria-labelledby text is given later', { by: 'role', role: 'textbox', name: 'Card' },
         `<span id="l">Car</span><input aria-labelledby="l">${
             later("document.getElementById('l').firstChild.data = 'Card'")}`],
        ['a name from text deep in it, changed later', pay,
         `<button><span>Pa</span></button>${later("document.querySelector('span').firstChild.data = 'Pay'")}`],
        ['a name from what is in it, no longer hidden', pay,
         `<button>Pa<span hidden>y</span></button>${later("document.querySelector('span').hidden = false")}`],
        ['a name from what is in it, no longer aria-hidden', pay,
         `<button>Pa<span aria-hidden="true">y</span></button>${
             later("document.querySelector('span').removeAttribute('aria-hidden')")}`],
        ['a name from alt given later to what is in it', pay,
         `<button><img width="10" height="10"></button>${
             later("document.querySelector('img').alt = 'Pay'")}`],
        ['in a cross-origin frame', pay, FRAME, PAY],
        ['in a cross-origin frame out of process', pay, FRAME, PAY],
        ['for a test that closes it at once, and in a later one without it', pay, [PAY, '<p>Paid</p>']],
    ];
    const isolated = await chromium.launch({ env: seal.env, args: ['--site-per-process'] });

    try
    {
        for (const [what, target, html, frame] of cases)
        {
            /* Long enough for a page drawn again each frame to be taken
               for one fighting the change, if it would be. */
            const { report, errors, saw } = await mutation({ kind: 'hide', target }, html, {
                frame, on: what.endsWith('out of process') ? isolated : browser,
                done: /^(drawn|touched|shown later|a selector that comes)|again and again/.test(what)
                    ? () => false : (r) => r.proven,
                look: async (page) => Promise.all([page.title(),
                                                   (what.includes('again and again') ? await seenBy(page) : 0)
                                                   + await showing(page, 'button:not([data-kept])'),
                                             page.locator('[data-kept]:not(:visible)').count(),
                                             page.locator('[hidden][style]').count(),
                                             showing(page, '.t')]) });

            assert.deepEqual([what, report, errors, saw.slice(1)],
                             [what, { proven: true, why: '', made: true }, [], [0, 0, 0, 0]]);
            if (what.startsWith('before'))
                assert.equal(saw[0], 'none');
        }
    }
    finally
    {
        await isolated.close();
    }

    /* As a test runner's page fixture is: made before mutated(), its page
       loaded before or after, and the browser closed as soon as the test
       is done. */
    for (const loaded of ['before', 'after'])
    {
        const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'shotbox-test-'));
        const own = await chromium.launch({ env: seal.env });

        Object.assign(process.env, { SHOTBOX_MUTATION: JSON.stringify({ kind: 'hide', target: pay }),
                                     SHOTBOX_MUTATION_REPORT: dir });
        try
        {
            const context = await own.newContext();
            const page = await context.newPage();

            if (loaded === 'before')
                await page.setContent(PAY);
            await mutated(context);
            if (loaded === 'after')
                await page.setContent(PAY);
            if (loaded === 'before')
                await page.close();
            await own.close();
            assert.deepEqual([loaded, JSON.parse(await fs.readFile(path.join(dir, `${process.pid}.json`), 'utf8'))],
                             [loaded, { proven: true, why: '', made: true }]);
        }
        finally
        {
            delete process.env.SHOTBOX_MUTATION;
            delete process.env.SHOTBOX_MUTATION_REPORT;
            await own.close();
            await fs.rm(dir, { recursive: true, force: true });
        }
    }
});

test('mutated makes the target dead to what the test does to it, by button, link, box, field, key, form and a pointerdown widget, ahead of the page\'s own listeners, in a shadow root and a frame; and its number one higher, in what it holds and how it is named, as the page changes it or draws it anew, but not what is hidden',
     { skip: why }, async () =>
{
    const acted = (css, type = 'click') => script(`document.querySelector('${css}').addEventListener('${type}',
        (e) => { e.preventDefault(); document.title = 'acted'; })`);
    const click = (page) => page.getByRole('button', { name: 'Pay' }).click();
    const card = { by: 'role', role: 'textbox', name: 'Card' };
    const dead = [
        ['a button clicked', pay, PAY + acted('button'), click],
        ['a button clicked on what it holds', pay, '<button><span>Pay</span></button>' + acted('button'), click],
        ...[['the button that holds it', '<button><b>$</b> <span>Pay</span></button>' + acted('button')],
            ['a component\'s button it is slotted into', '<x-pay><span>Pay</span></x-pay>' + script(`
                customElements.define('x-pay', class extends HTMLElement { constructor ()
                { super(); const b = document.createElement('button');
                  b.append(document.createElement('slot')); this.attachShadow({ mode: 'open' }).append(b);
                  b.addEventListener('click', () => document.title = 'acted'); } })`)],
            ['a button the page puts around it after', '<h3><span>Pay</span></h3>' + later(`
                const b = document.createElement('button'), h = document.querySelector('h3');
                b.append(...h.childNodes); h.append(b);
                b.addEventListener('click', () => document.title = 'acted')`)],
        ].map(([what, html]) => [`${what}, clicked and pressed`, { by: 'text', name: 'Pay' }, html, async (page) =>
        {
            await page.getByRole('button').waitFor();
            await page.getByText('Pay').click();
            await page.getByRole('button').press('Enter');
        }]),
        ['an editor clicked on what it holds, which can\'t be edited', { by: 'css', css: '#ed' },
         '<div id="ed" contenteditable>Note <span contenteditable="false">chip</span></div>' + acted('#ed'),
         (page) => page.getByText('chip').click()],
        ['a button clicked on a component it holds', pay, '<button><x-icon></x-icon>Pay</button>' + script(`
             customElements.define('x-icon', class extends HTMLElement
                 { constructor () { super(); this.attachShadow({ mode: 'open' }).textContent = '*'; } })`)
         + acted('button'), (page) => page.locator('x-icon').click()],
        ['a button clicked on what it holds, which only a script can focus', pay,
         '<div role="button" tabindex="0"><div tabindex="-1">Pay</div></div>' + acted('[role=button]'), click],
        ['a button pressed with Enter', pay, PAY + acted('button'),
         (page) => page.getByRole('button').press('Enter')],
        ['a link followed', { by: 'role', role: 'link', name: 'Pay' },
         `<a href="#paid">Pay</a>${script("addEventListener('hashchange', () => document.title = 'acted')")}`,
         (page) => page.getByRole('link').click()],
        ['a box ticked', { by: 'role', role: 'checkbox', name: 'Save it' },
         '<label for="c">Save it</label><input type="checkbox" id="c">' + acted('input', 'change'),
         (page) => page.getByRole('checkbox').click()],
        ['a field filled', card, '<input aria-label="Card">' + acted('input', 'input'),
         (page) => page.getByRole('textbox').fill('4242')],
        ['a field typed in', card, '<input aria-label="Card">' + acted('input', 'input'),
         (page) => page.getByRole('textbox').pressSequentially('42')],
        ['a widget that acts on pointerdown', pay, PAY + acted('button', 'pointerdown'), click],
        ['a button clicked, which takes the focus all the same', pay, PAY, async (page) =>
        {
            await click(page);
            await page.evaluate(() => document.activeElement.localName === 'button' || (document.title = 'unfocused'));
        }],
        ['a button tapped', pay, PAY + acted('button'), (page) => page.getByRole('button').tap()],
        ['a menu on a right click', pay, PAY + acted('button', 'contextmenu'),
         (page) => page.getByRole('button').click({ button: 'right' })],
        ['a button dragged', pay, '<button draggable="true">Pay</button><p>Here</p>' + acted('button', 'dragstart'),
         (page) => page.getByRole('button').dragTo(page.getByText('Here'))],
        ['a slider scrolled', { by: 'role', role: 'slider', name: 'Volume' },
         '<div role="slider" aria-label="Volume" aria-valuenow="5" tabindex="0">5</div>'
         + acted('div', 'wheel'), async (page) =>
         {
             await page.getByRole('slider').click();
             await page.mouse.wheel(0, 100);
         }],
        ['a field dropped onto', card, '<p draggable="true">4242</p><input aria-label="Card">' + script(`
             document.querySelector('p').addEventListener('dragstart',
                 (e) => e.dataTransfer.setData('text/plain', '4242'))`) + acted('input', 'drop'),
         (page) => page.getByText('4242').dragTo(page.getByRole('textbox'))],
        ['the page\'s own listener on the window', pay, PAY + script(`addEventListener('click',
             (e) => e.target.closest('button') && (document.title = 'acted'), true)`), click],
        ['a form, by the button in it', { by: 'role', role: 'form', name: 'Pay' },
         '<form aria-label="Pay"><button>Go</button></form>' + acted('form', 'submit'),
         (page) => page.getByRole('button').click()],
        ['a form\'s button, by Enter in its field', pay,
         `<form><input aria-label="Card">${PAY}</form>` + acted('form', 'submit'),
         (page) => page.getByRole('textbox').press('Enter')],
        ['in an open shadow root', pay, shadow(`fill(); document.getElementById('host').shadowRoot
             .querySelector('button').addEventListener('click', () => document.title = 'acted')`), click],
        ['in a cross-origin frame', pay, FRAME,
         (page) => page.frameLocator('iframe').getByRole('button', { name: 'Pay' }).click(),
         PAY + `<script>document.querySelector('button').addEventListener('click',
             () => document.title = 'acted')</script>`],
    ];

    const touch = { newContext: () => browser.newContext({ hasTouch: true }) };

    for (const [what, target, html, act, frame] of dead)
    {
        const { report, errors, saw } = await mutation({ kind: 'dead', target }, html, {
            frame, act, on: what.endsWith('tapped') ? touch : browser, look: (page) => Promise.all(page.frames().map((f) => f.title())) });

        assert.deepEqual([what, report, errors, saw.filter(Boolean)],
                         [what, { proven: true, why: '', made: true, used: true }, [], []]);
    }

    const total = { by: 'text', name: 'Total: 42.50' };
    const number = [
        ['text, in what it holds', total, TOTAL, 'p', 'Total: 42.51'],
        ['text by regex', { by: 'text', re: 'Total: .*' }, TOTAL, 'p', 'Total: 42.51'],
        ['a role and its name, carrying down', { by: 'role', role: 'button', name: 'Pay $9' },
         '<button>Pay $9</button>', 'button', 'Pay $8'],
        ['a name from aria-label, which is said as it shows', { by: 'role', role: 'button', name: 'Pay 1,299' },
         '<button aria-label="Pay 1,299">Pay 1,299</button>', 'button', 'Pay 1,300 | Pay 1,300'],
        ['not what is hidden', { by: 'text', re: 'Total: .*' }, '<p>Total: <span hidden>3 </span>42.50</p>', 'p',
         'Total: 3 42.51'],
        ['what shows, though aria-hidden', { by: 'text', re: 'Total.*' },
         '<p aria-label="Total 1">Total <span aria-hidden="true">5</span></p>', 'p', 'Total 6 | Total 2'],
        ['as the page changes it', total, TOTAL + later("document.querySelector('b').textContent = '50.00'"),
         'p', 'Total: 50.01'],
        ['as the page draws it anew', total, TOTAL + later(`const p = document.createElement('p'),
             b = document.createElement('b'); b.textContent = '42.50'; p.append('Total: ', b);
             document.querySelector('p').replaceWith(p)`), 'p', 'Total: 42.51'],
        ['as the page counts up to what it showed', { by: 'text', name: 'Count: 0' },
         '<p>Count: <b>0</b></p>' + later("document.querySelector('b').textContent = '1'"), 'p', 'Count: 2'],
        ['as the page counts up to what it showed, in the same text node', { by: 'text', name: 'Count: 0' },
         '<p>Count: <b>0</b></p>' + later("document.querySelector('b').firstChild.data = '1'"), 'p', 'Count: 2'],
        ['as the page counts up to what it showed, in its aria-label', { by: 'role', role: 'status', name: 'Count 0' },
         '<p role="status" aria-label="Count 0"></p>' + later(`
             document.querySelector('p').setAttribute('aria-label', 'Count 1')`), 'p', 'Count 2'],
        ['as the page counts up to what it showed, after changing what holds it', { by: 'text', name: 'Count: 0' },
         '<p>Count: <b>0</b></p>' + later(`document.querySelector('p').className = 'on';
             document.querySelector('b').firstChild.data = '1'`), 'p', 'Count: 2'],
        ['as the page counts up to what it showed, by way of something else', { by: 'text', name: 'Count: 0' },
         '<p>Count: <b>0</b></p>' + later(`const t = document.querySelector('b').firstChild;
             t.data = '\u2026'; t.data = '1'`), 'p', 'Count: 2'],
        ['as the page counts up to what it showed, its name and then its text',
         { by: 'role', role: 'button', name: 'Cart 0' }, '<button aria-label="Cart 0">Cart 0</button>' + later(`
             const b = document.querySelector('button'); b.setAttribute('aria-label', 'Cart 1'); b.textContent = 'Cart 1'`),
         'button', 'Cart 2 | Cart 2'],
        ['as the page fills in what was a placeholder', { by: 'text', re: 'Total: .*' },
         '<p>Total: \u2026</p>' + later("document.querySelector('p').textContent = 'Total: 42.50'"),
         'p', 'Total: 42.51'],
        ['as the page styles it, a number of nines in its name', { by: 'role', role: 'button', name: 'Total 9' },
         '<button aria-label="Total 9">Total 9</button>' + later(`const b = document.querySelector('button');
             for (const color of ['red', 'blue', 'green']) b.style.color = color`), 'button', 'Total 8 | Total 8'],
        ['as the page adds to it, a number of nines', { by: 'text', name: 'Total: 9' },
         '<p>Total: 9</p>' + later("document.querySelector('p').append(' ')"), 'p', 'Total: 8 '],
        ['as the page sets its text again, a number of nines, its name kept',
         { by: 'role', role: 'button', name: 'Pay 9' },
         '<button aria-label="Pay 9">Pay 9</button>' + later("document.querySelector('button').textContent = 'Pay 9'"),
         'button', 'Pay 8 | Pay 8'],
    ];

    for (const [what, target, html, css, want] of number)
    {
        const { report, errors, saw } = await mutation({ kind: 'number', target }, html, {
            done: what.startsWith('as the page') ? () => false : (r) => r.proven,
            look: (page) => page.locator(css).evaluate((e) =>
                [e.textContent, e.getAttribute('aria-label')].filter(Boolean).join(' | ')) });

        assert.deepEqual([what, report, errors, saw], [what, { proven: true, why: '', made: true }, [], want]);
    }
});

test('mutated runs every page at the run\'s size, those opened later too; and moves the target 40 px, the first way that covers no other control and leaves it the one at its center, as it remounts',
     { skip: why }, async () =>
{
    const sizes = (page) => page.context().pages().map((p) => Object.values(p.viewportSize()));

    const mobile = { newContext: () => browser.newContext({ isMobile: true }) };

    for (const [what, act, want, html = PAY, on = browser, screen = [1024, 768]] of [
        ['a page', () => null, [[1024, 768]]],
        ['a page, as it first loads', () => null, [[1024, 768]], PAY + script('document.title = innerWidth')],
        ['a page at a phone\'s size, in a context that takes it for a phone', () => null, [[375, 667]], PAY, mobile,
         [375, 667]],
        ['and a popup it opens', (page) => page.evaluate(() => window.open('about:blank')), [[1024, 768], [1024, 768]]],
    ])
    {
        const { report, errors, saw } = await mutation({ kind: 'size', screen }, html, {
            act, on, done: /popup|phone/.test(what) ? () => false : (r) => r.proven,
            look: async (page) => [await page.title(), await sizes(page)] });

        assert.deepEqual([what, report, errors, saw],
                         [what, { proven: true, why: '', made: true }, [], [html === PAY ? '' : '1024', want]]);
    }

    /* Its translate, and what the page wrote in its title. */
    const moved = (page) => page.evaluate(() => [getComputedStyle([...document.querySelectorAll('button')]
        .find((b) => b.textContent.startsWith('Pay'))).translate, document.title].filter(Boolean).join(' '));

    for (const [what, html, want] of [
        ['a button', PAY, '40px'],
        ['a button with another to its right', around([[40, 0]]), '0px 40px'],
        ['a button with another to its right and below', around([[40, 0], [0, 40]]), '-40px'],
        ['a button with something over where it would go', '<body>' + script(`const d = document.createElement('div');
             Object.assign(d.style, { position: 'absolute', left: '230px', top: '190px', width: '60px', height: '40px',
                                      zIndex: '1' });
             document.body.append(d)`) + around([]), '0px 40px'],
        ['a button at the right of the window', '<body>' + script(`const b = document.createElement('button');
             b.textContent = 'Pay'; Object.assign(b.style, { position: 'fixed', right: '0', top: '0' });
             document.body.append(b)`), '0px 40px'],
        ['a button at the bottom right of the window, on a longer page', '<body>' + script(`
             const b = document.createElement('button'); b.textContent = 'Pay'; document.body.style.height = '3000px';
             Object.assign(b.style, { position: 'fixed', right: '0', bottom: '0' }); document.body.append(b)`), '-40px'],
        ['a button with another to its right, in a shadow root', '<body>' + script(`const h = document.createElement('div');
             const o = document.createElement('button'); o.textContent = 'Other';
             Object.assign(o.style, { width: '40px', height: '20px', padding: '0', border: '0' });
             Object.assign(h.style, { position: 'absolute', left: '240px', top: '200px' });
             h.attachShadow({ mode: 'open' }).append(o); document.body.append(h)`) + around([]), '0px 40px'],
        ['a button under a bar that lies over it where it was too', '<body>' + script(`
             const d = document.createElement('div');
             Object.assign(d.style, { position: 'fixed', left: '0', top: '180px', width: '100%', height: '60px',
                                      zIndex: '1' }); document.body.append(d)`) + around([]), '40px'],
        ['a button the page hides after', PAY + later("document.querySelector('button').hidden = true"), '40px'],
        ['a button the page scrolls under a bar of links, which lies over it where it was too', '<body>' + script(`
             document.body.style.height = '3000px'; const bar = document.createElement('div');
             Object.assign(bar.style, { position: 'fixed', left: '0', top: '0', width: '100%', height: '40px',
                                        display: 'flex', zIndex: '1' });
             for (const name of ['Home', 'About'])
             { const a = document.createElement('a'); a.href = '#'; a.textContent = name;
               a.style.flex = '1'; bar.append(a); }
             const b = document.createElement('button'); b.textContent = 'Pay';
             Object.assign(b.style, { position: 'absolute', left: (innerWidth / 2 - 30) + 'px', top: '400px',
                                      width: '40px', height: '20px', padding: '0', border: '0' });
             document.body.append(bar, b); setTimeout(() => scrollTo(0, 390), 100)`), '40px'],
        ['a button with a transition of its own, which it keeps', '<body>' + script(`
             const b = document.createElement('button'); b.textContent = 'Pay';
             b.style.transitionDuration = '2s'; document.body.append(b);
             setTimeout(() => document.title = getComputedStyle(b).transitionDuration, 300)`), '40px 2s'],
        ['a button with no transition, the page taking the move off once', '<body>' + script(`
             const b = document.createElement('button'); b.textContent = 'Pay';
             b.style.setProperty('transition', 'none', 'important'); document.body.append(b);
             setTimeout(() => b.style.removeProperty('translate'), 100)`), '40px'],
        ['a button with a transition', '<button>Pa</button>' + script(`
             document.querySelector('button').style.transition = 'all 0.3s'`)
         + later("document.querySelector('button').textContent = 'Pay'"), '40px'],
        ['a button whose text the page keeps changing', '<button>Pay<span aria-hidden="true">0</span></button>'
         + script(`const t = document.querySelector('span').firstChild;
             setInterval(async () => { for (let i = 0; i < 12; i++)
                 { t.data = i; await new Promise((ok) => queueMicrotask(ok)); } }, 50)`), '40px'],
        ['as it remounts every 20 ms', PAY + script(`setInterval(() =>
             { const b = document.createElement('button'); b.textContent = 'Pay';
               document.querySelector('button').replaceWith(b); }, 20)`), '40px'],
    ])
    {
        const { report, errors, saw } = await mutation({ kind: 'move', target: pay }, html, {
            done: /^as it|keeps|once|bar|hides/.test(what) ? () => false : (r) => r.proven, look: moved });

        assert.deepEqual([what, report, errors, saw], [what, { proven: true, why: '', made: true }, [], want]);
    }
});

test('mutated: what it can\'t change or prove, unapplied, and why; a clean run changes nothing and says which targets it saw; outside mutate it does nothing',
     { skip: why }, async () =>
{
    const hide = (target) => ({ kind: 'hide', target });
    const dead = (target) => ({ kind: 'dead', target });
    const move = (target) => ({ kind: 'move', target });
    const nine = { by: 'role', role: 'button', name: 'Pay 9' };
    /* The size of a page with no viewport of its own. */
    const bare = await (async () =>
    {
        const context = await browser.newContext({ viewport: null });

        try
        {
            return await (await context.newPage()).evaluate(() => [innerWidth, innerHeight]);
        }
        finally
        {
            await context.close();
        }
    })();
    const cases = [
        ['the page puts it back', hide(pay), PAY + script(`const b = document.querySelector('button');
             const back = () => b.style.removeProperty('display');
             new MutationObserver(back).observe(b, { attributes: true }); back()`),
         /undoes the change/],
        ['another the page takes for something else', hide(pay),
         `${PAY}<div role="button" id="h"></div>${script(`
             document.getElementById('h').attachShadow({ mode: 'open' }).textContent = 'Pay'`)}`,
         /could still find it unchanged/],
        ['another the page can\'t read, shown after it was proven', hide(pay),
         PAY + later(`const b = document.createElement('button'); b.id = 'p';
             const sheet = new CSSStyleSheet(); sheet.replaceSync('#p::before { content: "Pay" }');
             document.adoptedStyleSheets = [sheet]; document.body.append(b)`),
         /could still find it unchanged/],
        ['shown unchanged by a later test, closed at once', hide(pay), [PAY, BEFORE, '<p>Paid</p>'],
         /could still find it unchanged/],
        ['shown unchanged by an earlier test, before it was ever made', hide(pay), [BEFORE, PAY],
         /could still find it unchanged/],
        ['another the page can\'t read, for a while', hide(pay), PAY + later(`
             const b = document.createElement('button'); b.id = 'p';
             const sheet = new CSSStyleSheet(); sheet.replaceSync('#p::before { content: "Pay" }');
             document.adoptedStyleSheets = [sheet]; document.body.append(b); setTimeout(() => b.remove(), 1100)`),
         /could still find it unchanged/],
        ['another the page can\'t read, in a frame', hide(pay), PAY + FRAME, /could still find it unchanged/,
         '<style>#p::before { content: "Pay" }</style><button id="p"></button>'],
        ['text the page can\'t read, shown after it was proven', hide({ by: 'text', name: 'Total: 42.50' }),
         '<p>Total: 42.50</p>' + later(`const i = document.createElement('input');
             i.type = 'submit'; i.value = 'Total: 42.50'; document.body.append(i)`),
         /could still find it unchanged/],
        ['another the page names differently', hide({ by: 'role', role: 'img', name: 'Logo' }),
         '<img alt="Logo" width="10" height="10"><svg role="img" width="10" height="10"><title>Logo</title></svg>',
         /could still find it unchanged/],
        ['no such target', hide({ ...pay, name: 'Refund' }), PAY, /never seen/],
        ['only hidden', hide(pay), '<button hidden>Pay</button>', /never seen/],
        ['only where the accessible tree leaves it out', hide(pay),
         `<div aria-hidden="true">${PAY}</div>`, /never seen/],
        ['a regex that isn\'t one', hide({ by: 'role', role: 'button', re: '(' }), PAY,
         /Invalid regular expression/],
        ['a selector that isn\'t one', hide({ by: 'css', css: '#pay[' }), PAY,
         /not a valid selector/],
        ['a selector only Playwright reads', hide({ by: 'css', css: 'button:has-text("Pay")' }), PAY,
         /can't look for the target: .*not a valid selector/],
        ['dead, and never used', dead(pay), PAY, /never used/],
        ['dead, and only pointed at, which the page still hears', dead(pay), PAY + script(`
             document.querySelector('button').addEventListener('mouseenter', () => document.title = 'hovered')`),
         /never used/, '', async (page) =>
         {
             await page.getByRole('button').hover();
             assert.equal(await page.title(), 'hovered');
         }],
        ['dead, and used only by the page itself', dead(pay),
         PAY + later("document.querySelector('button').click()"), /never used/],
        ['dead, a choice the test sets before any event', dead({ by: 'role', role: 'combobox', name: 'Plan' }),
         '<select aria-label="Plan"><option>A</option></select>', /before any event/],
        ['dead, a date the test sets before any event', dead({ by: 'css', css: 'input' }),
         '<input type="date">', /before any event/],
        ['dead, and another the page can\'t read', dead(pay), PAY + BEFORE, /could still find it unchanged/],
        ['dead, and another the page can\'t read, gone before the test used it', dead(pay),
         PAY + BEFORE + later("document.getElementById('p').remove()"), /could still find it unchanged/, '',
         async (page) => { await page.waitForTimeout(600); await page.getByRole('button').click(); }],
        ['dead, and only tabbed through', dead({ by: 'role', role: 'textbox', name: 'Card' }),
         '<input aria-label="Before"><input aria-label="Card"><input aria-label="Next">' + script(`
             for (const type of ['keydown', 'keyup'])
                 document.querySelector('[aria-label=Card]').addEventListener(type, () => document.title = 'heard')`),
         /never used/, '', async (page) =>
         {
             await page.getByRole('textbox', { name: 'Before' }).focus();
             await page.keyboard.press('Tab');
             await page.keyboard.press('Control');
             await page.keyboard.press('Tab');
             assert.deepEqual(await page.evaluate(() => [document.activeElement.ariaLabel, document.title]),
                              ['Next', '']);
             await page.keyboard.press('Shift+Tab');
             await page.keyboard.press('Shift+Tab');
             assert.deepEqual(await page.evaluate(() => [document.activeElement.ariaLabel, document.title]),
                              ['Before', '']);
         }],
        ['dead, and a box dropped onto it, which it can\'t take', dead({ by: 'role', role: 'region', name: 'Bin' }),
         '<p draggable="true">Old</p><section aria-label="Bin">Drop here</section>' + script(`
             document.querySelector('section').addEventListener('dragover', (e) => e.preventDefault());
             document.querySelector('p').addEventListener('dragend',
                 (e) => e.dataTransfer.dropEffect !== 'none' && e.target.remove())`), /never used/, '',
         async (page) =>
         {
             await page.getByText('Old').dragTo(page.getByRole('region'));
             assert.equal(await page.getByText('Old').count(), 1);
         }],
        ['dead, in a component, whose other parts still work', dead({ by: 'text', name: 'Cart' }),
         '<div tabindex="0"><app-cart><h2>Cart</h2><p>Row</p><form><input aria-label="Name"></form></app-cart></div>'
         + script(`document.querySelector('p').addEventListener('click', () => document.title += 'row;');
             document.querySelector('form').addEventListener('submit', (e) =>
                 { e.preventDefault(); document.title += 'sent;'; })`), /never used/, '', async (page) =>
         {
             await page.getByText('Row').click();
             await page.getByRole('textbox').press('Enter');
             assert.equal(await page.title(), 'row;sent;');
         }],
        ['dead, a form the page sends itself', dead({ by: 'role', role: 'form', name: 'F' }),
         '<form aria-label="F"></form>' + later("document.querySelector('form').requestSubmit()"), /never used/],
        ['dead, a form, whose fields still take what the test does', dead({ by: 'role', role: 'form', name: 'F' }),
         '<form aria-label="F"><input aria-label="Name"><select aria-label="Plan"><option>A</option>'
         + '<option>B</option></select><input type="checkbox" id="c"><label for="c">Keep</label>'
         + '<x-box></x-box></form>' + script(`
             customElements.define('x-box', class extends HTMLElement
             {
                 constructor () { super(); this.attachInternals().role = 'checkbox'; this.textContent = 'Box'; }
                 connectedCallback () { this.addEventListener('click', () => this.dataset.on = ''); }
             });
             document.querySelector('input').addEventListener('keydown',
                 () => requestAnimationFrame(() => document.forms[0].requestSubmit()))`), /never used/, '',
         async (page) =>
         {
             await page.getByRole('textbox').fill('Ann');
             await page.getByRole('textbox').press('a');
             await page.getByRole('combobox').selectOption('B');
             await page.getByText('Keep').click();
             await page.getByText('Box').click();
             assert.deepEqual(await page.evaluate(() => [...document.forms[0].elements].map((e) => e.value)
                                                       .concat(document.getElementById('c').checked,
                                                               'on' in document.querySelector('x-box').dataset)),
                              ['Anna', 'B', 'on', true, true]);
             await page.evaluate(() => setTimeout(() => document.forms[0].requestSubmit(), 50));
             await page.waitForTimeout(200);
         }],
        ['a number, in what has none', { kind: 'number', target: pay }, PAY, /no number/],
        ['a number, in what has none beside one that has', { kind: 'number', target: nine },
         '<button title="Pay 9"></button><button>Pay 9</button>', /no number/],
        ...[nine, { by: 'role', role: 'button', re: 'Pay \\d' }].flatMap((target) => [
            ['aria-labelledby', '<span id="l">Pay 9</span><button aria-labelledby="l">Go 5</button>'],
            ['its label', '<label for="b">Pay 9</label><button id="b">Pay 9</button>'],
        ].map(([by, html]) => [`a number, named by ${by}, ${target.re ? 'by a regex' : 'exactly'}`,
                               { kind: 'number', target }, html, /doesn't show the number/])),
        ['a number, as text, only in its aria-label', { kind: 'number', target: { by: 'text', name: 'Unread' } },
         '<p aria-label="3 unread">Unread</p>', /doesn't show the number/],
        ['a number the page puts back', { kind: 'number', target: { by: 'text', name: 'Total: 42.50' } },
         '<p>Total: 42.50</p>' + script(`const p = document.querySelector('p');
             const back = () => p.textContent !== 'Total: 42.50' && (p.textContent = 'Total: 42.50');
             new MutationObserver(back).observe(p, { subtree: true, childList: true, characterData: true });
             back()`), /undoes the change/],
        ['a kind it can\'t make yet', { kind: 'slow', target: pay }, PAY, /can't make slow/],
        ['a size the test then sets its own', { kind: 'size', screen: [1024, 768] }, PAY, /size of its own/, '',
         (page) => page.setViewportSize({ width: 800, height: 600 })],
        ['a move that would cover another control each way', move(pay), around([[40, 0], [0, 40], [-40, 0], [0, -40]]),
         /would cover/],
        ['a move that would leave the page each way it could go', move(pay), '<body>' + script(`
             for (const [x, y, name] of [[40, 0, 'R'], [0, 40, 'D'], [0, 0, 'Pay']])
             { const b = document.createElement('button'); b.textContent = name;
               Object.assign(b.style, { position: 'absolute', left: x + 'px', top: y + 'px', width: '40px',
                                        height: '20px', padding: '0', border: '0', margin: '0' });
               document.body.append(b); }`), /leave the page/],
        ...[['in sight', '200px'], ['partly in sight', '700px']].map(([where, top]) =>
            [`a move ${where}, under something the page puts there after`, move(pay), '<body>' + script(`
                 document.body.style.height = '3000px'; const b = document.createElement('button');
                 b.textContent = 'Pay'; Object.assign(b.style, { position: 'absolute', left: '200px', top: '${top}',
                                                                 width: '40px', height: '40px' });
                 document.body.append(b); setTimeout(() => { const d = document.createElement('div');
                     Object.assign(d.style, { position: 'absolute', left: '235px', top: '${top}', width: '60px',
                                              height: '40px', zIndex: '1' }); document.body.append(d); }, 100)`),
             /covered/, '', (page) => page.getByRole('button').scrollIntoViewIfNeeded()]),
        ['a size the page has already', { kind: 'size', screen: [1024, 768] }, PAY, /already/, '', undefined,
         { newContext: () => browser.newContext({ viewport: { width: 1024, height: 768 } }) }],
        ['a size the page has already, with no viewport of its own', { kind: 'size', screen: bare }, PAY,
         /already/, '', undefined, { newContext: () => browser.newContext({ viewport: null }) }],
        ['a move, and a control the page puts under it after', move(pay), around([]) + later(`
             const o = document.createElement('button'); o.textContent = 'Other';
             Object.assign(o.style, { position: 'absolute', left: '265px', top: '205px', width: '10px',
                                      height: '10px', padding: '0', border: '0' }); document.body.append(o)`),
         /covers another/],
        ['a move under something, out of sight until the test scrolls to it', move(pay), '<body>' + script(`
             const d = document.createElement('div'); document.body.style.height = '3000px';
             Object.assign(d.style, { position: 'absolute', left: '230px', top: '2190px', width: '60px',
                                      height: '40px', zIndex: '1' });
             const b = document.createElement('button'); b.textContent = 'Pay';
             Object.assign(b.style, { position: 'absolute', left: '200px', top: '2200px' });
             document.body.append(d, b)`), /covered/, '',
         (page) => page.getByRole('button').scrollIntoViewIfNeeded()],
        ['a size the test sets its own of and puts back at once', { kind: 'size', screen: [1024, 768] }, PAY,
         /size of its own/, '', async (page) =>
         {
             await page.setViewportSize({ width: 800, height: 600 });
             await page.setViewportSize({ width: 1024, height: 768 });
         }],
        ['a move that doesn\'t move it', move({ by: 'role', role: 'link', name: 'Pay' }), '<a href="#">Pay</a>',
         /can't be moved/],
        ['a move of what has a translate of its own', move(pay), '<body>' + script(`
             const b = document.createElement('button'); b.textContent = 'Pay'; b.style.translate = '1px';
             document.body.append(b)`), /translate of its own/],
        ['a move the page puts back', move(pay), PAY + script(`const b = document.querySelector('button');
             const back = () => b.style.removeProperty('translate');
             new MutationObserver(back).observe(b, { attributes: true }); back()`), /undoes the change/],
    ];

    for (const [what, spec, html, said, frame, act, on] of cases)
    {
        const { report, errors, saw } = await mutation(spec, html, { frame, act, on,
            done: () => false, look: (page) => showing(page, 'button') });

        assert.deepEqual([what, report.proven, errors], [what, false, []]);
        assert.match(report.why, said, what);
        if (what.startsWith('a kind'))
            assert.equal(saw, 1);
    }

    /* A copy the first look finds marks the run at once, before a look
       every half second would, so another of the test's processes still
       looking can't stand for it. */
    let early = {};

    await mutation(hide(pay), PAY + BEFORE, { done: (r) => (early = r).why === 'the test could still find it unchanged' });
    assert.equal(early.shown, true, JSON.stringify(early));

    /* Looks that can't be made in time say nothing either way. */
    const { report: busy } = await mutation(hide(pay),
        PAY + script("addEventListener('load', () => setTimeout(() => { for (;;); }, 300))"),
        { done: () => false });

    assert.deepEqual(busy, { proven: true, why: '', made: true }, 'a page busy for good once proven');

    const clean = { kind: null, targets: [pay, { by: 'css', css: '#twin' }, { by: 'css', css: '#pay[' }] };
    const { report, errors, saw } = await mutation(clean, `${PAY}<button id="twin" hidden>Pay</button>`,
        { done: (r) => r.seen?.[0], look: (page) => showing(page, 'button') });

    assert.deepEqual([report.seen, errors, saw], [[true, false, false], [], 1]);
    assert.match(report.bad[2], /'#pay\[' is not a valid selector/);
    assert.deepEqual((await mutation(clean, [PAY, '<p>Paid</p>'], { done: () => false })).report.seen,
                     [true, false, false], 'seen in a context closed at once');

    const context = await browser.newContext();

    try
    {
        await mutated(context);

        const page = await context.newPage();

        await page.goto(PAGE);
        assert.equal(await page.evaluate(() => Symbol.for('shotbox.mutation') in globalThis), false);
    }
    finally
    {
        await context.close();
    }
});

test('mutated loads a big page about as fast in a changed run as in a clean one, so a test fails on the change, not on the time it takes',
     { skip: why }, async () =>
{
    const BIG = `<body><script>for (let i = 0; i < 20000; i++)
        { const b = document.createElement('button'); b.textContent = 'B' + i; document.body.append(b); }
        const p = document.createElement('button'); p.textContent = 'Pay'; document.body.prepend(p)</script>`;
    const took = {};

    for (const spec of [{ kind: null, targets: [pay] }, { kind: 'hide', target: pay }])
    {
        const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'shotbox-test-'));
        const context = await browser.newContext();

        Object.assign(process.env, { SHOTBOX_MUTATION: JSON.stringify(spec), SHOTBOX_MUTATION_REPORT: dir });
        try
        {
            await mutated(context);
            const page = await context.newPage();
            const started = Date.now();

            /* As many tests load theirs. */
            await page.setContent(BIG);
            took[spec.kind ?? 'clean'] = Date.now() - started;
        }
        finally
        {
            delete process.env.SHOTBOX_MUTATION;
            delete process.env.SHOTBOX_MUTATION_REPORT;
            await context.close();
            await fs.rm(dir, { recursive: true, force: true });
        }
    }
    assert.ok(took.hide < took.clean + 1500, JSON.stringify(took));
});

test('mutated, when the page crashes or the browser is killed under the test: closing doesn\'t hang, nothing is left to throw, and what was proven stays so',
     { skip: why }, async () =>
{
    const thrown = [];
    const heard = (e) => thrown.push(String(e));

    process.on('unhandledRejection', heard);
    try
    {
        for (const end of ['crash', 'kill'])
        {
            const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'shotbox-test-'));
            const server = await chromium.launchServer({ env: seal.env });
            const own = await chromium.connect(server.wsEndpoint());

            Object.assign(process.env, { SHOTBOX_MUTATION: JSON.stringify({ kind: 'hide', target: pay }),
                                         SHOTBOX_MUTATION_REPORT: dir });
            try
            {
                const context = await own.newContext();

                await mutated(context);
                const page = await context.newPage();
                const read = async () => JSON.parse(await fs.readFile(path.join(dir, `${process.pid}.json`), 'utf8'));

                await page.setContent(PAY);
                await until(async () => (await read()).proven, 5000);
                if (end === 'crash')
                    await page.goto('chrome://crash').catch(() => {});
                else
                    server.process().kill('SIGKILL');
                /* Long enough for the half-second looks to meet what's left. */
                await new Promise((ok) => setTimeout(ok, 1500));
                const started = Date.now();

                await (end === 'crash' ? context.close() : own.close());
                assert.deepEqual([end, await read(), Date.now() - started < 15000],
                                 [end, { proven: true, why: '', made: true }, true]);
            }
            finally
            {
                delete process.env.SHOTBOX_MUTATION;
                delete process.env.SHOTBOX_MUTATION_REPORT;
                await server.close().catch(() => {});
                await fs.rm(dir, { recursive: true, force: true });
            }
        }
        await new Promise((ok) => setTimeout(ok, 500));
        assert.deepEqual(thrown, []);
    }
    finally
    {
        process.off('unhandledRejection', heard);
    }
});
