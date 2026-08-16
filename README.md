# personal-historical-archive (pha)

A local research archive for **historical documents — manuscripts, old books,
maps, and more — as PDFs and images**: drop files into a folder, a vision
model (VLM) transcribes each page, everything is indexed, and any LLM can
search the corpus through an **MCP server**.

The key design choice: text extraction is done with a **vision model and an
optional per-file custom prompt** (not with plain OCR). For historical
manuscripts — where low-quality embedded OCR text is common — a VLM that
"reads" the page image with instructions tailored to the document's structure
gives far better results, and you control the prompt per document.

```
 dropbox/documents/*.pdf, *.png, ...        individual documents
 dropbox/collections/COLX/*, ...            collections of sources
     │  (optional prompts: <stem>.prompt.md next to a file,
     │   or prompt.md inside a directory → applies to everything under it)
     ▼
 ┌─────────────────────── watcher / `pha scan` ───────────────────────┐
 │  1. render pages → JPEG (200 dpi, long edge ≤ 1800 px)             │
 │  2. per page: VLM transcription with resolved prompt (LM Studio)   │
 │  3. per page text stored in SQLite + Markdown copy in library/     │
 │  4. chunk (2000 chars) → embeddings → FTS5 + vector index          │
 └────────────────────────────────────────────────────────────────────┘
     │
     ▼
 SQLite (data/archive.db) ──► `pha search` (hybrid keyword+semantic,
     ▲                           optional --collection filter)
     └────────────────────────── FastMCP server: pha_search / pha_get_document /
                                 pha_list_documents / pha_scan_now / pha_extraction_status
```

---

## Requirements

- macOS / Linux, Python ≥ 3.11 (managed with [uv](https://docs.astral.sh/uv/))
- Any **OpenAI-compatible model server** (tested with **LM Studio**, default):
  - a **vision model** for extraction — e.g. `qwen/qwen3-vl-8b`
  - an **embedding model** for semantic search — e.g.
    `text-embedding-nomic-embed-text-v1.5` (LM Studio catalog) or
    `nomic-embed-text` (Ollama)
- Everything runs locally; no cloud calls.

## Quickstart

```bash
# 1. environment
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
alias pha=".venv/bin/python -m personal_historical_archive"

# 2. start LM Studio, load qwen/qwen3-vl-8b (+ an embedding model),
#    and start the local server (default port 1234). Check config.yaml.

# 3. drop manuscripts into dropbox/ and extract
pha scan                 # one-shot
pha scan --watch         # keep watching the dropbox

# 4. search
pha search "doação de Évora ao mosteiro"
pha search "alfange" --mode keyword
pha search "monastery donation charter" --mode semantic

# 5. MCP server (stdio)
pha mcp
```

### MCP client setup

Point any MCP-capable client at the stdio server. Example for
**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "personal-historical-archive": {
      "command": "/Users/jrc/develop/personal-historical-archive/.venv/bin/python",
      "args": ["-m", "personal_historical_archive", "mcp"],
      "env": { "PHA_HOME": "/Users/jrc/develop/personal-historical-archive" }
    }
  }
}
```

`PHA_HOME` makes the server find `config.yaml` regardless of the working
directory. An SSE variant is available: `pha mcp --transport sse --port 8000`.

**Full agent setup instructions** (Claude Desktop, Cursor, Claude Code,
MCP Inspector, SSE, and a suggested agent workflow) live in
[MCP_CLIENTS.md](MCP_CLIENTS.md).

Exposed tools:

| tool | purpose |
| --- | --- |
| `search(query, mode, limit, collection)` | ranked passages — hybrid (keyword+semantic), keyword, semantic; optionally restricted to a collection |
| `get_document(document_id, max_chars)` | metadata + full extracted per-page text |
| `pha_list_documents(status, limit, collection)` | browse the archive, optionally by collection |
| `pha_scan_now()` | ingest newly dropped files |
| `pha_extraction_status()` | ingestion summary |

## Dropbox layout: documents and collections

The dropbox is scanned recursively, so any subdirectory structure works.
The suggested convention:

```
dropbox/
  documents/                  individual documents
    myfile.pdf
    myfile.pdf.prompt.md      (optional per-file prompt)
    ms123/                    a document made of page-scan images:
      p01.jpg, p02.jpg, ...     each image is scanned as one page
      prompt.md                 (optional: applies to scanning EACH image)
  collections/
    COLX/                     one directory per collection of sources
      source1.pdf
      source2/                (a document made of images inside a collection)
      prompt.md               (optional: prompt for the whole collection)
