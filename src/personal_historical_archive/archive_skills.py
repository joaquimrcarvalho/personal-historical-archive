"""The `skills/` folder inside a pha archive.

An archive carries its own **pha-specific agent skills**: short instruction
files that fix the mistakes agents make while operating an archive (a search
hit is a snippet, not a document; an already-ingested document needs a
targeted, forced re-run). They live in the archive itself — a sibling of
`dropbox/`, `library/` and `notes/` — so an agent handed only the archive
directory finds them without the pha source checkout and without network.

Layout (the `~/.agents/skills/` convention agent runtimes use):

    <archive>/skills/README.md          what these are, and the format
    <archive>/skills/<name>/SKILL.md    one skill, YAML front matter + body
    <archive>/skills/<name>/...         helper files the skill ships

The skill files are **embedded in this package** (like `builtin_samples()`),
not read from the source tree: the machine that owns the archive may have pha
installed with no access to the pha repository — which is exactly the situation
these skills exist for. The repo's `skills/<name>/` files are the authored
copies; `tests/test_archive_skills.py` asserts the embedded files match them
byte for byte, so the two cannot drift.

Seeding writes a bundled file **only when it is missing** — like
`notes/README.md`, and unlike README.md/AGENTS.md (which are marker-stamped
and refreshed): an existing skill file is never overwritten, so the archive
owner's edits survive. A missing bundled file — e.g. a skill added in a newer
pha version — is (re)created on the next run, which is how updates reach an
existing archive.

This module deliberately imports nothing from the package, so both
`archive_init.init_archive` and `Config.ensure_dirs` can use it without an
import cycle through `config`.
"""
from __future__ import annotations

from pathlib import Path

_SKILL_DOCUMENT_OPERATIONS = """---
name: pha-document-operations
description: Re-run the pha pipeline on one existing document or collection in personal-historical-archive (pha) — re-scan (re-extract), re-edit, or re-encode it — without inspecting the pha source code. Use when the user asks to "rescan", "re-process", "re-run", "re-edit", "re-encode", "scan this document/collection again", or to change how an already-ingested document is processed. Teaches how to find the document's dropbox path, how to force a re-run (pha scan skips unchanged documents unless --reprocess), and how to verify the result.
---

# pha Document Operations

## Before you start: talk to a historian

You are working inside an archive, so the person asking is most likely a
**historian, not a programmer**. Say what you are about to re-run, and why, in
plain language and in terms of their documents — and report the result the same
way. If the job needs anything beyond the archive (installing software, a
password, files elsewhere on the machine), explain in simple words what you
need, what it is for and what will change; ask for the smallest permission that
does the job, and wait for a clear yes.

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

**Re-read ONE page with a stronger model** — a bad page must not cost the whole
volume. This is a MODEL re-read (the machine path); use `pha review` for a
human correction.

- C: `pha scan --path collections/COLX/vol04.pdf --page 337 \\
       --palaeographer <rules> --model <model>` (add `--dry-run` first: it
  prints the plan and calls no model). Only that page is rendered and
  transcribed, its provenance is recorded, and it is **pinned** so a later bulk
  scan keeps it. The editor and indexer then run for that page automatically —
  do **not** follow with `pha edit`/`pha reindex`.
- C: preview without touching the DB: `pha test <doc> --page 337 --palaeographer <rules> --model <model>`
- C: release the pin (text kept): `pha scan --path <doc> --page 337 --unpin`
- D: `pha_job_start({ action: 'scan', path: '<doc>', page: 337, palaeographer: '<rules>', model: '<model>' })`
- Needs `--path` naming ONE document. A page a human corrected (`reviewed`) is
  refused — release it with `pha review --unset --doc N --page 337` first,
  never by hand-editing around it.

If you only need to find out what a document currently resolves to (which
palaeographer / editor / prompt / encoders), inspect, don't re-run:

- C: `pha palaeographer collections/COLX`, `pha editor collections/COLX`,
  `pha prompts collections/COLX`, `pha encoder [file]`
- D/M: `pha_collection_config('collections/COLX')` (one object with the
  resolved `palaeographer`, `editor`, `prompt` and their `source`).

### 3. Respect the model-server lock

A job holds a lock on **every model-server it will use** (`pha scan`,
`pha edit`, `pha test`, `pha reindex`, `pha unbundle`, `pha handoff fetch`): a
server that loads models just in time keeps one resident, and loading two there
swaps/page-out and fills the disk. Jobs whose servers are disjoint may run
together. Never
start one while another is using the same server — check `pha status` /
`pha_extraction_status` first, and if a pass is running, wait (or, in Profile
D, poll `pha_job_status`). `pha doctor` lists the servers and their capacity.

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
| Re-read one page (a chosen model) | `pha scan --path <p> --page 3 --palaeographer X --model Y` | `pha_job_start scan path=… page=3 palaeographer=X model=Y` | preview only: `pha test <p> --page 3` |
| Re-encode | `pha encode --reprocess` | `pha_job_start encode reprocess=true` | not supported |
| Dry-run config | `pha test <path> --pages 3` | — | — |
| Inspect config | `pha editor/palaeographer/prompts <file>` | `pha_collection_config(<path>)` | `pha_collection_config(<path>)` |
| Verify | `pha status`, `pha page` | `pha_status`, `pha_page` | `pha_extraction_status`, `pha_get_page` |
| Get a page's text | `pha page <doc> <page> [--edited]` | `pha_page(id, page)` | `pha_get_page(id, page)` |
| Handed out to another machine | `pha handoff status` / `pha handoff fetch` | — | `pha_handoff_status()` |

## If the document is leased (out on hand-over)

A re-run that reports **nothing to do** for a document that clearly needs work
may not be a staleness problem: the document can be **leased** to a second
machine (`pha handoff`). `pha scan` / `pha edit` / `pha encode` / `pha reindex`
deliberately skip a leased document, printing the hand-off id and its age.

- Confirm with `pha status` (an "out on hand-over" section) or `pha handoff
  status [--json]`, and check `pha_handoff_status()` over MCP.
- When the second machine's work comes back, apply it with `pha handoff fetch
  <result-dir>` — not with a re-run. A page corrected here afterwards is kept
  and reported as a conflict.
- Release an abandoned loan with `pha handoff cancel <id>`; the document
  becomes usable here again, and any late result for it is then refused.
- Only override deliberately: `--include-leased` makes a scan/edit/encode/
  reindex touch a leased document anyway, which is how two machines end up
  transcribing the same pages.

On the **other** machine (the one that received the payload) nothing is leased:
`pha handoff in <payload>` imports the document and leaves it resumable, then a
plain `pha scan`/`edit`/`encode --path <doc>` finishes it and `pha handoff back`
writes the return payload.

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
"""

_SKILL_SEARCH_CONTEXT = """---
name: pha-search-context
description: Recover the full-document context of archive search results in personal-historical-archive (pha). Use when an agent has run `pha search` or the `pha_search` MCP tool and must answer from the hits, or when the user asks for the full transcription / edited version behind a search snippet. Number the search results; before quoting or answering from a hit, retrieve the complete text of that page (raw transcription and the edited variant when one exists); and when a hit is a page of a multi-page document that appears to have started on an earlier page, pull the earlier pages (back to the document/record start) so the answer has real context, never a bare snippet.
---

# pha Search Context

## Before you start: talk to a historian

You are working inside an archive, so the person asking is most likely a
**historian, not a programmer**. Present what you found in plain language, say
which reading you are quoting (the faithful transcription or the edited text),
and skip the jargon. If a step needs anything beyond the archive — installing
software, a password, files elsewhere on the machine — explain in simple words
what you need and what it will change before asking for it.

pha search results are **snippets of chunks of pages**, not documents. A hit
points at `(document_id, page_no)` plus a `variant` (`raw` transcription or
`edited`). A single search can return the *same page twice* — once for each
variant — and the same *document* many times from different pages. Agents
repeatedly make two mistakes:

1. Answering from the snippet alone, when the passage is mid-document and its
   meaning depends on text from earlier pages (a letter, a register entry, a
   catalogue row begun on a previous folio).
2. Not offering / fetching the full page or full document the hit comes from,
   in the user's preferred variant (faithful transcription vs the
   edited/modernised/translated text).

This skill fixes both: **present hits numbered, then always recover full
context before answering.**

## The two capability profiles

The recovery commands differ depending on where the agent runs. Determine your
profile first:

- **Profile M (MCP tools only)** — remote agent connected to the FastMCP
  server. Recovery is done with `pha_get_page(document_id, page_no)` (all
  variants of one page, optional image) and
  `pha_get_document(document_id, max_chars)` (whole raw document, truncated to
  `max_chars` chars, default cap 20000). Browse with `pha_list_documents`.
- **Profile C (CLI + files)** — agent on the archive machine, or one that can
  read the library folder. `pha page DOC PAGE` / `pha page DOC PAGE --edited`
  prints full page text; `pha search --json` shows structured hits; the
  per-page files live under `library/…/<stem>_<date>/`.

An agent that can see the filesystem should still prefer the CLI/MCP for
metadata, but either profile must follow the same numbered-results + context
recovery workflow below.

## Workflow

### 1. Run the search and present hits numbered

After `pha search "…"` (or `pha_search`), list every hit as

```
N. [variant] <filename> [collection] p.<page_no> — document #<id>
   snippet: "…"
```

- Keep the numbering visible: the user refers to results by number.
- A page indexed under both variants may appear as two hits. Where they are
  adjacent, present them as one entry with both variants and explain: *"this
  page is indexed both as the raw transcription and the edited version"*.
  When they are not adjacent, still dedupe by `(document_id, page_no)` in your
  presentation and mention the twin variant exists.
- If a hit's snippet is genuinely enough to answer a factual question exactly
  (a name, a date, a short list item), say the answer comes from the snippet
  and offer the page for verification — do not silently answer from the
  snippet when the user asked about the document.

### 2. Offer / retrieve the full text behind each hit

Before using *any* hit in an answer, retrieve the **complete page** it came
from, and offer the full document when the hit is one of several pages of the
same item.

- Full page, raw transcription (faithful reading):
  - M: `pha_get_page(<document_id>, <page_no>)` → field `transcribed`.
  - C: `pha page <doc> <page>` or `pha page <doc> <page> --json`.
- Edited variant, when it exists (the page went through the editor — modernised
  spelling and/or translation, named entities refined):
  - M: `pha_get_page(…)` → the `edited` dict (`{editor: text}`); the page has
    an edited version if that dict is non-empty. `encoded` records, when
    present, are structured extracts of the page — useful for list/record
    questions.
  - C: `pha page <doc> <page> --edited` (errors with a clear message if the
    document has no editor configured or the page was never edited). Checking
    whether an edited version exists: the document has an editor if
    `pha editor <doc>` prints one, or if a `library/…/edited-*/` folder exists
    for its date folder.
- Full document:
  - M: `pha_get_document(<document_id>, max_chars=…)`. It returns raw
    transcription only, page by page (`## Page N`), so raise `max_chars` for
    long documents; if it returns a truncation marker, re-ask with a larger
    cap or fetch pages selectively. If the user wants the whole document in
    edited form, retrieve page by page via `pha_get_page` (`edited` field) —
    there is no single whole-document edited call.
  - C: read the whole library document folder (`transcription-*/` pages) or run
    `pha page <doc> <page>` for each page; `pha page DOC 1` … through the page
    count. `pha export` / document concatenation exists in the library as the
    encoder input (e.g. `concatenated-*.md`) when present.

State clearly which variant you are quoting: raw transcription is the faithful
record of the source; the edited text is a modernised/translated reading — do
not present edited wording as if it were the original.

### 3. Check whether the hit page needs earlier pages

A page of a multi-page document is usually **not** self-contained. Before
answering, decide whether the hit sits mid-document and pull context:

- Look at the document as a whole first (M: `pha_get_document`, C: first page /
  folder listing). Ask:
  - Is this one continuous text (a treatise, a long letter, a diary)? Then a
    hit on page 37 may depend on anything before it — offer / fetch the whole
    document.
  - Is it a *collection of separate items* (letters, catalogue entries, register
    rows)? Then find **which item** the hit belongs to and read that item from
    its own start, not necessarily from page 1.
- Signals that a page continues text started earlier:
  - The page text begins mid-sentence, with a lowercase continuation, or with a
    connector such as *e*, *que*, *—*, *dito*, *o qual*, *…así que*, *«…»*,
    rather than with a heading, rubric, or clear new-item start.
  - The page begins with a word or phrase that refers back (anaphora, resumed
    argument), or an item clearly began on a previous folio (e.g. a letter
    whose first page is earlier, a catalogue person whose row starts on the
    previous page).
  - The snippet itself is a fragment that only makes sense with what precedes.
- **If the page appears to be part of a document/item started earlier, read the
  preceding pages** back to the start of the document or of the item (M: loop
  `pha_get_page` for page_no-1, page_no-2, … until a clean start; C: `pha page`
  the earlier pages / read the earlier library files, e.g. `502V.md` before
  `503.md` in the same folder). Also scan *forward* a page or two to where the
  passage ends if the snippet stops mid-thought.
- Mention the context pull in your reply: *"this hit is on p. 37; the
  passage continues an item that starts on p. 34, so I read pp. 34–37."*
- If you cannot tell whether an earlier page is needed, offer it to the user
  rather than guessing: *"want me to pull the earlier pages of this document?"*

### 4. Answer with grounded citations

Every substantive claim should cite the source it came from:

```
(document #<id>, p.<page_no>, raw transcription | edited [<editor>])
```

- Quote the *edited* variant only as the modernised reading; quote the *raw*
  transcription when the user wants the original wording.
- If you used a snippet alone (short factual hit), say so; if you used context
  pages, say which.

## Quick reference

| Need | Profile M (MCP) | Profile C (CLI/files) |
| --- | --- | --- |
| Numbered search results | `pha_search` returns list — number it yourself | `pha search "…"` (already numbered); `--json` for structured |
| Full page raw | `pha_get_page(id, p)` → `transcribed` | `pha page DOC p` |
| Full page edited | `pha_get_page(id, p)` → `edited` dict | `pha page DOC p --edited` |
| Structured records of page | `pha_get_page` → `encoded` | `pha page … --json` / library files |
| Whole document | `pha_get_document(id, max_chars)` (raw; edited per page) | read `library/…/<stem>_<date>/` files; `pha page` per page |
| Document metadata / editor | `pha_list_documents` | `pha status`, `pha editor DOC` |
| Is an edited version available? | `edited` non-empty in `pha_get_page` | `pha page DOC P --edited` succeeds; `edited-*/` folder exists |
| Page count / document size | `pha_get_document` → `pages`; `pha_list_documents` | page files in the date folder |

## When not to use this skill

- Local `pha test` runs that never touch the archive DB (scratch output only).
- Work purely on raw source images / renders without any search step.
- If the user is only configuring models/editors/encoders and not searching.

## Checklist

- [ ] Search results presented **numbered**, with document, page, variant.
- [ ] Twin hits (raw + edited of the same page) deduped / explained.
- [ ] No answer given from a bare snippet without offering the full page.
- [ ] Full page fetched (raw) before quoting; edited version fetched/offered
      when the page has one.
- [ ] Whole document offered when the hit is one page of a longer item.
- [ ] Mid-document hits: preceding pages read back to the document/item start
      and the context pull reported.
- [ ] Every claim cites document id, page, and variant (raw vs edited).
"""

