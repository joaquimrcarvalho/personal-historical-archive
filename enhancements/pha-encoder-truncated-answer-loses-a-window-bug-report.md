# Bug report — a truncated encoder answer loses a whole window, and the retries cannot recover

**Status:** **FIXED (F1-F3, 0.40.0).** Found 2026-10-06 while running the **first real encode** in
this archive (pha 0.39.0, repo checkout, `encode --path` on
`collections/litterae-quadrimestres/tomus-primus-1546-1552.pdf`).
**Severity:** silent, paid, loses data — the run reports success and a record
count while one window's records are missing, and nothing in the output says
which.

## 1. Summary

The encoder slices an over-long document into overlapping windows
(`batch_pages` / `overlap_pages`). When the model's **answer** for one window
does not fit `max_tokens`, the reply is cut off mid-JSON; `_parse_json_array()`
requires a **balanced** array and returns `None`; the retry loop then re-sends
the *identical* prompt at temperature 0 (for a chunked window with no detector
`start_page`) and receives an answer truncated the same way; after three
attempts the window is abandoned. The document is still reported as
encoded, with the records from the windows that did parse.

The failure is not "the model returned bad JSON". The model returned good JSON
that was **truncated by the output cap**, and pha never looks at the one signal
that says so — the provider's `finish_reason`.

## 2. Evidence (this archive)

`tomus-primus-1546-1552.pdf`, 796 pages, encoder `letters`
(`max_tokens: 8192`, `batch_pages: 20`, `overlap_pages: 6`,
`context_tokens: 131072`), model `deepseek-v4-flash`. The volume was encoded in
56 windows; the last one carried the volume's closing **INDEX LITTERARUM**:

