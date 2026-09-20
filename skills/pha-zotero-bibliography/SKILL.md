---
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
