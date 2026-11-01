/*
 * Copyright (C) 2026 Misha Nasledov <misha@nasledov.com>
 *
 * SPDX-License-Identifier: MIT
 */

import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { checks, gif, launch, sealed, serve, until } from '../node/index.mjs';
import { bumped } from '../node/mutate.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));

/* A request with the path exactly as written: fetch() tidies away the
   "../" a hostile client would send. */
const has = (cmd, flag = '-version') =>
{
    try { execFileSync(cmd, [flag], { stdio: 'ignore' }); return true; }
    catch { return false; }
};

const raw = (site, where) => new Promise((ok, no) =>
    http.get({ host: '127.0.0.1', port: site.address().port, path: where },
             (res) => { res.resume(); ok(res.statusCode); }).on('error', no));

test('serve answers with the file and its type, and nothing outside the root', async () =>
{
    const site = await serve(path.join(here, '..', 'node'));
    const base = `http://127.0.0.1:${site.address().port}`;

    try
    {
        const ok = await fetch(`${base}/index.mjs`);
        assert.equal(ok.status, 200);
        assert.match(ok.headers.get('content-type'), /text\/javascript/);
        assert.equal(await raw(site, '/..%2fREADME.md'), 403);
        assert.equal((await fetch(`${base}/%E0%A4%A`)).status, 400);
        assert.equal((await fetch(`${base}/nope.txt`)).status, 404);
    }
    finally
    {
        site.close();
    }
});

test('launch reports how a program ended and what it said', async () =>
{
    const a = launch('sh', ['-c', 'echo hello; exit 3']);
    assert.ok(await until(() => a.done, 5000));
    assert.match(a.why(0), /code 3/);
    assert.match(a.why(0), /hello/);

    const b = launch('shotbox-no-such-program');
    assert.ok(await until(() => b.done, 5000));
    assert.match(b.why(0), /could not be started/);
});

test('launch kills the whole group, children and all', async () =>
{
    const a = launch('sh', ['-c', 'sleep 30 & sleep 30; wait']);
    await new Promise((ok) => setTimeout(ok, 200));
    a.kill();
    assert.ok(await until(() => a.done, 5000));
});

test('checks count failures and say each one', () =>
{
    let said = '';
    const t = checks({ write: (s) => { said += s; } });

    t.check(true, 'one');
    t.check(false, 'two');
    t.skip('three');
    assert.equal(t.done(), 1);
    assert.match(said, /^ok {4}one$/m);
    assert.match(said, /^FAIL {2}two$/m);
    assert.match(said, /^skip {2}three$/m);
});

test('bumped changes the last digit of the first number, keeping its length', () =>
{
    for (const [text, want] of [
        ['Total: 42.50', 'Total: 42.51'],
        ['1,299', '1,300'],
        ['1.999,99 €', '2.000,00 €'],
        ['$0.99 for 3', '$1.00 for 3'],
        ['1\u00a0299', '1\u00a0300'],
        ['3 400 items', '4 400 items'],
        ['3\u00a012 items', '4\u00a012 items'],
        ['1\u00a02345', '2\u00a02345'],
        ['0.12345', '0.12346'],
        ['1\u202f299,50 €', '1\u202f299,51 €'],
        ["1'299", "1'300"],
        ['3 of 400', '4 of 400'],
        ['12:30', '12:31'],
        ['-3', '-4'],
        ['v2.0', 'v2.1'],
        ['9 items', '8 items'],
        ['$999,999.99', '$999,999.98'],
        ['no digits', null],
        ['', null],
    ])
        assert.equal(bumped(text), want, text);
});

test('bumped, over random texts: a text with a digit always changes, keeping its length and all but digits, from its first digit on, and reads one higher, or one lower where it was all nines', () =>
{
    let seed = 1;
    const random = (n) => (seed = (seed * 48271) % 2147483647) % n;
    const ALPHABET = '0123456789012345678901234567890123456789.,:\'\u00a0\u2009\u202f $-€abcxyz';

    for (let i = 0; i < 20000; i++)
    {
        const text = Array.from({ length: random(16) }, () => ALPHABET[random(ALPHABET.length)]).join('');
        const got = bumped(text);
        const what = JSON.stringify([text, got]);

        if (!/\d/.test(text))
        {
            assert.equal(got, null, what);
            continue;
        }
        assert.equal(got?.length, text.length, what);
        const changed = [...text].flatMap((c, at) => c === got[at] ? [] : [at]);

        assert.ok(changed.length && changed.every((at) => /\d/.test(text[at]) && /\d/.test(got[at])), what);
        const from = text.search(/\d/), to = changed.at(-1) + 1;
        const value = (s) => BigInt(s.slice(from, to).replace(/\D/g, ''));
        const was = text.slice(from, to).replace(/\D/g, '');

        assert.ok(changed[0] >= from, what);
        assert.equal(value(got) - value(text), /^9+$/.test(was) ? -1n : 1n, what);
    }
});

