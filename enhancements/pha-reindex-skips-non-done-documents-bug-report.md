# Bug report — `pha reindex` silently skips a document that is not `done`, so returned hand-over work is applied but never searchable

**Status:** **FIXED** (2026-09-26) — see §4 and §7. F1 was implemented in its
**corrected** form: the naive version in the first draft would have marked
never-read pages as transcribed (§7.1). Found: 2026-09-26, applying a hand-over of
`collections/jesuit-early-authors/fernao-guerreiro` (3 tomes, 1 392 pages) on
the owner machine, `pha handoff fetch` followed by `pha reindex`.
**Severity:** **silent and successful-looking.** The command exits `0` and
prints `reindexed 0 document(s) … unchanged chunks reused` while the document
has 476 pages of applied text and **zero chunks**. `--force` does not help — it
prints `[forced: every chunk re-embedded]` and still reindexes nothing. The
returned work is invisible to `pha search` with no error anywhere.

## 1. Summary

A hand-over returns the pages it has **text for**. Pages the worker found blank
(the 32 plate/blank pages here) come back with no content, and `handoff fetch`
**drops** them, leaving them `pending` locally. The ownership rule that follows
then composes badly with two others:

1. `handoff.py` marks the document `done` only if
   `count(pages WHERE status='done') >= page_count` — with 476 of 496 done, the
   document stays **`processing`**;
2. `reindex_all` **skips any document whose status is not `done`**, by design;
3. therefore the reindex reindexes nothing, and `--force` cannot help because
   the status gate sits upstream of the force flag.

Net effect: a document can be fetched, rendered, and hold every page of the
worker's work, yet be permanently unindexed with only a misleading
"0 document(s)" line to show for it.

## 2. Evidence

All measured on 2026-09-26.

### 2.1 `handoff fetch` reports the dropped pages

```
rendered 496 page image(s) for #101 (fernao-guerreiro-relacao-anual-t1-1600-1603.pdf)
rendered 442 page image(s) for #102 (fernao-guerreiro-relacao-anual-t2-1604-1606.pdf)
rendered 454 page image(s) for #103 (fernao-guerreiro-relacao-anual-t3-1607-1609.pdf)
applied without embedding: search still shows the OLD text for 3 document(s) — run
  pha reindex --doc 101 --doc 102 --doc 103
applied 3 document(s): 2720 page(s)/edit(s) from the worker, 0 skipped …, 32 dropped
```

The worker's own return leg reported `1392/1392 pages done, 1360 edit(s)`.

### 2.2 The dropped pages stay `pending`, so the document stays `processing`

```
doc 101: done=476  pending=20     (page_count 496, status processing)
doc 102: done=433  pending=9      (page_count 442, status processing)
doc 103: done=451  pending=3      (page_count 454, status processing)
```

32 dropped == 20 + 9 + 3.

### 2.3 The reindex is a no-op, twice, with exit 0

```
$ pha reindex --doc 101 --doc 102 --doc 103
reindexed 0 document(s) (doc #103) [incremental: unchanged chunks reused]

$ pha reindex --doc 101 --force
reindexed 0 document(s) (doc #101) [forced: every chunk re-embedded]

$ sqlite3 archive.db "select id,status,(select count(*) from chunks c where c.document_id=d.id) from documents d where d.id in (101,102,103)"
101|processing|0
102|processing|0
103|processing|0
```

### 2.4 Working around it

A local scan finished the 32 pending pages and indexed the document:

```
$ pha scan --path collections/jesuit-early-authors/fernao-guerreiro
  indexing 2362 chunks (2362 to embed, 0 reused) …
scanned 3 file(s): {'ingested': 3, 'skipped': 0, 'error': 0, …}
#101 done 496 pages 2303 chunks · #102 done 442 pages 2168 chunks · #103 done 454 pages 2362 chunks
```

So the returned text was always indexable; only the gate stopped it.

## 3. Root cause

Three rules that are each reasonable, composing into a silent dead end.

- **The apply drops content-less pages and leaves them `pending`**
  (`handoff.py` apply loop). The "32 dropped" count is reported, but the pages
  are not resolved — they are left as unfinished work.
- **The document status requires *every* expected page `done`**
  (`handoff.py:1370-1376`):

  ```python
  have = conn.execute(
      "SELECT COUNT(*) n FROM pages WHERE document_id = ? AND status = 'done'",
      (doc_id,)).fetchone()["n"]
  db.set_document_status(
      conn, doc_id, "done" if (expected and have >= expected) else "processing")
  ```

  With any dropped page, `have < expected` and the document is `processing`
  forever, because nothing local will ever revisit those pages unless asked.
- **`reindex_all` skips non-`done` documents** (`ingest.py:3846`, docstring:
  *"Documents whose status is not 'done' are skipped"*), and `--force` only
  governs *which chunks* are re-embedded, not *which documents* are considered.
  So the one command `fetch` itself tells the user to run is inert, and the
  force flag appears to run while doing nothing.

The same trap must apply to **any** interruption that leaves a document
`processing` — a stalled page, a killed pass, a partially-resumed scan — the
hand-over path just makes it certain, because dropping content-less pages is
its normal behaviour.

## 4. Fixes — as implemented

**F1 — resolve the dropped pages, but only the ones that were actually read
(FIXED, in a corrected form; see §7.1).** `_decide_merge` now reads the page's
`status`, which the payload already carried and nothing looked at:

- worker `status == 'done'` and no text → the worker **read** the page and there
  is nothing on it. The page is recorded exactly as a local blank page is
  (`set_page_result` with empty text **is** `done`, `db.py:456-469`), so the page
  count can close and the document can reach `done`. A new `blank` outcome counts
  these, separately from `took-worker`.
