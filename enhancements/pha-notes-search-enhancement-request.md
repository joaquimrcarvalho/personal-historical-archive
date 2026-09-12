# Enhancement request — search the notes folder

**Status:** draft for discussion.
**Author/date:** archive work session (Documenta Indica; Obsidian integration).
**Relates to:** `pha-stable-page-addresses-enhancement-request.md` (notes cite the
stable slug and can embed the render URL), `notes/README.md` (the note format and
citation convention), the `dsh-pha` plugin's `/pha/notes` + `/pha/note` routes
(browse and read today; search is the missing half), and `WEB_INTERFACE_PLAN.md`.

## 1. Motivation

`notes/` is the synthesis step of the workflow: `pha search` returns snippets,
`pha page` returns a full page, and a note is what a researcher writes after
reading them. But a note is **invisible to search** — the one artifact you author
is the one you cannot query.

The reason is structural. Everything searchable in pha is a chunk of a page:

- `chunks` requires a page: `page_id INTEGER NOT NULL REFERENCES pages(id)`
  (`db.py:34-43`), and chunks are built only from a page's `raw_text` and the
  editor's output in `index_document` (`ingest.py:786-833`).
- Keyword search joins the whole chain — `chunks_fts → chunks → pages →
  documents` (`db.py:556-576`) — and the collection filter matches
  `documents.dir_path` (`db.py:257`). Semantic search joins `documents` too
  (`db.py:523`).
- Hybrid fuses the two lists by `chunk_id` (`search.py:84-100`).

`notes/` never enters that pipeline, by design: `.md` is not a scan-supported
document type (`extract.py:8`), and AGENTS.md states notes are user output, not
pipeline output — `pha scan`/`edit`/`encode` never read or write them.

Consequence today: a question the archive answers only in a note written last
month returns nothing. The note has to be found by browsing the PHA view's notes
panel (`/pha/notes`) or by grepping the folder.

## 2. Proposed feature

### 2.1 A notes index beside the chunk index

New tables, additive to `SCHEMA` (they are `CREATE TABLE IF NOT EXISTS`, so an
existing archive gains them on the next connect — no migration function needed):

```sql
CREATE TABLE IF NOT EXISTS notes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,   -- file stem; the /pha/note key
    path      TEXT NOT NULL,
    title     TEXT,
    mtime     REAL,
    sha256    TEXT,
    text      TEXT NOT NULL,
    embedding BLOB
);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(text);
```

Deliberately **not** `documents` rows: notes stay out of `documents`/`pages`, so
`pha status`, `pha documents`, `pha export`, `pha bundle` and the review
round-trip are untouched (see §3).

### 2.2 Indexing and staleness

- `pha notes index [--force]` walks `cfg.notes/*.md`, **excludes `README.md`**
  (and any other template), and skips files whose `mtime`/`sha256` is unchanged.
- **Chunking**: one chunk per note for v1 — notes are short and the whole context
  is the point. Per-`##`-heading chunks are the follow-up if snippets prove too
  coarse; that needs a stable chunk identity for reindexing.
- **Same embedding model and prefix** as archive chunks (`embed.prefixed`) so the
  two lists are comparable in hybrid fusion; on an embed failure the note is still
  indexed text-only, exactly like `index_document`.
- **Freshness**: run with `pha scan` (and the watcher), plus `pha reindex`, so a
  hand-edited note is picked up without a manual step. Notes are edited by humans,
  so mtime-based staleness is the whole mechanism.
- Front matter is parsed for `title`; tags/wikilinks can be stored later.

### 2.3 Search integration

- `db.notes_keyword_search` (a mirror of `keyword_search` without the
  pages/documents join) and `db.all_note_embeddings`.
- `search._decorate_note` → a hit carrying `kind: "note"`, `note: <name>`,
  `title`, `path`, `text`, `snippet`, `score`, `source`.
- **The one real refactor**: `_rrf_merge` keys on `chunk_id` today; it must key on
  `(kind, id)` so a note id cannot collide with a chunk id when the three lists
  (keyword-archive, semantic-archive, keyword/semantic-notes) are fused.
- `--source archive|notes|all` (default `all`), threaded through the keyword and
  semantic helpers.
- Ranking caveat: notes share vocabulary with their sources and RRF is rank-based,
  so it does not inherently prefer primary sources. `--source` is the escape
  hatch; a small source weight is the fallback if notes dominate.

### 2.4 Surfaces

- **CLI** `pha search`: note hits print as `[note] <name> — <snippet>` with the
  note path instead of a `page_file`; `--json` carries `kind`.
