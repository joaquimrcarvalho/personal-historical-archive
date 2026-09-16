# pha enhancement requests — index

These are design/spec docs (drafts) for additions to pha, written while
working on the **Documenta Indica** collection. They are grouped by feature
and meant to be read together. Implemented so far: **stable page addresses**
(`pha cite` / `pha serve`), **page navigation for citations** (the served viewer
+ overview), **stage filters** (framework + six reference filters — see below),
**endpoint-scoped locking** (one model per model-server: `server:` on the model
interface, user-global keyed locks with declared capacity, and the search
degrade that no longer evicts a running job's model), and the **bug reports**
below (review scope, embed loss — 0.19.0; duplicate edited variants) plus
**scan resilience / `done`
honesty**. Still to do:
**notes search**, the `extends` composition directive, the encoder structure
prescan, **replaying `post` filters
without the model** (a deterministic filter change must not force model calls),
and **re-reading one page with a chosen palaeographer/model** (read a big
volume with a cheap model, then fix the pages it got wrong with a better one —
without re-extracting the volume).

The first two bug reports were blocking prerequisites for the remaining work,
which is why they went first:

- **review scope** — `pha review` stamped the whole library, freezing any
  document against re-processing. `FILTERS_PLAN.md` §8 assumes a filter change
  can re-run a stage over reviewed pages, so this had to land first; `pha
  review --unset` is now the supported way to do that.
- **embed loss** — a failed embed destroyed stored vectors and re-indexed
  text-only, which made *every* pipeline re-run quietly destructive — for
  filters as much as for reindex. Fixing it is also what made it safe to give
  `pha reindex` the single-model lock.

The remaining items are independent and can land in any order.

| doc | feature | one-line summary |
|---|---|---|
| `pha-filters-enhancement-request.md` | **Stage filters** | **IMPLEMENTED** (`6829dbf` + `b55889a`; design record in [`FILTERS_PLAN.md`](../FILTERS_PLAN.md), which is annotated with the deviations — Python filters run in-process and staleness is signature-based rather than mtime-based). `pre`/`post` text filters around a stage's model — a stage becomes `input → pre filters → rules model → post filters → output`. Deterministic, chainable scripts; the repo ships six reference filters (`line-numbers`, OCR-separator stripping, whitespace and footnote-marker cleanup, hyphen joining, and the `markdown-from-records` artifact filter). An edited filter re-runs its stage automatically. |
| `pha-stage-extends-enhancement-request.md` | **Prompt composition (`extends`)** | Let a rules file be "base rules + delta" (`extends:`/`include:` front matter), composed at load time so shared editor/palaeographer/encoder bodies live in one place (e.g. `latin-to-english-ocr` extends `latin-to-english`). Covers ordering, settings cascade, model-not-inherited, and base-file re-edit invalidation. |
| `pha-encoder-tools-enhancement-request.md` | **Encoder tools** | After an encode, pha runs collection-bundled *tools* that materialise artifacts from the records (e.g. `markdown-from-records`: one markdown file per document/section). Also documents the model-assisted entry detection, character-aware chunking, and the collection **structure prescan** (§3.4: per-document layout register deriving page filters/prompt blocks per volume). **Merged**: the artifact/`markdown-from-records` part is planned as an `encoder.post` **artifact filter** in `FILTERS_PLAN.md`; the prescan part is not yet planned. |
| `pha-stable-page-addresses-enhancement-request.md` | **Stable page addresses & render serving** | A re-scan-proof way to *link to* a page from outside pha. One canonical `slug` derived from the dropbox-relative path (no date, no hash, unlike `documents.id` / the dated library folder / `renders/<sha256>/`); `pha cite` naming the exact *filled* variant; `pha page --json` gaining `slug`/`rel_path`/`sha256`/`render`/`variants`; and `pha serve` — a read-only loopback endpoint with stable `/doc/{slug}/p{page}.jpg` URLs that resolves the current sha per request. Motivated by Obsidian footnotes; complements `WEB_INTERFACE_PLAN.md` (whose API surface has no render route) and would let `dsh-pha`'s `/pha/pageImage` return bytes instead of a data URL. **Implemented**: `addresses.py` (slug/rel path/render/variants), `pha cite`, `pha serve`, the new `pha page --json` fields, plus tests. |
| `pha-notes-search-enhancement-request.md` | **Search the notes folder** | Make `pha search` cover `notes/`: a separate `notes` + `notes_fts` + embeddings index (notes are NOT `documents` rows, so `pha status`/`export`/bundles/review stay clean), mtime-based reindex from `pha scan`/`reindex`, `--source archive\|notes\|all`, and a `kind` discriminator in results so the PHA view opens a note hit through its existing `openNote`. Rejects modelling notes as documents and rejects indexing the whole Obsidian vault. |
| `pha-page-navigation-enhancement-request.md` | **Page navigation for citations** | The follow-on to stable addresses: a citation lands on a static `/doc/{slug}/p{N}.jpg` with no next/previous, no position ("437" but not "437 of 638") and no way back to p. 1 — while the PHA view's reader already navigates. Adds an HTML **page viewer** at `/doc/{slug}/p{N}` (prev/next/first/last, jump box, keyboard, prefetch; works with JS off) and a **document overview** at `/doc/{slug}/` (page ranges, "start reading"), leaves `.jpg` untouched, points citations at the viewer, and adds `page_count`/`prev_page`/`next_page`/`viewer_url` to `meta.json`, `pha page --json`, `pha cite` and MCP `pha_get_page`. **Implemented**: viewer/overview/jump routes in `serve.py`, the `serve: {host, port}` config block, the navigation fields, and tests — the notes' links are migrated to the viewer. |
| `pha-per-server-model-lock-enhancement-request.md` | **Endpoint-scoped locking** | **IMPLEMENTED** (in `main`, unreleased). One model per **model-server**: a declared `server:` on each `models/*.md`, locks keyed on it in a **user-global** lock dir, two jobs allowed **iff their server sets are disjoint**, declared capacity (`servers: {<id>: {slots: N}}`), and unlabelled files taking the wildcard so today's global behaviour is preserved. Closes two real gaps: two archives on one machine take two different locks and load two models into one LM Studio, and `pha search`/MCP `pha_search` load the embed model with **no lock at all** — now search *observes* the embed server's lock and degrades to keyword results (naming the running job) before loading anything, with `--force` to override. Rejects URL-based local/remote inference (under LM Link a `localhost` request can be served by a remote device). |
| `pha-post-filter-replay-enhancement-request.md` | **Stage filters — replay `post` without the model** | A `post` filter is deterministic, but changing one re-runs the **model** for every page: only the *filtered* text is stored (`page_edits.text`) and the model's raw output is discarded, so `_edit_needed()`'s `filters_changed` branch has nothing to re-filter. Motivated by `collections/franco-imagens` — the 4 volumes were edited with **no** filters (`filters=''`, so the stored text *is* the model output), and adding one `editor.post` (`join-hyphenated-words`, to drop end-of-line hyphens while keeping the printed lineation) would cost ~**3 597** model calls to compute what is already derivable from the DB. Proposes `pha edit --replay-filters [--dry-run]` (opt-in, takes the scan lock, declines `pre`/rules/model changes and human-reviewed pages): sound on today's schema when the recorded `post` chain is empty (Option A, unblocks Franco now), and durable by persisting the pre-`post` model output in a new column (Option B). |
| `pha-single-page-rescan-enhancement-request.md` | **`pha scan` — one page, chosen palaeographer/model** | The reading model is chosen **per document**, never per page, so fixing one bad page means re-extracting the whole volume (and `pha scan --palaeographer` does not even override a collection's `pha.yaml` — `scan_once()` drops the `explicit` argument, contradicting `README.md:629`). Proposes `pha scan --path <one doc> --page N --palaeographer X --model Y`: page-scoped render/transcribe/re-edit/re-index, per-page provenance (new `pages` columns + page front matter), and a **pin** so a later bulk pass or `--reprocess` cannot discard the deliberate reading (`--unpin` releases it, mirroring `pha review --unset`). Motivating use: cheap model over a big volume, better model on the pages it got wrong. Also fixes `--palaeographer` to be a true per-run override and adds `pha test --page N` as a no-write preview. |
| `pha-model-response-resilience-enhancement-request.md` | **Scan resilience / `done` honesty** | A remote model's HTTP-200-with-null-`message` response raises a raw **`TypeError`** out of `_openai_chat` (the caught tuple had `KeyError, IndexError, AttributeError`; its sibling `_anthropic_chat` catches `TypeError`), which is not a `ModelError`, so it escapes the per-page guard and **kills the whole scan** instead of failing one page. It went unnoticed because `ingest_file` committed `status = done` **before** `edit_document` and `index_document` — and the standalone `pha edit` path never indexed at all. Measured cost on 2026-09-15: doc 57 of `documenta-indica` (961 pp) left `done` with **0 chunks** (editor died at page ~39) while the driver's status-only check reported `ALL DONE`, `unfinished=0`, `rc=0`. Fixed: `ModelError` on a malformed response, `done` written last, `pha edit` indexes (and repairs a missing index), `pha status` surfaces done-with-no-chunks, and `pha scan --path <file.pdf>` no longer scans zero files (`discover()` handled only directory roots). |

## Bug reports

| doc | area | one-line summary |
|---|---|---|
| `pha-review-scope-bug-report.md` | **`pha review`** | **FIXED (0.18.0).** `pha review` stamped the *whole* library as **reviewed** instead of only the pending files, so one run froze the archive against any later `pha scan`/`pha edit` — **even `--reprocess`**. Now imports only the pending set; `--all` keeps the blanket behaviour as an opt-in; `--unset [--doc N [--page P]]` lifts the stamp (text kept) so a frozen archive is recoverable. Reproduced on 0.17.0: 14 572 pages stamped after `pha status` had reported **5** pending. |
| `pha-embed-loss-bug-report.md` | **`pha reindex` / indexing** | **FIXED.** `index_document()` cleared a document's chunks *before* embedding, so a failed `embed()` (120 s batch timeout) fell back to text-only indexing having already deleted the stored vectors — `status=done`, no error, invisible except in the embedded count. **13 885 chunks** lost their vectors this way on `jesuit-archive` while two jobs overlapped. Now embeds first and leaves a document with vectors completely untouched on failure (reported; `pha reindex` exits 3), and `pha reindex` takes the single-model lock. Repair of the incident data = re-embedding 4 documents. |
| `pha-duplicate-edited-variants-bug-report.md` | **library variants / `pha cite`** | **FIXED (A + B).** `write_edited_pages()` named its output directory from `documents.editor_model` *as read at call time* (`ingest.py:1167`), and the edit path **NULLs that column on an editor change** (`:1554`) before writing, while the incremental path sets it (`:1740`) — so one logical variant was exported **twice**, as `edited-<rules>` (front matter `model: null`) and `edited-<rules>@<model>`. Measured on `jesuit-archive` 2026-09-16 — 41 documents with an editor: **28 byte-identical pairs**, 6 divergent, 1 bare-only, 7 `@model`-only, **35 bare dirs / 52.7 MB**; transcription dirs unaffected (**0** bare vs 56 `@model`), so it was specific to the edited stage. Two symptoms: `pha cite <doc> <page> --edited` **refused to cite** ("several filled edited variants … choose one"); and because `_pages_dir_for()` returned `sorted(...)[0]` the *bare* name won, which on docs 47/50 was **610/618 and 627/660 pages of `*waiting*` placeholders** while the `@model` dir held the real, DB-matching text. Now **(A)** the writer takes the resolved model as a required keyword (`edit_document`, `_edit_null`, `pha export`, `bundle` all pass it) and never reads the nullable column (front matter included), and **(B)** `addresses.parse_variant/pick_variant/collapse_variant_aliases` treat `edited-X` and `edited-X@Y` as **one variant** everywhere — `variant_files` (cite/page/MCP), `_pages_dir_for`/`library_page_path`, `serve` variants/meta, and `bundle` import (which also stopped folding `@model` into the editor id); the recorded model's directory wins, two *qualified* models of one id stay two readings, and a bare name that is the current model-less output keeps its directory. **(C)** also shipped, guarded: `pha prune --library-variants [--dry-run]` deletes a bare folder **only when every page in it is exactly what the DB holds** (regenerable by `pha export`), so the 25 identical pairs go and all 9 divergent readings of §6 (re-measured; the old "6" is stale) are reported and kept. Two further surfaces were fixed with it: the review scan behind `pha status`/`pha review` now skips a bare alias (it is mtime-based per file, so a stale bare file could have been imported as a human correction), and `bundle` import no longer folds `@model` into the editor id. §6 carries the current per-pair table. |

## Implementation plans

- [`FILTERS_PLAN.md`](../FILTERS_PLAN.md) — the stage-filter framework,
  including **artifact filters** (records→markdown) and the reference filter
  set. Absorbs the encoder-tools runner (owner ruling: one mechanism).
- [`ENCODER_TOOLS_PLAN.md`](../ENCODER_TOOLS_PLAN.md) — **superseded** by
  `FILTERS_PLAN.md` (kept for history).

## How they fit together

The three features are complementary and can land independently:

1. **Filters** ✅ (implemented) — mechanical, source-specific text shaping
   around a model. It removed OCR cleanup and line-number concerns from
   prompts and gives per-collection opt-in (`editor.pre: [line-numbers]`).
2. **`extends`** — avoids duplicating the shared model-prompt body when a
   variant still needs different *prompt* rules (the judgment layer), after
   the mechanical bits have moved into filters.
3. **Artifact filters + prescan** — the artifact half is **done**
   (`markdown-from-records` as an `encoder.post` filter, `6829dbf`); the
   structure prescan (multi-volume layout data-driven) remains.

Suggested reading order: filters → extends → artifact filters/prescan, since
filters subsume the OCR-cleanup that `extends` and the tools were partly
motivated by. Any collection can adopt a subset.

`extends` is now the more interesting of the two remaining prompt-level items
to revisit: with filters carrying the *mechanical* differences between two
editions, what is left for a shared base prompt body is smaller than when the
request was written, so the case for it should be re-measured before building
it (~½–1 day if it still holds).

**Notes search** is independent of those three: it reuses the existing
chunk/FTS/embedding machinery but adds a new *source* (the `notes/` folder), and
it pairs with the stable-page-addresses work — notes cite the slug and can embed
the render URL.

**Single-page re-scan** is independent too, and the smallest of the outstanding
items: it changes the *granularity* of a scan (one page, an override pair for
that action) rather than the pipeline. It also carries the fix for the
`--palaeographer` flag that `README.md:629` already promises, and it is the
machine-side counterpart of the human review round-trip — both exist so one bad
page does not cost a whole volume.

## Housekeeping notes

Not features — parked decisions that need revisiting rather than implementing.

- [`dot-writing-dir-review.md`](dot-writing-dir-review.md) — the repo-local
  `.writing/` directory (633 per-path markdown snapshots, 4.3 MB) is
  **gitignored**. Origin now answered by the operator: the Harness
  writing-tool's **snapshot cache** (reported, not independently verified — two
  searches of the installed bundle came up empty, and are recorded). Records
  the counts that decided it (618 identical / 15 stale / 0 orphaned), the
  deletion case, and the triggers that should reopen it.

## Reference implementation notes (in the archive, not the pha repo)

- Documenta Indica editors and encoders live under
  `dropbox/collections/documenta-indica/` (`editors/latin-to-english.md`,
  `editors/latin-to-english-ocr.md`, `encoders/`, `prescan/doca_prescan.py`).
- The Documenta Indica margin **line-numbers** are the motivating case for a
  `line-numbers` filter; the edited pages show them currently leaking as bare
  numbers (see `editors/latin-to-english.md` §"Line numbers of the edition").
