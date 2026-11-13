# Enhancement request — portable archive paths: `pha archive-relocate` (phase 1) and relative document identity (phase 2)

**Status:** Phase 1 drafted, decisions recorded 2026-10-23 (§12: name, `--paths`
default, `--global`, "warn and hold"). Phase 2 proposed (§13, the recommended
long-term design: a move needs no repair at all).
**Author/date:** archive work session, 2026-10-23.
**Written against:** pha 0.46.5.
**Related:** `pha set archive-dir`, `pha info`, `pha status`, `pha scan`,
`ARCHITECTURE.md` §1 (archive resolution), `OPERATING.md`,
`MULTI_COMPUTER.md` (moving between machines), `README.md` (CLI reference),
`db.get_document_by_path`, `ingest.ingest_file`, the `locks` module.

## 0. The two phases

- **Phase 1 — repair (`pha archive-relocate`, §1–§12).** A copied archive at a
  new root is made usable again: the stored absolute paths are rebased and the
  pointer moves. It fixes archives that are already broken, works on today's
  schema, and is what a user on an old release needs.
- **Phase 2 — remove the problem (relative identity, §13).** The document's
  identity becomes its dropbox-relative path and the absolute path is derived at
  runtime. Copying an archive anywhere then needs only the pointer change;
  `archive-relocate` shrinks to a repair tool for old databases.

Phase 1 can ship immediately and does not depend on Phase 2.

## 1. Motivation

Archives grow, and they get moved: onto an external drive, onto a bigger disk, or
to a new machine. Copying the folder is trivial — everything inside it is
relative by design (`dropbox/`, `library/`, `renders/<sha>`, `models/`,
`skills/`, the hand-off leases). **One column is not:** `documents.path` stores
the *absolute* path of each source file, and it is the identity a scan matches
on.

There is no command, no warning and no documentation for the move. A user who
copies the archive, points `pha set archive-dir` at the copy and runs `pha scan`
gets a scan that treats **every document as new**: full re-extraction (hours to
days of model calls), duplicate rows (the path is unique but the *new* path is a
different key), and no error — the scan reports success.

`pha set archive-dir` only writes the pointer. It never touches the DB, so the
archive is silently inconsistent from that moment on.

## 2. What happens today (measured, 0.46.5)

A throwaway archive with one known document:

| Step | Result |
|---|---|
| archive with one document in the DB | `documents: 1`, nothing "new" |
| `mv A B`, then `PHA_ARCHIVE_DIR=B pha status` | `documents: 1` **and `new: 1 file(s) not yet scanned`** — not recognised |
| the DB meanwhile | still holds `/…/A/dropbox/collections/X/doc.pdf` |
| after `UPDATE documents SET path = replace(path,'…/A','…/B')` | `new:` disappears — recognised again |

Cause, in code:

- `documents.path` is `TEXT NOT NULL UNIQUE` (`db.py`, schema) and holds an
  absolute path;
- `ingest_file()` looks a document up with `db.get_document_by_path(conn,
  str(path))`, an exact `WHERE path = ?` — no fallback to `sha256` or to the
  dropbox-relative path;
- `db.add_document()` would simply insert a second row for the new path, and
  `sha256` is not unique, so the old row survives beside it.

Three columns hold absolute paths:

| Column | Rebased? | Consequence if left stale |
|---|---|---|
| `documents.path` | **required** | the document looks new; re-extracted, duplicated |
| `notes.path` | optional | self-heals: `notes.py` compares `row["path"]` with the file's current path and re-indexes when they differ |
| `document_bibliography.sidecar_path` | optional | self-heals: `ingest.sync_bibliography()` compares the stored path with the sidecar's current path and re-parses when they differ |

Everything else is relative or content-addressed: `documents.dir_path`,
`documents.library_slug`, `library/`, `renders/<sha>`, `models/`,
`palaeographers/`, `editors/`, `encoders/`, `notes/`, `skills/`, and the
hand-off leases (`sha256` + `relpath`, in `.pha/handoffs/`). Model-server locks
live outside the archive (a user-global cache), and `pha-location.md` /
`.pha/location.json` describe the tool on this machine, not the data — they are
rewritten on the next run.

## 3. Goals

1. One command turns a *copied* archive into a *working* archive at its new
   location: it rebases the stored paths and points pha at it.
2. It is safe: previewable, backed up, transactional, and it refuses rather than
   guesses.
