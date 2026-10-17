from __future__ import annotations

import sqlite3

import numpy as np

from . import db
from . import locks
from . import notes as notes_memory
from .config import Config
from .embed import cosine, prefixed, unpack
from .model_client import ModelClient, ModelError


# `pages` are the transcriptions (raw + edited), `records` the encoder output,
# `notes` the researcher's own conclusions about them — the archive's memory.
# `all` means all three: a query about Timor must be able to surface both the
# page that names it and the note that already identified it.
SEARCH_SOURCES = ("pages", "records", "notes", "all")

# How much a *semantic-only* note hit counts against a keyword-matched one.
NOTE_ARM_WEIGHT = 0.5


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


def _decorate_note(row, source: str, score) -> dict | None:
    """A note hit: the researcher's own conclusion, never mistaken for evidence.

    `text` is the note's whole body (one row per note — the point of a note is
    its context), capped at `notes.NOTE_TEXT_CHARS` for the payload; `path` is
    the file to read when the cap bites.
    """
    if row is None:
        return None
    keys = row.keys()
    text = row["text"] or ""
    cap = notes_memory.NOTE_TEXT_CHARS
    has_snippet = "snippet" in keys and bool(row["snippet"])
    snippet = (row["snippet"] if has_snippet
               else notes_memory.body_start(text))
    return {
        "kind": "note",
        "note_id": row["note_id"] if "note_id" in keys else row["id"],
        "note": row["name"],
        "title": row["title"] if "title" in keys else None,
        "path": row["path"] if "path" in keys else None,
        "text": text[:cap],
        "text_truncated": len(text) > cap,
        "snippet": snippet,
        "score": score if score is not None else (row["bm"] if "bm" in keys else None),
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


def record_keyword_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
    collection: str | None = None,
    encoder: str | None = None,
    record_kind: str | None = None,
    fields: dict | None = None,
    ignore_case: bool = False,
) -> list[dict]:
    if query.strip():
        rows = db.record_keyword_search(
            conn, query, limit, collection=collection,
            encoder=encoder, record_kind=record_kind, fields=fields,
            ignore_case=ignore_case,
        )
    else:
        rows = db.record_field_search(
            conn, limit, collection=collection,
            encoder=encoder, record_kind=record_kind, fields=fields,
            ignore_case=ignore_case,
        )
    out = []
    for r in rows:
        d = _decorate_record(conn, r, "keyword", None)
        if d:
            out.append(d)
    return out


def _result_key(r: dict) -> tuple:
    """Identity for fusion: a page, a record and a note may share a number."""
    kind = r.get("kind")
    if kind == "record":
        return ("record", r.get("record_id"))
    if kind == "note":
        return ("note", r.get("note_id"))
    return ("page", r.get("chunk_id"))


def _rrf_merge_many(result_lists: list[list[dict]], limit: int, k: int = 60,
                    weights: list[float] | None = None) -> list[dict]:
    """RRF over page, record and/or note lists, keyed by (kind, id).

    `weights` (one per list, default 1.0) is how a weaker arm is expressed
    honestly instead of being silently trusted: a note found by the query's
    words is a stronger signal than one that merely sits near it in the
    vector space, so the semantic note arm carries a reduced weight.
    """
    scores: dict[tuple, float] = {}
    seen: dict[tuple, dict] = {}
    for i, lst in enumerate(result_lists):
        weight = 1.0 if not weights else (weights[i] if i < len(weights) else 1.0)
        for rank, r in enumerate(lst):
            key = _result_key(r)
            scores[key] = scores.get(key, 0.0) + weight / (k + rank + 1)
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


def _embed_lock_note(cfg: Config, allow_embed: bool) -> str | None:
    """The honest note when a running model job owns the embed server."""
    embed_server = locks.embed_key(cfg)
    if allow_embed or not locks.job_running(cfg, embed_server):
        return None
    who = locks.holder_label(embed_server)
    subject = f"A model job ({who})" if who else "A model job"
    return (f"{subject} is using the embedding server; semantic search "
            "skipped so it keeps its model loaded. Keyword results only - "
            "re-run when it finishes, pass --force, or point "
            "embeddings.base_url at a separate server.")


def semantic_record_search(
    conn: sqlite3.Connection,
    client: ModelClient,
    model: str,
    query: str,
    limit: int,
    collection: str | None = None,
    encoder: str | None = None,
    record_kind: str | None = None,
    fields: dict | None = None,
    ignore_case: bool = False,
) -> list[dict]:
    rows = db.all_record_embeddings(
        conn, collection=collection, encoder=encoder,
        record_kind=record_kind, embed_model=model, fields=fields,
        ignore_case=ignore_case,
    )
    if not rows:
        return []
    q = _embed_query(client, model, query)
    if q is None:
        return []
    scored = [(cosine(q, unpack(row["embedding"])), row) for row in rows]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    out = []
    for score, row in scored[:limit]:
        d = _decorate_record(conn, row, "semantic", round(score, 5))
        if d:
            out.append(d)
    return out


