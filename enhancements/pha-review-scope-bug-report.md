# Bug — `pha review` stamps the whole library as reviewed (freezing the archive)

**Status:** **FIXED** (2026-09, pha 0.18.0). See §10 for what changed. The
report below is kept as written against 0.17.0.
**Severity:** high — after one `pha review` the archive can no longer be
re-transcribed or re-edited, and there is no CLI way to undo it.
**Motivating incident:** importing 5 pending corrections into the
`jesuit-archive` (34 documents, 14 572 pages, 14 104 page edits).

## 1. Summary

`pha review` imports corrections from the library markdown files, but it stamps
**every** page and edit it reads as `reviewed` — not only the files that actually
changed. Because a reviewed row is never processed again (the check runs *before*
the `--reprocess`/`force` flag), a single `pha review` run freezes the entire
archive.

## 2. Steps to reproduce

1. Have an archive where nothing has been reviewed yet:
   `SELECT COUNT(*) FROM pages WHERE reviewed_at IS NOT NULL` → `0`.
2. Change nothing at all (or touch a single library page file).
3. Run `pha review`.
4. Observe that every page and every edit is now stamped.

## 3. Expected

Only the **pending** files — the ones `pha status` reports as *"corrections in
the library files that are not imported yet"*, i.e. whose mtime is newer than
pha's last write, the same set `pending_review_files()` computes — should be
imported and stamped. All other rows should be left untouched.

## 4. Actual

```
$ pha status
  ✏️  5 page(s) with corrections in the library files that are not imported yet.

$ pha review
reviewed: 14746 transcription page(s), 25754 edit(s) (skipped 16 unparsed files)

$ sqlite3 archive.db "SELECT COUNT(*), SUM(reviewed_at IS NOT NULL) FROM pages"
14572|14572        -- every page, although only 5 were pending

$ sqlite3 archive.db "SELECT COUNT(*), SUM(reviewed_at IS NOT NULL) FROM page_edits"
14104|14104
```

Before the run, `SELECT COUNT(*) FROM pages WHERE reviewed_at IS NOT NULL` was
`0`: not one page had been hand-corrected, yet all 14 572 became "human
corrections" from pha's point of view.

## 5. Impact

- `pha scan` can never re-extract an existing page and `pha edit` can never
  re-edit an existing edit again — **even with `--reprocess`**, because the
  reviewed check precedes the force flag:
  - `ingest.py` (`scan_once`, per page):
    `if page["reviewed_at"]: continue  # a human corrected this page; never re-extract over it`
    comes **before** `if page["status"] == "done" and not force: continue`;
  - `ingest.py` (`_needs_edit`):
    `if edit_row is not None and edit_row["reviewed_at"]: return False`
    comes **before** `if reprocess: return True`.