- worker `status != 'done'` and no text → the worker **never read** the page (a
  stalled or interrupted pass). Nothing is written, the page stays `pending`, and
  a new `unread` outcome reports it. Marking these `done` would fabricate a
  reading for a page nobody transcribed — see §7.1 for why that is the worse bug.
- a page this archive already holds text for is **kept**, and a human-`reviewed`
  page still outranks any machine answer. A blank never wipes a reading.

**F2 — `reindex` must not silently skip (FIXED, split by scope, not removed).**
The gate stayed for a bulk pass, where skipping a half-finished volume is
deliberate (embedding it is hours of work the remaining pages invalidate), but it
can no longer be silent:

- an **explicit** scope — `--doc N` or `--path …` — is honoured whatever the
  status is. This is the fix that matters: `pha reindex --doc N` is the command
  `handoff fetch` prints, and it now works on the document it just told you about.
- a bulk pass skips but **names** each skip with its page counts
  (`#101 vol.pdf: 476/496 pages done`), in `skipped_not_done` on the result and
  on stderr from `pha reindex`.

Deliberately **not** an exit-non-zero: with `--doc`/`--path` honoured, an
explicit request can never be silently skipped, and a bulk pass legitimately
skips unfinished documents during normal ingestion — failing the command every
time a collection is mid-scan would train the operator to ignore it. The failure
mode here was *silence*, and the report is now loud.

**F3 — `--force` means it (FIXED).** `--force` bypasses the status gate as well
as chunk reuse, so `[forced: every chunk re-embedded]` can no longer be printed
over `0 document(s)` because of a document-level gate. (It can still print 0 when
there are genuinely no documents, or when every one is out on a hand-over — and
that case is named on stdout, as before.)

**F4 — `handoff fetch` names the finish line (FIXED).** `apply_result` now
reports `status` and `open_pages` per document, and `pha handoff fetch` prints
the difference between the two kinds of empty page, because they mean opposite
things:

- blank pages are stated as **resolved** ("the worker READ them and found
  nothing — recorded as done, the same as a local blank page");
- pages the worker never read are stated as **unfinished**, per document
  (`#101 collections/…: 476/496 pages done, 20 never read — finish with: pha scan
  --path …`), so a `processing` document is explained instead of merely shown.

**Regression tests.** `tests/test_handoff.py` covers the two empty-payload cases
end to end (a blank resolves; an unread page does **not** become a reading), the
"never wipe an existing reading" rule, and the pure decision table.
`tests/test_ingest.py` covers the bulk skip report, explicit `--doc`/`--path`,
and `--force`.

## 5. Cost

1 392 pages and ~2 700 chunks of finished work sat applied but unsearchable
until noticed; the fix-up scan took ~20 minutes. On a larger hand-over (the
2 676-page Monumenta Brasiliae round trip has the same shape) this scales
linearly, and the failure is invisible from `pha status`, which shows only
`processing` on a document the operator believes is finished.

## 6. Method note

Reproduced end-to-end from the archive's own output: the fetch summary, the
`pages` table grouped by status, the `documents` rows with their chunk counts,
and three `reindex` invocations. No internals were poked; the workaround is the
documented `pha scan --path <collection>`.

## 7. Two corrections to the original report

Both were found while implementing the fixes, and both change what should be
built — hence this section rather than a silent edit above.

### 7.1 F1 as first written would have picked the worse bug

F1 rested on: *"A page the worker returned with no text **was read**; there is
nothing on it."* **That is not guaranteed.** `build_result` exports every page of
the worker's document —

```python
"SELECT * FROM pages WHERE document_id = ? ORDER BY page_no"   # handoff.py
```

— with **no status filter**, deliberately (the worker's unfinished pages have to
travel so the owner learns the document is incomplete). So a page the worker
never read — left `pending` by a stalled page, a killed pass, an interrupted
`--pages` list — arrives at the merge with empty `raw_text`, **byte-identical**
to a page the worker read and found blank. `_decide_merge` looked only at the
text, so it could not tell them apart, even though `_page_payload` puts `status`
in the payload and nothing read it.

Implemented as written, F1 would therefore have marked never-read pages `done`:
a **fabricated reading** — the archive asserting that a page was transcribed when
nobody transcribed it — plus genuinely unfinished work hidden behind a `done`
status. On this archive that is worse than the bug being fixed.

The distinction is not a detail to be waved through by the incident's own
numbers. Here the worker reported `1392/1392 pages done`, so all 32 dropped pages
*were* the blank kind and naive F1 would have looked correct. It would have
corrupted the first interrupted pass instead: exactly the case the design already
warns about elsewhere ("a `*waiting*` stub is the absence of a page, not
content").

So the rule keys on `status`, and the two outcomes are reported separately
(`blank` vs `unread`) rather than collapsed into one "dropped" count.

### 7.2 The situation was one flag from recovery

The report says the one command `fetch` tells the user to run is inert — true —
but concludes the work was unrecoverable short of a re-scan. It was not:
**`index_document` has no status gate.** Only `reindex_all` has one, and
`handoff fetch --index` calls `index_document` directly. So

```
pha handoff fetch <result> --index
```

would have embedded the 476 finished pages there and then, and
`pha reindex --doc N` would have too had the gate not swallowed it. The bug is
real and the gate is indefensible as it stood; but "applied but never
searchable" overstated it, and the omission of `--index` — a flag on the very
command that was run — made the incident look worse than it was. It matters
because it points at the actual defect: not that embedding was impossible, but
that the *advertised* path to it was inert and silent.