```
  encoding pages 779-796 (chunked, 51936 chars)
    (retry 1: response 24046 chars not parseable, head: '```json\n[\n  {"index_entry": "I. Ignatius of Loyola ... Bologna ... 1540 ... July 10 ... 8", "index_entry_attributes": {"number": "I", "author": "Ignatius of Loy...
    (retry 2: response 24057 chars not parseable, head: '```json\n[\n  {"index_entry": ...
    (retry 3: response 24164 chars not parseable, head: '```json\n[\n  {"index_entry": ...
    (model returned no parseable JSON array; response 24164 chars, head: '```json\n[\n ...')
encoded 1 document(s)
  + tomus-primus-1546-1552.pdf [letters] (174 records)
```

Three things identify truncation rather than malformed output:

1. **The sizes sit exactly on the cap.** 24 046 / 24 057 / 24 164 characters for
   a JSON payload is ≈ 8 000 tokens, against the encoder's `max_tokens: 8192`.
   A model that had *finished* would have emitted a closing `]`.
2. **The input was small, the output was not.** The window's input is 51 936
   chars — well inside the budget — so this is not the context limit. It is the
   *answer* that does not fit: the index window asks for hundreds of
   `index_entry` rows in one array.
3. **The parser is correct and the retries are futile.** `_parse_json_array()`
   (`ingest.py:2937`) finds the first `[` and scans with depth counting; if the
   array never closes it returns `None` (`ingest.py:2988`). A complete array
   *inside* a ```json fence parses fine, so **the fence is not the cause** — it
   is only what the log's `head:` happens to show. At temperature 0 an identical
   retry reproduces the identical truncated answer, so attempts 2 and 3 cost
   three model calls and change nothing.

Confirmed in the output: the records file for the volume contains only the
`letter` key — **174 letters, and no `index_entry` records at all**. The
volume's own index, which is the cross-check that lets a reader verify no letter
was missed, is absent, and the run's summary does not mention it.

## 3. Impact

1. **Silent data loss in the deliverable.** The records file is well-formed and
   the run exits 0, so the missing window is invisible unless someone knows to
   count `index_entry` rows against the printed index.
2. **It hits the most valuable window.** In this series every volume ends with
   dense two-column indexes; the index is the largest single answer in the
   volume and therefore the one most likely to exceed `max_tokens`. The feature
   built as a completeness check is the first thing lost.
3. **Every collection with an encoder is exposed**, because the shape is
   general: `documenta-indica` (`apparatus`, `documents`), `pfister-notices`
   (`biographies`, `table`) and `letters-from-missons` all ask for structured
   records from long documents.
4. **Three calls are paid for one failure**, and the retry loop reports each
   attempt as if it might succeed.

## 4. Root cause

`chat_text()` (`model_client.py:784`) returns a bare `str`, and **`finish_reason`
is never read anywhere in pha**:

```
$ grep -rn "finish_reason" src/personal_historical_archive/*.py
(no matches)
```

So the encoder cannot distinguish three different situations that all arrive as
`parsed is None`:

| situation | truth | correct response |
|---|---|---|
| answer truncated by `max_tokens` | the input is fine, the *answer* is too big | split the window (or raise the cap) and retry the halves |
| answer genuinely malformed | a model slip | retry as-is, or retry with an instruction |
| answer empty (`""`) | a stall | retry as-is |

and it treats all three the same way: up to three identical retries, then
`log a warning` and continue. The warning does not reach the summary, and no
window is named as lost.

## 5. Suggested fix

- **F1 — read `finish_reason`, and split on `length`.** Plumb the finish reason
  out of `chat_text()` (or return `(text, finish_reason)`); when it is `length`,
  the answer did not fit: **halve the window and retry the halves**, which is
  the honest remedy, since the input fits and only the output does not. This
  also fixes the general case of a window whose output is legitimately large.
- **F2 — make the retries differ, or stop after the first.** At temperature 0 a
  byte-identical retry is deterministic by construction. Either escalate
  (split, raise `max_tokens`, add "return only the JSON array, no code fence")
  or fail after one attempt and say so. Three identical calls per failure is
  cost with no information.
- **F3 — report the loss.** The per-document summary should state the windows
  attempted and the windows lost (`N records from M of K windows`), and the
  windows lost should appear in the run's output, not only as an interleaved
  warning. A run that quietly drops a window should not read like a clean one.
- **F4 (optional) — salvage what arrived.** A truncated array's complete leading
  records could be kept and the remainder recorded as missing, rather than
  discarding the window. That trades exactness for coverage, so it should be
  explicit and counted — not silent.

A regression test is easy and worth having: feed `_parse_json_array()` a
truncated array and assert `None` (current behaviour, so the fix in F1 is where
the new behaviour belongs), and feed it a complete array inside a ```json fence
and assert it **parses** — which it already does, and which is why the fence
must not be blamed in future investigations.

## 6. How it was met here

The 174 letter records for `tomus-primus-1546-1552` are correct and were kept
(they include the letter the trial run had mis-numbered: page 32 is `IV`, `P.
Daniel Paeybroeck` to Ignatius, Louvain, 17 March 1547). The missing
`index_entry` records need a second pass once F1 lands — the same volume
re-encoded with the index window split, or `max_tokens` raised for that encoder.
Until then the volume's records are complete for letters and empty for the
index, and that asymmetry is recorded here rather than left to be discovered.

## 7. Related

- `enhancements/pha-encoder-tools-enhancement-request.md` — the encoder feature
  this qualifies.
- `enhancements/pha-request-stall-timeout-bug-report.md` — the other failure
  mode that arrives as an unusable response and is handled by retrying.
- `enhancements/pha-model-response-resilience-enhancement-request.md` — the
  family this belongs to: what pha does when the model's answer is not what was
  asked for.
- Code: `ingest.py` `_parse_json_array()` (2937), the encoder call and retry loop
  (3376-3398), `_encode_needed()` (2991); `model_client.py` `chat_text()` (784);
  the encoder front matter `max_tokens` / `batch_pages` / `overlap_pages`.


## 8. Fix and verification (2026-10-06, 0.40.0)

- **F1 shipped.** `ModelClient.chat_text_ex()` returns `(text, finish_reason)`;
  `_openai_chat_ex()` / `_anthropic_chat_ex()` normalize OpenAI `length` and
  Anthropic `max_tokens` to `length`; `chat_text()` remains the old `str`
  wrapper. The encoder uses `chat_text_ex()` and, on `length`, splits the
  window into overlapping halves and retries both. Only a single page whose
  answer still does not fit is recorded as a lost window.
- **F2 shipped.** A non-length failure gets ONE differentiated retry (the
  escape-hatch prompt is replaced for detector windows; chunked windows get a
  "return only the JSON array" instruction), never three identical calls.
- **F3 shipped.** `encode_document()` returns `windows` and `lost_windows`;
  `pha encode` prints `N WINDOW(S) LOST`, names each lost window on stderr, and
  ends with an explicit incomplete-records warning.
- Regression coverage: `tests/test_encoder_truncation.py` (split, single-page
  loss, differentiated retry, no identical retries),
  `tests/test_model_client.py` (finish-reason normalization), and the CLI
  summary test in `tests/test_encode_target.py`. Full suite: 956 passed.
- Report corrections: the run had **56** windows, not 57; the repeated replies
  were truncated the same way but not byte-identical (24 046 / 24 057 /
  24 164 chars); the identical-prompt rule applies to chunked windows without
  a detector `start_page`.

---

## Addendum (2026-10-06, same day) — F1 landed in 0.40, but the split it relies on does not halve

**Status of this addendum: FIXED (0.40.1).** F1/F2/F3 above were implemented in pha 0.40.0
(`794bd25 fix(encoder): split truncated answers and report lost windows`). The
detection, the reporting and the *idea* of splitting are right. What the split
does arithmetically is not.

### What the split does

`_split_encoder_window(chunk, overlap)` (`ingest.py:3196`) takes its overlap from
the encoder's **`overlap_pages`** — the *sliding-window* setting — clamped only to
`mid - 1`:

    mid = n // 2
    ov  = min(max(0, overlap), mid - 1, n - mid - 1)
    left, right = chunk[: mid + ov], chunk[mid - ov :]

The Litterae letters encoder sets `overlap_pages: 6`. Six pages of overlap is
sensible for a window *step* — a letter must not be cut at a seam — and ruinous
for a *split*, whose whole purpose is to make each request smaller:

| window | halves | removed per level |
|--------|--------|-------------------|
| 20 pages | 16 + 16 | 4 |
| 18 pages | 15 + 15 | 3 |
| 13 pages | 11 + 12 | 2 |
| 10 pages | 9 + 9 | 1 |
| 8 pages | 7 + 7 | 1 |
| 3 pages | 1 + 2 | 2 |

The recursion removes one to four pages per level where the docstring says
"halve". It is linear where it should be logarithmic, and because each level
re-sends almost the whole window, the same text is paid for over and over.

### Measured

* **At `max_tokens: 8192`** (tomus-primus's INDEX LITTERARUM window, 18 pages):
  **129 split calls, 66 capped windows, ~40 minutes**, still descending when it
  was stopped by hand. This is the thrashing that prompted this addendum.
* **At `max_tokens: 32768`** (six volumes, 385 windows): **6 splits and 3 capped
  windows in total.** One level of splitting sufficed, because a half that only
  has to fit 32768 tokens usually does.

That contrast is why this is not urgent: raising the cap removed the pressure that
exposed the arithmetic. The arithmetic is unchanged.

### Fix

A split is not a window step. Give it little or no overlap — `ov = min(overlap, 1)`,
or `min(overlap, n // 4)` — so that each half is genuinely about half. One line.

### A caveat specific to ditto-based indexes

Splitting is the wrong remedy for an `INDEX LITTERARUM` in any case. Its rows
carry printed dittos (`[same]`), which the encoder resolves into `author_implied`
by reading the rows *above*; a window beginning mid-index has lost exactly the
context that field needs, and it fails silently, field by field. For this content
the honest remedies are a larger output budget (done for the letters encoder) or a
split at a semantic boundary — by year, or by letter-block — never a blind half.

### Evidence

* `_split_encoder_window` — `ingest.py:3196`; call site `ingest.py:3281`.
* Run logs in the archive: `.lq-qa/encode-tomus-primus-2.log` (at 8192) and
  `.lq-qa/encode-litterae-rest.log` (at 32768).


### Fix landed (0.40.1)

`_split_encoder_window()` now caps the split overlap at `n // 8` (plus the
existing bounds), so a split is genuinely close to half:

| window | old halves | new halves |
|---|---|---|
| 20 pages | 16 + 16 | 12 + 12 |
| 18 pages | 15 + 15 | 11 + 11 |
| 13 pages | 11 + 12 | 7 + 8 |
| 10 pages | 9 + 9 | 6 + 6 |
| 8 pages | 7 + 7 | 5 + 5 |

Regression test:
`tests/test_encoder_truncation.py::test_split_window_is_close_to_half_with_large_step_overlap`.
The ditto caveat above remains content-specific: no generic split recovers the
index rows above a mid-index seam; for `INDEX LITTERARUM` keep the larger
`max_tokens` budget or split at a semantic boundary.
