/*
 * Copyright (C) 2026 Misha Nasledov <misha@nasledov.com>
 *
 * SPDX-License-Identifier: MIT
 */

/* Checks the total and throws at once, leaving its browser open, as a
   plain script does. */

import { checkout } from './page.mjs';

const { browser, page } = await checkout();
const total = await page.getByText(/^Total: /).textContent();

if (total !== 'Total: $42.50')
    throw new Error(`the total is ${total}`);
await browser.close();
