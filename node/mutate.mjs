/*
 * Copyright (C) 2026 Misha Nasledov <misha@nasledov.com>
 *
 * SPDX-License-Identifier: MIT
 */

/*
 * mutate.mjs -- a test's half of `shotbox mutate`.
 *
 *     const context = await browser.newContext();
 *     await mutated(context);
 *     const page = await context.newPage();
 *
 * In a run of `shotbox mutate`, it makes the run's change to every page
 * and frame of the context, and writes down whether the change was made
 * and proven; a clean run changes nothing and says which targets it saw.
 * Anywhere else it does nothing. Call it before the first page loads.
 *
 * The change is made by an init script, which runs before the page's own
 * scripts, CSP or not, in every frame. Its observer makes it as soon as
 * the target is there, in the callback, so no frame is drawn without it,
 * and again after a re-render. It finds the target as near as a page can
 * tell; whether it worked is Playwright's to say, by the locator the test
 * would use: once it's made, the test must find nothing by the target
 * showing unchanged, then, every half second, and as each page closes.
 * A dead target must also be used, by the test as a person would.
 */

import fs from 'node:fs';

/* `text` with the last digit of its first number one higher, carrying
   ("42.50" to "42.51", "1,299" to "1,300"), or one lower where a carry
   would add a digit ("9" to "8"), so the text keeps its length and maps
   onto the same characters of the page's text nodes; null with no
   number. It runs in the page, from its source, as does number(), so
   they use nothing else. */
export function bumped(text)
{
    const m = number(text);

    if (!m)
        return null;
    const digits = [...m[0]];
    const last = digits.findLastIndex((c) => /\d/.test(c));
    const at = digits.findLastIndex((c) => /[0-8]/.test(c));

    if (at < 0)
        digits[last] = '8';
    else
    {
        digits[at] = String(Number(digits[at]) + 1);
        for (let i = at + 1; i < digits.length; i++)
            if (/\d/.test(digits[i]))
                digits[i] = '0';
    }
    return text.slice(0, m.index) + digits.join('') + text.slice(m.index + m[0].length);
}

/* The first number in `text`, as a match. A plain space doesn't join
   digits, as "3 400 items" are two numbers more often than one. */
