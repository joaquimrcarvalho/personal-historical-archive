# VLM_BENCHMARK_INFRA_PLAN.md — infrastructure specification

**Status:** proposal, not implemented. Companion to `VLM_BENCHMARK_PLAN.md`
(which covers *why* this exists, the Overshoot finding, and the material
classes). This document covers **how it is built** — schemas, tools, engine,
site, CI, tests.

**Deliberately sample-agnostic.** The choice of which pages to publish is a
separate decision, taken first, by a human. Nothing here presumes a particular
corpus, page count, model list or reference reading. The pipeline is a pure
function of whatever is committed under `corpus/`, so the sample can be chosen,
changed and grown without touching a line of the infrastructure — except that
`hvb inventory` (§7) exists specifically to *support* that choice with data.

---

## 1. The one governing principle

> **The published site is a pure function of the committed inputs.**

```
corpus/**  ──►  hvb score  ──►  data/results.json  ──►  hvb build  ──►  site/
   ▲                  │                  │
   │                  │                  └─ citable artefact (json + csv)
   │                  └─ deterministic, re-run on every build
   └─ the only hand-edited surface (readings, references, provenance)
```

Consequences, each of which the rest of this document enforces:

| # | rule | why |
|---|---|---|
| 1 | **No network at build or in CI.** No API keys in CI, ever. | keeps CI free, makes every build reproducible by a third party |
| 2 | **Scores are never hand-edited.** `data/` is regenerated on every build and committed by the workflow on `main`. | git history becomes the score history; a changed number always has a diff and a cause |
| 3 | **Readings are stored verbatim**, and **the prompt that produced each reading is stored beside it** (§5.5). The model's text is byte-exact; only *derived* normalised forms are computed, and they are never committed. | the reading is the evidence; a normalised copy is not evidence, and a reading whose prompt has been lost cannot be re-run or explained |
| 4 | **Every derived number records its inputs.** Reference hash, reading hash, profile id, scorer version. | a score whose basis is unknown is not a measurement |
| 5 | **The repository never depends on pha.** `pha` is an optional external tool used by one command. | anyone can build and audit the site without the archive |
| 6 | **The importer never writes to the archive.** Read-only access. | pha's own discipline; the archive is the source, not a scratch area |
| 7 | **Fail closed.** A schema violation, a missing rights statement, or a reading without provenance breaks the build. | an unpublished row is better than a confidently wrong one |

---

## 2. Repository layout

```
pha-vlm-bench/
├─ README.md                    # for historians, not developers
├─ METHODOLOGY.md               # metrics + normalisation + limits (generated → site)
├─ CONTRIBUTING.md
├─ LICENSE                      # code MIT; content/images per corpus `rights:`
├─ pyproject.toml               # uv-managed; entry point `hvb`
├─ schemas/                     # JSON Schema, one per artefact (validated in CI)
│  ├─ corpus.schema.json
│  ├─ page.schema.json
│  ├─ reference.schema.json
│  ├─ names.schema.json
│  ├─ reading.schema.json
│  ├─ run.schema.json
│  ├─ model.schema.json
│  └─ results.schema.json
├─ profiles/                    # normalisation profiles as DATA, not code
│  ├─ diplomatic.yaml           # nothing folded
│  ├─ modern-spelling.yaml      # u/v, i/j, long-s, hyphenation joined
│  └─ <corpus-id>.yaml          # a corpus may pin its own, referencing the above
├─ models/                      # one ficha per model (mirrors pha's models/ convention)
│  └─ <model-id>.yaml
├─ corpus/
│  └─ <corpus-id>/
│     ├─ corpus.yaml
│     ├─ notes.md               # hand/print, layout, traps, observations
│     └─ pages/<page-id>/
│        ├─ page.yaml           # image, source, layout, declared traps
│        ├─ page.jpg            # committed derivative (≤1600 px, q80)
│        ├─ page.thumb.jpg      # ~480 px, for grids
│        ├─ reference.md        # THE reference reading (front matter + fenced body)
│        ├─ names.yaml          # gold names/offices, hand-checked
│        └─ readings/
│           ├─ <model-id>.md        # verbatim reading + front matter
│           └─ <model-id>.run.yaml  # provenance
├─ collections.yaml             # declared collection sizes, for cost/time projection
├─ tools/  (hvb package)
│  ├─ cli.py                    # `hvb <inventory|import|score|build|images|validate|project>`
│  ├─ inventory.py              # §7 — support choosing the sample
│  ├─ importer.py               # §6 — archive → repo layout
│  ├─ runner.py                 # §8 — produce a new reading (delegates to `pha test`)
│  ├─ scorer.py                 # §9 — the scoring engine
│  ├─ projector.py              # §10 — collection-scale cost/time
│  ├─ site.py                   # §11 — site generator
│  └─ lib/{parse,normalise,align,metrics,config,validate}.py
├─ templates/                   # Jinja2, autoescape ON
│  ├─ base.html.j2  index.html.j2  corpus.html.j2  page.html.j2
│  ├─ model.html.j2  class.html.j2  traps.html.j2  methodology.html.j2
├─ assets/site.css              # one stylesheet; no framework
├─ data/                        # GENERATED, committed by CI on main
│  ├─ results.json
│  ├─ results.csv
│  └─ inventory.csv             # `hvb inventory` output, for choosing pages
├─ tests/                       # §13
└─ site/                        # GENERATED, gitignored, deployed to Pages
```

