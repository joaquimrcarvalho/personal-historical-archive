from __future__ import annotations

"""A query command must not be refused because a job is writing.

Opening a connection used to run DDL (`migrate()` / index creation), which needs
a write lock, so `pha config --doc N` failed with "the archive database is busy"
during a long scan (gap G6, measured 2026-09-21). Query-only commands now open
read-only: no DDL, `PRAGMA query_only=ON`.
"""

import sqlite3
import time
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli, db as _db, mcp_server, serve
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import sha256_of


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


def _seed(cfg: Config):
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
    _db.set_page_result(conn, pid, raw_text="machine text")
    _db.add_chunk(conn, doc, pid, 0, "machine text", None, "raw")
    _db.set_document_status(conn, doc, "done")
    conn.commit()
    from personal_historical_archive.ingest import write_document_pages
    write_document_pages(cfg, conn, doc)      # so page/cite have a filled variant
    conn.close()
    return doc


# --------------------------------------------------------------------------- db.connect

def test_readonly_connect_skips_all_ddl(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _seed(cfg)
    called: list[str] = []
    monkeypatch.setattr(_db, "migrate", lambda conn: called.append("migrate"))
    monkeypatch.setattr(_db, "_ensure_optional_indexes",
                        lambda conn: called.append("indexes"))

    conn = _db.connect(cfg.db_path, readonly=True)
    try:
        assert _db.get_document(conn, 1) is not None      # queries work
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert called == []                               # no DDL ran
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE documents SET filename='x' WHERE id=1")
    finally:
        conn.close()


def test_normal_connect_still_initialises(tmp_path):
    cfg = _cfg(tmp_path)
    conn = _db.connect(cfg.db_path)                        # creates the schema
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 0
        _db.add_document(conn, filename="a.pdf", path=str(tmp_path / "a.pdf"), sha256="a",
                         size_bytes=1, mtime=1, kind="pdf", now=time.time())
        conn.commit()
    finally:
        conn.close()


def test_readonly_connect_initialises_a_fresh_archive(tmp_path):
    """A brand-new archive must still get its schema (nothing can be writing)."""
    cfg = _cfg(tmp_path)
    assert not cfg.db_path.exists()
    conn = _db.connect(cfg.db_path, readonly=True)
    try:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
        ).fetchone()
    finally:
        conn.close()


def test_queries_work_while_another_connection_holds_the_write_lock(tmp_path):
    """The incident: a long writer must not refuse a query. With no DDL on
    connect, a read-only connection is not blocked (WAL readers never are)."""
    cfg = _cfg(tmp_path)
    _seed(cfg)
    writer = sqlite3.connect(str(cfg.db_path))
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("BEGIN IMMEDIATE")                      # holds the write lock
    try:
        conn = _db.connect(cfg.db_path, readonly=True)
        try:
            n = conn.execute("SELECT COUNT(*) n FROM documents").fetchone()["n"]
            assert n == 1
        finally:
            conn.close()
    finally:
        writer.rollback()
        writer.close()


# --------------------------------------------------------------------------- serve

def test_serve_reads_read_only_and_runs_no_ddl(tmp_path, monkeypatch):
    """`pha serve` has its own opener (`mode=ro`/`immutable=1`); assert it too."""
    cfg = _cfg(tmp_path)
    _seed(cfg)
    called: list[str] = []
    monkeypatch.setattr(_db, "migrate", lambda conn: called.append("migrate"))
    monkeypatch.setattr(_db, "_ensure_optional_indexes",
                        lambda conn: called.append("indexes"))

    conn, degraded = serve._connect_ro(cfg.db_path)
    try:
        assert degraded is False
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) n FROM documents").fetchone()["n"] == 1
        assert called == []
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE documents SET filename='x' WHERE id=1")
    finally:
        conn.close()


def test_serve_reader_is_not_refused_by_a_writer(tmp_path):
    """The viewer must keep answering while a scan holds the write lock."""
    cfg = _cfg(tmp_path)
    _seed(cfg)
    writer = sqlite3.connect(str(cfg.db_path))
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("BEGIN IMMEDIATE")
    try:
        conn, degraded = serve._connect_ro(cfg.db_path)
        try:
            assert degraded is False
            assert conn.execute("SELECT COUNT(*) n FROM documents").fetchone()["n"] == 1
        finally:
            conn.close()
    finally:
        writer.rollback()
        writer.close()


# --------------------------------------------------------------------------- old schema

# Columns 0.34.0 added for the per-page overrides (scan + edit). A read-only
# command runs NO migration, so it must answer on an archive whose DB predates
# them — this is the regression that broke `pha status` ("no such column:
# pe.pinned_at") for every archive that had not run a write pass since 0.34.0.
_PRE_034_COLUMNS = (
    ("pages", "palaeographer"), ("pages", "palaeographer_model"), ("pages", "pinned_at"),
    ("page_edits", "editor_model"), ("page_edits", "pinned_at"),
)


