# Bug report — the library folder name is derived from a timestamp in the machine's current timezone, so a document's transcriptions become invisible (and can be orphaned) when the machine's zone changes

**Status:** **F1 + F2 + F4 FIXED** (2026-10-10, §9) and the existing duplicate
folders **consolidated** on jesuit-archive (2026-10-11/12, §9). F3 deliberately
dropped (the stored name makes it unnecessary). Still open: the
`markdown-from-records` 0-file warning (§8, tracked by its own report) and the 16
`edited-*` folders that hold a genuine alternate reading (an archive decision,
not a defect).
**Found:** 2026-09-29, on the `jesuit-archive` machine, from a
user report that **document 54 appeared fully processed in the database but had
no library files**. pha `0.34.x` (editable checkout; a `uv tool` install of the
same package also present).

## 1. The incident

Document 54 (`franco-imagem-virtude-coimbra-1719-v1.pdf`, collection
`franco-imagens`) is `done`: 880 pages, 880/880 with `raw_text`, 880 edit rows,
3 963 indexed chunks, every page and edit carrying an `exported_at`. `pha
status` reported it as finished, exactly as expected.

There was **no library folder for it at all**, and no error anywhere.

```
$ ls library/collections/franco-imagens/
Franco et al. - 1714 - …_2026-09-13/          # doc 52
Franco et al. - 1717 - …_2026-09-13/          # doc 53
franco-imagem-virtude-coimbra-1719-v2_2026-09-14/   # doc 55
                                              # ← doc 54: nothing
```

The text was never at risk: it was in `archive.db`, and an identical copy was
still in the archive's own git repository (see §4). **11 further `done` documents
were in the same state** — not just doc 54:

| doc | collection | pages | folder git holds | folder this machine computes |
| --- | --- | --- | --- | --- |
| 38, 39, 40 | DocHistMissPadPortOriente vol09–vol11 | 2 654 | `_2026-09-10` | `_2026-09-09` |
| 45 | medina-docs-japon 1547-1557 | 864 | `_2026-09-11` | `_2026-09-10` |
| 47 | pfister-notices t1 | 618 | `_2026-09-11` | `_2026-09-10` |
| 54 | franco-imagens Coimbra v1 | 880 | `_2026-09-14` | `_2026-09-13` |
| 77, 78, 79, 80, 81 | documenta-indica 1563-1577 | 4 775 | `_2026-09-20` | `_2026-09-21` |
| 92 | documenta-indica 1595-1597 | 533 | `_2026-09-21` | `_2026-09-22` |

10 324 pages of finished work whose library files were on disk under a name this
machine no longer looks for.

## 2. D1 — the folder name is not stable: it depends on the machine's timezone

`ingest._doc_slug()` (`src/personal_historical_archive/ingest.py:481`) builds the
library folder name as

```python
stem = Path(doc["path"]).stem
date = datetime.datetime.fromtimestamp(doc["created_at"]).strftime("%Y-%m-%d")
return f"{stem}_{date}"
```

`datetime.fromtimestamp` renders the stored UTC epoch **in the local timezone of
whatever machine is running pha**, and the slug is **not stored** — it is
recomputed on every run. A document created at 20:07 UTC therefore lives in

- `…_2026-09-14` under UTC+8,
- `…_2026-09-13` under UTC+1 or UTC−3.

The archive has been operated from more than one zone — its own git commits carry
`-0300` (2026-09-25) and `+0100` (2026-09-26) — so the folders on disk were
written under different zones over time.
Computed against the current zone, **every one of the 12 missing folders matches
the name under UTC+8 or UTC−3, and none matches the name computed here**:

| doc | `created_at` (UTC) | slug now (+1) | slug at +8 | slug at −3 | folder on disk |
| --- | --- | --- | --- | --- | --- |
| 38 | 2026-09-09 17:16 | `_09-09` | `_09-10` | `_09-09` | `_09-10` |
| 45 | 2026-09-10 18:09 | `_09-10` | `_09-11` | `_09-10` | `_09-11` |
| 54 | 2026-09-13 20:07 | `_09-13` | `_09-14` | `_09-13` | `_09-14` |
| 77 | 2026-09-21 00:10 | `_09-21` | `_09-21` | `_09-20` | `_09-20` |
| 92 | 2026-09-22 00:19 | `_09-22` | `_09-22` | `_09-21` | `_09-21` |

