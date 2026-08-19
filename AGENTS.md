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
- **Palaeographers (vision models), editors (text models) and encoders (text
  models) are one file each** in the top-level `palaeographers/`, `editors/`
  and `encoders/` directories: YAML front matter = model config (endpoint,
  model, api key, temperature, `api_style: openai|anthropic`, and for
  encoders `context_tokens` — the model's input window that drives the
  single-pass/chunked decision), body = the prompt. To add one: duplicate
  `_sample.md`, rename, edit, save. The file stem is the id.
- **Staleness by mtime**: editing a palaeographer / editor / encoder / prompt
  file triggers re-extraction / re-editing / re-encoding of affected documents
  on the next scan/run. A document also re-extracts when the resolved
  palaeographer differs from the one recorded on it.
- **Prompt layering**: the palaeographer base prompt is the format authority
  and goes first; document/collection prompts add aspects only
  (e.g. modernizing spelling) and cannot change the output structure. The
  encoder stage composes three layers in order: encoder base prompt
  (model config + generic framing in the collection's `encoders/<name>.md`)
  → `encoders/<name>.prompt.md` (detection rules) →
  `encoders/<name>.langextract.md` (schema + few-shot examples).
- **Encoders live next to their sources**: dropbox/collections/COLX/encoders/
  has one file per structure type (table.md, biographies.md, letters.md);
  `pages:` in the front matter scopes an encoder to a page range and
  `pha encode` runs a document's encoders in page order (whole-doc last).
  The top-level `encoders/` dir holds only the sample template.
- **The null editor**: editor id `null`/`passthrough` copies the
  transcription verbatim as the "edited" text (no model call), so documents
  without a real editor still get `edited-null/` output, both-variant
  indexing and explicit provenance. With no editor at all, the encoder and
  indexer fall back to the raw transcription per page.
- **Encoders output LangExtract-flat JSON** (`{"<class>": "<exact text>",
  "<class>_attributes": {...}}`, one item per class, kinds stored per record);
  `pha encoder --new` is the non-technical wizard that creates encoder files
  (chat variant: `prompts/encoder-helper.md`). Never write a resolved API key
  into a generated encoder file — keep `${ENV}` placeholders.
- **Extraction, the editor pass and the encoder use different models**; LM
  Studio serves one model at a time, so do not run `pha scan` and `pha edit`
  concurrently. Remote models (e.g. MiniMax) don't compete with LM Studio.
- Pipeline: dropbox → palaeographer per-page transcription → optional editor
  transform → optional encoder (concatenated whole-document text, page-grounded
  records) → SQLite (FTS5 + embeddings, indexing both raw and edited
  variants) → hybrid search + FastMCP (`pha_*` tools).
- Full usage: README.md; planned web UI: WEB_INTERFACE_PLAN.md.
