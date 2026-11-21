# Enhancement request — encode one page and pin the record(s)

**Status:** IMPLEMENTED (working tree, 2026-11-21; targeted at 0.53.0).
**Written against:** pha 0.49.7 (source checkout) / 0.49.6 (installed at
`jesuit-archive`); implemented against 0.52.0. **Evidence:** `jesuit-archive`,
`collections/documenta-indica`, vol. VII (`DOCUMENTA-INDICA-1566-1569.pdf`,
doc 78). Review corrections and what actually shipped: **§11**.

**One line.** Let `pha encode` run a document's encoder over **one named
page**, store the record(s) it finds there, and **pin** them so a later bulk
encode keeps them — the record-stage counterpart of `pha scan --page N` and
`pha edit --page N`. By decision (§8 D2), a page where the model finds **no**
record is **not** pinned: the pin is a claim that a deliberate extraction
happened, and a page that yielded nothing must stay free for a later bulk pass.

## 1. Problem

### 1.1 The asymmetry between the three model stages

Each model stage copies the previous one, but the copy stops before the last:

| stage | per-page action | per-page provenance | pin | release |
|---|---|---|---|---|
| palaeographer | `pha scan --path <doc> --page N` | `pages.palaeographer` / `palaeographer_model` | `pages.pinned_at` | `--unpin` |
| editor | `pha edit --path <doc> --page N` | `page_edits.editor_model` | `page_edits.pinned_at` | `--unpin` |
| encoder | **none** | only `records.encoder` | **none** (`records` has no `pinned_at`) | **none** |

`pha encode` accepts `--path`, `--doc`, `--reprocess`, `--dry-run`
(`cli.py:5395`). There is no page selector, and `db.clear_records()`
(`db.py:1339`) deletes **every** row of that encoder for the document, so even
a record produced by a one-off run cannot survive the next bulk encode.

### 1.2 The case that exposed it

*Documenta Indica* vol. VII, document 22 — the letter of Br. Gaspar Paiz from
Goa (5 December 1566), printed p. 115 = archive page **195**.

The volume-wide entry detection is a regex fast-path (`_regex_candidates`,
`ingest.py:140`) driven by `candidate_pattern` in
`dropbox/collections/documenta-indica/encoders/documents.md`: a line holding
only the document number (`22`), or an isolated `DOC. 22`, followed by an
ALL-CAPS header line. Document 22 prints neither:

- the title block (`FR. GASPAR PAIS S. I.` / `FR. [FRANCISCO VAZ] S. I.
  ULYSSIPONEM` / `Goa 5 1566`) sits at the **foot of page 194**;
- the number survives only inside the running head at the top of page 195 —
  `DOC, 22 - GOA 5 DECEMBRIS 1566 115` — welded to place, date and printed
  page, and therefore not a lone-number line.

Measured on the volume: `records-documents.json` holds 132 document records and
**none numbered 22**; `segments-documents/` jumps from `document-0188-…` to
`document-0196-…`. The document is transcribed, edited and visible in the
viewer; only the record is missing. Nothing warns, and `pha encode` exits 0.

The same shape recurs in this series: a document number that survives only in
the running head cannot be seen by a rule keyed on a bare number line. Widening
`candidate_pattern` to the head is **not** a fix: the head repeats on every page
of a document, so on vol. VII it raises the candidate count from 97 to 213, and
pha de-duplicates only records that land on the **same** page (`_record_key`
includes the page, `ingest.py:3413`), so the repetitions survive as separate
records.

### 1.3 The model and the rules are not the problem

`pha test <one doc> --page 195` — the no-write per-page preview — returns
exactly the missing record:

```
number: 22    page_start: 195    doc_kind: letter
title: CAPITULO DE HUMA DO YRMÃO GASPAR PAIZ, DE GOA, PERA O YRMÃO REFEITOREIRO DE S. ROQUE
from: YRMÃO GASPAR PAIZ, DE GOA     to: O YRMÃO REFEITOREIRO DE S. ROQUE
place: GOA     date: 5 DECEMBER 1566     date_normalized: 1566-12-05
```

So the extraction prompt, the schema and the model are correct; the only
failing component is *where a document starts*. The repair therefore belongs at
the record level, not in a re-design of detection.

