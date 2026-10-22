# Bug report — the same-page record dedup silently deletes distinct entries

**Status:** **FIXED** (working tree, 2026-10-23; see "What landed" below).
**Found:** 2026-10-13 on `jesuit-archive` (Schütte, *Monumenta
historica Japoniae I*), **pha 0.43.1** (`src/personal_historical_archive/ingest.py`,
`encode_document`).
**Severity:** high — **silent loss of genuine data at store time**. On this
archive the alphabetical indexes kept **76 of 149 rows (51 %)** on five pages;
`pha encode` reported the pass as successful and the run's own summary counted
the surviving records only.
**Written against:** pha 0.43.1.

## Summary

While storing the records an encoder returned, `encode_document` drops any
record that starts on the **same page** as an already-kept record and scores
≥ 0.75 on `_record_similar()`:

```python
page = db.record_start_page(rec)
dup = False
if page is not None:
    for prev in records:
        if db.record_start_page(prev) == page and _record_similar(prev, rec) >= 0.75:
            dup = True
            break
if dup:
    continue
```

`_record_similar()` computed the ratio over **every** scalar field of the two
records (`flat()` joins them all). Records extracted from one catalogue page
share a great deal of boilerplate — `title`, `catalogue`, `community`, and a
`source_text` that is often templated — so two **different** entries routinely
score above 0.75, and the second is discarded with no warning, no counter and a
zero exit status.

The guard itself is legitimate: overlapping chunk windows and later extraction
passes re-read the same page, and the same entry must not be stored twice. The
defect is **what it compares**, not that it compares.

## Evidence (jesuit-archive, 2026-10-13)

The alphabetical indexes of the Schütte volume are one entry per line in the
source, which makes the loss exactly countable. Measured against the stored
records:

| archive page | entries in the source | records stored | lost |
|---|---|---|---|
| 337 | 28 | 12 | 16 |
| 338 | 31 | 13 | 18 |
| 339 | 37 | 22 | 15 |
| 340 | 28 | 15 | 13 |
| 341 | 25 | 6 | 19 |
| **total** | **149** | **76** | **73 (49 %)** |

Page 1148 (the 1654 syllabus) is reported by the completeness audit as 32 source
anchors → 8 records, the same signature.

The loss is at **store** time, not in the artifact filter: the records are absent
from `archive.db` and from `records-index_entry.json`, so no exporter can
recover them. Re-running the encoder is the only route back.

### The window is not the model's

Calling `_extract_encoder_window()` directly on page 337 returns **29** records.
Replaying the store loop over those 29 records with the shipped `_record_similar`
keeps **13**. The model's output was complete; pha deleted 16 of 29. (The table
above records **12** stored for that page: the replay extracts page 337 alone,
while the real encode read it inside a chunked window covering its neighbours,
and the raw rather than the edited reading — a one-record difference between two
runs of the same defective rule, not a second defect.)

### The pairs it merges are different people

Measured over the 29 records the model returned for page 337 (`old` = shipped
`_record_similar`, `new` = the comparison proposed below):

| first kept | discarded as a duplicate | old | new |
|---|---|---|---|
| `António Francisco` | `António Fernandes` | 0.839 | 0.000 |
| `Ambrósio Fernandes` | `Ambrósio de Barros` | 0.837 | 0.000 |
| `António Lopes` | `António Cordeiro` | 0.814 | 0.000 |
| `António Lopes` | `António Alvres` | 0.767 | 0.000 |
| `Afonso de Lucena` | `Álvaro Dias` | 0.762 | 0.000 |
| `Afonso de Lucena` | `Alonso González` | 0.750 | 0.000 |
| `Amador Gonçalves` | `Xynda Amador` | 0.756 | 0.000 |
| `Amdré Douria` | `Adam` | 0.753 | 0.000 |

Every one is a distinct entry with its own number in the catalogue. Note also
that 0.750 is exactly the threshold, so entries differing only in one surname
sit right on the boundary.

### A second, latent loss path (not measured)

`_record_key()` — the **exact**-duplicate key consulted before the fuzzy check —
is `kind + text/from/to/date/place`, with **no page**:

```python
parts = [str(rec.get("kind") or "record")]
for k in ("text", "from", "to", "date", "place"):
    …
```

Two records with byte-identical `text` on **different** pages therefore collapse
to one. Overlapping windows share pages, so the page is exactly what the key
should discriminate on; a recurring formula ("the same house description", a
notarial boilerplate line) recurring elsewhere in a volume is a different entry.
Not measured on this archive — flagged because it is the same silent-`continue`
shape and would not show up in any count.

## Root cause

1. `_record_similar()` compares the **whole record**. Shared boilerplate dominates
   the ratio, so the score measures "these two entries came from the same kind of
   page", not "these two entries are the same entry".
2. Nothing in the field set is treated as decisive. Two records whose catalogue
   **entry numbers differ** (`20` vs `21`) can still score ≥ 0.75 and merge,
   although a differing number proves they are different entries.
3. The drop is a bare `continue`: no warning, no counter, and `pha encode`
   reports success. Nothing surfaces the loss — it was found by an external
   completeness audit, not by pha.

## Suggested fix

Compare what **identifies** the entity, and let the numbering be decisive:

