# DeepSeek Harness plugin for pha — `dsh-pha/`

This repository ships a **DeepSeek Harness plugin** (`dsh-pha/`) that lets a Harness agent
drive the **personal-historical-archive** tool from chat, on any machine that has both
`pha` and a Harness install.

## What you get

- **`pha_*` model tools** (host): `pha_status`, `pha_documents`, `pha_document`, `pha_page`,
  `pha_search`, `pha_archive`, `pha_job_start`, `pha_job_status`, `pha_job_kill` — plus a
  read-only archive accessor (opens `archive.db` with `immutable=1`, the one mode that opens
  this WAL database without `-wal`/`-shm`) and a background job runner for
  `scan/edit/encode/reindex/review/inbox`.
- **A durable data API** for the GUI: same-origin read-only `/pha/*` JSON routes
  (`/pha/documents`, `/pha/document`, `/pha/page`, `/pha/search`, `/pha/pageImage`,
  `/pha/status`, `/pha/archive`) registered on the harness `webServer` service.
- **A PHA conversation view** (document list + search, page reader raw/edited, side-by-side
  page image, markdown rendering, draggable splitter) as a client module source at
  `dsh-pha/src/client/index.js`. It is registered into `conversation.view` and fetches `/pha/*`.

## Install on a Harness machine

See the full step-by-step in [`dsh-pha/README.md`](dsh-pha/README.md). In short:

```sh
# 1. make the package resolvable in the harness profile (pnpm workspace)
pnpm add /path/to/repo/dsh-pha

# 2. add the composition row (see dsh-pha/cordis.patch.example.yml)
#    -> id: dsh-pha / name: '@personal-historical-archive/dsh-pha'

# 3. restart the harness profile
```

The `pha_*` tools and the `/pha/*` API are active straight after install. The **PHA view**
ships self-contained: `dsh-pha/lib/client.js` is the already-built browser bundle, committed
in the repo, so after install + restart the harness serves it (via its `dsh.client` →
`./client` module) and the **PHA** tab appears in the conversation header — no `dev:web`
build needed on the end-user machine. To rebuild it after editing the source, run
`node dsh-pha/scripts/build-client.mjs` and commit `lib/client.js`.

## Caveats

- Requires `pha` on PATH (or at `~/.local/bin/pha`, `/usr/local/bin/pha`, `/opt/homebrew/bin/pha`)
  and an archive configured (`pha set archive-dir <path>` or `PHA_ARCHIVE_DIR`). The plugin
  discovers the archive from `pha status`/`pha doctor` at runtime.
- The `dsh.client.inject` list in `dsh-pha/package.json` is a best-effort guess against the
  installed client-module contract; if the view doesn't mount, adjust it. The host tools and
  `/pha/*` API are independent of it.
- `immutable=1` reads the main DB file and ignores any still-uncheckpointed WAL, so during an
  active `pha scan` brand-new pages may appear a moment late; `pha_search` is always current.

See also: `WEB_INTERFACE_PLAN.md` (web UI plan) and `VSCODE_EXTENSION_SPEC.md` (the VS Code
extension equivalent) for the broader interface design.
