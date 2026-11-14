---
name: palaeographers-compare
description: Compare multiple palaeographers' (or transcription models') readings of the same historical document page-by-page and produce a uniform comparative edition — an overview of differences plus one markdown file per page with entry-by-entry stacked readings (1. reading A / 2. reading B / … / N. reading N; any number of readings, 2 or more). Use when the user asks to compare or collate transcriptions, compare palaeographers or transcription models, find differences between readings of the same document, or generate a comparative overview of catalogue/archive transcriptions.
---

# Palaeographers Compare

Produces a **uniform comparative edition** from two or more independent
readings of
the same document (e.g. three palaeographers, or several vision-model
transcriptions of the same pages) and keeps the corpus behind it in the shape
`pha bench` expects:

    <corpus>/
      bench.yaml                 # reader order, source document, rules
      <reader>/<page>.md         # one reading per reader per page
      comparison/<page>.md       # aligned stacked readings (this skill's output)
      reference/<page>.md        # machine merge, variants joined with '/'
      human/<page>.md            # the human-corrected gold
      images/                    # optional; otherwise resolved from the source

The output of the comparison step is still a `comparison/` folder: one markdown
file per page in a **fixed uniform skeleton**, every entry with its readings
stacked as `1.` … `N.` (or `= all readings:` when identical), plus a "Key
differences" bullet list, and an `overview.md` that maps the readers, describes
the conventions and highlights the significant disagreements.

The reference implementation remains the 1577 Portuguese Jesuit catalogue
benchmark under `benchmarks/collections/jesuit-catalogues/…/1577/palaeographers/`
(readings + `comparison/` + `reference/` + `human/`).

## Inputs

- **One subdirectory per palaeographer** in the corpus folder, each holding one
  markdown file per page of the same document, with **matching basenames across
  readers** (`507v.md`, `508.md`, …). The transcription may sit inside a fenced
  block under `## Transcription` or be the body before `## Notes`; an unclosed
  fence left by an older run ends at the next heading. Only the transcription is
  compared — `Notes` / `Named entities` / `Content summary` are context.
- **`bench.yaml`** declares the reader order — that order is the `1..N`
  numbering used in every comparison and reference file — plus the source
  document, the transcription rules id and, optionally, the rights line.
  `pha bench init` writes it (taking the order from the comparison's own title
  when one exists); the order is data, not a guess made at generation time.
- **Page images are resolved, not copied:** corpus `images/<page>.<ext>` → the
  declared source directory → a PDF page rendered on demand.
  `pha bench export` materialises the images into the published site, and
  `pha bench verify` warns when no image can be found;
  `pha bench verify --require-images` makes a missing image a hard failure. A
  working corpus therefore does not need an `images/` folder.

## Workflow

`pha bench` does the mechanical steps; the comparative edition itself is still
the agent's work whenever the draft is wrong.

1. **Declare the corpus.** `pha bench init <corpus>` writes `bench.yaml` from the
   reader folders. Set `document.path` (and `document.pages` when the page ids
   are not the source image stems) and the `palaeographer` rules id, then check
   the result with `pha bench status <corpus>`.

2. **Produce readings** if they do not exist yet:

       pha bench run <corpus> --pages 4,5 --dry-run     # the plan, no model call
       pha bench run <corpus> --pages 4,5

   Each run is a `pha test` scratch run: the archive is never touched and the
   editor stays off. The model must be named per reader in `bench.yaml`; a
   reader with no model is refused rather than run with the archive default.

3. **Build the comparison draft.** `pha bench compare <corpus>` writes
   `comparison/` — profile `tabular` for numbered catalogue rows, `lines` for
   free text, `auto` by default. It is a mechanical draft: entries are aligned
   by row number or by line position. **It overwrites `comparison/`**, so if the
   folder holds a hand-edited edition, commit it (or copy it aside) before
   rerunning.

4. **Edit the comparison where the draft is wrong.** This is the skill's real
   work: split or merge entries the mechanical pass got wrong, align a missed
   line, rewrite Context, and write Key differences. Every file must follow the
   uniform skeleton below. Pages fan out naturally to subagents, but the
   skeleton is not negotiable.

5. **Verify.** `pha bench verify <corpus>` checks the skeleton, contiguous
   entries, each entry's reading count (default: the corpus's readers), the
   images, and the gold's structure and freshness. The bundled
   `scripts/verify_comparison.py` remains for a machine without `pha`.

6. **Reference merge.** `pha bench reference <corpus>` writes `reference/` —
   one line per entry, variants joined with `/`. It is generated; never
   hand-edit it, regenerate it.

7. **Human review.** The reviewer works in `pha bench serve` (the page image
   beside the stacked readings; saving writes `human/`). Agents do not edit
   `human/` — see below.

