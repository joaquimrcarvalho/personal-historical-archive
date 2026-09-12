"""`pha init-archive <path>` — create a new, self-contained pha archive.

Creates the default structure at `<path>`:
  dropbox/documents/  dropbox/collections/
  library/  renders/  notes/
  palaeographers/  editors/  encoders/   (seeded with zero-config defaults)
plus a README.md + AGENTS.md (the first files an agent reads: they explain
what this dir is and how to install `pha` and point it at this archive) and a
.gitignore (keeps user-facing data, excludes the DB, renders and temp files).

If `<path>` does not exist it is created. If it exists it must be empty,
otherwise init fails (never touches an existing archive).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .config import (
    _DEFAULT_ED, _DEFAULT_ENC, _DEFAULT_PAL, builtin_samples,
    find_project_root, notes_readme_template, _seed_default,
    _seed_sample,
)

PHA_GITHUB = "https://github.com/joaquimrcarvalho/personal-historical-archive"

ARCHIVE_README_MD = f"""# pha archive

This directory is an **archive** managed by
[personal-historical-archive (pha)]({PHA_GITHUB}) — a local tool that
transcribes historical documents with a vision model, edits the
transcriptions with a text model, and indexes them for full-text search.

- Source code & full manual: {PHA_GITHUB}
- This file and `AGENTS.md` are the first things an AI agent should read
  before operating on this archive.

## Quick start for agents (and humans)

`pha` is the only tool that reads and writes this archive. If it is not
already installed on this machine, install it and point it at this directory
before doing anything else.

### 1. Check whether `pha` is available

```bash
command -v pha
```

If that prints a path, skip to step 4.

### 2. Get the tool

```bash
git clone {PHA_GITHUB}.git
cd personal-historical-archive
```

If you don't have `uv` (the easiest installer), get it first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 3. Install `pha` as a global command

```bash
uv tool install --editable .
pha --help
```

This makes plain `pha` available in every shell, with no venv activation
needed. Alternatively you may skip the global install and call it by full
path from the checkout, e.g. `personal-historical-archive/.venv/bin/pha`.

### 4. Point `pha` at THIS archive

```bash
pha set archive-dir "<path-to-this-directory>"
```

or equivalently set the environment variable (which takes precedence):

```bash
export PHA_ARCHIVE_DIR="<path-to-this-directory>"
```

If `pha` ever reports "No pha archive is configured or found", this step is
missing (or the path is wrong): run `pha set archive-dir` with this directory.

> Do NOT run `pha` against the project checkout's default archive — this
> directory is the data root; the checkout directory is only where the code
> lives.

## Layout

| Path | What it is |
| --- | --- |
| `dropbox/documents/` | individual source documents |
| `dropbox/collections/COLX/` | collections of documents (per-collection model selections & prompts can sit beside them) |
| `inbox/` | documents parked ON HOLD — never scanned; `pha status` reports them, `pha inbox --move` puts them in the dropbox |
| `palaeographers/`, `editors/`, `encoders/` | model/prompt definitions (one file each; `_sample.md` = template) |
| `library/` | generated per-page transcriptions and edited text — the human review surface |
| `notes/` | Obsidian-compatible markdown notes generated from queries to this archive (see `notes/README.md`) |
| `renders/`, `archive.db` | generated cache and index (do not edit) |

## Everyday commands

```bash
pha status                       # what's ingested, pending corrections, on hold
pha inbox [--move]               # list / move documents parked in the inbox
pha scan                         # extract + index new/changed dropbox files
pha search "query"               # search the extracted text
pha review [--doc N]             # import human corrections from library/
pha reindex [--path collections/COLX]  # rebuild the index (or one collection/doc)
pha help                         # full command list
pha help agents                  # agent conventions
```

## Re-running one document

`pha scan` skips documents that are already transcribed and unchanged, so to
work on a document you've already processed you must target it and force it:

```bash
pha status                       # find a document's collection / dropbox path
pha scan --path collections/COLX                 # re-scan one collection or doc
pha scan --path collections/COLX --reprocess     # re-extract pages already done
pha edit --path collections/COLX --page 3        # re-run the editor on one page
pha reindex --path collections/COLX               # re-embed one collection or doc
pha test collections/COLX --pages 3              # dry-run a config on a sample
pha page <doc> <page>                            # read one page's full text
```

