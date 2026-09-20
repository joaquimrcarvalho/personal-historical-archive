# Enhancement request — replay `post` filters without re-running the model

**Status:** draft, not implemented. Motivated by a concrete, expensive case
(§2). **Date:** 2026-09-15. **Written against:** pha 0.23.0, repo state at
`f263b86`. **Updated:** 2026-09-18 — Option A was executed **by hand** on the
motivating case, with measurements (§2.1), and a defect found in the existing
staleness machinery became a prerequisite (§3 R8 and
`pha-filter-signature-mismatch-bug-report.md`).

**See also:** [`pha-filter-signature-mismatch-bug-report.md`](pha-filter-signature-mismatch-bug-report.md)
— pha *stores* one signature when it runs a filter and *compares* another for the
same chain (the manifest defaults are in the stored one only), so a replay that
records what it applied is stale again on the next pass and the saving is not
durable. Settle that with this request.

## 1. Problem

A stage's **`post` filters are deterministic**, but changing one today forces a
**non-deterministic, paid model call for every page of the document**. The
chain is:

```
raw → (editor.pre) → [model] → (editor.post) → page_edits.text
```

Only the **filtered** output is stored. `db.set_page_edit()` persists
`text` (post-filter) plus the chain `filters` signature — the model's raw
output is **discarded**. So when `_edit_needed()` sees the signature differ
(`filters_changed(recorded, expected_filters)`), the only path it has is
"re-edit", i.e. call the model again. See:

- `src/personal_historical_archive/ingest.py`, `edit_document()` — the
  `client.chat_text(...)` → `editor.post` → `db.set_page_edit(...)` block.
- `src/personal_historical_archive/ingest.py`, `_edit_needed()` — the
  `filters_changed` branch.
- `src/personal_historical_archive/db.py`, `set_page_edit()` — stores `text`
  and `filters` only.

That inverts the intent of the feature. `FILTERS_PLAN.md` / `README.md` §"Stage
filters" sell filters as the way to keep *mechanical* work **out of the model**;
as implemented, a mechanical change *is* a model change.

## 2. Motivating case (measured, 2026-09-15)

`collections/franco-imagens` — António Franco, *Imagem da virtude* (4 printed
volumes, 3 612 pages). The editor (`franco-imagem-virtude`, deepseek-v4-flash)
was run with **no filters at all**; every stored edit therefore has

```
page_edits.filters = ''        # verified on docs 52/53/54/55
```

i.e. **the stored edited text IS the model's raw output**. Adding one
deterministic `editor.post` filter (`join-hyphenated-words`: join a word split
across a line break, keeping the printed lineation) to fix end-of-line hyphens
therefore requires ~**3 597 model calls** to produce a result that is
*bit-for-bit computable from what is already in the database*.

Cost of the workaround (full `pha edit`): hours of model time and real API
spend. Cost of the correct operation: 0 model calls, seconds-to-minutes.

### 2.1 Option A executed by hand on that case (2026-09-18) — what it proved

Until `--replay-filters` exists, Option A was run over `franco-imagens` with a
throwaway script (`.pha-manual/aplicar-filtro-franco.py` in the archive), reusing
pha's own `filters.apply_filters()` / `write_edited_pages()` so that the result
is exactly what the command must produce:

| | |
|---|---|
| pages examined | 3 575 (the four volumes) |
| edited texts changed | **2 843** (732 recorded with no text change, 1 signature normalised) |
| declined | 20 pages `status != done`, **1 human-reviewed page untouched** |
| model calls | **0** |
| wall clock | **1 min 18 s** (vs ~7 h of model time for a re-edit) |
| end-of-line hyphens | **59 872 → 2 058** (of which ~517 are real word-splits; the rest are page-signature/catchword lines and uppercase compounds the filter deliberately leaves) |
| `library/` | re-exported per document via `write_edited_pages()` (refreshes `exported_at`) |
| search | `pha reindex` queued for the four documents |

Conclusions that belong in the implementation:

1. **Option A's soundness condition is the common case, not a corner.** All
   3 574 untreated pages of the corpus had `filters=''`; the replay is a
   ~1-minute, model-free operation over 3 600 pages, i.e. **three orders of
   magnitude** cheaper than the workaround it replaces.
2. **The replay must store the *configured* signature**, not the applied one,
   or the pages are stale again immediately (see R8 and the bug report).
3. **Writing must tolerate a concurrent scan, and must not endanger it.** The
   first attempt failed with `database is locked` because the running
   `pha scan` held the lock past a plain `busy_timeout`; the working version
   commits every 20 pages and retries like `db._write()`. The reverse risk is
   the one to avoid: a replay that keeps one long transaction open can push the
   *scan* past its own contention tolerance and abort a volume's work — the
   command's transaction discipline matters as much as its filters.
