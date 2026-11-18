from __future__ import annotations

"""A page semantic search must score only the vectors of the model that embedded
the query.

Cosine across two embedding models is meaningless. With two quantizations of
the same base model it does not even error (same dimension) — it silently ranks
the other model's chunks out of the top results, so after a model switch the
archive looked half-searched. The records and notes arms already filtered by
`embed_model`; the page arm did not. These tests pin the filter and the note
that tells the reader part of the index is on another model.
"""

import json
from types import SimpleNamespace

import pytest

from personal_historical_archive import db, ingest
from personal_historical_archive.embed import pack
from personal_historical_archive.model_client import ModelError
from personal_historical_archive.search import search


class _FakeEmbed:
    def embed(self, model, texts, batch_size=None):
        return [[1.0, 0.0, 0.0] for _ in texts]


class _BoomEmbed:
    def embed(self, model, texts, batch_size=None):
        raise ModelError("endpoint down")


def _cfg():
    return SimpleNamespace(
        default_mode="hybrid", top_k=10,
        embed_model="embed-current", embed_base_url="http://127.0.0.1:1/v1",
        embed_timeout_s=1, embed_batch_size=64,
    )


def _seed(tmp_path):
    """One document with two chunks: one on the current model, one on an old."""
    conn = db.connect(tmp_path / "archive.db")
    doc = db.add_document(
        conn, filename="d.pdf", path=str(tmp_path / "d.pdf"), sha256="a",
        size_bytes=1, mtime=1, kind="pdf", dir_path="collections/COLX",
        now="2026-01-01",
    )
    page = db.add_page(conn, doc, 1)
    db.set_page_result(conn, page, raw_text="Malaca e o padrao")
    db.add_chunk(conn, doc, page, 0, "Malaca e o padrao", pack([1, 0, 0]), "raw",
                 embed_model="embed-current")
    db.add_chunk(conn, doc, page, 1, "Goa e o padrao", pack([1, 0, 0]), "raw",
                 embed_model="embed-old")
    conn.commit()
    return conn


def test_all_embeddings_filters_by_model(tmp_path):
    conn = _seed(tmp_path)
    assert len(db.all_embeddings(conn)) == 2
    only = db.all_embeddings(conn, embed_model="embed-current")
    assert len(only) == 1
    conn.close()


def test_semantic_page_search_scores_only_the_query_model(tmp_path):
    conn = _seed(tmp_path)
    want = conn.execute(
        "SELECT id FROM chunks WHERE embed_model = 'embed-current'").fetchone()["id"]
    res = search(conn, _FakeEmbed(), _cfg(), "Malaca", mode="semantic",
                 source="pages", allow_embed=True)
    assert [r["chunk_id"] for r in res["results"]] == [want]
    conn.close()


def test_search_warns_that_another_model_is_not_searched(tmp_path):
    conn = _seed(tmp_path)
    res = search(conn, _FakeEmbed(), _cfg(), "Malaca", mode="hybrid",
                 source="pages", allow_embed=True)
    assert res["note"], "the mixed index must not be silent"
    assert "another embedding model" in res["note"]
    assert "embed-old" in res["note"]
    assert "pha reindex --source pages" in res["note"]
    conn.close()


def test_summary_reports_the_model_split(tmp_path):
    conn = _seed(tmp_path)
    models = {m["model"]: m["chunks"] for m in db.summary(conn)["chunk_models"]}
    assert models == {"embed-current": 1, "embed-old": 1}
    conn.close()


def test_index_records_reports_an_embed_failure(tmp_path):
    """A reindex must not silently leave a document keyword-only."""
    conn = db.connect(tmp_path / "archive.db")
    doc = db.add_document(
        conn, filename="d.pdf", path=str(tmp_path / "d.pdf"), sha256="a",
        size_bytes=1, mtime=1, kind="pdf", dir_path="collections/COLX",
        now="2026-01-01",
    )
    db.add_record(conn, doc, "letters", "letter",
                  json.dumps({"text": "Carta de Malaca"}))
    conn.commit()
    with pytest.raises(ModelError):
        ingest.index_records(_cfg(), conn, doc, embed_client=_BoomEmbed(),
                             verbose=False)
    assert db.all_record_embeddings(conn) == []
    conn.close()


def test_record_search_warns_about_vectors_on_another_model(tmp_path):
    conn = _seed(tmp_path)
    doc = conn.execute("SELECT document_id FROM chunks LIMIT 1").fetchone()[0]
    db.add_record(conn, doc, "letters", "letter",
                  json.dumps({"text": "Carta de Malaca"}))
    rid = conn.execute("SELECT id FROM records").fetchone()["id"]
    db.set_record_embedding(conn, rid, pack([1.0, 0.0, 0.0]), "embed-old")
    conn.commit()
    res = search(conn, _FakeEmbed(), _cfg(), "Malaca", mode="semantic",
                 source="records", allow_embed=True)
    assert res["note"]
    assert "another embedding model" in res["note"]
    assert "pha reindex --source records" in res["note"]
    conn.close()
