from __future__ import annotations

"""Search vs. a running model job: `pha search` must not evict the model a scan
is using. The degrade decision is made BEFORE embedding (see locks.py / search.py)."""

import time

import pytest

from personal_historical_archive import db as _db
from personal_historical_archive import locks
from personal_historical_archive.config import Config
from personal_historical_archive.search import search


def _cfg(tmp_path) -> Config:
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  models: models\n  prompts: prompts\n  db: archive.db\n"
        "embeddings:\n  base_url: http://127.0.0.1:1234/v1\n  model: embed-x\n"
    )
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


def _seed_chunk(cfg) -> None:
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    src = col / "d.pdf"
    src.write_bytes(b"%PDF-1.4 d")
    conn = _db.connect(cfg.db_path)
    doc = _db.add_document(conn, filename="d.pdf", path=str(src), sha256="a",
                           size_bytes=1, mtime=1, kind="pdf",
                           dir_path="collections/COLX", now="2026-01-01")
    page = _db.add_page(conn, doc, 1)
    _db.set_page_result(conn, page, raw_text="Malaca e o padroado")
    _db.add_chunk(conn, doc, page, 0, "Malaca e o padroado", None, "raw")
    _db.set_document_status(conn, doc, "done")
    conn.commit()
    conn.close()


class _ExplodingClient:
    """Fails loudly if an embed is attempted (i.e. if the degrade was too late)."""

    def embed(self, model, texts, batch_size=None):
        raise AssertionError("search embedded the query despite a running job")


class _WorkingClient:
    def __init__(self):
        self.calls = 0

    def embed(self, model, texts, batch_size=None):
        self.calls += 1
        return [[0.1, 0.2, 0.3] for _ in texts]


def _hold_embed_server(cfg, monkeypatch, pid: int = 999999, label: str = "pha scan"):
    monkeypatch.setattr(locks, "_pid_alive", lambda p: True)
    path = locks._slot_path(locks.embed_key(cfg), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid} {label}", encoding="utf-8")
    return path


def test_hybrid_skips_the_embed_while_a_job_holds_the_server(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _seed_chunk(cfg)
    path = _hold_embed_server(cfg, monkeypatch)
    try:
        conn = _db.connect(cfg.db_path)
        res = search(conn, _ExplodingClient(), cfg, "Malaca", mode="hybrid")
        conn.close()
        assert res["mode"] == "hybrid"
        assert res["results"], "keyword hits still answer the query"
        assert all(r["source"] == "keyword" for r in res["results"])
        assert "using the embedding server" in res["note"]
        assert "pha scan" in res["note"]
    finally:
        path.unlink(missing_ok=True)


def test_force_embeds_anyway(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _seed_chunk(cfg)
    path = _hold_embed_server(cfg, monkeypatch)
    try:
        conn = _db.connect(cfg.db_path)
        client = _WorkingClient()
        res = search(conn, client, cfg, "Malaca", mode="hybrid", allow_embed=True)
        conn.close()
        assert client.calls == 1, "--force must attempt the embed"
        # nothing is embedded in this index, so the ordinary note is used instead
        assert "using the embedding server" not in (res["note"] or "")
    finally:
        path.unlink(missing_ok=True)


def test_keyword_mode_is_unaffected(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _seed_chunk(cfg)
    path = _hold_embed_server(cfg, monkeypatch)
    try:
        conn = _db.connect(cfg.db_path)
        res = search(conn, _ExplodingClient(), cfg, "Malaca", mode="keyword")
        conn.close()
        assert res["note"] is None
        assert res["results"]
    finally:
        path.unlink(missing_ok=True)


def test_explicit_semantic_reports_the_running_job(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _seed_chunk(cfg)
    path = _hold_embed_server(cfg, monkeypatch)
    try:
        conn = _db.connect(cfg.db_path)
        res = search(conn, _ExplodingClient(), cfg, "Malaca", mode="semantic")
        conn.close()
        assert res["results"] == []
        assert "using the embedding server" in res["note"]
    finally:
        path.unlink(missing_ok=True)


def test_dead_or_stale_lock_does_not_degrade(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _seed_chunk(cfg)
    monkeypatch.setattr(locks, "_pid_alive", lambda p: False)
    path = locks._slot_path(locks.embed_key(cfg), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("999999 pha scan", encoding="utf-8")
    try:
        conn = _db.connect(cfg.db_path)
        client = _WorkingClient()
        search(conn, client, cfg, "Malaca", mode="hybrid")
        conn.close()
        assert client.calls == 1, "a crashed job must not degrade search forever"
    finally:
        path.unlink(missing_ok=True)


def test_stale_pidless_lock_does_not_degrade(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_chunk(cfg)
    path = locks._slot_path(locks.embed_key(cfg), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    old = time.time() - 7 * 3600
    import os
    os.utime(path, (old, old))
    try:
        conn = _db.connect(cfg.db_path)
        client = _WorkingClient()
        search(conn, client, cfg, "Malaca", mode="hybrid")
        conn.close()
        assert client.calls == 1
    finally:
        path.unlink(missing_ok=True)
