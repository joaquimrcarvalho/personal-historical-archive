# ARCHITECTURE.md — how pha is put together

The design-level reference for agents working in this repo: what lives where,
how the three config layers compose, what the runtime invariants are, and how
the SQLite/library layout is shaped.

This file is **repo-only** (not shipped in the wheel, not seeded into an
archive). `AGENTS.md` is the always-loaded contract and points here; read the
section you need rather than the whole file.

For the *one-model-per-server* lock rules, the destructive-command rules and
the review round-trip — the things an agent must know before acting — see
`AGENTS.md`, which keeps them inline. For task procedures, see `OPERATING.md`.

---

## 1. What pha is, and where its data lives

- **personal-historical-archive (pha)** — local archive of historical
  documents; CLI `pha`, Python package `personal_historical_archive`
  (`src/personal_historical_archive/`). The working branch is `main`.
- Pipeline: dropbox → palaeographer per-page transcription → optional editor
  transform → optional encoder (concatenated whole-document text, page-grounded
  records) → SQLite (FTS5 + embeddings, indexing both raw and edited
  variants) → hybrid search + FastMCP (`pha_*` tools).
- Beside that pipeline sits the **memory**: `notes/` (the researcher's own
  conclusions) is indexed into its own `notes` + `notes_fts` tables by
  `notes.py`, and `pha search` fuses pages, records and notes with RRF keyed on
  `(kind, id)`. Notes are never `documents` — search is the only surface that
  sees them, and a note hit is marked `kind: "note"` so a conclusion is never
  mistaken for evidence.

### archive_dir is the self-contained data root

Everything the archive owns lives under it:

| Path | Contents |
|---|---|
| `dropbox/` | the documents |
| `models/` | model-interface definitions (endpoint, api key, server model name, `api_style`, vision settings, `context_tokens`) |
| `palaeographers/`, `editors/`, `encoders/` | content rules: prompts only, no model |
| `notes/` | Obsidian-compatible research notes generated from archive queries |
| `skills/` | the pha-specific agent skills, seeded by pha and never overwritten |
| `library/` | the served, versioned page files (human review surface) |
| `renders/` | rendered page images (a derived cache) |
| `archive.db` | generated index |
| `pha-location.md`, `.pha/` | machine-local: WHERE the `pha` tool is on THIS machine — written on every run, gitignored, never archive content (see `MULTI_COMPUTER.md` §E) |

The project dir holds only code, `prompts/`, `schema/` and the `_sample.md`
templates.

### archive_dir resolution precedence

1. `PHA_ARCHIVE_DIR` in the environment
2. a legacy `PHA_ARCHIVE_DIR` line in a gitignored `.env`
3. `paths.archive_dir` in `config.yaml` (written by `pha set archive-dir`)
4. `paths.archive_dir` in the **per-user settings** (`~/.config/pha/config.yaml`,
   `%APPDATA%\pha\config.yaml` on Windows — `PHA_CONFIG_DIR` overrides the
   location; written by `pha set archive-dir --user`/`--global`, or by plain
   `pha set archive-dir` when no project `config.yaml` exists)
5. the default `.` (the project root)

Why the legacy `.env` line sits ABOVE `config.yaml`: the shipped `config.yaml`
carries `archive_dir: .` as a default, so treating that as authoritative would
silently move every pre-existing `.env` user's archive.

Why the per-user pointer sits BELOW a real project `config.yaml`: a checkout
config is more specific. The per-user pointer exists for the case with no
project at all — an installed pha launched by an MCP/agent wrapper from an
arbitrary cwd (often `/`).

Scope inference: plain `pha set archive-dir` updates a real project
`config.yaml` when one exists, and otherwise writes the per-user settings;
`--project` forces the project file.

The per-user file also keeps `paths.archives`, a list of alternatives managed
by `pha set archive-dir` (register), `pha list archive-dir`, `pha use
archive-dir` (switch active), and `pha rm archive-dir` (unregister, never delete
files).

`pha set archive-dir` also removes the legacy `.env` line, which is what makes
it effective.

`pha info` names the source that won (`archive_source`, with the
machine-readable `archive_source_kind` — `env` | `dotenv` | `config` | `user` |
`default` — and a `configured` boolean in `--json`). `configured: false` means
the project-root default, so a caller must not treat that directory as a usable
archive. `pha info` answers instead of prompting and creates nothing, so it is
the safe discovery probe.

