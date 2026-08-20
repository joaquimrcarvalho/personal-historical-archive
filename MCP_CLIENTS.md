# Connecting agents to the archive (MCP)

The archive exposes search, ingestion, upload, and configuration through an
MCP server. An MCP-capable agent (Claude Desktop, Cursor, Claude Code, the
`mcp` CLI / MCP Inspector, …) can connect to it and, with plain language,
query a manuscript corpus, upload documents, and configure how documents are
processed (palaeographer / editor / encoder).

## Tools available to the agent

| tool | purpose |
| --- | --- |
| `pha_search(query, mode, limit, collection)` | ranked passages — hybrid (keyword+semantic, default), keyword, semantic; optional collection filter |
| `pha_get_document(document_id, max_chars)` | metadata + full extracted per-page text |
| `pha_list_documents(status, limit, collection)` | browse the archive |
| `pha_upload(kind, name, content_b64, replace, merge)` | put a file into the dropbox (send base64 bytes) |
| `pha_palaeographers()` | list configured vision (palaeographer) models and the active default |
| `pha_editors()` | list configured text (editor) models |
| `pha_encoders(document_relpath)` | list the encoder files that apply to a document |
| `pha_collection_config(document_relpath)` | resolved palaeographer / editor / prompt for a document or collection |
| `pha_collection_status(collection)` | per-collection status report: documents with recorded vs resolved pal/editor/encoders, progress (pages done/total), and pipeline stage (transcribed / edited / encoded) |
| `pha_scan_now()` | ingest files that were dropped / uploaded into the dropbox |
| `pha_extraction_status()` | ingestion summary |

## Requirements

- Python + the venv with this package installed: `.venv/bin/pha` (see README →
  New machine setup). Paths below assume the project lives at
  `/path/to/personal-historical-archive` — use your real path on each machine.
- For **search** (hybrid/semantic) an embedding model must be served — e.g. LM
  Studio `text-embedding-nomic-embed-text-v1.5` on `http://127.0.0.1:1234`.
  Keyword search works without it.
- For **pha_scan_now** the configured palaeographer (vision model) must be
  served — e.g. LM Studio `qwen/qwen3-vl-8b`.
- Set `PHA_HOME` so the server finds `config.yaml` regardless of where the
  client launches it: `PHA_HOME=/path/to/personal-historical-archive`.
- Set the dropbox path for this machine first:
  `pha set dropbox /path/to/documents` (stored in the gitignored `.env`).

---

## Local connection (stdio — one client on the same machine)

### Claude Desktop (macOS)
Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "personal-historical-archive": {
      "command": "/path/to/personal-historical-archive/.venv/bin/python",
      "args": ["-m", "personal_historical_archive", "mcp"],
      "env": { "PHA_HOME": "/path/to/personal-historical-archive" }
    }
  }
}
```
Restart Claude Desktop — the `pha_*` tools appear under MCP tools.

### Cursor
Project `.cursor/mcp.json` (or Settings → MCP → Add):

```json
{
  "mcpServers": {
    "personal-historical-archive": {
      "command": "/path/to/personal-historical-archive/.venv/bin/python",
      "args": ["-m", "personal_historical_archive", "mcp"],
      "env": { "PHA_HOME": "/path/to/personal-historical-archive" }
    }
  }
}
```

### Claude Code (CLI)
```bash
claude mcp add personal-historical-archive \
  --env PHA_HOME=/path/to/personal-historical-archive -- \
  /path/to/personal-historical-archive/.venv/bin/python \
  -m personal_historical_archive mcp
```

### MCP Inspector (debug/try manually)
```bash
npx @modelcontextprotocol/inspector \
  /path/to/personal-historical-archive/.venv/bin/python \
  -m personal_historical_archive mcp
```

---

## Machine-to-machine (remote): connect an agent on machine A to the archive on machine B

The archive (documents, dropbox, models) lives on **machine B**; you want an
agent on **machine A** to search it, add documents, and configure processing.

### 1. On machine B — start the MCP server over the network

Bind to a reachable interface (not just `127.0.0.1`) and choose a port:

```bash
cd /path/to/personal-historical-archive
pha set dropbox /path/to/documents            # one-time; B's own dropbox
pha mcp --transport sse --host 0.0.0.0 --port 8000
```

Notes / security:
- `pha mcp` uses this machine's `config.yaml`, `.env`, and models. The remote
  agent drives **B's** dropbox and B's LM Studio — nothing runs on A.
- There is **no authentication**. Bind to a private/LAN address or run behind
  a VPN/SSH tunnel. On macOS, bind to your LAN IP (e.g. `192.168.1.20`) rather
  than `0.0.0.0` to limit exposure; or use an SSH tunnel:

```bash
# machine A: SSH tunnel to B, forwards local 8000 -> B's 8000
ssh -N -L 8000:127.0.0.1:8000 user@<machineB>
```
Then point the client at `http://127.0.0.1:8000/sse`.

### 2. On machine A — point your agent's MCP client at B

- **Claude Desktop / Cursor**: MCP does not natively expose remote SSE servers
  in every client. Use the **MCP Inspector** or a terminal, or a browser-based
  MCP client that supports SSE URLs. Point it at:
  ```
  http://<machineB>:8000/sse
  ```
