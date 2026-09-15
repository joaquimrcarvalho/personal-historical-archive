from __future__ import annotations

import sqlite3

import numpy as np

from . import db
from . import locks
from .config import Config
from .embed import cosine, prefixed, unpack
from .model_client import ModelClient, ModelError


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


def _rrf_merge(kw: list[dict], sem: list[dict], limit: int, k: int = 60) -> list[dict]:
    """Reciprocal rank fusion of the keyword and semantic result lists."""
    scores: dict[int, float] = {}
    seen: dict[int, dict] = {}
    for lst in (kw, sem):
        for rank, r in enumerate(lst):
            cid = r["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            seen.setdefault(cid, r)
    order = sorted(scores.items(), key=lambda t: t[1], reverse=True)
    # Label honestly: with only one arm there is no fusion to claim, so a
    # degraded hybrid (semantic skipped or unavailable) reports keyword hits as
    # keyword hits rather than as "hybrid".
    label = "hybrid" if (kw and sem) else ("semantic" if sem else "keyword")
    out = []
    for cid, s in order[:limit]:
        r = dict(seen[cid])
        r["score"] = round(s, 5)
        r["source"] = label
        out.append(r)
    return out


def search(
    conn: sqlite3.Connection,
    client: ModelClient,
    cfg: Config,
    query: str,
    mode: str | None = None,
    limit: int | None = None,
    collection: str | None = None,
    allow_embed: bool = False,
) -> dict:
    mode = (mode or cfg.default_mode).lower()
    limit = limit or cfg.top_k
    if mode not in ("hybrid", "keyword", "semantic"):
        raise ValueError(f"Unknown search mode {mode!r}; use hybrid, keyword or semantic")

    if mode == "keyword":
        return {
            "mode": mode, "query": query, "results": keyword_search(conn, query, limit, collection), "note": None,
        }

    # Decide BEFORE embedding. search() already degrades gracefully when the
    # embed call fails — but by then the load has happened, and loading the
    # embed model evicts the vision model a running scan is using. Observing the
    # embed server's lock (never taking it: a search must not block, or be
    # blocked, for the length of a scan) avoids the eviction entirely.
    note: str | None = None
    sem: list[dict] = []
    embed_server = locks.embed_key(cfg)
    if not allow_embed and locks.job_running(cfg, embed_server):
        who = locks.holder_label(embed_server)
        subject = f"A model job ({who})" if who else "A model job"
        note = (f"{subject} is using the embedding server; semantic search "
                "skipped so it keeps its model loaded. Keyword results only — "
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
