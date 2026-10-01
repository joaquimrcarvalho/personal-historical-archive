"""`pha export` — regenerate library files from the DB, and its scope flags.

The scoping matters beyond convenience: a blanket export rewrites every
document's library files, so it cannot be used to repair ONE document without
touching (and overwriting) un-imported human corrections elsewhere. See
`enhancements/pha-library-slug-timezone-bug-report.md`.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli
from personal_historical_archive import db as _db
from personal_historical_archive.config import Config


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


def _add_doc(cfg: Config, conn, name: str, *, pages: int = 2,
             editor: str | None = None) -> int:
    """A real file in the dropbox + its row + `pages` transcribed pages."""
    src = cfg.dropbox / "collections" / "COLX" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF-1.4 fake")
    doc_id = _db.add_document(
        conn, filename=name, path=str(src), sha256=f"sha-{name}", size_bytes=11,
        mtime=1, kind="pdf", now=time.time(), dir_path="collections/COLX",
        palaeographer="default", palaeographer_model="test-model",
        editor=editor, editor_model="test-model" if editor else None,
    )
    for n in range(1, pages + 1):
        pid = _db.add_page(conn, doc_id, n)
        _db.set_page_result(conn, pid, raw_text=f"raw {name} page {n}")
        if editor:
            _db.set_page_edit(conn, pid, editor, text=f"edited {name} page {n}",
                              raw_sha="x")
    conn.commit()
    return doc_id


def _export(cfg: Config, *, doc=None, path=None):
    return cli.cmd_export(cfg, SimpleNamespace(doc=doc, path=path))


def test_export_doc_writes_only_that_document(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    try:
        a = _add_doc(cfg, conn, "a.pdf", editor="fixer")
        b = _add_doc(cfg, conn, "b.pdf")
    finally:
        conn.close()

    _export(cfg, doc=a)

    a_dirs = sorted(p.name for p in (cfg.library / "collections/COLX").iterdir())
    # one folder for the document, holding both variants
    assert len(a_dirs) == 1 and a_dirs[0].startswith("a_")
    versions = sorted(p.name for p in (cfg.library / "collections/COLX" / a_dirs[0]).iterdir())
    assert versions == ["edited-fixer@test-model", "transcription-default@test-model"]
    assert len(list((cfg.library / "collections/COLX" / a_dirs[0] /
                     "transcription-default@test-model").glob("*.md"))) == 2
    # b was not exported: no folder at all, not an empty one
    assert not any(p.name.startswith("b_") for p in (cfg.library / "collections/COLX").iterdir())


def test_export_path_scopes_to_the_collection(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    try:
        _add_doc(cfg, conn, "a.pdf")
        _add_doc(cfg, conn, "b.pdf")
    finally:
        conn.close()

    _export(cfg, path="collections/COLX")

    names = sorted(p.name for p in (cfg.library / "collections/COLX").iterdir())
    assert len(names) == 2
    assert all(n.endswith(("_a", "_b")) or n.startswith(("a_", "b_")) for n in names)


def test_export_regenerates_a_deleted_folder(tmp_path, capsys):
    """The repair path: drop the folder, export, get the same page text back."""
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    try:
        a = _add_doc(cfg, conn, "a.pdf")
    finally:
        conn.close()
    _export(cfg, doc=a)
    doc_dir = next((cfg.library / "collections/COLX").iterdir())

    import shutil
    shutil.rmtree(doc_dir)
    assert not doc_dir.exists()
    capsys.readouterr()

    _export(cfg, doc=a)

    page = doc_dir / "transcription-default@test-model" / "page-001.md"
    assert page.is_file()
    assert "raw a.pdf page 1" in page.read_text(encoding="utf-8")
    assert str(doc_dir) in capsys.readouterr().out  # the folder is named back


def test_export_doc_and_path_are_mutually_exclusive(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _export(cfg, doc=1, path="collections/COLX")
    assert exc.value.code == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_export_unknown_doc_exits_2(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _export(cfg, doc=999)
    assert exc.value.code == 2
    assert "no document #999" in capsys.readouterr().err


def test_export_unknown_path_exits_2(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _export(cfg, path="collections/NOPE")
    assert exc.value.code == 2
    assert "target path does not exist" in capsys.readouterr().out


def test_export_path_with_no_ingested_documents_exits_2(tmp_path, capsys):
    """The path exists but nothing under it is in the DB — a different message
    from a mistyped path, and still a refusal rather than a silent no-op."""
    cfg = _make_cfg(tmp_path)
    (cfg.dropbox / "collections" / "EMPTY").mkdir(parents=True)
    with pytest.raises(SystemExit) as exc:
        _export(cfg, path="collections/EMPTY")
    assert exc.value.code == 2
    assert "no ingested document under" in capsys.readouterr().err
