from __future__ import annotations

from types import SimpleNamespace

import pytest

from personal_historical_archive.ingest import (
    _expand_records,
    _page_filter,
    _parse_json_array,
    _record_key,
    _record_similar,
    _regex_candidates,
    chunk_text,
    discover,
)


def test_discover_leaf_imagedir_is_one_document(tmp_path):
    """--path to a leaf image-folder treats the folder as ONE document, not
    each image as a separate file (regression for the rescan-granularity bug)."""
    drop = tmp_path / "dropbox"
    leaf = drop / "collections" / "COLX" / "ms123"
    leaf.mkdir(parents=True)
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (leaf / name).write_bytes(b"x")
    # whole dropbox: leaf is a document-dir -> 1 unit
    units = discover(drop, True)
    assert [u.name for u in units] == ["ms123"]
    # --path at the collection root: same (ms123 is the doc-dir)
    units = discover(drop, True, root=drop / "collections" / "COLX")
    assert [u.name for u in units] == ["ms123"]
    # --path at the LEAF folder itself: must still be 1 unit (the folder),
    # not 3 individual images
    units = discover(drop, True, root=leaf)
    assert [u.name for u in units] == ["ms123"]


def test_discover_leaf_mixed_dir_enumerates_files(tmp_path):
    """A leaf dir that is NOT an image-dir (has a PDF/subdir) still yields its
    individual files under --path."""
    drop = tmp_path / "dropbox"
    leaf = drop / "docs"
    leaf.mkdir(parents=True)
    (leaf / "a.pdf").write_bytes(b"x")
    (leaf / "note.txt").write_bytes(b"x")
    units = discover(drop, True, root=leaf)
    assert "a.pdf" in [u.name for u in units]
    assert "note.txt" not in [u.name for u in units]  # .txt not supported


def test_chunk_text_small():
    assert chunk_text("short text", 2000, 200) == ["short text"]


def test_chunk_text_empty():
    assert chunk_text("   ", 2000, 200) == []


def test_chunk_text_no_gaps_and_overlap():
    text = " ".join(f"word{n}" for n in range(300))
    chunks = chunk_text(text, 150, 40)
    assert len(chunks) > 1
    assert all(len(c) <= 150 for c in chunks)
    for w in text.split():
        assert any(w in c for c in chunks)
    for a, b in zip(chunks, chunks[1:]):
        assert b[0] in a


def test_parse_json_array_plain():
    assert _parse_json_array('[{"a": 1}]') == [{"a": 1}]


def test_parse_json_array_object_wrapper():
    assert _parse_json_array('{"records": [{"a": 1}]}') == [{"a": 1}]


def test_parse_json_array_nested_strings():
    assert _parse_json_array('[{"name": "a;b, [c]"}]') == [{"name": "a;b, [c]"}]


def test_parse_json_array_prose_prefix():
    assert _parse_json_array('here is the answer:\n[1, 2, 3]\nthat\'s all') == [1, 2, 3]


def test_parse_json_array_empty():
    assert _parse_json_array("no json here") == []


def test_expand_records_multi_class():
    parsed = [{
        "person": "Padre Mestre S. Francisco Xavier",
        "person_attributes": {"title": "Padre Mestre S.", "name": "Francisco Xavier"},
        "letter": "0 Padre ao ...",
        "letter_attributes": {"date": "1545-01-27", "place": "Cochim"},
    }]
    recs = _expand_records(parsed)
    assert {r["kind"] for r in recs} == {"person", "letter"}
    person = next(r for r in recs if r["kind"] == "person")
    assert person["name"] == "Francisco Xavier"
    letter = next(r for r in recs if r["kind"] == "letter")
    assert letter["place"] == "Cochim"


def test_expand_records_passthrough_plain():
    assert _expand_records([{"kind": "letter", "text": "x"}]) == [{"kind": "letter", "text": "x"}]


def test_record_key_normalizes():
    a = {"kind": "letter", "text": "Carta  ao  mosteiro"}
    b = {"kind": "letter", "text": "carta ao mosteiro"}
    assert _record_key(a) == _record_key(b)
    c = {"kind": "person", "text": "Carta ao mosteiro"}
    assert _record_key(a) != _record_key(c)


