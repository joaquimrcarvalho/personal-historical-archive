# Bug — one edited variant is exported twice, as `edited-<rules>` and `edited-<rules>@<model>`

**Status:** **FIXED (A + B, §8).** C — deleting the redundant bare folders on an
archive that already has them — remains a documented operator step (§6), not
automation. Found and measured on `jesuit-archive`, 2026-09-16.
**Severity:** medium-high — breaks `pha cite --edited` for **every affected
document**, and can serve a *stale* generation of the edited text as if it were
current.
**Written against:** pha 0.27.0.
**Motivating incident:** `pha cite 65 100 --edited` refused to cite a
freshly-ingested 479-page volume because the document carried two "filled"
edited variants — which are in fact the *same* variant written under two names.

## 1. Summary

`write_edited_pages()` derives its output directory from
`documents.editor_model` **as read at call time**
(`src/personal_historical_archive/ingest.py:1167`):

```python
out_dir = cfg.library / rel_dir / slug / (f"edited-{editor_id}" + (f"@{ed_model}" if ed_model else ""))
```

The edit path calls that writer in two places, and between them
`editor_model` changes from NULL to the model reference:

| where | what it does | directory produced |
|---|---|---|
| `ingest.py:1554` then `:1556` | on an editor change: `db.update_document(conn, doc_id, editor=resolved, editor_model=None)` — deliberately NULLs the model — **then** writes | `edited-<rules>` (front matter `model: null`) |
| `ingest.py:1735`, `:1742` | the incremental path: `db.update_document(..., editor_model=editor.model_ref or None)` then writes | `edited-<rules>@<model>` |

So **one logical variant lands in two directories**. The directory name encodes
transient bookkeeping state (whether the model had been recorded yet), not the
variant's identity.

Measured on `jesuit-archive` (41 documents carry an editor):

```
TRUE duplicate pairs   : 28   identical except the front-matter model: line
divergent pairs        :  6   same rules id, different content
bare-only variants     :  1
@model-only variants   :  7
```

The equivalent transcription directories show **no** duplication
(0 bare, 56 `@model`), because `write_document_pages()` runs after the
palaeographer/model is recorded. The defect is specific to the edited stage.

## 2. Symptoms

**S1 — `pha cite --edited` refuses to cite.** The affected variant counts twice,
so a document with a perfectly unambiguous edited text cannot be cited:

```
$ pha cite 65 100 --edited
several filled edited variants for doc 65 p.100 — choose one:
  edited-french-ocr
  edited-french-ocr@deepseek-v4-flash
  re-run with --editor <id>
```

**S2 — a stale directory can win.** `_pages_dir_for()` resolves a variant by
globbing and taking the alphabetically first hit
(`ingest.py:1037`):

```python
exact = base / variant
if exact.is_dir():
    return exact
hits = sorted(p for p in base.glob(f"{variant}*") if p.is_dir()) ...
return hits[0] if hits else None
```

`edited-french-ocr` sorts before `edited-french-ocr@…`, so **the bare directory
is preferred** — and the bare directory is the one written while the model was
NULL, i.e. frequently the *older* generation. On docs 47 and 50 the bare
`edited-french-ocr` directory is **610/618 and 627/660 pages of `*waiting*`
placeholders**, while the `@deepseek-v4-flash` directory holds the real text and
matches the database on every sampled page.

**S3 — ambiguity for readers and agents.** Two directories, same rules id, same
page count, differing content, no marker of which is current. Anything that
walks `library/` (an agent, a grep, a citation) has to guess.

**S4 — disk.** 35 bare `edited-*` directories, 52.7 MB here. Small, but it is the
visible symptom of S1/S2 rather than the problem itself.

## 3. Root cause

Three code facts combine:

1. **Name derived from transient state** — `ingest.py:1164-1168` uses
   `doc["editor_model"]`, which is NULL for part of the edit pass.
