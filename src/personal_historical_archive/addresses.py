"""Stable, public page addresses: slugs, relative paths, renders, variant sets.

Every address pha used to expose for a page is volatile across a re-process:
``documents.id`` is re-inserted on a content change, the library folder carries
the document's creation date (``<stem>_<YYYY-MM-DD>``) and the render folder is
keyed on the content hash (``renders/<sha256>/``). A citation that must still
resolve after the volume is re-scanned needs one identity derived from the
*dropbox-relative path*, which changes only when the document is renamed or
moved.

This module is the single source of that identity (plus the render lookup and
the variant enumeration built on it) so the CLI, the MCP tools and an
out-of-tree consumer all agree by construction.

See ``enhancements/pha-stable-page-addresses-enhancement-request.md``.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path, PurePosixPath

from .config import Config
from .ingest import _library_doc_dir

# Library variant directories:
#   transcription-<palaeographer>[@<model>]   the raw reading
#   edited-<editor>[@<model>]                 an editor's output
_VARIANT_RE = re.compile(r"^(transcription|edited)-([^@]+)(?:@(.+))?$")

# The body an export writes for a page that has no text yet (see
# ingest.write_document_pages / write_edited_pages).
_WAITING_BODY = "*waiting*"


def _as_dict(doc) -> dict:
    """Accept a dict or a sqlite3.Row; never raise on either."""
    if doc is None:
        return {}
    if isinstance(doc, dict):
        return doc
    try:
        return dict(doc)
    except (TypeError, ValueError):
        return {}


def document_rel_path(cfg: Config, doc) -> str:
    """The document's dropbox-relative path (posix separators).

    This is what a slug is derived from, and what an external consumer must be
    able to see: the DB stores an absolute ``path`` and a ``dir_path``, and
    string-surgery on a machine-specific absolute path is exactly the drift this
    module exists to prevent.
    """
    d = _as_dict(doc)
    raw = d.get("path") or ""
    if raw:
        try:
            return Path(raw).resolve().relative_to(Path(cfg.dropbox).resolve()).as_posix()
        except (ValueError, OSError):
            pass
    name = d.get("filename") or (Path(raw).name if raw else "")
    dir_path = d.get("dir_path") or ""
    if dir_path and name:
        return (PurePosixPath(str(dir_path)) / name).as_posix()
    return str(name or dir_path or "")


def doc_slug(rel_path: str) -> str:
    """Canonical slug for a dropbox-relative path.

    Rule (mechanical, so two implementations derive the same string): drop a
    leading ``collections/`` segment, drop the file extension, lowercase, fold
    accents to ASCII, collapse every run of non-alphanumeric characters to
    ``-``, trim leading/trailing ``-``. No date and no hash — so the slug
    survives re-processing, and only a rename/move changes it.
    """
    parts = list(PurePosixPath(str(rel_path)).parts)
    if parts and parts[0] == "collections":
        parts = parts[1:]
    if not parts:
        return ""
    parts[-1] = PurePosixPath(parts[-1]).stem  # drop the file extension
    text = unicodedata.normalize("NFKD", "/".join(parts).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def document_slug(cfg: Config, doc) -> str:
    """The slug of the document a row refers to."""
    return doc_slug(document_rel_path(cfg, doc))


def render_path(cfg: Config, doc, page_no: int, source_name: str | None = None) -> Path | None:
    """The rendered JPEG for one page, or None.

    Two candidates, matching the existing consumers: a document whose pages
    were exported as named images uses ``<source_name>.jpg``; a PDF uses
    ``p<NNN>.jpg``. The containing ``renders/<sha256>/`` directory is looked up
    from the document's CURRENT hash, so callers never hard-code it.
    """
    d = _as_dict(doc)
    sha = d.get("sha256")
    if not sha:
        return None
    base = Path(cfg.renders) / str(sha)
    try:
        page = int(page_no)
    except (TypeError, ValueError):
        return None
    names: list[str] = []
    if source_name:
        names += [f"{source_name}.jpg", f"{source_name}.jpeg"]
    names += [f"p{page:03d}.jpg", f"p{page:03d}.jpeg"]
    for name in names:
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


def _strip_frontmatter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1:])
    return text


def _is_filled(path: Path) -> bool:
    """Is this library page file real text (not the empty ``*waiting*`` stub)?"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    body = _strip_frontmatter(text).strip()
    return bool(body) and body != _WAITING_BODY


def variant_files(
    cfg: Config, doc, page_no: int, source_name: str | None = None
) -> dict[str, dict]:
    """Every variant directory of one page, keyed by its library folder name.

    ``{"edited-french-ocr@deepseek-v4-flash": {"stage": "edited", "id":
    "french-ocr", "model": "deepseek-v4-flash", "filled": True, "file": "…"}}``

    ``filled`` is False for a variant whose page file is missing or still the
    ``*waiting*`` stub — the difference between a citation that cites something
    and one that cites nothing.
    """
    d = _as_dict(doc)
    doc_dir = _library_doc_dir(cfg, d)
    out: dict[str, dict] = {}
    if doc_dir is None or not doc_dir.is_dir():
        return out
    try:
        page = int(page_no)
    except (TypeError, ValueError):
        return out
    name = f"{source_name}.md" if source_name else f"page-{page:03d}.md"
    for sub in sorted(doc_dir.iterdir()):
        if not sub.is_dir():
            continue
        m = _VARIANT_RE.match(sub.name)
        if not m:
            continue
        f = sub / name
        exists = f.is_file()
        out[sub.name] = {
            "stage": m.group(1),
            "id": m.group(2),
            "model": m.group(3),
            "filled": bool(exists and _is_filled(f)),
            "file": str(f) if exists else None,
        }
    return out


def variant_label(name: str) -> str:
    """``edited-french-ocr@deepseek-v4-flash`` -> ``edited: french-ocr@deepseek-v4-flash``."""
    m = _VARIANT_RE.match(name)
    if not m:
        return name
    stage, ident, model = m.group(1), m.group(2), m.group(3)
    return f"{stage}: {ident}" + (f"@{model}" if model else "")


def render_url(base_url: str, slug: str, page_no: int, ext: str = "jpg") -> str:
    """The stable URL of a page render served by ``pha serve``."""
    return f"{base_url.rstrip('/')}/doc/{slug}/p{int(page_no):03d}.{ext}"


def viewer_url(base_url: str, slug: str, page_no: int) -> str:
    """The stable URL of the served page **viewer** (prev/next, first/last).

    The same page as :func:`render_url`, but the HTML reading surface rather
    than the bare JPEG — this is what a citation should link to.
    """
    return f"{base_url.rstrip('/')}/doc/{slug}/p{int(page_no):03d}"


def overview_url(base_url: str, slug: str) -> str:
    """The stable URL of the served document overview (page ranges + jump box)."""
    return f"{base_url.rstrip('/')}/doc/{slug}/"
