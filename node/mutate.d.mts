/*
 * Copyright (C) 2026 Misha Nasledov <misha@nasledov.com>
 *
 * SPDX-License-Identifier: MIT
 */

import type { BrowserContext } from 'playwright';

/** In a run of `shotbox mutate`, make its change to the context's pages
    and prove it; anywhere else, nothing. Before the first page loads. */
export function mutated (context: BrowserContext): Promise<void>;
