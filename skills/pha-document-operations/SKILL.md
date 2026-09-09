---
name: pha-document-operations
description: Re-run the pha pipeline on one existing document or collection in personal-historical-archive (pha) — re-scan (re-extract), re-edit, or re-encode it — without inspecting the pha source code. Use when the user asks to "rescan", "re-process", "re-run", "re-edit", "re-encode", "scan this document/collection again", or to change how an already-ingested document is processed. Teaches how to find the document's dropbox path, how to force a re-run (pha scan skips unchanged documents unless --reprocess), and how to verify the result.
---

# pha Document Operations

The pipeline is: dropbox → palaeographer (per-page transcription) → optional
editor → optional encoder → index. Once a document is ingested, **`pha scan`
will not touch it again** unless something changed — an already-transcribed
document whose source is unchanged is reported as `unchanged` and skipped.
So "re-scan this document" is *not* a no-op request you can satisfy with a
plain `pha scan`; you must **target the document** and **force** the pass.

This skill tells you how to find the target, force the re-run, and verify it,
in three capability profiles.

## Determine your profile first

- **Profile C — CLI + files** (you are on the archive machine, or can read
  the `library/` folder). This is the common case: an agent working from the
  archive directory. Commands are `pha …`.
- **Profile D — dsh-pha Harness plugin** (agent on a machine with pha + the
  bundled `dsh-pha` plugin). Tools are `pha_status`, `pha_documents`,
  `pha_document`, `pha_page`, `pha_search`, `pha_archive` and the background
  job tools `pha_job_start` / `pha_job_status` / `pha_job_kill`.
- **Profile M — FastMCP server** (remote agent connected over MCP). Tools are
  `pha_list_documents`, `pha_get_document`, `pha_get_page`,
  `pha_collection_config`, `pha_extraction_status`, `pha_scan_now`,
  `pha_upload`. **Note the limitation:** `pha_scan_now()` scans the *whole*
  dropbox — it accepts no `path`/`reprocess`, so it cannot target one
  document. For a targeted re-run, use Profile C on the archive machine, or
  the dsh-pha `pha_job_start` tool (Profile D).

## The core rule: stale ≠ broken

`pha scan`/`pha edit` decide to (re)process a document by **staleness**, not
by what you want:

- A document re-EXTRACTS when its source changed (mtime/hash), when the
  resolved palaeographer (rules or model) differs from the one recorded, or
  when its prompt/editor changed — **or** when you pass `--reprocess`.
- A document re-EDITS when the editor's rules file or model changed, or the
  paired editor id differs, **or** with `--reprocess`.

So to force a re-run of a document that is otherwise unchanged, pass
`--reprocess`. Without it, expect `unchanged` / `skipped`.

## Workflow

### 1. Find the target's dropbox path

`pha scan --path` and `pha edit --path` take a **dropbox-relative subpath**,
not a document id. A document is one of:

- a collection directory — `collections/COLX`
- a directory-of-images document — `documents/ms123` (each image = one page)
- a single file — `documents/myfile.pdf`

Find it with `pha status` (the collection tree) or, in Profile D, `pha_status`
/ `pha_documents`. In Profile M, `pha_list_documents` returns `collection`
per document so you can build the relative path.

### 2. Force the re-run you were asked for

**Re-extract the transcription (re-scan):**

- C: `pha scan --path collections/COLX --reprocess`
- D: `pha_job_start({ action: 'scan', path: 'collections/COLX', reprocess: true })` then poll `pha_job_status`
- M: cannot target one document — see the limitation note above.

**Re-run the editor pass only:**

- C: `pha edit --path collections/COLX --reprocess`
- C: one page of one document: `pha edit --path collections/COLX --page 3`
- D: `pha_job_start({ action: 'edit', path: 'collections/COLX', reprocess: true })`

**Re-run the encoder:**

- C: `pha encode` (re-runs encoders on documents that have them);
  `pha encode --reprocess` to re-encode everything matched.
