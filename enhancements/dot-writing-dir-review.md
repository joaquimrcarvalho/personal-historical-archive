# Note — `.writing/` is gitignored; review that decision later

**Status:** ignored by decision. **Origin now answered** (reported, not
independently verified — see below): it is the Harness writing-tool's
**snapshot cache**, i.e. derived data. Nothing to implement here; this note
exists so the retention choice is revisited rather than fossilising.
**Date:** 2026-09. **Written against:** pha 0.23.0, repo state at `21add40`.

## What the directory is

A per-file **snapshot of the repo's markdown**, created `2026-09-12 11:47`:

```
.writing/
  files/<repo-relative-path>/001.md     # 633 files, 4.3 MB
  comments/                             # empty
```

Every capture is numbered `001.md` — one snapshot per path, taken at a single
moment, **not** a revision history. Groups captured: the top-level docs
(`README.md`, `AGENTS.md`, `FILTERS_PLAN.md`, `MCP_CLIENTS.md`,
`DSH_PLUGIN.md`, `VSCODE_EXTENSION_SPEC.md`, `WEB_INTERFACE_PLAN.md`,
`ENCODER_TOOLS_PLAN.md`, `HISTORIANS_README.md`), every `.md` under
`enhancements/`, `dsh-pha/`, `skills/`, `prompts/`, `notes/`, `editors/`,
`models/`, `palaeographers/`, `encoders/`, plus the data trees `dropbox/`,
`library/`, `benchmarks/` and `test-1577-archive/` (including its per-page
transcriptions and edits). Vendored licence/README markdown swept in from
`.uv-cache/` and `.pytest_cache/` too.

The `<path>/NNN.md` layout is used for **files and directories alike** (entries
exist for directories such as `library/.../transcription-minimax-vl`), which
is consistent with a tool capturing each changed path serially.

## What was verified (before deciding)

| check | result |
|---|---|
| identical to the live file | **618** |
| stale (live file changed since the snapshot) | **15** — `README.md` (270 diff lines), `AGENTS.md` (122), `DSH_PLUGIN.md` (5) and 12 others |
| orphaned (live file no longer exists) | **0** |
| size | 4.3 MB |

So it holds **no unique content**: nothing is orphaned, and the only entries
that differ are out-of-date copies of files that still exist and have moved on.
Deleting it would lose nothing that is not already in the working tree or in
git history.

## What is NOT known

**What creates it — now answered by the operator, but not independently
verified.** Reported (2026-09, another Harness session): *"`.writing/`
directory — that's the Harness writing-tool's snapshot cache, not part of the
repo."* That is the working explanation and it fits every observation: a
**snapshot cache** for a writing/editing tool, keyed per captured path, one
`001.md` per path, with the `comments/` sibling next to it. It is recorded here
as **reported, not proven** — see below for the two searches that failed to
confirm it.

Two attempts to corroborate from the installed Harness, both negative:

1. Grepping `/Applications/DSH Desktop.app/Contents/Resources/app` — **the path
   does not exist**; that checkout is a single packed `app.asar`, so the search
   found nothing rather than finding absence.
2. Scanning that `app.asar` for `.writing` — 10 hits, **all false positives**:
   `writingMode` (a CSS/DOM property) and `state.writing` (Node's stream
   state), plus a `writingScript` typeface option. No `.writing` directory, no
   snapshot cache, nothing about a writing tool.

So the bundle neither confirms nor denies it; the tool may live in
`app.asar.unpacked`, in a package not shipped here, or in a native module.
**The operator's answer is authoritative for practical purposes** — it makes
the gitignore decision clearly right, since a cache is derived data by
definition — but do not upgrade it to "verified" without seeing the writer.

## Decision taken

Added `.writing/` to `.gitignore`.

If it is a **cache** — as reported — this is not a close call: derived data
does not belong in version control, so **do not commit it**, ever. The only
real question left is deletion, and the conservative choice was made for a
reason that has now largely evaporated: with the origin known, the standing
recommendation is **delete it whenever convenient** (it regenerates, or it does
not; either way it holds nothing unique — 0 orphans). It is parked rather than
deleted only because deleting another tool's cache mid-session is not this
note's call.

Alternatives, for the record: **delete it** (recovers 4.3 MB; nothing unique is
lost, per the table above) or **leave it untracked** (harmless but noisy).

## When to revisit

1. **Deletion looks worthwhile** — e.g. a repo-wide sweep of the ignored scratch
   trees (`test-1577-archive/`, `benchmarks/`, `.writing/`). With the cache
   origin reported and `orphaned = 0`, deleting is safe; re-run the three
   counts first anyway.
2. **It grows.** It is a *snapshot* today; if entries start appearing as
   `002.md`, `003.md` per path the tool is retaining history, which changes the
   space calculus (and is worth knowing before it accumulates).
3. **The writer is seen.** If a tooling change or a direct observation shows the
   cache being written, record it here and mark the origin **verified**; the
   retention decision then belongs to that tool, not to us.
4. **A `.writing/` entry is ever missing a live counterpart** (`orphaned > 0`).
   That is the one condition that breaks the "no unique content" conclusion —
   a capture of a file that no longer exists anywhere, including git history —
   and it would force a file-by-file look before anything deletes the tree.

Re-evaluate with the same three numbers that decided it: *identical*,
*stale*, *orphaned*.
