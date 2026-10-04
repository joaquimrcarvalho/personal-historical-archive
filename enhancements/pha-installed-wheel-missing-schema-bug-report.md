# Bug report -- a wheel-installed pha cannot find schema/pha-sidecar.schema.json, so every sidecar-resolving command dies

**Status:** **FIXED** (F1/F4/F5 in sec. 10, F2 in sec. 11). **Found:** 2026-10-04, `jesuit-archive` machine, pha 0.36.0
(uv-tool install built from the checkout). **Impact:** every pha command that
resolves a `pha.yaml` sidecar (`scan`, `edit`, `test`, `palaeographer`,
`editor`, `handoff`, `bundle`, `render`, ...) exits 1 with a traceback and does
nothing, on any machine where `personal_historical_archive` is imported from an
installed distribution. On this machine it silently consumed a night of the
`collections/epistolae-mixtae` pipeline (see sec. 2.4).

## 1. Summary

`sidecar._schema()` locates the JSON schema by walking up from its own module:

    path = Path(__file__).resolve().parents[2] / "schema" / "pha-sidecar.schema.json"

That arithmetic holds only in a source checkout
(`<repo>/src/personal_historical_archive/sidecar.py` -> `<repo>/schema/...`).
In an installed wheel the module sits in
`.../site-packages/personal_historical_archive/`, so `parents[2]` is the
interpreter's `lib/python3.13/`, which has no `schema/`. The wheel does not ship
the file either: `pyproject.toml` packages only
`src/personal_historical_archive`, while `schema/` is a repo-root sibling of
`src/`. `uv tool install --reinstall` therefore reproduces the broken install
exactly.

## 2. Evidence

### 2.1 The traceback (any sidecar path)

`pha palaeographer <doc>` and `pha scan --path <doc>` end in:

    File ".../personal_historical_archive/sidecar.py", line 28, in _schema
      _SCHEMA = json.loads(path.read_text(encoding="utf-8"))
    FileNotFoundError: [Errno 2] No such file or directory:
      '/Users/jrc/.local/share/uv/tools/personal-historical-archive/lib/python3.13/schema/pha-sidecar.schema.json'

The traceback is identical whether the command comes from scanning, editing,
the palaeographer/editor inspectors, the test harness or a hand-over: they all
reach `resolve_sidecar()` -> `load_sidecar()` -> `_schema()` before doing work.

### 2.2 The file exists only in the checkout

    $ ls <tool>/lib/python3.13/schema/            -> No such file or directory
    $ find <tool> -name pha-sidecar.schema.json   -> (nothing)
    $ ls <repo>/schema/pha-sidecar.schema.json    -> present, 5833 bytes

### 2.3 Why a reinstall cannot fix it

    [tool.hatch.build.targets.wheel]
    packages = ["src/personal_historical_archive"]

Only that subtree enters the wheel; `schema/` is a repo-root sibling of `src/`.
There is no `include` / `force-include` / package-data entry for it, so each
`uv tool install --reinstall` builds the same schema-less artifact. After the
2026-10-03 23:47 reinstall the installed `sidecar.py` was unchanged and the
schema directory was still absent.

### 2.4 The incident (2026-10-03/04, collections/epistolae-mixtae)

- 23:47 -- a `uv tool install --reinstall` replaced site-packages but not the
  schema.
- 03:47-03:50 -- a supervisor chained `pha scan` / `pha edit` for the
  collection. Every call died in about 1 s with the traceback above. Three scan
  attempts each reported `unfinished=1`; Tomus II-V were never scanned.
- The queued Tomus I corrective edit (36 pages, thinking on) waited 4.0 h for a
  model lock and then gave up: `edited 0 document(s)`.
- The same commands succeed when the checkout is importable
  (`PYTHONPATH=<repo>/src`). That is how the MCP job runner and the one working
  scan (`pha-5`) were running, so the defect only surfaced on a fresh CLI
  invocation.

### 2.5 Which commands are hit

