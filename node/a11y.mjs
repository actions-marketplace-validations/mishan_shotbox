/*
 * Copyright (C) 2026 Misha Nasledov <misha@nasledov.com>
 *
 * SPDX-License-Identifier: MIT
 */

/*
 * a11y.mjs -- Chromium on a session's accessibility bus, inside
 * `shotbox run --a11y`, so `shotbox a11y` reads, lints and walks its pages.
 *
 *     const browser = await chromium.launch({ ...accessible });
 *     const browser = await chromium.launch({ ...accessible,
 *                                             args: [...accessible.args, ...steady] });
 *
 * The session says a screen reader is running (ACCESSIBILITY_ENABLED),
 * and `shotbox a11y` asks for the page's part of the tree as Orca does;
 * Chromium also needs a window: headless, it isn't on the bus at all.
 * And no LCD text: subpixel antialiasing tints every pixel of a thin glyph, so
 * `shotbox a11y contrast` would find none of the text's own color. And
 * reduced motion, so a page that honors it holds still to be measured:
 * a page's option, since Playwright's pages emulate no preference unless
 * told, which launch() ignores.
 *
 *     const page = await browser.newPage({ ...accessible });
 */

export const accessible = {
    headless: false,
    args: ['--disable-lcd-text'],
    reducedMotion: 'reduce',
};
