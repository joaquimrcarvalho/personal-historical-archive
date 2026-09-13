# Enhancement request — pha encoders with bundled tools (e.g. write one Markdown file per extracted record)

**Status:** **PARTLY IMPLEMENTED** (2026-09). The artifact half — one Markdown
file per record — shipped as the `markdown-from-records` **artifact filter**
(`encoder.post`, commit `6829dbf`), under the owner ruling that the
bundled-tools runner is absorbed into the stage-filter framework rather than
built as a second mechanism; see [`../FILTERS_PLAN.md`](../FILTERS_PLAN.md).
The **structure prescan** (§3.4) is **not implemented** and still needs a
design decision. Proposal §3.1–3.3 (model-assisted entry detection,
character-aware chunking, raw/edited cross-reference) are untouched.
**Author/date:** archive work session on the `documenta-indica` collection.
**Related files (reference implementation in the archive):**
`dropbox/collections/documenta-indica/encoders/` — `documents.md`,
`apparatus.md`, `*.prompt.md`, `*.langextract.md`, `documents.tools/`,
`apparatus.tools/`, `_tools/markdown-from-records/markdown_from_records.py`;
`dropbox/collections/documenta-indica/prescan/doca_prescan.py` (structure
prescan → per-volume register).

## 1. Motivation and use case

Today a pha **encoder** is a text-model prompt file that turns a document's
(edited) transcription into **structured records** only. The records are
stored in SQLite and mirrored to `library/<slug>/records-<encoder>.json`;
the concatenated input text is stored beside it as
`concatenated-<encoder>.md`.

For the *Documenta Indica* series (J. Wicki, MHSI) the historian wants more
than a register: the volume (a single pha document of ~1011 PDF pages) must
be **segmented into individual historical documents**, and **each segment
written out as one Markdown file** built from the edited pages of that
document's span, with the document's metadata (number, title, author "from",
recipient "to", place of production, date, archive pages) correct and the
page references pointing at the right archive pages. The same applies to the
editorial apparatus (title pages + ToC, bibliography, abbreviations,
Introducito Generalis, closing indexes), each as its own file.

That last step — records → per-segment Markdown artifacts on disk — is not
something the encoder prompt can do today: the encoder stage only parses a
JSON array of records out of the model answer, and nothing materialises the
per-record Markdown. Users therefore reach for scripts outside pha, losing
the "travels with the collection" property of encoder files.

## 2. Proposed feature: tools bundled with encoders

Encoders keep being **prompt files**; in addition they may **bundle tools**,
which pha runs after a successful encode of a document, receiving the
records the encoder produced.

### 2.1 Layout (travelling next to the sources, mirroring how encoders travel)

```
dropbox/collections/<COLX>/encoders/
  documents.md                # encoder prompt + front matter (model pairing lives in pha.yaml)
  documents.prompt.md
  documents.langextract.md
  documents.tools/            # tools bundled with THIS encoder
    markdown-from-records/
      tool.md                 # manifest: description + params schema/defaults
      markdown_from_records.py# executable (python/shell/js, any pha can spawn)
    structure-document/       # optional LLM-backed second tool
      tool.md
  _tools/                     # (optional) tools shared across the collection's encoders
    markdown-from-records/
      markdown_from_records.py
```

Tools are discovered per encoder: `encoders/<name>.tools/<tool>/`, falling
back to `encoders/_tools/<tool>/`, then to pha's own built-in tool dir (for
tools pha ships). `tool.md` is the manifest (description, accepted params and
their defaults); extra files are the tool's payload. Tool dirs are inert for
the current encoder loader (it only reads `encoders/*.md`), so this is fully
backward compatible.

### 2.2 Selecting tools and their parameters

An encoder activates tools in its **front matter** (pha ignores unknown keys
today, so this is additive):

```yaml
tools:
  - name: markdown-from-records
    out_dir: "segments-documents"          # resolved relative to the records dir
    page_marker: "--- page N ---"          # (default)
    footnote_policy: page                  # page | linked (default page)
    normalize_leaders: true
  - name: structure-document               # optional LLM structuring pass
    model: deepseek-v4-flash
```

`pha encode` runs every activated tool, in order, after the records of that
encoder have been written and stored, and reports per-tool success/failure.

### 2.3 Execution contract

pha invokes the tool with a small JSON "context" on stdin (or via a
`--context <file>` flag so interactive use is easy):

```json
{
  "encoder": "documents",
  "document": "/path/to/dropbox/.../DOCUMENTA-INDICA-1540-49.pdf",
  "records_file": "/…/library/<slug>/records-documents.json",
  "concatenated_file": "/…/library/<slug>/concatenated-documents.md",
  "library_dir": "/…/library/<slug>",
  "pages_dir_edited": "/…/edited-latin-to-english@deepseek-v4-flash",
  "pages_dir_raw": "/…/transcription-gemma-4-e4b-it@gemma-4-e4b-it",
  "params": { "out_dir": "segments-documents", ... }
}
```

