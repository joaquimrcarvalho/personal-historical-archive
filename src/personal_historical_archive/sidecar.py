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
    """A stage pointer: the content-rules id plus its model (both required).

    ``pre``/``post`` are the stage's filter chains (see FILTERS_PLAN.md): they
    shape the value flowing into/out of the model. Empty = no filters.
    """
    rules: str
    model: str
    pre: list = field(default_factory=list)
    post: list = field(default_factory=list)


@dataclass
class Sidecar:
    """Effective sidecar configuration for a document (merged nearest-wins)."""
    palaeographer: StageSpec | None = None
    editor: StageSpec | None = None        # None rules + editor_set = no editor
    editor_set: bool = False               # True when 'editor' key was present
    encoders: list[StageSpec] | None = None  # None = unspecified (dir discovery)
    render: dict = field(default_factory=dict)  # subset of render_dpi/max_image_px/jpeg_quality
    source: Path | None = None


def _normalize_filter(value):
    """One `pre:`/`post:` entry: a bare id, or {name, params}.

    Returns a FilterSpec, or None for an entry that names no filter. A filter
    that cannot be loaded is a run-time error (see filters.load_filter), not a
    parse error here — the sidecar may legitimately be read by tools that do
    not have the archive's filters/ directory to hand.
    """
    from .filters import FilterSpec

    if isinstance(value, str):
        name = value.strip()
        return FilterSpec(name=name) if name else None
    if isinstance(value, dict):
        name = str(value.get("name") or value.get("filter") or "").strip()
        if not name:
            return None
        params = value.get("params") or {}
        return FilterSpec(name=name, params=dict(params) if isinstance(params, dict) else {})
    return None


def _normalize_filter_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (str, dict)):
        value = [value]
    if not isinstance(value, list):
        return []
    return [f for f in (_normalize_filter(v) for v in value) if f is not None]


def _normalize_stage(value) -> StageSpec | None:
    """Accept a {rules, model} mapping (both required) and return a StageSpec."""
    if value is None or not isinstance(value, dict):
        return None
    rules = str(value.get("rules", "")).strip()
    model = str(value.get("model", "") or "").strip()
    if not rules or not model:
        return None
    return StageSpec(rules=rules, model=model,
                     pre=_normalize_filter_list(value.get("pre")),
                     post=_normalize_filter_list(value.get("post")))


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


def resolve_stages(cfg, sel_dir: Path, stem: str | None = None,
                   sidecar_stem: str | None = None) -> dict:
    """Resolve a document/collection's effective processing.

    A `pha.yaml` sidecar (nearest-wins up the directory chain) takes precedence
    over the legacy `palaeographer` / `editor` selection files. `stem` names the
    document (used by the legacy resolvers); `sidecar_stem` names a
    `<stem>.pha.yaml` next to the document (None for a collection directory).

    Returns the resolved palaeographer and editor — each with its model and the
    file the choice came from — plus the prompt and its source. This is the
    shape `pha config` and the MCP `pha_collection_config` tool expose.
    """
    from .extract import resolve_editor_id, resolve_palaeographer_id, resolve_prompt

    sc = resolve_sidecar(cfg.dropbox, sel_dir,
                         stem=(stem if sidecar_stem is None else sidecar_stem))
    pal_override = None
    if sc.palaeographer:
        pal_id, pal_src = sc.palaeographer.rules, f"{sc.source} (pha.yaml)"
        pal_override = sc.palaeographer.model
    else:
        pal_id, pal_src = resolve_palaeographer_id(stem, sel_dir, cfg.dropbox)
    ed_override = None
    if sc.editor_set:
        ed_id = sc.editor.rules if sc.editor else None
        ed_src = str(sc.source) if sc.source else None
        ed_override = sc.editor.model if sc.editor else None
    else:
        ed_id, ed_src = resolve_editor_id(stem, sel_dir, cfg.dropbox)
    prompt_txt, prompt_src = resolve_prompt(stem, sel_dir, cfg.dropbox, cfg.prompts)
    # Encoders: a pha.yaml `encoders:` list wins (rules + model, run in page
    # order); otherwise the collection's encoders/ files are discovered next to
    # the documents. Either way the answer is "what actually runs".
    from .extract import encoder_file_named, encoder_files_for

    encoders: list[dict] = []
    if sc.encoders:
        for spec in sc.encoders:
            f = encoder_file_named(spec.rules, sel_dir, cfg.dropbox)
            e = cfg.encoder_from_file(f) if f else None
            encoders.append({
                "id": spec.rules, "model": spec.model,
                "path": str(f) if f else None,
                "pages": (e.pages if e else None),
                "source": (f"{sc.source} (pha.yaml)" if sc.source else "pha.yaml"),
            })
    else:
        for f in encoder_files_for(stem or "", sel_dir, cfg.dropbox):
            e = cfg.encoder_from_file(f)
            encoders.append({
                "id": f.stem, "model": (e.model if e else None),
                "path": str(f), "source": "directory",
                "pages": (e.pages if e else None),
            })
    # An unknown id in pha.yaml (a typo, or a definition file that was removed) is
    # reported as a problem instead of raising — the view shows the misconfiguration
    # rather than a traceback.
    problems: list[str] = []
    pal = None
    if pal_id:
        pal = cfg.palaeographers.get(pal_id)
        if pal is None:
            problems.append(f"unknown palaeographer {pal_id!r}")
    else:
        try:
            pal = cfg.get_palaeographer()
        except KeyError:
            pal = None
    if pal is not None:
        try:
            pal = cfg.resolve_model(pal, pal_override)
        except KeyError:
            pass
    ed = cfg.editors.get(ed_id) if ed_id else None
    if ed_id and ed is None:
        problems.append(f"unknown editor {ed_id!r}")
    if ed is not None:
        try:
            ed = cfg.resolve_model(ed, ed_override)
        except KeyError:
            pass
    return {
        "palaeographer": {"id": (pal.id if pal else pal_id),
                          "model": (pal.model if pal else None),
                          "model_ref": (pal.model_ref if pal else None),
                          "source": pal_src},
        "editor": {"id": ed_id,
                   "model": (ed.model if ed else None),
                   "model_ref": (ed.model_ref if ed else None),
                   "source": ed_src},
        "prompt_source": prompt_src,
        "prompt": (prompt_txt or ""),
        "encoders": encoders,
        "problems": problems,
        "sidecar": sc,
    }