An EMPTY `PHA_ARCHIVE_DIR` — in the environment or a `.env` line — counts as
unset and warns: it is an accident, never a pointer.

A fresh archive is seeded with `default.md` for
model/palaeographer/editor/encoder (all qwen3-vl-8b) so it works with zero
config.

---

## 2. The three config layers

1. **`models/<id>.md`** — pure model interface: endpoint, api key, server model
   name, `api_style`, `max_vision_px`, `vision_jpeg_quality`, `context_tokens`.
2. **`palaeographers/`, `editors/`, `encoders/<id>.md`** — CONTENT ONLY:
   prompt/rules + `temperature`/`max_tokens` + `thinking` + encoder params.
   They carry NO model. A `thinking: on|off` on a rules file is a
   **stage-level override that wins over the paired model's** value (the model
   file states a capability, the rules file states what this pass needs; omit to
   inherit).
3. **`pha.yaml` sidecars** that pair each stage's `rules` with its `model`
   (BOTH required per stage), plus per-collection `render` settings.

To add one: duplicate `_sample.md`, rename, edit, save. The file stem is the id.
Legacy files that still inline their interface keep working (treated as an
inline model); run `pha migrate-config` to split them.

### Builtin `_sample*.md` catalogue

Seeded into the project AND into a fresh `pha init-archive`; never loaded — the
`_` prefix means "copy me to a real id":

- the how-to-create `_sample.md` per stage
- local engines: `_sample.tesseract.md`, `_sample.liteparse.md`,
  `_sample.liteparse.fra.md`, `_sample.liteparse.spa.md`,
  `_sample.liteparse.embedded.md`
- local LM Studio models: `_sample.local-qwen3-vl.md`, `_sample.local-gemma4.md`
- the **remote** provider `_sample.deepseek.md` (shows the `${PHA_ARCHIVIST}`
  api_key wiring, vision variant commented — a text and a vision id differ only
  in `model`/`api_style`/`max_vision_px`)
- printed-book rules: `_sample.printed-books.md`,
  `_sample.printed-critical-edition.md`
- `_sample.generic.md` (general-purpose editor)

`config.builtin_samples()` is the single source of truth; a test asserts the
committed files match it.

Samples are seeded into the PROJECT by `ensure_dirs` and into a NEW archive by
`pha init-archive`; an already-created archive does **not** receive a newly
added sample on a later run, so copy one in by hand if it is wanted there.

### Stage filters

Stage filters live in the **ARCHIVE** (`<archive>/filters/<id>/`), not the
project: `filter.py` (`run(value, ctx)`) plus an optional `filter.md` manifest,
referenced from a stage's `pre:`/`post:` in `pha.yaml` (`palaeographer.post`,
`editor.pre`, `editor.post`, `encoder.pre`, `encoder.post`). `pha filters` lists
them; `pha filter <id>` runs one over text.

Python filters run **in-process** (archive-owner code, same trust as a prompt
file — there is NO sandbox); a manifest `command:` runs another executable with
the same envelope. A filter failure fails that unit and stores nothing.

Editing a filter re-runs its stage: the applied chain (name + params + content
hash) is stored on each page/edit/records run and compared to what is configured
now, so an edited script, changed params or a filter added/removed
re-extracts/re-edits/re-encodes without `--reprocess` — the same rule as a
changed rules/model file. It is visible in the library page front matter
(`filters: <name>:<sha>:<params>`) and the records file; a hand-corrected page
has it cleared. An artifact filter (`returns: none`) is stamped under
`library/<slug>/.filter-stamps/` and re-runs only when its own files, its
declared `inputs:` or the EDITED pages change.

Adopt one by editing `pha.yaml`, not by moving files. Reference filters ship in
the repo's `filters/` (copy into the archive to adopt). Full design:
`FILTERS_PLAN.md`.

---

## 3. Runtime invariants

### Staleness by mtime

Editing a palaeographer / editor / encoder / prompt file triggers
re-extraction / re-editing / re-encoding of affected documents on the next
scan/run.

A document also re-extracts when the resolved palaeographer differs from the one
recorded on it.

