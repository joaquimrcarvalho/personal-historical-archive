# Implementation plan — pha stage filters (artifact filters included; merges the encoder-tools runner)

Design doc for the enhancement request tracked at
`enhancements/pha-filters-enhancement-request.md`, **re-verified against pha
0.16.1** (2026-09).

> ### Status: IMPLEMENTED (0.20.x, 2026-09)
>
> The framework and all six reference filters are in the tree, in two commits:
> `6829dbf` (framework + reference filters) and `b55889a` (staleness +
> provenance). `FILTERS_PLAN.md` is kept as the design record; the sections
> below are annotated with **IMPLEMENTED** / **DEVIATION** where reality
> differs from the plan, and §10 lists what remains open.
>
> Deviations from this document, all recorded in place below:
> 1. **DEC-1 is superseded** — Python filters run **in-process**, not as a
>    subprocess per page (the §8 cost risk, resolved). `command:` still takes
>    the documented subprocess path.
> 2. **Staleness is signature-based, not mtime-based** — the applied chain is
>    stored and compared, which also catches changed params and a removed
>    filter (a pure mtime watch cannot).
> 3. **No shared library-page reader in `ingest.py`** — the one consumer
>    (`markdown-from-records`) owns its reader.
> 4. **`pha prompts` / `pha encoder` do not yet print the filter chains.**
> 5. The repo ships the reference filters as **files** under `filters/`
>    (templates to copy into an archive), matching DEC-3; the archive's own
>    `filters/_sample/` is seeded by `ensure_dirs` and `init-archive`.

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

  > **SUPERSEDED at implementation (§8).** The envelope IS the contract and is
  > unchanged, but pha does not spend a process per page on it: a Python filter
  > (`filter.py`, no manifest `command:`) is loaded **in-process** and its
  > `run(value, ctx)` called directly. Filters are the archive owner's code,
  > with the same trust level as a prompt file, and an interpreter start per
  > page is minutes on a real volume. A manifest `command:` (any language) or
  > `run_filter(..., subprocess_only=True)` uses the subprocess path described
  > here. Both paths read/write the same envelope, so a filter is portable
  > between them — and `filter_api.load_run` compiles the source directly on
  > the subprocess side too (see the `__pycache__` note in §8).
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
- **Provenance.** **IMPLEMENTED, with one addition the plan did not have: the
  same signature is also the STALENESS mechanism.** The producing stage
  records which filters ran as a signature —
  `<name>:<sha>:<params>` joined per filter, where `sha` is the short content
  hash of the filter's files (script + manifest) and its declared `inputs:` —
  and **stores it in the database**: a `filters` column on `pages`,
  `page_edits` and `records` (migrated in place, `db.migrate`). That makes
  "did the chain that produced this value change?" an exact comparison rather
  than an mtime guess (see §4). Alongside it, the human-facing artifacts carry
  it too: page files gain `filters:` in their front matter (scan/edit stages),
  the records file gains a `"filters"` block (encode stage), and artifact
  filters write a stamp (below). This makes "why is this text like this"
  answerable and staleness checkable.

  A **hand-corrected page clears it** (`mark_page_reviewed` /
  `mark_edit_reviewed` set `filters = NULL`): the stored text is the
  historian's, not the output of the configured chain, and claiming otherwise
  would be a lie in the artifact. Note the library FILE keeps its last-written
  `filters:` line until the next export — `pha review` imports text without
  rewriting page files, precisely so mtimes stay untouched and the correction
  is not re-imported.

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

**IMPLEMENTED — but by SIGNATURE, not mtime.** The plan said "filters extend
the existing mtime-based staleness". That was replaced during implementation,
because mtime cannot express two things that matter:

- a filter whose **params changed in `pha.yaml`** (the script is untouched, so
  no mtime moves);
- a filter that was **removed from the chain** (nothing runs, so nothing is
  there to notice).

So the applied chain is recorded as a signature on the produced value (§2.6)
and the next pass compares it to the chain configured *now*. That comparison
is exact and covers edits, param changes and add/remove alike. Editing a
filter is still conceptually "editing the stage's rules"; it is just measured
by content instead of timestamp.