**Dependencies** (deliberately small): `pyyaml`, `jsonschema`, `jinja2`,
`pillow`; dev: `pytest`. *Not* `pandas`, *not* `numpy`, *not* `pha`. Alignment
is hand-rolled DP (§9.3) — a few dozen lines, no dependency, and it is the
piece that must stay inspectable.

**Why Jinja2:** model transcriptions routinely contain `&`, `<`, `[?]` and
occasionally something that looks like markup. Autoescaping is a correctness
requirement, not a convenience. The diff highlighting is built *after* escaping
from escaped fragments (§11.3).

---

## 3. Schema: `corpus/<id>/corpus.yaml`

```yaml
id: 1577-catalogo-brevis
title: "Catálogo Brevis, Provincia de Portugal, 1577"
material_class: manuscript        # manuscript | old-printed | critical-edition
language: [es, pt, la]
date: "1577-01-01"
shelfmark: "…"                    # as precise as is known; never invented
holding_institution: "…"
source_url: "…"                   # REQUIRED — where the images come from
rights: "…"                       # REQUIRED — short statement, e.g. "PD", "CC BY 4.0", "study use, thumbnail only"
rights_url: "…"
images_published: thumbnails      # full | thumbnails | none
normalisation_profile: 1577-catalogo-brevis
scoring: full                     # full | trap-only (for fundamentally divergent pages)
notes_file: notes.md
```

Build **fails** if `rights`, `source_url` or `images_published` is missing
(§1 rule 7, and the rights decision in the parent plan §9.6).

## 4. Schema: `corpus/<id>/pages/<page-id>/page.yaml`

```yaml
id: 507v
label: "507v"
image: page.jpg                   # omit if images_published: none
layout: single-column             # single-column | two-column | rotated | mixed
hand_or_type: manuscript
difficulty: hard                  # chosen before any model ran (see parent plan §4)
selection_reason: "title page; three readings disagree on every entry"
reference_status: human-corrected # human-corrected | machine-merged | pending
traps:
  - id: faint-names
    expect: no_fabrication
    detail: "faded ink; [?] / [illegible] expected rather than a guess"
```

Trap vocabulary (kept small and testable — each has one measurement in §9.5):
`empty` · `no_fabrication` · `no_repetition` · `order` (with
`must_precede: [A, B]`) · `terminates` (with `max_chars`).

## 5. Schemas: `reference.md`, `names.yaml`, `readings/*`

### 5.1 `reference.md` — machine-readable front matter, human-readable body

```markdown
---
page: 507v
status: human-corrected
reviewed_by: Joaquim Carvalho
reviewed_at: 2026-08-30
editing_rules:
  - original line breaks kept; line-end hyphen kept in its "=" form
  - original capitalisation kept
  - dots kept as information separators
  - long dash runs reduced to three dashes
  - "u → v, ƒ → ss/s (particular to this hand)"
---

## Reference reading

```
[1] + IESVS.
[2] Padres, y hermanos de la Pro=
vincia de Portugal. 1.º de henero
de. 1577.
```

## Notes

Reading notes, difficult words, readings still in doubt.
```

- The **`## Reference reading` fenced block** is what is scored. Entries are
  `[n]`-marked (the format `make_reference.py` already emits), which gives the
  integrity metric its segmentation for free.
- `status: machine-merged` is **allowed but disqualifying for ranking**: the
  scorer marks those results `provisional: true` and the site greys them.
- The body is never reformatted by any tool. `editing_rules` is displayed
  verbatim on the page view.

### 5.2 `names.yaml`

```yaml
page: 507v
source: derived-by-rule, hand-checked
entries:
  - gold: "Manuel Rodrigues"
    as_written: "Manuel Rodrigues[Roĩz]"
    office: Provincial
    variants_accepted: ["Rodriguez", "Roiz", "Rois"]
    checked_by: Joaquim Carvalho
```

Required when `reference_status: human-corrected`. If absent, the name column
renders `—` (not zero) — a missing measurement must not read as a bad score.

### 5.3 `readings/<model-id>/reading.md` — verbatim, plus front matter

```markdown
---
model_id: minimax-m3
run_id: 2026-08-29T14:12Z-8f3a
---

## Transcription

```
IESVS.

Padres, y hermanos de la Prouincia de Portugal. 1º de henero
de 1577.
```
```

The fenced body is byte-exact from the model. Tooling must never rewrite it;
if a model emits no fenced block (some emit bare prose), the importer records
`body_form: bare` and the parser takes everything before `## Notes` — and that
fact is visible in `run.yaml`.

**One directory per reading**, holding the reading, the prompt that produced it
and its provenance, so a reading is self-contained and auditable:

```
readings/<model-id>/
    reading.md      # the scored artefact (this section)
    prompt.md       # the prompt sent to the model (§5.5)
    run.yaml        # provenance, incl. the prompt's layer sources (§5.4)
```

