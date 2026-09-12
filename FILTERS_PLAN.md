# Implementation plan — pha stage filters (artifact filters included; merges the encoder-tools runner)

Design doc for the enhancement request tracked at
`enhancements/pha-filters-enhancement-request.md`, **re-verified against pha
0.16.1** (2026-09). No code written yet.

This plan **supersedes `ENCODER_TOOLS_PLAN.md`** (owner ruling DEC-5): the
"bundled tools" runner is absorbed into the filter framework — a filter is a
pipeline unit that may do arbitrary work, including writing files — and the
records→markdown case becomes the first **artifact filter**. What carries
over from the encoder-tools plan: the **context contract**, the **stamp
staleness** design (including edited-page tracking) and the
**overwrite-in-place** policy. The former phase C (structure prescan → `pages:`
indirection, prompt injection) remains a separate follow-up.

Scope of THIS plan: the filter framework end to end (config, resolution,
contract, hooks, staleness, provenance, CLI, tests) **plus the first
reference filters**, including `markdown-from-records`. Proposal §3.1–3.3
(model-assisted detection, character-aware chunking, raw/edited
cross-reference) are independent and not covered here.

Anchors name **functions/regions, not line numbers**; current positions are
kept once in §6 as an informational map.

## 1. Decisions recorded (archive-owner rulings)

- **DEC-1 — Invocation.** pha runs a filter as a **subprocess** with a JSON
  envelope on disk: it writes `--input <file>` and reads `--output <file>`
  (falling back to stdout). Default command is
  `[sys.executable, <filter>/filter.py, --input, …, --output, …]`; the
  manifest's `command:` overrides it. Python filters get a tiny helper
  module so the script is just `def run(value, ctx)` (see §2.4).
- **DEC-2 — Language.** Python by convention (the helper + default command);
  any executable/interpreter via the manifest `command:` escape hatch.
- **DEC-3 — Registry (one directory).** Filters live in **one
  archive-level `filters/` directory** (a sibling of `palaeographers/`,
  `editors/`, `encoders/`, `models/`; the archive is the self-contained data
  root). **Which filters run, at which stage and hook, with which params, is
  set in `pha.yaml`.** There is no collection-local tier and no separate
  builtin resolution: reference filters ship as files/templates placed in
  `filters/` (repo template + `pha init-archive`/`ensure_dirs` seeding), and
  a collection adopts one by referencing it in its sidecar.
- **DEC-4 — Declared inputs.** A filter may declare `inputs:` (globs/paths,
  e.g. a settings file or an abbreviation table). pha watches their mtimes
  for invalidation and includes them in provenance hashes. Files a filter
  reads without declaring are simply not watched (documented).
- **DEC-5 — Merge.** This plan absorbs the encoder-tools runner;
  `ENCODER_TOOLS_PLAN.md` is superseded (kept for history). Artifact filters
  keep the runner's context JSON, stamp staleness (tool sources ∪ edited-page
  mtimes) and overwrite-in-place semantics.

## 2. Model and contract

### 2.1 A filter is a pipeline unit

A stage's pipeline becomes:

```
palaeographer:  page image ─▶ [model/engine] ─▶ (post filters) ─▶ raw text
editor:         raw text ─▶ (pre filters) ─▶ [model] ─▶ (post filters) ─▶ edited text
encoder:        edited text ─▶ (pre filters) ─▶ [model] ─▶ records ─▶ (post filters) ─▶ records
```

Each filter is handed the **value produced by the previous unit** plus a
**context**, and returns the value for the next unit. It may do arbitrary
work on the side: write files, read declared settings/auxiliary files, call
libraries. Several filters run at a hook, in listed order.

