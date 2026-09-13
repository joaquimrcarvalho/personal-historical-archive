# Note — `.writing/` is gitignored; review that decision later

**Status:** ignored by decision, **not** resolved. Nothing to implement here;
this note exists so the choice is revisited rather than fossilising.
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

**What creates it.** A grep of the DSH Desktop checkout
(`/Applications/DSH Desktop.app/Contents/Resources/app`) for `.writing` finds no
reference, so it could not be confirmed as a Harness feature. The shape (a
`comments/` sibling, per-change serial numbering) reads like an editing/review
tool's capture, but that is inference, not evidence. **Do not state the origin
as fact until it is observed.**

## Decision taken

Added `.writing/` to `.gitignore` (the conservative middle option):

- it stays on disk, so a tool that depends on it is unaffected;
- `git status` is clean again, and a `git add -A` cannot commit 633 stale
  copies of the repo's own docs;
- no content is destroyed, which matters while the origin is unknown.

Alternatives, for the record: **delete it** (recovers 4.3 MB; nothing unique is
lost, per the table above) or **leave it untracked** (harmless but noisy).

## When to revisit — and what would settle it

1. **The origin is identified.** If tooling is found that reads or writes
   `.writing/`, its retention rules decide this, not us. The cheapest test:
   note the current `mtime`/file count, make a markdown edit, and see whether
   the directory changes. If it does, capture the creating process.
2. **It grows.** It is a *snapshot* today; if entries start appearing as
   `002.md`, `003.md` per path it has become a revision store — a different
   thing entirely, and worth understanding before it accumulates.
3. **Space or confusion matters.** At 4.3 MB this is trivial; if it reaches
   hundreds of MB, delete it (no unique content, per the table).
4. **A repo-wide cleanup happens** (`pha prune`-style housekeeping, or a sweep
   of `test-1577-archive/`, `benchmarks/` and the other ignored scratch trees),
   fold this in: the note is the reminder that `.writing/` was parked, not
   reviewed.

Re-evaluate with the same three numbers that decided it: *identical*,
*stale*, *orphaned*. If `orphaned` ever becomes non-zero, this note's
"no unique content" conclusion no longer holds and the directory must be
examined file by file before anything deletes it.
