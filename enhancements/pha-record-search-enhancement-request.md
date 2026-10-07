# Enhancement request - search the encoder's structured records

**Status:** **Phase 1 committed; Phase 2 implemented in the working tree, not committed/released. Phase 3 open.**
**Date:** 2026-10-06
**Relates to:** PIPELINE.md (the "records in the index" claim), README
*Encoders*, enhancements/pha-notes-search-enhancement-request.md,
enhancements/pha-encoder-tools-enhancement-request.md, pha search /
pha_search, pha reindex, pha_get_page, and
filters/markdown-from-records/.

## 1. Motivation

pha encode turns a document into structured records (letters, people,
tables), stores them in the records table and writes
records-<encoder>.json, but **those records are not in the search index**.
The searchable index is built only from page chunks:

- index_document() chunks pages.raw_text and page_edits.text into
  chunks (ingest.py), with chunks_fts + embeddings;
- search.py queries only chunks_fts / chunk embeddings;
- records has no FTS or vector table and only the document_id index.

So a user cannot ask the archive "which letters are from Xavier?", "which
records mention Malaca?", or "when was this person active?" through
pha search. PIPELINE.md currently shows rec -> idx and says the records are
in the index; that is **not true in the implementation** and is the
documentation/feature mismatch this request fixes.

Current workarounds are real but inadequate for normal use:

- search the page text, then call pha_get_page() and inspect its encoded
  field - only works for records the encoder page-grounded, and only after
  the page was already found by a text hit;
- query records.data directly with SQLite JSON1 / jq - fine for a
  developer, invisible to historians and agents using pha_search.

Structured records are the natural index for people, correspondence and
catalogue entries. They should be searchable with the same query surface as
page text, while the page index remains unchanged.

## 2. Current behaviour (verified 2026-10-06, pha 0.40.3)

- records(id, document_id, encoder, kind, data, source, created_at,
  filters).
- source = the page on which a grounded record starts (often empty).
- data = the record JSON (kind, class, text, plus encoder-specific
  attributes such as from, to, date, place).
- pha_get_page() returns encoded records where records.source =
  str(page_no); there is no document-wide or archive-wide record query.
- pha reindex re-embeds page chunks only.

## 3. Goals

1. After pha encode, records are searchable without SQL or jq.
2. Keyword and (in a later phase) semantic search over record content.
3. Metadata filters: collection, encoder, record kind (letter,
   person, ...), and page/document.
4. Results are safe to present: document id, filename, page, encoder,
   record kind, full record identity, and a link back to the source page.
5. Existing page search remains backward compatible.
6. pha reindex can build/repair the record index after:
   - upgrading an archive whose records predate the feature;
   - re-encoding;
   - switching the embedding model (semantic phase).
7. The authoritative data remains the records rows and the
   records-<encoder>.json files; search is a derived index.

## 4. Non-goals

- A full structured query language or faceted browse UI. Fielded filters
  are explicitly Phase 2+.
- Indexing concatenated-*.md or markdown-from-records artifacts.
- Replacing pha search page hits, the proposed notes/ search, or
  pha_get_page's per-page encoded payload.
- Entity resolution, cross-record joins, or generating new records.
- Synthetic pages/chunks rows for records. The record index is a
  separate source, exactly as the notes-search request proposes.
- Mutating record JSON from the search path.

## 5. Proposed design

### 5.1 A searchable projection of a record

Add a deterministic search_text projection, e.g.:

~~~
kind: letter
class: letter
text: Carta do Padre Mestre Sao Francisco Xavier ...
from: Sao Francisco Xavier
to: Paulo Camerte
date: 16 de Dezembro de 1545
place: Malaca
~~~

Rules:

- include scalar leaves only (strings, numbers, booleans);
- stable key order (JSON insertion order or sorted; pick one and document it);
- include kind/class explicitly;
- skip page? Include it as metadata? Prefer metadata fields separate;
  search text should not be dominated by page numbers.

Add records.search_text TEXT via the existing additive db.migrate()
pattern. It is derived; search_text can always be rebuilt from data.

### 5.2 Keyword index

Add an FTS5 table mirroring chunks_fts:

~~~sql
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(text);
~~~

- rowid = records.id.
- db.add_record() writes the flattened text.
- db.clear_records() deletes the matching FTS rows before deleting
  records rows.
- db.migrate() / a one-time backfill inserts any missing FTS rows from
  records.search_text.
- Query is a mirror of db.keyword_search() but joins
  records_fts -> records -> documents and supports collection,
  encoder, record_kind.
