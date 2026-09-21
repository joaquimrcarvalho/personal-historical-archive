# Enhancement request — hand a document to a second machine and take the results back (`pha handoff`)

**Status: IMPLEMENTED** (0.29.0), landed on branch `feature/handoff`.
**Date:** 2026-09-20. **Written against:** pha 0.28.0, repo state at `6681bd5`.

> **As implemented — the four deviations worth knowing.**
> (1) The merge needed **no per-page provenance columns**: keying on `sha256` +
> `reviewed_at` + `raw_sha` was enough, so this never depended on single-page
> re-scan and the `db.py` machine-provenance writers in §10 were not needed.
> (2) **`scan`/`edit`/`encode --path <doc>` stay the primary verbs**; `pha
> handoff work` is a thin wrapper that runs exactly those three and reports each
> stage's exit code (decision 4 below).
> (3) `fetch --dry-run` returns the **same `counts`/`conflicts`/`refused`/`stale`
> as the real apply** — the §3.5 decision table is a pure function, so the dry
> run is a genuine prediction rather than a document list.
> (4) A conflict is **named** (`<relpath> p.<n>`), not just counted; a bare count
> is not actionable.
> The independent `unbundle` waiting-stub bug this doc carried is fixed
> separately (`5a105b4`). An extra escape hatch was added that the doc does not
> propose: `--include-leased` on `scan`/`edit`/`encode`/`reindex`.

**One line.** Let one archive *lend* a document (or a collection) to a second,
always-on machine on the same LAN, have that machine run the pipeline while the
archive's own machine sleeps, and **apply the results back into the same
document** — same `documents.id`, same slug, same citation, same human
corrections — with a lease so the two machines can never work the same
document (or the same model server) at once.

Motivating setup: the archive lives on a MacBook that must sleep for hours at a
time, while a Mac Studio on the same network sits idle. LM Studio already routes
requests to the preferred (desktop) machine over **LM Link**, so *while both are
awake* nothing needs to change — this request is only about the hours when the
MacBook is closed and the Studio could be finishing the backlog. Concrete
backlog at the time of writing: **12 unprocessed Documenta Indica volumes**.

---

## 1. Problem

### 1.1 What already exists, and why it is not a handover

Almost every ingredient is already in the archive:

- **Identity by content.** `documents.sha256` / `sha256_of()` /
  `sha256_of_dir()` (`ingest.py:254-273`) key the document, and are also the
  render-cache key (`renders/<sha256>/`). They travel in the bundle manifest
  (`bundle.py:358`).
- **Per-page provenance.** `pages.raw_text` + `pages.filters`, `page_edits.text`
  + `raw_sha` + `filters`, each with `reviewed_at` (`db.py:396,412,427,503`).
  An edit is already bound to the raw text it was made from via `_raw_sha()`
  (`ingest.py:119`).
- **A portable unit.** `pha bundle` (`bundle.py:165`) already packs a collection's
  documents, resolved selection files, collection-local encoders, the
  palaeographer/editor/encoder/prompt/filter **definitions actually used**,
  finished library files and renders; `pha unbundle` (`bundle.py:785`) installs
  them with no model calls.
- **Self-describing page files.** Every library page carries front matter
  (`document_id`, `page`, `palaeographer`, `model`, `editor`, `filters`,
  `status`, `reviewed`) plus the text (`ingest.py:1260-1320`) — a returned
  result needs no new transport format, only a new *import* semantics.
- **Resumability.** A document with pending pages has status `processing`, and
  `ingest_file()` resumes it, keeping the pages that are already `done`
  (`ingest.py:786-825`).

What does **not** exist is a handover, for three reasons.

### 1.2 `unbundle` imports a copy, it does not apply an update

`import_bundle` always calls `db.add_document(...)` (`bundle.py:630`),
so a returned document becomes a **second row with a new id**, a second slug
and a second library folder. Doc ids, slugs, `pha cite` addresses and the
footnotes in `notes/*.md` all key off the local document; a handover must
therefore *join* on content identity and update in place, not append.