2. **The NULL is deliberate, then undone** — `ingest.py:1554` writes
   `editor_model=None` when the editor changes (marking "model unknown for this
   re-resolved row"), and `ingest.py:1740` later writes the real
   `editor.model_ref`.
3. **Resolution prefers the bare name** — `_pages_dir_for()` (`ingest.py:1037`)
   returns `sorted(...)[0]`, and `edited-X` < `edited-X@Y`.

Nothing in the pipeline treats the two names as one variant: the viewer, the
citation path and the library walk all see two.

## 4. Proposed fix

**A — don't derive identity from a nullable column (the real fix).**
Give `write_edited_pages()` the resolved model instead of letting it re-read the
row: every call site already has the editor object in hand
(`ingest.py:1552/1556/1735/1742` hold `editor.model_ref`). Then the directory is
always `edited-<rules>@<model>`, whatever the DB column says at that instant.
The `editor_model=None` write at `:1554` can stay for its bookkeeping purpose
without changing where output goes.

**B — make the names one variant (defensive, fixes existing trees).**
Treat `edited-X` as an *alias* of `edited-X@Y` in variant enumeration and
resolution: prefer the model-qualified directory, and never present both. That
alone makes S1 and S2 impossible on an archive that already has the pairs, which
matters because A cannot repair the past.

**C — remediation (opt-in, separate).**
A one-time sweep that removes redundant bare directories, with a guard that
**refuses to delete** a bare directory whose content does not match what the
database holds for that document/editor. `pha prune` is the natural home, or a
new `pha library-variants --dedupe`.

Recommendation: **A + B**, with C as a documented operator step (§6).

## 5. Test plan

- A document whose editor changes during a run produces **exactly one**
  `edited-*` directory, named with the model.
- With both directories present, `pha cite --editor <id> --edited` cites the
  model-qualified file and does not prompt.
- With both directories present, `_pages_dir_for(base, "edited-X")` returns the
  `@Y` directory (today it returns the bare one).
- A bare directory that is stale/placeholder is never the one served.

## 6. Remediation of the existing archive

**Re-measured 2026-09-16 (later the same day, after the archive had been
re-edited), superseding the original snapshot. 34 documents carry a bare +
qualified pair — 25 identical, 9 divergent** (the first measurement found 28/6;
three `jesuit-cat4` pairs had since diverged). The *qualified* folder matched the
database on every page of every divergent pair; the bare one is not a duplicate
in any of the nine:

| group | count | action |
|---|---|---|
| identical except `model:` | **25** | bare dir is redundant — delete (this is what `pha prune --library-variants` does) |
| divergent, real content | **7** | keep — a real page exists only there |
| divergent, placeholder shell | **2** (docs 47, 50) | delete — every real page survives; the rest is `*waiting*` |
| bare-only (no qualified sibling) | **1** | keep — the document's only edited output |
| `@model`-only | **43** | keep as is |

Sizes: 35 bare dirs, 52.7 MB total. The 25 identical pairs differ *only* in the
front-matter line (`model: null` on the bare side):

```diff
- model: null
+ model: deepseek-v4-flash
```

The nine divergent pairs, with what the bare folder actually holds:

| doc | bare → qualified | pages differ | what the bare side is |
|---|---|---|---|
| 47 | `edited-french-ocr` → `…@deepseek-v4-flash` | 610 / 618 | 610 `*waiting*` placeholders + 8 filled — an abandoned partial run |
| 50 | `edited-french-ocr` → `…@deepseek-v4-flash` | 627 / 660 | same, 627 placeholders |
| 56 | `edited-documenta-indica-ocr` → `…@deepseek-v4-flash` | 722 / 725 | a **different complete OCR pass** (dropped page numbers, different Notes, Latin kept where the qualified translated) |
| 19 | `edited-latin-to-english` → `…@deepseek-v4-flash` | 1 / 1011 | on that page the **Latin original**, where the qualified has the English |
| 44 | `edited-modern-portuguese` → `…@minimax-m2-5` | 1 / 192 | a different modernization (`só faço este viaje` vs `somente faço este viagem`) |
| 29 | `edited-jesuit-cat4` → `…@minimax-m2-5` | 1 / 16 | a page in another translation generation (Spanish kept vs Portuguese) |
| 30 | `edited-jesuit-cat4` → `…@minimax-m2-5` | 1 / 10 | same, Spanish where the qualified has Portuguese |
| 27 | `edited-jesuit-cat4` → `…@minimax-m2-5` | 1 / 8 | same, a different Portuguese rendering (+1 `*waiting*`) |
| 21 | `edited-modern-portuguese` → `…@deepseek-v4-flash` | 1 / 622 | one extra blank line (cosmetic, but a byte-compare flags it) |

That is why deleting is a **content** decision, never a filename one: a rule like
"same rules id ⇒ the bare folder is redundant" would have discarded a whole
alternate OCR pass (doc 56) and one page in another translation on five more
documents. The safe automation is the guard in §8 — delete only what survives
elsewhere — which authorises the 25 identical pairs and refuses the 7 with real
unique content.

Docs 47/50 are the interesting middle: their `pages differ` counts are huge only
because of placeholders. Doc 47's bare folder is 618 files of which **8 carry
text** (pages 11, 13, 39, 601, 609, 611, 614, 616) and **610 are `*waiting*`**;
doc 50's is 660 files with **33 real** (3–10, 12, 16, 17, 19, 21, 22, 25, then
614–659) and **627 `*waiting*`**. Every one of those 8/33 pages is byte-identical
to the qualified folder and the DB, and a `*waiting*` file is the *absence* of a
page, not a reading — so both folders hold nothing at all, and §8's rule treats a
stub as empty rather than as content (it was refusing them at first). They are
fossils: written 2026-09-11 12:35 / 14:20, about 12 h before the qualified
folders, from a pass that had text for only those pages; no `page_edits` row now
predates them.

