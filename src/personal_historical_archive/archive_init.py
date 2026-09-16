"""`pha init-archive <path>` — create a new, self-contained pha archive.

Creates the default structure at `<path>`:
  dropbox/documents/  dropbox/collections/
  library/  renders/  notes/
  palaeographers/  editors/  encoders/   (seeded with zero-config defaults)
  skills/                                (the pha-specific agent skills)
plus a README.md + AGENTS.md (the first files an agent reads: they explain
what this dir is and how to install `pha` and point it at this archive) and a
.gitignore (keeps user-facing data, excludes the DB, renders and temp files).

If `<path>` does not exist it is created. If it exists it must be empty,
otherwise init fails (never touches an existing archive).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .archive_skills import seed_archive_skills
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

> [!IMPORTANT]
> **Agents:** the person you are working with here is most likely a
> **historian, not a programmer**. Explain in plain language what you are
> going to do before you do it, and explain plainly what you need — and what
> it will change — before asking for any extra permission on their computer.
> See `AGENTS.md`, section "Who you are talking to".

> [!TIP]
> **Where is `pha` on THIS machine?** → **`pha-location.md`**, next to this
> file. The last pha run wrote it, so it names the exact executable, a
> PATH-proof fallback (`<python> -m personal_historical_archive`), the version
> and the MCP command for this machine — try it before installing anything. If
> the file is absent, pha has never run in this directory on this machine: use
> the install steps below. (It is machine-specific and gitignored, so it says
> nothing about any other machine.)

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

> [!WARNING]
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
| `skills/` | pha-specific agent skills — read the matching `skills/<name>/SKILL.md` before operating (see `skills/README.md`) |
| `pha-location.md`, `.pha/` | machine-local: where pha is installed on THIS machine (auto-generated, gitignored — see above) |
| `renders/`, `archive.db` | generated cache and index (do not edit) |

## Everyday commands

```bash
pha status                       # what's ingested, pending corrections, on hold
pha inbox [--move]               # list / move documents parked in the inbox
pha scan                         # extract + index new/changed dropbox files
pha search "query"               # search the extracted text
pha review [--doc N]             # import human corrections from library/
pha reindex [--path collections/COLX]  # rebuild the index (or one collection/doc)
pha prune [--dry-run]            # delete orphaned render image caches
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

## Bundled agent skills (`skills/`)

This archive carries its own **pha-specific agent skills** — read the matching
one before the task, no pha source checkout needed:

- `skills/pha-search-context/SKILL.md` — a `pha search` hit is a snippet;
  recover the complete page (raw transcription *and* the edited variant) before
  quoting or summarizing, and read back to the start of the document/item.
- `skills/pha-document-operations/SKILL.md` — re-scan / re-edit / re-encode one
  **already-ingested** document or collection (`pha scan --path … --reprocess`,
  `pha edit --path … --page N`, `pha test`).

Each skill is `<name>/SKILL.md` with YAML front matter whose `name` matches the
folder; `skills/README.md` documents the format and how to install a skill into
an agent runtime (`cp -R skills/<name> ~/.agents/skills/`). `pha` seeds this
folder once and never overwrites it — edit or extend it freely.

## Operating discipline

- **Never edit `renders/`, `archive.db`, or other generated files directly.**
- Rendered page images are cached under `renders/<content-sha>/`. Orphaned
  folders (from a changed or removed document) are cleaned up automatically;
  sweep any leftovers with `pha prune [--dry-run]`.
- Editing a page file under `library/` is a human correction: run `pha review`
  to import it, then `pha reindex`.
- **One model per model-server at a time** — `pha scan`, `pha edit`,
  `pha reindex`, `pha test` and `pha unbundle` lock every model-server they
  will use and refuse if one is busy; jobs on different servers may run
  together. Running two models on one server fills the disk and hangs the
  machine. `pha doctor` shows the servers and their capacity.
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

## Who you are talking to

You are working **inside an archive**, not inside the pha source code, so
assume the person you are talking with is a **historian, not a programmer**.
They know their documents and their research; they asked you to spare them the
technical steps. Unless they tell you otherwise:

- Explain what you are about to do, and why, in plain language and in terms of
  *their* archive (which documents, which pages, what will change) — not code,
  file names or command internals. Name a command only when you are handing
  them something to run, and say what it does.
- Report results the same way: "12 pages transcribed, 3 need your review" —
  not log lines or raw error output.
- Do not set them work you could do yourself. When they must run something on
  their own machine (a password prompt, an installer), give the exact step and
  explain it before they run it.
- **Before asking for more rights on their computer, stop and explain — in
  simple words — (1) what you need it for, (2) what it will change, and (3)
  that it is the smallest scope that does the job and can be undone.** Then
  ask, and wait for a clear yes. Never ask for broad access (the whole disk,
  the network, an administrator account) when a narrower permission is enough,
  and never take a permission they did not grant. This applies to every
  escalation request, including the sandbox / approval prompts your agent
  runtime raises.
- If something fails, say what went wrong and what you propose, in the same
  plain language — not just the raw error text.

## Before anything else: make sure `pha` is available and pointed here

`pha` is the tool that reads and writes this archive. **On this machine it is
probably already installed** — the last pha run recorded the exact path in
**`pha-location.md`** (this directory), together with a PATH-proof fallback
(`<python> -m personal_historical_archive`) that works from any shell, and the
MCP command. Read that file FIRST: it is machine-local and gitignored, so if it
is absent, pha has never run in this directory on this machine and you do need
to install it as below.

If it is not installed (or not on PATH) on this machine, install it first:

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
- `skills/` — **pha-specific agent skills**: read the matching
  `skills/<name>/SKILL.md` before doing that task (see `skills/README.md`).
- `renders/`, `archive.db` — generated cache and index (do not edit).

## Read the bundled skills in `skills/` FIRST

This archive ships the agent skills for operating it, so you do **not** need
the pha source repository. Before the matching task, read the skill and follow
it:

- `skills/pha-search-context/SKILL.md` — a `pha search` hit is a **snippet of a
  page**, not a document: number the hits, then recover the complete text of
  the page (raw transcription *and* the edited variant when one exists) with
  `pha page <doc> <page> [--edited]` before quoting or answering; when a hit
  starts mid-document, read back to the document/item start.
- `skills/pha-document-operations/SKILL.md` — re-run the pipeline on an
  **already-ingested** document or collection (rescan / re-edit / re-encode):
  `pha scan --path collections/COLX [--reprocess]`,
  `pha edit --path collections/COLX --page N`, `pha test collections/COLX --pages 3`.

Each skill is `skills/<name>/SKILL.md` with YAML front matter whose `name`
matches its folder name. `skills/README.md` documents the format, and how to
make a runtime pick a skill up automatically (copy it to that runtime's
user-level skills directory, e.g. `cp -R skills/pha-search-context
~/.agents/skills/`). `pha` seeds the folder once and never overwrites it —
edit, delete or add skills freely.

## How an agent should operate

- Never edit `renders/`, `archive.db` or other generated files directly.
- Rendered page images are cached in `renders/<content-sha>/`; orphaned
  folders are pruned automatically when a document changes or is removed.
  Sweep any leftovers with `pha prune [--dry-run]` (never touches the DB or
  `library/`).
- `pha status` reports progress and pending review corrections.
- Editing a page file under `library/` is a human correction; run
  `pha review` to import it, then `pha reindex`.
- One model per model-server at a time; `pha scan`/`pha edit`/`pha reindex`
  take the locks of the servers they use, so check `pha status` before
  starting a pass that shares a server with one already running.
- To change how a collection/document is processed (its palaeographer /
  editor / encoders / prompt), inspect what is already set first:
  `pha palaeographer <file>`, `pha editor <file>`, `pha prompts <file>`,
  `pha encoder [file]`. A collection selects its models with a `pha.yaml`
  sidecar next to the documents; the legacy plain-text `palaeographer` /
  `editor` files are the fallback.
- Full usage: see the pha README (in the source repository linked above, or
  the README.md in this directory).

## Installing a local OCR engine (only if a collection uses one)

Some collections are read by a **local OCR engine** — Tesseract, or LiteParse —
instead of a vision model; the palaeographer's model file then declares
`engine: tesseract` or `engine: liteparse`. These are ordinary local programs,
not AI models. Probe first with `pha doctor` (it reports what is missing and
how to install it) and install only what is actually needed.

- **LiteParse** (`lit parse`, ships the `lit` CLI): install the **Python**
  package — `pip install liteparse` (or `pipx install liteparse` /
  `uv tool install liteparse`). That is the recommended route and it works on
  Windows. `npm i -g @llamaindex/liteparse` is the alternative, but it
  **frequently fails on Windows** (and can leave the wrong `lit` on PATH): if
  it fails, do NOT give up and do not tell the historian that OCR is
  unavailable — install the Python package instead. The `lit` CLI is the SAME
  either way, so install one, not both. A bare `command -v lit` hit is not
  proof (`lit` is a common name, e.g. LLVM's test runner): `lit --version`
  must print a LiteParse version. LiteParse bundles its own Tesseract.
- **Tesseract**: macOS `brew install tesseract tesseract-lang`; Debian/Ubuntu
  `apt-get install tesseract-ocr tesseract-ocr-por` (one package per language);
  Windows `choco install tesseract` or the UB-Mannheim installer.
- Install it on the machine that keeps the archive, then run `pha doctor`
  again to confirm. A non-English `liteparse_lang`/`tesseract_lang` needs that
  language's data installed too.

## Writing a note in `notes/`

Cite the source document + page **and the variant**, and link the served page
viewer so a reader can page forward and back:

    [^1]: *DocHist do Padroado do Oriente* vol04 (doc 22), p. 437
          (edited: modern-portuguese@deepseek-v4-flash) —
          [p. 437](http://127.0.0.1:8765/doc/<slug>/p437) · `pha cite 22 437 --edited`

- `pha cite <doc> <page> [--edited]` prints the citation, the stable slug and
  the exact **filled** variant; it refuses to cite an empty (`*waiting*`) one.
- Link `/doc/<slug>/p<page>` — the **viewer** has prev/next/first/last, the
  position (`p. 437 of 618`) and a jump box. Embed `/p<page>.jpg` only when the
  picture belongs inline (an embed cannot navigate).
- Those links resolve only while `pha serve` is running, so put a short warning
  near the top of the note. Never embed a `library/` path — it carries the
  version date and goes stale on re-processing.
- Full format: `notes/README.md` in this directory.

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

# pha: machine-local tool location (describes THIS machine, not archive content)
.pha/
pha-location.md

# NOTE: dropbox/, library/, palaeographers/, editors/, encoders/, notes/ and
# skills/ are kept (they are the user-facing documents, transcriptions,
# definitions, research notes and agent skills).
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

    # the pha-specific agent skills: the archive carries its own copy (plus a
    # README documenting the format), seeded from the constants embedded in
    # pha — so the machine that owns the archive needs no source checkout.
    seed_archive_skills(p)

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
