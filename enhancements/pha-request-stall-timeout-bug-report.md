# Bug report — a stalled model request is never bounded by `timeout_s`

**Status:** **FIXED** — see §9 for what landed (F1–F4 plus the batch rule from
`pha-handover-editing-workflow-enhancement-request.md` G4).
**Found:** 2026-09-19/20 while scanning two cloud-vision volumes
for `jesuit-archive` (pha 0.28.0, repo checkout).
**Severity:** silent wall-clock loss measured in hours, twice in two days; a
stalled scan also *looks* healthy — no error, no log line, `pha status` says
`processing` — so nothing tells the operator to intervene.

## 1. Summary

`models/<id>.md` exposes `timeout_s`, and `ModelClient` passes it straight to
httpx:

```python
# model_client.py:541
self._http = httpx.Client(base_url=self.base_url, timeout=timeout_s, headers=headers)
```

A bare float in httpx is **per operation** (connect / read / write / pool), not
a budget for the request. A read timeout only fires when *no byte at all*
arrives for that long. Providers routinely keep an idle connection alive with
periodic bytes (SSE comments, whitespace, chunked keep-alives), and a reasoning
model can spend many minutes producing no visible output — so a request whose
completion never arrives can sit **indefinitely**, with the connection healthy
by every measure httpx applies.

Measured twice on real scans (both with `timeout_s: 600` set on the model):

| incident | document | where it stalled | wall-clock lost |
|---|---|---|---|
| 2026-09-19 | `ricci-entrata-nella-china-1622-it.pdf` | last page written 08:54, killed by hand ~15:2x | **≈6 h 30** |
| 2026-09-20 | `ricci-trigault-de-christiana-expeditione-1615.pdf` | last page written 08:56:28, found at 13:13 | **≈4 h 17** |

In both cases: **no page error recorded**, no failed HTTP status, and the
provider's billing flat (OpenRouter spend went $3,67 → $3,70 across the second
stall's 4.4 h — the connection was alive but nothing was being generated).

## 2. Evidence (second incident, sampled)

`sample <pid>` of the stalled `pha scan` (2 s, every frame in one branch):

```
_ssl__SSLSocket_read  (libpython3.12.dylib)
└─ PySSL_select
   └─ poll  (libsystem_kernel.dylib)
```

i.e. the process is blocked reading a TLS socket, not computing, not writing the
DB, not waiting on a lock. Together with `pages` count frozen at 466/686 and
zero rows carrying an `error`, the request was simply never answered.

The archive's own scan log shows the last line as the in-flight page:

```
page 467/686: extracting ...
```

and the document row's `updated_at` pins the moment it stopped.

Also relevant: the same scan's `timeout_s: 600` was added to the model sheet
*before* both incidents, so the knob was not absent — it was ineffective.

## 3. Impact

1. **Hours of wall clock**, silently. On a queued night run (this archive runs
   scans back to back) everything behind the stalled volume waits too.
2. **The failure is invisible.** No exception reaches the DB, the log stops
   mid-page, and `pha status` reports `processing`. Without an external
   watchdog the only symptom is "it got slower".
3. **It defeats the retry logic.** `_post()` (`model_client.py:561`) retries
   `self.retries + 1` times with `time.sleep(2 * (attempt + 1))`, so the *whole
   call* is bounded by `(retries + 1) × timeout_s + backoff` — but only if the
   per-operation timeout ever fires. With keepalives it never does, so neither
   the retries nor the eventual `ModelError` happen.
4. **It punishes exactly the intended use.** The long-silence profile is typical
   of the *reasoning* models the sheets configure (`thinking: enabled`), which
   is why the deadline has to be generous rather than tight.

## 4. Root cause

Two independent facts combine:

- **httpx semantics.** `timeout=float` expands to per-operation timeouts; httpx
  has no "whole request" timeout (neither does `requests`). Nothing in pha
  imposes a deadline of its own on the call.