### 5.4 `readings/<model-id>/run.yaml` — provenance (non-negotiable)

```yaml
model_id: minimax-m3
model_name: MiniMax-M3
access: direct-api            # local | local-ocr-engine | openrouter | direct-api | agent
endpoint: https://api.minimax.io/v1
weights: proprietary          # or open-weights
quant: null
run_date: 2026-08-29
produced_by: "pha test --pages 1 --palaeographer jesuit-cat4 --model minimax-m3"
render: {dpi: 300, max_image_px: 3000, jpeg_quality: 55}
params: {temperature: 0.1, max_tokens: 4096, thinking: disabled}
measured:
  latency_s: 41.2
  cost_usd: 0.031
  tokens_in: 4820
  tokens_out: 3610
prompt:                       # §5.5 — what the model was told
  file: prompt.md
  sha256: 4b1e…
  captured: exact             # exact | reconstructed | unavailable | not-applicable
  layers:
    - {role: palaeographer-base, path: palaeographers/jesuit-cat4.md, sha256: 9f2c…}
    - {role: document-default, path: "builtin:extract.py:DEFAULT_PROMPT", sha256: 7a03…}
  composition: 'base + "\n\n---\n\n" + doc prompt, then "Document: <name>\nPage: N of M\n\n"'
  page_header: "Document: 508.jpg\nPage: 1 of 1"
  params_from: palaeographers/jesuit-cat4.md   # front matter carried temp/max_tokens
reproducible: true
provenance: recorded          # recorded | unknown  ← `unknown` ⇒ row is not ranked
notes: "best on this hand; prone to dropping an entry number"
```

`provenance: unknown` exists for legacy readings whose model build cannot be
established. It is a **warning, not an error** — the row is imported, shown and
excluded from ranking. This is how the `Qwen3.8-Max` case is handled without
guessing.

## 5.5 `readings/<model-id>/prompt.md` — the prompt as sent

**A reading is not reproducible without the prompt that produced it.** The
rules *id* is not enough: the text sent to the model is **composed from up to
three layers**, and any of them can be edited afterwards, silently changing what
an old score means. So the prompt is stored **verbatim, next to the reading**
(`prompt.md`), and `run.yaml.prompt` records every layer it came from with its
hash.

### 5.5.1 How pha composes it (verified in `extract.py`, `testrun.py`)

```
prompt sent = "Document: {filename}\nPage: {page_no} of {total}\n\n"
              + compose_prompts(palaeographer_body, doc_prompt)

compose_prompts(pal, doc) = f"{pal}\n\n---\n\n{doc}"   # base FIRST; if pal is
                                                       # empty, doc alone
```

- `palaeographer_body` = the **body** of `palaeographers/<rules>.md`, front
  matter stripped (the front matter supplies `temperature`/`max_tokens`, which
  are *parameters*, not prompt — they belong in `run.yaml.params`).
- `doc_prompt` = a `<stem>.prompt.md` / `<collection>/prompt.md` sidecar if one
  exists, else the **builtin** `DEFAULT_PROMPT = "Extract text from this image"`
  (`extract.py`). *The fallback lives in the code, so reconstruction depends on
  the pha version* — record which one.
- `build_page_prompt()` prepends a **two-line per-page header**. This is the
  part most easily lost: `pha test` writes the *composed* prompt to
  `.pha-test/<doc>-<ts>/prompt-transcription.md` but **not** this header, so
  that file is one wrapper short of what was actually sent.

### 5.5.2 Capture mode — recorded, never assumed

| `captured` | when | site behaviour |
|---|---|---|
| `exact` | harvested from the run that produced the reading (new readings) | prompt shown in full |
| `reconstructed` | rebuilt from files on disk that provably predate the reading | prompt shown **with a warning banner** and the reconstruction basis |
| `unavailable` | a layer changed after the reading, or provenance is unknown | prompt replaced by *"not recorded; the rules file has changed since this reading"* |
| `not-applicable` | `access: local-ocr-engine` (tesseract / liteparse ignore prompts) or an agent reading | the engine/model settings are shown instead, from `run.yaml` |

**Reconstruction is testable, not hopeful:** compare the mtime of every layer
against the reading's `run_date` (the same principle as pha's
`_prompt_newer_than` staleness check). If any layer is newer, the prompt used is
**not** the one on disk → `unavailable`, and the score stays visible but the
prompt does not pretend to be known.

### 5.5.3 What this means for the readings that already exist

Checked on the real archive (2026-09-18):

| corpus | rules layer | layer mtime | newest reading | prompt |
|---|---|---|---|---|
| 1577 Catálogo Brevis | `jesuit-cat4.md` | 2026-08-24 | 2026-09-02 | ✅ `reconstructed` — layer predates the readings; no `prompt.md` sidecar exists in that tree, so the doc layer was the builtin one-liner |
| BPE *Memorial das Missões* | `missiones-catalogue.md` | 2026-09-17 12:09 | 2026-09-17 12:42 | ✅ `reconstructed` — 33-minute margin; flagged as tight |
| Franco / DocHist / Documenta Indica | `ocr` + `tesseract`/`liteparse` | — | 2026-09-13 | `not-applicable` — engines ignore the prompt; record `tesseract_lang`/`psm`/dpi instead |

