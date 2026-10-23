from __future__ import annotations

import time

from personal_historical_archive import db


def test_build_fts_query():
    assert db.build_fts_query("doação de Évora") == '"doação" AND "de" AND "Évora"'
    assert db.build_fts_query('"mosteiro de são" benta') == '"mosteiro de são" AND "benta"'
    assert db.build_fts_query("") == '""'


def test_db_roundtrip_and_search(tmp_path):
    conn = db.connect(tmp_path / "archive.db")
    now = time.time()
    doc_id = db.add_document(
        conn, filename="a.pdf", path=str(tmp_path / "a.pdf"), sha256="abc",
        size_bytes=1, mtime=now, kind="pdf", now=now, dir_path="documents",
    )
    page_id = db.add_page(conn, doc_id, 1)
    db.set_page_result(conn, page_id, raw_text="O mosteiro de São Bento de Lisboa recebeu a herdade.")
    db.add_chunk(conn, doc_id, page_id, 0, "O mosteiro de São Bento de Lisboa recebeu a herdade.", None)
    db.set_document_status(conn, doc_id, "done")
    conn.commit()

    rows = db.keyword_search(conn, "mosteiro Bento", limit=5)
    assert len(rows) == 1
    assert rows[0]["page_no"] == 1
    assert rows[0]["variant"] == "raw"
    assert rows[0]["dir_path"] == "documents"

    d = db.get_document(conn, doc_id)
    assert d["status"] == "done"
    conn.close()


def test_schema_reports_real_columns_and_fk(tmp_path):
    conn = db.connect(tmp_path / "archive.db")
    s = db.schema(conn)
    page_cols = [c["name"] for c in s["tables"]["pages"]["columns"]]
    # pages links via document_id and carries no path/sha256
    assert "document_id" in page_cols
    assert "doc_id" not in page_cols
    assert "path" not in page_cols
    assert "sha256" not in page_cols
    # path/sha256 belong to documents
    doc_cols = [c["name"] for c in s["tables"]["documents"]["columns"]]
    assert "path" in doc_cols and "sha256" in doc_cols
    # the FK joins an agent needs are listed
    rels = "\n".join(s["relations"])
    assert "pages.document_id -> documents.id" in rels
    assert "page_edits.page_id -> pages.id" in rels
    conn.close()


def test_chunk_stats_per_document(tmp_path):
    """chunk_stats reports per-document chunks vs embedded chunks, so a
    keyword-only document (embeddings missing) is visible at doc level."""
    conn = db.connect(tmp_path / "archive.db")
    now = time.time()
    d1 = db.add_document(conn, filename="a.pdf", path=str(tmp_path / "a.pdf"),
                         sha256="a", size_bytes=1, mtime=now, kind="pdf",
                         now=now, dir_path="documents")
    d2 = db.add_document(conn, filename="b.pdf", path=str(tmp_path / "b.pdf"),
                         sha256="b", size_bytes=1, mtime=now, kind="pdf",
                         now=now, dir_path="documents")
    p1 = db.add_page(conn, d1, 1)
    p2 = db.add_page(conn, d2, 1)
    # d1: one text-only chunk + one embedded chunk; d2: none
    db.add_chunk(conn, d1, p1, 0, "text only", None)
    db.add_chunk(conn, d1, p1, 1, "embedded", b"\x00\x01vec")
    db.add_chunk(conn, d2, p2, 0, "no embedding", None)
    conn.commit()

    stats = db.chunk_stats(conn)
    assert stats[d1] == {"chunks": 2, "embedded": 1}
    assert stats[d2] == {"chunks": 1, "embedded": 0}
    assert db.chunk_stats(conn, doc_id=d1) == {d1: {"chunks": 2, "embedded": 1}}
    assert db.chunk_stats(conn, doc_id=999) == {}
    conn.close()


def test_collection_filter(tmp_path):
    conn = db.connect(tmp_path / "archive.db")
    now = time.time()
    for name, col in (("doc1.pdf", "documents"), ("doc2.pdf", "collections/COLX")):
        db.add_document(
            conn, filename=name, path=str(tmp_path / name), sha256=name,
            size_bytes=1, mtime=now, kind="pdf", now=now, dir_path=col,
        )
    conn.commit()
    docs = db.list_documents(conn, collection="COLX", limit=10)
    assert [d["filename"] for d in docs] == ["doc2.pdf"]
    docs = db.list_documents(conn, collection="documents", limit=10)
    assert [d["filename"] for d in docs] == ["doc1.pdf"]
    conn.close()


def test_summary(tmp_path):
    conn = db.connect(tmp_path / "archive.db")
    now = time.time()
    db.add_document(
        conn, filename="a.pdf", path=str(tmp_path / "a.pdf"), sha256="abc",
        size_bytes=1, mtime=now, kind="pdf", now=now, dir_path="",
    )
    conn.commit()
    s = db.summary(conn)
    assert s["documents"] == {"pending": 1}
    conn.close()


def test_summary_reuses_chunk_stats_and_embedded_count_is_indexed(tmp_path):
    """`pha status` passes its chunk_stats in (one chunks scan) and the
    embedded-chunk count must not fall back to a full table scan of blobs."""
    conn = db.connect(tmp_path / "archive.db")
    now = time.time()
    d1 = db.add_document(conn, filename="a.pdf", path=str(tmp_path / "a.pdf"),
                         sha256="a", size_bytes=1, mtime=now, kind="pdf", now=now, dir_path="")
    p1 = db.add_page(conn, d1, 1)
    db.add_chunk(conn, d1, p1, 0, "text only", None)
    db.add_chunk(conn, d1, p1, 1, "embedded", b"\x00\x01vec")
    conn.commit()

    plain = db.summary(conn)
    stats = db.chunk_stats(conn)
    assert db.summary(conn, chunk_stats=stats) == plain
    assert plain["chunks"] == 2 and plain["chunks_embedded"] == 1
    assert stats[d1] == {"chunks": 2, "embedded": 1}

    plan = [r[-1] for r in conn.execute(
        "EXPLAIN QUERY PLAN SELECT document_id, COUNT(*) e FROM chunks "
        "WHERE embedding IS NOT NULL GROUP BY document_id")]
    assert any("idx_chunks_embedded" in p for p in plan), plan
    conn.close()