test('gif cuts the start and keeps to the palette it is given',
     { skip: !has('ffmpeg') && 'no ffmpeg' }, async () =>
{
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'shotbox-test-'));
    const film = path.join(dir, 'film.webm');
    const frames = (file) => Number(execFileSync('ffprobe', [
        '-v', 'error', '-count_frames', '-select_streams', 'v:0',
        '-show_entries', 'stream=nb_read_frames', '-of', 'csv=p=0', file,
    ]).toString().trim());

    try
    {
        execFileSync('ffmpeg', ['-y', '-f', 'lavfi', '-i',
                                'testsrc=size=160x120:rate=10:duration=2', film],
                     { stdio: 'ignore' });

        const all = await gif(film, path.join(dir, 'all.gif'),
                              { width: 80, fps: 10 });
        const cut = await gif(film, path.join(dir, 'cut.gif'),
                              { width: 80, fps: 10, from: 1, colors: 16,
                                dither: 'none' });

        assert.equal(frames(all), 20);
        assert.equal(frames(cut), 10);

        /* Counted in the frames, not read off the header: ffmpeg writes
           a full-sized color table whatever it puts in it. */
        const used = execFileSync('identify', ['-format', '%k\n', cut])
            .toString().trim().split('\n').map(Number);
        assert.ok(Math.max(...used) <= 16, `colors per frame: ${used}`);
    }
    finally
    {
        await fs.rm(dir, { recursive: true, force: true });
    }
});

test('sealed keeps what is allowed, and nothing of the caller\'s', async () =>
{
    const before = { ...process.env };

    Object.assign(process.env, { DISPLAY: ':0', WAYLAND_DISPLAY: 'wayland-0',
                                 SSH_AUTH_SOCK: '/nope', SHOTBOX_TEST: 'yes' });

    const seal = await sealed({ pass: ['SHOTBOX_TEST'], env: { A: 'b' } });

    process.env = before;

    try
    {
        const { env } = seal;

        for (const k of ['DISPLAY', 'WAYLAND_DISPLAY', 'SSH_AUTH_SOCK'])
            assert.equal(env[k], undefined, k);
        assert.equal(env.PATH, process.env.PATH);
        assert.equal(env.SHOTBOX_TEST, 'yes');
        assert.equal(env.A, 'b');
        assert.equal(env.TZ, 'UTC');
        assert.ok(env.HOME.startsWith(seal.dir + path.sep));
        assert.ok(env.XDG_CONFIG_HOME.startsWith(env.HOME + path.sep));
        assert.equal((await fs.stat(env.XDG_RUNTIME_DIR)).mode & 0o777, 0o700);
    }
    finally
    {
        await seal.close();
    }

    await assert.rejects(fs.stat(seal.dir));
});

/* What it is for: a fontconfig alias in the caller's home, which a
   browser would draw with, is not one in the seal's. The alias is to the
   monospace font, which is not what an unknown family falls back to. */
test('sealed leaves the caller\'s fonts behind',
     { skip: !has('fc-match', '--version') && 'no fontconfig' }, async () =>
{
    const home = await fs.mkdtemp(path.join(os.tmpdir(), 'shotbox-test-'));
    const conf = path.join(home, '.config', 'fontconfig');
    const seal = await sealed();
    const match = (family, env = seal.env) => execFileSync(
        'fc-match', ['-f', '%{family[0]}', family], { env }).toString();
    const mono = match('monospace');

    try
    {
        assert.notEqual(match('ShotboxTest'), mono);

        await fs.mkdir(conf, { recursive: true });
        await fs.writeFile(path.join(conf, 'fonts.conf'), `<?xml version="1.0"?>
<fontconfig>
  <alias binding="strong">
    <family>ShotboxTest</family>
    <prefer><family>${mono}</family></prefer>
  </alias>
</fontconfig>
`);

        const theirs = { ...process.env, HOME: home };

        delete theirs.XDG_CONFIG_HOME;
        assert.equal(match('ShotboxTest', theirs), mono);
        assert.notEqual(match('ShotboxTest'), mono);
    }
    finally
    {
        await seal.close();
        await fs.rm(home, { recursive: true, force: true });
    }
});

test('shotbox-serve, run through a link as npm runs it, says where the page is',
     async () =>
{
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'shotbox-test-'));
    const link = path.join(dir, 'shotbox-serve');

    await fs.mkdir(path.join(dir, 'site', 'demo'), { recursive: true });
    await fs.writeFile(path.join(dir, 'site', 'demo', 'index.html'), 'here');
    await fs.symlink(path.join(here, '..', 'node', 'serve.mjs'), link);

    const app = launch(link, [path.join(dir, 'site'), '0', 'demo/']);

    try
    {
        assert.ok(await until(() => /\n/.test(app.output), 5000), app.why(5000));

        const url = app.output.trim();

        assert.match(url, /^http:\/\/127\.0\.0\.1:\d+\/demo\/$/);
        assert.equal(await (await fetch(url)).text(), 'here');
    }
    finally
    {
        app.kill();
        await fs.rm(dir, { recursive: true, force: true });
    }
});