The affected set is exactly the documents whose `created_at` falls within the
zone offset of midnight — which is why 77 documents are fine and these 12 are
not. Nothing about the *documents* differs; only the wall clock the timestamp was
rendered on.

## 3. D2 — the code disagrees with itself about which folder is the document's

Because the slug is recomputed per call, two functions in the same module can
address different directories for one document:

- `write_document_pages()` (`ingest.py:1694`) and `write_edited_pages()`
  (`ingest.py:1823`) write to `cfg.library / dir_path / _doc_slug(doc) / …`
  — the **computed** name;
- `remove_library_artifact()` (`ingest.py:499`) deletes `_doc_slug(doc)`
  — the computed name, so it **cannot** delete the folder that actually exists
  under another zone's name (this is why the loss was not caused by pha);
- `_pages_dir_for()` (`ingest.py:1679`) returns `None` when the computed name is
  not a directory, so **filters and artifact stamps silently lose the pages dir**
  (`ctx.pages_dir_edited` becomes null) for an affected document;
- `_library_doc_dir()` (`ingest.py:1908`) *does* fall back to globbing
  `f"{stem}_*"` and taking the newest — so `pha status`/pending-detection keeps
  working while the writing functions do not.

The practical consequences, in increasing severity:

1. a re-processing pass writes a **second** folder beside the real one — the same
   "one logical variant in two directories" shape already reported in
   `pha-duplicate-edited-variants-bug-report.md`;
2. the folder a human is told to edit (and that filters read) is not the one pha
   would write;
3. once the mis-named folder is gone (deleted by hand, by a sync, or by any
   outside cleanup — see §4) the document is still `done` and **nothing in pha
   says the library is missing**, because no pass recomputes library paths for a
   finished document.

## 4. What actually removed the files (separate from D1)

The 12 folders were not removed by pha. They were removed **from the working tree
without a commit**, in the archive's own git checkout:

```
$ git status --short | grep -c '^ D'          # tracked, deleted in the worktree
28223
$ git diff --stat | tail -1
 28232 files changed, 279 insertions(+), 1812796 deletions(-)
```

`git log --diff-filter=D -- library/collections/franco-imagens` is **empty** —
no commit ever deleted them — and the parent directory mtimes put the event at
**2026-09-28 ~23:57**. The 28 223 files include genuinely superseded variant
folders of healthy documents (the deliberate "superseded variants retired"
maintenance visible in the commit log), so this reads as one broad cleanup that
also took the 12 mis-named-but-current folders, which no pha command would have
recognised as current. The exact tool is not recorded anywhere in the archive's
logs; a sync with `--delete`, a hand cleanup, or Finder are all candidates.

**The point for pha is not who deleted them** but that a `done` document can lose
its entire library and pha will not notice, because it never re-derives or
verifies that path after the pass that wrote it.

## 5. Evidence that the work was intact

Before repairing anything, the git copy of doc 54's folder was compared against
the database **page by page, all 1 760 files**:

```
transcription-default@minimax-m3:               880 match, 0 differ
edited-franco-imagem-virtude@deepseek-v4-flash: 860 match, 20 differ
```

The 20 "differences" are the normal `*waiting*` stub written for a page whose
edit row has no text (the DB value is the empty string) — i.e. the committed files
were a byte-faithful mirror of the DB, including its 20 unedited pages.

## 6. Repair performed (2026-09-29)

`pha export` already regenerates library files from the DB with no model call,
but it had **no scope**: it rewrote every document (~78 000 files) and would have
silently overwritten the one un-imported human correction (`pha status`: doc 19
p. 53) before `pha review` imported it. So `pha export` gained the same scoping
its sibling commands already have:

```
pha export [--doc N | --path collections/COLX]
```

(`cli.py:2383`; `--doc`/`--path` are mutually exclusive, `--path` reuses
`ingest._documents_under()`, a scoped run names each document and the folder it
wrote to.) It reads and writes only `library/`, takes no model lock, and leaves
the DB unchanged apart from `exported_at`.

The 12 documents were then exported one at a time. Verification afterwards:

- every document in the archive now has its computed library folder, except the
  four `fontes-narrativi` volumes, which are `processing` hand-overs with
  **0 page rows** (there is nothing to export — correct);
- **20 648 page files across the 12 documents re-checked against the DB: 0
  differences**;
- `pha status` reports no new corrections — still only the pre-existing doc 19
  p. 53 — and `pha pending --doc 54` is empty.

## 7. Proposed fix (needs a decision, not a silent change)

