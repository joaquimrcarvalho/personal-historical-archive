from __future__ import annotations

from fastmcp import FastMCP

from . import db
from .config import Config
from .ingest import scan_once
from .model_client import ModelClient
from .search import search as run_search


def make_server(cfg: Config) -> FastMCP:
    mcp = FastMCP(
        "manuscript-archive",
        instructions=(
            "This server provides access to a local archive of manuscript PDFs and images "
            "that have been transcribed page-by-page by a vision model. Use `search` to "
            "find relevant passages, `get_document` to read full extracted text, "
            "`list_documents` to browse, `scan_now` after new files are dropped into the "
            "dropbox, and `extraction_status` to see ingestion progress."
        ),
    )

    @mcp.tool()
    def search(query: str, mode: str = "hybrid", limit: int = 10, collection: str | None = None) -> list[dict]:
        """Search the extracted manuscript text and return ranked passages.

        Args:
            query: free-text search query (keyword terms or a natural-language description).
            mode: hybrid (keyword + semantic, default), keyword (FTS5), or semantic (embeddings).
            limit: maximum number of results (1-50).
            collection: restrict to a collection or directory, e.g. 'documents',
                'COLX' (resolves to collections/COLX), or 'collections/COLX'.
        Returns:
            Ranked passages with document id/name, collection, page number, snippet, and full chunk text.
        """
        if mode not in ("hybrid", "keyword", "semantic"):
            mode = "hybrid"
        limit = max(1, min(int(limit), 50))
        conn = db.connect(cfg.db_path)
        client = ModelClient(cfg.embed_base_url, timeout_s=cfg.embed_timeout_s)
        try:
            res = run_search(conn, client, cfg, query, mode=mode, limit=limit, collection=collection)
        finally:
            client.close()
            conn.close()
        return res["results"]

    @mcp.tool()
    def get_document(document_id: int, max_chars: int = 20000) -> dict:
        """Return metadata and the extracted per-page text of one document.

        Args:
            document_id: id from `search` or `list_documents`.
            max_chars: cap on how many characters of extracted text to return.
        """
        conn = db.connect(cfg.db_path)
        try:
            doc = db.get_document(conn, document_id)
            if not doc:
                return {"error": f"no document with id {document_id}"}
            pages = db.get_pages(conn, document_id)
            out = {k: doc[k] for k in doc.keys()}
            texts: list[str] = []
            used = 0
            truncated = False
            for p in pages:
                t = p["raw_text"] or ""
                if used >= max_chars:
                    truncated = True
                    break
                take = t[: max_chars - used]
                texts.append(f"## Page {p['page_no']}\n\n{take}")
                used += len(take)
            if truncated:
                texts.append(f"… (truncated at {max_chars} chars; ask again with a larger max_chars)")
            out["text"] = "\n\n".join(texts)
            out["pages"] = len(pages)
            return out
        finally:
            conn.close()

    @mcp.tool()
    def list_documents(status: str | None = None, limit: int = 100, collection: str | None = None) -> list[dict]:
        """List documents in the archive.

        Args:
            status: optional filter: done, error, processing, pending.
            limit: maximum number of entries (1-500).
            collection: restrict to a collection/directory (see `search`).
        """
        limit = max(1, min(int(limit), 500))
        if status and status not in ("done", "error", "processing", "pending"):
            status = None
        conn = db.connect(cfg.db_path)
        try:
            docs = db.list_documents(conn, status=status, limit=limit, collection=collection)
            return [{k: d[k] for k in d.keys()} for d in docs]
        finally:
            conn.close()

    @mcp.tool()
    def scan_now() -> dict:
        """Scan the dropbox for new or changed files, extract text with the vision model,
        and index them. Call this after the user has dropped new files."""
        client = ModelClient(cfg.vision_base_url, timeout_s=cfg.vision_timeout_s)
        try:
            return scan_once(cfg, client, verbose=False)
        finally:
            client.close()

    @mcp.tool()
    def extraction_status() -> dict:
        """Summary of the archive: documents by status, pages extracted, chunks indexed."""
        conn = db.connect(cfg.db_path)
        try:
            return db.summary(conn)
        finally:
            conn.close()

    return mcp


def main(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000) -> None:
    cfg = Config.load()
    cfg.ensure_dirs()
    mcp = make_server(cfg)
    if transport == "sse":
        mcp.run(transport="sse", host=host, port=port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