3. It is idempotent and reports exactly what changed and what it could not fix.
4. The failure mode stops being silent: `pha status` (and the start of
   `pha scan`) say that the archive appears to have moved, and name the command.
5. An agent or a historian gets the same answer from `pha help` and from the
   manual, with the external-drive caveats in one place.
6. **Long term (§13):** a move needs no repair at all — identity is the
   dropbox-relative path, the absolute path is derived, and only the pointer
   changes.

## 4. Non-goals

- **Not moving the bytes.** The user copies the folder (or uses the drive's
  own tooling); this command only reconciles the DB with where the files now
  are. A `--move` that renames the folder is out of scope for v1.
- **Not rebuilding renders.** `renders/` may be left behind entirely; it is a
  content-addressed cache (`pha render` rebuilds, `pha prune` sweeps).
- **Not touching the library.** Library folders and `library_slug` are already
  relative and keep working.
- **Not migrating between machines' tool installs.** `pha update` /
  `pha-location.md` cover where the *tool* lives.
- **Not auto-repairing on every scan** (see §8).
- **Not a schema change.** Phase 1 repairs an existing database. Making the
  identity relative (so no repair is ever needed) is Phase 2, §13.

## 5. Proposed design

### 5.1 Command surface

```
pha archive-relocate <new-root> [--from <old-root>] [--dry-run] [--no-pointer]
                     [--no-backup] [--paths documents|all] [--global] [--force]
```

- `<new-root>` — the copied archive (the directory holding `archive.db`,
  `dropbox/`, `library/` …). Resolved with `Path.resolve()`.
- `--from` — the old root. Default: `cfg.archive_dir` (the resolved archive
  pha is currently using).
- `--dry-run` — print the plan and the counts; write nothing (DB and config
  untouched).
- `--no-pointer` — rebase only; do not change `paths.archive_dir`.
- `--no-backup` — skip the DB backup (documented as not recommended).
- `--paths documents|all` — **default `all`.** All three absolute-path columns
  are rewritten, so the notes index and the stored bibliography references are
  consistent immediately and nothing has to be re-read. `documents` rewrites
  only the load-bearing column; the other two then repair themselves the next
  time notes or bibliography are used (that self-healing is existing
  behaviour, not something this command introduces).
- `--global` — write the pointer to the per-user settings
  (`~/.config/pha/config.yaml`, `%APPDATA%\pha\config.yaml` on Windows) instead
  of the project `config.yaml`. Use it when the archive is not tied to a
  project directory, exactly as `pha set archive-dir --global` does.
- `--force` — proceed when rows exist outside both roots (see §5.2), reporting
  them.

`archive-relocate` is the chosen name (§12). It says "the archive's root
moves", which `relocate` alone did not, and it stays distinct from `pha mv`
(moves a document *inside* the archive) and from `pha set archive-dir` (writes
the pointer only).

### 5.2 Preflight — refuse, do not guess

Run every check before the first write:

1. `<new-root>` exists, is a directory, and contains `archive.db`; warn and stop
   if it does not look like an archive (no `dropbox/`).
2. `old-root != new-root` after resolving symlinks.
3. **No pha job is running.** Refuse if any model-server lock is held for a
   configured server (`locks.job_keys(...)` / the lock dir the `doctor`
   command prints), because a running scan or edit is writing the same DB.
   Say who holds it, as the scan does.
4. Open the DB and count rows by prefix:
   - `N` rows under `old-root` → the work;
   - `M` rows under `new-root` → already rebased (report; not an error);
   - `K` rows under neither → refuse unless `--force`, naming a few. These are
     usually a document removed outside pha, or a half-finished earlier move.
5. If `N == 0` and `M > 0`: nothing to rebase — report "already relocated" and
   (unless `--no-pointer`) just update the pointer. If `N == 0` and `M == 0`:
   refuse, and suggest `pha set archive-dir` alone if the archive was never
   used.

### 5.3 Writes

1. `PRAGMA wal_checkpoint(TRUNCATE)` (the DB is in WAL mode), so the backup is
   complete and nothing is left in `-wal`.
2. Back up: `archive.db` → `archive.db.bak-<UTC stamp>` beside it (unless
   `--no-backup`). Print the path.
3. One transaction, three prefix replacements, each matched with the **path
   separator** so a sibling directory is never caught (`/Users/jrc/pha` must not
   rewrite `/Users/jrc/pha-other`):
   ```sql
   UPDATE documents SET path = replace(path, :old, :new)
    WHERE path LIKE :old || '/' || '%';
   ```
   (`notes.path` and `document_bibliography.sidecar_path` identically; that is
   the default, `--paths all`).