def test_record_similar_kind_gate():
    a = {"kind": "letter", "text": "x"}
    b = {"kind": "letter", "text": "x"}
    c = {"kind": "person", "text": "x"}
    assert _record_similar(a, b) == 1.0
    assert _record_similar(a, c) == 0.0


def test_page_filter():
    assert _page_filter(SimpleNamespace(pages="1-15")) == set(range(1, 16))
    assert _page_filter(SimpleNamespace(pages="1-15,40")) == set(range(1, 16)) | {40}
    assert _page_filter(SimpleNamespace(pages="all")) is None
    assert _page_filter(SimpleNamespace(pages="")) is None


def test_regex_candidates():
    enc = SimpleNamespace(
        candidate_pattern=r"^\s*[ivxlcdm]+\s*$",
        candidate_header=r"^[A-ZÀ-Ú]",
    )
    texts = [(1, "plain text"), (2, "xii\nD. João ao Padre"), (3, "more text")]
    assert _regex_candidates(texts, enc) == [2]


def test_review_import_updates_db(tmp_path):
    """pha review imports corrections from library .md files into the DB and
    stamps them reviewed."""
    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive.ingest import review_import, write_document_pages
    import yaml

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    drop = cfg.dropbox
    col = drop / "collections" / "testcol"
    col.mkdir(parents=True)
    src = col / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="doc.pdf", path=str(src), sha256="a",
                              size_bytes=10, mtime=1, kind="pdf",
                              dir_path="collections/testcol", now="2026-01-01")
    pid = _db.add_page(conn, doc_id, 1)
    _db.set_page_result(conn, pid, raw_text="ORIGINAL MACHINE TEXT")
    _db.set_document_status(conn, doc_id, "done")
    conn.commit()

    # write a library transcription file the historian "corrected"
    out = write_document_pages(cfg, conn, doc_id)
    lib_file = out / "page-001.md"
    corrected = lib_file.read_text(encoding="utf-8").replace("ORIGINAL MACHINE TEXT", "HUMAN CORRECTED TEXT")
    lib_file.write_text(corrected, encoding="utf-8")

    res = review_import(cfg, conn, doc_id=doc_id, verbose=False)
    assert res["pages"] == 1
    row = conn.execute("SELECT raw_text, reviewed_at FROM pages WHERE id=?", (pid,)).fetchone()
    assert row["raw_text"] == "HUMAN CORRECTED TEXT"
    assert row["reviewed_at"] is not None
    conn.close()


def test_review_protects_from_reprocess(tmp_path):
    """A reviewed page must survive a --reprocess scan (never overwritten)."""
    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive.ingest import review_import, _edit_needed, write_document_pages
    from types import SimpleNamespace

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    drop = cfg.dropbox
    col = drop / "collections" / "testcol"
    col.mkdir(parents=True)
    src = col / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="doc.pdf", path=str(src), sha256="a",
                              size_bytes=10, mtime=1, kind="pdf",
                              dir_path="collections/testcol", now="2026-01-01")
    pid = _db.add_page(conn, doc_id, 1)
    _db.set_page_result(conn, pid, raw_text="ORIGINAL")
    _db.set_document_status(conn, doc_id, "done")
    conn.commit()
    write_document_pages(cfg, conn, doc_id)

    # mark the page reviewed (as pha review would)
    _db.mark_page_reviewed(conn, pid, "HUMAN CORRECTED")
    conn.commit()

    # _edit_needed must refuse to re-edit a reviewed edit
    edit_row = {"reviewed_at": 123, "status": "done", "text": "x", "raw_sha": "x", "updated_at": 1}
    page = SimpleNamespace(id=pid, raw_text="HUMAN CORRECTED")
    editor = SimpleNamespace(prompt_file=None)
    assert _edit_needed(page, edit_row, editor, reprocess=True) is False
    conn.close()