- Read-only commands must tolerate the table not existing yet (older
  archive opened before a writer migrated it): degrade to empty + a note
  such as "record index not built yet; run pha encode or
  pha reindex --source records."

### 5.3 Semantic index (Phase 2)

One vector per record, because records are already discrete short objects:

~~~sql
CREATE TABLE IF NOT EXISTS record_embeddings (
    record_id   INTEGER PRIMARY KEY REFERENCES records(id) ON DELETE CASCADE,
    embedding   BLOB NOT NULL,
    embed_model TEXT,
    updated_at  REAL NOT NULL
);
~~~

- Use the same embedding model and prefixed(model, text, "doc") as
  chunks; query side uses the "query" prefix.
- index_records() embeds in batches after records are inserted.
- If the embed endpoint fails, records remain keyword-searchable and the
  document's encode result reports a note; no record text is lost.
- Semantic record search uses only vectors whose embed_model matches
  the configured model. If none match, it degrades to keyword with a note
  (same honesty rule as page search when the embed server is unavailable).
- pha reindex learns a --source pages|records|all scope. Default should
  be decided at approval (see section 9); the safe default for correctness
  after an embedding-model switch is all, while pages preserves today's
  cost.

### 5.4 Search integration

Refactor search.py around a discriminated result:

- page hit: existing shape plus kind: "page";
- record hit: kind: "record", record_id, document_id, filename,
  collection, encoder, record_kind, page_no, text, snippet,
  score, source (keyword/semantic/hybrid).

New CLI/MCP arguments:

~~~text
pha search QUERY --source pages|records|all
                 [--encoder ID] [--record-kind KIND]
                 [--collection COLX] [--limit N] [--mode ...] [--json]
~~~

