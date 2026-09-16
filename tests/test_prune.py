from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli
from personal_historical_archive import db as _db
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import (
    prune_orphan_renders,
    prune_redundant_edited_dirs,
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


# --- `pha prune --library-variants`: the guarded duplicate-folder sweep -------

def _variant_doc(cfg, conn, *, editor="mod", editor_model="m1", db_text="same text"):
    """A document with one edited variant in two folders + a matching edit row."""
    src = cfg.dropbox / "collections" / "COLX" / "d.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF d")
    doc_id = _db.add_document(conn, filename="d.pdf", path=str(src), sha256=sha256_of(src),
                              size_bytes=1, mtime=1, kind="pdf", now=time.time(),
                              dir_path="collections/COLX", editor=editor,
                              editor_model=editor_model)
    pid = _db.add_page(conn, doc_id, 1)
    _db.set_page_result(conn, pid, raw_text="raw text")
    _db.set_page_edit(conn, pid, editor, text=db_text, raw_sha="x")
    conn.commit()
    return doc_id, pid


def _write_variant(cfg, conn, doc_id, dirname, body):
    from personal_historical_archive.ingest import _doc_slug

    doc = dict(conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone())
    d = cfg.library / "collections" / "COLX" / _doc_slug(doc) / dirname
    d.mkdir(parents=True, exist_ok=True)
    f = d / "page-001.md"
    f.write_text(f"---\ndocument_id: {doc_id}\npage: 1\n---\n\n{body}\n", encoding="utf-8")
    return f


def test_prune_library_variants_removes_a_provably_redundant_folder(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    doc_id, _pid = _variant_doc(cfg, conn)
    bare = _write_variant(cfg, conn, doc_id, "edited-mod", "same text")
    qual = _write_variant(cfg, conn, doc_id, "edited-mod@m1", "same text")

    res = prune_redundant_edited_dirs(cfg, conn, verbose=False)
    assert [r["editor"] for r in res["removed"]] == ["mod"]
    assert res["refused"] == []
    assert not bare.parent.exists()          # deleted: it is exactly the DB text
    assert qual.exists()                     # the current variant stays
    conn.close()


def test_prune_library_variants_removes_a_folder_the_sibling_still_holds(tmp_path):
    """A bare folder byte-identical to its `@model` sibling is a duplicate even
    when the DB has moved on from both (the doc-54 case on jesuit-archive): the
    surviving sibling keeps every page, so nothing is lost."""
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    doc_id, _pid = _variant_doc(cfg, conn, db_text="the DB moved on")
    bare = _write_variant(cfg, conn, doc_id, "edited-mod", "an older export")
    qual = _write_variant(cfg, conn, doc_id, "edited-mod@m1", "an older export")

    res = prune_redundant_edited_dirs(cfg, conn, verbose=False)
    assert [r["editor"] for r in res["removed"]] == ["mod"]
    assert res["removed"][0]["why"] == "identical to edited-mod@m1"
    assert not bare.parent.exists()
    assert qual.exists()
    conn.close()


def test_prune_library_variants_keeps_a_different_reading(tmp_path):
    """A bare folder that is NOT the DB text is a different reading (an older
    OCR pass, another translation) — it must never be deleted."""
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    doc_id, _pid = _variant_doc(cfg, conn)
    bare = _write_variant(cfg, conn, doc_id, "edited-mod", "a different reading")
    _write_variant(cfg, conn, doc_id, "edited-mod@m1", "same text")

    res = prune_redundant_edited_dirs(cfg, conn, verbose=False)
    assert res["removed"] == []
    assert len(res["refused"]) == 1 and res["refused"][0]["differing"] == 1
    assert bare.exists()
    conn.close()


def test_prune_library_variants_keeps_a_model_less_current_variant(tmp_path):
    """When the document's editor has no model, the BARE folder is the current
    output and the qualified sibling is the older reading: keep it."""
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    doc_id, _pid = _variant_doc(cfg, conn, editor_model=None)
    bare = _write_variant(cfg, conn, doc_id, "edited-mod", "same text")
    _write_variant(cfg, conn, doc_id, "edited-mod@old", "same text")

    res = prune_redundant_edited_dirs(cfg, conn, verbose=False)
    assert res["removed"] == []
    assert res["refused"][0]["reason"] == "current model-less variant"
    assert bare.exists()
    conn.close()


def test_prune_library_variants_dry_run_deletes_nothing(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    doc_id, _pid = _variant_doc(cfg, conn)
    bare = _write_variant(cfg, conn, doc_id, "edited-mod", "same text")
    _write_variant(cfg, conn, doc_id, "edited-mod@m1", "same text")

    res = prune_redundant_edited_dirs(cfg, conn, dry_run=True, verbose=False)
    assert len(res["removed"]) == 1 and res["bytes"] > 0
    assert bare.exists()  # still there
    conn.close()


def test_prune_library_variants_reports_a_failed_delete(tmp_path, monkeypatch, capsys):
    """A delete that does not happen must never be reported as removed (the first
    run of this sweep did exactly that: `rmtree(ignore_errors=True)` swallowed a
    permission error and still counted 25 folders as gone)."""
    import personal_historical_archive.ingest as ingest

    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    doc_id, _pid = _variant_doc(cfg, conn)
    bare = _write_variant(cfg, conn, doc_id, "edited-mod", "same text")
    _write_variant(cfg, conn, doc_id, "edited-mod@m1", "same text")

    def boom(path, *a, **k):
        raise OSError("Operation not permitted")

    monkeypatch.setattr(ingest.shutil, "rmtree", boom)
    res = prune_redundant_edited_dirs(cfg, conn, verbose=True)
    assert res["removed"] == [] and res["bytes"] == 0
    assert len(res["failed"]) == 1 and "Operation not permitted" in res["failed"][0]["reason"]
    assert bare.exists()
    assert "FAILED" in capsys.readouterr().out

    # and the CLI turns that into a non-zero exit
    conn.close()
    conn = _db.connect(cfg.db_path)
    monkeypatch.setattr(ingest.shutil, "rmtree", boom)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_prune(cfg, SimpleNamespace(dry_run=False, library_variants=True))
    assert exc.value.code == 3
    assert bare.exists()
    conn.close()


def test_cmd_prune_library_variants(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    doc_id, _pid = _variant_doc(cfg, conn)
    bare = _write_variant(cfg, conn, doc_id, "edited-mod", "same text")
    _write_variant(cfg, conn, doc_id, "edited-mod@m1", "same text")
    conn.close()

    cli.cmd_prune(cfg, SimpleNamespace(dry_run=True, library_variants=True))
    assert bare.exists()
    assert "would remove 1 redundant edited folder(s)" in capsys.readouterr().out

    cli.cmd_prune(cfg, SimpleNamespace(dry_run=False, library_variants=True))
    assert not bare.exists()
    assert "removed 1 redundant edited folder(s)" in capsys.readouterr().out


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
