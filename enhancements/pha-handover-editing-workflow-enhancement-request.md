# Enhancement request — hand-over and per-page editing: five workflow gaps, measured

**Status:** draft for decision. **Written:** 2026-09-22, from a real hand-over:
4 volumes of *Monumenta Brasiliae* (2 676 pages) processed on a Mac Mini and
applied back on the owner's MacBook. Every number below was measured, not
estimated.

**Why this document exists.** The owner's rule, stated after watching this
hand-over: *pha is driven through its external interface — the CLI and the MCP
server — never through scripts that reach into its internals; when the workflow
needs more, the need is specified and implemented in pha.* During the hand-over
the gaps below were worked around with local scripts (a per-page fix loop, a
no-index patch, a detached endgame, a lock-window script). They worked, and
that is exactly the problem: they are unrecorded, unreviewable, and invisible to
anyone who picks up the archive later. Each gap therefore gets a proposed
**interface**, not a workaround.

## G1 — the worker indexes work the owner indexes again

`pha scan` and `pha edit` call `index_document` for every document they touch
(`ingest.py:1011`, `ingest.py:2089`). On a hand-over the owner's `fetch` re-indexes
the same documents when it applies the results (`handoff.py:1222`). Measured here:
the Mini embedded **2 676 pages** (raw *and* edited variants) that were re-embedded
on the MacBook minutes later — hours of local embedding work spent twice.

**Spec.** A worker's passes should not embed. Either
- an explicit `--no-index` on `scan` / `edit` / `handoff work`, or
- an implicit rule: *a document under an active out-lease (`handoff out`) is not
  indexed on the machine that holds the lease*, since the owner's `fetch` is the
  single place that embeds.

`handoff back` should report how many chunks were deliberately left unbuilt, so
the omission is visible rather than assumed.

## G2 — a single-page edit re-indexes the whole document

`edit_document` finishes by calling `index_document(doc_id)` (`ingest.py:2089`),
which re-chunks and re-embeds **every page of the document**, regardless of how
many pages were edited. On a 642–754-page volume (~2 700 chunks) that is
**~4–5 minutes per corrected page**, against ~15–20 s for the model call itself.
Measured during a per-page correction pass (timestamps of successive
`page_edits.updated_at`):

```
00:00:01  00:04:25  00:08:34  00:13:22  00:18:20  00:22:28  00:27:03  00:31:14
```

Correcting the 290 pages the archive needs would cost **a day of wall time** for
roughly half an hour of actual model work.

**Spec.** Index what changed:
- default: index **only the edited page** (one chunk set), so `pha edit --page N`
  costs seconds;
- `--reindex-document` (or `--reindex`) for the current behaviour when a full
  refresh is genuinely wanted;
- batch corrections then end with one `pha reindex --path <collection>`, which
  already exists.

Also worth stating in `pha edit --help` today: that a one-page edit re-embeds the
whole document. Nothing in the output says so.

**Status: FIXED.** `index_document` is now **incremental and page-scopable**. A
chunk whose text is byte-identical to the stored one, and whose stored vector
names the embed model now configured (`chunks.embed_model`), is reused instead
of re-embedded; only the changed chunks reach the embed model. So the default
above — "index only the edited page" — is what `pha edit --page N` does now,
and `pha reindex --doc N --page P` does it for a library correction. The
"current behaviour" escape hatch is `pha reindex --force` (re-embed every
chunk); `pha reindex --doc N` scopes the whole-document refresh to one
document. Regression tests:
`tests/test_ingest.py::test_index_document_reuses_unchanged_chunks`,
`::test_reindex_all_page_scope_leaves_other_pages_untouched`,
`::test_index_document_reembeds_when_embed_model_changed`.

## G3 — `handoff fetch` cannot wait for a busy model server

`apply_result` takes the embedding lock and **raises** if it is busy
(`handoff.py:1130-1135`); locks are refuse-not-queue by design
(`locks.acquire`, `locks.py:336-358`). Consequence measured here: with a
long-running `pha scan` holding the lock, applying a finished hand-over required
*stopping that scan* — so the owner deferred the fetch instead, and the returned
work sat unapplied.

