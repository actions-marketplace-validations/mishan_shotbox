/*
 * Copyright (C) 2026 Misha Nasledov <misha@nasledov.com>
 *
 * SPDX-License-Identifier: MIT
 */

/* A checkout page, from a host of its own, in a context that mutated()
   changes: what the tests here drive under `shotbox mutate`. */

import { chromium } from 'playwright';
import { mutated } from '../../node/index.mjs';

const PAGE = `<!doctype html><title>Checkout</title>
<style>body { font: 16px sans-serif; margin: 24px }
  button { position: absolute; left: 24px; top: 80px; width: 80px; height: 36px }
  #paid { margin-top: 80px }</style>
<p>Total: $42.50</p>
<button>Pay</button>
<p id="paid" hidden>Paid $42.50</p>
<script>
  document.querySelector('button').addEventListener('click', () => document.getElementById('paid').hidden = false);
</script>`;

export async function checkout ()
{
    const browser = await chromium.launch();
    const context = await browser.newContext({ viewport: { width: 400, height: 300 } });

    await context.route('http://shop.test/', (r) => r.fulfill({ contentType: 'text/html', body: PAGE }));
    await mutated(context);
    const page = await context.newPage();

    await page.goto('http://shop.test/');
    return { browser, page };
}
