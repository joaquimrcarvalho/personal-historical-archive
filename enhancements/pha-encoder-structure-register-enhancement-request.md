# Enhancement request — per-document structure register for encoder page ranges (no more run-state in encoder files)

**Status:** implemented in the working tree (2026-10-08); not committed/released yet.
**Author/date:** archive work session (Documenta Indica collection), 2026-10-08.
**Related:** `pha-encoder-tools-enhancement-request.md` §3.4 (structure prescan, *not
implemented*), `pha-stage-extends-enhancement-request.md` (composing *prompts*, not
per-document data), `pha-filters-enhancement-request.md` (shipped; filters cannot
carry configuration).

## 1. Motivation

A collection need not have one layout. The **Documenta Indica** collection is 17
PDF volumes (≈500–1 200 pages each, 16 245 pages in total) of the same edition,
and each volume has its own printed→archive offset and its own spans for the
front matter (title pages, `INDEX GENERALIS`, `INDEX OPERUM IMPRESSORUM`,
`NOTAE COMPENDIARIAE`, `INTRODUCTIO GENERALIS`) and back matter
(`INDEX PERSONARUM, RERUM, LOCORUM`). Measured offsets run from 49 to 140 pages:

| volume | pages | printed offset | documents area |
|---|---|---|---|
| 1540–49 | 1 011 | 140 | 141–954 |
| 1550–53 | 725 | 64 | 65–690 |
| 1583–85 | 976 | 49 | 50–925 |
| 1595–97 | 533 | 82 | 83–533 (no closing index) |

The two encoders (`documents`, `apparatus`) therefore need **different `pages:`
filters per volume**, but:

* `pages:` is a **static field of the encoder rules file** — pha reads it
  (`config.py`: `pages=str(fm.get("pages", "") or "").strip()`) and never writes it;
* the encoders are **collection-level**: one `dropbox/collections/documenta-indica/encoders/documents.md`
  and one `…/apparatus.md` serve all 17 volumes.

With today's pha the only way to give a volume its ranges is to **rewrite the
shared encoder files before each encode**. That is exactly what this archive
does: `dropbox/collections/documenta-indica/prescan/doca_prescan.py`
(`_write_pages()`, invoked by `--write-encoder-pages`) substitutes the line in
place:

```python
text2 = pat.sub(f'pages: "{value}"', text, count=1)
```

So a **configuration file carries per-run state**, mutated by a side script and
silently consumed by any later `pha encode`. Two measured incidents:

1. A collection-wide encode ran with vol. I's ranges (141–954) written in the
   files, so 15 other volumes were encoded against the wrong block of pages —
   every one of their document records started at page 141, which is how the
   mistake was detected.
2. A single `pha encode --doc 81` (no prescan for that volume) used whatever
   ranges were left in the files from another volume (82–920) and replaced 124
   records with 35.

Neither failure produced an error: pha cannot tell that a `pages:` line belongs
to a different document. The information is **per document**; it should live
with the document, not in shared configuration.

## 2. Proposed feature: a per-document structure register, resolved at encode time

### 2.1 The register file

A JSON file **next to the source document** (same stem, `.structure.json`):

```
dropbox/collections/documenta-indica/DOCUMENTA-INDICA-1550-1553.pdf
dropbox/collections/documenta-indica/DOCUMENTA-INDICA-1550-1553.structure.json
```

```json
{
  "schema": 1,
  "document": "collections/documenta-indica/DOCUMENTA-INDICA-1550-1553.pdf",
  "source_sha256": "<sha256 of the PDF as ingested>",
  "pages_total": 725,
  "printed_offset": 64,
  "sections": [
    {"kind": "title",        "page_start": 1,  "page_end": 6,   "label": "volume title pages"},
    {"kind": "toc",          "page_start": 7,  "page_end": 14,  "label": "INDEX GENERALIS"},
    {"kind": "bibliography", "page_start": 15, "page_end": 22,  "label": "INDEX OPERUM IMPRESSORUM"},
    {"kind": "abbreviations","page_start": 23, "page_end": 24,  "label": "NOTAE COMPENDIARIAE"},
    {"kind": "introduction", "page_start": 25, "page_end": 64,  "label": "INTRODUCTIO GENERALIS"}
  ],
  "documents_area": {"main": [65, 690], "appendices": [], "closing_index": [691, 725]},
  "pages": {"documents": "65-690", "apparatus": "1-64,691-725"},
  "generated_by": "prescan/doca_prescan.py",
  "generated_at": "2026-10-08T00:00:00Z"
}
```

The `pages` object is the part pha consumes: **named page groups** the encoders
refer to. The rest is provenance and makes the register auditable by a human.

A register may also be placed beside the encoder (collection-level) when a
collection genuinely has one layout; the per-document file wins.

### 2.2 Encoder front matter

```yaml
# encoders/documents.md
pages: "@structure:documents"     # resolve from this document's register

# encoders/apparatus.md
pages: "@structure:apparatus"
```

A literal `pages: "141-954"` keeps working unchanged (backward compatible), and
an encoder may name an explicit path instead of the default stem
(`structure: "<path>"`).

### Decision: no pages_fallback