So the existing readings can be given a **reconstructed, clearly-labelled**
prompt, and everything produced from now on gets an `exact` one. Neither is
silently upgraded to the other.

### 5.5.4 The pha change this needed — **implemented 2026-09-18**

`pha test` already wrote the effective prompts, but only the *composed* one,
without the per-page wrapper. **Fixed in `testrun.py`**: the run directory now
also holds `prompts-sent/`, with the exact text handed to the model, verbatim
and undecorated —

| file | what it is |
|---|---|
| `prompts-sent/transcription-p<NNN>.md` | the exact vision prompt (`Document: … / Page: n of m` + composed layers) |
| `prompts-sent/edit-p<NNN>.md` | the exact editor prompt (includes the transcription being edited) |
| `prompts-sent/encode-<name>.md` | the exact encoder prompt (includes the page payload) |

Consequences for this project:

- `hvb run` **copies `prompts-sent/transcription-p<NNN>.md` straight to the
  reading's `prompt.md`** and marks it `captured: exact`. No reconstruction, no
  rebuilding the header from three values, no dependence on the pha version.
- No `transcription-p<NNN>.md` is written for an `engine` palaeographer
  (tesseract/liteparse), because no prompt is sent to an engine — which lines up
  exactly with `captured: not-applicable` (§5.5.2).
- The two file sets are deliberately kept apart: `prompt-*.md` is for a human
  tuning a prompt (composed prompt + its sources); `prompts-sent/` is the
  audit artefact. **Cite `prompts-sent/`, never `prompt-*.md`**, when recording
  provenance — the latter is a different string.
- Test: `tests/test_testrun.py::test_testrun_records_the_exact_prompt_sent`
  asserts the stored file is byte-identical to the prompt the client received,
  and that it differs from the corresponding `prompt-*.md`.

Only the older readings — made before this change — need the `reconstructed`
path (§5.5.3).

### 5.5.5 Multi-pass readings

If a reading was produced by an iterative/refine strategy (the archive's
`refine:` block re-reads doubtful regions in bands), there is **one prompt per
pass**. `prompt.md` then holds the composed page prompt and a sibling
`passes/` directory holds `NNN-prompt.md` + `NNN-reading.md` per pass, with
`run.yaml.prompt.passes: N`. Out of scope for I2; the directory layout already
allows it, and `captured: exact` is what makes such a reading auditable at all.

## 6. Schema: `models/<id>.yaml` — the registry

Mirrors pha's `models/` front matter and adds what the site needs:

```yaml
id: minimax-m3
name: MiniMax-M3
access: direct-api
vendor: MiniMax
model_string: MiniMax-M3
endpoint: https://api.minimax.io/v1
api_style: openai
weights: proprietary
context_tokens: 200000
price: {in_per_mtok: 0.30, out_per_mtok: 1.20, currency: USD, as_of: 2026-09-18}
runs_on_24gb_mac: false
runs_offline: false
docs_url: "…"
```

`price` is required for any non-local access (used when a reading's measured
usage is missing, §10). `runs_on_24gb_mac` and `runs_offline` drive the
historian-facing filter.

---

## 7. `hvb inventory` — support for choosing the sample (the first decision)

Reads the **archive** (never writes to it) and emits one row per candidate
page, so the sample is chosen from data rather than memory. This is the tool
that makes "choose a sample of pages first" a spreadsheet exercise.

```bash
hvb inventory --archive ~/jesuit-archive --out data/inventory.csv
hvb inventory --rank-by disagreement      # informative pages first
hvb inventory --material-class manuscript
```

Columns: `corpus, page, material_class, layout, image_path, page_count_of_doc,
has_gold, gold_status, n_readings, models, n_entries, mean_pairwise_distance,
degenerate_flags, trap_candidates, in_archive_library`.

**`mean_pairwise_distance`** — the hardness proxy: for pages whose comparison
files exist, the normalised edit distance among the existing readings, averaged
over pairs. High disagreement means the page *discriminates* between models,
which is exactly what a benchmark page should do; unanimous pages waste a slot.
This is computed from data already on disk, so it costs nothing and it is
defensible: it is stated on the site as a *selection* statistic, never as a
score.

`degenerate_flags` are detected mechanically from existing readings: an empty
body, a repeated n-gram run over a threshold, or a body whose length is a large
multiple of the gold. These are the cheap, objective signals behind the trap
pages in the parent plan §2.5.

The tool **proposes**; it does not select. `corpus/` is populated by the human
decision (§5 of the parent plan: cleanest + hardest per volume, chosen before
any model is run).

---

## 8. Producing a reading: `hvb import` and `hvb run`

### 8.1 `hvb import` — archive → repository

```bash
hvb import --archive ~/jesuit-archive --source benchmarks/collections/<...> \
           --corpus 1577-catalogo-brevis --page 507v --model minimax-m3
hvb import --archive ~/jesuit-archive --manifest imports/1577.yaml
```

Responsibilities:

1. locate the reading in the archive's model-named folder, copy it **verbatim**;
2. normalise it into the repo layout (front matter added, body untouched);
3. **refuse to import without provenance** — either a `run.yaml` supplied by
   the caller, or `provenance: unknown` set explicitly, never silently;
