# pha enhancement requests - index

Last reviewed: 2026-10-23. Current release: pha 0.46.4.

This file is the map, not the detail: each row links to the full document.
Open includes anything not fully shipped. A partially implemented item stays
under Open and names what remains. Closed means the requested work is shipped
or otherwise resolved; the document remains for the record.

## Open

### Bugs

| Report | Status | Remaining |
|---|---|---|
| [pha-archive-pointer-loss-bug-report.md](pha-archive-pointer-loss-bug-report.md) | PARTLY FIXED | D1/D2/D4: defaults still seeded before an archive check, silent project-root fallback, no machine-level pointer; dsh-pha should treat configured: false as no archive. |
| [pha-latin-not-translated-thinking-disabled-bug-report.md](pha-latin-not-translated-thinking-disabled-bug-report.md) | PHA SIDE FIXED | F3 config-declared page-range editors; F5 a language guard that warns when a promised translation did not happen. |
| [pha-orphaned-model-lock-wedges-every-job-bug-report.md](pha-orphaned-model-lock-wedges-every-job-bug-report.md) | PARTLY FIXED | F1 age rule for a live-looking pid; F3 heartbeat; F4 unlock / orphan reporting; F5 an honest holder message. |
| [pha-markdown-from-records-drops-shared-page-records-bug-report.md](pha-markdown-from-records-drops-shared-page-records-bug-report.md) | OPEN (found 2026-10-10, 0.42.0) | Records that share a page lose their markdown file (isolated `continue`), and same-page records can overwrite one another; measured 472/6 609 records (7.1 %) on jesuit-archive. Proposed: never drop a record, unique file names, count and report. |
| [pha-same-page-record-dedup-deletes-distinct-entries-bug-report.md](pha-same-page-record-dedup-deletes-distinct-entries-bug-report.md) | FIXED (round 2, working tree 2026-10-23) — pending the archive re-encode | Round 1 (identity comparison, page in the key) took the Schütte index from 76 to 182 records but pp. 339/340 stayed SHORT: the fuzzy same-page check still ran **within one extraction window**. Round 2 adds `_dedupe_window()` (the guard compares only against records stored BEFORE the window) and puts the identity fields in `_record_key` (a text-less index row gave every entry on one page the same key — found while writing the regression test). Four new tests in `tests/test_ingest.py`. Remaining: release it, then `pha encode --doc 18 --reprocess` for `index_entry` and `person` and re-run `audit-completeness.py --doc 18` (pp. 339/340 must reach 37 and 28). |
| [pha-library-slug-timezone-bug-report.md](pha-library-slug-timezone-bug-report.md) | FIXED — F1 + F2 + F4 (§9); duplicates consolidated 2026-10-11/12 | The folder name is stored (`documents.library_slug`), writes/reads resolve before using it, and `pha status`/`pha doctor` report a `done` document with no library folder. F3 (UTC) dropped as unnecessary. The six two-dated folders on jesuit-archive were consolidated (793 files moved into the canonical folder, remainder parked in `bin/`), and `pha prune --library-variants` removed 3 provably redundant bare aliases. Remaining: the `markdown-from-records` 0-file warning (§8, its own report) and the 16 refused folders that hold a real reading. |

### Enhancements

