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

## 6. Remediation of the existing archive (measured 2026-09-16)

| group | count | action |
|---|---|---|
| identical except `model:` | **28** | bare dir is redundant — delete |
| divergent, `@model` matches the DB | **5** | bare is stale — delete after per-pair check |
| divergent, neither matches the DB | **1** (doc 21) | inspect by hand before touching |
| bare-only | **1** | keep as is |
| `@model`-only | **7** | keep as is |

Sizes: 35 bare dirs, 52.7 MB total. Deleting the redundant ones is safe only
with the identity check above — the six divergent pairs are *not* duplicates:

| doc | bare | qualified | pages differing | DB matches |
|---|---|---|---|---|
| 19 | `edited-latin-to-english` | `…@deepseek-v4-flash` | 1 / 1011 | bare 0, qual 1 |
| 21 | `edited-modern-portuguese` | `…@deepseek-v4-flash` | 1 / 622 | bare 0, qual 0 |
| 44 | `edited-modern-portuguese` | `…@minimax-m2-5` | 1 / 192 | bare 0, qual 1 |
| 47 | `edited-french-ocr` | `…@deepseek-v4-flash` | 610 / 618 | bare 0, qual 8 (bare is 610 × `*waiting*`) |
| 50 | `edited-french-ocr` | `…@deepseek-v4-flash` | 627 / 660 | bare 0, qual 8 (bare is 627 × `*waiting*`) |
| 56 | `edited-documenta-indica-ocr` | `…@deepseek-v4-flash` | 722 / 725 | bare 0, qual 8 |

Note doc 44's qualified directory is `@minimax-m2-5` — a *different model* from
the document's current `editor_model`, i.e. the pair spans two generations of
the same variant.

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

**C is not automated.** `pha prune` and the library were left alone: §6's guard
(refuse to delete a bare folder whose content does not match the DB) needs a
per-pair human decision on the six divergent pairs, and the bare folders no
longer affect anything pha does.

Test coverage: `tests/test_ingest.py` (one directory per pass, the writer names
by the resolved model, both resolution helpers), `tests/test_addresses.py` (the
collapse and the pick rules), `tests/test_cli_cite.py` (no prompt between a
reading and its own alias) and `tests/test_serve.py` (`meta.json` lists the
variant once). All four new ingest assertions fail against the pre-fix source.
