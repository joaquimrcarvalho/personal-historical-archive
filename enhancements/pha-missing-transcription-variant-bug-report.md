# Bug report — a finished document can lose its `transcription-*` variant while keeping its edited pages, and no check says so

**Status:** **FIXED in the working tree (unreleased)** — see §4.
**Found:** 2026-11-13, on `jesuit-archive`, from a user report: pressing **Edit**
on the **raw** text of doc 67 p. 53 (the *Memorial das Missões*, BPE Códice
CXV 2-8, nº9) failed with

```
pha open failed: no library file on disk for bpe-memorial-das-missoes-1541-1657.pdf
page 53 — run `pha scan`/`pha export` first
```

while **Edit** on the same page's **edited** text opened the file normally.
pha `0.49.0` (`uv tool` install of git `main`, commit `10c799a`).

## 1. The incident

Document 67 is `done`: 53 pages, all with `raw_text`, `pages.exported_at` set on
2026-09-18 05:20 UTC. Its library folder held **only** the edited variant:

```
library/collections/archives/bpe/bpe-memorial-das-missoes-1541-1657_2026-09-17/
  edited-missiones-catalogue@deepseek-v4-flash/   53 pages   ← Edit works
  transcription-missiones-catalogue@minimax-m3/   absent     ← Edit errors
```

No pass is wrong at read time: `pha open` hands over an existing library file and
there is none for the raw variant. The text itself was never at risk — it is in
`archive.db`, and it is also still in the archive's own git history.

## 2. What removed it — two folders, then a cleanup that kept the wrong one

This is the timezone-dependent folder name of
[`pha-library-slug-timezone-bug-report.md`](pha-library-slug-timezone-bug-report.md)
meeting a cleanup that trusted the name:

1. **2026-09-18 05:20 UTC** — the scan wrote the 53 transcriptions into the
   folder the machine computed **then** (UTC+8): `…_2026-09-18/`.
2. **2026-09-19/20** — the edit pass ran in **UTC−3**, computed the name from
   the same `created_at` as `…_2026-09-17/`, and wrote the 53 edited pages
   there. The document now had two folders: pages in one, edits in the other.
   Those 106 `_2026-09-18` files were committed on 2026-09-25 (`63a77b6`).
3. **2026-10-05** — commit `2d4f6837a` *"library: archives — superseded
   variants retired"* deleted the **whole** `…_2026-09-18/` folder: 53
   `transcription-*` pages **and** 53 `edited-*` pages. The edited copy was
   already in `_2026-09-17`, so it survived; the transcription had no second
   copy, so it did not.

`pha prune --library-variants` is not implicated (it never touches a
`transcription-*` folder). The deletion was a git-level cleanup that treated a
**date sibling** of one folder as a superseded variant of it.

Before repairing, all 53 deleted bodies were compared against `pages.raw_text`:

```
git show 2d4f6837a^:…_2026-09-18/transcription-…/page-NNN.md
  vs  SELECT raw_text FROM pages WHERE document_id=67   →  53 match, 0 differ
```

Nothing was lost, only absent from disk.

## 3. Why nothing reported it

**F4's test was too weak.** `ingest.library_missing_documents()` asked *"is
there ANY `<stem>_*` folder"* — doc 67 had one, so `pha status` and
`pha doctor` called it healthy for six weeks. The interesting loss is one step
further from nothing: **the folder exists, but the base stage every later stage
derives from is gone.**

**The error named the wrong cure.** *"run `pha scan`/`pha export` first"* —
`pha scan` does not rewrite a finished document (nothing is stale), so it is not
a cure; and the cure that does work, `pha export --doc 67`, would have
**overwritten the user's un-imported one-word correction** on the edited p. 53
(`pha pending --doc 67` reported it: *"Vingueira" → "Vigueira"*), because export
rewrote every page from the database unconditionally.

## 4. What changed (working tree)

- **`ingest.library_missing_documents()`** now reports two reasons:
  `"folder"` (no `<stem>_*` folder at all — the old case) and
  `"transcription"` (a folder exists, but none of them carries a
  `transcription-*` variant). Same cheap algorithm: one listing per collection,
  a prefix test per row, one `iterdir` per candidate folder (cached).
- **`pha status`** shows `⚠ transcription missing` on the document line and its
  own line naming the cure; `--json` carries `library_missing[].reason`.
- **`pha doctor`** has a separate block for the transcription case. It also
  reads `doc_id` **or** `id` — the CLI passed ingest's `id` while the renderer
  read `doc_id`, so `pha doctor` would have raised `KeyError` on any archive
  with a missing library (latent; no such archive had been run through it).
- **`pha open DOC PAGE`** names the real cure and the document:
  ``run `pha export --doc N` to rewrite it from the database``.
- **`pha export` preserves un-imported corrections.** `write_document_pages()`
  and `write_edited_pages()` take a keyword-only `keep` set of page files; an
  export passes the files `pending_review_files()` reports, so those files are
  neither rewritten nor re-stamped (the mtime is what keeps them pending), and
  the run reports `kept N un-imported library correction(s)`. A scan/edit pass
  passes nothing — there the point is to write the new reading. This makes
  *"run pha export --doc N"* safe advice for the first time.

## 5. Repair performed on `jesuit-archive` (2026-11-13)

The 53 raw pages were written back from the database into the **existing**
folder (`write_document_pages`, i.e. exactly what `pha export --doc 67` writes
for the raw variant), which also froze `documents.library_slug` to
`bpe-memorial-das-missoes-1541-1657_2026-09-17`. The edited folder and the
un-imported p. 53 correction were not touched (still pending, still the only
copy). `pha page 67 53` now lists both variants and `page_file` resolves.

## 6. Tests

`tests/test_library_slug.py` (transcription reason, status wording, JSON
reason), `tests/test_cli_open.py` (the message names `pha export --doc N`) and
`tests/test_cli_export.py` (repair keeps the correction and its mtime, raw pages
restored). Full suite: 1139 passed.

## 7. What this does not fix

- A git-level cleanup can still delete a folder that holds the **only**
  transcription, because the tree cannot tell "superseded variant" from "the
  other date's folder that owns the base stage". The guard here detects the
  loss afterwards; the defence is to run `pha status` before such a sweep.
- The document still has a legacy folder name (`_2026-09-17`, from UTC−3) that
  this machine (UTC+8) would not compute; the stored `library_slug` now pins it,
  so it can never move again.
