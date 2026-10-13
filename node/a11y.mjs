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
 * The session says a screen reader is running (ACCESSIBILITY_ENABLED);
 * Chromium also needs its own switch, or the page's part of the tree is
 * empty, and a window: headless, it isn't on the bus at all.
 */

export const accessible = {
    headless: false,
    args: ['--force-renderer-accessibility'],
};