- **MCP** `pha_search`: passes results through unchanged; an agent reading a note
  hit in full uses the Obsidian MCP (`mcp__obsidian__get_file_contents`) or the
  plugin's `/pha/note` route — not `pha page`.
- **PHA view**: search hits are grouped by `hit.document_id` and open a document
  (`dsh-pha/lib/client.js:356,455`); a `kind === "note"` branch calls the existing
  `openNote(name)` (`client.js:404`). Small change, big payoff.
- A `pha notes` (list/show) command would be a natural companion; out of scope here.

### 2.5 Deferred — other sources

A `source` column would let the same table hold the personal Obsidian vault
(`~/jrc-notes`) later. Not now — see §4.

## 3. Rejected — model notes as `documents` (`kind='note'`)

The tempting shortcut: give each note a `documents` row plus one synthetic
`pages` row (page 1), and search, FTS, embeddings, RRF, `reindex` and the MCP
payloads all work with almost no change. Rejected because every surface that
iterates documents would then see notes:

| surface | effect |
|---|---|
| `pha export` (`cli.py:1018` → `write_document_pages`) | writes `library/…/page-001.md` for every note |
| `pha status`, `pha documents` (MCP) | counts inflate; a fake `notes` "collection" appears |
| `pha bundle` | would ship notes as if they were scanned documents |
| PHA view document list | notes interleave with scans |
| AGENTS.md | "`notes/` is not pipeline output" becomes false |

It also requires a synthetic page per note purely to satisfy
`chunks.page_id NOT NULL`. The shortcut buys a day and costs the model.

## 4. Rejected — index the whole Obsidian vault

Same machinery with `source='vault'`, but the vault is ~7,200 notes outside the
archive, churns constantly, and the Obsidian MCP already searches it (keyword
`search`, plus Smart Connections `semantic_search`). The cleaner division of
labour: **pha search** covers the archive plus the notes written about it;
**Obsidian MCP** covers the vault.

## 5. Decisions to settle

- Default on, or `--source notes` opt-in? (Recommend default on, with `--source`.)
- One chunk per note, or per heading?
- Should note hits rank below archive hits at equal RRF score?
- Freshness: piggyback `pha scan`/watch, or require `pha notes index`?
- Keep `notes/README.md` excluded always (yes), and any other templates?

## 6. Tests to add

- Indexing: rows created/updated/removed on mtime/sha change; `README.md`
  excluded; an unreadable file is skipped, not fatal.
- Keyword, semantic and hybrid each find a note; `--source notes` returns only
  notes and `archive` only pages.
- `_rrf_merge` keeps both kinds — a note id equal to a chunk id must not collapse
  the two hits (regression test for the `(kind, id)` key).
- Result shape: a note hit has `kind`/`note` and no `page_file`.
- Embed endpoint down: the note is still found by keyword search.
- With no notes indexed, existing search results are byte-identical to today.

## 7. Non-goals

- **No vault indexing** (§4).
- **No writes to notes.** Notes stay human/agent-authored files; `pha` only reads
  them (the pipeline never creates or edits note content).
- **No notes in the review round-trip** — `pending_review_files` walks the
  library; notes are not transcriptions.
- **No change to the archive chunk schema** or to existing search results when
  the notes index is empty.

## Reference implementation notes

- `src/personal_historical_archive/db.py` — `chunks` schema (`34-43`),
  `chunks_fts` (`55`), `_dir_clause` (`257`), `all_embeddings` (`523`),
  `build_fts_query` (`544`), `keyword_search` (`556`).
- `src/personal_historical_archive/search.py` — `_decorate` (`23`),
  `keyword_search` (`50`), `semantic_search` (`60`), `_rrf_merge` (`84`),
  `search` (`103`).
- `src/personal_historical_archive/ingest.py` — `chunk_text` (`403`),
  `index_document` (`786`), `reindex_all` (`2080`).
- `src/personal_historical_archive/cli.py` — `cmd_reindex` (`925`),
  `cmd_export` (`1018`).
- `dsh-pha/lib/client.js` — `openNote` (`404`), search-hit grouping (`356`, `455`);
  `dsh-pha/lib/index.js` — the `/pha/notes` and `/pha/note` routes.
- Motivating archive data: the Jesuit archive's `notes/` holds
  `malaca.md`, `coimbra-in-pfister.md`, `afonso-mexia.md`,
  `francisco-estrada.md` and `obsidian-integration.md` — all currently
  unfindable by `pha search`.
