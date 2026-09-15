# Bibliographic references for documents — design and implementation plan

Status: **implemented** (2026-09). The design below is what shipped; §12 records
what the real Zotero sample run taught us, and §13 what was built.

Goal: every document may carry a full bibliographic reference, so that a
citation names the *work* it comes from (author, title, volume, place,
publisher, date, repository, shelfmark) instead of only a filename, and so the
page displays (`pha page`, the `pha serve` viewer, the library front matter,
MCP, and later the public mirror) can show it.

Before this, the only citable string was built in `cli.py` (now
`bibliography.format_citation`'s fallback path):

```python
citation = f"{doc['filename']} — doc {doc['id']}, p. {args.page} ({label})"
```

and the `documents` table (`db.py:8`) had no author/title/date/publisher/
repository/shelfmark columns at all. "`sample_charter.pdf` — doc 3, p. 1" is
not a citation a historian can use.

## 1. The design decisions (settled)

1. **One sidecar per document, beside the document**, in one of two accepted
   formats:
   - `<stem>.mods.xml` — MODS 3.8 (Library of Congress), namespace
     `http://www.loc.gov/mods/v3`
   - `<stem>.dc.json` — qualified Dublin Core (DCMI Metadata Terms) as JSON-LD
   If a sidecar exists we have a reference; if not, we do not.
2. **No inheritance, no default, no fallback chain.** A document without a
   sidecar keeps exactly today's citation behaviour. A missing reference is
   *visibly absent*, never quietly inherited from a parent directory.
3. **Two vocabularies, one internal model, one renderer.** Each sidecar format
   is parsed into the same normalised record (§3), and a single
   `format_citation()` (§4) produces the string for every surface. Adding a
   third format later means one parser, not a new rendering path.
4. **An exchange format, not an export format.** BibTeX was rejected: it is
   LaTeX-escaped, ad-hoc in its field set, and a typesetting input rather than
   a description. Both accepted formats are library exchange vocabularies.

### Why no inheritance (the most important rule here)

Pipeline configuration and bibliographic identity have **opposite fallback
semantics**:

- A pipeline default is *safe* to inherit. If a document has no `pha.yaml`
  anywhere, falling back to the collection's palaeographer and then to
  `default.md` is correct — that is why `resolve_stages` (`sidecar.py:193`)
  ends in `cfg.get_palaeographer()`.
- A bibliographic default is *dangerous*. A document that silently inherits a
  parent's reference produces a **confident wrong citation**, which is worse
  than no citation. There is no meaningful archive-wide default reference, and
  nearest-wins inheritance misattributes a page the moment a folder holds more
  than one work.

This is also why the reference does **not** live in `pha.yaml`: it would need a
sidecar per document to say anything specific anyway, and it would drag in the
inheritance rule we just rejected. A useful side effect: this design needs
**no change to `schema/pha-sidecar.schema.json`** (which is
`additionalProperties: false`, so a new key there would fail validation).

### Why two formats rather than one

They are not redundant, and the division is deliberate:

- **MODS** has real fields for the things this archive needs — the containing
  work (`relatedItem[@type='host']`), a volume number
  (`part/detail[@type='volume']/number`), a shelfmark (`location/shelfLocator`),
  an imprint, and record provenance (`recordInfo`). It is the standard for
  printed books, manuscripts and multi-volume sets.
- **Dublin Core JSON-LD** is smaller, JSON-native, and readable by any RDF
  tool; it is the format a lightweight or web-oriented record wants. A nested
  `dcterms:isPartOf` recovers the containing-work description that flat DC
  lacks.

The DC record is **lossy** in ways worth stating, because they explain why MODS
stays the richer option rather than a duplicate:

| Field | MODS | Qualified DC |
| --- | --- | --- |
| place of publication | `originInfo/place/placeTerm` | **no term** (spatial coverage means *about*, not *published at*) |
| edition | `originInfo/edition` | **no term** |
| volume number | `part/detail[@type='volume']/number` | **no term** |
| repository (holding institution) | `location/physicalLocation` | **no term** |
| shelfmark | `location/shelfLocator` | only via `dcterms:identifier` (type lost) |
| creator role (editor/translator) | `name/role/roleTerm` | **no term** |
| record provenance | `recordInfo/recordOrigin` | **no term** |

Every "no term" is recoverable in the DC sidecar as a **local `pha:` term
declared in `@context`** — normal JSON-LD application-profile practice, which
leaves the file valid JSON-LD and readable by standards-only tools while still
carrying what this archive needs:

```json
"@context": {
  "dc":      "http://purl.org/dc/elements/1.1/",
  "dcterms": "http://purl.org/dc/terms/",
  "pha":     "https://github.com/joaquimrcarvalho/personal-historical-archive/ns#"
}
```

The loader reads `pha:` terms when present and ignores them otherwise, so a
pure-DC file still works (degrading those fields into the citation string).

## 2. File naming and lookup

`sidecar_path(doc)` — one mechanical rule, no configuration:

| Document kind | Sidecar path |
| --- | --- |
| file (`doc22.pdf`, `doc22.tif`, …) | `doc22.mods.xml` or `doc22.dc.json` beside it |
| directory-of-images (`vol04/`, `ingest.py:338`) | `vol04/vol04.mods.xml` or `vol04/vol04.dc.json` inside the folder |

Formally: `<dir of the document layer>/<stem>.<ext>`, where a
directory-of-images document's "document layer" is the folder itself.

**When both files exist** — an ambiguity that could produce a wrong citation,
so it must not be silent:

- MODS wins (it is the richer record), **and**
- `pha cite` writes a warning to stderr naming both paths, **and**
- `pha bib --check` reports the document as misconfigured.

Precedence is never silent, and never between two disagreeing records with no
signal.

Two further properties worth noting:

- **It cannot collide.** A file `vol04.pdf` and a folder `vol04/` in the same
  directory resolve to different paths (`vol04.mods.xml` vs
  `vol04/vol04.mods.xml`).
- **A sidecar is inert to the pipeline.** Neither `.mods.xml` nor `.dc.json` is
  in `SUPPORTED_EXTS` (`extract.py:8`), so a sidecar can never be ingested as a
  document; and `_dir_sha` hashes only supported files (`ingest.py:331–334`),
  so adding or editing one cannot perturb a content hash, cannot create a new
  document version, and cannot trigger re-transcription.

## 3. The normalised record and the two parsers

Both parsers produce one dataclass; nothing downstream knows which file it came
from:

```python
@dataclass
class Name:      name: str; role: str | None = None
@dataclass
class Identifier: value: str; type: str | None = None
@dataclass
class Host:      title: str | None; volume: str | None
                 date: str | None; publisher: str | None
                 identifiers: list[Identifier]; shelfmark: str | None

@dataclass
class Bibliography:
    title: str | None; sub_title: str | None
    part_number: str | None; part_name: str | None
    creators: list[Name]
    type: str | None; genre: str | None
    place: str | None; publisher: str | None
    date_issued: str | None; edition: str | None
    language: str | None; form: str | None; extent: str | None
    identifiers: list[Identifier]
    repository: str | None; shelfmark: str | None; url: str | None
    host: Host | None
    rights: str | None
    citation_override: str | None      # explicit human string, if given
    record_id: str | None; record_origin: str | None
    source_format: str                 # "mods" | "dc"
    source_path: str
```

### Field mapping

| Normalised field | MODS (`.mods.xml`) | Qualified DC (`.dc.json`) |
| --- | --- | --- |
| `title` | `titleInfo/title` | `dcterms:title` |
| `sub_title` | `titleInfo/subTitle` | `dcterms:alternative` |
| `part_number` / `part_name` | `titleInfo/partNumber`, `partName` | `pha:partNumber`, `pha:partName` |
| `creators` | `name/namePart` + `role/roleTerm` | `dcterms:creator` (string or nested object for a role) |
| `type` / `genre` | `typeOfResource`, `genre` | `dcterms:type` |
| `place` | `originInfo/place/placeTerm` | `pha:placeOfPublication` |
| `publisher` | `originInfo/publisher` | `dcterms:publisher` |
| `date_issued` | `originInfo/dateIssued` **or `copyrightDate`** (Zotero emits the latter for books) | `dcterms:issued` |
| `edition` | `originInfo/edition` | `pha:edition` |
| `language` | `language/languageTerm` | `dcterms:language` |
| `form` / `extent` | `physicalDescription/form`, `extent` | `dcterms:format`, `dcterms:extent` |
| `identifiers` | `identifier[@type]` | `dcterms:identifier` |
| `repository` | `location/physicalLocation` | `pha:repository` |
| `shelfmark` | `location/shelfLocator` | `pha:shelfmark` |
| `url` | `location/url` | `dcterms:identifier` as `{"@id": …}` |
| `host` | `relatedItem[@type='host']` **or `[@type='series']`** (Zotero emits `series`) | `dcterms:isPartOf` (nested object) |
| `host.volume` | `relatedItem/part/detail[@type='volume']/number` | `pha:volumeNumber` inside `isPartOf` |
| `rights` | `accessCondition` | `dcterms:rights` |
| `citation_override` | `note[@type='bibliographicCitation']` | `dcterms:bibliographicCitation` |
| `record_id` | `recordInfo/recordIdentifier[@source]` | `@id`, or `dcterms:identifier` |
| `record_origin` | `recordInfo/recordOrigin` | `pha:recordOrigin` |

Parsing rules for both:

- **Standard library only.** `xml.etree.ElementTree` for MODS and `json` for
  DC — no new dependency (`pyproject.toml` has no XML library and does not need
  one). MODS matching is on **local names**, so a `mods:` prefix or a default
  namespace both work.
- **Tolerant, never fatal.** Unknown elements and unknown `pha:` terms are
  ignored, so a fuller library export still works. Malformed XML/JSON is
  reported by `pha bib --check` and treated as *no reference* — a broken
  sidecar must never break `pha cite`.
- **Multi-valued fields.** Repeated `name`, `identifier` and `dcterms:creator`
  become lists; a single value becomes a one-element list.
- **The host relationship is a declared reference, not inheritance.** Every
  volume of a set repeats its own host description. The duplication is real,
  but it is how the relationship is expressed and it is what keeps each sidecar
  self-contained — the point of "no inheritance".
- **The page number is not in the sidecar.** A sidecar describes the document
  (the volume/scan); the page is supplied at render time. A per-page sidecar
  would be unusable.

## 4. One citation renderer

`format_citation(bib, page_no, variant_label) -> str` composes the human string
from the normalised record. It never re-parses, never reads the filesystem, and
is the only place citation style lives — so `pha cite`, the viewer, MCP and the
front matter cannot drift apart.

Priority:

1. `citation_override`, when the record supplies an explicit string (MODS
   `note[@type='bibliographicCitation']`, DC `dcterms:bibliographicCitation`).
   Per the DCMI guidelines a plain-text citation should always be present, so
   honouring it verbatim is the interoperable choice.
2. Otherwise compose from the fields, then append the archive's own locator
   (`— doc N, p. P (label)`), which is unchanged from today.
3. **No sidecar** → exactly today's string, byte for byte.

Worked example (MODS, or DC with the `pha:` terms), page 437:

```
Documentos históricos do Padroado do Oriente, vol. IV (Lisboa: Imprensa
Nacional, 1947), BNP RES. 1234 V. — doc 22, p. 437 (edited: modern-portuguese@…)
```

### The two sidecars, side by side

```xml
<?xml version="1.0" encoding="UTF-8"?>
<mods xmlns="http://www.loc.gov/mods/v3" version="3.8">
  <titleInfo>
    <title>Documentos históricos do Padroado do Oriente</title>
    <partNumber>IV</partNumber>
  </titleInfo>
  <name type="personal">
    <namePart>Silva, António</namePart>
    <role><roleTerm type="text">editor</roleTerm></role>
  </name>
  <typeOfResource>text</typeOfResource>
  <genre authority="marcgt">book</genre>
  <originInfo>
    <place><placeTerm type="text">Lisboa</placeTerm></place>
    <publisher>Imprensa Nacional</publisher>
    <dateIssued encoding="w3cdtf">1947</dateIssued>
  </originInfo>
  <language><languageTerm type="code" authority="iso639-2b">por</languageTerm></language>
  <location>
    <physicalLocation>Biblioteca Nacional de Portugal</physicalLocation>
    <shelfLocator>BNP RES. 1234 V.</shelfLocator>
  </location>
  <relatedItem type="host">
    <titleInfo><title>Documentos históricos do Padroado do Oriente</title></titleInfo>
    <part><detail type="volume"><number>4</number></detail></part>
  </relatedItem>
  <recordInfo>
    <recordIdentifier source="pha">22</recordIdentifier>
    <recordOrigin>human-supplied</recordOrigin>
  </recordInfo>
</mods>
```

```json
{
  "@context": {
    "dc":      "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "pha":     "https://github.com/joaquimrcarvalho/personal-historical-archive/ns#"
  },
  "dcterms:title": "Documentos históricos do Padroado do Oriente",
  "pha:partNumber": "IV",
  "dcterms:creator": [{ "pha:name": "Silva, António", "pha:role": "editor" }],
  "dcterms:type": "Text",
  "pha:placeOfPublication": "Lisboa",
  "dcterms:publisher": "Imprensa Nacional",
  "dcterms:issued": "1947",
  "dcterms:language": "por",
  "pha:repository": "Biblioteca Nacional de Portugal",
  "pha:shelfmark": "BNP RES. 1234 V.",
  "dcterms:isPartOf": {
    "dcterms:title": "Documentos históricos do Padroado do Oriente",
    "pha:volumeNumber": "4"
  },
  "pha:recordOrigin": "human-supplied"
}
```

## 5. Storage and freshness

Add a table keyed by document, mirroring the `page_edits` pattern (`db.py:44`)
and using the `migrate()` helper (`db.py:104`):

```sql
CREATE TABLE IF NOT EXISTS document_bibliography (
    document_id   INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    sidecar_path  TEXT,
    sidecar_sha   TEXT,   -- content hash of the sidecar: reload only when it changes
    source_format TEXT,   -- "mods" | "dc"
    citation      TEXT,   -- the rendered human-readable string
    parsed_json   TEXT,   -- the normalised record, for viewer/MCP/mirror
    record_origin TEXT,   -- human-supplied | agent-drafted-unverified | ...
    updated_at    REAL NOT NULL
);
```

A DB snapshot (rather than reading files per request) is needed because:

- `pha serve` builds its index from one read-only query (`serve.py:203`) and
  must not touch the filesystem per request;
- MCP `pha_page` / `pha_document` payloads (`mcp_server.py:125`) must carry the
  reference without a second lookup;
- the public mirror needs it as *data* (`SEARCH_WEB_SPEC.md` §6.2 `documents`
  row, §9 `/api/cite/{slug}/{N}`) regardless.

Storing the **normalised** record (not the raw file) is what keeps every
downstream surface format-agnostic.

**Freshness rules:**

- Reload when `sidecar_sha` changes, on `pha scan` (and `pha reindex`) — the
  same "compare the applied content hash" idea FILTERS_PLAN.md uses for stage
  filters.
- **Editing a sidecar must never mark a document stale for scan/edit**, must
  never alter `documents.sha256`, and must never write a new library version
  (guaranteed mechanically — see §2).
- A removed sidecar deletes the row, so the citation falls back cleanly.

## 6. Integration points (all additive)

| Surface | Change |
| --- | --- |
| `cli.py:237` `cmd_cite` | print the rendered reference + page; keep today's string when absent; warn on a both-formats conflict |
| `cli.py` `cmd_page` (`--json` meta, ~180–222) | add a `bibliography` object |
| `ingest.py:1115` / `ingest.py:1234` front-matter builders | add a `bibliographic_reference` key |
| `serve.py:203` index query, viewer (~481 footer), overview (~515) | `LEFT JOIN document_bibliography`, show the reference |
| `mcp_server.py:125` (`pha_page`) and `pha_document` | include the reference |
| new `pha bib` command | coverage (`--check`: documents with no reference, malformed sidecars, both-formats conflicts, agent-drafted records), `--json` |
| `README.md`, `AGENTS.md`, `HISTORIANS_README.md`, archive `notes/README.md` | document both sidecar formats; the citation convention should name the work, not just the filename |

`pha cite` keeps its existing contract: it still refuses to cite an empty
(`*waiting*`) variant, and the reference never changes variant selection.

## 7. `pha bundle` / `pha unbundle` — required, or the feature silently breaks

This is the change that must not be missed. `bundle.py` copies, per document
(`bundle.py:239–274`): the document itself, the resolved palaeographer/editor
selection files, the encoder directory, and the prompt file. **A sidecar beside
a file document is not in that set**, so without a change it would be silently
dropped from a bundle — the exact "does not travel" failure that ruled out a
central bibliography file.

- Add `bibliography_sidecar_path(cfg, row)` beside the resolver, and in the
  bundle payload loop `_copy2` whichever sidecar exists into `bdrop` when it is
  inside the dropbox and not already copied, adding it to `copied` — the shape
  of the existing selection-file loop (`bundle.py:250–256`).
- Carry it in `manifest.json`'s per-document metadata (`bundle.py:~349`) so an
  operator can see whether a bundled document carried a reference.
- **Directory-of-images documents already work**: `bundle.py:241` calls
  `_copy_tree`, which copies a sidecar inside the folder with no change.
- `unbundle` copies the dropbox payload wholesale (`_copy_dropbox_payload`,
  `bundle.py:512`), so the sidecar lands; the DB snapshot then needs the same
  load pass the post-unbundle indexing already runs.
- `pha bundle --move` should delete the sidecar along with the document.

## 8. Public mirror (spec follow-up, not this change)

`SEARCH_WEB_SPEC.md` needs a companion edit when the mirror is built:

- §6.2: reference columns on the mirror `documents` table.
- §9: `/api/cite/{slug}/{N}` returns the reference.
- **§11.4/§11.6 (policy and leak checklist): a sidecar can contain
  `physicalLocation`, `shelfLocator` and repository names.** For a private
  collection these must not leak through an otherwise-public citation. The
  `public:` allow-list must gate the reference exactly as it gates images and
  text, and the leak checklist should name repository/shelfmark as protected.

## 9. Anti-hallucination discipline

Bibliographic data is precisely what an LLM will confabulate: an invented
volume number, publisher or shelfmark looks exactly like a correct one. This
archive is agent-driven, so the rule must be mechanical, not aspirational:

- `record_origin` (`recordInfo/recordOrigin`, or `pha:recordOrigin`) records the
  provenance of the record. Only `human-supplied` (or human-confirmed) counts
  as verified.
- `pha cite` must **mark** an `agent-drafted-unverified` reference rather than
  printing it as fact, and `pha bib --check` lists them.
- An agent may draft a sidecar when asked, but must mark it unverified, must
  not invent a shelfmark or an imprint, and must not edit an existing
  human-supplied record without saying so.
- The sidecar is **input** (like `pha.yaml`), so unlike a library page it is
  not part of the `pha review` round-trip: the owner edits it directly. It must
  never be written into `library/`, which is version-dated, regenerated output.

## 10. Testing

- `tests/test_bibliography.py` (new) — both parsers into the same normalised
  record; namespace-prefixed and default-namespace MODS; `pha:` terms present
  and absent; malformed XML/JSON tolerated as "no reference"; multi-valued
  names and identifiers; `relatedItem[@type='host']` volume extraction.
- **Cross-format equivalence** — the MODS and DC examples in §4 must produce
  the same normalised record and the same rendered citation. This is the test
  that keeps "support both" honest.
- `tests/test_cli_cite.py` — with each format, with none, and with both
  (the warning); the no-sidecar output must be byte-identical to today's.
- `tests/test_bundle.py` — a file document carries its sidecar (both
  extensions); a directory document carries its own; `--move` removes it. The
  regression test for §7.
- `tests/test_db.py` — the table, the `migrate()` path on an old DB, the
  sha-change reload, and removal when the sidecar disappears.
- `tests/test_sidecar.py` — the lookup rule for both document kinds, including
  the `vol04.pdf` + `vol04/` collision.
- `tests/test_serve.py`, `tests/test_mcp.py` — the reference appears in the
  viewer and the MCP payloads.
- A test asserting a sidecar edit does **not** change `documents.sha256`,
  `status`, or create a new library version.

## 11. Open questions

1. **XSD validation for MODS.** Vendor `mods-3-8.xsd` under `schema/mods/` and
   validate? That needs `lxml` to do properly. Current plan: tolerant parse via
   the stdlib, `pha bib --check` reporting problems. Deferred.
2. **JSON Schema for the DC sidecar.** The same question in JSON — we already
   depend on `jsonschema`, so a `schema/bibliography-dc.schema.json` is cheap,
   and editors would autocomplete it. Worth doing.
3. **`_sample.mods.xml` / `_sample.dc.json`.** AGENTS.md says
   `config.builtin_samples()` is the single source of truth for the seeded
   `_sample*` catalogue and a test asserts the committed files match. Adding a
   sample means adding a new sample category, or documenting why it is not
   seeded.
4. **Citation style.** One composed style, or a `--style` option? Start with
   one deterministic formatter plus the override.
5. **Version bump.** Per AGENTS.md this is user-driven and manual; a
   user-visible feature landing means a **minor** bump (0.23.0 → 0.24.0) when
   the human says "push and update version".

## 12. Findings from real Zotero data (sample run, 2026-09)

24 of the archive's 38 documents were matched to records in the owner's Zotero
library (12,721 top-level items) via its **local API**
(`http://localhost:23119/api/users/0`), and 24 sidecars were installed under
`<archive>/dropbox/` using the naming rule in §2. This section records what the
real data taught us; items 1–3 are corrections to earlier assumptions.

### 12.1 The local API exports MODS natively

`GET /api/users/0/items/<key>?format=mods` returns a `<modsCollection>`
wrapping one `<mods>`. So the sidecar can be **produced from Zotero directly**
rather than hand-written — that is the whole sample-data path. `format=csljson`
and `format=bibtex` also work; `format=dc` is rejected (HTTP 400), which is
another argument for MODS as the richer sidecar. The export also confirms the
vocabulary choice empirically: `relatedItem`, `part/detail`, `shelfLocator` and
`recordInfo` are all populated by a real library tool.

### 12.2 Correction 1 — dated as `copyrightDate`, not `dateIssued`

Every one of the 22 records used `<copyrightDate>`; none used `<dateIssued>`.
The loader must read both. Handled in the mapping table in §3.

### 12.3 Correction 2 — the containing work is `relatedItem type="series"`

Zotero emits `<relatedItem type="series">` for the multi-volume set, not
`type="host"`. The loader must accept both. This matters for the archive's most
important case: the 12 volumes of Silva Rego's *Documentação para a história
das missões do padroado português do Oriente* all carry the series title, and
their `<part><detail type="volume"><number>` values matched the archive's own
filenames **12/12** by date range.

### 12.4 Correction 3 — Zotero MODS carries the owner's private notes

This is the significant finding. Zotero's MODS export embeds every note,
including **annotation highlights with page positions and encoded citation
blobs**, and the owner's own research commentary (e.g. wikilinks such as
`[[Agostinhos no Oriente]]`, tags like `pesquisado-coimbra`).

