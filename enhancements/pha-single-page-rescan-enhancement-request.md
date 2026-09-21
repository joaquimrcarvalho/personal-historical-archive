# Enhancement request — re-read a single page with a specific palaeographer / model

**Status:** IMPLEMENTED (0.33.0+). **Date:** 2026-09-15; implemented
2026-09-21. **Written against:** pha 0.24.0 (design), 0.33.0 (implementation).
The shipped command is `pha scan --path <one doc> --page N [--page M …]
[--palaeographer X] [--model Y] [--dry-run] [--no-pin] [--unpin]`, plus
`pha test --page N` as the no-write preview (§3.5) and `pha_job_start` on the
dsh-pha plugin (§3.6). Decisions §8 are as recorded; the per-page provenance
lands in `pages.palaeographer`/`palaeographer_model`/`pinned_at` and in the
page file's front matter.

**One line.** Let `pha scan` re-transcribe **one named page** with a
**palaeographer and/or model chosen for that action only**, overriding the
document's configured pair without rewriting it — so a large document can be
read cheaply and fast end to end, and the handful of pages the cheap model got
wrong can be re-read by a better (or more specialised) one.

## 1. Problem

Today the reading model is chosen **per document**, never per page:

- `pha scan` has `--palaeographer` but **no `--page` and no `--model`**
  (`src/personal_historical_archive/cli.py:2506-2515`);
- `pha edit` has `--page` but operates on the **editor** stage and cannot touch
  the raw transcription (`cli.py:2718-2726`);
- `pha test` has `--palaeographer`/`--model` but `--pages` is a **sample
  count**, not a selector (`--pages 1` = page 1, `--pages 12` = the first
  twelve pages — `src/personal_historical_archive/testrun.py:92-104`), and it
  writes only to `.pha-test/`;
- the only way to write a chosen reading of a chosen page into the DB is the
  human review round-trip (edit
  `library/.../transcription-<pal>@<model>/page-NNN.md`, `pha review`,
  `pha edit`, `pha reindex`), which is manual and records no
  palaeographer/model provenance for the page.

Two further defects make the flag route unusable even if `--page` were added:

