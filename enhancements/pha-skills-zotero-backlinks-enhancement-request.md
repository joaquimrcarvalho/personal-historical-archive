# Enhancement request — `pha-zotero-bibliography`: link the Zotero item and the archive document

**Status:** Draft — the text is written and in use in one archive (§3); not
upstreamed. **Date:** 2026-10-14. **Found on:** jesuit-archive, document 150
(Dehergne, «La mission de Pékin vers 1700. Étude de géographie missionnaire»,
*Archivum Historicum Societatis Iesu* 22 (1953) 314-338).

**One line.** The bundled `pha-zotero-bibliography` skill teaches how to read a
record out of Zotero and how to write a sidecar beside the PDF, but never says
how the two records point at each other — nor how to authorize a Zotero 10
local-API *write* at all; this request adds a section for both, beside four
quick-reference rows and two checklist items.

## 1. Problem

Three gaps, in the order they cost time on a real document.

### 1.1 No backlink guidance

The skill ends at the sidecar. Nothing states which field names the Zotero item,
or where the Zotero item should record where the reading lives. The normal end
state — a PDF in the archive *and* an item in Zotero — therefore leaves two
records with no way to find each other, and the obvious workarounds are all
wrong: a `127.0.0.1:8765` address in `url` displaces the canonical online source
and is not a location (it answers only while `pha serve` runs), and a `library/…`
path carries the processing date and goes stale on the next re-read.

### 1.2 The local API's write path is absent

Route A documents **reads** (MODS, no authentication); Route B documents RDF
**imports**. Zotero 10 can also be written to through the same local API — which
is how an item gets created for a PDF that is already in the archive, how its
`extra` is set, and how a file or a link is attached — but only with a key the
user grants through a confirmation dialog:

- `POST /api/local/authorize` with a `Zotero-Server-ID` header, which raises the
  dialog (the header is required on every write: `428` without it, `412` if it is
  stale — the id comes from any response header, e.g. a bare `GET /api/`);
- the key is **single-use unless the user picks "Always Allow"**, so a client
  that asks for it per write shows one dialog per write — three dialogs for one
  PDF attachment (create the item, request the upload, register it);