SKILLS_README_MD = r'''# Skills

This folder holds the **agent skills pha bundles with an archive** — short,
self-contained instruction files that tell an AI agent how to operate *this*
archive (and do recurring research tasks on it) without reading the pha source
code. They are the archive's own copy, seeded here when the archive was
created, so an agent that was pointed at this directory alone can find them (no
pha checkout, no network).

Each skill is one folder with a `SKILL.md` inside:

    skills/<name>/SKILL.md

A skill may also ship the files it needs — helper `scripts/`, `examples/`, a
`LICENSE`, its own `README.md`. `pha` seeds every bundled file, so an archive
with no source checkout gets the whole skill, not only the instructions.

Paths inside a skill are relative to the skill's own folder: run its
`scripts/...` from there (or with the full path), not from the archive root.

The `SKILL.md` file starts with YAML front matter carrying a `name` (which
**must match the folder name**) and a `description` (the trigger — when an
agent should reach for it), followed by the instructions:

    ---
    name: pha-search-context
    description: Use when an agent has run `pha search` and must answer ...
    ---

    # pha Search Context
    ...

## How an agent should use these

**Using the archive:** before the matching task, read
`skills/<name>/SKILL.md` and follow it. The six skills seeded here cover the
most common tasks:

- `pha-search-context` — a search hit is a *snippet*; recover the full page
  (raw and edited) before quoting or summarizing.
- `pha-document-operations` — re-scan / re-edit / re-encode one
  **already-ingested** document or collection (`pha scan --path … --reprocess`,
  `pha edit --path … --page N`, `pha test`).
- `pha-zotero-bibliography` — import a PDF from Zotero with its bibliographic
  sidecar, or build/refresh one document's reference from the owner's Zotero
  library (the local API's MODS, an RDF export, `pha bib <doc> --to-json
  --write`, and the provenance rules that keep an unverified reference from
  being cited as fact).
- `palaeographers-compare` — compare two or more palaeographers' (or
  transcription models') readings of the same pages into a uniform comparative
  edition: a `comparison/` folder with one file per page (readings stacked
  under `## Entry-by-entry comparison` plus a `## Key differences on this
  page` list) and an `overview.md`; the compared pages' images are copied to a sibling `images/` folder. Ships `scripts/normalize_comparison.py`
  (skeleton normalisation), `scripts/verify_comparison.py` (skeleton +
  reading-count checks) and `scripts/make_reference.py` (one-line-per-entry
  `reference/` variant); a reviewed `human/` folder is read-only for agents.
- `obsidian-vault` - search, create and organise notes in the owner's
  Obsidian vault (wikilinks, index notes), and keep archive-derived notes in
  sync: YAML provenance is checked/stamped with
  `scripts/archive_note_sync.py` (`OBSIDIAN_VAULT`, `PHA_ARCHIVE_DIR`; flags
  `--vault`/`--archive` override).
- `timelink-kleio-provenance` - cite facts from Timelink/Dehergne
  prosopography by going from the SQLite attribute row back to the Kleio
  file and line, and render a `vscode://file/<absolute-path>:<line>` link;
  helper `timelink_provenance.py` (`MHK_HOME`).

**Installing them into an agent runtime:** some runtimes (DeepSeek Harness and
other tools that read the shared agent-skills convention) discover skills from
a user-level directory instead of the archive. Copy the whole skill folder
there — including any `scripts/`, `examples/`, … files it ships:

    cp -R skills/palaeographers-compare ~/.agents/skills/

Keep the folder names unchanged — a skill's front-matter `name` must match its
folder name.

## Editing and updating

`pha` seeds this folder from the skills bundled with the installed pha
version: a bundled file is written **only when it is missing**, and an existing
file is never overwritten — so your edits survive. A skill or file added in a
newer pha version appears here on the next pha run, which is how an updated
install reaches an existing archive. (A bundled file you delete is re-seeded
the same way; to keep a custom version, edit the file in place instead.)

Add your own skills — any folder holding a `SKILL.md` conforming to the format
above — and edit or extend the bundled ones freely. To pick up a newer authored
version of a skill, copy it from the `skills/` folder of the pha source
repository, or from a newly created archive of the same pha version; pha will
not replace the files you already have.
'''

