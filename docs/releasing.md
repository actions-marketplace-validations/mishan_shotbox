# Releasing

One version covers both packages: `shotbox` on PyPI (the command and
`import shotbox`) and `shotbox` on npm (the Node helpers). It's in
`shotbox/__init__.py`, `package.json` and `package-lock.json`, and it's the
changelog's newest heading; `test/run.sh` fails if they disagree.

## A release

1. Set the version: `__version__` in `shotbox/__init__.py`, `"version"` in
   `package.json` (by hand: `npm version` reformats the whole file), then
   `npm install --package-lock-only` for the lockfile, and both
   `"version"`s in `server.json`, the MCP registry's entry, and the
   Action's `uses: mishan/shotbox@vX.Y.Z` in README.md and docs/recipes.md.
2. Turn the changelog's `## Unreleased` into `## X.Y.Z — DATE`, with a
   line or two on what the release is.
3. Merge that to main, then tag it and push the tag:

   ```
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```

4. Once `release.yml` has published it, add the release on GitHub, with
   the changelog's section for it as its notes:

   ```
   gh release create vX.Y.Z --verify-tag --title X.Y.Z --notes-file NOTES.md
   ```

5. On the release's page, edit it, check "Publish this Action to the
   GitHub Marketplace" (category Testing) and update it. Only the web
   page can, and the Marketplace's "latest version" stays at the last
   release published there.

`.github/workflows/release.yml` then runs the tests (`ci.yml`, including
its build of both packages from scratch), checks the tag is the version,
and publishes to PyPI and npm, then, once PyPI has it, `server.json` to
the [MCP registry](https://registry.modelcontextprotocol.io) as
`io.github.mishan/shotbox`. The registry takes the PyPI package as
shotbox's because its description, the README, has an `mcp-name` line.

## Once, before the first release

- **GitHub:** in the repository's settings, create two environments,
  `pypi` and `npm`. A required reviewer on them makes each publish wait
  for a click.
- **PyPI:** add a pending trusted publisher for the project `shotbox`
  (Account settings, Publishing): owner `mishan`, repository `shotbox`,
  workflow `release.yml`, environment `pypi`. The first release creates the
  project; no token.
- **npm:** no token, ever; keep 2FA on. npm sets up trusted publishing
  from a package's own settings, so the package has to exist first, and
  staged publishing needs it to exist too. So the first release goes out
  by hand, from a checkout of the release commit, before its tag is
  pushed, with your 2FA code:

  ```
  npm ci
  npm publish
  ```

  Then, on npmjs.com, in the package's settings, add a trusted publisher:
  GitHub Actions, `mishan/shotbox`, workflow `release.yml`, environment
  `npm`, and push the tag. The workflow skips a version npm already has;
  every later release it publishes itself, with provenance, which npm adds
  to what trusted publishing publishes.

To approve each npm release by hand rather than have the tag publish it,
make the workflow's last step `npm stage publish` and approve it with
`npm stage approve`, which asks for 2FA.
