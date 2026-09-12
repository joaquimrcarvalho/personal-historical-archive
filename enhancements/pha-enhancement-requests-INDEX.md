# pha enhancement requests — index

These are design/spec docs (drafts) for additions to pha, written while
working on the **Documenta Indica** collection. They are grouped by feature
and meant to be read together; the first three are not implemented yet. The
**stable page addresses** request (Jesuit archive / Obsidian vault workstream) is
independent of the others and **is implemented**; **notes search** is a new draft
that builds on it.

One **bug report** (not an enhancement request) is filed in the section below the
table: `pha-review-scope-bug-report.md` — **fixed in 0.18.0** (scoped `pha
review`, plus `--all` and `--unset`). With that unblocked, the stage filters
are next in line; they were partly gated on re-running stages over
human-reviewed pages.

| doc | feature | one-line summary |
|---|---|---|
| `pha-filters-enhancement-request.md` | **Stage filters** | `pre`/`post` text filters around a stage's model — a stage becomes `input → pre filters → rules model → post filters → output`. Deterministic, chainable scripts (repo ships generic ones; collections add their own). `line-numbers`, OCR-separator stripping, whitespace/footnote-marker cleanup, hyphen joining. Per-stage scope table + `editor.pre` vs `palaeographer.post` guidance. |
| `pha-stage-extends-enhancement-request.md` | **Prompt composition (`extends`)** | Let a rules file be "base rules + delta" (`extends:`/`include:` front matter), composed at load time so shared editor/palaeographer/encoder bodies live in one place (e.g. `latin-to-english-ocr` extends `latin-to-english`). Covers ordering, settings cascade, model-not-inherited, and base-file re-edit invalidation. |
| `pha-encoder-tools-enhancement-request.md` | **Encoder tools** | After an encode, pha runs collection-bundled *tools* that materialise artifacts from the records (e.g. `markdown-from-records`: one markdown file per document/section). Also documents the model-assisted entry detection, character-aware chunking, and the collection **structure prescan** (§3.4: per-document layout register deriving page filters/prompt blocks per volume). **Merged**: the artifact/`markdown-from-records` part is planned as an `encoder.post` **artifact filter** in `FILTERS_PLAN.md`; the prescan part is not yet planned. |
| `pha-stable-page-addresses-enhancement-request.md` | **Stable page addresses & render serving** | A re-scan-proof way to *link to* a page from outside pha. One canonical `slug` derived from the dropbox-relative path (no date, no hash, unlike `documents.id` / the dated library folder / `renders/<sha256>/`); `pha cite` naming the exact *filled* variant; `pha page --json` gaining `slug`/`rel_path`/`sha256`/`render`/`variants`; and `pha serve` — a read-only loopback endpoint with stable `/doc/{slug}/p{page}.jpg` URLs that resolves the current sha per request. Motivated by Obsidian footnotes; complements `WEB_INTERFACE_PLAN.md` (whose API surface has no render route) and would let `dsh-pha`'s `/pha/pageImage` return bytes instead of a data URL. **Implemented**: `addresses.py` (slug/rel path/render/variants), `pha cite`, `pha serve`, the new `pha page --json` fields, plus tests. |
| `pha-notes-search-enhancement-request.md` | **Search the notes folder** | Make `pha search` cover `notes/`: a separate `notes` + `notes_fts` + embeddings index (notes are NOT `documents` rows, so `pha status`/`export`/bundles/review stay clean), mtime-based reindex from `pha scan`/`reindex`, `--source archive\|notes\|all`, and a `kind` discriminator in results so the PHA view opens a note hit through its existing `openNote`. Rejects modelling notes as documents and rejects indexing the whole Obsidian vault. |

## Bug reports

| doc | area | one-line summary |
|---|---|---|
| `pha-review-scope-bug-report.md` | **`pha review`** | **FIXED (0.18.0).** `pha review` stamped the *whole* library as **reviewed** instead of only the pending files, so one run froze the archive against any later `pha scan`/`pha edit` — **even `--reprocess`**. Now imports only the pending set; `--all` keeps the blanket behaviour as an opt-in; `--unset [--doc N [--page P]]` lifts the stamp (text kept) so a frozen archive is recoverable. Reproduced on 0.17.0: 14 572 pages stamped after `pha status` had reported **5** pending. |

## Implementation plans

- [`FILTERS_PLAN.md`](../FILTERS_PLAN.md) — the stage-filter framework,
  including **artifact filters** (records→markdown) and the reference filter
  set. Absorbs the encoder-tools runner (owner ruling: one mechanism).
- [`ENCODER_TOOLS_PLAN.md`](../ENCODER_TOOLS_PLAN.md) — **superseded** by
  `FILTERS_PLAN.md` (kept for history).

## How they fit together

The three features are complementary and can land independently:

1. **Filters** (this one, most general) — mechanical, source-specific text
   shaping around a model. It removes OCR cleanup and line-number concerns
   from prompts and gives per-collection opt-in (`editor.pre: [line-numbers]`).
2. **`extends`** — avoids duplicating the shared model-prompt body when a
   variant still needs different *prompt* rules (the judgment layer), after
   the mechanical bits have moved into filters.
3. **Artifact filters + prescan** — make `pha encode`'s JSON records usable
   (per-document markdown, as an `encoder.post` artifact filter once filters
   exist) and make multi-volume layout data-driven.

Suggested reading order: filters → extends → artifact filters/prescan, since
filters subsume the OCR-cleanup that `extends` and the tools were partly
motivated by. Any collection can adopt a subset.

**Notes search** is independent of those three: it reuses the existing
chunk/FTS/embedding machinery but adds a new *source* (the `notes/` folder), and
it pairs with the stable-page-addresses work — notes cite the slug and can embed
the render URL.

## Reference implementation notes (in the archive, not the pha repo)

- Documenta Indica editors and encoders live under
  `dropbox/collections/documenta-indica/` (`editors/latin-to-english.md`,
  `editors/latin-to-english-ocr.md`, `encoders/`, `prescan/doca_prescan.py`).
- The Documenta Indica margin **line-numbers** are the motivating case for a
  `line-numbers` filter; the edited pages show them currently leaking as bare
  numbers (see `editors/latin-to-english.md` §"Line numbers of the edition").