A document RE-EDITS when the editor's rules file (`editors/<id>.md`) OR its model
interface file (`models/<id>.md`) changed, or when the paired editor MODEL id
differs from the one recorded — re-run `pha edit` (all documents) or
`pha edit --path collections/COLX` (just one collection/document) to pick the
change up.

### Prompt layering

The palaeographer base prompt is the format authority and goes first;
document/collection prompts add aspects only (e.g. modernizing spelling) and
cannot change the output structure.

The encoder stage composes three layers in order: encoder base prompt (model
config + generic framing in the collection's `encoders/<name>.md`) →
`encoders/<name>.prompt.md` (detection rules) →
`encoders/<name>.langextract.md` (schema + few-shot examples).

### Palaeographer prompts are TRANSCRIPTION-ONLY

No text-modification rules. The palaeographer reproduces the page faithfully
(original spelling, `[illegible]`/`[?]`); it must NOT expand abbreviations,
modernize, translate or normalise names. All such transformation belongs in the
editor prompt (a separate text-model pass over the transcription). Keeping
modification rules out of the palaeographer avoids double-expansion and keeps
the raw transcription a faithful record of the source.

### Palaeographer Notes are READING NOTES only

Language, Script, difficult words. The palaeographer never outputs named-entity
lists or content summaries — entities/notes come from the editor, structured
records from the encoder.

### Encoders live next to their sources

`dropbox/collections/COLX/encoders/` has one file per structure type
(`table.md`, `biographies.md`, `letters.md`); `pages:` in the front matter scopes
an encoder to a page range and `pha encode` runs a document's encoders in page
order (whole-doc last).

`pha encode --path P` (a document/collection) or `--doc ID` targets ONE
document; `--dry-run` lists the plan without calls or writes. This is what
`pha handoff work` uses per document, so a hand-over no longer has to encode the
whole archive.

The top-level `encoders/` dir holds only the sample template.

### The null editor

Editor id `null`/`passthrough` copies the transcription verbatim as the "edited"
text (no model call), so documents without a real editor still get
`edited-null/` output, both-variant indexing and explicit provenance. With no
editor at all, the encoder and indexer fall back to the raw transcription per
page.

### Encoder output format

Encoders output LangExtract-flat JSON (`{"<class>": "<exact text>",
"<class>_attributes": {...}}`, one item per class, kinds stored per record);
`pha encoder --new` is the non-technical wizard that creates encoder files (chat
variant: `prompts/encoder-helper.md`). Never write a resolved API key into a
generated encoder file — keep `${ENV}` placeholders.

---

## 4. Model-server locking

**One model per model-server at a time.** A server that loads models just in
time keeps a single one resident — loading two (e.g. qwen vision + amalia
editor) causes swap/page-out that fills the disk and wedges the server.

So `pha scan`, `pha edit`, `pha reindex`, `pha test`, `pha unbundle` and
`pha handoff fetch` each take a lock on **every model-server they will talk to**
and refuse if one is busy, naming the server and the holding job. Two jobs may
run concurrently **iff their servers are disjoint**, so a job on a remote model
does not block a local one.

Keys are declared per model file: `models/<id>.md` may carry
`server: mac-studio`; an **unlabelled** model file takes the wildcard, which
serialises with everything (the old global rule), and the embedding model uses
`embeddings.server:` or its endpoint.

Declare real capacity once per machine in `config.yaml` when the models are
already loaded (auto-evict off):

```yaml
servers:
  mac-studio: {slots: 2}
```

`pha doctor` prints the resulting keys, their capacity and the lock dir.

A busy server makes a job **refuse**, not queue — add `--wait[=SECONDS]` (or
`PHA_LOCK_WAIT`) on `scan`/`edit`/`reindex`/`handoff fetch` to queue up to that
ceiling: it names the holder and its age, `--wait` bare means 300 s, `--no-wait`
overrides the env, and there is deliberately no unbounded form (waiting for the
embedding server can mean waiting for a scan that runs for hours).

Locks live in a **user-global** directory (`~/Library/Caches/pha/locks` on
macOS), so two archives sharing one server serialise too.

`pha search` never takes the lock: while a job uses the embedding server it
answers with keyword results and a `note` instead of loading the embed model
(`--force` embeds anyway).

Read-only commands (`status`, `search`, `page`, `cite`, `config`, `pending`, and
the FastMCP query tools) open the DB read-only — no schema work on connect — so a
running job cannot refuse them. **Consequence: a query command must never
reference a column a newer pha adds**, because no migration ran — read such a
column through `db.row_get(row, "<col>")` and guard a whole-table check with
`db.has_column(conn, "<table>", "<col>")` (0.34.1: `pha status` failed with "no
such column: pe.pinned_at" on every archive that had not been written to since
the per-page columns landed).

**`pha reindex` belongs in that set because re-embedding loads the embed
model** — running it alongside a scan/edit is what times out `embed()` (see the
vector-loss incident in `enhancements/pha-embed-loss-bug-report.md`).

### Local OCR/parse engines run WITHOUT LM Studio

`engine: tesseract` / `engine: liteparse` are local executables, not LLMs, so an
OCR scan loads no model (it still takes the pha scan lock).

The engine + its settings live in the MODEL file (`models/tesseract.md`:
`tesseract_lang`, `tesseract_psm`; `models/liteparse.md`: `liteparse_lang`,
`liteparse_dpi`, `liteparse_ocr` fresh|embedded|prefer-embedded,
`liteparse_format` text|markdown|json); a content-only rules file names the pass
and is paired in `pha.yaml`, e.g. `palaeographer: {rules: ocr, model: tesseract}`
(one rules file serves both engines — OCR ignores the prompt). Start from the
samples `models/_sample.tesseract.md`, `models/_sample.liteparse.md`,
`palaeographers/_sample.ocr.md`.

**`liteparse_ocr: prefer-embedded`** reuses a PDF page's own text layer only when
it passes a quality gate (enough text, mostly letters, word-like tokens —
`liteparse_embedded_min_chars`, `liteparse_embedded_min_quality`; Latin-script
word test only, so CJK/Greek/Cyrillic pass), else OCRs the raster; conservative
by design, sample `models/_sample.liteparse.embedded.md`.

**Install on the ARCHIVE machine.** Probe FIRST with `pha doctor` — it checks the
binaries pha spawns (and works even before an archive is set). Options:
`--engine liteparse` treats an engine as required, `--json` prints a
machine-readable report, and it exits non-zero when a required engine is broken.
Remotely, call the MCP tool `pha_doctor()` — the same check, run on the archive
machine.

HOW pha finds engines (why a tool can work in your Terminal yet "not be on PATH"
for pha): pha is often launched from a GUI / agent / cron context whose PATH is
minimal, unlike your interactive shell. So pha resolves each engine binary as:

1. its own PATH;
2. the dirs in the `PHA_ENGINE_PATH` env var;
3. the PATH your login shell would provide (queried once via `$SHELL -lic`,
   cached; set `PHA_NO_LOGIN_PATH=1` to disable);
4. the bin dir of the interpreter pha runs from.

In practice any install your Terminal can run — brew, pyenv, nvm, pipx,
`uv tool`, npm -g, `pip` into pha's own venv — is found automatically; only
genuinely unusual locations need `PHA_ENGINE_PATH`. When pha DOES report a
missing engine at scan time ("…is not installed or not on PATH"), the tool is
really absent — install it rather than retrying.

- **Tesseract** (standalone OCR): macOS `brew install tesseract tesseract-lang`
  (the `-lang` formula provides the language data, e.g. `por`, `lat`);
  Debian/Ubuntu `apt-get install tesseract-ocr tesseract-ocr-por` (one package
  per language); Windows `choco install tesseract` or the UB-Mannheim installer.
- **LiteParse** (`lit parse`, ships the `lit` CLI): install the **Python**
  package — `pip install liteparse` (or `pipx install liteparse` /
  `uv tool install liteparse`). That is the recommended route and it works on
  Windows. `npm i -g @llamaindex/liteparse` (Node) is the alternative, but it
  **frequently fails on Windows** (and can leave the wrong `lit` on PATH) — if it
  fails, do NOT give up and do not tell the user LiteParse is unavailable:
  install the Python package instead. The `lit` CLI is the SAME either way —
  install one, not both (see resolution above — any normal install works). A
  bare `command -v lit` hit is NOT proof (`lit` is a common name, e.g. LLVM's
  test runner): `lit --version` must print a LiteParse version. LiteParse BUNDLES
  its own Tesseract, so it needs no separate tesseract install. A non-English
  `liteparse_lang` requires that language's traineddata to be reachable —
  offline, set `TESSDATA_PREFIX` to a directory containing the `.traineddata`
  files.

---

## 5. SQLite schema

**Do not guess column names.**

- The `pages` table links to a document via **`document_id`** (not `doc_id`) and
  has no `path`/`sha256`; those columns live on **`documents`**.
- `page_edits` keys on `(page_id, editor)`.
- **`notes` is not `documents`.** The `notes` + `notes_fts` tables mirror the
  `notes/` folder for search only (`db.notes_*`, `notes.py`): no `document_id`,
  no page, no collection. A note hit is `kind: "note"` with `note`/`title`/
  `path`; it is the researcher's own conclusion (memory), so it is never mixed
  into `documents`, `chunks`, `pha status` counts, exports or bundles. Fusion
  keys on `(kind, id)` so a note id cannot collide with a chunk id.

Introspect with `pha_schema()` / `db.schema(conn)` (columns + FK joins) before
writing raw SQL against `archive.db`.

---

## 6. Library layout

**Library folders are readable + version-safe.** Each document version lives in
`library/<dir>/<stem>_<YYYY-MM-DD>/` (creation date; a content change creates a
new row/date, so old folders stay).

**The folder name is STORED, not recomputed.** `documents.library_slug` freezes
it when the row is created, and `ingest._doc_slug()` prefers that value (falling
back to the computed name only for a row that predates the column). Computing it
means rendering `created_at` in the machine's **local timezone**, so a machine
that changed zone looked for — and wrote — a folder under a different date than
the one on disk: one document ended up in two folders, and a reader that
recomputed the name reported its variants as missing (an artifact filter then
wrote 0 files and called the pass a success). Full account:
`enhancements/pha-library-slug-timezone-bug-report.md`.

- **One resolver, `ingest._resolve_library_dir(cfg, doc)`**, answers "where does
  this document's output go". A stored slug is authoritative; a legacy row (no
  slug) joins the existing folder that already holds its `transcription-*`, and is
  **stamped** with that name on the next write (`_stamp_library_slug`), so the
  choice sticks. Writers never create a second folder for one document.
