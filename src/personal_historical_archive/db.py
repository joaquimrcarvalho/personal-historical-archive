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
    dir_path TEXT,
    palaeographer TEXT,
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
    variant TEXT NOT NULL DEFAULT 'raw',
    UNIQUE (page_id, chunk_no, variant)
);
CREATE TABLE IF NOT EXISTS page_edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    editor TEXT NOT NULL,
    text TEXT,
    raw_sha TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    updated_at REAL,
    UNIQUE (page_id, editor)
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text);
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    encoder TEXT NOT NULL,
    kind TEXT,
    data TEXT NOT NULL,
    source TEXT,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_pages_doc ON pages(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_edits_page ON page_edits(page_id);
CREATE INDEX IF NOT EXISTS idx_records_doc ON records(document_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=20000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    migrate(conn)
    conn.commit()
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the first release, if missing.

    DDL needs an exclusive lock; retry in case a watcher/scan is mid-write.
    """
    cols = [r[1] for r in conn.execute("PRAGMA table_info(documents)")]
    statements = []
    if "dir_path" not in cols:
        statements.append("ALTER TABLE documents ADD COLUMN dir_path TEXT")
    if "palaeographer" not in cols:
        statements.append("ALTER TABLE documents ADD COLUMN palaeographer TEXT")
    if "editor" not in cols:
        statements.append("ALTER TABLE documents ADD COLUMN editor TEXT")
    if "encoder" not in cols:
        statements.append("ALTER TABLE documents ADD COLUMN encoder TEXT")
    if statements:
        import time

        last_err: Exception | None = None
        for attempt in range(15):
            try:
                for stmt in statements:
                    conn.execute(stmt)
                break
            except sqlite3.OperationalError as e:
                last_err = e
                time.sleep(3)
        else:
            raise last_err or RuntimeError("migration failed")
    ccols = [r[1] for r in conn.execute("PRAGMA table_info(chunks)")]
    if "variant" not in ccols:
        for attempt in range(15):
            try:
                conn.execute("ALTER TABLE chunks ADD COLUMN variant TEXT NOT NULL DEFAULT 'raw'")
                break
            except sqlite3.OperationalError as e:
                if attempt >= 14:
                    raise
                time.sleep(3)
    # page status vocabulary: failed pages are 'waiting' (retried on next scan).
    # Only writes when rows need converting, so normal connections stay read-only.
    if conn.execute("SELECT COUNT(*) AS n FROM pages WHERE status = 'error'").fetchone()["n"]:
        _write(conn, "UPDATE pages SET status = 'waiting' WHERE status = 'error'")
        conn.commit()


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
    dir_path: str = "",
    palaeographer: str | None = None,
    editor: str | None = None,
) -> int:
    cur = _write(
        conn,
        """INSERT INTO documents (filename, path, sha256, size_bytes, mtime, kind, dir_path, palaeographer, editor, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (filename, path, sha256, size_bytes, mtime, kind, dir_path, palaeographer, editor, now, now),
    )
    return int(cur.lastrowid)


def backfill_dir_path(conn: sqlite3.Connection, dropbox: Path) -> None:
    """Fill dir_path for rows created before the column existed."""
    rows = conn.execute("SELECT id, path FROM documents WHERE dir_path IS NULL").fetchall()
    if not rows:
        return
    root = str(Path(dropbox).resolve())
    for r in rows:
        p = Path(r["path"])
        try:
            rel = str(p.parent.relative_to(root)) if str(p.parent) != root else ""
        except ValueError:
            rel = ""
        conn.execute("UPDATE documents SET dir_path = ? WHERE id = ?", (rel, r["id"]))
    conn.commit()


def update_document(conn: sqlite3.Connection, doc_id: int, **fields) -> None:
    if not fields:
        return
    keys = ", ".join(f"{k} = ?" for k in fields)
    _write(conn, f"UPDATE documents SET {keys}, updated_at = ? WHERE id = ?",
           (*fields.values(), _now(), doc_id))


def touch_document(conn: sqlite3.Connection, doc_id: int) -> None:
    """Per-page heartbeat so interrupted runs are detectable."""
    _write(conn, "UPDATE documents SET updated_at = ? WHERE id = ?", (_now(), doc_id))


def set_document_status(
    conn: sqlite3.Connection,
    doc_id: int,
    status: str,
    error: str | None = None,
    prompt_source: str | None = None,
) -> None:
    _write(
        conn,
        "UPDATE documents SET status = ?, error = ?, prompt_source = COALESCE(?, prompt_source), updated_at = ? WHERE id = ?",
        (status, error, prompt_source, _now(), doc_id),
    )


def delete_document(conn: sqlite3.Connection, doc_id: int) -> None:
    conn.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE document_id = ?)", (doc_id,))
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


def _dir_clause(value: str | None, alias: str = "d") -> tuple[str, list]:
    """SQL fragment matching documents under a collection/dir.

    'documents' matches the documents/ tree; 'COLX' matches collections/COLX
    (bare names are resolved against the collections/ prefix too);
    'collections/COLX' and nested paths match exactly or as a prefix.
    """
    if not value:
        return "", []
    col = "dir_path" if not alias else f"{alias}.dir_path"
    parts = [value]
    if "/" not in value:
        parts.append("collections/" + value)
    clauses, params = [], []
    for p in parts:
        clauses.append(f"({col} = ? OR {col} LIKE ?)")
        params += [p, p + "/%"]
    return " AND (" + " OR ".join(clauses) + ")", params


def list_documents(
    conn: sqlite3.Connection,
    status: str | None = None,
    limit: int = 100,
    collection: str | None = None,
) -> list[sqlite3.Row]:
    clause, params = _dir_clause(collection, alias="")
    where = []
    if status:
        where.append("status = ?")
        params.append(status)
    if clause:
        where.append(clause.lstrip(" AND "))
    sql = "SELECT * FROM documents"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


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
    _write(
        conn,
        "INSERT OR IGNORE INTO pages (document_id, page_no) VALUES (?, ?)",
        (doc_id, page_no),
    )
    # INSERT OR IGNORE leaves lastrowid stale when the row already exists
    # (it returns the previous insert's rowid), so always look the row up.
    row = conn.execute(
        "SELECT id FROM pages WHERE document_id = ? AND page_no = ?", (doc_id, page_no)
    ).fetchone()
    if row is None:
        raise RuntimeError(f"failed to create/read page {page_no} of document {doc_id}")
    return int(row["id"])


def set_page_result(
    conn: sqlite3.Connection, page_id: int, raw_text: str | None = None, error: str | None = None
) -> None:
    if error is not None:
        # 'waiting': the page will be retried on the next scan; the message
        # stays in the error column for diagnostics only.
        _write(conn, "UPDATE pages SET error = ?, status = 'waiting' WHERE id = ?", (error, page_id))
    else:
        _write(conn, "UPDATE pages SET raw_text = ?, error = NULL, status = 'done' WHERE id = ?", (raw_text, page_id))


def get_pages(conn: sqlite3.Connection, doc_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM pages WHERE document_id = ? ORDER BY page_no", (doc_id,)
    ).fetchall()


def document_pages(conn: sqlite3.Connection, doc_id: int) -> list[sqlite3.Row]:
    return get_pages(conn, doc_id)


# --------------------------------------------------------------------------- page edits (editors)

def get_page_edit(
    conn: sqlite3.Connection, page_id: int, editor: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM page_edits WHERE page_id = ? AND editor = ?", (page_id, editor)
    ).fetchone()


def set_page_edit(
    conn: sqlite3.Connection,
    page_id: int,
    editor: str,
    text: str | None = None,
    error: str | None = None,
    raw_sha: str | None = None,
) -> None:
    import time as _t

    if error is not None:
        _write(
            conn,
            """INSERT INTO page_edits (page_id, editor, text, raw_sha, status, error, updated_at)
               VALUES (?, ?, NULL, ?, 'waiting', ?, ?)
               ON CONFLICT(page_id, editor) DO UPDATE SET
                 text = NULL, raw_sha = ?, status = 'waiting', error = ?, updated_at = ?""",
            (page_id, editor, raw_sha, error, _t.time(), raw_sha, error, _t.time()),
        )
    else:
        _write(
            conn,
            """INSERT INTO page_edits (page_id, editor, text, raw_sha, status, error, updated_at)
               VALUES (?, ?, ?, ?, 'done', NULL, ?)
               ON CONFLICT(page_id, editor) DO UPDATE SET
                 text = ?, raw_sha = ?, status = 'done', error = NULL, updated_at = ?""",
            (page_id, editor, text, raw_sha, _t.time(), text, raw_sha, _t.time()),
        )


def edits_for_document(
    conn: sqlite3.Connection, doc_id: int, editor: str
) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT pe.* FROM page_edits pe JOIN pages p ON p.id = pe.page_id
           WHERE p.document_id = ? AND pe.editor = ?""",
        (doc_id, editor),
    ).fetchall()


def clear_page_edits(conn: sqlite3.Connection, doc_id: int, editor: str) -> None:
    _write(
        conn,
        """DELETE FROM page_edits WHERE editor = ? AND page_id IN
           (SELECT id FROM pages WHERE document_id = ?)""",
        (editor, doc_id),
    )


# --------------------------------------------------------------------------- chunks

def clear_chunks(conn: sqlite3.Connection, doc_id: int) -> None:
    _write(
        conn,
        "DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE document_id = ?)",
        (doc_id,),
    )
    _write(conn, "DELETE FROM chunks WHERE document_id = ?", (doc_id,))


def add_chunk(
    conn: sqlite3.Connection,
    doc_id: int,
    page_id: int,
    chunk_no: int,
    text: str,
    embedding: bytes | None,
    variant: str = "raw",
) -> None:
    cur = _write(
        conn,
        "INSERT INTO chunks (document_id, page_id, chunk_no, text, embedding, variant) VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, page_id, chunk_no, text, embedding, variant),
    )
    _write(conn, "INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (cur.lastrowid, text))


def all_embeddings(
    conn: sqlite3.Connection, collection: str | None = None
) -> list[tuple[int, bytes]]:
    clause, params = _dir_clause(collection)
    sql = (
        "SELECT c.id, c.embedding FROM chunks c "
        "JOIN documents d ON d.id = c.document_id "
        "WHERE c.embedding IS NOT NULL"
    )
    if clause:
        sql += clause
    return conn.execute(sql, params).fetchall()


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


def keyword_search(
    conn: sqlite3.Connection, query: str, limit: int = 10, collection: str | None = None
) -> list[sqlite3.Row]:
    fts_q = build_fts_query(query)
    clause, params = _dir_clause(collection)
    return conn.execute(
        """SELECT c.id AS chunk_id, c.document_id, c.page_id, p.page_no, c.chunk_no, c.text, c.variant,
                  d.dir_path,
                  -bm25(chunks_fts) AS bm,
                  snippet(chunks_fts, 0, '…', '…', '…', 28) AS snippet
           FROM chunks_fts
           JOIN chunks c ON c.id = chunks_fts.rowid
           JOIN pages p ON p.id = c.page_id
           JOIN documents d ON d.id = c.document_id
           WHERE chunks_fts MATCH ?"""
        + clause
        + """
           ORDER BY bm25(chunks_fts)
           LIMIT ?""",
        (fts_q, *params, limit),
    ).fetchall()


# --------------------------------------------------------------------------- misc

def _now() -> float:
    import time

    return time.time()


def _write(conn: sqlite3.Connection, sql: str, params=()):
    """Execute a write, retrying through transient 'database is locked'
    (watcher + manual scans + monitor queries share the DB)."""
    import time as _t

    for attempt in range(6):
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or attempt >= 5:
                raise
            _t.sleep(1 + attempt * 2)


# --------------------------------------------------------------------------- records (encoders)

def clear_records(conn: sqlite3.Connection, doc_id: int, encoder: str) -> None:
    _write(conn, "DELETE FROM records WHERE document_id = ? AND encoder = ?", (doc_id, encoder))


def add_record(
    conn: sqlite3.Connection,
    doc_id: int,
    encoder: str,
    kind: str | None,
    data: str,
    source: str | None = None,
) -> None:
    import time as _t

    _write(
        conn,
        "INSERT INTO records (document_id, encoder, kind, data, source, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, encoder, kind, data, source, _t.time()),
    )


def records_for_document(conn: sqlite3.Connection, doc_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM records WHERE document_id = ? ORDER BY id", (doc_id,)
    ).fetchall()