| hook | value in | typical use |
|---|---|---|
| `palaeographer.post` | `text` (one page's raw text) | OCR cleanup, page-separator strip, margin line-numbers |
| `editor.pre` | `text` (one page's raw text) | shape the transcription before the model |
| `editor.post` | `text` (one page's edited text) | clean the model's output before storing |
| `encoder.pre` | `text` (whole document, `--- page N ---`) | normalise the concatenated input before extraction |
| `encoder.post` | `records` (parsed, per encoder) | artifacts (markdown), record enrichment/normalisation |

`palaeographer.pre` does not exist: its input is an image, and image
preparation is a `render` concern (draft §2.1/§8).

**Pass-through rule.** A filter that returns **no value** leaves the incoming
value unchanged for the next unit. This is how artifact filters fit: a filter
may consume the value, write files, and return nothing.

### 2.2 Envelope (subprocess contract)

pha writes `--input <file>`:

```json
{
  "kind": "text" | "records",
  "value": "<string>" | [ {"<class>": "…", "<class>_attributes": {…}}, … ],
  "context": { … §2.3 … }
}
```

and reads `--output <file>` (preferred) or stdout:

```json
{ "kind": "text" | "records", "value": … }
```

or an empty/absent result (pass-through). Rules:
- the returned `kind` must match what the hook expects (`encoder.post` may
  return `records` or nothing; text hooks `text` or nothing);
- a filter declared `returns: none` must not return a value;
- `accepts:` in the manifest is validated against the hook at load time;
- large values (whole-document text) travel as files, not argv or stdin
  pipes — Windows-safe and lets a human replay a run.

### 2.3 Context

```json
{
  "archive_dir": "…", "document": "/…/dropbox/…/VOL.pdf",
  "document_id": 19, "filename": "VOL.pdf", "collection": "collections/documenta-indica",
  "stage": "encoder", "hook": "post", "encoder": "documents",
  "page": 141, "source_name": null,
  "library_dir": "…/library/<slug>",
  "pages_dir_raw": "…/transcription-gemma-4-e4b-it@gemma-4-e4b-it",
  "pages_dir_edited": "…/edited-latin-to-english@deepseek-v4-flash",
  "records_file": "…/library/<slug>/records-documents.json",
  "concatenated_file": "…/library/<slug>/concatenated-documents.md",
  "sidecar": "…/dropbox/collections/documenta-indica/pha.yaml",
  "params": { … manifested defaults overridden by the sidecar … },
  "inputs": { "<declared id or path>": "<resolved path>" }
}
```

Page-level fields (`page`, `source_name`) are present only for
palaeographer/editor hooks; `encoder` only for encoder hooks; a field that
does not exist is `null` (e.g. no edited variant yet).

### 2.4 Python helper (DEC-1/DEC-2)

`personal_historical_archive.filter_api` gives filters the ergonomic form:

```python
from personal_historical_archive.filter_api import run

def run(value, ctx):
    # value: str or list[dict]; ctx: dict (§2.3)
    ...            # side effects allowed (write files, read ctx["inputs"])
    return value   # or None for pass-through
```

`filter.py` may either use the helper (pha calls `run` in a subprocess via
the documented CLI shim `python -m personal_historical_archive.filter_api
--input … --output …`) or read the envelope itself — the envelope is the
contract, the helper is convenience.

### 2.5 Manifest (`filters/<name>/filter.md`)

```markdown
---
name: markdown-from-records      # must equal the directory name (warning only)
description: one markdown file per record, built from the edited pages
accepts: records                 # text | records | any
returns: none                    # text | records | none
command: []                      # optional argv override (else python filter.py)
timeout_s: 1800
inputs:                          # files/dirs watched for invalidation (DEC-4)
  - "settings/*.yaml"
params:                          # defaults; overridden by pha.yaml
  out_dir: segments-documents
  page_marker: "--- page N ---"
  split_shared_pages: true
---
Human-readable description (shown by `pha filters`).
```

A directory without `filter.md` uses the convention `<name>/filter.py`.
Directories/files starting with `_` are ignored (consistent with the other
definition folders). A referenced-but-undefined filter is a **load-time
error** (draft §2.1).

### 2.6 Params, failure, security, provenance

- **Params.** Manifest `params:` defaults, overridden by the sidecar's
  `{name, params}` form; merged dict is `context.params`.
- **Failure.** Non-zero exit or exception → that unit fails for that page /
  document / encoder, the error is recorded, and **nothing partially
  filtered is stored** (draft §2.1). Captured stdout/stderr tail is reported.
  Artifact writes are not transactional: reference filters write to a temp
  file + rename; pha records failures.
- **Security.** Filters are arbitrary code supplied by the archive owner
  (same trust as prompt files). **No sandbox**; documented in README/AGENTS
  and in the sample.
- **Provenance.** The producing stage records which filters ran with short
  content hashes (script + manifest + declared inputs): page files gain
  `filters:` in their front matter (scan/edit stages); the records file gains
  a `"filters"` block (encode stage); artifact filters write a stamp (below).
  This makes "why is this text like this" answerable and staleness checkable.

## 3. Configuration

`pha.yaml` stage blocks gain `pre:` / `post:` lists; a filter is a bare id or
an object with params. Encoder list items get them too (one chain per
encoder):

```yaml
palaeographer:
  rules: ocr
  model: liteparse
  post: [strip-ocr-page-separator]
editor:
  rules: latin-to-english
  model: deepseek-v4-flash
  pre:
    - line-numbers
    - {name: collapse-whitespace}
  post: [join-hyphenated-words]
encoders:
  - rules: documents
    model: deepseek-v4-flash
    post:
      - {name: markdown-from-records, params: {out_dir: segments-documents}}
  - rules: apparatus
    model: deepseek-v4-flash
    post:
      - {name: markdown-from-records, params: {out_dir: segments-apparatus}}
```

Resolution: `filters/<name>/` in the archive only (DEC-3). Order = listed
order. Empty/absent lists = no-op. The schema
(`schema/pha-sidecar.schema.json`) gains the two arrays inside the
palaeographer/editor stage objects and the encoder item objects (they are
`additionalProperties: false` today, so this is required for editor tooling).

## 4. Staleness and re-run

Filters extend the existing mtime-based staleness; editing a filter is
editing the stage's rules.

| change | re-runs |
|---|---|
| `palaeographer.post` script/manifest/inputs changed, or provenance hash differs | the palaeographer stage for affected pages (reviewed pages stay untouched) |
| `editor.pre`/`editor.post` changed | `pha edit` for affected pages |
| `encoder.pre` changed | `pha encode` (records re-extracted) |
| `encoder.post` changed | `pha encode`; artifact filters re-run when their **stamp** is stale |
| a filter's declared input changed | the same as above (its stage) |
| `--reprocess` | forces the pass |

**Artifact stamp (carried over from the encoder-tools runner).** For an
`encoder.post` filter that writes files, pha keeps
`library/<slug>/.filter-stamps/<encoder>.<filter>.stamp` holding the newest
mtime over (filter script/manifest ∪ declared inputs ∪ **edited-page files
under `pages_dir_edited`**). A newer input marks the filter stale, so:

- a tool/param edit re-runs only the filter (never the model pass);
- a historian's correction to an edited page re-materialises the affected
  artifacts on the next `pha encode`, imported or not (the filter reads the
  page files);
- a failed run writes no stamp → retried next time.

## 5. Reference filters (first batch)

Seeded into the archive's `filters/` (repo ships the templates; `ensure_dirs`
creates the directory and seeds `_sample/`; a collection adopts a filter by
naming it in its sidecar — DEC-3):

| id | hook | does |
|---|---|---|
| `strip-ocr-page-separator` | text pre/post | removes the engine's synthetic `--- Page N ---` line |
| `collapse-whitespace` | text pre | collapses justified-print space runs (keeps blank lines / foliation) **and reduces spaced-dot runs (index dotted leaders, `. . . . .`) to a single space; a real ellipsis `...` is preserved** |
| `footnote-marker-residue` | text pre | removes stray floating `*`/`**`/`°`/`º` superscript residue; keeps footnote blocks and digit refs |
| `line-numbers` | text pre | MHSI margin line-numbers → `[l. N]` (whole-document aware) |
| `join-hyphenated-words` | text post | re-joins a word split across a line break: (a) `X-` + lowercase continuation → drop the hyphen (`gover-`/`nador` → `governador`); (b) hyphen at BOTH the end of one line and the start of the next (`X-`/`-Y`) → one word keeping ONE internal hyphen (`Dizer-`/`-vos` → `Dizer-vos`); (c) Portuguese enclitic/mesoclitic pronouns keep their hyphen (`encarecer-vo-`/`s` → `encarecer-vos`) |
| **`markdown-from-records`** | **encoder.post** | **one Markdown file per record, built from the edited pages (artifact)** |

### `markdown-from-records` (the merged encoder-tools deliverable)

Port the validated reference script
(`dropbox/collections/documenta-indica/encoders/_tools/markdown-from-records/markdown_from_records.py`,
235 lines, stdlib) into `filters/markdown-from-records/` with the manifest
`accepts: records`, `returns: none`. It uses the context
(`records_file`, `pages_dir_edited`, `library_dir`) and its params
(`out_dir`, `page_marker`, `footnote_policy`, `normalize_leaders`,
`split_shared_pages`), and writes `library_dir/<out_dir>/…` —
**deterministic names, overwritten in place** (DEC-5); artifacts are
regenerated files, the human review surface stays the library edited pages.
A **shared library-page reader** lands in `ingest.py` (page md → body with
YAML front matter and any trailing `## Notes` block stripped) and the
`library_page_path` first-match loop learns to prefer the `@model` run folder.

**Correctness requirement.** Records **span pages** and a record may **start
mid-page** (normal in historical source editions). The reference already
handles this: `page_chunks` infers `page_end` as the page before the next
record's `page_start` (or the last page); `find_boundary` uses the record's
`line_start`, falling back to a header search, to split a shared page so the
top stays with the previous document. Port it faithfully and test explicitly:
(i) a record spanning several pages, (ii) the next record starting mid-page
(`line_start` set), (iii) the last record ending at the volume's final page,
(iv) two records sharing one page, (v) no `line_start`, header found by
search.

The documenta-indica encoders' current inert `tools:` front matter migrates
to `encoder.post: [markdown-from-records]` (per encoder, with `params.out_dir`).

## 6. File-by-file changes

Informational anchor map (pha 0.16.1): `sidecar._normalize_stage` 50;
`config.Config.ensure_dirs` 610; `ingest.write_transcription_pages` ~837
(front matter at ~850, `db.set_page_result(...)` in `scan_once` ~752);
`ingest.write_edited_pages` 929; `ingest.edit_document` 1290;
`ingest.encode_document` 1724; `cli` parsers ~1747–1771;
`archive_init` dirs ~394–409; `bundle` encoders copy ~229–239.

- **`sidecar.py`** — `StageSpec` gains `pre`/`post: list[FilterSpec]`;
  `_normalize_stage` parses a bare id string or `{name, params}`; encoder
  list items carry the same.
- **`schema/pha-sidecar.schema.json`** — add `pre`/`post` arrays (id or
  `{name, params}`) to the palaeographer/editor stage objects and the encoder
  item object.
- **`config.py`** — add `filters_dir` (archive-level) to `Config`/paths;
  `ensure_dirs` creates `filters/` and seeds `_sample/`; sample constants
  (`_FILTER_SAMPLE`, `_FILTER_API` doc).
- **new `src/personal_historical_archive/filters.py`** — `FilterSpec`,
  discovery + manifest parse, envelope IO, `run_filter` (subprocess +
  timeout + output tail), `apply_filters(value, specs, ctx)` chain helper,
  provenance hashes, artifact stamp read/write. Imports `config`/stdlib only.
- **new `src/personal_historical_archive/filter_api.py`** — the helper
  (`run(value, ctx)` shim, envelope codec) filters import.
- **`ingest.py`** — apply `palaeographer.post` in `scan_once` before
  `db.set_page_result`; `editor.pre`/`editor.post` in `edit_document`
  (before/after the model call); `encoder.pre` on the concatenated text and
  `encoder.post` on the parsed records in `encode_document`; record
  `filters:` provenance in the page/records front matter; artifact stamps
  under `library/<slug>/.filter-stamps/`; shared library-page reader (above).
- **`cli.py`** — `pha filters` (list id + description + hook-agnostic);
  `pha filter <id> [--params k=v] [--in FILE]` to run one filter on text
  (authoring/testing, draft §6); show chains in `pha prompts`/`pha encoder`
  output. Existing `pel`/`encoder` parsers extended.
- **`archive_init.py`** — create `filters/` (+ seed sample) in a new archive.
- **`bundle.py`** — carry the filter directories referenced by a bundled
  collection's stages into `defs/filters/` (archive-level filters otherwise
  do not travel with a bundle); `unbundle` restores them.
- **`doctor.py`** (optional) — report configured filters that are missing or
  not executable.
- **Docs** — README "Stage filters" section (contract, hooks, params,
  staleness, security); AGENTS.md layout + conventions (filters are
  archive-owner code, no sandbox; a filter edit re-runs its stage only);
  HISTORIANS_README plain-language note; `enhancements/…INDEX.md`.

## 7. Test plan (`tests/test_filters.py` + stage integration)

1. Contract: envelope → filter → envelope round-trip; pass-through on empty
   result; wrong `kind` rejected; `accepts`/`returns` validation; missing
   filter is a load error.
2. Params: manifest defaults < sidecar override; declared `inputs` resolved
   into `context.inputs`.
3. Chain: order honoured for multiple filters; a failing filter aborts the
   unit, records an error, stores nothing.
4. Stage hooks: `palaeographer.post` applied at scan (and re-scan when the
   filter changes, reviewed pages untouched); `editor.pre`/`post` around the
   model; `encoder.pre` on the concatenated text; `encoder.post` on records
   (model calls monkeypatched).
5. Artifact filter end-to-end: a fake filter writes a file to `out_dir`;
   stamp prevents a second run; touching the script, a declared input, or an
   edited page re-runs it; non-zero exit → failure, no stamp, records intact.
6. `markdown-from-records` reconstruction vectors (i)–(v) in §5, plus
   overwrite-in-place on re-run.
7. CLI: `pha filters` lists; `pha filter <id>` transforms text.
8. Back-compat: sidecars without `pre`/`post` behave exactly as today;
   schema validates both forms; non-adopting collections are unchanged.
9. Built-in acceptance vectors from the reference use case (ER §4.1):
   `collapse-whitespace` — justified space runs collapsed to one space; index
   dotted leaders (`. . . . .`) reduced to a single space; a real ellipsis
   (`...`, no spaces) preserved; blank lines and `[3v]`/`[l. 10]` foliation
   untouched. `join-hyphenated-words` — a soft-hyphen split joined without the
   hyphen (`gover-`/`nador` → `governador`); the doubled form joined keeping ONE
   hyphen (`Dizer-`/`-vos` → `Dizer-vos`, `del-`/`-rei` → `del-rei`); a
   Portuguese enclitic keeps its hyphen (`encarecer-vo-`/`s` →
   `encarecer-vos`). `strip-ocr-page-separator` — the `--- Page N ---` line
   removed and nothing else touched.

Run: `.venv/bin/python -m pytest tests/test_filters.py` + the existing
`test_ingest.py`/`test_testrun.py`/`test_sidecar.py`/`test_config.py`
suites (no regressions).

## 8. Risks / compat notes

- **Per-page subprocess cost.** Scan/edit run filters per page; Python
  startup (~30-50 ms per invocation) over a 1 000-page book × several
  filters is minutes. Mitigations to consider at implementation time:
  encourage combining filters, or a small worker/keep-alive mode in
  `filters.py`. Flagged, not designed here.
- **Runtime tolerance.** Today `pre`/`post` in `pha.yaml` are ignored by the
  runtime parser and rejected by the JSON schema; after this lands, old
  archives are unaffected and the schema accepts them.
- **Reviewed pages.** A filter-change re-run must respect `reviewed` stamps
  exactly as the model stages do (never overwrite a human correction).
- **Declared-only watching.** Undeclared file reads are not watched — a
  filter whose behaviour depends on a file it did not declare can go stale
  silently. Documented in the manifest guidance.
- **Artifact atomicity.** Non-transactional writes; reference filters write
  temp + rename; a crashed filter may leave a stray file (reported).
- **Bundle.** Archive-level filters do not travel automatically; the bundle
  extension in §6 covers referenced ones only.

## 9. Later / out of scope

- **Structure prescan → layout injection** (former encoder-tools phase C):
  `structure:` per document, cached register, `pages: "@documents"`
  indirection, prompt block injection.
- **Model-backed filters** (discouraged; define lock/retry semantics if
  adopted) and **image pre-filters** (a `render` concern).
- Indexing artifacts for search / linking them to `notes/`.
- Proposal §3.1–3.3 encoder-stage internals (model-assisted detection,
  character-aware chunking, raw/edited cross-reference).