4. Collision check before commit: since `documents.path` is `UNIQUE`, verify the
   replacement cannot produce a duplicate (rows already at `new-root` would
   collide). If any would, abort and roll back with the colliding rows named.
5. Commit, then update the pointer with the existing `_set_archive_dir_smart()`
   path (unless `--no-pointer`), so the user does not have to run a second
   command and the source is recorded the same way the CLI already records it.
   With `--global` it goes through `_set_archive_dir_in_user_config()` instead,
   which is what `pha set archive-dir --global` does.

### 5.4 Report

```
pha archive-relocate /Volumes/Archive/pha-home
  from   /Users/jrc/pha-home          (config.yaml)
  to     /Volumes/Archive/pha-home    (resolved)
  backup /Volumes/Archive/pha-home/archive.db.bak-20261023T201500Z
  documents.path                       3 updated
  notes.path                           1 updated
  document_bibliography.sidecar_path   2 updated
  pointer  paths.archive_dir -> /Volumes/Archive/pha-home   (user settings)
  ! 1 document path is missing on disk: collections/X/gone.pdf (left as-is)
  next: pha status   — expect no "new" files that were already in the archive
```

Counts go to stdout; the "missing on disk" list and any refusal go to stderr,
matching the rest of the CLI.

### 5.5 Verification and idempotence

- After the rewrite, check every rebased path with `Path.exists()` and list the
  ones that do not (a file removed outside pha, or a path-normalisation
  difference — see §6). Never "fix" those silently.
- A second run rebases 0 rows and says so; the pointer is already correct.
- Exit codes: `0` success (including "already relocated"); `2` refused (with the
  reason); `1` unexpected error.

## 6. Companion: make the move detectable

The command only helps someone who already suspects. Add a cheap check wherever
an archive opens read-only:

```sql
SELECT COUNT(*) FROM documents WHERE path NOT LIKE :root || '/' || '%';
```

- `pha status` warns (read-only; it must never stop someone looking):
  `! N document(s) are stored under another path (/Users/jrc/pha-home) — this archive appears to have moved. Run: pha archive-relocate <this archive>`
- **The passes that would do the expensive thing warn and hold.** `pha scan`,
  `pha edit` and `pha encode` print the same line and **stop before doing any
  work** (exit `2`), because continuing means re-reading an archive that merely
  moved — the measured failure spent hours and reported success. The scan waits
  for the user: relocate, or pass `--ignore-moved` to proceed deliberately
  (the rare legitimate case — files replaced outside pha, or a half-moved
  archive being finished by hand).
- `pha doctor` / `pha info --json` carry the count as a field for agents, so an
  agent can answer "why does my scan refuse?" without guessing.

This is the part that would have caught the measured failure: the scan was about
to re-extract 2 700 pages and reported success.

## 7. Edge cases

- **Symlinked roots.** Resolve both roots before comparing or replacing:
  `/tmp/…` is `/private/tmp/…` on macOS, and a mismatch there makes a "same"
  path look different (this happened while measuring §2).
- **Prefix safety.** Match `old + os.sep`, never a bare prefix.
- **Directory-of-images documents.** Their `path` is a directory; the same
  replacement applies. `dir_path` stays relative.
- **Unicode normalisation.** APFS stores names NFD, some external filesystems
  preserve whatever bytes were written. The textual replacement keeps the
  suffix; if a rebased path does not exist, report it rather than guessing a
  normalised variant (a future `--repair-normalisation` could try).
- **Case-insensitive filesystem.** `resolve()` handles the common macOS case;
  do not lowercase anything when comparing.
- **External-drive filesystem.** The DB is SQLite in **WAL** mode: APFS
  (macOS), ext4 (Linux) or NTFS (Windows) are safe; exFAT/FAT32 and network
  shares (SMB/NFS) are not (locking/shared memory, 4 GB limits). This belongs
  in the documented recipe, not in the command, but the command should point at
  it when `<new-root>` is not on a local volume it recognises.
- **Renders left behind.** Fine: `pha render` rebuilds on demand, `pha prune`
  sweeps. Say so in the report when `renders/` is absent from `<new-root>`.
- **Hand-off leases.** `.pha/handoffs/*.json` are relpath-based and machine-local;
  they travel harmlessly. A lease still out means the document is skipped —
  finish or cancel it before moving, and say so in the recipe.
