from __future__ import annotations

import json
import time
from types import SimpleNamespace

from personal_historical_archive import cli
from personal_historical_archive import db as _db
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import _doc_slug, pending_review_files, sha256_of


def _make_cfg(tmp_path) -> Config:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        f"paths:\n  archive_dir: {root / 'archive'}\n"
        "  dropbox: dropbox\n  inbox: inbox\n  library: library\n  renders: renders\n"
        "  notes: notes\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  models: models\n  prompts: prompts\n  db: archive.db\n"
    )
    return Config.load(root)


def _add_doc_with_page(cfg, page_no=1, exported_at=1000.0):
    src = cfg.dropbox / "collections" / "COLX" / "d.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF d")
    conn = _db.connect(cfg.db_path)
    _db.add_document(conn, filename="d.pdf", path=str(src), sha256=sha256_of(src),
                     size_bytes=1, mtime=1, kind="pdf", now=time.time(),
                     dir_path="collections/COLX")
    doc_id = _db.get_document_by_path(conn, str(src))["id"]
    _db.add_page(conn, doc_id, page_no)
    conn.execute("UPDATE pages SET exported_at=? WHERE document_id=? AND page_no=?",
                 (exported_at, doc_id, page_no))
    conn.commit()
    doc = dict(conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone())
    conn.close()
    return doc_id, doc


def _write_page_file(cfg, doc, page_no, variant="transcription-default", body="original text"):
    d = cfg.library / "collections" / "COLX" / _doc_slug(doc) / variant
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"page-{page_no:03d}.md"
    f.write_text(
        f"---\ndocument_id: {doc['id']}\npage: {page_no}\nfilename: d.pdf\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return f


def test_pending_detects_edited_library_file(tmp_path):
    """A library page file with mtime newer than the page's exported_at is pending."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id, doc = _add_doc_with_page(cfg, page_no=1, exported_at=1000.0)
    _write_page_file(cfg, doc, 1)  # mtime = now > exported_at
    conn = _db.connect(cfg.db_path)
    pend = pending_review_files(cfg, conn)
    conn.close()
    assert len(pend) == 1
    assert pend[0]["document_id"] == doc_id
    assert pend[0]["page_no"] == 1
    assert pend[0]["variant"].startswith("transcription-")


def test_pending_scope_to_one_document(tmp_path):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id, doc = _add_doc_with_page(cfg, page_no=1, exported_at=1000.0)
    _write_page_file(cfg, doc, 1)
    conn = _db.connect(cfg.db_path)
    scoped = pending_review_files(cfg, conn, doc_id=doc_id)
    unknown = pending_review_files(cfg, conn, doc_id=999999)
    conn.close()
    assert len(scoped) == 1 and scoped[0]["page_no"] == 1
    assert unknown == []


def test_cli_pending_json_reports_needed_passes(tmp_path, capsys):
    """A transcription correction needs review -> edit -> reindex."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id, doc = _add_doc_with_page(cfg, page_no=1, exported_at=1000.0)
    _write_page_file(cfg, doc, 1)
    cli.cmd_pending(cfg, SimpleNamespace(doc=doc_id, json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["count"] == 1
    assert data["needs"] == {"review": True, "edit": True, "reindex": True}
    assert data["pending"][0]["page_no"] == 1


def test_cli_pending_edited_variant_does_not_need_edit(tmp_path, capsys):
    """An edited-* correction needs review -> reindex (the editor is not re-run)."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id, doc = _add_doc_with_page(cfg, page_no=2, exported_at=1000.0)
    _write_page_file(cfg, doc, 2, variant="edited-latin-to-english", body="correction")
    cli.cmd_pending(cfg, SimpleNamespace(doc=doc_id, json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["count"] == 1
    assert data["needs"] == {"review": True, "edit": False, "reindex": True}
    assert data["pending"][0]["editor"] == "latin-to-english"


def test_cli_pending_clean_is_reported(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id, doc = _add_doc_with_page(cfg, page_no=1, exported_at=time.time() + 60)
    _write_page_file(cfg, doc, 1)
    cli.cmd_pending(cfg, SimpleNamespace(doc=doc_id, json=False))
    assert "up to date" in capsys.readouterr().out
