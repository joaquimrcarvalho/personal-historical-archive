# personal-historical-archive (pha)

> **For human historians:** this README is the technical reference. If you
> just want to get your documents into the archive and ask your AI assistant
> questions about them, head to
> **[HISTORIANS_README.md](HISTORIANS_README.md)** — a step-by-step guide
> written for historians, no terminal experience needed. Everything here also
> works from there, through your agent.

A local research archive for **historical documents — manuscripts, old books,
maps, and more — as PDFs and images**: drop files into a folder, a **vision
model (a *palaeographer*)** reads each page and transcribes it, an optional
**text model (an *editor*)** can then modernize or translate the transcriptions
and refine the named entities, an optional **encoder** turns the transcriptions
into structured records (e.g. letter from/to/date/place), everything is indexed,
and any LLM can search the corpus through an **MCP server**.

The key design choice: text extraction is done with a **vision model and an
optional per-file custom prompt** (not with plain OCR). For historical
documents — where low-quality embedded OCR text is common — a model that
"reads" the page image with instructions tailored to the document's structure
gives far better results, and you control the prompts per document. Then an
**editor** (a different, text-only model, possibly remote) transforms the
faithful transcription — modernizing spelling, translating, and correcting the
named entities that get indexed. Finally an **encoder** (another text model,
possibly remote) reads the whole concatenated document text and returns
structured records grounded to the page each one starts on.

```
 dropbox/documents/*.pdf, *.png, ...        individual documents
 dropbox/collections/COLX/*, ...            collections of sources
     │  (optional prompts: <stem>.prompt.md next to a file,
     │   or prompt.md inside a directory → applies to everything under it)
     ▼
 ┌─────────────────────── watcher / `pha scan` ───────────────────────┐
 │  1. render pages → JPEG (200 dpi, long edge ≤ 1800 px)             │
 │  2. per page: palaeographer (vision) transcribes the page          │
 │     with the resolved prompt (local or remote model)               │
 │  3. optional editor (text model): modernize/translate, refine      │
 │     named entities, keep footnotes separate — `pha edit`           │
 │  4. optional encoder (text model): structured records from the     │
 │     concatenated document text, grounded to start pages — `pha encode`
 │  5. per page text stored in SQLite + per-page files in library/    │
 │  6. chunk (2000 chars) → embeddings → FTS5 + vector index          │
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

- macOS / Linux / Windows, Python ≥ 3.11 (managed with [uv](https://docs.astral.sh/uv/))
- Any **OpenAI-compatible model server** (tested with **LM Studio**, default):
  - a **vision model** (palaeographer) for reading pages — e.g. `qwen/qwen3-vl-8b`
  - an **embedding model** for semantic search — e.g.
    `text-embedding-nomic-embed-text-v1.5` (LM Studio catalog) or
    `nomic-embed-text` (Ollama)
  - a **text model** (editor, optional) for modernizing/translating — e.g.
    `amalia-9b-0626-dpo` (LM Studio) or any remote OpenAI-compatible model
- Runs locally by default; **remote OpenAI-compatible models** (e.g. MiniMax)
  are supported for palaeographers and editors via an API key
  (`pha key --set` stores it in the OS secret store).

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

A **palaeographer** is a named vision model that transcribes the documents.
Each palaeographer is **one file** in the `palaeographers/` directory (the
file name, without extension, is the id): YAML front matter holds the model
settings, the body is the base prompt:

```markdown
# palaeographers/qwen-local.md
---
description: qwen3-vl-8b via LM Studio (local, default)
base_url: http://127.0.0.1:1234/v1        # local or remote OpenAI-compatible endpoint
model: qwen/qwen3-vl-8b
api_key: ""                                # remote APIs: "${MY_API_KEY}" (env expansion)
temperature: 0.1
max_tokens: 4096
timeout_s: 900
---

You are a palaeographer specialised in Western European manuscripts …
```

**To add a palaeographer**: duplicate `palaeographers/_sample.md`, rename
(the name becomes the id), edit the settings, replace the body with your
expertise, save. Invalid files are skipped with a warning — a typo never
breaks the load. `pha palaeographer` lists the configured palaeographers.

- `vision.palaeographer` in `config.yaml` selects the active one; `pha scan
  --palaeographer ID` overrides it for one run; the MCP `pha_scan_now` uses
  the configured default.
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
- The body of the file is the palaeographer's **base prompt**, and it is the
  **format authority**: it defines the output structure — `## Transcription`,
  then `## Notes` with `### Named entities` (one bullet per entity) and
  `### Content summary`.
- The base prompt is always sent **first**, before the document/collection
  prompt. The document/collection prompt only adds specific aspects (fields
  to prioritise, transcription style such as modernizing spelling) and cannot
  change the output structure.
