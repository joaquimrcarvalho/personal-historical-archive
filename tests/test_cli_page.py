from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli
from personal_historical_archive import db as _db
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import sha256_of


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


def _add_doc_with_page(cfg, page_no=1, editor="latin-to-english"):
    """One document in collections/COLX with a single transcribed page."""
    src = cfg.dropbox / "collections" / "COLX" / "d.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF d")
    conn = _db.connect(cfg.db_path)
    _db.add_document(conn, filename="d.pdf", path=str(src), sha256=sha256_of(src),
                     size_bytes=1, mtime=1, kind="pdf", now=time.time(),
                     dir_path="collections/COLX")
    doc_id = _db.get_document_by_path(conn, str(src))["id"]
    if editor:
        conn.execute("UPDATE documents SET editor=? WHERE id=?", (editor, doc_id))
    page_id = _db.add_page(conn, doc_id, page_no)
    _db.set_page_result(conn, page_id, raw_text="raw transcription body")
    conn.commit()
    conn.close()
    return doc_id, page_id


def _page_args(**kw):
    base = dict(doc=1, page=1, edited=False, editor=None, json=True)
    base.update(kw)
    return SimpleNamespace(**base)


def test_cli_page_raw_json(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id, _ = _add_doc_with_page(cfg)
    cli.cmd_page(cfg, _page_args(doc=doc_id))
    data = json.loads(capsys.readouterr().out)
    assert data["variant"] == "raw"
    assert data["editor"] is None
    assert data["text"] == "raw transcription body"


def test_cli_page_edited_json(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id, page_id = _add_doc_with_page(cfg)
    conn = _db.connect(cfg.db_path)
    _db.set_page_edit(conn, page_id, "latin-to-english", text="translated body")
    conn.commit()
    conn.close()
    cli.cmd_page(cfg, _page_args(doc=doc_id, edited=True))
    data = json.loads(capsys.readouterr().out)
    assert data["variant"] == "edited"
    assert data["editor"] == "latin-to-english"
    assert data["text"] == "translated body"


def test_cli_page_edited_without_edit_names_the_pass(tmp_path, capsys):
    """--edited with no edited text yet must name the pass that produces it.

    The Harness GUI shows this message in the text pane, so it has to say what to
    run — not just that the text is missing.
    """
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id, _ = _add_doc_with_page(cfg)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_page(cfg, _page_args(doc=doc_id, edited=True))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "no edited text for page 1 (editor latin-to-english)" in err
    assert "run: pha edit --path collections/COLX" in err


def test_cli_page_edited_without_editor_points_at_pha_yaml(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id, _ = _add_doc_with_page(cfg, editor=None)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_page(cfg, _page_args(doc=doc_id, edited=True))
    assert exc.value.code == 1
    assert "no editor configured" in capsys.readouterr().err


def test_cli_page_falls_back_to_the_document_editor(tmp_path, capsys):
    """--editor overrides the document's editor; both must resolve the same row."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id, page_id = _add_doc_with_page(cfg)
    conn = _db.connect(cfg.db_path)
    _db.set_page_edit(conn, page_id, "modern-portuguese", text="modernised body")
    conn.commit()
    conn.close()
    cli.cmd_page(cfg, _page_args(doc=doc_id, edited=True, editor="modern-portuguese"))
    data = json.loads(capsys.readouterr().out)
    assert data["editor"] == "modern-portuguese"
    assert data["text"] == "modernised body"


def test_cli_page_unknown_page_exits(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id, _ = _add_doc_with_page(cfg)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_page(cfg, _page_args(doc=doc_id, page=42))
    assert exc.value.code == 1
    assert "has no page 42" in capsys.readouterr().err


def test_cli_page_unknown_document_exits(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    with pytest.raises(SystemExit) as exc:
        cli.cmd_page(cfg, _page_args(doc="nope"))
    assert exc.value.code == 1
    assert "no document matching" in capsys.readouterr().err
