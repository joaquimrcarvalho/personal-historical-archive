# Enhancement request — stage filters (pre/post processing around a rules model)

**Status:** draft for discussion.
**Author/date:** archive work session (Documenta Indica collection).
**Motivating use cases:** OCR cleanup for early-modern printed editions;
edition-specific margin line-numbers; general "shape the transcription
before/after the LLM" concerns.

## 1. Motivation

A pha stage is today a single transformation:
`input → (model | engine) → output`. Many real concerns are **mechanical and
source-specific** and do not belong in the LLM prompt at all — removing an
OCR engine's synthetic page separator, collapsing the justified-print space
runs, dropping superscript-footnote residue, stamping the edition's margin
line-numbers (`[l. N]`), joining words hyphenated across a line break. Baking
these into the prompt (a) bloats every editor/palaeographer, (b) forces the
model to do deterministic work it does inconsistently, and (c) cannot be
turned on/off per source cleanly.

**Filters** separate those deterministic transforms from the model: a stage
becomes

```
  input ──pre filters──▶ model / engine (the rules) ──post filters──▶ output
```

Each filter is a small script with an input and an output; filters chain.
The **model keeps only the work that needs judgment**; deterministic,
source-specific shaping moves into filters that are opt-in per collection.

This subsumes several earlier ideas:
- the "OCR cleanup" steps of the `latin-to-english-ocr` editor become
  `editor.pre` filters, so that editor can collapse back to plain
  `latin-to-english` rules;
- the Documenta Indica margin **line-numbers** (`[l. N]`) become a `pre`
  filter listed in that collection's sidecar only — no global rule, no
  extra `edition:` schema key needed;
- it is a natural home for the deterministic line-number detector.

## 2. Proposed configuration

A stage (`palaeographer`, `editor`, `encoder`) may list `pre` and `post`
filters. A filter is named by its id, or given as an object when it takes
parameters:

```yaml
# dropbox/collections/documenta-indica/pha.yaml   (OCR path)
palaeographer:
  rules: ocr
  model: liteparse
editor:
  rules: latin-to-english        # plain base rules — OCR cleanup moved out
  model: deepseek-v4-flash
  pre:                           # applied to each page transcription
    - strip-ocr-page-separator   # delete the engine's "--- Page N ---"
    - collapse-whitespace        # collapse justified-print space runs
    - footnote-marker-residue    # drop stray * ** *^ ° º after words
    - line-numbers               # edition margin numbers -> [l. N]
  post:
    - join-hyphenated-words      # rejoin words split by a soft hyphen
```

### 2.1 Where `pre`/`post` apply (per stage)

`pre`/`post` filters are **text transforms**, so they are meaningful only on
stages whose input is text:

| stage | input | `pre` (text) | `post` (text) |
|---|---|---|---|
| palaeographer | page **image** | n/a — image prep belongs to `render`, not to a text filter | optional raw-text shaping of the engine's output |
| editor | page text | clean the transcription *before* the translation model | shape the model's edited output |
| encoder | whole-document text | shape the text before record extraction | shape the extracted records/output |

`encoder:` uses the same `pre`/`post` around the record-extraction calls
(whole-document text and model output respectively).

**Palaeographer has no text `pre`.** Its input is a page image, so a text
filter has nothing to run on there; image preprocessing is a separate
`render` concern (dpi, pixel cap), not the text-filter scheme. The only text
hook at the palaeographer stage is `post`, on the raw text the engine/vision
model just produced.

**Choose one home for raw-text cleanup — avoid double application.** Cleaning
the raw transcription (separator removal, whitespace collapse, `[l. N]`, …)
could live in either `palaeographer.post` or `editor.pre`; do not configure
both. Recommended default: `editor.pre` — the stored raw transcription stays
faithful (for review and the raw search variant), and only the edited stream
is mechanically cleaned before translation. Use `palaeographer.post` instead
only when you also want the *stored raw / raw search variant* clean, in which
case leave `editor.pre` empty for those filters.

A filter referenced but not defined is an error at load time. Order is the
listed order. Failure of any filter aborts the stage for that unit and
records an error — pha never stores partially-filtered output silently.

## 3. Filter contract

- **Location.** Archive-level `filters/` (alongside `palaeographers/`,
  `editors/`, `encoders/`, `models/`) for shared filters; optional
  collection-local `filters/` next to the sources so collection-specific
  filters travel with the documents (mirroring the encoders/prompts
  resolution, nearest-wins).
