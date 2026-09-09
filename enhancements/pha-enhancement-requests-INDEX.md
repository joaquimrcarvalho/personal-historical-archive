# pha enhancement requests — index

These are design/spec docs (drafts) for additions to pha, written while
working on the **Documenta Indica** collection. They are grouped by feature
and meant to be read together; none is implemented yet.

| doc | feature | one-line summary |
|---|---|---|
| `pha-filters-enhancement-request.md` | **Stage filters** | `pre`/`post` text filters around a stage's model — a stage becomes `input → pre filters → rules model → post filters → output`. Deterministic, chainable scripts (repo ships generic ones; collections add their own). `line-numbers`, OCR-separator stripping, whitespace/footnote-marker cleanup, hyphen joining. Per-stage scope table + `editor.pre` vs `palaeographer.post` guidance. |
| `pha-stage-extends-enhancement-request.md` | **Prompt composition (`extends`)** | Let a rules file be "base rules + delta" (`extends:`/`include:` front matter), composed at load time so shared editor/palaeographer/encoder bodies live in one place (e.g. `latin-to-english-ocr` extends `latin-to-english`). Covers ordering, settings cascade, model-not-inherited, and base-file re-edit invalidation. |
| `pha-encoder-tools-enhancement-request.md` | **Encoder tools** | After an encode, pha runs collection-bundled *tools* that materialise artifacts from the records (e.g. `markdown-from-records`: one markdown file per document/section). Also documents the model-assisted entry detection, character-aware chunking, and the collection **structure prescan** (§3.4: per-document layout register deriving page filters/prompt blocks per volume). |

## How they fit together

The three features are complementary and can land independently:

1. **Filters** (this one, most general) — mechanical, source-specific text
   shaping around a model. It removes OCR cleanup and line-number concerns
   from prompts and gives per-collection opt-in (`editor.pre: [line-numbers]`).
2. **`extends`** — avoids duplicating the shared model-prompt body when a
   variant still needs different *prompt* rules (the judgment layer), after
   the mechanical bits have moved into filters.
3. **Encoder tools + prescan** — make `pha encode`'s JSON records usable
   (per-document markdown) and make multi-volume layout data-driven.

Suggested reading order: filters → extends → encoder tools/prescan, since
filters subsume the OCR-cleanup that `extends` and the tools were partly
motivated by. Any collection can adopt a subset.

## Reference implementation notes (in the archive, not the pha repo)

- Documenta Indica editors and encoders live under
  `dropbox/collections/documenta-indica/` (`editors/latin-to-english.md`,
  `editors/latin-to-english-ocr.md`, `encoders/`, `prescan/doca_prescan.py`).
- The Documenta Indica margin **line-numbers** are the motivating case for a
  `line-numbers` filter; the edited pages show them currently leaking as bare
  numbers (see `editors/latin-to-english.md` §"Line numbers of the edition").