def _record_semantic_arm(
    conn: sqlite3.Connection,
    client: ModelClient,
    cfg: Config,
    query: str,
    limit: int,
    collection: str | None,
    encoder: str | None,
    record_kind: str | None,
    allow_embed: bool,
    fields: dict | None = None,
    ignore_case: bool = False,
) -> tuple[list[dict], str | None]:
    note = _embed_lock_note(cfg, allow_embed)
    if note is not None:
        return [], note
    hits = semantic_record_search(
        conn, client, cfg.embed_model, query, limit,
        collection=collection, encoder=encoder, record_kind=record_kind,
        fields=fields, ignore_case=ignore_case,
    )
    if not hits:
        return [], ("Record semantic search unavailable: embedding model "
                    "unreachable, no record embeddings, or run "
                    "pha reindex --source records.")
    return hits, None


def _record_hybrid(
    conn: sqlite3.Connection,
    client: ModelClient,
    cfg: Config,
    query: str,
    limit: int,
    collection: str | None,
    encoder: str | None,
    record_kind: str | None,
    allow_embed: bool,
    fields: dict | None = None,
    ignore_case: bool = False,
) -> tuple[list[dict], str | None]:
    kw = record_keyword_search(
        conn, query, limit, collection=collection,
        encoder=encoder, record_kind=record_kind, fields=fields,
        ignore_case=ignore_case,
    )
    sem, note = _record_semantic_arm(
        conn, client, cfg, query, limit, collection, encoder,
        record_kind, allow_embed, fields=fields, ignore_case=ignore_case,
    )
    if not kw and not sem:
        return [], note or _record_index_note(conn, "records", 0)
    if not sem:
        for r in kw:
            r["source"] = "keyword"
        return kw, note
    if not kw:
        return sem, note
    merged = _rrf_merge_many([kw, sem], limit)
    for r in merged:
        r["source"] = "hybrid"
    return merged, note


# --------------------------------------------------------------------------- notes (memory)

def note_keyword_search(conn: sqlite3.Connection, query: str, limit: int) -> list[dict]:
    out = []
    for row in db.notes_keyword_search(conn, query, limit):
        hit = _decorate_note(row, "keyword", None)
        if hit:
            out.append(hit)
    return out


def note_semantic_search(conn: sqlite3.Connection, client: ModelClient, model: str,
                         query: str, limit: int) -> list[dict]:
    rows = db.all_note_embeddings(conn, embed_model=model)
    if not rows or client is None:
        return []
    q = _embed_query(client, model, query)
    if q is None:
        return []
    scored = [(cosine(q, unpack(row["embedding"])), int(row["id"])) for row in rows]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    out = []
    for score, note_id in scored[:limit]:
        hit = _decorate_note(db.get_note(conn, note_id=note_id), "semantic",
                             round(score, 5))
        if hit:
            out.append(hit)
    return out


def _note_index_note(conn: sqlite3.Connection, hits: int) -> str | None:
    if not db.notes_fts_exists(conn):
        return ("note index not built yet; run `pha notes index` "
                "(the next `pha search` also builds it)")
    if not hits:
        return "no note hits"
    return None


def _note_arms(conn: sqlite3.Connection, client: ModelClient, cfg: Config,
               query: str, mode: str, limit: int,
               allow_embed: bool) -> tuple[list[dict], list[dict], str | None]:
    """The notes' two arms: (keyword hits, semantic hits, degradation note).

    Kept apart — rather than pre-fused — so the caller can weight them: a note
    whose text contains the query's terms is evidence the question was already
    addressed; a long note that merely lands near the query vector is a hint.
    Measured on the jesuit archive with the shipped nomic q4 embedder, an
    unrelated 40 kB note and the note that literally names Timor score 0.667
    vs 0.661, so the semantic arm is worth keeping for recall but must not
    outrank a lexical match (see `NOTE_ARM_WEIGHT`).
    """
    kw: list[dict] = []
    if mode != "semantic":
        kw = note_keyword_search(conn, query, limit)
    note: str | None = None
    sem: list[dict] = []
    if mode != "keyword":
        note = _embed_lock_note(cfg, allow_embed)
        if note is None:
            sem = note_semantic_search(conn, client, cfg.embed_model, query, limit)
    return kw, sem, note


