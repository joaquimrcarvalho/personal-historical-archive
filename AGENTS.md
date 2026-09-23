# AGENTS.md — conventions for agents working in this repo

## Commits

- **Agent-made commits MUST end their message with a model trailer**, naming
  the agent's model, e.g.:
  `Model: deepseek-v4-flash (DeepSeek Harness)`
  (use the current session's model name; append it as the last line of the
  commit message).
- Commits made by the human do NOT carry the trailer — it is manual only
  (there is no hook).
- **Version bumps are manual and user-driven.** The version lives in two
  places that must stay in sync: `pyproject.toml` (`version = ...`) and
  `src/personal_historical_archive/__init__.py` (`__version__ = ...`). It is
  NOT bumped automatically per commit/push. When the human says **"push"**
  they mean push without a bump; **"push and update version"** (or "bump the
  version") means bump first (semver: patch for fixes, minor for features),
  then commit + push. When asked to just "push", advise whether a bump seems
  warranted (e.g. a user-visible feature landed since the last bump) and let
  the human decide. The self-update check compares the installed
  `__version__` against the one on the GitHub default branch, so a push only
  reaches the installed base as an "update available" notice when the version
  was actually bumped.
- Verify with `git log -1 --format='%B'`.

## Project essentials

- **personal-historical-archive (pha)** — local archive of historical
  documents; CLI `pha`, Python package `personal_historical_archive`
  (`src/personal_historical_archive/`). The working branch is `main`.
- **archive_dir is the self-contained data root.** Everything the archive
  owns lives under it: `dropbox/` (documents), `models/` (model-interface
  definitions), `palaeographers/`, `editors/`, `encoders/` (content rules),
  `notes/` (Obsidian-compatible research notes generated from archive queries),
  `skills/` (the pha-specific agent skills, seeded by pha and never
  overwritten), `library/`, `renders/`, `archive.db` (generated), plus
  `pha-location.md` + `.pha/` (machine-local: WHERE the `pha` tool is on THIS
  machine — written on every run, gitignored, never archive content; see
  "`pha` not on PATH?" under Remote / machine-to-machine). The project
  dir holds only code, `prompts/`, `schema/` and the `_sample.md` templates.
  Precedence: `PHA_ARCHIVE_DIR` in the environment > a legacy
  `PHA_ARCHIVE_DIR` line in a gitignored `.env` > `paths.archive_dir` in
  `config.yaml` (written by `pha set archive-dir`) > the default `.` (the
  project root). The legacy `.env` line sits ABOVE `config.yaml` on purpose:
  the shipped `config.yaml` carries `archive_dir: .` as a default, so treating
  that as authoritative would silently move every pre-existing `.env` user's
  archive. `config.yaml` is still the tracked, reviewable home — `pha set
  archive-dir` writes it AND removes the legacy `.env` line, which is what
  makes it effective; `pha info` names the source that won
  (`archive_source`). A fresh archive is seeded with
  `default.md` for model/palaeographer/editor/encoder (all qwen3-vl-8b) so it
  works with zero config.
- **Three config layers.** (1) `models/<id>.md` — pure model interface
  (endpoint, api key, server model name, `api_style`, `max_vision_px`,
  `vision_jpeg_quality`, `context_tokens`); (2) `palaeographers/`, `editors/`,
  `encoders/<id>.md` — CONTENT ONLY (prompt/rules + `temperature`/`max_tokens`
  + `thinking` + encoder params; they carry NO model). A `thinking: on|off`
  on a rules file is a **stage-level override that wins over the paired
  model's** value (the model file states a capability, the rules file states
  what this pass needs; omit to inherit); (3) `pha.yaml` sidecars that pair
  each stage's `rules` with its `model` (BOTH required per stage), plus
  per-collection `render` settings. To add one: duplicate `_sample.md`,
  rename, edit, save. The file stem is the id. Legacy files that still inline
  their interface keep working (treated as an inline model); run
  `pha migrate-config` to split them.
