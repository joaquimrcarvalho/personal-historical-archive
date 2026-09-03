"""pha.yaml sidecar: per-document/collection pipeline configuration.

A `pha.yaml` file sits next to a document or at a collection root and holds
the four pointers (palaeographer/editor/encoder rules + their optional model
overrides) plus per-collection render settings. Several sidecars along the
directory chain (dropbox root → collection → document directory) are merged
nearest-wins PER KEY: the nearest sidecar that sets a key wins for that key,
and a key it leaves unset is inherited from an ancestor.

The file is validated against schema/pha-sidecar.schema.json so editors (VS
Code via the YAML language server) and the loader share one definition.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_SCHEMA: dict | None = None


def _schema() -> dict:
    global _SCHEMA
    if _SCHEMA is None:
        path = Path(__file__).resolve().parents[2] / "schema" / "pha-sidecar.schema.json"
        _SCHEMA = json.loads(path.read_text(encoding="utf-8"))
    return _SCHEMA


@dataclass
class StageSpec:
    """A stage pointer: the content-rules id plus its model (both required)."""
    rules: str
    model: str


@dataclass
class Sidecar:
    """Effective sidecar configuration for a document (merged nearest-wins)."""
    palaeographer: StageSpec | None = None
    editor: StageSpec | None = None        # None rules + editor_set = no editor
    editor_set: bool = False               # True when 'editor' key was present
    encoders: list[StageSpec] | None = None  # None = unspecified (dir discovery)
    render: dict = field(default_factory=dict)  # subset of render_dpi/max_image_px/jpeg_quality
    source: Path | None = None


def _normalize_stage(value) -> StageSpec | None:
    """Accept a {rules, model} mapping (both required) and return a StageSpec."""
    if value is None or not isinstance(value, dict):
        return None
    rules = str(value.get("rules", "")).strip()
    model = str(value.get("model", "") or "").strip()
    if not rules or not model:
        return None
    return StageSpec(rules=rules, model=model)


def _merge_keywise(merged: dict, data: dict) -> None:
    """Merge one sidecar's keys into `merged`, nearest-wins per top-level key."""
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        if key in data:  # presence-based, so an explicit `editor: null` wins
            merged[key] = value


def load_sidecar(path: Path) -> dict:
    """Read and validate a single pha.yaml, returning its dict. Raises
    ValueError with a readable message on YAML/schema errors."""
    try:
        # Tolerate stray `---` document markers (a once-migrated file might
        # carry them); take the first non-empty document.
        docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        data = next((d for d in docs if d is not None), {}) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping, got {type(data).__name__}")
    try:
        import jsonschema
        jsonschema.validate(data, _schema())
    except ImportError:
        pass  # jsonschema not installed; schema check skipped
    except jsonschema.ValidationError as e:
        raise ValueError(f"invalid {path}: {e.message}") from e
    return data


def sidecar_chain(dropbox: Path, file_dir: Path) -> list[Path]:
    """Ordered sidecar paths from dropbox root down to file_dir (root first)."""
    chain: list[Path] = []
    d = file_dir.resolve()
    dropbox = dropbox.resolve()
    while True:
        chain.append(d)
        if d == dropbox or dropbox not in d.parents:
            break
        d = d.parent
    return list(reversed(chain))


def resolve_sidecar(dropbox: Path, file_dir: Path, stem: str | None = None) -> Sidecar:
    """Merge the pha.yaml sidecars along the chain root→nearest (per-key).

    `stem` names a document-specific sidecar (`<stem>.pha.yaml` next to the
    document) that wins over the directory-level `pha.yaml`."""
    merged: dict = {}
    last_source: Path | None = None
    for d in sidecar_chain(dropbox, file_dir):
        p = d / "pha.yaml"
        if p.is_file():
            merged.update(load_sidecar(p))
            last_source = p
    if stem:
        p = file_dir / f"{stem}.pha.yaml"
        if p.is_file():
            merged.update(load_sidecar(p))
            last_source = p
    return sidecar_from_dict(merged, last_source)


def sidecar_from_dict(data: dict, source: Path | None = None) -> Sidecar:
    """Build a Sidecar from a (merged) dict. `data` must already be schema-valid
    or empty."""
    sc = Sidecar(source=source)
    if "palaeographer" in data and data["palaeographer"] is not None:
        sc.palaeographer = _normalize_stage(data["palaeographer"])
    if "editor" in data:
        sc.editor_set = True
        sc.editor = _normalize_stage(data["editor"])
    if "encoders" in data:
        raw = data["encoders"]
        sc.encoders = [_normalize_stage(item) for item in raw] if isinstance(raw, list) else None
    if "render" in data and isinstance(data["render"], dict):
        sc.render = data["render"]
    return sc


def effective_render(cfg, sidecar: Sidecar | None) -> tuple[int, int, int]:
    """Return (render_dpi, max_image_px, jpeg_quality) with sidecar render
    overrides applied over the global extraction.* defaults."""
    r = (sidecar.render if sidecar else {}) or {}
    return (
        int(r.get("render_dpi", cfg.render_dpi)),
        int(r.get("max_image_px", cfg.max_image_px)),
        int(r.get("jpeg_quality", cfg.jpeg_quality)),
    )
