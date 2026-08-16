from __future__ import annotations

import re
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER,
    mtime REAL,
    kind TEXT,
    page_count INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    prompt_source TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_no INTEGER NOT NULL,
    raw_text TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    UNIQUE (document_id, page_no)
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    chunk_no INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB,
    UNIQUE (page_id, chunk_no)
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text);
CREATE INDEX IF NOT EXISTS idx_pages_doc ON pages(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=20000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# --------------------------------------------------------------------------- documents

def get_document_by_path(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM documents WHERE path = ?", (path,)).fetchone()


def get_document(conn: sqlite3.Connection, doc_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()


def add_document(
    conn: sqlite3.Connection,
    *,
    filename: str,
    path: str,
    sha256: str,
    size_bytes: int,
    mtime: float,
    kind: str,
    now: str,
) -> int:
    cur = conn.execute(
        """INSERT INTO documents (filename, path, sha256, size_bytes, mtime, kind, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (filename, path, sha256, size_bytes, mtime, kind, now, now),
    )
    return int(cur.lastrowid)


def update_document(conn: sqlite3.Connection, doc_id: int, **fields) -> None:
    if not fields:
        return
    keys = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE documents SET {keys}, updated_at = ? WHERE id = ?",
                 (*fields.values(), _now(), doc_id))


def set_document_status(
    conn: sqlite3.Connection,
    doc_id: int,
    status: str,
    error: str | None = None,
    prompt_source: str | None = None,
) -> None:
    conn.execute(
        "UPDATE documents SET status = ?, error = ?, prompt_source = COALESCE(?, prompt_source), updated_at = ? WHERE id = ?",
        (status, error, prompt_source, _now(), doc_id),
    )


def delete_document(conn: sqlite3.Connection, doc_id: int) -> None:
    conn.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE document_id = ?)", (doc_id,))
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


def list_documents(conn: sqlite3.Connection, status: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
    if status:
        return conn.execute(
            "SELECT * FROM documents WHERE status = ? ORDER BY updated_at DESC LIMIT ?", (status, limit)
        ).fetchall()
    return conn.execute("SELECT * FROM documents ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()


def summary(conn: sqlite3.Connection) -> dict:
    docs = conn.execute("SELECT status, COUNT(*) n FROM documents GROUP BY status").fetchall()
    pages = conn.execute("SELECT COUNT(*) n FROM pages WHERE status = 'done'").fetchone()["n"]
    chunks = conn.execute("SELECT COUNT(*) n FROM chunks").fetchone()["n"]
    embedded = conn.execute("SELECT COUNT(*) n FROM chunks WHERE embedding IS NOT NULL").fetchone()["n"]
    return {
        "documents": {r["status"]: r["n"] for r in docs},
        "pages_done": pages,
        "chunks": chunks,
        "chunks_embedded": embedded,
    }


# --------------------------------------------------------------------------- pages

def add_page(conn: sqlite3.Connection, doc_id: int, page_no: int) -> int:
    cur = conn.execute(
        "INSERT OR IGNORE INTO pages (document_id, page_no) VALUES (?, ?)", (doc_id, page_no)
    )
    if cur.lastrowid:
        return int(cur.lastrowid)
    row = conn.execute(
        "SELECT id FROM pages WHERE document_id = ? AND page_no = ?", (doc_id, page_no)
    ).fetchone()
    return int(row["id"])


def set_page_result(
    conn: sqlite3.Connection, page_id: int, raw_text: str | None = None, error: str | None = None
) -> None:
    if error is not None:
        conn.execute("UPDATE pages SET error = ?, status = 'error' WHERE id = ?", (error, page_id))
    else:
        conn.execute("UPDATE pages SET raw_text = ?, error = NULL, status = 'done' WHERE id = ?", (raw_text, page_id))


def get_pages(conn: sqlite3.Connection, doc_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM pages WHERE document_id = ? ORDER BY page_no", (doc_id,)
    ).fetchall()


def document_pages(conn: sqlite3.Connection, doc_id: int) -> list[sqlite3.Row]:
    return get_pages(conn, doc_id)


# --------------------------------------------------------------------------- chunks

def clear_chunks(conn: sqlite3.Connection, doc_id: int) -> None:
    conn.execute(
        "DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE document_id = ?)", (doc_id,)
    )
    conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))


def add_chunk(
    conn: sqlite3.Connection, doc_id: int, page_id: int, chunk_no: int, text: str, embedding: bytes | None
) -> None:
    cur = conn.execute(
        "INSERT INTO chunks (document_id, page_id, chunk_no, text, embedding) VALUES (?, ?, ?, ?, ?)",
        (doc_id, page_id, chunk_no, text, embedding),
    )
    conn.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (cur.lastrowid, text))


def all_embeddings(conn: sqlite3.Connection) -> list[tuple[int, bytes]]:
    return conn.execute(
        "SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL"
    ).fetchall()


def get_chunk_text(conn: sqlite3.Connection, chunk_id: int) -> str | None:
    row = conn.execute("SELECT text FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
    return row["text"] if row else None


# --------------------------------------------------------------------------- search

def build_fts_query(query: str) -> str:
    """Wrap each term in quotes so FTS5 treats it literally; supports "phrases"."""
    tokens = re.findall(r'"[^"]+"|\S+', query)
    parts = []
    for t in tokens:
        if t.startswith('"') and t.endswith('"') and len(t) > 2:
            parts.append(t)
        else:
            parts.append('"' + t.replace('"', "") + '"')
    return " AND ".join(parts) or '""'


def keyword_search(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[sqlite3.Row]:
    fts_q = build_fts_query(query)
    return conn.execute(
        """SELECT c.id AS chunk_id, c.document_id, c.page_id, p.page_no, c.chunk_no, c.text,
                  -bm25(chunks_fts) AS bm,
                  snippet(chunks_fts, 0, '…', '…', '…', 28) AS snippet
           FROM chunks_fts
           JOIN chunks c ON c.id = chunks_fts.rowid
           JOIN pages p ON p.id = c.page_id
           WHERE chunks_fts MATCH ?
           ORDER BY bm25(chunks_fts)
           LIMIT ?""",
        (fts_q, limit),
    ).fetchall()


# --------------------------------------------------------------------------- misc

def _now() -> float:
    import time

    return time.time()