def test_migrate_readds_embed_model_column(tmp_path):
    """An archive written before `chunks.embed_model` existed gains the column
    on the next connect (NULL on old rows = unknown provenance, never reused),
    so an upgrade does not need a manual migration step."""
    from personal_historical_archive import db

    path = tmp_path / "archive.db"
    conn = db.connect(path)
    conn.execute("ALTER TABLE chunks DROP COLUMN embed_model")
    conn.commit()
    conn.close()

    conn = db.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chunks)")}
    assert "embed_model" in cols
    conn.close()


def test_any_embedded_chunk_scopes_to_pages(tmp_path):
    """`any_embedded_chunk` answers the 'would a failure lose a vector?' question
    for the whole document or for a page subset, without reading blobs."""
    from personal_historical_archive import db

    conn = db.connect(tmp_path / "archive.db")
    now = time.time()
    d = db.add_document(conn, filename="a.pdf", path=str(tmp_path / "a.pdf"),
                        sha256="a", size_bytes=1, mtime=now, kind="pdf", now=now, dir_path="")
    p1 = db.add_page(conn, d, 1)
    p2 = db.add_page(conn, d, 2)
    db.add_chunk(conn, d, p1, 0, "text only", None)
    db.add_chunk(conn, d, p2, 0, "embedded", b"\x00\x01vec", embed_model="m1")
    conn.commit()

    assert db.any_embedded_chunk(conn, d) is True
    assert db.any_embedded_chunk(conn, d, [p1]) is False
    assert db.any_embedded_chunk(conn, d, [p2]) is True
    assert db.any_embedded_chunk(conn, d, []) is False

    row = conn.execute("SELECT embed_model FROM chunks WHERE page_id=?", (p2,)).fetchone()
    assert row["embed_model"] == "m1"
    conn.close()


def test_reviewed_edit_guard_handles_a_legacy_page_edits_table():
    """Read-only commands run no migration; a legacy table has no reviewed_at."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE page_edits (id INTEGER PRIMARY KEY, page_id INTEGER, "
        "editor TEXT, text TEXT, status TEXT)")
    assert db.reviewed_edit_for_page(conn, 1) is None
    conn.close()


def test_migrate_repairs_windows_dir_path(tmp_path):
    """dir_path is a collection key matched with '/' patterns (_dir_clause, and
    the stable page addresses built from it), so a row written by a build that
    ran on Windows — native separators — is repaired on connect; otherwise
    `pha search --collection` and the MCP/plugin collection filters never match
    it on that archive."""
    dbp = tmp_path / "archive.db"
    conn = db.connect(dbp)
    now = time.time()
    doc_id = db.add_document(
        conn, filename="a.pdf", path=str(tmp_path / "a.pdf"), sha256="a",
        size_bytes=1, mtime=now, kind="pdf", now=now,
        dir_path="collections\\COLX",
    )
    conn.commit()
    assert db.list_documents(conn, collection="COLX", limit=10) == []
    conn.close()

    conn = db.connect(dbp)  # migrate() runs on every connect
    assert db.get_document(conn, doc_id)["dir_path"] == "collections/COLX"
    assert [d["filename"] for d in db.list_documents(conn, collection="COLX", limit=10)] == ["a.pdf"]
    conn.close()


def test_migrate_tolerates_a_readonly_database(tmp_path, monkeypatch):
    """The dir_path repair must never make a READ fail.

    It runs on every connect, so a database we may not write to (a read-only
    mount, a file owned by another user) must still connect — the repair is
    skipped there, leaving the rows as they are, instead of failing every
    command with "attempt to write a readonly database".
    """
    import sqlite3

    dbp = tmp_path / "archive.db"
    conn = db.connect(dbp)
    now = time.time()
    doc_id = db.add_document(
        conn, filename="a.pdf", path=str(tmp_path / "a.pdf"), sha256="a",
        size_bytes=1, mtime=now, kind="pdf", now=now,
        dir_path="collections\\COLX",
    )
    conn.commit()
    conn.close()

    real_write = db._write

    def readonly(conn_, sql, params=()):
        if "replace(dir_path" in sql:
            raise sqlite3.OperationalError("attempt to write a readonly database")
        return real_write(conn_, sql, params)

    monkeypatch.setattr(db, "_write", readonly)
    conn = db.connect(dbp)  # must not raise
    assert db.get_document(conn, doc_id)["dir_path"] == "collections\\COLX"
    conn.close()


def test_backfill_dir_path_uses_posix(tmp_path):
    """Rows predating the dir_path column are filled POSIX-style on every
    platform — the same key contract as the ingest writer."""
    drop = tmp_path / "dropbox"
    src = drop / "collections" / "COLX" / "a.pdf"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x")
    conn = db.connect(tmp_path / "archive.db")
    now = time.time()
    doc_id = db.add_document(
        conn, filename="a.pdf", path=str(src), sha256="a",
        size_bytes=1, mtime=now, kind="pdf", now=now, dir_path=None,
    )
    conn.commit()
    db.backfill_dir_path(conn, drop)
    assert db.get_document(conn, doc_id)["dir_path"] == "collections/COLX"
    conn.close()
