"""`pha init-archive <path>` — create a new, self-contained pha archive.

Creates the default structure at `<path>`:
  dropbox/documents/  dropbox/collections/
  library/  renders/
  palaeographers/  editors/  encoders/   (seeded with zero-config defaults)
plus an AGENTS.md (tells agents this dir is managed by pha) and a .gitignore
(keeps user-facing data, excludes the DB, renders and temp files).

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

ARCHIVE_AGENTS_MD = f"""# This directory is a pha archive

This directory is an **archive** managed by **personal-historical-archive
(pha)** — a local tool that transcribes historical documents with a vision
model, edits the transcriptions with a text model, and indexes them for
search.

- Project / source code: {PHA_GITHUB}
- The archive layout and the pipeline are documented there.

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
- Full usage: see the pha README (in the source repository linked above).
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
    (p / "AGENTS.md").write_text(ARCHIVE_AGENTS_MD, encoding="utf-8")
    (p / ".gitignore").write_text(ARCHIVE_GITIGNORE, encoding="utf-8")
    return p
