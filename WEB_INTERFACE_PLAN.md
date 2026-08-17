# Web interface plan (pha web)

A local web UI for **personal-historical-archive (pha)** so a user can manage
the system without the CLI. Runs on the same machine, bound to `127.0.0.1`.

## Goals (from the user)

1. Configure new **palaeographers** (endpoint, api key, model, temperature,
   base prompt) and test them.
2. **Check and edit prompts**: default, document sidecars, collection, and
   palaeographer prompts.
3. **Job status**: per document, the extraction status **by each palaeographer**.
4. **Global settings**: everything in `config.yaml` that is user-changeable.

## Constraints

- Default binding **localhost only (127.0.0.1), no auth**. Optional **same-subnet**
  binding (`--host 0.0.0.0` or config `web.bind`) — when bound beyond localhost,
  access requires a **simple shared token** (auto-generated on first run, stored
  in `data/` and shown once; users enter it in the browser). No full account
  system.
- **No Node.js on the machine** → no React/Vite build chain. Frontend is
  **Jinja2 + HTMX** (vendored single JS file) — build-free.
- Reuse the existing modules (`config`, `db`, `ingest`, `extract`, `model_client`)
  and the SQLite DB (WAL — safe for concurrent readers with the watcher).
- Config and prompts are **files**; editing them has consequences (prompt edits
  trigger re-extraction via the mtime/staleness checks). The UI must make that
  explicit and reuse the same mechanisms.
- **The web app owns the watcher lifecycle**: it starts/stops/restarts
  `pha scan --watch` (as a managed subprocess) and shows its status. A
  "restart watcher" button applies config changes — no shell knowledge needed
  for non-technical users.

## Stack

- **Backend**: FastAPI + uvicorn (`fastapi` + `jinja2` are new deps; uvicorn
  already present). Entry point: `pha web [--host 127.0.0.1 --port 8080]`.
- **Frontend**: Jinja2 templates + HTMX (vendored single JS file) — no build
  step. Fallback: a single `index.html` vanilla-JS SPA calling the JSON API.
- **API**: JSON under `/api/*`; the same pages render the UI.

## Data model addition

To show per-document status *per palaeographer*, add an `extraction_runs` table
(DB migration, like `dir_path`/`palaeographer`):

```sql
CREATE TABLE extraction_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  palaeographer TEXT NOT NULL,
  status TEXT NOT NULL,            -- running | done | error | waiting
  pages_done INTEGER DEFAULT 0,
  pages_total INTEGER,
  started_at REAL,
  finished_at REAL,
  error TEXT,
  UNIQUE (document_id, palaeographer)   -- one active run per (doc, pal)
);
```

`ingest_file` upserts the run at entry, bumps `pages_done` per completed page
(alongside the existing heartbeat), and finalizes on completion/abort. This is
small and reuses the existing page loop. The dashboard then shows, per
document, a row per palaeographer (transcriptions also live side-by-side in
`library/.../transcription-<pal>/`).

## API surface (draft)

| method | path | purpose |
| --- | --- | --- |
| GET | `/api/status` | summary + documents (status, pages, palaeographer, prompt, updated) + their `extraction_runs` |
| POST | `/api/scan` | trigger a scan (lock-aware `scan_once`) |
| POST | `/api/documents/{id}/reprocess` | re-extract one document |
| DELETE | `/api/documents/{id}` | remove from index (wraps `pha rm`) |
| GET/PUT | `/api/palaeographers` | list / save map (validated; keys masked) |
| POST | `/api/palaeographers/{id}/test` | connectivity test: GET `/v1/models` + tiny chat |
| GET | `/api/prompts?type=default\|palaeographer\|collection\|document` | list prompt files |
| GET/PUT | `/api/prompts/{kind}/{path}` | read / write a prompt file (path-encoded, sandboxed to `prompts/` and `dropbox/`) |
| GET | `/api/prompts/resolved/{document_id}` | preview the composed prompt for a document |
| GET/PUT | `/api/config` | read / write `config.yaml` (schema-validated) |
| POST | `/api/config/reload` | re-read config (new processes pick it up) |
| GET | `/api/watcher/status` | is `pha scan --watch` running (managed subprocess)? |
| POST | `/api/watcher/start` | start the watcher (idempotent, lock-aware) |
| POST | `/api/watcher/stop` | stop the watcher |
| POST | `/api/watcher/restart` | restart (applies config/settings changes) |

## Screens

1. **Dashboard** — summary cards (docs by status, pages, chunks, active
   palaeographer); documents table with expandable per-palaeographer runs
   (status, pages done/total, started/finished, error); actions: Scan now,
   Reprocess, Remove.
2. **Palaeographers** — cards for each entry; add/edit form (id, description,
   base_url, api_key — masked, model, temperature, max_tokens, timeout_s,
   prompt file or inline text); **Test connection**; set as `vision.palaeographer`;
   delete. Editing triggers re-extraction of affected docs — show a warning.
3. **Prompts** — grouped list (default, palaeographers/, collection `prompt.md`,
   document sidecars `*.prompt.md`); editor (textarea, markdown) with save;
   before saving, show "this re-extracts N documents"; a **resolved preview**
   showing base prompt + `---` + document prompt for a chosen document.
4. **Settings** — form generated from `config.yaml` (extraction, search,
   embeddings, vision.palaeographer; paths read-only); validation (types,
   ranges) on save; after saving, an explicit **"Restart watcher to apply"**
   action (the app manages the watcher, so no shell needed).

## Semantics to get right

- **Prompt/config edits reuse the existing triggers**: saving a prompt file is
  exactly what the staleness check watches, so documents re-extract on the next
  scan — the UI must confirm this before saving.
- **Secrets**: `api_key` values are stored as written (ideally `${ENV_VAR}`
  references); the UI shows only a masked form and never logs the value. The
  web-access token (used when binding beyond localhost) is generated once,
  stored in `data/`, shown to the admin on first run.
- **Scan safety**: all triggers go through `scan_once` (pid lock), so the UI
  never collides with the watcher. The web app starts the watcher as a
  subprocess it supervises; restarts are graceful (the extraction is resumable).
- **File sandbox**: prompt editing paths are validated to live under
  `prompts/` and `dropbox/` (no arbitrary file writes).

## Implementation phases

1. **Backend core**: `extraction_runs` migration + recording; FastAPI app with
   status/scan/reprocess/remove; config read/write with validation; prompt
   list/read/write with sandbox + resolved preview; **watcher supervision**
   (start/stop/restart `pha scan --watch` as a managed subprocess); optional
   token auth when binding beyond localhost. CLI `pha web`.
2. **Frontend**: dashboard (status + per-palaeographer runs + watcher status),
   then settings, palaeographers, prompts — Jinja2 + HTMX.
3. **Polish**: test-connection, secret masking, first-run token display,
   error surfacing, README/MCP_CLIENTS updates, `pha web` docs.

## Testing

- API: curl/httpx against the running app (no browser needed).
- UI: browser-automation checks on 127.0.0.1:8080.
- Concurrency: verify the UI's "scan now" and watcher restart respect the lock;
  verify prompt-edit → re-extraction behaves as announced.
- Subnet: with `--host 0.0.0.0`, verify the token gate works from another
  machine on the same network.

## Decisions (confirmed by the user)

1. Frontend: **Jinja2 + HTMX**, no build step.
2. Watcher: **the web app manages it** — start/stop/restart from the UI,
   because explaining shell resets to non-technical users is not viable.
3. Binding/auth: **localhost by default (no auth)**; optional **same-subnet**
   binding behind a simple shared token.
