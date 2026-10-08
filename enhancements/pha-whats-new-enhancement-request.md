# Enhancement request - "what's new" release notes for each bump

**Status:** draft for discussion - not implemented.
**Author/date:** archive work session, 2026-10-08.
**Related:** RELEASING.md (release history), scripts/bump_release.py, `pha update`,
`pha version`, `pha help`, source-checkout vs uv-tool installs.

## 1. Motivation

Every pha release now changes user-visible behaviour, but an installed machine
has no easy way to learn WHAT changed. `pha update` installs the new version;
it does not explain it. The release history in RELEASING.md is written for the
repository, not for the installed operator or agent.

Two concrete audiences:

- a historian who runs `pha update` and wants to know whether anything they
  rely on changed;
- an agent that must explain a new command or behaviour without reading the
  whole repository history.

Git commits already contain most of the raw information, but relying on them
directly does not work for a normal installation: a uv-tool or wheel install
has no `.git` directory. The release notes must therefore be generated at
release time and shipped with the package.

## 2. Goals

1. `pha whatsnew` shows the changes in the installed version.
2. `pha whatsnew --since VERSION` shows everything between that version and
   the installed one.
3. `pha whatsnew VERSION` shows one release's notes.
4. `pha whatsnew --json` is machine-readable for agents.
5. `pha update` points at the new notes (or prints them) after an update.
6. The notes ship in the wheel, so an installed machine never needs the git
   repository or network access.
7. A source checkout can optionally fall back to `git log`, but that is a
   development convenience, not the primary path.
8. Generation is automated from commits, with a human-editable layer for the
   release summary.

## 3. Non-goals

- Replacing RELEASING.md's human-written release history; the generated notes
  are the installed-machine view.
- Adding a full release-management or git-tagging system in the first phase.
- Network calls during `pha whatsnew`.
- Fetching or reading GitHub releases at runtime.
- Turning every commit body into a polished essay.

## 4. Proposed design

### 4.1 Packaged changelog

Generate a file at release time:

~~~text
src/personal_historical_archive/CHANGELOG.json
~~~

Shape:

~~~json
{
  "schema": 1,
  "releases": {
    "0.42.0": {
      "date": "2026-10-08",
      "summary": "Per-document encoder structure registers",
      "changes": [
        {"type": "feat", "scope": "encode",
         "text": "pages: @structure resolves a per-document register",
         "commit": "a5bed70"},
        {"type": "fix", "scope": "encode",
         "text": "missing register refuses and suggests the prescan",
         "commit": "..."}
      ]
    }
  }
}
~~~

The file is shipped like PIPELINE.md and the JSON schema: add it to
`tool.hatch.build.targets.wheel.force-include`.

### 4.2 `pha whatsnew`

Add a command:

~~~text
pha whatsnew [VERSION] [--since VERSION] [--json] [--all]
~~~

Behaviour:

- no arguments: notes for the installed version;
- `VERSION`: notes for that exact release;
- `--since VERSION`: all releases newer than that version, oldest first;
- `--all`: every packaged release, newest first;
- `--json`: structured output;
- missing/older changelog entry: say so plainly and exit 0 (unless a named
  version or `--since` was requested and cannot be found, in which case use a
  clear non-zero error);
- output is plain language, grouped by type.

### 4.3 Generation

Add `scripts/generate_changelog.py` or extend `scripts/bump_release.py` with a
generation step.

Inputs:

- a version string (the version being released);
- a git range (`since` ref or the previous release boundary);
- an optional human summary.

For each commit in the range:

- read subject and body;
- parse a conventional subject when present,
  `type(scope): description`;
- otherwise use the subject as the change text under `other`;
- ignore merge commits and release-bump commits;
- preserve the short commit hash for traceability;
- keep body text only when it adds information that is not in the subject.

The first useful version can be:

~~~bash
python scripts/generate_changelog.py --version 0.42.0 --since 0.41.1
~~~

and update `CHANGELOG.json` in place.

### 4.4 Human summary and overrides

The generated entries are a draft. A release can carry one short summary
written by the archive owner or agent:

~~~json
"summary": "Encoders can resolve page ranges from a per-document register."
~~~

For the first phase, the summary can be passed to the generator. A later phase
can read it from RELEASING.md or a small `releases/<version>.md` source file.

### 4.5 Update integration

`pha update`:

- remembers the installed version before updating;
- after the update, runs the new code's `pha whatsnew --since OLD`;
- if that fails for any reason, the update itself must not fail; print a hint
  to run `pha whatsnew` manually.

A `--no-notes` flag suppresses the notes for scripted updates.

### 4.6 Source-checkout fallback

When running from a source checkout and `CHANGELOG.json` has no entry for the
built version, `pha whatsnew` may fall back to a `git log` over the same
generation logic. This keeps development useful without weakening the packaged
path for installed machines.

## 5. Tests to add

- `CHANGELOG.json` is valid JSON, schema 1, and contains the current version.
- `pha whatsnew` prints the installed version's summary and grouped changes.
- `pha whatsnew VERSION` selects one release; an unknown version is a clear
  error.
- `pha whatsnew --since VERSION` includes exactly the releases in the range.
- `pha whatsnew --json` is stable and machine-readable.
- A wheel built from a tree with an updated changelog contains
  `personal_historical_archive/CHANGELOG.json`.
- `pha update --no-notes` does not print notes, and a notes failure does not
  make the update fail.
- A source checkout with no packaged entry falls back to git (development
  test), while an installed wheel never expects a repository.

## 6. Execution plan

**Phase 1 - packaged notes and the read-only command**

1. Add `CHANGELOG.json` under the package and force-include it in the wheel.
2. Add a small `release_notes.py` reader (schema validation, version
   selection).
3. Add `pha whatsnew` with `VERSION`, `--since`, `--json` and `--all`.
4. Add tests for the reader and CLI output.
5. Document the command in README and `pha help`.

**Phase 2 - generation**

1. Add a commit-to-changelog generator using conventional subjects.
2. Let `bump_release.py` write the new entry with a summary.
3. Define the release boundary (decision below).
4. Add a test fixture repository for generation and grouping.

**Phase 3 - update integration**

1. Teach `pha update` to capture the old version and print new notes.
2. Add `--no-notes`.
3. Ensure update failure paths never depend on notes.
4. Test a simulated update with an injected changelog.

**Phase 4 - curation (optional)**

1. Allow a human summary/notes source next to the release.
2. Merge generated bullets with the curated summary at build time.
3. Keep RELEASING.md as the long-form history.

## 7. Decisions needed before implementation

1. **Release boundary.** What defines "the previous release"?
   - a git tag per release (cleanest, but tags are not currently used);
   - the previous commit whose subject starts with `release:`;
   - an explicit `--since` passed to the generator.
   Recommendation: phase 1 uses explicit `--since`; add tags later.
2. **Command name.** `pha whatsnew`, `pha news`, or `pha changes`.
   Recommendation: `whatsnew` (matches the user-facing idea) with `--since`.
3. **Automatic vs curated summaries.** Recommendation: generate bullets from
   commits and require/pass one human summary line per release.
4. **Wheel inclusion.** Recommendation: force-include the package copy, like
   PIPELINE.md.
5. **Update output.** Recommendation: print a concise summary after a
   successful update, not the whole changelog.

## 8. Reference implementation notes

- `scripts/bump_release.py` - already writes the three version files; the
  generator can live beside it or be called from it.
- `src/personal_historical_archive/update.py` - `pha update` flow; capture the
  old version before the update and run `whatsnew` after.
- `src/personal_historical_archive/cli.py` - add the command and the
  documentation pointer.
- `pyproject.toml` - add the wheel force-include.
- `archive_init.py` / packaged resources - follow the PIPELINE.md pattern for
  reading a packaged file.
- `tests/` - CLI and JSON reader tests, plus the wheel-content gate.

## 9. Approval requested

Please decide:

- Phase 1 only (packaged notes plus read-only command), or Phases 1-3?
- command name;
- release boundary (`--since`, tags, or `release:` commits);
- whether to ship one short human summary required per release.

No implementation will start until this request is approved.
