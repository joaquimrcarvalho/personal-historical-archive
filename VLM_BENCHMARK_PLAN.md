# VLM_BENCHMARK_PLAN.md — a public benchmark of vision models on historical material

**Status:** proposal, not implemented. Nothing in this document exists yet; the
independent repository it describes has not been created.

**Companion:** `VLM_BENCHMARK_INFRA_PLAN.md` is the build specification —
schemas, tools, scoring engine, site generator, CI and tests. It is deliberately
sample-agnostic: **the choice of which pages to publish is taken first and
separately** (supported by the `hvb inventory` tool specified there), and the
infrastructure consumes whatever sample is committed. Treat §3–§5 below as
rationale for the layout and the reference-reading design, not as a page list;
the corpus table in §4 is a menu of what the archive holds, not a selection.

**Question it answers for a historian:** *which vision model should I use to read
my documents — and what will it cost, how long will it take, and where will it
fail?*

---

## 0. Summary

There is a **catalogue** of VLM benchmarks (Overshoot) and there is **no
measurement** of vision models on early-modern historical material. The
`jesuit-archive` already holds most of the expensive part of such a
measurement: repeated readings of the same pages by different models, one
human-corrected reading, and — in its `pha.yaml` comments — hard-won
per-model verdicts. What is missing is (a) a machine-checkable score, and
(b) somewhere a historian can look at it.

The plan is a small, separate, static repository that publishes:

1. a **tiny sample of real pages** — manuscripts, 18th-century printed books,
   and modern critical editions such as *Documenta Indica*;
2. a **reference reading per page**, human-signed, with the normalisation
   rules stated in the open;
3. a **per-page table** scoring each model's reading against that reference
   (CER, WER, personal-name accuracy, structure), plus each model's
   **access method** (local / local OCR engine / OpenRouter / direct API /
   agent-assisted), **speed** and **cost**;
4. the same numbers re-expressed in the historian's own units: *"transcribing
   your 3,612-page Franco collection with this model costs $X and takes Y
   days"*;
5. a GitHub Pages site (no JavaScript framework) plus `results.json` /
   `results.csv` for reuse in papers.

The honest constraint, verified in §2.4: **only one page currently has a
human-corrected reference reading.** So the site ships with one scored page
and shows reference coverage as a progress meter. Growing the gold — page by
page, by a named reviewer — *is* the project, not a prerequisite for it.

---

## 1. Is `Overshoot-ai/vlm-benchmarks` useful?

**Verdict: useful as the literature layer, useless as the decision layer.
Adopt it for §9.4 of the methodology page; do not build on it.**

### 1.1 What it actually is