- write bodies must be a **JSON array** even for one object (`400` "Uploaded data
  must be a JSON array" otherwise), and a malformed write still consumes a
  single-use key.

None of this is in the skill, so an agent asked to create the item or attach the
file has nothing to follow — and the failure modes above are silent or
misleading rather than loud.

### 1.3 `record_id` has no stated rule

Measured over jesuit-archive's 72 sidecars that carry a `record_id`:

| value | count | verdict |
| --- | --- | --- |
| Zotero item key (8 chars) | 37 | the end state |
| Internet Archive identifier | 9 | legitimate — no Zotero item was ever made (`epistolae-mixtae`, `ricci-*`) |
| numeric Zotero itemID (RDF) | 2 | legitimate — Route B carries no key |
| **pha doc id** | **11** | **not an identifier of the source at all** — these say `record_origin: fetched-from-zotero-*` but never recorded the key (docs 16, 18, 19, 44, 47, 50, 52-55, 66) |

Without a rule, an agent copying the nearest sibling can copy the wrong kind —
which is exactly what happened here before it was checked.

## 2. Requirements

- **R1** The skill states what `record_id` holds, and that it changes from the
  source identifier to the Zotero item key once an item exists.
- **R2** The skill states where the Zotero → archive backlink goes (`extra`),
  in what format, and that it must be **appended**, never replace the field.
- **R3** The skill states that the clickable link is a `linked_url` child
  attachment, not `url`, and gives the POST body.
- **R4** The skill names the two traps: the viewer URL takes the **document**
  page number (`/p001`, not the printed page), and a `library/…` path must never
  be stored.
- **R5** The skill documents the local-API write authorization flow (§1.2).
- **R6** The quick reference gains rows for the backlinks and for authorizing a
  write; the checklist gains items so a reviewer can see the work is done.
- **R7** No behaviour change: this is the bundled skill text plus its embedded
  copy. Existing archives are unaffected (seeding never overwrites), so the
  request must be honest that it helps archives seeded from the next release on.

## 3. Design — the text to add

Inserted after `## Provenance: mark, never assert` and before
`## Fix the source in Zotero, not the sidecar`:

````markdown
## Linking the two records (backlinks)

A reference is half the job. The Zotero item should say where the *reading* lives,
and the sidecar should say which Zotero item it belongs to. One field in each
direction; nothing invented, nothing duplicated.

### pha → Zotero: `record_id` is the Zotero item key

| field | holds |
| --- | --- |
| `record_id: 6URU6HYK` | the Zotero **item key** (8 chars) — how pha finds the item |
| `record_origin` | where the *reference* came from (see Provenance above) |
| `url`, `identifiers` | the canonical online source and the ISSN/ISBN/IA id |

`record_id` names the record **in the system that owns it**: the Zotero **item
key** as soon as the item exists, and — only until then — the source identifier
of an import that has no Zotero item. The archive holds both kinds, so read a
sibling before writing: `Dehergne 1980` → `8UUDFV9F` and `1982` → `TIWLLWKP` are
keys, while the `epistolae-mixtae` and `ricci-*` volumes keep their Internet
Archive ids because no Zotero item was ever made for them. Never a publisher or
an ISBN — those have `url` and `identifiers`. The usual order of work puts the
PDF first and the Zotero item later, so **set the key when the item is created**:
an Internet Archive id left behind afterwards is a link not yet made, and a **pha
doc id** in this field is not an identifier of the source at all (the archive has
a backlog entry listing the ones left over from Zotero imports).

### Zotero → pha: three lines in the item's `extra`

`extra` is where Zotero already keeps machine-readable lines (`Citation Key:` for
Better BibTeX); it syncs, survives export, and does not displace `url`:

    pha-doc: 150
    pha-slug: reference-works-dehergne-1953-la-mission-de-pekin-vers-1700
    pha-path: collections/reference-works/Dehergne - 1953 - La mission de Pékin vers 1700.pdf

Three, because they fail differently: the **doc id** is what a person types
(`pha page 150 1 --edited`), the **slug** is what the viewer URL is built from,
the **path** is the anchor that survives a database rebuild (ids are assigned by
the DB; the slug derives from the file path, so renaming the file breaks the slug
but not the path). **Append** them — keep whatever human notes the field already
carries (`extra` may hold a note someone typed, or a title-variant note you wrote
earlier); never replace the field wholesale.

### The clickable link is a child attachment, not `url`

Create a "Link to URI" (`linkMode: linked_url`, `contentType: text/html`):

    POST /api/users/0/items
    [{ "itemType": "attachment", "parentItem": "<itemKey>", "linkMode": "linked_url",
       "title": "pha archive — doc 150 (article p. 314)",
       "url": "http://127.0.0.1:8765/doc/<slug>/p001",
       "accessDate": "<ISO-8601 Z>", "contentType": "text/html", "charset": "" }]

`url` stays the item's canonical online location. A `127.0.0.1:8765` address is
not a location: it answers only while `pha serve` runs, and means nothing on
another machine or after a restart — in a link attachment, though, "a link I
consult" is exactly the meaning.

### Two traps

- The viewer URL takes the **document** page number, not the printed one:
  `/p001` is the article's first page even where it is printed *314*; `/p314` is
  a 404. Generate the link with `pha cite <doc> <page>`, which prints the right one
  (the archive's own rule: never store a `library/…` path — it carries the
  processing date and goes stale on the next re-read).
- Both directions are **writes**: the sidecar is a file edit (free), while
  `extra` and the attachment are local-API writes needing a key from the
  confirmation dialog. Ask for **Always Allow** — with plain "Allow" you get one
  dialog per write (`POST /api/local/authorize`, `Zotero-Server-ID` header,
  Zotero 10+; a write without the header is `428`, a stale one `412`).
````

Four rows for the **Quick reference** table:

```markdown
| Link Zotero → pha | append `pha-doc:` / `pha-slug:` / `pha-path:` lines to the item's `extra` |
| Link pha → Zotero | set the sidecar's `record_id` to the Zotero **item key** |
| Clickable link to the reading | child attachment, `linkMode: linked_url`, `…/doc/<slug>/p001` |
| Authorize a local-API write | `POST /api/local/authorize` with `Zotero-Server-ID` → user picks **Always Allow** |
```

Two items for the **Checklist**, after the "only one sidecar" line:

```markdown
- [ ] Backlinks set both ways: `record_id` = the Zotero **item key** once the item
      exists (an Internet Archive id is only for an import with no Zotero item —
      never a pha doc id), and the item's `extra` carries `pha-doc` / `pha-slug` /
      `pha-path` — appended, not overwriting what was there.
- [ ] Clickable link added as a `linked_url` child attachment built from
      `pha cite` (document page number — `/p001`, not the printed page), and
      `url` left as the canonical online source.
```

And one sentence appended to the front-matter `description`, which currently
ends "…and the provenance rules that stop an unverified reference from being
cited as fact.":

```markdown
…, and the backlinks that tie a Zotero item to its archive document (`record_id` ↔ the item's `extra` plus a viewer link attachment).
```

### 3.1 Files to change

| file | change |
| --- | --- |
| `skills/pha-zotero-bibliography/SKILL.md` | the authored copy — everything above |
| `src/personal_historical_archive/archive_skills.py` | re-embed the same text (the embedded copy is asserted byte-for-byte by `tests/test_archive_skills.py`) |
| `enhancements/pha-enhancement-requests-INDEX.md` | one row under Open → Enhancements |

## 4. Edge cases

- **A record with no Zotero item** (the Internet Archive imports): `record_id`
  keeps the source identifier, and there is no `extra`/attachment to write —
  the backlink is one-directional by construction, and `url` still resolves.
- **Several archive documents pointing at one Zotero item** (a book split into
  parts, each part its own PDF and sidecar): each sidecar carries the same key,
  but `extra` can only carry one set of `pha-*` lines. The request does not
  solve this; it should at least say which one wins (the first part), or the
  lines should name the document each time.
- **The same work in two archives**: the slug is derived from the file path, so
  it differs per archive; the `pha-*` lines therefore describe *that* archive,
  and a copied item must not carry them across.
- **Renaming the PDF** breaks the slug and so the stored URL, while `pha-path`
  survives — which is the reason for three lines rather than one.
- **`pha serve` not running**: the link attachment 404s, the slug and path still
  resolve; this is expected, not a defect.

## 5. Test plan

1. `python -m pytest tests/test_archive_skills.py` — the embedded copy matches
   the authored one after re-embedding (this is the guard that will fail if only
   one of the two files is edited).
2. `pha init-archive` into a scratch directory and confirm the seeded
   `skills/pha-zotero-bibliography/SKILL.md` contains the new section.
3. Against an existing archive: apply by hand and confirm `pha bib <doc>` still
   reads the sidecar after the `record_id` change (metadata only — no re-scan,
   no re-transcription), and `pha cite <doc> <page>` prints the same link the
   attachment uses.
4. In Zotero: the `linked_url` child appears under the item and resolves to
   `/doc/<slug>/p001` while `pha serve` runs.

## 6. Acceptance criteria

- The bundled skill contains the backlink section, the four quick-reference
  rows, the two checklist items, and the extended description.
- `skills/pha-zotero-bibliography/SKILL.md` and the embedded text in
  `archive_skills.py` are equal, and the test passes.
- Nothing else changes: no CLI, no sidecar parsing, no behaviour.

## 7. Non-goals

- No change to how pha reads `record_id` (this is guidance, not parsing).
- No new command (a `pha link` that wrote the Zotero side for you would be a
  separate, larger request; the method here is metadata plus one API call).
- Not a fix for the 11 legacy sidecars in jesuit-archive: those belong to that
  archive's backlog, and the skill only needs to state the rule.

## 8. Open question for the owner

**8.1 A second, archive-local divergence.** jesuit-archive's copy of this skill
also carries a section the repo does not: *"Where an export lands:
`zotero-export/` is staging, not provenance"*, including "The owner chooses the
reading route — propose, then wait". It is a local edit (it is in neither the
authored `skills/` copy nor the embedded text of 0.42.0). It is good material for
the same skill and could ride along with this change; it is called out separately
because it is the owner's text, not this request's.

## 9. Effort

Small. Two files hold the same text (authored + embedded) and a test pins them;
one index row. No code paths change, so no migration or release note beyond the
changelog entry.

## 10. References

- `skills/pha-zotero-bibliography/SKILL.md` — the authored copy to change.
- `src/personal_historical_archive/archive_skills.py` — `SKILL_FILES` and the
  embedded `SKILL.md` texts; `bundled_skill_files()`.
- `tests/test_archive_skills.py` — asserts authored == embedded, byte for byte.
- `OPERATING.md` §skills — "an embedded file is never overwritten", and the
  drift test.
- Zotero local API — <https://www.zotero.org/support/dev/web_api/v3/local_api>
  (§Authorizing Writes, §Server ID, §File Uploads).
- Worked example on jesuit-archive, document 150: sidecar
  `Dehergne - 1953 - La mission de Pékin vers 1700.dc.json` (`record_id:
  6URU6HYK`), Zotero item `6URU6HYK` (three `pha-*` lines in `extra` and a
  `linked_url` child), and `notes/archive-todo-list.md` for the sidecars whose
  `record_id` is still a pha doc id.