```

A **document** is either a single file (PDF or image) or a directory of
images (auto-detected: images and no subdirectories/PDFs — each image becomes
a page, exactly like the pages of a PDF). Such a directory is indexed as one
document, its images share one prompt, and the library output is a single
markdown file. Set `extraction.dir_documents: false` to treat every directory
as a plain grouping of separate files instead.

Every document is tagged with its relative directory (`documents`,
`documents/ms123`, `collections/COLX`, …), which shows up in search results
and can be used to filter: `pha search "..." --collection COLX` or via the MCP
`collection` parameter. `pha status` lists documents grouped by collection.

## Custom extraction prompts

Each document can carry its own extraction instructions. Prompt resolution
order:

1. `--prompt <file>` CLI/MCP flag
2. **file sidecar** — `<stem>.prompt.md` / `<stem>.pdf.prompt.md` next to the
   document (works in any subdirectory)
3. **directory chain** — for each directory from the document's location up to
   the dropbox root (nearest wins): `<dir>/prompt.md`, then
   `<dir>/<dirname>.prompt.md`, then `<parent>/<dirname>.prompt.md` (a sidecar
   next to the directory). This is how:
   - a collection prompt in `dropbox/collections/COLX/prompt.md` applies to
     every file under it,
   - a document-directory prompt (`documents/ms123/prompt.md` or
     `documents/ms123.prompt.md`) applies to **scanning each image** in that
     folder.
4. `prompts/<stem>.prompt.md`
5. `prompts/default_prompt.md` (the shipped scholarly-transcription prompt)
6. built-in default

A sidecar lets the VLM return **structured data** for a specific document
type. Example (`dropbox/sample_charter.prompt.md` asks for JSON with
`document_type`, `date`, `parties`, `places`, `summary`, `transcription`,
`archival_marks`; the sample collection uses a Markdown table instead).
Editing a prompt file (sidecar, collection `prompt.md`, or the default)
**automatically re-extracts** the affected documents on the next scan
(prompt mtime is compared against the document's last update). Use
`pha scan --reprocess` to force a full re-extraction.

`pha prompts [file]` shows how a prompt resolves.

## Palaeographers (vision models)

A **palaeographer** is a named vision model that transcribes the documents,
configured in `config.yaml`:

```yaml
palaeographers:
  qwen-local:                     # id used by vision.palaeographer
    description: qwen3-vl-8b via LM Studio (local, default)
    base_url: http://127.0.0.1:1234/v1   # local or remote OpenAI-compatible endpoint
    model: qwen/qwen3-vl-8b
    api_key: ""                          # remote APIs: "${MY_API_KEY}" (env expansion)
    temperature: 0.1
    max_tokens: 4096
    timeout_s: 900
    prompt_file: prompts/palaeographers/qwen-local.md   # this palaeographer's base prompt
```

- `vision.palaeographer` selects the active one; `pha scan --palaeographer ID`
  overrides it for one run; the MCP `pha_scan_now` uses the configured default.
- **Per-document / per-collection selection**: place a file named
  `palaeographer` (with an optional `.txt` or `.md` extension so it is easy
  to edit on a desktop) containing the palaeographer id — either next to a
  document (`<stem>.palaeographer`, `<stem>.palaeographer.md`, …) or in a
  directory/collection (`dropbox/collections/COLX/palaeographer.txt`). It is
  resolved with the same nearest-wins chain as prompts (file sidecar, then
  walking up to the dropbox root), and overrides the config default for
  everything under it:

  ```
  dropbox/collections/COLX/palaeographer.md   → "portuguese-secretary"
  ```

  Changing a `palaeographer` file re-extracts the affected document(s) with
  the new palaeographer; output goes to a sibling
  `transcription-<palaeographer>/` folder. `pha palaeographer [file]` shows
  how a document resolves.
- Each palaeographer can carry its own **base prompt** (its expertise/working
  rules). It is always prepended **before** the document/collection prompt and
  is the **format authority** (## Transcription, ## Notes with
  ### Named entities — one bullet per entity — and ### Content summary). The
  document/collection prompt that follows only adds specific aspects (fields
  to prioritise, transcription style) and cannot change the structure:

  ```
  [palaeographer base prompt — format authority]
  ---
  [document / collection prompt — adds aspects only]
  ```
- `api_key` supports `${ENV_VAR}` and `${ENV_VAR:-default}` expansion, so keys
  never need to be committed. Local servers (LM Studio, Ollama) need no key.
- Every document records which palaeographer extracted it (shown by
  `pha status`, in search results, and in the library markdown header).

## CLI reference

```
pha scan [--watch] [--debounce N] [--prompt FILE] [--palaeographer ID] [--reprocess]
pha search QUERY [--mode hybrid|keyword|semantic] [--collection COLX] [--limit N] [--json]
pha status
pha export
pha reindex
pha rm ID|NAME
pha prompts [file]
pha mcp [--transport stdio|sse] [--port 8000]
```

## Configuration (`config.yaml`)

- `vision.*` — model server + vision model for extraction
- `embeddings.*` — model server + embedding model
- `extraction.*` — render dpi, image cap, chunk size/overlap, concurrency
- `search.*` — default mode and result count

**Switching backends** (e.g. Ollama instead of LM Studio) is a config change:

```yaml
vision:
  base_url: http://127.0.0.1:11434/v1
  model: qwen2.5vl:7b