4. reconstruct provenance where the archive documents it (the corpus
   `overview.md` §2 files record model, endpoint, resolution and temperature for
   the Franco corpus) and cite the source file in `run.yaml.notes`;
5. pick up the reference reading from `human/<page>.md` when it carries
   `## Corrected text` (the only mark of a real review — the seven
   non-corrected copies must **not** be imported as gold; see the parent plan
   §2.2);
6. **reconstruct the prompt** (`captured: reconstructed`) from the archive's own
   `palaeographers/<rules>.md` body + the builtin/`prompt.md` document layer,
   writing `prompt.md` + `run.yaml.prompt`, but **only when every layer provably
   predates the reading** (mtime < `run_date`, the same test as pha's
   `_prompt_newer_than`). If any layer is newer, write `captured: unavailable`
   and say why — an approximate prompt presented as exact is a fabricated
   artefact (verified status for the existing corpora: §5.5.3);
7. place the page image from the dropbox path and generate derivatives;
8. write `imports/<corpus>.yaml` recording source paths + hashes so the import
   is auditable against the archive.

The importer is the **only** component that knows the archive exists. After it
runs, the repository is self-contained and CI never needs the archive.

### 8.2 `hvb run` — a new reading

```bash
hvb run --corpus 1577-catalogo-brevis --page 508 --model minimax-m3 \
        --rules jesuit-cat4 --out corpus/1577-catalogo-brevis/pages/508/readings/
```

Delegates to pha so the reading is produced by the **production path** — the
same `pha.yaml` resolution, the same prompt, the same render settings that the
archive really uses. `pha test --pages 1` is the vehicle: it writes to
`.pha-test/` without touching the DB, library or renders, and honours the
model-server lock.

- `pha` is located via `--pha`, `PHA_BIN`, or `PATH`; absence is a clear error,
  not a crash mid-run.
- The runner **serialises per `server:` key** (§ the one-model-per-server rule):
  two readings on the same LM Studio instance must not run concurrently.
- **The prompt is copied, not re-derived.** `pha test` writes the exact text
  sent to the model under `prompts-sent/` (`.pha-test/<doc>-<ts>/`, since
  2026-09-18 — §5.5.4). The runner copies
  `prompts-sent/transcription-p<NNN>.md` to the reading's `prompt.md`, records
  each layer with its hash in `run.yaml.prompt`, and marks it `captured: exact`.
  Nothing is rebuilt from values the runner happens to know. Only if a reading
  predates that change does the runner fall back to reconstruction, and then
  `captured: reconstructed` — never `exact`.
- For `access: local-ocr-engine` the prompt is `not-applicable`; the runner
  records the engine settings instead (`tesseract_lang`, `psm`, `dpi`,
  `liteparse_*`).
- Measured `latency_s`, `cost_usd`, `tokens_in/out` come from the harness (or
  the API `usage` block) and go into `run.yaml`.
- `--dry-run` prints the resolved command and writes nothing.
- Readings are dated; the site flags any reading older than 6 months as
  `stale` (§11.4).

`hvb run` is **never** invoked by CI on a paid model (§1 rule 1).

---

## 9. The scoring engine

```bash
hvb score [--corpus ID] [--page ID] [--model ID] [--json data/results.json] [--csv data/results.csv]
```

Pure, offline, deterministic. Pipeline:

```
parse → normalise (×2 profiles) → align → metrics → results.json
```

### 9.1 `parse`
Extract the transcription from a reading file: the `## Transcription` fenced
block, else the body before `## Notes`, else the whole file. Strip the front
matter. **Never** strip content, never reflow, never trim internal whitespace.

### 9.2 `normalise` — profiles as data

`profiles/*.yaml` is an ordered list of transforms; **the same list is applied
to the gold and to the reading**, and the profile id is recorded with the
score:

```yaml
id: modern-spelling
label: "Normalised (u/v, i/j, long s, hyphenation joined)"
ops:
  - {op: unicode_nfc}
  - {op: replace, pattern: "ſ", with: "s"}
  - {op: collapse_whitespace}
  - {op: join_line_end_hyphen}
  - {op: fold, classes: [u_v, i_j]}
padding_ignored:          # never counted as errors (matches make_reference.py)
  - trailing_punctuation
  - long_dash_runs
  - name_role_connector
```

Two profiles are reported for every page: `diplomatic` (only NFC) and the
corpus's declared profile. `unicode_nfc` first is not cosmetic — without it a
composed `é` and a decomposed `e` + combining acute count as a character error,
which would add noise proportional to how the model emits diacritics.

### 9.3 `align` — 2-way DP over tokens

A compact Needleman–Wunsch / edit-distance DP, adapted from the
`palaeographers-compare` skill's `make_reference.py` (which already does N-way
alignment with insertions and deletions). Scoring needs the 2-way case.

Output: an alignment path plus, per aligned pair, a character-level diff for
the site. One aligner backs both the number and its evidence.

Guard: if the alignment collapses (e.g. a fundamentally divergent leaf — the
1577 p.511 case), the page is scored **trap-only** (`scoring: trap-only` in
`corpus.yaml`/`page.yaml`) and CER is reported as `n/a`, with the reason. A CER
computed across a non-alignment is a meaningless number dressed as a precise
one.