4. **A per-page invariant makes the replay verifiable.** Cheap and decisive:
   after a `post` replay, `re.sub(r"[\s\-]+", "", before)` must equal the same
   on `after` (a text filter may remove hyphens and move line breaks, nothing
   else). 0 violations over 2 827 changed pages here. Reusing it as a guard in
   the command (and as the integration assertion) turns "the filter is
   deterministic" into something the run itself checks.
5. **It is also the cheap way to *iterate* on filter parameters.** Because
   re-applying costs a minute rather than a day, a collection's `post` chain can
   be tuned by trial; without replay, each experiment costs a full model pass.
   That is an argument for landing A before B, even though B is the durable
   design.

## 3. Requirements

- **R1 — no model call for a `post`-only change.** When the only difference
  between the recorded and the configured chain is the **`post`** part, pha must
  be able to (re)produce the stored edited text **without calling the model**.
- **R2 — lose nothing else.** A replay must not touch the transcription, the
  model identity, or the pages' `raw_sha`; it must update `text`, `filters`,
  `updated_at` and the `library/` export exactly as a re-edit would.
- **R3 — `pre`, rules or model changes still re-edit.** If `editor.pre`, the
  rules file, the model id or the raw transcription changed, the model's input
  changed and a replay is impossible — keep today's behaviour (re-edit).
- **R4 — human corrections are inviolable.** A page with `reviewed_at` set is
  never replayed over (`_edit_needed` already returns `False`; the replay path
  must do the same).
- **R5 — never silent.** Replay is an explicit, opt-in act
  (`pha edit --replay-filters`), never a side effect of a normal `pha edit`, and
  it reports how many pages it replayed and how many it declined (and why).
- **R6 — idempotent.** Replaying an already-current chain is a no-op; replaying
  twice yields the same bytes and does not append/stamp anything twice.
- **R7 — serialised.** It writes the DB and `library/`, so it must take the
  same scan/edit lock (even though it loads no model).
- **R8 — the signature it stores must be the one pha compares.** A replay has no
  model call to hide behind: if it records the *applied* chain (manifest
  defaults included) while `_configured_filters_signature()` records the
  *declared* one, the page is stale again on the next pass and the saving
  evaporates. Either normalise the two (F1/F2 of
  `pha-filter-signature-mismatch-bug-report.md`) or have the replay store the
  configured form, and assert equality in a test.

## 4. Design

### 4.1 Option A — replay the `post` chain over the stored text (minimal, works on today's schema)

Permit a replay for a page **iff all** of:

1. a stored edit exists with `status='done'` and non-empty `text`;
2. `raw_sha` matches the current transcription `_raw_sha(page["raw_text"])`;
3. the rules/model identity is unchanged;
4. the editor's **`pre`** chain is unchanged;
5. the recorded chain's **`post`** part is **empty** (so the stored text is
   exactly the model output — the invariant that holds across all four Franco
   volumes today).

Then: `new_text = apply_filters(stored_text, configured_post, hook="editor.post")`,
and store `text=new_text`, `filters=<the configured signature>` (R8), then
re-export the document's edited pages.

Precondition 5 is what makes A sound, and it is also A's limit: it cannot help
when the previous chain already ran post filters (the stored text is already
shaped and the operation is not, in general, invertible).

### 4.2 Option B — persist the model output (root fix)

Add a column to `page_edits` holding the model's output **before** `post`
filters (e.g. `model_text`), written on every edit. Then a post-only change is
always replayable: `model_text → new post chain → text`.

- Backfill: none possible for existing rows; a page without `model_text` falls
  back to Option A's precondition 5 (and to re-edit otherwise). Populate it
  lazily on the next real edit.
