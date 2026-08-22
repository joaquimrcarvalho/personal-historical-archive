# AGENTS.md — conventions for agents working in this repo

## Commits

- **Agent-made commits MUST end their message with a model trailer**, naming
  the agent's model, e.g.:
  `Model: deepseek-v4-flash (DeepSeek Harness)`
  (use the current session's model name; append it as the last line of the
  commit message).
- Commits made by the human do NOT carry the trailer — it is manual only
  (there is no hook).
- Verify with `git log -1 --format='%B'`.

## Project essentials

- **personal-historical-archive (pha)** — local archive of historical
  documents; CLI `pha`, Python package `personal_historical_archive`
  (`src/personal_historical_archive/`). The working branch is `main`.
- **archive_dir is the self-contained data root.** Everything the archive
  owns lives under it: `dropbox/` (documents), `palaeographers/`, `editors/`,
  `encoders/` (model/prompt definitions), `library/`, `renders/`, `archive.db`
  (generated). The project dir holds only code, `prompts/` and the
  `_sample.md` templates. Precedence: `PHA_ARCHIVE_DIR` env > `PHA_ARCHIVE_DIR`
  in `.env` (`pha set archive-dir`) > `paths.archive_dir` > default `.`.
  A fresh archive is seeded with `default.md` for palaeographer/editor/encoder
  (all qwen3-vl-8b) so it works with zero config.
- **Palaeographers (vision models), editors (text models) and encoders (text
  models) are one file each** in the top-level `palaeographers/`, `editors/`
  and `encoders/` directories: YAML front matter = model config (endpoint,
  model, api key, temperature, `api_style: openai|anthropic`, and for
  encoders `context_tokens` — the model's input window that drives the
  single-pass/chunked decision), body = the prompt. To add one: duplicate
  `_sample.md`, rename, edit, save. The file stem is the id.
- **Staleness by mtime**: editing a palaeographer / editor / encoder / prompt
  file triggers re-extraction / re-editing / re-encoding of affected documents
  on the next scan/run. A document also re-extracts when the resolved
  palaeographer differs from the one recorded on it.
