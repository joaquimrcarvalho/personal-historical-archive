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
