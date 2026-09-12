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
        # one text-only chunk -> keyword-only index for doc1
        p1 = conn.execute(
            "SELECT id FROM pages WHERE document_id=? AND page_no=1", (doc1,)
        ).fetchone()["id"]
        db.add_chunk(conn, doc1, p1, 0, "text chunk", None)
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
    # per-document index stats: text-only chunk -> no embeddings yet
    assert doc["index"]["chunks"] == 1
    assert doc["index"]["embedded"] == 0
    assert "embedded" not in doc["stage"]


def test_collection_status_embedded_stage(tmp_path):
    """A document whose chunks all carry vectors reports the embedded stage."""
    cfg = _make_config(tmp_path)
    seed = _seed(cfg)
    conn = db.connect(cfg.db_path)
    try:
        conn.execute("UPDATE chunks SET embedding = x'01' WHERE document_id = ?",
                     (seed["doc1"],))
        conn.commit()
    finally:
        conn.close()
    import asyncio
    mcp = mcp_server.make_server(cfg)
    status_fn = None
    for t in asyncio.run(mcp.list_tools()):
        if t.name == "pha_collection_status":
            status_fn = t.fn
    report = status_fn()
    colgroup = next((g for g in report if g["collection"] == "collections/testcol"), None)
    doc = colgroup["documents"][0]
    assert doc["index"]["chunks"] == 1
    assert doc["index"]["embedded"] == 1
    assert "embedded" in doc["stage"]


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


def _sidecar_cfg(tmp_path: Path) -> Config:
    """A project whose collection is configured by a pha.yaml sidecar pointing at
    real definitions, with a STALE legacy selection file beside it (the sidecar
    must win)."""
    cfg = _make_config(tmp_path)
    cfg.palaeographers_dir.mkdir(parents=True, exist_ok=True)
    cfg.editors_dir.mkdir(parents=True, exist_ok=True)
    cfg.models_dir.mkdir(parents=True, exist_ok=True)
    (cfg.palaeographers_dir / "ocr.md").write_text(
        "---\ndescription: OCR pass\n---\nTranscribe with a local OCR engine.\n")
    (cfg.editors_dir / "modern-pt.md").write_text(
        "---\ndescription: modernise\n---\nModernise the spelling.\n")
    (cfg.models_dir / "liteparse.md").write_text(
        "---\ndescription: LiteParse local OCR\nengine: liteparse\n"
        "liteparse_lang: por\n---\n")
    cfg = Config.load(cfg.root)          # pick the new definitions up
    _seed(cfg)
    (cfg.dropbox / "collections" / "testcol" / "pha.yaml").write_text(
        "palaeographer:\n  rules: ocr\n  model: liteparse\n"
        "editor:\n  rules: modern-pt\n  model: default\n", encoding="utf-8")
    return cfg


def test_collection_status_honours_pha_yaml_sidecar(tmp_path):
    """A collection configured by a pha.yaml sidecar is reported with the
    sidecar's rules + model, even with a stale legacy selection file beside it.

    Regression test: pha_collection_status used to resolve the live config with
    the legacy `palaeographer` / `editor` resolvers only, so every
    sidecar-configured collection was reported as the configured default
    palaeographer with no editor — disagreeing with the CLI and the pipeline
    (sidecar.resolve_stages).
    """
    cfg = _sidecar_cfg(tmp_path)

    import asyncio
    mcp = mcp_server.make_server(cfg)
    fns = {t.name: t.fn for t in asyncio.run(mcp.list_tools())}
    report = fns["pha_collection_status"]()
    doc = next(g for g in report
               if g["collection"] == "collections/testcol")["documents"][0]
    resolved = doc["config_resolved"]

    # the sidecar wins over the legacy files ("qwen-local" / "generic")
    assert resolved["palaeographer"] == "ocr"
    assert resolved["palaeographer_model"] == "liteparse"
    assert "pha.yaml" in resolved["palaeographer_source"]
    assert resolved["editor"] == "modern-pt"
    assert resolved["editor_model"] == "default"
    assert resolved["problems"] == []


def test_config_tools_agree_on_sidecar_config(tmp_path):
    """Both config-inspection tools resolve through sidecar.resolve_stages, so
    pha_collection_config and pha_collection_status cannot disagree about the
    same collection (they used to: one honoured the pha.yaml sidecar, the other
    reported the configured default)."""
    cfg = _sidecar_cfg(tmp_path)

    import asyncio
    fns = {t.name: t.fn
           for t in asyncio.run(mcp_server.make_server(cfg).list_tools())}

    cfg_tool = fns["pha_collection_config"]("collections/testcol")
    resolved = fns["pha_collection_status"](
        "collections/testcol")[0]["documents"][0]["config_resolved"]

    assert cfg_tool["palaeographer"]["id"] == resolved["palaeographer"] == "ocr"
    assert (cfg_tool["palaeographer"]["model_ref"]
            == resolved["palaeographer_model"] == "liteparse")
    assert cfg_tool["editor"]["id"] == resolved["editor"] == "modern-pt"
    assert cfg_tool["editor"]["model_ref"] == resolved["editor_model"] == "default"
    assert cfg_tool["problems"] == resolved["problems"] == []
    # encoders are reported by both, identically
    assert [e["id"] for e in cfg_tool["encoders"]] == resolved["encoders"] == ["letters"]
