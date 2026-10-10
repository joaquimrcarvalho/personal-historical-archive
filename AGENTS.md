# AGENTS.md — conventions for agents working in this repo

This file is the **always-loaded contract**: the rules that must not be got
wrong, and a map of where everything else is written down. It is deliberately
short — the detail lives in topic documents that are read *when the task needs
them*.

**Read the pointer that matches the task before you act:**

| If the task is… | Read |
|---|---|
| anything that changes data | this file, first |
| running/altering the pipeline, per-page re-runs, human review | `OPERATING.md` |
| how pha is built, config layers, schema, library layout, engines | `ARCHITECTURE.md` |
| two machines, bundles, hand-over | `MULTI_COMPUTER.md` |
| commands, configuration, quickstart | `README.md` |
| explaining pha to the archive owner | `PIPELINE.md` |
| a release or version bump | `RELEASING.md` |
| filters / bibliography / web UI designs | `FILTERS_PLAN.md`, `BIBLIOGRAPHY_PLAN.md`, `WEB_INTERFACE_PLAN.md` |

## Commits

- **Agent-made commits MUST end their message with a model trailer**, naming
  the agent's model, e.g.:
  `Model: deepseek-v4-flash (DeepSeek Harness)`
  (use the current session's model name; append it as the last line of the
  commit message).
- Commits made by the human do NOT carry the trailer — it is manual only
  (there is no hook).
- **Version bumps are manual and user-driven, but the release unit is the
  whole repository.** The version lives in three places that must stay in
  sync: `pyproject.toml`, `src/personal_historical_archive/__init__.py`, and
  `dsh-pha/package.json`. Bump all three together with
  `python scripts/bump_release.py patch` (or `minor` / `major`). Any change to
  the Python tool or the shipped `dsh-pha` payload must bump the release:
  `pha update` watches remote `__version__`, so an unbumped push never reaches
  installed machines. Repo-only docs/tests do not need a bump unless they must
  reach installed machines. `python scripts/check_release_bump.py origin/main`
  is the CI guard for plugin-only changes. When the human says **"push"** they
  mean push without a bump; **"push and update version"** (or "bump the
  version") means bump first (semver: patch for fixes, minor for features),
  then commit and push. When asked to just "push", advise whether a bump is
  warranted and let the human decide. Full runbook: [RELEASING.md](RELEASING.md).
- Verify with `git log -1 --format='%B'`.

Note: `README.md`, `PIPELINE.md`, `MULTI_COMPUTER.md`, `MCP_CLIENTS.md`,
`HISTORIANS_README.md`, `AGENTS.md`, `HARNESS_INTRODUCTION.md` and
`DSH_PLUGIN.md` are force-included into the wheel (see `pyproject.toml`), so
editing one of them can be a change that reaches installed machines. The
archive's own `AGENTS.md`/`README.md`/`PIPELINE.md` are **generated** from
templates in `src/personal_historical_archive/archive_init.py`, not copied from
these files — the two are independent.

## What pha is

**personal-historical-archive (pha)** — a local archive of historical documents.
CLI `pha`, Python package `personal_historical_archive`
(`src/personal_historical_archive/`). Working branch `main`.

Pipeline: dropbox → palaeographer per-page transcription → optional editor
transform → optional encoder (page-grounded records) → SQLite (FTS5 +
embeddings, raw and edited variants both indexed) → hybrid search + FastMCP
(`pha_*` tools).

`archive_dir` is the **self-contained data root** — `dropbox/`, `models/`,
`palaeographers/`, `editors/`, `encoders/`, `notes/`, `skills/`, `library/`,
`renders/`, `archive.db`, plus the machine-local `pha-location.md` + `.pha/`.
The project dir holds only code, `prompts/`, `schema/` and the `_sample.md`
templates.

Resolving which archive is in use, and the precedence of environment / legacy
`.env` / project `config.yaml` / per-user settings / default, is documented in
`ARCHITECTURE.md` §1. `pha info` reports the source that won and a
`configured` boolean — `configured: false` means the project-root default, which
is **not** a usable archive. `pha info` creates nothing and never prompts, so it
is the safe discovery probe.

## The rules you must not get wrong

1. **One model per model-server at a time.** `pha scan`, `pha edit`,
   `pha reindex`, `pha test`, `pha unbundle` and `pha handoff fetch` each lock
   every model-server they will talk to and **refuse** (naming the holder) if one
   is busy. Two jobs may run concurrently only if their servers are disjoint.
   Never start an edit/reindex against a server a scan is using — that is what
   fills the disk and costs a document its vectors. Check `pha doctor` and
   `pha_extraction_status` before starting a pass. Full mechanism:
   `ARCHITECTURE.md` §4.
2. **Use `pha rm` / `pha mv`, never a shell `rm` or `mv`.** A shell `mv` makes
   the next scan treat the file as new and re-extract every page; a shell `rm`
   loses the library folder and index. Both commands refuse a `processing`
   document. Details: `OPERATING.md` §4.
3. **A `reviewed` page outranks `--reprocess`.** `pha review --unset` is the
   only way to re-run a stage over human-touched text — required before applying
   a new palaeographer/editor/filter to those pages. The review round-trip is in
   `OPERATING.md` §5.
4. **Probe before installing or re-running.** A missing engine reported by pha is
   really absent (install it, do not retry); an OCR scan needs no LM Studio.
   `ARCHITECTURE.md` §4 lists the resolution order and the install commands.
5. **Never write a resolved API key into a file** — keep `${ENV}` placeholders.
6. **Palaeographer prompts are transcription-only.** They reproduce the page
   faithfully and must not expand abbreviations, modernize, translate or
   normalise names; that belongs in the editor prompt. `ARCHITECTURE.md` §3.

## Stage configuration, in one screen

A collection selects its stages with a **`pha.yaml` sidecar** next to the
documents (nearest-wins per key up the directory chain). Each stage pairs `rules`
with its `model` — **both required** — plus optional `pre:`/`post:` filters and
per-collection `render` settings:

```yaml
palaeographer:
  rules: jesuit-cat1
  model: minimax-m3
editor:                     # or `editor: null` for no editor
  rules: modernise
  model: minimax-m2-5
encoders:
  - rules: table
    model: minimax-m2-5
render: {max_image_px: 3000, jpeg_quality: 88}
```

Model interfaces live in `models/<id>.md`; prompts/rules live in
`palaeographers/`, `editors/`, `encoders/` and carry **no** model. To add one:
duplicate `_sample.md`, rename, edit — the file stem is the id. A change is
picked up by staleness-by-mtime on the next matching pass, so re-run `pha scan` /
`pha edit` / `pha encode` afterwards. Full layering, the builtin `_sample*`
catalogue, and the null editor: `ARCHITECTURE.md` §2–3. How to inspect or change
what a collection is set to: `OPERATING.md` §2–3.

## Working in an archive (not this repo)

An agent may be running **from an archive directory**, where the person is almost
always a historian, not a programmer. The communication and permission rules —
plain language, explain before asking for any extra right, never take a
permission that was not granted — are in **`OPERATING.md` §1**, together with the
skills that ship with an archive and the pointer to `PIPELINE.md` for
"explain how this works" questions. Read that section before the first
user-facing answer in an archive.

The archive carries its own generated `AGENTS.md`/`README.md` and its own
`skills/` folder, so an agent working only from the archive directory does not
need this checkout.

## Full usage

`README.md` is the manual. `pha help <readme|pipeline|mcp|historians|agents|multi-computer|harness|view>`
prints the right document and names which copy it found.