- **Moving to another machine.** Same command (the DB travels); the machine-local
  `pha-location.md` / `.pha/location.json` are rewritten. The recipe should say
  "copy, then relocate, then `pha status`".

## 8. Alternatives considered

| Alternative | Why not |
|---|---|
| Documentation only (the SQL recipe) | Cheap, but the user must run SQL against a WAL DB by hand, and the silent re-extraction stays |
| Auto-rebase on the first scan that sees paths outside the root | Writes during a "read" command; ambiguous when a file legitimately moved inside the dropbox (that is `pha mv`'s job); surprising in scripts |
| Fall back to `relpath` + `sha256` in `get_document_by_path` | Would make a move "just work" for scans, but leaves every stored path stale (and `pha mv`/`status`/`serve` read them), and risks adopting a different file that landed on the same relpath |
| `pha set archive-dir <new> --rebase` | Discoverable, but `set archive-dir` is documented as pointer-only and is called by wrappers/tests; overloading it risks accidental data writes |
| `pha bundle` + `pha unbundle` | Not a move: new ids, new rows, re-import, and it needs a second archive |

## 9. Risks

- **A wrong prefix rewrites the wrong rows.** Mitigated by separator-anchored
  matches, `--dry-run`, the preflight counts, a backup inside the transaction's
  neighbourhood, and the collision check.
- **A move while a job runs.** Mitigated by the lock check (§5.2.3).
- **Partial state** (rows under both roots). Refused by default, reported with
  `--force`; the replacement can never collide because old-root rows move to a
  prefix no row occupies (checked).
- **WAL left behind after a copy.** The command checkpoints before backing up;
  the documented recipe checkpoints before copying.

## 10. Acceptance criteria and tests (`tests/test_archive_relocate.py`)

1. **Happy path.** Init an archive, insert two documents (one PDF, one
   directory), copy the folder to a new path, run `pha archive-relocate NEW`:
   the pointer moves, `documents.path` all start with NEW, document ids/pages/
   edits/records/library folders are unchanged, and `pha status` reports no
   "new" files that were known before.
2. **Dry run.** DB bytes and `config.yaml` are unchanged after
   `pha archive-relocate NEW --dry-run`; the report shows the counts.
3. **Idempotence.** A second run updates 0 rows, says "already relocated", and
   leaves the pointer correct.
4. **Refusals.** Missing `<new-root>`; `<new-root>` without `archive.db`;
   `old == new`; a held model-server lock (monkeypatched); rows outside both
   roots without `--force` — each exits non-zero with the reason on stderr,
   and nothing is written.
5. **`--no-pointer`.** The DB is rebased; the config pointer is untouched.
6. **`--paths`.** By default all three columns are rebased
   (`notes.path`, `document_bibliography.sidecar_path` included); with
   `--paths documents` only `documents.path` changes, and the other two still
   re-index correctly on next use (their self-healing is already covered by
   their own tests).
7. **`--global`.** The pointer lands in the per-user settings file, not the
   project `config.yaml`.
8. **Prefix safety.** A sibling directory (`/…/pha-other`) keeps its rows.
9. **Backup.** `archive.db.bak-*` exists, opens, and contains the same rows
   (count) as the rebased DB.
10. **Collision.** A row already at the destination path aborts the transaction;
    the DB is unchanged.
11. **Detection.** `pha status` on a moved-but-not-relocated archive prints the
    "appears to have moved" line naming `pha archive-relocate` (capsys test).
12. **Warn and hold.** `pha scan` on the same archive exits `2` **before any
    extraction** and names the command; `pha scan --ignore-moved` proceeds
    (monkeypatched extraction), and `pha edit` / `pha encode` behave the same.

## 11. Documentation to update

- `README.md`: a "Moving the archive (external drive or new disk)" section with
  the recipe — stop jobs, checkpoint/copy (or `.backup`),
  `pha archive-relocate`, `pha status` — and the filesystem caveat; plus the
  CLI reference line.
- `OPERATING.md`: the data-changing command list, and the warn-and-hold
  semantics for `scan`/`edit`/`encode`.
- `pha help` / `pha --help`: the command in COMMON COMMANDS.
- `ARCHITECTURE.md` §1: the three absolute-path columns and why only
  `documents.path` is load-bearing.
- `MULTI_COMPUTER.md`: a cross-reference for "moving the archive to the other
  machine" (copy, relocate, status) next to the hand-over section.
- The archive's own generated docs (`archive_init.py`) need no change: an agent
  reading them learns the command from `pha help`.

## 12. Decisions (2026-10-23)

1. **Name: `pha archive-relocate`.** Chosen over `pha relocate` (which does not
   say *what* moves) and over `pha set archive-dir --rebase` (which would give a
   pointer command data-writing semantics).
2. **`--paths` default: `all`.** The question was whether to also rewrite
   `notes.path` and `document_bibliography.sidecar_path`, which are not
   load-bearing: if they are left stale, pha notices the path difference the
   next time notes or bibliography are used and re-reads them (nothing is lost).
   Rewriting them anyway costs one `UPDATE` and leaves the index and the stored
   references consistent immediately, so that is the default; `--paths
   documents` is there for a minimal, load-bearing-only run.
3. **`--move` excluded for now.** Copying is the user's business and the safer
   order (copy, verify, relocate). A future `--move` could rename the folder
   after a successful rebase.
4. **Detection: warn and hold.** `pha status` / `pha info` warn only (a lookup
   must never be refused). `pha scan`, `pha edit` and `pha encode` print the
   warning and **stop before doing any work** (exit `2`), so the archive waits
   for the user to relocate; `--ignore-moved` is the deliberate override.
5. **`--global`: yes.** The archive may move to a disk that is not tied to a
   project directory, so the pointer write offers the same scope choice as
   `pha set archive-dir` (`--global` → per-user settings).
6. **Phase 2 recorded (§13).** Asked why every path is not relative to the
   archive root in the first place: it can be, and should be. `documents.path`
   is the legacy identity; the dropbox-relative value is already computed and
   stored as `dir_path` + `filename` (and used by hand-off/bundles as
   `relpath`). §13 records the target schema, migration, code changes and
   acceptance criteria, and keeps Phase 1 as the interim fix for archives that
   are already broken.

## 13. Phase 2 — make the document identity relative (recommended long-term)

Phase 1 repairs a database after a move. Phase 2 removes the need for the
repair: identify a document by its **dropbox-relative** path and derive the
absolute path at runtime, and copying the archive anywhere — another disk,
another mount point, another machine — needs only the pointer change.

### 13.1 The relative value already exists

At insert time `ingest_file()` computes:

```python
rel_dir = path.parent.relative_to(cfg.dropbox).as_posix()   # → documents.dir_path
```

and `filename` completes it. Hand-off and bundles already identify documents as
`sha256` + `relpath` (the same value) precisely because ids and absolute paths do
not travel. The absolute `path` is the only identity consumer left — the odd one
out, not a requirement.

Note the base is the **dropbox**, not the archive root: `paths.dropbox` /
`PHA_DROPBOX` / the deprecated `pha set dropbox` can place the documents
directory outside the archive, and only dropbox-relative is portable *and*
correct.

### 13.2 Target schema

```sql
documents.rel_path TEXT     -- POSIX, relative to cfg.dropbox; NULL until backfilled
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_rel_path
    ON documents(rel_path) WHERE rel_path IS NOT NULL;
```

- `rel_path` = `dir_path` + `/` + `filename` (`""` for a document at the dropbox
  root), always POSIX (`as_posix()`), because it is matched with `/` by
  `--collection`, the MCP/plugin filters and the page addresses.
- Keep `documents.path` but **demote it to a cache** (`cfg.dropbox / rel_path`,
  refreshed on scan/mv). It is no longer identity. Dropping the column is a
  separate, optional later cleanup: 41 call sites read it and a cache is cheap.

### 13.3 Migration

1. `ALTER TABLE documents ADD COLUMN rel_path TEXT` (the existing
   `db.migrate()` column-addition pattern).
2. **Backfill in Python, where `cfg` is available.** `db.migrate()` receives only
   the connection today, so it needs the root passed in
   (`migrate(conn, dropbox=cfg.dropbox)`), or the backfill runs from
   `Config.ensure_dirs`. For each row with `rel_path IS NULL`, compute it from
   `path` minus the resolved dropbox root. Rows whose path is not under the
   dropbox stay NULL and are reported (the outside-dropbox case, §13.5).
3. Create the partial unique index (NULLs allowed), so the migration is
   idempotent and can be re-run.
4. **One release writes both columns and reads `rel_path` first**, falling back
   to `path` when it is NULL. That fallback is what makes a downgrade safe: an
   older pha writing a row after the migration inserts `path` only, the newer pha
   recognises it through `path`, and fills `rel_path` on the next write.

### 13.4 Code changes

- `Config.doc_path(rel_path)` → `cfg.dropbox / rel_path`, and
  `Config.doc_relpath(path)` → POSIX-relative (None when outside the dropbox).
- `db.get_document_by_relpath(conn, rel)` becomes the scan identity, replacing
  `get_document_by_path` (19 call sites; keep the old function while `path`
  exists).
- `ingest_file()`: compute `rel` once, match on it, store `rel_path` + `path`.
- `pha mv`: rewrite `rel_path`, `dir_path`, `filename` and `path` consistently.
- `pha page` / `cite` / `open` / `serve` / MCP: resolve through `doc_path()`,
  never through a stored absolute path.
- hand-off/bundle: use the same POSIX `rel_path` instead of
  `str(unit.relative_to(cfg.dropbox))` (which carries the OS separator, so the
  value differs on Windows).
- notes: `notes.path` → `name` — notes are top-level `*.md` under
  `<archive>/notes/` and the indexer deliberately does not recurse.
- bibliography: derive `sidecar_path` from the document's `rel_path` (it is
  re-parsed live anyway; a cached path is optional).