function number (text)
{
    return /\d+(?:[.,:]\d+|['\u00a0\u2009\u202f]\d{3}(?!\d))*/.exec(text);
}

const BINDING = '__shotboxMutation';
const KINDS = new Set(['size', 'move', 'hide', 'dead', 'number']);
/* More undos than this before a frame is drawn is a page fighting the
   change, and what the test does then says nothing of it. One a frame is
   a page drawing itself, as React does, which the change is made again
   before. */
const UNDOS = 10;
const FOUGHT = `the page undoes the change, over ${UNDOS} times before a frame is drawn`;
/* As long as a look at the pages may take, and closing waits for one:
   Playwright's first look by role, on a page of 50,000 elements, takes
   8 s. */
const LOOKING = 10000;
/* How often to look while the test runs, for a copy of the target it
   could use and the page missed; a copy used and gone between two looks
   isn't seen. */
const EVERY = 500;
/* As long as one of those looks may take, which a closing page waits
   for. */
const GLANCE = 3000;
const SHOWN = 'the test could still find it unchanged';
const UNUSED = 'the change was made, and the test never used the target';
const WRAPPED = Symbol('shotbox.mutation');

/* What this process has found, for all its contexts, as a test runner
   makes one for each test: what one saw, proved, or found showing stays
   so. The runner puts it together with its other processes'. */
let report = null, reporting = null;

export async function mutated (context)
{
    const { SHOTBOX_MUTATION: given, SHOTBOX_MUTATION_REPORT: out } = process.env;

    if (!given || !out || context[WRAPPED])
        return;
    const spec = JSON.parse(given);

    if (reporting !== out)
    {
        reporting = out;
        report = spec.kind === 'size' ? { proven: false, why: 'no page was opened' }
            : spec.kind ? { proven: false, why: 'the target was never seen, showing' }
            : { seen: spec.targets.map(() => false) };
    }
    if (spec.kind && !KINDS.has(spec.kind))
    {
        report.why = `this shotbox can't make ${spec.kind} changes yet`;
        return write(out);
    }
    write(out);
    if (spec.kind === 'size')
        return sized(context, spec.screen, out);

    const spoiled = () => report.fought || report.shown;
    /* Why the target counts as still showing in `pages`, if it does: by
       Playwright's own locator, as the test would look for it, or because
       the look failed or took too long, which can't be taken for its
       being gone. */
    const look = async (pages, limit = LOOKING) =>
    {
        try
        {
            const got = await Promise.race([showing(pages, spec.target),
                                            new Promise((ok) => setTimeout(ok, limit).unref())]);

            return got === undefined ? 'it couldn\'t be looked for in time' : got ? SHOWN : '';
        }
        catch (e)
        {
            return pages.every((p) => p.isClosed()) ? 'the page closed before it could be looked for'
                : `it couldn't be looked for: ${e.message.split('\n')[0]}`;
        }
    };
    /* Found showing, for good. A look that fails or takes too long is
       tried again on the next tick: a page busy a while, or a frame gone
       as it was looked in, says nothing of a copy. Not of a target the
       page can't look for, which a locator may find all the same. */
    const audit = async (pages) =>
    {
        if (!bad && !spoiled() && await look(pages, GLANCE) === SHOWN && !spoiled())
        {
            Object.assign(report, { proven: false, shown: true, why: unfit || SHOWN });
            write(out);
        }
    };
    /* Whether the look on making it found nothing unchanged, and whether
       the test has acted on a dead target: one it never used says
       nothing of a test. */
    let proving = null, auditing = null, bad = false, clear = false, used = false, unfit = '';
    /* One look at a time, and at close only where none is under way. */
    const done = (pages) => async () =>
    {
        await proving;
        await (auditing ?? audit(pages().filter((p) => !p.isClosed())));
    };
    const opened = (page) => waits(page, done(() => [page]));
    const ticking = spec.kind && setInterval(() =>
    {
        auditing ??= audit(context.pages()).finally(() => auditing = null);
    }, EVERY).unref();

    context.on('close', () => clearInterval(ticking));
    waits(context, done(() => context.pages()));
    if (context.browser())
        waits(context.browser(), () => Promise.all(context.browser().contexts().map((c) => c[WRAPPED]?.())));
    context.pages().forEach(opened);
    context.on('page', opened);
    await context.exposeBinding(BINDING, async ({ page }, said) =>
    {
        if ('seen' in said)
            report.seen[said.seen] = true;
        else if (!spec.kind && said.bad)
            (report.bad ??= {})[said.target] = said.bad;
        else if (said.fought)
            Object.assign(report, { proven: false, fought: true, why: FOUGHT });
        else if (said.covered)
            Object.assign(report, { proven: false, shown: true, why: said.covered });
        else if (said.unfit)
        {
            unfit = said.unfit;
            if (report.made || spoiled())
                return;
            report.why = unfit;
        }
        else if (said.used)
        {
            used = report.used = true;
            if (clear && !spoiled() && !report.proven)
                Object.assign(report, { proven: true, why: '' });
        }
        else if (spoiled() || report.proven || proving)
            return;
        else if (said.bad)
        {
            bad = true;
            report.why = `the page can't look for the target: ${said.bad}`;
        }
        else
        {
            /* Looking, until the look is done: a test that fails before
               then failed on the change. */
            Object.assign(report, { made: true, looking: true, why: 'the change was made, and not yet proven' });
            write(out);
            proving = look([page]).finally(() => proving = null);

            const why = await proving;

            delete report.looking;

            if (spoiled())
                return;
            clear = !why;
            Object.assign(report, why ? { why } : spec.kind === 'dead' && !used ? { why: UNUSED }
                                                                                : { proven: true, why: '' });
        }
        write(out);
    });
    const script = `(() => { ${number} ${bumped}
        (${inPage})(${JSON.stringify({ spec, binding: BINDING, undos: UNDOS })}, number, bumped); })()`;

    await context.addInitScript(script);
    /* A page made before, as a test runner's own page is, until it next
       loads one. */
    for (const frame of context.pages().flatMap((p) => p.frames()))
        await frame.evaluate(script).catch(() => {});
}

/* Whether any frame of `pages` shows what the test could find by the
   target, by the locator it would use, that wasn't changed. */
async function showing (pages, t)
{
    const name = t.re === undefined ? t.name : new RegExp(`^(?:${t.re})$`);

    for (const frame of pages.flatMap((p) => p.frames()).filter((f) => !f.isDetached()))
    {
        const found = t.by === 'role' ? frame.getByRole(t.role, { name, exact: true })
            : t.by === 'text' ? frame.getByText(name, { exact: true }) : frame.locator(t.css);

        if (await found.filter({ visible: true }).evaluateAll((els) =>
            els.some((el) => !globalThis[Symbol.for('shotbox.mutation')]?.(el))))
            return true;
    }
    return false;
}

/* Every page of the context at the run's size, by Playwright, as the
   session can't set it: those made before, and each made after as it is
   made, before it loads anything, but for a popup the page opens with an
   address, which loads at the context's size first. The test setting a
   size of its own spoils it. */
function sized (context, [width, height], out)
{
    const take = async (page) =>
    {
        const set = page.setViewportSize;
        /* With no viewport of its own, its window's, read again where a
           load the test began loses the first read. */
        let was = page.viewportSize();

        for (let tries = 3; !was && tries-- && !page.isClosed();)
            was = await page.evaluate(() => ({ width: innerWidth, height: innerHeight })).catch(() => null);

        if (was?.width === width && was?.height === height)
        {
            if (!report.made)
                report.why = 'the page is at that size already';
            return write(out);
        }
        page.setViewportSize = async function (size)
        {
            if (size.width !== width || size.height !== height)
            {
                Object.assign(report, { proven: false, shown: true, why: 'the test set a size of its own' });
                write(out);
            }
            return set.call(this, size);
        };
        return set.call(page, { width, height }).then(() =>
        {
            if (!report.shown && !report.made)
                Object.assign(report, { made: true, proven: true, why: '' });
        }, (e) =>
        {
            if (!report.made)
                report.why = `the page couldn't be set to the size: ${e.message.split('\n')[0]}`;
        }).then(() => write(out));
    };

    context.on('page', take);
    return Promise.all(context.pages().map(take));
}

/* Make `closing.close()` wait for `first` before it closes. */
function waits (closing, first)
{
    if (closing[WRAPPED])
        return;
    const close = closing.close;

    closing[WRAPPED] = first;
    closing.close = async function (...args)
    {
        await first();
        return close.apply(this, args);
    };
}

/* The report, as this process's own file in the run's report directory. */
function write (out)
{
    const to = `${out}/${process.pid}`;

    fs.writeFileSync(`${to}.part`, JSON.stringify(report));
    fs.renameSync(`${to}.part`, `${to}.json`);
}

/* The page's half, run in every frame from its source: it can use
   nothing outside itself. */
function inPage ({ spec, binding, undos }, number, bumped)
{
    const key = Symbol.for('shotbox.mutation');

    if (globalThis[key])
        return;
    const send = globalThis[binding];
    const targets = spec.kind ? [spec.target] : spec.targets;
    const clean = (s) => s.replace(/\s+/g, ' ').trim();
    const said = (m) => Promise.resolve().then(() => send(m)).catch(() => {});
    let fought = false;

    /* The role, name and text the page gives it, as near as the page can
       tell: what Chromium gives it is only read through CDP. */
    const INPUTS = { button: 'button', submit: 'button', reset: 'button', image: 'button',
                     checkbox: 'checkbox', radio: 'radio', range: 'slider', number: 'spinbutton',
                     search: 'searchbox' };
    const DEFAULTS = { submit: 'Submit', reset: 'Reset' };
    const FIELDS = new Set(['input', 'select', 'textarea', 'button', 'meter', 'output', 'progress']);
    const TAGS = { button: 'button', textarea: 'textbox', nav: 'navigation', main: 'main',
                   dialog: 'dialog', ul: 'list', ol: 'list', li: 'listitem', option: 'option',
                   table: 'table', tr: 'row', td: 'cell', summary: 'button', section: 'region',
                   form: 'form', article: 'article', aside: 'complementary',
                   progress: 'progressbar', h1: 'heading', h2: 'heading', h3: 'heading',
                   h4: 'heading', h5: 'heading', h6: 'heading' };
    const CONTENT = new Set(['button', 'link', 'heading', 'cell', 'columnheader', 'rowheader',
                             'checkbox', 'radio', 'switch', 'option', 'tab', 'menuitem',
                             'treeitem', 'tooltip']);
    const UNSAID = new Set(['script', 'style', 'template', 'noscript']);
    const role = (el) =>
    {
        const given = el.getAttribute('role')?.trim().split(/\s+/)[0];

        if (given)
            return given;
        switch (el.localName)
        {
        case 'input': return INPUTS[el.type] ?? 'textbox';
        case 'a': case 'area': return el.hasAttribute('href') ? 'link' : null;
        case 'select': return el.multiple || el.size > 1 ? 'listbox' : 'combobox';
        case 'img': return el.getAttribute('alt') === '' ? 'presentation' : 'img';
        case 'th': return el.scope === 'row' ? 'rowheader' : 'columnheader';
        default: return TAGS[el.localName] ?? null;
        }
    };
    /* Hidden by its own style, not by what it's in, which may be shown
       later with nothing it holds changing. */
    const unshown = (c) =>
    {
        const style = getComputedStyle(c);

        return style.display === 'none' || style.visibility === 'hidden';
    };
    const showsText = (c) => c.nodeType === Node.ELEMENT_NODE && !UNSAID.has(c.localName) && !c.hidden
                             && !unshown(c);
    const spoken = (c) => showsText(c) && c.getAttribute('aria-hidden') !== 'true';
    /* Its text, less what is hidden; for a name, with what its images
       and labeled parts are called. */
    const words = (n, named) => [...n.childNodes].map((c) =>
    {
        if (c.nodeType === Node.TEXT_NODE)
            return c.data;
        if (!spoken(c))
            return '';
        if (named && c.hasAttribute('aria-label'))
            return ` ${c.getAttribute('aria-label')} `;
        if (named && c.localName === 'img')
            return ` ${c.getAttribute('alt') ?? ''} `;
        return words(c, named);
    }).join('');
    /* Each source in turn, as finding a label can take a while. */
    const name = (el, as) =>
    {
        const ids = el.getAttribute('aria-labelledby');
        const root = el.getRootNode();
        const sources = [
            () => ids && ids.split(/\s+/).map((id) => root.getElementById?.(id)?.textContent ?? '').join(' '),
            () => el.getAttribute('aria-label'),
            /* `labels` searches the whole page, so only where one could
               be: around it, or pointing at its id. */
            () => FIELDS.has(el.localName) && (el.id || el.closest('label')) && el.labels
                  && [...el.labels].map((l) => l.textContent).join(' '),
            () => (el.localName === 'img' || el.type === 'image') && el.getAttribute('alt'),
            () => el.localName === 'input' && ['button', 'submit', 'reset'].includes(el.type)
                  && (el.value || DEFAULTS[el.type]),
            () => CONTENT.has(as) && words(el, true),
            () => el.getAttribute('title'),
            () => el.getAttribute('placeholder'),
        ];

        for (const source of sources)
        {
            const s = clean(source() || '');

            if (s)
                return s;
        }
        return '';
    };
    const named = (t) =>
    {
        if (t.re === undefined)
            return (s) => s === clean(t.name);
        const re = new RegExp(`^(?:${t.re})$`);

        return (s) => re.test(s);
    };
    const nothing = () => false;
    const matches = targets.map((t, i) =>
    {
        try
        {
            if (t.by === 'css')
            {
                document.createDocumentFragment().querySelector(t.css);
                return (el) => el.matches(t.css);
            }
            const test = named(t);

            if (t.by === 'text')
                return (el) => test(clean(words(el)))
                               && ![...el.children].some((c) => test(clean(words(c))));
            return (el) =>
            {
                const as = role(el);

                return as === t.role && test(name(el, as));
            };
        }
        catch (e)
        {
            said(spec.kind ? { bad: e.message } : { bad: e.message, target: i });
            return nothing;
        }
    });
    /* For a role, not one aria-hidden leaves out, which a test can't find
       by its role either. */
    const showing = (el, t) => el.checkVisibility({ visibilityProperty: true })
                               && !(t.by === 'role' && el.closest('[aria-hidden="true"]'));

    const changed = new WeakSet();
    /* Those changed that are still in the page: what holds one is no text
       target, being no nearest element, and needn't be read again. */
    const live = new Set();
    const holds = (el) => [...live].some((c) => c.isConnected ? c !== el && el.contains(c)
                                                              : !live.delete(c));
    const hidden = (el) => el.style.getPropertyValue('display') === 'none'
                           && el.style.getPropertyPriority('display') === 'important';
    /* The text nodes of what it shows, aria-hidden or not, and what it is
       called, for a number. */
    const texts = (n) => [...n.childNodes].flatMap((c) => c.nodeType === Node.TEXT_NODE ? [c]
                                                        : showsText(c) ? texts(c) : []);
    const shown = (el) => texts(el).map((t) => t.data).join('');
    const wrote = new WeakMap();
    /* Which way each was moved. */
    const way = new WeakMap();
    /* What the page has written over, of what was made: each write made
       is owed a record of its own, which isn't the page's. */
    const owed = new Map(), rewritten = new WeakSet();
    const now = (n) => n.nodeType === Node.TEXT_NODE ? n.data
        : `${n.getAttribute('style')}\n${n.getAttribute('aria-label')}`;
    /* A write that changes nothing makes no record. */
    const write = (n, f) =>
    {
        const was = now(n);

        f();
        if (now(n) !== was)
            owed.set(n, (owed.get(n) ?? 0) + 1);
    };
    const over = (was, nodes) => was.nodes.some((t) => rewritten.has(t) || !nodes.includes(t));
    /* Playwright sets these first, and only then sends their events. */
    const SET = 'select, input:is([type=color], [type=date], [type=datetime-local], [type=month], '
                + '[type=range], [type=time], [type=week], [type=file])';
    /* Each kind's change, whether the page has undone it, and why it
       can't be made, where it can't. */
    const KINDS = {
        hide: {
            /* Without a transition, which would keep it showing a while. */
            make: (el) =>
            {
                el.style.setProperty('transition', 'none', 'important');
                el.style.setProperty('display', 'none', 'important');
            },
            undone: (el) => !hidden(el),
            unfit: () => '',
        },
        dead: {
            make: () => {},
            undone: () => false,
            unfit: (el) => el.matches(SET)
                ? 'a test sets its value before any event, which a dead change can\'t stop' : '',
        },
        /* Each text node keeps its length, so the number shows in the same
           places, and its name says the same. Made again where the page
           has written over what was made, even with what it was made, and
           not where it only changes what is around it. */
        number: {
            make: (el) =>
            {
                const was = wrote.get(el);
                const nodes = texts(el);
                const text = nodes.map((t) => t.data).join('');
                const now = (!was?.nodes || over(was, nodes)) && bumped(text);
                const label = el.getAttribute('aria-label');
                const named = label && (!was || rewritten.has(el) || label !== was.label) && bumped(label);
                const mine = [];
                let at = 0;

                for (const t of now ? nodes : [])
                {
                    const s = now.slice(at, at + t.data.length);

                    at += t.data.length;
                    if (s !== t.data)
                    {
                        write(t, () => t.data = s);
                        mine.push(t);
                    }
                }
                if (named)
                    write(el, () => el.setAttribute('aria-label', named));
                [el, ...nodes].forEach((n) => rewritten.delete(n));
                wrote.set(el, { nodes: now ? mine : was?.nodes, text: shown(el),
                                label: el.getAttribute('aria-label') });
            },
            undone: (el) =>
            {
                const was = wrote.get(el);

                return was.text !== shown(el) || was.label !== el.getAttribute('aria-label')
                       || rewritten.has(el) || !!was.nodes && over(was, texts(el));
            },
            unfit: (el) => bumped(shown(el)) || bumped(el.getAttribute('aria-label') ?? '') ? ''
                : 'its text and aria-label have no number to change',
        },
    };
    /* Whether at x, y nothing lies over it but what would where it was,
       moved dx, dy from there. */
    const clear = (el, x, y, dx, dy) =>
    {
        const root = el.getRootNode(), hit = root.elementFromPoint(x, y), was = root.elementFromPoint(x - dx, y - dy);

        if (el.contains(hit))
            return true;
        /* What holds both, as a bar of links does, if it doesn't hold it. */
        for (let c = hit; c; c = up(c))
            if (c.contains(was))
                return !c.contains(el);
        return false;
    };
    /* The centers of the other controls showing, in any open root. */
    const centers = (el) => [document, ...roots].flatMap((root) => [...root.querySelectorAll(CONTROLS)])
        .filter((c) => !c.contains(el) && !el.contains(c) && c.checkVisibility())
        .map((c) => c.getBoundingClientRect()).map((r) => [r.x + r.width / 2, r.y + r.height / 2]);
    const covers = (r, points) => points.some(([a, b]) => a >= r.left && a < r.right && b >= r.top && b < r.bottom);
    /* Covered where it moved, looked at each frame its center is in
       sight, and covering another control's center, each frame after the
       page changes: the page may put either there later. */
    let covered = false, stirred = false;
    const cover = () =>
    {
        for (const el of covered || spec.kind !== 'move' ? [] : live)
        {
            const r = el.getBoundingClientRect(), [x, y] = [r.x + r.width / 2, r.y + r.height / 2];
            const [dx = 0, dy = 0] = way.get(el).split(' ').map(parseFloat);

            if (!changed.has(el) || !el.isConnected || !el.checkVisibility())
                continue;
            const why = stirred && covers(r, centers(el)) ? 'it covers another control where it moved'
                : x >= 0 && y >= 0 && x < innerWidth && y < innerHeight && !clear(el, x, y, dx, dy)
                    ? 'it is covered where it moved' : '';

            if (why)
            {
                covered = true;
                said({ covered: why });
            }
        }
        stirred = false;
    };
    const fixed = (el) =>
    {
        for (let a = el; a; a = up(a))
            if (a.nodeType === Node.ELEMENT_NODE && getComputedStyle(a).position === 'fixed')
                return true;
        return false;
    };
    KINDS.move = {
        make: (el) => el.style.setProperty('translate', way.get(el), 'important'),
        undone: (el) => el.style.getPropertyValue('translate') !== way.get(el),
        unfit: (el) => rule(el),
    };
    /* The first way it can go: still on the page, or in the window if it
       is fixed there; over no other control's center, in any root; and
       still what lies at its own center, where that is in sight. */
    const rule = (el) =>
    {
        if (getComputedStyle(el).translate !== 'none')
            return 'it has a translate of its own';
        const points = centers(el);
        const page = document.documentElement;
        const [left, top, right, bottom] = fixed(el) ? [0, 0, innerWidth, innerHeight]
            : [-scrollX, -scrollY, Math.max(page.scrollWidth, innerWidth) - scrollX,
               Math.max(page.scrollHeight, innerHeight) - scrollY];
        const was = el.getBoundingClientRect();
        /* Its own transition, put back as it was, a part at a time. */
        const kept = ['property', 'duration', 'timing-function', 'delay', 'behavior'].map((part) =>
            [`transition-${part}`, el.style.getPropertyValue(`transition-${part}`),
             el.style.getPropertyPriority(`transition-${part}`)]);

        /* Each tried, its writes owed, or they would be taken for the
           page's and tried again. */
        write(el, () => el.style.setProperty('transition', 'none', 'important'));
        try
        {
            for (const [x, y] of [[40, 0], [0, 40], [-40, 0], [0, -40]])
            {
                write(el, () => el.style.setProperty('translate', `${x}px ${y}px`, 'important'));
                const r = el.getBoundingClientRect();
                const [cx, cy] = [r.x + r.width / 2, r.y + r.height / 2];
                const sighted = cx >= 0 && cy >= 0 && cx < innerWidth && cy < innerHeight;
                const open = !sighted || clear(el, cx, cy, x, y);
                const value = el.style.getPropertyValue('translate');

                write(el, () => el.style.removeProperty('translate'));
                if (r.x === was.x && r.y === was.y)
                    return 'it can\'t be moved, as what is inline in text can\'t';
                if (r.left >= left && r.top >= top && r.right <= right && r.bottom <= bottom
                    && open
                    && !covers(r, points))
                {
                    way.set(el, value);
                    return '';
                }
            }
            return 'it would cover another control, be covered, or leave the page, whichever way it moved';
        }
        finally
        {
            for (const [name, value, priority] of kept)
                write(el, () => value ? el.style.setProperty(name, value, priority) : el.style.removeProperty(name));
        }
    };
    const kind = KINDS[spec.kind];
    /* Counted until a frame is drawn, or for a second where none is, as
       in a frame out of sight. */
    let undone = new Map(), since = performance.now();
    const again = (el) =>
    {
        if (!kind.undone(el))
            return;
        if (performance.now() - since > 1000)
            [undone, since] = [new Map(), performance.now()];
        undone.set(el, (undone.get(el) ?? 0) + 1);
        if (undone.get(el) > undos)
        {
            fought = true;
            said({ fought: true });
            return;
        }
        kind.make(el);
    };
    const change = (el) =>
    {
        const why = kind.unfit(el);

        if (why)
            return said({ unfit: why });
        /* A number by what the test finds it by: its name, or the text it
           shows. */
        const t = spec.target;
        const by = spec.kind === 'number' && { role: () => name(el, t.role), text: () => shown(el) }[t.by];
        const was = by && by();

        kind.make(el);
        if (by && by() === was)
            return said({ unfit: `what the test finds it by doesn't show the number changed` });
        changed.add(el);
        live.add(el);

        said({ made: true });
    };

    const seen = targets.map(() => false);
    /* What matches but isn't showing, looked at again each frame before
       it's drawn: a popover, a menu shown on hover or a style sheet can
       show it with nothing in the page changing. */
    const waiting = new Set();
    /* What is named by others, to look at again when they change. */
    const labelled = new Set();
    let looked = new Set();
    const visit = (el) =>
    {
        if (looked.has(el))
            return;
        looked.add(el);
        waiting.delete(el);
        if (el.hasAttribute('aria-labelledby'))
            labelled.add(el);
        for (let i = 0; i < targets.length; i++)
        {
            if (spec.kind && changed.has(el))
                again(el);
            else if ((spec.kind ? !(targets[i].by === 'text' && holds(el)) : !seen[i]) && matches[i](el))
            {
                if (!showing(el, targets[i]))
                    waiting.add(el);
                else if (spec.kind)
                    change(el);
                else
                {
                    seen[i] = true;
                    said({ seen: i });
                }
            }
        }
    };
    const watch = (root) => observer.observe(root, { childList: true, subtree: true,
                                                     attributes: true, characterData: true });
    const watched = new WeakSet();
    /* The open shadow roots, for what is in them. */
    const roots = new Set();
    const scan = (root) =>
    {
        for (const el of [root, ...root.querySelectorAll('*')])
        {
            if (el.shadowRoot && !watched.has(el.shadowRoot))
            {
                watched.add(el.shadowRoot);
                roots.add(el.shadowRoot);
                watch(el.shadowRoot);
            }
            if (el.shadowRoot)
                scan(el.shadowRoot);
            if (el.nodeType === Node.ELEMENT_NODE)
                visit(el);
        }
    };
    const up = (n) => n.parentElement ?? n.parentNode?.host ?? n.host ?? null;
    /* What a change to `n` can have changed: what is in it, if `inside`;
       it, and if `outside` what it is in, whose text and names it is
       part of; and what it names, as a label. */
    const around = (n, inside, outside) =>
    {
        const el = n.nodeType === Node.ELEMENT_NODE ? n : up(n);

        if (inside && n.querySelectorAll)
            scan(n);
        for (let a = el; a; a = outside ? up(a) : null)
        {
            visit(a);
            if (a.control)
                visit(a.control);
            for (const r of a.id ? labelled : [])
            {
                if (!r.isConnected)
                    labelled.delete(r);
                else if (r.getAttribute('aria-labelledby')?.split(/\s+/).includes(a.id))
                    visit(r);
            }
        }
    };
    /* The attributes that can change the text or names of what holds the
       element, of all a busy page changes many times a second. */
    const NAMING = new Set(['hidden', 'aria-hidden', 'aria-label', 'alt']);
    const selectors = targets.filter((t, i) => t.by === 'css' && matches[i] !== nothing)
                             .map((t) => t.css);
    /* Which records are the page's, all of them before any is acted on,
       as what is made on one would be owed the record of a later one. */
    const theirs = (r) =>
    {
        if (r.type !== 'characterData' && r.attributeName !== 'aria-label' && r.attributeName !== 'style')
            return true;
        const n = owed.get(r.target);

        if (!n && r.attributeName !== 'style')
            rewritten.add(r.target);
        else if (n > 1)
            owed.set(r.target, n - 1);
        else
            owed.delete(r.target);
        return !n;
    };
    const observer = new MutationObserver((records) =>
    {
        looked = new Set();
        stirred = true;
        for (const r of records.filter(theirs))
        {
            if (fought)
                return;
            for (const n of r.addedNodes)
                around(n, true, false);
            if (r.type === 'attributes')
                around(r.target, false, NAMING.has(r.attributeName));
            else
                around(r.target, false, true);
        }
    });
    /* What matches and wasn't showing, and what a selector finds, which
       can come to show or match with nothing it holds changing: by what
       holds it, a popover, a state such as :hover or :checked, or a style
       sheet. After each event that may have shown it, before anything
       else can look, and each frame before it's drawn. */
    const recheck = () =>
    {
        looked = new Set();
        for (const css of selectors)
            document.querySelectorAll(css).forEach(visit);
        for (const el of waiting)
        {
            if (el.isConnected)
                visit(el);
            else
                waiting.delete(el);
        }
    };
    for (const type of ['beforetoggle', 'mouseover', 'mouseout', 'focusin', 'focusout', 'change',
                        'transitionend', 'animationend'])
        addEventListener(type, () => queueMicrotask(recheck), true);
    /* What a dead target does, stopped first, on the window, before the
       page's own listeners: what reaches it from the pointer, touch or
       keys, less what is the controls' it holds, as the fields of a dead
       form still take typing. But for what Tab, Shift and the like and
       the wheel do, the focus a press gives it, a drop it won't take,
       and a pointer passing over it. What the test does to it, as a
       person would, counted, as is a submit the test set off. */
    const ACTS = new Set(['pointerdown', 'mousedown', 'touchstart', 'click', 'dblclick', 'auxclick',
                          'keydown', 'beforeinput', 'drop']);
    const KEPT = new Set(['pointerdown', 'mousedown', 'wheel', 'dragenter', 'dragover']);
    const PASSING = new Set(['Tab', 'Shift', 'Control', 'Alt', 'Meta']);
    const WIDGETS = ['a[href]', 'area[href]', 'button', 'input', 'select', 'textarea', 'summary',
                     ...['button', 'link', 'checkbox', 'radio', 'switch', 'tab', 'menuitem',
                         'menuitemcheckbox', 'menuitemradio', 'option', 'slider', 'spinbutton',
                         'textbox', 'searchbox', 'combobox', 'treeitem'].map((r) => `[role=${r}]`)].join();
    const CONTROLS = [WIDGETS, 'label', '[contenteditable]:not([contenteditable=false])',
                      '[tabindex]:not([tabindex^="-"])'].join();
    /* A custom element may be a control by what only it knows: its
       internals, or a closed shadow root. */
    const control = (n) => n.matches?.(CONTROLS) || n.localName?.includes('-');
    /* What dies: the widget that holds it, as drawn, through slots, as
       the text in a button names the button; else itself. */
    const holder = (el) =>
    {
        for (let c = el; c; c = c.assignedSlot ?? up(c))
            if (c.matches?.(WIDGETS))
                return c;
        return el;
    };
    let used = false, inside = false;

    for (const type of spec.kind === 'dead' ? [...ACTS, 'pointerup', 'pointercancel', 'mouseup',
        'touchend', 'touchmove', 'touchcancel', 'keyup', 'keypress', 'contextmenu', 'dragstart',
        'dragenter', 'dragover', 'wheel', 'submit'] : [])
        addEventListener(type, (e) =>
        {
            const path = e.composedPath();
            const act = e.isTrusted && ACTS.has(e.type) && !PASSING.has(e.key);

            /* Through what dies, and whether it is its own: all of a widget
               is, and of anything else, what no control it holds takes. */
            let through = false, dead = false;

            for (const el of live)
            {
                const d = changed.has(el) && holder(el), at = d ? path.indexOf(d) : -1;

                through ||= at >= 0;
                dead ||= at >= 0 && (d.matches?.(WIDGETS) || !path.slice(0, at).some(control));
            }
            /* Until the page's own next task or frame. */
            if (act && through)
            {
                inside = true;
                setTimeout(() => inside = false);
                requestAnimationFrame(() => inside = false);
            }
            if (!dead)
                return;
            if (!KEPT.has(e.type) && !PASSING.has(e.key))
                e.preventDefault();
            e.stopImmediatePropagation();
            if ((act || e.type === 'submit' && inside) && !used)
            {
                used = true;
                said({ used: true });
            }
        }, { capture: true, passive: false });
    const frame = () =>
    {
        [undone, since] = [new Map(), performance.now()];
        recheck();
        cover();
        if (!fought)
            requestAnimationFrame(frame);
    };
    const attach = Element.prototype.attachShadow;

    /* A shadow root filled after the page has loaded changes nothing an
       observer outside it hears of. */
    Element.prototype.attachShadow = function (init)
    {
        const root = attach.call(this, init);

        if (init?.mode === 'open')
        {
            watched.add(root);
            roots.add(root);
            watch(root);
        }
        return root;
    };
    /* Which elements were changed, for Playwright to tell a copy the page
       missed from what it changed. */
    globalThis[key] = (el) => changed.has(el);
    watch(document);
    /* What a page made before mutated() has already. */
    scan(document);
    requestAnimationFrame(frame);
}