- **Provider behaviour.** A gateway may keep the connection warm (keep-alive
  bytes) while the completion is not ready; each arriving byte resets the read
  timeout, so a request can outlive any per-read bound by an arbitrary margin.

The model-sheet reference in `config.py` documents `timeout_s` without saying
which of the two it is, which is how a reasonable operator sets 600 s and
expects a 10-minute ceiling.

## 5. Suggested fix

**F1 (preferred) — a wall-clock deadline per request, enforced by pha.** Add
`deadline_s` (or `total_timeout_s`) to the model sheet, distinct from
`timeout_s`, and enforce it in `ModelClient` around the call, e.g.:

- run the request in the event loop (`httpx.AsyncClient`) under
  `asyncio.wait_for(..., deadline_s)`, or
- run it in a worker thread and abandon/cancel it on expiry
  (`client.close()` from the timer, or a `threading.Event` the request loop
  checks), or
- consume the response as a stream and abort when the deadline passes (this also
  makes progress observable while a page is being read).

On expiry: raise `ModelError` (the existing path), so the page is recorded with
its `error`, the consecutive-failure counter advances, and the scan either moves
on or aborts the document — all behaviours the pipeline already has.

**F2 — bound the whole page, not just one attempt.** The budget should cover the
retries too (`(retries + 1)` attempts), so the worst case per page is a known
number of minutes. Log the elapsed time per page (`page N/M: extracted in Xs`),
which also makes a slow provider visible *before* it becomes a stall.

**F3 — make a stall visible without a watchdog.** Cheap and high value:
- when a request exceeds, say, 3× the median page time for the document, print a
  warning line (flushed, so it lands in the log immediately);
- have `pha status` (and the `processing` document line) show **how long since
  the last page was written**, so "processing" is never indistinguishable from
  "hung for hours".

**F4 — document the knob.** In the model-sheet reference say explicitly that
`timeout_s` is per-operation (connect/read/write/pool) and **not** a total
request budget, and point at `deadline_s`. Consider a sane default per style
(e.g. 900 s read, 1800 s deadline for `thinking: enabled`).

## 6. Tests to add

1. **Blackhole server** — accepts the connection and sends nothing: assert the
   call raises `ModelError` within ~`deadline_s`, not at the OS level.
2. **Keep-alive trickle** — sends one newline every 30 s and never completes:
   assert it still raises within `deadline_s` (this is the case that defeats the
   current code).
3. **Slow but honest** — a stream that finishes just inside the deadline:
   assert success (the deadline must not break legitimate long pages).
4. **Retry budget** — one blackhole attempt followed by a good one: assert the
   retry still happens and the total stays inside `(retries + 1) × deadline`.

## 7. Workaround used here (outside the tool)

A shell watchdog around the scan, keyed on *progress* rather than on the HTTP
layer: it samples `pages + page_edits(status='done') + chunks` for the document
every minute and, after 20 minutes without a change, kills the scan and restarts
it (pha resumes per page, so nothing is re-read or re-paid). It recovered the
second incident in one restart and is now guarding the remaining volume.

That is the behaviour F1/F3 belong to pha: an external script should not be the
only thing that notices a stalled request.

## 8. Related

- `enhancements/pha-embed-loss-bug-report.md` — the other "the pipeline fails
  quietly" defect; the embed path has the same shape (a timeout that ends in a
  silent degrade) and the same appetite for a real deadline.
- `enhancements/pha-model-response-resilience-enhancement-request.md` — the
  error taxonomy this report's `ModelError` should feed.
- Code: `model_client.py` `ModelClient.__init__` (541), `_post` (561),
  `_post_to` (578), `chat_text` (701); `ingest.py` scan loop (per-page error
  handling and the consecutive-failure abort); `config.py` model-sheet
  reference (the `timeout_s` documentation).

## 9. What landed (FIXED)