| change | re-runs | where the check lives |
|---|---|---|
| `palaeographer.post` script/manifest/inputs or params changed, or the filter was added/removed | the palaeographer stage for affected pages (reviewed pages stay untouched) | `_stored_page_filters` + `_configured_filters_signature` per page, and once per document so the "unchanged" early return cannot skip the loop |
| `editor.pre`/`editor.post` changed | `pha edit` for affected pages | `_edit_needed(expected_filters=…)` |
| `encoder.pre` (or a non-artifact `encoder.post`) changed | `pha encode` (records re-extracted) | `_encode_needed(expected_filters=…)` |
| an **artifact** filter changed | only the artifact filter re-runs; the model pass does not | its **stamp** (below) |
| a filter's declared input changed | the same as above (its stage) | included in the signature hash |
| `--reprocess` | forces the pass | unchanged |

Only **value-shaping** filters (all `pre`, plus non-artifact `post`) enter the
encoder's recorded signature: an artifact filter writes files and does not
change what is stored, so letting it into the signature would re-encode on
every artifact edit for nothing.

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

> **IMPLEMENTED with the params trimmed and the reader NOT shared.**
> Shipped params are `out_dir` (default `segments`), `strip_notes` and
> `write_index`; the other three named above were not needed by the shipped
> implementation (page markers are not stripped from record bodies, leaders
> are not normalised here, and shared pages are handled by the span
> inference below rather than a flag). **DEVIATION:** there is no shared
> library-page reader in `ingest.py` — `markdown-from-records` has its own
> small `_read_page_body` (front matter and a trailing `## Notes` block
> stripped). `library_page_path` keeps its existing first-match behaviour;
> preferring the `@model` run folder there was not done. If a second
> consumer appears, promote the reader then — one caller does not justify a
> shared abstraction.

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

The anchor map below was written against 0.16.1 and is now HISTORICAL — the
positions drifted with the implementation (and with unrelated work), so treat
the markdown file names as the map and the numbers as approximate.

Each item is annotated with what actually landed:

- **`sidecar.py`** — `StageSpec` gains `pre`/`post: list[FilterSpec]`;
  `_normalize_stage` parses a bare id string or `{name, params}`; encoder
  list items carry the same. **DONE** (`_normalize_filter` /
  `_normalize_filter_list`). A stage that names no filter yields `[]`, so a
  sidecar without `pre`/`post` is byte-for-byte equivalent to before.
- **`schema/pha-sidecar.schema.json`** — add `pre`/`post` arrays (id or
  `{name, params}`) to the palaeographer/editor stage objects and the encoder
  item object. **DONE**, via a shared `$defs/filterRef` so all three stages
  validate identically.
- **`config.py`** — add `filters_dir` (archive-level) to `Config`/paths;
  `ensure_dirs` creates `filters/` and seeds `_sample/`; sample constants.
  **DONE.** The sample text lives in `filters.py`
  (`FILTER_SAMPLE_MD`/`FILTER_SAMPLE_PY`) rather than in `config.py`, and
  seeding happens through `Config._seed_filter_sample()` (called by
  `ensure_dirs`) and again in `archive_init.init_archive`.
- **new `src/personal_historical_archive/filters.py`** — `FilterSpec`,
  discovery + manifest parse, envelope IO, `run_filter`, `apply_filters(value,
  specs, ctx)`, provenance hashes, artifact stamp read/write. **DONE.** It
  does NOT import `config` (callers pass `filters_dir`), so `config.py` can
  import it for the sample without a cycle. Adds `filters_signature` /
  `filters_changed` (the §4 staleness mechanism) and `validate_for_hook`.
- **new `src/personal_historical_archive/filter_api.py`** — the helper
  (`run(value, ctx)` shim, envelope codec) filters import. **DONE**, plus the
  `python -m personal_historical_archive.filter_api` shim used by the
  subprocess path.
