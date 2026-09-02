"""`pha init-archive <path>` — create a new, self-contained pha archive.

Creates the default structure at `<path>`:
  dropbox/documents/  dropbox/collections/
  library/  renders/
  palaeographers/  editors/  encoders/   (seeded with zero-config defaults)
plus a README.md + AGENTS.md (the first files an agent reads: they explain
what this dir is and how to install `pha` and point it at this archive) and a
.gitignore (keeps user-facing data, excludes the DB, renders and temp files).

If `<path>` does not exist it is created. If it exists it must be empty,
otherwise init fails (never touches an existing archive).
"""
from __future__ import annotations

from pathlib import Path

from .config import (
    _DEFAULT_ED, _DEFAULT_ENC, _DEFAULT_PAL, _ED_SAMPLE, _ENC_SAMPLE,
    _PAL_SAMPLE, _seed_default, _seed_sample,
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
| `palaeographers/`, `editors/`, `encoders/` | model/prompt definitions (one file each; `_sample.md` = template) |
| `library/` | generated per-page transcriptions and edited text — the human review surface |
| `renders/`, `archive.db` | generated cache and index (do not edit) |

## Everyday commands

```bash
pha status                       # what's ingested, pending corrections
pha scan                         # extract + index new/changed dropbox files
pha search "query"               # search the extracted text
pha review [--doc N]             # import human corrections from library/
pha reindex                      # rebuild the index
pha help                         # full command list
pha help agents                  # agent conventions
```

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
- `renders/`, `archive.db` — generated cache and index (do not edit).

## How an agent should operate

- Never edit `renders/`, `archive.db` or other generated files directly.
- `pha status` reports progress and pending review corrections.
- Editing a page file under `library/` is a human correction; run
  `pha review` to import it, then `pha reindex`.
- Only ONE local-model job at a time; check `pha status` before starting a
  scan/edit on this machine.
- Full usage: see the pha README (in the source repository linked above, or
  the README.md in this directory).
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

# NOTE: dropbox/, library/, palaeographers/, editors/, encoders/ are kept
# (they are the user-facing documents, transcriptions and definitions).
"""


def init_archive(path: str | Path) -> Path:
    """Create the default archive structure at `path`. Raises FileExistsError
    if the directory exists and is not empty."""
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
    (p / "library").mkdir(exist_ok=True)
    (p / "renders").mkdir(exist_ok=True)
    pal = p / "palaeographers"
    ed = p / "editors"
    enc = p / "encoders"
    pal.mkdir(exist_ok=True)
    ed.mkdir(exist_ok=True)
    enc.mkdir(exist_ok=True)

    # seed the _sample.md templates (the "how to create new" starting points)
    _seed_sample(pal, "_sample.md", _PAL_SAMPLE)
    _seed_sample(ed, "_sample.md", _ED_SAMPLE)
    _seed_sample(enc, "_sample.md", _ENC_SAMPLE)
    # seed zero-config defaults so the archive works immediately
    _seed_default(pal, _DEFAULT_PAL)
    _seed_default(ed, _DEFAULT_ED)
    _seed_default(enc, _DEFAULT_ENC)

    # agent guidance + git hygiene
    (p / "README.md").write_text(ARCHIVE_README_MD, encoding="utf-8")
    (p / "AGENTS.md").write_text(ARCHIVE_AGENTS_MD, encoding="utf-8")
    (p / ".gitignore").write_text(ARCHIVE_GITIGNORE, encoding="utf-8")
    return p