_SKILL_ZOTERO_BIBLIOGRAPHY = """---
name: pha-zotero-bibliography
description: Import a PDF from Zotero into personal-historical-archive (pha) and give it a bibliographic sidecar, and build or refresh the reference of a document already in the archive from the owner's Zotero library. Use when the user says they "exported from Zotero", hands over a Zotero RDF or MODS export file, asks to "add the bibliographic data / reference / citation" to a document, or wants a sidecar (`<stem>.dc.json`) beside a PDF. Covers the Zotero local API (MODS), the Zotero RDF export package, where the PDF belongs, the sidecar formats, and the provenance rules that stop an unverified reference from being cited as fact.
---

# pha Zotero Bibliography

Two jobs, one skill:

- **Import** a PDF that lives in Zotero (or in a Zotero export package) into
  the archive, with its reference beside it.
- **Build or refresh the reference** of a document that is *already* in the
  archive, from the matching Zotero record.

## Before you start: talk to a historian

You are working inside an archive, so the person asking is most likely a
**historian, not a programmer**. Say what you are about to import or record,
and why, in plain language and in terms of their documents — "I'm adding the
catalogue to your ANTT collection with its full reference from your Zotero
library", not "writing a sidecar". If the job needs anything beyond the archive
(reaching Zotero on the machine, installing something), explain in simple words
what you need, what it is for and what will change, and wait for a clear yes.

**A wrong reference reads exactly like a correct one.** Everything below exists
to stop a plausible-looking citation from being treated as fact. When in doubt,
record less and mark it unverified.

## What a bibliographic sidecar is

A document may carry its reference in a file **beside it**, all parsing to the
same record, so the format never changes how a citation looks:

| file | role |
| --- | --- |
| `<stem>.dc.json` | JSON — **the form a human edits** |
| `<stem>.mods.xml` | MODS 3.8 — a machine interchange format (what Zotero exports) |
| `<stem>.bib` | BibTeX — the form an agent can draft from a scan |

Rules that follow from that:

- **Same stem as the document**, in the document's own folder. A
  directory-of-images document keeps it *inside* the folder
  (`vol04/vol04.dc.json`).
- **Presence only — no inheritance, no default.** A document with no sidecar
  simply has no reference. Never copy a neighbour's reference and never add a
  sidecar "to fill in" a collection: an inherited reference is a confidently
  wrong citation, which is worse than none.
- **JSON wins.** If more than one is present, `pha cite` uses the JSON and
  warns; resolve it (`pha bib --check` reports it). The clean end state is
  **one** sidecar per document — the `.dc.json`.
- Key spelling is forgiving (`part_number`, `partNumber`, `dcterms:partNumber`
  are one field), so a qualified Dublin Core JSON-LD record works too.

## Determine your profile first

- **C — CLI + files** (you are on the archive machine). Zotero runs on this
  machine and answers on `http://localhost:23119`. This is the normal case.
- **D — dsh-pha plugin**: the `pha_*` model tools plus the archive's files.
  The Zotero API calls still have to run on the archive machine.
- **M — FastMCP** (remote agent): you **cannot reach the owner's Zotero** —
  `localhost:23119` is on their machine, not yours. Ask the operator to run the
  one `curl` below and hand you the MODS, or to drop it in the archive; then
  use `pha_upload` to push content into the dropbox and `pha_scan_now()`.

## Route A — the Zotero local API (preferred)

Zotero 10+ exposes a read API while it is running:

    curl "http://localhost:23119/api/users/0/items/<KEY>?format=mods"

- **Find `<KEY>`** — search the API, or in Zotero right-click the item and
  choose *Show Items…*/look at its key in the right pane:
  `curl --get --data-urlencode "q=<title words>" --data "qmode=titleCreatorYear&limit=5" "http://localhost:23119/api/users/0/items"`
  returns JSON with each item's `key`.
  **`itemID` is NOT a query filter.** A request like
  `?itemID=18283` is *silently ignored* and returns the library's first items —
  a real trap: check that the returned `title` is the work you meant.
- **Zotero not running / API off?** A connection error means Zotero is closed;
  `403` means the local API is switched off in Zotero's advanced settings. On
  **Zotero 9 or older** the answer carries no `Zotero-Server-ID` header and the
  API is **read-only** — reading is still fine.
- **Strip `<note>` before saving.** Zotero's MODS embeds every annotation and
  highlight the owner wrote (~86% of the bytes). They are not bibliographic
  data and must not be republished:

      python3 - <<'PY'
      import xml.etree.ElementTree as ET
      src, dst = "item.mods.xml", "REF.mods.xml"
      tree = ET.parse(src); root = tree.getroot()
      parent = {c: p for p in root.iter() for c in p}
      for note in [e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "note"]:
          parent[note].remove(note)
      tree.write(dst, encoding="utf-8", xml_declaration=True)
      PY

## Route B — a Zotero RDF export package

*Export Items* with **Format: RDF** produces a folder holding `<name>.rdf` plus
the PDFs under `files/<itemID>/`. This route needs no running Zotero, and it is
how a Zotero item travels *with* its file. It is **lossy** — often no
publisher, date, ISBN or volume — so if Zotero is reachable, prefer Route A.

Reading the RDF:

- The **item** is the child of `rdf:RDF` that is neither `z:Attachment` nor
  `bib:Memo`.
- A **`bib:Memo` is a Zotero note**, i.e. the owner's annotation — never
  bibliographic data; drop it.
- Each `z:Attachment` carries `z:path@rdf:resource` = `files/<itemID>/<name>`,
  the PDF to move.
- `rdf:about="#item_N"` on the item is the Zotero **itemID** (not the 8-char
  key); it is a fine `record_id` when the key is not available.

Field mapping (Zotero RDF → sidecar):

| RDF | sidecar |
| --- | --- |
| `dc:title` | `title` |
| `bib:authors/rdf:Seq/rdf:li/foaf:Person` (`foaf:surname`, `foaf:givenName`) | `creators` (`"Surname, Given"`, role `aut`) |
| `dc:date` | `date_issued` |
| `dc:publisher/foaf:Organization/foaf:name` | `publisher` |
| `z:language` | `language` |
| `z:itemType` | `type` (`text`) + `genre` (`book`, `article`, `chapter`, `thesis`, `manuscript`, `report`, `misc`) |
| `z:archive` | `repository` |
| `dc:coverage` | `shelfmark` |
| attachment `dc:identifier/dcterms:URI/rdf:value` | `url` |

## Where the PDF goes

- **`dropbox/collections/<collection>/`** — a collection is just a folder under
  the dropbox. Put it where the user's own words (or their inbox layout) point;
  if it is genuinely unclear, ask rather than invent a new collection.
- **Filename**: a stable lowercase-hyphenated slug (`antt-armario-jesuitico-catalogo.pdf`),
  or the original name when that is already good. The sidecar must share the
  stem exactly.
- **`inbox/` is a hold area** — never scanned. `pha inbox` lists it and
  `pha inbox --move` relocates an entry into the dropbox **mirroring its
  relative path** (`inbox/collections/COLX/a.pdf` → `dropbox/collections/COLX/a.pdf`).
- A Zotero export of one work can arrive as several PDFs (parts of a book).
  Each file is a separate document, and **each needs its own sidecar** —
  presence-only means no sharing. (Joining the parts into one file first is
  fine too; then there is one document and one sidecar.)

## Writing the sidecar

**Never hand-craft MODS XML, and never ask a human to edit it.** Convert it
once, then keep the JSON:

    pha bib <doc> --to-json --write          # writes <stem>.dc.json, drops the other formats
    pha bib <doc> --to-bibtex --write        # BibTeX instead
    pha bib <doc> --to-json --write --keep-others   # keep the .mods.xml/.bib as well
    pha bib <doc> --to-json --qualified      # Dublin Core JSON-LD (dcterms:/pha: keys)

`--to-json`/`--to-bibtex` only *print* unless you add `--write`. `<doc>` is a
document id or a filename substring, so the document must already be known to
the archive: for a **brand-new** PDF, place it in the dropbox and `pha scan`
it first (the sidecar may be added before or after — a reference never
re-transcribes anything).

For a **Route B (RDF)** import there is no MODS to convert, so write the JSON
with pha's own emitter, which guarantees the file parses back identically:

    python3 - <<'PY'
    import pathlib
    from personal_historical_archive import bibliography as B
    bib = B.Bibliography(
        title="Armário Jesuítico: catálogo",
        creators=[B.Name(name="Braga, Joana", role="aut")],
        type="text", genre="book",
        place="Lisboa", publisher="Arquivo Nacional da Torre do Tombo",
        date_issued="2020", language="pt", url="https://…",
        record_id="67CD94VL",
        record_origin="imported-from-zotero-rdf-unverified",
    )
    pathlib.Path("antt-armario-jesuitico-catalogo.dc.json").write_text(
        B.to_dc_json_text(bib), encoding="utf-8")
    PY

Keep the source `.rdf` beside the document as `<stem>.zotero.rdf` if the user
wants the raw export kept — pha ignores unknown suffixes, so it stays out of
the way.

## Provenance: mark, never assert

`record_origin` decides how the reference is reported. Set it honestly:

| value | meaning | effect |
| --- | --- | --- |
| `fetched-from-zotero-unverified` | read from the owner's library via the API | reported by `pha bib`, **not** badged in citations |
| `imported-from-zotero-rdf-unverified` | same, from an RDF export | as above |
| `imported-from-internet-archive-unverified` | another maintained source | as above |
| `agent-drafted-unverified` | **a model drafted it** (e.g. from a title page) | every citation ends `[unverified reference]` |
| `human-supplied` / `human-confirmed` / `human-reviewed` | a person checked it | treated as verified |

- Anything containing `zotero`, `imported` or `unverified` counts as
  not-yet-reviewed; anything containing `agent`/`draft` also badges the
  citation; a human marker always wins.
- **Only a human may set a `human-*` marker.** Never upgrade one yourself, and
  never remove `agent-drafted-unverified`.
- **Never invent** a shelfmark, publisher, volume, place or date. Omit the
  field instead — a missing field is visibly missing; a fabricated one is not.
- **Never overwrite a `human-supplied` record** without saying so.

## Fix the source in Zotero, not the sidecar

The sidecar mirrors Zotero; Zotero is the source of truth. When the record is
*wrong* — a "creator" that is really the holding library, a journal article
that is a thesis, a year that contradicts the title page — **fix it in Zotero**
and re-import. Patching only the sidecar creates a second, diverging record
that the next import silently overwrites.

Tell the user plainly what you found and what it should be, and let them
decide; their Zotero library is theirs. Report data-quality findings even when
you do not act on them.

## Verify

    pha bib <doc>                 # one document's reference, and whether it is verified
    pha bib                       # coverage: which documents have no reference
    pha bib --check               # sidecars that are broken, empty or duplicated
    pha cite <doc> <page>         # the rendered citation, as a reader will see it

**Editing a sidecar never re-transcribes a document.** It is metadata: it does
not touch the hash, page text, status or library version, so no `pha scan` is
needed to pick it up — `pha cite` and `pha bib` read the file live. The DB
snapshot that `pha serve` and the MCP tools read is refreshed by `pha scan`,
`pha reindex`, or any `pha bib` run.

## Quick reference

| Need | Command |
| --- | --- |
| Read a record from Zotero (MODS) | `curl "http://localhost:23119/api/users/0/items/<KEY>?format=mods"` |
| Find a key by title | `curl --get --data-urlencode "q=<title>" --data "qmode=titleCreatorYear" .../items` |
| Register a new PDF | `pha scan --path collections/COLX` |
| MODS → editable JSON | `pha bib <doc> --to-json --write` |
| Draft a reference from a scan | `pha bib <doc> --to-bibtex --origin agent-drafted-unverified --write` |
| Show one reference | `pha bib <doc>` |
| List documents with no reference | `pha bib` |
| Find broken/duplicate sidecars | `pha bib --check` |
| Render the citation | `pha cite <doc> <page>` |
| Move a held document out of the inbox | `pha inbox --move` |

## When not to use this skill

- The user just wants a quick reference drafted from the **scan itself**, with
  no Zotero involved — that is a plain `.bib` with
  `--origin agent-drafted-unverified`, and the same "never invent" rules apply.
- Re-running transcription/editing on an already-ingested document — that is
  `pha-document-operations`.
- Finding or quoting text — that is `pha-search-context`.

## Checklist

- [ ] Asked which Zotero item, and confirmed the record is the *right* one
      (compare the returned title/creator, not the id).
- [ ] Route chosen: the live API (MODS, preferred) or the RDF export package.
- [ ] `<note>` elements stripped from any MODS before it is saved.
- [ ] PDF placed in the right collection, with a stable filename; the sidecar
      shares the stem.
- [ ] Reference written as `.dc.json` (converted with `pha bib --to-json
      --write`, or emitted with `to_dc_json_text`) — not hand-written MODS.
- [ ] `record_origin` set honestly, and **no** `human-*` marker invented.
- [ ] Nothing invented: every shelfmark, publisher and date came from the
      record or the user.
- [ ] Wrong source data reported to the user (to fix in Zotero), not silently
      patched in the sidecar.
- [ ] Only **one** sidecar per document; verified with `pha bib <doc>` and
      `pha cite <doc> <page>`.
"""