### 13.5 Edge cases and open questions

- **Documents outside the dropbox.** `ingest_file()` already has an
  `except ValueError: rel_dir = ""` for a path outside the dropbox. Phase 2 must
  decide: refuse at ingest (the scan and `pha upload` only ever produce paths
  under the dropbox), or keep an explicit absolute fallback for that row.
  Recommend refuse, with a message naming the path.
- **`rel_path` vs the `dir_path` + `filename` composite.** A single normalised
  column matches hand-off's `relpath` and gives a simple unique index; the
  composite avoids a new column but makes every lookup two-column. Recommend the
  single column.
- **Keep or drop `path`.** Recommend keep as a cache (it helps the 41 call sites
  and debugging); dropping it is a later decision.
- **Downgrade window.** Old pha → new DB: fine (an extra column is ignored).
  New pha → row written by old pha after the migration: `rel_path` NULL →
  fallback to `path` → backfilled on the next write. Document both rules.
- **Windows separators.** Store POSIX; resolve through `pathlib`, which accepts
  `/` everywhere.
- **Migration cost.** One pass over `documents` (not pages); seconds on archives
  of thousands of documents — pages, chunks and renders are untouched.
- **Schema version marker.** There is no `user_version`; the column's presence
  is the marker, so the backfill must stay idempotent (`WHERE rel_path IS
  NULL`).

