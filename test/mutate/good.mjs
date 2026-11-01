/*
 * Copyright (C) 2026 Misha Nasledov <misha@nasledov.com>
 *
 * SPDX-License-Identifier: MIT
 */

/* Pays by the button's name, and checks the total and the receipt. */

import { checkout } from './page.mjs';

const { browser, page } = await checkout();

try
{
    const total = page.getByText(/^Total: /).filter({ visible: true });

    if (await total.textContent({ timeout: 3000 }) !== 'Total: $42.50')
        throw new Error(`the total is ${await total.textContent()}`);
    await page.getByRole('button', { name: 'Pay' }).click({ timeout: 3000 });
    await page.getByText('Paid $42.50').waitFor({ timeout: 3000 });
}
finally
{
    await browser.close();
}
