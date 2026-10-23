/*
 * Copyright (C) 2026 Misha Nasledov <misha@nasledov.com>
 *
 * SPDX-License-Identifier: MIT
 */

/**
 * Chromium's launch options for `shotbox run --a11y`, `launch({ ...accessible })`,
 * and its pages', `newPage({ ...accessible })`.
 */
export const accessible: { readonly headless: false; readonly args: string[];
                           readonly reducedMotion: 'reduce' };
