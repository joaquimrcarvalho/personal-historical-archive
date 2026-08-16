from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


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
class Config:
    root: Path
    # paths
    dropbox: Path
    library: Path
    data: Path
    renders: Path
    prompts: Path
    db_path: Path
    # vision model (OpenAI-compatible endpoint)
    vision_backend: str
    vision_base_url: str
    vision_model: str
    vision_temperature: float
    vision_max_tokens: int
    vision_timeout_s: int
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
        vis = raw.get("vision", {})
        emb = raw.get("embeddings", {})
        ext = raw.get("extraction", {})
        sea = raw.get("search", {})
        return cls(
            root=root,
            dropbox=_p(root, paths.get("dropbox", "dropbox")),
            library=_p(root, paths.get("library", "library")),
            data=_p(root, paths.get("data", "data")),
            renders=_p(root, paths.get("renders", "data/renders")),
            prompts=_p(root, paths.get("prompts", "prompts")),
            db_path=_p(root, paths.get("db", "data/archive.db")),
            vision_backend=str(vis.get("backend", "lmstudio")),
            vision_base_url=str(vis.get("base_url", "http://127.0.0.1:1234/v1")),
            vision_model=str(vis.get("model", "qwen/qwen3-vl-8b")),
            vision_temperature=float(vis.get("temperature", 0.1)),
            vision_max_tokens=int(vis.get("max_tokens", 4096)),
            vision_timeout_s=int(vis.get("timeout_s", 900)),
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

    def ensure_dirs(self) -> None:
        for d in (self.dropbox, self.library, self.data, self.renders, self.prompts):
            d.mkdir(parents=True, exist_ok=True)


def _p(root: Path, s: str) -> Path:
    p = Path(s)
    return p if p.is_absolute() else (root / p).resolve()