```python
_IDENTITY_FIELDS = ("name", "house_name", "number", "entry_number",
                    "target_number", "target_section", "target_kind", "index_doc")
_DISCRIMINATING_FIELDS = ("number", "entry_number", "target_number",
                          "target_section", "target_kind")

def _record_similar(a, b):
    if (a.get("kind") or "record") != (b.get("kind") or "record"):
        return 0.0
    ia, ib = ident(a), ident(b)                       # only _IDENTITY_FIELDS
    for k in _DISCRIMINATING_FIELDS:
        if k in ia and k in ib and ia[k] != ib[k]:
            return 0.0                                # entry 20 ≠ entry 21
    if ia and ib:
        return difflib.SequenceMatcher(None, join(ia), join(ib)).ratio()
    return difflib.SequenceMatcher(None, flat(a), flat(b)).ratio()   # old behaviour
```

and include the start page in `_record_key()`.

This keeps the collapse the guard was written for — one entry re-read by a later
pass, `Santo` vs `São`, still scores 0.75 — and never merges entries whose own
numbering differs. On page 337 it stores **29 of 29** instead of 13.

Two further suggestions, independent of the comparison:

1. **Count and report.** Print how many records were dropped as duplicates
   (`kept N of M, D same-page duplicates`) and warn loudly when a same-page drop
   occurs, so a future loss of this kind surfaces in the run rather than in an
   external audit.
2. **Make the same-page check opt-in per encoder.** `extraction_passes` and
   `overlap_pages` are what create the duplicate risk; an encoder with
   `extraction_passes: 1` and no window overlap has no legitimate same-page
   duplicates at all, and should not run the check.

A ready-to-run patch implementing the two code changes is in the archive that
found this, `jesuit-archive/local-tools/fix-pha-same-page-dedup.py` (dry-run by
default; `--write` applies it; `git checkout` reverts).

## Reproduction

```bash
# 1. encode, then ask the completeness audit (jesuit-archive tools/)
pha encode --doc 18
python3 dropbox/collections/schute-monumenta-historica-japonica/tools/audit-completeness.py \
        --doc 18 --encoder index_entry
#    -> pages SHORT of records: 6   (337: 29 anchors / 12 records, …)

# 2. prove the model was not at fault — one direct extraction of page 337
#    returns 29 records; replaying the store loop keeps 13.
```

## Related

- `pha-markdown-from-records-drops-shared-page-records-bug-report.md` (OPEN) —
  the same family and the same silent-`continue` shape, but a **different**
  defect: that one loses the *markdown file* of records that do share a page;
  this one loses the *record itself*, before any filter runs. Fixing that one
  will not recover these records, and vice versa. Both were found on
  `jesuit-archive` and both are hidden by a successful exit status.
- `pha-encoder-truncated-answer-loses-a-window-bug-report.md` (FIXED 0.40.0) —
  the other way an encoder pass loses records silently; that fix reports lost
  windows, which is what this report asks for at the dedup step.

## What landed (2026-10-23)

`src/personal_historical_archive/ingest.py`:

- **`_record_similar()` compares what identifies the entity.** It runs the ratio
  over `_IDENTITY_FIELDS` (`name`, `house_name`, `number`, `entry_number`,
  `target_number`, `target_section`, `target_kind`, `index_doc`) instead of every
  scalar field, so shared boilerplate (`title`, `catalogue`, `community`, a
  templated `source_text`) can no longer carry two different entries over the
  threshold. A differing value in `_DISCRIMINATING_FIELDS` (the entry's own
  number and its target) returns **0.0** outright — entries 20 and 21 can never
  be the same entry. A record with **no** identity field keeps the old
  whole-record comparison, because nothing better is available; a genuine re-read
  of one entry (`Santo` vs `São`, same number) still scores >= 0.75 and merges.
- **`_record_key()` includes the start page**, closing the latent second loss
  path: byte-identical text on a different page is a different entry, while the
  same entry re-read from an overlapping window still collapses.
- **The drop is counted and reported.** The store loop now tracks exact and
  same-page drops (`duplicates_dropped`, `same_page_duplicates`), prints
  `kept N record(s), dropped D duplicate(s) (S same-page)` when verbose, warns on
  stderr with up to three examples (`p.337 António Fernandes ~ António Francisco
  (score 0.0)`) and returns the counts in the encode result. `pha encode` prints
  the count in its per-document detail and a `warning:` line when any same-page
  drop happened — the silence in "Root cause" 3 is gone.
- The decision is isolated in `_same_page_duplicate()`, so the step that used to
  delete entries is testable without a model.

**Not implemented, deliberately:** the report's suggestion 2 (make the same-page
check opt-in per encoder). With the identity comparison the merge is already
restricted to genuine re-reads, and a new config knob would change defaults;
the counting above is what makes a future loss visible. Revisit only if a real
archive still shows a same-page collapse.

### Tests

`tests/test_ingest.py`: `_record_key` page discrimination, the entry-number
decisive rule (the report's own `António Francisco` / `António Fernandes` pair
now scores 0.0), a genuine re-read still merging, the no-identity fallback, and
`_same_page_duplicate` requiring the same page.

### For the affected archive

The records already lost are **not** recovered by the code change — they were
never stored. Re-run the encoder for the affected document
(`pha encode --doc 18 --reprocess`) after updating pha; the run now prints how
many duplicates it drops, so the completeness audit should stop reporting pages
SHORT.
