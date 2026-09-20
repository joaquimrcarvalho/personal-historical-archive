# pha enhancement requests — index

Design/spec docs (drafts) for additions to **pha**, written while working on the
**Documenta Indica** collection. Statuses below are verified against the code,
not copied from each doc's own header — a few headers have drifted and are
flagged where they have.

**Current: pha 0.28.0.** Shipped: stable page addresses, page navigation, stage
filters, endpoint-scoped locking, per-document bibliographic references, scan
resilience, and four of the bug reports. Open: **three live defects** (two of
them measured in production), four feature requests, and three proposals
awaiting a decision.

## At a glance

| # | item | status |
|---|---|---|
| **1** | [`pha-filter-signature-mismatch-bug-report.md`](pha-filter-signature-mismatch-bug-report.md) | **OPEN — live trap.** Re-runs a stage's model on *every* pass |
| **2** | [`pha-request-stall-timeout-bug-report.md`](pha-request-stall-timeout-bug-report.md) | **OPEN.** No wall-clock deadline; two stalls of 6 h 30 / 4 h 17 measured |
| **3** | [`pha-review-scope-bug-report.md`](pha-review-scope-bug-report.md) §11 | **OPEN again.** `pha review` during a scan imports every page written so far |
| 4 | [`pha-post-filter-replay-enhancement-request.md`](pha-post-filter-replay-enhancement-request.md) | Draft — **unblocked once #1 lands**; procedure already proven by hand |
| 5 | [`pha-single-page-rescan-enhancement-request.md`](pha-single-page-rescan-enhancement-request.md) | Draft — smallest outstanding feature |
| 6 | [`pha-notes-search-enhancement-request.md`](pha-notes-search-enhancement-request.md) | Draft — independent, no-op when the notes index is empty |
| 7 | [`pha-handoff-enhancement-request.md`](pha-handoff-enhancement-request.md) | Draft — largest; carries an independent `unbundle` stub bug |
| 8 | [`SEARCH_WEB_SPEC.md`](../SEARCH_WEB_SPEC.md) | Draft **for decision** |
| 9 | [`VLM_BENCHMARK_PLAN.md`](../VLM_BENCHMARK_PLAN.md) + [`VLM_BENCHMARK_INFRA_PLAN.md`](../VLM_BENCHMARK_INFRA_PLAN.md) | Proposal, not implemented (separate repo) |
| 10 | `extends`, encoder prescan | Draft — **re-measure before building**; filters shrank both |

## Enhancement requests

| doc | feature | status |
|---|---|---|
| `pha-filters-enhancement-request.md` | **Stage filters** — `pre`/`post` steps around a stage's model; a stage becomes `input → pre filters → model → post filters → output`, with hooks on `palaeographer.post`, `editor.pre`/`post`, `encoder.pre`/`post`. Ships six reference filters (line-numbers, OCR-separator strip, whitespace/leader collapse, footnote-marker residue, hyphen joining) plus the `markdown-from-records` **artifact** filter. | **SHIPPED** (`6829dbf`, `b55889a`) |
| `pha-per-server-model-lock-enhancement-request.md` | **Endpoint-scoped locking** — one model per **model-server**, not per archive: `server:` declared per model file, user-global keyed locks, declared capacity, disjoint servers run concurrently, unlabelled files take the wildcard. Closes two real gaps: two archives on one machine sharing one LM Studio, and `pha search` loading the embed model with no lock (now it *observes* the lock and degrades to keyword + a note, `--force` to override). | **SHIPPED, released in 0.28.0** — the doc's header still says "in `main`, unreleased" |
| `pha-stable-page-addresses-enhancement-request.md` | **Stable page addresses & render serving** — a re-scan-proof `slug` (no date, no hash), `pha cite` naming the exact *filled* variant, `pha page --json` gaining the address fields, and `pha serve` with stable `/doc/{slug}/p{page}.jpg` URLs. | **SHIPPED** |
| `pha-page-navigation-enhancement-request.md` | **Page navigation for citations** — an HTML page **viewer** (`/doc/{slug}/p{N}`: prev/next/first/last, jump box, position) and a **document overview**, so a citation lands somewhere you can keep reading. | **SHIPPED** |
| `pha-post-filter-replay-enhancement-request.md` | **Replay `post` filters without the model** — a `post` filter is deterministic, but changing one re-runs the model for every page, because only the *filtered* text is stored and the model's raw output is discarded. Proposes `pha edit --replay-filters [--dry-run]`. Motivated by `franco-imagens` (~3 597 model calls to recompute what is derivable from the DB); **proven by hand** on 2026-09-18 — 3 575 pages, 2 843 texts changed, **0 model calls**, end-of-line hyphens 59 872 → 2 058. | Draft, not implemented — **effectively blocked by the signature defect (#1)** |
| `pha-single-page-rescan-enhancement-request.md` | **`pha scan` — one page, chosen palaeographer/model** — the reading model is chosen per *document*, so fixing one bad page costs a whole volume; and `--palaeographer` is dropped by `scan_once()`, contradicting `README.md`. Proposes page-scoped render/transcribe/re-edit/re-index, per-page provenance, and a **pin** so a later bulk pass cannot discard a deliberate reading. | Draft, not implemented |
| `pha-notes-search-enhancement-request.md` | **Search the notes folder** — make `pha search` cover `notes/` via a separate `notes` + `notes_fts` + embeddings index (NOT `documents` rows, so status/export/bundles/review stay clean), `--source archive\|notes\|all`, and a `kind` discriminator so the PHA view opens a note hit. Rejects indexing the whole Obsidian vault. | Draft, not implemented |
| `pha-handoff-enhancement-request.md` | **Two-machine hand-over (`pha handoff`)** — lend a document to an always-on LAN machine while the archive machine sleeps, then apply the results into the *same* document. Bundles cannot: they mint new ids, pin-and-skip the imported docs, and import a `*waiting*` stub as **content** (verified: a partly-processed document arrives fully `done`). Proposes a content-keyed round trip (sha256 + `source_name`), a document-scoped lease, and an honest stale-under-current-config report. | Draft, not implemented. Carries an independent `unbundle` stub bug worth fixing regardless |
| `pha-stage-extends-enhancement-request.md` | **Prompt composition (`extends`)** — a rules file as "base rules + delta", composed at load time. | Draft. **Re-measure first:** with filters carrying the *mechanical* differences, the remaining case for a shared prompt body is thinner than when this was written (~½–1 day if it still holds) |
| `pha-encoder-tools-enhancement-request.md` | **Encoder tools** — bundled tools that materialise artifacts from records. The artifact half is now the `markdown-from-records` filter. The **structure prescan** (§3.4: per-document layout register deriving page filters/prompt blocks per volume) is not planned and needs a design decision, not just code. | Superseded (artifact half shipped); prescan open |
| `pha-model-response-resilience-enhancement-request.md` | **Scan resilience / `done` honesty** — a malformed HTTP-200 response raised a raw `TypeError` that escaped the per-page guard and killed the whole scan; `done` was written *before* editing and indexing, so a document could be `done` with 0 chunks while a status-only check reported success. | **The code is shipped** (`_openai_chat` catches `TypeError`; `done` written last; `pha edit` indexes; `status` surfaces done-with-no-chunks). **This doc's header still says "not implemented" — it needs updating** |

