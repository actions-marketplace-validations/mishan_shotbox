# Mutation testing

`shotbox mutate` runs a browser test against small changes to the page
it tests, to find a test that fails on a change that shouldn't matter
(BRITTLE) or passes on one that should (BLIND):

```
shotbox mutate --target 'button "Pay"' --target 'text:/Total: .*/' -- node checkout.test.mjs
```

```
shotbox mutate: 10 runs, plus 3 clean
clean      pass  pass  (1.1 s)
size 1024x768                   pass     robust
size 1920x1080                  pass     robust
move button "Pay"               pass     robust
hide button "Pay"               FAIL     caught
dead button "Pay"               pass     BLIND
number button "Pay"             pass     UNAPPLIED  (its text and aria-label have no number to change)
move text:/Total: .*/           pass     robust
hide text:/Total: .*/           pass     BLIND
dead text:/Total: .*/           pass     UNAPPLIED  (the change was made, and the test never used the target)
number text:/Total: .*/         pass     BLIND
control    pass
3 brittle or blind of 10; 2 unapplied
reproduce: shotbox mutate --target 'button "Pay"' --target 'text:/Total: .*/' --only 'dead button "Pay"' -- node checkout.test.mjs
```

That test clicks Pay and checks nothing after: it passes with Pay dead,
with the total hidden, and with the total a cent off.

## The test's half

The test opts in with one line, before it opens its first page:

```js
import { chromium } from 'playwright';
import { mutated } from 'shotbox';

const browser = await chromium.launch();
const context = await browser.newContext();
await mutated(context);
const page = await context.newPage();
try
{
    // the test
}
finally
{
    await browser.close();
}
```

Close the browser or the context before the test ends, in a `finally`:
the last look at the target is made as it closes. A test that dies
with its browser open, and has made a change that should matter fail,
still counts as having caught it.

With Playwright Test, which makes a context for each test, give its
`context` fixture the line:

```js
import { test as base } from '@playwright/test';
import { mutated } from 'shotbox';

export const test = base.extend({
    context: async ({ context }, use) =>
    {
        await mutated(context);
        await use(context);
    },
});
```

and run `shotbox mutate --target ... -- npx playwright test`.

In a run of `shotbox mutate`, `mutated()` makes that run's change to
every page and frame of the context, and writes down whether it was
made and proven; in a clean run it changes nothing and notes which
targets it saw. Anywhere else it does nothing. Call it for each
context a test makes, as a test runner makes one for each test.

The change is made by a Playwright init script, which runs before the
page's own scripts, in every frame, whatever the page's CSP. It is
made again when the page draws the target anew.

## Targets

- `ROLE "NAME"`: an ARIA role and its exact name, as
  `getByRole(role, { name, exact: true })` finds it; `ROLE /RE/` for a
  JavaScript regex the name matches in full.
- `text:"TEXT"`, `text:/RE/`: the nearest element all of the text is
  in, as `getByText(text, { exact: true })` finds it; the regex matches
  it in full.
- `css:SELECTOR`: what the selector finds.

With no `--target`, only the `size` runs are made.

## Changes

| kind | should it matter | what it does |
|---|---|---|
| `size` | no | runs every page at 1024x768, then at 1920x1080 (a run each) |
| `move` | no | moves the target 40 px, the first way of right, down, left and up where it stays on the page, covers no other control's center and nothing comes over it |
| `hide` | yes | `display: none` on the target |
| `dead` | yes | the target looks and is named the same, but clicks, keys, typing, drags, drops and submits aimed at it never reach the page |
| `number` | yes | its first number one higher (`42.50` to `42.51`, `1,299` to `1,300`), or lower where a carry would add a digit (`9` to `8`), in the text it shows and its aria-label |

A change to a target is proven by Playwright, looking for the target
as the test would: once it's made, the test must find nothing by the
target that wasn't changed, at once, every half second while the test
runs, and as each page closes. A dead target must also be used, by a
real press, click, key, input or drop: a pointer passing over it, a Tab
through it, or the page's own click says nothing of the test. A size is
proven once Playwright has set it on a page; it's UNAPPLIED where the
page has that size already, and spoiled by the test calling
`setViewportSize` with another (a context's own viewport is
overridden).

A dead target that a widget holds, as a button holds its text, kills
the whole widget; a dead container, as a form, loses only its own
actions, so its fields still take typing.

`size` and `move` assume a functional test: they break a screenshot
comparison by design.

## Verdicts

| change | test passes | test fails | stopped at `--timeout` | not proven |
|---|---|---|---|---|
| shouldn't matter | robust | BRITTLE, if it fails again on a rerun while a clean rerun passes; else inconclusive | inconclusive | UNAPPLIED |
| should matter | BLIND | caught | caught (timeout) | UNAPPLIED |