### 9.4 `metrics` — per (page × model × profile)

| metric | definition |
|---|---|
| `cer` | `(S+D+I) / N` over characters of the normalised gold |
| `wer` | the same over whitespace tokens |
| `names.correct` / `names.total` | `names.yaml` entries matched exactly or via `variants_accepted`, after name-normalisation |
| `integrity.missing_entries` | gold `[n]` entries with no aligned counterpart |
| `integrity.extra_blocks` | reading content with no gold counterpart, above a size floor |
| `empty_output` | reading body is empty (a result, not an error) |
| `traps[]` | per declared trap: `pass` / `fail` + measured value |
| `latency_s`, `cost_usd` | from `run.yaml`; `cost_usd` derived from `models/` price if unmeasured |

### 9.5 Trap measurements (one implementation each)

| trap | measurement |
|---|---|
| `empty` | reading body is empty or whitespace only |
| `no_fabrication` | reading is non-empty where the gold is empty/`[illegible]` |
| `no_repetition` | longest repeated n-gram run / total tokens ≤ threshold |
| `order` | every phrase in `must_precede` occurs, and in that order |
| `terminates` | `len(body) ≤ max_chars` (catches runaway generation) |

### 9.6 `results.json` — the citable artefact

```jsonc
{
  "schema_version": 1,
  "scorer_version": "1.0.0",          // bumping it invalidates cross-version comparability
  "generated_at": "2026-09-18T10:22:04Z",
  "generated_from_git": "abc1234",
  "profiles": { "diplomatic": {...}, "modern-spelling": {...} },
  "pages": [{
    "corpus": "1577-catalogo-brevis", "page": "507v", "material_class": "manuscript",
    "reference": { "status": "human-corrected", "reviewed_by": "…", "sha256": "…" },
    "scoring": "full",
    "models": [{
      "model_id": "minimax-m3", "access": "direct-api",
      "provenance": "recorded", "ranked": true, "partial": false,
      "reading_sha256": "…", "input_sha256": "…",
      "cer": { "diplomatic": 0.084, "modern-spelling": 0.061 },
      "wer": { "diplomatic": 0.142, "modern-spelling": 0.109 },
      "names": { "correct": 33, "total": 35 },
      "integrity": { "missing_entries": 0, "extra_blocks": 1 },
      "empty_output": false,
      "traps": [{ "id": "faint-names", "result": "pass" }],
      "latency_s": 41.2, "cost_usd": 0.031
    }]
  }],
  "aggregates": [{ "corpus": "…", "material_class": "…", "model_id": "…",
                   "n_pages": 8, "names_accuracy": 0.94,
                   "cer_norm_mean": 0.061, "cer_norm_spread": [0.02, 0.19] }]
}
```

`input_sha256` = hash(`reading_sha256` + `reference sha256` + profile id +
`scorer_version`). It is the honesty mechanism: if any input moves, the number
was recomputed, and the diff in `data/results.json` says so.

`results.csv` is the same content flattened, one row per (page × model ×
profile) — for spreadsheets and for `R`/`pandas` users.

---

## 10. `hvb project` — cost and time at collection scale

`collections.yaml` declares the real workloads, with the date they were
measured:

```yaml
collections:
  - id: franco-imagem-virtude
    label: "Franco, Imagem da virtude (1714–1719)"
    pages: 3612
    measured_on: 2026-09-14
  - id: jesuit-archive
    label: "the whole archive"
    pages: 24263
    measured_on: 2026-09-18
```

`hvb project` combines these with `results.json` (median s/page and $/page per
model, per material class) into the projection table the site's front page
shows. It prints its inputs and its `measured_on` dates, because a projection
without its basis is a guess.

---

## 11. The site generator

```bash
hvb build [--out site/] [--base-url /pha-vlm-bench/]
```

Reads `data/results.json` + `corpus/` + `templates/`. Writes `site/`. Plain
static HTML + one stylesheet.

### 11.1 Output

| path | content |
|---|---|
| `index.html` | what this is · legend · **reference-coverage bar** · leaderboard **by material class** · the "how to choose a model" procedure · projections |
| `corpora/<id>.html` | corpus description, hand/print, layout, page grid with badges, corpus notes |
| `pages/<corpus>/<page>.html` | **the page view** (§11.2) |
| `models/<id>.html` | ficha: access method, endpoint, price, weights, quant, runs-on-24GB, every page read, failure modes |
| `material/<class>.html` | manuscript / old-printed / critical-edition — the filter a historian arrives with |
| `traps.html` | the declared traps and who failed them |
| `methodology.html` | generated from `METHODOLOGY.md` + the Overshoot finding + limits |
| `data/results.json`, `data/results.csv` | downloads |
| `img/**` | `page.jpg` / `page.thumb.jpg` derivatives |

### 11.2 The page view