The tool writes artifacts (default: under `library/<slug>/<out_dir>/`, i.e.
next to the records file), prints human-readable progress to stdout and
exits non-zero on failure (pha keeps the records; the tool run is reported).
Re-encode triggers a re-run: the existing invalidation logic that watches the
encoder files and page edits should also touch the tool manifest files
(`documents.tools/**` mtimes).

Security note: encoder tool payloads are arbitrary local code provided by the
archive owner (like palaeographer/editor prompts), so no sandbox is implied;
document this.

### 2.4 Built-in tool: `markdown-from-records`

Reference implementation: `markdown_from_records.py` (stdlib Python). For
each record of a kind (from `records-<encoder>.json`) it:

1. sorts records by `page_start`;
2. takes each record's page span (`page_start` .. `page_end`, where
   `page_end` defaults to the page before the next record's `page_start`, or
   the last input page);
3. reads the edited page files of the span (stripping page YAML front matter
   and any editor "## Notes" block);
4. **splits shared pages**: when a new record's header begins mid-page
   (record attribute `line_start`, else a header-line search by number/title
   words), the top of the page stays with the previous record and the header
   onward starts the new one — so a document starting mid-page is not merged
   into the previous one and neither document loses text;
5. emits ONE Markdown file per record (`doc-<number>-<slug>.md`,
   `<prefix>-<slug>.md` …) with a small YAML front matter (archive pages and
   the record's metadata) and the verbatim edited text separated by
   `--- page N ---` markers, footnotes kept at page level as printed.

The optional LLM tool `structure-document` then adds the intra-document
headings (editorial-comment sections I–VI, `textus`/parts enumeration) per
the Documenta Indica layout rules (see the bundled `structure-document/tool.md`).

## 3. Related improvements this use case surfaced (open for discussion)

1. **Model-assisted entry detection** — `detect_entry_pages` only implements
   the regex fast path today; the docstring already anticipates "a cheap
   model scan per chunk driven by the collection's detection rules". The
   Documenta Indica headers vary enough (missing bare-number lines,
   mid-page starts, translated ALL-CAPS, bracketed reconstructions) that a
   regex over the whole volume leaves recall at ~60%. A model-driven
   detection (or an instruction to chunked extraction "emit every entry that
   starts in your pages") would be more robust. The doca encoders therefore
   deliberately set **no** `candidate_pattern` and enumerate documents
   inside chunked extraction instead.
2. **Character-aware chunking** — `batch_pages`-based chunking assumes
   uniform page sizes; the ToC pages of volume I contain huge leader-dot
   runs. Chunking by a character budget (with `max_input_chars` already on
   the Encoder dataclass) would prevent one oversized page from blowing a
   model window.
3. **Original-vs-edited cross-reference** — the `documents` encoder stores
   metadata from the *edited* text; the tool could enrich each Markdown's
   header with the faithful printed Latin/Portuguese header read from the
   raw transcription pages of the same numbers, since both page sets exist
   in the library.
4. **Collection structure prescan → per-document layout injection.** A
   Documenta Indica volume's layout (front-matter sections, documents area,
   appendices, closing index, printed→archive offset) is *discovered*, not
   hand-maintained: a small collection-local script
   (`prescan/doca_prescan.py`, reference implementation, validated against
   volume I) reads the volume's page text and emits a register JSON:

   ```json
   {"pages_total": 1011, "printed_offset": 140,
    "sections": [{"kind": "toc", "page_start": 7, "page_end": 16}, ...],
    "documents_area": {"main": [141, 887],
                       "appendices": [{"label": "APPENDIX I",
                                       "page_start": 888, "page_end": 895}, ...],
                       "closing_index": [955, 1011]},
    "doc_headers": [{"page": 148, "number": "4", "caps": "..."}, ...]}
   ```

   For the framework, pha should resolve this per DOCUMENT at encode time
   (each volume of the series is a separate pha document), not per rules
   file: an encoder declares
   `structure: prescan` (a collection-local script) in its front matter, pha
   runs it lazily on the document's pages, caches the register at
   `library/<slug>/structure-<slug>.json` (mtime-invalidated like the
   records), derives the encoder's `pages:` filter from the register
   (`pages: "@documents"`, `pages: "@sections"` …), and appends the layout
   block of the register to the composed prompt. Until that lands, the same
   register drives everything with one command
   (`doca_prescan.py --write-encoder-pages`, which updates the `pages:` line
   of `encoders/{documents,apparatus}.md` for the volume currently being
   processed) — so no per-volume hand numbers are ever authored.

## 4. Out of scope / non-goals (this session)

- No changes to the pha repository were made; the encoder files above are
  pure collection data + prompts that load cleanly in current pha
  (`pha encoder collections/documenta-indica/DOCUMENTA-INDICA-1540-49.pdf`
  resolves both `apparatus` and `documents`).
- The JSON record format of the encoder stage is unchanged.