| Request | Status | Summary and remaining work |
|---|---|---|
| [pha-post-filter-replay-enhancement-request.md](pha-post-filter-replay-enhancement-request.md) | Draft | Replay post filters without re-running the model; procedure proved by hand. |
| [pha-notes-search-enhancement-request.md](pha-notes-search-enhancement-request.md) | IMPLEMENTED (unreleased) | The notes are indexed (`notes`/`notes_fts`) and searched: `--source pages/records/notes/all`, `pha notes`, `pha_search(source=...)`, and the search skill offers them as memory. See §7 for what shipped vs the draft. |
| [pha-encoder-tools-enhancement-request.md](pha-encoder-tools-enhancement-request.md) | PARTLY IMPLEMENTED | The markdown-from-records artifact filter shipped; the structure prescan is still open. |
| [pha-handoff-enhancement-request.md](pha-handoff-enhancement-request.md) | SHIPPED (0.29.0) - follow-on open | Two-machine hand-over works; the section 12 render gap remains: an unscanned hand-out can come home with no page images. |
| [pha-stage-extends-enhancement-request.md](pha-stage-extends-enhancement-request.md) | Draft | Compose prompt files with extends (base plus delta). |
| [pha-skills-zotero-backlinks-enhancement-request.md](pha-skills-zotero-backlinks-enhancement-request.md) | IMPLEMENTED in this change (unreleased) | The bundled `pha-zotero-bibliography` skill gains the two-way backlinks (`record_id` = Zotero item key; `pha-doc`/`pha-slug`/`pha-path` in the item's `extra`; a `linked_url` child for the viewer), the local-API write-authorization flow it never had, and the two traps (document page number, never a `library/` path). §8.1 resolved: the archive-local `zotero-export/` staging text is stale — the local API's attachment `/file` redirect gives the PDF's path directly, so Route A now imports the file and the RDF export is explicitly the fallback. |

### Proposals

| Proposal | Status | Summary |
|---|---|---|
| [SEARCH_WEB_SPEC.md](../SEARCH_WEB_SPEC.md) | For decision | Read-only search web mirror and UI. |
| [WEB_INTERFACE_PLAN.md](../WEB_INTERFACE_PLAN.md) | For decision | Web interface plan. |
| [VSCODE_EXTENSION_SPEC.md](../VSCODE_EXTENSION_SPEC.md) | For decision | VS Code extension spec. |
| [VLM_BENCHMARK_PLAN.md](../VLM_BENCHMARK_PLAN.md) and [VLM_BENCHMARK_INFRA_PLAN.md](../VLM_BENCHMARK_INFRA_PLAN.md) | Proposal, not implemented | Separate repository benchmark plan. |

## Closed

### Bugs

| Report | Status |
|---|---|
| [pha-duplicate-edited-variants-bug-report.md](pha-duplicate-edited-variants-bug-report.md) | FIXED |
| [pha-edit-staleness-trim-mismatch-bug-report.md](pha-edit-staleness-trim-mismatch-bug-report.md) | FIXED in 0.37.1 |
| [pha-embed-loss-bug-report.md](pha-embed-loss-bug-report.md) | FIXED |
| [pha-encoder-truncated-answer-loses-a-window-bug-report.md](pha-encoder-truncated-answer-loses-a-window-bug-report.md) | FIXED in 0.40.0 and 0.40.1 |
| [pha-filter-signature-mismatch-bug-report.md](pha-filter-signature-mismatch-bug-report.md) | FIXED |
| [pha-installed-wheel-missing-schema-bug-report.md](pha-installed-wheel-missing-schema-bug-report.md) | FIXED |
| [pha-markdown-from-records-page-contract-bug-report.md](pha-markdown-from-records-page-contract-bug-report.md) | FIXED |
| [pha-reindex-doc-flag-does-not-accumulate-bug-report.md](pha-reindex-doc-flag-does-not-accumulate-bug-report.md) | FIXED in 0.40.2 |
| [pha-reindex-skips-non-done-documents-bug-report.md](pha-reindex-skips-non-done-documents-bug-report.md) | FIXED |
| [pha-request-stall-timeout-bug-report.md](pha-request-stall-timeout-bug-report.md) | FIXED |
| [pha-review-scope-bug-report.md](pha-review-scope-bug-report.md) | FIXED |

### Enhancements

| Request | Status |
|---|---|
| [pha-record-search-enhancement-request.md](pha-record-search-enhancement-request.md) | Phase 1-3 committed and released (keyword, semantic and fielded record search) |
| [pha-encoder-structure-register-enhancement-request.md](pha-encoder-structure-register-enhancement-request.md) | Implemented and released in 0.42.0 |
| [pha-whats-new-enhancement-request.md](pha-whats-new-enhancement-request.md) | SHIPPED in 0.43.0 (packaged CHANGELOG.json, `pha whatsnew`, generator and update integration) |
| [pha-filters-enhancement-request.md](pha-filters-enhancement-request.md) | SHIPPED: stage filters plus the markdown-from-records artifact filter |
| [pha-per-server-model-lock-enhancement-request.md](pha-per-server-model-lock-enhancement-request.md) | SHIPPED in 0.28.0 |
| [pha-stable-page-addresses-enhancement-request.md](pha-stable-page-addresses-enhancement-request.md) | SHIPPED |
| [pha-page-navigation-enhancement-request.md](pha-page-navigation-enhancement-request.md) | SHIPPED |
| [pha-serve-page-text-enhancement-request.md](pha-serve-page-text-enhancement-request.md) | IMPLEMENTED (2026-10-21) | The served page viewer shows the render plus raw transcription and the effective edited reading when available; optional page JSON deferred. |
| [pha-single-page-rescan-enhancement-request.md](pha-single-page-rescan-enhancement-request.md) | IMPLEMENTED (0.33.0+) |
| [pha-handoff-transport-enhancement-request.md](pha-handoff-transport-enhancement-request.md) | IMPLEMENTED (2026-09-26) |
| [pha-handover-editing-workflow-enhancement-request.md](pha-handover-editing-workflow-enhancement-request.md) | ALL SIX GAPS FIXED |
| [pha-model-response-resilience-enhancement-request.md](pha-model-response-resilience-enhancement-request.md) | IMPLEMENTED |
| per-page edit override (the edit-stage twin of the single-page re-scan) | IMPLEMENTED (2026-09-22); see AGENTS.md and README.md |

### Proposals

No closed proposals yet.

## Notes

- `dot-writing-dir-review.md` is a process review, not a feature request; it is
  not part of the open/closed status table.
- Stable IDs and long per-item verification notes were removed from this index;
  the linked document is the source of detail. The git history keeps the long
  table if a specific old note is needed.
- The previous rules are kept: a partially implemented item stays under Open;
  a released item moves to Closed.