_SKILL_PALEOGRAPHERS_COMPARE = r'''---
name: palaeographers-compare
description: Compare multiple palaeographers' (or transcription models') readings of the same historical document page-by-page and produce a uniform comparative edition — an overview of differences plus one markdown file per page with entry-by-entry stacked readings (1. reading A / 2. reading B / … / N. reading N; any number of readings, 2 or more). Use when the user asks to compare or collate transcriptions, compare palaeographers or transcription models, find differences between readings of the same document, or generate a comparative overview of catalogue/archive transcriptions.
---

# Palaeographers Compare

Produces a **uniform comparative edition** from two or more independent readings of the same document (e.g. three palaeographers, or several vision-model transcriptions of the same manuscript pages). The output is a `comparison/` folder with:

- one markdown file per page, in a **fixed uniform skeleton**, showing every entry with the readings stacked as `1.` … `N.` (or `= all readings:` when identical), plus a "Key differences" bullet list;
- an `overview.md` summarising corpus, palaeographer mapping, systematic conventions, and the most significant disagreements.

The reference implementation of the workflow (and of the expected output format) is `palaeographers/comparison/` under the 1577 Portuguese Jesuit catalogue benchmark — the same layout (corpus readings + sibling `images/` + `comparison/`) must be reproduced for every new corpus.

## Inputs

- An **input directory** containing one subdirectory per palaeographer (e.g. `palaeographers/DeepSeek-V4-FVE/`, `palaeographers/M3/`, `palaeographers/Qwen3.8-Max/`). Each subdirectory has one markdown file per page of the same document, with **matching basenames across palaeographers** (e.g. `507v.md`, `508.md`, …).
- **`images/`** (required): a sibling of the `palaeographers/` folder holding a copy of
  the **source image of every compared page**, with the same basename as the page files
  (`507v.md` -> `507v.jpg`; `.jpg`/`.jpeg`/`.png`/`.tif`/`.tiff`/`.webp` accepted). It is
  what a reviewer reads against when correcting a reading, and it keeps the benchmark
  self-contained. `verify_comparison.py` checks it (`--require-images` makes a missing image a hard failure).
- Each page file contains a transcription section. The transcription may be inside a fenced code block (under `## Transcription`) or be the body before `## Notes`. Only the transcription content is compared; `Notes` / `Named entities` / `Content summary` sections are context, never part of the comparison.

## Workflow

1. **Name the palaeographers.** List the subdirectories of the input dir. Fix their order with the user (this becomes the numbering used everywhere); default to folder order or alphabetical. Example: `1. DeepSeek-V4-FVE · 2. M3 · 3. Qwen3.8-Max`.

2. **Copy the page images.** Make sure an **`images/`** folder sits next to `palaeographers/` with a copy of every compared page image, named with the same basename as the page files (`507v.jpg`, `508.jpg`, ...). The comparison and the reference are meant to be corrected by a human **against the image**, so the benchmark must be self-contained. `verify_comparison.py` checks this.

3. **Discover the pages.** Take the intersection of filenames across all palaeographer subdirectories (a page must exist for every palaeographer). Sort them in reading order (recto/verso).

4. **Read every transcription** for every page×palaeographer. Note per-page context (community/college, column layout, rotation, damage, show-through) from the notes sections.

5. **Generate the per-page comparison files** (see "Output format" below). This step fans out naturally — generate each page's file independently; you may parallelise pages across subagents, but every file must follow the same skeleton exactly.

6. **Generate `overview.md`** (see "Overview" below).

7. **Normalise + verify** the whole output folder with the bundled scripts (see "Scripts" below) so every file shares the exact skeleton.

## Alignment rules

- One **entry** = one line of the catalogue (a person's name with or without role, a heading such as `Casos.` / `1ª classe de humanidad.`, a count marker, the `+IESVS` header).
- Entries appear in the same order in all readings; if a palaeographer misses, adds, or splits a line, align by position and meaning and **flag it** in Key differences.
- When readings diverge **fundamentally** (no shared lines — e.g. one palaeographer reads a blank leaf, another a rotated index, a third a show-through roster), present each reading **in full as one entry** (`[1]` … `[N]`) inside the fenced block and explain the disagreement in Context and Key differences.
- Preserve each reading's own structure verbatim (columns, numbering, class numbers) — never harmonise the palaeographers' structural choices; flag the disagreement.

## Fidelity rules (verbatim transcription)

- Preserve the original text **exactly**: diacritics (`ç ã ñ`), `[square bracket]` expansions, `[?]` and `[illegible]` markers, honorific variants (`P.`, `P[adr]e`, `P.e`, `Pº`), punctuation, slashes, trailing dashes, abbreviation forms (`Hernade3`, `coadintor`, `co[n]fessa`). Never modernise or normalise.
- `= all readings:` (older files may say `= all three:`) is used **only** when the readings are identical in every substantive character. Trailing periods, long filler-dash runs (`————`), and the name↔role connector (`—` vs `,`+dashes vs `/`) are treated as padding and ignored for the equality test.

## Output format — per-page file (uniform skeleton)

Every page file must have **exactly** this layout (title line, one Context paragraph, one section with a *single* fenced code block, one Key-differences section):

```markdown
# <page> — Comparative readings (1. <P1> · … · N. <PN>)

Context: <one or two sentences: what the page contains, column layout, rotation/damage notes, reading order>

## Entry-by-entry comparison

```
[1] 1. <reading 1>
    2. <reading 2>
    3. <reading 3>
    …

[2] = all readings: <shared reading>

[3] 1. <only the differing lines are shown; identical lines are dropped>
    ...
```

## Key differences on this page

- <bullet list of the most significant disagreements: name variants, one reading legible where another has [?], different role/office readings, extra/missing entries, structural differences — quoting the readings concisely>
```

Skeleton rules (non-negotiable):

- Title: `# <page> — Comparative readings (1. <P1> · … · N. <PN>)` — one numbered reading line per reading, `1.` … `N.` in order.
- Exactly two `##` sections, named `## Entry-by-entry comparison` and `## Key differences on this page`.
- **One single fenced code block** holding *all* `[n]` entries — never one fenced block per entry, never narrative prose sections.
- One blank line before the opening fence; entries separated by one blank line; stacked readings indented under their number.
- No stray label lines inside the block (e.g. a subagent's `Nuovices heading.`), no redundant "Numbering:" line (the mapping is in the title).

## Output format — overview.md

1. **Corpus** — what the document is (date, communities/colleges listed), table of pages with one-line content summaries.
2. **The palaeographers** — the fixed 1.…N. mapping, with the user's example reading quoted if one was given.
3. **Overall picture** — how much the readings agree; systematic orthographic/editorial conventions per palaeographer; the three kinds of difference (spelling conventions, damaged-character readings, structural interpretation).
4. **Page-by-page highlights** — for each page, the most significant disagreements with the competing readings quoted (e.g. a name read three ways, a role disagreement, a class-numbering difference).
5. **Consensus notes** — where a majority of readings agree, that reading is more probable; flag internally suspicious readings (e.g. dittography); keep historical identifications hedged ("historically plausible", verify against the edition) unless confirmed.
6. **Files** — index of the per-page files and sources.

## Scripts

After generating the files, normalise and verify the whole folder:

```bash
# Merge per-entry fenced blocks into one block; restore blank lines; collapse double blanks
python3 scripts/normalize_comparison.py comparison/

# Structural check: 2 fences per file, correct sections, contiguous entries
python3 scripts/verify_comparison.py comparison/

# ...and check every entry carries exactly N readings:
python3 scripts/verify_comparison.py comparison/ --readings 3
```

- `normalize_comparison.py` — for every page file (not `overview.md`): locates the section between `## Entry-by-entry comparison` and `## Key differences on this page`, removes per-entry fence lines, collapses blank runs, and wraps the entries in a single pair of fences. Optionally `--drop-label "Nuovices heading."` style stray lines via repeated `--drop-label` arguments.
- `verify_comparison.py` — prints OK/FAIL per file for: exactly 2 fences, exactly the two `##` sections, contiguous numbered entries inside one block, blank line before the opening fence, and matching entry counts. With `--readings N` it also fails any entry whose reading count is not N (0 is accepted for `= all …` entries; pages kept unconsolidated — a single reading per entry — are exempt), catching silent mis-merges. Exits non-zero if any check fails.
- `make_reference.py` — builds a **`reference/` folder for producing a reference translation**: every entry becomes ONE line with common words written once and variants joined with `/` in reading order (any number of readings). See "Reference variant" below.

## Reference variant (for producing a reference translation)

From the comparison folder, generate a sibling `reference/` folder aimed at translation work:

```bash
python3 scripts/make_reference.py comparison/            # writes reference/ next to comparison/
python3 scripts/make_reference.py comparison/ reference/ # explicit output folder

# guard against a wrong reading count (refuses to write if a page has more):
python3 scripts/make_reference.py comparison/ --readings 3

# pages that cannot be consolidated are auto-detected; force one explicitly:
python3 scripts/make_reference.py comparison/ --divergent 511
```

Each entry becomes one line, e.g. (entry [7] of `507v.md`):

```
[7] P./P[adr]e Iuan/Juan Correa/correa —————————/— Ministro.
```

Rules implemented by the script:

- Token-level sequence alignment (edit-distance DP) merges the N readings per entry, handling insertions/deletions.
- Common words are written once; variants are joined with `/` in reading order (1/2/…/N).
- Padding: trailing periods/commas, line-end hyphenation (`-`/`=` at line end), and standalone `/`/`-` name↔role connectors are dropped; em-dash layout leaders (`————`) are kept and shown as variants (`————————/—`).
- The first variant keeps its own punctuation (`P.`), later variants are punctuation-normalised (`P[adr]e`).
- Entries identical in all readings are given as-is. Pages whose readings diverge fundamentally (every entry carrying a single full reading — e.g. a leaf read as blank vs index vs show-through) are auto-detected and kept unconsolidated; `--divergent PAGE` forces the same for a named page. Reading markers are recognised only in sequence (`1.`, `2.`, …), so an embedded number in a wrapped line ("3. Nouicos.") is not mistaken for a reading.
- The output files keep the same uniform skeleton (title with palaeographer mapping, Context, one fenced block, Notes).

## Human-reviewed gold standard (human/ folder)

After the comparison and reference folders are produced, a **human reviewer** may edit the reference content into a corrected, variant-free version kept in a sibling `human/` folder: same page files and uniform skeleton, but with a `## Corrected text` section where the variants are resolved to the correct reading and the original line breaks are restored. (In the reference corpus, the review is by Joaquim Carvalho, started 2026-08-30; only `507v.md` has been reviewed so far — the other files are still unedited copies of the reference.)

The reviewer documents the editing rules in the file header (e.g. kept line-break hyphens in their `=` form, original capitalisation preserved, dots kept as word separators, long dash runs reduced to three dashes, letter normalisation `u`→`v` and `ƒ`→`ss`/`s` particular to this hand) and signs the review in the Notes section.

**Rules for agents:**

- `human/` is **read-only for agents** — never edit, "correct", regenerate, or reformat anything inside it; the human review is authoritative and must remain untouched.
- It may be **read freely**, and its purpose is twofold:
  - **Benchmarking palaeographers**: compare each palaeographer's raw reading (or the consolidated reference line) against the human corrected text — count per-entry agreement on names, roles, spellings and offices; identify which palaeographer is most accurate per page and which readings the human rejected.
  - **Improving prompts**: use the human version paired with the slashed variants as few-shot examples of correct readings, or feed the disagreements back into the transcription/vision prompt to target recurring failure modes (e.g. surname confusions, role misreadings).

The three-folder pipeline is therefore: `comparison/` (aligned stacked readings) → `reference/` (slashed variants for translation) → `human/` (reviewed gold standard for benchmarking and prompt tuning).

## Quality checklist

- [ ] Every page file shares the exact uniform skeleton (title / Context / one fenced block / Key differences).
- [ ] All readings verbatim: diacritics, `[?]`/`[illegible]`, bracket expansions, honorific variants preserved.
- [ ] `= all readings` (older files: `= all three`) used only for substantive identity; differing readings all shown.
- [ ] Structural disagreements (class numbering, columns, fundamental page disagreements) flagged, not harmonised.
- [ ] `overview.md` present with palaeographer mapping, conventions, page-by-page highlights, consensus notes.
- [ ] `verify_comparison.py` passes for every file.

## Examples

- `examples/entry-format.md` — minimal sample of the uniform skeleton.
- Live reference output: `palaeographers/comparison/`, `palaeographers/reference/` and `palaeographers/human/` in the 1577 Jesuit catalogue benchmark (8 pages, 3 palaeographers).
'''

