"""Upload documents/collections into the dropbox at the conventional location.

`pha upload document <PATH>` copies a single document (a file, or a directory
of images = one document) into dropbox/documents/. `pha upload collection
<PATH>` copies a whole collection directory into dropbox/collections/.

When running through the MCP server on another machine, `pha_upload` uses the
SAME helpers with that machine's dropbox, so a client on machine A can push
documents into the dropbox on machine B.

Destination policy: by default we REFUSE to overwrite an existing destination
(document or collection) unless --replace/--merge is given. --replace removes
the existing destination first; --merge copies into it, overwriting only the
files that change.
"""
from __future__ import annotations

import shutil

from pathlib import Path

from .config import Config
from .ingest import _is_document_dir


def _resolve_src(src: str, cwd: Path | None = None) -> Path:
    p = Path(src)
    if not p.is_absolute():
        p = (cwd or Path.cwd()) / p
    return p.resolve()


def classify(src: Path) -> str:
    """Return 'document' or 'collection' for a local path (or raise)."""
    if src.is_file():
        return "document"
    if src.is_dir():
        # a directory of images (no subdirs/PDFs) is ONE document; any other
        # directory (has subdirs, PDFs, or nested files) is treated as a
        # collection.
        try:
            if _is_document_dir(src):
                return "document"
        except OSError:
            pass
        return "collection"
    raise FileNotFoundError(f"no such path: {src}")


def _dest_for(cfg: Config, kind: str, src: Path, name: str | None) -> Path:
    """Compute the destination path inside the dropbox."""
    leaf = name or src.name
    if kind == "collection":
        return cfg.dropbox / "collections" / leaf
    # document: files go to documents/<leaf>; image-dir documents go to
    # documents/<leaf>/... preserving the dir
    if src.is_dir():
        return cfg.dropbox / "documents" / leaf
    return cfg.dropbox / "documents" / leaf


def upload(cfg: Config, src: str, kind: str | None = None, *, name: str | None = None,
           replace: bool = False, merge: bool = False) -> dict:
    """Copy `src` into the dropbox under the conventional location.

    kind: 'document' or 'collection'; if None, auto-detected from the source.
    Returns a report dict with the destination and what was done.
    """
    src = _resolve_src(src, cwd=cfg.root)
    if not src.exists():
        raise FileNotFoundError(f"no such path: {src}")
    if kind is None:
        kind = classify(src)
    elif kind not in ("document", "collection"):
        raise ValueError(f"kind must be 'document' or 'collection', got {kind!r}")

    dest = _dest_for(cfg, kind, src, name)
    exists = dest.exists()

    if exists and not (replace or merge):
        raise FileExistsError(
            f"{kind} already exists in the dropbox: {dest} "
            f"(pass --replace to overwrite it, or --merge to copy into it)"
        )

    if exists:
        if replace:
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        # merge: keep dest, copy underneath/into it

    dest.parent.mkdir(parents=True, exist_ok=True)

    copied = 0
    if src.is_dir():
        # copy_dir: into dest (dest is the collection/doc-dir)
        if kind == "collection":
            # copy the collection directory contents into dest (dest == dropbox/collections/<name>)
            if src.name != dest.name:
                dst_root = dest
            else:
                dst_root = dest
            if not exists:
                dst_root.mkdir(parents=True, exist_ok=True)
            for item in src.iterdir():
                if item.is_dir():
                    shutil.copytree(item, dst_root / item.name, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dst_root / item.name)
                copied += 1
        else:
            # document image-dir -> copy the dir itself to documents/<name>
            if exists and merge:
                for item in src.iterdir():
                    if item.is_dir():
                        shutil.copytree(item, dest / item.name, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest / item.name)
                    copied += 1
            else:
                shutil.copytree(src, dest)
                copied = sum(1 for _ in dest.rglob("*")) or 1
    else:
        shutil.copy2(src, dest)
        copied = 1

    return {
        "action": "uploaded",
        "kind": kind,
        "source": str(src),
        "destination": str(dest),
        "files_copied": copied,
        "replaced": exists and replace,
        "merged": exists and merge,
    }


# ---------------------------------------------------------------------------
# MCP / file-content uploads (client and server are different machines, so we
# receive bytes directly rather than a server-side path)
# ---------------------------------------------------------------------------


def _resolve_dest_b64(cfg: Config, kind: str, name: str) -> Path:
    """Resolve where a single uploaded file should land, given a kind + name.

    kind='document': name is a filename -> dropbox/documents/<name>.
    kind='collection': name may be 'COLX/file.pdf' -> dropbox/collections/<name>.
    """
    name = name.strip()
    if not name:
        raise ValueError("a destination name is required")
    if kind == "document":
        return cfg.dropbox / "documents" / name
    if kind == "collection":
        return cfg.dropbox / "collections" / name
    raise ValueError(f"kind must be 'document' or 'collection', got {kind!r}")


def save_upload(cfg: Config, kind: str, name: str, blob: bytes, dest: Path,
                exists: bool, replace: bool, merge: bool) -> dict:
    """Persist an uploaded file's bytes at `dest` under the kind rules."""
    if exists and not (replace or merge):
        raise FileExistsError(
            f"{kind} already exists in the dropbox: {dest} (pass replace=True "
            f"to overwrite it, or merge=True to update)"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(blob)
    return {
        "action": "uploaded",
        "kind": kind,
        "destination": str(dest),
        "files_copied": 1,
        "replaced": exists and replace,
        "merged": exists and merge,
    }
