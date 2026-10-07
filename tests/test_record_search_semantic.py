from __future__ import annotations

import json
import time
from types import SimpleNamespace

from personal_historical_archive import db
from personal_historical_archive import ingest
from personal_historical_archive.search import search


class _FakeEmbed:
    def __init__(self):
        self.calls = 0

    def embed(self, model, texts, batch_size=None):
        self.calls += 1
        out = []
        for text in texts:
            if "Malaca" in text:
                out.append([1.0, 0.0])
            else:
                out.append([0.0, 1.0])
        return out


def _cfg():
    return SimpleNamespace(
        default_mode="hybrid", top_k=10,
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
        "page_start": 69,
    }))
    db.add_record(conn, doc, "letters", "letter", json.dumps({
        "kind": "letter", "text": "Carta de Goa", "from": "Xavier", "page": 20,
    }))
    conn.commit()
    return conn, doc


def test_index_records_builds_record_embeddings(tmp_path):
    conn, doc = _seed(tmp_path)
    client = _FakeEmbed()
    n = ingest.index_records(_cfg(), conn, doc, embed_client=client, verbose=False)
    assert n == 2
    rows = db.all_record_embeddings(conn, embed_model="embed-x")
    assert len(rows) == 2
    assert all(row["embedding"] for row in rows)
    conn.close()


def test_semantic_record_search_finds_malaca(tmp_path):
    conn, doc = _seed(tmp_path)
    client = _FakeEmbed()
    ingest.index_records(_cfg(), conn, doc, embed_client=client, verbose=False)
    res = search(conn, client, _cfg(), "Malaca", mode="semantic", source="records")
    assert res["results"]
    hit = res["results"][0]
    assert hit["kind"] == "record"
    assert hit["text"] == "Carta de Malaca"
    assert hit["page_no"] == 69
    assert hit["source"] == "semantic"
    conn.close()


def test_hybrid_record_search_fuses_keyword_and_semantic(tmp_path):
    conn, doc = _seed(tmp_path)
    client = _FakeEmbed()
    ingest.index_records(_cfg(), conn, doc, embed_client=client, verbose=False)
    res = search(conn, client, _cfg(), "Malaca", mode="hybrid", source="records")
    assert res["results"]
    assert res["results"][0]["text"] == "Carta de Malaca"
    assert res["results"][0]["source"] == "hybrid"
    conn.close()


def test_semantic_source_all_includes_page_and_record_hits(tmp_path):
    conn, doc = _seed(tmp_path)
    page = db.add_page(conn, doc, 1)
    db.set_page_result(conn, page, raw_text="Malaca na pagina")
    db.add_chunk(conn, doc, page, 0, "Malaca na pagina", None)
    conn.commit()
    client = _FakeEmbed()
    ingest.index_records(_cfg(), conn, doc, embed_client=client, verbose=False)
    res = search(conn, client, _cfg(), "Malaca", mode="hybrid", source="all")
    kinds = {r["kind"] for r in res["results"]}
    assert "record" in kinds
    assert "page" in kinds
    conn.close()