## Bug reports

| doc | area | status |
|---|---|---|
| `pha-filter-signature-mismatch-bug-report.md` | **stage filters / staleness** | **OPEN — highest priority.** pha *stores* one filter signature and *compares* another: `apply_filters()` records the params **resolved** with the manifest defaults, while `_configured_filters_signature()` records the **declared** ones. A filter whose manifest declares `params:` and whose sidecar leaves them out (the normal spelling, `editor.post: [join-hyphenated-words]`) therefore reads as "changed" on **every** pass. Verified against 0.28.0 — applied `…{"keep_hyphen_before_enclitic": true}` vs configured `…{}`. Fix: resolve params on the configured side (normalise both), plus a one-shot migrator for stored signatures. See the trap note below. |
| `pha-request-stall-timeout-bug-report.md` | **model calls / `timeout_s`** | **OPEN.** `timeout_s` goes straight into httpx, where it is **per operation**, so a provider that keeps the connection warm with periodic bytes can stall indefinitely; the retry path is defeated because `(retries+1) × timeout_s` never elapses. Measured twice *with* `timeout_s: 600`: **≈6 h 30** and **≈4 h 17**, no page error, no failed status, provider billing flat. Fix: a wall-clock `deadline_s` bound the whole page including retries, and make a stall visible (`status` showing time since the last page write instead of a bare `processing`). |
| `pha-review-scope-bug-report.md` | **`pha review`** | **FIXED in 0.18.0 … but see §11: OPEN again in a new shape.** The original defect (stamping the whole library instead of the changed files, freezing the archive against all later scan/edit, even `--reprocess`) is fixed: only the pending set is imported, with `--all` and `--unset` as escape hatches. **However** "pending" is mtime-based and a running scan grows the library page by page, so a review run **during a scan** imports everything written so far — measured 2026-09-18: **435** pages imported when **4** were real corrections, 431 pages of a scanned volume falsely stamped reviewed (text unharmed; the review record freezes them). Fix: refuse while a scan holds the lock or the target document is `processing` (unless `--force`), and make "pending" mean "body differs". |
| `pha-embed-loss-bug-report.md` | **`pha reindex` / indexing** | **FIXED.** `index_document()` cleared chunks *before* embedding, so a failed embed fell back to text-only having already deleted the vectors — `status=done`, no error, invisible except in the embed count. **13 885 chunks** lost their vectors on `jesuit-archive` when two jobs overlapped. Now embeds first and leaves a document with vectors untouched on failure (reported; `reindex` exits 3), and `reindex` takes the model-server lock. |
| `pha-duplicate-edited-variants-bug-report.md` | **library variants / `pha cite`** | **FIXED (A + B); C shipped too (guarded).** One logical variant was exported twice — `edited-<rules>` and `edited-<rules>@<model>` — because the writer read a column the edit path had just NULLed. On `jesuit-archive`: 35 bare dirs / 52.7 MB, 28 byte-identical pairs; and since the bare name sorted first, `pha cite --edited` refused to cite, or picked 610/618 pages of `*waiting*` placeholders over the real text. Now the writer takes the resolved model as a required argument, and `edited-X` / `edited-X@Y` are treated as **one variant** everywhere (cite/page/MCP/serve/bundle). **(C)** `pha prune --library-variants [--dry-run] [--doc N]` deletes a bare folder only when nothing lives in it alone — present in the CLI. **This doc's header reads as if C were still open**; confirm and tidy. |

