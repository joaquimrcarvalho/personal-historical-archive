# Bug report — an edited page is never "up to date" when its transcript has surrounding whitespace

**Status:** **FIXED (F1, 0.37.1).** Found 2026-10-05 (pha 0.36.2, uv-tool install), while
investigating why the `edit` stage of `pha handoff work` re-translated ~half of
the 812 pages of `litterae-quadrimestres/tomus-tertius` that the immediately
preceding `scan` stage had just translated.
**Severity:** silent, recurring, paid — every later `pha scan` / `pha edit`
re-runs the editor's model for pages that are already up to date, for ever, and
the output is identical so nothing looks wrong.

## 1. Summary

`edit_document()` records the hash of the page's **stripped** transcript on the
edit row, but `_edit_needed()` compares it against the hash of the **unstripped**
transcript. `_raw_sha()` hashes its argument verbatim, so for any page whose
stored `pages.raw_text` begins or ends with whitespace the two can never agree.
Such a page is reported "the page was re-transcribed since the edit" on every
pass, the model is called again, and the same text is written back — which then
records the stripped hash again, so the page stays stale for ever.

The two call sites disagree about what "the reading" is; the defect is a
`strip()` on one side of a comparison and not the other.

## 2. Evidence (this archive)

Measured 2026-10-05 over every page that has both a transcript and an edit, by
recomputing both hashes and comparing them with the stored value:

