from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from personal_historical_archive import db
from personal_historical_archive import ingest
from personal_historical_archive.search import search


class _FakeEmbed:
    def embed(self, model, texts, batch_size=None):
        out = []
        for text in texts:
            out.append([1.0, 0.0] if "Malaca" in text else [0.0, 1.0])
        return out


def _cfg():
    return SimpleNamespace(
        default_mode="keyword", top_k=10,
        embed_model="embed-x", embed_base_url="http://127.0.0.1:1/v1",
        embed_timeout_s=1, embed_batch_size=64,
    )


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
        "place": "Malaca", "page_start": 69,
    }))
    db.add_record(conn, doc, "letters", "letter", json.dumps({
        "kind": "letter", "text": "Carta de Goa", "from": "Xavier",
        "place": "Goa", "page_start": 20,
    }))
    conn.commit()
    return conn, doc


def test_keyword_record_search_field_filter(tmp_path):
    conn, _doc = _seed(tmp_path)
    res = search(conn, None, _cfg(), "Carta", mode="keyword",
                 source="records", fields={"place": "Malaca"})
    assert len(res["results"]) == 1
    assert res["results"][0]["text"] == "Carta de Malaca"
    conn.close()


def test_keyword_record_search_multiple_field_filters(tmp_path):
    conn, _doc = _seed(tmp_path)
    res = search(conn, None, _cfg(), "Carta", mode="keyword",
                 source="records",
                 fields={"from": "Xavier", "place": "Goa"})
    assert len(res["results"]) == 1
    assert res["results"][0]["text"] == "Carta de Goa"
    conn.close()


def test_semantic_record_search_field_filter(tmp_path):
    conn, doc = _seed(tmp_path)
    client = _FakeEmbed()
    ingest.index_records(_cfg(), conn, doc, embed_client=client, verbose=False)
    res = search(conn, client, _cfg(), "Malaca", mode="semantic",
                 source="records", fields={"place": "Malaca"})
    assert len(res["results"]) == 1
    assert res["results"][0]["text"] == "Carta de Malaca"
    conn.close()


def test_invalid_field_name_is_rejected(tmp_path):
    conn, _doc = _seed(tmp_path)
    with pytest.raises(ValueError):
        search(conn, None, _cfg(), "Carta", mode="keyword",
               source="records", fields={"place; DROP TABLE records": "Malaca"})
    conn.close()


def test_fields_are_rejected_for_page_only_search(tmp_path):
    conn, _doc = _seed(tmp_path)
    with pytest.raises(ValueError):
        search(conn, None, _cfg(), "Carta", mode="keyword",
               source="pages", fields={"place": "Malaca"})
    conn.close()


def test_field_only_listing_without_query(tmp_path):
    conn, _doc = _seed(tmp_path)
    res = search(conn, None, _cfg(), "", mode="keyword",
                 source="records", fields={"place": "Malaca"})
    assert len(res["results"]) == 1
    assert res["results"][0]["text"] == "Carta de Malaca"
    assert "no text query" in (res["note"] or "")
    conn.close()


def test_empty_query_without_fields_is_rejected(tmp_path):
    conn, _doc = _seed(tmp_path)
    with pytest.raises(ValueError):
        search(conn, None, _cfg(), "", mode="keyword", source="records")
    conn.close()


def test_field_filter_ignore_case(tmp_path):
    conn, _doc = _seed(tmp_path)
    res = search(conn, None, _cfg(), "Carta", mode="keyword",
                 source="records", fields={"place": "malaca"})
    assert res["results"] == []

    res = search(conn, None, _cfg(), "Carta", mode="keyword",
                 source="records", fields={"place": "malaca"},
                 ignore_case=True)
    assert len(res["results"]) == 1
    assert res["results"][0]["text"] == "Carta de Malaca"
    conn.close()


def test_field_only_listing_ignore_case(tmp_path):
    conn, _doc = _seed(tmp_path)
    res = search(conn, None, _cfg(), "", mode="keyword",
                 source="records", fields={"place": "malaca"},
                 ignore_case=True)
    assert len(res["results"]) == 1
    assert res["results"][0]["text"] == "Carta de Malaca"
    conn.close()