It is a **catalogue of benchmark papers**, not a benchmark and not a harness.
Per its [README](https://github.com/Overshoot-ai/vlm-benchmarks):

- **3,187** benchmarks, updated daily by a GitHub Action that scans arXiv,
  classifies each new paper with Claude, and commits the entry;
- data in `data/benchmarks.json` and `data/benchmarks.csv`, MIT-licensed;
- fields: `benchmark_name`, `category`, `num_samples`, `modalities`,
  `task_types`, `description`, `repo_links`, `paper_title`, `arxiv_id`,
  `published`, `authors`;
- 22 categories; a companion search UI at
  <https://vlm-benchmarks-search.vercel.app>.

There are **no model scores, no images, no questions, no ground truth and no
runner** in the repository. It is an index of *papers about* benchmarks.

### 1.2 What it is genuinely good for

- **Literature discovery.** Finding the existing OCR/document benchmarks, their
  metrics and their datasets, so this project does not re-invent CER/WER
  reporting or a layout schema.
- **Justifying the gap** in a methodology section, with a citable, dated census
  rather than an assertion.
- **Cross-linking.** Its `repo_links` field points at the code/data of
  benchmarks worth reading.

### 1.3 What it cannot do (measured, 2026-09-18)

I downloaded `data/benchmarks.csv` (3,187 rows) and filtered it:

| query | result |
|---|---|
| whole catalogue | 3,187 entries |
| `document_ocr` category | **128** entries |
| text mentions *handwrit-* | **14** |
| text mentions *manuscript / paleograph / medieval / incunabula / papyrus / cuneiform* | **2** |
| text mentions *historical document / early modern / 16th / 17th century / 18th century* | **0** |

The 14 "handwritten" entries are, without exception, **modern** handwriting:
student maths work (`DrawEduMath`, `MathCog`, `FERMAT`, `EDU-CIRCUIT-HW`),
post-WWII care-and-maintenance forms (`CM1`), contemporary multilingual notes
(`HW-MLVQA`, 1,600 pages), or general document parsers that include a
handwritten subset (`OmniDocBench`, `OCRBench v2`, `DISCO`, `NoTeS-Bank`). The
only "manuscript" hit in the entire catalogue is `TimeTravel`, and it is
visual-question-answering about cultural artefacts across 266 cultures — not
transcription of a hand.

**Caveat, stated plainly:** the catalogue's `description` text is
machine-generated from arXiv abstracts, and the HTR tradition publishes in
ICDAR/ICFHR proceedings rather than arXiv. So absence here is *weak* evidence
about the field and *strong* evidence about this catalogue: it under-covers the
handwriting-recognition literature by construction. That is itself a finding
worth writing down.

### 1.4 Why that leaves your question unanswered

Neither this catalogue nor any entry in it can tell you:

- whether a model **breaks down** on show-through, a rotated final leaf, or a
  dense two-column folio;
- whether it **fabricates a page** when handed a blank one (documented in this
  archive: a model did exactly that before the blank-page guard existed);
- whether it reads *Manuel Aluarez* where the hand says *Manuel Rodrigues* —
  i.e. whether the **names**, the only thing a prosopographer needs, are right;
- what it costs and how long it takes **at collection scale**;
- how to reach it (a local 4-bit build on a 24 GB Mac, an OpenRouter slug, or a
  vendor API with its own key).

Those are the columns the site in this plan publishes. **Use Overshoot to find
the papers; build the measurement here** — because the measurement already
half-exists in the archive (§2).

---

## 2. The asset that already exists

All of this is on disk today under `jesuit-archive/benchmarks/` and
`jesuit-archive/palaeographers/`. None of it is scored, versioned or published.

### 2.1 Three material classes, three corpora

| corpus | material class | source | pages | readings | reference | human gold |
|---|---|---|---|---|---|---|
| **1577 Catálogo Brevis** | **manuscript** — Iberian hand, 1 Jan 1577, Jesuit Province of Portugal | archive MS (images `507v.jpg`…`511.jpg`) | 8 | 3 complete + 6 partial | ✅ slashed variants | ⚠️ **1 of 8 pages** |
| **Franco, *Imagem da virtude*** | **old printed** — Portuguese, 1714–1719, 1- and 2-column, long-ſ, Latin quotations | HathiTrust PDFs | 8 (2 per volume) | 6 | ✅ slashed variants | ❌ folder empty |
| **BPE *Memorial das Missões*** | **manuscript** — 17th-c. Portuguese italic/humanistic cursive register | BPE Cód. CXV 2-8, nº 9 | 53 (sample needed) | 2 | ✅ slashed variants | ❌ none |
| **Documenta Indica** | **critical edition** — MHSI, ed. Wicki, 20th-c. print, Latin/Portuguese/Italian/Spanish with apparatus + footnotes | archive PDFs (3 vols in, 12+ in inbox) | 0 read | 0 | ❌ | ❌ |

### 2.2 The 1577 manuscript corpus, in detail

The richest one, and the template. `benchmarks/collections/jesuit-catalogues/
jesuit-cat-type4/portugal/1577/palaeographers/`:

- `DeepSeek-V4-FVE/`, `M3/`, `Qwen3.8-Max/` — **8 pages each**, complete;
- `qwen3vl8b/` (2 pp), `glm46v/` (2 pp), `qwen3vl4b/` (1), `gemma412b/` (1),
  `gemma4e4b-gguf/` (1), `gemma4e4b-mlx4bit/` (1) — **partial**, one page each;
- `comparison/` — 8 per-page files, entry-by-entry stacked readings, plus an
  `overview.md` that is already a model-comparison report;
- `reference/` — 8 pages, variants merged into slashed lines;
- `human/` — 8 files, of which **only `507v.md` is human-corrected**.

That last point is the finding that shapes the schedule. `human/README.md`
documents the *reference* format (it is the reference folder's own README, not
a review log). Only `507v.md` carries a `## Corrected text` section, a reviewer
signature and a stated editing protocol:

> *Joaquim Carvalho correction started in 2026-08-30* — reintroduced the
> original line breaks; kept the original capitalisation; preserved dots as
> information separators; all long dash runs reduced to three dashes;
> **`u` → `v`** as particular to this hand; **`ƒ` → `ss`/`s`** as a variant
> particular to this hand.

The other seven `human/` files differ from `reference/` only in boilerplate
wording. **They are not gold.** Any plan that scores 8 pages on day one would
be scoring against a machine-merged consensus and calling it human truth.

### 2.3 The Franco corpus, in detail

`benchmarks/collections/franco-imagens/imagem-da-virtude/palaeographers/`, 8
pages, 6 readings, with an `overview.md` §2 that already records provenance
per reader: `tesseract-psm3` (tesseract 5.5.3, `por+lat+fra+eng`, 300 dpi);
`qwen3-vl-4b` and `gemma4-local`/`gemma4-e4b` (LM Studio); `M3`
(api.minimax.io); `agent-vision` (the agent's own reading from 300 dpi column
crops). Pages were chosen deliberately as each volume's **cleanest and
hardest**, spanning 1-column and 2-column layouts.

This corpus is where the **normalisation problem** bites: the six readings
split systematically on the printed long s — `f` throughout (tesseract, and
`agent-vision` following the print), mixed `f`/`ſ`, mostly modern `s`, modern
throughout (M3). A single CER number would flatter whichever convention the
gold happened to use. Hence §8.3.

### 2.4 The archive's own benchmarks, buried in comments

`jesuit-archive/dropbox/collections/archives/bpe/pha.yaml` contains a complete
four-model benchmark on pages 3, 20, 35, 50 of the *Memorial das Missões*, with
verdicts — `minimax-m3` best on the hand but drops entry numbers;
`deepseek-v4-flash-vision-exp` best structure but returned **0 characters on
4/4 pages** (a reasoning model spending its whole budget on thinking);
`qwen3-vl-235b-a22b` heavy surname garbling; `glm-5v-turbo` **unusable**,
consumed all 4,096 tokens as reasoning and returned nothing
(`finish_reason=length`).

Similarly the `documenta-indica` and `franco-imagens` sidecars record
degeneration and recall measurements (e.g. `qwen3-vl-4b` looping on the *Index
Generalis*; HathiTrust layer 92.0% vs tesseract 88.0% at 2.6 s/page).

These are exactly the results the site should publish — they are currently
readable only by someone who opens a YAML comment. **A benchmark result that
lives in a comment is not a result.**

### 2.5 The trap pages (the most valuable part)

Already identified, already on disk:

| trap | page | expected behaviour | why it matters |
|---|---|---|---|
| mirror show-through / rotated final leaf | 1577 p.511 | must not transcribe the facing page as if it were this one | three models read it three different ways (roster / index key / blank docket) |
| blank leaf with show-through | BPE p.052 | must return **nothing** | documented fabrication risk |
| repetition loop | Documenta Indica *Index Generalis* p.7 | must terminate, must not repeat | `qwen3-vl-4b` degenerated here |
| reasoning-budget exhaustion | BPE pp. 3/20/35/50 | must emit a transcription | two models returned 0 characters |
| column flatt — dense 2-column folio | Franco Coimbra v.2 | correct reading order (left column complete, then right) | `psm 6` and the HathiTrust layer both interleave |
| faint / damaged names | 1577 pp. 507v–510v | `[?]` / `[illegible]` rather than a guess | a confident wrong name is worse than an admitted gap |

No generic VLM benchmark tests any of these. They are the reason a
domain-specific site is worth building.

---

## 3. The repository

**Proposed name:** `pha-vlm-bench` → served at
`https://joaquimrcarvalho.github.io/pha-vlm-bench/`.
(Alternatives: `historical-vlm-bench`, `ler-o-passado`. The name should say
*historical material*, not *pha*, because historians are the audience; keep
`pha-vlm-bench` only if you want the tool link explicit.)

**Deliberately a separate repository, not a directory here.** Reasons:
GitHub Pages needs its own deploy target; the benchmark contains third-party
page images under other institutions' rights (§9.6); and results should be
citable and versioned independently of the tool. The pha repo keeps only this
plan.

```
pha-vlm-bench/
├─ README.md                  # for historians: what the table means, how to read it
├─ METHODOLOGY.md             # metrics, normalisation, limits, how to reproduce
├─ CONTRIBUTING.md            # how to correct a reference reading
├─ LICENSE                    # code: MIT. Content/images: per-corpus (see rights:)
├─ .github/
│  ├─ workflows/pages.yml     # build site/ and deploy to Pages on push
│  └─ ISSUE_TEMPLATE/correct-reference.md
├─ corpus/
│  └─ <corpus-id>/
│     ├─ corpus.yaml          # class, shelfmark, date, source, rights, normalisation profile
│     ├─ pages/
│     │  └─ <page-id>/
│     │     ├─ page.jpg       # committed derivative (long edge ≤1600 px, q80)
│     │     ├─ page.full.txt  # where to get the full-resolution original (URL)
│     │     ├─ reference.md   # THE reference reading (human-signed)
│     │     ├─ names.yaml     # the gold personal names / offices, for name accuracy
│     │     └─ readings/
│     │        └─ <model-id>/
│     │           ├─ reading.md   # the reading, verbatim, as the model emitted it
│     │           ├─ prompt.md    # the prompt that produced it (see §6.1)
│     │           └─ run.yaml     # provenance: how it was produced
│     └─ notes.md             # corpus-level observations (layout, hand, traps)
├─ models/                    # one ficha per model, mirroring pha's models/ convention
│  └─ <model-id>.yaml         # access method, endpoint, price, weights, size, params
├─ tools/
│  ├─ import_from_pha.py      # archive benchmark folders -> this repo's page-first layout
│  ├─ run_reading.py          # produce a new reading (delegates to `pha test`)
│  ├─ score.py                # reference vs reading -> CER/WER/names/structure
│  ├─ build_site.py           # corpus + results -> site/ (static HTML, no JS framework)
│  └─ lib/diff.py             # token/char alignment (adapted from palaeographers-compare)
├─ data/
│  ├─ results.json            # every score, machine-readable (committed)
│  └─ results.csv             # spreadsheet-friendly (committed)
└─ site/                      # generated, gitignored, deployed by CI
```

**Page-first, not model-first.** The archive stores `palaeographers/<MODEL>/<page>.md`
because that is how the readings are produced. The site is read page-first
("show me 507v and who read it how"), so the repository layout is page-first
and `import_from_pha.py` is the bridge. The importer is the only piece that
knows about the archive.

---

## 4. The sample: what gets published

Small, deliberate, and labelled by material class — the three classes the
archive actually holds, plus the traps.

| tier | corpus | material class | pages | why these pages |
|---|---|---|---|---|
| **A — scored now** | 1577 Catálogo Brevis | manuscript, 16th-c. Iberian hand | 1 (507v) | the only human-corrected reference that exists |
| **B — readings exist, gold next** | 1577 Catálogo Brevis | manuscript | +7 (508–511, both sides) | finish the gold on material already read 3× |
| | BPE *Memorial das Missões* | manuscript, 17th-c. cursive register | 8 of 53 | a *different* hand and a list register, not prose; two readings ready |
| | Franco *Imagem da virtude* | old printed, 1714–1719 | 8 | 6 readings incl. a local OCR engine; 1- and 2-column |
| **C — new readings needed** | Documenta Indica | **critical edition** | 8 | the class named in the brief; apparatus + footnotes + mixed languages |
| | *DocHistMissPadPortOriente* | critical edition, dense 16th-c. print | 4 | 12 volumes already in the archive; a second critical-edition shape |
| **Traps — separately labelled** | all of the above | — | 6 | §2.5; these carry an *expected behaviour*, not a score |

Total scored surface at full delivery: **~38 pages**. Small on purpose. Eight
pages cannot establish a ranking, and the site will say so in as many words
(§8.5); what it can do is catch a model that **fails**, on material that
matters, with evidence a reader can check by looking at the page.

**Sample selection rule (must be written down and enforced):** each corpus
contributes its *cleanest* and its *hardest* page per volume/section, chosen
before any model is run, recorded in `corpus.yaml`. Otherwise pages get picked
because they flatter a model.

---

## 5. The reference reading (gold)

### 5.1 Format

One `reference.md` per page:

```markdown
# 507v — Reference reading
Corpus: 1577-catalogo-brevis · class: manuscript
Reviewed by: Joaquim Carvalho · date: 2026-08-30 · status: human-corrected

## Editing rules applied
- original line breaks kept; line-end hyphen kept in its "=" form
- original capitalisation kept
- dots kept as information separators
- long dash runs reduced to three dashes
- u → v, ƒ → ss/s (particular to this hand)

## Reference reading
```
[1] + IESVS.
[2] Padres, y hermanos de la Pro=
vincia de Portugal. 1.º de henero
de. 1577.
[3] P. Manuel Rodrigues[Roĩz] professo de 4 votos. —
Provincial.
```

## Notes
Reading notes, difficult words, and any reading still in doubt.
```

Three states, shown as badges on the site:
**`human-corrected`** (signed, eligible to score) ·
**`machine-merged`** (from `make_reference.py`, shown but **scores are
provisional and greyed**) ·
**`pending`** (nothing; models listed unscored).

### 5.2 The reviewer's rules are part of the artefact

The `## Editing rules applied` block is not decoration: it is what makes the
score reproducible and the convention auditable. A reference reading without
stated rules is not a gold standard, it is one more reading.

### 5.3 Names are extracted separately and reviewed

`names.yaml` holds the entries the name-accuracy metric is computed over:

```yaml
page: 507v
entries:
  - gold: "Manuel Rodrigues"      # normalised for comparison
    as_written: "Manuel Rodrigues[Roĩz]"
    office: Provincial
    variants_accepted: ["Rodriguez", "Roiz", "Rois"]
  - gold: "Juan Freire"
    office: Procurador
    variants_accepted: ["Iuan Freire", "João Freire"]
```

Derived from the gold by rule (the catalogues are regular: `P. <Name> —
<office>`), then **hand-checked**, because this file decides the column
historians will actually read. `variants_accepted` exists because
`Rodrigues`/`Roiz` is a genuine orthographic range, not a misreading — the
distinction between a spelling variant and a misread name is the whole point,
and it must be a human judgement recorded in the open.

### 5.4 Growing the gold is the workstream

`507v` proves the protocol. The remaining ~37 pages need the same treatment
from a named reviewer. The site is designed so this is *visible and
incremental*: every page shows its badge, the front page shows a coverage bar
(`gold: 1 / 38 pages`), and a newly signed page immediately changes the scores.
For the seven 1577 pages the raw materials are already in place — three
independent readings and a slashed-variant reference — so the reviewer is
choosing between candidates, not transcribing from scratch, which is far
cheaper per page.

---

## 6. Provenance and access method

Every reading carries a `<model-id>/run.yaml`. This is non-negotiable: a score
whose model version, render settings **or prompt** are unknown is not a
measurement.

```yaml
# readings/minimax-m3/run.yaml
model_id: minimax-m3
model_name: MiniMax-M3                 # the server-side model string
access: direct-api                     # local | local-ocr-engine | openrouter | direct-api | agent
vendor: MiniMax
endpoint: https://api.minimax.io/v1
weights: proprietary                   # or open-weights
quant: null                            # e.g. mlx-8bit, gguf-q4_k_m
runs_on_24gb_mac: false
run_date: 2026-08-29
produced_by: pha test --pages 1        # the exact command / tool path
render: {dpi: 300, max_image_px: 3000, jpeg_quality: 55}
params: {temperature: 0.1, max_tokens: 4096, thinking: disabled}
measured:
  latency_s: 41.2
  cost_usd: 0.031
  tokens_in: 4820
  tokens_out: 3610
prompt:                                # the prompt used — stored, not just referenced
  file: prompt.md                      # verbatim, beside the reading
  sha256: 4b1e…
  captured: exact                      # exact | reconstructed | unavailable | not-applicable
  layers:                              # every layer it was composed from, in order
    - {role: palaeographer-base, path: palaeographers/jesuit-cat4.md, sha256: 9f2c…}
    - {role: document-default, path: "builtin:extract.py:DEFAULT_PROMPT", sha256: 7a03…}
reproducible: true
notes: "best on this hand; prone to dropping an entry number"
```

### 6.1 The prompt is stored with the reading

A rules *id* plus a hash is not enough: what the model actually receives is
composed from **up to three layers** (the palaeographer rules body, a
document/collection `<stem>.prompt.md` or the builtin one-line default, and a
two-line per-page header), and any layer can be edited afterwards, silently
changing what an old score means. So each reading stores `prompt.md` — the text
as sent, verbatim — and `run.yaml.prompt` records the layers it came from.

Three honest capture modes, never conflated: **`exact`** (harvested from the run
that produced the reading), **`reconstructed`** (rebuilt from files that
provably predate it — flagged on the site), **`unavailable`** (a layer changed
since, so the prompt is *not recorded*, not guessed), and
**`not-applicable`** for OCR engines and agent readings, where the engine
settings or the agent's instruction replace it. Full mechanics, the verified
status of the corpora that already exist, and the small `pha test` change that
would make future capture exact: `VLM_BENCHMARK_INFRA_PLAN.md` §5.5.

**Access methods and how the site shows them:**

| badge | meaning | cost | runs where |
|---|---|---|---|
| 🖥️ **local** | LM Studio / Ollama on the archive machine | $0 | your hardware (24 GB in this archive) |
| 🔤 **local OCR engine** | tesseract / liteparse — not an LLM, no model server, no prompt | $0 | anywhere, ~2.6 s/page |
| 🔀 **OpenRouter** | one key, many models | per-token | someone else's GPUs |
| ☁️ **direct API** | vendor endpoint (DeepSeek, MiniMax, …), vendor key | per-token | vendor |
| 🤖 **agent-assisted** | an interactive agent's own reading from crops | $0-ish | not reproducible — **never ranked** |

`agent-vision` in the Franco corpus is 🤖. It is honest and useful (it shows
the ceiling a careful human-supervised reading reaches) but it must be visually
separated from the ranked models, or it silently poisons the table.

**The one legacy gap, flagged rather than papered over:** `Qwen3.8-Max` (1577,
8 pages) has **no recorded provenance anywhere** — no `models/` ficha, no
sidecar mention, nothing in the archive. `import_from_pha.py` must **refuse to
import a reading without provenance**; the page renders it as
`provenance: unknown — not ranked` until the model string, endpoint and date
are confirmed. Silently guessing which "Qwen3.8-Max" it was would produce a
confidently wrong row, which is worse than no row (the same rule the archive
already applies to bibliographic references).

---

## 7. Producing readings

**Principle: score the model as the archive actually uses it** — its shipped
palaeographer prompt, its render settings, its thinking flag. A benchmark of a
model nobody will run is theatre.

So `run_reading.py` **delegates to pha**:

```bash
pha test <doc> --pages 1 \
  --palaeographer jesuit-cat4 --model minimax-m3 \
  --temperature 0.1 --max-tokens 4096
```

`pha test` already: resolves the same `pha.yaml` the real run uses, writes to
`.pha-test/` **without touching the DB, library or renders**, prints a
`report.md`, and honours the model-server lock. The runner harvests the
transcription from `.pha-test/…`, writes `readings/<model-id>.md` verbatim, and
fills in `run.yaml` from the resolved config plus the API `usage` block.

Consequences the plan must respect:

- **One model per server** still holds: readings for two models on the same
  LM Studio instance must run sequentially. The runner serialises per
  `models/<id>.yaml`'s `server:` key and refuses if a job holds it.
- **Free/local where possible.** Local models and OCR engines are $0, which
  makes weekly refreshes feasible for anyone who clones the repo; the paid rows
  are produced once and dated.
- **`reproduce.sh` per reading** (generated into the site) so a third party can
  re-run one row without reading this document.
- Readings carry their **age**. The site flags a reading older than ~6 months,
  or older than the model's last version change, as `stale`.

---

## 8. Scoring

### 8.1 What is computed per (page × model)

| metric | definition | why |
|---|---|---|
| **CER** | `(S+D+I) / N` over characters of the gold, after alignment | the standard HTR/OCR figure; comparable to the literature §1.2 |
| **WER** | the same over whitespace tokens | catches word-boundary and hyphenation damage that CER hides |
| **Name accuracy** | entries in `names.yaml` read exactly (or as an accepted variant) / total | the only column a prosopographer needs |
| **Structure** | entry count vs gold; reading-order correct (Y/N) | catches column flattening and dropped/reshuffled entries |
| **Trap verdict** | pass/fail against the page's declared expected behaviour | blank → blank; no repetition; no fabrication |
| **Speed** | seconds per page | decides whether a 3,612-page collection is feasible |
| **Cost** | measured `$` per page, and `$` per 1,000 pages | same |

### 8.2 Alignment

Reuse the token-level DP alignment already written for the
`palaeographers-compare` skill (`scripts/make_reference.py` does N-way
sequence alignment with insertion/deletion handling). Scoring needs the 2-way
case, gold ↔ reading, which is a subset — extract it into `tools/lib/diff.py`
rather than re-deriving edit distance. The same aligner drives the
**per-page visual diff** on the site (character-level `<del>`/`<ins>`), so one
piece of code backs both the number and the evidence for it.

Treat as padding (never as errors), declared per corpus: trailing
periods/commas, line-end hyphenation, long dash runs, the name↔role connector.
This is exactly the padding set `make_reference.py` already ignores.

### 8.3 Two normalisation profiles, two CER columns — the key design decision

The Franco corpus settles this: the readings split over the printed long s
(`f` / `ſ` / modern `s`), and `u`/`v`, `i`/`j`, and the 1577 reviewer's own
`u → v`, `ƒ → ss` rules all exist. One CER would reward whichever convention
the gold happened to use.

So every page is scored twice, and both columns are shown:

- **Diplomatic CER** — nothing folded. Rewards fidelity to the printed/hand
  form. Tesseract and `agent-vision` look good here; a model that silently
  modernises looks bad.
- **Normalised CER** — long ſ→s, u/v and i/j folded, line-end hyphens joined,
  whitespace collapsed, per the corpus's declared profile. Measures *reading
  content*. M3 looks good here; tesseract's OCR noise shows.

A model that is only good on one column is exactly the model a historian needs
to know about, and the pair of numbers says which kind of good it is. **The
normalisation is applied identically to the gold and to the reading**, and the
profile is printed on the site next to the scores — never applied to the
reading alone.

### 8.4 Names, not averages

A model can win on CER and still be useless, because its errors cluster on the
names. The 1577 overview is full of this: `Aluarez`/`Rodrigues`,
`ferrando`/`Serrano`, `Barrera`/`Barreira`, `Prouizador`/`Procurador`. The
leaderboard therefore leads with **name accuracy**, with CER as the secondary
column, and the site lets a reader sort by either.

### 8.5 Honesty rules (written into the site, not just the code)

- No single global ranking. Scores are shown **per page**, grouped by material
  class; an aggregate is shown only with its per-page spread.
- Every aggregate carries `n = <pages>` and the sentence *"a sample of 8 pages
  is a screen for failure, not a ranking."*
- A row with fewer than the corpus's full page count is marked **partial** and
  excluded from aggregates (the six partial 1577 readings).
- A row scored against a `machine-merged` rather than `human-corrected`
  reference is **greyed and labelled provisional**.
- `agent-assisted` rows are never ranked (§6).
- Every published number links to the two texts it was computed from.

---

## 9. The site

### 9.1 Build

`tools/build_site.py` → `site/`: plain static HTML + one CSS file, **no
JavaScript framework** (a few lines of CSS for zoom and `<details>` for
disclosure). Rationale: tables must be real HTML (printable, screen-reader
friendly, citable), the thing must still render in ten years, and historians
should be able to read it on a phone. CI builds `site/` on every push and
deploys to GitHub Pages.

### 9.2 The page view — the heart of it

For each page (`site/pages/<corpus>/<page>.html`):

```
┌─────────────────────────┬──────────────────────────────────────────────┐
│                         │  REFERENCE READING  (badge: human-corrected) │
│      the page image     │  [3] P. Manuel Rodrigues professo de 4 …     │
│      (zoomable)         │                                              │
│                         ├──────────────────────────────────────────────┤
│                         │  Minnie  |  1. Pº Manuel [illegible] …       │
│                         │  M3      |  2. P[adr]e Manuel Aluarez …      │
│                         │  Qwen3.8 |  3. P. Manuel Rodr[igue]z …       │
│                         │            ^character-level diff highlighting│
│                         ├──────────────────────────────────────────────┤
│                         │  model | CER-dip | CER-norm | names | s/pg … │
└─────────────────────────┴──────────────────────────────────────────────┘
```

- the image and the reference side by side, so *any* score can be checked by
  eye in one screen — this is the single most important usability property;
- a per-page table under it;
- a "how this was scored" `<details>` printing the normalisation profile and
  padding rules actually applied;
- a "what each model was told" `<details>` printing the prompt stored with each
  reading (§6.1) and its capture mode — an exact prompt, a flagged
  reconstruction, or an explicit *not recorded*.

### 9.3 The rest of the site

| page | content |
|---|---|
| `index.html` | what this is, the legend (🖥️🔤🔀☁️🤖), the gold-coverage bar, the leaderboard **by material class**, and the "how to choose a model" procedure (§10) |
| `corpora/<id>.html` | one corpus: what it is, the hand/print, layout, page grid with badges, corpus-level notes and traps |
| `material/<class>.html` | manuscript / old-printed / critical-edition views — *the* filter a historian arrives with |
| `models/<id>.html` | one model: access method, endpoint, price, weights, quant, runs-on-24 GB, every page it read, its failure modes |
| `traps.html` | the six trap pages and who failed them |
| `methodology.html` | §8 in full, the Overshoot finding §1, limits, how to reproduce |
| `data/results.json`, `data/results.csv` | every score; documented schema; the citable artefact |

### 9.4 The historian's question, answered on the front page

The numbers are also expressed in the units of the actual job. Every model row
carries a computed line:

> **MiniMax-M3** — 94.1% of names correct on the 1577 catalogue; $0.031 and
> 41 s per page → the Franco collection (3,612 pp) in **≈41 h and ≈$112**;
> the whole archive (24,263 pp) in **≈276 h and ≈$752**. Local, $0:
> `gemma4-local` at 3.6 min/page → 3,612 pp in **≈9 days** on a 24 GB Mac.

This is what turns a benchmark table into a decision. The collection page
counts come from `pha status` and are stated with the date they were read.

### 9.5 What is *not* on the site

No live model calls (a public page must not spend the owner's API budget), no
login, no analytics, no JavaScript framework, no auto-refresh of paid rows.
Refreshes are explicit and committed, so every published number has a date and
a diff.

### 9.6 Rights and images — a decision to make before publishing

The three classes have different rights, and this must be settled before the
repository goes public:

- **Franco 1714–1719** — the work is public domain; the scans are HathiTrust's.
  Thumbnail + link to the HathiTrust page, with a `source_url`.
- **BPE *Memorial das Missões*** — a 17th-c. manuscript; the digital images are
  BPE's and usually carry an open licence, but it must be checked per item and
  recorded in `corpus.yaml` `rights:`.
- **Documenta Indica (MHSI, Wicki, 20th-c.)** — **in copyright.** The safe
  default: publish a small, low-resolution thumbnail under a study/quotation
  rationale, carry a prominent rights statement and a link to the publisher,
  and make `page.full.txt` a pointer rather than a file. If that is
  uncomfortable, exclude the images and publish only the reference readings and
  scores with a link — the benchmark still works, because the *readings* are
  what is scored.

`corpus.yaml` therefore requires `rights:` and `source_url:`, and the build
**fails** if either is missing.

---

## 10. How a historian uses it to choose a model

The site's front page states this procedure explicitly:

1. **Filter by material class first** — manuscript ≠ old printed ≠ critical
   edition. A model good on 18th-c. print can be worthless on a 16th-c. hand.
2. **Filter by what you can actually run** — do you have a 24 GB Mac (🖥️/🔤),
   a card for OpenRouter (🔀), or vendor keys (☁️)?
3. **Read the name column, not the average** (§8.4).
4. **Check the trap pages for your failure mode** — show-through, blank
   leaves, two-column folios, repetition loops, reasoning-budget exhaustion
   (§2.5). A model that fabricates on a blank page is disqualified regardless
   of its CER.
5. **Do the arithmetic** in your own collection's pages (§9.4).
6. **Then confirm on your own pages** with `pha test <your-doc> --pages 3
   --model <candidate>` before committing to a full pass — the site's job is to
   shortlist, not to decide for your documents.
7. **Re-check when a model version changes**: readings carry dates and the site
   flags stale ones (§7).

---

## 11. Phased delivery

Each phase ends with something published and honest.

**P0 — one real page, live.** Create the repository; `import_from_pha.py` for
the 1577 corpus (3 full readings + 6 partials, provenance reconstructed from
`comparison/overview.md` §2; `Qwen3.8-Max` flagged `provenance: unknown`); the
single human-corrected page (507v) plus its `names.yaml`; `score.py` with both
CER profiles; `build_site.py`; Pages workflow. **Deliverable: a working site
with 1 scored page, 7 `gold pending`, and a visible coverage bar.** This proves
the whole pipeline on the least material and produces no unearned claims.

**P1 — the manuscript class.** Reviewer produces gold for 1577 pp. 508–511
(from the existing comparisons — cheap per page); sample and import 8 pages of
the BPE *Memorial* with its 2 readings and produce their gold. **Deliverable:
~17 scored manuscript pages, one full corpus, one handwritten register.**

**P2 — the old-printed class.** Import the Franco 8 pages with all 6 readings
(including `tesseract-psm3` as a 🔤 row and `agent-vision` as an unranked 🤖
row); produce gold; add the two-column folio trap explicitly. **Deliverable:
the print-vs-manuscript contrast, and the two-CER-column story made concrete.**

**P3 — the critical-edition class.** Produce first readings for *Documenta
Indica* (8 pages, incl. the *Index Generalis* p.7 repetition trap and an
apparatus-heavy page) and *DocHistMissPadPortOriente* (4 pages); produce gold.
**Deliverable: all three classes named in the brief, scored.**

**P4 — keep it alive.** `CONTRIBUTING.md` + the correction issue template;
per-reading `reproduce.sh`; stale-reading flags; a documented local refresh
flow (`make refresh-local` — free rows only). **Deliverable: a benchmark that
can be maintained by a historian with help, not by the author alone.**

---

## 12. Open decisions and risks

| # | decision / risk | recommendation |
|---|---|---|
| 1 | **Repository name** (`pha-vlm-bench` vs `historical-vlm-bench`) | name it for the audience: `historical-vlm-bench`; keep the pha link in the README |
| 2 | **Gold production is the bottleneck** — ~37 pages need a named reviewer | accept it; make it visible; do P0 with 1 page rather than fake 8 |
| 3 | **Documenta Indica image rights** (§9.6) | thumbnail + rights note + link; be ready to drop images and keep scores |
| 4 | **`Qwen3.8-Max` provenance unknown** | flag and exclude from ranking; confirm or retire the reading |
| 5 | **Partial readings** (6 models read only 1–2 pages) | import them, mark `partial`, exclude from aggregates — they still prove a failure mode |
| 6 | **API cost of re-running paid rows** | paid rows are produced once and dated; only free/local rows refresh automatically |
| 7 | **Sampling bias** — 8 pages per corpus | pre-declare cleanest+hardest selection; never call an aggregate a ranking |
| 8 | **A model version changes silently** | record `model_name` + `run_date` per reading; flag stale rows; the site shows the date next to every number |
| 9 | **Reference reading drift** — a corrected gold changes old scores | scores are regenerated from committed inputs; `results.json` is rebuilt in CI, never hand-edited |
| 10 | **Scope creep into a general OCR benchmark** | the site is about *historical material for this archive's research*; the traps and the name metric are the differentiators, not breadth |

---

## 13. Files this plan would create (in the new repository)

Nothing has been created. P0 would add: `README.md`, `METHODOLOGY.md`,
`CONTRIBUTING.md`, `LICENSE`, `.github/workflows/pages.yml`,
`.github/ISSUE_TEMPLATE/correct-reference.md`, `corpus/1577-catalogo-brevis/
{corpus.yaml, notes.md, pages/507v/…}`, `models/*.yaml`,
`tools/{import_from_pha.py, run_reading.py, score.py, build_site.py,
lib/diff.py}`, `data/{results.json, results.csv}`.

In **this** repository, the only artefact is this document.

---

## 14. Appendix — the Overshoot answer in one paragraph

`Overshoot-ai/vlm-benchmarks` is a daily-refreshed **catalogue of 3,187 VLM
benchmark papers** with a CSV/JSON index and no scores, images or harness. It
is worth using as the *literature* layer of this project's methodology page:
it locates the OCR/document benchmarks (128 entries in `document_ocr`) and
their metrics. It is not usable as the *decision* layer: filtering its 3,187
entries for handwritten material yields 14 results, all of them modern
handwriting or general document parsers, and filtering for early-modern or
historical-document material yields none — so it cannot tell you whether a
model reads *Manuel Rodrigues* or invents *Manuel Aluarez*, whether it
fabricates text on a blank leaf, or what a 3,612-page transcription would
cost. The archive already holds the material, the repeated readings and one
human-corrected gold page needed to answer those questions; this plan publishes
them.
