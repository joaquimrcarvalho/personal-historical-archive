# AGENTS.md — conventions for agents working in this repo

## Commits

- **Agent-made commits MUST end their message with a model trailer**, naming
  the agent's model, e.g.:
  `Model: deepseek-v4-flash (DeepSeek Harness)`
  (use the current session's model name; append it as the last line of the
  commit message).
- Commits made by the human do NOT carry the trailer — it is manual only
  (there is no hook).
- Verify with `git log -1 --format='%B'`.

## Project essentials

- **personal-historical-archive (pha)** — local archive of historical
  documents; CLI `pha`, Python package `personal_historical_archive`
  (`src/personal_historical_archive/`). The working branch is `rename-pha`
  (to be merged into `main`).
- **Palaeographers (vision models) and editors (text models) are one file
  each** in the top-level `palaeographers/` and `editors/` directories:
  YAML front matter = model config (endpoint, model, api key, temperature),
  body = the prompt. To add one: duplicate `_sample.md`, rename, edit, save.
  The file stem is the id.
- **Staleness by mtime**: editing a palaeographer / editor / prompt file
  triggers re-extraction / re-editing of affected documents on the next scan.
  A document also re-extracts when the resolved palaeographer differs from the
  one recorded on it.
- **Prompt layering**: the palaeographer base prompt is the format authority
  and goes first; document/collection prompts add aspects only
  (e.g. modernizing spelling) and cannot change the output structure.
- **Extraction and the editor pass use different models**; LM Studio serves
  one model at a time, so do not run `pha scan` and `pha edit` concurrently.
- Pipeline: dropbox → palaeographer per-page transcription → optional editor
  transform → SQLite (FTS5 + embeddings, indexing both raw and edited
  variants) → hybrid search + FastMCP (`pha_*` tools).
- Full usage: README.md; planned web UI: WEB_INTERFACE_PLAN.md.
