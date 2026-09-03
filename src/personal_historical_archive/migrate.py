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
import os
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

# Prepended to every generated pha.yaml so editors bind it to the project's
# JSON Schema (instead of a schemastore auto-match, e.g. "CrowdSec Collection").
_SCHEMA_MODELINE = (
    "# yaml-language-server: $schema="
    "https://raw.githubusercontent.com/joaquimrcarvalho/personal-historical-archive/"
    "main/schema/pha-sidecar.schema.json"
)


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


def _stage_content(fm: dict, body: str) -> str:
    """Content-only stage file: description + stage params + body (NO model —
    the model is chosen in pha.yaml)."""
    out = {}
    if fm.get("description"):
        out["description"] = fm["description"]
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


def _migrate_stage_files(cfg: Config, stage_dirs: list[tuple[Path, str]], report: _Report, dry_run: bool) -> dict[str, str]:
    """Split model interfaces out of stage files and return a stage-id → model-id
    map for writing into pha.yaml sidecars.

    Handles three states: legacy inline (interface in front matter → split into
    models/ + rules-only stage), old migrated (`model:` in front matter → move
    the reference to pha.yaml), and pure rules (already correct → unchanged).
    Model ids are named after the model name; when the same model name maps to
    DIFFERENT interfaces, each is disambiguated with its stage kind."""
    stage_model: dict[str, str] = {}       # stage stem -> model id
    legacy: list[tuple[Path, str, dict, str, dict]] = []  # (file, kind, fm, body, iface)
    rewrite: list[tuple[Path, dict, str]] = []            # (file, fm, body) → pure rules

    # First pass: classify each stage file.
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
            if _is_legacy(fm):
                iface = {"description": fm.get("description")}
                for k in _INTERFACE_KEYS:
                    if k in fm:
                        iface[k] = fm[k]
                legacy.append((f, kind, fm, body, iface))
            elif fm.get("model"):
                # old migrated layout: record the reference, strip it to rules-only
                stage_model[f.stem] = str(fm["model"]).strip()
                rewrite.append((f, fm, body))
            else:
                report.unchanged.append(str(f))

    # Assign model ids for legacy entries: group distinct interfaces by name.
    base_sigs: dict[str, set[str]] = {}
    sig_kind: dict[str, str] = {}
    for _f, kind, _fm, _body, iface in legacy:
        base = _slug(str(iface.get("model", "")))
        sig = _interface_sig(iface)
        base_sigs.setdefault(base, set()).add(sig)
        sig_kind.setdefault(sig, kind)

    taken = set(cfg.models)  # existing model ids are never overwritten
    assigned: dict[str, str] = {}
    created: set[str] = set()
    for _f, _kind, _fm, _body, iface in legacy:
        base = _slug(str(iface.get("model", "")))
        sig = _interface_sig(iface)
        if sig in assigned:
            continue
        mid = base if len(base_sigs[base]) == 1 else f"{base}-{sig_kind[sig]}"
        n = 2
        cand = mid
        while cand in taken:
            cand = f"{mid}-{n}"
            n += 1
        assigned[sig] = cand
        taken.add(cand)
        created.add(cand)

    # Create model files + queue legacy stage files for rewrite.
    for f, _kind, fm, body, iface in legacy:
        model_id = assigned[_interface_sig(iface)]
        stage_model[f.stem] = model_id
        rewrite.append((f, fm, body))
        model_path = cfg.models_dir / f"{model_id}.md"
        if dry_run:
            print(f"[dry-run] would write {model_path}")
        elif not model_path.exists():
            model_path.write_text(_model_content(model_id, iface), encoding="utf-8")

    # Rewrite every queued stage file as pure rules (no model reference).
    for f, fm, body in rewrite:
        new_stage = _stage_content(fm, body)
        if dry_run:
            print(f"[dry-run] would rewrite {f}")
        else:
            # Preserve mtime: the prompt body is unchanged, so migrating must
            # NOT trigger a spurious re-extraction on the next scan.
            try:
                st = f.stat()
                f.write_text(new_stage, encoding="utf-8")
                os.utime(f, (st.st_atime, st.st_mtime))
            except OSError:
                pass
        report.stages_rewritten.append(str(f))
    for mid in sorted(created):
        report.models_created.append(mid)
    return stage_model


def _migrate_selection_files(cfg: Config, report: _Report, dry_run: bool, remove: bool, stage_model: dict[str, str]) -> None:
    """Convert palaeographer/editor selection files into pha.yaml sidecars,
    attaching the model id recorded for each stage."""
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
            updates[stem] = {"rules": rid, "model": stage_model.get(rid, cfg.default_model)}
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
            # pha.yaml is plain YAML (no `---` front-matter delimiters — those
            # belong only to the .md definition files). The leading comment
            # binds it to the pha-sidecar JSON Schema in editors.
            body = yaml.safe_dump(merged, sort_keys=False, default_flow_style=False, allow_unicode=True)
            sidecar.write_text(_SCHEMA_MODELINE + "\n" + body, encoding="utf-8")
        report.sidecars_written.append(str(sidecar))
        if remove:
            for p in to_remove:
                if dry_run:
                    print(f"[dry-run] would remove {p}")
                else:
                    p.unlink(missing_ok=True)
                report.selection_files_removed.append(str(p))


def _migrate_encoder_lists(cfg: Config, report: _Report, dry_run: bool, stage_model: dict[str, str]) -> None:
    """Write each collection's encoders/ into its pha.yaml `encoders:` list,
    attaching each encoder's model (they are rules-only after the split)."""
    dropbox = cfg.dropbox
    if not dropbox.is_dir():
        return
    for enc_dir in sorted(dropbox.rglob("encoders")):
        if not enc_dir.is_dir():
            continue
        encoders = []
        for f in sorted(enc_dir.iterdir()):
            if not f.is_file() or f.name.startswith(("_", ".")):
                continue
            if f.suffix.lower() not in (".md", ".txt"):
                continue
            if re.search(r"\.(prompt|langextract)\.(md|txt)$", f.name):
                continue  # companion files, not definitions
            encoders.append({"rules": f.stem, "model": stage_model.get(f.stem, cfg.default_model)})
        if not encoders:
            continue
        sidecar = enc_dir.parent / "pha.yaml"
        merged: dict = {}
        if sidecar.is_file():
            try:
                merged = yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                merged = {}
        merged["encoders"] = encoders
        if dry_run:
            print(f"[dry-run] would write {sidecar}")
        else:
            body = yaml.safe_dump(merged, sort_keys=False, default_flow_style=False, allow_unicode=True)
            sidecar.write_text(_SCHEMA_MODELINE + "\n" + body, encoding="utf-8")
        report.sidecars_written.append(str(sidecar))


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
    stage_model = _migrate_stage_files(cfg, stage_dirs, report, dry_run)
    _migrate_selection_files(cfg, report, dry_run, remove_selection_files, stage_model)
    _migrate_encoder_lists(cfg, report, dry_run, stage_model)
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