- **`ingest.py`** — apply `palaeographer.post` in `scan_once` before
  `db.set_page_result`; `editor.pre`/`editor.post` in `edit_document`
  (before/after the model call); `encoder.pre` on the concatenated text and
  `encoder.post` on the parsed records in `encode_document`; record
  provenance; artifact stamps under `library/<slug>/.filter-stamps/`.
  **DONE**, all five hooks, plus:
  - `_run_stage_filters` (context + chain, `return_ran=True` for provenance)
  - `_configured_filters_signature` / `_stored_page_filters` (staleness)
  - `_encoder_stage_for`, `_is_artifact`, `_artifact_due`, `_split_page_blocks`
    (re-splitting filtered whole-document text back into page pairs, so
    `encoder.pre` can work document-wide without losing page grounding)
  - `mark_page_reviewed` / `mark_edit_reviewed` clear the provenance
  - `_library_dir_for` / `_pages_dir_for` / `_stage_filter_dirs` (context)
  - `encoder.post` runs **after** the records file is written, so an artifact
    filter can read the file it consumes.
  NOT done here: the shared library-page reader (§5).
- **`cli.py`** — `pha filters`; `pha filter <id> …`. **DONE** (`cmd_filters`
  with `--json`, `cmd_filter` with
  `--input/--hook/--params/--ctx/--doc/--json`; the flag is `--input`, not
  `--in`). **NOT done:** showing the chains in `pha prompts`/`pha encoder`
  output — `pha filters` is the discovery surface today.
- **`archive_init.py`** — create `filters/` (+ seed sample) in a new archive.
  **DONE.**
- **`bundle.py`** — carry the filter directories referenced by a bundled
  collection's stages into `defs/filters/`; `unbundle` restores them.
  **DONE** (`_filter_specs_for` collects the names from the sidecar in scope;
  `_install_defs` copies directories and never overwrites an existing filter).
- **`doctor.py`** (optional) — report configured filters that are missing or
  not executable. **NOT done.** A referenced-but-missing filter is a load-time
  `FilterError` at run time, and `_configured_filters_signature` folds a
  `"missing"` marker into the signature so the stage re-runs once it is fixed.
- **Docs** — README "Stage filters" section (contract, hooks, params,
  staleness, security); AGENTS.md layout + conventions.
  **DONE** for README and AGENTS.md (including the `filters:` front-matter
  provenance and the unchanged-by-default guarantee). Also updated with this
  revision: `enhancements/pha-enhancement-requests-INDEX.md`, the original
  enhancement request, and the encoder-tools request (artifact half done,
  prescan still open). No `HISTORIANS_README` note: historians neither author
  nor configure filters, and their workflow is unchanged.

## 7. Test plan (`tests/test_filters.py` + stage integration)

*(Planned as one file; shipped as `test_filters.py` + `test_filter_hooks.py`
— see the notes at the end of this section.)*

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

**IMPLEMENTED as two files** (the split keeps framework tests away from
pipeline fixtures):

- `tests/test_filters.py` — §7 items 1–3, 7, 8 and 9: envelope
  round-trip/pass-through/kind-mismatch, `accepts`/`returns` validation,
  manifest errors, chains in order, a failing filter aborting the unit, param
  merge, the documented context keys, the subprocess `command:` path,
  provenance-hash change, artifact stamp lifecycle, the CLI-facing helpers,
  sidecar back-compat, and every acceptance vector in item 9 verbatim.
- `tests/test_filter_hooks.py` (10 tests) — §7 items 4 and 5 plus
  staleness/provenance: each hook driven through the REAL
  `scan_once`/`edit_document`/`encode_document` with the model stubbed
  (asserting the model saw the `pre`-filtered text and the stored value
  carries `post`), a failing filter storing no edit and never calling the
  model, an artifact filter writing files and leaving a stamp, the
  filter-change staleness for all three stages (including the unit-level
  `_edit_needed` decision and reviewed-page precedence), the front-matter
  provenance, review clearing it, and the same-second same-size reload
  regression.

Not covered by explicit vectors: item 6's (i)–(v) `markdown-from-records`
reconstruction cases as named — the span/shared-page inference is implemented
and exercised indirectly, but there is no dedicated test per case. That is the
most valuable gap to close before relying on the artifact filter in anger.

**Two bugs the tests caught**, both worth knowing about:

1. A filter **edited twice within one filesystem timestamp tick, to the same
   size**, left a stale `__pycache__` `.pyc` whose `(mtime, size)` still
   matched — and the import machinery executed the OLD code. This surfaced
   only in the ordered full-suite run, and it broke exactly the promise
   staleness makes. `_load_module` and `filter_api.load_run` now `compile()`
   the source bytes directly, so no bytecode cache is consulted.