A document is a dropbox-relative subpath: `collections/COLX`, `documents/`
(individual files), or a directory-of-images document (`documents/ms123`).
`pha scan`/`pha edit`/`pha test` share the single-model lock — run one at a time.

## Operating discipline

- **Never edit `renders/`, `archive.db`, or other generated files directly.**
- Editing a page file under `library/` is a human correction: run `pha review`
  to import it, then `pha reindex`.
- **Only ONE local-model job at a time** — `pha scan` and `pha edit` share a
  lock; running two local models at once fills the disk and hangs the machine.
  Quit LM Studio when not ingesting.
- Full usage and troubleshooting: see the pha README in the source repository
  linked at the top.
"""

ARCHIVE_AGENTS_MD = f"""# This directory is a pha archive

This directory is an **archive** managed by **personal-historical-archive
(pha)** — a local tool that transcribes historical documents with a vision
model, edits the transcriptions with a text model, and indexes them for
search.

- Project / source code: {PHA_GITHUB}
- The archive layout and the pipeline are documented there (and in this
  directory's README.md).

## Before anything else: make sure `pha` is available and pointed here

`pha` is the tool that reads and writes this archive. If it is not installed
(or not on PATH) on this machine, install it first:

1. Check whether it is already available: `command -v pha`. If that prints a
   path, skip to step 4.
2. Get the tool:
   ```bash
   git clone {PHA_GITHUB}.git
   cd personal-historical-archive
   ```
   (`uv` is the easiest installer: `curl -LsSf https://astral.sh/uv/install.sh | sh`.)
3. Install it as a global command so it works in every shell:
   ```bash
   uv tool install --editable .
   pha --help
   ```
   (Alternatively call it by the venv path,
   `personal-historical-archive/.venv/bin/pha`, without installing globally.)
4. Point `pha` at THIS archive directory (this exact folder):
   ```bash
   pha set archive-dir "<this-directory>"
   ```
   or set the environment variable `PHA_ARCHIVE_DIR="<this-directory>"`.

If `pha` reports "No pha archive is configured or found", step 4 is still
needed: run `pha set archive-dir <path>` with this directory. Do not run
against the project checkout's default archive — this directory is the data
root.

## What lives here

- `dropbox/` — your source documents (drop them here; `documents/` for
  individual files, `collections/COLX/` for collections). Per-collection
  model selections and prompts can sit next to the documents.
- `palaeographers/`, `editors/`, `encoders/` — model/prompt definitions.
- `library/` — generated per-page transcriptions and edited text (the
  human-readable review surface).
- `notes/` — Obsidian-compatible markdown notes generated from queries to this
  archive (see `notes/README.md`).
- `renders/`, `archive.db` — generated cache and index (do not edit).

## How an agent should operate

- Never edit `renders/`, `archive.db` or other generated files directly.
- `pha status` reports progress and pending review corrections.
- Editing a page file under `library/` is a human correction; run
  `pha review` to import it, then `pha reindex`.
- Only ONE local-model job at a time; check `pha status` before starting a
  scan/edit on this machine.
- To change how a collection/document is processed (its palaeographer /
  editor / encoders / prompt), inspect what is already set first:
  `pha palaeographer <file>`, `pha editor <file>`, `pha prompts <file>`,
  `pha encoder [file]`. A collection selects its models with a `pha.yaml`
  sidecar next to the documents; the legacy plain-text `palaeographer` /
  `editor` files are the fallback.
- Full usage: see the pha README (in the source repository linked above, or
  the README.md in this directory).

## Re-running / re-scanning a specific document

`pha scan` only processes NEW or CHANGED files: an already-transcribed
document whose source is unchanged is skipped as `unchanged`. To make pha
work on a document you have already processed, target it and force it.

- Find the path: `pha status` shows the collection tree. A document is a
  dropbox-relative subpath — a collection (`collections/COLX`), a
  directory-of-images document (`documents/ms123`), or a single file
  (`documents/myfile.pdf`).
- Rescan one collection / document (extracts + indexes its files):
  ```bash
  pha scan --path collections/COLX
  ```
- Force it to re-extract pages that are already done (without `--reprocess`
  an unchanged document is skipped as `unchanged`):
  ```bash
  pha scan --path collections/COLX --reprocess
  ```
- Re-run only the editor pass on ONE page of one document:
  ```bash
  pha edit --path collections/COLX --page 3
  ```
- Try a configuration on a sample before a full pass:
  ```bash
  pha test collections/COLX --pages 3
  ```
- Read one page's full text to verify a re-scan:
  ```bash
  pha page <document-id-or-substring> <page>
  ```

The same single-local-model rule applies: `pha scan`, `pha edit` and
`pha test` share the lock, so never start one while another is running on
this machine. After a re-scan confirm with `pha status` (and `pha page` for
the page text).
"""

ARCHIVE_GITIGNORE = """# pha archive — keep user-facing data, exclude generated/temp files

# generated index + cache (regenerate with pha scan / pha reindex)
archive.db
archive.db-*
renders/

# scan lock / transient
scan.lock
*.tmp
*.~lock*

# OS cruft
.DS_Store

# NOTE: dropbox/, library/, palaeographers/, editors/, encoders/, notes/ are
# kept (they are the user-facing documents, transcriptions, definitions and
# research notes).
"""

# --- archive agent docs: seeding, and refreshing them on pha updates ---------
#
# An archive's README.md and AGENTS.md are pha-generated guidance (the first
# files an agent reads). They are written at init-archive time, but pha can be
# updated later, so we also refresh them into an existing dedicated archive on
# every run (see Config.ensure_dirs). To avoid destroying a user's edits we
# stamp each managed file with a marker that records a hash of its body: a file
# that still carries a matching hash is pristine and is safely refreshed; a file
# whose body no longer matches (or that lost the marker entirely) is treated as
# user-customized and left alone.

_DOC_MARKER = "<!-- pha-docs-template: sha256:"
_DOC_MARKER_END = " -->"

# The archive-facing docs and their canonical opening line. The opening line is
# the fallback used to recognise a LEGACY generated file (written before the
# marker existed) so existing archives still get refreshed once.
_DOC_FILES = (
    ("README.md", "# pha archive"),
    ("AGENTS.md", "# This directory is a pha archive"),
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stamp(body: str, project_root: Path | None = None) -> str:
    """Prepend the template marker (a hash of `body`) to a doc template."""
    # fresh archives seed notes/README.md from the project root template; here we
    # only stamp the two agent-facing docs, which carry no project_root param.
    return f"{_DOC_MARKER}{_sha256(body)}{_DOC_MARKER_END}\n\n{body}"


def _split_marker(text: str) -> tuple[str | None, str | None]:
    """Return (body, recorded_hash) if `text` is a marked pha doc, else (None, None)."""
    if not text.startswith(_DOC_MARKER):
        return None, None
    end = text.find(_DOC_MARKER_END)
    if end == -1:
        return None, None
    recorded = text[len(_DOC_MARKER):end]
    body = text[end + len(_DOC_MARKER_END):].lstrip("\n")
    return body, recorded


def _should_refresh(path: Path, template: str, opening_line: str) -> bool:
    """Should pha (re)write this archive doc with the current template?"""
    if not path.exists():
        return True  # missing -> seed it
    cur = path.read_text(encoding="utf-8")
    if cur == _stamp(template):
        return False  # already the current marked template -> no-op
    body, recorded = _split_marker(cur)
    if body is not None:
        # a managed (marked) file: refresh only when it is pristine
        return _sha256(body) == recorded
    # no marker -> either a legacy generated file or a user replacement. Refresh
    # only when it still begins like the template (legacy generated).
    return cur.lstrip().startswith(opening_line)


def refresh_archive_agent_docs(archive_dir: str | Path) -> list[tuple[str, str]]:
    """Refresh the archive's agent-facing docs (README.md, AGENTS.md) to the
    current pha templates.

    Creates a doc when missing, refreshes a pristine (unmodified) doc, and
    leaves a user-customised doc alone. Never touches notes/README.md (that is
    seeded once and never overwritten). Returns [(filename, action)] where
    action is one of 'created' | 'updated' | 'kept' for callers that want to
    report it.
    """
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, str]] = []
    # Look the templates up by name so a bump in one place takes effect here.
    templates = {
        "README.md": ARCHIVE_README_MD,
        "AGENTS.md": ARCHIVE_AGENTS_MD,
    }
    for name, opening_line in _DOC_FILES:
        template = templates[name]
        path = archive_dir / name
        if not path.exists():
            path.write_text(_stamp(template), encoding="utf-8")
            results.append((name, "created"))
            continue
        if _should_refresh(path, template, opening_line):
            path.write_text(_stamp(template), encoding="utf-8")
            results.append((name, "updated"))
        else:
            results.append((name, "kept"))
    return results


def init_archive(path: str | Path, project_root: Path | None = None) -> Path:
    """Create the default archive structure at `path`. Raises FileExistsError
    if the directory exists and is not empty.

    `project_root` is where the canonical `notes/README.md` lives (defaults to
    the pha project root); new archives seed their `notes/README.md` from it."""
    p = Path(path).expanduser().resolve()
    if p.exists():
        if not p.is_dir():
            raise NotADirectoryError(f"{p} is not a directory")
        entries = [e for e in p.iterdir() if e.name not in (".DS_Store",)]
        if entries:
            raise FileExistsError(
                f"directory {p} is not empty ({len(entries)} entries); "
                f"init only creates a NEW archive"
            )
    else:
        p.mkdir(parents=True, exist_ok=True)

    # user-facing structure
    (p / "dropbox" / "documents").mkdir(parents=True, exist_ok=True)
    (p / "dropbox" / "collections").mkdir(parents=True, exist_ok=True)
    (p / "inbox").mkdir(exist_ok=True)
    (p / "library").mkdir(exist_ok=True)
    (p / "renders").mkdir(exist_ok=True)
    notes = p / "notes"
    notes.mkdir(exist_ok=True)
    (notes / "README.md").write_text(
        notes_readme_template(project_root or find_project_root()), encoding="utf-8"
    )
    pal = p / "palaeographers"
    ed = p / "editors"
    enc = p / "encoders"
    pal.mkdir(exist_ok=True)
    ed.mkdir(exist_ok=True)
    enc.mkdir(exist_ok=True)

    # seed the BUILTIN samples ("how to create new" templates + the
    # ready-to-duplicate catalogue: OCR/parse engines, local LM Studio models,
    # generic printed-book palaeographers and a generic editor). Never loaded
    # as definitions themselves — every name starts with '_'.
    for sub, fname, content in builtin_samples():
        d = p / sub
        d.mkdir(parents=True, exist_ok=True)
        _seed_sample(d, fname, content)
    # seed zero-config defaults so the archive works immediately
    _seed_default(pal, _DEFAULT_PAL)
    _seed_default(ed, _DEFAULT_ED)
    _seed_default(enc, _DEFAULT_ENC)

    # stage filters live in the ARCHIVE (they shape archive data): seed the
    # how-to-write-one template, which `pha filters` points a new user at.
    try:
        from .filters import FILTER_SAMPLE_MD, FILTER_SAMPLE_PY
        sample = p / "filters" / "_sample"
        sample.mkdir(parents=True, exist_ok=True)
        _seed_sample(sample, "filter.md", FILTER_SAMPLE_MD)
        _seed_sample(sample, "filter.py", FILTER_SAMPLE_PY)
    except Exception:  # noqa: BLE001 - a missing template must not break init
        pass

    # agent guidance + git hygiene (stamped so later pha updates can refresh
    # a pristine generated doc without clobbering a user's edits)
    (p / "README.md").write_text(_stamp(ARCHIVE_README_MD), encoding="utf-8")
    (p / "AGENTS.md").write_text(_stamp(ARCHIVE_AGENTS_MD), encoding="utf-8")
    (p / ".gitignore").write_text(ARCHIVE_GITIGNORE, encoding="utf-8")
    return p
