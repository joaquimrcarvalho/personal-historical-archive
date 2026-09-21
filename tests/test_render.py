"""Page-image rendering — the cache a hand-over does NOT carry.

`renders/<sha>/` is a derived cache keyed by the source content hash. The
hand-out ships none (the worker re-renders to extract) and the return leg
carries none either, so a document this archive NEVER scanned has no images when
its text comes back. `render_document_pages` (and `pha render`) rebuild them
locally from the source, with the sidecar's settings — no transfer, no model.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pymupdf

from personal_historical_archive import cli
from personal_historical_archive import db as _db
from personal_historical_archive.ingest import render_document_pages, sha256_of


def _real_pdf(path: Path, pages: int = 3) -> None:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()


def _doc(cfg, pages: int = 3, filename: str = "v.pdf") -> int:
    src = cfg.dropbox / "collections" / "COLX" / filename
    src.parent.mkdir(parents=True, exist_ok=True)
    _real_pdf(src, pages)
    conn = _db.connect(cfg.db_path)
    try:
        doc_id = _db.add_document(conn, filename=filename, path=str(src),
                                  sha256=sha256_of(src), size_bytes=src.stat().st_size,
                                  mtime=src.stat().st_mtime, kind="pdf", now=time.time(),
                                  dir_path="collections/COLX")
        _db.update_document(conn, doc_id, page_count=pages)
        conn.commit()
    finally:
        conn.close()
    return doc_id


def test_render_document_pages_builds_the_missing_cache(cfg):
    doc_id = _doc(cfg, pages=3)
    conn = _db.connect(cfg.db_path)
    try:
        sha = _db.get_document(conn, doc_id)["sha256"]
        assert not (cfg.renders / sha).exists(), "the fixture must start with no renders"
        res = render_document_pages(cfg, conn, doc_id, verbose=False)
        assert res["action"] == "rendered" and res["images"] == 3
        assert sorted(p.name for p in (cfg.renders / sha).iterdir()) == \
            ["p001.jpg", "p002.jpg", "p003.jpg"]
    finally:
        conn.close()


def test_render_document_pages_is_idempotent(cfg):
    """A complete cache is left alone, so calling this after every fetch is free."""
    doc_id = _doc(cfg, pages=2)
    conn = _db.connect(cfg.db_path)
    try:
        render_document_pages(cfg, conn, doc_id, verbose=False)
        again = render_document_pages(cfg, conn, doc_id, verbose=False)
        assert again["action"] == "skipped" and again["reason"] == "already rendered"
    finally:
        conn.close()


def test_render_document_pages_reports_an_unreadable_source(cfg):
    """A bad source is REPORTED, never raised: `fetch` has already applied the
    text, and losing that to a render would be far worse than missing pictures."""
    src = cfg.dropbox / "collections" / "COLX" / "bad.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF-1.4 not really a pdf")
    conn = _db.connect(cfg.db_path)
    try:
        doc_id = _db.add_document(conn, filename="bad.pdf", path=str(src),
                                  sha256=sha256_of(src), size_bytes=1, mtime=1,
                                  kind="pdf", now=time.time(), dir_path="collections/COLX")
        _db.update_document(conn, doc_id, page_count=2)
        conn.commit()
        res = render_document_pages(cfg, conn, doc_id, verbose=False)
        assert res["action"] == "error" and res["error"]
    finally:
        conn.close()


def test_cmd_render_dry_run_then_real(cfg, capsys):
    _doc(cfg, pages=2)
    cli.cmd_render(cfg, SimpleNamespace(path=None, doc=None, dry_run=True))
    assert "would render: 1 document(s)" in capsys.readouterr().out

    cli.cmd_render(cfg, SimpleNamespace(path=None, doc=None, dry_run=False))
    assert "rendered: 1 document(s)" in capsys.readouterr().out

    cli.cmd_render(cfg, SimpleNamespace(path=None, doc=None, dry_run=False))
    assert "already complete" in capsys.readouterr().out