- D: `pha_job_start({ action: 'encode', reprocess: true })`

**Dry-run a configuration before a full pass** (never touches the DB/library/
renders — writes to `<archive>/.pha-test/`):

- C: `pha test collections/COLX --pages 3` (add `--random` / `--seed`)

If you only need to find out what a document currently resolves to (which
palaeographer / editor / prompt / encoders), inspect, don't re-run:

- C: `pha palaeographer collections/COLX`, `pha editor collections/COLX`,
  `pha prompts collections/COLX`, `pha encoder [file]`
- D/M: `pha_collection_config('collections/COLX')` (one object with the
  resolved `palaeographer`, `editor`, `prompt` and their `source`).

### 3. Respect the single-model lock

`pha scan`, `pha edit` and `pha test` share **one lock**: a machine can hold a
single local model at a time (LM Studio loads one model; loading two
swaps/page-out and fills the disk). Never start one while another is running —
check `pha status` / `pha_extraction_status` first, and if a pass is running,
wait (or, in Profile D, poll `pha_job_status`).

### 4. Verify the result

- `pha status` (D: `pha_status`, M: `pha_extraction_status`) confirms the
  document is now re-processed.
- Read a page's actual text to confirm the new reading:
  - C: `pha page <doc-substring-or-id> <page>` (add `--edited` for the
    edited variant)
  - D: `pha_page(document_id, page_no)`; M: `pha_get_page(document_id, page_no)`
- If the document was a human-corrected page (`reviewed: true` in the library
  file), note that `pha scan` never re-reads it — change the palaeographer or
  use `pha reindex` / `pha edit` as appropriate.

## Quick reference

| Need | C (CLI + files) | D (dsh-pha plugin) | M (FastMCP) |
| --- | --- | --- | --- |
| Find target path | `pha status` | `pha_status` / `pha_documents` | `pha_list_documents` |
| Re-scan one doc | `pha scan --path <p> --reprocess` | `pha_job_start scan path=… reprocess=true` | not supported (whole-dropbox only) |
| Re-edit one doc | `pha edit --path <p> --reprocess` | `pha_job_start edit path=… reprocess=true` | not supported |
| Re-edit one page | `pha edit --path <p> --page 3` | `pha_job_start edit path=… page=3` | not supported |
| Re-encode | `pha encode --reprocess` | `pha_job_start encode reprocess=true` | not supported |
| Dry-run config | `pha test <path> --pages 3` | — | — |
| Inspect config | `pha editor/palaeographer/prompts <file>` | `pha_collection_config(<path>)` | `pha_collection_config(<path>)` |
| Verify | `pha status`, `pha page` | `pha_status`, `pha_page` | `pha_extraction_status`, `pha_get_page` |
| Get a page's text | `pha page <doc> <page> [--edited]` | `pha_page(id, page)` | `pha_get_page(id, page)` |

## When not to use this skill

- Uploading a brand-new document (no re-run needed) — use `pha upload` /
  `pha_upload`, then `pha scan`.
- Moving collections between archives — use `pha bundle` / `pha unbundle`
  (no re-scan on the target).
- Simply searching or reading — that is `pha-search-context`.
- Work purely on config (which editor/encoder a collection should use) without
  re-running any pass — the AGENTS.md conventions cover that.

## Checklist

- [ ] Profile determined (C / D / M).
- [ ] Target dropbox-relative path found via `pha status` (or the equivalent).
- [ ] Re-run **forced** with `--reprocess` (or `reprocess: true`) — a plain
      `pha scan` skips an unchanged document as `unchanged`.
- [ ] Single-model lock checked: no other `pha scan`/`edit`/`test` running.
- [ ] Result verified with `pha status` and `pha page` (or the MCP/dsh
      equivalents).
- [ ] If asked to change how a doc is processed (palaeographer/editor/
      encoder), config inspected first, *then* the matching pass re-run.
