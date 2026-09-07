---
name: pha-search-context
description: Recover the full-document context of archive search results in personal-historical-archive (pha). Use when an agent has run `pha search` or the `pha_search` MCP tool and must answer from the hits, or when the user asks for the full transcription / edited version behind a search snippet. Number the search results; before quoting or answering from a hit, retrieve the complete text of that page (raw transcription and the edited variant when one exists); and when a hit is a page of a multi-page document that appears to have started on an earlier page, pull the earlier pages (back to the document/record start) so the answer has real context, never a bare snippet.
---

# pha Search Context

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