`unbundle` also **pins and skips**: `_pin_selections()` (`bundle.py:569`)
writes selection files so the receiving archive resolves the same
palaeographer/editor, and the imported document is deliberately left alone by
`pha scan`. That is right for "give a finished collection to a colleague" and
exactly wrong for "finish this document here": the worker must **resume** the
pending pages, not skip them.

### 1.3 The `*waiting*` stub becomes content (verified)

`write_document_pages()` writes a placeholder page for every not-yet-extracted
page: front matter `status: waiting` and body `*waiting*`
(`ingest.py:500,1260-1303`). Those files are copied into a bundle like any
other, but the import loop feeds the body straight into the DB with no stub
check (`bundle.py:651-671`):

```python
parsed = _parse_library_file(f)
fm, body = parsed
page_id = db.add_page(conn, doc_id, int(pno), source_name=source_name)
db.set_page_result(conn, page_id, raw_text=body)   # body == "*waiting*"
```

`set_page_result` marks the page `done` (`db.py:396-409`), `pages_ok` counts it,
and the status rule at `bundle.py:744-746` then reports the document `done`.

Reproduced on 2026-09-20 with a hand-built bundle (one real page, one waiting
stub), importing into an empty archive:

```
import result: ... {'id': 1, 'status': 'done', 'pages': 2, 'chunks': 2}
document    : {'id': 1, 'status': 'done', 'page_count': None}
page 1: status='done' raw_text='PAGE ONE real transcription'
page 2: status='done' raw_text='*waiting*'          <-- placeholder became content
```

So a handover built on today's commands does not merely lose the pending work:
it **silently converts it into "done" text**, and the worker's `pha scan` then
skips the document as `unchanged` (`ingest.py:819`). Every handover of a
partially-processed document is affected, which is the normal case — a
document is usually handed over *because* it still has pending pages.

The code base already knows a `*waiting*` stub is "the absence of a page, not
content" (`ingest.py:545,609`); only the bundle importer does not.

### 1.4 Nothing prevents the two machines colliding

Model-server locks are **user-global per machine** by design
(`locks.py:1-39,64-82`): two archives on one box serialise, but two *boxes*
cannot see each other's locks. With LM Link both machines address the same
LM Studio, so the documented failure modes apply to a handover that starts
while the archive machine is still busy: model swap → page-out → disk fill, and
a `pha reindex` overlapping an embed pass losing a document's vectors
(`enhancements/pha-embed-loss-bug-report.md`). The laptop being asleep hides
this most of the time — but "the laptop woke early" and "the run was started
twice" are exactly the states a lease exists to make safe.

---

## 2. Requirements

- **R1 — content identity, not ids.** The round trip joins on the source's
  `sha256` (with `relpath` as a human-readable cross-check). A returned result
  updates the local document in place: same `documents.id`, same slug, same
  library folder version, same citations. No duplicate document is created for
  a document this archive already has.
- **R2 — resume, never skip.** Importing a hand-out into the worker archive
  marks the pages it received as `done` and every remaining expected page as
  **`waiting`**, and leaves the document `processing`, so the worker's
  `pha scan` finishes the document instead of skipping it.
- **R3 — `*waiting*` is never content.** No stub body is ever imported as text,
  by handoff or by `unbundle` (the bug in §1.3 is fixed for both).
- **R4 — the return is an apply, not an import.** Applying results writes the
  worker's text and provenance into the local pages/edits, regenerates the
  library files from the DB, re-indexes only the affected documents, and
  **never invents new documents** for docs the archive already has.
- **R5 — human work wins.** A locally `reviewed` page/edit is never overwritten
  by a worker result. A worker-reviewed page is carried back as reviewed. Two
  conflicting human readings are reported, never silently resolved.
- **R6 — machine work is not "reviewed".** Imported pipeline text gets pipeline
  provenance, not `reviewed_at`. `reviewed` continues to mean *a human checked
  this*.
- **R7 — a lease the archive machine honours.** While a document is handed out,
  `pha scan` / `pha edit` / `pha encode` / `pha reindex` skip it with a clear
  message (overridable), and `pha status` reports it as *out on handover*.
  Applying the return clears the lease.
