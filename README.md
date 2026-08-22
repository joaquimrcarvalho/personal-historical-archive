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
- **Model endpoints & contexts are per-model config**: every palaeographer /
  editor / encoder file can declare
  - `api_style: openai` (default — `/chat/completions`, works with LM Studio,
    Ollama, vLLM, OpenAI, OpenRouter, ...) **or** `anthropic`
    (`/anthropic/v1/messages`; needed for MiniMax, whose OpenAI-compatible
    endpoint silently drops `image_url` blocks — the image goes as a
    plain-text data URI);
  - `max_vision_px` + `vision_jpeg_quality` (palaeographer): in the
    anthropic form the base64 travels as TEXT (~2 chars/token, a full render
    ~= 190k tokens), so MiniMax keeps full 1800px resolution at q55 (~150k
    tokens) instead of downsizing — see `palaeographers/minimax-vl.md`. Local
    openai-style images are sent as rendered, untouched;
  - `context_tokens` (encoder) — the model's input window; the
    single-pass/chunked decision is derived from it (~4 chars/token,
    override with `max_input_chars`). MiniMax M2.5 = 200000; a local 7B
    might be 32768. See the samples.

## Quickstart

```bash
# 1. environment
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
export PATH="$PWD/.venv/bin:$PATH"     # puts `pha` on PATH for this shell
pha --help                             # sanity check

# 2. start LM Studio, load qwen/qwen3-vl-8b (+ an embedding model),
#    and start the local server (default port 1234). Check config.yaml.

# 3. drop manuscripts into dropbox/ and extract
pha scan                            # one-shot (whole dropbox)
pha scan --path collections/COLX    # only a specific collection/subpath
pha scan --watch                    # keep watching the dropbox

# 4. search
pha search "doação de Évora ao mosteiro"
pha search "alfange" --mode keyword
pha search "monastery donation charter" --mode semantic

# 5. MCP server (stdio)
pha mcp
```

### New machine setup

To set this project up on a fresh machine (or another Mac), from scratch:

```bash
# 1. clone (defaults to the `main` branch — the base branch)
git clone https://github.com/joaquimrcarvalho/personal-historical-archive.git
cd personal-historical-archive

# 2. environment (uv-managed Python)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .

# The `pha` console script lives INSIDE the venv — it is NOT on the system
# PATH. In any shell / for any agent, call it via the venv (full path, so it
# works even in a fresh or non-interactive shell):
PHA=.venv/bin/pha            # set once; then use  $PHA ...  or  $PHA
# Alternatively:  export PATH="$PWD/.venv/bin:$PATH"   then plain  pha  works.
# Note: `alias pha=...` only lasts for the one interactive shell it's set in.

# 3. point at your ARCHIVE directory (a single self-contained data root:
#    documents + model definitions + generated output). The friendly way
#    stores the path in a gitignored .env (not committed):
$PHA set archive-dir /path/to/your/archive
#    (or interactively: run `pha set archive-dir` and type the path when asked)
#    Equivalent: set the PHA_ARCHIVE_DIR env var (takes precedence), or put
#    paths.archive_dir in config.yaml (not recommended — machine-specific).
#    Defaults to the project dir, so a fresh clone works with zero config:
#    palaeographers/, editors/ and encoders/ are seeded with a default that
#    uses qwen/qwen3-vl-8b on LM Studio.

# 4. start LM Studio, load qwen/qwen3-vl-8b (or your palaeo model), and the
#    embedding model; start the local server on port 1234
$PHA key --set MINIMAX_API_KEY   # only if you use remote MiniMax editors

# 5. extract + search
$PHA scan
$PHA search "your query"
```

Model notes for a new machine:

- **Only ONE local model at a time.** LM Studio holds a single model in
  memory; loading two local models (e.g. qwen vision + amalia editor)
  concurrently causes swap/page-out that fills the disk and hangs the server.
  `pha scan` and `pha edit` share a lock, so they refuse to run at the same
  time. A palaeographer and editor that use the SAME local model (e.g. qwen
  for both on Pfister) keep one slot loaded. **Quit LM Studio when you are not
  ingesting** — its model page-out is the thing that eats disk space.
- **Picking a palaeographer per collection**: put a `palaeographer` file (the
  model id) next to a document/collection;
  `dropbox/collections/COLX/palaeographer -> minimax-vl` for MiniMax, or
  `-> qwen-local-19-20c-books` for a local 19th–20th c. printed-book model.
