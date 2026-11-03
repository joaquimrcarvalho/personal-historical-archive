# Notes
This folder stores **Markdown notes generated from queries to this archive** —
human- or agent-written research notes that summarize what the archive holds on
a topic. Notes live in the archive itself (a sibling of `dropbox/`, `library/`,
etc.) and are **Obsidian compatible**: use `[[wikilinks]]` to connect notes and
`[^1]`-style footnotes for citations.

For the wider picture — embedding a page image in a note, and letting an agent
read or write a personal Obsidian vault — see `obsidian-integration.md` in this
folder.

## Why a notes folder?

`pha search` returns snippets and `pha page` returns a full page; a note is the
step after that. It pulls together hits across several documents/pages into one
human-readable summary with links and provenance, so the next query — or the
next person — does not have to re-search from scratch.

## Notes are searchable (they are the archive's memory)

A note is not just a document you might open later: pha **indexes this folder**
so a future search can find it. That is the point — the conclusion you wrote
after three days in the catalogue comes back when you ask about the subject
again, instead of being re-derived.

- `pha search "…"` (the default `--source all`) returns your page hits **and**
  your notes on the subject. A note hit is marked `kind: "note"` and carries
  the note's name, title and path — never a document/page number, because a
  note is a conclusion, not a source.
- `pha search "…" --source notes` searches the notes alone: *"what have I
  already concluded about this?"*
- Freshness is automatic: a search indexes whatever notes changed since last
  time (only the changed ones). `pha notes index` forces the pass,
  `pha notes show <name>` prints one note, `pha status` reports how much of
  this folder is searchable.
- Keep one note per subject with a **stable, descriptive file name**
  (`zumbae-tchamp-pa-identification.md`, `taprobana.md`): the file stem is the
  note's identity in search and in the view. `README.md` and files starting
  with `_` are not indexed (instructions and templates), and neither are
  subfolders (tool state, not research).
- **What you conclude is still yours to doubt.** A note records what you
  thought at the time; agents are told to offer notes to the reader and to
  verify their claims against the cited page rather than quoting them as
  evidence.

## Note format (Obsidian compatible)

- **Filename**: `lowercase-hyphenated.md` (e.g. `malaca.md`). One note per
  topic; keep it focused.
- **Wikilinks** `[[Note Name]]` link between notes. **Only link to notes that
  already exist in this `notes/` folder and that are genuinely related to the
  new note's content. Never leave a `[[wikilink]]` pointing at a note that does
  not exist.** If you are tempted to link to a not-yet-written topic, instead
  mention it in plain prose (or create that note now and then link it). Before
  finishing a note, check that every `[[wikilink]]` resolves to a real file.
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

Every claim in a note should be traceable back to the archive, and the best
citation does two jobs at once: it can be **quoted in a publication**, and it
can be **clicked to see the page**. So give both — the page number, and a
**live link** to the page. A page can also have several readings (the raw
transcription plus one or more edited versions, each by a different model), so
the citation must say which one the claim rests on.

The format to aim for:

    doc 65, p. 45 (edited: french-ocr@deepseek-v4-flash) —
    http://127.0.0.1:8765/doc/reference-works-joseph-dehergne-1973-repertoire-des-jesuites-de-chine-de-1542-a-1800/p045 · `pha cite 65 45 --edited`

- the **citation** — `doc 65, p. 45 (edited: french-ocr@deepseek-v4-flash)` —
  is what a reader quotes: document, page, and the exact variant the claim
  rests on. Use the page number **printed on the page** when the source has
  one; the URL always carries the archive's own page number (`/p045` is the
  45th page of the document), and `pha cite` prints the matching URL for you.
- the **link** — `http://127.0.0.1:8765/doc/<slug>/p045` — opens that page in
  the `pha serve` viewer, so the reader sees the image and the transcription
  immediately instead of hunting through the catalogue.
- the **command** — `pha cite 65 45 --edited` — reproduces the citation and the
  link. It also names the document and page where a local link cannot work (a
  printed copy, another machine), so keep it in the note.