| | raw export | after stripping `<note>` |
| --- | --- | --- |
| 24 records | 326,621 B | 46,282 B |
| notes removed | — | 62 |

One record alone (`8T96AZDE`) was 127 KB, of which 126 KB was notes. Two
consequences, both already implied by §11.6:

- **An importer must strip `<note>` before writing a sidecar.** Publishing a
  collection would otherwise republish the owner's research apparatus — the
  precise leak the §8/§11.4 policy exists to prevent — and bloat every page
  display.
- Regardless of publication, a citation is not the place for reading notes.

Also worth flagging: the exported `<subject>` values mix legitimate headings
(`India`, `Padroado português`, `História da Igreja`) with personal workflow
tags (`pesquisado-coimbra`). The importer should keep subjects but a human
should decide about workflow tags.

### 12.5 Zotero supplies no join key or provenance marker

No record contained `recordInfo/recordIdentifier` or `recordInfo/recordOrigin`,
so the design's join key and its anti-hallucination marker (§9) cannot come
from Zotero. The importer must inject them. Each installed sidecar carries:

```xml
<recordInfo>
  <recordContentSource>Zotero (local API); webopac.sib.uc.pt Library Catalog</recordContentSource>
  <recordIdentifier source="zotero">NJK5U3TY</recordIdentifier>
  <recordIdentifier source="pha">22</recordIdentifier>
  <recordOrigin>fetched-from-zotero-unverified</recordOrigin>
</recordInfo>
```

