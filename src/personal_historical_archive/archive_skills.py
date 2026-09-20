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

The skill bodies are **embedded in this package** (like `builtin_samples()`),
not read from the source tree: the machine that owns the archive may have pha
installed with no access to the pha repository — which is exactly the situation
these skills exist for. The repo's `skills/<name>/SKILL.md` files are the
authored copies; `tests/test_archive_skills.py` asserts the embedded constants match
them byte for byte, so the two cannot drift.

Seeding is **once** — like `notes/README.md`, and unlike README.md/AGENTS.md
(which are marker-stamped and refreshed): a skill file is never overwritten,
because the archive owner is expected to edit, add or remove them.

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

SKILLS_README_MD = """# Skills

This folder holds **pha-specific agent skills** — short, self-contained
instruction files that tell an AI agent how to operate *this* archive without
reading the pha source code. They are the archive's own copy, seeded here when
the archive was created, so an agent that was pointed at this directory alone
can find them (no pha checkout, no network).

Each skill is one folder with a `SKILL.md` inside:

    skills/<name>/SKILL.md

The file starts with YAML front matter carrying a `name` (which **must match
the folder name**) and a `description` (the trigger — when an agent should
reach for it), followed by the instructions:

    ---
    name: pha-search-context
    description: Use when an agent has run `pha search` and must answer ...
    ---

    # pha Search Context
    ...

## How an agent should use these

**Using the archive:** before the matching task, read
`skills/<name>/SKILL.md` and follow it. The three seeded here cover the most
common mistakes:

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

**Installing them into an agent runtime:** some runtimes (DeepSeek Harness and
other tools that read the shared agent-skills convention) discover skills from
a user-level directory instead of the archive. Copy the folder there:

    cp -R skills/pha-search-context ~/.agents/skills/

Keep the folder names unchanged — a skill's front-matter `name` must match its
folder name.

## Editing and updating

`pha` seeds this folder **once** and never overwrites it: edit these files
freely, delete the ones you do not want, and add your own (any folder holding a
`SKILL.md` conforming to the format above). To pick up a newer version shipped
with pha, copy it from the `skills/` folder of the pha source repository, or
from a newly created archive of the same pha version — pha will not silently
replace your edits.
"""

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

# The pha-specific skills pha ships, as (folder_name, SKILL.md body). Embedded
# so seeding works with no source checkout; the repo's skills/ folder is the
# authored copy and a test keeps the two identical.
SKILLS: tuple[tuple[str, str], ...] = (
    ("pha-document-operations", _SKILL_DOCUMENT_OPERATIONS),
    ("pha-search-context", _SKILL_SEARCH_CONTEXT),
    ("pha-zotero-bibliography", _SKILL_ZOTERO_BIBLIOGRAPHY),
)


def bundled_skills() -> tuple[tuple[str, str], ...]:
    """The pha-specific skills pha ships, as (folder_name, SKILL.md text)."""
    return SKILLS


def seed_archive_skills(archive_dir: str | Path) -> list[str]:
    """Create `<archive>/skills/` and seed it: the README plus every bundled
    pha-specific skill (each `<name>/SKILL.md`), **only when missing**.

    Never overwrites an existing file, so a user's edits and additions survive
    every later pha run. Returns the archive-relative paths it created, e.g.
    ['README.md', 'pha-search-context/SKILL.md'], for callers that report it.
    """
    root = Path(archive_dir) / "skills"
    root.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(SKILLS_README_MD, encoding="utf-8")
        created.append("README.md")
    for name, body in bundled_skills():
        target = root / name / "SKILL.md"
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        created.append(f"{name}/SKILL.md")
    return created