- **Readers search the siblings too.** `_pages_dir_for()` /
  `library_page_path()` try the resolved folder first, then every other
  `<stem>_*` folder, so a variant written under another date is still found.
- `_library_doc_dir()` is the read-only view: the resolved folder when it exists,
  else None (it never invents a path a writer has yet to create).
- **A `done` document with no library folder at all is reported** by
  `pha status` (text and `--json`) and `pha doctor`
  (`ingest.library_missing_documents`); re-export it with `pha export --doc N`.
- Pages of a directory-of-images document are named after the source scan
  (`502V.md`); PDF pages use `page-NNN.md`.
- A variant folder may carry a model suffix (`edited-<editor>@<model>`); the bare
  `edited-<editor>` and its `@<model>` sibling are **one variant** — the bare name
  only records that the model was unknown when that folder was written — so pha
  lists/resolves only the qualified one and a leftover bare folder (an older pha
  wrote both) is ignored, never cited or served.

Read a variant through `pha cite` / `pha page` rather than globbing the folders
yourself.

---

## 7. Where the rest is written down

| Topic | File |
|---|---|
| running the pipeline, per-page passes, review round-trip | `OPERATING.md` |
| plain-language explanation for the archive owner | `PIPELINE.md` |
| commands, configuration, quickstart | `README.md` |
| stage filters (full design) | `FILTERS_PLAN.md` |
| bibliographic sidecars (plan and rationale) | `BIBLIOGRAPHY_PLAN.md` |
| two-machine setups, bundles, hand-over | `MULTI_COMPUTER.md` |
| MCP client wiring | `MCP_CLIENTS.md` |
| the DeepSeek Harness plugin | `DSH_PLUGIN.md`, `dsh-pha/README.md` |
| releases and version bumps | `RELEASING.md` |
| planned web UI | `WEB_INTERFACE_PLAN.md` |