An encoder that opts into @structure must not carry a literal
pages_fallback. A shared encoder file cannot know a per-volume range, and
a fallback literal would silently reintroduce the wrong-volume failure this
request removes. A literal pages: value still works for encoders that do
not use @structure; for an @structure encoder, a missing/invalid register
or group is a refusal naming the document and suggesting the collection's
structure prescan run, never a fallback.

### 2.3 Resolution at encode time

For each document and each encoder run:

1. read the register next to the source (or the `structure:` path);
2. if the encoder's `pages:` is `@structure[:<group>]`, take the group (or the
   group named after the encoder id);
3. if any of these is missing, invalid, or `source_sha256` no longer matches the
   ingested source, **refuse with a message naming the document and suggesting
   the collection's structure prescan run** - there is no literal fallback for
   an @structure encoder, and pha must never silently encode a block chosen for
   another volume;
4. print the resolved ranges in the run header (as `pha editor` /
   `pha palaeographer` already print effective parameters), so the operator sees
   which block is being encoded.

### 2.4 Who writes the register

Not pha, in this proposal. The register is **derived data** produced by whatever
knows the volume's layout — here a collection-local script
(`prescan/doca_prescan.py`), later possibly pha's own detection (§3.4 of the
encoder-tools request). This request deliberately fixes only the **contract**
between such a producer and pha, so the two can land independently.

### 2.5 Invalidation

The register's content hash joins the encoder's staleness check, like filters
(`library/<slug>/.filter-stamps/`): a changed register re-encodes that document;
an unchanged register does not. The source hash inside the register invalidates
it when the PDF changes.

### 2.6 Audit copy per pass

At each encode, pha copies the register it used into the run's library dir — a
`structure.json` beside the records and the generated artifacts. The
source-side register is *live* and may be retuned over time (our prescan has
already changed twice), so the pass copy is what answers, later, “what ranges
did this encoding actually run under?”. This is the same reasoning that keeps
`records-<encoder>.json` next to `records-<encoder>.md` artifacts.

### 2.7 Concurrency

Once the encoder files are immutable, encoding volume A no longer depends on the
last run of volume B: two documents of one collection can be encoded in any
order, and a stale `pages:` can no longer leak between volumes.

## 3. Interim fallback (no repo change needed today)

The archive already does the following, and will keep doing it until this lands:

* the prescan writes the **register next to the PDF** (durable, per volume) so
  the information lives with the document — this is the file that should become
  pha's input;
* it *also* writes the derived `pages:` line into the two shared encoders, purely
  so today's pha can consume it — that line is treated as **generated output**,
  not as source, and the encoder templates are kept separately;
* every per-volume encode is driven by the runner
  (`.doca-encoder-tests/encode-doca-volumes2.sh`: prescan → validate the
  partition of `1..pages_total` → clear stale artifact stamps → encode), so a
  plain `pha encode` is never issued for a volume whose ranges were not just
  written.

## 4. Tests to add

* `pages: "@structure:documents"` resolves the group from the register; a
  literal `pages:` still works; both in one collection.
* A missing register, a register without the requested group, and a source-hash
  mismatch each produce a refusal naming the document and suggesting the
  collection's structure prescan run - never a silent default or fallback.
* Changing the register re-encodes exactly that document; leaving it alone skips.
* The pass writes the register copy into the library dir, and `pha status` /
  `pha encode --dry-run` report the resolved ranges.
* Two documents of one collection encode back-to-back without either affecting
  the other's ranges.

## 5. Non-goals

* **Detecting** the structure (model or regex) — that is §3.4 of the
  encoder-tools request; this request only says how a register is consumed.
* **Per-volume encoder rule files** — they multiply configuration and, with
  pha's current selection mechanism, need a sidecar per document, against this
  archive's one-sidecar-per-collection rule. A register is data, not a second
  rules file.
* Carrying prompt, editor or model selection in the register: it is page
  geometry and provenance only.
* Replacing filters or `extends`: those compose stage *behaviour*; this supplies
  a stage *input scope*.

## 6. Evidence and current artefacts

* Writer: `dropbox/collections/documenta-indica/prescan/doca_prescan.py`
  (`_write_pages`, `--write-encoder-pages`); reader: pha `config.py`
  (`pages=str(fm.get("pages", …))`).
* The corrected prescan derives registers for all 17 volumes; validation of the
  `documents`/`apparatus` partition over `1..pages_total` is in
  `.doca-encoder-tests/validate_encoder_pages.py`.
* Result so far: 2 259 document records + 102 apparatus sections across the 17
  volumes, one markdown file per document (plus a per-volume `index.md`).


## 6. Implementation status - 2026-10-08

Implemented in the working tree:

- new `src/personal_historical_archive/structure.py`:
  `parse_pages()`, default/explicit register-path resolution,
  `resolve_encoder_pages()` and `write_pass_copy()`;
- `Encoder.structure` front-matter field;
- `pages: "@structure[:group]"` resolved per document at encode time;
- source-hash mismatch, missing register, missing group and out-of-range
  pages all refuse with a message naming the document and suggesting the
  collection's structure prescan;
- resolved ranges printed in the encode run header and in
  `pha encode --dry-run`;
- register content hash used for staleness through
  `.structure-<encoder>.sha256` in the document library dir;
- the used register copied beside the records as `structure.json`;
- encoder ordering resolves `@structure` groups before sorting;
- tests: `tests/test_encoder_structure_register.py` (11 cases); the full
  suite passes (1000 tests).
