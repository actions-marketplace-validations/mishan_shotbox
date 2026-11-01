/*
 * Copyright (C) 2026 Misha Nasledov <misha@nasledov.com>
 *
 * SPDX-License-Identifier: MIT
 */

/* Pays by clicking where the button is drawn, and checks the receipt. */

import { checkout } from './page.mjs';

const { browser, page } = await checkout();

try
{
    await page.mouse.click(30, 98);
    await page.getByText('Paid $42.50').waitFor({ timeout: 3000 });
}
finally
{
    await browser.close();
}
