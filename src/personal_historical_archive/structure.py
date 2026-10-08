"""Per-document structure registers for encoder page ranges.

An encoder may declare pages: "@structure:documents" instead of a literal
range. The range is then read from a JSON register next to the source
document (or from an explicit structure: path), validated against the
ingested source hash, and resolved to the same page-filter grammar the
literal form already uses.

There is deliberately no fallback literal: a missing/invalid register, a
missing group, or a source-hash mismatch refuses the encode and tells the
operator to run the collection's structure prescan.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config, Encoder


class StructureError(RuntimeError):
    """The register needed by an @structure encoder is missing or invalid."""


_STRUCTURE_RE = re.compile(r"^@structure(?::([A-Za-z0-9_.-]+))?$", re.IGNORECASE)


@dataclass(frozen=True)
class ResolvedPages:
    """Pages resolved for one encoder/document pair."""

    pages: str
    page_filter: frozenset[int] | None
    register_path: Path | None = None
    group: str | None = None
    register: dict[str, Any] | None = None

    @property
    def used_structure(self) -> bool:
        return self.register_path is not None


def parse_pages(value: str | None) -> frozenset[int] | None:
    """Parse 1-15,40 / all / * into a page set. None means all/empty."""
    if not value or value.strip().lower() in ("all", "*", ""):
        return None
    wanted: set[int] = set()
    for part in value.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            first, _, last = part.partition("-")
            try:
                wanted.update(range(int(first), int(last) + 1))
            except ValueError:
                continue
        else:
            try:
                wanted.add(int(part))
            except ValueError:
                continue
    return frozenset(wanted) if wanted else None


def default_structure_path(source: Path) -> Path:
    """Register beside a file document: doc.pdf -> doc.structure.json.

    A directory-of-images document has no suffix, so its register is a
    sibling: pages/ -> pages.structure.json.
    """
    if source.suffix:
        return source.with_suffix(".structure.json")
    return source.parent / f"{source.name}.structure.json"


def structure_path_for(cfg: Config, source: Path, encoder: Encoder) -> Path:
    """Resolve the encoder's structure: path, or the default register path."""
    raw = (getattr(encoder, "structure", "") or "").strip()
    if not raw:
        return default_structure_path(source)
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    local = source.parent / path
    if local.exists():
        return local
    return cfg.dropbox / path


def _load_register(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise StructureError(f"structure register not found at {path}: {e}") from e
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as e:
        raise StructureError(f"structure register is not valid JSON: {path}: {e}") from e
    if not isinstance(data, dict):
        raise StructureError(f"structure register must be a JSON object: {path}")
    if data.get("schema") != 1:
        raise StructureError(
            f"unsupported structure-register schema {data.get('schema')}: {path} "
            "(pha understands schema 1)"
        )
    if not isinstance(data.get("pages"), dict):
        raise StructureError(f"structure register has no pages object: {path}")
    return data


def _prescan_hint(source: Path) -> str:
    return (
        "run the collection's structure prescan to generate a register for "
        f"{source.name}, then retry"
    )


def resolve_encoder_pages(
    cfg: Config,
    doc: dict,
    encoder: Encoder,
    encoder_id: str,
    page_count: int | None = None,
) -> ResolvedPages:
    """Resolve an encoder's pages: for one document.

    A literal value is returned unchanged. @structure[:group] reads the
    document's register; every failure is a refusal, never a fallback.
    """
    token = (getattr(encoder, "pages", "") or "").strip()
    match = _STRUCTURE_RE.fullmatch(token)
    if match is None:
        return ResolvedPages(pages=token, page_filter=parse_pages(token))

    source = Path(doc["path"])
    path = structure_path_for(cfg, source, encoder)
    try:
        register = _load_register(path)
    except StructureError as e:
        raise StructureError(
            f"encoder {encoder_id} needs {e}; {_prescan_hint(source)}"
        ) from e

    expected = str(doc.get("sha256") or "")
    actual = str(register.get("source_sha256") or "")
    if not expected or not actual or actual.lower() != expected.lower():
        raise StructureError(
            f"structure register {path} does not match the ingested source "
            f"{source.name} (source_sha256 mismatch); {_prescan_hint(source)}"
        )

    group = match.group(1) or encoder_id
    value = register["pages"].get(group)
    if not isinstance(value, str) or not value.strip():
        raise StructureError(
            f"structure register {path} has no page group {group} for "
            f"encoder {encoder_id}; {_prescan_hint(source)}"
        )
    page_filter = parse_pages(value)
    if not page_filter:
        raise StructureError(
            f"structure register {path} page group {group} is empty or "
            f"unparseable ({value}); {_prescan_hint(source)}"
        )
    if page_count:
        invalid = sorted(p for p in page_filter if p < 1 or p > int(page_count))
        if invalid:
            raise StructureError(
                f"structure register {path} page group {group} contains "
                f"pages outside 1..{page_count}: {invalid}; {_prescan_hint(source)}"
            )
    return ResolvedPages(
        pages=value,
        page_filter=page_filter,
        register_path=path,
        group=group,
        register=register,
    )


def structure_sha256(path: Path | None) -> str | None:
    """Content hash of a register, or None when it cannot be read."""
    if path is None:
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def write_pass_copy(resolved: ResolvedPages, library_dir: Path | None) -> Path | None:
    """Copy the register actually used beside the pass records, for audit."""
    if resolved.register_path is None or library_dir is None:
        return None
    dest = library_dir / "structure.json"
    try:
        if resolved.register_path.resolve() != dest.resolve():
            shutil.copy2(resolved.register_path, dest)
    except OSError:
        return None
    return dest