Recomputing the slug is the defect; changing the formula alone is not enough,
because 77 documents' folders were written under the *current* zone and a new
formula would move all of them at once. Ordered by preference:

- **F1 — store the slug when the document is created.** Add
  `documents.library_slug` (nullable), set it on first ingest, and have
  `_doc_slug()` prefer the stored value, falling back to the computed one for
  rows that predate the column (the same read-old-rows pattern the rest of the
  DB uses). The path then travels with the row across machines and zones, which
  is what "version-safe folder" was meant to mean.
- **F2 — resolve before writing.** Have `write_document_pages()` /
  `write_edited_pages()` call `_library_doc_dir()` first (the `{stem}_*` glob
  that already exists) and write into the existing folder rather than creating a
  second one. This alone stops the duplicate-folder half of the bug for archives
  that already carry mis-dated folders, and is a natural companion to F1.
- **F3 — compute the date in UTC.** Removes the zone dependence but changes the
  name for every document whose `created_at` sits near midnight — do it only
  together with F1's stored value, or accept a one-off renaming pass.
- **F4 — make the loss loud.** `pha status` (and `pha doctor`) should report a
  `done` document with no library folder on disk — a one-line
  `⚠ #54 …: finished, but its library folder is missing — run pha export --doc 54`
  would have surfaced this the day it happened instead of by chance weeks later.

**Superseded-variant cleanup is not affected**: `pha prune --library-variants`
deliberately deletes only a bare `edited-<editor>` folder whose pages survive
elsewhere, and refuses one holding a different reading. The 28 223-file deletion
in §4 was not that command — the retired variants and the mis-dated current
folders went in the same sweep.

## 8. New manifestation, 2026-10-10 — an artifact filter writes 0 files and reports success (pha 0.42.0)

Found on a second archive series (`DocHistMissPadPortOriente`, 12 volumes), on
**pha 0.42.0** — i.e. still reproducible after the fix this report's index row
credits. Three volumes ended a re-encode pass with a `done` document, 84–103
records in the database, and **no markdown artifacts at all**:

| doc | library folders | records → segment files |
|---|---|---|
| 38 (vol IX) | `…_2026-09-09` (edited pages) **and** `…_2026-09-10` (records) | 103 → **0** |
| 39 (vol X) | idem | 84 → **0** |
| 40 (vol XI) | idem | 27 → **0** |

`pha encode --doc N --reprocess` wrote the records and created
`segments-documents/` + `segments-apparatus/` inside the **newer** folder, while
the edited pages lived in the **older** one. `ctx.pages_dir_edited` was
therefore null (D2 above) and `markdown-from-records` wrote 0 files. The pass
printed `encoded 2 document(s)` and exited 0.

Detection was manual: counting files against records. Regenerating with the same
filter pointed at the pages folder (no model calls) produced 104 / 77 / 28 files
immediately — nothing was wrong with the records or the pages, only with the
path resolution.

**Consequence to add to §3's list** (the most damaging form of D2): an
**artifact filter silently produces an empty deliverable** — the database looks
complete, `pha status` looks clean, and the human-readable output the archive
exists for is missing. F1–F4 all help; the cheapest guard is F4 plus a
filter-side warning when a pass writes 0 files for a document that has records
(analogous to the "skipped N records with no usable page anchor" warning the
same filter already prints).

## 9. What landed — F1 + F2 + F4 (2026-10-10)

Fixed in the working tree. F3 (compute in UTC) deliberately **not** done: it
would rename every folder whose `created_at` sits near local midnight, and with
F1 in place the name no longer depends on the clock, so it buys nothing.

**F1 — the name is stored.** `documents.library_slug` (new column; `db.migrate()`
adds it to an existing archive, `db.add_document()` writes it). `ingest_file()`
freezes it at creation (`_new_library_slug`), so a content-changed version still
gets a NEW name — the version-safe behaviour of §6 is unchanged.
`ingest._doc_slug()` returns the stored value and computes the old
timezone-dependent name (`_computed_doc_slug`) only for a row that predates the
column.

**F2 — resolve before writing and before reading.** One resolver,
`ingest._resolve_library_dir(cfg, doc)`, answers "where does this document's
output go":

1. a stored slug is authoritative;
2. a **legacy row** (no slug) joins the existing `<stem>_*` folder that already
   holds its `transcription-*` — preferring the name this machine computes when
   it is one of them — and is then **stamped** with that name
   (`_stamp_library_slug`), so the choice sticks;
