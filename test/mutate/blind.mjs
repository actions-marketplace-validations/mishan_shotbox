/*
 * Copyright (C) 2026 Misha Nasledov <misha@nasledov.com>
 *
 * SPDX-License-Identifier: MIT
 */

/* Pays, and checks nothing. */

import { checkout } from './page.mjs';

const { browser, page } = await checkout();

try
{
    await page.getByRole('button', { name: 'Pay' }).click({ timeout: 3000 });
}
finally
{
    await browser.close();
}
