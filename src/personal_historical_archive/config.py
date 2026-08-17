from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand(value: str) -> str:
    """Expand ${ENV} and ${ENV:-default} in config strings."""

    def repl(m: re.Match) -> str:
        name, default = m.group(1), m.group(2)
        return os.environ.get(name, default or "")

    return _ENV_RE.sub(repl, value)


def find_project_root(start: Path | None = None) -> Path:
    env = os.environ.get("PHA_HOME")
    if env:
        return Path(env).resolve()
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / "config.yaml").exists():
            return p
    # Fallback: the editable install lives inside the project tree.
    pkg = Path(__file__).resolve().parents[2]
    if (pkg / "config.yaml").exists():
        return pkg
    return cur


@dataclass
class Palaeographer:
    """A named vision model that transcribes documents.

    `prompt_text` is the palaeographer's base prompt; it is prepended BEFORE
    the document/collection sidecar prompt when extracting a page.
    """

    id: str
    description: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    timeout_s: int
    prompt_text: str
    prompt_file: Path | None = None

    @property
    def prompt_source(self) -> str:
        return f"palaeographer:{self.id}"


@dataclass
class Editor:
    """A named text model that transforms the palaeographer's transcription.

    An editor is a completely DIFFERENT model from the palaeographer: it runs
    on its own endpoint (local or remote) and applies an editing prompt
    (modernize spelling, translate, ...) to the per-page transcription text.
    """

    id: str
    description: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    timeout_s: int
    prompt_text: str
    prompt_file: Path | None = None


@dataclass
class Config:
    root: Path
    # paths
    dropbox: Path
    library: Path
    data: Path
    renders: Path
    prompts: Path
    palaeographers_dir: Path
    editors_dir: Path
    db_path: Path
    # palaeographers (vision models)
    palaeographers: dict[str, Palaeographer]
    active_palaeographer: str
    # editors (text models that transform transcriptions)
    editors: dict[str, Editor]
    # embedding model
    embed_backend: str
    embed_base_url: str
    embed_model: str
    embed_timeout_s: int
    # extraction
    render_dpi: int
    max_image_px: int
    jpeg_quality: int
    chunk_chars: int
    chunk_overlap: int
    concurrency: int
    dir_documents: bool
    # search
    default_mode: str
    top_k: int

    @classmethod
    def load(cls, root: Path | None = None) -> "Config":
        root = find_project_root(root)
        cfg_path = root / "config.yaml"
        raw: dict = {}
        if cfg_path.exists():
            raw = yaml.safe_load(cfg_path.read_text()) or {}
        paths = raw.get("paths", {})
        vis = raw.get("vision", {}) or {}
        emb = raw.get("embeddings", {}) or {}
        ext = raw.get("extraction", {}) or {}
        sea = raw.get("search", {}) or {}
        prompts_dir = _p(root, paths.get("prompts", "prompts"))
        pal_dir = _p(root, paths.get("palaeographers", "palaeographers"))
        ed_dir = _p(root, paths.get("editors", "editors"))

        palaeographers, active = _parse_palaeographers(raw, vis, prompts_dir, root, pal_dir)
        editors = _parse_editors(raw, prompts_dir, root, ed_dir)

        return cls(
            root=root,
            dropbox=_p(root, paths.get("dropbox", "dropbox")),
            library=_p(root, paths.get("library", "library")),
            data=_p(root, paths.get("data", "data")),
            renders=_p(root, paths.get("renders", "data/renders")),
            prompts=prompts_dir,
            palaeographers_dir=pal_dir,
            editors_dir=ed_dir,
            db_path=_p(root, paths.get("db", "data/archive.db")),
            palaeographers=palaeographers,
            active_palaeographer=active,
            editors=editors,
            embed_backend=str(emb.get("backend", "lmstudio")),
            embed_base_url=str(emb.get("base_url", "http://127.0.0.1:1234/v1")),
            embed_model=str(emb.get("model", "text-embedding-nomic-embed-text-v1.5@q4_k_m")),
            embed_timeout_s=int(emb.get("timeout_s", 120)),
            render_dpi=int(ext.get("render_dpi", 200)),
            max_image_px=int(ext.get("max_image_px", 1800)),
            jpeg_quality=int(ext.get("jpeg_quality", 88)),
            chunk_chars=int(ext.get("chunk_chars", 2000)),
            chunk_overlap=int(ext.get("chunk_overlap", 200)),
            concurrency=int(ext.get("concurrency", 1)),
            dir_documents=bool(ext.get("dir_documents", True)),
            default_mode=str(sea.get("default_mode", "hybrid")),
            top_k=int(sea.get("top_k", 10)),
        )

    def get_palaeographer(self, pal_id: str | None = None) -> Palaeographer:
        pal_id = pal_id or self.active_palaeographer
        if pal_id not in self.palaeographers:
            raise KeyError(
                f"unknown palaeographer {pal_id!r}; configured: {sorted(self.palaeographers)}"
            )
        return self.palaeographers[pal_id]

    def get_editor(self, editor_id: str) -> Editor:
        if editor_id not in self.editors:
            raise KeyError(f"unknown editor {editor_id!r}; configured: {sorted(self.editors)}")
        return self.editors[editor_id]

    def ensure_dirs(self) -> None:
        for d in (self.dropbox, self.library, self.data, self.renders, self.prompts,
                  self.palaeographers_dir, self.editors_dir):
            d.mkdir(parents=True, exist_ok=True)
        # seed sample configuration files on first run
        _seed_sample(self.palaeographers_dir, "_sample.md", _PAL_SAMPLE)
        _seed_sample(self.editors_dir, "_sample.md", _ED_SAMPLE)


