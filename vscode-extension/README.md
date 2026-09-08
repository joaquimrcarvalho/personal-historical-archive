# pha — VS Code extension (Phase 1 MVP)

Implementation of Phase 1 of `VSCODE_EXTENSION_SPEC.md`: a **local-mode**
extension for the archive operator. It wires together what pha already
exposes — the filesystem, a **read-only** `archive.db`, and the `pha` CLI —
and never re-implements pha logic.

## What it gives you

- **PHA Explorer tree** — documents and collections from the dropbox
  (including *unscanned* files), the on-hold **inbox**, and a Configuration
  section over `models/`, `palaeographers/`, `editors/`, `encoders/`.
- **Pipeline drill-down** — every scanned document expands into its pages;
  each page opens its raw **transcription**, **edited** variant, the
  **encoded records**, a **split view** raw ⇄ edited, or the **page render
  image** side-by-side with the transcription.
- **Review round-trip** — pages corrected by a human but not yet imported are
  badged "corrections pending"; *PHA: Import corrections* runs `pha review`
  (never overwrites reviewed pages).
- **Jobs** — scan / edit / encode / reindex / review / inbox --move run as
  managed background jobs, one at a time, deferring to pha's own single-model
  lock ("another scan/edit job is running" is surfaced, never hidden).
- **Status** — status bar summary + a Status view (docs by status, pages,
  chunks vs embedded, pending review).

## Setup

```sh
cd vscode-extension
npm install && npm run compile
npx @vscode/vsce package   # produces pha-vscode-0.1.0.vsix
code --install-extension pha-vscode-0.1.0.vsix
```

Settings: `pha.executablePath` (if `pha` is not on PATH), `pha.archiveDir`
(optional; otherwise resolved like pha: `PHA_ARCHIVE_DIR` env → project
`.env` → `config.yaml paths.archive_dir` → workspace root), `pha.pythonPath`
(used for the read-only DB queries; pha guarantees a Python).

## Design notes

- `archive.db` is opened **read-only** (`file:...?mode=ro` via python3); all
  writes go through the `pha` CLI so WAL, locks, and migrations are
  respected. No native SQLite Node module is shipped.
- Config files open as normal Markdown editors; the extension only lists,
  opens, and duplicates samples. **Saving a definition re-processes affected
  documents** on the next pass (mtime staleness) — same contract as the CLI
  and the planned web UI.
- Chat/agent access needs no extension at all: see `VSCODE_MCP_SETUP.md`.

Phase 2 (chat participant, search tree) and Phase 3 (remote SSE connection)
are spec'd in `VSCODE_EXTENSION_SPEC.md` §16.