embeddings:
  base_url: http://127.0.0.1:11434/v1
  model: nomic-embed-text
```

After switching the embedding model run `pha reindex`.

## Data layout

```
dropbox/documents/        ← individual documents (+ .prompt.md sidecars)
dropbox/collections/COLX/ ← collections of sources (+ prompt.md for the collection)
library/
  collections/letters-from-missons/   ← mirrors the dropbox directory structure
    <doc-slug>/                       ← one folder per document
      transcription-qwen-local/       ← one folder per palaeographer
        page-001.md                   ← one file per page, YAML front matter repeated
        page-002.md                     (source filename, collection, page, palaeographer,
        …                               prompt, status) + the transcription body
data/renders/<sha>/       ← cached page JPEGs fed to the VLM
data/archive.db           ← documents / pages / chunks + FTS5 + embeddings
prompts/default_prompt.md ← shipped default prompt
```

Per-page files are written **incrementally** while a document is being
extracted, so output is visible immediately (no need to wait for completion).
`pha export` regenerates all per-page files from the database without
re-extracting. Running a different palaeographer over the same document adds
a sibling `transcription-<palaeographer>/` folder for side-by-side
comparison.

## Notes on quality & performance

- Extraction is page-by-page; on an M2/24 GB, `qwen3-vl-8b` takes roughly
  10–60 s per page depending on density. `extraction.concurrency` can be
  raised to 2 on 24 GB, but 1 is the safe default.
- Resumable: pages already extracted are skipped on rescan; failed pages are
  retried. Prompt changes or `--reprocess` force re-extraction.
- The VLM output is stored verbatim per page; the default prompt asks for
  `[illegible]` markers and a structured `## Notes` block — tune
  `prompts/default_prompt.md` to your manuscript tradition.
- Semantic search needs an embedding model served at `embeddings.base_url`;
  if it is unreachable, hybrid search degrades to keyword-only (a note is
  returned).

## Alternatives considered (why not X)

| option | fit | gap for this workflow |
| --- | --- | --- |
| [Paperless-ngx](https://github.com/paperless-ngx/paperless-ngx) + AI forks ([paperless-gpt](https://github.com/icereed/paperless-gpt), [Paperless-AIssist](https://nyxtron.github.io/paperless-aissist/)) | turnkey DMS: consumption folder, OCR, tagging, web UI, REST | Docker/Postgres stack; vision extraction targets *metadata* (type, date, sender), not arbitrary per-file content prompts; heavier to run and extend |
| [Docling](https://pypi.org/project/docling/) (IBM) | excellent PDF→structured-text parsing, VLM table/OCR support | a parsing library, not a drop-folder→index→MCP system; no per-file prompt workflow |
| [MinerU](https://github.com/opendatalab/MinerU) + mineru-mcp | strong layout/OCR parsing, MCP wrapper | optimized for printed documents/layout; per-file custom prompts not the model |
| [olmOCR / local-llm-pdf-ocr](https://github.com/ahnafnafee/local-llm-pdf-ocr) | PDF→text with VLMs, web UI | OCR-oriented; no index/search/MCP; no per-file prompts |
| [local-rag](https://github.com/aihaysteve/local-rag) / [mcp-rag](https://github.com/JMRussas/mcp-rag) | sqlite/embeddings RAG + MCP, close to the search half | no VLM document ingestion or per-file prompts |
| [local-mmcp](https://github.com/rorojiao/local-mmcp) | multimodal MCP toolkit for Apple Silicon (oMLX VLM + MinerU + ASR) | general-purpose toolkit, not a manuscript workflow |

This project keeps the parts you actually asked for — drop folder + watcher,
VLM extraction with per-file prompts, hybrid index, MCP search — as a small,
fully local, model-server-agnostic pipeline (~1 kLOC).