`resolve_sidecar()` / `_doc_sidecar()` callers in the source:

- `cli.py` -- `palaeographer`, `editor`
- `ingest.py` -- `scan`, `edit`, `encode`, `render`, and the lock-key
  computation for every model job (`_servers_for_document`)
- `testrun.py` -- `test`
- `handoff.py`, `bundle.py` -- `handoff`, `bundle`

Commands that only read the DB (`status`, `search`, `page`) do not call it, so
the failure looks intermittent and command-specific.

## 3. Root cause

Two coupled assumptions:

1. `__file__`-relative upward walking (`parents[2]`) is used to locate a
   *shipped* resource. It holds for editable/source runs only, and is invalid
   for any built distribution (wheel, pipx, uv tool, zipapp).
2. The build does not include the resource in the distribution at all, so even
   correct path arithmetic would find nothing.

`config.find_project_root()` carries the same `parents[2]` fallback, and
`update.py` uses it for self-update (legitimately checkout-only, but it should
say so rather than compute a path under `lib/python3.13`). Any other runtime
asset read from the repo root (`prompts/`, bundled `skills/`, sample defaults,
the notes README template) is exposed to the same class of failure.

## 4. Proposed fixes

### F1 -- ship the schema inside the package and load it as a resource

    git mv schema/pha-sidecar.schema.json \
           src/personal_historical_archive/schema/pha-sidecar.schema.json

    from importlib.resources import files

    def _schema() -> dict:
        global _SCHEMA
        if _SCHEMA is None:
            _SCHEMA = json.loads(
                files("personal_historical_archive")
                .joinpath("schema/pha-sidecar.schema.json")
                .read_text(encoding="utf-8"))
        return _SCHEMA

Hatchling includes files under the package directory by default, so the wheel,
sdist, editable install and any zipapp carry it. This alone makes a clean
`uv tool install` work on any machine with no env vars and no post-install copy.

### F2 -- stop using `parents[2]` for shipped data

- `sidecar._schema()`: as F1.
- `config.find_project_root()`: keep `PHA_HOME` / `config.yaml` discovery, but
  do not fall back into the install tree to find defaults; resolve immutable
  defaults via `importlib.resources` and user-editable assets via the
  configured project/archive root.
- `update.py`: keep it checkout-only, but raise a clear "not a source checkout"
  error instead of computing `<env>/lib/python3.13`.

### F3 -- audit the other repo-root runtime assets

`prompts/`, `models/`, `palaeographers/`, `editors/`, `encoders/`, `filters/`,
`notes/README.md`, `skills/`. Split them into shipped defaults (package data)
and archive/user assets (seeded by `pha init-archive`, already partly done via
`_seed_default`).

### F4 -- CI gate