- **Renders**: default `render_dpi: 72` is fast and adequate for printed
  books (qwen reads fine at 72 dpi). Bump it in `config.yaml` if your source
  needs more detail.

### Uploading documents & collections

Copy a document (a file, or a folder of page images) or a whole collection
directory into the dropbox's conventional location:

```bash
pha upload document /path/to/myfile.pdf        # -> dropbox/documents/myfile.pdf
pha upload document /path/to/pages_folder      # image-dir document -> dropbox/documents/<name>/
pha upload collection /path/to/COLX_dir        # -> dropbox/collections/<name>/
pha upload document /path/f.pdf --name gone.md # custom destination name
```

By default it REFUSES if the destination already exists; pass `--replace` to
overwrite it, or `--merge` to copy into an existing destination (updating the
files).

**From another machine (remote dropbox):** expose the archive through the
MCP server on the machine that owns the dropbox (`pha mcp`), and use the
`pha_upload` MCP tool. It takes the file's bytes as base64 plus a destination
name, so a client on machine A can push a document into the dropbox that
lives on machine B. Uploading then `pha_scan_now` ingests it. See
[`MCP_CLIENTS.md`](MCP_CLIENTS.md) for the full **machine-to-machine** setup —
connecting an agent to another machine's server, uploading documents, and
setting palaeographers/editors/encoders remotely.

### MCP client setup

Point any MCP-capable client at the stdio server. **For remote / networked
connections (another machine, SSE, security, SSH tunnels) see
[`MCP_CLIENTS.md`](MCP_CLIENTS.md).** Example for **Claude Desktop**
(`~/Library/Application Support/Claude/claude_desktop_config.json`):

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
- **Palaeographer prompts must not contain text-modification rules.** The
  palaeographer's job is faithful transcription — reproduce exactly what is
  visible, keep the original spelling, mark `[illegible]`/`[?]`. Any
  transformation of the text (abbreviation expansion, modernizing spelling,
  translating, normalising names) belongs in the **editor** prompt, which
  runs on the transcribed text as a separate pass. Keeping modification
  rules out of the palaeographer avoids double-expansion and keeps the
  transcription layer a faithful record of the source.
- **Palaeographer Notes are READING NOTES only** — Language, Script, and
  difficult words. The palaeographer does NOT produce named-entity lists or
  content summaries; those belong to the editor (entities/notes) and encoder
  (structured records) passes.
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
- **The null editor**: the special editor id `null` (or `passthrough`) keeps
  the transcription verbatim — `pha edit` copies each page's text as the
  "edited" version without a model call. Select it with an `editor` file
  containing `null` so a document without a real editor still flows through
  the same pipeline (`edited-null/` folder, both-variant indexing, explicit
  provenance). If no editor is configured at all, the encoder and indexer
  fall back to the raw transcription per page.
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

Encoders live **next to their sources** so they travel with the documents:
one file per *structure type* in the document's `encoders/` folder (model
config + framing in the front matter/body; add `batch_pages`, `context_tokens`,
`extraction_passes`, and `pages` — the page range this encoder handles — in
the front matter):

```
dropbox/collections/pfister-notices/encoders/
  table.md            →  chronological table (front matter, pages 1-15)
  table.prompt.md     →  table detection rules
  table.langextract.md→  table schema + examples
  biographies.md      →  the person notices (whole document)
  biographies.prompt.md
  biographies.langextract.md
```

A document with several structural sections defines one encoder per section;
`pha encode` runs them **in page order** (`pages:` front matter, whole-doc
encoders last). `pages:` uses **PDF page numbers** — the position in the PDF,
NOT the number printed on the page (e.g. Pfister's chronological table is
printed i–xv but occupies PDF pages 1-15).

- `pha encode` runs every encoder found next to a document; `pha encoder
  [file]` shows resolution.
- **`pha encoder --new` creates an encoder interactively** — a wizard that asks
  a non-technical user plain-language questions (what the material is, a sample
  passage, which things matter and what to record about each), uses the chosen
  model to propose classes/attributes and draft the example, VERIFIES every
  extracted value appears verbatim in the sample (grounding), and stores the
  encoder files in the collection's `encoders/` folder — without the user
  knowing the file format. The same interview is available as a chat prompt in
  `prompts/encoder-helper.md` for use in any chat model.
- The encoder prefers the **edited** text (per page) when an editor is
  configured, falling back to the raw transcription.