## Implementation plans and proposals

- [`FILTERS_PLAN.md`](../FILTERS_PLAN.md) — the stage-filter framework,
  including artifact filters. **Implemented**; annotated in place with the two
  deviations from the original design (Python filters run *in-process*;
  staleness is signature-based rather than mtime-based).
- [`BIBLIOGRAPHY_PLAN.md`](../BIBLIOGRAPHY_PLAN.md) — per-document
  bibliographic references as sidecars (`.dc.json` / `.bib` / `.mods.xml`),
  rendered by `pha cite` and inspected with `pha bib`; an agent may draft one
  only as `agent-drafted-unverified`. **Implemented** (0.28.0).
- [`SEARCH_WEB_SPEC.md`](../SEARCH_WEB_SPEC.md) — a **public, search-only** web
  interface over an archive, with its own embedding server and query LLM so it
  can never disturb a running `pha`. **Draft for decision.**
- [`VLM_BENCHMARK_PLAN.md`](../VLM_BENCHMARK_PLAN.md) +
  [`VLM_BENCHMARK_INFRA_PLAN.md`](../VLM_BENCHMARK_INFRA_PLAN.md) — a public
  benchmark of vision models on historical material, in a separate repository.
  **Proposal, not implemented**; the *selection of which pages to publish* is
  deliberately taken before the infra is built.
- [`HARNESS_INTRODUCTION.md`](../HARNESS_INTRODUCTION.md) — a short orientation
  to DeepSeek Harness and how pha uses it. Overview, not a plan.
- [`ENCODER_TOOLS_PLAN.md`](../ENCODER_TOOLS_PLAN.md) — **superseded** by
  `FILTERS_PLAN.md` (kept for history).

## How they fit together

- **Filters** (shipped) removed the *mechanical* work from prompts — OCR
  cleanup, line numbers, hyphen joins. That is why the two prompt-level items
  below are smaller than they look: **`extends`** now shares only the judgment
  layer, and the **prescan** matters only for layout, not cleanup.
- **Post-filter replay** assumes a *re-run is safe and cheap*. Both halves have
  to hold: the embed-loss fix made re-running safe, and the signature defect
  makes it neither safe nor cheap — so **#1 is a prerequisite in practice**,
  not just a related bug.
- **Notes search** is independent and pairs with stable addresses: notes cite
  the slug and can link the served viewer.
- **Single-page re-scan** is the machine-side counterpart of the human review
  round-trip — both exist so one bad page does not cost a whole volume. It is
  the smallest outstanding feature and carries the `--palaeographer` fix
  `README.md` already promises.
- **Hand-over** shares its subject with `bundle`/`unbundle` but not its
  semantics: bundles *copy* to another archive (new ids), a hand-over *updates*
  the same document. It is best landed after single-page re-scan, whose
  per-page provenance columns make a mixed-model hand-over exact rather than
  document-level.

### The signature-defect trap worth knowing

The filter-signature defect (#1) is currently **dormant** on the collection the
report measured: `franco-imagens` stores the *configured* form (`{}`) on 3 575
rows because the 2026-09-18 manual replay wrote it that way against the
sidecar's bare declarations. But a normal `pha edit` pass writes the *resolved*
form — from then on every pass re-calls the model for ~3 612 pages, forever.
So the cost is armed by the next routine edit rather than standing. That is why
it is first in the list despite looking quiet today.

## Housekeeping notes

Not features — parked decisions that need revisiting rather than implementing.

- [`dot-writing-dir-review.md`](dot-writing-dir-review.md) — the repo-local
  `.writing/` directory (633 per-path markdown snapshots, 4.3 MB) is
  **gitignored**. Origin answered by the operator: the Harness writing-tool's
  **snapshot cache** (reported, not independently verified — two searches of
  the installed bundle came up empty). Records the counts that decided it
  (618 identical / 15 stale / 0 orphaned) and the triggers that reopen it.

## Reference implementation notes (in the archive, not the pha repo)

- Documenta Indica editors and encoders live under
  `dropbox/collections/documenta-indica/` (`editors/`, `encoders/`,
  `prescan/doca_prescan.py`).
- The Documenta Indica margin **line-numbers** were the motivating case for the
  `line-numbers` filter; the printed lineation is the reason `franco-imagens`
  adopted `join-hyphenated-words` as an `editor.post`.