Build the wheel, install it into a throwaway venv, and assert the schema loads:

    uv build
    python -m venv /tmp/v && /tmp/v/bin/pip install dist/*.whl
    /tmp/v/bin/python -c "from personal_historical_archive.sidecar import _schema; print(_schema()['title'])"

Today that import raises `FileNotFoundError` from the installed wheel. Also
check the artifact directly: `unzip -l dist/*.whl | grep pha-sidecar`.

### F5 -- defensive override

Optional search order in `_schema()`: `PHA_SCHEMA` env -> packaged resource ->
`<project_root>/schema` (developer override) -> fail with a message naming all
three. Not a substitute for F1.

## 5. Test plan

- Unit: the `importlib.resources` `_schema()` loads from source and from a wheel.
- Packaging: the CI gate in F4 plus `unzip -l`.
- End-to-end: `uv tool install --reinstall` the built wheel on a clean machine,
  then `pha palaeographer <fixture>` and `pha scan --path <fixture>`.

## 6. Workarounds (until F1 lands)

- `PYTHONPATH=<repo>/src` -- imports the checkout (also the MCP runner's
  de-facto behaviour).
- Copy the file to the path the code computes:
  `<tool>/lib/python3.13/schema/pha-sidecar.schema.json` -- must be redone
  after every reinstall.
- Editable install (`uv tool install -e <repo>`).

## 7. Relation to other reports

- `pha-orphaned-model-lock-wedges-every-job-bug-report.md` -- during the same
  incident, two locks orphaned by a killed `pha-5` (pid 34989) were
  unreclaimable because `ps` is blocked in the agent sandbox; the workaround
  was `PHA_LOCK_DIR`. That is F1/F4 territory there, not this report.
- `pha-latin-not-translated-thinking-disabled-bug-report.md` -- the corrective
  pass this defect silently blocked.

## 8. Method note

Observed directly: the tracebacks in `.lq-qa/pipeline-supervisor.out` (archive),
`ls`/`find` of the installed tool, `pyproject.toml`, and the reinstall mtimes.
The `importlib.resources` fix was exercised in the source tree (via
`PYTHONPATH`) but not yet built or installed.

## 9. Addendum -- the 2026-10-04 reinstall

An explicit `uv tool install --reinstall /Users/jrc/develop/personal-historical-archive`
run on 2026-10-04 left the tool in the same state: `<tool>/lib/python3.13/schema/`
was still absent, and `/Users/jrc/.local/bin/pha palaeographer <doc>` still
raised the same `FileNotFoundError`. Reinstallation is not the fix; F1 is.

## 10. Fix (2026-10-04)

Fixed on branch `fix/ship-sidecar-schema`.

- **F1** -- `sidecar._schema()` no longer counts parents from `__file__`. It
  reads the schema from, in order: `PHA_SCHEMA` (the F5 override) -> the copy
  shipped inside the package via `importlib.resources` -> `<checkout>/schema/`
  located by walking up for `pyproject.toml` (source and editable runs).
- **The canonical file stays at the repo root.** `pyproject.toml` force-includes
  it into the wheel at `personal_historical_archive/schema/`, so the schema's
  published `$id`, the `.vscode` mapping and the `yaml-language-server`
  module-line that `migrate._SCHEMA_MODELINE` bakes into `pha.yaml` all keep
  working. Nothing moves, so no archive is disturbed; a comment-only `pha.yaml`
  edit does not trigger reprocessing anyway (staleness keys off resolved config
  values, not the file's bytes or mtime).
- **F4 gate** -- `scripts/check_wheel_ships_schema.py` builds a wheel, asserts
  the member is present, and imports `_schema()` from the unpacked wheel outside
  the checkout. `tests/test_sidecar.py` guards the force-include config plus the
  env-override and clear-error paths.

Verified: the `uv build` wheel contains
`personal_historical_archive/schema/pha-sidecar.schema.json`; a clean venv
install loads `_schema()` and validates a `pha.yaml`; source/editable and
zipimport paths also load; full test suite 890 passed.

F2 was still open when this landed; it is fixed separately in sec. 11. F3 (audit
of the other repo-root runtime assets) remains open.

## 11. F2 fix (2026-10-04)

Branch `fix/project-root-resolution` removes the last two `parents[N]` sites.

- New `config.source_checkout_root()` walks up from `__file__` for
  `pyproject.toml` and STOPS at a `site-packages` / `dist-packages` boundary, so
  a wheel installed into a venv that lives inside a checkout (the `repo/.venv`
  layout) is not mistaken for that checkout.
- `config.find_project_root()` only accepts a real checkout that also holds
  `config.yaml`; it no longer returns `<env>/lib/python3.x` when a `config.yaml`
  happens to sit there.
- `update.project_root()` raises a clear `UpdateError` when there is no
  checkout, and `install_update()` reinstalls from the repository in that case
  (a built distribution has no checkout to fast-forward).

Regression tests fail against the previous code (confirmed by stashing only the
source change). Full suite: 898 passed. Remaining: F3 (the other repo-root
runtime assets: `prompts/`, `models/`, `palaeographers/`, `editors/`,
`encoders/`, `filters/`, `notes/README.md`, `skills/`).