### 1.4 The existing workarounds are the wrong shape

- **A page-scoped encoder** (`pages: "195"` on a second encoder) persists the
  record but duplicates the `documents` **record type**, so it must be retired
  as soon as detection is fixed, or it will double-record the same document.
- **Re-encoding the volume** costs 847 pages' worth of calls to add one record.
- **Writing the row into `archive.db` by hand** is forbidden by the archive
  contract, and would leave `records_fts`, `record_embeddings` and the
  `segments-*` artifacts inconsistent.

### 1.5 What is *not* at issue: document sub-types

The archive owner's model is two distinct situations:

1. **What kind of document is it** (letter / report / catalogue / …) is a
   **model** decision over the result of the cheap detection. This already
   works that way: the cheap regex only finds *where* a document starts, and
   `doc_kind` is produced by the extraction model from vocabulary that lives in
   the collection's rules (`documents.langextract.md`), not in pha. Adding
   `catalogue` on 2026-11-14 took one line of vocabulary and no code — verified
   on vol. VI p. 701, which now returns `doc_kind: "catalogue"`. This request
   does not touch that taxonomy.
2. **A document the cheap detection missed** is an exception, repaired page by
   page and frozen — which is this request.

## 2. Requirements

- **R1 — page-scoped encoding.** `pha encode --doc D --page P` runs **one**
  document's encoder over **that page only**. Every other page's records,
  chunks and artifacts are untouched.
- **R2 — no detection in page mode.** Page mode must never call the entry
  detection (`detect_entry_pages` / `_regex_candidates`). Detection is the step
  that failed; re-running it for the page would reproduce the miss.
- **R3 — the record is pinned.** The record(s) produced are stored with
  `pinned_at`, so a later bulk `pha encode`, a `--reprocess`, or a changed
  encoder config does not silently discard a record that was deliberately
  extracted. Explicit release is `--unpin`.
- **R4 — no record, no pin.** If the model returns **no** record for the page,
  nothing is stored and **nothing is pinned**; the run says so plainly
  (`no record detected on page P — nothing pinned`). The page must remain as
  free as it was, so a later bulk pass can still treat it normally. This is the
  owner's explicit condition (2026-11-14).
- **R5 — the same modus operandi as `scan`/`edit`.** Same flag names and
  meanings (`--page` repeatable/comma list, `--encoder`, `--model`, `--dry-run`,
  `--no-pin`, `--unpin`), same refusal rules, same model-server lock, same
  verbose "before → after" reporting, same pin semantics.
- **R6 — provenance persists.** The record carries the encoder rules id it ran
  under *and* the model that produced it, so a pinned record is auditable years
  later (`pages.palaeographer_model` / `page_edits.editor_model` are the
  precedent).
- **R7 — idempotent.** Re-encoding the same page replaces its pinned record
  instead of adding a second one; an identical record is dropped and counted by
  the existing dedupe contract (`_dedupe_window`, `ingest.py:3498`).
- **R8 — stages keep their semantics.** `encoder.post` artifact filters run for
  the touched encoder (so `segments-*` gains or updates only that document's
  file), and the new records are embedded so `pha record search` finds them.
- **R9 — the same surface everywhere.** `pha test --page N` previews the
  encoder on one page without writing; `pha_job_start` (`action=encode`) takes
  the same page argument; `pha records --page P` shows the pinned state.
- **R10 — no silent second copy of a document.** Because the running head
  repeats, a page encode can return a document number that the encoder already
  recorded on an earlier page — and the dedupe key contains the page, so it
  would not be caught. Page mode therefore compares the returned identity
  (`number` + `series`) against the document's existing records **regardless of
  page**. A hit is refused by default, naming the page where the document is
  already recorded; `--replace` supersedes that record (and re-pins).

## 3. Design

### 3.1 CLI

```
pha encode --doc D --page P [--page Q ...]
           [--encoder <rules-id>] [--model <model-id>]
           [--dry-run] [--no-pin] [--unpin] [--replace]
```

- `--page` is repeatable and accepts a comma list (`--page 195,196`), matching
  `scan`/`edit`.
- `--page` requires a target resolving to **exactly one** document. A
  `--path` that matches several documents is refused (deliberately stricter
  than `pha edit --page`, which hits page N of every matched document).