def test_pending_review_files_detects_correction(tmp_path):
    """pha status detects a library file whose body differs from the DB."""
    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive.ingest import review_import, write_document_pages, pending_review_files

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    drop = cfg.dropbox
    col = drop / "collections" / "testcol"
    col.mkdir(parents=True)
    src = col / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="doc.pdf", path=str(src), sha256="a",
                              size_bytes=10, mtime=1, kind="pdf",
                              dir_path="collections/testcol", now="2026-01-01")
    pid = _db.add_page(conn, doc_id, 1)
    _db.set_page_result(conn, pid, raw_text="SOME TEXT\n\n## Notes\n\n### Named entities\n- x")
    _db.set_document_status(conn, doc_id, "done")
    conn.commit()
    out = write_document_pages(cfg, conn, doc_id)

    # no pending yet
    assert pending_review_files(cfg, conn) == []

    # edit the file -> pending (mtime newer than exported_at)
    import time as _t
    _t.sleep(0.05)
    lib_file = out / "page-001.md"
    lib_file.write_text(lib_file.read_text(encoding="utf-8").replace("SOME TEXT", "SOME CORRECTED TEXT"), encoding="utf-8")
    pend = pending_review_files(cfg, conn)
    assert len(pend) == 1
    assert pend[0]["page_no"] == 1
    conn.close()


def test_doc_slug_readable_date():
    """Library folder names use a readable date, not an opaque hash."""
    from personal_historical_archive.ingest import _doc_slug
    doc = {"path": "/x/collections/testcol/1576", "created_at": 1787360000.0,
           "sha256": "a84d7b42abc"}
    slug = _doc_slug(doc)
    assert slug.startswith("1576_20")  # 1576_YYYY-MM-DD
    assert "__" not in slug
    assert "sha" not in slug.lower() and len(slug) < 25


def test_encode_needed_records_without_pages_updated_at(tmp_path):
    """A document that already has records must not crash `pha encode` on the
    legacy schema (pages has no updated_at column). Regression for the
    post-unbundle workflow."""
    import time as _t
    from types import SimpleNamespace
    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive.ingest import _encode_needed

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    drop = cfg.dropbox
    col = drop / "collections" / "testcol"
    col.mkdir(parents=True)
    src = col / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="doc.pdf", path=str(src), sha256="a",
                              size_bytes=10, mtime=1, kind="pdf",
                              dir_path="collections/testcol", now=_t.time())
    pid = _db.add_page(conn, doc_id, 1)
    _db.set_page_result(conn, pid, raw_text="TEXT")
    _db.set_document_status(conn, doc_id, "done")
    _db.add_record(conn, doc_id, "enc1", "letter", '{"text": "x"}')
    conn.commit()
    encoder = SimpleNamespace(prompt_file=None)
    cfg_lite = SimpleNamespace(dropbox=cfg.dropbox, prompts=cfg.prompts, encoders={})
    # must not raise; records present + nothing newer -> no re-encode
    assert _encode_needed(cfg_lite, conn, doc_id, encoder, "enc1", src, False, None) is False
    conn.close()


# --------------------------------------------------------------------------- engine dispatch
# A non-LLM engine (tesseract / liteparse / future tools) is looked up in
# model_client.PAGE_ENGINES and run on the page render; the default (""/"llm")
# calls chat_vision on the vision LLM.

def test_transcribe_page_dispatches_tesseract(monkeypatch):
    """An `engine: tesseract` palaeographer runs Tesseract, never chat_vision."""
    from pathlib import Path

    from personal_historical_archive import config as cconfig
    from personal_historical_archive.ingest import transcribe_page

    calls: dict = {}

    def fake_run(img, lang, psm):
        calls["tesseract"] = (img, lang, psm)
        return "TESS TEXT"

    monkeypatch.setattr("personal_historical_archive.model_client.run_tesseract", fake_run)

    class FakeClient:
        def chat_vision(self, *a, **k):  # pragma: no cover - must not be called
            calls["chat_vision"] = True
            return "LLM TEXT"

    pal = cconfig.Palaeographer(
        id="ocr", description="", base_url="", api_key="", model="",
        temperature=0.1, max_tokens=4096, timeout_s=600, prompt_text="",
        engine="tesseract", tesseract_lang="por", tesseract_psm=6,
    )
    out = transcribe_page(FakeClient(), pal, "prompt", Path("/tmp/p.jpg"))
    assert out == "TESS TEXT"
    assert calls["tesseract"] == (Path("/tmp/p.jpg"), "por", 6)
    assert "chat_vision" not in calls