`pha cite <doc> <page> [--edited]` prints every part: the citation with the
exact **filled** variant, the stable **slug** the URL is built from, and the
URL itself. Prefer it to hand-writing a path — it refuses to cite an empty
(`*waiting*`) variant, and when several filled variants exist it lists them and
asks for `--editor` / `--palaeographer` rather than guessing. When the document
has a bibliographic sidecar it also puts the reference ahead of the locator,
which is what you want in a publication:

    Dehergne, Joseph. Répertoire des Jésuites de Chine de 1552 à 1800. vol. 37.
    (Roma : Paris: Institutum historicum ; Letouzey & Ané, 1973). — doc 65,
    p. 45 (edited: french-ocr@deepseek-v4-flash)

As a footnote:

    Malaca's Jewish community is described in the 1548–50 register.[^1]

    [^1]: *DocHist do Padroado do Oriente* vol04 — doc 22, p. 437
          (edited: modern-portuguese@deepseek-v4-flash) —
          http://127.0.0.1:8765/doc/<slug>/p437 · `pha cite 22 437 --edited`

Inside running text the same link can be compact:
`[p. 437](http://127.0.0.1:8765/doc/<slug>/p437)`. An agent asked to "show the
page referred to in footnote 12" follows the link (or runs the command) and
reports the text — no guessing from a bare page number.

### Live links (use them in every citation — they don't rot)

A document's numeric id, its dated library folder (`<stem>_YYYY-MM-DD`) and its
`renders/<sha256>/` directory all change when the document is re-processed, so
never embed those in a note. The **slug** does not change: it derives from the
dropbox-relative path (no date, no hash), and only renaming or moving the source
file changes it. A link built from the slug therefore keeps working.

`pha cite <doc> <page> --json` reports the slug, the relative path, the current
sha, the render path, the full variant set and the `url`, so a tool can build a
citation without guessing. With `pha serve` running (read-only, loopback) there
are two forms, and they do different jobs:

    [p. 437](http://127.0.0.1:8765/doc/<slug>/p437)      ← LINK: the page viewer
    ![](http://127.0.0.1:8765/doc/<slug>/p437.jpg)       ← EMBED: the image only

**Link to the viewer** (`/p437`, no extension) in every citation: it shows the
render, the stored raw transcription and the served edited reading when one is
available, and lets the reader page forward/back, jump to the first or last
page, and see the position (`p. 437 of 618`) — so a citation is somewhere to
keep reading. **Embed the `.jpg`** when the picture itself belongs inline: an
embed cannot navigate (a note cannot contain the viewer — Obsidian blocks
iframes).

Both resolve the document's current render per request, so a re-scan changes the
bytes behind the URL without breaking the note.

**They resolve only while that server is running** — start it with `pha serve`
(read-only, loopback `http://127.0.0.1:8765`). Say so when handing a note to
someone else, and keep the printed citation and the `pha cite` command beside
the link: a note copied to another machine loses the links, not the citation.
**A note that embeds an image must carry a short warning near the top**, so a
reader who sees a missing image knows it is the server, not the note. Full
setup and caveats: `obsidian-integration.md`.

## How an agent should create a note

1. **Search** the archive for the topic: `pha search "Malaca"` (or the
   `pha_search` MCP tool).
2. **Read each hit in full** — run `pha page <doc> <page>` (and `--edited` when
   available) to get the complete page text. Do not summarize from a snippet
   alone.
3. **Synthesize** into a single note in THIS folder:
   - State only what the archive supports; say what it does *not* contain.
   - Cite each fact to its document + page in a footnote **with its live link**
     (`pha cite <doc> <page>` gives you the locator, the exact variant, the slug
     and the URL).
   - Link to other notes with `[[wikilinks]]`; create a new note when another
     topic deserves its own page.
4. **Save it** here as `lowercase-hyphenated.md` (create any linked-topic note
   too), and make sure every footnote `[^n]` has a matching definition.
5. Re-check that no citation points at the wrong page, that every citation
   carries its live link, that every `[[wikilink]]` resolves to a real file, and
   that all footnotes resolve.

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
footnote per page cited, and `[[wikilinks]]` to any genuinely related notes that
already exist in this folder.