```
┌────────────────────────┬───────────────────────────────────────────────┐
│                        │  REFERENCE READING   [human-corrected]        │
│    the page image      │  editing rules applied  ▸ (verbatim)          │
│    (zoom)              │  [3] P. Manuel Rodrigues professo de 4 …      │
│                        ├───────────────────────────────────────────────┤
│                        │  ☁ MiniMax-M3   1. Pº Manuel [illegible] …    │
│                        │  🖥 gemma4      2. P[adr]e Manuel Aluarez …    │
│                        │  🤖 agent       …            ← char-level diff │
│                        ├───────────────────────────────────────────────┤
│                        │  model │ access │ CER-dip │ CER-norm │ names  │
│                        │        │        │         │          │ s/pg $ │
│                        ├───────────────────────────────────────────────┤
│                        │  ▸ how this was scored (profile, padding,     │
│                        │    alignment, scorer version)                 │
│                        │  ▸ the prompt sent to each model (prompt.md,   │
│                        │    with its capture mode + layer sources)      │
└────────────────────────┴───────────────────────────────────────────────┘
```

Image beside reference, so **any** number can be checked by eye on one screen.
Every displayed number links to the two texts it came from.

### 11.3 Diff rendering — after escaping

Build the highlight from **escaped** fragments: tokenise, `escape()` each
fragment, then emit `<del>`/`<ins>` around them. Never inject unescaped model
text into HTML. Where a page is `trap-only` (no alignment), each reading is
shown in full for that entry, with the disagreement explained.

### 11.4 Badges and honesty affordances (rendered, not documented only)

