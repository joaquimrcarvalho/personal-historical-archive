from __future__ import annotations

import time
from types import SimpleNamespace

from personal_historical_archive import cli
from personal_historical_archive import db as _db
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import sha256_of


def _make_cfg(tmp_path) -> Config:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        f"paths:\n  archive_dir: {root / 'archive'}\n"
        "  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    return Config.load(root)


def test_status_shows_unscanned_dropbox_files(tmp_path, capsys):
    """pha status reports files that are in the dropbox but not yet in the
    archive (never scanned), before `pha scan` runs."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()

    # one already-scanned document (has a DB record)
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True)
    scanned = col / "scanned.pdf"
    scanned.write_bytes(b"%PDF scanned")
    conn = _db.connect(cfg.db_path)
    _db.add_document(conn, filename="scanned.pdf", path=str(scanned),
                     sha256=sha256_of(scanned), size_bytes=1, mtime=1, kind="pdf",
                     now=time.time(), dir_path="collections/COLX")
    _db.set_document_status(conn, _db.get_document_by_path(conn, str(scanned))["id"], "done")
    conn.commit()
    conn.close()

    # two files dropped in but never scanned (one in the collection, one at root)
    fresh1 = col / "fresh.pdf"
    fresh1.write_bytes(b"%PDF fresh")
    fresh2 = cfg.dropbox / "documents" / "rootdoc.pdf"
    fresh2.parent.mkdir(parents=True, exist_ok=True)
    fresh2.write_bytes(b"%PDF root")

    cli.cmd_status(cfg, SimpleNamespace())
    out = capsys.readouterr().out
    assert "scanned.pdf" in out            # the archived doc is listed
    assert "new in dropbox (not yet scanned — run `pha scan`)" in out
    assert "collections/COLX: 1 file(s)" in out
    assert "fresh.pdf" in out
    assert "documents: 1 file(s)" in out
    assert "rootdoc.pdf" in out


def test_status_no_new_section_when_everything_scanned(tmp_path, capsys):
    """No 'new in dropbox' section when every dropbox file has a record."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True)
    src = col / "doc.pdf"
    src.write_bytes(b"%PDF doc")
    conn = _db.connect(cfg.db_path)
    _db.add_document(conn, filename="doc.pdf", path=str(src), sha256=sha256_of(src),
                     size_bytes=1, mtime=1, kind="pdf", now=time.time(),
                     dir_path="collections/COLX")
    conn.commit()
    conn.close()

    cli.cmd_status(cfg, SimpleNamespace())
    out = capsys.readouterr().out
    assert "new in dropbox" not in out
    assert "doc.pdf" in out
