# Bug report — `markdown-from-records` silently drops every record that shares a page, and overwrites same-page records that share a file name

**Status:** open. **Found:** 2026-10-10 on `jesuit-archive`, **pha 0.42.0**
(`filters/markdown-from-records/filter.py`).
**Severity:** medium-high — **silent loss of the generated deliverable**: on
this archive **472 of 6 609 records (7.1 %)** produced no markdown file at all,
and `pha encode` reported the pass as successful.
**Written against:** pha 0.42.0.

## Summary

When the encoder emits records that **share a page** — several short letters on
one page, a receipt and an account on the same page — the artifact filter
cannot always split that page between them. `_page_slices()` splits a shared
page at the following record's `line_start` *when the record carries one*,
otherwise at a line that looks like the following record's header
(`_find_boundary`). When no boundary is found it assigns the **whole page to the
previous record** (a documented fallback), leaving the later record with an
empty slice. The filter then skips it:

```python
body = …
if not body:
    continue          # no file, no warning, no counter
```

So records that are complete in `records-<encoder>.json` (and searchable in the
DB) have **no markdown file**, and nothing in the run says so.

A second, independent loss: the output file name is
`<kind>-<page:04d>-<slug>.md`, with the slug derived from the record's text. Two
records on the same page whose truncated titles agree produce the **same name**;
the second silently overwrites the first.

## Evidence (jesuit-archive, 2026-10-10)

Measured with the shipped filter, replaying each collection's existing records
against its edited pages:

| collection | records | files written | empty slice (dropped) | name collisions | lost |
|---|---|---|---|---|---|
| `litterae-quadrimestres` | 3 058 | 2 603 | 356 | 99 | **455 (14.9 %)** |
| `DocHistMissPadPortOriente` | 1 189 | 1 181 | 8 | 0 | 8 |
| `documenta-indica` | 2 362 | 2 353 | 9 | 0 | 9 |
| **total** | **6 609** | **6 137** | **373** | **99** | **472 (7.1 %)** |

The `litterae-quadrimestres` case is the clearest: its letters are short, three
to five records per page is normal, and the encoder emits no `line_start`
(see "Encoder side" below). In `tomus-primus-1546-1552`: **361 records → 286
files**, 66 empty slices, 9 collisions. The dropped records were verified to be
present and complete in the records JSON; only the markdown is missing.

## Root cause

1. `_page_slices()` has no fallback that keeps the record: a failed boundary
   search yields an empty slice rather than "give the later record the whole
   page" (which would duplicate a little text but lose nothing).
2. The empty slice is handled by `continue`. The filter prints a warning only
   for records with **no usable page anchor**, not for this case, and the pass
   exits successfully.
3. Output file names are not made unique within a page, so a collision silently
   overwrites.

## Suggested fix

1. **Never drop a record.** When a shared page cannot be split, give the later
   record the whole page (or the page's tail) and mark its front matter
   accordingly (`pages: "437 (shared)"`). Duplicated words are recoverable;
   a missing file is not.
2. **Make the file name unique**: append `-2`, `-3` … on collision, or include
   the record's `line_start` (e.g. `letter-0437-l2-<slug>.md`).
3. **Count and report**: at the end of the pass print
   `wrote N/N records (M shared-page, K renamed)` and return a non-zero status
   (or at least a loud warning) when any record produced no file.
4. **Document the contract** in the encoder guidance: a record that shares a
   page with another SHOULD carry `line_start`; a filter that finds one must
   honour it (it does).

## Encoder side (context, not the defect)

None of the three encoders in use emits `line_start`, so the boundary heuristic
is the only defence — which is why the loss is concentrated where records are
short and dense. Even with a `line_start`-emitting encoder, fix 1–3 remain
necessary: the filter must not silently lose a record.

## Related

- `pha-markdown-from-records-page-contract-bug-report.md` (FIXED) — the
  `page` vs `page_start` resolution in the same function; a different defect,
  discovered earlier on the same collections.
- `pha-library-slug-timezone-bug-report.md` (§8 added 2026-10-10) — the other
  silent-artifact-loss path: records and edited pages in different library
  folders, so the filter writes 0 files and reports success.
