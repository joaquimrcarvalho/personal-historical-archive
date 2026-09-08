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

## GUI (conversation) view — follow-up

The dynamic prototype also added a graphical **PHA view** (document list, page reader,
side-by-side page image, search). That browser UI is **not yet part of this durable package**,
because it uses the dynamic-plugin–only client↔host RPC (`harness.handle`/`host.call`), which
real client modules don't have; a durable version must instead:

1. expose the read/mutation surface as a real client↔host **remote service** on the host side,
2. ship a **client module** (`lib/client.js` + a `dsh.client` declaration in `package.json`),
   registered into `conversation.view`, and
3. be rebuilt into the harness web bundle (the `dev:web` client build) and the app restarted.

This is a separate change in this same package; the plan is documented in the repo's
`WEB_INTERFACE_PLAN.md` / `VSCODE_EXTENSION_SPEC.md` spirit. The host tool surface above is the
durable, portable part that already works — chat with your archive from any session.

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