- `--encoder` names the collection-local encoder rules to use. Without it, the
  document's encoder list is filtered to those whose declared `pages` cover P;
  exactly one is required — zero or several is an error naming what was
  registered.
- `--model` overrides the model interface for this run only. Like `scan`/`edit`,
  nothing is written to `pha.yaml`; the override is authoritative for the run.
- `--dry-run` prints the resolved plan — document, page, encoder, model, how
  many records on the page would be replaced, whether the result would be pinned,
  and whether the page is outside the encoder's declared range — with no model
  call and no write. It cannot report an identity clash (R10): that depends on
  what the model returns, which a dry run does not ask.
- `--unpin` clears the pin of the named page (or of every page of the document
  when `--page` is omitted) **without a model call**; the records stay until the
  next bulk encode, exactly like `pha review --unset` keeps the page text.
- `--no-pin` encodes and stores without pinning — a one-off that a later bulk
  pass may overwrite. Default is pin.
- `--replace` (R10) supersedes an existing record with the same identity found
  on another page; without it such a run is refused.

Intended recipes:

```bash
# preview: what would the documents encoder find on that page?
pha encode --doc 78 --page 195 --encoder documents --dry-run

# the repair: one page, the document's own encoder and model, frozen
pha encode --doc 78 --page 195 --encoder documents

# release it when the general detection is fixed
pha encode --doc 78 --page 195 --unpin
```

### 3.2 Storage

- New column `records.pinned_at REAL`, added in `db.migrate()` with the existing
  PRAGMA + `ALTER TABLE … ADD COLUMN` pattern already used for
  `pages.pinned_at` and `page_edits.pinned_at` (`db.py:263-277`). `NULL` = a
  bulk-produced record; **no backfill**, so existing archives are unchanged in
  meaning.
- New column `records.encoder_model TEXT` (R6), set at write time from the
  resolved encoder; `NULL` for rows written before this change.
- `db.clear_records(conn, doc_id, encoder)` (`db.py:1339`) gains
  `keep_pinned: bool = True`: it must delete the pinned rows' `records_fts` and
  `record_embeddings` rows only when it actually deletes the record, and must
  leave pinned rows (and their search/embedding rows) in place.
- `db.set_record_pin(conn, record_ids, pinned: bool)` and
  `db.clear_record_pins(conn, doc_id, page_no=None)` mirror the page/edition
  helpers (`db.py:601-625`).

### 3.3 Flow

1. Resolve the document (exactly one) from `--doc`/`--path`; refuse otherwise.
2. Resolve the encoder: `--encoder` if given (collection-local
   `encoders/<id>.md` wins over a global id), else the document's encoder list
   filtered by declared `pages` (§3.1).
3. Check the source: the page must exist and have **text**. Page mode uses the
   same effective reading a bulk encode reads — the edited text when the
   document has an editor, else the pinned/reviewed page edit, else the raw
   transcription — so a collection without an editor is supported. A missing,
   empty or `*waiting*` page is refused with the command that fills it
   (`pha edit --page P` when there is a reading, else `pha scan --page P`).
4. Build the input as one entry: `[(P, edited_text)]`. Never call
   `detect_entry_pages` (R2).
5. Call the encoder **once** with the page told to the model — the same
   "a document starts at page P — extract only it" instruction used by the
   entry-span mode (`ingest.py:3886-3903`), so the model knows the boundary.
6. Parse and de-duplicate with the existing contract (`_record_key`,
   `_dedupe_window`), then apply the identity check of R10: compare
   `number`+`series` against the document's existing records of that encoder on
   **any** page. Same page + identical → dropped and counted. Another page →
   refused, naming that page, unless `--replace`.
7. Store. If **no** record was returned: store nothing, pin nothing, print the
   R4 message, and exit 0 (pha's convention for a job that ran and found
   nothing). If records were returned: write them with `pinned_at = now()`
   unless `--no-pin`, and record `encoder_model`.
8. Run `encoder.post` artifact filters for that encoder, so the affected
   `segments-*` file(s) are regenerated from the new record set; embed the new
   records (R8).
9. Report per page: encoder, model, records kept, records dropped as
   duplicates, records refused by R10, pinned yes/no, and — when nothing was
   found — the R4 line.

