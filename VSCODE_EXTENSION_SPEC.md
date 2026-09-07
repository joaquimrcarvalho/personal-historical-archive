# VS Code extension for pha — specification (for analysis)

Status: **Spec v1.1** — §17 open questions resolved · Author: AI agent · Scope: research + design, no code yet.

This document is a spec for a VS Code extension that helps users **manage a
personal-historical-archive (pha)** archive from inside VS Code. It is written
to be analysed and decided on before any implementation. It is deliberately
grounded in what pha *already* exposes (CLI, MCP server, SQLite, filesystem) so
the analysis is about wiring, not inventing new backend work.

---

## 1. Purpose and goals

The user asked for an extension that:

1. **Browses the dropbox** — a navigable view of the archive's contents.
2. **Walks the pipeline** — easy movement from *document* → *transcription* →
   *edited version* → *encoding* (the four stages pha already produces).
3. **Checks configuration** — palaeographers, editors, encoders (and models),
   including what each document/collection resolves to.
4. **Shows status** — per-document/per-collection progress, errors, pending
   review, and "config changed since last run" signals.

Non-goals for v1: re-implementing the pipeline, running models in the editor,
becoming a full document editor, or replacing the historian review surface
(the library `.md` files remain the review surface — the extension just opens
them and imports them back via `pha review`).

---

## 2. Feasibility verdict

**Strongly feasible. This is one of the most natural fits possible**, because
pha was already designed to be driven by external tools. The extension does not
need to add any new backend to pha for the core experience — it wires together
four existing, stable surfaces:

| pha surface | What it already gives the extension | Used for |
| --- | --- | --- |
| **MCP server** (`pha mcp`, stdio + SSE) | 15 `pha_*` tools: search, get document/page (all three variants + page image), list documents, collection config, collection status, scan now, extraction status, upload, schema, doctor, list palaeographers/editors/encoders | Chat/agent access, and the *remote* (machine-to-machine) data path |
| **CLI** (`pha <cmd>`) | scan, edit, encode, reindex, review, status, page, search, test, bundle/unbundle, upload, doctor, key, palaeographer/editor/encoder, prompts, set archive-dir | Actions (triggers), and cheap read fallbacks |
| **SQLite** (`archive.db`, WAL) | `documents`, `pages`, `page_edits`, `chunks`, `records`, `chunks_fts` with a documented, introspectable schema (`pha_schema`) | Fast read-only browsing/status without spawning processes |
| **Filesystem** | `dropbox/`, `library/`, `renders/`, `models/`, `palaeographers/`, `editors/`, `encoders/`, `pha.yaml` sidecars | The dropbox browser (incl. files *not yet* scanned), opening library `.md` pages, and the config file views |

The VS Code side is equally ready. The extension surface we need is all
shipped/stable, not proposed API:

- **Custom views / Tree View** — `contributes.views` + `TreeDataProvider` for
  the dropbox browser and the config tree ([Tree View guide](https://code.visualstudio.com/api/extension-guides/tree-view)).
- **Webview panels** — for a dashboard/diff view ([Webview guide](https://code.visualstudio.com/api/extension-guides/webview)).
- **Status bar / file decorations / tasks** — for live status and running
  `pha scan`/`edit` in a managed terminal/task.
- **Native MCP client** — VS Code ships built-in MCP support via
  `.vscode/mcp.json` (workspace) and user-profile `mcp.json`, exposing server
  tools to chat/agents with sandboxing and trust prompts
  ([MCP servers doc](https://code.visualstudio.com/docs/agent-customization/mcp-servers)).
- **Chat Participant API** (`vscode.chat.createChatParticipant`) and
  **Language Model Tool API** (`vscode.lm.registerTool`) for an `@pha`
  participant and tool-calling ([Chat Participant guide](https://code.visualstudio.com/api/extension-guides/ai/chat),
  [Language Model Tool guide](https://code.visualstudio.com/api/extension-guides/ai/tools)).

**The single most important architectural fact:** pha already ships a complete,
already-tested MCP server. VS Code can consume it *without any extension* by
pointing `.vscode/mcp.json` at `pha mcp`. The extension's job is therefore to
add what MCP/chat does **not** give: a *structured, clickable* browser, config
checker, and status view — plus, optionally, to write that `.vscode/mcp.json`
for the user so the chat experience works out of the box.

---

## 3. The pivotal design decision: local vs. remote

pha has two deployment modes (see `MCP_CLIENTS.md`):

- **Local** — VS Code runs on the **archive machine** (the one with the
  dropbox, models, and `archive.db`).
- **Remote** — VS Code runs on **machine A**; the archive (dropbox/models/DB)
  lives on **machine B**, reachable only via the MCP server over SSE
  (`pha mcp --transport sse --host <LAN-IP> --port 8000`).

This decision changes the data layer completely, so the spec makes it a
first-class concept with **two interchangeable backends behind one interface**:

```ts
interface ArchiveConnection {
  readonly mode: "local" | "remote";
  // reads
  listDocuments(filter): Promise<DocumentSummary[]>;
  getDocument(id): Promise<DocumentDetail>;          // pages + variants + records
  listDropboxTree(relPath): Promise<DropboxEntry[]>; // incl. unscanned files
  getCollectionStatus(collection?): Promise<CollectionStatus[]>;
  listConfig(): Promise<ConfigInventory>;            // models/pals/editors/encoders/sidecars
  // actions
  scan(path?): Promise<RunHandle>;
  edit(path?): Promise<RunHandle>;
  encode(): Promise<RunHandle>;
  reindex(): Promise<RunHandle>;
  review(docId?): Promise<RunHandle>;
}
```

- `LocalConnection` — filesystem + SQLite (read-only) + `pha` CLI subprocess.
- `RemoteConnection` — `@modelcontextprotocol/sdk` client over SSE to machine B
  (v2; see §10 for the one tool gap it has).

**Decision (§17): local-first.** Build **local first** (it is the common case
for the person who operates the archive, and it needs no new pha code), and
design the interface so `RemoteConnection` slots in later. For remote users in
the meantime, the extension's "Configure MCP" command writes the correct
`.vscode/mcp.json` and the built-in chat/agent experience carries the load.

---

## 4. The dropbox browser (goal 1)

### 4.1 Data model of the tree

Three sources must be merged, because pha tracks **three disjoint sets**:

- **Archived documents** — rows in `documents` (the DB), one per *unit* (a PDF,
  an image, or a directory-of-images). Keyed by `path` (unique), with
  `dir_path`, `status`, `page_count`, `palaeographer`, `editor`, `encoder`,
  `palaeographer_model`, `editor_model`.
- **Unscanned dropbox files** — files under `dropbox/` whose resolved path is
  **not** in `documents.path`. This is exactly what `pha status` computes as its
  "new" list.
- **On-hold inbox files** — files under `<archive_dir>/inbox/` (a *sibling* of
  `dropbox/`, configurable via `paths.inbox`). Never scanned or indexed;
  `pha status` reports them as "on hold", and `pha inbox [--move|--dry-run]`
  lists/moves them into the dropbox.

Tree shape:

```
PHA
├─ 📁 documents/
│   └─ 📄 sample_letter  (dir of images)      # status · 2 pages · pal · editor
│        ├─ page-001  [transcription | edited | encoded]
│        └─ page-002  [transcription | edited | encoded]
├─ 📁 collections/
│   └─ 📁 COLX/
│       ├─ 📄 sample_charter.pdf               # done · 2 pages · portuguese-secretary
│       └─ ⏳ new-file.pdf                     # "not yet scanned"
├─ 📥 inbox/                                   # on hold — never scanned
│   └─ 📄 parked-letter.pdf                    # "on hold · pha inbox --move"
└─ ⚙️ Configuration
    ├─ Models            (models/*.md)
    ├─ Palaeographers   (palaeographers/*.md)
    ├─ Editors          (editors/*.md)
    ├─ Encoders         (encoders/*.md + collection-local encoders/)
    └─ pha.yaml sidecars (per collection/document)
```

Conventions to honour (from `AGENTS.md` + the code):

- Collections are `collections/COLX/`; loose files live in `documents/`;
  `dir_path` is the directory **relative to the dropbox**, `(root)` when empty.
- A **directory-of-images** document is one unit (its pages are named after the
  source image stem, e.g. `502V.md`, not `page-NNN.md`) — the tree must show
  it as one document, not one node per image.
- `render/` cache and library folders are *derived*, so they should not be part
  of the "dropbox" tree (they surface inside the document drill-down instead).
- `inbox/` is a sibling of `dropbox/`, not inside it — show it as its own
  top-level "on hold" node; its files get a "Move to dropbox" action
  (`pha inbox --move`), never a scan/edit action.

### 4.2 Refresh and performance

- Refresh on demand (toolbar "Refresh") + on a `FileSystemWatcher` over
  `dropbox/`, `inbox/` and `library/` (debounced) + after any action completes.
- For a large archive, **lazy-load** children (`getChildren` per node) and read
  the DB read-only. SQLite in WAL mode tolerates concurrent readers with the
  watcher/CLI writers; the extension opens `archive.db` with
  `PRAGMA query_only=ON` (or a `file:...?mode=ro` URI) so it can never contend
  with or corrupt a running scan.
- Cap initial per-collection expansion (e.g. show the first 200 docs and a
  "…+N more" node), matching the CLI's own truncation instincts.

---

## 5. Walking the pipeline: document → transcription → edited → encoding (goal 2)

This is the heart of the extension. Every **document node** expands into its
**pages**, and every **page** exposes its *variants* as one-click open/diff
actions. The variant paths come straight from `ingest.library_page_path` /
`write_document_pages` / `write_edited_pages`:

```
library/<dir_path>/<slug>/transcription-<palaeographer>[@model]/page-NNN.md   (raw)
library/<dir_path>/<slug>/edited-<editor>[@model]/page-NNN.md                 (edited)
library/<dir_path>/<slug>/records-<encoder>.json                             (encoded)
library/<dir_path>/<slug>/concatenated-<encoder>.md                          (whole-doc encoder input)
```

### 5.1 Per-page commands (context menu + inline buttons)

| Action | Behaviour |
| --- | --- |
| **Open transcription** | Open the `transcription-<pal>/page-NNN.md` in the editor (raw, faithful reading). |
| **Open edited** | Open `edited-<editor>/page-NNN.md`. If no editor configured, show "no editor" and offer to configure one. |
| **Open encoded** | Open `records-<encoder>.json` (or the whole-doc concatenated record), pretty-printed. |
| **Split: raw ⇄ edited** | Two-column editor: transcription on the left, edited on the right (same page number). Optionally a **diff** view. |
| **Show page image** | Webview showing the cached render `renders/<sha256>/pNNN.jpg` side-by-side with the text (the exact image the model read). |
| **Copy full page text** | Equivalent of `pha page <doc> <page> [--edited]`. |
| **Review status** | Show `reviewed: true` badge when the page has `reviewed_at`; surface "human-corrected, not yet imported" when file mtime > `exported_at`. |

### 5.2 Document-level commands

- **Open whole document** — concatenated transcription/edited text (raw DB or
  `concatenated-*.md`).
- **View records** — all `records` rows for the document, grouped by `encoder`
  and `kind`, rendered as a table/JSON.
- **Config summary** — resolved palaeographer / editor / encoders + their
  `source` (pha.yaml vs legacy selection file vs default), plus the *recorded*
  ones from the last run, so "config changed since last run" is visible
  (reuse `pha_collection_config` + `pha_collection_status` semantics).

### 5.3 The review round-trip (must preserve)

The library `.md` files are the human correction surface. The extension must:

- Open the `.md` (not a virtual copy) so edits land on the real review file.
- Watch `library/**/*.md` and, using the same **mtime vs `exported_at`** rule as
  `pending_review_files`, badge a page/document as "corrections pending".
- Offer **"Import corrections"** which runs `pha review` (then reminds the user
  to run `pha edit` + `pha reindex` if a *transcription* was corrected, per
  `AGENTS.md`).

---

## 6. Checking configuration (goal 3)

### 6.1 What "configuration" means in pha (three layers)

1. **Model interfaces** — `models/<id>.md` (endpoint, api key, server model
   name, `api_style`, `max_vision_px`, `vision_jpeg_quality`, `context_tokens`,
   and non-LLM `engine:` settings).
2. **Content rules** — `palaeographers/<id>.md`, `editors/<id>.md`,
   `encoders/<id>.md` (prompt/rules body + `temperature`/`max_tokens` + encoder
   params). These carry **no model**.
3. **Pairing** — `pha.yaml` sidecars next to the documents (`palaeographer:
   {rules, model}`, `editor: {rules, model}`, `encoders: [...]`), plus the
   legacy plain `palaeographer`/`editor` selection files, plus collection-local
   `collections/COLX/encoders/*.md`.

### 6.2 Config views

A **Configuration** tree (or a dedicated webview) that is a *file manager* over
these directories — the same idea as `WEB_INTERFACE_PLAN.md`'s screens, but
native to VS Code:

- **List** each definition by id (= file stem) with its `description`, model
  ref, and engine (for OCR models).
- **Open** a definition as a normal Markdown editor (front matter = settings,
  body = prompt) — VS Code already edits `.md`/`.yaml` well; the extension
  adds IntelliSense/snippets for the front-matter keys (via a JSON schema +
  snippets, not a new editor).
- **New from sample** — duplicate `_sample.md` (or `_sample.tesseract.md` /
  `_sample.liteparse.md` / `_sample.ocr.md`) and open it for editing.
- **Validate** — parse front matter on save; surface malformed files as
  Problems (diagnostics), mirroring the loader's "skip with a warning" so the
  user sees the same error the CLI would print.
- **Resolve** — for a chosen document/collection, show the **effective**
  palaeographer/editor/encoders/prompt and their `source` (this is
  `pha_collection_config`, exposed read-only in the UI).
- **Secrets** — always show `api_key` as masked; never write a resolved key;
  encourage `${ENV}` placeholders (`pha key --set NAME` stores the secret).
- **Staleness warning** — before saving a prompt/definition that will trigger
  re-extraction/re-editing (mtime staleness), show "this will re-process N
  documents" and require confirmation. This is the same contract as the web
  UI plan.

---

## 7. Status information (goal 4)

### 7.1 Status bar (always visible)

- Left: archive summary, e.g. `PHA 12 docs · 5 processing · 2 pending review`.
- Right (clickable): current connection (`local` / `remote:<host>`), and a
  "job running" spinner with the live `pha scan`/`edit` lock state.

### 7.2 Status view / dashboard

A tree or webview backed by `db.summary` + `pha_collection_status`:

- Documents by status (`done`, `processing`, `pending`, `error`, `waiting`
  pages), pages extracted, chunks indexed vs embedded (flag "keyword-only —
  run `pha reindex`").
- Per collection: documents with **progress** (pages done/total), **stage**
  (transcribed / edited / encoded / embedded), **render phase** (rendering /
  transcribing / complete), and **config drift** (recorded vs resolved
  pal/editor/encoder).
- **New/unscanned** files list, and the **on hold (inbox)** list with a
  "Move to dropbox" action (`pha inbox --move`).
- **Pending review** list, grouped by document+pages (reuse
  `_pending_summary_lines` semantics).
- **Errors** surfaced inline (document `error`, page `error`).

### 7.3 Jobs & the single-model lock

- All mutating actions (`scan`, `edit`, `encode`, `reindex`, `review`) run as
  **managed background jobs** (a `Task`/`Pseudoterminal` or `child_process`
  with output capture), shown in a dedicated "PHA Jobs" view with live stdout.
- **Respect the lock**: `pha scan` and `pha edit` share one local-model lock,
  and only one local-model job may run at a time. The extension must (a) not
  start `edit` while `scan` runs, (b) reuse pha's own lock rather than
  implementing a second one, and (c) surface "another scan/edit job is
  running" from pha verbatim instead of hiding it.
- `pha doctor --json` is surfaced as a "Engines" health panel (tesseract /
  liteparse availability), with a "Run pha doctor" action.

---

## 8. Actions surface (commands)

Mirror the CLI, with sensible defaults and no shell knowledge required:

| Command | Underlying | Notes |
| --- | --- | --- |
| `pha.scan` / `pha.scanCollection` | `pha scan [--path …]` | optional `--palaeographer`, `--prompt` |
| `pha.edit` / `pha.editCollection` | `pha edit [--path …]` | respects lock |
| `pha.encode` | `pha encode` | |
| `pha.reindex` | `pha reindex` | |
| `pha.review` | `pha review [--doc N]` | after corrections |
| `pha.test` | `pha test <target> [--pages N] [--random]` | show `report.md` in a webview; `--show` re-prints, `--list` lists, `--clean [--dry-run]` deletes saved runs |
| `pha.inbox` | `pha inbox [--move \| --dry-run]` | list on-hold documents; move them into the dropbox (then `pha scan`) |
| `pha.bundle` / `pha.unbundle` | `pha bundle/unbundle` | |
| `pha.upload` | `pha upload document|collection <path>` | native file picker → dropbox |
| `pha.doctor` | `pha doctor --json` | engines health |
| `pha.setArchiveDir` | `pha set archive-dir <path>` | first-run onboarding |
| `pha.configureMcp` | writes `.vscode/mcp.json` (see §9) | zero-config chat wiring |
| `pha.search` | `pha search "…"` / DB query | opens results in a results tree with "open full page" links |

---

## 9. Chat / agent integration (optional, high value)

Two complementary options, both built on what already exists:

1. **Zero-code (ship it first).** A command `PHA: Configure MCP connection`
   writes a workspace `.vscode/mcp.json` (or the user-profile `mcp.json`) with
   the correct `pha mcp` stdio entry (or an SSE `http` entry for remote), using
   the same JSON shape documented in `MCP_CLIENTS.md`. VS Code's built-in MCP
   client then exposes all 15 `pha_*` tools to Copilot/agents with sandboxing
   and per-tool approval. This is the fastest path to "chat with my archive".

2. **First-class (v2).** Register an `@pha` chat participant
   (`vscode.chat.createChatParticipant`) and/or language-model tools
   (`vscode.lm.registerTool`) so the extension can answer archive questions and
   trigger scans from chat, with richer responses (buttons that open a page,
   `stream.filetree` pointing at the dropbox, command links). This reuses the
   local/remote `ArchiveConnection` rather than re-implementing pha's logic.

**Why not re-implement pha's tools inside the extension?** The MCP server is
already the tested, correct implementation (schema quirks like
`pages.document_id` vs `doc_id`, the `(root)` grouping, lock semantics, secret
expansion). Duplicating that in TypeScript is the highest-risk part of the
whole project; prefer to call the MCP server or the CLI and keep the extension
thin.

---

## 10. Gaps to close in pha (small, optional)

These are the only places the extension would benefit from new pha code. Items
1 and 2 are **approved** (see §17); none blocks the local MVP:

1. **`pha_list_dropbox` MCP tool** *(approved — §17)* — the MCP surface has no
   way to browse the *unscanned* dropbox tree (only `pha_get_archive`'s
   diagnostic 10-PDF list). A remote `RemoteConnection` needs this to build the
   browser. (Local mode reads the filesystem directly, so it is unaffected.)
2. **JSON output on a few CLI read commands** *(approved — §17)* — `pha status`,
   `pha palaeographer`, `pha editor`, `pha encoder`, `pha prompts` print human
   text. Adding `--json` would let the extension shell out cleanly instead of
   reading the DB directly. (Nice-to-have; the DB read is a fine substitute.)
3. **A stable, versioned read-only SQLite accessor** — the schema is already
   documented via `pha_schema`; freezing it (or adding a tiny
   `pha db query`/`pha api` JSON surface) would decouple the extension from
   schema drift. (Nice-to-have.)

Each is additive and backward-compatible.

---

## 11. Security and correctness constraints

These are hard requirements derived from `AGENTS.md` and the code:

- **Read-only DB.** Open `archive.db` with `mode=ro`/`query_only`. Never write
  through the extension's SQLite handle; writes go through `pha` so WAL, locks,
  and migrations are respected.
- **Single-model lock.** Never run `scan` and `edit` concurrently; never start
  a second local-model job while one holds the lock. Delegate to pha's lock.
- **Secrets.** Mask `api_key` in every UI; never log it; never write a resolved
  key into a generated file; keep `${ENV}` placeholders; use `pha key`.
- **Staleness semantics.** Editing a prompt/definition re-processes documents
  by mtime. The UI must warn before saving and never silently edit files.
- **Review round-trip.** The library `.md` files are the source of truth for
  corrections; the extension edits the real files and imports via `pha review`;
  it must not overwrite `reviewed` pages/edits.
- **Workspace trust.** Disable file-writing features in untrusted workspaces
  (VS Code `workspace.isTrusted`), consistent with the dropbox-file sandbox
  rule used by `pha_upload`.
- **Remote has no auth.** The SSE server is unauthenticated (private LAN/VPN/
  SSH tunnel only). The extension must warn when configuring a remote
  connection and never bind/`0.0.0.0` on its own.
- **`pha` discovery.** Resolve the executable from a setting
  (`pha.executablePath`), defaulting to `pha`, then `.venv/bin/pha`, then
  `uv tool` locations — and report "pha not found" with the exact fix, never
  guess silently. Honour `PHA_HOME` and `PHA_ARCHIVE_DIR`.

---

## 12. Settings

```jsonc
{
  "pha.executablePath": "",          // "pha" default; else full path to the venv entry
  "pha.archiveDir": "",              // optional; mirrors PHA_ARCHIVE_DIR
  "pha.connection.mode": "auto",     // "auto" | "local" | "remote"
  "pha.connection.remoteUrl": "http://127.0.0.1:8000/sse",  // SSE endpoint for remote
  "pha.refresh.intervalSeconds": 30, // dashboard/status polling
  "pha.jobs.showOutputOnRun": true,
  "pha.secrets.mask": true
}
```

---

## 13. Technology and packaging

- **Language:** TypeScript; extension host is Node (VS Code supplies it — no
  constraint from pha's "no Node on the archive machine" rule, which applies to
  the *web UI* plan, not to the user's editor).
- **Key dependencies:** `@modelcontextprotocol/sdk` (remote mode + chat tools),
  `better-sqlite3` or `node:sqlite` (read-only DB), no other heavy deps.
- **Engines:** `node >= 20` (VS Code's current baseline); target `vscode@^1.96`
  or later (higher if we use `vscode.lm` tool registration, which is the
  newest surface).
- **Distribution:** private `.vsix` (no Marketplace publishing, per §17), with
  the extension source living **in this repository** (e.g. under
  `vscode-extension/`); activation on `onView:pha.explorer` +
  `workspaceContains:archive.db`/`.vscode/mcp.json`.
- **Testing:** unit tests for the DB/SQL + path resolution (the
  `library_page_path` naming rules, `(root)` grouping, source-stem page names);
  integration tests that run a real archive through scan→edit→encode and assert
  the tree/drill-down/status; manual smoke tests against the sample archive.

---

## 14. Relationship to `WEB_INTERFACE_PLAN.md`

There is already a plan for a browser UI (`pha web`). The extension and the web
UI overlap on three screens (dashboard, palaeographers/editors file manager,
prompts). Options:

- **Complementary (recommended):** the extension targets **VS Code users** and
  the archive operator; `pha web` targets browser-first users. Both reuse the
  same pha backend and the same correctness contract (lock, staleness, secrets,
  review round-trip). The extension can later embed `pha web` in a webview as a
  "full dashboard" escape hatch, avoiding a re-implementation.
- **Supersede:** if VS Code is the primary surface, the extension's dashboard
  can replace the web UI's dashboard, and `pha web` can stay a thin API server.

**Decision (§17): stay complementary.** The extension and `pha web` coexist:
the extension targets VS Code users, `pha web` targets browser-first users, and
both share the same backend and correctness contract.

---

## 15. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Schema drift breaks the DB reader | Browsing/status break | Read-only + `pha_schema` introspection; prefer MCP/CLI for writes; pin schema version |
| Concurrent job start wedges LM Studio (swap/disk fill) | Machine hang | Reuse pha's lock; UI-level job gating; surface "already running" verbatim |
| Duplicating pha logic in TypeScript drifts from Python | Bugs, wrong results | Keep the extension a thin client over MCP/CLI; never re-implement extraction |
| Remote mode can't see unscanned files | Incomplete browser | Add `pha_list_dropbox` MCP tool (small) or defer remote browser to v2 |
| Secret leakage in UI/logs | Security | Mask everywhere; never persist resolved keys; `${ENV}` + `pha key` |
| Large archive makes the tree slow | UX | Lazy children, DB paging, debounced watchers, cap expansions |
| `pha` not on PATH | "Nothing works" | Explicit setting + discovery + a clear first-run onboarding to `pha set archive-dir` |

---

## 16. Phased delivery plan

**Phase 0 — Zero-code win (hours, no extension code).**
Write `VSCODE_MCP_SETUP.md` (or a small snippet) showing users how to point
`.vscode/mcp.json` at `pha mcp`. This immediately delivers the entire
chat/agent experience via VS Code's native MCP client.

**Phase 1 — MVP local extension.**
`LocalConnection` (FS + read-only SQLite + CLI). Dropbox browser tree, document
→ page → variant drill-down (open raw/edited/encoded, split view, page image),
status bar + status view, config tree (list/open/validate/new-from-sample),
and the action commands (scan/edit/encode/reindex/review/test) as managed jobs
with lock gating. Ship as `.vsix`.

**Phase 2 — Chat + convenience.**
`PHA: Configure MCP connection` command; `@pha` chat participant and/or
`vscode.lm` tools on top of the existing MCP server; search results tree;
diff view raw⇄edited; review-pending badges.

**Phase 3 — Remote.**
`RemoteConnection` via `@modelcontextprotocol/sdk` over SSE; the approved
`pha_list_dropbox` tool and `--json` read commands (see §10, §17) can land in
pha any time before this phase so the remote browser is complete.

---

## 17. Decisions (confirmed by the user)

1. **Local-first.** The extension is built for the *archive operator on the
   archive machine* (`LocalConnection` first); remote (`RemoteConnection`/SSE)
   stays Phase 3.
2. **Chat/agent = Phase 2.** Ship the zero-code `.vscode/mcp.json` wiring
   regardless; treat the `@pha` chat participant / language-model tools as
   Phase 2.
3. **Stay complementary to `pha web`.** The extension does not embed or replace
   `pha web`; the two coexist and share the backend/correctness contract.
4. **Add the additional tool.** Approve the small, backward-compatible
   `pha_list_dropbox` MCP tool and `--json` on the read commands so the remote
   browser and CLI shell-outs are complete.
5. **Private, in-repo.** Distribute as a private `.vsix` (no Marketplace
   publishing) and keep the extension source in this repository.

---

## Appendix A — pha surface reference (as of this analysis)

**MCP tools (15 shipped, 1 approved addition):** `pha_search`,
`pha_get_document`, `pha_get_page`, `pha_list_documents`, `pha_upload`,
`pha_get_archive`, `pha_palaeographers`, `pha_editors`, `pha_encoders`,
`pha_collection_config`, `pha_collection_status`, `pha_scan_now`,
`pha_extraction_status`, `pha_schema`, `pha_doctor` — plus
`pha_list_dropbox` (approved — §17, not yet implemented).

**CLI read commands gaining `--json` (approved — §17):** `pha status`,
`pha palaeographer`, `pha editor`, `pha encoder`, `pha prompts`.

**SQLite (key columns only — see `pha_schema()` for the full picture):**

- `documents` — `id, filename, path (UNIQUE), sha256, size_bytes, mtime, kind,
  page_count, status, prompt_source, dir_path, palaeographer, editor, encoder,
  palaeographer_model, editor_model, error, created_at, updated_at`.
- `pages` — `id, document_id, page_no, raw_text, status, error, source_name,
  reviewed_at, exported_at`. (Links via `document_id`, **not** `doc_id`; has no
  `path`/`sha256`.)
- `page_edits` — `page_id, editor, text, raw_sha, status, error, updated_at,
  reviewed_at, exported_at` (key `(page_id, editor)`).
- `chunks` — `document_id, page_id, chunk_no, text, embedding, variant
  (raw|edited)`.
- `records` — `document_id, encoder, kind, data, source, created_at`.

**Library layout:** `library/<dir_path>/<slug>/transcription-<pal>[@model]/page-NNN.md`,
`edited-<editor>[@model]/page-NNN.md`, `records-<encoder>.json`,
`concatenated-<encoder>.md`; directory-of-images documents name pages after the
source stem (`502V.md`).

**Config layout:** `models/<id>.md` (interface), `palaeographers/`,
`editors/`, `encoders/<id>.md` (content rules), `dropbox/…/pha.yaml` (pairs
`rules`+`model`), `collections/COLX/encoders/*.md` (collection-local), legacy
plain `palaeographer`/`editor` selection files. `inbox/` (a sibling of
`dropbox/`, configurable via `paths.inbox`) holds on-hold documents that are
never scanned; `pha inbox [--move|--dry-run]` manages them.
