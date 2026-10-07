# Bug report - markdown-from-records ignores the canonical page_start contract

Status: FIXED in the working tree (full page-span port and tests); not committed yet.
Found: 2026-10-06 on jesuit-archive, pha working tree 0.40.3 with the
record-search changes uncommitted.
Impact: silent wrong output and broken page lookup. Every generated segment
points at page 1 and records.source was empty for current records.

## Summary

filters/markdown-from-records/filter.py resolves the starting page with
rec.get("page"). Documenta Indica and Litterae Quadrimestres records use
page_start (and sometimes page_end/line_start), so _page_chunks() falls back to
start = 1 for every record. The filter writes every file with pages: "1" and
the body of page 1 instead of failing.

The same class of bug existed in ingest.encode_document(). That side is
already fixed in the working tree through db.record_start_page(); the filter
still has the old spelling.

## Evidence (jesuit-archive, 2026-10-06)

- records-documents.json for DOCUMENTA-INDICA-1540-49.pdf: 194 records, all
  page_start.
- All 9 current records-*.json files: 3,258 records, 3,258 page_start, 0 legacy
  page.
- segments-documents/: 184 .md files + index.md for 194 records; sampled files
  all have pages: "1" and page-1 bodies.
- The reference implementation
  dropbox/collections/documenta-indica/encoders/_tools/markdown-from-records/markdown_from_records.py
  already uses page_start, page_end and line_start.
- records.source was empty for every row; it was backfilled locally from
  page_start on 2026-10-06 (3,258 / 3,258 filled).

## Root cause

The shipped filter was not the approved port of the reference implementation.
FILTERS_PLAN.md section 5 requires page_start/page_end/line_start and a faithful
port of the shared-page split; the current filter supports only the legacy page
key.

db.record_start_page() in the working tree already defines the accepted keys:

    RECORD_START_PAGE_KEYS = ("page_start", "page", "start_page", "source_page")

but the filter does not use it.

## Suggested fix

1. Reuse the same resolver contract in the filter, either by importing the
   package helper or by keeping a small local helper with identical keys.
2. Port the reference implementation's page logic:
   - sort by (page_start, line_start);
   - use explicit page_end or the next record's page_start;
   - split a shared page at line_start, else search for the next record header;
   - emit only that record's page slice.
3. Fail loudly when a record has no usable page anchor instead of defaulting to
   page 1.
4. Add regression tests:
   - legacy page record;
   - current page_start record;
   - explicit page_end;
   - two records sharing a page with line_start;
   - shared page without line_start (documented fallback);
   - no page anchor gives a recorded run failure or clear warning.
5. Add a test that records.source and the filter page span agree for the same
   record.

## Data notes

- Current records-*.json files are already canonical on page_start; no key
  rename is required if the filter is fixed. A temporary page alias in the data
  would be a compatibility shim only.
- The records.source backfill is idempotent:
  UPDATE records SET source = CAST(json_extract(data, '$.page_start') AS TEXT)
  WHERE COALESCE(source, '') = '' AND json_extract(data, '$.page_start') IS NOT NULL;
- Future pha encode runs must keep this correct through
  db.record_start_page()/add_record(); the existing working-tree change already
  does that on the writer side.

## Related

- enhancements/pha-enhancement-requests-INDEX.md
- FILTERS_PLAN.md section 5 - correctness requirement and explicit test vectors
- filters/markdown-from-records/filter.py
- src/personal_historical_archive/db.py::record_start_page
- src/personal_historical_archive/ingest.py::encode_document
- dropbox/collections/documenta-indica/encoders/_tools/markdown-from-records/markdown_from_records.py

## Working-tree update - 2026-10-06

Partially fixed while implementing the record-search enhancement:

- `filters/markdown-from-records/filter.py` now resolves the starting page
  with the same contract as `db.record_start_page()`: `page_start` first,
  then `page`, `start_page`, `source_page`.
- Records are sorted by `(page_start, line_start)`, explicit `page_end` is
  honoured, and a record with no usable page anchor is skipped instead of
  being silently assigned page 1.
- The filter now uses the resolved start for both the span inference and the
  output filename/front matter, so current `page_start` records no longer
  produce `pages: "1"` or page-1 bodies.
- Regression test added:
  `tests/test_filters.py::test_markdown_from_records_resolves_page_start_and_legacy_page`.
- `db.record_start_page()` and `ingest.encode_document()` continue to write
  the resolved start into `records.source`, so the same record shows the same
  page in search, `pha_get_page()` and the artifact filter.

Still open from this report:

- The full reference port of shared-page slicing (header search, exact
  `line_start` split, and the documented fallback when no anchor is found) is
  not implemented. Today a shared page without `page_end` or a usable
  `line_start` is still assigned wholly to the earlier record.
- A filter-level failure/warning for a record with no page anchor is not yet
  surfaced to the run report; it is skipped with no output.


## Full fix - 2026-10-06

The remaining shared-page work from the suggested fix is now implemented in
`filters/markdown-from-records/filter.py`:

- records are sorted by `(page_start, line_start)`;
- explicit `page_end` is honoured;
- a shared page is split at the next record's `line_start`, or at its header
  via the reference `find_boundary` fallback;
- when no own anchor can be found, the shared page is given to the previous
  record and the later record receives no duplicate slice;
- records with no usable page anchor are skipped with a clear warning, not
  silently assigned page 1;
- regression tests cover the current `page_start` spelling, legacy `page`,
  explicit `page_end`, shared-page `line_start`, no-anchor fallback and a
  full `run()` output check for `pages: "69"`.