### 3.4 Interaction with bulk encoding

- `pha encode --doc D --reprocess` regenerates the encoder's records **except**
  the pinned ones (§3.2). The pinned page keeps the record it was given; the
  rest of the volume is re-derived as today.
- Naming a pinned page again re-encodes it, **replaces** its record and re-pins
  it (updated provenance) — never a second record for the same document page.
- `--unpin` is the only way a pinned record becomes eligible for a bulk
  rewrite; it never deletes the record itself.
- Optional, cheap to add: `pha status` reports the pinned-record count per
  document, so a frozen exception is visible in the overview.

### 3.5 Surfaces

- **`pha test <doc> --page P [--encoder X]`** is the no-write preview and should
  be the documented way to try the page encode first. It currently has a defect
  that must be fixed for this to be trustworthy: `testrun._resolve_encoders`
  (`testrun.py:205`) applies a model only from the `pha.yaml` spec; with
  `--encoder` it passes `None`, so the encoder falls back to the default model.
  Observed 2026-11-14: `--encoder documents` resolved `google/gemma-4-e4b` and
  called LM Studio (`127.0.0.1:1234`, HTTP 400), while a run without the flag
  correctly used the collection's `deepseek-v4-flash`. Fix: when `--encoder`
  names a rules id present in the sidecar's `encoders:` list, resolve that
  spec's `model` as the hint.
- **`pha_job_start`** (`action=encode`) gains the same `page` argument.
- **`pha records --doc D --page P --json`** already selects by page; expose
  `pinned` and `encoder_model` in the payload.

### 3.6 Rejected alternatives

- **A page-scoped encoder in `pha.yaml`.** Duplicates the record type (§1.4),
  needs a volume-specific sidecar to avoid applying the page range to every
  volume, and must be retired later — the maintenance trap this request exists
  to avoid.
- **A second `documents` encoder entry with `pages: "194-195"`.** Same problem,
  plus a second prompt/schema copy (`<id>.prompt.md`, `<id>.langextract.md`,
  `extract.py:70`).
- **Widening `candidate_pattern` to the running head.** Measured: 97 → 213
  candidates on vol. VII, and same-document duplicates survive because the
  dedupe key contains the page (§1.2). Detection improvements are a separate
  piece of work and are not required to ship this.
- **Writing the row into the DB directly.** Forbidden by the archive contract;
  breaks FTS/embeddings/artifacts.

## 4. Edge cases

| Case | Behaviour |
|---|---|
| Page outside the encoder's declared `pages` (e.g. `documents` is 81-788) | Run it, but warn and print the declared range; `--dry-run` shows it. The operator named the page deliberately — that is the exception path. |
| Several encoders' ranges cover P and no `--encoder` | Error, listing the encoders and their ranges. |
| No encoder's range covers P and no `--encoder` | Error naming the registered ranges. |
| Page missing, or its edited text empty / `*waiting*` | Refuse; name `pha edit --page P` (or `pha scan --page P`). |
| Model returns several records on the page | Store and pin all of them, subject to R10. |
| Returned record duplicates one already stored **on the same page** | Dropped and counted by the existing dedupe; if nothing remains, nothing is pinned (R4). |
| Returned record carries a `number`+`series` already recorded **on another page** | Refused by default, naming that page; `--replace` supersedes and re-pins (R10). |
| Model returns a record with no number (a title-only fragment) | Stored only if its title is not already recorded; the run reports it as an unnumbered record so it can be judged. |
| Same page named twice in one run | De-duplicated within the run. |
| A scan/edit/encode holds the model-server lock | Refuse with the holder's message, exactly as `pha encode` does today. |
| Archive without an edited variant | Refuse and point at `pha edit` (the encoder reads the edited text). |
| `--page` + `--path` matching several documents | Refused (R5, §3.1). |
| `--unpin` on a page with no pinned record | No-op, reported. |

## 5. Test plan

Unit tests in `tests/` (fixture: a small archive with one document, an edited
variant of three pages, one encoder):

