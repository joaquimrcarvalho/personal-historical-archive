# BENCHMARK_TOOL_PLAN.md — from a local comparison script to a pha benchmark workbench

**Status:** proposal for decision, 2026-11-14.
**Scope:** bring the *palaeographer benchmark* into the main distribution, and decide
where its interface lives.
**Companions:** `VLM_BENCHMARK_PLAN.md` and `VLM_BENCHMARK_INFRA_PLAN.md` (the
*public* benchmark — unchanged, still out of tree), `WEB_INTERFACE_PLAN.md`
(`pha web`), `SEARCH_WEB_SPEC.md` §5.5 / §14.1 (the "share the code, not the
service" doctrine this follows), `DSH_PLUGIN.md` (PHA View).

---

## 0. Decision, in four lines

**Two points confirmed by the user on 2026-11-14:** corpora live under the
archive's `benchmarks/` (§11.1), and the human gold keeps today's file layout
with a YAML header — option (b) (§11.2).

1. **The tool moves into pha.** A `pha bench` command group owns the corpus model,
   harvest, comparison, verification, scoring and export. The three scripts in
   `jesuit-archive/local-tools/` become the reference implementation of its first
   commands.
2. **The authoring interface is a separate local app**, started by `pha bench serve`
   and shipped inside the pha package. It is **not** a PHA View tab and **not** a
   set of write routes on `pha serve`.
3. **The read-only comparison is shared code**, not a second viewer: one page-render
   module feeds `pha serve`, the bench app and (later) the public static site. PHA
   View gets a deep link and, at most, a compact read-only panel.
4. **Publication stays separate and static** — the planned `hvb build` site, a
   different artefact with different rights. The workbench never publishes.

The rest of this document is the evidence and the build plan.

**Implementation status (2026-11-14):** P0–P3 are shipped. `bench.py` and the
`pha bench` group (`init`, `list`, `status`, `verify`, `compare`, `reference`,
`run`, `page`, `score`, `export`, `serve`) are the engine; `viewer.py` is the
shared page shell extracted from `serve.py`; `benchui.py` is the local writable
workbench (`pha bench serve`); `benchexport.py` writes the static site. Tests
live in `tests/test_bench.py`, `tests/test_benchui.py` and
`tests/test_benchscore.py`. All nine corpora under the archive's `benchmarks/`
carry a `bench.yaml`, have a comparison and, where a source is declared, serve
their page images; the 1577 and BNP `human/` files carry the decided front
matter (1577: one `human-corrected` page, seven `pending`). P4 (scoring) and P5
(export) are implemented; only the PHA View entry point and the skill-text
re-sync remain. Details in §14.

---

## 1. What exists today

| Piece | Where | What it is | State |
|---|---|---|---|
| Harvest driver | `jesuit-archive/local-tools/benchmark-palaeographers.py` | loops `pha test --page N --editor none` over readers × pages, harvests the scratch transcription, writes `<out>/<reader>/<page>.md` | works; `READERS` hard-coded; guard against `ERROR:` blocks; resumable |
| Comparison builder A | `local-tools/build-palaeographer-comparison.py` | 2-reading tabular comparison + overview, hard-coded `BASE`/`READERS` | one-off for the 1654–55 sample |
| Comparison builder B | `local-tools/build-benchmark-comparison.py` | N-reading variant of the same, hard-coded `BASE`/`PREF` | near-duplicate of A |
| Format authority | `skills/palaeographers-compare/` (shipped by pha) | corpus layout, uniform skeleton, `normalize_comparison.py`, `verify_comparison.py`, `make_reference.py`, `human/` rules | authoritative; each script re-implements the grammar itself |
| Corpora | `jesuit-archive/benchmarks/collections/…` (244 files, archive repo) | readings per reader, `comparison/`, `human/`, some `reference/` | the 1577 "reference implementation" has **no `images/`** — `verify_comparison.py` warns |
| Scratch run | `pha test` (`src/personal_historical_archive/testrun.py`) | per-page no-write run into `.pha-test/`, `prompts-sent/` capture, `--show`, `--dry-run` | shipped, and the only part currently in the distribution |
| Visualisation | — | none. The local tool's "infrastructure" is **markdown artefacts** (stacked readings + overview) plus the `human/` convention. The only designed visual layer is `hvb build`, a **static site generator that is not implemented** | nothing to fold in |

Six gaps follow directly:

- **G1 — no corpus model.** Readers, models, rules and document are encoded three
  times (driver table, two builders' globals). Adding a corpus means editing code.
- **G2 — no single parser.** Five independent implementations of the page-file
  grammar: two in the builders (reading → transcription: fenced body under
  `## Transcription`, or body before `## Notes`, or front matter), and three in
  the skill (`make_reference` parses the comparison's entries, `verify_comparison`
  counts readings, `normalize_comparison` rewrites the sections). They already
  disagree on the edges: `build-benchmark-comparison.py` understands a fenced
  `## Transcription`, `build-palaeographer-comparison.py` only strips a leading
  title and splits on `## Notes`. The archive's audit scripts
  (`audit-latin-leftover.py`, `local-model-test.py`) parse a further variant —
  library page files with YAML front matter.
- **G3 — the gold has no provenance.** Nothing records *which* readings a
  `human/` page was corrected against, so a later re-read silently invalidates
  the gold with no signal.
- **G4 — images are manual.** The skill's review loop needs `images/` next to the
  corpus; nothing produces it. The reference corpus itself does not have one.
- **G5 — no view.** Reviewing is reading markdown in an editor while hunting the
  page image elsewhere. The one property the site plan calls "the single most
  important usability property" — image and reference side by side — does not
  exist anywhere today.
- **G6 — no scoring.** Nothing compares model readings to the human gold, so the
  "benchmark" produces a collation, not a measurement.

---

## 2. What the interface actually has to do

The request mixes two jobs with opposite requirements. They must not be one
screen by accident.

| | **Look** (inspect) | **Author** (produce the gold) |
|---|---|---|
| User | historian, agent, visitor | the reviewer (Joaquim) + an agent |
| Frequency | many times, after the fact | once per page, during production |
| Lifetime | permanent, citable | working session |
| Needs | image + readings + scores, one screen; stable URL; no writes | image + readings + editable target, entry by entry; keyboard; save; validation; progress |
| Data | read-only corpus | corpus that the user **changes** |
| Failure that matters | showing a stale or wrong number | losing or silently corrupting human work |

Requirements the authoring screen must meet, as acceptance tests:

1. **One screen, no window juggling.** Page image (zoomable, native resolution
   available) beside the stacked readings and the corrected text.
2. **Entry-level work.** Each entry shows readings `1..N`; one action accepts a
   reading; editing is plain text; `[?]`, diacritics and line breaks survive
   verbatim.
3. **Nothing is destroyed.** Readings are never written. A save writes only the
   page's gold file, and only after the on-disk state is re-checked.
4. **Validated on save.** The same structural checks as `verify_comparison.py`
   run in-process; a failed check refuses the save and says why.
5. **Progress is visible.** Which pages are reviewed, which are untouched, and
   which gold files are stale because the readings changed underneath them.
6. **Runs where the archive runs.** No DSH, no Node, no build step, no network;
   loopback only; works on the Mac mini as on the laptop.
7. **The agent can drive it.** Every screen has a file/JSON equivalent, so the
   skill and the CLI remain a first-class path (batch work, 40 pages at a time).

---

## 3. Where it should live

### Options

| | A. PHA View tab | B. `pha serve` routes | C. `pha web` screen | D. `pha bench serve` (new local app) | E. static site only |
|---|---|---|---|---|---|
| Can write the gold | yes, with new mutation routes | no (read-only by construction) | yes | yes | no |
| Works without DSH | **no** | yes | yes | yes | yes |
| Fits the read-only contract | breaks it | breaks it | n/a | keeps both contracts | keeps it |
| Reachable data | archive DB + `pha` CLI | `archive.db` only | archive config + DB | the corpus files | exported corpus |
| Full-page workbench | no (chat view, no own URL) | yes | yes | yes | yes |
| Offline / local-only | yes | yes | yes | yes | n/a |
| Build cost | medium + DSH coupling | medium (and wrong place) | high, and unbuilt | medium | low (but no authoring) |
| Serves the historian later | only if DSH installed | yes | yes | yes | yes (public) |

### A. PHA View (`dsh-pha`) — no for the editor

PHA View is the right place to *look* and the wrong place to *write*:

- its data layer is deliberately read-only (`lib/index.js`, `immutable=1`, four
  mutation routes funnel through the `pha` CLI), and the whole plugin is built on
  "mutations go through pha so its locks and staleness rules are respected";
- it exists only on machines with DeepSeek Harness. The archive does not. A
  reviewer on the Mac mini, or a historian given a corpus, would not have it;
- it is a **view inside the chat client**, not a document workspace: `pha-root`
  fills the conversation area (`flex: 1`), but it has no browser tab or URL of
  its own, no print, and its mount point is the plugin's `dsh.client.inject`
  list — which `DSH_PLUGIN.md` itself calls "a best-effort guess against the
  installed client-module contract". A gold-editing tool meant to last years
  would inherit that fragility;
- the corpus is **not in `archive.db`**. It is a file tree under
  `benchmarks/`. So PHA View would either learn the corpus grammar in
  JavaScript (duplicating the parser) or read everything through new
  `pha bench` commands — which this plan builds for other reasons anyway, but
  which makes the plugin a client of an API that does not exist yet.

The honest case for PHA View: if `pha bench` exposes `show --json` and
`save --json`, the plugin can be a thin shell that keeps the "mutations go
through pha" discipline, and there is no second server to run. That is a real
option — it fails on audience, on having no URL of its own, and on the client
contract's fragility, not on architecture.

What PHA View *should* get is cheap and worth doing: a **"Benchmark" entry**
that lists corpora and deep-links to `pha bench serve`'s page view (and, later, a
compact read-only compare panel), plus `pha_bench_*` host tools so an agent can
read a page's readings and the gold without leaving chat. That is one route and
one button, not an editor.

### B. `pha serve` — no for writes, optional for a read-only view

`serve.py` is the archive's operator viewer: stdlib only, loopback, no auth, no
`do_POST`, DB opened `mode=ro` + `query_only=ON` (see its module docstring and
`_connect_ro`). More importantly it is the module `SEARCH_WEB_SPEC.md` names as
the **shared viewer** for the future public site (§5.5, §14.1): "share the code,
not the service". Putting a writable, corpus-backed authoring surface into it
would (a) destroy the property that module is being kept safe for, (b) add a
second data source and a second identity model to a 1 100-line viewer, and
(c) require auth/CSRF for a loopback server that has none.

A *read-only* `/bench/...` route in `pha serve` is defensible (same URL space,
citations, one process to run) but is not required: the bench app can mount the
same render functions itself. Do not put writes there.

### C. `pha web` — a future host, not a dependency

`WEB_INTERFACE_PLAN.md` describes a local **admin** UI (FastAPI + Jinja2 + HTMX,
no Node) that is not built: no `web` subcommand, no `fastapi`/`jinja2` in
`pyproject.toml`, no `extraction_runs` table. Benchmark authoring is not archive
admin: different data root, different user, different lifecycle. The bench app
should **share the stack and conventions** (so it can later be mounted under the
same process if that is ever wanted) but must not wait for `pha web`.

### D. `pha bench serve` — recommended

A separate, local, **writable** app in the pha package:

- it owns exactly one job (produce and score gold readings), so its write path
  can be narrow and auditable;
- its data is the corpus file tree, so it needs neither the DB nor the archive
  config to be correct;
- it is where the pipeline steps (`run`, `compare`, `verify`, `score`) can be
  triggered with progress and cost confirmation, while remaining available as
  CLI commands for the agent;
- it is one `pip` install, because it ships with `pha` — no second package to
  install, version and document;
- it uses the same shared viewer module, so the rendered page view is the one
  the public site will later use.

### E. Static site only — the publication half

`VLM_BENCHMARK_INFRA_PLAN.md` §11 is a good design for a *published* benchmark:
static HTML, no JS framework, scores as data, rights per corpus. It cannot
author: a static build has no write path, no session, no progress. Keep it, build
it later, from the corpus the workbench produces.

### The decisive test

> The corpus is product-independent and the editor must write files. The viewer
> family (`pha serve`, PHA View) is read-only by contract and bound to
> `archive.db`. Therefore the editor cannot live in the viewer family, and the
> viewer cannot be the only interface.

**Recommendation: D**, with A's cheap read-only entry point and E as a later
export. What would change it: if the tool were only ever used from one DSH
session on one machine by one person, folding a read-only panel into PHA View and
accepting "the agent edits the files" would be cheaper. That is a smaller
ambition than the one in the request.

---

## 4. Architecture

Five layers, one parser, three shells:

```
             corpus files                     archive.db + renders
   benchmarks/<corpus>/{bench.yaml,          (existing, untouched)
     <reader>/<page>.md, comparison/,
     reference/, human/, images/}
                 │                                   │
                 ▼                                   ▼
      ┌────────────────────────┐          ┌──────────────────────┐
      │ bench corpus library   │          │ addresses / renders  │
      │ parse·model·validate   │          │ (existing pha)       │
      └───────────┬────────────┘          └──────────┬───────────┘
                  │                                  │
        ┌─────────┴──────────┐             ┌─────────┴─────────┐
        │ pha bench (CLI)    │             │ shared viewer.py  │
        │ run compare verify │             │ (page shell, img, │
        │ reference score    │             │  nav, variants)   │
        │ export             │             └─────┬───────┬─────┘
        └─────────┬──────────┘                   │       │
                  │                    ┌─────────┘       └────────┐
                  ▼                    ▼                          ▼
        ┌──────────────────┐   ┌──────────────┐          ┌──────────────┐
        │ pha bench serve  │   │ pha serve    │          │ PHA View     │
        │ authoring app    │   │ read-only    │          │ read-only    │
        │ (writes gold)    │   │ archive view │          │ panel + link │
        └──────────────────┘   └──────────────┘          └──────────────┘
                                            later: hvb build → static site
```

Rules:

- **One parser.** `bench.py` is the only code that reads a page file, in all its
  historical shapes (fenced body under `## Transcription`, body before
  `## Notes`, `## Corrected text`, front matter). The skill scripts, the CLI and
  the app all call it.
- **One model.** `bench.yaml` per corpus (§5) replaces every hard-coded list. The
  reader order is data, because it is the numbering used in every comparison file.
- **The CLI is the engine.** The app calls the same functions; nothing is
  implemented twice. An agent can do the whole workflow without the app.
- **The app writes only gold.** See the save contract (§6).
- **The viewer is shared.** `pha serve` (operator), the bench page view and the
  future public site render the same page shell; only the data source differs
  (this is already the stated plan in `SEARCH_WEB_SPEC.md` §5.5).

---

## 5. Corpus model

A corpus is a directory under the archive's `benchmarks/` (decided, §11.1) with
a sidecar, the same nearest-wins idea as `pha.yaml`:

```yaml
# benchmarks/collections/.../1577/palaeographers/bench.yaml
title: 1577 Catálogo Brevis — Portuguese Province
document:                        # where the pages really come from (for images + provenance)
  path: dropbox/collections/…/bc-1577.pdf   # or a directory of images
  pages: {1: 507v, 2: 508, 3: 508v}         # pha page number → corpus page id
readings:                        # ORDER IS THE NUMBERING IN EVERY COMPARISON FILE
  - {dir: DeepSeek-V4-FVE, model: deepseek-v4-flash-vision-exp, label: DeepSeek V4 FVE}
  - {dir: M3,              model: minimax-m3,                   label: MiniMax M3}
  - {dir: Qwen3.8-Max,     model: qwen3.8-max,                  label: Qwen3.8 Max}
palaeographer: <rules-id>          # the rules file every reading used
images: {mode: render, source: images}   # render = from the archive's render cache; copy = materialise here
gold: {reviewer: Joaquim Carvalho, started: 2026-08-30}
```

Derived status per page, computed, never stored by hand:

| field | meaning |
|---|---|
| `readings` | which readers have a non-empty, non-`ERROR` reading |
| `compared` | comparison file exists and is newer than every reading |
| `gold` | `human/<page>.md` exists **and** declares `status: human-corrected` (today: 1 of 8 files; the rest are unedited copies) |
| `stale` | `readings_hash` recorded in the gold ≠ hash of the readings today |
| `verified` | last structural check passed |

`readings_hash` is the piece that prevents silent decay: the gold file's front
matter records the hash of the readings it was produced from. Change a reading
(re-read a page with a new model, or re-run after a prompt edit) and every gold
page derived from it is flagged **stale** until re-reviewed. This is the same
"staleness by mtime" discipline pha already applies to prompts, applied to gold.

### The gold page file (front matter added to today's layout)

Today's `human/507v.md` carries its provenance in prose: "Human edited" in the
title, a free-text editing-rules paragraph, and `Reviewed Joaquim 2026-08-30`
under `## Notes`. The other seven files in the same `human/` folder are still
unedited copies of `reference/` (title "Reference text (variants in slashes)",
section `## Reference text`), so "is this page gold?" is answered only by
reading it. Decided format (option b, 2026-11-14) — **additive, same body**:

```markdown
---
page: 508v
status: human-corrected        # human-corrected | machine-merged | pending
reviewed_by: Joaquim Carvalho
reviewed_at: 2026-11-14
editing_rules:
  - original line breaks kept; line-end hyphen kept in its "=" form
  - "u → v, ƒ → ss/s (particular to this hand)"
readings: [DeepSeek-V4-FVE, M3, Qwen3.8-Max]
readings_hash: sha256:6f1c…
source: dropbox/collections/…/bc-1577.pdf (pha page 3)
---
# 508v — Reference text …
Context: …same as today…
## Corrected text
…same fenced `[n]` block as today…
```

The body, the section names and the `[n]` entry format do not change, so the
existing skill scripts keep working (they already skip a leading `---` block in
reading files). What changes is that coverage, staleness and provenance become
computable from the file itself, without a side index and without parsing prose.
This is the *working* format; the INFRA plan's `reference.md` + `names.yaml` +
`readings/<model>/` layout (VLM_BENCHMARK_INFRA_PLAN.md §5) stays the
**export/published** schema.

**Images.** The workbench resolves a page image in this order: corpus
`images/<page>.<ext>` → archive render cache via
`addresses.render_path()` using the `document` mapping → on-demand render of the
declared source PDF. `images/` is materialised only by `pha bench export`
(publishing needs self-contained files; editing does not). This fixes G4 and
explains why the 1577 corpus passes today only with a warning.

---

## 6. The workbench (`pha bench serve`)

### Screens and routes

| route | screen |
|---|---|
| `GET /` | corpora under the configured bench root, with coverage: pages, readers, gold %, stale count |
| `GET /c/<corpus>` | overview: readers and models, page grid with status badges, pipeline buttons (harvest / compare / verify / score), cost estimate |
| `GET /c/<corpus>/p/<page>` | **the workbench** (below) |
| `POST /c/<corpus>/p/<page>/save` | write the gold page (JSON, CSRF token, `If-Unmodified-Since`-style guard) |
| `POST /c/<corpus>/verify` | run the structural checks over the corpus, render the report |
| `POST /c/<corpus>/score` | score readings vs gold; render per-model table (P4) |
| `GET /c/<corpus>/img/<page>.jpg` | page image (zoom variants from the existing render pipeline) |
| `GET /api/…` | the same data as JSON, for the agent and for tests |

### The page workbench

```
┌──────────────────────┬──────────────────────────────────────────────────────┐
│                      │  507v   ‹ 507  508 ›      reviewed ●   stale ○       │
│   page image         ├──────────────────────────────────────────────────────┤
│   (click = full      │  [3] 1. P. Manuel Rodrigues[Roĩz] professo de 4 …    │
│    resolution,       │      2. Pº Manuel [illegible] …          [ accept 1 ]│
│    scroll = pan)     │      3. P. Manuel Rodr[igue]z …          [ accept 2 ]│
│                      │      ─────────────────────────────────────────────── │
│                      │  GOLD   P. Manuel Rodrigues[Roĩz] professo de 4 …    │
│                      │         (textarea, per entry)                        │
│                      ├──────────────────────────────────────────────────────┤
│                      │  ▸ machine merge (reference/, slashed variants)      │
│                      │  ▸ what each reader was told (prompts-sent)          │
│                      │  ▸ notes / review rules for this page                │
└──────────────────────┴──────────────────────────────────────────────────────┘
```

Behaviour:

- **Two artefacts, one screen.** The machine merge (`reference/`, variants joined
  with `/`) is generated for every page and is the starting point for
  translation; the human gold (`human/`) is what this screen produces. The
  reviewer accepts a whole reading **or** the merge line (resolving its slashes
  by hand) into the gold entry: the merge is a suggestion, never authority.
- **Accept** copies a reading into the current entry's gold text, verbatim. The
  reviewer edits from there; nothing is auto-normalised.
- **Entry navigation** with the keyboard (`j`/`k`, `1..N` to accept, `Enter` to
  commit the entry, `⌘S` to save). A no-JS fallback remains: a plain form per
  entry that still saves, so the app is never unusable.
- **Progress**: "14 / 45 entries resolved", page counter in the corpus overview.
- **Diff**: the gold line against each reading, token-level, computed after
  escaping (the `VLM_BENCHMARK_INFRA_PLAN.md` §11.3 rule, applied here too).
- **No rich text.** Plain text in `<textarea>`/`<pre>`; markdown is never
  interpreted inside a transcription.

### The save contract (the part that must not be got wrong)

The app may write **only**:

- `human/<page>.md` — the reviewer's gold, with front matter
  `reviewed_by`, `reviewed_at`, `rules`, `readings_hash`, `source`;
- `<corpus>/bench.yaml` — only the small status/progress keys, never the reader
  order, from a dedicated action;
- `reference/<page>.md` — **generated**, from comparison; never hand-edited in
  the UI (a regenerate button may rewrite it, after showing the diff).

It must **never** write `comparison/`, any reader directory, `library/`, or the
DB. It refuses a save when: the file changed on disk since it was loaded (hash
mismatch), the structural check fails, an entry would be lost, or the reviewer
is not identified. Every save appends to `<corpus>/review-log.jsonl` (who, when,
page, entries changed) and leaves the file ready for `git diff` — the corpus
lives in the archive's git repo, so git is the undo.

### Stack

FastAPI + Jinja2 + HTMX, the stack `WEB_INTERFACE_PLAN.md` already chose and the
`VLM_BENCHMARK_INFRA_PLAN.md` chose for its site (Jinja2 for autoescaping model
text — a correctness requirement, not taste). Weight is small: `starlette`,
`pydantic` and `uvicorn` are already installed alongside `fastmcp` (verified in
the uv-tool environment); the new **declared** dependencies would be `fastapi`
and `jinja2`. One vendored HTMX file, no Node, no build step, no CDN; the
templates ship in the wheel through `force-include`, the same mechanism that
ships `dsh-pha/` (see `pyproject.toml`).

---

## 7. Verification and scoring

`pha bench verify` is `verify_comparison.py` plus the corpus model: fence count,
the two sections, contiguous entries, exactly N readings per entry, image
present, gold entries a subset of the compared entries (an entry cannot vanish
between comparison and gold). The app calls the same function before every save;
the CLI can run it over a whole corpus.

`pha bench score` (P4) compares each reading — and the machine merge — to the
gold: character error rate under two normalisation profiles (diplomatic and
modern), per-entry exact match, and a names-only metric (the metric that actually
decides a prosopography: names, places, dates). Honesty rules from
`VLM_BENCHMARK_INFRA_PLAN.md` §11.4 carry over: `n_pages` on every aggregate, no
global ranking from a small sample, stale gold excluded, provenance shown.

*As implemented:* CER is computed with `difflib` character alignment on two
normalisation profiles; entry-exact counts only entries the gold and the
comparison share (a deliberate split/merge is reported, never guessed);
names are capitalised-token matches, not entity links; the reference merge is
scored as its first variant (it is a variant digest, and scoring the slashes
would be meaningless); pages with pending or stale gold are excluded by name.

---

## 8. Migration of the local scripts

| today | becomes |
|---|---|
| `benchmark-palaeographers.py` | `pha bench run <corpus> --pages … --readers …` — calls `testrun.run_test()` in-process instead of spawning `pha`, so it inherits the model lock and the `prompts-sent/` capture the wrapper currently re-implements |
| `build-palaeographer-comparison.py`, `build-benchmark-comparison.py` | `pha bench compare <corpus>` (N readers from `bench.yaml`; the two tabular and free-text layouts become two render profiles) |
| `skills/…/normalize_comparison.py` | `pha bench compare --normalize` (kept as a skill script until the CLI ships, then a thin wrapper) |
| `skills/…/verify_comparison.py` | `pha bench verify` (same checks + corpus model) |
| `skills/…/make_reference.py` | `pha bench reference` (slashed merge; same alignment) |
| corpus `human/` conventions | unchanged on disk; the app adds front matter and the review log |

The skill stays the agent-facing document; its prose is generated into archives
by `src/personal_historical_archive/archive_skills.py`, so the format and the
`human/` rules must be edited **there** and re-synced, not only in
`jesuit-archive/skills/`.

---

## 9. Relationship to the existing plans

- **`VLM_BENCHMARK_PLAN` / `_INFRA_PLAN`:** keep. They describe the *public*
  benchmark (own repo, static site, rights, scoring spec, corpus schema). This
  plan absorbs their corpus ideas where they are cheap now (`readings/<model>/`,
  `prompt.md` capture — already implemented by `pha test`) and treats the
  published repo as an **export**, so its rule "the repository never depends on
  pha" remains true: the boundary is `pha bench export`.
- **`SEARCH_WEB_SPEC` D9:** this plan is an application of it. Extract the shared
  viewer module as that spec already requires, and the bench page view becomes a
  second caller instead of a second viewer.
- **`WEB_INTERFACE_PLAN`:** share the stack and conventions; do not block on it.
  If `pha web` ever ships, `pha bench serve` can run under the same process, but
  the bench app must stand alone.
- **`DSH_PLUGIN`:** PHA View gets the read-only entry point and `pha_bench_*`
  tools; the editor is out of scope for the plugin.

---

## 10. Phasing

| phase | deliverable | acceptance |
|---|---|---|
| **P0** | `bench` corpus library: parser, model, `bench.yaml`, status/hash, validation; tests against the 1577, 1654–55, ANTT and Schütte corpora | every existing corpus parses; `verify` reports the same verdicts as today's script |
| **P1** | `pha bench run / compare / verify / reference`; the two builders and the driver retired | a new corpus is produced end to end with no Python edit |
| **P2** | shared viewer module + `GET /c/<corpus>/p/<page>` read-only; PHA View deep link | image + stacked readings on one screen at a stable URL |
| **P3** | **the workbench**: accept/edit/save, validation, progress, staleness, review log | one page reviewed end to end in the browser; gold survives `verify`; a changed reading flags the page |
| **P4** | `pha bench score` + the table in the app | CER/names per reader against the gold, with provenance and `n_pages` |
| **P5** | `pha bench export` → the static site (or `hvb build` consuming the export) | a published corpus with self-contained images and no pha on the reading side |

---

## 11. Decisions confirmed by the user (2026-11-14)

1. **Corpora live under the archive's `benchmarks/`.** `bench.root` defaults to
   `<archive_dir>/benchmarks`, so the existing `benchmarks/collections/…` trees
   are corpus roots as they stand. (The *repo's* `benchmarks/` is gitignored
   scratch and is not a corpus store.)
2. **Gold file format: option (b).** `human/<page>.md` keeps today's directory
   layout, section names and `[n]` body; it gains YAML front matter (`page`,
   `status`, `reviewed_by`, `reviewed_at`, `editing_rules`, `readings`,
   `readings_hash`, `source`). The INFRA per-page-folder schema stays the
   *export/published* format (§5, VLM_BENCHMARK_INFRA_PLAN.md §5.1).
   Consequence for P0's migration: of the 8 files in the 1577 `human/` folder,
   the one corrected page becomes `status: human-corrected`; the other seven are
   unedited copies and must become `status: pending` or move back to
   `reference/` — they must not read as gold.

## 12. Open decisions

1. **Writes to `reference/`.** Recommend: generated only; the UI may regenerate
   with a diff, never edit.
2. **`pha bench serve` port.** Default `127.0.0.1:8766`, next to `pha serve`'s
   8765; loopback only, no auth (same policy as `pha serve`), with a warning if
   bound wider.
3. **Names review.** The INFRA plan reviews `names.yaml` separately; that is a
   second screen and belongs to P4/P5, not P3.
4. **Naming.** `pha bench` (short, matches `pha test`); `pha benchmark` only if
   the group grows past six commands.
5. **Public publication and rights.** Untouched here; it is the INFRA plan's
   subject and a decision for the archive owner, per corpus.

---

## 13. Risks and non-goals

- **Not a full transcription editor.** The workbench edits only the gold of a
  benchmark corpus. The archive's library text keeps its existing round trip
  (`pha open` → edit → `pha review`); do not merge the two.
- **Not a second `pha web`.** No config editing, no scanning, no watcher
  supervision, no document deletion. If those are wanted, they belong to
  `pha web`.
- **Cost and locks.** Harvesting readers × pages is paid model work holding a
  model-server lock per call. The app must run readers sequentially, show an
  estimate and require confirmation (rule 1 of `AGENTS.md`); it must never
  parallelise across a shared server.
- **Write-path safety.** One writable surface, one allow-list, validation before
  write, hash guard, git as undo. The failure that matters is silently losing
  human corrections; every design choice above is subordinate to that.
- **Scope.** P3 is the largest single piece; P0–P2 deliver value without it and
  should ship first, because the corpus library and the read-only view are what
  make the gold worth producing.

---

## 14. Implementation status (2026-11-14)

**Shipped (P0/P1).** `bench.py` is the single parser and corpus model; the `pha
bench` command group is the engine. Concretely:

- `bench.yaml` per corpus, with the reader order taken from the corpus's own
  comparison title when one exists (that order is the `1..N` numbering); a
  corpus's reader set is the compared set, and `init --all-readers` opts into
  every reader folder.
- One parser for every page-file shape (fenced `## Transcription`, body before
  `## Notes`, `## Corrected text`, `## Reference text`, front matter).
- Per-page status: readings present/missing, compared, gold, stale (by
  `readings_hash`).
- `verify` covers the comparison skeleton, the gold's structure and freshness,
  the image presence, and reading counts; a gold whose entry count differs from
  the comparison's is a **note** (a reviewer may split an entry), not a failure.
- `compare` (tabular rows, positional lines, or auto) and `reference` (the
  slashed merge, with the "refuse on MORE readings than declared" guard) are the
  two local builders, generalised to N readers.
- `run` harvests readings through `testrun.run_test()` in-process: same model
  locks, the exact prompt captured, editor off, `--dry-run` to plan, and a hard
  refusal to run a reader whose `model:` is unset (a benchmark that runs an
  unrecorded model is not a benchmark).
- Nine corpora declared under the archive's `benchmarks/`; they verify clean
  (warnings: no `images/` folder, which P2 resolves from the render cache).
- Gold migration: 1577 `human/` (one `human-corrected`, seven `pending`) and BNP
  `human/` (three `pending` — their contents are older merges, not gold), plus
  the rewritten `human/README.md`.

**Known gaps, deliberately left for the next pass.**

- The `palaeographers-compare` skill text still documents the pre-front-matter
  `human/` folder; `archive_skills.py` must be updated and the skill re-synced —
  that is a tool change, so it belongs to the next release bump, not this one.
- The three `jesuit-archive/local-tools/` scripts are still present and still
  work; they retire once the skill points at `pha bench` and the last corpus
  using them is rebuilt.
- `pha bench score` (P4) and `pha bench export` (P5) are not implemented.
  `pha bench run` has not been exercised against a paid model in this pass; only
  its planning path (`--dry-run`) and the real corpora's parsing/verification
  were.

**Shipped (P2/P3).**

- `viewer.py` now holds the page shell (`CSS`, `html_page`, `esc`), imported by
  `serve.py` — the "share the code, not the service" split SEARCH_WEB_SPEC asks
  for, without touching `serve.py`'s routes or its read-only contract.
- `benchui.py` + `pha bench serve`: corpus list, corpus page (page grid with
  gold/stale/missing badges, verification report, rebuild buttons), and the
  **page workbench** — image beside the stacked readings, one textarea per
  `[n]` entry pre-filled from the gold, the reference, or the on-the-fly merge,
  a "use" button per reading, `⌘S` to save, and a reviewer field.
- **Save contract implemented**: form token, `base` hash guard (a file changed
  on disk is never overwritten), contiguous non-empty entries, a required
  reviewer, the structural verifier run on the exact bytes before an atomic
  replace, `readings_hash` recorded, and an append-only `review-log.jsonl`.
  Writes only `human/`, plus generated `comparison/` and `reference/`.
- Image resolution: corpus `images/` → declared source directory (in place) →
  PDF rendered on demand into the ignored `<archive>/.pha-test/bench-cache/`.
- **Conversions done on the real corpora**: `document.path` set for 1577,
  Schütte, 1587, 1567-Coimbra and 1576 (images now resolve); comparisons built
  for 1587, 1567, 1576 and Schütte with `pha bench compare` (a mechanical draft,
  regenerable at any time); every corpus verifies with 0 failures.
- Parser hardening found by the real data: readings that open a ``` fence and
  never close it (several older corpora) are now read correctly, and markdown
  pipe-table rows (`| 3 P. Petrus Gómez | …`) are recognised as numbered table
  rows.
- **Deviations, recorded**: the app is stdlib `http.server` + the shared
  `viewer.py` shell rather than FastAPI/Jinja2 — the shared viewer is
  stdlib-only by design, and one more dependency was not worth it for a local
  single-user tool (FastAPI stays the plan for `pha web`). Routes use query
  strings (`/page?c=<corpus>&p=<page>`) instead of `/c/<corpus>/p/<page>`,
  because corpus paths are deeply nested; the app is loopback-only and the
  server prints its URL.
- **Not done**: the PHA View entry point (a read-only link/panel in `dsh-pha`)
  is deferred until the plugin is next revisited; it is a convenience, not part
  of the authoring loop.

**Shipped (P4/P5).**

- `pha bench score` and `bench.score_corpus`: CER (diplomatic/modern),
  per-entry exact match where the gold and comparison share a number, and a
  token-level names metric; pending and stale gold pages are excluded with the
  reason; `scores/results.json` + `scores/results.csv` are written (relative
  corpus id, timezone-stamped) and the caveats are part of the artefact.
  The workbench shows the table and can trigger a score.
- `pha bench export`: a self-contained static site (index, one page per page,
  one CSS file, copied images, `data/results.json`/`.csv`, `manifest.json`),
  with the gold, the labelled machine merge when there is no gold, the readings,
  the comparison in a `<details>`, provenance, the caveats and a rights line.
  Images are downscaled to 1600 px on the long edge with pymupdf (no new
  dependency), and the export self-checks that no absolute host path leaked into
  the HTML. It refuses a non-empty directory without `--force`.
- Real run: 1577 scored on its single gold page (the other seven are `pending`
  and excluded) — `Qwen3.8-Max` 0.127 CER-diplomatic / 54.7% entry-exact,
  `DeepSeek-V4-FVE` 0.208 / 20.0%, `M3` 0.293 / 26.7%, the first-variant merge
  0.347 / —. **n = 1: a screen for failure, not a ranking**, as the artefact
  itself says. The exported 1577 site is 916 KB for 8 pages with images.
- Known limits, deliberately visible: names are not entity-linked (a variant
  spelling is a miss); CER is `difflib`, not a gold-standard edit distance;
  entry-exact is undefined where a reviewer split or merged entries.