- **R8 — the hand-out carries the settings.** The worker resolves the same
  palaeographer / editor / encoder / prompt / filters as the archive machine,
  with no manual config copying: reuse `defs/` and the selection pinning the
  bundle already does.
- **R9 — staleness is honest.** If the archive's configuration changed while a
  document was away, the apply must say so (the imported pages are stale under
  the current config) instead of silently keeping work that the next scan will
  redo, or silently redoing it.
- **R10 — partial scope.** Handing out 3 of 12 documents leases exactly those 3;
  the other 9 remain fully usable on the archive machine.
- **R11 — transport is the user's.** The payload is a directory; moving it
  (`rsync`, a share, a USB stick) is out of scope. Both machines are awake at
  hand-out and at fetch by definition.
- **R12 — no new trust model.** Same-archive-owner code and data; no
  authentication layer, no server-to-server protocol, no daemon.

---

## 3. Design

### 3.1 Identity

| level | key | why |
|---|---|---|
| document | `sha256` of the source bytes (+ `relpath`) | stable across machines; already stored; already the render key |
| page | (`sha256`, `source_name` or `page_no`) | `source_name` exists for directory-of-images scans (`pages.source_name`, `db.py:160`) |
| variant | `transcription-<pal>` / `edited-<editor>[@<model>]` | already the library layout, parsed by `addresses.parse_variant` |

Nothing about the *worker's* doc ids travels back. The worker archive is a
scratch workspace; its ids are meaningless on the archive machine and are
discarded on apply.

### 3.2 Command surface

One new command group, mirroring `pha bundle` / `pha unbundle`:

```bash
# --- on the archive machine (the MacBook), before it sleeps
pha handoff out collections/documenta-indica/vol04 documents/doc.pdf \
    -o ~/handoff-DI-vol04            # leases what it exports, writes handoff.json
rsync -a ~/handoff-DI-vol04/  studio:~/pha-handoffs/DI-vol04/

# --- on the worker machine (the Mac Studio), any time
pha handoff in  ~/pha-handoffs/DI-vol04        # import + resume pending pages
pha handoff work ~/pha-handoffs/DI-vol04       # scan → edit → encode for its docs
pha handoff back ~/pha-handoffs/DI-vol04 -o ~/handoff-DI-vol04-back

# --- back on the archive machine, after it wakes
rsync -a studio:~/handoff-DI-vol04-back/ ~/handoff-DI-vol04-back/
pha handoff fetch ~/handoff-DI-vol04-back      # apply in place, reindex, clear lease

pha handoff status                             # what is out, and since when
pha handoff cancel <handoff-id>                # release a lease without applying
```

`work` is a convenience wrapper (the three real commands still work
individually, and the worker can be driven by an agent through the existing
MCP tools / CLI). `--dry-run` on `in`, `work` and `fetch` prints the plan and
touches nothing; `fetch --on-conflict keep|take|ask` and
`--include-leased` on the four pipeline commands are the escape hatches.

### 3.3 What travels

| leg | payload |
|---|---|
| out | source documents (bytes) · collection `pha.yaml` + selection files + local `encoders/` · `defs/` (palaeographer/editor/encoder/prompt **and** `filters/` actually used) · the *real* done pages for resume (stubs excluded) · `handoff.json` · **no renders** (the worker re-renders from the source; the outbound leg is large enough already) |
| back | `handoff-result.json` · per-page texts + provenance · edited pages + `raw_sha` · encoder records · reviewed stamps · **no source bytes, no renders** (the archive machine already has them) |

`handoff.json` is the bundle manifest (`bundle.py:358,382-392`) plus:

```json
{
  "format": "pha-handoff", "version": 1,
  "handoff_id": "DI-vol04-20260920T2130Z",
  "origin_archive": "<archive uuid>", "origin_host": "macbook",
  "created_at": 1758400000.0,
  "documents": [
    {"relpath": "collections/documenta-indica/vol04", "sha256": "…",
     "page_count": 638, "palaeographer": "…", "editor": "…",
     "config_signature": "…", "pages_done": ["0001","0002", "…"]}
  ]
}
```

`config_signature` is the resolved (palaeographer, model, editor, model, encoder
set, prompt source, filter chain) per document — enough for R9.