_SKILL_PALEOGRAPHERS_COMPARE_README = r'''# palaeographers-compare

Compare several palaeographers' (or transcription models') readings of the same
historical document, page by page, and turn them into three review surfaces:

1. **`comparison/`** — every entry with the readings stacked side by side (`1.` … `N.`; any number of readings, 2 or more),
   plus an overview of the differences;
2. **`reference/`** — the same entries collapsed into one line each, with common words
   written once and variants joined by `/`, made for producing a translation;
3. **`human/`** — the place for your own hand-corrected, variant-free text (the gold standard).

Works on any folder of aligned transcriptions. It needs nothing but Python 3 —
no pha install, no special tooling, no network.

---

## How to use it (step by step)

### What you need first

A folder containing **one subdirectory per palaeographer**, each with **one markdown
file per page, using the same filenames across all of them**, plus an **`images/`**
folder next to it with a copy of the same pages:

```
1577/
├── images/                      507v.jpg  508.jpg  508v.jpg  ...  511.jpg
└── palaeographers/
    ├── DeepSeek-V4-FVE/         507v.md   508.md   508v.md   ...  511.md
    ├── M3/                      507v.md   508.md   508v.md   ...  511.md
    └── Qwen3.8-Max/             507v.md   508.md   508v.md   ...  511.md
```

Each page file needs its transcription (the readable text of the page). The `Notes` /
named-entity sections are ignored; they are only used as context.

The `images/` folder holds the **source image of every compared page**, with the same
basename as the page file (`507v.jpg` for `507v.md`; `.jpg`/`.jpeg`/`.png`/`.tif`/
`.tiff`/`.webp` accepted). It is required: a reviewer correcting a reading needs the
original next to it, and it keeps the benchmark self-contained. `verify_comparison.py`
checks that every page has its image.

### 1. Ask the agent

Give it the folder and fix the numbering — that numbering becomes `1.`, `2.`, … `N.`
in every output file (any number of palaeographers works — 2 or more):

> Compare the palaeographer readings in
> `benchmarks/collections/jesuit-catalogues/jesuit-cat-type4/portugal/1577/palaeographers`
> — **1 = DeepSeek-V4-FVE, 2 = M3, 3 = Qwen3.8-Max** — and produce the per-page
> comparison plus an overview.

If you don't give an order, the agent will ask you or use folder order. The skill also
triggers on plain requests such as *"compare these transcriptions"*, *"collate the
readings"*, or *"which readings disagree on this page?"*.

### 2. Get the comparison

The agent creates a `comparison/` folder inside the one you named:

- `comparison/overview.md` — what the document is, who the palaeographers are, how much
  they agree, the systematic spelling habits of each, and the most significant
  disagreements per page.
- `comparison/<page>.md` — one file per page, every entry numbered `[n]` with the three
  readings stacked (`= all readings:` when they match), and a "Key differences" list.

### 3. Review it

Open `overview.md` first to see where the readings disagree, then dip into the page
files for the exact wording. Anything one palaeographer left as `[illegible]` while
another read a name is worth a look — those are the decisions that matter.

### 4. Build the reference text (for translation)

Ask: *"now build the reference text from the comparison"*. You get `reference/<page>.md`,
one line per entry:

```
[7] P./P[adr]e Iuan/Juan Correa/correa —————————/— Ministro.
```

Read it as: the readings agree on everything except the variants separated by `/`
(here `P.` / `P[adr]e`, `Iuan` / `Juan`, `Correa` / `correa`, a long dash / a short one).
Common words appear once. This is the working text to translate from — where variants
are listed, pick the correct reading (or simply skip them if the variant does not change
the sense).

### 5. Record your own correction (human gold standard)

Copy `reference/` to a sibling folder named `human/` and edit it by hand: resolve the
variants, restore the original line breaks, and note your editing rules at the top
of each file.

**Important:** `human/` is *yours*. Agents are told to treat it as **read-only** — they
must never edit, reformat, or regenerate it. Its value is as the authoritative text,
used later to **benchmark how accurate each palaeographer was** and to **improve the
transcription prompts**.

### Adding pages or a new palaeographer later

Just add the files (same filenames in every palaeographer folder) and re-run the request.
New pages get their own files; existing ones are regenerated, so re-check anything you had
edited by hand in `comparison/` or `reference/` — `human/` is never touched.

---

## What each output folder contains

| Folder | Who writes it | What it is |
|---|---|---|
| `comparison/` | the agent | Aligned readings, `[n]` per entry, stacked `1.`…`N.`, plus `overview.md` |
| `reference/` | the agent (`make_reference.py`) | One line per entry, variants joined by `/` — for translation |
| `human/` | **you** | Hand-corrected, variant-free gold standard. Read-only for agents |

## Useful commands (optional)

The agent runs these for you; you can also run them yourself to check the output:

```bash
# force the uniform layout (one fenced block per page file) and report changes
python3 scripts/normalize_comparison.py comparison/

# check every page file is well formed (2 fences, correct sections, entries [1]..[N])
python3 scripts/verify_comparison.py comparison/

# ...and check every entry carries exactly N readings
python3 scripts/verify_comparison.py comparison/ --readings 3

# (re)build the reference text from the comparison
python3 scripts/make_reference.py comparison/            # writes reference/ next to comparison/
python3 scripts/make_reference.py comparison/ reference/ # explicit destination

# guard against a wrong reading count (refuses to write if a page has more readings)
python3 scripts/make_reference.py comparison/ --readings 3
```

Requires only Python 3 (standard library).

## Notes and gotchas

- **Uniform layout matters.** Every page file must have the same shape: title, one
  `Context:` paragraph, one `## Entry-by-entry comparison` block with all `[n]` entries in
  a *single* fenced code block, and one `## Key differences on this page` list. The
  `normalize_comparison.py` + `verify_comparison.py` pair keeps that true.
- **Readings are never "corrected"** by the skill: diacritics, `[?]`, `[illegible]`,
  bracket expansions and odd spellings are preserved exactly as the palaeographer wrote them.
- **Pages that cannot be compared** (one reads a blank leaf, another an index, a third
  show-through) are auto-detected and each full reading is kept, rather than forced into a
  misleading alignment.
- **Any number of readings works** — 2, 3, 4 or more. Pass `--readings N` so that a page
  with more readings than you expect is refused loudly instead of being silently merged
  wrongly (the reference generator is N-ary; without the flag it auto-detects the count).
- **Inside a pha archive**, work in a benchmark/working folder like the one above and never
  edit pha's generated files (`library/`, `renders/`, `archive.db`).

## Reference implementation

`palaeographers/comparison/`, `palaeographers/reference/` and `palaeographers/human/` under
the 1577 Portuguese Jesuit catalogue benchmark — 8 pages, 3 palaeographers
(DeepSeek-V4-FVE, M3, Qwen3.8-Max).

## Deploy / publish

```bash
cd ~/develop/historical-skills && ./deploy.sh   # copies to ~/.agents/skills/
```

To share: create a GitHub repo for this directory, push it, and others can install with
`npx skills add github.com/USER/palaeographers-compare`.

## License

MIT
'''

_SKILL_PALEOGRAPHERS_COMPARE_LICENSE = r'''MIT License

Copyright (c) 2026 Joaquim R. Carvalho

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''

_SKILL_PALEOGRAPHERS_COMPARE_ENTRY_FORMAT = r'''# 12r — Comparative readings (1. Alpha · 2. Beta · 3. Gamma)

Context: Folio 12 recto — start of the community roster; two columns; mild show-through from the verso.

## Entry-by-entry comparison

```
[1] 1. P. Juan Perez — confessa.
    2. P[adr]e Juan Perez — co[n]fessa
    3. P. Juan Perez — confiessa.

[2] = all three: Casos.

[3] 1. P. Pero [illegible] — leyente.
    2. P. Pero de Mora — leyente
    3. P. Pero de Mora — leyente
```

## Key differences on this page

- Entry [1]: verb forms differ (confessa / co[n]fessa / confiessa) — spelling only.
- Entry [3]: the Casos reader's surname is illegible in reading 1 but read as "de Mora" by readings 2 and 3.
'''

_SKILL_PALEOGRAPHERS_COMPARE_MAKE_REFERENCE = r'''#!/usr/bin/env python3
"""Build a reference/ folder (consolidated variant lines) from a comparison/ folder.

Works with ANY number of readings per entry (2, 3, 4, …).

For every page file in comparison/, produce reference/<page>.md where each entry
is ONE line: common words written once, differing words joined with '/' in
reading order. Token-level sequence alignment handles insertions/deletions;
trailing periods/commas and line-end hyphenation are treated as padding; em-dash
layout leaders are kept as variants, bare '/' and '-' name<->role connectors are
dropped.

Usage:
  python3 scripts/make_reference.py comparison/ [reference/] [--readings N] [--divergent PAGE ...]

  --readings N     expected number of readings per entry; the script refuses to
                   write anything if a page turns out to have MORE readings than
                   declared (silent mis-merges are the failure mode to avoid)
  --divergent PAGE page stem(s) to keep unconsolidated (repeatable). Pages whose
                   entries each carry only a single reading are detected as
                   divergent automatically.
"""
import argparse
import pathlib
import re
import sys

M1 = "## Entry-by-entry comparison"
M2 = "## Key differences on this page"

ANNOT = re.compile(r"\s*\([^)]*(?:reading|renders|is written|not transcribed|omitted)[^)]*\)")
# matches "= all three:", "= all readings:", "= all:" …
ALL_RE = re.compile(r"^=\s*all\b[^:]*:\s*(.*)$", re.I)
READ_RE = re.compile(r"^(\d+)\.\s+(.*)$")

# standalone tokens that act as name<->role connectors (padding for equality)
CONN = {"--", "-", "/"}


def canon_conn(tok):
    return "--" if tok in CONN else tok


def dehyphenate(lines):
    """Join transcription lines, resolving line-end hyphenation (- or =)."""
    out = ""
    for ln in lines:
        s = ANNOT.sub("", ln.strip())
        if not s:
            continue
        if out and out[-1] in "-=" and s[0] not in "-=":
            out = out[:-1] + s
        elif out:
            out = out + " " + s
        else:
            out = s
    return out


def parse_entries(path):
    """Parse a comparison page file.

    Returns (entries, max_reading):
      entries      -> list of (num, kind, payload)
                      kind 'all'  -> payload is the shared text (str)
                      kind 'diff' -> payload is a LIST of the readings present
      max_reading  -> highest reading index seen on the page (0 if none)

    Reading markers are recognised only in sequence (1., 2., 3., …), so a
    continuation line that happens to start with a number ("3. Nouicos.") is
    not mistaken for a new reading.
    """
    text = path.read_text()
    i = text.index(M1)
    j = text.index(M2)
    block = text[i + len(M1):j].splitlines()
    raw = []
    cur = None
    for ln in block:
        s = ln.strip()
        if s == "" or s == "```":
            continue
        m = re.match(r"^\[(\d+)\]", s)
        if m:
            if cur:
                raw.append(cur)
            cur = {"num": int(m.group(1)), "rest": s[m.end():], "lines": []}
        elif cur is not None:
            cur["lines"].append(ln)
    if cur:
        raw.append(cur)

    entries = []
    max_reading = 0
    for e in raw:
        rest = e["rest"].strip()
        m = ALL_RE.match(rest)
        if m:
            txt = dehyphenate([m.group(1)] + e["lines"])
            entries.append((e["num"], "all", txt))
            continue
        reads = {}
        cur_n = None
        expected = 1
        for ln in [rest] + e["lines"]:
            s = ln.strip()
            m = READ_RE.match(s)
            if m and int(m.group(1)) == expected:
                cur_n = int(m.group(1))
                reads[cur_n] = [m.group(2)]
                expected += 1
            elif cur_n is not None:
                reads[cur_n].append(s)
        if not reads:                       # no reading markers at all
            reads[1] = [rest] + [l.strip() for l in e["lines"]]
        idxs = sorted(reads)
        max_reading = max(max_reading, idxs[-1])
        entries.append((e["num"], "diff", [dehyphenate(reads[k]) for k in idxs]))
    return entries, max_reading


def tok_eq(a, b):
    return a.rstrip(".,") == b.rstrip(".,") or (
        a in CONN and b in CONN)


