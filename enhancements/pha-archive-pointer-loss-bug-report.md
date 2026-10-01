# Bug report — losing the archive pointer makes `pha` seed definitions into the wrong tree, and the PHA View dies

**Status:** partly fixed — **D3 and D5 closed 2026-09-29**; D1, D2 and D4 open
(see §8). **Found:** 2026-09-21, on the `jesuit-archive` machine, when the
DSH **PHA View** could not open any page: every click answered *"No pha archive
is configured or found."* pha `0.28.0` (installed tool + editable checkout).

## 1. The incident

- The archive pointer lived, as it had for months, in a line of the gitignored
  `.env` at the checkout root:
  `PHA_ARCHIVE_DIR=/Users/jrc/jesuit-archive`.
- That file was found **0 bytes** (mtime 10:13). No pha code writes it, so the
  truncation came from outside pha (an editor, a shell redirect, another tool —
  not identified).
- From then on, every `pha` invocation without the variable in its own
  environment resolved the **pha source checkout** as the archive:
  - `pha info --json` and `pha status` printed *"No pha archive is configured or
    found."* on stderr — the text the view surfaced on every click;
  - a read-only command **wrote into the source tree**: `editors/default.md`,
    `encoders/default.md`, `palaeographers/default.md` (10:34, all untracked in
    git) and touched `config.yaml` (10:27).
- The archive itself was never at risk: 69 documents, 36 025 pages, 118 951
  chunks, every row intact. Only the pointer was lost.

## 2. Why the viewer is especially exposed

`dsh-pha` (the plugin behind the PHA View) discovers the archive by running pha
**with `cwd = '/'`** (`dsh-pha/lib/index.js:59`) — the view needs cwd-independent
resolution, because nothing about `/` says where the data lives.

With the environment variable gone, the only remaining path is
`find_project_root()`'s last resort — the editable install's own tree
(`config.py:164-168`) — which lands on the checkout, whose tracked `config.yaml`
says `paths.archive_dir: .`. A machine-local pointer kept in a *gitignored* file
was therefore the single point of failure for the whole view.

## 3. Defects, sharpest first

- **D1 — a read-only command mutates the filesystem.** `Config.load()` seeds
  `_DEFAULT_MODEL` / `_DEFAULT_PAL` / `_DEFAULT_ED` / `_DEFAULT_ENC` into the
  resolved root **before** anything establishes that the root is an archive
  (`config.py:551-554`). So `pha info --json` — described by the plugin as
  "cheapest and always correct… reads only the config, opens no DB" — created
  three definition files in the source repository. Seeding belongs to a
  *confirmed* archive (`pha init-archive`, or a root that already holds
  `archive.db` / the layout), not to whatever directory resolution happens to
  produce.
- **D2 — silent fallback to "the project root is the archive".** When nothing is
  configured, `archive_dir` defaults to `.` (`config.py:518`). The problem is
  reported only later, by `_prompt_archive_setup` (`cli.py:2857`) — *after* the
  seeding. A non-interactive run should fail fast and name the roots it tried.
- **D3 — `pha info --json` cannot say "unconfigured".** It exposes
  `archive_source` (here `PHA_ARCHIVE_DIR in .env (legacy)`) but a caller doing
  *discovery* treats any `archive_dir` as success. The plugin already has a
  fallback chain and a clear error path; it needs the truth to act on — e.g.
  `"configured": false, "source": "project-root-fallback"`.
- **D4 — neither durable pointer is machine-level.** `pha set archive-dir`
  writes `paths.archive_dir` into the **tracked** `config.yaml`
  (`cli.py:2322-2323`, deliberately: "a tracked, reviewable line rather than a
  gitignored `.env` value"), while the `.env` route is labelled *legacy*. (Note
  the resolution order: the legacy `.env` line is checked BEFORE
  `paths.archive_dir` — the shipped `config.yaml` carries `archive_dir: .` as a
  default, so it cannot be authoritative without moving every pre-existing
  `.env` user's archive — which is exactly why `pha set archive-dir` also
  removes that line. See `Config.load` and AGENTS.md.) Both
  live in the checkout; a repo edit or a `git checkout` can undo one, an
  accident can empty the other. A user-level config (say
  `~/.config/pha/config.yaml`, consulted after the environment and before the
  project fallback) would give a pointer no repository operation can wipe.
