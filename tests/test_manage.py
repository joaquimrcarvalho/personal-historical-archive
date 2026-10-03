"""Tests for document lifecycle management: bin/purge/restore and move."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from personal_historical_archive import db, manage
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import _doc_slug, sha256_of


def _make_cfg(tmp_path: Path) -> Config:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        f"paths:\n  archive_dir: {root / 'archive'}\n"
        "  dropbox: dropbox\n  library: library\n  renders: renders\n  bin: bin\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


def _lib_dir(cfg: Config, conn, doc_id: int, variant: str = "transcription-default") -> Path:
    doc = db.get_document(conn, doc_id)
    return cfg.library / (doc["dir_path"] or "") / _doc_slug(doc) / variant


def _add_doc(cfg: Config, conn, *, filename: str = "vol.pdf",
             dir_path: str = "collections/COLX", pages: int = 2, sha: str | None = None,
             sidecar: bool = False, library: bool = True, palaeographer: str = "default",
             editor: str | None = None) -> int:
    """A dropbox document + DB row + pages + (optionally) a library folder."""
    src = cfg.dropbox / dir_path / filename
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF " + filename.encode())
    doc_id = db.add_document(
        conn, filename=filename, path=str(src), sha256=sha or sha256_of(src),
        size_bytes=src.stat().st_size, mtime=src.stat().st_mtime, kind="pdf",
        now=time.time(), dir_path=dir_path, palaeographer=palaeographer, editor=editor,
    )
    for p in range(1, pages + 1):
        pid = db.add_page(conn, doc_id, p)
        db.set_page_result(conn, pid, raw_text=f"page {p}")
        db.add_chunk(conn, doc_id, pid, 0, f"text {p}", None)
    conn.commit()
    if library:
        vd = _lib_dir(cfg, conn, doc_id)
        vd.mkdir(parents=True, exist_ok=True)
        (vd / "page-001.md").write_text("---\npage: 1\n---\n\nhello\n", encoding="utf-8")
    if sidecar:
        base = src if src.is_dir() else src.parent
        stem = src.name if src.is_dir() else src.stem
        (base / f"{stem}.dc.json").write_text('{"title": "x"}', encoding="utf-8")
    return doc_id


def _render(cfg: Config, sha: str) -> Path:
    d = cfg.renders / sha
    d.mkdir(parents=True, exist_ok=True)
    (d / "p001.jpg").write_bytes(b"jpeg")
    return d


def _batch(cfg: Config) -> Path:
    batches = manage.list_batches(cfg)
    assert len(batches) == 1
    return Path(batches[0]["bin_dir"])


# --------------------------------------------------------------------- removal

def test_bin_moves_source_and_library_and_clears_index(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn, sidecar=True)
    doc = db.get_document(conn, doc_id)
    src = Path(doc["path"])
    lib = _lib_dir(cfg, conn, doc_id)
    _render(cfg, doc["sha256"])

    res = manage.bin_documents(cfg, conn, [doc])

    assert not src.exists()
    assert not lib.exists()
    assert not (cfg.renders / doc["sha256"]).exists()
    batch = Path(res["bin_dir"])
    assert (batch / "dropbox" / "collections/COLX/vol.pdf").is_file()
    assert (batch / "dropbox" / "collections/COLX/vol.dc.json").is_file()
    assert (batch / "library" / (lib.relative_to(cfg.library))).is_dir()
    manifest = json.loads((batch / "manifest.json").read_text())
    assert [d["id"] for d in manifest["documents"]] == [doc_id]
    assert manifest["documents"][0]["sidecars"] == ["collections/COLX/vol.dc.json"]
    # index really cleared
    assert db.get_document(conn, doc_id) is None
    assert conn.execute("SELECT COUNT(*) FROM pages WHERE document_id=?", (doc_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM chunks WHERE document_id=?", (doc_id,)).fetchone()[0] == 0
    conn.close()


def test_bin_dry_run_touches_nothing(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn)
    doc = db.get_document(conn, doc_id)

    res = manage.bin_documents(cfg, conn, [doc], dry_run=True)

    assert res["dry_run"] is True
    assert Path(doc["path"]).exists()
    assert _lib_dir(cfg, conn, doc_id).exists()
    assert db.get_document(conn, doc_id) is not None
    assert not list(cfg.bin.glob("*/manifest.json"))
    conn.close()


def test_bin_keeps_render_shared_by_two_documents(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    a = _add_doc(cfg, conn, filename="a.pdf", sha="same")
    b = _add_doc(cfg, conn, filename="b.pdf", sha="same")
    _render(cfg, "same")

    manage.bin_documents(cfg, conn, [db.get_document(conn, a)])
    assert (cfg.renders / "same").exists()

    manage.bin_documents(cfg, conn, [db.get_document(conn, b)])
    assert not (cfg.renders / "same").exists()
    conn.close()


def test_purge_deletes_files_and_index(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn, sidecar=True)
    doc = db.get_document(conn, doc_id)
    src = Path(doc["path"])
    lib = _lib_dir(cfg, conn, doc_id)

    res = manage.purge_documents(cfg, conn, [doc], force=True)

    assert res["mode"] == "purge"
    assert not src.exists()
    assert not lib.exists()
    assert db.get_document(conn, doc_id) is None
    assert not list(cfg.bin.glob("*/manifest.json"))  # nothing binned
    conn.close()


def test_purge_refuses_pending_corrections_unless_forced(tmp_path, monkeypatch):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn)
    doc = db.get_document(conn, doc_id)
    monkeypatch.setattr(manage, "pending_review_count", lambda *a, **k: 1)

    with pytest.raises(manage.ManageError, match="unimported correction"):
        manage.purge_documents(cfg, conn, [doc])
    assert Path(doc["path"]).exists()

    manage.purge_documents(cfg, conn, [doc], force=True)
    assert not Path(doc["path"]).exists()
    conn.close()


def test_unregister_keeps_files(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn)
    doc = db.get_document(conn, doc_id)
    lib = _lib_dir(cfg, conn, doc_id)

    res = manage.unregister_documents(cfg, conn, [doc])

    assert res["mode"] == "keep-files"
    assert Path(doc["path"]).exists()
    assert lib.exists()
    assert db.get_document(conn, doc_id) is None
    conn.close()


def test_documents_matching_by_id_and_substring(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    a = _add_doc(cfg, conn, filename="alpha-1576.pdf")
    b = _add_doc(cfg, conn, filename="beta-1576.pdf")
    c = _add_doc(cfg, conn, filename="gamma.pdf")
    assert [d["id"] for d in manage.documents_matching(conn, str(a))] == [a]
    # "1576" is all digits -> treated as an id, NOT a substring of the filename
    assert manage.documents_matching(conn, "1576") == []
    assert sorted(d["id"] for d in manage.documents_matching(conn, "alpha")) == [a]
    assert sorted(d["id"] for d in manage.documents_matching(conn, "beta-1576")) == [b]
    assert [d["id"] for d in manage.documents_matching(conn, "gamma")] == [c]
    conn.close()


# ------------------------------------------------------------------- bin tooling

def test_restore_puts_files_back_and_prints_rescan(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn, sidecar=True)
    doc = db.get_document(conn, doc_id)
    src = Path(doc["path"])
    lib = _lib_dir(cfg, conn, doc_id)
    manage.bin_documents(cfg, conn, [doc])
    batch = _batch(cfg)

    res = manage.restore_batch(cfg, conn, batch)

    assert src.is_file()
    assert (src.parent / "vol.dc.json").is_file()
    assert lib.is_dir()
    assert db.get_document(conn, doc_id) is None       # index is not rebuilt
    assert res["rescan"] == [f"pha scan --path collections/COLX/vol.pdf"]
    assert not batch.exists()
    conn.close()


def test_restore_refuses_occupied_destination(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn)
    doc = db.get_document(conn, doc_id)
    src = Path(doc["path"])
    manage.bin_documents(cfg, conn, [doc])
    src.write_bytes(b"a different file appeared")
    batch = _batch(cfg)

    with pytest.raises(manage.ManageError, match="destination already exists"):
        manage.restore_batch(cfg, conn, batch)
    assert batch.exists()
    conn.close()


def test_resolve_batch_by_document_name(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    a = _add_doc(cfg, conn, filename="alpha.pdf")
    b = _add_doc(cfg, conn, filename="beta.pdf")
    manage.bin_documents(cfg, conn, [db.get_document(conn, a), db.get_document(conn, b)])
    batch = _batch(cfg)

    assert manage.resolve_batch(cfg, "alpha.pdf") == batch
    assert manage.resolve_batch(cfg, batch.name) == batch
    with pytest.raises(manage.ManageError):
        manage.resolve_batch(cfg, "nothing-like-this")
    conn.close()


def test_purge_batch_deletes_everything(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn)
    manage.bin_documents(cfg, conn, [db.get_document(conn, doc_id)])
    batch = _batch(cfg)

    manage.purge_batch(cfg, batch)

    assert not batch.exists()
    assert manage.list_batches(cfg) == []
    conn.close()


# ------------------------------------------------------------------------- move

def test_move_updates_row_and_moves_library_and_sidecar(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn, filename="vol.pdf", sidecar=True)
    doc = db.get_document(conn, doc_id)
    _render(cfg, doc["sha256"])
    db.set_bibliography(conn, doc_id, sidecar_path=str(Path(doc["path"]).parent / "vol.dc.json"),
                        sidecar_sha="abc", source_format="dc.json",
                        citation="A reference", parsed_json=None, record_origin=None)
    conn.commit()

    res = manage.move_document(cfg, conn, doc, "collections/MOVED")

    old_src = Path(doc["path"])
    new_src = cfg.dropbox / "collections/MOVED/vol.pdf"
    assert res["moved"] is True
    assert not old_src.exists()
    assert new_src.is_file()
    assert (new_src.parent / "vol.dc.json").is_file()
    assert (cfg.library / "collections/MOVED" / _doc_slug(db.get_document(conn, doc_id))).is_dir()
    row = db.get_document(conn, doc_id)
    assert row["id"] == doc_id                       # same row, not a new one
    assert Path(row["path"]) == new_src
    assert row["dir_path"] == "collections/MOVED"
    assert conn.execute("SELECT COUNT(*) FROM pages WHERE document_id=?", (doc_id,)).fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM chunks WHERE document_id=?", (doc_id,)).fetchone()[0] == 2
    assert (cfg.renders / doc["sha256"]).is_dir()     # sha-keyed cache survives
    assert db.get_bibliography(conn, doc_id)["sidecar_path"] == str(new_src.parent / "vol.dc.json")
    conn.close()


def test_move_keeps_id_and_updated_at(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn)
    doc = dict(db.get_document(conn, doc_id))
    manage.move_document(cfg, conn, doc, "collections/OTHER")
    row = db.get_document(conn, doc_id)
    assert row["updated_at"] == doc["updated_at"]
    conn.close()


def test_move_bare_name_resolves_into_collections(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    (cfg.dropbox / "collections/academic-works").mkdir(parents=True, exist_ok=True)
    doc_id = _add_doc(cfg, conn, filename="paper.pdf", dir_path="collections/misc")
    doc = db.get_document(conn, doc_id)

    res = manage.move_document(cfg, conn, doc, "academic-works")

    assert res["to_rel"] == "collections/academic-works/paper.pdf"
    assert (cfg.dropbox / "collections/academic-works/paper.pdf").is_file()
    conn.close()


def test_move_refuses_existing_or_registered_destination(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    a = _add_doc(cfg, conn, filename="a.pdf", dir_path="collections/COLX")
    b = _add_doc(cfg, conn, filename="a.pdf", dir_path="collections/OTHER")
    doc = db.get_document(conn, a)

    with pytest.raises(manage.ManageError, match="already registered"):
        manage.move_document(cfg, conn, doc, "collections/OTHER")
    assert Path(doc["path"]).exists()
    conn.close()


def test_move_refuses_destination_outside_dropbox(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn)
    doc = db.get_document(conn, doc_id)
    with pytest.raises(manage.ManageError, match="inside the dropbox"):
        manage.move_document(cfg, conn, doc, str(tmp_path / "elsewhere"))
    conn.close()


def test_move_dry_run_touches_nothing(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn)
    doc = db.get_document(conn, doc_id)

    res = manage.move_document(cfg, conn, doc, "collections/NEW", dry_run=True)

    assert res["dry_run"] is True
    assert Path(doc["path"]).exists()
    assert not (cfg.dropbox / "collections/NEW").exists()
    assert db.get_document(conn, doc_id)["dir_path"] == "collections/COLX"
    conn.close()


def test_move_refuses_processing_document(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn)
    db.set_document_status(conn, doc_id, "processing")
    doc = db.get_document(conn, doc_id)
    with pytest.raises(manage.ManageError, match="processing"):
        manage.move_document(cfg, conn, doc, "collections/NEW")
    conn.close()


def test_move_directory_document(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    src = cfg.dropbox / "documents/pages"
    src.mkdir(parents=True)
    (src / "p001.png").write_bytes(b"png")
    doc_id = db.add_document(conn, filename="pages", path=str(src), sha256="dirsha",
                             size_bytes=3, mtime=1, kind="dir", now=time.time(),
                             dir_path="documents")
    conn.commit()
    doc = db.get_document(conn, doc_id)

    manage.move_document(cfg, conn, doc, "collections/IMG")
    assert (cfg.dropbox / "collections/IMG/pages/p001.png").is_file()
    assert not src.exists()
    assert db.get_document(conn, doc_id)["dir_path"] == "collections/IMG"
    conn.close()


# --------------------------------------------------------------------------- MCP

def test_mcp_document_management_tools(tmp_path):
    import asyncio

    from personal_historical_archive import mcp_server

    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn)
    conn.close()

    mcp = mcp_server.make_server(cfg)
    fns = {t.name: t.fn for t in asyncio.run(mcp.list_tools())}
    for name in ("pha_remove_document", "pha_move_document", "pha_bin_list", "pha_bin_restore"):
        assert name in fns, f"{name} not registered"

    moved = fns["pha_move_document"](document_id=doc_id, dest="collections/MCP")
    assert moved["ok"] is True
    assert moved["to_rel"] == "collections/MCP/vol.pdf"

    removed = fns["pha_remove_document"](document_id=doc_id)
    assert removed["ok"] is True and removed["mode"] == "bin"
    listing = fns["pha_bin_list"]()
    assert listing["ok"] is True and len(listing["batches"]) == 1
    batch = listing["batches"][0]["batch"]
    restored = fns["pha_bin_restore"](batch=batch)
    assert restored["ok"] is True and restored["restored"]
    assert (cfg.dropbox / "collections/MCP/vol.pdf").is_file()


# --------------------------------------------------------------------------- CLI

def _rm_args(target, **kw):
    from types import SimpleNamespace
    base = dict(target=target, purge=False, keep_files=False, dry_run=False,
                json=False, force=False, all=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_cmd_rm_refuses_ambiguous_substring(tmp_path, capsys):
    from personal_historical_archive import cli

    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    _add_doc(cfg, conn, filename="vol-1.pdf")
    _add_doc(cfg, conn, filename="vol-2.pdf")
    conn.close()

    with pytest.raises(SystemExit) as e:
        cli.cmd_rm(cfg, _rm_args("vol"))
    assert e.value.code == 2
    assert "refusing" in capsys.readouterr().err
    assert (cfg.dropbox / "collections/COLX/vol-1.pdf").exists()


def test_cmd_rm_dry_run_all_reports_without_moving(tmp_path, capsys):
    from personal_historical_archive import cli

    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    _add_doc(cfg, conn, filename="vol-1.pdf")
    _add_doc(cfg, conn, filename="vol-2.pdf")
    conn.close()

    cli.cmd_rm(cfg, _rm_args("vol", all=True, dry_run=True))
    out = capsys.readouterr().out
    assert "would bin #" in out
    assert (cfg.dropbox / "collections/COLX/vol-1.pdf").exists()
    assert (cfg.dropbox / "collections/COLX/vol-2.pdf").exists()
    assert not list(cfg.bin.glob("*/manifest.json"))


def test_cmd_rm_keep_files_leaves_everything(tmp_path, capsys):
    from personal_historical_archive import cli

    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn)
    doc = db.get_document(conn, doc_id)
    conn.close()

    cli.cmd_rm(cfg, _rm_args(str(doc_id), keep_files=True))
    assert "unregistered #" in capsys.readouterr().out
    assert Path(doc["path"]).exists()
    conn = db.connect(cfg.db_path)
    assert db.get_document(conn, doc_id) is None
    conn.close()


def test_cli_help_exposes_rm_bin_mv(tmp_path, monkeypatch, capsys):
    from personal_historical_archive import cli

    cfg = _make_cfg(tmp_path)
    monkeypatch.delenv("PHA_HOME", raising=False)
    monkeypatch.chdir(cfg.root)
    for argv in (["rm", "--help"], ["bin", "--help"], ["mv", "--help"]):
        with pytest.raises(SystemExit) as e:
            cli.main(argv)
        assert e.value.code == 0
        assert "usage" in capsys.readouterr().out.lower()


def test_cli_main_mv_then_bin(tmp_path, monkeypatch, capsys):
    from personal_historical_archive import cli

    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn)
    conn.close()
    monkeypatch.delenv("PHA_HOME", raising=False)
    monkeypatch.chdir(cfg.root)
    import personal_historical_archive.update as upd
    monkeypatch.setattr(upd, "maybe_notify_update", lambda *a, **k: None)

    cli.main(["mv", str(doc_id), "collections/CLI"])
    assert "moved #" in capsys.readouterr().out
    assert (cfg.dropbox / "collections/CLI/vol.pdf").is_file()

    cli.main(["rm", str(doc_id)])
    assert "binned #" in capsys.readouterr().out
    assert not (cfg.dropbox / "collections/CLI/vol.pdf").exists()

    cli.main(["bin"])
    out = capsys.readouterr().out
    assert "1 batch" in out and "collections/CLI/vol.pdf" in out

    batch = manage.list_batches(cfg)[0]["batch"]
    cli.main(["bin", "restore", batch])
    out = capsys.readouterr().out
    assert "restored #" in out and "pha scan --path collections/CLI/vol.pdf" in out
    assert (cfg.dropbox / "collections/CLI/vol.pdf").is_file()


def test_remove_refuses_processing_unless_forced(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn)
    db.set_document_status(conn, doc_id, "processing")
    doc = db.get_document(conn, doc_id)

    with pytest.raises(manage.ManageError, match="processing"):
        manage.bin_documents(cfg, conn, [doc])
    with pytest.raises(manage.ManageError, match="processing"):
        manage.purge_documents(cfg, conn, [doc])
    # force really clears the way (no pending corrections here)
    manage.bin_documents(cfg, conn, [doc], force=True)
    assert db.get_document(conn, doc_id) is None
    conn.close()


def test_move_force_overwrites_existing_destination(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    doc_id = _add_doc(cfg, conn, filename="vol.pdf")
    doc = db.get_document(conn, doc_id)
    dest = cfg.dropbox / "collections/NEW"
    dest.mkdir(parents=True)
    (dest / "vol.pdf").write_bytes(b"an older occupant")

    with pytest.raises(manage.ManageError, match="already exists"):
        manage.move_document(cfg, conn, doc, "collections/NEW")
    res = manage.move_document(cfg, conn, db.get_document(conn, doc_id), "collections/NEW",
                               force=True)
    assert res["moved"] is True
    assert (dest / "vol.pdf").read_bytes() != b"an older occupant"
    conn.close()


def test_move_directory_document_repoints_bibliography(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    src = cfg.dropbox / "documents/pages"
    src.mkdir(parents=True)
    (src / "p001.png").write_bytes(b"png")
    sidecar = src / "pages.dc.json"          # a dir document's sidecar lives INSIDE it
    sidecar.write_text('{"title": "x"}', encoding="utf-8")
    doc_id = db.add_document(conn, filename="pages", path=str(src), sha256="dirsha",
                             size_bytes=3, mtime=1, kind="dir", now=time.time(),
                             dir_path="documents")
    db.set_bibliography(conn, doc_id, sidecar_path=str(sidecar), sidecar_sha="abc",
                        source_format="dc.json", citation="Ref", parsed_json=None,
                        record_origin=None)
    conn.commit()
    doc = db.get_document(conn, doc_id)

    manage.move_document(cfg, conn, doc, "collections/IMG")

    moved_sidecar = cfg.dropbox / "collections/IMG/pages/pages.dc.json"
    assert moved_sidecar.is_file()
    assert not sidecar.exists()
    assert db.get_bibliography(conn, doc_id)["sidecar_path"] == str(moved_sidecar)
    conn.close()


def test_bin_restore_directory_document_with_inner_sidecar(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    src = cfg.dropbox / "documents/pages"
    src.mkdir(parents=True)
    (src / "p001.png").write_bytes(b"png")
    (src / "pages.dc.json").write_text('{"title": "x"}', encoding="utf-8")
    doc_id = db.add_document(conn, filename="pages", path=str(src), sha256="dirsha",
                             size_bytes=3, mtime=1, kind="dir", now=time.time(),
                             dir_path="documents")
    db.set_bibliography(conn, doc_id, sidecar_path=str(src / "pages.dc.json"),
                        sidecar_sha="abc", source_format="dc.json", citation="Ref",
                        parsed_json=None, record_origin=None)
    conn.commit()
    doc = db.get_document(conn, doc_id)

    manage.bin_documents(cfg, conn, [doc])
    batch = _batch(cfg)
    assert not src.exists()
    assert (batch / "dropbox/documents/pages/pages.dc.json").is_file()

    manage.restore_batch(cfg, conn, batch)
    assert (src / "p001.png").is_file()
    assert (src / "pages.dc.json").is_file()
    conn.close()