def _drop_new_columns(cfg: Config) -> None:
    """Make the DB look like one written by pha < 0.34.0."""
    conn = sqlite3.connect(str(cfg.db_path))
    try:
        for table, col in _PRE_034_COLUMNS:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
        conn.commit()
    finally:
        conn.close()


def test_read_only_commands_survive_a_pre_034_schema(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    doc = _seed(cfg)
    # give the document an editor and one edited page, then take the 0.34.0
    # columns away: this is the state of every archive that has not been
    # written to since the update.
    conn = _db.connect(cfg.db_path)
    try:
        _db.update_document(conn, doc, editor="french-ocr", editor_model="deepseek-v4-flash")
        pid = conn.execute("SELECT id FROM pages WHERE document_id=? AND page_no=1",
                           (doc,)).fetchone()["id"]
        _db.set_page_edit(conn, pid, "french-ocr", text="edited text")
        conn.commit()
    finally:
        conn.close()
    _drop_new_columns(cfg)

    cli.cmd_status(cfg, SimpleNamespace())
    cli.cmd_pending(cfg, SimpleNamespace(doc=None, json=True))
    cli.cmd_page(cfg, SimpleNamespace(doc=str(doc), page=1, edited=True, editor=None,
                                      json=True))
    cli.cmd_cite(cfg, SimpleNamespace(doc=str(doc), page=1, edited=False, editor=None,
                                      palaeographer=None, json=True))

    import asyncio
    fns = {t.name: t.fn for t in asyncio.run(mcp_server.make_server(cfg).list_tools())}
    page = fns["pha_get_page"](doc, 1)
    assert page["edited_served"]["editor"] == "french-ocr"   # no crash, honest answer
    assert page["edited_served"]["pinned"] is False
    capsys.readouterr()

    # a WRITE connection migrates the columns back, and the feature works again
    conn = _db.connect(cfg.db_path)
    try:
        ecols = {r[1] for r in conn.execute("PRAGMA table_info(page_edits)")}
        pcols = {r[1] for r in conn.execute("PRAGMA table_info(pages)")}
        assert {"editor_model", "pinned_at"} <= ecols
        assert {"palaeographer", "palaeographer_model", "pinned_at"} <= pcols
    finally:
        conn.close()


# --------------------------------------------------------------------------- call sites

def _spy_connect(monkeypatch):
    """Record every db.connect call (readonly flag) and still connect for real."""
    from personal_historical_archive import db as dbmod
    real = dbmod.connect
    calls: list[bool] = []

    def spy(path, readonly=False):
        calls.append(readonly)
        return real(path, readonly=readonly)

    monkeypatch.setattr(dbmod, "connect", spy)
    return calls


def test_query_commands_open_read_only(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    doc = _seed(cfg)
    calls = _spy_connect(monkeypatch)

    cli.cmd_pending(cfg, SimpleNamespace(doc=None, json=True))
    cli.cmd_status(cfg, SimpleNamespace())
    cli.cmd_config(cfg, SimpleNamespace(doc=str(doc), path=None, write=False))
    cli.cmd_page(cfg, SimpleNamespace(doc=str(doc), page=1, edited=False, editor=None, json=True))
    cli.cmd_cite(cfg, SimpleNamespace(doc=str(doc), page=1, edited=False, editor=None,
                                      palaeographer=None, json=True))
    cli.cmd_search(cfg, SimpleNamespace(query="machine", mode="keyword", limit=1,
                                        collection=None, json=True, force=False))
    capsys.readouterr()

    assert calls and all(calls), f"a query command opened the DB read-write: {calls}"


def test_write_commands_still_open_read_write(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    doc = _seed(cfg)
    calls = _spy_connect(monkeypatch)

    cli.cmd_review(cfg, SimpleNamespace(doc=doc, page=None, all=False, unset=False, force=False))
    capsys.readouterr()

    assert calls == [False], f"review must open read-write (it imports): {calls}"


def test_mcp_query_tools_open_read_only(tmp_path, monkeypatch):
    """Every FastMCP tool is a query; a search during a scan must not be refused."""
    cfg = _cfg(tmp_path)
    _seed(cfg)
    calls = _spy_connect(monkeypatch)

    import asyncio
    mcp = mcp_server.make_server(cfg)
    tools = {t.name: t.fn for t in asyncio.run(mcp.list_tools())}
    tools["pha_search"](query="machine", mode="keyword", limit=5)
    tools["pha_list_documents"]()
    tools["pha_get_document"](document_id=1)
    tools["pha_get_page"](document_id=1, page_no=1)
    tools["pha_schema"]()
    tools["pha_collection_status"]()

    assert calls and all(calls), f"an MCP query tool opened read-write: {calls}"