- **Builtin `_sample*.md` catalogue** (seeded into the project AND into a
  fresh `pha init-archive`; never loaded — the `_` prefix means "copy me to a
  real id"): the how-to-create `_sample.md` per stage; local engines
  `_sample.tesseract.md`, `_sample.liteparse.md`, `_sample.liteparse.fra.md`,
  `_sample.liteparse.spa.md`, `_sample.liteparse.embedded.md`; local LM Studio
  models
  `_sample.local-qwen3-vl.md`, `_sample.local-gemma4.md`; the **remote**
  provider `_sample.deepseek.md` (shows the `${PHA_ARCHIVIST}` api_key wiring,
  vision variant commented — a text and a vision id differ only in
  `model`/`api_style`/`max_vision_px`); printed-book rules
  `_sample.printed-books.md`, `_sample.printed-critical-edition.md`; and
  `_sample.generic.md` (general-purpose editor). `config.builtin_samples()` is
  the single source of truth; a test asserts the committed files match it.
  Samples are seeded into the PROJECT by `ensure_dirs` and into a NEW archive
  by `pha init-archive`; an already-created archive does **not** receive a
  newly added sample on a later run, so copy one in by hand if it is wanted
  there.
- **Stage filters live in the ARCHIVE** (`<archive>/filters/<id>/`), not the
  project: `filter.py` (`run(value, ctx)`) plus an optional `filter.md`
  manifest, referenced from a stage's `pre:`/`post:` in `pha.yaml`
  (`palaeographer.post`, `editor.pre`, `editor.post`, `encoder.pre`,
  `encoder.post`). `pha filters` lists them; `pha filter <id>` runs one over
  text. Python filters run **in-process** (archive-owner code, same trust as a
  prompt file — there is NO sandbox); a manifest `command:` runs another
  executable with the same envelope. A filter failure fails that unit and
  stores nothing. Editing a filter re-runs its stage: the applied chain (name +
  params + content hash) is stored on each page/edit/records run and compared
  to what is configured now, so an edited script, changed params or a filter
  added/removed re-extracts/re-edits/re-encodes without `--reprocess` — the
  same rule as a changed rules/model file. It is visible in the library page
  front matter (`filters: <name>:<sha>:<params>`) and the records file; a
  hand-corrected page has it cleared. An artifact filter (`returns: none`) is
  stamped under `library/<slug>/.filter-stamps/` and re-runs only when its own
  files, its declared `inputs:` or the EDITED pages change. Adopt one by
  editing `pha.yaml`, not by moving files. Reference filters ship in the repo's
  `filters/` (copy into the archive to adopt). Full design:
  `FILTERS_PLAN.md`.
- **Staleness by mtime**: editing a palaeographer / editor / encoder / prompt
  file triggers re-extraction / re-editing / re-encoding of affected documents
  on the next scan/run. A document also re-extracts when the resolved
  palaeographer differs from the one recorded on it. A document RE-EDITS when
  the editor's rules file (`editors/<id>.md`) OR its model interface file
  (`models/<id>.md`) changed, or when the paired editor MODEL id differs from
  the one recorded — re-run `pha edit` (all documents) or `pha edit --path
  collections/COLX` (just one collection/document) to pick the change up.
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
- **Palaeographer Notes are READING NOTES only** (Language, Script, difficult
  words). The palaeographer never outputs named-entity lists or content
  summaries — entities/notes come from the editor, structured records from
  the encoder.
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
- **One model per model-server at a time.** A server that loads models just in
  time keeps a single one resident — loading two (e.g. qwen vision + amalia
  editor) causes swap/page-out that fills the disk and wedges the server.
  So `pha scan`, `pha edit`, `pha reindex`, `pha test`, `pha unbundle` and
  `pha handoff fetch` each take a lock on **every model-server they will talk
  to** and refuse if one is busy, naming the server and the holding job. Two
  jobs may run concurrently **iff their servers are disjoint**, so a job on a
  remote model does not block a local one. Keys are declared per model file:
  `models/<id>.md` may carry `server: mac-studio`; an **unlabelled** model file
  takes the wildcard, which serialises with everything (the old global rule),
  and the embedding model uses `embeddings.server:` or its endpoint. Declare
  real capacity once per machine in `config.yaml` when the models are already
  loaded (auto-evict off):
  ```yaml
  servers:
    mac-studio: {slots: 2}
  ```
  `pha doctor` prints the resulting keys, their capacity and the lock dir.
  Locks live in a **user-global** directory (`~/Library/Caches/pha/locks` on
  macOS), so two archives sharing one server serialise too. `pha search` never
  takes the lock: while a job uses the embedding server it answers with keyword
  results and a `note` instead of loading the embed model (`--force` embeds
  anyway). Read-only commands (`status`, `search`, `page`, `cite`, `config`,
  `pending`, and the FastMCP query tools) open the DB read-only — no schema work
  on connect — so a running job cannot refuse them. **Consequence: a query
  command must never reference a column a newer pha adds**, because no
  migration ran — read such a column through `db.row_get(row, "<col>")` and
  guard a whole-table check with `db.has_column(conn, "<table>", "<col>")`
  (0.34.1: `pha status` failed with "no such column: pe.pinned_at" on every
  archive that had not been written to since the per-page columns landed). **`pha reindex` belongs in
  that set because re-embedding loads the embed model** — running it alongside a scan/edit is what times out `embed()`
  (see the vector-loss incident in
  `enhancements/pha-embed-loss-bug-report.md`).
- **Local OCR/parse engines (`engine: tesseract` / `engine: liteparse`) run
  WITHOUT LM Studio** — they are local executables, not LLMs, so an OCR scan
  loads no model (it still takes the pha scan lock). The engine + its settings
  live in the MODEL file (`models/tesseract.md`: `tesseract_lang`,
  `tesseract_psm`; `models/liteparse.md`: `liteparse_lang`, `liteparse_dpi`,
  `liteparse_ocr` fresh|embedded|prefer-embedded, `liteparse_format`
  text|markdown|json); a
  content-only rules file names the pass and is paired in pha.yaml, e.g.
  `palaeographer: {rules: ocr, model: tesseract}` (one rules file serves both
  engines — OCR ignores the prompt). Start from the samples
  `models/_sample.tesseract.md`, `models/_sample.liteparse.md`,
  `palaeographers/_sample.ocr.md`.
  **`liteparse_ocr: prefer-embedded`** reuses a PDF page's own text layer only
  when it passes a quality gate (enough text, mostly letters, word-like
  tokens — `liteparse_embedded_min_chars`, `liteparse_embedded_min_quality`;
  Latin-script word test only, so CJK/Greek/Cyrillic pass), else OCRs the
  raster; conservative by design, sample `models/_sample.liteparse.embedded.md`.
  **Install on the ARCHIVE machine.** Probe FIRST with `pha doctor` — it
  checks the binaries pha spawns (and works even before an archive is set).
  Options: `--engine liteparse` treats an engine as required, `--json` prints
  a machine-readable report, and it exits non-zero when a required engine is
  broken. Remotely, call the MCP tool `pha_doctor()` — the same check, run on
  the archive machine. HOW pha finds engines (why a tool can work in your
  Terminal yet "not be on PATH" for pha): pha is often launched from a GUI /
  agent / cron context whose PATH is minimal, unlike your interactive shell.
  So pha resolves each engine binary as: (1) its own PATH; (2) the dirs in
  the `PHA_ENGINE_PATH` env var; (3) the PATH your login shell would provide
  (queried once via `$SHELL -lic`, cached; set `PHA_NO_LOGIN_PATH=1` to
  disable); (4) the bin dir of the interpreter pha runs from. In practice any
  install your Terminal can run — brew, pyenv, nvm, pipx, `uv tool`, npm -g,
  `pip` into pha's own venv — is found automatically; only genuinely unusual
  locations need `PHA_ENGINE_PATH`. When pha DOES report a missing engine at
  scan time ("…is not installed or not on PATH"), the tool is really absent —
  install it rather than retrying.
  - Tesseract (standalone OCR): macOS `brew install tesseract tesseract-lang`
    (the `-lang` formula provides the language data, e.g. `por`, `lat`);
    Debian/Ubuntu `apt-get install tesseract-ocr tesseract-ocr-por` (one
    package per language); Windows `choco install tesseract` or the
    UB-Mannheim installer.
  - LiteParse (`lit parse`, ships the `lit` CLI): install the **Python**
    package — `pip install liteparse` (or `pipx install liteparse` /
    `uv tool install liteparse`). That is the recommended route and it works
    on Windows. `npm i -g @llamaindex/liteparse` (Node) is the alternative,
    but it **frequently fails on Windows** (and can leave the wrong `lit` on
    PATH) — if it fails, do NOT give up and do not tell the user LiteParse is
    unavailable: install the Python package instead. The `lit` CLI is the SAME
    either way — install one, not both (see resolution above — any normal
    install works). A bare `command -v lit` hit is NOT proof (`lit` is a
    common name, e.g. LLVM's test runner): `lit --version` must print a
    LiteParse version. LiteParse BUNDLES its own Tesseract, so it needs no
    separate tesseract install. A non-English `liteparse_lang` requires that
    language's traineddata to be reachable — offline, set `TESSDATA_PREFIX` to
    a directory containing the `.traineddata` files.
- Pipeline: dropbox → palaeographer per-page transcription → optional editor
  transform → optional encoder (concatenated whole-document text, page-grounded
  records) → SQLite (FTS5 + embeddings, indexing both raw and edited
  variants) → hybrid search + FastMCP (`pha_*` tools).
- **Search → full page text**: `pha search` hits carry a `page_file` (the
  library `.md`) and the CLI prints that path plus a shortcut under each hit.
  `pha page <doc> <page> [--edited]` prints one page's FULL text (raw, or the
  edited/translated variant with `--edited`); `<doc>` is an id or filename
  substring, and `--json` gives agent-friendly output. Use it to read the
  context around a search snippet.
- **SQLite schema — do not guess column names.** The `pages` table links to a
  document via **`document_id`** (not `doc_id`) and has no `path`/`sha256`;
  those columns live on **`documents`**. `page_edits` keys on `(page_id,
  editor)`. Introspect with `pha_schema()` / `db.schema(conn)` (columns +
  FK joins) before writing raw SQL against `archive.db`.
- **Library folders are readable + version-safe**: each document version lives
  in `library/<dir>/<stem>_<YYYY-MM-DD>/` (creation date; a content change
  creates a new row/date, so old folders stay). Pages of a directory-of-images
  document are named after the source scan (`502V.md`); PDF pages use
  `page-NNN.md`. A variant folder may carry a model suffix
  (`edited-<editor>@<model>`); the bare `edited-<editor>` and its `@<model>`
  sibling are **one variant** — the bare name only records that the model was
  unknown when that folder was written — so pha lists/resolves only the
  qualified one and a leftover bare folder (an older pha wrote both) is ignored,
  never cited or served. Read a variant through `pha cite` / `pha page` rather
  than globbing the folders yourself.
- **Review round-trip (historians correct the files)**: the library `.md`
  files are the human review surface. A historian edits a page body; `pha
  status` reports un-imported corrections (a file is pending when its mtime is
  newer than the page's `exported_at` **and its body differs** from the stored
  text — mtime alone reported machine-written pages as corrections, measured
  2026-09-18: 435 imported when 4 were real); `pha review [--doc N]` imports
  them into the DB — **only the changed files**, which is the same pending set
  `pha status` reports. It **refuses** while a scan/edit/reindex holds its
  model-server lock or a document is still `processing` (`--force` overrides;
  `--unset` is not gated — releasing a stamp is safe mid-pass). (`--all` is the opt-in blanket import that stamps every file;
  `--unset [--doc N [--page P]]` clears the stamp again and keeps the text.)
  Correcting a `transcription-*` page fixes the palaeographer's reading:
  the page is stamped `reviewed` (`reviewed_at`), so `pha scan` never re-reads
  it, and `pha edit` must then run so the editor re-processes JUST that page
  from the corrected text (it detects the raw changed). Correcting an
  `edited-*` page fixes the final output: that edit is stamped `reviewed` and
  is never overwritten by `pha scan`/`pha edit`. Either way, follow with
  `pha reindex --doc N` (scope it to the corrected document; re-indexing is
  incremental, so only the changed chunks are embedded — `--force` re-embeds
  every chunk, `--page P` narrows it to one page). Reviewed pages show
  `reviewed: true` in front matter. A
  `reviewed` row outranks `--reprocess`, so **`pha review --unset` is the only
  way to re-run a stage over human-touched text** — required before applying a
  new palaeographer/editor (or the planned stage filters) to those pages.
- **Re-reading ONE page with a chosen model (`pha scan --page N`)** — the
  machine counterpart of the review round-trip (a model re-read, not a human
  correction). `pha scan --path <one doc> --page N [--page M …]
  [--palaeographer X] [--model Y] [--dry-run] [--no-pin]` renders and
  transcribes ONLY those pages, with the override **authoritative for that run**
  and its two halves independent (`--model` alone keeps the document's rules,
  `--palaeographer` alone keeps its model; a VLM prompt paired with a local OCR
  engine is warned about). Each re-read page records its own provenance
  (`pages.palaeographer`/`palaeographer_model`, plus `palaeographer:`/`model:`/
  `pinned: true` in its library front matter) and is **pinned**: a later bulk
  `pha scan`, `--reprocess`, or changed collection config keeps it and says
  `kept N pinned page(s)`. The editor re-runs for that page and the indexer
  re-embeds it (incrementally), so **no follow-up command is needed**. It needs
  a target resolving to exactly ONE document; `--page` is refused with
  `--watch`. A human-`reviewed` transcription is refused (release with
  `pha review --unset --doc N --page P`), and a human-corrected edit is kept.
  Naming the page re-reads a pinned page on purpose and re-pins it; `--unpin`
  releases the protection (text kept), `--no-pin` records provenance without
  pinning, `--dry-run` prints the plan with no model call. `pha test --page N …`
  previews it without touching the DB, `pha status` shows `N pinned` per
  document, and `pha page <doc> N --json` reports the page's pair.
- **Re-editing ONE page with a chosen editor (`pha edit --page N`)** — the
  EDIT-stage twin of the single-page re-scan. `pha edit --path <one doc>
  --page N [--page M …] [--editor X] [--model Y] [--dry-run] [--no-pin]`
  re-edits ONLY those pages (`--page` is repeatable or comma-separated);
  `--editor`/`--model` are **authoritative for that run** and need
  `--page` (a whole-document pass uses the `pha.yaml` config). The page records
  its own pair (`page_edits.editor_model` + `pinned_at`, and `editor:`/`model:`/
  `pinned: true` in its library front matter) and stays in the **document's**
  `edited-<editor>@<model>` folder — the page file is authoritative for its own
  pair, exactly as `pha scan --page N` works for the transcription. It is
  **pinned**: a later bulk `pha edit` (or `--reprocess` or a changed collection
  config) keeps it and reports `kept N pinned page(s)`; the document row
  (`documents.editor`) is never rewritten by a per-page override. The indexer
  re-embeds just that page (incremental), so no follow-up command is needed.
  A page whose served edit is human-`reviewed` is refused (release with
  `pha review --unset --doc N --page P`); naming the page with the CONFIGURED
  editor (no override) re-edits it and releases that page's override pin;
  `--unpin [--path <doc> [--page P]]` releases the protection (text kept);
  `--no-pin` records provenance without pinning; `--dry-run` prints the plan
  with no model call. `pha test <doc> --page N --editor X --model Y` previews it
  without touching the DB. `pha status` shows `N pinned page edit(s)`, `pha page
  --edited --json` reports `page_editor`/`page_editor_model`/`edit_pinned`, and
  `pha cite` labels the citation with the pair the text actually came from. An
  explicit `--editor X` on `pha page`/`pha cite` still selects a VARIANT FOLDER
  by name; the page's own folder is the document's.
- Full usage: README.md; planned web UI: WEB_INTERFACE_PLAN.md.

## Usage — how agents operate the archive (not just develop it)

These conventions cover everyday *use* of pha, the same way the sections above
cover code. The archive is meant to be driven by an AI agent: browse, set
how a collection is processed (palaeographer / editor / encoder), add
documents, scan, edit, encode, and search.

### Who you are talking to (in an archive: a historian, not a programmer)

An agent may be running **from an archive directory rather than this
repository**. There, the person is almost always a **historian, not a
programmer** — assume that for every archive session unless they say
otherwise:

- Explain what you are about to do, and why, in plain language and in terms
  of their documents and research — not code, files or internals. Report the
  result the same way ("14 pages transcribed, 2 need your review", not a log).
- **Before asking for extra rights on their computer** — installing software,
  reaching files outside the archive, an administrator password — say in
  simple words *what* you need, *what it is for*, and *what will change* on
  the machine; then ask for the narrowest scope that does the job and wait for
  a clear yes. If you must ask for broad access, explain why a narrower one
  will not do. Never take a permission they did not grant.
- Do not make them edit config files or run commands you could run yourself.
  When they must run something (a password prompt, an installer), give the
  exact step and explain it before they run it.
- This applies to every escalation request, including the sandbox / approval
  prompts raised by the agent runtime — not just to commands you type.

Run `pha help` (or `pha help <readme|mcp|historians|agents>`) for an
orientation that points at this file, README.md, MCP_CLIENTS.md and
HISTORIANS_README.md — it always works, even before an archive is configured.

Bundled **skills** (`skills/*/SKILL.md`, installable to `~/.agents/skills/`)
cover two common agent mistakes: `pha-search-context` (don't answer from a
search snippet — recover the full page/document) and `pha-document-operations`
(re-run the pipeline on an already-ingested document: `pha scan --path …`,
`--reprocess` to force it, `pha edit --path … --page N`, `pha test`). An
archive created with `pha init-archive` carries its own `AGENTS.md`/`README.md`
with the same re-run-one-document guidance, so an agent working only from the
archive directory does not need the source.

**The archive carries its own `skills/` folder** — the pha-specific skills as
`<archive>/skills/<name>/SKILL.md` plus `skills/README.md` (the format, and how
to install one into a runtime). `pha init-archive` seeds it, and every run
seeds it into an existing dedicated archive that lacks it (`Config.ensure_dirs`);
seeding is **once and never overwritten**, so an archive owner's edits, extra
skills and deletions survive. The bodies are **embedded in the pha package**
(not read from this checkout), precisely because the agent operating an archive
may have pha installed with no access to this repository — and the archive's
own `AGENTS.md`/`README.md` point agents at `<archive>/skills/` first. The repo
`skills/*/SKILL.md` files remain the authored copies; a test
(`tests/test_skills.py`) asserts the embedded constants match them byte for
byte, so the two cannot drift. Keep the folder name equal to the front-matter
`name`.

### Notes (research notes generated from archive queries)

- **`notes/` is a top-level archive folder** (a sibling of `dropbox/`,
  `library/`, `renders/`) holding **Obsidian-compatible markdown notes**
  generated from queries to the archive. It is user-facing research output,
  **not pipeline output**: `pha scan`/`edit`/`encode` never read or write it,
  and `notes/README.md` (seeded automatically, never overwritten) documents the
  format for humans and agents. `pha` creates `notes/` + README in any archive
  that lacks it on its next run (`ensure_dirs`).
- **Agent workflow**: when asked to write a note (e.g. "search the archive for
  'Malaca' and summarize in a note"), search (`pha search`), read each hit's
  full page with `pha page <doc> <page>` (and `--edited` when available) —
  never summarize from a snippet alone — then synthesize into one
  `lowercase-hyphenated.md` in `notes/`, citing every fact to its source.
- **Format**: `[[wikilinks]]` connect notes; `[^n]` footnotes hold citations;
  YAML front matter (`title`, `created`, `tags`, `sources`) is recommended.
  Otherwise keep it plain Markdown so the files render in any viewer.
- **Citations name the document + page + the variant, and link the served
  viewer.** Use `pha cite <doc> <page> [--edited]` — it prints the citation, the
  stable slug and the exact **filled** variant, and refuses to cite an empty
  (`*waiting*`) one. Link the page **viewer** so a reader can carry on reading:

      [^1]: *DocHist do Padroado do Oriente* vol04 (doc 22), p. 437
            (edited: modern-portuguese@deepseek-v4-flash) —
            [p. 437](http://127.0.0.1:8765/doc/<slug>/p437) · `pha cite 22 437 --edited`

  Link `/doc/<slug>/p<page>` (the viewer: prev/next/first/last, position, jump
  box); embed `/p<page>.jpg` **only** when the picture belongs inline (an embed
  cannot navigate). Never embed a `library/` path — it carries the version date
  and goes stale on re-processing.
- **Those links resolve only while `pha serve` is running**, so a note that
  embeds or links a page must carry a short warning near the top (`pha serve` is
  read-only, loopback `127.0.0.1:8765`). Resolve a citation on request (e.g.
  "show the page in footnote 12") with `pha page <doc> <page> [--edited]`.
- Full instructions: see `notes/README.md` in the archive (or the repo's
  top-level `notes/README.md`, the seed template), plus `obsidian-integration.md`
  in the archive's `notes/`.

### Bibliographic references (sidecars)

A document may carry a full bibliographic reference in a **sidecar beside it**:
`<stem>.dc.json` (JSON — the structured form a human edits), `<stem>.bib`
(BibTeX — the form an agent can draft from the scan, and a human can still
edit), or `<stem>.mods.xml` (MODS 3.8 — a machine interchange format, e.g. what
Zotero exports). A directory-of-images document keeps it *inside* the folder
(`vol04/vol04.dc.json`). All three parse into one record, so the format never
changes how a citation looks. Key spelling is forgiving (`part_number`,
`partNumber` and `dcterms:partNumber` are one field; BibTeX reads
`record_origin` or `recordOrigin`), so a qualified Dublin Core JSON-LD record
also works. If more than one sidecar exists, **JSON wins** (it is the one a
human maintains) and `pha cite` warns.

- **Drafting a reference from a scan is allowed — and must be marked.** Reading
  a title page to write a `.bib` needs no Zotero, which is the point of the
  format. But the entry MUST carry `record_origin = {agent-drafted-unverified}`
  (`pha bib <doc> --to-bibtex --origin agent-drafted-unverified --write`), which
  badges every citation `[unverified reference]` until a human checks it. Never
  remove that marker yourself, and never invent a shelfmark, publisher, volume
  or date: a wrong one reads exactly like a correct one. Only a human may set
  `human-supplied` / `human-confirmed`.
- **Never tell a human to hand-edit MODS XML.** It is an interchange format.
  Convert it once — `pha bib <doc> --to-json --write` for editable JSON, or
  `--to-bibtex` for BibTeX (add `--write` to save; `--keep-others` to keep the
  other formats, `--qualified` for JSON-LD).

- **The rule is presence-only — no inheritance, no default.** A document without
  a sidecar keeps the filename-only citation. Do NOT copy a neighbouring
  document's reference, and do not add a sidecar to "fill in" a collection: an
  inherited reference is a confidently wrong citation, which is worse than none.
  `pha bib` lists what has no reference, and that gap is expected.
- **Read and report, don't invent.** `pha bib <doc>` shows one document's
  reference and `pha bib` the coverage. `pha cite <doc> <page>` renders it;
  when the record is machine-drafted the citation ends `[unverified reference]`.
- **Never state a reference as fact unless it is verified.** Bibliographic data
  is exactly what a model confabulates: a wrong volume number, publisher or
  shelfmark looks like a correct one. An agent may draft a sidecar when the
  human asks, but must set `recordOrigin` to `agent-drafted-unverified`, must
  not invent a shelfmark or an imprint, and must not overwrite a
  `human-supplied` record without saying so. A wrong source record (e.g. a
  creator that is really the holding library) belongs fixed in the source
  system — Zotero — not silently patched in the sidecar.
- **Editing a sidecar never re-transcribes a document.** A reference is
  metadata: it does not touch `sha256`, page text, status or the library
  version, so `pha scan` is not needed to pick it up (`pha cite` and `pha bib`
  read the file live; the DB snapshot that `pha serve`/MCP read is refreshed by
  `pha scan`, `pha reindex`, or any `pha bib` run).
- **Importing from Zotero:** the local API exports MODS —
  `curl "http://localhost:23119/api/users/0/items/<KEY>?format=mods"`. Strip
  the `<note>` elements before saving (they carry the owner's annotations and
  highlights and are ~86% of the bytes) and add a `recordInfo` with the
  document id, the Zotero key and `fetched-from-zotero-unverified`. If more
  than one sidecar is present the JSON wins and `pha cite` warns — resolve it,
  `pha bib --check` reports it.
- Design and rationale: `BIBLIOGRAPHY_PLAN.md`.

### DeepSeek Harness plugin (dsh-pha)

A Harness agent on a machine with pha + a Harness install can expose the archive through the
repo's bundled `dsh-pha` plugin: it registers the `pha_*` model tools (`pha_status`,
`pha_documents`, `pha_document`, `pha_page`, `pha_search`, `pha_archive`,
`pha_job_start`/`pha_job_status`/`pha_job_kill`) and a same-origin `/pha/*` JSON API. See
[`DSH_PLUGIN.md`](DSH_PLUGIN.md) and [`dsh-pha/README.md`](dsh-pha/README.md). Install it into
a Harness profile (`pnpm add <repo>/dsh-pha` + the [`cordis.patch` row](dsh-pha/cordis.patch.example.yml)
+ restart). Reads are read-only (`immutable=1` sqlite / the `pha` CLI); mutations still go
through the real `pha` CLI, so the model-server lock, staleness and review round-trip rules
below still apply — never start `pha scan`/`pha edit` while another job holds the server they
need.

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
- **`pha test <doc-or-collection> [--pages N] [--random]`** → run the WHOLE
  pipeline (transcription → editing → encoding) on a SAMPLE of pages to
  sanity-check / fine-tune a config before a full pass. It resolves the same
  `pha.yaml` the real run uses, writes everything to
  `<archive_dir>/.pha-test/<doc>-<timestamp>/` (never touching the DB/library/
  renders), and prints a `report.md`. `--palaeographer/--editor/--encoder/
  --model/--prompt/--temperature/--max-tokens` override the config for that
  run; `pha test --show` re-prints the most recent report, `pha test --list`
  lists saved runs, `pha test --clean [target] [--dry-run]` deletes the `.pha-test`
  scratch dirs (run dirs accumulate one per run). It takes the same
  model-server locks as `pha scan`/`pha edit` (one model per server).
  The run dir holds BOTH the *effective* prompt per stage
  (`prompt-transcription.md`, `prompt-edit.md`, `prompt-encode-<name>.md` —
  the composed prompt plus its sources, for a human tuning it) AND
  `prompts-sent/` with the **exact text handed to the model**, verbatim:
  `transcription-p<NNN>.md`, `edit-p<NNN>.md`, `encode-<name>.md`. They differ
  by design — the `prompt-*.md` files omit the `Document: … / Page: n of m`
  wrapper (and, for the editor, the transcription being edited) — so an agent
  recording provenance for a reading must cite `prompts-sent/`, never
  `prompt-*.md`. An `engine` palaeographer (tesseract/liteparse) sends no
  prompt and therefore has no `transcription-p<NNN>.md`.
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

- A collection selects its models with a **`pha.yaml` sidecar** next to the
  documents (nearest-wins per key up the directory chain). Each stage pairs
  `rules` with its `model` — **both required** — e.g.:
  ```yaml
  palaeographer:
    rules: jesuit-cat1
    model: minimax-m3
  editor:                     # or `editor: null` for no editor
    rules: modernise
    model: minimax-m2-5
  encoders:
    - rules: table
      model: minimax-m2-5
    - rules: biographies
      model: minimax-m2-5
  render: {max_image_px: 3000, jpeg_quality: 88}
  ```
  The legacy plain-text selection files (`palaeographer`, `editor`) still work
  as a fallback. Encoders are files in `collections/COLX/encoders/`.
- **Locally**: write `pha.yaml` directly, then re-process so the change takes
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

- **One model per model-server at a time.** `pha scan`, `pha edit`,
  `pha reindex`, `pha test`, `pha unbundle` and `pha handoff fetch` take a lock
  on each server they will use and refuse if one is busy (naming it and the
  holding job); jobs on disjoint servers run together. Never start `pha
  edit`/re-edit/reindex while a `pha_scan_now` is running **against the same
  server** (two models there → swap → disk fill → hang, and for reindex →
  `embed()` timeouts that can cost a document its vectors). Check
  `pha_extraction_status` and `pha doctor` (which
  lists the keys and the lock dir) before starting a pass.
- **Quit LM Studio when not ingesting** — its model page-out is what eats disk
  space. Do not leave a vision + editor model loaded at the same time.
- **A stalled model request is bounded by `deadline_s`, and is not a page
  failure.** `timeout_s` (in a palaeographer/editor/encoder file) is
  per-OPERATION, and a provider that trickles keep-alive bytes resets it, so a
  request whose answer never arrives could sit for hours. pha enforces a
  wall-clock `deadline_s` (same file, default `2 × timeout_s`, ≥ 60s; `0`
  disables) — a *silent* connection is still bounded by `timeout_s`, the
  deadline bounds the *trickle*. On expiry the page is **abandoned for that
  pass**, named in the output (`⏱ page(s) abandoned … re-run`) and left pending
  for a later `pha scan`/`pha edit` — never recorded as a page error, and it
  does not count toward the consecutive-failure abort; what was read is still
  edited/indexed and the document stays `processing`. `pha status` flags a
  `processing` document with no progress for 30 min (`no progress for 4h17m —
  stalled?`, `PHA_STALL_WARN_S` overrides). Report:
  `enhancements/pha-request-stall-timeout-bug-report.md`.
- **Render cache is pruned automatically.** Rendered page images live in
  `renders/<content-sha>/`. A superseded or removed document's folder is
  deleted when a changed file is re-scanned, on `pha rm`, and on `pha bundle
  --move` — but only once no live document still shares that content hash.
  Sweep any leftovers with `pha prune [--dry-run]` (a content-cache GC; it
  never touches the DB or `library/`). **`pha prune --library-variants
  [--dry-run] [--doc N]` is the exception**: it deletes a bare `edited-<editor>`
  folder whose pages all survive elsewhere — exactly what the DB holds, or
  byte-identical to its `@<model>` sibling; a `*waiting*` stub is a placeholder,
  not content, so it does not make a folder look unique — and reports (never
  deletes) a folder holding a different reading. Never hand-delete a bare folder
  yourself; run the sweep and read its report.
- **After changing config**: re-run the matching pass (`pha scan`, `pha edit`,
  `pha encode`) so staleness-by-mtime picks up the change, then confirm with
  `pha status` / `pha_extraction_status`.

### Remote / machine-to-machine

- **Moving collections between archives without redoing scan/edit**: `pha
  bundle <collection-or-doc...>` packs documents + finished library
  transcriptions/edits/records + renders + the model definitions used into a
  portable directory; `pha unbundle <bundle>` (run on the target archive)
  imports it with NEW ids into a populated archive, pins the recorded
  palaeographer/editor via selection files (so `pha scan`/`pha edit` skip the
  imported docs instead of re-running them), and indexes it for search.
  `pha bundle --move` additionally DELETES the bundled documents from the
  source archive once the bundle is written (the bundle is the backup). See
  the README section "Moving / sharing collections between archives".
- **Lending a document to a second machine and taking the work back**:
  `pha handoff` is the other shape from `bundle` — the document stays in its
  own archive (same id, no copy) and a second machine does the slow
  scan/edit/encode. On the owner: `pha handoff out <target> [--worker NAME]`
  writes a payload and **leases** the document, so `pha scan`/`edit`/`encode`/
  `reindex` skip it (name+age shown, `pha status` lists it under "out on
  hand-over"; `--include-leased` overrides). A document that has never been
  scanned can be handed out too: `handoff out` **registers** it first
  (identity, resolved stages, page count — no render, no transcription), and
  that record is what the lease is looked up through. An `inbox/<rel>` target
  is moved into the dropbox first (mirroring the layout; only entries you name,
  refusing to overwrite unless `--force-inbox`). Renders are NOT carried either
  way (the worker re-renders to extract), so `handoff fetch` rebuilds the page
  images missing here — a document handed out before its first scan has none —
  and `pha render [--path … | --doc N]` does the same on demand. Renders are a
  derived cache, so byte-identity across machines is deliberately not required.
  On the worker: `pha handoff in
  <payload>`, then `pha scan/edit/encode --path <doc>` (or the `pha handoff
  work` wrapper), then `pha handoff back`. Back on the owner: `pha handoff
  fetch <result>` merges the work into the SAME document — local human
  corrections are kept and reported as conflicts, and a document processed
  under a different palaeographer/editor is reported `stale`. Identity across
  machines is `sha256` + dropbox-relative path (ids are per-machine and mean
  nothing across them); only the finished work travels back, never the source
  bytes. Leases live in `<archive>/.pha/handoffs/<id>.json` (machine-local,
  gitignored). `pha handoff status [--json]` / MCP `pha_handoff_status()` show
  what is out; `pha handoff cancel <id>` releases it and refuses a later
  result.
  **The worker does not embed hand-over material** (gap G1): `import_handoff`
  records the document set here as RECEIVED (`state: "in"` — NOT a lease, so the
  worker still scans/edits/encodes it), and `pha scan`/`pha edit` on the worker
  skip the index for those documents, because the return payload carries text and
  provenance but **no vectors** — the owner's `fetch` is the only place that
  embeds, so embedding on both machines repeats the same hours of work (measured:
  2,676 pages twice). `pha status` shows `index deferred — received for
  hand-over` instead of `NOT INDEXED`, `pha handoff back` reports the documents
  returned without chunks, and the escape hatches are `pha scan --index` /
  `pha edit --index` (one run) or `pha handoff cancel <id>` (permanent).
- The MCP server runs on the machine that owns the dropbox and models. A
  client on another machine needs only an MCP connection — no local models or
  dropbox. Start on the archive machine with
  `pha mcp --transport sse --host <LAN-IP> --port 8000` (no auth — use a
  private LAN/VPN/SSH tunnel). See MCP_CLIENTS.md for the full wiring.
- **`pha` not on PATH? Look in the archive first, don't guess.** Every pha run
  leaves a machine-local trace of where the tool lives on THIS machine:
  **`<archive>/pha-location.md`** (visible; the archive's `README.md`/`AGENTS.md`
  point at it) and **`<archive>/.pha/location.json`** (the machine-readable
  twin). Both record the resolved executable, the version, the source checkout,
  the MCP commands and — the part that always works — the PATH-proof
  interpreter form `<python> -m personal_historical_archive`, which needs no
  PATH entry and no activated venv and so works from any shell, cron job or
  agent runtime. Run it with the archive passed explicitly:
  `PHA_ARCHIVE_DIR=<archive> <python> -m personal_historical_archive status`.
  `pha info` prints the same tool fields (`pha_command`, `pha_version`,
  `location_file`) from inside pha. Only if that file is absent (pha has never
  run in this directory on this machine) install it — `uv tool install
  --editable .` makes plain `pha` work in every shell. The trace describes ONE
  machine (`hostname` + `written_at`): after a reinstall or a move the recorded
  path may be stale, so fall back to the interpreter form rather than trusting
  it blindly. It is gitignored — never commit it, and never let a copied
  archive inherit it.
- **`pha` reports "No pha archive is configured or found"** — this is a fresh
  install with no archive_dir set. Don't guess. Either point it at the real
  archive (`pha set archive-dir <path>`) or create one
  (`pha init-archive ~/pha-home && pha set archive-dir ~/pha-home`). It stops
  rather than silently running against an empty default DB.
- Upload documents from another machine via `pha_upload(kind, name, content_b64)`
  (files travel as base64; single files per call), then `pha_scan_now()`.