- **Multi-class records**: the model outputs LangExtract-flat JSON items —
  each item is `{"<class>": "<exact text>", "<class>_attributes": {…}}`, one
  per class (e.g. `person` AND `letter` in the same array). Each item becomes
  one record with `kind` = class, so a run produces e.g. a persons index plus
  the letter list, with letters referencing people via `from`/`to`. The
  records file groups by kind.
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
    stay verifiable against the source;
  - **few-shot `## Examples`** in the encoder file teach the model the exact
    classes/attributes/shapes with grounded Q/A pairs.

## CLI reference

```
pha scan [--watch] [--debounce N] [--prompt FILE] [--palaeographer ID] [--path COLLECTION] [--reprocess]
pha search QUERY [--mode hybrid|keyword|semantic] [--collection COLX] [--limit N] [--json]
pha status
pha export
pha reindex
pha review [--doc N]      # import human corrections from library .md files into the DB
pha edit [--reprocess]
pha rm ID|NAME
pha prompts [file]
pha palaeographer [file]
pha editor [file]
pha encoder [file] [--new]
pha encode [--reprocess]
pha set archive-dir [PATH]   # set the archive data root (stored in gitignored .env)
pha archive-dir              # alias for `pha set archive-dir`
pha set dropbox [PATH]       # DEPRECATED: set only the documents folder
pha upload document PATH [--name N] [--replace] [--merge]
pha upload collection PATH [--name N] [--replace] [--merge]
pha mcp [--transport stdio|sse] [--port 8000]
```

## Configuration (`config.yaml`)

- `paths.archive_dir` — the single self-contained **data root** (see Data
  layout). Default `.` (the project dir). Set it per machine via
  `pha set archive-dir` or the `PHA_ARCHIVE_DIR` env var — never commit a
  machine-specific path.
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

Everything the archive owns lives under ONE `archive_dir` (default: the
project dir), so the archive is self-contained and copyable as a unit:

```
archive_dir/
  dropbox/documents/        ← individual documents (+ .prompt.md sidecars)
  dropbox/collections/COLX/ ← collections of sources (+ prompt.md)
  palaeographers/           ← model definitions (default.md seeded with qwen)
  editors/                  ← model definitions (default.md seeded with qwen)
  encoders/                 ← default encoder (default.md) + _sample.md template
  library/                  ← generated per-page markdown (mirrors dropbox)
    collections/letters-from-missons/
      <doc>_<YYYY-MM-DD>/            ← readable folder per document version
        transcription-qwen-local/   ← one folder per palaeographer
          502V.md                   ← one file per page, named after the source
                                    ←   scan (dir-of-images) or page-NNN.md (PDF)
  renders/<sha>/            ← cached page JPEGs fed to the VLM
  archive.db                ← documents / pages / chunks + FTS5 + embeddings
```

The PROJECT dir (code, versioned) holds only `src/`, `config.yaml`,
`prompts/default_prompt.md`, `prompts/encoder-helper.md`, and the
`palaeographers/_sample.md`, `editors/_sample.md`, `encoders/_sample.md`
templates. A fresh archive is seeded with `default.md` palaeographer/editor/
encoder pointing at `qwen/qwen3-vl-8b` (LM Studio), so it works with zero
configuration; refine by adding sidecar definitions next to documents or
collections.

Per-page files are written **incrementally** while a document is being
extracted, so output is visible immediately (no need to wait for completion).
`pha export` regenerates all per-page files from the database without
re-extracting. Running a different palaeographer over the same document adds
a sibling `transcription-<palaeographer>/` folder for side-by-side
comparison.

**Library folder naming**: each document version lives in a folder named
`<stem>_<YYYY-MM-DD>` (e.g. `1567-Coimbra_2026-08-22`) — the readable
creation date of that version. When a document's content changes, pha creates
a NEW document row (with a new date), so versions never collide and the old
folder stays on disk. Pages of a directory-of-images document are named after
their source scan (`502V.md`); PDF pages use `page-NNN.md`.

### Reviewing and correcting transcriptions

The library files are meant to be READ and CORRECTED by a historian:

1. Edit a page file: `library/.../transcription-<pal>/502V.md` or
   `library/.../edited-<editor>/502V.md` (keep the YAML front matter; change
   the body).
2. `pha status` shows how many pages have un-imported corrections
   (timestamp-based: a file whose mtime is newer than when pha last wrote it
   is pending).
3. `pha review [--doc N]` imports those corrections back into the database
   and stamps the pages **reviewed** — later `pha scan` / `pha edit` passes
   never overwrite a reviewed page, even with `--reprocess`.
4. `pha reindex` so search uses the corrected text.

Reviewed pages show `reviewed: true` in their front matter.

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
