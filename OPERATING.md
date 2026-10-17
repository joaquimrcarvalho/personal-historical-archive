# OPERATING.md — driving the pha pipeline

Task procedures for an agent *using* the archive (as opposed to developing pha):
how to find out what a collection is configured to do, how to change it, how to
re-run one stage over one page, and how to handle research notes and
bibliographic references.

This file is **repo-only**. `AGENTS.md` keeps the rules you must not get wrong
(the destructive-command rule, the model-server lock discipline, the review
round-trip's meaning) and points here for the detail. Read the section you need.

Run `pha help <topic>` for the shipped documents: `readme`, `pipeline`, `mcp`,
`historians`, `agents`, `multi-computer`, `harness`, `view`.

---

## 1. Who you are talking to (in an archive: a historian, not a programmer)

An agent may be running **from an archive directory rather than this
repository**. There, the person is almost always a **historian, not a
programmer** — assume that for every archive session unless they say otherwise:

- Explain what you are about to do, and why, in plain language and in terms of
  their documents and research — not code, files or internals. Report the result
  the same way ("14 pages transcribed, 2 need your review", not a log).
- **"Explain how this works" means `PIPELINE.md`.** When they ask how pha works,
  what happens to their documents, or what the difference between the editor and
  the encoder is, read `PIPELINE.md` (seeded into every archive beside
  `README.md`/`AGENTS.md`, and at this repo's root) and explain it in their
  terms. It is written for the archive owner; `AGENTS.md` is written for you — do
  not answer such a question from `AGENTS.md`.
- **Before asking for extra rights on their computer** — installing software,
  reaching files outside the archive, an administrator password — say in simple
  words *what* you need, *what it is for*, and *what will change* on the machine;
  then ask for the narrowest scope that does the job and wait for a clear yes. If
  you must ask for broad access, explain why a narrower one will not do. Never
  take a permission they did not grant.
- Do not make them edit config files or run commands you could run yourself. When
  they must run something (a password prompt, an installer), give the exact step
  and explain it before they run it.
- This applies to every escalation request, including the sandbox / approval
  prompts raised by the agent runtime — not just to commands you type.

`pha help` always works, even before an archive is configured, and it names
which copy of each file it found — your archive's, the checkout's, or the one the
installed package ships.

### Bundled skills

Bundled skills (`skills/<name>/`, installable to `~/.agents/skills/`) cover the
common agent tasks:

- `pha-search-context` — don't answer from a search snippet; recover the full
  page/document.
- `pha-document-operations` — re-run the pipeline on an already-ingested
  document.
- `pha-library-folders` — find and consolidate a document whose library folder
  was split in two (two dated `<stem>_<date>` folders after a timezone change, or
  a bare `edited-<editor>` beside its `@<model>` sibling); ships
  `scripts/consolidate_library_date_dupes.py` (dry run by default).
- `lmstudio-model-locality` — check local vs remote LM Studio models under LM
  Link before a scan/edit; helper `scripts/lmstudio_locality.py`.
- `pha-zotero-bibliography` — import a Zotero PDF with its sidecar; build/refresh
  a document's reference.
- `palaeographers-compare` — compare two or more readings of the same pages into
  a uniform comparative edition, with bundled `scripts/` to normalise, verify and
  build the `reference/` variant.
- `obsidian-vault` — search, create and organise notes in the owner's Obsidian
  vault, with archive-note sync provenance.
- `timelink-kleio-provenance` — cite Timelink/Dehergne facts back to the Kleio
  file and line.

An archive created with `pha init-archive` carries its own `AGENTS.md`/`README.md`
with the same guidance, so an agent working only from the archive directory does
not need the source.

**More skills come from the open pha skill index** —
<https://github.com/joaquimrcarvalho/pha-skill-index> — a README that *lists*
skills published elsewhere (name, description, repository, the folder inside it,
licence; today `digitarq-get` and `bnp-digital-download`, which download page
images from the Portuguese archives). When the owner asks what else is
available, or names that repository, read its README, present the entries, and
**ask which one to install** — never invent a skill, URL, path or licence that is
not in the index. Installing is a clone plus a copy of the folder the index names
into `<archive>/skills/` (add `~/.agents/skills/` for the runtime); the skill
then behaves like a bundled one, and pha never overwrites it. Do not confuse the
two folders: `skills/` in this repo is the authored copy of what pha *bundles*;
the index is a catalogue of what it does not.

The archive carries its own `skills/` folder — every bundled skill as
`<archive>/skills/<name>/SKILL.md` (plus the skill's helper files) and
`skills/README.md` (the format, and how to install one into a runtime).
`pha init-archive` seeds it, and every run adds anything missing to an existing
dedicated archive (`Config.ensure_dirs`). Every bundled file pha writes is
stamped (a trailing `sha256` comment), which is how an update tells its own
text from the owner's: `pha skills update` (run by `pha update`) refreshes a
file whose stamp still matches, and **reports** a file the owner edited instead
of overwriting it — `pha skills update --replace [skill]` takes pha's version
after backing the local copy up under `.pha/skills-backup/`. `pha skills status`
shows which files are current, outdated, edited here or predate the stamp. Every bundled file is embedded in the pha
package (not read from this checkout), precisely because the agent operating an
archive may have pha installed with no access to this repository — and the
archive's own `AGENTS.md`/`README.md` point agents at `<archive>/skills/` first.
The repo `skills/<name>/` files remain the authored copies; a test
(`tests/test_archive_skills.py`) asserts the embedded files match them byte for
byte, so the two cannot drift. Keep the folder name equal to the front-matter
`name`.

---

## 2. How to check how a collection/document is configured

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
  - `pha encoder` → lists collection-local encoders; `pha encoder <file>` shows
    a document's resolved encoders.
- **Remotely / connected via MCP** (agent on another machine):
  - `pha_collection_config("collections/COLX")` returns **one object** with the
    resolved `palaeographer`, `editor` (or `{id: None, ...}` when none is
    configured), `prompt`, and their `source` files.
  - `pha_palaeographers()`, `pha_editors()`, `pha_encoders(relpath)` list the
    available definitions.

Interpretation: an editor value of `None`/empty means the collection currently
has **no editor** — ask the historian before inventing one, and if an editor is
wanted, set it (below). The `source` field tells you where the selection came
from (a dropbox `editor` file, a config default, or nowhere).

### `pha test` — the whole pipeline on a sample

`pha test <doc-or-collection> [--pages N] [--random]` runs the WHOLE pipeline
(transcription → editing → encoding) on a SAMPLE of pages to sanity-check /
fine-tune a config before a full pass. It resolves the same `pha.yaml` the real
run uses, writes everything to `<archive_dir>/.pha-test/<doc>-<timestamp>/`
(never touching the DB/library/renders), and prints a `report.md`.

`--palaeographer/--editor/--encoder/--model/--prompt/--temperature/--max-tokens`
override the config for that run; `pha test --show` re-prints the most recent
report, `pha test --list` lists saved runs, `pha test --clean [target]
[--dry-run]` deletes the `.pha-test` scratch dirs (run dirs accumulate one per
run). It takes the same model-server locks as `pha scan`/`pha edit` (one model
per server).

The run dir holds BOTH the *effective* prompt per stage
(`prompt-transcription.md`, `prompt-edit.md`, `prompt-encode-<name>.md` — the
composed prompt plus its sources, for a human tuning it) AND `prompts-sent/` with
the **exact text handed to the model**, verbatim: `transcription-p<NNN>.md`,
`edit-p<NNN>.md`, `encode-<name>.md`.

They differ by design — the `prompt-*.md` files omit the `Document: … / Page: n
of m` wrapper (and, for the editor, the transcription being edited) — so an agent
recording provenance for a reading must cite `prompts-sent/`, never
`prompt-*.md`. An `engine` palaeographer (tesseract/liteparse) sends no prompt
and therefore has no `transcription-p<NNN>.md`.

---

## 3. Setting the palaeographer / editor / encoders for a collection

A collection selects its models with a **`pha.yaml` sidecar** next to the
documents (nearest-wins per key up the directory chain). Each stage pairs `rules`
with its `model` — **both required** — e.g.:

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

The legacy plain-text selection files (`palaeographer`, `editor`) still work as a
fallback. Encoders are files in `collections/COLX/encoders/`.

- **Locally**: write `pha.yaml` directly, then re-process so the change takes
  effect (`pha scan` re-extracts when the resolved palaeographer changes;
  `pha edit` re-edits; `pha encode` runs encoders).
- **Remotely via MCP**: push the selection/encoder file content with
  `pha_upload` (base64) under the right dropbox path (e.g.
  `collections/COLX/editor`), then `pha_scan_now()` to ingest/re-process.
- To add a **new definition** (a palaeographer/editor/encoder `*.md`): the
  palaeographer/editor definitions live at the project root beside the dropbox —
  a remote agent cannot write those via `pha_upload` (dropbox-scoped); ask the
  operator on the archive machine to copy a `*_sample.md` and edit it, or stage
  it in the dropbox first. Encoder definitions live inside the dropbox and can be
  uploaded directly.

---

## 4. Operating discipline (avoid breaking the machine)

The invariants behind these rules are in `ARCHITECTURE.md` §4; what follows is
what to do about them.

- **One model per model-server at a time.** Never start `pha edit`/re-edit/
  reindex while a `pha_scan_now` is running **against the same server** (two
  models there → swap → disk fill → hang, and for reindex → `embed()` timeouts
  that can cost a document its vectors). Check `pha_extraction_status` and
  `pha doctor` (which lists the keys and the lock dir) before starting a pass.
- **Quit LM Studio when not ingesting** — its model page-out is what eats disk
  space. Do not leave a vision + editor model loaded at the same time.
- **A stalled model request is bounded by `deadline_s`, and is not a page
  failure.** `timeout_s` (in a palaeographer/editor/encoder file) is
  per-OPERATION, and a provider that trickles keep-alive bytes resets it, so a
  request whose answer never arrives could sit for hours. pha enforces a
  wall-clock `deadline_s` (same file, default `2 × timeout_s`, ≥ 60s; `0`
  disables) — a *silent* connection is still bounded by `timeout_s`, the deadline
  bounds the *trickle*. On expiry the page is **abandoned for that pass**, named
  in the output (`⏱ page(s) abandoned … re-run`) and left pending for a later
  `pha scan`/`pha edit` — never recorded as a page error, and it does not count
  toward the consecutive-failure abort; what was read is still edited/indexed and
  the document stays `processing`. `pha status` flags a `processing` document
  with no progress for 30 min (`no progress for 4h17m — stalled?`,
  `PHA_STALL_WARN_S` overrides). Report:
  `enhancements/pha-request-stall-timeout-bug-report.md`.
- **Render cache is pruned automatically.** Rendered page images live in
  `renders/<content-sha>/`. A superseded or removed document's folder is deleted
  when a changed file is re-scanned, on `pha rm`, and on `pha bundle --move` —
  but only once no live document still shares that content hash. Sweep any
  leftovers with `pha prune [--dry-run]` (a content-cache GC; it never touches
  the DB or `library/`).
  **`pha prune --library-variants [--dry-run] [--doc N]` is the exception**: it
  deletes a bare `edited-<editor>` folder whose pages all survive elsewhere —
  exactly what the DB holds, or byte-identical to its `@<model>` sibling; a
  `*waiting*` stub is a placeholder, not content, so it does not make a folder
  look unique — and reports (never deletes) a folder holding a different reading.
  Never hand-delete a bare folder yourself; run the sweep and read its report.
- **After changing config**: re-run the matching pass (`pha scan`, `pha edit`,
  `pha encode`) so staleness-by-mtime picks up the change, then confirm with
  `pha status` / `pha_extraction_status`.

### Removing or moving a document

**Use `pha rm` / `pha mv`, never a shell `mv` or `rm`.**

- `pha rm ID|NAME` (default) moves the dropbox file(s) + sidecars and the library
  folder into `<archive>/bin/<stamp>/` and clears the index; it is reversible
  with `pha bin restore BATCH`, which prints the `pha scan` needed to rebuild the
  index. `--purge` deletes for good; `--keep-files` clears only the index. A
  filename substring that matches several documents is refused without `--all`.
- `pha mv ID|NAME DEST` relocates a processed document IN PLACE: the same DB row,
  pages, chunks, edits, records and sha-keyed renders survive; a shell `mv` would
  make the next scan treat the file as new and re-extract every page. A move
  changes the dropbox-relative path, hence the citation slug, and may change the
  resolved palaeographer/editor (reported; the next scan re-reads if so).
- A processing document is refused by both commands.

---

## 5. The review round-trip (historians correct the files)

The library `.md` files are the human review surface.

A historian edits a page body; `pha status` reports un-imported corrections (a
file is pending when its mtime is newer than the page's `exported_at` **and its
body differs** from the stored text — mtime alone reported machine-written pages
as corrections, measured 2026-09-18: 435 imported when 4 were real); `pha review
[--doc N]` imports them into the DB — **only the changed files**, which is the
same pending set `pha status` reports.

It **refuses** while a scan/edit/reindex holds its model-server lock or a
document is still `processing` (`--force` overrides; `--unset` is not gated —
releasing a stamp is safe mid-pass). `--all` is the opt-in blanket import that
stamps every file; `--unset [--doc N [--page P]]` clears the stamp again and
keeps the text.

- Correcting a `transcription-*` page fixes the palaeographer's reading: the page
  is stamped `reviewed` (`reviewed_at`), so `pha scan` never re-reads it, and
  `pha edit` must then run so the editor re-processes JUST that page from the
  corrected text (it detects the raw changed).
- Correcting an `edited-*` page fixes the final output: that edit is stamped
  `reviewed` and is never overwritten by `pha scan`/`pha edit`.

Either way, follow with `pha reindex --doc N` (scope it to the corrected
document; re-indexing is incremental, so only the changed chunks are embedded —
`--force` re-embeds every chunk, `--page P` narrows it to one page). Reviewed
pages show `reviewed: true` in front matter.

A `reviewed` row outranks `--reprocess`, so **`pha review --unset` is the only
way to re-run a stage over human-touched text** — required before applying a new
palaeographer/editor (or the planned stage filters) to those pages.

---

## 6. Re-reading ONE page with a chosen model (`pha scan --page N`)

The machine counterpart of the review round-trip — a model re-read, not a human
correction.

```
pha scan --path <one doc> --page N [--page M …] [--palaeographer X] [--model Y]
         [--dry-run] [--no-pin]
```

Renders and transcribes ONLY those pages, with the override **authoritative for
that run** and its two halves independent (`--model` alone keeps the document's
rules, `--palaeographer` alone keeps its model; a VLM prompt paired with a local
OCR engine is warned about).

Each re-read page records its own provenance (`pages.palaeographer`/
`palaeographer_model`, plus `palaeographer:`/`model:`/`pinned: true` in its
library front matter) and is **pinned**: a later bulk `pha scan`, `--reprocess`,
or changed collection config keeps it and says `kept N pinned page(s)`. The
editor re-runs for that page and the indexer re-embeds it (incrementally), so
**no follow-up command is needed**.

It needs a target resolving to exactly ONE document; `--page` is refused with
`--watch`. A human-`reviewed` transcription is refused (release with
`pha review --unset --doc N --page P`), and a human-corrected edit is kept.

Naming the page re-reads a pinned page on purpose and re-pins it; `--unpin`
releases the protection (text kept), `--no-pin` records provenance without
pinning, `--dry-run` prints the plan with no model call. `pha test --page N …`
previews it without touching the DB, `pha status` shows `N pinned` per document,
and `pha page <doc> N --json` reports the page's pair.

---

## 7. Re-editing ONE page with a chosen editor (`pha edit --page N`)

The EDIT-stage twin of the single-page re-scan.

```
pha edit --path <one doc> --page N [--page M …] [--editor X] [--model Y]
         [--dry-run] [--no-pin]
```

Re-edits ONLY those pages (`--page` is repeatable or comma-separated);
`--editor`/`--model` are **authoritative for that run** and need `--page` (a
whole-document pass uses the `pha.yaml` config).

The page records its own pair (`page_edits.editor_model` + `pinned_at`, and
`editor:`/`model:`/`pinned: true` in its library front matter) and stays in the
**document's** `edited-<editor>@<model>` folder — the page file is authoritative
for its own pair, exactly as `pha scan --page N` works for the transcription.

It is **pinned**: a later bulk `pha edit` (or `--reprocess` or a changed
collection config) keeps it and reports `kept N pinned page(s)`; the document row
(`documents.editor`) is never rewritten by a per-page override. The indexer
re-embeds just that page (incremental), so no follow-up command is needed.

A page whose served edit is human-`reviewed` is refused (release with
`pha review --unset --doc N --page P`); naming the page with the CONFIGURED
editor (no override) re-edits it and releases that page's override pin;
`--unpin [--path <doc> [--page P]]` releases the protection (text kept);
`--no-pin` records provenance without pinning; `--dry-run` prints the plan with
no model call. `pha test <doc> --page N --editor X --model Y` previews it without
touching the DB. `pha status` shows `N pinned page edit(s)`, `pha page --edited
--json` reports `page_editor`/`page_editor_model`/`edit_pinned`, and `pha cite`
labels the citation with the pair the text actually came from. An explicit
`--editor X` on `pha page`/`pha cite` still selects a VARIANT FOLDER by name; the
page's own folder is the document's.

---

## 8. Notes (research notes generated from archive queries)

- **`notes/` is a top-level archive folder** (a sibling of `dropbox/`,
  `library/`, `renders/`) holding **Obsidian-compatible markdown notes** generated
  from queries to the archive. It is user-facing research output, **not pipeline
  output**: `pha scan`/`edit`/`encode` never read or write it, and
  `notes/README.md` (seeded automatically, never overwritten) documents the format
  for humans and agents. `pha` creates `notes/` + README in any archive that
  lacks it on its next run (`ensure_dirs`).
- **The notes are the archive's MEMORY, and search indexes them.** A note is a
  conclusion worked out from the sources (an identification, a rejected
  hypothesis), so a question asked today must be able to find the answer
  settled in March. `notes.py` mirrors `notes/` into the `notes` + `notes_fts`
  tables and `pha search` fuses them with pages and records:
  `--source notes` searches notes alone, `--source all` (default) includes
  them, `--source pages|records` excludes them. A note hit is `kind: "note"`
  with `note`/`title`/`path` and **no** `document_id`/`page_no` — it is never a
  document (no `documents` row, no synthetic page, no export, no bundle).
  `pha search` re-indexes changed notes automatically before querying (it
  writes only when something changed), `pha notes index` does it explicitly,
  `pha reindex --source notes|all` adds their embeddings, and `pha status`
  reports `notes: N indexed of M ... (K embedded)`.
- **Never present a note as evidence.** When search returns note hits, offer
  them to the owner ("the archive also has notes on this — should I read
  them?") and, if used, report them as earlier conclusions with their own
  citations, verified against the page where the answer matters. The bundled
  `pha-search-context` skill carries the full workflow (step 0).
- **Agent workflow**: when asked to write a note (e.g. "search the archive for
  'Malaca' and summarize in a note"), search (`pha search`), read each hit's full
  page with `pha page <doc> <page>` (and `--edited` when available) — never
  summarize from a snippet alone — then synthesize into one
  `lowercase-hyphenated.md` in `notes/`, citing every fact to its source.
- **Format**: `[[wikilinks]]` connect notes; `[^n]` footnotes hold citations; YAML
  front matter (`title`, `created`, `tags`, `sources`) is recommended. Otherwise
  keep it plain Markdown so the files render in any viewer.
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

---

## 9. Bibliographic references (sidecars)

A document may carry a full bibliographic reference in a **sidecar beside it**:
`<stem>.dc.json` (JSON — the structured form a human edits), `<stem>.bib`
(BibTeX — the form an agent can draft from the scan, and a human can still edit),
or `<stem>.mods.xml` (MODS 3.8 — a machine interchange format, e.g. what Zotero
exports). A directory-of-images document keeps it *inside* the folder
(`vol04/vol04.dc.json`). All three parse into one record, so the format never
changes how a citation looks. Key spelling is forgiving (`part_number`,
`partNumber` and `dcterms:partNumber` are one field; BibTeX reads `record_origin`
or `recordOrigin`), so a qualified Dublin Core JSON-LD record also works. If more
than one sidecar exists, **JSON wins** (it is the one a human maintains) and
`pha cite` warns.

- **Drafting a reference from a scan is allowed — and must be marked.** Reading a
  title page to write a `.bib` needs no Zotero, which is the point of the format.
  But the entry MUST carry `record_origin = {agent-drafted-unverified}`
  (`pha bib <doc> --to-bibtex --origin agent-drafted-unverified --write`), which
  badges every citation `[unverified reference]` until a human checks it. Never
  remove that marker yourself, and never invent a shelfmark, publisher, volume or
  date: a wrong one reads exactly like a correct one. Only a human may set
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
  reference and `pha bib` the coverage. `pha cite <doc> <page>` renders it; when
  the record is machine-drafted the citation ends `[unverified reference]`.
- **Never state a reference as fact unless it is verified.** Bibliographic data is
  exactly what a model confabulates: a wrong volume number, publisher or
  shelfmark looks like a correct one. An agent may draft a sidecar when the human
  asks, but must set `recordOrigin` to `agent-drafted-unverified`, must not
  invent a shelfmark or an imprint, and must not overwrite a `human-supplied`
  record without saying so. A wrong source record (e.g. a creator that is really
  the holding library) belongs fixed in the source system — Zotero — not silently
  patched in the sidecar.
- **Editing a sidecar never re-transcribes a document.** A reference is metadata:
  it does not touch `sha256`, page text, status or the library version, so
  `pha scan` is not needed to pick it up (`pha cite` and `pha bib` read the file
  live; the DB snapshot that `pha serve`/MCP read is refreshed by `pha scan`,
  `pha reindex`, or any `pha bib` run).
- **Importing from Zotero:** the local API exports MODS —
  `curl "http://localhost:23119/api/users/0/items/<KEY>?format=mods"`. Strip the
  `<note>` elements before saving (they carry the owner's annotations and
  highlights and are ~86% of the bytes) and add a `recordInfo` with the document
  id, the Zotero key and `fetched-from-zotero-unverified`. If more than one
  sidecar is present the JSON wins and `pha cite` warns — resolve it,
  `pha bib --check` reports it.
- Design and rationale: `BIBLIOGRAPHY_PLAN.md`.

---

## 10. Search: from a hit to the full text

- **Search the memory too**: a query about a subject the archive has notes on
  should surface them — `pha search "…" --source notes` (or the default
  `all`), then offer the note to the owner before folding it into an answer.
  See §8.
- **Search → full page text**: `pha search` hits carry a `page_file` (the library
  `.md`) and the CLI prints that path plus a shortcut under each hit.
  `pha page <doc> <page> [--edited]` prints one page's FULL text (raw, or the
  edited/translated variant with `--edited`); `<doc>` is an id or filename
  substring, and `--json` gives agent-friendly output. Use it to read the context
  around a search snippet.
- **Search → structured records and notes too**: `--source pages|records|notes|all` (default
  `all`) searches page text, the encoder records *and* `notes/` (the archive's
  memory — see §8). Narrow with
  `--encoder <id>` / `--record-kind <kind>` (`letter`, `person`, …), and match
  **exact** record fields with `--field NAME=VALUE` (repeatable; alias
  `--where`; `--ignore-case` for ASCII case-insensitive values). With records,
  the text query may be OMITTED when at least one `--field` is given, so
  `pha search --source records --field place=Malaca` lists every record with that
  value — this is how you answer "every letter sent by X" without guessing
  wording. Record *semantic* search needs `pha reindex --source records`
  (`record_embeddings`); keyword record search works without it. With
  `--source all`, every hit carries **`kind`** — `"page"` or `"record"` — so
  filter on that, not on the query. A record hit carries `record_kind` (the
  class), `encoder`, the document, the page it starts on (`page_no`, resolved
  from `page_start` then `page`/`start_page`/`source_page`) and **`data`**, the
  record's own fields. MCP: `pha_search(source="records", record_kind=…,
  encoder=…, fields={…})`. A record is not a page — to quote the passage, open
  the page it points at.

---

## 11. Remote / machine-to-machine

Detail lives in `MULTI_COMPUTER.md` — written for the owner in plain language,
with an **Operator reference** half (from "Operator reference" onward) carrying the
command-level coverage of bundles, hand-over, the dsh-pha plugin, MCP and the
`pha`-not-on-PATH trace.

Starting points:

- **MCP server** runs on the machine that owns the dropbox and models; a client
  on another machine needs only an MCP connection. `pha mcp --transport sse
  --host <LAN-IP> --port 8000` (no auth — use a private LAN/VPN/SSH tunnel); see
  `MCP_CLIENTS.md`.
- **`pha` not on PATH?** Look in the archive first: `<archive>/pha-location.md`
  (and `.pha/location.json`) records where the tool lives on THIS machine,
  including the PATH-proof interpreter form
  `PHA_ARCHIVE_DIR=<archive> <python> -m personal_historical_archive status`.
  Never commit it, and never let a copied archive inherit it.
- **"No pha archive is configured or found"** — a fresh install with no
  archive_dir. Don't guess: either point it at the real archive
  (`pha set archive-dir <path>`) or create one (`pha init-archive ~/pha-home &&
  pha set archive-dir ~/pha-home`). It stops rather than silently running against
  an empty default DB.
- **Upload documents** from another machine via
  `pha_upload(kind, name, content_b64)` (base64; single files per call), then
  `pha_scan_now()`.