- **Shape.** One id per filter, e.g. `filters/<name>/` containing an
  executable (`filter.py`, `run.sh`, …) and an optional `filter.md`
  manifest (description + declared `params` schema + defaults). Files whose
  names start with `_` are ignored (consistent with the model/prompt
  folders).
- **Invocation.** pha runs the filter as a subprocess: page/document text on
  stdin (or a temp file for very large inputs), transformed text on stdout,
  diagnostics on stderr, exit code 0 = success. For the parameterized form
  (`{name, params}`) the manifest declares the accepted params and their
  defaults.
- **Determinism.** Built-in and recommended filters are **pure** (same input
  ⇒ same output). This keeps the pipeline reproducible and lets filters be
  unit-tested. A filter may instead be *model-backed* (declared in its
  manifest) — in which case it takes the same single-local-job lock as
  scan/edit; do not use model-backed filters unless needed.
- **Safe operations only.** Filters are arbitrary code provided by the
  archive owner / collection (same trust level as prompt files); no sandbox
  is implied. Document this.

## 4. Built-in filter catalogue (first batch, repo-provided)

| id | pre/post | does |
|---|---|---|
| `strip-ocr-page-separator` | pre | removes the engine's synthetic `--- Page N ---` line |
| `collapse-whitespace` | pre | collapses justified-print runs of spaces to single spaces (keeps blank lines / `[3v]` foliation) |
| `footnote-marker-residue` | pre | removes stray floating `*`/`**`/`*^`/`°`/`º` superscript residue after words; keeps footnote blocks and legible digit references |
| `line-numbers` | pre | recognises MHSI margin line-numbers (even pages: leading column-0 multiple of 5; odd pages: trailing; whole-document-aware so odd-page numbers are confirmed by the ascending 5-step sequence) and stamps `[l. N]` |
| `join-hyphenated-words` | post | re-joins a word split by a soft hyphen across a line break (line ending in `-` + next line starting lowercase) |

Collections/agents write more for their own sources.

## 5. Interaction with existing pieces

- **Editor composition.** Once filters exist, deterministic concerns are
  removed from prompts: the shared `latin-to-english` prompt stops carrying
  the line-number/OCR instructions, and `latin-to-english-ocr` can be
  reduced to the plain `latin-to-english` rules + a `pre` filter list (or
  kept as a thin `extends:` of it — see the `extends` request) if only some
  OCR slips still need model judgment.
- **Sidecar schema.** `pre`/`post` are added inside the `palaeographer` /
  `editor` / `encoder` blocks of `pha.yaml`. The block schemas are updated
  accordingly (no new top-level key required).
- **Provenance & re-run invalidation.** The stage stores which filters ran
  (names + versions/hashes) with its output, and the existing
  "re-run when the rules file changed" checks also watch each referenced
  filter's manifest/script mtime — otherwise editing `filters/line-numbers`
  would never re-trigger already-edited pages.

## 6. CLI for authors

- `pha filters` — list available filters (id, manifest description).
- `pha filter <id> [--params …]` — run one filter over stdin / a file, so a
  filter can be developed and unit-tested on sample text before wiring it
  into a sidecar (`pha filter line-numbers < page.raw.txt`).

## 7. Tests to add

- a filter transforms stdin→stdout as documented (repo unit tests for each
  built-in);
- chained `pre`/`post` order is honoured; a failing filter records an error
  and stores nothing;
- editing a referenced filter's file re-triggers the stage (invalidation);
- a per-collection `line-numbers` filter runs on `documenta-indica` and is
  inert elsewhere (regression: other collections' edited text unchanged);
- end-to-end: OCR page → `pre` (line numbers stamped, separator removed) →
  `latin-to-english` model → `post`.

## 8. Non-goals / open questions

- Filters are not a general shell/pipe sandbox; they are one text transform
  each.
- Image preprocessing (e.g. deskew, border-crop, binarize) is deliberately
  out of scope for this text-filter scheme — it belongs to `render`. If an
  image-in/image-out `pre` is ever wanted for the vision/OCR palaeographer,
  that is a separate `render`-stage feature, not a text filter.
- Model-backed filters are allowed but discouraged; define the lock and
  retry semantics if we adopt them.
- Confirm whether `encoder.post` should operate on the parsed records or on
  the raw model output (likely records, but worth stating).
