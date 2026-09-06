from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from personal_historical_archive import cli
from personal_historical_archive import db as _db
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import (
    prune_orphan_renders,
    remove_render_dir,
    remove_render_if_orphaned,
    sha256_of,
)


def _make_cfg(tmp_path: Path, name: str = "proj") -> Config:
    root = tmp_path / name
    root.mkdir()
    (root / "config.yaml").write_text(
        f"paths:\n  archive_dir: {root / 'archive'}\n"
        "  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


def _renders(cfg: Config, *shas: str) -> None:
    """Create a renders/<sha> folder for each sha with a placeholder JPEG."""
    for sha in shas:
        d = cfg.renders / sha
        d.mkdir(parents=True, exist_ok=True)
        (d / "p001.jpg").write_bytes(b"jpeg")


def test_remove_render_dir_missing_is_noop(tmp_path):
    cfg = _make_cfg(tmp_path)
    assert remove_render_dir(cfg, "no/such") is False
    assert remove_render_dir(cfg, "") is False


def test_remove_render_dir_deletes_folder(tmp_path):
    cfg = _make_cfg(tmp_path)
    _renders(cfg, "abc123")
    assert (cfg.renders / "abc123").exists()
    assert remove_render_dir(cfg, "abc123") is True
    assert not (cfg.renders / "abc123").exists()


def test_remove_render_if_orphaned_keeps_shared_sha(tmp_path):
    """A render folder shared by two identical-content documents must survive
    dropping one document's row, and only go when the last one is gone."""
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    _renders(cfg, "same")
    a = cfg.dropbox / "documents" / "a.pdf"
    a.parent.mkdir(parents=True, exist_ok=True)
    a.write_bytes(b"%PDF a")
    b = cfg.dropbox / "documents" / "b.pdf"
    b.write_bytes(b"%PDF b")
    id_a = _db.add_document(conn, filename="a.pdf", path=str(a), sha256="same",
                            size_bytes=1, mtime=1, kind="pdf", now=time.time(),
                            dir_path="documents")
    id_b = _db.add_document(conn, filename="b.pdf", path=str(b), sha256="same",
                            size_bytes=1, mtime=1, kind="pdf", now=time.time(),
                            dir_path="documents")
    conn.commit()

    # both docs reference "same" -> still referenced, not removed
    assert remove_render_if_orphaned(cfg, conn, "same") is False
    assert (cfg.renders / "same").exists()

    # drop one document -> still referenced by the other -> kept
    _db.delete_document(conn, id_a)
    conn.commit()
    assert remove_render_if_orphaned(cfg, conn, "same") is False
    assert (cfg.renders / "same").exists()

    # drop the last document -> orphaned -> removed
    _db.delete_document(conn, id_b)
    conn.commit()
    assert remove_render_if_orphaned(cfg, conn, "same") is True
    assert not (cfg.renders / "same").exists()
    conn.close()


def test_remove_render_if_orphaned_without_reference(tmp_path):
    """No document references the sha -> the folder is removed."""
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    _renders(cfg, "orphan")
    conn.commit()
    assert remove_render_if_orphaned(cfg, conn, "orphan") is True
    assert not (cfg.renders / "orphan").exists()
    conn.close()


def test_prune_removes_only_orphaned_folders(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    _renders(cfg, "live", "stale1", "stale2")
    # a document that references the "live" sha
    src = cfg.dropbox / "documents" / "doc.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF doc")
    _db.add_document(conn, filename="doc.pdf", path=str(src), sha256="live",
                     size_bytes=1, mtime=1, kind="pdf", now=time.time(),
                     dir_path="documents")
    conn.commit()

    n = prune_orphan_renders(cfg, conn, verbose=False)
    assert n == 2
    assert (cfg.renders / "live").exists()
    assert not (cfg.renders / "stale1").exists()
    assert not (cfg.renders / "stale2").exists()
    conn.close()


def test_prune_dry_run_reports_without_deleting(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    _renders(cfg, "stale")
    conn.commit()
    n = prune_orphan_renders(cfg, conn, dry_run=True, verbose=False)
    assert n == 1
    assert (cfg.renders / "stale").exists()  # still there
    conn.close()


def test_prune_empty_renders_dir(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    cfg.renders.mkdir(parents=True, exist_ok=True)
    conn.commit()
    assert prune_orphan_renders(cfg, conn, verbose=False) == 0
    conn.close()


def test_prune_missing_renders_dir(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    conn.commit()
    # renders dir never created
    assert prune_orphan_renders(cfg, conn, verbose=False) == 0
    conn.close()


def test_cmd_prune_removes_orphans(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    _renders(cfg, "stale")
    conn.close()
    cli.cmd_prune(cfg, SimpleNamespace(dry_run=False))
    assert not (cfg.renders / "stale").exists()
    assert "removed 1 orphaned render folder(s)" in capsys.readouterr().out


def test_cmd_prune_dry_run(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    _renders(cfg, "stale")
    conn.close()
    cli.cmd_prune(cfg, SimpleNamespace(dry_run=True))
    assert (cfg.renders / "stale").exists()
    assert "would remove 1 orphaned render folder(s)" in capsys.readouterr().out


def test_ingest_content_change_removes_old_render_dir(tmp_path, monkeypatch):
    """Re-ingesting a changed document (new content sha) drops the render folder
    for the superseded version, as long as no other document shares it."""
    from personal_historical_archive import config as cconfig
    from personal_historical_archive.ingest import ingest_file

    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)

    src = cfg.dropbox / "documents" / "doc.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    v1 = b"%PDF-1.4 version one"
    v2 = b"%PDF-1.4 version two changed"
    src.write_bytes(v1)
    old_sha = sha256_of(src)

    # the "first ingest" artifacts: a DB row + the render folder keyed by it
    _db.add_document(conn, filename="doc.pdf", path=str(src), sha256=old_sha,
                     size_bytes=len(v1), mtime=1, kind="pdf", now=time.time(),
                     dir_path="documents")
    conn.commit()
    _renders(cfg, old_sha)

    pal = cconfig.Palaeographer(
        id="default", description="", base_url="", api_key="", model="",
        temperature=0.1, max_tokens=4096, timeout_s=600, prompt_text="Transcribe.",
    )

    # avoid the real model, renderer, editor, and embed server
    monkeypatch.setattr("personal_historical_archive.ingest.page_count", lambda path: 1)

    def _fake_render(path, out_dir, dpi, max_px, q, prefix=None, pages=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "p001.jpg").write_bytes(b"jpeg")
        return [out_dir / "p001.jpg"]
    monkeypatch.setattr("personal_historical_archive.ingest.render_document", _fake_render)
    monkeypatch.setattr("personal_historical_archive.ingest.transcribe_page", lambda *a, **k: "TEXT")
    monkeypatch.setattr("personal_historical_archive.ingest.edit_document", lambda *a, **k: None)
    monkeypatch.setattr("personal_historical_archive.ingest.index_document", lambda *a, **k: 0)

    # change the file on disk -> a NEW content sha, then ingest again
    src.write_bytes(v2)
    res = ingest_file(cfg, conn, object(), src, pal, verbose=False)
    assert res.get("action") == "ingested", res

    # the old (superseded) render folder is gone; a new one for the new sha exists
    assert not (cfg.renders / old_sha).exists()
    assert (cfg.renders / sha256_of(src)).exists(), res
    conn.close()
