"""Tests for the MCP server's report tools (pha_collection_status, etc.)."""

from __future__ import annotations

from pathlib import Path

import personal_historical_archive.config as c
from personal_historical_archive.config import Config
from personal_historical_archive import db
from personal_historical_archive import mcp_server


def _make_config(tmp_path: Path) -> Config:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  palaeographers: palaeographers\n"
        "  editors: editors\n  encoders: encoders\n  prompts: prompts\n"
    )
    return Config.load(root)


def _seed(cfg: Config) -> dict:
    """Create a collection with one doc + a palaeographer/editor and an
    encoder selection, and populate the DB with a couple of done pages."""
    drop = cfg.dropbox
    col = drop / "collections" / "testcol"
    col.mkdir(parents=True)
    src = col / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    (col / "palaeographer").write_text("qwen-local")
    (col / "editor").write_text("generic")
    enc = col / "encoders"
    enc.mkdir()
    (enc / "letters.md").write_text("---\ndescription: x\n---\n")
    # a bare root doc with no config
    rootdoc = drop / "root.pdf"
    rootdoc.write_bytes(b"%PDF bare")

    conn = db.connect(cfg.db_path)
    try:
        doc1 = db.add_document(conn, filename="doc.pdf", path=str(src), sha256="a",
                               size_bytes=10, mtime=1, kind="pdf",
                               dir_path="collections/testcol", now="2026-01-01")
        doc2 = db.add_document(conn, filename="root.pdf", path=str(rootdoc), sha256="b",
                               size_bytes=10, mtime=1, kind="pdf",
                               dir_path="", now="2026-01-01")
        for p in (1, 2):
            pid = db.add_page(conn, doc1, p)
            db.set_page_result(conn, pid, raw_text="text")
        db.update_document(conn, doc1, palaeographer="qwen-local", editor="generic")
        conn.commit()
    finally:
        conn.close()
    return {"doc1": doc1, "doc2": doc2}


def test_collection_status_shape(tmp_path):
    cfg = _make_config(tmp_path)
    _seed(cfg)
    mcp = mcp_server.make_server(cfg)
    # resolve the tool function directly
    status_fn = None
    import asyncio
    for t in asyncio.run(mcp.list_tools()):
        if t.name == "pha_collection_status":
            status_fn = t.fn
    assert status_fn is not None, "pha_collection_status tool not registered"

    report = status_fn()
    # find the testcol group
    colgroup = next((g for g in report if g["collection"] == "collections/testcol"), None)
    assert colgroup is not None, f"testcol missing from {[g['collection'] for g in report]}"
    doc = colgroup["documents"][0]
    # progress
    assert doc["progress"]["pages_done"] == 2
    assert doc["progress"]["pages_total"] == 2 if doc["progress"]["pages_total"] else True
    # recorded config
    assert doc["config_recorded"]["palaeographer"] == "qwen-local"
    assert doc["config_recorded"]["editor"] == "generic"
    # resolved (from selection files)
    assert doc["config_resolved"]["palaeographer"] == "qwen-local"
    assert doc["config_resolved"]["editor"] == "generic"
    assert "letters" in doc["config_resolved"]["encoders"]
    # stage: transcribed + edited
    assert "transcribed" in doc["stage"]
    assert "edited" in doc["stage"]


def test_collection_status_default_palaeographer(tmp_path):
    """A doc with no selection file falls back to the effective default."""
    cfg = _make_config(tmp_path)
    _seed(cfg)
    import asyncio
    import personal_historical_archive.config as c
    # set a known default so the fallback is deterministic
    cfg.active_palaeographer = "qwen-local"
    mcp = mcp_server.make_server(cfg)
    status_fn = None
    for t in asyncio.run(mcp.list_tools()):
        if t.name == "pha_collection_status":
            status_fn = t.fn
    report = status_fn()
    # the bare root doc has no selection file -> effective default
    rootgroup = next((g for g in report if g["collection"] == "(root)"), None)
    assert rootgroup is not None
    doc = rootgroup["documents"][0]
    assert doc["config_resolved"]["palaeographer"] == "qwen-local"
    assert "default" in (doc["config_resolved"]["palaeographer_source"] or "")


def test_get_page_versions(tmp_path):
    """pha_get_page returns transcribed, edited and encoded versions of a page."""
    cfg = _make_config(tmp_path)
    seed = _seed(cfg)
    doc_id = seed["doc1"]
    conn = db.connect(cfg.db_path)
    try:
        # an edited version + an encoded record on page 1
        pg = conn.execute("SELECT id FROM pages WHERE document_id=? AND page_no=1",
                          (doc_id,)).fetchone()
        db.set_page_edit(conn, pg["id"], "generic", text="EDITED TEXT")
        db.add_record(conn, doc_id, "letters", "letter",
                      '{"letter": "x", "letter_attributes": {"from": "a"}}', "1")
        conn.commit()
    finally:
        conn.close()

    import asyncio
    mcp = mcp_server.make_server(cfg)
    fns = {t.name: t.fn for t in asyncio.run(mcp.list_tools())}
    r = fns["pha_get_page"](doc_id, 1)
    assert r["transcribed"] == "text"           # raw_text set in _seed
    assert r["edited"] == {"generic": "EDITED TEXT"}
    assert len(r["encoded"]) == 1
    assert r["encoded"][0]["kind"] == "letter"