| # | Test |
|---|---|
| T1 | Page encode stores one record and sets `records.pinned_at`. |
| T2 | A page whose model answer has no record stores nothing and leaves `pinned_at` NULL (R4). |
| T3 | `--dry-run` makes no model call and no write, and prints encoder/model/range/pin plan. |
| T4 | `--no-pin` stores the record with `pinned_at` NULL. |
| T5 | `--unpin` clears the pin and keeps the record and its FTS row. |
| T6 | Bulk `--reprocess` keeps a pinned record and does not duplicate it. |
| T7 | Re-encoding the same page replaces the pinned record (one row, `pinned_at` refreshed). |
| T8 | An identical record on the same page is dropped and counted (`_dedupe_window`). |
| T9 | Page outside the encoder's `pages` range runs with a warning. |
| T10 | Empty/`*waiting*` edited page is refused with the actionable message. |
| T11 | Several encoders covering the page without `--encoder` is an error naming them. |
| T12 | Lock held → refused with the holder message (`locks` fixture). |
| T13 | A record whose `number` is already recorded on another page is refused, and `--replace` supersedes it (R10). |

## 6. Acceptance criteria

On `jesuit-archive`, `collections/documenta-indica`, doc 78:

1. `pha encode --doc 78 --page 195 --encoder documents --dry-run` prints the
   plan (page 195, `documents`@`deepseek-v4-flash`, pinned, page inside 81-788)
   and writes nothing.
2. The real run adds exactly one record — number 22, `page_start` 195,
   `doc_kind` `letter` — pinned, with `encoder_model` recorded.
3. `pha records --doc 78 --page 195` returns it; the `documents` record count
   goes from 132 to 133.
4. `pha encode --doc 78 --reprocess` leaves record 22 in place (pinned) and does
   not duplicate it.
5. A page the model answers with no record prints `no record detected on page
   P — nothing pinned` and leaves `pinned_at` NULL; the controlled case is T2
   (fixture), and any real page that yields nothing serves as the spot check.

## 7. Non-goals

- Changing the entry detection (`candidate_pattern`), or the structure register.
- Changing the `doc_kind` vocabulary (that is rules work; §1.5).
- Per-page model choice for **bulk** encoding (the page override here is an
  exception path).
- Editing record contents by hand.
- Running *every* encoder over one page (the request names one).

## 8. Decisions (proposed — for the owner)

| # | Decision |
|---|---|
| D1 | Pin by default; `--no-pin` and `--unpin` mirror `scan`/`edit`. |
| D2 | **No record → no pin** (owner, 2026-11-14). The pin certifies an extraction that happened. |
| D3 | A page outside the encoder's declared range is allowed with a warning — the exception path must not be blocked by the range that missed it. |
| D4 | Records store `encoder_model` (provenance parity with `pages`/`page_edits`). |
| D5 | Nothing found exits 0 with an explicit line, matching pha's "ran, found nothing" convention; the line is greppable so an agent cannot read exit 0 as success. |
| D6 | `--page` requires exactly one document (stricter than `pha edit --page`). |
| D7 | A record whose identity is already stored on another page is refused unless `--replace` (R10). |

## 9. Effort

About one day including tests: one migration + three DB helpers, one CLI flag
block, one branch in `encode_document` (page list instead of structure), the
identity check (§3.3 step 6), the `testrun` model-hint fix, and the thirteen
tests above. Additive only — no behaviour change for any existing command.

## 10. References

- `src/personal_historical_archive/cli.py:5395` — the `encode` parser (no
  `--page` today).
- `src/personal_historical_archive/ingest.py:3886-3903` — detection call and
  the single-pass/chunked fallback; page mode replaces this with one page.
- `src/personal_historical_archive/ingest.py:140` — `_regex_candidates` (the
  cheap detection that missed doc. 22).
- `src/personal_historical_archive/ingest.py:3413`, `:3498` — `_record_key`,
  `_dedupe_window` (page is part of the key).
- `src/personal_historical_archive/db.py:1339` — `clear_records` (the wholesale
  delete a pin must except).
- `src/personal_historical_archive/db.py:263-277`, `:601-625` — the
  `pinned_at` migration and page/edition pin helpers to mirror.
- `src/personal_historical_archive/extract.py:70` — `resolve_encoder_prompt`
  (per-encoder `<id>.prompt.md` / `<id>.langextract.md`).