### 13.6 Acceptance criteria

1. Copy + `set archive-dir` + `status`: no "new" documents and **no
   `archive-relocate` needed** — the Phase 1 happy path without the repair.
2. A legacy DB (rows with `path` only) migrates: every under-dropbox row gets
   `rel_path`, the unique index exists, and a scan matches on it.
3. A row written by an old pha after the migration (`rel_path` NULL) is
   recognised through `path` and gets `rel_path` on its next write.
4. `pha mv` updates `rel_path`, `dir_path`, `filename` and `path` consistently.
5. `pha page` / `cite` / `serve` / `open` work after the archive is copied, with
   no rebase.
6. A document outside the dropbox is refused (or handled by the chosen
   fallback) with a message naming the path.
7. Notes and bibliography work after a move (paths derived, nothing stale).
8. Windows: a DB created there and opened on macOS resolves the same `rel_path`
   (POSIX stored, `/` matched).

### 13.7 Order of work

- **2a** — schema, backfill, write both columns, read `rel_path` first with the
  `path` fallback. No command output changes; safe to ship on its own.
- **2b** — switch the scan identity and `pha mv` to `rel_path`.
- **2c** (optional) — retire the cached `path` and the §6 warn-and-hold
  detection, which becomes unnecessary for moves (keep a diagnostic in
  `pha doctor` for stale databases).

## 14. Related

- `pha-archive-pointer-loss-bug-report.md` — the pointer side of "where is the
  archive"; this request is the data side of moving it.
- `pha-handoff-enhancement-request.md` / `MULTI_COMPUTER.md` — the other two
  shapes of using more than one machine (they do not move the archive).
- `pha-library-slug-timezone-bug-report.md` — a neighbouring class of bug:
  a stored value recomputed instead of stored; here the value is stored, and
  the question is how to update it when the root changes.
- `ARCHITECTURE.md` §1 — where the archive-resolution rules live; it should
  document the three absolute-path columns (§2) and, after Phase 2, that
  `rel_path` is the identity and `path` only a cache.
- `db.migrate()` — the column-addition pattern Phase 2a builds on (it needs the
  dropbox root passed in for the backfill, see §13.3).
