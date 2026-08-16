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
    env = os.environ.get("MA_HOME")
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

    @property
    def prompt_source(self) -> str:
        return f"palaeographer:{self.id}"


@dataclass
class Config:
    root: Path
    # paths
    dropbox: Path
    library: Path
    data: Path
    renders: Path
    prompts: Path
    db_path: Path
    # palaeographers (vision models)
    palaeographers: dict[str, Palaeographer]
    active_palaeographer: str
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

        palaeographers, active = _parse_palaeographers(raw, vis, prompts_dir, root)

        return cls(
            root=root,
            dropbox=_p(root, paths.get("dropbox", "dropbox")),
            library=_p(root, paths.get("library", "library")),
            data=_p(root, paths.get("data", "data")),
            renders=_p(root, paths.get("renders", "data/renders")),
            prompts=prompts_dir,
            db_path=_p(root, paths.get("db", "data/archive.db")),
            palaeographers=palaeographers,
            active_palaeographer=active,
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

    def ensure_dirs(self) -> None:
        for d in (self.dropbox, self.library, self.data, self.renders, self.prompts):
            d.mkdir(parents=True, exist_ok=True)


def _parse_palaeographers(
    raw: dict, vis: dict, prompts_dir: Path, root: Path
) -> tuple[dict[str, Palaeographer], str]:
    """Parse the `palaeographers:` map; fall back to the legacy `vision:` block."""
    raw_pals = raw.get("palaeographers")
    if isinstance(raw_pals, dict) and raw_pals:
        pals: dict[str, Palaeographer] = {}
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


def _palaeographer_from_entry(
    pal_id: str, entry: dict, prompts_dir: Path, root: Path
) -> Palaeographer:
    prompt_text = ""
    if entry.get("prompt_file"):
        p = Path(str(entry["prompt_file"]))
        if not p.is_absolute():
            p = root / p  # config paths are relative to the project root
            if not p.exists():
                alt = prompts_dir / Path(str(entry["prompt_file"]))
                if alt.exists():
                    p = alt
        if p.exists():
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
    )


def _p(root: Path, s: str) -> Path:
    p = Path(s)
    return p if p.is_absolute() else (root / p).resolve()