**Removal performed** on `jesuit-archive`, later the same day, in two passes:
the 25 identical folders (43.6 MB), then docs 47 and 50 once placeholders were
counted as empty (0.5 MB of file bytes; ~5 MB of allocated blocks, since each
tiny stub occupies a 4 KB block). Bare folders went **35 → 8**: the 7 different
readings (19, 21, 27, 29, 30, 44, 56) and the 1 bare-only variant (vol08). A
re-run is a no-op.

Only 19 of the 27 deleted folders were git-tracked (the rest sit in a gitignored
`library/` subtree), so git is not the safety net — the proof is that every
deleted page had been shown to be either the DB's exact text or byte-identical
to the surviving sibling. Verified after each pass: doc 20's
`@deepseek-v4-flash` folder holds all 418 pages, doc 54's all 880, the Pfister
`@deepseek-v4-flash` folders all 618 / 660, and `pha status` is unchanged (48
documents, 24 947 pages, 93 569 chunks, no pending corrections).

## 7. Reproduction

```bash
# any document with an editor and a completed scan
ls library/<rel_dir>/<slug>*/ | grep '^edited-'
pha cite <doc> <page> --edited          # prompts with two variants
```

Analysis script used for the numbers above: `analyse-dupes.py` in the archive
root (`jesuit-archive`), which reads `archive.db` read-only and prints the
pairing, the divergence and the DB-match counts.

## 8. What landed (A + B)

Both fixes are in, with the identity rule living in one place (`addresses.py`):

- **A — the writer takes the resolved model.** `write_edited_pages()` now has a
  keyword-only `model` parameter and never reads `documents.editor_model`. Every
  call site passes the model it resolved: `edit_document()` (both the
  page-by-page growth write and the final one), `_edit_null` (`model=None` — the
  null editor has no model), `pha export`, and `bundle` import. The front matter
  `model:` now records the model of the pass, not the transient column, so the
  `model: null` bare folders are not produced again.
- **B — one variant, one name.** `addresses.parse_variant()` /
  `pick_variant()` / `collapse_variant_aliases()` decide the identity, and every
  surface uses them: `variant_files()` (`pha cite`, `pha page --json`, MCP
  `pha_get_page`), `ingest._pages_dir_for()` / `library_page_path()` (search
  hits, `pha page`, filter context), `serve._document_variants()` (the served
  overview and `meta.json`), and `bundle` import. The directory matching the
  document's recorded model wins; otherwise the model-qualified name wins over
  the bare alias. Two *qualified* names of one id are two real models and both
  stay, and a bare name that **is** the current, model-less output (a legacy
  inline interface) keeps its directory.