- **D5 — an empty value is indistinguishable from an absent one.**
  `_env_setting` returns `None` for an empty value and falls through
  (`config.py:499-508`). A `NAME=` line that exists but is empty is evidence of
  an accident, not a configuration, and deserves a warning.

## 4. Reproduction (measured today)

```bash
cd /Users/jrc/develop/personal-historical-archive
cp .env .env.bak
: > .env                    # simulate the accident
cd / && pha info --json     # → "No pha archive is configured or found."
ls editors/ encoders/ palaeographers/    # → three seeded default*.md files appear
mv .env.bak .env            # the archive resolves again
```

## 5. What would have turned this into a non-event

- **D1 + D2**: fail fast, seed nothing, and print the roots that were tried.
- **D3**: mark the archive unconfigured in `pha info --json`; the plugin's
  `discover()` (`dsh-pha/lib/index.js:120-160`) already knows how to report it.
- **D4**: a machine-level pointer, so that losing a repository file cannot take
  the view down.
- **D5**: warn when a variable exists but is empty.

## 6. Cost

About half an hour in which a historian could not open a single page of the
archive, plus three stray files left in the source tree (an attempt to move them
out of the repository was refused by the sandbox, so they remain in place).

## 7. Workaround, today

Restore the line in the checkout's `.env`:

```
PHA_ARCHIVE_DIR=/Users/jrc/jesuit-archive
```

and verify from the directory the plugin actually uses:

```bash
cd / && pha info --json     # must report archive_dir …/jesuit-archive
```

Running `pha set archive-dir /Users/jrc/jesuit-archive` as well makes the pointer
survive a `.env` accident, at the price of putting a machine-local path into the
tracked `config.yaml`.

## 8. Progress — 2026-09-29 (pha 0.35.0)

**D3 closed.** `pha info --json` now answers instead of refusing:

```json
{
  "archive_dir": "/private/tmp/bare",
  "archive_source": "default (project root)",
  "archive_source_kind": "default",
  "configured": false,
  ...
}
```

- `configured` is the boolean a discovery caller needs, and
  `archive_source_kind` (`env` / `dotenv` / `config` / `default`) is the stable,
  machine-readable twin of the prose `archive_source`, which is unchanged.
- `pha info` is now dispatched **before** the fresh-install guard and before
  `ensure_dirs()` (like `pha version`), so it neither prompts/exits nor creates
  an archive layout in whatever directory resolution fell back to. Verified: on
  a bare root it exits 0 with the JSON above and creates no `library/`,
  `dropbox/`, `data/`, `archive.db` or `pha-location.md`.
- Both facts are **recorded by `Config.load`** (`Config.archive_source_kind`),
  where the precedence is decided. `pha info`, the resolved-archive one-liner
  and `_archive_explicitly_set` used to re-derive the source from the same three
  files independently — which is how an empty `.env` value made them disagree
  with what was actually used.
- **Still to do on the consumer side:** `dsh-pha`'s `discover()`
  (`dsh-pha/lib/index.js:122-162`) still runs with `cwd = '/'` and accepts any
  `archive_dir`. It should treat `configured: false` as "no archive" so the view
  reports that clearly instead of acting on the project-root fallback.

**D5 closed.** An empty value is ignored *and* warned about, in both sources:

```
$ PHA_ARCHIVE_DIR= pha info
warning: PHA_ARCHIVE_DIR is set but EMPTY in the environment — ignoring it. An
empty value is usually an accident (a truncated file or a redirect), and the
fallback may point pha at a different archive.
```

- `_env_setting` was `if v: return v`; the reader now reports **where** a value
  came from and treats whitespace-only as empty too.
- An empty `PHA_ARCHIVE_DIR=` line in `.env` no longer counts as a pointer:
  `_archive_explicitly_set` returns False for it (it previously returned **True**,
  so an emptied line suppressed the fresh-install guard while resolving the
  project-root default), `_dotenv_archive_dir` returns None, and a real
  `paths.archive_dir` still wins the precedence.
- Both directions are covered by 12 tests in `tests/test_archive_pointer.py`.

**D1, D2, D4 remain open** — defaults are still seeded by `Config.load()` into
whatever root resolution produces (so `pha info` on a bare directory still
creates the four `default.md` files, just no longer a whole archive layout), the
project-root fallback is still silent at resolution time, and there is still no
machine-level pointer. D1 is now the most visible remaining defect, and the
`dsh-pha` wiring above is the shortest path to making the original incident
impossible to repeat.