**F1 — a wall-clock deadline per request, enforced by pha.**
`ModelClient` now takes `deadline_s` (distinct from the per-operation
`timeout_s`) and consumes the response as a **stream in the calling thread**,
abandoning the attempt once the deadline passes and raising the retryable
`_AttemptDeadline`; when the retries are exhausted the call raises the new
**`ModelStall(ModelError)`**, so every existing `except ModelError` guard still
catches it while a per-page pass can tell a stall apart from a bad answer. A
streaming read (not an abandoned worker thread) is what makes the bound leak-free:
a blocked socket read cannot be interrupted from another thread, so an abandoned
worker would survive an endlessly-trickling provider. `deadline_s` is a **stage**
field (palaeographer/editor/encoder file, next to `timeout_s` — it is not a model
field), parsed from front matter and from the legacy `config.yaml` blocks.
Default `2 × timeout_s`, floor 60s; **`deadline_s: 0` disables the ceiling**
(the old behaviour). Because the default is derived, every existing model sheet
is bounded with no config change; because the deadline is checked between read
chunks, a *legitimate* slow page inside the budget still succeeds.

The division of labour is deliberate: **`timeout_s` keeps bounding a SILENT
connection** (no byte at all — the read timeout fires first and stays a normal,
non-stall error), while **`deadline_s` bounds the TRICKLE** (bytes keep arriving,
completion never does) — the case that previously had no bound at all.

**F2 — the budget is known and progress is visible.**
The deadline is per ATTEMPT, so one page costs at most
`(retries + 1) × (deadline_s + min(timeout_s, deadline_s))` plus the existing
backoff — a known number of minutes, whereas before it was unbounded. (The
`+ min(timeout_s, deadline_s)` is the one read that may already be in flight when
the deadline passes; every low-level operation is also capped by the deadline, so
neither a slow pool wait nor a connect can outlast it.) The scan and edit loops
now print `page N/M: done in Xs` (a slow page is marked, `PHA_SLOW_PAGE_S`
overrides the 600s threshold) and `page N/M: FAILED after Xs` on the error path.

**F3 — a stall is visible without an external watchdog.**
Per-page elapsed time in the log (above) *and* `pha status` now flags a
`processing` document whose row has not moved:
`updated 2026-09-20 08:56 · no progress for 4h17m — stalled?`
(threshold 30 min, `PHA_STALL_WARN_S` overrides). "processing" is no longer
indistinguishable from "hung for hours".

**F4 — the knob is documented.**
The per-stage front-matter reference (`palaeographers/_sample.md` and the
`config.py` builtin constants), the README (both the per-stage field list and
the palaeographer example), the model-file comments that say `timeout_s` is not
a model field, and AGENTS.md's operating discipline all state that `timeout_s`
is per-operation and `deadline_s` is the wall-clock ceiling.

**Batch behaviour (the G4 addition, implemented here).** Inside a multi-page
pass a deadline expiry abandons the page **for that pass**: no page error is
recorded, the page row stays pending, it does not count toward the
consecutive-failure abort, and what was read is still edited and indexed — the
document stays `processing` so the next `pha scan`/`pha edit` resumes exactly
the abandoned pages. The pass names them
(`⏱ 2 page(s) abandoned for this pass (model stalled): 12, 34 — re-run …`) and
the result carries `action: "stalled"` + `stalled: [N, …]` for agents/JSON.

**Tests** — `tests/test_model_stall.py` runs a real socket server:
blackhole, **keep-alive trickle** (the case that defeats `timeout_s`),
slow-but-honest inside the deadline, retry after a stalled attempt, `0`
disables, and the derived default. `tests/test_stall_batch.py` covers the batch
rule in scan and edit (page abandoned, named, no error, retried next pass; six
stalls in a row never trip the abort; a genuine `ModelError` still fails the
page). `tests/test_cli_status.py` covers the stall flag.