3. else the computed name.

All four writers (`write_document_pages`, `write_edited_pages`,
`write_concatenated_file`, `write_records_file`) and `_library_dir_for` /
`remove_library_artifact` go through it, so a second folder for one document is
no longer produced. Reading was the other half: `_pages_dir_for()` and
`library_page_path()` now try the resolved folder **and then its siblings**
(`_doc_folder_candidates`), because the transcription and an edited variant can
sit in two folders — reading only the resolved one is what made the filter write
0 files. `_library_doc_dir()` is now the read-only view (resolved folder if it
exists, else None).

**F4 — the loss is loud.** `ingest.library_missing_documents(cfg, conn)` reports
every `done` document with no `<stem>_*` folder at all, cheaply (one directory
listing per collection, then a prefix test per row) and migration-safely (it
never names `library_slug` in SQL, since `pha status` opens the database
read-only and therefore runs no migration). `pha status` prints
`⚠ library folder missing` on the document line plus its own
`run pha export --doc N` line (kept off the truncated line on purpose), and
carries the same list as `library_missing` in `--json`; `pha doctor` prints a
`library` section. Deliberately **not** part of `pha doctor`'s `ok`/exit code:
that flag gates the engine checks scripts depend on, and a missing library is a
data condition to fix, not a broken install.

### Measured on jesuit-archive before and after

Six documents had two folders. `_pages_dir_for()` returned **None for all six**
on the raw transcription, and None for the edited variant on three of them
(38/39/40 — the §8 shape, whose records and segments live in the folder this
machine computes while the pages live in the other). After the fix every one
resolves: the resolver joins the folder holding the transcription
(`…_2026-09-09` for 38/39/40, `…_2026-09-21` for 89/90/91) and both variants are
found. `library_missing_documents()` reports 0 — no document is *entirely*
without a library, which is why §8's damage was invisible: the folders existed,
they were just not the ones pha read.

### The existing duplicates were consolidated (2026-10-11/12)

`scripts/consolidate_library_date_dupes.py` (dry-run by default) repaired the six
documents on `jesuit-archive`. The "duplicate" folders were **not** duplicates:
the folder the fixed resolver picks holds the pages, while the other held the
**encoder deliverables** (`records-*.json`, `concatenated-*.md`, `segments-*`,
`structure.json`, `.structure-*.sha256`, `.filter-stamps/`). Deleting them would
have destroyed the output §8 is about — so every file absent from (or differing
from) the canonical folder was **moved into it** (793 files), the `source:`
front-matter line of the moved `segments-*/*.md` was repointed at the new folder,
and the remainder — files already byte-identical to the canonical copy — was
parked in `bin/20261011-library-date-dupes/` (3 230 files, 16 MB) with a README
that states a `mv` rollback is only partial.

Verified afterwards: exactly one folder per document, both variants and their
pages still resolve, artifact counts unchanged (104/77/28/164/166/176 segment
files), `PRAGMA quick_check` ok, `library_missing` empty. The connection is
**read-only**: the repair writes no database row, and the folder name is stamped
by the next real scan/edit/encode pass.

The bare `edited-<editor>` aliases were handled by the tool that owns them —
`pha prune --library-variants` removed the three it could prove redundant
(`fernao-guerreiro` t1–t3, "identical to the database"), backed up first to
`bin/20261012-bare-edited-variants/`; it refused 16 that hold a genuine alternate
reading and kept 16 bare-only variants. A re-run is now a no-op.

### What is NOT fixed here

- **The filter-side 0-file warning** proposed at the end of §8 is not here: it
  belongs to `markdown-from-records`, tracked by
  `pha-markdown-from-records-drops-shared-page-records-bug-report.md`.
- **The 16 refused `edited-*` folders are a judgement call, not a defect**: they
  hold real readings the guard will never delete; inspecting them by hand is an
  archive decision.
- **F3** (UTC naming) — see above.

### Tests

`tests/test_library_slug.py` (13 assertions). Ten fail against the pre-fix
source; the two that pass cover the legacy fallback and the absence of the
`pha doctor` section, which is what they are for.

**Deviation from §7's wording**: F2 said "call `_library_doc_dir()` first (the
`{stem}_*` glob that already exists)". `_library_doc_dir()` is a read-only view
(it returns None when nothing exists), so the write path could not use it
directly; the precedence above keeps its glob but makes the folder holding the
transcription the tie-breaker, which is what stops the 38/39/40 shape from
gaining a third folder.