### 3.4 Worker-side import: resume, not skip

`pha handoff in` reuses `_install_defs()` (`bundle.py:486`) and
`_copy_dropbox_payload()` (`bundle.py:541`), and replaces `import_bundle`'s
page loop with:

1. create/reuse the document row (same sha ⇒ reuse, so repeated handovers of
   the same document do not multiply it),
2. import each carried page whose body is **not** a stub (`body.strip() ==
   _WAITING_STUB`, or front-matter `status: waiting`) as `done`,
3. create a `waiting` row for every expected page missing from the payload
   (`page_count` from the manifest),
4. stamp document status `done` **only** when all expected pages are `done`,
   else `processing`,
5. `_pin_selections()` as today (R8) — the pin is what makes the worker resolve
   the same pair and what stops a later bulk pass from re-reading good pages,
6. no renders in, so the worker renders from source as a normal scan does.

Then `pha scan --path …` resumes exactly the pending pages. This is the
existing resume path (`ingest.py:786-825`), which is why the change is small.

### 3.5 Return and apply: the merge

`pha handoff back` builds the result from the worker's **DB rows, not the
library files**, so provenance columns travel (`palaeographer`, model,
`filters`, `status`; edits with `raw_sha`). Library files remain the readable
artifact and are regenerated on apply.

`pha handoff fetch` applies per page, using a partial order rather than
"newest wins":

| local page | worker page | result |
|---|---|---|
| `reviewed` | not reviewed | **keep local** (R5) |
| not reviewed | `reviewed` | take worker, carry `reviewed` |
| `reviewed` | `reviewed`, same text | take either; stamps preserved |
| `reviewed` | `reviewed`, different text | **conflict** — report, default keep local (`--on-conflict take`) |
| neither | produced by the worker | take worker text + provenance, `status='done'`, **no** `reviewed_at` (R6) |
| neither | not in the payload | leave as `waiting` |

For an **edit**, the payload's `raw_sha` must equal `_raw_sha(local raw after the
raw apply)`; otherwise the edit was made from a different reading and is
dropped with a report (this is the existing guard, `ingest.py:119`,
`db.py:503`). Encoder records are imported when the worker's run is newer and
the page set it was built from is unchanged. Then, for each affected document:

1. `write_document_pages()` / `write_edited_pages()` regenerate the library
   files from the DB (front matter carries *this* archive's ids and paths),
2. `index_document()` for that document only (never a global `pha reindex`),
3. clear the lease for that document.

Documents whose local source no longer matches the hand-out `sha256` (the file
was replaced while away) are refused with a message; the archive is expected to
re-run them locally or hand them out again.

### 3.6 Lease, and the model server

A hand-out writes `<archive>/.pha/handoffs/<handoff-id>.json` — `.pha/` is the
existing machine-local, gitignored area that also holds `location.json`:

```json
{"handoff_id": "…", "state": "out", "worker": "mac-studio",
 "created_at": 1758400000.0,
 "documents": [{"sha256": "…", "relpath": "…", "doc_id": 41}]}