def align(a, b):
    """Token-level sequence alignment; returns list of (a_tok|None, b_tok|None)."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            c = 0 if tok_eq(a[i - 1], b[j - 1]) else 1
            dp[i][j] = min(dp[i - 1][j - 1] + c, dp[i - 1][j] + 1, dp[i][j - 1] + 1)
    i, j = n, m
    pairs = []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + (0 if tok_eq(a[i - 1], b[j - 1]) else 1):
            pairs.append((a[i - 1], b[j - 1]))
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            pairs.append((a[i - 1], None))
            i -= 1
        else:
            pairs.append((None, b[j - 1]))
            j -= 1
    pairs.reverse()
    return pairs


def merge(readings):
    """readings: list of N token lists -> consolidated string with '/' variants."""
    lists = []
    for toks in readings:
        # bare '/' and '-' are name<->role connectors (padding): drop them before
        # alignment; em-dash runs ('—', '————') are layout leaders and are kept.
        lists.append([t for t in toks if t not in ("/", "-")])
    lists = [l for l in lists if l]
    if not lists:
        return ""
    if len(lists) == 1:
        return " ".join(lists[0])

    base = lists[0]
    cols = [{0: t0, 1: t1} for t0, t1 in align(base, lists[1])]

    for idx in range(2, len(lists)):
        col = 0
        for t0, ti in align(base, lists[idx]):
            if t0 is not None:
                while col < len(cols) and (cols[col].get(0) is None or cols[col][0] != t0):
                    col += 1
                if col < len(cols):
                    cols[col][idx] = ti
                    col += 1
            else:
                nxt = col
                while nxt < len(cols) and cols[nxt].get(0) is None:
                    nxt += 1
                # merge into the preceding insertion column (same position, no token for this reading yet)
                if nxt > 0 and cols[nxt - 1].get(0) is None and cols[nxt - 1].get(idx) is None:
                    cols[nxt - 1][idx] = ti
                else:
                    cols.insert(nxt, {0: None, idx: ti})

    def norm(tok):
        return tok.rstrip(".,;:")

    parts = []
    for k in cols:
        vals = [k.get(i) for i in range(len(lists))]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        vals = [canon_conn(v) for v in vals]
        if all(norm(v) == norm(vals[0]) for v in vals):
            parts.append(vals[0])
            continue
        # first variant kept raw (keeps its punctuation, e.g. 'P.'), the
        # remaining variants are punctuation-normalised ('P[adr]e')
        uniq = [vals[0]]
        for v in vals[1:]:
            if norm(v) not in [norm(u) for u in uniq]:
                uniq.append(v)
        rendered = uniq[0]
        if len(uniq) > 1:
            rendered += "/" + "/".join(norm(u) for u in uniq[1:])
        parts.append(rendered)
    return " ".join(parts)


def reference_text(entries):
    """Render the consolidated lines for a page (str for the fenced block)."""
    lines = []
    for num, kind, payload in entries:
        if kind == "all":
            lines.append(f"[{num}] {payload}")
        else:
            lines.append(f"[{num}] {merge([t.split() for t in payload])}")
    return "\n".join(lines)


def page_meta(src_text):
    raw_title = re.match(r"^# (.+)$", src_text, re.M).group(1)
    title = "# " + raw_title.replace("Comparative readings", "Reference text with variants")
    order = re.search(r"\((.*)\)\s*$", raw_title)
    order = order.group(1) if order else "reading order"
    ctx = re.search(r"^Context: .*$", src_text, re.M)
    context = ctx.group(0) if ctx else "Context: (see comparison file)"
    return title, order, context


def write_page(src, dst, force_divergent=False):
    """Write one reference page. Returns (n_entries, n_readings_max, divergent)."""
    src_text = src.read_text()
    title, order, context = page_meta(src_text)
    entries, max_reading = parse_entries(src)
    diff_entries = [e for e in entries if e[1] == "diff"]

    # a page where every entry carries a single reading cannot be consolidated
    divergent = force_divergent or (
        bool(diff_entries) and all(len(e[2]) == 1 for e in diff_entries))

    if divergent:
        block = re.search(r"```\n(.*?)\n```", src_text, re.S).group(1)
        section = (
            "No consolidation possible — the readings for this page diverge fundamentally "
            "(see comparison/{0}). Each reading is given in full:\n\n"
            "```\n{1}\n```".format(src.name, block)
        )
        notes = [
            "- The readings for this page cannot be aligned; every reading is kept in full.",
            "- See comparison/{0} for the full discussion.".format(src.name),
        ]
        print(f"wrote {dst.name}: divergent readings kept in full")
    else:
        section = "```\n" + reference_text(entries) + "\n```"
        notes = [
            "- Convention: common words are written once; where the readings differ the variants are "
            "joined with '/' in reading order ({0}).".format(order),
            "- Trailing periods/commas and line-end hyphenation are treated as padding; the name<->role "
            "connector ('—', '/', '-') is rendered as '—'. Entries identical in all readings are given as-is.",
            "- Generated from comparison/{0} — see that file for the full stacked readings and the key "
            "differences.".format(src.name),
        ]
        print(f"wrote {dst.name}: {len(entries)} entries")

    content = (
        title + "\n\n" + context + "\n\n"
        "## Reference text (variants in slashes)\n\n"
        + section + "\n\n"
        "## Notes\n\n" + "\n".join(notes) + "\n"
    )
    dst.write_text(content)
    return len(entries), max_reading, divergent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("comparison", help="comparison folder (input)")
    ap.add_argument("reference", nargs="?", default=None,
                    help="reference folder (output; default: sibling 'reference' of the comparison folder)")
    ap.add_argument("--readings", type=int, default=None, metavar="N",
                    help="expected number of readings per entry; causes an error if a page has more")
    ap.add_argument("--divergent", action="append", default=[], metavar="PAGE",
                    help="page stem(s) to keep unconsolidated (repeatable)")
    args = ap.parse_args()

    comp = pathlib.Path(args.comparison)
    if not comp.is_dir():
        print(f"error: {comp} is not a directory", file=sys.stderr)
        return 2
    if args.readings is not None and args.readings < 2:
        print("error: --readings must be at least 2", file=sys.stderr)
        return 2

    pages = [p for p in sorted(comp.glob("*.md")) if p.name != "overview.md"]
    if not pages:
        print(f"error: no page files found in {comp}", file=sys.stderr)
        return 2

    # validate BEFORE writing anything, so a wrong --readings never produces bad output
    if args.readings is not None:
        too_many, too_few = [], []
        for p in pages:
            entries, max_reading = parse_entries(p)
            if max_reading > args.readings:
                too_many.append((p.name, max_reading))
            diffs = [e for e in entries if e[1] == "diff"]
            divergent = bool(diffs) and all(len(e[2]) == 1 for e in diffs)
            if divergent:
                continue
            for num, kind, payload in entries:
                if kind == "diff" and len(payload) < args.readings:
                    too_few.append((p.name, num, len(payload)))
                    break
        if too_many:
            print(f"error: --readings {args.readings} was declared, but these pages have MORE readings:",
                  file=sys.stderr)
            for name, mx in too_many:
                print(f"  - {name}: reading {mx} found", file=sys.stderr)
            print("  (nothing was written — fix --readings or the comparison files)", file=sys.stderr)
            return 1
        if too_few:
            print(f"warning: --readings {args.readings} declared; pages with fewer readings detected:",
                  file=sys.stderr)
            for name, num, n in too_few[:10]:
                print(f"  - {name} entry [{num}]: {n} reading(s)", file=sys.stderr)

    ref = pathlib.Path(args.reference) if args.reference else comp.parent / "reference"
    ref.mkdir(parents=True, exist_ok=True)

    divergent = set(args.divergent)
    n_div = 0
    for p in pages:
        _, _, was_divergent = write_page(p, ref / p.name, force_divergent=p.stem in divergent)
        n_div += int(was_divergent)
    print(f"\nreference folder: {ref}  ({len(pages)} pages, {n_div} kept unconsolidated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

_SKILL_PALEOGRAPHERS_COMPARE_NORMALIZE = r'''#!/usr/bin/env python3
"""Normalise the layout of palaeographers-compare output files.

For every page file (not overview.md) in a comparison folder, collapses
per-entry fenced code blocks into ONE fenced block (the uniform skeleton):
  - removes lines that are exactly ```
  - trims leading/trailing blank lines of the entry section
  - collapses runs of 2+ blank lines into one
  - wraps the whole entry section in a single pair of fences
  - restores a single blank line before the opening fence
Also collapses double blank lines in the header area and optionally drops
stray label lines (--drop-label "...").
"""
import argparse
import pathlib
import re
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", nargs="?", default="comparison",
                    help="comparison output folder (default: comparison)")
    ap.add_argument("--drop-label", action="append", default=[],
                    metavar="TEXT",
                    help="drop lines that exactly equal TEXT (repeatable)")
    args = ap.parse_args()

    folder = pathlib.Path(args.folder)
    if not folder.is_dir():
        print(f"error: {folder} is not a directory", file=sys.stderr)
        return 2

    changed = 0
    for p in sorted(folder.glob("*.md")):
        if p.name == "overview.md":
            continue
        text = p.read_text()
        original = text

        for label in args.drop_label:
            text = re.sub(rf"^\s*{re.escape(label)}\s*$\n?", "", text, flags=re.M)

        m1 = "## Entry-by-entry comparison"
        m2 = "## Key differences on this page"
        if m1 not in text or m2 not in text:
            print(f"skip  {p.name}: section markers not found")
            continue
        i = text.index(m1)
        j = text.index(m2)
        head, middle, tail = text[:i], text[i + len(m1):j], text[j:]

        lines = [ln for ln in middle.splitlines() if ln.strip() != "```"]
        while lines and lines[0].strip() == "":
            lines.pop(0)
        while lines and lines[-1].strip() == "":
            lines.pop()
        out, prev_blank = [], False
        for ln in lines:
            blank = (ln.strip() == "")
            if blank and prev_blank:
                continue
            out.append(ln)
            prev_blank = blank
        body = "\n".join(out)

        head = re.sub(r"\n{3,}(## Entry-by-entry comparison)", r"\n\n\1", head)
        new_text = head + m1 + "\n\n```\n" + body + "\n```\n\n" + tail
        if new_text != original:
            p.write_text(new_text)
            changed += 1
            n_entries = sum(1 for ln in out if ln.startswith("["))
            print(f"fixed {p.name}: {n_entries} entries in one fenced block")
        else:
            print(f"ok    {p.name}")

    print(f"\n{changed} file(s) normalised")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

_SKILL_PALEOGRAPHERS_COMPARE_VERIFY = r'''#!/usr/bin/env python3
"""Verify the uniform skeleton of palaeographers-compare output files.

Checks per page file (not overview.md):
  - exactly 2 fenced code blocks
  - exactly the two sections "## Entry-by-entry comparison" and
    "## Key differences on this page", in that order
  - a blank line before the opening fence
  - all [n] entry markers contiguous inside the single fenced block
  - entry numbers form a continuous sequence [1]..[N]
  - with --readings N: every entry carries exactly N readings (or is an
    "= all …" entry); catches silent mis-merges on corpora with a different
    number of palaeographers
  - the corpus images/ folder sits next to palaeographers/ and every
    compared page has its image there (warn by default;
    --require-images makes it a failure)
Exits non-zero if any check fails.
"""
import argparse
import pathlib
import re
import sys


def find_images_dir(folder):
    """Locate the images/ folder that should sit next to the corpus.

    The comparison folder is normally <corpus>/palaeographers/comparison, so
    images/ can be a sibling of the comparison folder, of palaeographers/, or
    of the corpus. Return the nearest existing one, or None.
    """
    for cand in (folder / "images", folder.parent / "images",
                 folder.parent.parent / "images"):
        if cand.is_dir():
            return cand
    return None


def reading_counts(middle):
    """Count the reading markers of every [n] entry in a fenced block.

    Reading markers are only recognised in sequence (1., 2., 3., …), matching
    make_reference.py, so embedded numbering ("3. Nouicos.") is not counted.
    "= all …" entries count as 0 readings.
    """
    counts = {}
    cur = None
    expected = 0
    for ln in middle.splitlines():
        s = ln.strip()
        if s == "" or s == "```":
            continue
        m = re.match(r"^\[(\d+)\]", s)
        if m:
            cur = int(m.group(1))
            counts[cur] = 0
            expected = 1
            s = s[m.end():].strip()
        if cur is None:
            continue
        mm = re.match(r"^(\d+)\.\s+", s)
        if mm and int(mm.group(1)) == expected:
            counts[cur] += 1
            expected += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", nargs="?", default="comparison")
    ap.add_argument("--readings", type=int, default=None, metavar="N",
                    help="expected number of readings per entry")
    ap.add_argument("--require-images", action="store_true",
                    help="fail when the images/ folder or a page image is missing")
    args = ap.parse_args()

    folder = pathlib.Path(args.folder)
    if not folder.is_dir():
        print(f"error: {folder} is not a directory", file=sys.stderr)
        return 2

    failures = 0
    files = [p for p in sorted(folder.glob("*.md")) if p.name != "overview.md"]

    images_dir = find_images_dir(folder)
    image_exts = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp")
    if images_dir is None:
        msg = (f"no images/ folder for the corpus (looked in {folder}/images, "
               f"{folder.parent}/images, {folder.parent.parent}/images)")
        if args.require_images:
            print(f"FAIL {msg}", file=sys.stderr)
            return 1
        print(f"WARN {msg} - copy each page image next to palaeographers/")
    else:
        print(f"images: {images_dir}")
    for p in files:
        text = p.read_text()
        problems = []

        fences = [ln for ln in text.splitlines() if ln.strip() == "```"]
        if len(fences) != 2:
            problems.append(f"expected 2 fence lines, found {len(fences)}")

        sections = re.findall(r"^## (.+)$", text, flags=re.M)
        if sections != ["Entry-by-entry comparison", "Key differences on this page"]:
            problems.append(f"sections are {sections!r}")

        if "## Entry-by-entry comparison" in text and "## Key differences on this page" in text:
            i = text.index("## Entry-by-entry comparison")
            j = text.index("## Key differences on this page")
            middle = text[i + len("## Entry-by-entry comparison"):j]

            if not middle.startswith("\n\n```"):
                problems.append("no blank line before the opening fence")
            if not middle.rstrip().endswith("```"):
                problems.append("entry block not closed by a fence")

            body = [ln for ln in middle.splitlines()
                    if ln.strip() not in ("", "```")]
            nums = []
            for ln in body:
                m = re.match(r"^\[(\d+)\]", ln.strip())
                if m:
                    nums.append(int(m.group(1)))
            if not nums:
                problems.append("no [n] entry markers found")
            elif nums != list(range(1, len(nums) + 1)):
                problems.append(f"entry numbers not continuous: {nums[:8]}...")

            if args.readings is not None:
                counts = reading_counts(middle)
                nonzero = [c for c in counts.values() if c]
                divergent = bool(nonzero) and all(c == 1 for c in nonzero)
                if divergent:
                    pass  # page kept unconsolidated (one full reading per entry)
                else:
                    bad = {n: c for n, c in counts.items()
                           if c not in (0, args.readings)}
                    if bad:
                        preview = ", ".join(f"[{n}]={c}" for n, c in sorted(bad.items())[:6])
                        problems.append(
                            f"entries whose reading count != {args.readings}: {preview}")

        if images_dir is not None and not any(
                (images_dir / (p.stem + e)).exists() for e in image_exts):
            msg = f"no image for {p.name} in {images_dir}"
            if args.require_images:
                problems.append(msg)
            else:
                print(f"warn {p.name}: {msg}")

        if problems:
            failures += 1
            print(f"FAIL {p.name}:")
            for pr in problems:
                print(f"     - {pr}")
        else:
            print(f"ok   {p.name}")

    print(f"\n{len(files) - failures}/{len(files)} files OK")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
'''

