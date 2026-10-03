---
name: palaeographers-compare
description: Compare multiple palaeographers' (or transcription models') readings of the same historical document page-by-page and produce a uniform comparative edition — an overview of differences plus one markdown file per page with entry-by-entry stacked readings (1. reading A / 2. reading B / … / N. reading N; any number of readings, 2 or more). Use when the user asks to compare or collate transcriptions, compare palaeographers or transcription models, find differences between readings of the same document, or generate a comparative overview of catalogue/archive transcriptions.
---

# Palaeographers Compare

Produces a **uniform comparative edition** from two or more independent readings of the same document (e.g. three palaeographers, or several vision-model transcriptions of the same manuscript pages). The output is a `comparison/` folder with:

- one markdown file per page, in a **fixed uniform skeleton**, showing every entry with the readings stacked as `1.` … `N.` (or `= all readings:` when identical), plus a "Key differences" bullet list;
- an `overview.md` summarising corpus, palaeographer mapping, systematic conventions, and the most significant disagreements.

The reference implementation of the workflow (and of the expected output format) is `palaeographers/comparison/` under the 1577 Portuguese Jesuit catalogue benchmark — the same layout (corpus readings + sibling `images/` + `comparison/`) must be reproduced for every new corpus.

## Inputs

- An **input directory** containing one subdirectory per palaeographer (e.g. `palaeographers/DeepSeek-V4-FVE/`, `palaeographers/M3/`, `palaeographers/Qwen3.8-Max/`). Each subdirectory has one markdown file per page of the same document, with **matching basenames across palaeographers** (e.g. `507v.md`, `508.md`, …).
- **`images/`** (required): a sibling of the `palaeographers/` folder holding a copy of
  the **source image of every compared page**, with the same basename as the page files
  (`507v.md` -> `507v.jpg`; `.jpg`/`.jpeg`/`.png`/`.tif`/`.tiff`/`.webp` accepted). It is
  what a reviewer reads against when correcting a reading, and it keeps the benchmark
  self-contained. `verify_comparison.py` checks it (`--require-images` makes a missing image a hard failure).
- Each page file contains a transcription section. The transcription may be inside a fenced code block (under `## Transcription`) or be the body before `## Notes`. Only the transcription content is compared; `Notes` / `Named entities` / `Content summary` sections are context, never part of the comparison.

## Workflow

1. **Name the palaeographers.** List the subdirectories of the input dir. Fix their order with the user (this becomes the numbering used everywhere); default to folder order or alphabetical. Example: `1. DeepSeek-V4-FVE · 2. M3 · 3. Qwen3.8-Max`.

2. **Copy the page images.** Make sure an **`images/`** folder sits next to `palaeographers/` with a copy of every compared page image, named with the same basename as the page files (`507v.jpg`, `508.jpg`, ...). The comparison and the reference are meant to be corrected by a human **against the image**, so the benchmark must be self-contained. `verify_comparison.py` checks this.

3. **Discover the pages.** Take the intersection of filenames across all palaeographer subdirectories (a page must exist for every palaeographer). Sort them in reading order (recto/verso).

4. **Read every transcription** for every page×palaeographer. Note per-page context (community/college, column layout, rotation, damage, show-through) from the notes sections.

5. **Generate the per-page comparison files** (see "Output format" below). This step fans out naturally — generate each page's file independently; you may parallelise pages across subagents, but every file must follow the same skeleton exactly.

6. **Generate `overview.md`** (see "Overview" below).

7. **Normalise + verify** the whole output folder with the bundled scripts (see "Scripts" below) so every file shares the exact skeleton.

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

After generating the files, normalise and verify the whole folder:

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

## Human-reviewed gold standard (human/ folder)

After the comparison and reference folders are produced, a **human reviewer** may edit the reference content into a corrected, variant-free version kept in a sibling `human/` folder: same page files and uniform skeleton, but with a `## Corrected text` section where the variants are resolved to the correct reading and the original line breaks are restored. (In the reference corpus, the review is by Joaquim Carvalho, started 2026-08-30; only `507v.md` has been reviewed so far — the other files are still unedited copies of the reference.)

The reviewer documents the editing rules in the file header (e.g. kept line-break hyphens in their `=` form, original capitalisation preserved, dots kept as word separators, long dash runs reduced to three dashes, letter normalisation `u`→`v` and `ƒ`→`ss`/`s` particular to this hand) and signs the review in the Notes section.

**Rules for agents:**

- `human/` is **read-only for agents** — never edit, "correct", regenerate, or reformat anything inside it; the human review is authoritative and must remain untouched.
- It may be **read freely**, and its purpose is twofold:
  - **Benchmarking palaeographers**: compare each palaeographer's raw reading (or the consolidated reference line) against the human corrected text — count per-entry agreement on names, roles, spellings and offices; identify which palaeographer is most accurate per page and which readings the human rejected.
  - **Improving prompts**: use the human version paired with the slashed variants as few-shot examples of correct readings, or feed the disagreements back into the transcription/vision prompt to target recurring failure modes (e.g. surname confusions, role misreadings).

The three-folder pipeline is therefore: `comparison/` (aligned stacked readings) → `reference/` (slashed variants for translation) → `human/` (reviewed gold standard for benchmarking and prompt tuning).

## Quality checklist

- [ ] Every page file shares the exact uniform skeleton (title / Context / one fenced block / Key differences).
- [ ] All readings verbatim: diacritics, `[?]`/`[illegible]`, bracket expansions, honorific variants preserved.
- [ ] `= all readings` (older files: `= all three`) used only for substantive identity; differing readings all shown.
- [ ] Structural disagreements (class numbering, columns, fundamental page disagreements) flagged, not harmonised.
- [ ] `overview.md` present with palaeographer mapping, conventions, page-by-page highlights, consensus notes.
- [ ] `verify_comparison.py` passes for every file.

## Examples

- `examples/entry-format.md` — minimal sample of the uniform skeleton.
- Live reference output: `palaeographers/comparison/`, `palaeographers/reference/` and `palaeographers/human/` in the 1577 Jesuit catalogue benchmark (8 pages, 3 palaeographers).
