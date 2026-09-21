from __future__ import annotations

"""`pha review` must not import while a pass is writing library pages.

Measured 2026-09-18: a review run during a scan imported 435 pages when 4 were
real corrections, stamping 431 machine-written pages `reviewed` (which freezes
them until `pha review --unset`). See enhancements/pha-review-scope-bug-report.md
§11.
"""

import time
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli, db as _db, ingest, locks
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import pending_review_files, sha256_of


def _cfg(tmp_path) -> Config:
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  models: models\n  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


def _seed(cfg: Config, *, status: str = "done", text: str = "machine text"):
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    src = col / "d.pdf"
    src.write_bytes(b"%PDF-1.4 d")
    conn = _db.connect(cfg.db_path)
    doc = _db.add_document(conn, filename="d.pdf", path=str(src), sha256=sha256_of(src),
                           size_bytes=1, mtime=1, kind="pdf",
                           dir_path="collections/COLX", now=time.time(),
                           palaeographer="default", palaeographer_model="default")
    pid = _db.add_page(conn, doc, 1)
    _db.set_page_result(conn, pid, raw_text=text)
    _db.set_document_status(conn, doc, status)
    conn.commit()
    lib = cfg.library / "collections" / "COLX" / ingest._doc_slug(
        dict(conn.execute("SELECT * FROM documents WHERE id=?", (doc,)).fetchone()))
    conn.close()
    return src, doc, lib


def _corrected_file(lib, doc: int, body: str = "human corrected text"):
    """A human-edited transcription page, where pha reads it: inside the
    document's `transcription-<pal>` variant directory."""
    d = lib / "transcription-default"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "page-001.md"
    f.write_text(f"---\ndocument_id: {doc}\npage: 1\nfilename: d.pdf\n---\n\n{body}\n",
                 encoding="utf-8")
    return f


def _hold_the_embed_server(cfg, monkeypatch, label: str = "pha scan"):
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
    path = locks._slot_path(locks.embed_key(cfg), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"999999 {label}", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- refusal

def test_review_refuses_while_a_job_is_writing(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _src, doc, lib = _seed(cfg)
    _corrected_file(lib, doc)
    held = _hold_the_embed_server(cfg, monkeypatch)
    conn = _db.connect(cfg.db_path)
    try:
        res = ingest.review_import(cfg, conn, doc_id=doc, verbose=False)
    finally:
        conn.close()
        held.unlink(missing_ok=True)

    assert res["pages"] == 0 and res.get("refused")
    assert "pha scan" in res["refused"] and "--force" in res["refused"]
    # nothing was imported or stamped
    conn = _db.connect(cfg.db_path)
    try:
        row = conn.execute("SELECT raw_text, reviewed_at FROM pages WHERE document_id=?",
                           (doc,)).fetchone()
        assert row["raw_text"] == "machine text"
        assert row["reviewed_at"] is None
    finally:
        conn.close()


def test_review_refuses_a_document_still_processing(tmp_path):
    cfg = _cfg(tmp_path)
    _src, doc, lib = _seed(cfg, status="processing")
    _corrected_file(lib, doc)
    conn = _db.connect(cfg.db_path)
    try:
        res = ingest.review_import(cfg, conn, doc_id=doc, verbose=False)
    finally:
        conn.close()
    assert res["pages"] == 0
    assert "still being processed" in res["refused"]


def test_force_imports_anyway(tmp_path):
    cfg = _cfg(tmp_path)
    _src, doc, lib = _seed(cfg, status="processing")
    _corrected_file(lib, doc)
    conn = _db.connect(cfg.db_path)
    try:
        res = ingest.review_import(cfg, conn, doc_id=doc, verbose=False, force=True)
    finally:
        conn.close()
    assert res["pages"] == 1
    conn = _db.connect(cfg.db_path)
    try:
        row = conn.execute("SELECT raw_text, reviewed_at FROM pages WHERE document_id=?",
                           (doc,)).fetchone()
        assert row["raw_text"] == "human corrected text"
        assert row["reviewed_at"] is not None
    finally:
        conn.close()


def test_whole_archive_review_refuses_while_any_document_is_processing(tmp_path):
    """An unscoped review covers documents mid-pass, whose pages are still being
    written — refuse. A `--doc`-scoped review of a finished document is precise
    enough to proceed."""
    cfg = _cfg(tmp_path)
    _src, doc, lib = _seed(cfg)
    _corrected_file(lib, doc)
    other = cfg.dropbox / "collections" / "COLX" / "e.pdf"
    other.write_bytes(b"%PDF e")
    conn = _db.connect(cfg.db_path)
    try:
        d2 = _db.add_document(conn, filename="e.pdf", path=str(other), sha256="x",
                              size_bytes=1, mtime=1, kind="pdf",
                              dir_path="collections/COLX", now=time.time())
        _db.set_document_status(conn, d2, "processing")
        conn.commit()
        unscoped = ingest.review_import(cfg, conn, verbose=False)
        assert unscoped["pages"] == 0
        assert "still being processed" in unscoped["refused"]
        # scoped to the finished document: allowed
        scoped = ingest.review_import(cfg, conn, doc_id=doc, verbose=False)
    finally:
        conn.close()
    assert scoped["pages"] == 1


# --------------------------------------------------------------------------- CLI

def test_cli_review_exits_2_on_refusal_and_prints_the_reason(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    _src, doc, lib = _seed(cfg)
    _corrected_file(lib, doc)
    held = _hold_the_embed_server(cfg, monkeypatch)
    try:
        with pytest.raises(SystemExit) as e:
            cli.cmd_review(cfg, SimpleNamespace(doc=doc, page=None, all=False,
                                                unset=False, force=False))
        assert e.value.code == 2
    finally:
        held.unlink(missing_ok=True)
    assert "review refused" in capsys.readouterr().err


def test_cli_review_force_succeeds(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    _src, doc, lib = _seed(cfg, status="processing")
    _corrected_file(lib, doc)
    cli.cmd_review(cfg, SimpleNamespace(doc=doc, page=None, all=False,
                                        unset=False, force=True))
    assert "reviewed: 1 transcription page(s)" in capsys.readouterr().out


def test_unset_is_not_gated(tmp_path, capsys):
    """Releasing a stamp is safe (and often urgent) during a pass — only the
    IMPORT is gated."""
    cfg = _cfg(tmp_path)
    _src, doc, lib = _seed(cfg)
    _corrected_file(lib, doc)
    conn = _db.connect(cfg.db_path)
    try:
        ingest.review_import(cfg, conn, doc_id=doc, verbose=False)   # stamp it
    finally:
        conn.close()
    conn = _db.connect(cfg.db_path)
    try:
        _db.set_document_status(conn, doc, "processing")
        conn.commit()
    finally:
        conn.close()
    cli.cmd_review(cfg, SimpleNamespace(doc=doc, page=None, all=False,
                                        unset=True, force=False))
    assert "unreviewed: 1" in capsys.readouterr().out
