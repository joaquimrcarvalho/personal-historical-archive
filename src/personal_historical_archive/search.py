from __future__ import annotations

import sqlite3

import numpy as np

from . import db
from . import locks
from .config import Config
from .embed import cosine, prefixed, unpack
from .model_client import ModelClient, ModelError


SEARCH_SOURCES = ("pages", "records", "all")


def _embed_query(client: ModelClient, model: str, query: str) -> np.ndarray | None:
    try:
        vecs = client.embed(model, [prefixed(model, query, "query")])
    except ModelError:
        return None
    if not vecs:
        return None
    return np.asarray(vecs[0], dtype=np.float32)


def _decorate(conn: sqlite3.Connection, chunk_id: int, row, source: str, score) -> dict | None:
    if row is None:
        row = conn.execute(
            "SELECT c.*, p.page_no FROM chunks c JOIN pages p ON p.id = c.page_id WHERE c.id = ?",
            (chunk_id,),
        ).fetchone()
        if row is None:
            return None
    doc = db.get_document(conn, row["document_id"])
    keys = row.keys()
    snippet = row["snippet"] if "snippet" in keys and row["snippet"] else (row["text"] or "")[:220]
    score = score if score is not None else (row["bm"] if "bm" in keys else None)
    return {
        "kind": "page",
        "chunk_id": chunk_id,
        "document_id": row["document_id"],
        "filename": doc["filename"] if doc else None,
        "collection": (doc["dir_path"] if doc and doc["dir_path"] else "(root)"),
        "path": doc["path"] if doc else None,
        "page_no": row["page_no"],
        "variant": row["variant"] if "variant" in keys else "raw",
        "text": row["text"],
        "snippet": snippet,
        "score": score,
        "source": source,
    }


def _record_page_no(value) -> int | str | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return str(value)


def _record_text(rec: dict, fallback: str = "") -> str:
    text = rec.get("text")
    if isinstance(text, str) and text.strip():
        return text
    return fallback or db.record_search_text(rec)


def _decorate_record(conn: sqlite3.Connection, row, source: str, score) -> dict | None:
    try:
        rec = db._as_record_dict(row["data"])
    except Exception:
        rec = {}
    doc = db.get_document(conn, row["document_id"])
    try:
        snippet = row["snippet"]
    except (IndexError, KeyError):
        snippet = None
    text = _record_text(rec)
    if not snippet:
        snippet = text[:220]
    score = score if score is not None else (row["bm"] if "bm" in row.keys() else None)
    return {
        "kind": "record",
        "record_id": row["record_id"],
        "document_id": row["document_id"],
        "filename": doc["filename"] if doc else None,
        "collection": (doc["dir_path"] if doc and doc["dir_path"] else "(root)"),
        "path": doc["path"] if doc else None,
        "page_no": _record_page_no(row["source"]),
        "encoder": row["encoder"],
        "record_kind": row["record_kind"],
        "text": text,
        "snippet": snippet,
        "score": score,
        "source": source,
        "data": rec,
    }


def keyword_search(conn: sqlite3.Connection, query: str, limit: int, collection: str | None = None) -> list[dict]:
    rows = db.keyword_search(conn, query, limit, collection=collection)
    out = []
    for r in rows:
        d = _decorate(conn, r["chunk_id"], r, "keyword", None)
        if d:
            out.append(d)
    return out


def semantic_search(
    conn: sqlite3.Connection,
    client: ModelClient,
    model: str,
    query: str,
    limit: int,
    collection: str | None = None,
) -> list[dict]:
    q = _embed_query(client, model, query)
    if q is None:
        return []
    embs = db.all_embeddings(conn, collection=collection)
    if not embs:
        return []
    scored = [(cosine(q, unpack(b)), cid) for cid, b in embs]
    scored.sort(key=lambda t: t[0], reverse=True)
    out = []
    for score, chunk_id in scored[:limit]:
        d = _decorate(conn, chunk_id, None, "semantic", round(score, 5))
        if d:
            out.append(d)
    return out


def record_keyword_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
    collection: str | None = None,
    encoder: str | None = None,
    record_kind: str | None = None,
) -> list[dict]:
    rows = db.record_keyword_search(
        conn, query, limit, collection=collection,
        encoder=encoder, record_kind=record_kind,
    )
    out = []
    for r in rows:
        d = _decorate_record(conn, r, "keyword", None)
        if d:
            out.append(d)
    return out


def _result_key(r: dict) -> tuple:
    if r.get("kind") == "record":
        return ("record", r.get("record_id"))
    return ("page", r.get("chunk_id"))