8. **Score and export.** `pha bench score <corpus>` writes
   `scores/results.json` (pending and stale gold pages excluded, with the
   reasons and the caveats); `pha bench export <corpus> --out DIR` writes a
   self-contained static site.

## Alignment rules


- One **entry** = one line of the catalogue (a person's name with or without role, a heading such as `Casos.` / `1ª classe de humanidad.`, a count marker, the `+IESVS` header).
- Entries appear in the same order in all readings; if a palaeographer misses, adds, or splits a line, align by position and meaning and **flag it** in Key differences.
- When readings diverge **fundamentally** (no shared lines — e.g. one palaeographer reads a blank leaf, another a rotated index, a third a show-through roster), present each reading **in full as one entry** (`[1]` … `[N]`) inside the fenced block and explain the disagreement in Context and Key differences.
- Preserve each reading's own structure verbatim (columns, numbering, class numbers) — never harmonise the palaeographers' structural choices; flag the disagreement.

## Fidelity rules (verbatim transcription)

- Preserve the original text **exactly**: diacritics (`ç ã ñ`), `[square bracket]` expansions, `[?]` and `[illegible]` markers, honorific variants (`P.`, `P[adr]e`, `P.e`, `Pº`), punctuation, slashes, trailing dashes, abbreviation forms (`Hernade3`, `coadintor`, `co[n]fessa`). Never modernise or normalise.
- `= all readings:` (older files may say `= all three:`) is used **only** when the readings are identical in every substantive character. Trailing periods, long filler-dash runs (`————`), and the name↔role connector (`—` vs `,`+dashes vs `/`) are treated as padding and ignored for the equality test.

## Output format — per-page file (uniform skeleton)

Every page file must have **exactly** this layout (title line, one Context paragraph, one section with a *single* fenced code block, one Key-differences section):

```markdown
# <page> — Comparative readings (1. <P1> · … · N. <PN>)

Context: <one or two sentences: what the page contains, column layout, rotation/damage notes, reading order>

## Entry-by-entry comparison

```
[1] 1. <reading 1>
    2. <reading 2>
    3. <reading 3>
    …

[2] = all readings: <shared reading>

[3] 1. <only the differing lines are shown; identical lines are dropped>
    ...
```

## Key differences on this page

- <bullet list of the most significant disagreements: name variants, one reading legible where another has [?], different role/office readings, extra/missing entries, structural differences — quoting the readings concisely>
```

Skeleton rules (non-negotiable):

- Title: `# <page> — Comparative readings (1. <P1> · … · N. <PN>)` — one numbered reading line per reading, `1.` … `N.` in order.
- Exactly two `##` sections, named `## Entry-by-entry comparison` and `## Key differences on this page`.
- **One single fenced code block** holding *all* `[n]` entries — never one fenced block per entry, never narrative prose sections.
- One blank line before the opening fence; entries separated by one blank line; stacked readings indented under their number.
- No stray label lines inside the block (e.g. a subagent's `Nuovices heading.`), no redundant "Numbering:" line (the mapping is in the title).

## Output format — overview.md

1. **Corpus** — what the document is (date, communities/colleges listed), table of pages with one-line content summaries.
2. **The palaeographers** — the fixed 1.…N. mapping, with the user's example reading quoted if one was given.
3. **Overall picture** — how much the readings agree; systematic orthographic/editorial conventions per palaeographer; the three kinds of difference (spelling conventions, damaged-character readings, structural interpretation).
4. **Page-by-page highlights** — for each page, the most significant disagreements with the competing readings quoted (e.g. a name read three ways, a role disagreement, a class-numbering difference).
5. **Consensus notes** — where a majority of readings agree, that reading is more probable; flag internally suspicious readings (e.g. dittography); keep historical identifications hedged ("historically plausible", verify against the edition) unless confirmed.
6. **Files** — index of the per-page files and sources.

## Scripts

`pha bench verify` and `pha bench reference` supersede these scripts in an
archive with the current pha; they are kept for a machine that has only the
skill folder, and their checks are what `pha bench verify` runs.

After generating the files by hand, normalise and verify the whole folder:

```bash
# Merge per-entry fenced blocks into one block; restore blank lines; collapse double blanks
python3 scripts/normalize_comparison.py comparison/

# Structural check: 2 fences per file, correct sections, contiguous entries
python3 scripts/verify_comparison.py comparison/

# ...and check every entry carries exactly N readings:
python3 scripts/verify_comparison.py comparison/ --readings 3
```

- `normalize_comparison.py` — for every page file (not `overview.md`): locates the section between `## Entry-by-entry comparison` and `## Key differences on this page`, removes per-entry fence lines, collapses blank runs, and wraps the entries in a single pair of fences. Optionally `--drop-label "Nuovices heading."` style stray lines via repeated `--drop-label` arguments.
- `verify_comparison.py` — prints OK/FAIL per file for: exactly 2 fences, exactly the two `##` sections, contiguous numbered entries inside one block, blank line before the opening fence, and matching entry counts. With `--readings N` it also fails any entry whose reading count is not N (0 is accepted for `= all …` entries; pages kept unconsolidated — a single reading per entry — are exempt), catching silent mis-merges. Exits non-zero if any check fails.
- `make_reference.py` — builds a **`reference/` folder for producing a reference translation**: every entry becomes ONE line with common words written once and variants joined with `/` in reading order (any number of readings). See "Reference variant" below.

## Reference variant (for producing a reference translation)

`pha bench reference <corpus>` is the supported path (same alignment, front
matter and output skeleton); the bundled script below does the same merge when
`pha` is not installed.

From the comparison folder, generate a sibling `reference/` folder aimed at translation work:

```bash
python3 scripts/make_reference.py comparison/            # writes reference/ next to comparison/
python3 scripts/make_reference.py comparison/ reference/ # explicit output folder

# guard against a wrong reading count (refuses to write if a page has more):
python3 scripts/make_reference.py comparison/ --readings 3

# pages that cannot be consolidated are auto-detected; force one explicitly:
python3 scripts/make_reference.py comparison/ --divergent 511
```

Each entry becomes one line, e.g. (entry [7] of `507v.md`):

```
[7] P./P[adr]e Iuan/Juan Correa/correa —————————/— Ministro.
```

Rules implemented by the script:

- Token-level sequence alignment (edit-distance DP) merges the N readings per entry, handling insertions/deletions.
- Common words are written once; variants are joined with `/` in reading order (1/2/…/N).
- Padding: trailing periods/commas, line-end hyphenation (`-`/`=` at line end), and standalone `/`/`-` name↔role connectors are dropped; em-dash layout leaders (`————`) are kept and shown as variants (`————————/—`).
- The first variant keeps its own punctuation (`P.`), later variants are punctuation-normalised (`P[adr]e`).
- Entries identical in all readings are given as-is. Pages whose readings diverge fundamentally (every entry carrying a single full reading — e.g. a leaf read as blank vs index vs show-through) are auto-detected and kept unconsolidated; `--divergent PAGE` forces the same for a named page. Reading markers are recognised only in sequence (`1.`, `2.`, …), so an embedded number in a wrapped line ("3. Nouicos.") is not mistaken for a reading.
- The output files keep the same uniform skeleton (title with palaeographer mapping, Context, one fenced block, Notes).

## Human-reviewed gold (`human/`)

The gold is the reviewer's resolution of the variants: same page files, a
`## Corrected text` section, and YAML front matter that records who validated
it and against which readings:

    ---
    page: 507v
    status: human-corrected        # human-corrected | machine-merged | pending
    reviewed_by: Joaquim Carvalho
    reviewed_at: 2026-08-30
    readings: [DeepSeek-V4-FVE, M3, Qwen3.8-Max]
    readings_hash: sha256:…
    ---

- Only `status: human-corrected` counts as gold. `pending` marks a copy kept
  for reference (August 2026: seven of the eight 1577 files are such copies of
  an older reference, not reviewed readings); `machine-merged` is a generated
  merge.
- `readings_hash` makes the gold **stale** when a reading beneath it changes
  (a re-read, a new model, a prompt edit). `pha bench status` and
  `pha bench verify` report it, and `pha bench score` excludes a stale page.
- The reviewer works in `pha bench serve`, which writes the file for them. An
  agent never edits `human/` — not to correct, not to reformat, not to
  regenerate. It may read the gold freely, compare readings against it, and
  use the disagreements to improve prompts (the gold paired with the slashed
  variants is the best few-shot evidence there is).

## Quality checklist


- [ ] Every page file shares the exact uniform skeleton (title / Context / one fenced block / Key differences).
- [ ] All readings verbatim: diacritics, `[?]`/`[illegible]`, bracket expansions, honorific variants preserved.
- [ ] `= all readings` (older files: `= all three`) used only for substantive identity; differing readings all shown.
- [ ] Structural disagreements (class numbering, columns, fundamental page disagreements) flagged, not harmonised.
- [ ] `overview.md` present with palaeographer mapping, conventions, page-by-page highlights, consensus notes.
- [ ] `pha bench verify <corpus>` passes (or `scripts/verify_comparison.py`
      when `pha` is not installed).
- [ ] No `human-corrected` page is reported stale, and no agent has edited
      `human/`.

## Examples

- `examples/entry-format.md` — minimal sample of the uniform skeleton.
- Live reference output: `comparison/`, `reference/` and `human/` in the 1577
  Jesuit catalogue benchmark (8 pages, 3 palaeographers); `pha bench status
  <corpus>` reports its coverage and `pha bench verify` its shape.
