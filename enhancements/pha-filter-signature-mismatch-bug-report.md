# Bug report — a filter with manifest defaults makes its stage re-run forever

**Status:** open. **Found:** 2026-09-18, while executing Option A of
`pha-post-filter-replay-enhancement-request.md` by hand on
`collections/franco-imagens` (pha 0.28.0, repo checkout).
**Severity:** silent, recurring, paid — every later scan/edit re-runs the stage's
model for pages that are already up to date.

## 1. Summary

The filter signature pha **stores** when it runs a chain is not the string pha
**computes** for the same chain as configured. `filters_changed()` compares them
for equality, so it reports "changed" on every pass and the stage's model is
called again — for every page, forever.

The two disagree whenever a filter's manifest declares `params:` defaults that
the sidecar does not repeat (the normal case: sidecars list only the overrides).

## 2. Evidence (this archive)

`dropbox/collections/franco-imagens/pha.yaml`:

```yaml
editor:
  rules: franco-imagem-virtude
  model: deepseek-v4-flash
  post:
    - join-hyphenated-words        # bare id -> FilterSpec(name=..., params={})
```

`filters/join-hyphenated-words/filter.md` declares:

```yaml
params:
  keep_hyphen_before_enclitic: true
  notsign_as_hyphen: true
```

Measured on 2026-09-18, same chain, same filter version:

| | signature |
|---|---|
| configured (`_configured_filters_signature`) | `join-hyphenated-words:7ffca804081dede0:{}` |
| applied (`apply_filters` → `filters_signature(ran)`) | `join-hyphenated-words:7ffca804081dede0:{"keep_hyphen_before_enclitic": true, "notsign_as_hyphen": true}` |

They differ only in the params field, and only because the second one includes
the manifest defaults. `filters_changed(recorded, expected)` (`filters.py:520`)
is therefore **always True**.

Corroboration from the data: one page of doc 54 was re-edited by hand on
2026-09-15 as a test and carries the **applied** form — so that page alone is
permanently stale. The other 3 574 pages of the four volumes carried `''` (no
chain recorded), i.e. they became stale the moment the filter was added — which
is correct — but they would have become stale *again* on every pass after a
replay that stored the applied form.

## 3. Impact

1. **Recurring cost.** Any collection using a filter with manifest defaults
   re-runs that stage's model on each `pha scan` / `pha edit`, with no change
   having occurred. On `franco-imagens` that is 3 612 pages of
   deepseek-v4-flash per pass (the same work measured at ~7 h when it was
   actually needed).
2. **It defeats the fix it should enable.** `--replay-filters`
   (`pha-post-filter-replay-enhancement-request.md`) exists to make a
   deterministic filter change cost 0 model calls. A replay that stores the
   signature it actually applied leaves the page stale under this defect, so the
   next pass calls the model anyway — the saving is not durable. This report is a
   prerequisite for that request, not a duplicate of it.
3. **Invisible.** Both records look plausible in the DB and in the sidecar; only
   comparing the two strings (or watching the model get called again) reveals it.

## 4. Root cause

The two call sites disagree about what "the chain" is:

- `ingest.py:1138` `_configured_filters_signature()` —
  `ran.append({"name": …, "sha": …, "params": dict(spec.params)})`, i.e. the
  params **declared in the sidecar only**.
- `filters.py:438` `apply_filters()` —
  `ran.append({"name": …, "params": chain_ctx["params"], "sha": …})`, where
  `chain_ctx["params"] = resolve_params(f, spec)` (`filters.py:355`) = **manifest
  defaults + sidecar overrides**.

`filters_signature()` (`filters.py:497`) serialises whatever it is handed, so the
same chain yields two different strings. Note that `filter_sha()` already hashes
the filter's **manifest** along with the script and declared inputs, so the
defaults are covered by the sha too — including them in the params field adds
nothing to change detection. Either side can therefore be normalised without
losing staleness fidelity.

## 5. Suggested fix

Pick one, apply it on **both** sides, and add a regression test that the two
functions agree for a filter with manifest defaults and for one with a sidecar
override:

- **F1 (preferred, smallest):** make `_configured_filters_signature()` resolve
  params exactly as `apply_filters()` does
  (`resolve_params(load_filter(...), spec)`). Stored signatures in the "applied"
  form (resolved) then become correct — including the doc 54 page above.
- **F2:** record only the **overrides** (`spec.params`) in both places. Simpler
  to read, but stored rows written in the resolved form (F2's opposite) go stale
  once, and a filter whose defaults change would then be detected only via the
  sha (still detected, since the manifest is hashed).

Whichever is chosen, a one-shot normaliser for existing rows is cheap: rewrite
only the params field of stored signatures, in place, with no model call — this
is exactly what the manual pass below did (it stored the **configured** form, so
those pages are considered up to date today).

## 6. How it was worked around here (2026-09-18)

`collections/franco-imagens` needed `join-hyphenated-words` applied to edited
text produced with no filters. Executed by hand, per Option A of the replay
request, storing the **configured** signature so the pages are not stale:

- 3 575 pages examined; **2 843** edited texts actually changed; 732 recorded
  with no text change; 1 signature normalised (the doc 54 test page); 20 pages
  left `status != done` and **1 human-reviewed page untouched**.
- 0 model calls, 1 min 18 s wall clock (vs ~7 h of model time for a re-edit).
- End-of-line hyphens in the 4 volumes: **59 872 → 2 058** (of which ~517 are
  real word-splits; the rest are page-signature/catchword lines and uppercase
  compounds the filter deliberately leaves).
- Safety invariant checked per page before writing: the filter may only remove
  hyphens and move line breaks, so `re.sub(r"[\s\-]+", "", before)` must equal
  the same on `after`. **0 violations** across all 2 827 pages that changed.
- `write_edited_pages()` re-exported each volume's `library/` pages (which also
  refreshes `exported_at`, keeping the review round-trip honest); `pha reindex`
  was queued for the four documents so search matches the new text.

Two operational notes for whoever implements the command: (a) short write
transactions are mandatory — a replay run concurrently with a scan must commit
frequently, or it can push the *scan* past its own ~45 s contention tolerance
(`db.py` `busy_timeout=20000` + `_write()` retries) and abort a volume's work;
(b) the replay must take the scan/edit lock, as the request already says.

## 7. Related

- `enhancements/pha-post-filter-replay-enhancement-request.md` — the command this
  defect blocks (Option A was executed manually; Option B persists the model
  output).
- `enhancements/pha-filters-enhancement-request.md` — the implemented feature
  whose staleness rule this qualifies.
- `FILTERS_PLAN.md` §4 ("Staleness and re-run") — the design statement that a
  changed filter re-runs its stage; correct in intent, unimplementable in
  practice while the two signatures disagree.
- Code: `filters.py` `resolve_params()`, `apply_filters()`,
  `filters_signature()`, `filters_changed()`, `filter_sha()`;
  `ingest.py` `_configured_filters_signature()`, `_edit_needed()`,
  `edit_document()`.