- reference status: `human-corrected` / `machine-merged` (greyed, provisional) / `pending`
- access: 🖥 local · 🔤 local OCR engine · 🔀 OpenRouter · ☁ direct API · 🤖 agent-assisted
- `partial` (row covers fewer than the corpus's pages) → excluded from aggregates
- `provenance: unknown` → not ranked
- `prompt.captured: reconstructed` → banner: *"prompt rebuilt from files that predate this reading"*; `unavailable` → *"prompt not recorded; the rules file has changed since"*
- `stale` (reading older than 6 months) → flagged with its date
- **no global ranking**: aggregates carry `n_pages` and the per-page spread, and
  the sentence *"a sample of this size is a screen for failure, not a ranking."*
- every aggregate links to the pages behind it

### 11.5 No JavaScript requirement

A single optional ~15-line vanilla `app.js` may enhance image zoom and the
per-page model filter. Everything must be readable and printable with JS
disabled; tables are real `<table>` elements; disclosure uses `<details>`.

### 11.6 Image derivatives

`hvb images` (Pillow): long edge ≤1600 px, JPEG q80, EXIF stripped,
`page.thumb.jpg` at ~480 px. Enforced by `images_published` in `corpus.yaml`:
`full` | `thumbnails` | `none`. With `none`, the page view renders text-only and
`page.full.txt` (a URL pointer) is the only image reference — the benchmark
still works, because the *readings* are what is scored.

---

## 12. CI and deployment

`.github/workflows/build.yml`:

```yaml
on: {push: {branches: [main]}, pull_request: {}}
jobs:
  build:
    steps:
      - checkout
      - setup uv + python
      - uv sync
      - hvb validate                     # schemas + fail-closed policy checks (errors → fail)
      - hvb score --json data/results.json --csv data/results.csv
      - pytest -q                        # §13
      - hvb build --base-url /pha-vlm-bench/
      - upload-pages-artifact site/
  deploy:
    needs: build
    if: github.ref == 'refs/heads/main'
    # actions/deploy-pages
  commit-results:
    needs: build
    if: github.ref == 'refs/heads/main'
    # commit data/ back to main, message: "results: rebuild [skip ci]"
```

**`hvb validate` — errors vs warnings** (errors fail the build):

| check | severity |
|---|---|
| schema violation on any artefact | **error** |
| `corpus.yaml` missing `rights` / `source_url` / `images_published` | **error** |
| reading without `run.yaml` | **error** |
| reading without `prompt.md` **and** `run.yaml.prompt.captured` not in `{not-applicable}` | **error** |
| `captured: exact` or `reconstructed` but no layer hashes recorded | **error** |
| `captured: reconstructed` whose `run.yaml.prompt` lacks the reconstruction basis / warning flag | **error** |
| `reference_status: human-corrected` without `reviewed_by`/`reviewed_at` | **error** |
| `reference_status: human-corrected` without `names.yaml` | **error** |
| `page.yaml` missing `selection_reason`/`difficulty` | **error** |
| non-local model in `models/` without `price` | **error** |
| duplicate `(corpus, page, model)` reading | **error** |
| `prompt.captured: unavailable` | warning (prompt shown as *not recorded*) |
| `prompt.captured: reconstructed` | warning (banner on the page view) |
| `provenance: unknown` | warning |
| `machine-merged` reference present | warning |
| reading older than 6 months | warning (rendered as `stale`) |
| fewer than 3 pages in a corpus | warning (aggregates suppressed) |

**No networked model calls in CI.** GitHub runners cannot host a 24 GB
LM Studio instance and must not spend the owner's API budget. Local and free
rows are refreshed by a documented manual `hvb run` on the archive machine
(a self-hosted runner is explicitly out of scope, noted for later).

**Determinism gate:** CI builds twice and diffs `data/results.json`; a
non-identical second build fails. Cheap insurance against dict-ordering,
timestamps and float formatting leaking into published numbers.

---

## 13. Tests

| test | what it pins |
|---|---|
| `test_metrics_golden.py` | hand-computed CER/WER on crafted fixtures, including an empty reading and a 100%-wrong reading |
| `test_alignment.py` | insertions, deletions, substitutions, a whole-page divergence, and the trap-only fallback |
| `test_normalise.py` | profile idempotence, NFC handling of composed vs decomposed accents, long-s and u/v folding |
| `test_parse.py` | fenced block, bare body, `## Notes` boundary, front matter, preserving internal whitespace byte-exactly |
| `test_integrity.py` | missing entries and extra blocks on crafted cases |
| `test_traps.py` | one case per trap: pass and fail |
| `test_escaping.py` | a reading containing `<script>`, `&`, `</table>` renders escaped on every template |
| `test_determinism.py` | double build → byte-identical `results.json` |
| `test_import_gold.py` | a `human/*.md` **without** `## Corrected text` is *not* imported as gold (the failure mode found in the archive) |
| `test_provenance.py` | a reading without `run.yaml` is refused; `provenance: unknown` imports but is unranked |
| `test_prompt_capture.py` | the prompt sent equals `build_page_prompt(compose_prompts(base, doc), …)` byte-for-byte; the per-page header is not lost; a reading without `prompt.md` is refused unless `not-applicable` |
| `test_prompt_reconstruct.py` | a layer newer than `run_date` ⇒ `captured: unavailable`, never `reconstructed`; a corpus with a `prompt.md` sidecar composes base-then-doc in that order |
| `test_prompt_escape.py` | a prompt containing `<`, `&`, `{{ }}` renders escaped (prompts are model-authored text like any other) |
| `test_validate.py` | each error row in §12 actually fails validation |

---

## 14. Build phases for the infrastructure

Infrastructure only; a corpus with **one** page is sufficient to exercise all
of it, and that is the point of keeping this sample-agnostic.

| phase | deliverable | exit criterion |
|---|---|---|
| **I0 — skeleton** | `pyproject.toml`, package, `hvb` CLI, all schemas, `hvb validate`, `hvb inventory` | `hvb inventory --archive ~/jesuit-archive --out data/inventory.csv` produces a usable candidate table → **the sample can be chosen** |
| **I1 — ingest** | `hvb import` (verbatim readings, provenance rules, **prompt capture/reconstruction**, gold detection), `hvb images`, `hvb validate` policies | one page imported end-to-end with an auditable `imports/*.yaml` **and** a `prompt.md` whose `captured` mode is justified by layer mtimes |
| **I2 — score** | `parse/normalise/align/metrics`, two profiles, traps, `results.json` + `results.csv`, the whole test suite | golden tests pass; double build byte-identical |
| **I3 — site** | templates, page view with diff, indices, badges, projections, `site.css` | site builds and reads correctly with JS disabled; every number links to its texts |
| **I4 — deploy** | `build.yml`, Pages, `commit-results`, `reproduce.sh` per reading, `CONTRIBUTING.md` + correction issue template | pushing to `main` publishes the site; a third party can rebuild and get identical `results.json` |

I0 is the piece that unblocks the immediate need: it is the tool that lets the
page sample be chosen from evidence. Everything after it consumes whatever
sample is committed.

---

## 15. Open infrastructure decisions

| # | decision | recommendation |
|---|---|---|
| 1 | Scorer versioning policy | semver in `results.json`; a metric change **must** bump it, and the site labels which version produced a score |
| 2 | Alignment tokenisation for CER | characters of the normalised text, whitespace included; state it explicitly on the methodology page |
| 3 | Whether to commit `data/` | yes, committed by CI on `main` — git history is the score history |
| 4 | `agent-assisted` rows | imported, shown, never ranked (parent plan §6) |
| 5 | `trap-only` pages | no CER; trap verdicts + full readings; reason printed |
| 6 | Site styling/name | plain, print-friendly, no branding dependency; name the repo for historians, not for pha |
| 7 | Self-hosted runner for local-model refresh | out of scope now; revisit if refresh cadence becomes a burden |
| 8 | Non-Latin scripts (e.g. the archive's Chinese/CJK material) | profiles are data, so a CJK profile is a new YAML file — but tokenisation and the name metric need a decision before publishing such a corpus |
| 9 | Prompt storage granularity | **decided: one `prompt.md` beside each reading** (§5.5), with layers + hashes in `run.yaml.prompt`; multi-pass refine readings get a `passes/` subdirectory rather than a new layout |
| 10 | Whether to accept `reconstructed` prompts at all | accept, but always flagged on the page view and never shown as if captured — the alternative is losing the prompt for every reading made before today |
| 11 | The small `pha test` change (§5.5.4) | **done 2026-09-18** — `prompts-sent/` writes the exact text sent; every future reading is `captured: exact` |

---

## 16. What this document does not decide

Which pages, which corpora, how many, and which models — those are the sample
decision, taken first and separately, supported by `hvb inventory` (I0). The
infrastructure above is complete for any sample; adding a corpus is adding
files under `corpus/`, never changing `tools/`.