- **Prompt layering**: the palaeographer base prompt is the format authority
  and goes first; document/collection prompts add aspects only
  (e.g. modernizing spelling) and cannot change the output structure. The
  encoder stage composes three layers in order: encoder base prompt
  (model config + generic framing in the collection's `encoders/<name>.md`)
  → `encoders/<name>.prompt.md` (detection rules) →
  `encoders/<name>.langextract.md` (schema + few-shot examples).
- **Palaeographer prompts are TRANSCRIPTION-ONLY — no text-modification
  rules.** The palaeographer reproduces the page faithfully (original
  spelling, `[illegible]`/`[?]`); it must NOT expand abbreviations, modernize,
  translate or normalise names. All such transformation belongs in the
  editor prompt (a separate text-model pass over the transcription). Keeping
  modification rules out of the palaeographer avoids double-expansion and
  keeps the raw transcription a faithful record of the source.
- **Encoders live next to their sources**: dropbox/collections/COLX/encoders/
  has one file per structure type (table.md, biographies.md, letters.md);
  `pages:` in the front matter scopes an encoder to a page range and
  `pha encode` runs a document's encoders in page order (whole-doc last).
  The top-level `encoders/` dir holds only the sample template.
- **The null editor**: editor id `null`/`passthrough` copies the
  transcription verbatim as the "edited" text (no model call), so documents
  without a real editor still get `edited-null/` output, both-variant
  indexing and explicit provenance. With no editor at all, the encoder and
  indexer fall back to the raw transcription per page.
- **Encoders output LangExtract-flat JSON** (`{"<class>": "<exact text>",
  "<class>_attributes": {...}}`, one item per class, kinds stored per record);
  `pha encoder --new` is the non-technical wizard that creates encoder files
  (chat variant: `prompts/encoder-helper.md`). Never write a resolved API key
  into a generated encoder file — keep `${ENV}` placeholders.
- **Only ONE local-model job at a time.** LM Studio holds a single model in
  memory — loading two local models (e.g. qwen vision + amalia editor)
  simultaneously causes swap/page-out that fills the disk and wedges the
  server. `pha scan` and `pha edit` now share the SAME lock, so they cannot
  run concurrently; if one runs while the other holds the lock it reports
  "another scan/edit job is running" and exits. Remote models (MiniMax)
  don't compete with LM Studio. A palaeographer and editor may use the SAME
  local model (e.g. qwen for both on Pfister) to keep one slot loaded.
- Pipeline: dropbox → palaeographer per-page transcription → optional editor
  transform → optional encoder (concatenated whole-document text, page-grounded
  records) → SQLite (FTS5 + embeddings, indexing both raw and edited
  variants) → hybrid search + FastMCP (`pha_*` tools).
- **Library folders are readable + version-safe**: each document version lives
  in `library/<dir>/<stem>_<YYYY-MM-DD>/` (creation date; a content change
  creates a new row/date, so old folders stay). Pages of a directory-of-images
  document are named after the source scan (`502V.md`); PDF pages use
  `page-NNN.md`.
- **Review round-trip (historians correct the files)**: the library `.md`
  files are the human review surface. A historian edits a page body; `pha
  status` reports un-imported corrections (timestamp-based: file mtime newer
  than the page's `exported_at`); `pha review [--doc N]` imports them into the
  DB and stamps the page `reviewed` (`reviewed_at`), so later `pha scan` /
  `pha edit` (even `--reprocess`) never overwrite a reviewed page. Then
  `pha reindex`. Reviewed pages show `reviewed: true` in front matter.
- Full usage: README.md; planned web UI: WEB_INTERFACE_PLAN.md.

## Usage — how agents operate the archive (not just develop it)

These conventions cover everyday *use* of pha, the same way the sections above
cover code. The archive is meant to be driven by an AI agent: browse, set
how a collection is processed (palaeographer / editor / encoder), add
documents, scan, edit, encode, and search.

### How to check how a collection/document is configured

Before processing or changing anything, find out what is already set. A
collection's processing = the resolved **palaeographer**, **editor**, and
**encoders** + the effective prompt.

- **Locally** (on the archive machine, via CLI):
  - `pha palaeographer <collection-or-doc>` → resolved vision model.
  - `pha editor <collection-or-doc>` → resolved editor, **or "none"** if no
    editor is configured (a collection with no `editor` selection file and no
    editor name fallback is processed without an editor, or with the null
    editor).
  - `pha prompts <collection-or-doc>` → effective prompt + its source file.
  - `pha encoder` → lists collection-local encoders; `pha encoder <file>`
    shows a document's resolved encoders.
- **Remotely / connected via MCP** (agent on another machine):
  - `pha_collection_config("collections/COLX")` returns **one object** with
    the resolved `palaeographer`, `editor` (or `{id: None, ...}` when none is
    configured), `prompt`, and their `source` files.
  - `pha_palaeographers()`, `pha_editors()`, `pha_encoders(relpath)` list the
    available definitions.

Interpretation: an editor value of `None`/empty means the collection currently
has **no editor** — ask the historian before inventing one, and if an editor
is wanted, set it (below). The `source` field tells you where the selection
came from (a dropbox `editor` file, a config default, or nowhere).

### Setting the palaeographer / editor / encoders for a collection

- A collection selects its models with **plain-text selection files** next to
  the documents: a file named `palaeographer` (or `editor`) containing the id
  (nearest-wins chain). Encoders are files in `collections/COLX/encoders/`.
- **Locally**: write the file directly, e.g.
  `echo qwen-generic > dropbox/collections/COLX/editor` (or create the
  `encoders/<name>.md` + companions). Then re-process so the change takes
  effect (`pha scan` re-extracts when the resolved palaeographer changes;
  `pha edit` re-edits; `pha encode` runs encoders).
- **Remotely via MCP**: push the selection/encoder file content with
  `pha_upload` (base64) under the right dropbox path (e.g.
  `collections/COLX/editor`), then `pha_scan_now()` to ingest/re-process.
- To add a **new definition** (a palaeographer/editor/encoder `*.md`): the
  palaeographer/editor definitions live at the project root beside the
  dropbox — a remote agent cannot write those via `pha_upload` (dropbox-
  scoped); ask the operator on the archive machine to copy a `*_sample.md`
  and edit it, or stage it in the dropbox first. Encoder definitions live
  inside the dropbox and can be uploaded directly.

### Operating discipline (avoid breaking the machine)

- **One local-model job at a time.** `pha scan` and `pha edit` share a lock;
  never start `pha_edit`/re-edit while a `pha_scan_now` is running on the same
  machine (two local models → swap → disk fill → hang). Check
  `pha_extraction_status` / the lock before starting a pass.
- **Quit LM Studio when not ingesting** — its model page-out is what eats disk
  space. Do not leave a vision + editor model loaded at the same time.
- **After changing config**: re-run the matching pass (`pha scan`, `pha edit`,
  `pha encode`) so staleness-by-mtime picks up the change, then confirm with
  `pha status` / `pha_extraction_status`.

### Remote / machine-to-machine

- The MCP server runs on the machine that owns the dropbox and models. A
  client on another machine needs only an MCP connection — no local models or
  dropbox. Start on the archive machine with
  `pha mcp --transport sse --host <LAN-IP> --port 8000` (no auth — use a
  private LAN/VPN/SSH tunnel). See MCP_CLIENTS.md for the full wiring.
- Upload documents from another machine via `pha_upload(kind, name, content_b64)`
  (files travel as base64; single files per call), then `pha_scan_now()`.