- `api_key` supports `${ENV_VAR}` and `${ENV_VAR:-default}` expansion, so keys
  never need to be committed. Resolution order: real environment → a
  gitignored `.env` file → the **macOS Keychain** (service `pha`) → empty.
  Store a key safely with `pha key --set VARNAME` (reads the value from stdin,
  writes to the OS secret store, or `.env` if it is unavailable);
  `pha key` shows where each referenced variable resolves. Local servers
  (LM Studio, Ollama) need no key.
- Every document records which palaeographer extracted it (shown by
  `pha status`, in search results, and in the library markdown header).
  Editing a palaeographer file re-extracts the documents that use it.

## Editors (transforming transcriptions)

A **palaeographer reads** the page; an **editor transforms** the result. An
editor is a **different model** (own endpoint/model/api key — local or remote)
that applies an editing prompt to each page's transcription text: modernize
spelling, translate, normalize names, … The faithful transcription is never
destroyed — the edited version is a derivative.

Each editor is **one file** in the `editors/` directory (same convention as
palaeographers: front matter = settings, body = editing prompt). To add one,
duplicate `editors/_sample.md`, rename, edit, save:

```markdown
# editors/modern-portuguese.md
---
description: Convert to modern Portuguese orthography, expand abbreviations
base_url: http://127.0.0.1:1234/v1
model: amalia-9b-0626-dpo            # a text LLM, not the vision model
temperature: 0.0
max_tokens: 4096
timeout_s: 300
---

You are a scholarly editor of historical Portuguese texts. Convert the
transcription to MODERN Portuguese orthography …
```

Select an editor per document/collection with an **`editor` file** (same
nearest-wins chain and `.txt`/`.md` variants as palaeographers):

```
dropbox/collections/letters-from-missons/editor   →  "modern-portuguese"
```

- `pha edit` runs the editor pass over every document that has an editor and
  re-indexes; `pha editor [file]` shows resolution.
- Edited output lands in `library/.../edited-<editor>/page-NNN.md`, next to the
  faithful `transcription-<pal>/` pages.
- **Search indexes both variants** (`raw` and `edited`) — results are tagged
  with the variant, so you can find passages whether you search the faithful
  or the modernized text. A page re-edits when its transcription or the
  editor's file changes.

## Encoders (structured records from the text)

An **encoder** is a text model (own endpoint/model/api key — local or remote)
that turns a document's transcription into **structured records**, e.g. the
metadata of each letter in a correspondence volume. Unlike the palaeographer
(one page at a time), the encoder reads the document as **one concatenated
text** with `--- page N ---` markers, so a record whose parts span several
pages (a letter header on one page, its body on the next) is seen whole:

- when the concatenated text fits the model's context window (`max_input_chars`)
  it is sent in **one call**; larger documents are **chunked with overlap**
  (`batch_pages`, `overlap_pages`) and duplicate records are collapsed;
- the prompt asks the model to cite the **page each record starts on**
  (LangExtract-style source grounding), stored per record and shown in the
  records file.

Each encoder is **one file** in the `encoders/` directory (same convention as
palaeographers/editors; add `batch_pages`, `max_input_chars`, `overlap_pages`,
`extraction_passes` in the front matter). Select one per document/collection
with an **`encoder` file**; stage-qualified prompts live in **`encoder.prompt.md`**
files (plain `prompt.md` stays the palaeographer prompt):

```
dropbox/collections/letters-from-missons/encoder            →  "letters"
dropbox/collections/letters-from-missons/encoder.prompt.md  →  letter-detection rules
```

- `pha encode` runs the encoder pass; `pha encoder [file]` shows resolution.
- The encoder prefers the **edited** text (per page) when an editor is
  configured, falling back to the raw transcription.
- Records are stored in SQLite and written to
  `library/<dir>/<slug>/records-<encoder>.json`, with the exact input
  concatenated text beside it as `concatenated-<encoder>.md` for inspection.
- Re-encodes when the encoder file, its `encoder.prompt.md`, or the source
  transcription (raw or edited) changes since the records were created.
- **Long-document techniques** (inspired by
  [LangExtract](https://github.com/google/langextract)):
  - the whole document is one **concatenated text** (records spanning pages
    are seen whole); single call when it fits `max_input_chars`, otherwise
    overlapping chunks;
  - **page grounding**: every record carries the page it starts on;
  - `extraction_passes > 1` re-runs extraction independently and merges
    **first-pass-wins** (recall boost against stochastic misses);
  - prompts demand **exact text** from the input (no paraphrasing) so records
    stay verifiable against the source.

## CLI reference

```
pha scan [--watch] [--debounce N] [--prompt FILE] [--palaeographer ID] [--reprocess]
pha search QUERY [--mode hybrid|keyword|semantic] [--collection COLX] [--limit N] [--json]
pha status
pha export
pha reindex
pha edit [--reprocess]
pha rm ID|NAME
pha prompts [file]
pha palaeographer [file]
pha editor [file]
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
