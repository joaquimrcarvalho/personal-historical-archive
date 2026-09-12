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


# --- the fast scan must keep the legacy (exported_at NULL) body comparison -----

def _legacy_doc(tmp_path, page_no=1):
    """A document whose page row has no exported_at, so bodies are compared."""
    from personal_historical_archive.extract import format_notes

    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id, doc = _add_doc_with_page(cfg, page_no=page_no, exported_at=None)
    conn = _db.connect(cfg.db_path)
    page = conn.execute("SELECT id FROM pages WHERE document_id=? AND page_no=?",
                        (doc_id, page_no)).fetchone()
    _db.set_page_result(conn, page["id"], raw_text="original text")
    conn.commit()
    return cfg, doc_id, doc, conn, page["id"], format_notes("original text").strip()


def test_pending_legacy_transcription_compares_bodies(tmp_path):
    cfg, doc_id, doc, conn, _pid, body = _legacy_doc(tmp_path)
    f = _write_page_file(cfg, doc, 1, body=body)
    assert pending_review_files(cfg, conn) == []          # identical body -> clean
    assert pending_review_files(cfg, conn, doc_id=doc_id) == []

    f.write_text(f.read_text(encoding="utf-8").replace("original", "hand corrected"),
                 encoding="utf-8")
    pend = pending_review_files(cfg, conn)
    assert len(pend) == 1 and pend[0]["page_no"] == 1 and pend[0]["document_id"] == doc_id
    assert pending_review_files(cfg, conn, doc_id=doc_id) == pend  # scoped agrees
    conn.close()


def test_pending_legacy_edited_compares_bodies(tmp_path):
    cfg, doc_id, doc, conn, pid, _body = _legacy_doc(tmp_path, page_no=2)
    _db.set_page_edit(conn, pid, "latin-to-english", text="edited text")
    conn.commit()
    f = _write_page_file(cfg, doc, 2, variant="edited-latin-to-english", body="edited text")
    assert pending_review_files(cfg, conn) == []

    f.write_text("---\ndocument_id: %d\npage: 2\n---\n\nchanged by hand\n" % doc_id,
                 encoding="utf-8")
    pend = pending_review_files(cfg, conn)
    assert len(pend) == 1 and pend[0]["editor"] == "latin-to-english"
    assert pending_review_files(cfg, conn, doc_id=doc_id) == pend
    conn.close()


# --- `pha review` scope: pending only, --all opt-in, --unset undo ------------

def _review_args(doc=None, page=None, all=False, unset=False):
    return SimpleNamespace(doc=doc, page=page, all=all, unset=unset)


def test_cli_review_only_imports_pending_files(tmp_path, capsys):
    """`pha review` imports the corrected file and leaves the others unstamped."""
    import os

    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id, doc = _add_doc_with_page(cfg, page_no=1, exported_at=None)
    conn = _db.connect(cfg.db_path)
    _db.add_page(conn, doc_id, 2)
    conn.commit()
    conn.close()
    # both files written as pha would export them, then their exported_at is
    # pinned to the write time so neither reads as changed yet
    f1 = _write_page_file(cfg, doc, 1, body="machine text 1")
    f2 = _write_page_file(cfg, doc, 2, body="machine text 2")
    conn = _db.connect(cfg.db_path)
    for n, f in ((1, f1), (2, f2)):
        conn.execute("UPDATE pages SET exported_at=? WHERE document_id=? AND page_no=?",
                     (f.stat().st_mtime, doc_id, n))
    conn.commit()
    conn.close()

    # the historian corrects ONLY page 1 (mtime must move past exported_at)
    time.sleep(0.05)
    f1.write_text(f1.read_text(encoding="utf-8").replace("machine text 1", "HUMAN CORRECTION"),
                  encoding="utf-8")
    os.utime(f1, (f1.stat().st_mtime + 1, f1.stat().st_mtime + 1))

    cli.cmd_review(cfg, _review_args(doc=doc_id))
    out = capsys.readouterr().out
    assert "reviewed: 1 transcription page(s)" in out

    conn = _db.connect(cfg.db_path)
    rows = {r["page_no"]: r for r in conn.execute(
        "SELECT page_no, raw_text, reviewed_at FROM pages WHERE document_id=?", (doc_id,))}
    conn.close()
    assert rows[1]["raw_text"] == "HUMAN CORRECTION"
    assert rows[1]["reviewed_at"] is not None
    assert rows[2]["reviewed_at"] is None          # untouched -> still re-scannable


def test_cli_review_all_is_opt_in(tmp_path, capsys):
    """`--all` is the deliberate blanket review: it stamps uncorrected files too."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id, doc = _add_doc_with_page(cfg, page_no=1, exported_at=1000.0)
    conn = _db.connect(cfg.db_path)
    _db.add_page(conn, doc_id, 2)
    conn.commit()
    conn.close()
    _write_page_file(cfg, doc, 1, body="HUMAN CORRECTION")
    _write_page_file(cfg, doc, 2, body="machine text")

    cli.cmd_review(cfg, _review_args(doc=doc_id, all=True))
    assert "every library file" in capsys.readouterr().out
    conn = _db.connect(cfg.db_path)
    n = conn.execute(
        "SELECT COUNT(*) n FROM pages WHERE document_id=? AND reviewed_at IS NOT NULL",
        (doc_id,)).fetchone()["n"]
    conn.close()
    assert n == 2


def test_cli_review_unset_lifts_the_stamp(tmp_path, capsys):
    """`--unset` clears pages and edits so they can be re-scanned/re-edited."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id, doc = _add_doc_with_page(cfg, page_no=1, exported_at=1000.0)
    _write_page_file(cfg, doc, 1, body="HUMAN CORRECTION")
    cli.cmd_review(cfg, _review_args(doc=doc_id))
    capsys.readouterr()

    conn = _db.connect(cfg.db_path)
    assert conn.execute("SELECT COUNT(*) n FROM pages WHERE reviewed_at IS NOT NULL"
                        ).fetchone()["n"] == 1
    conn.close()

    cli.cmd_review(cfg, _review_args(doc=doc_id, unset=True))
    assert "can be re-scanned/re-edited" in capsys.readouterr().out
    conn = _db.connect(cfg.db_path)
    assert conn.execute("SELECT COUNT(*) n FROM pages WHERE reviewed_at IS NOT NULL"
                        ).fetchone()["n"] == 0
    # the human's text is kept — only the protection is lifted
    row = conn.execute("SELECT raw_text FROM pages WHERE document_id=?", (doc_id,)).fetchone()
    conn.close()
    assert row["raw_text"] == "HUMAN CORRECTION"


def test_cli_review_page_requires_doc(tmp_path, capsys):
    """Refuse `--page` without `--doc` instead of unstamping every page."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    import pytest
    with pytest.raises(SystemExit) as exc:
        cli.cmd_review(cfg, _review_args(page=1, unset=True))
    assert exc.value.code == 2
    assert "--page requires --doc" in capsys.readouterr().err