```

- `pha scan|edit|encode|reindex` skip a leased document, naming the handoff and
  its age (`--include-leased` overrides).
- `pha status` (and MCP `pha_collection_status`) report it as *out on
  handover* — this is the "check in later" surface, and it is also how an agent
  driving the archive machine learns that the work is in flight.
- `handoff fetch` / `handoff cancel` set state `applied` / `cancelled`;
  a stale entry (worker gone, bundle lost) is released by `cancel`.
- The `<handoff-id>` in the result must match the lease, so a stray bundle
  cannot be applied to the wrong document set.

The lease is **document-scoped**. Server-scoped exclusivity across machines
cannot be enforced by local lock files and is not attempted here: the
operational rule stays *do not run a job on the archive machine while the
worker is running*, which the sleep schedule makes natural. An optional
stricter mode (`handoff out --exclusive-server`, refusing any local job that
resolves to a server key an outstanding handoff uses) is a decision to settle
(§9).

### 3.7 Data model

No schema change is strictly required: `pages`/`page_edits` already carry text,
`filters`, `reviewed_at` and `exported_at`. Two additions would make the apply
exact:

- a DB writer for **machine-provenance page writes** (text + `filters`, *no*
  `reviewed_at`, `status='done'`) — the mirror of `mark_page_reviewed()`
  (`db.py:412`); and for edits, the mirror of `mark_edit_reviewed()`
  (`db.py:427`);
- per-page `palaeographer`/`model` provenance, needed only if a handover may
  contain pages re-read with a different model. Today provenance is
  document-level (`documents.palaeographer_model`); the per-page columns are
  proposed in `pha-single-page-rescan-enhancement-request.md`. Handoff v1 can
  record the worker's per-page pair in the returned front matter and use it on
  apply when those columns exist, and otherwise apply the document-level pair —
  no dependency, a known limitation.

### 3.8 Staleness interaction

The imported rows must be stamped so the archive's own staleness logic does not
immediately undo them. Two rules:

- record the worker's exact `filters` signature (`ingest.py:1138,1160`) and
  `palaeographer`/`editor` ids, so pages produced with the same configuration
  are *not* stale;
- if the local configuration changed while the document was away, the signature
  will not match — report it as **"N pages imported from the worker are stale
  under the current config"** and let `pha status` keep showing it, rather than
  either silently discarding the worker's output or silently keeping text the
  next scan will overwrite.

### 3.9 Surfaces

- **CLI**: `pha handoff out|in|work|back|fetch|status|cancel` (registered beside
  `bundle`/`unbundle`, `cli.py:2910-2921`).
- **MCP**: read-only `pha_handoff_status` (what is out), consistent with the
  rule that mutations go through the real `pha` CLI and therefore keep the
  model-server lock, staleness and review semantics.
- **Docs**: a README section beside "Moving / sharing collections between
  archives", and a short note in the archive's own `AGENTS.md`/`skills/` (the
  worker machine's agent must know it is working a handoff, not the archive).

### 3.10 Worked example (the 12 Documenta Indica volumes)

```bash
# MacBook, 22:40 — lease the three that will finish overnight
pha handoff out collections/documenta-indica/vol04 collections/documenta-indica/vol05 \
               collections/documenta-indica/vol06 -o ~/ho-DI
rsync -a ~/ho-DI/ studio:~/pha-handoffs/ho-DI/
# sleep

# Mac Studio (or its agent, via MCP)
pha handoff in ~/pha-handoffs/ho-DI && pha handoff work ~/pha-handoffs/ho-DI
pha handoff back ~/pha-handoffs/ho-DI -o ~/ho-DI-back