_SKILL_OBSIDIAN_VAULT = r'''---
name: obsidian-vault
description: Search, create, and manage notes in the Obsidian vault with wikilinks and index notes. Use when user wants to find, create, or organize notes in Obsidian, or when syncing archive-derived notes between the pha archive and the main Obsidian vault.
---

# Obsidian Vault

## Vault location

The vault path is machine-specific: use the `OBSIDIAN_VAULT` environment
variable (or pass `--vault <path>` to the bundled script). The examples below
write it as `$OBSIDIAN_VAULT`.

Mostly flat at root level, with a main `01 Notes/` folder for topic notes.

## Naming conventions

- **Index notes**: aggregate related topics, e.g. `Ralph Wiggum Index.md`
- **Title case** for note names: `Cosme de Torres.md`
- No folders for organisation inside `01 Notes`; use links and index notes instead.

## Linking

- Use Obsidian `[[wikilinks]]`.
- Add related notes at the bottom under `## See also`.
- If the exact note title differs, use an alias link: `[[Nicolau Lanciloto|Nicolau Lancillotto]]`.

## Archive-derived notes and sync provenance

Some vault notes are summaries or mirrors of pha archive notes living in
`<archive>/notes/`, where `<archive>` is the pha archive root (`PHA_ARCHIVE_DIR`
when set):

When creating such a vault note, record provenance in YAML frontmatter:

```yaml
archive_source: notes/cosme-de-torres.md
archive_source_sha256: <sha256 of the archive note>
archive_source_mtime: 2026-10-04T19:02:04
vault_note_created: 2026-10-04
vault_note_updated: 2026-10-04
vault_synced_at: 2026-10-04T19:50:46+08:00
sync_policy: archive-note-is-source-of-truth
```

Never invent an archive hash: compute it from the archive note at the moment
the vault note is created or updated.

### Checking for stale vault notes

Use the bundled script:

```bash
python3 <archive>/skills/obsidian-vault/scripts/archive_note_sync.py check \
    --vault "$OBSIDIAN_VAULT" --archive "<archive>"
```

It scans the vault for notes carrying `archive_source`, recomputes the source
SHA-256/mtime, and prints `UP_TO_DATE`, `STALE`, or `MISSING_SOURCE`.

When a note is genuinely reviewed and brought up to date, stamp it with:

```bash
python3 <archive>/skills/obsidian-vault/scripts/archive_note_sync.py stamp \
    --vault "$OBSIDIAN_VAULT" --archive "<archive>"
```

The script defaults to `$OBSIDIAN_VAULT` and `$PHA_ARCHIVE_DIR`, falling back
to `~/Obsidian` and the current directory; pass `--vault`/`--archive` to
override. It only edits the frontmatter keys `archive_source_sha256`,
`archive_source_mtime`, `vault_synced_at`, and `vault_note_updated`; it never
rewrites the note body. Reviewing the archive change and updating the body is a
human or agent decision.

## Workflows

### Search for notes

```bash
find "$OBSIDIAN_VAULT" -name "*.md" | grep -i "keyword"
grep -rl "keyword" "$OBSIDIAN_VAULT" --include="*.md"
```

Or use Grep/Glob tools directly on the vault path.

### Create a new note

1. Use **Title Case** for the filename.
2. Write content as a unit of learning.
3. Add `[[wikilinks]]` to related existing notes at the bottom.
4. If derived from an archive note, add the sync provenance frontmatter above.
5. Run the sync checker and, if desired, `stamp`.

### Find related notes

```bash
grep -rl "\\[\\[Note Title\\]\\]" "$OBSIDIAN_VAULT"
```

### Find index notes

```bash
find "$OBSIDIAN_VAULT" -name "*Index*"
```
'''

_SKILL_OBSIDIAN_VAULT_ARCHIVE_NOTE_SYNC = r'''from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import pathlib
import re
import sys

DEFAULT_VAULT = pathlib.Path(
    os.environ.get('OBSIDIAN_VAULT') or (pathlib.Path.home() / 'Obsidian')
).expanduser()
DEFAULT_ARCHIVE = pathlib.Path(
    os.environ.get('PHA_ARCHIVE_DIR') or pathlib.Path.cwd()
).expanduser()
FENCE = '---'


def read_frontmatter(path):
    lines = path.read_text(encoding='utf-8').splitlines()
    if not lines or lines[0].strip() != FENCE:
        return None, lines
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == FENCE:
            end = i
            break
    if end is None:
        return None, lines
    fm = {}
    for line in lines[1:end]:
        m = re.match(r'^([A-Za-z0-9_\-]+):\s*(.*)$', line)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm, lines


def write_frontmatter(path, lines, updates):
    if not lines or lines[0].strip() != FENCE:
        raise SystemExit(str(path) + ': no YAML frontmatter; refusing to stamp')
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == FENCE:
            end = i
            break
    if end is None:
        raise SystemExit(str(path) + ': unterminated YAML frontmatter')
    out = list(lines)
    for key, value in updates.items():
        pat = re.compile(r'^' + re.escape(key) + r':\s*')
        for i in range(1, end):
            if pat.match(out[i]):
                out[i] = key + ': ' + value
                break
        else:
            out.insert(end, key + ': ' + value)
            end += 1
    path.write_text('\n'.join(out) + '\n', encoding='utf-8')


def sha256_file(path):
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def mtime_iso(path):
    return dt.datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0).isoformat()


def iter_archive_notes(vault):
    for path in sorted(vault.rglob('*.md')):
        fm, _lines = read_frontmatter(path)
        if fm and fm.get('archive_source'):
            yield path, fm


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['check', 'stamp'])
    parser.add_argument('--vault', type=pathlib.Path, default=DEFAULT_VAULT,
                        help='Obsidian vault (default: $OBSIDIAN_VAULT or ~/Obsidian)')
    parser.add_argument('--archive', type=pathlib.Path, default=DEFAULT_ARCHIVE,
                        help='pha archive root (default: $PHA_ARCHIVE_DIR or cwd)')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()

    changed = 0
    checked = 0
    for note, fm in iter_archive_notes(args.vault):
        checked += 1
        src = args.archive / fm['archive_source']
        if not src.exists():
            print('MISSING_SOURCE\t' + str(note) + '\t' + fm['archive_source'])
            changed += 1
            continue
        current_sha = sha256_file(src)
        current_mtime = mtime_iso(src)
        recorded_sha = fm.get('archive_source_sha256', '')
        status = 'UP_TO_DATE' if recorded_sha == current_sha else 'STALE'
        if status == 'STALE':
            changed += 1
        if args.command == 'check':
            print(status + '\t' + str(note) + '\t' + fm['archive_source'])
        else:
            _, lines = read_frontmatter(note)
            updates = {
                'archive_source_sha256': current_sha,
                'archive_source_mtime': current_mtime,
                'vault_synced_at': dt.datetime.now().replace(microsecond=0).isoformat(),
                'vault_note_updated': dt.date.today().isoformat(),
            }
            write_frontmatter(note, lines, updates)
            print('STAMPED\t' + str(note) + '\t' + fm['archive_source'])
        if args.verbose:
            shown = recorded_sha[:12] if recorded_sha else 'missing'
            print('  recorded=' + shown + ' current=' + current_sha[:12] + ' mtime=' + current_mtime)
    if args.command == 'check':
        print('checked=' + str(checked) + ' stale_or_missing=' + str(changed))
    return 0


if __name__ == '__main__':
    sys.exit(main())
'''

