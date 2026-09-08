# dsh-pha — DeepSeek Harness plugin for personal-historical-archive (pha)

A [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) **host plugin** that
exposes the `pha_*` model tools and the archive runner, so any agent session on a machine
that has `pha` + an archive can drive it from chat.

It is intended to live **inside this repository** and be installed by anyone who has both
the `personal-historical-archive` tool and a DeepSeek Harness install, so that *other
machines / other harnesses pick it up* by installing this package (or referencing this
directory) as a plugin in their harness profile.

## What it provides

Model tools (registered globally, so they are available to the agent in **every** session):

| tool | purpose |
| --- | --- |
| `pha_status` | `pha status` text report |
| `pha_documents` | structured document list (id, filename, dir_path, kind, page_count, status, palaeographer, editor, models) |
| `pha_document` | one document's pages + per-page editor entries |
| `pha_page` | one page's text (raw / `--edited`) |
| `pha_search` | full-text search (hybrid) → structured hits |
| `pha_archive` | archive dir + engine health (`pha doctor`) |
| `pha_job_start` / `pha_job_status` / `pha_job_kill` | run `pha scan/edit/encode/reindex/review/inbox` as background jobs |

All reads go through `pha` (any where a structured/JSON surface exists) or a **read-only,
`immutable=1`** sqlite open of `archive.db` — which is the one mode that opens this WAL
database without `-wal`/`-shm`. Mutations always go through the real `pha` CLI, so pha's own
single-model lock, staleness, and review round-trip semantics are respected.

## Requirements

- A DeepSeek Harness install (`dsh` on PATH) and the profile you want to attach this to
  (the Desktop/`web` profile, or a custom one).
- The `personal-historical-archive` package installed and its CLI on PATH (`pha`), with an
  archive configured (`pha set archive-dir <path>` or `PHA_ARCHIVE_DIR`). The plugin
  discovers the archive from `pha status`/`pha doctor` at runtime.

## Install (per machine / per harness)

The harness composes plugins from **profiles** of `cordis` patch layers. To attach this
plugin to a profile there are two parts: make the package resolvable, and add one row.

### 1. Make the package resolvable in the profile

The profile's workspace lives under `$DSH_HOME/profiles/<name>/` (e.g. `desktop`). Add this
package as a dependency (pnpm, since that is how the bundled profiles are laid out):

```sh
# from inside the profile's workspace (where its package.json / pnpm-workspace.yaml live)
pnpm add /path/to/this/repo/dsh-pha
```

If the profile uses a lockfile/workspace layout you prefer not to touch, an alternative is to
copy/symlink the package into the profile's `node_modules`:

```sh
mkdir -p "$DSH_HOME/profiles/<name>/node_modules/@personal-historical-archive"
ln -s /path/to/repo/dsh-pha "$DSH_HOME/profiles/<name>/node_modules/@personal-historical-archive/dsh-pha"
```

> `@deepseek-ai/dsh-tools` (the plugin's only dependency) is already present in the harness
> deployment, so it does not need a separate install.

### 2. Add a composition row to the profile

Append to the profile's `cordis.patch.yml` (see `cordis.patch.example.yml` in this folder):

```yaml
- insert:
    - id: dsh-pha
      name: '@personal-historical-archive/dsh-pha'
```

### 3. Restart the harness profile

```sh
dsh --profile <name> --help   # confirm it boots; then start the app as you normally do
```

After the profile starts, the nine `pha_*` tools are registered in the host `tools`
registry and appear in every session's tool catalog. Trigger them like any tool, e.g. ask the
agent: `list the documents`, `show document 1 page 2`, `search for missão`. Mutating actions
(`pha scan/edit/encode/reindex/review/inbox`) can be started with `pha_job_start` and polled
with `pha_job_status`; pha's own lock prevents overlapping local-model jobs.

## GUI (conversation) view

The graphical **PHA view** is preserved in this package as:

- **Host data layer** (`lib/index.js`): same-origin read-only JSON routes under `/pha/*`
  (`/pha/documents`, `/pha/document`, `/pha/page`, `/pha/search`, `/pha/pageImage`,
  `/pha/status`, `/pha/archive`) registered on the harness `webServer` service.
- **Client module source** (`src/client/index.js`): the PHA conversation view (document
  list + search, page reader with raw/edited, side-by-side page image, markdown rendering,
  draggable splitter), registered into `conversation.view`, fetching `/pha/*`.

`package.json` already declares the module (`dsh.client` → `./client`) and the `dsh.client`
inject list. The view is **not built into the harness web bundle here** — the DSH
`dev:web` client build compiles `src/client/` into `lib/client.js`, which is registered into
the browser on the next app start. So after install + restart the PHA view appears in the
conversation header (labelled **PHA**), alongside the `pha_*` tools.

Steps to render it on a machine with the harness build toolchain:

```sh
# 1. Build the client module (run from the harness repo's web build)
pnpm --filter <web> build:client        # or `pnpm dev:web` — the deployment's client build
#    outputs lib/client.js in dsh-pha (and re-runs the client module scan)

# 2. Install + compose (as above)
pnpm add /path/to/repo/dsh-pha          # into the profile workspace
# add the row to cordis.patch.yml (see cordis.patch.example.yml)

# 3. Restart the harness profile
```

> The `dsh.client.inject` list is a best-effort guess against the installed version's
> client module contract (`@deepseek-ai/dsh-client-ui-slots`). If the view doesn't mount,
> adjust that inject list to the client APIs the module actually uses; the host data layer
> and tools are independent of it and work regardless.

## Development

- `lib/index.js` is the host plugin (`export { apply, inject, name }`), ESM, no build step.
- The tool bodies mirror the logic proven in the session-scoped prototype; keep the same
  read-only `immutable=1` DB open and the same pha-CLI mutation path.

## Notes / caveats

- `immutable=1` reads the main DB file and ignores any still-uncheckpointed WAL, so during an
  *active* `pha scan` brand-new pages may not appear until pha checkpoints; `pha_search` is
  always current.
- The plugin assumes `pha` is on the harness process's PATH or at a common install location
  (`~/.local/bin/pha`, `/usr/local/bin/pha`, `/opt/homebrew/bin/pha`); adjust the candidate
  list in `lib/index.js` if needed.