# MacBook, 08:10
rsync -a studio:~/ho-DI-back/ ~/ho-DI-back/
pha handoff fetch ~/ho-DI-back
# → "applied 3 documents: 1 914 pages transcribed, 1 902 edited,
#    12 pages skipped (you had corrected them), 2 conflicts, 0 stale"
```

The other nine volumes were never leased: `pha scan --path` on the MacBook
still sees them, and `pha handoff status` names the three that are out.

---

## 4. Interim procedure (today, without code changes)

For documents this archive has **no rows for** (never scanned), today's commands
are enough, with one caveat at the end:

1. `rsync -a <archive>/dropbox/collections/<COL>/ <worker>/dropbox/collections/<COL>/`,
   plus the palaeographer/editor/encoder definitions and any
   `<archive>/filters/<id>/` the collection references.
2. On the worker: `pha scan --path collections/<COL>`, `pha edit --path …`,
   `pha encode --path …`.
3. `pha bundle collections/<COL> -o ~/<COL>-back`, rsync back,
   `pha unbundle ~/<COL>-back` on the archive machine. The documents appear with
   *this* archive's ids and regenerated front matter.

Caveats (all consequences of §1.2–1.3):

- Only for documents with **no local rows**. Importing a bundle for a document
  the archive already has creates a **duplicate** (new id) — never do that for a
  partially-processed volume.
- If a document *is* partially processed, delete its `*waiting*` page files from
  the outgoing bundle before unbundling, so the worker sees `pages_ok <
  page_count` and stays `processing` instead of importing stubs as text.
- Do not run `pha scan` on the archive machine for a document the worker still
  holds.

This is why the interim path is only a stopgap: it is exactly the kind of
careful, error-prone, undocumented-in-the-tool operation the enhancement
removes.

---

## 5. Edge cases

- **Document changed on the archive machine while away** (source replaced ⇒ new
  sha): the hand-out's sha matches nothing; refuse and report.
- **Hand-out of a document that is fully `done`** (hand it over only to edit /
  encode): import all pages `done`, status `done`; `pha edit` runs normally.
- **The worker ran a stage the archive had already run** (same config): the
  page texts are identical; apply is a no-op except for `exported_at`.
- **Reviewed on both sides with different text**: conflict report (R5); default
  keep local.
- **A reviewed page locally, worker text differs**: keep local, count it in the
  summary ("12 pages skipped (you had corrected them)").
- **Partial return** (worker finished 2 of 3 documents): apply what returned,
  keep the lease on the third, `handoff status` shows it.
- **Two hand-outs of the same document**: the second `out` refuses while the
  lease is open (or supersedes it with `--force`, cancelling the first).
- **Same document in two hand-offs from two archives sharing a worker archive**:
  joined by sha, so the worker holds one row; the result carries the hand-off id
  and only the originating archive applies it.
- **Worker archive no longer has the hand-off** (wiped): `back` refuses; the
  archive machine runs the work locally after `handoff cancel`.
- **`*waiting*` written by an older worker** (an old pha on the worker): treated
  as a stub on apply, so it can never enter the DB as text (R3) — and the apply
  should say so ("the worker ran pha < X; upgrade it").
- **Embedding endpoint down on the worker**: records/pages still return;
  indexing on the archive machine is a local concern at apply time.
- **Documentation sidecars** (`.dc.json`/`.bib`/`.mods.xml`): travel with the
  source (already handled by `bundle`, `bundle.py:276-290`); applying a result
  never touches them.

## 6. Test plan

- **Unit — stub safety.** A bundle whose library page body is `*waiting*`
  imports as a `waiting` page with no text, for both `pha handoff in` and the
  fixed `pha unbundle` (§1.3 reproduces the current behaviour; must be turned
  into a regression test).
- **Unit — resume.** Import a hand-out with 2 of 5 pages done; assert document
  `processing`, done pages untouched, other pages `waiting`, and that a
  subsequent (stubbed-client) `pha scan` attempts exactly the three pending
  pages.
- **Round trip.** Archive A: doc with a partially done document + one
  human-reviewed page. Hand out → import into B → produce B's results → apply
  on A. Assert: same `documents.id` and slug, page texts from B, the reviewed
  page unchanged, no new document row, chunk count matches the page count.
- **Conflict.** Reviewed on both sides with different text: default keeps local,
  `--on-conflict take` takes B, both report.
- **Edit provenance.** An edit whose `raw_sha` does not match the applied raw is
  dropped and reported.
- **Lease.** After `out`, `pha scan|edit|encode|reindex` over the leased document
  do nothing and say why; `--include-leased` overrides; `fetch`/`cancel` clear
  it; `pha status` shows the lease.
- **Identity.** `pha handoff fetch` for a sha the archive does not have creates a
  document (never-scanned case) exactly once; a second fetch is idempotent.
- **Staleness.** Change the editor config on A between `out` and `fetch`; assert
  the apply reports the pages as stale under the current config.
- **No side effects.** `--dry-run` writes nothing anywhere.

## 7. Acceptance criteria

1. The 12-volume scenario in §3.10 completes with the MacBook asleep for the
   middle of it, and the applied pages are indistinguishable from a local run
   (same ids, same slugs, same library layout, indexed).
2. No `*waiting*` text can reach the DB from any bundle path.
3. A human correction made on either machine survives the round trip, or is
   reported as a conflict — never silently replaced.
4. Running `pha scan` on the archive machine while a document is out is
   impossible without an explicit override.
5. `pha unbundle` behaviour for the existing "share a finished collection"
   use case is unchanged except for the stub fix.

## 8. Non-goals and rejected alternatives

- **Rejected — synchronise one `archive.db` over a share (SMB/NFS) and let
  either machine work it.** SQLite is not a network filesystem database
  (advisory locks do not hold), and `library/` is version-dated output. One
  writer at a time, or two stores with an explicit apply — not a shared file.
- **Rejected — make the always-on machine the master archive.** The MacBook is
  often out of reach of the desktop; the archive must work fully offline. (The
  reverse — desktop as a *model server* — already works via LM Link and needs no
  code.)
- **Rejected — a background daemon / job queue / push protocol.** The pipeline is
  resumable and idempotent per page; pending work is derivable. A lease file and
  three commands solve the actual problem.
- **Rejected — extend `unbundle` with a flag instead of a new group.** The two
  differ in identity (new ids vs update in place), in resume semantics (skip vs
  finish), and in direction (import vs apply). Overloading one command would put
  "delete and re-add this document" one flag away from "apply the overnight
  work".
- **Non-goal — concurrent editing on both machines.** The lease serialises; the
  merge is designed for *one* writer at a time plus human corrections.
- **Non-goal — cross-archive trust/authentication.** Same owner, private LAN/VPN.
- **Non-goal — moving documents between archives permanently.** That is
  `pha bundle --move`, unchanged.

## 9. Decisions to settle

Settled 2026-09-20; the choice is named after each.

1. **Lease strictness.** Document-scoped lease + documented rule (recommended),
   or also `--exclusive-server` (refuse local jobs sharing a server key with an
   outstanding hand-out)?
   → **Document-scoped lease.** No server-level exclusivity: the model-server
   lock is already user-global *per machine*, and the two machines cannot see
   each other's locks, so a lease on the document is the part that matters.
   `--include-leased` was added as the deliberate override.
2. **Worker archive lifetime.** One persistent worker archive that accumulates
   hand-offs (recommended: defs installed once, renders cached), or a fresh
   scratch archive per hand-out (`--scratch`)?
   → **One persistent worker archive**; no `--scratch`.
3. **Conflict default.** Keep local (recommended, conservative) or take the
   worker's text?
   → **Keep local**, and report the page so it can be looked at.
4. **`work` convenience.** Include it, or keep the three commands explicit so
   the operator (or agent) sees each stage's outcome? (An agent-driven worker
   may prefer the explicit form.)
   → **Both, with the explicit commands primary** — `work` runs exactly those
   three and surfaces each exit code (deviation from the either/or framing).
5. **Reviewed on the worker.** Should a page a human corrected *on the worker*
   come back as `reviewed` (recommended: yes — it is a human correction), or be
   marked as an imported machine result?
   → **Yes**, carrying the worker's own timestamp. Machine output comes back
   unmarked, exactly as if produced locally.
6. **Where the hand-off id lives.** `<archive>/.pha/handoffs/` (recommended,
   machine-local and gitignored) or inside the archive's `library/` so it
   travels with it?
   → **`<archive>/.pha/handoffs/<id>.json`** (machine-local, gitignored).

## 10. Effort

- `src/personal_historical_archive/handoff.py` — new: hand-out export, worker
  import, result build, apply, lease I/O (~400–600 lines).
- `bundle.py` — factor the shared helpers (`_install_defs`,
  `_copy_dropbox_payload`, `_pin_selections`, `_parse_library_file`) so both
  paths use them; **fix the stub import** for `unbundle` too (small).
- `db.py` — two machine-provenance writers (mirrors of `mark_page_reviewed` /
  `mark_edit_reviewed`). **Not needed in the end** — see the note at the top of
  this file.
- `ingest.py` / `cli.py` — lease checks in `scan_once`, `edit_all`/`edit_path`,
  `encode_*`, `reindex_all`; the `handoff` command group; `pha status` lease
  line.
- `mcp_server.py` — read-only `pha_handoff_status`.
- Tests per §6, plus the stub regression test.
- Docs: README section, `AGENTS.md` (archive-side guidance), and a
  `skills/` entry for an agent working a hand-off.

Roughly the size of the stage-filters change: a focused new module reusing
existing internals, one real bug fix, and lease plumbing at four call sites.
The risky parts are the merge rules and the staleness interaction (§3.5, §3.8) —
worth landing behind `--dry-run` and exercising on one volume before the
12-volume run.

## 11. References

- `src/personal_historical_archive/bundle.py` — `export_bundle` (`165`),
  manifest (`358-392`), `_install_defs` (`486`), `_copy_dropbox_payload` (`541`),
  `_pin_selections` (`569`), `import_bundle` (`785`), page import loop
  (`651-671`), status rule (`744-746`).
- `src/personal_historical_archive/ingest.py` — `_raw_sha` (`119`), resume/skip
  logic (`786-825`), `_WAITING_STUB` (`500`), `write_document_pages`
  (`1260-1320`), `_configured_filters_signature` (`1138`), `_stored_page_filters`
  (`1160`), `pending_review_files` (`1599`), `_parse_library_file` (`1624`),
  `review_import` (`1687`), `unreview_import` (`1750`), `sha256_of` (`254`),
  `_doc_slug` (`407`).
- `src/personal_historical_archive/db.py` — `add_page` (`378`),
  `set_page_result` (`396`), `mark_page_reviewed` (`412`), `mark_edit_reviewed`
  (`427`), `set_page_edit` (`503`), `set_document_status` (`261`).
- `src/personal_historical_archive/locks.py` — the user-global, per-machine lock
  dir (`64-82`), the "endpoint URLs cannot prove locality" rationale (`15-28`).
- `src/personal_historical_archive/cli.py` — `cmd_bundle` (`1560`),
  `cmd_unbundle` (`1591`), parser registrations (`2910-2921`).
- Related: `enhancements/pha-embed-loss-bug-report.md` (why overlapping jobs
  matter), `enhancements/pha-review-scope-bug-report.md` (the review stamp),
  `enhancements/pha-single-page-rescan-enhancement-request.md` (per-page
  provenance), `README.md` §"Moving / sharing collections between archives",
  `MCP_CLIENTS.md` §"Machine-to-machine".

## 12. Addendum (2026-09-21) — what does *not* travel: the page renders

§3.3 says the return leg carries "no renders (the archive machine already has
them)". That holds only for a document rendered **before** it left. Hand out a
document that was registered but never scanned and the assumption breaks in a
way nothing reports.

Measured on `collections/monumenta-brasiliae` (4 volumes, 2 676 pages, handed
out with 0 pages each — the owner had seeded the rows and killed the first scan
after a few seconds, leaving 9–17 stray renders per volume):

- `pha handoff back` carries per-page texts, edits and provenance — **no renders**.
- `pha handoff fetch` applies them, writes the library and indexes the chunks
  (`index_document`, `handoff.py:1222`) — and creates no renders.
- The viewer resolves an image through `addresses.render_path()` and answers
  **404 "no render for this page"** for every page of the returned document
  (`serve.py:478-497`). The archive is searchable and citable but not
  *viewable* — and for a historian checking a doubtful reading the image is the
  point.

**Verified workaround (no model, no network).** Renders are a pure function of
the source and the render settings. Calling pha's own renderer with the
collection's settings — `render_document(pdf, renders/<sha256>, dpi=300,
max_px=2500, jpeg_quality=88)` — reproduced page 1 of volume I **byte for byte**
as the worker had made it:

```
240 467 bytes
sha256 2d78dfdaaf91fddf53d2c8c99b0a88c0d43a64cd6b369f4250d7ec405824ab12
(same size and same digest on both machines)
```

A ten-line script regenerated the 2 624 missing pages of the four volumes.

**Proposed fixes, cheapest first:**

- **R1 — `fetch` renders what it just imported when the local render is
  missing.** The pages are in hand, the source is in the dropbox, the operation
  is deterministic and free; `pha scan` already has the render step, so this is
  reuse rather than new machinery.
- **R2 — `handoff out` includes renders for a document that has no local
  render** (it is leaving unscanned; the worker renders anyway, but the owner
  will have nothing on return).
- **R3 — at minimum, say it.** A fetch that imports 2 676 pages with no local
  render should print "N pages imported without a page image; `/<slug>/pNNN.jpg`
  will 404 until they are rendered", instead of leaving it to be discovered by
  clicking a citation.
