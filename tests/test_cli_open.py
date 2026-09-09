from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli
from personal_historical_archive import db as _db
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import _doc_slug, sha256_of


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


def _record_open(monkeypatch) -> list:
    """Replace the OS opener with a recorder; returns the list of opened paths."""
    calls: list = []
    monkeypatch.setattr(cli, "_open_with_os_default", calls.append)
    return calls


def test_open_path_opens_archive_markdown(tmp_path, capsys, monkeypatch):
    """`pha open <path>` opens an existing .md under the archive."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    target = cfg.library / "collections" / "COLX" / "note.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# note\n")

    calls = _record_open(monkeypatch)
    cli.cmd_open(cfg, SimpleNamespace(doc=str(target), page=None, edited=False, editor=None))

    assert calls == [target.resolve()]


def test_open_doc_page_opens_library_file(tmp_path, capsys, monkeypatch):
    """`pha open <doc> <page>` resolves the page (like `pha page`) and opens its
    raw library transcription file."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    src = cfg.dropbox / "collections" / "COLX" / "d.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF d")

    conn = _db.connect(cfg.db_path)
    _db.add_document(conn, filename="d.pdf", path=str(src), sha256=sha256_of(src),
                     size_bytes=1, mtime=1, kind="pdf", now=time.time(),
                     dir_path="collections/COLX")
    doc_id = _db.get_document_by_path(conn, str(src))["id"]
    _db.add_page(conn, doc_id, 1)
    conn.commit()
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    libfile = (cfg.library / "collections" / "COLX" / _doc_slug(dict(doc))
               / "transcription-default" / "page-001.md")
    libfile.parent.mkdir(parents=True, exist_ok=True)
    libfile.write_text("page one")
    conn.close()

    calls = _record_open(monkeypatch)
    cli.cmd_open(cfg, SimpleNamespace(doc=str(doc_id), page=1, edited=False, editor=None))

    assert calls == [libfile.resolve()]


def test_open_path_rejects_outside_archive(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    outside = tmp_path / "elsewhere.md"
    outside.write_text("x")
    with pytest.raises(SystemExit):
        cli.cmd_open(cfg, SimpleNamespace(doc=str(outside), page=None, edited=False, editor=None))
    assert "not inside the archive" in capsys.readouterr().err


def test_open_path_rejects_non_markdown(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    target = cfg.library / "x.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x")
    with pytest.raises(SystemExit):
        cli.cmd_open(cfg, SimpleNamespace(doc=str(target), page=None, edited=False, editor=None))
    assert "markdown" in capsys.readouterr().err


def test_open_path_rejects_missing_file(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    target = cfg.library / "missing.md"
    with pytest.raises(SystemExit):
        cli.cmd_open(cfg, SimpleNamespace(doc=str(target), page=None, edited=False, editor=None))
    assert "no such file" in capsys.readouterr().err


def test_open_unknown_doc_fails(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    _db.connect(cfg.db_path).close()  # empty archive
    with pytest.raises(SystemExit):
        cli.cmd_open(cfg, SimpleNamespace(doc="nope", page=1, edited=False, editor=None))
    assert "no document matching" in capsys.readouterr().err


def test_open_doc_page_no_library_file_fails(tmp_path, capsys):
    """A page known to the DB but with no exported library file gets a clear error."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    src = cfg.dropbox / "a.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF a")
    conn = _db.connect(cfg.db_path)
    _db.add_document(conn, filename="a.pdf", path=str(src), sha256=sha256_of(src),
                     size_bytes=1, mtime=1, kind="pdf", now=time.time(), dir_path="")
    doc_id = _db.get_document_by_path(conn, str(src))["id"]
    _db.add_page(conn, doc_id, 2)
    conn.commit()
    conn.close()
    with pytest.raises(SystemExit):
        cli.cmd_open(cfg, SimpleNamespace(doc=str(doc_id), page=2, edited=False, editor=None))
    assert "no library file on disk" in capsys.readouterr().err


def test_open_opener_failure_is_reported(tmp_path, capsys, monkeypatch):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    target = cfg.library / "x.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x")

    class R:
        returncode = 1
        stderr = "no handler"
        stdout = ""

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(SystemExit):
        cli.cmd_open(cfg, SimpleNamespace(doc=str(target), page=None, edited=False, editor=None))
    assert "could not open" in capsys.readouterr().err


def test_open_builds_opener_argv(tmp_path, monkeypatch):
    """The OS opener is spawned with a bare argv (no shell interpolation)."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    target = cfg.library / "x.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x")
    captured: dict = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    cli._open_with_os_default(target.resolve())
    argv = captured["argv"]
    assert argv[0] in ("open", "xdg-open")  # darwin -> open, linux -> xdg-open
    assert argv[1] == str(target.resolve())