| document | edited pages | raw text with surrounding whitespace | stored hash == sha256(**stripped**) | stored hash == sha256(**unstripped**) |
|---|---|---|---|---|
| Epistolae mixtae, Tomus II (#117) | 970 | **670** | 970 | 300 |
| Epistolae mixtae, Tomus IV (#119) | 950 | **694** | 950 | 256 |
| Litterae quadrimestres, tomus-tertius (#115) | 793 | **0** | 793 | 793 |

Two things follow, and both are exactly what the defect predicts:

1. **The stored hash is always the stripped one** — 100 % of rows in all three
   documents. The writer is consistent.
2. **The page can only be considered current if its text has no surrounding
   whitespace at all.** tomus-tertius (0 such pages) is the control: it matches
   under both readings and is therefore *not* stale. The two Epistolae volumes,
   where most pages do carry a trailing newline, are stale on 69 % and 73 % of
   their pages respectively — permanently.

Observed symptom in the field, same cause: during `pha handoff work` on
tomus-tertius (2026-10-04/05, Mac Mini) the `scan` stage translated all 800
readable pages, reported the document `done`, and the `edit` stage that followed
immediately re-translated **389 of them** — pages that had been translated
minutes earlier, with no change to anything. The re-edit reproduced the same
text, and the pages were still non-matching afterwards, which is what pointed
away from "the reading really changed" and led to this report.

## 3. Impact

1. **Recurring cost, unbounded.** Any page whose transcript has surrounding
   whitespace is re-translated on *every* pass over that document, for ever. On
   this archive that is 1 364 pages across two Epistolae volumes — at the
   measured ~1.5 min/page of a thinking-on editor, **~34 hours of model time**
   every time those volumes are edited in full; at ~5 s/page with thinking off,
   ~2 hours. The work is idempotent, so all of it is wasted.
2. **It makes `handoff work` pay twice.** The workflow is
   `scan` → `edit` → `encode`, and `scan` already runs the editor. The second
   stage is intended to catch pages the scan left unpublished; instead it
   re-translates the whole document on a worker machine, doubling the wall-clock
   cost of every hand-over (measured: ~2 h 40 m of the 6 h tomus-tertius spent
   on the Mini).
3. **It defeats targeted re-editing.** The documented pattern for correcting a
   late-C19th Latin volume — fast pass with `thinking: false`, then re-edit only
   the pages a language audit flags — silently becomes "re-edit those pages
   plus every page whose text ends in a newline".
4. **Invisible.** Both hashes are plausible 64-hex strings, the rows say
   `status='done'` with no error, the served text is correct, and the audit that
   looks for translated/untranslated text finds nothing wrong. Only recomputing
   both hashes (or watching the model get called again with no input change)
   reveals it.

## 4. Root cause

`_raw_sha()` hashes what it is given, with no normalisation
(`ingest.py:119`):

```python
def _raw_sha(text: str) -> str:
    import hashlib
    return hashlib.sha256((text or "").encode()).hexdigest()
```

**The writer strips** (`ingest.py:2607`, then 2649 / 2684 / 2702):

```python
raw = (p["raw_text"] or "").strip()          # 2607
if not raw:
    continue
...
db.set_page_edit(conn, p["id"], resolved, text=out, raw_sha=_raw_sha(raw), ...)   # 2684
```

**The comparator does not** (`ingest.py:1798`, in `_edit_needed()`):

```python
if edit_row["raw_sha"] != _raw_sha(page["raw_text"]):
    return True  # page was re-transcribed since the edit
```

So the stored value describes `raw_text.strip()` while the check computes
`raw_text`. Trailing `\n` from the OCR/LiteParse output, or a leading blank
line, is enough to make the page permanently stale.

Note the intended semantics are not in doubt: the editor feeds the model
`raw` (the stripped text) and the edit describes that input, so the **stripped**
form is the correct fingerprint and the comparator is the side that is wrong.

The human-review import path writes the *unstripped* hash
(`ingest.py:2296`, `_raw_sha(page_row_raw(...))`), so the two writers disagree
with each other as well — harmless today only because `_edit_needed()` returns
`False` for a reviewed page before it reaches the comparison.

## 5. Suggested fix

- **F1 (preferred, smallest): normalise inside `_raw_sha()`** —
  `hashlib.sha256((text or "").strip().encode()).hexdigest()`. Both writers and
  the comparator then agree by construction, the writer's existing stored values
  (already the stripped hash) remain **correct and need no migration**, and the
  review path is normalised at the same time. One line, no call-site changes.
- **F2:** normalise at the comparison site
  (`_raw_sha((page["raw_text"] or "").strip())`) and at the review writer. More
  explicit, but leaves the trap in place for the next caller of `_raw_sha()`.
- Either way, **a regression test**: write an edit for a page whose `raw_text`
  ends in `"\n"`, then assert `_edit_needed(...)` is `False` on the next pass.
  (The existing suite has no case with surrounding whitespace, which is why this
  survived.)

No stored data needs repairing under F1. A global re-audit of 50,389 stored
edit hashes found 49,304 rows already matching `sha256(strip(raw))` (6,180 of
them with surrounding whitespace — the cohort that was falsely stale), and
1,085 rows matching neither form. Those 1,085 are genuinely changed readings
and remain stale after the fix, which is correct. Zero rows match only the
unstripped form, so no row flips from current to stale under F1.

## 6. How it was met here

Detected, not worked around: the re-edits were allowed to complete (they are
idempotent), and the audit that measures untranslated Latin was re-run
afterwards, so the finished volumes are correct. The operational rules drawn
from it:

- Treat the `edit` stage of `pha handoff work` as **expected to re-translate the
  document**, and budget for it; it is not a no-op and does not mean the reading
  changed.
- Do **not** read the per-document line `+ <doc> [<editor>] (N pages)` as the
  number of pages attempted — it counts pages whose text *changed*. On
  tomus-tertius it said `76 pages` while all 83 named pages had been rewritten
  (verified by timestamp).
- A quick local probe of "did the reading move?" is to recompute both hashes and
  compare, as in §2 — a page that mismatches under *both* readings has a genuine
  reading change, and one that mismatches only unstripped is this defect.

## 7. Related

- `enhancements/pha-filter-signature-mismatch-bug-report.md` — the same *shape*
  of defect (stored signature ≠ computed signature, so a deterministic stage
  re-runs for ever) in the filter chain rather than the raw-text hash. Fixed in
  `7136ece`; worth checking whether `filters_signature()` normalises whitespace
  for the same reason.
- `enhancements/pha-reindex-skips-non-done-documents-bug-report.md` — the other
  defect that made `handoff work`/`handoff fetch` look complete while silently
  doing nothing.
- `enhancements/pha-handover-editing-workflow-enhancement-request.md` — G5
  (`handoff work --resume`) plans from the DB. Note: `plan_work()` already
  strips before comparing `raw_sha` (`handoff.py:880-890`, since `62a764a`), so
  this defect does not mislead `--resume`. It is reached by `_edit_needed()`
  in direct `pha edit`, `pha scan`'s editor pass, and non-resume
  `handoff work`.
- Code: `ingest.py` `_raw_sha()` (119), `_edit_needed()` (1798),
  `edit_document()` (2607, 2649, 2684, 2702), the review import (2296);
  `db.set_page_edit()`, `db.get_page_edit()`.

## 8. Verification and fix (2026-10-05, 0.37.1)

- **F1 applied** — `_raw_sha()` now hashes `(text or "").strip()`, so the
  writer and `_edit_needed()` agree by construction.
- Regression test:
  `tests/test_filter_hooks.py::test_edit_needed_ignores_surrounding_whitespace_in_raw_text`
  covers spaces, newlines and tabs around the reading.
- Full suite: 925 passed.
- Global audit: 49,304 rows now match the fixed function; 6,180 surrounding-
  whitespace rows stop being stale; the 1,085 rows matching neither form remain
  stale because their raw text genuinely changed; 0 rows match unstripped-only,
  so no migration is needed.
- The §7 `--resume` sentence was corrected: `plan_work()` already normalised,
  so interrupted resumed hand-overs were not affected by this defect.