def _parse_palaeographers(
    raw: dict, vis: dict, prompts_dir: Path, root: Path, pal_dir: Path
) -> tuple[dict[str, Palaeographer], str]:
    """Palaeographers are one file per palaeographer in `palaeographers/`
    (YAML front matter = model config, body = the base prompt). Falls back to
    the legacy `palaeographers:` config map / `vision:` block."""
    pals = _load_model_dir(pal_dir, "palaeographer", _palaeographer_from_frontmatter)
    if pals:
        active = str(vis.get("palaeographer", "")) or (next(iter(pals), ""))
        return pals, active

    raw_pals = raw.get("palaeographers")
    if isinstance(raw_pals, dict) and raw_pals:
        pals = {}
        for pal_id, entry in raw_pals.items():
            if not isinstance(entry, dict):
                continue
            pals[str(pal_id)] = _palaeographer_from_entry(
                str(pal_id), entry, prompts_dir, root
            )
        active = str(vis.get("palaeographer", "")) or (next(iter(pals), ""))
        return pals, active

    # legacy: single vision block
    default_file = prompts_dir / "palaeographers" / "default.md"
    prompt_text = ""
    if default_file.exists():
        prompt_text = default_file.read_text()
    pal = Palaeographer(
        id="default",
        description="legacy vision block",
        base_url=str(vis.get("base_url", "http://127.0.0.1:1234/v1")),
        api_key=_expand(str(vis.get("api_key", ""))),
        model=str(vis.get("model", "qwen/qwen3-vl-8b")),
        temperature=float(vis.get("temperature", 0.1)),
        max_tokens=int(vis.get("max_tokens", 4096)),
        timeout_s=int(vis.get("timeout_s", 900)),
        prompt_text=prompt_text,
    )
    return {"default": pal}, "default"


def _parse_editors(raw: dict, prompts_dir: Path, root: Path, ed_dir: Path) -> dict[str, Editor]:
    """Editors are one file per editor in `editors/` (front matter = model
    config, body = the editing prompt). Falls back to the legacy `editors:`
    config map."""
    eds = _load_model_dir(ed_dir, "editor", _editor_from_frontmatter)
    if eds:
        return eds
    raw_eds = raw.get("editors")
    editors: dict[str, Editor] = {}
    if not isinstance(raw_eds, dict):
        return editors
    for ed_id, entry in raw_eds.items():
        if not isinstance(entry, dict):
            continue
        prompt_text = ""
        prompt_file: Path | None = None
        pf = entry.get("prompt_file")
        if pf:
            p = Path(str(pf))
            if not p.is_absolute():
                p = root / p
                if not p.exists():
                    alt = prompts_dir / p
                    if alt.exists():
                        p = alt
            if p.exists():
                prompt_file = p
                prompt_text = p.read_text()
        elif isinstance(entry.get("prompt"), str):
            prompt_text = entry["prompt"]
        editors[str(ed_id)] = Editor(
            id=str(ed_id),
            description=str(entry.get("description", "")),
            base_url=_expand(str(entry.get("base_url", "http://127.0.0.1:1234/v1"))),
            api_key=_expand(str(entry.get("api_key", ""))),
            model=str(entry.get("model", "")),
            temperature=float(entry.get("temperature", 0.1)),
            max_tokens=int(entry.get("max_tokens", 4096)),
            timeout_s=int(entry.get("timeout_s", 300)),
            prompt_text=prompt_text,
            prompt_file=prompt_file,
        )
    return editors


def _palaeographer_from_entry(
    pal_id: str, entry: dict, prompts_dir: Path, root: Path
) -> Palaeographer:
    prompt_text = ""
    prompt_file: Path | None = None
    if entry.get("prompt_file"):
        p = Path(str(entry["prompt_file"]))
        if not p.is_absolute():
            p = root / p  # config paths are relative to the project root
            if not p.exists():
                alt = prompts_dir / Path(str(entry["prompt_file"]))
                if alt.exists():
                    p = alt
        if p.exists():
            prompt_file = p
            prompt_text = p.read_text()
    elif isinstance(entry.get("prompt"), str):
        prompt_text = entry["prompt"]
    return Palaeographer(
        id=pal_id,
        description=str(entry.get("description", "")),
        base_url=_expand(str(entry.get("base_url", "http://127.0.0.1:1234/v1"))),
        api_key=_expand(str(entry.get("api_key", ""))),
        model=str(entry.get("model", "")),
        temperature=float(entry.get("temperature", 0.1)),
        max_tokens=int(entry.get("max_tokens", 4096)),
        timeout_s=int(entry.get("timeout_s", 900)),
        prompt_text=prompt_text,
        prompt_file=prompt_file,
    )