MCP: pha_search(query, source=..., encoder=..., record_kind=..., ...).
Add pha_get_record(record_id) for fetching one full JSON record by id
(small, read-only, useful when a hit's data is truncated or omitted).

RRF must key on (kind, id), not chunk_id, so a record id can never
collide with a chunk id. In source=all, page and record hits are fused;
the ranking decision (do records get a small demotion?) is listed in
section 9.

### 5.5 Reindex and lifecycle

- pha encode writes records and immediately updates the keyword index
  (and Phase 2 embeddings). No silent stale record index after an encode.
- pha reindex --source records|all rebuilds search_text, records_fts
  and record_embeddings for the selected scope.
- Incremental behaviour mirrors chunks: skip embedding when the record has
  a current-model vector and unchanged search_text; --force re-embeds.
- pha rm / document deletion clears record rows and embedding rows via
  the existing cascade / cleanup path.

## 6. Surfaces and user stories

| User asks | Expected path |
|---|---|
| "Which letters are from Xavier?" | pha search "Xavier" --source records --record-kind letter |
| "Search all records that mention Malaca" | pha search Malaca --source records |
| "Search pages and records, fused" | pha search Malaca --source all |
| "Only the letters encoder" | add --encoder letters |
| "Show me that record" | pha_get_record(record_id) or pha page DOC PAGE --json |
| "Rebuild the record search after upgrading" | pha reindex --source records |

CLI output should label record hits clearly, e.g.:

~~~text
 3. [record][letters/letter] da Camara ... p.69  score=0.032
     Carta ... Malaca ...
     page: pha page 18 69
~~~

MCP record hits should carry enough metadata for an agent to fetch the page
and cite it without re-parsing the JSON file.

## 7. Tests and acceptance criteria

Acceptance criteria:

- [ ] pha encode on a document makes its records findable by
      pha search --source records.
- [ ] pha search without --source (or with --source pages) returns
      byte-identical results to today on the same archive.
- [ ] pha search --source records supports collection, encoder and
      record-kind filters and returns document + page metadata.
- [ ] Works when records.source is empty: the record is still findable
      and the hit says the record is not page-grounded.
- [ ] Keyword record search works with the embedding endpoint down.
- [ ] Phase 2: semantic and hybrid record search reuse the same embed
      model/prefix conventions as chunks and degrade honestly.
- [ ] pha reindex --source records repairs an archive that had records
      but no record index.
- [ ] Existing page-search tests are unchanged; new record tests cover
      empty, non-grounded, multi-kind and multi-encoder record sets.
- [ ] PIPELINE.md, README and MCP docs stop claiming records are already
      in the index, and document the new command/arguments.

Tests to add (indicative):

- tests/test_record_index.py - projection, FTS insert/delete, backfill,
  filters, missing-table degradation.
- tests/test_record_search.py - keyword, JSON result shape, RRF keying
  with (kind, id), source filter behaviour.
- tests/test_mcp.py - pha_search source/encoder/kind args and
  pha_get_record.
- Reuse tests/test_ingest.py / tests/test_filter_hooks.py fixtures for
  an encoded document.

## 8. Execution plan

**Phase 0 - approval**
- Confirm scope and settle the decisions in section 9.
- No code, schema or archive writes before this gate.

**Phase 1 - keyword record index and search surface**
1. Add records.search_text, records_fts, projection helper and
   db.migrate()/backfill in db.py.
2. Wire writes in db.add_record() / db.clear_records().
3. Add db.record_keyword_search() mirroring db.keyword_search() with
   collection/encoder/record-kind filters.
4. Add record-search functions and result discrimination in search.py;
   key RRF on (kind, id).
5. Add --source, --encoder, --record-kind to pha search and the
   MCP pha_search tool, plus pha_get_record.
6. Index records at the end of encode_document().
7. Add tests; update docs; verify on a real encoded collection
   (letters-from-missons / Documenta Indica).
8. Acceptance check against the criteria above; report in the Obsidian
   PHA task note.

**Phase 2 - semantic record search**
1. Add record_embeddings, embed/reuse logic and current-model filtering.
2. Add semantic + hybrid record lists; extend _rrf_merge() to fuse page
   and record hits.
3. Extend pha reindex with --source pages|records|all.
4. Add embed-down and model-switch tests.
5. Re-verify on the real archive; reindex cost measured and reported.

**Phase 3 - optional fielded filters**
- --field name=value / --where against json_extract(data,'$.name'),
  with an explicit allowlist or safe parameter handling.
- Only after Phase 1/2 proves useful; not part of the initial approval.

**Rollback**
- The new tables/columns are additive and derived. Disabling the feature
  (keep source=pages) leaves today's page search untouched. Dropping
  records_fts, record_embeddings and records.search_text loses only
  derived search data, never the records themselves.
- No existing command behaviour changes unless --source records|all or
  the new reindex scope is used.

## 9. Decisions needed before implementation

1. **Default source for pha search / pha_search.**
   Recommendation: pages (zero regression); records and all are
   explicit. Alternative: default all, which is more discoverable but
   changes result shape/ranking.
2. **Record semantic search in V1, or keyword-only first?**
   Recommendation: keyword-only Phase 1; semantic Phase 2. The request
   keeps both in scope, but approval can stop after Phase 1.
3. **pha reindex default scope.**
   Recommendation: all once Phase 2 exists, so "switch embedding model,
   run pha reindex" remains true for records too; pages preserves
   today's cost. Phase 1 can ship without changing the reindex command.
4. **Fielded filters.**
   Recommendation: V2; V1 uses full-text + encoder/kind/collection.
5. **Ranking in source=all.**
   Recommendation: fuse with RRF and no artificial record demotion until
   measured; expose metadata so an agent can prefer primary text.

## 10. Open questions / risks

- **Projection noise:** indexing the raw JSON would also index keys,
  booleans and page numbers. The search_text projection is the mitigation.
- **Record volume:** one embedding per record is efficient only while
  records are short. Measure on Documenta Indica before enabling semantic
  by default.
- **Recent schemas and read-only commands:** a query command opened before
  any writer created the new tables must not error; it must degrade to
  empty with an explicit "record index not built" note.
- **Re-encode identity:** records are replaced, so record ids change on a
  re-encode. The index writer must rebuild FTS/embeddings for the new rows;
  old rows are removed by cascade/clear.
- **Documentation drift:** PIPELINE.md currently promises this feature;
  Phase 1 must either implement it or correct the diagram until it ships.

## 11. Reference implementation notes

- src/personal_historical_archive/db.py - records schema and
  add_record() / clear_records(), keyword_search() /
  build_fts_query(), migrate().
- src/personal_historical_archive/ingest.py - index_document(),
  encode_document(), write_records_file().
- src/personal_historical_archive/search.py - _decorate(),
  keyword_search(), semantic_search(), _rrf_merge(), search().
- src/personal_historical_archive/cli.py - cmd_search(),
  cmd_reindex() and the argparse definitions.
- src/personal_historical_archive/mcp_server.py - pha_search and
  page/document query tools.
- tests/ - existing fixture patterns in test_ingest.py,
  test_filter_hooks.py, test_mcp.py, test_cli_reindex.py.

## 12. Approval requested

Please approve or amend:

- Phase 1 alone (keyword record index) or Phase 1+2 (plus semantic);
- the default source (recommended: pages);
- the reindex default scope (recommended: all after Phase 2);
- whether fielded filters should be deferred to Phase 3.

No implementation will start until this request is approved.

## 13. Approved decisions (2026-10-06)

The owner approved:

1. **Phase 1 first** - keyword record index and search surface. Phase 2
   semantic record search is deferred but remains designed.
2. **Default search source: all** - pha search / pha_search search both page
   chunks and structured records by default. Page-only remains available as
   --source pages.
3. **Default reindex source: all** - pha reindex rebuilds page chunks and the
   record keyword index by default. If an archive has no records at all,
   all is a no-op for the record side and the command does the same work as
   pages.
4. **Fielded filters deferred** to Phase 3. V1 uses full-text search plus
   collection, encoder and record-kind filters.

### Starting-page field naming

The starting page field is **not uniform across encoders in the current
archive**. Two spellings are already in production records:

- page_start - the Documenta Indica / letters / apparatus encoders;
- page - the older letters encoder (and the sample archive).

The canonical name for new work is **page_start**, meaning the 1-based
PDF page on which the record starts. The implementation will:

- resolve page_start first, then page and start_page as accepted aliases
  (source_page as a future-compatible alias);
- write the resolved value into the existing records.source column, so
  pha_get_page() and page linking work for both spellings;
- expose the resolved value as **page_no** in search results, so callers do
  not need to know which encoder wrote the record;
- document the alias resolution in the code and in the encoder guidance.

## 14. Related bug found during approval (2026-10-06)

`enhancements/pha-markdown-from-records-page-contract-bug-report.md` landed
while this request was being approved. It reports the same page-spelling
problem on the artifact side: `filters/markdown-from-records/filter.py` used
only `rec.get("page")`, so current `page_start` records generated segments
with `pages: "1"` and page-1 bodies, and `records.source` was empty.

The writer side is already covered by this request's alias resolver
(`db.record_start_page()` and `ingest.encode_document()`). The artifact filter
has been given the same alias contract in the working tree, with a regression
test. The bug report's full shared-page slicing port (line_start/header search,
explicit page_end, warning on no anchor) remains open and should be treated as
a follow-up, not silently folded into the Phase 1 search work.