2. The document-level "unchanged" early return in `scan_once` ran BEFORE the
   per-page loop, so a filter-only change was skipped without ever reaching
   the per-page check. The document now consults
   `_stored_page_filters` too.

## 8. Risks / compat notes

- **Per-page subprocess cost.** Scan/edit run filters per page; Python
  startup (~30-50 ms per invocation) over a 1 000-page book × several
  filters is minutes. Mitigations to consider at implementation time:
  encourage combining filters, or a small worker/keep-alive mode in
  `filters.py`. Flagged, not designed here.

  > **RESOLVED — in-process Python filters (DEC-1 superseded).** A Python
  > filter is loaded once and called directly, so the per-page cost is a
  > function call plus the filter's own work; there is no interpreter start
  > and no envelope serialisation on that path. Measured cost is therefore the
  > filter's algorithm, not pha's plumbing — which is why the plan's
  > "combine filters or build a worker" mitigation was not needed. A manifest
  > `command:` still pays the subprocess cost, deliberately, because that is
  > the escape hatch for another language. The process-global module cache is
  > bounded by (filter × distinct source version), and a long scan picks up a
  > mid-run edit on its next call (content-checked, see the regression above).
- **Runtime tolerance.** Today `pre`/`post` in `pha.yaml` are ignored by the
  runtime parser and rejected by the JSON schema; after this lands, old
  archives are unaffected and the schema accepts them. **VERIFIED:** a stage
  with no `pre`/`post` parses to empty lists, and §7 item 8 is tested both
  ways.
- **Reviewed pages.** A filter-change re-run must respect `reviewed` stamps
  exactly as the model stages do (never overwrite a human correction).
  **HONOURED, and now asserted:** `_edit_needed` still returns False for a
  reviewed edit *before* consulting the filter signature, `scan_once` skips
  reviewed pages before the filter check, and a reviewed page's stored
  provenance is cleared (it is the human's text).
- **Declared-only watching.** Undeclared file reads are not watched — a
  filter whose behaviour depends on a file it did not declare can go stale
  silently. Documented in the manifest guidance. **Unchanged.**
- **Artifact atomicity.** Non-transactional writes; reference filters write
  temp + rename; a crashed filter may leave a stray file (reported).
  **Honoured by `markdown-from-records`** (`_atomic_write`), and a failed
  artifact run writes no stamp, so it is retried rather than recorded as done.
- **Bundle.** Archive-level filters do not travel automatically; the bundle
  extension in §6 covers referenced ones only. **As designed** — a filter
  named by a bundled collection's sidecar is copied and installed; one used
  some other way is not.

## 9. Later / out of scope

- **Structure prescan → layout injection** (former encoder-tools phase C):
  `structure:` per document, cached register, `pages: "@documents"`
  indirection, prompt block injection.
- **Model-backed filters** (discouraged; define lock/retry semantics if
  adopted) and **image pre-filters** (a `render` concern).
- Indexing artifacts for search / linking them to `notes/`.
- Proposal §3.1–3.3 encoder-stage internals (model-assisted detection,
  character-aware chunking, raw/edited cross-reference).

## 10. Open after implementation

Small, concrete, none of them blocking:

1. **`markdown-from-records` reconstruction tests (i)–(v)** — the plan's §5
   correctness requirement is implemented but not pinned case-by-case; see §7.
2. **Show the chains in `pha prompts` / `pha encoder`** — the operator asking
   "what will actually run on this document?" still has to read `pha.yaml`
   plus `pha filters` rather than getting one answer.
3. **`doctor.py`: flag a configured-but-missing filter.** Today it is a
   run-time `FilterError`; a pre-flight check would be friendlier.
4. **`enhancements/…INDEX.md`** — done alongside this update. A
   `HISTORIANS_README` note was dropped as unnecessary: historians never
   author or configure filters, and `pha review` / `pha edit` behave for them
   exactly as before.
5. **Adopt filters on a real collection.** Nothing in the repo exercises
   `line-numbers`/`collapse-whitespace` on real OCR output; the motivating
   case is documenta-indica (MHSI margin line-numbers, justified Latin). That
   trial is what would settle `palaeographer.post` vs `editor.pre` placement
   and measure the real per-page cost on ~1 000 pages.
6. **Decide the version bump.** The feature landed at 0.20.0 and is a
   user-visible addition; a minor bump is arguably owed.