**Deviations from the draft (§4):**

1. "Prefer the qualified one, never present both" is refined in the two cases
   just named. The draft assumed a bare edited folder could only come from this
   bug, but a legacy inline-interface editor still writes bare, and two models
   of one id are two readings rather than one variant.
2. `serve`'s SQLite snapshot did not select `editor` / `editor_model` (nor the
   palaeographer pair), so it could not tell which directory was current; the
   four columns are now selected in both the normal and the pre-bibliography
   fallback query.
3. `bundle` import always treated the `@model` suffix as part of the editor id
   (`edited-x@y` came back as an editor named `x@y`); it now parses the variant
   with `parse_variant()` and re-exports under the qualified name.
4. The **review round-trip** was a read path the draft did not list, and it still
   walked both folders: `ingest._pending_scan()` (behind `pha status` /
   `pha review`) is mtime-based per file, not variant-resolving, so a stale bare
   file whose mtime beat the stored `exported_at` would have been imported as a
   human correction — over good text, stamped `reviewed`. It now skips a bare
   directory that has a model-qualified sibling (same `collapse_variant_aliases`
   rule everywhere). Measured before the change: of 17,392 bare files with a
   stored `exported_at`, **0** were newer, so the archive was quiet — the fix
   closes the hole rather than repairing damage.

**C is shipped, guarded.** `pha prune --library-variants [--dry-run] [--doc N]`
(`ingest.prune_redundant_edited_dirs`) deletes a bare folder **only when nothing
lives in it alone**, proved either way: every page file *with text* is exactly
what the database holds (regenerable by `pha export`), **or** every such file is
body-identical to the same file in a model-qualified sibling (that folder keeps
the same text byte for byte). A `*waiting*` stub counts as **no page at all** —
it is what pha writes for a page it has no text for, so a folder of placeholders
holds nothing and stubs never block a delete (this is what unblocked docs 47/50;
without it the sweep was conservatively refusing a folder whose only content was
already next door). Anything that differs from both — the 7 real readings of §6 —
is reported and kept, as are bare-only variants and a bare folder that is the
document's current model-less output.

Two refinements over §4C's wording came from running it:

1. A folder can be identical to its sibling yet differ from the DB (doc 54 — the
   DB moved on after both folders were written), and deleting it is still safe
   because the sibling survives, so "matches the DB" alone would have been
   needlessly conservative and would have left one of the 25 duplicates behind.
2. Placeholders are not content (docs 47/50), so they must not make a redundant
   folder look unique.

The sweep is deliberately separate and opt-in, not something `pha scan` or
`pha edit` does on its own, because it is the only pha operation that deletes
from `library/`. `--doc N` narrows it to one document, like `pha review --doc N`.

Test coverage: `tests/test_ingest.py` (one directory per pass, the writer names
by the resolved model, both resolution helpers), `tests/test_addresses.py` (the
collapse and the pick rules), `tests/test_cli_cite.py` (no prompt between a
reading and its own alias), `tests/test_serve.py` (`meta.json` lists the variant
once), `tests/test_cli_pending.py` (the review scan ignores a bare alias) and
`tests/test_prune.py` (the sweep removes a redundant folder, removes one the
sibling still holds, keeps a different reading and a model-less current variant,
ignores `*waiting*` stubs, still refuses a real page that survives nowhere, can
target one document with `doc_id=`, deletes nothing in a dry run, and **reports a
failed delete instead of counting it as removed** — the first real run of the
sweep hit a permission error, `rmtree(ignore_errors=True)` swallowed it, and it
announced 25 removals while nothing had been deleted; the CLI now exits 3). All
four new ingest assertions, the pending-scan assertion and the failed-delete
assertion fail against the pre-fix source.