## 15. Implementation status - 2026-10-06

Phase 1 is implemented in the working tree:

- `records.search_text` + `records_fts`; writes in `db.add_record()` /
  `db.clear_records()` / `db.delete_document()` and an idempotent backfill on
  writer connect.
- `db.record_start_page()` resolves `page_start`, `page`, `start_page`,
  `source_page`; `ingest.encode_document()` and `db.add_record()` write the
  resolved value into `records.source`.
- Record hit shape carries `kind: "record"`, `record_id`, `encoder`,
  `record_kind`, `page_no`, `text`, `snippet`, `score`, `source` and `data`.
- `pha search` / `pha_search` gained `--source pages|records|all`
  (default all), `--encoder` and `--record-kind`; `pha_get_record(record_id)`
  fetches one record.
- `pha reindex --source pages|records|all` (default all) rebuilds the record
  keyword index; when an archive has no records, all does the same work as
  pages.
- Tests: `tests/test_record_search.py`, plus the filter alias regression test.
  The full suite passes (970 passed).
- Real-data smoke test on a copy of the jesuit-archive DB: 3,258 records
  indexed, 3,258 FTS rows, all `source` values populated, keyword hits for
  Xavier at the expected records.

Phase 2 (semantic record search), Phase 3 (fielded filters) and the full
shared-page artifact port from `pha-markdown-from-records-page-contract-bug-report.md`
remain open.


## 16. Phase 2 implementation status - 2026-10-06

Phase 2 (semantic record search) is implemented in the working tree:

- `record_embeddings(record_id, embedding, embed_model, updated_at)` with
  `ON DELETE CASCADE`, add/clear/query helpers in `db.py`.
- `ingest.index_records()` embeds one vector per record using the same embed
  model and document prefix as page chunks. It reuses current-model vectors
  incrementally and leaves existing vectors untouched if a re-embed fails.
- `pha reindex --source records|all` now builds record embeddings and the
  record keyword index. `--source all` (default) still does the same work as
  `pages` when no records exist.
- `search.semantic_record_search()` and record hybrid search are wired into
  `pha search` / `pha_search` for `--source records` and `--source all`;
  page+record RRF keys on `(kind, id)`.
- Tests: `tests/test_record_search_semantic.py`; full suite passes
  (978 tests).
- Real-data smoke test on a copy of the jesuit-archive DB: 3,258 records
  across 8 documents embedded and stored.

Phase 3 (fielded filters such as `--field from=Xavier`) remains open.