def _rrf_merge_many(result_lists: list[list[dict]], limit: int, k: int = 60) -> list[dict]:
    """RRF over page and/or record lists, keyed by (kind, id)."""
    scores: dict[tuple, float] = {}
    seen: dict[tuple, dict] = {}
    for lst in result_lists:
        for rank, r in enumerate(lst):
            key = _result_key(r)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            seen.setdefault(key, r)
    order = sorted(scores.items(), key=lambda t: t[1], reverse=True)
    out = []
    for key, s in order[:limit]:
        r = dict(seen[key])
        r["score"] = round(s, 5)
        out.append(r)
    return out


def _rrf_merge(kw: list[dict], sem: list[dict], limit: int, k: int = 60) -> list[dict]:
    """Backward-compatible page-only RRF with the original source labels."""
    merged = _rrf_merge_many([kw, sem], limit, k=k)
    label = "hybrid" if (kw and sem) else ("semantic" if sem else "keyword")
    for r in merged:
        r["source"] = label
    return merged


def _page_search(
    conn: sqlite3.Connection,
    client: ModelClient,
    cfg: Config,
    query: str,
    mode: str,
    limit: int,
    collection: str | None,
    allow_embed: bool,
) -> dict:
    """The original page-only search path, unchanged in its result shape."""
    if mode == "keyword":
        return {
            "mode": mode, "query": query,
            "results": keyword_search(conn, query, limit, collection),
            "note": None,
        }

    note: str | None = None
    sem: list[dict] = []
    embed_server = locks.embed_key(cfg)
    if not allow_embed and locks.job_running(cfg, embed_server):
        who = locks.holder_label(embed_server)
        subject = f"A model job ({who})" if who else "A model job"
        note = (f"{subject} is using the embedding server; semantic search "
                "skipped so it keeps its model loaded. Keyword results only - "
                "re-run when it finishes, pass --force, or point "
                "embeddings.base_url at a separate server.")
    else:
        sem = semantic_search(conn, client, cfg.embed_model, query, limit, collection)

    if mode == "semantic":
        if note is None and not sem:
            note = "Semantic search unavailable: embedding model unreachable or no embedded chunks."
        return {"mode": mode, "query": query, "results": sem, "note": note}

    kw = keyword_search(conn, query, limit, collection)
    if note is None and not sem:
        note = "Embedding model unreachable or no embedded chunks; showing keyword results only."
    merged = _rrf_merge(kw, sem, limit)
    return {"mode": mode, "query": query, "results": merged, "note": note}


def _record_index_note(conn: sqlite3.Connection, source: str, hits: int) -> str | None:
    if not db.records_fts_exists(conn):
        return ("record index not built yet; run pha encode or "
                "pha reindex --source all")
    if hits:
        return None
    if source == "records":
        return "no record hits"
    return None


def search(
    conn: sqlite3.Connection,
    client: ModelClient,
    cfg: Config,
    query: str,
    mode: str | None = None,
    limit: int | None = None,
    collection: str | None = None,
    allow_embed: bool = False,
    source: str = "all",
    encoder: str | None = None,
    record_kind: str | None = None,
) -> dict:
    mode = (mode or cfg.default_mode).lower()
    limit = limit or cfg.top_k
    source = (source or "all").lower()
    if mode not in ("hybrid", "keyword", "semantic"):
        raise ValueError(f"Unknown search mode {mode}; use hybrid, keyword or semantic")
    if source not in SEARCH_SOURCES:
        raise ValueError(f"Unknown search source {source}; use pages, records or all")

    if source == "records":
        if mode == "semantic":
            return {
                "mode": mode, "query": query, "results": [],
                "note": "semantic record search is not implemented yet; "
                        "use --mode keyword or --source pages",
            }
        recs = record_keyword_search(
            conn, query, limit, collection=collection,
            encoder=encoder, record_kind=record_kind,
        )
        return {"mode": mode, "query": query, "results": recs,
                "note": _record_index_note(conn, source, len(recs))}

    page_res = _page_search(
        conn, client, cfg, query, mode, limit, collection, allow_embed,
    )

    if source == "pages":
        return page_res

    recs: list[dict] = []
    record_note: str | None = None
    if mode in ("keyword", "hybrid"):
        recs = record_keyword_search(
            conn, query, limit, collection=collection,
            encoder=encoder, record_kind=record_kind,
        )
        if db.records_fts_exists(conn):
            record_note = _record_index_note(conn, source, len(recs))
    elif mode == "semantic":
        record_note = "semantic record search is not implemented yet; showing page results only"

    page_results = page_res.get("results") or []
    if not recs:
        note = page_res.get("note") or record_note
        return {"mode": mode, "query": query, "results": page_results, "note": note}

    merged = _rrf_merge_many([page_results, recs], limit)
    note_parts = [n for n in (page_res.get("note"), record_note) if n]
    return {"mode": mode, "query": query, "results": merged,
            "note": " | ".join(note_parts) if note_parts else None}
