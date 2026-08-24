from __future__ import annotations

from fastmcp import FastMCP

from . import db
from .config import Config
from .ingest import make_vision_client, scan_once
from .model_client import ModelClient
from .search import search as run_search


def make_server(cfg: Config) -> FastMCP:
    mcp = FastMCP(
        "personal-historical-archive",
        instructions=(
            "This server provides access to a local archive of historical documents (manuscripts, old books, maps, ...) "
            "that have been transcribed page-by-page by a vision model. Use `pha_search` to "
            "find relevant passages, `pha_get_document` to read full extracted text, "
            "`pha_list_documents` to browse, `pha_upload` to add a document/collection file "
            "into the dropbox (send its base64 bytes — the client and server may be on "
            "different machines), `pha_scan_now` after new files are dropped into the "
            "dropbox, and `pha_extraction_status` to see ingestion progress."
        ),
    )

    @mcp.tool()
    def pha_search(query: str, mode: str = "hybrid", limit: int = 10, collection: str | None = None) -> list[dict]:
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
    def pha_get_document(document_id: int, max_chars: int = 20000) -> dict:
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
    def pha_get_page(document_id: int, page_no: int, include_image: bool = False) -> dict:
        """Return every version of ONE page of a document.

        Args:
            document_id: id from `pha_list_documents`.
            page_no: the page number (1-based PDF/page number).
            include_image: also return the cached page render as base64 JPEG
                (the image that was sent to the model to read this page).

        Returns, when present:
            transcribed: the faithful raw transcription (palaeographer output).
            edited: dict of editor-id -> edited text (optional editor pass).
            encoded: list of structured records derived from this page
                (encoder output; records tag their source page number).
            image_base64: JPEG bytes of the page render (if include_image and
                the render exists), plus its JPEG size.
        """
        import base64
        from pathlib import Path
        conn = db.connect(cfg.db_path)
        try:
            doc = db.get_document(conn, document_id)
            if not doc:
                return {"error": f"no document with id {document_id}"}
            page = None
            for p in db.get_pages(conn, document_id):
                if p["page_no"] == page_no:
                    page = p
                    break
            if page is None:
                return {"error": f"no page {page_no} in document {document_id}",
                        "page_count": doc["page_count"]}
            out = {
                "document_id": document_id,
                "filename": doc["filename"],
                "page_no": page_no,
                "status": page["status"],
                "transcribed": page["raw_text"] or None,
            }
            # edited versions (all editors that produced one)
            edits = conn.execute(
                "SELECT editor, text FROM page_edits WHERE page_id = ? AND status='done'",
                (page["id"],)).fetchall()
            out["edited"] = {r["editor"]: r["text"] for r in edits} or None
            # encoded records for this page (source is the page number)
            recs = conn.execute(
                "SELECT id, encoder, kind, data FROM records WHERE document_id=? AND source=?",
                (document_id, str(page_no))).fetchall()
            out["encoded"] = []
            for r in recs:
                try:
                    out["encoded"].append({"id": r["id"], "encoder": r["encoder"],
                                           "kind": r["kind"], "data": r["data"]})
                except Exception:
                    continue
            out["encoded"] = out["encoded"] or None
            # cached page render (the image the model actually read)
            if include_image:
                render_dir = cfg.renders / doc["sha256"]
                img = render_dir / f"p{page_no:03d}.jpg"
                if img.exists():
                    blob = img.read_bytes()
                    out["image_base64"] = base64.b64encode(blob).decode()
                    out["image_bytes"] = len(blob)
                else:
                    out["image_base64"] = None
                    out["image_bytes"] = 0
                    out["render_missing"] = str(render_dir)
            return out
        finally:
            conn.close()

    @mcp.tool()
    def pha_list_documents(status: str | None = None, limit: int = 100, collection: str | None = None) -> list[dict]:
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
    def pha_scan_now() -> dict:
        """Scan the dropbox for new or changed files, extract text with the active
        palaeographer (vision model), and index them. Call this after the user has
        dropped new files."""
        client, pal = make_vision_client(cfg)
        try:
            return scan_once(cfg, client, pal, verbose=False)
        finally:
            client.close()

    @mcp.tool()
    def pha_extraction_status() -> dict:
        """Summary of the archive: documents by status, pages extracted, chunks indexed."""
        conn = db.connect(cfg.db_path)
        try:
            return db.summary(conn)
        finally:
            conn.close()

    @mcp.tool()
    def pha_schema() -> dict:
        """Return the real SQLite schema of the archive: columns per table plus
        the foreign-key joins between them.

        Read this BEFORE writing your own SQL against the archive. Note in
        particular that:
          - `pages` links to a document via `document_id` (NOT `doc_id`), and
            holds `page_no`, `raw_text`, `status`, `error` — but NO `path` or
            `sha256`.
          - `path` and `sha256` live on `documents`.
          - `page_edits` links to `pages` via `page_id` and keys on
            (page_id, editor); it holds the edited `text` and `raw_sha`.
        """
        conn = db.connect(cfg.db_path)
        try:
            return db.schema(conn)
        finally:
            conn.close()

    @mcp.tool()
    def pha_upload(kind: str, name: str, content_b64: str, replace: bool = False,
                   merge: bool = False) -> dict:
        """Upload ONE document or collection file into this server's dropbox.

        Call this when you have a file on YOUR (client) machine and want it in
        the dropbox of the machine running this MCP server. Because the client
        and server are different machines, you send the file's bytes as
        base64 (content_b64) plus a destination name, instead of a path.

        Args:
            kind: 'document' (a single file, e.g. a PDF or image) or
                  'collection' (a file that belongs to a collection directory).
            name: the destination filename in the dropbox. For a collection,
                  use a path like 'COLX/filename.pdf' to place it inside
                  collections/COLX/. For a document, a bare filename goes to
                  documents/.
            content_b64: the file bytes, base64-encoded.
            replace: overwrite the destination if it exists.
            merge: copy into / update the existing destination instead of
                   refusing.

        Returns a report with the destination path in the dropbox.
        """
        import base64
        from .upload import _resolve_dest_b64, save_upload

        dest = _resolve_dest_b64(cfg, kind, name)
        exists = dest.exists()
        if exists and not (replace or merge):
            raise FileExistsError(
                f"already exists in the dropbox: {dest} (pass replace=True to "
                f"overwrite, or merge=True to update)"
            )
        blob = base64.b64decode(content_b64)
        return save_upload(cfg, kind, name, blob, dest, exists, replace, merge)

    @mcp.tool()
    def pha_palaeographers() -> list[dict]:
        """List the configured palaeographer (vision) models and the active default."""
        out = []
        for pid in sorted(cfg.palaeographers):
            p = cfg.palaeographers[pid]
            out.append({"id": pid, "description": p.description or "",
                        "model": p.model, "base_url": p.base_url,
                        "active": pid == cfg.active_palaeographer})
        return out

    @mcp.tool()
    def pha_editors() -> list[dict]:
        """List the configured editor (text) models."""
        return [{"id": eid, "description": e.description or "", "model": e.model,
                 "base_url": e.base_url}
                for eid, e in sorted(cfg.editors.items())]

    @mcp.tool()
    def pha_encoders(document_relpath: str) -> list[dict]:
        """List the encoders that apply to a document in the dropbox.

        Args:
            document_relpath: path of the document relative to the dropbox,
                e.g. "collections/pfister-notices/...t1.pdf".
        """
        from .extract import encoder_files_for
        from pathlib import Path
        p = (cfg.dropbox / document_relpath).resolve()
        stem = p.stem
        parent = p.parent
        return [{"file": str(f.relative_to(cfg.dropbox))} for f in
                encoder_files_for(stem, parent, cfg.dropbox)]

    @mcp.tool()
    def pha_collection_config(document_relpath: str) -> dict:
        """Resolved palaeographer, editor, and prompt for a document/collection.

        Args:
            document_relpath: path relative to the dropbox, e.g.
                "collections/letters-from-missons" (a collection dir) or a
                specific file inside it.
        """
        from .extract import (resolve_editor_id, resolve_palaeographer_id,
                              resolve_prompt)
        from pathlib import Path
        p = (cfg.dropbox / document_relpath).resolve()
        sel_path = p if p.is_dir() else p.parent
        pal_id, pal_src = resolve_palaeographer_id(p.stem, sel_path, cfg.dropbox)
        ed_id, ed_src = resolve_editor_id(p.stem, sel_path, cfg.dropbox)
        prompt_txt, prompt_src = resolve_prompt(p.stem, sel_path, cfg.dropbox, cfg.prompts)
        pal = cfg.get_palaeographer(pal_id) if pal_id else cfg.get_palaeographer()
        ed = cfg.editors.get(ed_id) if ed_id else None
        return {
            "document": document_relpath,
            "palaeographer": {"id": pal.id, "model": pal.model, "source": pal_src},
            "editor": {"id": ed.id, "model": ed.model, "source": ed_src} if ed else
                      {"id": None, "model": None, "source": ed_src},
            "prompt_source": prompt_src,
            "prompt": (prompt_txt or ""),
        }

    @mcp.tool()
    def pha_get_archive() -> dict:
        """Diagnostic: the server's effective archive_dir and the data paths it
        owns (dropbox, library, renders, db), plus whether the documents
        registered in the DB still exist on disk.

        Use this to debug a scan that is finding 0 documents (e.g. an
        archive_dir / dropbox path mismatch, or files that were moved/removed)."""
        import os
        conn = db.connect(cfg.db_path)
        try:
            docs = db.list_documents(conn, limit=10000)
        finally:
            conn.close()
        return {
            "archive_dir": str(cfg.archive_dir),
            "dropbox": str(cfg.dropbox),
            "library": str(cfg.library),
            "renders": str(cfg.renders),
            "db": str(cfg.db_path),
            "dropbox_index": list(cfg.dropbox.rglob("*.pdf"))[:10] if cfg.dropbox.exists() else [],
            "documents": [
                {"id": d["id"], "filename": d["filename"],
                 "recorded_path": d["path"],
                 "file_on_disk": os.path.exists(d["path"])}
                for d in docs
            ],
        }

    @mcp.tool()
    def pha_get_dropbox() -> dict:
        """DEPRECATED alias for pha_get_archive (kept for existing clients)."""
        return pha_get_archive()

    @mcp.tool()
    def pha_collection_status(collection: str | None = None) -> list[dict]:
        """Status report, grouped by collection, for the remote dropbox.

        For each collection (or just `collection` if given, e.g.
        "collections/pfister-notices") returns its documents with:
          - config status: the RECORDED palaeographer/editor/encoder (the models
            used when the last pass ran) and the LIVE/RESOLVED ones (from the
            current selection files + definitions), so you can see if a config
            change is pending;
          - progress: pages done / total;
          - stage: which pipeline passes have run (transcribed / edited /
            encoded / embedded) and which have not.

        A document in the same directory as the dropbox root is reported under
        "(root)"; collections are the subdirectories of collections/.
        """
        conn = db.connect(cfg.db_path)
        try:
            docs = db.list_documents(conn, limit=10000)
            # per-doc done-page counts in one query
            counts = {}
            for row in conn.execute(
                "SELECT document_id, COUNT(*) n FROM pages WHERE status='done' GROUP BY document_id"
            ).fetchall():
                counts[row["document_id"]] = row["n"]
        finally:
            conn.close()
        from collections import OrderedDict
        from .extract import (encoder_files_for, resolve_editor_id,
                              resolve_palaeographer_id)
        from pathlib import Path

        def stage(doc) -> list[str]:
            s = []
            if doc["status"] == "done" or doc["palaeographer"]:
                s.append("transcribed")
            if doc["editor"]:
                s.append("edited")
            if doc["encoder"]:
                s.append("encoded")
            return s

        groups: "OrderedDict[str, list]" = OrderedDict()
        for d in docs:
            rel = d["dir_path"] or "(root)"
            groups.setdefault(rel, []).append(d)

        out = []
        for rel, ds in groups.items():
            if collection and rel != collection:
                continue
            items = []
            for d in ds:
                p = (cfg.dropbox / (d["dir_path"] or "") / d["filename"])
                sel_dir = p.parent if p.exists() or d["dir_path"] else cfg.dropbox
                # recorded config:
                rec = {"palaeographer": d["palaeographer"],
                       "editor": d["editor"], "encoder": d["encoder"]}
                # resolved (live) config: explicit selection, else the
                # effective default that would actually run.
                try:
                    pal_id, pal_src = resolve_palaeographer_id(
                        Path(d["filename"]).stem, sel_dir, cfg.dropbox)
                    ed_id, ed_src = resolve_editor_id(
                        Path(d["filename"]).stem, sel_dir, cfg.dropbox)
                    encs = encoder_files_for(Path(d["filename"]).stem, sel_dir, cfg.dropbox)
                    enc_names = [Path(f).stem for f in encs]
                except Exception:
                    pal_id, pal_src, ed_id, ed_src, enc_names = None, None, None, None, []
                # effective palaeographer: selection, else the active default
                eff_pal = pal_id or cfg.active_palaeographer
                pal_src = pal_src or (
                    "config default (vision.palaeographer)" if eff_pal != pal_id else None)
                total = d["page_count"] or 0
                done = counts.get(d["id"], 0)
                # render phase: how many page images have been rendered to the
                # cache dir vs how many transcripts exist. If rendered > done,
                # the scan is (or was) in the render phase.
                render_dir = cfg.renders / d["sha256"]
                rendered = 0
                if render_dir.is_dir():
                    try:
                        rendered = len([f for f in render_dir.glob("p*.jpg")
                                        if f.name[1:-4].isdigit()])
                    except OSError:
                        rendered = 0
                if done >= total and total:
                    phase = "complete"
                elif rendered >= total and total:
                    phase = "transcribing"   # all pages rendered, not yet transcribed
                else:
                    phase = "rendering"      # fewer renders than pages
                items.append({
                    "document_id": d["id"],
                    "filename": d["filename"],
                    "status": d["status"],
                    "progress": {"pages_done": done, "pages_total": total,
                                 "fraction": round(done / total, 3) if total else None},
                    "render": {"pages_rendered": rendered, "pages_transcribed": done,
                               "phase": phase,
                               "render_dir": str(render_dir)},
                    "config_recorded": rec,
                    "config_resolved": {"palaeographer": eff_pal,
                                        "palaeographer_source": pal_src,
                                        "editor": ed_id,
                                        "editor_source": ed_src,
                                        "encoders": enc_names},
                    "stage": stage(d),
                })
            out.append({"collection": rel, "documents": items})
        return out

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