# --------------------------------------------------------------------------- file-based model configs

def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split '--- yaml ---' front matter from the prompt body."""
    fm: dict = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            try:
                fm = yaml.safe_load(text[3:end].strip()) or {}
                if not isinstance(fm, dict):
                    fm = {}
            except yaml.YAMLError:
                fm = {}
            body = text[end + 4 :].strip()
    return fm, body


def _load_model_dir(directory: Path, kind: str, builder) -> dict:
    """Load one config per file from a directory. File stem = id; files whose
    name starts with '_' or '.' (samples, hidden) are ignored; malformed files
    are skipped with a warning so a typo never breaks the whole load."""
    models: dict = {}
    if not directory.is_dir():
        return models
    for f in sorted(directory.iterdir()):
        if not f.is_file() or f.name.startswith(("_", ".")):
            continue
        if f.suffix.lower() not in (".md", ".txt"):
            continue
        try:
            model = builder(f.stem, f.read_text(), f)
        except Exception as e:  # noqa: BLE001 - a bad file must not kill the load
            print(f"warning: invalid {kind} file {f}: {e}")
            continue
        if model is not None:
            models[f.stem] = model
    return models


def _palaeographer_from_frontmatter(pal_id: str, text: str, file: Path) -> Palaeographer | None:
    fm, body = _split_frontmatter(text)
    return Palaeographer(
        id=pal_id,
        description=str(fm.get("description", "")),
        base_url=_expand(str(fm.get("base_url", "http://127.0.0.1:1234/v1"))),
        api_key=_expand(str(fm.get("api_key", ""))),
        model=str(fm.get("model", "")),
        temperature=float(fm.get("temperature", 0.1)),
        max_tokens=int(fm.get("max_tokens", 4096)),
        timeout_s=int(fm.get("timeout_s", 900)),
        prompt_text=body,
        prompt_file=file,
    )


def _editor_from_frontmatter(ed_id: str, text: str, file: Path) -> Editor | None:
    fm, body = _split_frontmatter(text)
    return Editor(
        id=ed_id,
        description=str(fm.get("description", "")),
        base_url=_expand(str(fm.get("base_url", "http://127.0.0.1:1234/v1"))),
        api_key=_expand(str(fm.get("api_key", ""))),
        model=str(fm.get("model", "")),
        temperature=float(fm.get("temperature", 0.1)),
        max_tokens=int(fm.get("max_tokens", 4096)),
        timeout_s=int(fm.get("timeout_s", 300)),
        prompt_text=body,
        prompt_file=file,
    )


def _p(root: Path, s: str) -> Path:
    p = Path(s)
    return p if p.is_absolute() else (root / p).resolve()


def _seed_sample(directory: Path, name: str, content: str) -> None:
    sample = directory / name
    if not sample.exists():
        sample.write_text(content)


_PAL_SAMPLE = """---
# HOW TO CREATE A NEW PALAEOGRAPHER
#   1. Duplicate this file and give it a new name (the file name, without the
#      extension, becomes the palaeographer's id, e.g. "my-hand.md").
#   2. Edit the settings below: endpoint, model, api key, temperature.
#   3. Replace this body with the instructions you want the vision model to
#      follow when transcribing (your palaeographic expertise).
#   4. Save — the palaeographer is ready. Select it per document/collection
#      with a 'palaeographer' file next to the document.
# Files starting with '_' are ignored (this sample is never loaded).
description: example palaeographer — edit me
base_url: http://127.0.0.1:1234/v1
model: qwen/qwen3-vl-8b
api_key: ""
temperature: 0.1
max_tokens: 4096
timeout_s: 900
---

You are a palaeographer specialised in Western European manuscripts of the
15th–19th centuries. Transcribe the page faithfully; mark [illegible] parts;
add a "## Notes" section in English (Language, Script, Date clues,
### Named entities as one bullet per entity, ### Content summary). This is
one page of a multi-page document — do not comment on completeness.
"""

_ED_SAMPLE = """---
# HOW TO CREATE A NEW EDITOR
#   1. Duplicate this file and give it a new name (the file name, without the
#      extension, becomes the editor's id, e.g. "translate-english.md").
#   2. Edit the settings below. The editor is usually a TEXT model — it can be
#      a completely different model/server than the palaeographer.
#   3. Replace this body with your editing instructions (e.g. convert to
#      modern Portuguese, translate to English, normalize names).
#   4. Save — the editor is ready. Select it per document/collection with an
#      'editor' file next to the document.
# Files starting with '_' are ignored (this sample is never loaded).
description: example editor — edit me
base_url: http://127.0.0.1:1234/v1
model: amalia-9b-0626-dpo
api_key: ""
temperature: 0.0
max_tokens: 4096
timeout_s: 300
---

You are a scholarly editor. Transform the transcription as requested by these
instructions. Keep the content faithful: do not add, remove or reorder
information. Keep the document structure. Output only the edited text.
"""
