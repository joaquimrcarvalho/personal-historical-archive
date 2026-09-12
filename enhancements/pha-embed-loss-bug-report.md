# Bug — a failed embed silently deletes a document's stored vectors

**Status:** **FIXED** (2026-09). See §6 for what changed. Written from the
2026-09-12 incident on `jesuit-archive`.
**Severity:** high — silent, unrecoverable-without-re-embedding loss of the
semantic index for whole documents; invisible in `pha status`.
**Motivating incident:** 13 885 chunks across 4 documents lost all embeddings
while two jobs overlapped.

## 1. Summary

`index_document()` replaces a document's chunks **wholesale**: it calls
`db.clear_chunks(conn, doc_id)` and only then builds and embeds the new chunk
list. When `embed()` raised `ModelError` — a 120 s batch timeout, the classic
symptom of endpoint contention — the old code caught it and substituted
`[None] * len(items)`, indexing text-only:

```python
db.clear_chunks(conn, doc_id)          # vectors destroyed FIRST
...
except ModelError as e:
    vecs = [None] * len(items)         # ... then quietly degrade
    print(f"  warning: embeddings unavailable ({e}); indexing text-only")
```

So a transient embed failure **deleted vectors that were already stored** and
replaced them with a text-only index. The document kept `status = done` and an
empty `error`, so nothing surfaced it: `pha status` reported a healthy archive
and `pha reindex` reported success. The only trace was the embedded-chunk
count.

There was no lock protecting this: `pha scan` and `pha edit` shared the
single-model lock, but `pha reindex` — which loads the embed model — took
nothing, so it could run concurrently with another local-model job. That
concurrency is what times the embed requests out, making the data-loss path
easy to reach.

## 2. Observed damage (jesuit-archive)

```
doc  collection             chunks  without embedding
45   medina-docs-japon      3 561   3 561
46   medina-docs-japon      3 557   3 557
47   pfister-notices        3 251   3 251
50   pfister-notices        3 516   3 516
                                    -----
                                    13 885
```

`59158 indexed (45273 embedded)` — hybrid search falls back to keyword-only
for exactly those two collections.

Chunk-id ranges proved the chronology: a `medina+pfister` reindex ran
(ctx `253457`–`267341`) inside the window in which another operator's reindex
was still finishing (`247508`–`253456`), i.e. two jobs overlapping.

## 3. Why the existing guards did not catch it

- **Staleness/mtime checks** never look at the embedded count.
- **`pha status`** counts chunks and embedded chunks, but as two totals — a
  per-document deficit is not called out, and `documents.status` stays `done`.
- **`pha reindex`** printed `reindexed N document(s)` with no failure count.
- **The `error` column** is only written on transcription failures, not on an
  indexing degradation.

## 4. Impact

Semantic search silently disappears for the affected documents; the archive
still answers keyword queries, so the degradation is easy to miss. Repair
means re-embedding every affected chunk (≈2 h of local model time for the 4
documents above), and the loss is only visible if someone counts embeddings
per document.

## 5. Interaction with other work

`FILTERS_PLAN.md` §4/§8 and the review-scope fix both assume re-running a
stage is safe and cheap. An indexing step that can destroy its own output on a
transient error undermines that assumption for every future re-run.

## 6. Resolution

**Compute before destroying.** `index_document()` now embeds the new chunks
**first** and only calls `clear_chunks()` once the vectors are in hand — so a
failed embed cannot touch the stored index at all. The outcome then depends on
whether there is anything to lose:

- **Document already has vectors** → the `ModelError` propagates, the document
  is left **completely untouched** (chunks and vectors intact), and the caller
  reports it. A transient failure can no longer cost a finished document its
  semantic index; the fix is to re-run.
- **Document has no vectors yet** (a fresh ingest while the embed endpoint is
  down) → still degrades to text-only with the warning, preserving `pha scan`'s
  zero-config behaviour. This is safe: there is nothing to delete.

**`pha reindex` takes the single-model lock.** It loads the embed model, so it
now uses the same lock as `pha scan`/`pha edit`; a concurrent job makes it
refuse with `another scan/edit/reindex job is running (one local model at a
time)` and exit non-zero, instead of running and timing out.

**Failures are reported.** `reindex_all()` returns a `failed` list (document
id, filename, error) instead of counting a failed document as reindexed, and
`pha reindex` prints them to stderr and exits **3**, so a scripted/background
run cannot mistake a failed re-index for a successful one. `pha status`'s
per-document chunk/embedded counts remain the way to audit a past run.

**Tests** (`tests/test_ingest.py`):
`test_index_document_keeps_vectors_when_embed_fails` (the exact regression:
vectors survive a dead endpoint, chunks are not cleared, and a document with
no vectors still degrades to text-only),
`test_reindex_all_reports_failed_documents_and_keeps_them`, and
`test_reindex_all_holds_the_single_model_lock`.

## 7. Repair of the incident data

The four documents are text-only but otherwise intact (chunks and FTS are
present), so they only need re-embedding — and because they currently hold no
vectors, the new code cannot make them worse if the endpoint fails again.

**Run this on the archive machine, from the archive directory** (see the
warning below about a workspace `.env` hijacking `PHA_ARCHIVE_DIR`):

```bash
cd /Users/jrc/jesuit-archive
pha reindex --path collections/medina-docs-japon    # docs 45, 46
pha reindex --path collections/pfister-notices      # docs 47, 50
```

Run them **one after the other** — the lock now makes the second refuse
rather than compete for the embed endpoint. Nothing needs re-scanning or
re-editing: only vectors are missing, and the chunk text/FTS rows are what
keyword search already uses. Verify with:

```sql
SELECT c.document_id, COUNT(*) chunks, SUM(c.embedding IS NOT NULL) embedded
FROM chunks c GROUP BY c.document_id HAVING embedded = 0;
```

### Warning — a workspace `.env` can silently retarget the archive

While verifying the repair: a `pha` run from a directory whose `.env` sets
`PHA_ARCHIVE_DIR` targets **that** archive, not the one named by the shell's
`PHA_ARCHIVE_DIR` — `/Users/jrc/develop/personal-historical-archive/.env`
points at the project root, so `pha reindex` run from the checkout reported
`reindexed 0 document(s)` and exited **0** against an empty DB instead of
failing loudly. That is how two repair attempts appeared to succeed while
doing nothing. Check `pha info --json` (it prints the resolved archive/db
paths) before trusting a background job's output, and run archive maintenance
from the archive directory.

## 8. Still open

- **An `error`/`status` marker for indexing degradation.** `pha status`
  already flags a document whose `embedded < chunks` per document
  (`4 000 chunks (0 embedded)` plus `[keyword-only — run pha reindex]`), which
  is how the incident was caught — but the archive-level `overview` totals
  (`chunks: 59158 indexed (45273 embedded)`) and `pha reindex`'s own output
  gave no hint. A document indexed text-only during a FRESH ingest is likewise
  indistinguishable from a healthy one on every surface except that marker.
- **Backfilling vectors already lost to this bug** has to be a full reindex of
  the affected documents; there is no cheaper path, and no way to tell *when*
  a document degraded (chunks carry no embedding timestamp).
