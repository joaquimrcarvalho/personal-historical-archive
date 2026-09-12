from __future__ import annotations

import time
from pathlib import Path

import pytest

from personal_historical_archive import db as _db
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import _doc_slug, sha256_of


def _make_cfg(tmp_path) -> Config:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        f"paths:\n  archive_dir: {root / 'archive'}\n"
        "  dropbox: dropbox\n  inbox: inbox\n  library: library\n  renders: renders\n"
        "  notes: notes\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  models: models\n  prompts: prompts\n  db: archive.db\n"
    )
    return Config.load(root)


@pytest.fixture
def cfg(tmp_path) -> Config:
    c = _make_cfg(tmp_path)
    c.ensure_dirs()
    return c


@pytest.fixture
def add_document(cfg):
    """Create a dropbox document + its DB row and pages; return the document id.

    Mirrors the real shapes the address helpers depend on: an absolute `path`,
    a `dir_path` relative to the dropbox, a content `sha256`, a page count and
    an optional `source_name` per page (directory-of-images documents).
    """

    def _add(
        *,
        collection: str = "collections/COLX",
        filename: str = "d.pdf",
        pages=(1,),
        palaeographer: str | None = "ocr",
        palaeographer_model: str | None = "liteparse-fra",
        editor: str | None = "french-ocr",
        editor_model: str | None = "deepseek-v4-flash",
        dir_path: str | None = None,
        source_names=None,
        sha: str | None = None,
    ) -> int:
        src = cfg.dropbox / collection / filename
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"%PDF fake " + filename.encode())
        conn = _db.connect(cfg.db_path)
        try:
            _db.add_document(
                conn,
                filename=filename,
                path=str(src),
                sha256=sha or sha256_of(src),
                size_bytes=src.stat().st_size,
                mtime=1,
                kind="pdf",
                now=time.time(),
                dir_path=collection if dir_path is None else dir_path,
                palaeographer=palaeographer,
                editor=editor,
                palaeographer_model=palaeographer_model,
                editor_model=editor_model,
            )
            doc_id = _db.get_document_by_path(conn, str(src))["id"]
            for i, p in enumerate(pages):
                page_id = _db.add_page(conn, doc_id, p)
                _db.set_page_result(conn, page_id, raw_text=f"raw text page {p}")
                if source_names and i < len(source_names) and source_names[i]:
                    conn.execute("UPDATE pages SET source_name=? WHERE id=?",
                                 (source_names[i], page_id))
            conn.execute("UPDATE documents SET page_count=?, status='done' WHERE id=?",
                         (len(pages), doc_id))
            conn.commit()
        finally:
            conn.close()
        return doc_id

    return _add


@pytest.fixture
def write_variant(cfg):
    """Write a library page file for a variant directory (the review surface).

    `body=None` writes the `*waiting*` stub an export leaves for an empty page.
    """

    def _write(doc_id: int, dirname: str, page_no: int, body: str | None,
               source_name: str | None = None) -> Path:
        conn = _db.connect(cfg.db_path)
        try:
            doc = _db.get_document(conn, doc_id)
        finally:
            conn.close()
        # The dated document folder is what the export would create; make it
        # explicitly so `_library_doc_dir` finds it.
        doc_dir = cfg.library / Path(doc["dir_path"] or "") / _doc_slug(doc)
        doc_dir.mkdir(parents=True, exist_ok=True)
        name = f"{source_name}.md" if source_name else f"page-{page_no:03d}.md"
        d = doc_dir / dirname
        d.mkdir(parents=True, exist_ok=True)
        f = d / name
        text = "*waiting*" if body is None else body
        f.write_text(f"---\npage: {page_no}\n---\n\n{text}\n", encoding="utf-8")
        return f

    return _write


@pytest.fixture
def write_render(cfg):
    """Write a page render under the document's CURRENT sha (renders/<sha>/…)."""

    def _write(doc_id: int, page_no: int, data: bytes = b"\xff\xd8\xff\xe0JPEGDATA",
               source_name: str | None = None) -> Path:
        conn = _db.connect(cfg.db_path)
        try:
            sha = _db.get_document(conn, doc_id)["sha256"]
        finally:
            conn.close()
        d = cfg.renders / sha
        d.mkdir(parents=True, exist_ok=True)
        name = f"{source_name}.jpg" if source_name else f"p{page_no:03d}.jpg"
        f = d / name
        f.write_bytes(data)
        return f

    return _write