def test_transcribe_page_dispatches_liteparse(monkeypatch):
    """An `engine: liteparse` palaeographer runs `lit parse`, never chat_vision."""
    from pathlib import Path

    from personal_historical_archive import config as cconfig
    from personal_historical_archive.ingest import transcribe_page

    calls: dict = {}

    def fake_run(img, lang, dpi):
        calls["liteparse"] = (img, lang, dpi)
        return "LITE TEXT"

    monkeypatch.setattr("personal_historical_archive.model_client.run_liteparse", fake_run)

    class FakeClient:
        def chat_vision(self, *a, **k):  # pragma: no cover - must not be called
            calls["chat_vision"] = True
            return "LLM TEXT"

    pal = cconfig.Palaeographer(
        id="lparse", description="", base_url="", api_key="", model="",
        temperature=0.1, max_tokens=4096, timeout_s=600, prompt_text="",
        engine="liteparse", liteparse_lang="por", liteparse_dpi=300,
    )
    out = transcribe_page(FakeClient(), pal, "prompt", Path("/tmp/p.jpg"))
    assert out == "LITE TEXT"
    assert calls["liteparse"] == (Path("/tmp/p.jpg"), "por", 300)
    assert "chat_vision" not in calls


def test_transcribe_page_unknown_engine_raises(monkeypatch):
    from pathlib import Path

    from personal_historical_archive.model_client import ModelError
    from personal_historical_archive import config as cconfig
    from personal_historical_archive.ingest import transcribe_page

    pal = cconfig.Palaeographer(
        id="x", description="", base_url="", api_key="", model="",
        temperature=0.1, max_tokens=4096, timeout_s=600, prompt_text="",
        engine="not-a-real-engine",
    )
    with pytest.raises(ModelError, match="unknown palaeographer engine"):
        transcribe_page(object(), pal, "prompt", Path("/tmp/p.jpg"))


def test_transcribe_page_dispatches_llm(monkeypatch):
    """A normal palaeographer calls chat_vision with its model settings."""
    from pathlib import Path

    from personal_historical_archive import config as cconfig
    from personal_historical_archive.ingest import transcribe_page

    seen: dict = {}

    def fake_run(img, lang, psm):  # pragma: no cover - must not be called
        seen["tesseract"] = True
        return "TESS"

    monkeypatch.setattr("personal_historical_archive.model_client.run_tesseract", fake_run)

    class FakeClient:
        def chat_vision(self, model, prompt, img, temperature, max_tokens,
                        thinking, max_vision_px, jpeg_quality):
            seen["chat"] = dict(model=model, prompt=prompt, img=img, temperature=temperature,
                                max_tokens=max_tokens, thinking=thinking,
                                max_vision_px=max_vision_px, jpeg_quality=jpeg_quality)
            return "LLM TEXT"

    pal = cconfig.Palaeographer(
        id="qwen", description="", base_url="http://x/v1", api_key="",
        model="qwen/qwen3-vl-8b", temperature=0.2, max_tokens=2048,
        timeout_s=900, prompt_text="", thinking=False,
        max_vision_px=1400, vision_jpeg_quality=77,
    )
    out = transcribe_page(FakeClient(), pal, "P", Path("/tmp/p.jpg"))
    assert out == "LLM TEXT"
    assert seen["chat"]["model"] == "qwen/qwen3-vl-8b"
    assert seen["chat"]["temperature"] == 0.2
    assert seen["chat"]["max_vision_px"] == 1400
    assert seen["chat"]["jpeg_quality"] == 77
    assert "tesseract" not in seen