- **`pha scan --palaeographer ID` does not override a configured document.**
  `scan_once()` calls `resolve_palaeographer_id(...)` without the `explicit`
  argument (`ingest.py:2493`) and then lets the `pha.yaml` sidecar win
  (`ingest.py:2497-2509`). `resolve_palaeographer_id(..., explicit=...)`
  already exists (`src/personal_historical_archive/extract.py:294-309`) but no
  caller passes it. Verified with a throwaway test: a document whose collection
  selects `ocr` transcribed with `ocr` even when the flag said a VLM rules id;
  the flag only acts as the fallback default for documents that resolve
  nothing. `README.md:629` claims the opposite ("overrides the rules for one
  run") — a documentation/code mismatch that should be fixed with this work.
- **A palaeographer change re-extracts the whole document.** `pal_changed`
  sets `prompt_changed`, which sets `force`, which re-reads every non-reviewed
  page (`ingest.py:645-672`, `714`, `760`). For a 900-page volume, fixing one
  page costs 900 model calls and overwrites the good pages.

The cost shape this defeats: a cheap model over the bulk, then a targeted
expensive pass over the pages that actually need it.

## 2. Requirements

- **R1 — page-scoped action.** `pha scan` accepts a page selector and a
  palaeographer and/or model override. The override applies to that invocation
  only; **no `pha.yaml`, selection file or `vision.*` config is written**.
- **R2 — the override is authoritative.** For the targeted action it beats the
  document sidecar, the legacy selection file and the global default. This
  fixes the `--palaeographer` bug above and makes `README.md:629` true.
- **R3 — nothing else changes.** Only the selected page(s) are rendered,
  transcribed, re-edited and re-indexed. Every other page's `raw_text`,
  `exported_at`, `page_edits` row and chunks are untouched.
- **R4 — per-page provenance persists.** The page records the palaeographer and
  model that actually read it, in the DB and in its library front matter. The
  document keeps recording its bulk pair. `pha page --json`, `pha cite` and
  `pha status` must report the page's real provenance, not the document's.
- **R5 — the targeted reading is protected.** A later bulk `pha scan`, a
  `--reprocess`, or a changed collection config must not silently discard a
  page that was deliberately re-read. The page is **pinned**; the explicit
  release is `--unpin` (mirroring `pha review --unset`).
- **R6 — human text always wins.** A page whose *transcription* is `reviewed`
  is refused, with a message pointing at
  `pha review --unset --doc N --page P`. A page whose *edit* is `reviewed` is
  re-transcribed but its human edit is preserved, and the run says so.
- **R7 — never ambiguous, never silent.** `--page` requires a target that
  resolves to exactly one document. `--dry-run` prints the resolved plan
  (document, page, current → new `rules@model`, whether a human edit will be
  kept) and makes no model call. A real run reports per page before → after.
- **R8 — stages keep their semantics.** `palaeographer.post` filters apply to
  the re-read page and their signature is recorded as today; the editor re-runs
  for that page because `raw_sha` changed; chunks are re-embedded so search
  sees the new text.
- **R9 — the same surface everywhere.** `pha test --page N` previews the
  override on one page without touching the DB or `library/`; the dsh-pha
  `pha_job_start` tool takes the same arguments (the FastMCP `pha_scan_now`
  stays whole-dropbox — see §3.6).

## 3. Design

"For that action only" governs the **configuration**: the override is never
written into `pha.yaml`, a selection file or `vision.*`, and the next bulk scan
of the document resolves exactly what it resolved before. What *is* persisted is
the reading itself — the page's text and the provenance of what produced it —
which is the entire point, and is protected by the pin (§3.2, R5).

### 3.1 CLI

```
pha scan --path <one document> --page N [--page M ...]
         [--palaeographer <rules-id>] [--model <model-id>]
         [--reprocess] [--dry-run]
         [--unpin] [--no-pin]
```

- `--page` is repeatable (and accepts a comma list). It implies force for the
  selected pages: `--reprocess` is accepted but does not broaden the scope.
- `--page` is refused with `--watch`, and refused when the target resolves to
  more than one document (a future `--all-docs` could opt in; see §4). This is
  deliberately stricter than `pha edit --page`, which hits page N of *every*
  matched document.
- `--model` names the model interface used by the **palaeographer** stage for
  this run. (Note: `pha test --model` means "all stages" — the divergence must
  be documented in both help strings, or scan should accept both
  `--model`/`--palaeographer-model`.)
- `--unpin --path <doc> [--page N]` clears the pin without re-reading, exactly
  as `pha review --unset` clears `reviewed_at` without importing.
- `--no-pin` (optional) re-reads the page without pinning it — a one-off
  reading the operator intends to redo later. Default is pin.

Intended recipes:

```bash
# the bulk pass, cheap
pha scan --path collections/DocHistMissPadPortOriente

# the one page the cheap reading got wrong, better model, nothing else touched
pha scan --path collections/DocHistMissPadPortOriente/DocHistMissPadPortOriente_vol04_1548-1550.pdf \
         --page 337 --palaeographer printed-critical-edition --model minimax-m3 --dry-run
# ... then the same command without --dry-run; it also re-edits and re-indexes
# the touched document, so no separate `pha reindex` is required (3.4 step 6)
```

### 3.2 Where the re-read lives (rejected alternatives first)

Two shapes were considered for storing a page that deviates from its document:

- **A partial variant directory** (`transcription-<pal>@<model>` holding one
  page). **Rejected.** Variants are enumerated per page and assumed to be whole
  (`addresses.variant_files`, `src/personal_historical_archive/addresses.py:144-181`;
  chunks carry only a `raw`/`edited` variant; `pha export` regenerates per
  document). Partial variants would break search, `pha cite` and review.
- **A machine re-read stamped as `reviewed`.** **Rejected as the mechanism**
  (it conflates human and machine provenance) but **borrowed as the pattern**:
  `db.mark_page_reviewed()` already lets one page diverge from the document's
  reading inside the same variant folder, with the deviation recorded per page
  (`db.py:412-424`, front matter `reviewed: true` in
  `ingest.py:1127-1140`).

**Chosen shape.** The page row and the page's library file carry the truth; the
folder stays the document's stage folder.

- New `pages` columns, added in `db.migrate()` following the existing
  PRAGMA + `ALTER TABLE ... ADD COLUMN` pattern (`db.py:114-193`):
  `palaeographer TEXT`, `palaeographer_model TEXT`, `pinned_at REAL`.
  `NULL` = the page was read by the document's pair (all existing rows, and
  every bulk-read page — no backfill, no churn).
- `write_document_pages()` (`ingest.py:1099-1164`) writes the page's own
  `palaeographer:`/`model:` when set, plus `pinned: true`. The folder name
  `transcription-<doc-pal>@<doc-model>` (line 1113) stays the **stage id**; the
  page file is authoritative. Document that explicitly — it is the one
  cosmetic cost of this design.
