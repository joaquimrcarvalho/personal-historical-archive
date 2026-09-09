# Notes
This folder stores **Markdown notes generated from queries to this archive** —
human- or agent-written research notes that summarize what the archive holds on
a topic. Notes live in the archive itself (a sibling of `dropbox/`, `library/`,
etc.) and are **Obsidian compatible**: use `[[wikilinks]]` to connect notes and
`[^1]`-style footnotes for citations.

## Why a notes folder?

`pha search` returns snippets and `pha page` returns a full page; a note is the
step after that. It pulls together hits across several documents/pages into one
human-readable summary with links and provenance, so the next query — or the
next person — does not have to re-search from scratch.

## Note format (Obsidian compatible)

- **Filename**: `lowercase-hyphenated.md` (e.g. `malaca.md`). One note per
  topic; keep it focused.
- **Wikilinks** `[[Note Name]]` link between notes. Link to a note that does
  not exist yet and Obsidian will offer to create it; create it when it is a
  real topic.
- **Footnotes** use standard Markdown/Obsidian footnote syntax:

      The port was fortified in 1547.[^1]

      [^1]: *Historians' description of Malaca*, DocHistMissPadPortOriente vol02, doc 20, p. 187.

  The next footnote is `[^2]`, the next `[^3]`, and so on; the definition
  block (each `[^n]: ...` line) sits at the end of the file.
- **Front matter** (recommended) is a YAML block at the very top, delimited by
  `---` lines:

      ---
      title: Malaca
      created: 2026-09-08
      tags: [portugal, malaca, 16c]
      sources: ["DocHistMissPadPortOriente", "Documenta Indica"]
      ---

- Use ordinary Markdown for headings, tables, quotes and inline code. Keep the
  Obsidian-specific syntax to wikilinks + footnotes so the files also render in
  any Markdown viewer.

## How to cite an archive source

A citation names an archive **document + page** so the exact text can be opened.
It is a pointer, not a hyperlink — the agent handling the note resolves it when
asked (e.g. "show the page referred to in footnote 12").

- `pha search "Malaca"` → hits carry a `page_file` and a page number.
- `pha page <doc> <page>` prints that page's full transcription;
  `pha page <doc> <page> --edited` prints the edited/translated variant.
- `<doc>` is a document id or a filename substring, so both
  `pha page 19 379` and `pha page DOCUMENTA-INDICA 379` work.

Cite as a footnote, e.g.:

    Malaca's Jewish community is described in the 1548–50 register.[^1]

    [^1]: *DocHist do Padroado do Oriente* vol04 (doc 22), p. 437, edited —
          `pha page 22 437 --edited`.

The footnote carries enough to open the page: the document (doc 22) and the
page (437). An agent asked to "show the page referred to in footnote 12" runs
`pha page 22 437 --edited` and reports the text. Keep the footnote as this plain
pointer — don't embed a file path or a wikilink, because library paths carry
the version date and go stale when a document is re-processed.

## How an agent should create a note

1. **Search** the archive for the topic: `pha search "Malaca"` (or the
   `pha_search` MCP tool).
2. **Read each hit in full** — run `pha page <doc> <page>` (and `--edited` when
   available) to get the complete page text. Do not summarize from a snippet
   alone.
3. **Synthesize** into a single note in THIS folder:
   - State only what the archive supports; say what it does *not* contain.
   - Cite each fact to its document + page in a footnote.
   - Link to other notes with `[[wikilinks]]`; create a new note when another
     topic deserves its own page.
4. **Save it** here as `lowercase-hyphenated.md` (create any linked-topic note
   too), and make sure every footnote `[^n]` has a matching definition.
5. Re-check that no citation points at the wrong page and that all footnotes
   resolve.

### Recording the ask

Optional: put the prompt/query that produced the note in an HTML comment at the
top, so a later reader knows what question it answers:

```
<!-- Prompt: search the archive for "Malaca" and summarize available
     information in a new note in this archive. -->
```

## Example prompt

> Search the archive for "Malaca" and summarize available information in a new
> note in this archive.

The agent answers by writing e.g. `malaca.md` in this folder: the archive to be
searched, the documents/pages it found, a synthesis of what they say, a
footnote per page cited, and `[[wikilinks]]` to any related notes (e.g.
`[[portugal-in-asia]]`, `[[malacca-fortress]]`).