Note the merge: MODS permits **one** `recordInfo`, so the importer folds
Zotero's existing source into the injected one rather than appending a second
(a first attempt created two and was fixed).

### 12.6 Data-quality traps found in the source library

These are the reasons §9's "mark, never assert" rule is not theoretical:

- **A wrong creator.** The record for `medina-doc-japon-1547-1557.pdf` has
  `<namePart>Boston College</namePart>` with role `ctb` — a catalog-import
  artifact, not the author (the series is Ruiz-de-Medina's). It is preserved and
  marked `unverified` rather than silently corrected, because **Zotero is the
  source of truth and must be fixed there**; correcting it only in the sidecar
  would create a second, diverging record.
- **Dirty volume designators.** `1.° VOL. (1499-1522)`, `4." VOL. (1548- 1550)`,
  `9.o VOL. (1562-1565)` — inconsistent roman/ordinal punctuation. These are
  fine for display and unfit for a `number`-typed field; the renderer
  normalises, the sidecar stays faithful.
- **A misused `volume` field.** For *Documenta Indica* (doc 19) the volume value
  is the MHSI series numbering (`vol. 70, 72, 74, …`), not a volume of the work,
  so that document has no usable volume from Zotero — the archive filename is
  the better source.
- **An unreliable `location/url`.** Most values are genuine URLs (HathiTrust,
  archive.org, Google Books, macaumemory.mo) and worth keeping, but one is the
  shorthand `DHMPPO`. The importer keeps only `https?://` values.
- **Missing/inconsistent language.** Absent for most records, `FR` once
  (uppercase), `lat por spa` once (three languages in one string).
- **Inconsistent relation entry.** Volume 12 lacks the series `relatedItem` that
  volumes 1–11 have.

### 12.7 Coverage, and why the unmatched documents matter

| Collection | Documents | In Zotero |
| --- | --- | --- |
| `DocHistMissPadPortOriente` | 12 | 12 (Silva Rego, Agência Geral das Colónias) |
| `franco-imagens` | 4 | 4 (Franco, *Imagem da virtude*, 1714/1717/1719) |
| `pfister-notices` | 2 | 2 (one Zotero item covers both volumes) |
| `medina-docs-japon` | 2 | 2 (1547-1557, 1558-1562) |
| `documenta-indica` | 1 | 1 (Wicki) |
| `letters-from-missons` | 1 | 1 (da Câmara Manuel, 1894) |
| `schute-monumenta-historica-japonica` | 1 | 1 (Schütte) |
| `documents` (root) | 1 | 1 (António Franco, *Synopsis*, 1726) |
| `jesuit-catalogues` | **14** | **0** |
| **total** | **38** | **24** |

The 14 unmatched documents are the annual Jesuit catalogue scans, held as
directory-of-images documents with year names (`1587`, `1601`, `1625-T1-LUS-44II-410`)
in `jesuit-cat-type1` / `jesuit-cat-type4`. Searches for the usual catalogue
titles (`Catalogus personarum`, `catalogi Societatis`, `Assistentia
Lusitaniae`) returned nothing, so they appear genuinely absent from the library.

**This is the strongest argument for §1's no-fallback rule.** Because there is
no inheritance and no default, these 14 documents simply keep today's
`filename — doc N, p. N` citation, and `pha bib --check` will list them as
unreferenced. Under a scheme with inheritance they would silently inherit the
wrong reference from a neighbouring work. The gap is visible and closable, not
hidden.

Also noted for the catalogue documents specifically: if they are ever added to
Zotero, the natural record is the *volume/box* they were filmed from (a
manuscript or a bound catalogue), not the individual year — which is exactly the
`relatedItem`-style declared relationship the MODS sidecar supports, still
without inheritance.


## 13. What was built

| Piece | Where |
| --- | --- |
| Normalised record, MODS parser, DC parser, `format_citation`, `clean_volume`, sidecar lookup | `src/personal_historical_archive/bibliography.py` |
| `document_bibliography` table + `set/get/clear/bibliography_for_documents` | `src/personal_historical_archive/db.py` |
| `sync_bibliography` (hash-compared refresh) + `_bibliography_front_matter` | `src/personal_historical_archive/ingest.py` |
| `pha cite` renders the reference; `pha page --json` carries it; new `pha bib [<doc>] [--check] [--json]` | `src/personal_historical_archive/cli.py` |
| Viewer, overview and `/doc/<slug>/meta.json` (with a fallback for pre-bibliography DBs) | `src/personal_historical_archive/serve.py` |
| `pha_get_page` / `pha_get_document` / `pha_list_documents` carry the snapshot | `src/personal_historical_archive/mcp_server.py` |
| Sidecar copied into the bundle, recorded in `manifest.json`, deleted on `--move` | `src/personal_historical_archive/bundle.py` |
| Tests | `tests/test_bibliography.py`, `tests/test_cli_bib.py`, plus additions to `test_cli_cite.py`, `test_serve.py`, `test_mcp.py`, `test_bundle.py`, `conftest.py` |
| Docs | `README.md` ("Bibliographic references (sidecars)"), `AGENTS.md`, `HISTORIANS_README.md` |

Deliberate implementation choices worth recording:

- **`pha cite` and `pha bib` read the sidecar live** (the file is the source of
  truth, so an edit shows immediately without a scan); **`pha serve` and MCP
  read the DB snapshot**, because they may be read-only or remote. One
  `format_citation` still renders both, so they cannot disagree about wording.
- **`serve` tolerates a DB with no `document_bibliography` table**, falling
  back to a query without the join — an archive written before this feature
  still serves (tested).
- **A malformed or empty sidecar is treated as "no reference"**, never as an
  error: `pha cite` must not break because a file is truncated.
- **`_compose` never doubles a terminal full stop** (`BNP RES. 1234 V.` must not
  become `V..`).
- **Two surface fields beyond the plan's dataclass**: `subjects` and
  `classification`, because the real records carry them and they cost nothing.

Not done (still open from §11): an XSD/JSON-Schema for the sidecars, a seeded
`_sample.mods.xml`, a `--style` option, and BibTeX/CSL *export* (the storage
formats are MODS and DC; export was only ever a convenience).

### 13.1 Correction — which format is for whom

The original design treated MODS as the primary sidecar and recommended
hand-writing it (see §12.4's example and the first draft of
`HISTORIANS_README.md`). That was wrong: **MODS XML is a machine interchange
format** — it is what Zotero exports — and telling a historian to hand-edit XML
is a bad ask.

The role split is now explicit:

- **JSON (`<stem>.dc.json`) is the format a person writes and edits.** Key
  spelling is forgiving: `_canon()` strips any prefix and every
  non-alphanumeric, so `part_number`, `partNumber`, `pha:partNumber` and
  `dcterms:partNumber` are one field. A plain, ceremony-free file therefore
  parses, and so does a qualified Dublin Core JSON-LD record from another tool.
- **MODS is the import format.** `pha bib <doc> --to-json [--write]` converts
  it (a whole archive at once with `pha bib --to-json --write`), removing the
  `.mods.xml` because it would otherwise win the lookup; `--keep-mods` keeps
  both, `--qualified` emits JSON-LD for another tool.

Both parse into the same record and render identically — verified by a
round-trip test over the 24 real Zotero sidecars (all 24 identical, unqualified
and qualified).

**Provenance was also split in two.** A model-drafted reference
(`agent-drafted-*`) is badged `[unverified reference]` in the citation itself,
because an invented shelfmark reads exactly like a correct one. A reference
merely *imported* from the owner's own library (`fetched-from-zotero-*`) is
reported as not-yet-reviewed by `pha bib` but does **not** clutter every
footnote. `verified_from_origin` (review status) and `agent_drafted_from_origin`
(inline badge) are separate rules, both living in `bibliography.py` so
`pha cite`, `pha serve` and MCP cannot disagree.

### 13.2 BibTeX added — the agent-draftable format

BibTeX was rejected early as the *storage* format (LaTeX escaping, ad-hoc field
set). That is still true, and it is now an accepted **sidecar** anyway, for a
reason the original analysis missed: it is the one format an agent can **draft
from the scan itself** — reading a title page or colophon — with no Zotero and
no external tool, and a human can still correct it afterwards. So:

- `parse_bib` reads a `.bib` sidecar: `@string` macros, `#` concatenation,
  `crossref` inheritance, LaTeX accents (`{\'o}` → `ó`, `\c{c}` → `ç`) and
  protective braces (`{S}ocietatis` → `Societatis`), plus UTF-8.
- `to_bibtex` writes one back, so `pha bib <doc> --to-bibtex [--write]` converts
  any existing reference. Output is UTF-8 (legible, round-trips) with only the
  structural characters escaped, and `pages`/`extent` invert correctly
  (`311--338` → "pp. 311–338", `599` → "599 p.").
- `--origin` stamps provenance, which is how an agent marks its own draft:
  `pha bib <doc> --to-bibtex --origin agent-drafted-unverified --write` badges
  every citation `[unverified reference]` until a human checks it. A `.bib`
  with no provenance field defaults to `imported` — reported by `pha bib`,
  never badged.
- Role words (`author`/`editor`/`translator`) rather than MARC codes, so a MODS
  `roleTerm type="text"` round-trips unchanged.

**Precedence also flipped**: `.dc.json` now wins over `.mods.xml` and `.bib`.
JSON is the format a person maintains, so an edit to it must not be silently
overridden by a stale import — which is also what makes `--keep-others` safe
rather than a trap. `pha bib --check` reports the conflict.