- Storage: one extra copy of the edited text per page. For the Franco corpus
  that is ~3 600 pages × ~4 KB ≈ **15 MB** — acceptable; if it is not, store it
  only when a post chain is configured (and treat its absence as "no post
  filters ran").
- Requires a schema migration. Follow whatever `db.connect()` already does for
  added columns, and keep the column optional so an older DB keeps working.

**Recommendation:** implement **A now** (unblocks the Franco volumes with no
schema change) and **B as the durable design**, so `--replay-filters` degrades
from "post-only, previously-unfiltered" to "post-only, always".

## 5. CLI / API sketch

```bash
# Re-apply the configured editor.post chain to the stored edited text.
# No model is loaded; the single-model lock is still taken (DB/library writes).
pha edit --replay-filters [--path collections/COLX] [--page N] [--dry-run]
```

- `--dry-run` reports what would change (pages replayed / declined with reasons)
  and writes nothing — the cheap way to answer "what would this cost?".
- Output mirrors `pha edit`:
  `replayed N page(s); declined M (pre/rules/model changed: a; no stored edit: b; human-reviewed: c)`
- `pha status` could surface a count of pages whose stored `filters` signature
  differs from the configured one **in the post part only** ("N page(s) can be
  replayed without the model").

## 6. Edge cases to decide

- **Filter removed** from the chain: replay cannot un-apply it → decline, and
  report "re-edit required".
- **Blank pages** (`_BLANK_EDIT_TEXT`): the edit path skips the model *and*
  filters but records the signature — the replay path must record the new
  signature too (cheap) and not try to filter the placeholder.
- **Pages in error** (`status='waiting'`, `error` set): re-edit, never replay.
- **Partial/aborted exports**: replay must finish by re-exporting the affected
  documents' `library/` pages so the human-readable surface and the DB agree
  (a re-edit normally ends with `write_edited_pages(...)`; mirror that).
- **Signature form** (R8): a stored signature that is the *same chain* in the
  other params form (resolved vs declared) is not a change — recognise it and
  normalise it rather than declining or re-running. On 2026-09-18 exactly one
  page (doc 54) was in that state.
- **Concurrent jobs**: commit frequently (e.g. every N pages) and retry on
  `database is locked`; never hold one transaction across a whole document while
  a scan is running (see §2.1 conclusion 3).
- **Artifact filters** (`returns: none`, `encoder.post`): out of scope here —
  they already have a stamp mechanism (`library/<slug>/.filter-stamps/`) and
  re-materialise without a model.

## 7. Test plan

1. **Unit** — a decision function `_replayable(page, edit_row, editor_stage, expected_filters)`
   covering: no edit; error; `raw_sha` mismatch; `reviewed_at`; `pre` changed;
   rules/model changed; recorded post empty (replayable); recorded post
   non-empty (declined); chain already current (no-op).
2. **Integration (model mocked)** — a collection with an empty recorded post
   chain + a new `post` filter: assert **zero** `chat_text` calls, the stored
   `text` equals `filter(old_text)`, `filters` updated, and the `library/`
   edited pages rewritten.
3. **Negative** — a `pre`-only change: assert the model **is** called.
4. **Idempotency** — run twice: second run reports 0 replays.
5. **Lock** — a concurrent `pha scan` must block the replay (and vice versa).
6. **Regression on the motivating case** — `collections/franco-imagens`:
   `pha edit --replay-filters --path collections/franco-imagens --dry-run`
   reports ~3 597 replayable pages, then the real run leaves no `-\n<lowercase>`
   line-end hyphen in the edited text while the **raw** transcription keeps the
   hyphens exactly as before.
7. **Signature agreement (R8)** — for a filter with manifest defaults and for one
   with a sidecar override: `_configured_filters_signature(cfg, specs) ==
   filters_signature(ran)` after `apply_filters(...)`, and a replay stores the
   former. This is the test that would have caught
   `pha-filter-signature-mismatch-bug-report.md`.
8. **Invariant guard** — for a `post` replay, `re.sub(r"[\s\-]+", "", before) ==`
   the same on `after` for every changed page (§2.1 conclusion 4).

## 8. Acceptance criteria

- Changing only `editor.post` on a document whose recorded post chain was empty
  completes **without any model request** and produces the same result as a full
  re-edit would (modulo model non-determinism).
- `pre`/rules/model/raw changes still re-edit, and human-reviewed pages are
  never touched.
- The command is opt-in, reports counts, supports `--dry-run`, and takes the
  scan lock.
- After a replay, a subsequent `pha edit` on the same document makes **no**
  model call and reports no pending work (R8 — measured on 2026-09-18 by hand:
  the four Franco volumes are in that state today).
- With Option B, the same holds when the previous chain already had `post`
  filters.

## 9. Non-goals

- Recomputing anything from the **page image** (that is `scan`).
- Replaying `pre` filters or editor prompts — their input is the model's, so a
  model call is inherent.
- Making replay automatic: it stays an explicit operator action.

## 10. References

- `README.md` §"Stage filters (deterministic text shaping around a model)".
- `FILTERS_PLAN.md` (the framework this extends).
- `enhancements/pha-filters-enhancement-request.md` (implemented; §"Editing a
  filter re-runs its stage" is the sentence this request qualifies).
- `enhancements/pha-filter-signature-mismatch-bug-report.md` (2026-09-18) — the
  defect that makes a replay's stored signature disagree with the configured one;
  a prerequisite, and the record of the manual Option-A run on this archive.
- Code: `ingest.py` `edit_document()`, `_edit_needed()`,
  `_configured_filters_signature()`, `_run_stage_filters()`;
  `db.py` `set_page_edit()`; `filters.py` `apply_filters()`, `filters_changed()`,
  `resolve_params()`.
