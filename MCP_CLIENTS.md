# Connecting agents to the archive (MCP)

The archive exposes its search and ingestion through an MCP server. Any
MCP-capable agent (Claude Desktop, Cursor, Claude Code, Cursor-compatible
IDEs, `mcp` CLI / MCP Inspector, …) can connect to it and query your
manuscript corpus with plain language.

## What the agent gets

| tool | purpose |
| --- | --- |
| `search(query, mode, limit, collection)` | ranked passages — hybrid (keyword+semantic, default), keyword, semantic; optional collection filter |
| `get_document(document_id, max_chars)` | metadata + full extracted per-page text |
| `list_documents(status, limit, collection)` | browse the archive |
| `scan_now()` | ingest files dropped into the dropbox |
| `extraction_status()` | ingestion summary |

## Requirements before connecting

- The MCP server needs **Python + the venv** at
  `/Users/jrc/develop/personal-historical-archive/.venv/bin/python`.
- For **search** (hybrid/semantic) the embedding model must be served —
  currently LM Studio with `text-embedding-nomic-embed-text-v1.5` on
  `http://127.0.0.1:1234`. Keyword search works without it.
- For **scan_now / extraction** the vision model must be served — currently
  LM Studio with `qwen/qwen3-vl-8b`. The active palaeographer decides which
  endpoint is used (see `config.yaml`).
- Set `MA_HOME` to the project root so the server finds `config.yaml`
  regardless of the working directory the client launches it from:
  `MA_HOME=/Users/jrc/develop/personal-historical-archive`

## Claude Desktop (macOS)

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "manuscript-archive": {
      "command": "/Users/jrc/develop/personal-historical-archive/.venv/bin/python",
      "args": ["-m", "manuscript_archive", "mcp"],
      "env": { "MA_HOME": "/Users/jrc/develop/personal-historical-archive" }
    }
  }
}
```

Restart Claude Desktop. The archive tools then appear under the MCP tools;
you can ask e.g. "search the manuscript archive for Francis Xavier's letters".

## Cursor

Add to the project's `.cursor/mcp.json` (or via Settings → MCP → Add):

```json
{
  "mcpServers": {
    "manuscript-archive": {
      "command": "/Users/jrc/develop/personal-historical-archive/.venv/bin/python",
      "args": ["-m", "manuscript_archive", "mcp"],
      "env": { "MA_HOME": "/Users/jrc/develop/personal-historical-archive" }
    }
  }
}
```

## Claude Code (CLI)

```bash
claude mcp add manuscript-archive \
  --env MA_HOME=/Users/jrc/develop/personal-historical-archive -- \
  /Users/jrc/develop/personal-historical-archive/.venv/bin/python \
  -m manuscript_archive mcp
```

## Any MCP client / debugging

The MCP Inspector (`npx @modelcontextprotocol/inspector`) is the easiest way
to try the tools manually:

```bash
npx @modelcontextprotocol/inspector \
  /Users/jrc/develop/personal-historical-archive/.venv/bin/python \
  -m manuscript_archive mcp
```

## Network (SSE) transport — other machines / containers

By default the server speaks stdio (safe, one client). If you need a network
endpoint, run it as SSE on localhost:

```bash
ma mcp --transport sse --host 127.0.0.1 --port 8000
```

then point an MCP client at `http://127.0.0.1:8000/sse`. Keep it bound to
`127.0.0.1` — there is no authentication.

## Suggested agent workflow

1. `extraction_status()` — see what is ingested.
2. `scan_now()` — after the user drops new files into `dropbox/`.
3. `search("…", collection="collections/letters-from-missons")` — find
   relevant passages.
4. `get_document(id)` — read the full extracted text of the best hit.
5. `list_documents(status="processing")` — check on an ongoing extraction.
