"""One-shot migration from the legacy (pre-registry) configuration layout to
the new three-layer layout: models/ registry + content-only stage files +
pha.yaml sidecar.

Run with `pha migrate-config`. Two steps:

1. Split bundled model interfaces out of palaeographer/editor/encoder files
   into models/<id>.md, leaving the stage files content-only (`model: <id>`).
2. Convert the nearest-wins `palaeographer`/`editor` selection files into
   pha.yaml sidecars (the legacy global `encoder` selection file is left in
   place — it still works via the deprecated plain-file fallback).

Idempotent: already-migrated files are left untouched; existing models are
reused by id when their interface matches.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import _split_frontmatter, Config

_INTERFACE_KEYS = (
    "base_url", "api_key", "model", "api_style",
    "thinking", "max_vision_px", "vision_jpeg_quality", "context_tokens",
)
_STAGE_KEEP_KEYS = (
    "temperature", "max_tokens", "timeout_s", "batch_pages", "max_input_chars",
    "overlap_pages", "extraction_passes", "candidate_pattern",
    "candidate_header", "pages",
)
_SELECTION_STEMS = ("palaeographer", "editor")


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name or "").strip("-").lower()
    return s or "model"


def _is_legacy(fm: dict) -> bool:
    return ("base_url" in fm) or ("api_key" in fm) or ("api_style" in fm)


def _interface_sig(iface: dict) -> str:
    return json.dumps({k: iface.get(k) for k in _INTERFACE_KEYS}, sort_keys=True)


def _dump_fm(d: dict) -> str:
    return "---\n" + yaml.safe_dump(d, sort_keys=False, default_flow_style=False) + "---\n"


def _model_content(model_id: str, iface: dict) -> str:
    fm = {}
    if iface.get("description"):
        fm["description"] = iface["description"]
    for k in _INTERFACE_KEYS:
        if iface.get(k) is not None:
            fm[k] = iface[k]
    return _dump_fm(fm) + "\n"


def _stage_content(fm: dict, model_id: str, body: str) -> str:
    out = {}
    if fm.get("description"):
        out["description"] = fm["description"]
    out["model"] = model_id
    for k in _STAGE_KEEP_KEYS:
        if k in fm and fm[k] not in (None, ""):
            out[k] = fm[k]
    return _dump_fm(out) + (body.strip() + "\n" if body.strip() else "\n")


@dataclass
class _Report:
    models_created: list[str] = field(default_factory=list)
    models_reused: list[str] = field(default_factory=list)
    stages_rewritten: list[str] = field(default_factory=list)
    sidecars_written: list[str] = field(default_factory=list)
    selection_files_removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def _migrate_stage_files(cfg: Config, stage_dirs: list[tuple[Path, str]], report: _Report, dry_run: bool) -> None:
    """Split model interfaces out of stage files across (dir, kind) pairs.

    Model ids are named after the model name; when the same model name maps to
    DIFFERENT interfaces for different stages, each is disambiguated with its
    stage kind (e.g. minimax-m3-palaeographer vs minimax-m3-editor)."""
    # First pass: collect legacy stage files + their interfaces.
    entries: list[tuple[Path, str, dict, str, dict]] = []  # (file, kind, fm, body, iface)
    for directory, kind in stage_dirs:
        if not directory.is_dir():
            continue
        for f in sorted(directory.iterdir()):
            if not f.is_file() or f.name.startswith(("_", ".")):
                continue
            if f.suffix.lower() not in (".md", ".txt"):
                continue
            text = f.read_text(encoding="utf-8")
            fm, body = _split_frontmatter(text)
            if not _is_legacy(fm):
                report.unchanged.append(str(f))
                continue
            iface = {"description": fm.get("description")}
            for k in _INTERFACE_KEYS:
                if k in fm:
                    iface[k] = fm[k]
            entries.append((f, kind, fm, body, iface))

    # Assign ids: group distinct interfaces by base model name.
    base_sigs: dict[str, set[str]] = {}
    sig_kind: dict[str, str] = {}
    for _f, kind, _fm, _body, iface in entries:
        base = _slug(str(iface.get("model", "")))
        sig = _interface_sig(iface)
        base_sigs.setdefault(base, set()).add(sig)
        sig_kind.setdefault(sig, kind)

    taken = set(cfg.models)  # existing model ids are never overwritten
    assigned: dict[str, str] = {}  # sig -> model id
    created: set[str] = set()
    for _f, _kind, _fm, _body, iface in entries:
        base = _slug(str(iface.get("model", "")))
        sig = _interface_sig(iface)
        if sig in assigned:
            continue
        if len(base_sigs[base]) == 1:
            mid = base
        else:
            mid = f"{base}-{sig_kind[sig]}"
        n = 2
        cand = mid
        while cand in taken:
            cand = f"{mid}-{n}"
            n += 1
        assigned[sig] = cand
        taken.add(cand)
        created.add(cand)

    # Second pass: write model + rewritten stage files.
    for f, _kind, fm, body, iface in entries:
        sig = _interface_sig(iface)
        model_id = assigned[sig]
        model_path = cfg.models_dir / f"{model_id}.md"
        new_stage = _stage_content(fm, model_id, body)
        if dry_run:
            print(f"[dry-run] would write {model_path}")
            print(f"[dry-run] would rewrite {f}")
        else:
            if not model_path.exists():
                model_path.write_text(_model_content(model_id, iface), encoding="utf-8")
            f.write_text(new_stage, encoding="utf-8")
        report.stages_rewritten.append(str(f))
    for mid in sorted(created):
        report.models_created.append(mid)


def _migrate_selection_files(cfg: Config, report: _Report, dry_run: bool, remove: bool) -> None:
    """Convert palaeographer/editor selection files into pha.yaml sidecars."""
    dropbox = cfg.dropbox
    if not dropbox.is_dir():
        return
    for directory in sorted(dropbox.rglob("*")):
        if not directory.is_dir():
            continue
        updates: dict = {}
        to_remove: list[Path] = []
        for stem in _SELECTION_STEMS:
            found: Path | None = None
            for ext in ("", ".txt", ".md"):
                cand = directory / f"{stem}{ext}"
                if cand.is_file():
                    found = cand
                    break
            if found is None:
                continue
            rid = found.read_text(encoding="utf-8").strip()
            rid = re.sub(r"^[#\-*\s]+", "", rid.splitlines()[0] if rid else "").strip()
            if not rid:
                continue
            updates[stem] = {"rules": rid}
            to_remove.append(found)
        if not updates:
            continue
        sidecar = directory / "pha.yaml"
        merged: dict = {}
        if sidecar.is_file():
            try:
                merged = yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                merged = {}
        merged.update(updates)
        if dry_run:
            print(f"[dry-run] would write {sidecar}")
        else:
            sidecar.write_text(_dump_fm(merged) + "\n", encoding="utf-8")
        report.sidecars_written.append(str(sidecar))
        if remove:
            for p in to_remove:
                if dry_run:
                    print(f"[dry-run] would remove {p}")
                else:
                    p.unlink(missing_ok=True)
                report.selection_files_removed.append(str(p))


def _collection_encoder_dirs(cfg: Config) -> list[Path]:
    """Find collection-local encoders/ dirs under the dropbox."""
    dirs: list[Path] = []
    if not cfg.dropbox.is_dir():
        return dirs
    for d in cfg.dropbox.rglob("encoders"):
        if d.is_dir():
            dirs.append(d)
    return dirs


def migrate_config(cfg: Config, dry_run: bool = False, remove_selection_files: bool = False) -> _Report:
    """Run the one-shot migration and return a report."""
    report = _Report()
    stage_dirs: list[tuple[Path, str]] = [
        (cfg.palaeographers_dir, "palaeographer"),
        (cfg.editors_dir, "editor"),
        (cfg.encoders_dir, "encoder"),
    ]
    stage_dirs += [(d, "encoder") for d in _collection_encoder_dirs(cfg)]
    _migrate_stage_files(cfg, stage_dirs, report, dry_run)
    _migrate_selection_files(cfg, report, dry_run, remove_selection_files)
    return report


def print_report(report: _Report) -> None:
    print(f"models created: {len(report.models_created)}")
    for m in report.models_created:
        print(f"  + {m}")
    print(f"models reused:   {len(set(report.models_reused))}")
    print(f"stages rewritten: {len(report.stages_rewritten)}")
    print(f"sidecars written: {len(report.sidecars_written)}")
    print(f"selection files removed: {len(report.selection_files_removed)}")
    print(f"unchanged: {len(report.unchanged)}")