- `pha page --json`, `pha cite` and `pha status` report the page's provenance
  (`page.palaeographer`/`page.palaeographer_model` falling back to the
  document's).

### 3.3 Override resolution (and the `--palaeographer` fix)

Replace the ad-hoc resolution in `scan_once()` with one function used by both
bulk and page-scoped scans:

| Priority | rules | model |
|---|---|---|
| 1 | `--palaeographer` | `--model` |
| 2 | document `pha.yaml` | document `pha.yaml` |
| 3 | `<dir>/palaeographer` selection file | — |
| 4 | `vision.palaeographer` | `vision.model` |

Both halves resolve independently, so `--model expensive` keeps the document's
`ocr` rules (the useful case: same pass, better engine/model). The resolved
pair is printed before any work (as `cmd_scan` already does at
`cli.py:52`) and is what `--dry-run` reports.

Edge case to handle loudly: `--palaeographer <vlm-rules>` **without** `--model`
on a document whose model is a local OCR engine (`engine: liteparse` /
`tesseract`) pairs a prompt with an engine that ignores prompts. Detect
(engine model + non-OCR rules) and warn, suggesting `--model`; do not silently
produce a 900-page-style surprise on one page.

### 3.4 Scan flow

`scan_once()` / `ingest_file()` grow page-scoped parameters
(`pages`, `pal_override`, `model_override`, `unpin`, `no_pin`):

1. Resolve the target and **require exactly one document** when `pages` is set.
2. Skip the document-level "unchanged" early return when `pages` is set
   (`ingest.py:665-667`), but leave the document-level staleness rules
   otherwise untouched.
3. Render **only** the selected pages, reusing
   `render_document(..., pages=set(...))` (`extract.py:17-31`) exactly as
   `_page_units()` already does for `pha test`
   (`testrun.py:266-288`). Render settings stay the document's resolved
   `render_dpi`/`max_image_px`/`jpeg_quality` — renders are content-addressed
   per document sha and per-model resolution is a non-goal (§7).
4. For each selected page: refuse if `reviewed_at`; otherwise transcribe with
   the override pair, run `palaeographer.post`, `db.set_page_result(...)`, and
   set `palaeographer`/`palaeographer_model`/`pinned_at` on the row.
5. Run `edit_document(..., page_no=P)` for each touched page — its existing
   `page_no` filter and `_edit_needed()` already re-edit precisely the page
   whose `raw_sha` changed (`ingest.py:1584-1600`, `1728`, `1170-1204`).
6. `index_document()` once (whole-document re-index is the safe, simple
   choice; the embed cost is already paid by a normal scan).
7. Report per page: `p.337: ocr@liteparse → printed-critical-edition@minimax-m3
   (1 812 → 1 794 chars, pinned; human edit kept)`.

`--unpin` runs steps 1-2 only (clear `pinned_at`, rewrite front matter, report);
no render and no model call.

Locking is unchanged: the action takes the scan lock, so it never overlaps a
bulk scan/edit/reindex.

### 3.5 `pha test` preview

`pha test <doc> --page N --palaeographer X --model Y` selects exactly page N
(`--page` beats the count-based `--pages` when both are given) and writes one
page to `.pha-test/`. The help text for `--pages` must keep saying "number of
pages to sample" so the two are not confused.

### 3.6 Surfaces

- **MCP / dsh-pha.** `dsh-pha/lib/index.js:194` already forwards `--page` to
  *every* job action; today `pha_job_start({action:'scan', page:N})` dies with
  argparse's `unrecognized arguments`. Once scan accepts it, add `page`,
  `palaeographer`, `model` to the tool schema and to `startJobAction`. The
  FastMCP `pha_scan_now()` remains whole-dropbox (documented limitation in
  `skills/pha-document-operations/SKILL.md`).
- **Skill / docs.** Update the `pha-document-operations` "Re-edit one page"
  row with a "Re-read one page" row, and add the recipe to `README.md` and
  `AGENTS.md` (staleness + review sections).
- **Web UI (phase 3).** A "re-read this page with…" action in the page reader
  that starts the job.

## 4. Edge cases

- **Reviewed transcription page** → refuse, name the page, point at
  `pha review --unset --doc N --page P` (the only supported release,
  `README.md:1325`).
- **Reviewed edit on the page** → re-read the raw, keep the human edit,
  report it (`_edit_needed()` returns `False` for a reviewed edit).
- **Page out of range** → error naming `page_count`; no partial work.
- **Directory-of-images document** → page N is the Nth sorted image; keep
  `source_name` so the file stays `<stem>.md` (e.g. `502V.md`).
- **Already-pinned page** → re-reading replaces the pin and reports the
  previous pair (idempotent).
- **`--unpin` alone** → clears the pin only; the text is kept.
- **`--page` with `--watch`** → rejected.
- **`--page` matching >1 document** → rejected unless a future `--all-docs`
  (cost guardrail: one paid page per document is still cheap only if the
  operator says so).
- **Document `processing` / stale lock** → existing `scan_once` resume rules.
- **Source file changed (new sha)** → the document row is replaced
  (`ingest.py:673-682`) and page-level provenance goes with the old row; the
  pinned text survives on disk in the old version's library folder. Acceptable,
  but must be stated in the docs.
- **Bundles.** `pha bundle` pins the *document* palaeographer on the receiving
  archive; page-level overrides must travel too (page rows + library front
  matter). Verify the bundle manifest carries them; add a test.
- **`pha review --all`** stamps everything; `reviewed` beats `pinned`.

## 5. Test plan

- `--page N` re-reads only N: other pages' `raw_text`, `exported_at`,
  `page_edits` and chunks are byte-identical afterwards.
- Override beats a `pha.yaml` sidecar for the targeted page (the R2 fix), and
  the bulk `--palaeographer` flag now does too (regression for `README.md:629`).
- `--model` alone keeps the document's rules; the resolved pair is printed.
- Pinned page survives `pha scan --path doc --reprocess` and a later
  collection-config change; `--unpin` releases it.
- Reviewed transcription page is refused without a model call; reviewed edit
  is preserved and reported.
- `--dry-run` performs no model call and prints the plan.
- `--page` + `--watch` rejected; `--page` + multi-document target rejected.
- `pha test --page 12` samples exactly `[12]`; `--pages 3` still samples 1-3.
- Front matter, `pha page --json` and `pha cite` show the page provenance.
- Migration: an old DB gains the three columns; all existing pages have `NULL`
  provenance and behave as before.
- Bundle round-trip preserves a page override.

## 6. Acceptance criteria

1. One command re-reads one page with a chosen palaeographer and/or model, and
   a second bulk scan does not undo it.
2. No config file is modified by the action.
3. No other page's text, edit or chunks change.
4. The page's real provenance is visible in the DB, the library front matter,
   `pha page --json` and `pha cite`.
5. Human-reviewed text is never overwritten.

## 7. Non-goals

- Per-page **editor** or **encoder** overrides (the natural follow-on, same
  mechanism; the edited variant should stay consistent within a document).
- Per-page render settings (a model needing a different `max_vision_px`).
- Page *ranges* or whole-document batches (`--pages 100-200`); start with
  named pages.
- Automatic detection of "bad" pages (that is the human/agent judgment this
  feature exists to serve).
- Any change to bulk staleness semantics beyond the `--palaeographer` fix.

## 8. Decisions (agreed by the archive owner, 2026-09-15)

The four recommendations below were accepted; the design above assumes them.

1. **Pin by default** (mirrors `reviewed`). `--no-pin` is the opt-out for a
   one-off reading the operator intends to redo later.
2. **`--reprocess` does not override pins.** A bulk migration cannot quietly
   discard a paid reading; `--unpin` is the release.
3. **`--model` on `pha scan` means the palaeographer's model**, not all stages
   (unlike `pha test --model`). The help text and `AGENTS.md` must say so.
4. **`pha status` reports mixed provenance** — one line per document with
   pinned pages.

## 9. Effort

Phase 1 (R1-R8: CLI, resolution fix, page-scoped `ingest_file`, migration,
provenance, pin/unpin, tests) — **~0.5-1 day**. Phase 2 (R9: `pha test --page`,
MCP/plugin schema, front matter/`cite`/`status` surfacing, docs) — **~0.5 day**.
Phase 3 (web UI action) — separate.

## 10. References

- `src/personal_historical_archive/cli.py:50-71, 2506-2515, 2718-2726`
- `src/personal_historical_archive/ingest.py:605-814, 1099-1164, 1170-1204,
  1584-1600, 1728, 2450-2537`
- `src/personal_historical_archive/extract.py:17-31, 294-309`
- `src/personal_historical_archive/db.py:25-33, 114-193, 412-424`
- `src/personal_historical_archive/addresses.py:144-181`
- `src/personal_historical_archive/testrun.py:92-104, 266-288`
- `dsh-pha/lib/index.js:194`; `mcp_server.py:247`
- `README.md:629, 955, 1316-1330`; `AGENTS.md` (staleness, review round-trip);
  `skills/pha-document-operations/SKILL.md`
