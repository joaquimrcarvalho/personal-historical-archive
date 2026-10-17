---
name: pha-search-context
description: Answer from personal-historical-archive (pha) search results — the archive's memory. Use when an agent has run `pha search` or the `pha_search` MCP tool and must answer from the hits, when the user asks "what do I have about X", or when the user asks for the full transcription / edited version behind a search snippet. First check whether the archive already has notes on the question (`kind: "note"` hits) and offer to read them — notes are earlier conclusions, not evidence. Then number the results; before quoting or answering from a page hit, retrieve the complete text of that page (raw transcription and the edited variant when one exists); and when a hit is a page of a multi-page document that appears to have started on an earlier page, pull the earlier pages (back to the document/record start) so the answer has real context, never a bare snippet.
---

# pha Search Context

## Before you start: talk to a historian

You are working inside an archive, so the person asking is most likely a
**historian, not a programmer**. Present what you found in plain language, say
which reading you are quoting (the faithful transcription or the edited text),
and skip the jargon. If a step needs anything beyond the archive — installing
software, a password, files elsewhere on the machine — explain in simple words
what you need and what it will change before asking for it.

pha search covers two different kinds of thing, and confusing them is the
worst mistake an agent can make here:

- **pages** — snippets of the *sources*: a hit points at `(document_id,
  page_no)` plus a `variant` (`raw` transcription or `edited`).
- **notes** — the *researcher's own conclusions* about those sources: an
  identification worked out last month, a rejected hypothesis, a draft
  narrative. A note hit has `kind: "note"`, a `note` name, a `title` and a
  `path`; it is **memory, not evidence**.

The notes matter because they are the cumulative part of the archive: a
question asked today should profit from the answer worked out in March. So a
search is never "just" a page lookup — before answering, check what the
archive already concluded, and say so. But a note can be outdated, partial or
wrong, so never present one as a source.

Agents repeatedly make three mistakes:

0. Answering "what do I have about X" without noticing that the archive
   already contains a note about X — and so re-deriving a conclusion that was
   settled months ago, or contradicting it silently.

1. Answering from the snippet alone, when the passage is mid-document and its
   meaning depends on text from earlier pages (a letter, a register entry, a
   catalogue row begun on a previous folio).
2. Not offering / fetching the full page or full document the hit comes from,
   in the user's preferred variant (faithful transcription vs the
   edited/modernised/translated text).

This skill fixes all three: **check the notes first, present page hits
numbered, then always recover full context before answering.**

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

### 0. Check the archive's memory: the notes

Before (and after) looking at pages, find out what the archive already
concluded about the question. This is the step that turns a search into a
conversation with the researcher instead of a re-derivation of their past work.

- A normal `pha search "…"` already includes the notes: `--source all` (the
  default) searches pages + encoder records + `notes/`. Note hits are
  marked `kind: "note"` in `--json` (MCP: `pha_search(..., source="notes")`).
- If the search was run with `--source pages` or `--source records`, or the
  user's question is conceptual ("what do I have about Timor and Sunda?",
  "did I ever look into X?"), run a notes-only search as well:

  ```
  pha search "<the question's key terms>" --source notes
  ```

- **When there are note hits, say so and ask before using them.** Present them
  first, as their own list, and put the question to the user:

  ```
  The archive also has notes on this:
    1. "zumbae-tchamp-pa-identification" — Zumbae (Tchamp-pa) = Sumba
    2. "taprobana" — Taprobana = Ceylon?
  Would you like me to read them too, or answer from the sources alone?
  ```

  Do not bury the offer in a footnote, and do not silently incorporate a
  note's conclusion into an answer the user asked for from the documents.
- **Rank what you offer.** A hit score in `pha search` reflects both *the
  query's words appearing in the note* and *meaning-only similarity*. Notes
  whose text actually contains the query's terms are the strong candidates;
  anything considerably lower scored matched only by meaning and may be
  loosely related — offer the top two or three and say which are loose, rather
  than presenting five unrelated notes as "the answer".
- If the user says yes (or the question is clearly "what have I got on X",
  where their own earlier conclusions are the point), read the note in full:
  - M: the note hit's `text` is the whole note (20000-char cap, `text_truncated`
    when it bites) — read `path` for the rest.
  - C: `pha notes show <name>`, or read `notes/<name>.md`.
- **A note is a prior conclusion, not evidence.** Report it as such ("in a note
  from 2026 you concluded that Zumbae = Sumba, on the strength of Schütte …
  p. 1089"), keep its page citations separate from your own, and when the
  answer matters, verify the claim against the page it cites. A note may be
  wrong, may predate a better reading, or may record a hypothesis you should
  re-test — never present it as if the source said it.
- If there are no note hits, do not invent an "I also checked the notes" line;
  just answer from the sources.

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
- Present the **page** hits as their own numbered list, separate from the note
  hits of step 0: the user must be able to tell "what the source says" from
  "what I concluded about it". The two lists are numbered independently, and
  every later reference says which list it means.

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
| Notes already written on the subject | `pha_search(source="notes")` → `kind: "note"` hits | `pha search "…" --source notes` |
| Read one note in full | the note hit's `text` (cap 20000), else `path` | `pha notes show <name>`, or `notes/<name>.md` |
| What notes exist at all | `pha_search(source="notes")` with a broad term | `pha notes` (list) |
| Whole document | `pha_get_document(id, max_chars)` (raw; edited per page) | read `library/…/<stem>_<date>/` files; `pha page` per page |
| Document metadata / editor | `pha_list_documents` | `pha status`, `pha editor DOC` |
| Is an edited version available? | `edited` non-empty in `pha_get_page` | `pha page DOC P --edited` succeeds; `edited-*/` folder exists |
| Page count / document size | `pha_get_document` → `pages`; `pha_list_documents` | page files in the date folder |

## When not to use this skill

- Local `pha test` runs that never touch the archive DB (scratch output only).
- Work purely on raw source images / renders without any search step.
- If the user is only configuring models/editors/encoders and not searching.

## Checklist

- [ ] The notes were searched for the subject, and any note hits were
      **offered to the user** before being folded into an answer.
- [ ] A note that was used is reported as an earlier conclusion, kept separate
      from the source evidence, and verified against the page where it matters.
- [ ] Search results presented **numbered**, with document, page, variant.
- [ ] Twin hits (raw + edited of the same page) deduped / explained.
- [ ] No answer given from a bare snippet without offering the full page.
- [ ] Full page fetched (raw) before quoting; edited version fetched/offered
      when the page has one.
- [ ] Whole document offered when the hit is one page of a longer item.
- [ ] Mid-document hits: preceding pages read back to the document/item start
      and the context pull reported.
- [ ] Every claim cites document id, page, and variant (raw vs edited).
