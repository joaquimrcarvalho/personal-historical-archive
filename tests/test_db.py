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