def _note_search(
    conn: sqlite3.Connection,
    client: ModelClient,
    cfg: Config,
    query: str,
    mode: str,
    limit: int,
    allow_embed: bool,
) -> dict:
    """Notes only — the archive's memory arm of the search."""
    kw, sem, note = _note_arms(conn, client, cfg, query, mode, limit, allow_embed)
    if mode == "keyword":
        return {"mode": mode, "query": query, "results": kw,
                "note": _note_index_note(conn, len(kw))}
    if mode == "semantic":
        if note is None and not sem:
            note = ("Note semantic search unavailable: embedding model unreachable "
                    "or notes not embedded yet (run `pha notes index`).")
        return {"mode": mode, "query": query, "results": sem, "note": note}
    if not sem:
        return {"mode": mode, "query": query, "results": kw,
                "note": note or _note_index_note(conn, len(kw))}
    if not kw:
        return {"mode": mode, "query": query, "results": sem, "note": note}
    merged = _rrf_merge_many([kw, sem], limit, weights=[1.0, NOTE_ARM_WEIGHT])
    for r in merged:
        r["source"] = "hybrid"
    return {"mode": mode, "query": query, "results": merged, "note": note}


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
    fields: dict | None = None,
    ignore_case: bool = False,
) -> dict:
    mode = (mode or cfg.default_mode).lower()
    limit = limit or cfg.top_k
    source = (source or "all").lower()
    query_text = (query or "").strip()
    if mode not in ("hybrid", "keyword", "semantic"):
        raise ValueError(f"Unknown search mode {mode}; use hybrid, keyword or semantic")
    if source not in SEARCH_SOURCES:
        raise ValueError(f"Unknown search source {source}; use pages, records, notes or all")
    if fields and source not in ("records", "all"):
        raise ValueError("--field only applies to record search (--source records or all)")
    if not query_text:
        if source in ("pages", "notes") or not fields:
            raise ValueError("a query is required unless --field is given with record search")
        recs = record_keyword_search(
            conn, "", limit, collection=collection,
            encoder=encoder, record_kind=record_kind, fields=fields,
            ignore_case=ignore_case,
        )
        return {"mode": mode, "query": query, "results": recs,
                "note": "no text query; records filtered by the given field(s)"}

    if source == "records":
        if mode == "keyword":
            recs = record_keyword_search(
                conn, query, limit, collection=collection,
                encoder=encoder, record_kind=record_kind, fields=fields,
                ignore_case=ignore_case,
            )
            return {"mode": mode, "query": query, "results": recs,
                    "note": _record_index_note(conn, source, len(recs))}
        if mode == "semantic":
            recs, note = _record_semantic_arm(
                conn, client, cfg, query, limit, collection,
                encoder, record_kind, allow_embed, fields=fields,
                ignore_case=ignore_case,
            )
            return {"mode": mode, "query": query, "results": recs, "note": note}
        recs, note = _record_hybrid(
            conn, client, cfg, query, limit, collection,
            encoder, record_kind, allow_embed, fields=fields,
            ignore_case=ignore_case,
        )
        return {"mode": mode, "query": query, "results": recs, "note": note}

    if source == "notes":
        return _note_search(conn, client, cfg, query, mode, limit, allow_embed)

    page_res = _page_search(
        conn, client, cfg, query, mode, limit, collection, allow_embed,
    )

    if source == "pages":
        return page_res

    recs: list[dict] = []
    record_note: str | None = None
    if mode == "keyword":
        recs = record_keyword_search(
            conn, query, limit, collection=collection,
            encoder=encoder, record_kind=record_kind, fields=fields,
            ignore_case=ignore_case,
        )
        if db.records_fts_exists(conn):
            record_note = _record_index_note(conn, source, len(recs))
    elif mode == "semantic":
        recs, record_note = _record_semantic_arm(
            conn, client, cfg, query, limit, collection,
            encoder, record_kind, allow_embed, fields=fields,
            ignore_case=ignore_case,
        )
    else:
        recs, record_note = _record_hybrid(
            conn, client, cfg, query, limit, collection,
            encoder, record_kind, allow_embed, fields=fields,
            ignore_case=ignore_case,
        )

    # `all` also means the notes: what the researcher already concluded about
    # these sources is part of what the archive "has" on a question. Note hits
    # stay marked `kind: "note"` through the merge, so a caller can keep a
    # conclusion distinct from the page it rests on.
    note_kw: list[dict] = []
    note_sem: list[dict] = []
    if source == "all":
        note_kw, note_sem, _note_note = _note_arms(
            conn, client, cfg, query, mode, limit, allow_embed)

    page_results = page_res.get("results") or []
    # The two note arms enter the fusion with their own weights, so a
    # semantically-near note does not outrank a page that names the query.
    arms = [(lst, weight) for lst, weight in (
        (page_results, 1.0), (recs, 1.0),
        (note_kw, 1.0), (note_sem, NOTE_ARM_WEIGHT),
    ) if lst]
    note_parts = [n for n in (page_res.get("note"), record_note) if n]
    result_note = " | ".join(note_parts) if note_parts else None
    if not arms:
        return {"mode": mode, "query": query, "results": [], "note": result_note}
    if len(arms) == 1:
        return {"mode": mode, "query": query, "results": arms[0][0],
                "note": result_note}
    return {"mode": mode, "query": query,
            "results": _rrf_merge_many([lst for lst, _ in arms], limit,
                                       weights=[w for _, w in arms]),
            "note": result_note}
