# Bug report — `pha reindex --doc A --doc B …` silently reindexes only the last document

**Status:** **FIXED (F1/F3, 0.40.2).** Found 2026-10-06 while reindexing four volumes returned from a
hand-over.
**Severity:** silent partial work — and it is the tool's *own printed advice*, so
the failure is systematic: the operator believes N documents were reindexed when
one was, and the run exits 0.

## 1. Summary

`pha reindex` declares `--doc N` as a single-value option. Repeating it does not
accumulate; the last occurrence silently wins. `pha handoff fetch` closes by
printing a command in exactly that repeated form, so anyone who follows the
hand-over output reindexes one document out of four and is told nothing.

## 2. Evidence

The tool's own closing advice (archive `.lq-qa/fontes-fetch.log`, line 7), after
applying four returned volumes:

```
    pha reindex --doc 110 --doc 111 --doc 112 --doc 113
```

Run as printed, extended with the fifth stale document:

```
$ pha reindex --doc 110 --doc 111 --doc 112 --doc 113 --doc 114
    indexing 4311 chunks (4166 to embed, 145 reused) ...
  reindexed 1 document(s) (doc #114) [incremental: unchanged chunks reused]
  === exit=0
```

One document. No warning that four arguments were discarded. The four Fontes
volumes kept **zero** chunks; the intended work had to be redone as
`pha reindex --path collections/jesuit-early-authors/fontes-narrativi`
(2 096 chunks), which then reported `reindexed 4 document(s)`.

## 3. Impact

- **Silent.** Exit 0, a plausible one-line summary, no indication that anything
  was dropped. Nothing distinguishes "reindexed the five I asked for" from
  "reindexed the last one".
- **Self-inflicted by the tool.** The wrong command is printed by `handoff fetch`,
  so the mistake does not depend on the operator inventing it.
- **Cost here:** roughly an hour, and a window in which search over four
  returned volumes was empty while the operator had been told to index them and
  believed it was done.

## 4. Root cause

`--doc` is a scalar option: a second occurrence overwrites the first. The
hand-over message builds the multi-`--doc` form because repeating a flag is the
natural way to say "these four documents" — so the message and the parser
disagree about what the flag means. Neither side is wrong on its own; the pair
is.

Note the same shape as
`pha-encoder-truncated-answer-loses-a-window-bug-report.md`: a run that reports
success while part of the work was dropped. Silent partial results are the
recurring hazard, not crashes.

## 5. Suggested fix

- **F1 (preferred) — make `--doc` repeatable**, so the printed command works as
  written: `action="append"`, `nargs="+"`, or accept a comma-separated list.
  Check sibling options for the same assumption (`--page`?).
- **F2 — or fix the message** to print something runnable as-is:
  `pha reindex --path <collection>` (the form that worked), or one line per
  document.
- **F3 (defence in depth) — never discard an argument silently.** If an option
  that cannot accumulate is given more than once, say so:
  `warning: --doc given 4 times; using the last (114)`. Whichever of F1/F2 is
  chosen, this one is cheap and closes the whole class.

## 6. How it was met here

The four Fontes volumes were reindexed with `--path`; the resulting counts are
2 096 / 1 557 / 1 929 / 2 057, plus tomus-sextus at 4 311. Search over the
returned translation works.

## 7. Related

- `pha-encoder-truncated-answer-loses-a-window-bug-report.md` — the same hazard
  shape (partial work, exit 0), and this session's other instance of it.
- `pha-handoff-transport-enhancement-request.md` — the hand-over whose closing
  message prints the non-runnable command.


## 8. Fix and verification (2026-10-06, 0.40.2)

- **F1 shipped.** `pha reindex --doc` is now repeatable
  (`action="append"`), and `--page` is repeatable too. The command printed by
  `handoff fetch` therefore runs as written:
  `pha reindex --doc 110 --doc 111 --doc 112 --doc 113`.
- `reindex_all()` resolves EVERY named id and refuses before any work when one
  is missing, rather than silently doing a subset.
- **F3 shipped by construction.** Repeating the flags no longer discards any
  value; `--page` without a document, or with more than one document, is an
  explicit error.
- Evidence verified: `.lq-qa/fontes-fetch.log` line 7 prints the repeated form;
  the scalar parser behaviour was reproduced, then fixed. Regression tests:
  `tests/test_cli_reindex.py::test_reindex_repeated_doc_flags_accumulate`,
  `tests/test_cli_reindex.py::test_reindex_page_needs_exactly_one_doc`, and
  `tests/test_ingest.py::test_reindex_all_multiple_docs_scopes_to_all_named`.
