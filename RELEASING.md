# Releasing pha and the PHA view plugin (`dsh-pha`)

This repository publishes two packages from one release unit:

- the Python tool `personal-historical-archive` (`pha`) — version in
  `pyproject.toml` and `src/personal_historical_archive/__init__.py`;
- the npm package `@personal-historical-archive/dsh-pha` — version in
  `dsh-pha/package.json`.

They move together. `pha update` watches the remote Python `__version__` on the
GitHub default branch, so **any change to the Python tool or the shipped
`dsh-pha` payload must bump the release**. An unbumped push is invisible to
installed machines.

Repo-only docs/tests (`README.md`, `AGENTS.md`, `RELEASING.md`, tests) can be
pushed without a bump unless they must reach installed machines. The CI guard
only requires a bump when `dsh-pha/` changes.

## Release checklist

1. Make the change.
2. Bump all three version files together:
   ```sh
   python scripts/bump_release.py patch    # or minor / major / --set X.Y.Z
   ```
3. Run the CI guard for plugin-only changes:
   ```sh
   python scripts/check_release_bump.py origin/main
   ```
4. Verify:
   ```sh
   PYTHONPATH=src .venv/bin/python -m pytest -q
   node dsh-pha/scripts/smoke.mjs
   UV_CACHE_DIR=/tmp/uv-cache .venv/bin/python scripts/check_wheel_ships_schema.py
   ```
   The wheel gate also proves that `_view/package.json`, `_view/lib/index.js`
   and `_view/lib/client.js` are bundled.
5. Commit and push. Agent commits end with a `Model:` trailer (see
   [AGENTS.md](AGENTS.md)).
6. Confirm the remote raw version has refreshed:
   ```sh
   curl -fsSL https://raw.githubusercontent.com/joaquimrcarvalho/personal-historical-archive/main/src/personal_historical_archive/__init__.py
   ```
   GitHub raw can serve the previous revision for a short time after a push.
   If `pha update --check` reports the old version immediately after a push,
   retry a minute later.

## What `pha update` does

- Source checkout install: `git pull --ff-only origin main`.
- Wheel/uv-tool install: `uv tool install --force git+https://github.com/.../@main`.
- From release 0.36.1 onward it then runs `pha view install` in a fresh process
  to install or refresh the DSH plugin in every profile that uses it.

## `pha view install` and `pha view status`

- `pha view install [--profile NAME] [--all-profiles]` links the bundled payload
  into `$DSH_HOME/profiles/<name>/node_modules/@personal-historical-archive/dsh-pha`,
  writes/updates the `cordis.patch.yml` row, and adds `projectRoot` and/or
  `archiveDir`.
- `pha view status` reports the payload, version, link and row per profile.
- No DSH install: the update hook returns silently; a direct `pha view install`
  prints `no DeepSeek Harness profiles found` and exits `0`.
- An explicit `--profile` that does not exist is an error (exit `2`).
- A DSH host restart is always required to load a new host plugin.

## First upgrade from a version before the view sync

Versions before 0.36.1 do not know about `pha view install`, so the first
transition on such a machine needs one manual step:

```sh
pha update
pha view install
# restart DSH
```

From 0.36.1 onward `pha update` runs both steps itself.

## How the plugin finds the archive

- `dsh-pha` accepts row config `projectRoot` and `archiveDir`, and passes them
  to its `pha` child processes as `PHA_HOME` and `PHA_ARCHIVE_DIR`.
- If neither config nor `PHA_HOME` is set, the plugin derives `projectRoot`
  from its own real path when it is linked from a checkout.
- `pha view install` writes `archiveDir` from `cfg.archive_dir` when the
  archive is explicit, so a uv-tool/pip machine with no checkout still works.

## Release history

- `0.37.1` `fix(ingest): normalize raw_sha for surrounding whitespace` — the
  edit-staleness trim mismatch (regression test in `tests/test_filter_hooks.py`)
- `0.37.0` `8e23a32` `fix(pha): never build an unowned archive; per-user settings + --global`
- `0.36.2` `db22376` `fix(view): skip gracefully when no DSH profiles are installed`
- `0.36.1` `c574d83` `feat(update): ship and sync the PHA view plugin with pha`
- `0.36.0` `0ed96f0` and earlier: no bundled view-plugin sync
