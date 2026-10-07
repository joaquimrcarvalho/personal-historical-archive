from __future__ import annotations

import json
import time
from types import SimpleNamespace

from personal_historical_archive import db
from personal_historical_archive.search import search


def _cfg():
    return SimpleNamespace(default_mode="keyword", top_k=10, embed_model="embed-x",
                           embed_base_url="http://127.0.0.1:1/v1",
                           embed_timeout_s=1)


def _seed(tmp_path):
    conn = db.connect(tmp_path / "archive.db")
    now = time.time()
    doc = db.add_document(
        conn, filename="letters.pdf", path=str(tmp_path / "letters.pdf"),
        sha256="a", size_bytes=1, mtime=now, kind="pdf",
        dir_path="collections/COLX", now=now,
    )
    db.add_record(conn, doc, "letters", "letter", json.dumps({
        "kind": "letter", "text": "Carta de Malaca", "from": "Xavier",
        "page_start": 69,
    }))
    db.add_record(conn, doc, "letters", "letter", json.dumps({
        "kind": "letter", "text": "Carta de Goa", "from": "Xavier", "page": 20,
    }))
    db.add_record(conn, doc, "letters", "person", json.dumps({
        "kind": "person", "text": "Sao Francisco Xavier", "name": "Xavier",
    }))
    conn.commit()
    return conn, doc


def test_record_start_page_aliases():
    assert db.record_start_page({"page_start": 69}) == 69
    assert db.record_start_page({"page": "20"}) == "20"
    assert db.record_start_page({"start_page": 7}) == 7
    assert db.record_start_page({}) is None


def test_add_record_indexes_search_text_and_source(tmp_path):
    conn, _doc = _seed(tmp_path)
    rows = conn.execute(
        "SELECT source, search_text FROM records ORDER BY id"
    ).fetchall()
    assert rows[0]["source"] == "69"
    assert "Carta de Malaca" in rows[0]["search_text"]
    assert rows[1]["source"] == "20"
    assert rows[2]["source"] == ""
    fts = conn.execute("SELECT rowid FROM records_fts ORDER BY rowid").fetchall()
    assert [r["rowid"] for r in fts] == [1, 2, 3]
    conn.close()


def test_keyword_record_search_and_metadata_filters(tmp_path):
    conn, _doc = _seed(tmp_path)
    res = search(conn, None, _cfg(), "Malaca", mode="keyword", source="records")
    assert len(res["results"]) == 1
    hit = res["results"][0]
    assert hit["kind"] == "record"
    assert hit["record_kind"] == "letter"
    assert hit["page_no"] == 69
    assert hit["encoder"] == "letters"
    assert hit["data"]["from"] == "Xavier"

    res = search(conn, None, _cfg(), "Xavier", mode="keyword", source="records",
                 record_kind="person")
    assert [r["record_kind"] for r in res["results"]] == ["person"]

    res = search(conn, None, _cfg(), "Malaca", mode="keyword", source="records",
                 encoder="missing")
    assert res["results"] == []
    conn.close()


def test_source_all_keeps_page_and_record_hits(tmp_path):
    conn, doc = _seed(tmp_path)
    page_id = db.add_page(conn, doc, 1)
    db.set_page_result(conn, page_id, raw_text="Malaca no texto da pagina")
    db.add_chunk(conn, doc, page_id, 0, "Malaca no texto da pagina", None)
    conn.commit()

    res = search(conn, None, _cfg(), "Malaca", mode="keyword", source="all")
    kinds = {r["kind"] for r in res["results"]}
    assert kinds == {"page", "record"}
    conn.close()


def test_rebuild_records_index_repairs_missing_fts_and_source(tmp_path):
    conn, _doc = _seed(tmp_path)
    conn.execute("UPDATE records SET search_text = NULL, source = ''")
    conn.execute("DELETE FROM records_fts")
    conn.commit()
    n = db.rebuild_records_index(conn)
    conn.commit()
    assert n == 3
    assert conn.execute(
        "SELECT source FROM records ORDER BY id"
    ).fetchall()[0]["source"] == "69"
    hit = db.record_keyword_search(conn, "Malaca", limit=5)
    assert len(hit) == 1
    assert hit[0]["source"] == "69"
    conn.close()