_SKILL_TIMELINK_KLEIO_PROVENANCE = r'''---
name: timelink-kleio-provenance
description: Use when citing facts that come from Timelink/Dehergne prosopography (entities such as `deh-*`, `bio-*`, `ivc-*`, `manuel-*`) or when a note needs a link to the original Kleio `.cli`/`.kleio` source file and line. Covers where the Timelink SQLite databases live, the `attributes → entities → sources` join that yields the Kleio file path and line number, how to turn that into a `vscode://file/<absolute-path>:<line>` link, and the caveats about line drift, uncertainty markers and file-level bibliographies.
---

# Timelink / Kleio provenance and citations

## What this is for

Timelink prosopographies (the Dehergne *Répertoire des Jésuites de Chine*, the
China–Coimbra project, etc.) are built from **Kleio source files** (`.cli` /
`.kleio`). Those files are imported into a SQLite database, where each fact is
an **attribute** row. The database keeps enough information to point back to
the exact **file and line** of the original fact. This skill says how to find
that file and line, and how to render it as a clickable link (VS Code
`vscode://file/…:line`).

Never cite the Obsidian `deh-*.md` export as if it were the source. Go to the
SQLite row and from there to the Kleio file + line.

## Where the data lives

The Timelink home is `$MHK_HOME` (the helper reads `MHK_HOME`, then `MHK`,
defaulting to `~/mhk-home`; check `.mhk-home`, `.kleio.json`). Useful entry
points:

| path | what it is |
| --- | --- |
| `mhk-home/.db_status` | database schemas and row counts (`dehergne`, `china_coimbra`, `mhk`) |
| `mhk-home/sources/<project>/database/sqlite/<project>.sqlite` | read-only SQLite backup of the project's database (e.g. `sources/dehergne/database/sqlite/dehergne.sqlite`, `sources/china-coimbra/database/sqlite/china_coimbra.sqlite`) |
| `mhk-home/sources/<project>/sources/*.cli` | the Kleio source files (e.g. `sources/dehergne/sources/dehergne-t.cli`) |
| `mhk-home/system/db/mhk/imports/<project>/` | import folders per project |
| `mhk-home/sources/<project>/inferences/` | derived reports (e.g. the Coimbra lists) |

The live services (Kleio at `http://localhost:8088`, MySQL at `:3306`) may not
be running. The SQLite backups are read-only and sufficient. Do **not** modify
them.

## Schema (the bits that matter)

- `attributes`: `id` (the **attr_id**), `entity` (the person id), `the_type`,
  `the_value`, `the_date`, `obs`.
- `entities`: `id`, `class`, `inside`, `the_source`, `the_order`, `the_level`,
  `the_line`, `groupname`, `extra_info`.
- `sources`: `id`, `the_type`, `the_date`, `loc`, `ref`, `kleiofile`,
  `replaces`, `obs`.

Key model facts:

- Every attribute is itself an entity: **`attributes.id = entities.id`**; the
  person it belongs to is `attributes.entity`.
- The attribute entity's **`the_source`** is the source id; **`the_line`** is
  the line for the attribute in the Kleio file.
- **`sources.kleiofile`** is the path of the Kleio file relative to
  *kleio-home*; **`sources.obs`** is the bibliographic description of the
  source (file-level, not necessarily person-level).

## The query

```sql
SELECT a.id AS attr_id, a.entity AS entity_id, a.the_type, a.the_value, a.the_date,
       e.the_line, e.the_source, s.kleiofile, s.obs AS source_desc
FROM attributes a
JOIN entities e ON e.id = a.id
LEFT JOIN sources s ON s.id = e.the_source
WHERE a.entity = ? AND a.the_type LIKE ? AND a.the_value LIKE ?;
```

Worked example:

- `attributes` row: `id = deh-antoine-thomas-att430-124`,
  `entity = deh-antoine-thomas`, `the_type = estadia-x`,
  `the_value = Coimbra`, `the_date = 1678000`.
- `entities` row for that attr_id: `the_line = 670`,
  `the_source = dehergne-t`.
- `sources` row `dehergne-t`: `kleiofile = /kleio-home/sources/dehergne-t.cli`,
  `obs = Dehergne, … Répertoire…, 1973. Letra T…`.
- Result to cite: **`sources/dehergne-t.cli`, line 670**.

## Kleio-home and the local file

*Kleio-home* is the directory that contains the `database` directory of the
SQLite database. For
`$MHK_HOME/sources/dehergne/database/sqlite/dehergne.sqlite`,
kleio-home is `$MHK_HOME/sources/dehergne`; for
`…/sources/china-coimbra/database/sqlite/china_coimbra.sqlite`, it is
`$MHK_HOME/sources/china-coimbra`.

The `kleiofile` value keeps the original container prefix `/kleio-home/…`. To
get the local file:

1. strip `/kleio-home/` from `kleiofile` (e.g. `sources/dehergne-t.cli`, or
   `sources/china-coimbra-biografias/sources/coimbra-visitantes.cli`);
2. locate the file under `mhk-home/sources/**/sources/` by basename, e.g.
   - `sources/dehergne-t.cli` →
     `$MHK_HOME/sources/dehergne/sources/dehergne-t.cli`;
   - `sources/china-coimbra-biografias/sources/coimbra-visitantes.cli` →
     `$MHK_HOME/sources/china-coimbra-biografias/sources/coimbra-visitantes.cli`.

**Verify the local file exists before writing a link.**

## The citation and the link

Cite the `attr_id`, the Kleio file, the line, the source id and `sources.obs`.
VS Code link format:

```
vscode://file/<absolute-path>:<line>
```

The Markdown form (label = relative path + line):

```markdown
[sources/dehergne-t.cli:670](vscode://file/<absolute-path-to-kleio-home>/sources/dehergne-t.cli:670)
```

- No extra slash after `file/`: `vscode://file/<absolute-path>:<line>`, not
  `vscode://file//<absolute-path>:<line>`.
- Paths with spaces must be percent-encoded (`%20`).
- Alternative from a terminal: `code -g <absolute-path>:<line>`.
- Obsidian may or may not make custom URI schemes clickable; always keep the
  plain path + line and the `attr_id` visible as a fallback.

## Caveats

- **Line drift:** `entities.the_line` is the line recorded at import time. If
  the `.cli` has been edited since, the line may have shifted. The `attr_id`
  disambiguates; if a link lands on the wrong line, give the `attr_id` and/or
  search the file for the `the_type`/`the_value` token. Example: the DB
  records line 670 for `deh-antoine-thomas-att430-124`, while the current file
  may have the `ls$estadia-x/Coimbra` line at a different position.
- **Uncertainty:** `%?` in the Kleio line (and often the Timelink record)
  flags the fact as uncertain; keep the `(?)` in the note.
- **File-level bibliography:** `sources.obs` describes the whole source file
  (e.g. "Dehergne, … Letra T"), not the individual person.
- **Multiple projects:** search both `dehergne.sqlite` (entities `deh-*`) and
  `china_coimbra.sqlite` (entities `bio-*`, `ivc-*`, `duarte-*`,
  `manuel-*`, …); a person can appear in both with different ids.

## Common searches

Find every attribute of a person that mentions Coimbra:

```sql
SELECT a.id, a.the_type, a.the_value, a.the_date
FROM attributes a
WHERE a.entity = 'deh-antoine-thomas' AND a.the_value LIKE '%Coimbra%';
```

Find everyone with an `estadia` in Coimbra (not only `jesuita-entrada`):

```sql
SELECT a.id, a.entity, a.the_type, a.the_value, a.the_date
FROM attributes a
WHERE a.the_type LIKE 'estadia%' AND a.the_value LIKE '%Coimbra%';
```

Then run the provenance query above on each `a.id`/`a.entity`.

## Helper

`timelink_provenance.py` in this skill folder (`<archive>/skills/timelink-kleio-provenance/`
when seeded into an archive) takes a database or project name and an entity id,
runs the join and prints the citation and the VS Code link. It reads `MHK_HOME`
(or `MHK`, defaulting to `~/mhk-home`), and `--db` also accepts a `.sqlite` path.
See its `--help`.
'''

_SKILL_TIMELINK_KLEIO_PROVENANCE_HELPER = """#!/usr/bin/env python3
\"\"\"Timelink/Kleio provenance helper.

Set MHK_HOME (or MHK) to the Timelink home, or pass a .sqlite path as --db.

Usage:
  python3 timelink_provenance.py --db dehergne --entity deh-antoine-thomas
  python3 timelink_provenance.py --db china_coimbra --entity duarte-de-sande --value Coimbra
  python3 timelink_provenance.py --db /path/to/dehergne.sqlite --entity deh-adriano-pestana --json

For each matching attribute it prints the attr_id, the Kleio file and line
(from entities.the_line / sources.kleiofile), and a vscode://file/<abs>:<line>
link. Read-only: it never writes to the database or the Kleio files.
\"\"\"
import argparse, glob, json, os, sqlite3, sys

MHK = os.path.expanduser(
    os.environ.get('MHK_HOME') or os.environ.get('MHK') or '~/mhk-home'
)

def resolve_db(name):
    if os.path.isfile(name):
        return os.path.abspath(name)
    pats = [os.path.join(MHK, 'sources', '*', 'database', 'sqlite', name + '.sqlite'),
            os.path.join(MHK, 'sources', '*', 'database', 'sqlite', name),
            os.path.join(MHK, '**', name + '.sqlite')]
    for p in pats:
        hits = sorted(glob.glob(p, recursive=True))
        if hits:
            return hits[0]
    sys.exit('database not found: ' + name + ' (set MHK_HOME or pass a .sqlite path)')

def kleio_home(db):
    # directory containing the 'database' directory of the sqlite db
    d = os.path.dirname(os.path.abspath(db))
    while d and d != '/':
        if os.path.basename(d) == 'database':
            return os.path.dirname(d)
        d = os.path.dirname(d)
    return os.path.dirname(os.path.abspath(db))

def local_file(kleiofile):
    rel = (kleiofile or '').replace('/kleio-home/', '').lstrip('/')
    base = os.path.basename(rel)
    hits = [h for h in glob.glob(os.path.join(MHK, 'sources', '**', base), recursive=True)
            if os.sep + 'sources' + os.sep in h]
    hits.sort(key=len)
    if hits:
        return hits[0]
    # fallback: try the relative path under mhk-home
    cand = os.path.join(MHK, rel)
    return cand if os.path.exists(cand) else ''

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True, help='project name (dehergne, china_coimbra) or path to .sqlite')
    ap.add_argument('--entity', required=True, help='entity id, e.g. deh-antoine-thomas')
    ap.add_argument('--type', default='%', help='attribute type (SQL LIKE), default all')
    ap.add_argument('--value', default='%', help='attribute value (SQL LIKE), default all')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    db = resolve_db(args.db)
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    q = '''SELECT a.id AS attr_id, a.entity AS entity_id, a.the_type, a.the_value, a.the_date,
                  e.the_line, e.the_source, s.kleiofile, s.obs AS source_desc
           FROM attributes a
           JOIN entities e ON e.id = a.id
           LEFT JOIN sources s ON s.id = e.the_source
           WHERE a.entity = ? AND a.the_type LIKE ? AND a.the_value LIKE ?'''
    rows = [dict(r) for r in con.execute(q, (args.entity, args.type, args.value))]
    con.close()
    out = []
    for r in rows:
        lf = local_file(r['kleiofile'])
        line = r['the_line']
        r['local_file'] = lf
        r['vscode'] = ('vscode://file' + lf + ':' + str(line)) if lf else ''
        rel = (r['kleiofile'] or '').replace('/kleio-home/', '')
        r['cite'] = rel + ':' + str(line)
        out.append(r)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    if not out:
        print('no attributes for', args.entity)
        return
    for r in out:
        print('entity :', r['entity_id'])
        print('attr_id:', r['attr_id'])
        print('type   :', r['the_type'], '=', r['the_value'], '| date:', r['the_date'])
        print('cite   :', r['cite'])
        print('file   :', r['local_file'] or r['kleiofile'])
        print('link   :', r['vscode'])
        print('source :', r['the_source'])
        print('desc   :', (r['source_desc'] or '').strip()[:300])
        print('-' * 70)

if __name__ == '__main__':
    main()
"""

# The skills pha ships, as (folder_name, SKILL.md body). Embedded
# so seeding works with no source checkout; the repo's skills/ folder is the
# authored copy and a test keeps the two identical.
SKILLS: tuple[tuple[str, str], ...] = (
    ("obsidian-vault", _SKILL_OBSIDIAN_VAULT),
    ("palaeographers-compare", _SKILL_PALEOGRAPHERS_COMPARE),
    ("pha-document-operations", _SKILL_DOCUMENT_OPERATIONS),
    ("pha-search-context", _SKILL_SEARCH_CONTEXT),
    ("pha-zotero-bibliography", _SKILL_ZOTERO_BIBLIOGRAPHY),
    ("timelink-kleio-provenance", _SKILL_TIMELINK_KLEIO_PROVENANCE),
)

# Extra files that travel with a skill (helper scripts, examples, licence, its
# own README), as (folder_name, ((path relative to the skill folder, text),
# ...)). SKILL.md is implicit and always first; a skill with no entry here is
# just its SKILL.md.
SKILL_FILES: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "obsidian-vault",
        (
            ("scripts/archive_note_sync.py", _SKILL_OBSIDIAN_VAULT_ARCHIVE_NOTE_SYNC),
        ),
    ),
    (
        "palaeographers-compare",
        (
            ("README.md", _SKILL_PALEOGRAPHERS_COMPARE_README),
            ("LICENSE", _SKILL_PALEOGRAPHERS_COMPARE_LICENSE),
            ("examples/entry-format.md", _SKILL_PALEOGRAPHERS_COMPARE_ENTRY_FORMAT),
            ("scripts/make_reference.py", _SKILL_PALEOGRAPHERS_COMPARE_MAKE_REFERENCE),
            ("scripts/normalize_comparison.py", _SKILL_PALEOGRAPHERS_COMPARE_NORMALIZE),
            ("scripts/verify_comparison.py", _SKILL_PALEOGRAPHERS_COMPARE_VERIFY),
        ),
    ),
    (
        "timelink-kleio-provenance",
        (
            ("timelink_provenance.py", _SKILL_TIMELINK_KLEIO_PROVENANCE_HELPER),
        ),
    ),
)


def bundled_skills() -> tuple[tuple[str, str], ...]:
    """The skills pha ships, as (folder_name, SKILL.md text)."""
    return SKILLS


def bundled_skill_files() -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    """Every file of every bundled skill, as
    (folder_name, ((path relative to the skill folder, text), ...)).

    `SKILL.md` is always first, followed by the files in `SKILL_FILES`. The
    order is deterministic (skills follow `SKILLS`), so seeding is reproducible.
    """
    extras = dict(SKILL_FILES)
    return tuple(
        (name, (("SKILL.md", body),) + extras.get(name, ()))
        for name, body in bundled_skills()
    )


def seed_archive_skills(archive_dir: str | Path) -> list[str]:
    """Create `<archive>/skills/` and seed it: the README plus every file of
    every bundled skill, **only when missing**.

    An existing file is never overwritten, so a user's edits survive every
    later pha run. A bundled file that is missing (including a whole skill
    added in a newer pha version) is (re)created, which is how an updated
    install reaches an existing archive. Returns the archive-relative paths it
    created, e.g. ['README.md', 'pha-search-context/SKILL.md'].
    """
    root = Path(archive_dir) / "skills"
    root.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(SKILLS_README_MD, encoding="utf-8")
        created.append("README.md")
    for name, files in bundled_skill_files():
        for relative_path, body in files:
            target = root / name / relative_path
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
            created.append(f"{name}/{relative_path}")
    return created
