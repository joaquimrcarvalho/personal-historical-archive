# palaeographers-compare

Compare several palaeographers' (or transcription models') readings of the same
historical document, page by page, and turn them into three review surfaces:

1. **`comparison/`** — every entry with the readings stacked side by side (`1.` … `N.`; any number of readings, 2 or more),
   plus an overview of the differences;
2. **`reference/`** — the same entries collapsed into one line each, with common words
   written once and variants joined by `/`, made for producing a translation;
3. **`human/`** — the place for your own hand-corrected, variant-free text (the gold standard).

Works on any folder of aligned transcriptions. It needs nothing but Python 3 —
no pha install, no special tooling, no network.

---

## How to use it (step by step)

### What you need first

A folder containing **one subdirectory per palaeographer**, each with **one markdown
file per page, using the same filenames across all of them**, plus an **`images/`**
folder next to it with a copy of the same pages:

```
1577/
├── images/                      507v.jpg  508.jpg  508v.jpg  ...  511.jpg
└── palaeographers/
    ├── DeepSeek-V4-FVE/         507v.md   508.md   508v.md   ...  511.md
    ├── M3/                      507v.md   508.md   508v.md   ...  511.md
    └── Qwen3.8-Max/             507v.md   508.md   508v.md   ...  511.md
```

Each page file needs its transcription (the readable text of the page). The `Notes` /
named-entity sections are ignored; they are only used as context.

The `images/` folder holds the **source image of every compared page**, with the same
basename as the page file (`507v.jpg` for `507v.md`; `.jpg`/`.jpeg`/`.png`/`.tif`/
`.tiff`/`.webp` accepted). It is required: a reviewer correcting a reading needs the
original next to it, and it keeps the benchmark self-contained. `verify_comparison.py`
checks that every page has its image.

### 1. Ask the agent

Give it the folder and fix the numbering — that numbering becomes `1.`, `2.`, … `N.`
in every output file (any number of palaeographers works — 2 or more):

> Compare the palaeographer readings in
> `benchmarks/collections/jesuit-catalogues/jesuit-cat-type4/portugal/1577/palaeographers`
> — **1 = DeepSeek-V4-FVE, 2 = M3, 3 = Qwen3.8-Max** — and produce the per-page
> comparison plus an overview.

If you don't give an order, the agent will ask you or use folder order. The skill also
triggers on plain requests such as *"compare these transcriptions"*, *"collate the
readings"*, or *"which readings disagree on this page?"*.

### 2. Get the comparison

The agent creates a `comparison/` folder inside the one you named:

- `comparison/overview.md` — what the document is, who the palaeographers are, how much
  they agree, the systematic spelling habits of each, and the most significant
  disagreements per page.
- `comparison/<page>.md` — one file per page, every entry numbered `[n]` with the three
  readings stacked (`= all readings:` when they match), and a "Key differences" list.

### 3. Review it

Open `overview.md` first to see where the readings disagree, then dip into the page
files for the exact wording. Anything one palaeographer left as `[illegible]` while
another read a name is worth a look — those are the decisions that matter.

### 4. Build the reference text (for translation)

Ask: *"now build the reference text from the comparison"*. You get `reference/<page>.md`,
one line per entry:

```
[7] P./P[adr]e Iuan/Juan Correa/correa —————————/— Ministro.
```

Read it as: the readings agree on everything except the variants separated by `/`
(here `P.` / `P[adr]e`, `Iuan` / `Juan`, `Correa` / `correa`, a long dash / a short one).
Common words appear once. This is the working text to translate from — where variants
are listed, pick the correct reading (or simply skip them if the variant does not change
the sense).

### 5. Record your own correction (human gold standard)

Copy `reference/` to a sibling folder named `human/` and edit it by hand: resolve the
variants, restore the original line breaks, and note your editing rules at the top
of each file.

**Important:** `human/` is *yours*. Agents are told to treat it as **read-only** — they
must never edit, reformat, or regenerate it. Its value is as the authoritative text,
used later to **benchmark how accurate each palaeographer was** and to **improve the
transcription prompts**.

### Adding pages or a new palaeographer later

Just add the files (same filenames in every palaeographer folder) and re-run the request.
New pages get their own files; existing ones are regenerated, so re-check anything you had
edited by hand in `comparison/` or `reference/` — `human/` is never touched.

---

## What each output folder contains

| Folder | Who writes it | What it is |
|---|---|---|
| `comparison/` | the agent | Aligned readings, `[n]` per entry, stacked `1.`…`N.`, plus `overview.md` |
| `reference/` | the agent (`make_reference.py`) | One line per entry, variants joined by `/` — for translation |
| `human/` | **you** | Hand-corrected, variant-free gold standard. Read-only for agents |

## Useful commands (optional)

The agent runs these for you; you can also run them yourself to check the output:

```bash
# force the uniform layout (one fenced block per page file) and report changes
python3 scripts/normalize_comparison.py comparison/

# check every page file is well formed (2 fences, correct sections, entries [1]..[N])
python3 scripts/verify_comparison.py comparison/

# ...and check every entry carries exactly N readings
python3 scripts/verify_comparison.py comparison/ --readings 3

# (re)build the reference text from the comparison
python3 scripts/make_reference.py comparison/            # writes reference/ next to comparison/
python3 scripts/make_reference.py comparison/ reference/ # explicit destination

# guard against a wrong reading count (refuses to write if a page has more readings)
python3 scripts/make_reference.py comparison/ --readings 3
```

Requires only Python 3 (standard library).

## Notes and gotchas

- **Uniform layout matters.** Every page file must have the same shape: title, one
  `Context:` paragraph, one `## Entry-by-entry comparison` block with all `[n]` entries in
  a *single* fenced code block, and one `## Key differences on this page` list. The
  `normalize_comparison.py` + `verify_comparison.py` pair keeps that true.
- **Readings are never "corrected"** by the skill: diacritics, `[?]`, `[illegible]`,
  bracket expansions and odd spellings are preserved exactly as the palaeographer wrote them.
- **Pages that cannot be compared** (one reads a blank leaf, another an index, a third
  show-through) are auto-detected and each full reading is kept, rather than forced into a
  misleading alignment.
- **Any number of readings works** — 2, 3, 4 or more. Pass `--readings N` so that a page
  with more readings than you expect is refused loudly instead of being silently merged
  wrongly (the reference generator is N-ary; without the flag it auto-detects the count).
- **Inside a pha archive**, work in a benchmark/working folder like the one above and never
  edit pha's generated files (`library/`, `renders/`, `archive.db`).

## Reference implementation

`palaeographers/comparison/`, `palaeographers/reference/` and `palaeographers/human/` under
the 1577 Portuguese Jesuit catalogue benchmark — 8 pages, 3 palaeographers
(DeepSeek-V4-FVE, M3, Qwen3.8-Max).

## Deploy / publish

```bash
cd ~/develop/historical-skills && ./deploy.sh   # copies to ~/.agents/skills/
```

To share: create a GitHub repo for this directory, push it, and others can install with
`npx skills add github.com/USER/palaeographers-compare`.

## License

MIT
