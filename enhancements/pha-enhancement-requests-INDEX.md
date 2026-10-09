# pha enhancement requests - index

Last reviewed: 2026-10-08. Current release: pha 0.42.1.

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

### Enhancements

| Request | Status | Summary and remaining work |
|---|---|---|
| [pha-post-filter-replay-enhancement-request.md](pha-post-filter-replay-enhancement-request.md) | Draft | Replay post filters without re-running the model; procedure proved by hand. |
| [pha-notes-search-enhancement-request.md](pha-notes-search-enhancement-request.md) | Draft | Search the notes folder with its own index and --source archive/notes/all. |
| [pha-encoder-tools-enhancement-request.md](pha-encoder-tools-enhancement-request.md) | PARTLY IMPLEMENTED | The markdown-from-records artifact filter shipped; the structure prescan is still open. |
| [pha-handoff-enhancement-request.md](pha-handoff-enhancement-request.md) | SHIPPED (0.29.0) - follow-on open | Two-machine hand-over works; the section 12 render gap remains: an unscanned hand-out can come home with no page images. |
| [pha-stage-extends-enhancement-request.md](pha-stage-extends-enhancement-request.md) | Draft | Compose prompt files with extends (base plus delta). |
| [pha-serve-page-text-enhancement-request.md](pha-serve-page-text-enhancement-request.md) | Stored for later | The served page viewer gains the raw transcription and the effective edited reading beside the image. |
| [pha-whats-new-enhancement-request.md](pha-whats-new-enhancement-request.md) | Implemented in working tree, not committed | Packaged CHANGELOG.json, pha whatsnew, generator and update integration; pending commit/release. |

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
| [pha-library-slug-timezone-bug-report.md](pha-library-slug-timezone-bug-report.md) | FIXED |
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
| [pha-filters-enhancement-request.md](pha-filters-enhancement-request.md) | SHIPPED: stage filters plus the markdown-from-records artifact filter |
| [pha-per-server-model-lock-enhancement-request.md](pha-per-server-model-lock-enhancement-request.md) | SHIPPED in 0.28.0 |
| [pha-stable-page-addresses-enhancement-request.md](pha-stable-page-addresses-enhancement-request.md) | SHIPPED |
| [pha-page-navigation-enhancement-request.md](pha-page-navigation-enhancement-request.md) | SHIPPED |
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