- The documented follow-up workflow breaks. For a *transcription* correction the
  README prescribes `pha review` → `pha edit` ("re-runs the editor for just that
  page"). But the same `pha review` also stamps that page's **edit** as reviewed,
  so `pha edit` refuses to re-edit it: the raw text is corrected while the
  edited text keeps the old reading — permanently and silently.
- There is no `pha unreview` (or any flag) to clear the stamp; recovery requires
  editing `archive.db` by hand, which `AGENTS.md` explicitly forbids.
- It blocks future work that needs the existing pages re-processed: applying the
  planned stage filters, or re-running a collection with a new palaeographer /
  editor / prompt. `FILTERS_PLAN.md` §8 already states the constraint ("A
  filter-change re-run must respect `reviewed` stamps exactly as the model stages
  do"), so the freeze would survive the filters landing too.

## 6. Root cause

`ingest.review_import()` walks **every** markdown file under `cfg.library` and
stamps unconditionally; there is no "is this file newer than what pha wrote?"
test:

```python
for p in sorted(cfg.library.rglob("*.md")):
    ...
    if variant.startswith("transcription-"):
        db.mark_page_reviewed(conn, page["id"], body)
    elif variant.startswith("edited-"):
        db.set_page_edit(conn, page["id"], editor, text=body, raw_sha=...)
        db.mark_edit_reviewed(conn, page["id"], editor, body)
```

The notion of "changed since pha last wrote it" already exists and is used by
`pha status` / `pha pending` (`pending_review_files()`); `review_import` simply
does not consult it.

## 7. Suggested fix

- Restrict both the import and the stamp to the pending set (compute it as
  `pending_review_files()` does, or compare each candidate file's mtime against
  the DB's last write for that page/edit and skip when it is not newer).
- Keep an explicit `pha review --all` (or `--force`) for the deliberate
  "review everything" case, so the broad behaviour stays available but opt-in.
- Provide a way to clear a reviewed stamp (e.g. `pha review --unset --doc N
  --page P`), since a mistaken blanket review is currently unrecoverable
  without hand-editing `archive.db`.
- Consider making the reviewed check respect `--reprocess` (or at least
  document loudly that it does not, in the `--reprocess` help text).

## 8. Workaround (manual, unsupported)

On a backup copy of the archive, clear the stamps and re-stamp only the genuinely
corrected rows, then re-run the edit pass for transcription corrections and
`pha reindex`:

```sql
UPDATE pages SET reviewed_at = NULL;
UPDATE page_edits SET reviewed_at = NULL;
-- re-stamp only the real corrections (example from the incident):
UPDATE pages SET reviewed_at = strftime('%s','now')
  WHERE (document_id=27 AND page_no=1) OR (document_id=29 AND page_no=1)
     OR (document_id=44 AND page_no=64);            -- transcription corrections
UPDATE page_edits SET reviewed_at = strftime('%s','now')
  WHERE page_id IN (SELECT id FROM pages
                    WHERE (document_id=19 AND page_no=53)
                       OR (document_id=21 AND page_no=44));  -- edited-variant corrections
```

Note that after clearing, an editor whose prompt file is newer than a page's edit
will also re-edit that page on the next `pha edit` — scope the `--path` of the
edit run accordingly.

## 9. Related

- `README.md` §"You corrected the TRANSCRIPTION / the EDITED text" — the
  documented review workflow the bug contradicts.
- `FILTERS_PLAN.md` §4/§8 — filter staleness and the reviewed-page constraint.
- `pha-filters-enhancement-request.md` §2.1 — where deterministic cleanup lives,
  and why re-running stages must stay possible.

## 10. Resolution (0.18.0)

Implemented as suggested in §7. `review_import()` (`ingest.py`) no longer
walks `library/**` unconditionally: it imports and stamps **only** the
`pending_review_files()` set — the same files `pha status` reports — so an
untouched page file can never be marked `reviewed` again. The confirmed
side-effects of the fix:

- **The round-trip works again.** A review of a changed `transcription-*` file
  no longer stamps the corresponding edit, so the documented
  `pha review` → `pha edit` sequence re-edits exactly the corrected page.
- **`pha review --all`** keeps the old broad behaviour as an explicit,
  documented opt-in (`_all_review_files()` walks the library as before).
- **`pha review --unset [--doc N [--page P]]`** (`unreview_import()` +
  `db.clear_page_reviewed()` / `db.clear_edit_reviewed()`) clears the stamp
  without touching text, so an already-frozen archive is recoverable in place
  — no hand-editing of `archive.db`. `--page` requires `--doc` so a stray flag
  cannot unstamp an entire archive.
- **Documented**, not changed: a `reviewed` row still outranks `--reprocess`
  (that ordering is what protects human corrections); `--unset` is the
  supported way to force a stage over human-touched text. README and
  `AGENTS.md` both say so now.
- Tests: `test_review_import_only_stamps_pending_files`,
  `test_review_import_handles_edited_variant_scoped`,
  `test_unreview_import_scopes_page_and_restores_reprocess`
  (`tests/test_ingest.py`) and the `pha review` scope/`--all`/`--unset` cases
  in `tests/test_cli_pending.py`.

The incident archive (`jesuit-archive`) had already been recovered with the §8
workaround; its current stamps (4 pages, 2 edits) are genuine human
corrections and must be preserved, so no blanket `--unset` was run against it.