**Spec.** A waiting mode on lock-taking commands:
`--wait[=SECONDS]` (or `PHA_LOCK_WAIT`, or `--wait` on `handoff fetch`,
`scan`, `edit`, `reindex`): queue instead of refusing, print who holds the lock
and how long it has been held, and keep `--no-wait` as today's default. The
hand-over is the motivating case: applying results should never demand that the
owner interrupt work in progress.

## G4 — model-call stalls are unbounded inside a per-page loop

`timeout_s` is per httpx *operation*, not per request (see
`pha-request-stall-timeout-bug-report.md`), so one stalled socket can hold a
per-page loop for hours — measured twice in this archive (6 h 30 and 4 h 17).
During the pass above, one `pha edit` process was found parked in
`internal_select → poll` on an SSL socket while the rest of the run advanced at
its steady 4–5 min/page.

**Already specified elsewhere — G4 adds no second fix.** The defect itself is
`pha-request-stall-timeout-bug-report.md`: **F1** a wall-clock `deadline_s` per
request (distinct from the per-operation `timeout_s`), **F2** a budget that covers
the whole page including retries, **F3** visibility without an external watchdog,
**F4** documenting the knob, plus the tests — including the "slow but honest"
case, so a legitimate long page is not cut off.

**The one point worth adding to that report** is the batch behaviour: when the
deadline expires inside a multi-page pass, the page should be **abandoned for
this pass**, named in the output, and retried by a later pass — rather than
failing the page (or the run). With the per-page loop measured at 4–5 min/page
before G2, one stalled socket could otherwise hold the whole loop for hours.

**Status: FIXED** (together with F1–F4 in that report). `ModelClient` raises
`ModelStall(ModelError)` when the per-attempt `deadline_s` expires, and the scan
and edit page loops catch it *before* the `ModelError` guard: the page is
abandoned for the pass (no error row, left pending, not counted toward the
consecutive-failure abort), the pass continues and names it
(`⏱ 2 page(s) abandoned for this pass (model stalled): 12, 34 — re-run …`),
what was already read is still edited and indexed, and the document is left
`processing` so the next `pha scan`/`pha edit` resumes exactly those pages.
The result carries `action: "stalled"` and `stalled: [N, …]` for agents.
Tests: `tests/test_stall_batch.py`.

## G5 — a worker's job should be one standard command

Today, correcting a page list on the worker meant the owner orchestrating
repeated `pha edit --path … --page N` invocations over ssh, from a local script.
`pha handoff work` exists as a thin wrapper over `scan` → `edit` → `encode`;
extending it (or adding a `--pages` / `--resume` form) would let the *entire*
worker job be a single pha command, run by the worker, with no external
orchestration.

## Acceptance criteria

1. A hand-over of an unscanned document builds **no** chunks on the worker and
   complete chunks on the owner; `handoff back` states the deliberate omission.
2. Correcting one page of a 700-page volume costs a model call plus seconds, not
   minutes.
3. `handoff fetch` can wait for a busy model server and reports the holder.
4. A stalled page cannot hold a batch run past its deadline; the run continues
   and names the page it abandoned.
5. The whole worker job is expressible as one pha command.

## Relation to existing documents

- `pha-request-stall-timeout-bug-report.md` — G4 is the batch-command view of the
  same defect; the deadline is per page, the bug report's is per request.
- `pha-handoff-enhancement-request.md` §12 — the renders gap (a document handed
  out unscanned comes home with no page images).
- `pha-per-server-model-lock-enhancement-request.md` — G3 asks for a waiting mode
  on top of the shipped endpoint-scoped locking; the "refuse, never queue" choice
  is deliberate there and is what bites here.
- `pha-single-page-rescan-enhancement-request.md` — page-scoped operations; G2 is
  the indexing half of the same problem.

## Note on process

The workarounds used during this hand-over were **recovery**, with the owner's
confirmation, and have been removed from the worker machine. This document is
what replaces them.