- `src/personal_historical_archive/testrun.py:205` — `_resolve_encoders`, and
  the `--encoder` model-binding defect (§3.5).
- `enhancements/pha-single-page-rescan-enhancement-request.md` — the `scan
  --page` design this request mirrors.
- Archive evidence: `jesuit-archive/notes/archive-todo-list.md`
  (§ `collections/documenta-indica`, items of 2026-11-14).

## 11. Review and implementation notes (2026-11-21)

Reviewed against 0.52.0 and implemented in the working tree. The design was
sound; five points were corrected while implementing it.

1. **"The encoder reads the edited text" was too strong.** `encode_document`
   already falls back to the raw transcription (and the archival *served* edit
   can be a reviewed/pinned page edit with another editor), so page mode uses
   `db.effective_edit_for_page` and refuses only when there is no text at all —
   not "no edited variant" (which would make the feature unusable on a
   collection with no editor). §3.3 step 3 / §4 updated above.
2. **`pha encode` takes no model-server lock today**, so "the same lock as
   `pha encode`" did not exist. Page mode takes the encoder's server key itself
   (`locks.stage_key`), with the usual `--wait`/`--no-wait`; bulk encode is left
   unchanged (out of scope). R5 is thereby met in the safer direction.
3. **R8's "new records are embedded" is not how `encode` works** — no encode
   pass embeds records (`index_records` is only called by `reindex_all`). Page
   mode does not either: the records are keyword-searchable immediately and
   `pha reindex --source records --doc N` embeds them. Artifact filters DO run
   (`encoder.post`), so `segments-*` stays consistent.
4. **`clear_records(keep_pinned=True)` alone was not enough.** A bulk pass that
   re-derives the same entry would still add a second copy (the dedupe key
   contains the page). Bulk `encode_document` now also skips a derived record
   whose identity — `(kind, number, series)` — matches a pinned row, and reports
   `pinned_kept` / `pinned_duplicates_dropped`. This is what acceptance #4
   actually requires.
5. **R10 needs `series`.** The real `documents` records carry `series` ("main"),
   so identity is `(kind, number, series)`, not number alone. A record with no
   number falls back to a title match against existing records.

Smaller decisions taken while implementing:

- **A model miss never deletes.** If a re-run on a page returns no record, the
  existing record(s) on that page are left in place and nothing is pinned — a
  transient bad answer must not erase a deliberate extraction. "Replaces" (R7)
  applies only when the model did return something.
- **`records.encoder_model`** is written by page mode AND bulk `encode` (the
  column is `NULL` on rows written before this change).
- **Dry run cannot pre-check R10** (no model call), as noted in §3.1.
- **Page mode does not rewrite `documents.encoder`**; a one-off repair with an
  explicitly chosen encoder must not change the document's configured encoder.
- **The record is grounded to the named page.** The entry hint asks the model to
  put the page in the record, but a model echoing a running head's *printed*
  page would store a record that `pha records --page P` cannot find and a re-run
  would duplicate — so the record's `page_start`/`page` is set to P.
- **`pha_job_start` needed no plugin change**: `buildArgv` already forwards
  `--page` for every action, so `pha_job_start({action:'encode', doc:N, page:P})`
  works once `pha encode` accepts it. Only the tool's `page` description was
  updated.
- **Upgrade/schema**: `db.migrate()` adds `records.pinned_at` and
  `records.encoder_model`; `pha records` (read-only, no migration) exposes both
  only when the columns exist, so an older archive still lists its records.

Shipped tests (`tests/test_encode_page.py`, 18 cases): store+pin, no-record,
model-miss keeps the old record, `--no-pin`, replace-on-repage, `--unpin` keeps
FTS, bulk keeps pinned without duplicating, cross-page refusal then `--replace`,
outside-range warning, no/multiple covering encoders, no-text refusal, edited
text reaches the model, dry-run, lock refusal, exactly-one-document, and the CLI
`no record detected … nothing pinned` line. `tests/test_testrun.py` adds the
`--encoder` sidecar-model binding fix. Measured: 1268 tests pass.

Not verified on the real archive in this session (no writes): acceptance §6
requires `pha encode --doc 78 --page 195 --encoder documents` on
`jesuit-archive` and checking that the `documents` count goes 132 → 133.