- **Claude Code (CLI)** supports remote MCP via a URL:
  ```bash
  claude mcp add personal-historical-archive remote \
    --transport sse --url http://<machineB>:8000/sse
  ```
- If the client only accepts stdio `command`, put a tiny wrapper on A that
  proxies to B, or use the `mcp` CLI:
  ```bash
  npx @modelcontextprotocol/inspector \
    --transport sse --url http://<machineB>:8000/sse
  ```

### 3. Machine A has no local models / dropbox

Because the server runs on B, the agent on A needs **only an MCP connection** —
no LM Studio, no dropbox, no `pha` install on A. All model calls happen on B.

---

## Uploading documents from another machine

An agent on A pushes a document/collection/selection-file into **B's** dropbox
with `pha_upload`. Files travel as base64 bytes; only single files are
transferred per call (for a folder, upload its files one by one, or zip).

- **A single document file** (PDF/image):
  ```
  pha_upload(kind="document", name="letters-1894.pdf",
             content_b64="<base64>")
  → B:/dropbox/documents/letters-1894.pdf
  ```
- **A document folder of page images**: upload each image under
  `documents/<name>/...`:
  ```
  pha_upload(kind="document", name="documents/ms123/p01.jpg", content_b64="…")
  ```
- **A collection file**: place it under the collection:
  ```
  pha_upload(kind="collection", name="COLX/source1.pdf", content_b64="…")
  ```
- Same `replace`/`merge` policy as `pha upload`; it refuses if the target
  exists unless `replace=True` / `merge=True`.

After uploading, run `pha_scan_now()` to ingest the new files.

---

## Setting palaeographers, editors and encoders from another machine

Palaeographers / editors / encoders are **one file per model** in the
`palaeographers/`, `editors/`, `encoders/` directories (id = file stem, YAML
front matter = config, body = prompt), and per-document/collection **selection
files** live next to the documents in the dropbox (a plain text file named
`palaeographer` / `editor`, or an `encoders/<name>.md` folder). A remote agent
configures them on B with the config helpers + `pha_upload`.

### Inspect current configuration (read)

```python
pha_palaeographers()                  # list models + active default
pha_editors()                         # list editor models
pha_encoders("collections/COLX/doc.pdf")  # encoder files that apply
pha_collection_config("collections/COLX")# resolved pal/editor/prompt for a collection
```

### Select a palaeographer / editor for a collection

Create (or update) a selection file in B's dropbox with `pha_upload`:

```python
# for the collection collections/COLX:
pha_upload(kind="document", name="collections/COLX/palaeographer",
           content_b64=base64("portuguese-secretary"))
pha_upload(kind="document", name="collections/COLX/editor",
           content_b64=base64("generic"))
```
After that, `pha_collection_config("collections/COLX")` shows the new
resolved palaeographer/editor. Run `pha_scan_now()` to (re)extract with the
new settings.

### Add a new palaeographer / editor / encoder definition

The model-definition files (`palaeographers/*.md`, `editors/*.md`) live at the
**project root**, outside the dropbox, so `pha_upload` cannot write them
directly (it is scoped to the dropbox for safety). Two ways a remote agent
adds one:

- **Ask on the archive machine** (the one running the server) to run
  `pha editor` / `pha palaeographer` setup, or to copy a `*-sample.md` into
  the folder — e.g. the operator runs: `cp palaeographers/_sample.md
  palaeographers/my-hand.md` and edits it.
- Or stage the definition **inside** the dropbox (e.g. under a temporary
  folder) with `pha_upload`, then have an operator move it into
  `palaeographers/` on the archive machine. Keep API keys as `${ENV}`
  placeholders (`pha key --set VAR` stores the secret on the archive
  machine) — never commit a resolved key.

Uploading a definition **file** as a dropbox **payload** and then placing it is
the safe pattern; avoid paths with `..` that escape the dropbox.

**Encoder** definition files, by contrast, live *inside* the dropbox (one per
structure type in `collections/COLX/encoders/`), so a remote agent can upload
them directly:

```python
pha_upload(kind="collection", name="collections/COLX/encoders/letters.md",
           content_b64=base64(encoder_front_matter + "\n" + prompt))
```
`pha_encoders("collections/COLX/doc.pdf")` then lists it.

---

## Suggested agent workflow

1. `pha_extraction_status()` — what is ingested.
2. `pha_list_documents(status="processing")` / browse.
3. `pha_upload(...)` + `pha_scan_now()` — add a document/collection.
4. `pha_palaeographers()` / `pha_editors()` / `pha_encoders()` /
   `pha_collection_config(...)` — see or set how a collection is processed.
5. `pha_search("…", collection="…")` — find relevant passages.
6. `pha_get_document(id)` — read the full extracted text of the best hit.

---

## Notes

- **One local model at a time.** On machine B LM Studio holds a single model;
  `pha scan` and `pha edit` share a lock and never run concurrently. The
  remote agent should not start `pha_scan_now` while a `pha edit` job is
  running on B.
- **Remote models don't compete** with B's local LM Studio (e.g. a
  MiniMax editor runs over its own API).
- **Dropping a raw file via `pha_upload` does not require writing to the dropbox
  from A** — the bytes are written on B, so A needs no access to B's disk.