A change that should matter, made and still being looked for when the
test failed, as a test that dies at once can't wait for, counts as
caught; not where the page fought it, a copy of the target was found
showing, or the test never used a dead target.

An UNAPPLIED run says nothing of the test, with why: the target was
never seen, the page undoes the change faster than it's drawn, a copy
of the target is still showing unchanged, the test never used a dead
target, and so on.

`shotbox mutate` runs the test twice clean first, and stops with 2 if
either clean run fails, never calls `mutated()`, or misses any target;
and for a bad option, a `--save` directory that isn't empty, or a
session that can't start. Otherwise it exits 1 when something is
BRITTLE or BLIND (UNAPPLIED too, with `--strict`) and 0 when nothing
is; an inconclusive run doesn't count. A final clean run checks that
nothing outside the sessions (files, the network) changed between
runs; if it fails, the findings are marked suspect, which doesn't
change the exit status.

Options: `--kinds` picks the changes, `--only RUN` reruns one (as the
`reproduce:` line does), `--confirm N` sets how many reruns a BRITTLE
needs (1), `--timeout S` stops a run after S seconds (by default, a
changed run after 3 times the slower clean run, at least 30 s, and the
first two clean runs never), `--save DIR` keeps each run's output and
the page side's reports. The session options apply to every run. A
hidden target makes a test that clicks it wait out its timeout: the
Playwright library's 30 s action timeout, or Playwright Test's 30 s
test timeout, unless the test sets a shorter one. The count of
clean runs at the start leaves out the clean reruns that confirm a
BRITTLE.

`--json` prints, in place of the lines above, `{runs: [{label, spec,
ended, seconds, proven, why, verdict}], clean, control, suspect,
strict, reproduce}`, and nothing when it stops with 2:

- `spec`: the change, as `{kind, target}` with the target as
  `{by: "role", role, name}` (or `re` for a regex), `{by: "text", name}`
  or `re`, or `{by: "css", css}`; for a size, `{kind: "size", screen:
  [width, height]}`.
- `ended`: `pass`, `fail` or `timeout`; `seconds`, how long it ran.
- `why`: the page side's reason, without the reruns' notes; for a run
  caught though its change wasn't yet proven (`proven` false), that.
- `clean`: how the two first clean runs ended, `["pass", "pass"]`;
  `control`: how the last one did.
- `reproduce`: the command for the first finding, or `null`.

From Python: `shotbox.mutate(cmd, targets, kinds=None, confirm=1,
timeout=None, only=None, save=None, strict=False, out=None,
**session_options)`, with `cmd` a list of the program and its
arguments, `kinds` a list or `"a,b"`, `out` a file for the lines above
but the `reproduce:` line, and the session options as `Session` takes
them (`size=(1024, 768)`, `a11y=True`, `net="loopback"`: [Python](python.md)).
It returns a `MutationReport`: `runs`, `brittle_runs`, `blind_runs`,
`unapplied_runs`, `clean`, `control`, `suspect`, `strict` and
`status`, 0 or 1; to rerun one, pass its `label` as `only`. Where the
command stops with 2, it raises `shotbox.SessionError`.

## Limits

- Chromium through Playwright for Node only. Only contexts passed to
  `mutated()` are changed; a page already open when it's called is
  changed late, after its own scripts have run and maybe been drawn
  unchanged.
- The verdict looks for the target as the target says, not by the
  test's own locator. A test that finds the same element some other
  way is judged by that look.
- The page side reads names and roles as near as a page can. A target
  named only by what it can't read (a `::before` label, a row's or grid
  cell's text) isn't found, and the clean runs stop with 2; a copy of
  it named so leaves the run UNAPPLIED.
- A copy of the target that appears, is used and is gone within half
  a second, between two looks, isn't seen. A process that exits
  without closing its context keeps its last look.
- Closing a page can take up to 13 s, while the last look finishes.
- On big pages: a role target on a page of about 50,000 elements can't
  be proven in time and is UNAPPLIED; the half-second looks cost about
  10% on a 40,000-element page; a CSS target matching a thousand
  elements makes `move` take seconds.
- `dead`: focus and `mousemove` still reach the page, so a widget that
  opens on focus still works; the page's own wheel listeners over it
  don't hear the wheel; an iframe inside it isn't dead; a `<select>`
  or a field Playwright sets with no event (a date, a color, a range, a
  file) can't be made dead; Tab and Shift do what they do, but the
  page's own listeners on it don't hear them. A filmed run's `dress()`
  pointer shows no press over a dead target.
- `number`: the text the target shows and its aria-label, not a field's
  value. A page that writes a text node's own text back to it has it
  bumped again each time.
- `size`: a popup the page opens with an address loads at the
  context's size first; a page with no viewport of its own may draw
  for a moment at its window's.
- `move`: other controls are judged by their centers, so it can cover
  part of a wide one; an overlay in the parent page over a target in
  an iframe isn't seen.
