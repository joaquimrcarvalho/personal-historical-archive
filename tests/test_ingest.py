from __future__ import annotations

import os
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
    assert _parse_json_array("no json here") is None


def test_parse_json_array_valid_empty_array():
    """A valid-but-empty array (the model's honest 'nothing to extract') is
    distinct from 'no JSON found': it returns [] (falsy but not None), so the
    encode retry loop can stop instead of insisting and hallucinating."""
    assert _parse_json_array("[]") == []


def test_parse_json_array_prose_only_is_none():
    assert _parse_json_array("just prose, no array") is None


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


def test_review_import_only_stamps_pending_files(tmp_path):
    """Regression: `pha review` must stamp ONLY the corrected file.

    The old scope walked every library .md and stamped unconditionally, so one
    review froze the whole archive against scan/edit — even --reprocess — with
    no way back. Untouched files must stay un-stamped.
    """
    import time as _t
    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive.ingest import review_import, write_document_pages

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    col = cfg.dropbox / "collections" / "testcol"
    col.mkdir(parents=True)
    src = col / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="doc.pdf", path=str(src), sha256="a",
                              size_bytes=10, mtime=1, kind="pdf",
                              dir_path="collections/testcol", now="2026-01-01")
    pids = []
    for n in (1, 2, 3):
        pid = _db.add_page(conn, doc_id, n)
        _db.set_page_result(conn, pid, raw_text=f"MACHINE TEXT {n}")
        pids.append(pid)
    _db.set_document_status(conn, doc_id, "done")
    conn.commit()
    out = write_document_pages(cfg, conn, doc_id)

    # nothing changed -> nothing pending -> a review imports and stamps nothing
    assert review_import(cfg, conn, doc_id=doc_id, verbose=False)["pages"] == 0
    stamps = conn.execute(
        "SELECT reviewed_at FROM pages WHERE document_id=?", (doc_id,)).fetchall()
    assert all(r["reviewed_at"] is None for r in stamps)

    # correct ONLY page 2 (mtime must move past exported_at)
    _t.sleep(0.05)
    f2 = out / "page-002.md"
    f2.write_text(f2.read_text(encoding="utf-8").replace("MACHINE TEXT 2", "HUMAN TEXT 2"),
                  encoding="utf-8")
    res = review_import(cfg, conn, doc_id=doc_id, verbose=False)
    assert res["pages"] == 1
    rows = {r["page_no"]: r for r in conn.execute(
        "SELECT page_no, raw_text, reviewed_at FROM pages WHERE document_id=?", (doc_id,))}
    assert rows[2]["raw_text"] == "HUMAN TEXT 2"
    assert rows[2]["reviewed_at"] is not None
    # the two untouched pages are still free to be re-scanned
    assert rows[1]["reviewed_at"] is None and rows[3]["reviewed_at"] is None
    assert rows[1]["raw_text"] == "MACHINE TEXT 1"

    # a second review is a no-op: importing updated exported_at, so nothing reads
    # as pending any more (the file was not edited again).
    assert review_import(cfg, conn, doc_id=doc_id, verbose=False)["pages"] == 0

    # --all is the explicit blanket import and DOES stamp the rest
    res = review_import(cfg, conn, doc_id=doc_id, verbose=False, include_all=True)
    assert res["pages"] == 3
    stamps = conn.execute(
        "SELECT COUNT(*) n FROM pages WHERE document_id=? AND reviewed_at IS NOT NULL",
        (doc_id,)).fetchone()["n"]
    assert stamps == 3
    conn.close()


def test_review_import_handles_edited_variant_scoped(tmp_path):
    """Only the corrected edited-* page is stamped; its sibling is untouched."""
    import time as _t
    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive.ingest import (
        review_import, unreview_import, write_edited_pages)

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    col = cfg.dropbox / "collections" / "testcol"
    col.mkdir(parents=True)
    src = col / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="doc.pdf", path=str(src), sha256="a",
                              size_bytes=10, mtime=1, kind="pdf",
                              dir_path="collections/testcol", now="2026-01-01")
    for n in (1, 2):
        pid = _db.add_page(conn, doc_id, n)
        _db.set_page_result(conn, pid, raw_text=f"RAW {n}")
        _db.set_page_edit(conn, pid, "mod", text=f"EDITED {n}", raw_sha="x")
    _db.set_document_status(conn, doc_id, "done")
    conn.commit()
    out = write_edited_pages(cfg, conn, doc_id, "mod", model=None)

    _t.sleep(0.05)
    f1 = out / "page-001.md"
    f1.write_text(f1.read_text(encoding="utf-8").replace("EDITED 1", "CORRECTED 1"),
                  encoding="utf-8")
    res = review_import(cfg, conn, doc_id=doc_id, verbose=False)
    assert res["edits"] == 1
    rows = {r["page_no"]: r for r in conn.execute(
        "SELECT p.page_no, pe.text, pe.reviewed_at FROM page_edits pe "
        "JOIN pages p ON p.id = pe.page_id WHERE p.document_id=? AND pe.editor='mod'",
        (doc_id,))}
    assert rows[1]["text"] == "CORRECTED 1" and rows[1]["reviewed_at"] is not None
    assert rows[2]["text"] == "EDITED 2" and rows[2]["reviewed_at"] is None

    # --unset lifts both the page and the edit protection, scoped to the doc
    cleared = unreview_import(cfg, conn, doc_id=doc_id, verbose=False)
    assert cleared == {"pages": 0, "edits": 1}
    n = conn.execute(
        "SELECT COUNT(*) n FROM page_edits pe JOIN pages p ON p.id = pe.page_id "
        "WHERE p.document_id=? AND pe.reviewed_at IS NOT NULL", (doc_id,)).fetchone()["n"]
    assert n == 0
    # the corrected text is KEPT — only the stamp goes
    kept = conn.execute(
        "SELECT pe.text FROM page_edits pe JOIN pages p ON p.id = pe.page_id "
        "WHERE p.document_id=? AND p.page_no=1 AND pe.editor='mod'", (doc_id,)).fetchone()
    assert kept["text"] == "CORRECTED 1"
    conn.close()


def test_unreview_import_scopes_page_and_restores_reprocess(tmp_path):
    """--unset --doc N --page P clears only that page, then edit can run again."""
    import time as _t
    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive.ingest import (
        review_import, unreview_import, write_document_pages, write_edited_pages,
        _edit_needed)
    from types import SimpleNamespace

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    col = cfg.dropbox / "collections" / "testcol"
    col.mkdir(parents=True)
    src = col / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="doc.pdf", path=str(src), sha256="a",
                              size_bytes=10, mtime=1, kind="pdf",
                              dir_path="collections/testcol", now="2026-01-01")
    for n in (1, 2):
        pid = _db.add_page(conn, doc_id, n)
        _db.set_page_result(conn, pid, raw_text=f"RAW {n}")
        _db.set_page_edit(conn, pid, "mod", text=f"EDITED {n}", raw_sha="x")
    _db.set_document_status(conn, doc_id, "done")
    conn.commit()
    out = write_document_pages(cfg, conn, doc_id)
    edit_out = write_edited_pages(cfg, conn, doc_id, "mod", model=None)

    _t.sleep(0.05)
    for n in (1, 2):
        f = out / f"page-{n:03d}.md"
        f.write_text(f.read_text(encoding="utf-8").replace(f"RAW {n}", f"HUMAN {n}"),
                     encoding="utf-8")
        e = edit_out / f"page-{n:03d}.md"
        e.write_text(e.read_text(encoding="utf-8").replace(f"EDITED {n}", f"CORRECTED {n}"),
                     encoding="utf-8")
    res = review_import(cfg, conn, doc_id=doc_id, verbose=False)
    assert res["pages"] == 2 and res["edits"] == 2
    # a reviewed edit refuses even --reprocess
    page1 = conn.execute("SELECT * FROM pages WHERE document_id=? AND page_no=1", (doc_id,)).fetchone()
    editor = SimpleNamespace(prompt_file=None)
    edit_row = dict(conn.execute(
        "SELECT pe.* FROM page_edits pe WHERE pe.page_id=? AND pe.editor='mod'",
        (page1["id"],)).fetchone())
    assert edit_row["reviewed_at"] is not None
    assert _edit_needed(page1, edit_row, editor, reprocess=True) is False

    # unset just page 1
    res = unreview_import(cfg, conn, doc_id=doc_id, page_no=1, verbose=False)
    assert res == {"pages": 1, "edits": 1}
    page1 = conn.execute("SELECT * FROM pages WHERE document_id=? AND page_no=1", (doc_id,)).fetchone()
    assert page1["reviewed_at"] is None
    edit_row = dict(conn.execute(
        "SELECT pe.* FROM page_edits pe WHERE pe.page_id=? AND pe.editor='mod'",
        (page1["id"],)).fetchone())
    assert edit_row["reviewed_at"] is None
    assert _edit_needed(page1, edit_row, editor, reprocess=True) is True
    # page 2 is untouched by the page-scoped unset
    page2 = conn.execute("SELECT * FROM pages WHERE document_id=? AND page_no=2", (doc_id,)).fetchone()
    assert page2["reviewed_at"] is not None
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

    def fake_run(img, lang, dpi, fmt="text"):
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


# --------------------------------------------------------------------------- scan lock (atomic)
# The lock itself now lives in locks.py (one model per model-server); its tests
# are in tests/test_locks.py. What matters here is that a job takes it: the
# reindex refusal case is covered below.


def test_edit_needed_model_file_staleness(tmp_path):
    """Editing the editor's MODEL file (models/<id>.md) re-triggers editing."""
    import time as _t

    from personal_historical_archive.ingest import _edit_needed, _raw_sha

    page = {"raw_text": "raw text"}
    editor = SimpleNamespace(prompt_file=None)
    model_file = tmp_path / "model.md"
    model_file.write_text("interface")
    now = _t.time()

    # last edit is OLDER than the model file -> re-edit
    row = {"reviewed_at": None, "status": "done", "text": "e",
           "updated_at": now - 100, "raw_sha": _raw_sha("raw text")}
    assert _edit_needed(page, row, editor, reprocess=False,
                        model_files=(model_file,)) is True
    # last edit is NEWER than the model file -> no re-edit
    os.utime(model_file, (now - 500, now - 500))
    row2 = {"reviewed_at": None, "status": "done", "text": "e",
            "updated_at": now, "raw_sha": _raw_sha("raw text")}
    assert _edit_needed(page, row2, editor, reprocess=False,
                        model_files=(model_file,)) is False
    # reviewed edits are never re-run, even with a newer model file
    row3 = dict(row, reviewed_at=now)
    assert _edit_needed(page, row3, editor, reprocess=True,
                        model_files=(model_file,)) is False


def test_edit_document_page_filter_edits_only_that_page(tmp_path, monkeypatch):
    """edit_document(page_no=N) edits only page N of the document."""
    import time as _t

    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive.ingest import edit_document

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {tmp_path / 'arc'}\n")
    cfg = Config.load(root)
    drop = cfg.dropbox
    src_dir = drop / "collections" / "tcol"
    src_dir.mkdir(parents=True)
    src = src_dir / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="doc.pdf", path=str(src), sha256="a",
                              size_bytes=10, mtime=1, kind="pdf",
                              dir_path="collections/tcol", now=_t.time())
    for n in (1, 2, 3):
        pid = _db.add_page(conn, doc_id, n)
        # keep well above the blank-page threshold so the model IS called
        _db.set_page_result(
            conn, pid, raw_text=f"raw page {n} with more than forty characters of transcribed text"
        )
    _db.update_document(conn, doc_id, page_count=3)
    conn.commit()

    calls: list[dict] = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

        def chat_text(self, model, prompt, temperature, max_tokens, thinking):
            # the prompt embeds "Page: N of M"
            calls.append(prompt)
            return "edited"

    monkeypatch.setattr("personal_historical_archive.ingest.ModelClient", FakeClient)
    res = edit_document(cfg, conn, doc_id, editor_id="default", page_no=2)
    assert res["pages"] == 1
    assert len(calls) == 1
    assert "Page: 2 of 3" in calls[0]
    # only page 2 got an edit row
    rows = conn.execute("SELECT page_id FROM page_edits WHERE editor='default'").fetchall()
    assert [r["page_id"] for r in rows] == [_db.get_pages(conn, doc_id)[1]["id"]]
    conn.close()


def test_edit_document_blank_page_skips_model(tmp_path, monkeypatch):
    """A page whose transcription has no readable content (after the page
    marker) is stamped blank deterministically — the editor model is never
    called, while a page with real text still is. The blank sentinel never
    reaches the index or the encoder's edited text."""
    import time as _t

    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive.ingest import (
        _BLANK_EDIT_TEXT,
        _edited_texts,
        _is_blank_edit,
        edit_document,
        index_document,
    )

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {tmp_path / 'arc'}\n")
    cfg = Config.load(root)
    src_dir = cfg.dropbox / "collections" / "tcol"
    src_dir.mkdir(parents=True)
    src = src_dir / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="doc.pdf", path=str(src), sha256="a",
                              size_bytes=10, mtime=1, kind="pdf",
                              dir_path="collections/tcol", now=_t.time())
    # page 1: only the page marker -> blank; page 2: real content -> model call
    pid1 = _db.add_page(conn, doc_id, 1)
    _db.set_page_result(conn, pid1, raw_text="--- Page 1 ---")
    pid2 = _db.add_page(conn, doc_id, 2)
    _db.set_page_result(conn, pid2, raw_text="A" * 100)
    _db.update_document(conn, doc_id, page_count=2)
    conn.commit()

    calls: list[str] = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

        def chat_text(self, model, prompt, temperature, max_tokens, thinking):
            calls.append(prompt)
            return "edited"

    monkeypatch.setattr("personal_historical_archive.ingest.ModelClient", FakeClient)
    res = edit_document(cfg, conn, doc_id, editor_id="default")
    conn.commit()

    edits = {r["page_id"]: r for r in conn.execute(
        "SELECT * FROM page_edits WHERE editor='default'").fetchall()}
    assert edits[pid1]["status"] == "done"
    assert edits[pid1]["text"] == _BLANK_EDIT_TEXT
    assert edits[pid2]["status"] == "done"
    assert edits[pid2]["text"] == "edited"
    # only the real page reached the model; the blank page was stamped in code
    assert len(calls) == 1
    assert res["pages"] == 2

    # the blank stamp is recognized as such, and excluded from the edited text
    # the indexer and encoder consume (so it never reaches search or the prompt)
    assert _is_blank_edit(_BLANK_EDIT_TEXT) is True
    assert _is_blank_edit("edited") is False
    texts = _edited_texts(conn, doc_id, "default")
    assert pid1 not in texts
    assert texts[pid2] == "edited"

    class FakeEmbed:
        def embed(self, model, texts, batch_size):
            return [None] * len(texts)

    index_document(cfg, conn, doc_id, embed_client=FakeEmbed(), verbose=False)
    chunks = conn.execute(
        "SELECT page_id, text, variant FROM chunks WHERE document_id=?", (doc_id,)
    ).fetchall()
    assert not any(c["text"] == _BLANK_EDIT_TEXT for c in chunks)
    assert not any(c["page_id"] == pid1 and c["variant"] == "edited" for c in chunks)
    assert any(c["page_id"] == pid2 and c["variant"] == "edited" for c in chunks)
    conn.close()


def test_index_document_keeps_vectors_when_embed_fails(tmp_path):
    """Regression: a failed embed must NEVER strip a document's embeddings.

    `index_document` replaces a document's chunks wholesale. It used to
    `clear_chunks()` FIRST and then fall back to text-only indexing when
    `embed()` failed, so one endpoint timeout silently deleted every vector
    while leaving status=done / error empty — the whole document then had to
    be re-embedded (13 885 chunks lost this way on the jesuit archive).
    """
    import time as _t
    import pytest
    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive.ingest import index_document
    from personal_historical_archive.model_client import ModelError

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {tmp_path / 'arc'}\n")
    cfg = Config.load(root)
    src = cfg.dropbox / "collections" / "tcol" / "doc.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="doc.pdf", path=str(src), sha256="a",
                              size_bytes=10, mtime=1, kind="pdf",
                              dir_path="collections/tcol", now=_t.time())
    pid = _db.add_page(conn, doc_id, 1)
    _db.set_page_result(conn, pid, raw_text="INDEXED TEXT " * 40)
    conn.commit()

    # 1) first pass with a WORKING embedder -> the document has vectors
    class GoodEmbed:
        def embed(self, model, texts, batch_size):
            return [[0.5, 0.25] for _ in texts]

    n = index_document(cfg, conn, doc_id, embed_client=GoodEmbed(), verbose=False)
    assert n > 0
    before = conn.execute(
        "SELECT COUNT(*) n, SUM(embedding IS NOT NULL) e FROM chunks WHERE document_id=?",
        (doc_id,)).fetchone()
    assert before["n"] == n and before["e"] == n, "setup: document must start fully embedded"

    # 2) re-index while the endpoint is DOWN -> must refuse, not degrade
    class DeadEmbed:
        def embed(self, model, texts, batch_size):
            raise ModelError("timeout after 120s")

    with pytest.raises(ModelError) as exc:
        index_document(cfg, conn, doc_id, embed_client=DeadEmbed(), verbose=False,
                       incremental=False)
    assert "left unchanged" in str(exc.value)

    after = conn.execute(
        "SELECT COUNT(*) n, SUM(embedding IS NOT NULL) e FROM chunks WHERE document_id=?",
        (doc_id,)).fetchone()
    assert after["n"] == before["n"], "chunks must not be cleared"
    assert after["e"] == before["e"] == n, "every stored vector must survive"

    # 3) a document with NO vectors yet still degrades to text-only (a fresh
    #    ingest with the endpoint down keeps pha's zero-config behaviour)
    pid2 = _db.add_page(conn, doc_id, 2)
    _db.set_page_result(conn, pid2, raw_text="FRESH TEXT " * 40)
    conn.commit()
    conn.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
    conn.commit()
    n2 = index_document(cfg, conn, doc_id, embed_client=DeadEmbed(), verbose=False,
                        incremental=False)
    assert n2 > 0
    rows = conn.execute(
        "SELECT COUNT(*) n, SUM(embedding IS NOT NULL) e FROM chunks WHERE document_id=?",
        (doc_id,)).fetchone()
    assert rows["n"] == n2 and rows["e"] == 0  # text-only, as before
    conn.close()


def test_reindex_all_reports_failed_documents_and_keeps_them(tmp_path):
    """`pha reindex` reports a document it could not embed instead of
    silently leaving it text-only, and does not touch its chunks."""
    import time as _t
    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive.ingest import index_document, reindex_all
    from personal_historical_archive.model_client import ModelError

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {tmp_path / 'arc'}\n")
    cfg = Config.load(root)
    cfg.ensure_dirs()
    src = cfg.dropbox / "collections" / "tcol" / "doc.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="doc.pdf", path=str(src), sha256="a",
                              size_bytes=10, mtime=1, kind="pdf",
                              dir_path="collections/tcol", now=_t.time())
    pid = _db.add_page(conn, doc_id, 1)
    _db.set_page_result(conn, pid, raw_text="INDEXED TEXT " * 40)
    _db.set_document_status(conn, doc_id, "done")
    conn.commit()

    class GoodEmbed:
        def embed(self, model, texts, batch_size):
            return [[1.0] * 4 for _ in texts]

    index_document(cfg, conn, doc_id, embed_client=GoodEmbed(), verbose=False)
    embedded = conn.execute(
        "SELECT SUM(embedding IS NOT NULL) e FROM chunks WHERE document_id=?",
        (doc_id,)).fetchone()["e"]
    assert embedded > 0
    conn.close()

    class DeadEmbed:
        def embed(self, model, texts, batch_size):
            raise ModelError("connection refused")

    res = reindex_all(cfg, DeadEmbed(), verbose=False, force=True)
    assert res["reindexed"] == 0
    assert len(res["failed"]) == 1
    assert res["failed"][0]["id"] == doc_id
    assert "left unchanged" in res["failed"][0]["error"]

    conn = _db.connect(cfg.db_path)
    kept = conn.execute(
        "SELECT SUM(embedding IS NOT NULL) e FROM chunks WHERE document_id=?",
        (doc_id,)).fetchone()["e"]
    conn.close()
    assert kept == embedded, "the failed reindex must leave the vectors alone"


def test_reindex_all_holds_the_single_model_lock(tmp_path, monkeypatch):
    """`pha reindex` embeds through the model server, so it must take that
    server's lock (see locks.py) — running it concurrently is what caused the
    embed timeouts (and the vector loss) in the first place."""
    import time as _t
    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive import ingest
    from personal_historical_archive.ingest import reindex_all

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {tmp_path / 'arc'}\n")
    cfg = Config.load(root)
    cfg.ensure_dirs()
    src = cfg.dropbox / "collections" / "tcol" / "doc.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="doc.pdf", path=str(src), sha256="a",
                              size_bytes=10, mtime=1, kind="pdf",
                              dir_path="collections/tcol", now=_t.time())
    pid = _db.add_page(conn, doc_id, 1)
    _db.set_page_result(conn, pid, raw_text="TEXT " * 40)
    _db.set_document_status(conn, doc_id, "done")
    conn.commit()
    conn.close()

    class Embed:
        def embed(self, model, texts, batch_size):
            return [[2.0] * 4 for _ in texts]

    # a foreign job holds the embedding model's server: the reindex must refuse,
    # name that server, and leave the other job's lock alone
    from personal_historical_archive import locks
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
    key = locks.embed_key(cfg)
    lock_path = locks._slot_path(key, 1)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("999999 pha scan", encoding="utf-8")
    try:
        res = reindex_all(cfg, Embed(), verbose=False)
        assert res["reindexed"] == 0
        assert key in res["reason"]
        assert "busy" in res["reason"]
        # and it released nothing it did not own
        assert lock_path.exists()
    finally:
        lock_path.unlink(missing_ok=True)


def test_reindex_all_path_selects_only_docs_under(monkeypatch, tmp_path):
    """`pha reindex --path collections/COLA` reindexes only the documents under
    that subpath, leaving other collections untouched."""
    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive import ingest

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    col_a = cfg.dropbox / "collections" / "COLA"
    col_b = cfg.dropbox / "collections" / "COLB"
    col_a.mkdir(parents=True)
    col_b.mkdir(parents=True)
    (col_a / "a.pdf").write_bytes(b"%PDF-1.4 a")
    (col_b / "b.pdf").write_bytes(b"%PDF-1.4 b")
    conn = _db.connect(cfg.db_path)
    da = _db.add_document(conn, filename="a.pdf", path=str((col_a / "a.pdf").resolve()),
                          sha256="a", size_bytes=10, mtime=1, kind="pdf",
                          dir_path="collections/COLA", now="2026-01-01")
    db_b = _db.add_document(conn, filename="b.pdf", path=str((col_b / "b.pdf").resolve()),
                            sha256="b", size_bytes=10, mtime=1, kind="pdf",
                            dir_path="collections/COLB", now="2026-01-01")
    _db.set_document_status(conn, da, "done")
    _db.set_document_status(conn, db_b, "done")
    conn.commit()
    conn.close()

    reindexed = []
    monkeypatch.setattr(ingest, "index_document",
                        lambda cfg_, conn_, doc_id, embed_client=None, verbose=True, **kw:
                        reindexed.append(doc_id) or 0)

    res = ingest.reindex_all(cfg, None, path="collections/COLA")
    assert reindexed == [da]
    assert res == {"reindexed": 1, "chunks": {da: 0}, "failed": []}


def test_reindex_all_path_to_single_file(monkeypatch, tmp_path):
    """`pha reindex --path collections/COLA/a.pdf` reindexes that one document."""
    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive import ingest

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    col_a = cfg.dropbox / "collections" / "COLA"
    col_a.mkdir(parents=True)
    (col_a / "a.pdf").write_bytes(b"%PDF-1.4 a")
    (col_a / "b.pdf").write_bytes(b"%PDF-1.4 b")
    conn = _db.connect(cfg.db_path)
    da = _db.add_document(conn, filename="a.pdf", path=str((col_a / "a.pdf").resolve()),
                          sha256="a", size_bytes=10, mtime=1, kind="pdf",
                          dir_path="collections/COLA", now="2026-01-01")
    db_b = _db.add_document(conn, filename="b.pdf", path=str((col_a / "b.pdf").resolve()),
                            sha256="b", size_bytes=10, mtime=1, kind="pdf",
                            dir_path="collections/COLA", now="2026-01-01")
    _db.set_document_status(conn, da, "done")
    _db.set_document_status(conn, db_b, "done")
    conn.commit()
    conn.close()

    reindexed = []
    monkeypatch.setattr(ingest, "index_document",
                        lambda cfg_, conn_, doc_id, embed_client=None, verbose=True, **kw:
                        reindexed.append(doc_id) or 0)

    res = ingest.reindex_all(cfg, None, path="collections/COLA/a.pdf")
    assert reindexed == [da]
    assert res == {"reindexed": 1, "chunks": {da: 0}, "failed": []}


def test_reindex_all_no_path_reindexes_everything(monkeypatch, tmp_path):
    """Without --path, reindex_all still covers every 'done' document."""
    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive import ingest

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    col = cfg.dropbox / "collections" / "COLA"
    col.mkdir(parents=True)
    (col / "a.pdf").write_bytes(b"%PDF-1.4 a")
    conn = _db.connect(cfg.db_path)
    da = _db.add_document(conn, filename="a.pdf", path=str((col / "a.pdf").resolve()),
                          sha256="a", size_bytes=10, mtime=1, kind="pdf",
                          dir_path="collections/COLA", now="2026-01-01")
    _db.set_document_status(conn, da, "done")
    conn.commit()
    conn.close()

    reindexed = []
    monkeypatch.setattr(ingest, "index_document",
                        lambda cfg_, conn_, doc_id, embed_client=None, verbose=True, **kw:
                        reindexed.append(doc_id) or 0)

    res = ingest.reindex_all(cfg, None)
    assert reindexed == [da]
    assert res == {"reindexed": 1, "chunks": {da: 0}, "failed": []}


class _CountingEmbed:
    """An embed client that records how many texts it was asked to embed.

    The vector encodes the text length, so a reused vector keeps the value it
    was stored with rather than being recomputed.
    """

    def __init__(self):
        self.calls: list[list[str]] = []

    @property
    def texts(self) -> int:
        return sum(len(c) for c in self.calls)

    def embed(self, model, texts, batch_size=1, **kw):
        texts = list(texts)
        self.calls.append(texts)
        return [[float(len(t)), 1.0, 2.0, 3.0] for t in texts]

    def close(self):
        pass


def _doc_with_pages(cfg, pages=3, editor=None):
    """A dropbox document with N transcribed pages; returns (conn, doc_id, page_ids)."""
    import time as _t
    from personal_historical_archive import db as _db

    src = cfg.dropbox / "collections" / "tcol" / "vol.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="vol.pdf", path=str(src), sha256="a",
                              size_bytes=10, mtime=1, kind="pdf",
                              dir_path="collections/tcol", now=_t.time(), editor=editor)
    page_ids = []
    for n in range(1, pages + 1):
        pid = _db.add_page(conn, doc_id, n)
        _db.set_page_result(conn, pid, raw_text=f"PAGE-{n} " + "word " * 100)
        page_ids.append(pid)
    _db.update_document(conn, doc_id, page_count=pages)
    conn.commit()
    return conn, doc_id, page_ids


def _cfg_at(tmp_path):
    from personal_historical_archive.config import Config

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {tmp_path / 'arc'}\n")
    return Config.load(root)


def test_index_document_reuses_unchanged_chunks(tmp_path):
    """Correcting one page re-embeds that page's chunk only.

    The reported limitation: rewriting one page of a volume forced a reindex of
    every page in it. `index_document` is incremental now — an unchanged chunk
    whose stored vector came from the current embed model is reused.
    """
    from personal_historical_archive import db as _db
    from personal_historical_archive.ingest import index_document

    cfg = _cfg_at(tmp_path)
    conn, doc_id, pids = _doc_with_pages(cfg, 3)

    first = _CountingEmbed()
    assert index_document(cfg, conn, doc_id, embed_client=first, verbose=False) == 3
    assert first.texts == 3, "a first index embeds every chunk"
    before = {r["page_id"]: r["embedding"] for r in conn.execute(
        "SELECT page_id, embedding FROM chunks WHERE document_id=? AND variant='raw'",
        (doc_id,))}
    assert {r["embed_model"] for r in conn.execute(
        "SELECT embed_model FROM chunks WHERE document_id=?", (doc_id,))} == {cfg.embed_model}

    # the historian corrects page 2's reading
    _db.set_page_result(conn, pids[1], raw_text="PAGE-2 CORRECTED " + "word " * 100)
    conn.commit()

    second = _CountingEmbed()
    assert index_document(cfg, conn, doc_id, embed_client=second, verbose=False) == 3
    assert second.texts == 1, "only the changed page's chunk is re-embedded"
    after = {r["page_id"]: r["embedding"] for r in conn.execute(
        "SELECT page_id, embedding FROM chunks WHERE document_id=? AND variant='raw'",
        (doc_id,))}
    assert after[pids[0]] == before[pids[0]], "an untouched page keeps its stored vector"
    assert after[pids[2]] == before[pids[2]]
    conn.close()


def test_index_document_noop_reindex_makes_no_embed_call(tmp_path):
    """Re-indexing with nothing changed embeds nothing and rewrites nothing."""
    from personal_historical_archive.ingest import index_document

    cfg = _cfg_at(tmp_path)
    conn, doc_id, _pids = _doc_with_pages(cfg, 2)
    index_document(cfg, conn, doc_id, embed_client=_CountingEmbed(), verbose=False)
    ids_before = [r["id"] for r in conn.execute(
        "SELECT id FROM chunks WHERE document_id=? ORDER BY id", (doc_id,))]

    again = _CountingEmbed()
    assert index_document(cfg, conn, doc_id, embed_client=again, verbose=False) == 2
    assert again.texts == 0, "an unchanged index is not re-embedded"
    assert [r["id"] for r in conn.execute(
        "SELECT id FROM chunks WHERE document_id=? ORDER BY id", (doc_id,))] == ids_before
    conn.close()


def test_index_document_reembeds_when_embed_model_changed(tmp_path):
    """A vector whose embed_model is unknown or different is never reused."""
    from personal_historical_archive.ingest import index_document

    cfg = _cfg_at(tmp_path)
    conn, doc_id, _pids = _doc_with_pages(cfg, 2)
    index_document(cfg, conn, doc_id, embed_client=_CountingEmbed(), verbose=False)

    # a vector from another model (or from before the column existed: NULL)
    for stored in ("some-other-model", None):
        conn.execute("UPDATE chunks SET embed_model=? WHERE document_id=?", (stored, doc_id))
        conn.commit()
        again = _CountingEmbed()
        index_document(cfg, conn, doc_id, embed_client=again, verbose=False)
        assert again.texts == 2, f"embed_model={stored!r} must not be reused"
    conn.close()


def test_index_document_force_reembeds_everything(tmp_path):
    """incremental=False is the explicit full rebuild (`pha reindex --force`)."""
    from personal_historical_archive.ingest import index_document

    cfg = _cfg_at(tmp_path)
    conn, doc_id, _pids = _doc_with_pages(cfg, 3)
    index_document(cfg, conn, doc_id, embed_client=_CountingEmbed(), verbose=False)

    forced = _CountingEmbed()
    index_document(cfg, conn, doc_id, embed_client=forced, verbose=False, incremental=False)
    assert forced.texts == 3
    conn.close()


def test_reindex_all_doc_scopes_to_one_document(monkeypatch, tmp_path):
    """`pha reindex --doc N` touches only that document."""
    from personal_historical_archive import db as _db
    from personal_historical_archive import ingest

    cfg = _cfg_at(tmp_path)
    conn, da, _pids = _doc_with_pages(cfg, 2)
    _db.add_document(conn, filename="other.pdf", path=str(cfg.dropbox / "other.pdf"),
                     sha256="b", size_bytes=10, mtime=1, kind="pdf",
                     dir_path="collections/COLX", now="2026-01-01")
    other = _db.get_document_by_path(conn, str(cfg.dropbox / "other.pdf"))["id"]
    _db.set_document_status(conn, da, "done")
    _db.set_document_status(conn, other, "done")
    conn.commit()
    conn.close()

    seen = []
    monkeypatch.setattr(ingest, "index_document",
                        lambda cfg_, conn_, doc_id, **kw: seen.append((doc_id, kw)) or 0)
    res = ingest.reindex_all(cfg, None, doc=da)
    assert [s[0] for s in seen] == [da]
    assert res == {"reindexed": 1, "chunks": {da: 0}, "failed": []}

    seen.clear()
    res = ingest.reindex_all(cfg, None, doc=99999)
    assert seen == [] and res["reindexed"] == 0
    assert "no document #99999" in res["reason"]


def test_reindex_all_page_scope_leaves_other_pages_untouched(tmp_path):
    """`pha reindex --doc N --page P` replaces only page P's chunks."""
    from personal_historical_archive import db as _db
    from personal_historical_archive.ingest import index_document, reindex_all

    cfg = _cfg_at(tmp_path)
    conn, doc_id, pids = _doc_with_pages(cfg, 3)
    _db.set_document_status(conn, doc_id, "done")
    conn.commit()
    index_document(cfg, conn, doc_id, embed_client=_CountingEmbed(), verbose=False)
    before = {r["page_id"]: (r["id"], r["embedding"]) for r in conn.execute(
        "SELECT id, page_id, embedding FROM chunks WHERE document_id=?", (doc_id,))}
    conn.close()

    # correct page 2, then reindex just that page
    conn = _db.connect(cfg.db_path)
    _db.set_page_result(conn, pids[1], raw_text="PAGE-2 CORRECTED " + "word " * 100)
    conn.commit()
    conn.close()

    counting = _CountingEmbed()
    res = reindex_all(cfg, counting, verbose=False, doc=doc_id, page=2)
    assert res["reindexed"] == 1 and counting.texts == 1

    conn = _db.connect(cfg.db_path)
    after = {r["page_id"]: (r["id"], r["embedding"]) for r in conn.execute(
        "SELECT id, page_id, embedding FROM chunks WHERE document_id=?", (doc_id,))}
    # pages 1 and 3 are byte-for-byte the same rows (same ids, same vectors)
    assert after[pids[0]] == before[pids[0]]
    assert after[pids[2]] == before[pids[2]]
    text = conn.execute("SELECT text FROM chunks WHERE document_id=? AND page_id=?",
                        (doc_id, pids[1])).fetchone()["text"]
    assert text.startswith("PAGE-2 CORRECTED")
    # the corrected page's own vector was re-embedded (a new length-bearing vector)
    assert after[pids[1]][1] != before[pids[1]][1]
    conn.close()


def test_reindex_all_page_without_doc_refuses(tmp_path):
    """A page-scoped reindex with no document is refused, not applied archive-wide."""
    from personal_historical_archive.ingest import reindex_all

    cfg = _cfg_at(tmp_path)
    res = reindex_all(cfg, None, page=5)
    assert res["reindexed"] == 0
    assert "--page needs --doc" in res["reason"]


def test_library_page_path_resolves_raw_and_edited(tmp_path):
    from personal_historical_archive.ingest import library_page_path

    cfg = SimpleNamespace(library=tmp_path / "library")
    (cfg.library / "doc_1970-01-01" / "transcription-default@qwen").mkdir(parents=True)
    (cfg.library / "doc_1970-01-01" / "edited-latin-english@minimax").mkdir(parents=True)
    raw_f = cfg.library / "doc_1970-01-01" / "transcription-default@qwen" / "page-007.md"
    raw_f.write_text("raw")
    ed_f = cfg.library / "doc_1970-01-01" / "edited-latin-english@minimax" / "page-007.md"
    ed_f.write_text("edited")
    doc = {"path": "/x/doc.pdf", "dir_path": "", "created_at": 0,
           "palaeographer": "default", "editor": "latin-english",
           "palaeographer_model": "qwen", "editor_model": "minimax"}
    assert library_page_path(cfg, doc, 7) == raw_f
    assert library_page_path(cfg, doc, 7, variant="edited", editor_id="latin-english") == ed_f
    assert library_page_path(cfg, doc, 99) is None
    # falls back to the newest <stem>_* folder when the exact slug is missing
    old = cfg.library / "doc_2020-01-01"
    (old / "transcription-default").mkdir(parents=True)
    (old / "transcription-default" / "page-001.md").write_text("x")
    doc2 = dict(doc, created_at=1_700_000_000)  # slug doc_2023-11-14 -> not on disk
    assert library_page_path(cfg, doc2, 1) == old / "transcription-default" / "page-001.md"


def test_library_page_path_prefers_the_model_qualified_alias(tmp_path):
    """`edited-X` and `edited-X@Y` are one variant: the bare alias must not win
    the page just because it sorts/first appears earlier (docs 47/50 served 610
    pages of `*waiting*` that way)."""
    from personal_historical_archive.ingest import library_page_path

    cfg = SimpleNamespace(library=tmp_path / "library")
    doc_dir = cfg.library / "doc_1970-01-01"
    (doc_dir / "edited-mod").mkdir(parents=True)
    (doc_dir / "edited-mod" / "page-001.md").write_text("*waiting*")
    (doc_dir / "edited-mod@minimax").mkdir(parents=True)
    real = doc_dir / "edited-mod@minimax" / "page-001.md"
    real.write_text("real text")
    doc = {"path": "/x/doc.pdf", "dir_path": "", "created_at": 0,
           "palaeographer": "default", "palaeographer_model": "qwen",
           "editor": "mod", "editor_model": "minimax"}
    assert library_page_path(cfg, doc, 1, variant="edited", editor_id="mod") == real
    # a model-less current editor keeps its bare directory (the qualified
    # sibling is an older, different reading)
    doc_none = dict(doc, editor_model=None)
    (doc_dir / "edited-mod" / "page-001.md").write_text("model-less text")
    assert library_page_path(cfg, doc_none, 1, variant="edited",
                             editor_id="mod") == doc_dir / "edited-mod" / "page-001.md"


def test_write_edited_pages_names_the_directory_from_the_resolved_model(tmp_path):
    """The writer must not re-read the nullable `editor_model` column: while an
    editor change is being recorded it is NULL, and reading it there wrote the
    same variant into `edited-mod` *and* `edited-mod@m1`."""
    import time as _t

    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive.ingest import write_edited_pages

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {tmp_path / 'arc'}\n")
    cfg = Config.load(root)
    src_dir = cfg.dropbox / "collections" / "tcol"
    src_dir.mkdir(parents=True)
    src = src_dir / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="doc.pdf", path=str(src), sha256="a",
                              size_bytes=10, mtime=1, kind="pdf",
                              dir_path="collections/tcol", now=_t.time(),
                              editor="mod", editor_model=None)  # NULL on purpose
    pid = _db.add_page(conn, doc_id, 1)
    _db.set_page_result(conn, pid, raw_text="raw page one with plenty of text")
    _db.set_page_edit(conn, pid, "mod", text="EDITED", raw_sha="x")
    _db.update_document(conn, doc_id, page_count=1)
    conn.commit()

    out = write_edited_pages(cfg, conn, doc_id, "mod", model="m1")
    assert out.name == "edited-mod@m1"
    parent = out.parent
    assert sorted(d.name for d in parent.iterdir() if d.is_dir()) == ["edited-mod@m1"]
    # the front matter names the model the pass actually used, not the NULL column
    assert "model: m1" in (out / "page-001.md").read_text(encoding="utf-8")
    conn.close()


def test_edit_document_writes_exactly_one_edited_directory(tmp_path, monkeypatch):
    """The pre-fix write grew a bare `edited-default` directory page by page and
    the final write added `edited-default@<model>` — two directories for one
    variant. One pass must produce one directory, named with the model."""
    import time as _t

    from personal_historical_archive.config import Config
    from personal_historical_archive import db as _db
    from personal_historical_archive.ingest import _doc_slug, edit_document

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {tmp_path / 'arc'}\n")
    cfg = Config.load(root)
    src_dir = cfg.dropbox / "collections" / "tcol"
    src_dir.mkdir(parents=True)
    src = src_dir / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="doc.pdf", path=str(src), sha256="a",
                              size_bytes=10, mtime=1, kind="pdf",
                              dir_path="collections/tcol", now=_t.time(),
                              editor_model=None)  # NULL at the start of the pass
    for n in (1, 2):
        pid = _db.add_page(conn, doc_id, n)
        _db.set_page_result(
            conn, pid, raw_text=f"raw page {n} with more than forty characters of text")
    _db.update_document(conn, doc_id, page_count=2)
    conn.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

        def chat_text(self, model, prompt, temperature, max_tokens, thinking):
            return "edited"

    monkeypatch.setattr("personal_historical_archive.ingest.ModelClient", FakeClient)
    res = edit_document(cfg, conn, doc_id, editor_id="default")
    assert res["pages"] == 2

    doc = _db.get_document(conn, doc_id)
    assert doc["editor_model"]  # the pass recorded the model it used
    doc_dir = cfg.library / "collections" / "tcol" / _doc_slug(doc)
    dirs = sorted(d.name for d in doc_dir.iterdir() if d.is_dir())
    assert dirs == [f"edited-default@{doc['editor_model']}"], dirs
    conn.close()


def test_pages_dir_for_prefers_the_model_qualified_alias(cfg, add_document):
    """`_pages_dir_for` must return `@Y` when both `edited-X` and `edited-X@Y`
    exist — the bare one sorts first and used to win."""
    from personal_historical_archive import db as _db
    from personal_historical_archive.ingest import _doc_slug, _pages_dir_for

    doc_id = add_document(editor="french-ocr", editor_model="deepseek-v4-flash")
    conn = _db.connect(cfg.db_path)
    try:
        doc = _db.get_document(conn, doc_id)
    finally:
        conn.close()
    base = cfg.library / "collections" / "COLX" / _doc_slug(doc)
    (base / "edited-french-ocr").mkdir(parents=True)
    (base / "edited-french-ocr@deepseek-v4-flash").mkdir(parents=True)
    assert _pages_dir_for(cfg, doc, "edited-french-ocr") == \
        base / "edited-french-ocr@deepseek-v4-flash"


# --------------------------------------------------------------------------- discover: a single-file --path

def test_discover_single_file_root_is_that_document(tmp_path):
    """`pha scan --path <a single .pdf>` must find that one document. It used
    to walk `root.rglob(...)`, which yields nothing for a file root, so the
    command silently scanned zero files while AGENTS.md documents it."""
    drop = tmp_path / "dropbox"
    col = drop / "collections" / "COLX"
    col.mkdir(parents=True)
    doc = col / "a.pdf"
    doc.write_bytes(b"%PDF-1.4 a")
    (col / "b.pdf").write_bytes(b"%PDF-1.4 b")
    assert discover(drop, True, root=doc) == [doc]
    # a file outside any document-dir, and a non-document file
    (drop / "notes.txt").write_bytes(b"x")
    assert discover(drop, True, root=drop / "notes.txt") == []
    # excluded (e.g. the inbox) still wins
    assert discover(drop, True, root=doc, exclude=[col]) == []


# --------------------------------------------------------------------------- `done` is written last

def _tiny_ingest_cfg(tmp_path):
    from personal_historical_archive.config import Config

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  models: models\n  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


def _stub_ingest(monkeypatch, ingest_mod, index_impl):
    """Stub rendering/transcription/editing and the indexer for ingest_file."""
    from personal_historical_archive.config import Palaeographer

    monkeypatch.setattr(ingest_mod, "page_count", lambda path: 1)

    def fake_render(path, out_dir, dpi, max_px, q, prefix=None, pages=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        f = out_dir / "p001.jpg"
        f.write_bytes(b"jpeg")
        return [f]

    monkeypatch.setattr(ingest_mod, "render_document", fake_render)
    monkeypatch.setattr(ingest_mod, "transcribe_page", lambda *a, **k: "TEXT")
    monkeypatch.setattr(ingest_mod, "edit_document", lambda *a, **k: None)
    monkeypatch.setattr(ingest_mod, "index_document", index_impl)
    return Palaeographer(id="default", description="", base_url="", api_key="", model="",
                         temperature=0.1, max_tokens=16, timeout_s=10, prompt_text="Transcribe.")


def test_ingest_sets_done_only_after_indexing(tmp_path, monkeypatch):
    """`done` is written AFTER the editor and indexer: while index_document
    runs, the document is still 'processing', and only then becomes 'done'.
    This is what made doc 57's done-but-0-chunks state impossible to spot."""
    from personal_historical_archive import db as _db
    from personal_historical_archive import ingest

    cfg = _tiny_ingest_cfg(tmp_path)
    src = cfg.dropbox / "documents" / "doc.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)
    seen: dict = {}

    def index_impl(cfg_, conn_, doc_id, **k):
        seen["status_while_indexing"] = _db.get_document(conn_, doc_id)["status"]
        return 3

    pal = _stub_ingest(monkeypatch, ingest, index_impl)
    res = ingest.ingest_file(cfg, conn, object(), src, pal, verbose=False)
    assert res["action"] == "ingested"
    assert seen["status_while_indexing"] == "processing"
    assert _db.get_document_by_path(conn, str(src))["status"] == "done"
    conn.close()


def test_ingest_leaves_processing_when_indexing_crashes(tmp_path, monkeypatch):
    """A crash while indexing must not leave a healthy-looking `done` row: the
    document stays 'processing' so the next scan resumes it (the 2026-09-15
    incident reported such a row as complete for hours)."""
    import pytest

    from personal_historical_archive import db as _db
    from personal_historical_archive import ingest
    from personal_historical_archive.model_client import ModelError

    cfg = _tiny_ingest_cfg(tmp_path)
    src = cfg.dropbox / "documents" / "doc.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF-1.4 fake")
    conn = _db.connect(cfg.db_path)

    def boom(*a, **k):
        raise ModelError("embeddings unavailable")

    pal = _stub_ingest(monkeypatch, ingest, boom)
    with pytest.raises(ModelError):
        ingest.ingest_file(cfg, conn, object(), src, pal, verbose=False)
    doc = _db.get_document_by_path(conn, str(src))
    assert doc["status"] == "processing"      # resumable, not a false 'done'
    assert doc["status"] != "done"
    conn.close()


# --------------------------------------------------------------------------- `pha edit` indexes what it touched

def test_index_after_edit_decides_by_data(tmp_path, monkeypatch):
    """`_index_after_edit` indexes when pages were re-edited OR when the
    document has no chunks at all (the missing-index repair), and never
    touches a document that is edited-and-indexed already."""
    from personal_historical_archive import db as _db
    from personal_historical_archive import ingest
    from personal_historical_archive.model_client import ModelError

    cfg = _tiny_ingest_cfg(tmp_path)
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True)
    src = col / "d.pdf"
    src.write_bytes(b"%PDF-1.4 d")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="d.pdf", path=str(src), sha256="a",
                              size_bytes=1, mtime=1, kind="pdf",
                              dir_path="collections/COLX", now="2026-01-01")
    _db.set_document_status(conn, doc_id, "done")
    page = _db.add_page(conn, doc_id, 1)
    _db.add_chunk(conn, doc_id, page, 0, "text", None, "raw")
    conn.commit()

    calls: list[int] = []
    monkeypatch.setattr(ingest, "index_document",
                        lambda cfg_, conn_, doc_id_, **k: calls.append(doc_id_) or 5)

    # edited nothing, index present -> no work, no embed call
    assert ingest._index_after_edit(cfg, conn, doc_id, 0, verbose=False) == {"indexed": False}
    assert calls == []
    # edited pages -> index
    assert ingest._index_after_edit(cfg, conn, doc_id, 2, verbose=False)["indexed"] is True
    assert calls == [doc_id]
    # nothing edited but the index is gone -> repair it
    _db.clear_chunks(conn, doc_id)
    conn.commit()
    assert ingest._index_after_edit(cfg, conn, doc_id, 0, verbose=False)["indexed"] is True
    assert calls == [doc_id, doc_id]

    # a failing embed is reported, not raised
    def boom(*a, **k):
        raise ModelError("embed down")
    monkeypatch.setattr(ingest, "index_document", boom)
    res = ingest._index_after_edit(cfg, conn, doc_id, 2, verbose=False)
    assert res["indexed"] is False and "embed down" in res["index_error"]
    conn.close()


def test_edit_all_indexes_edited_documents(tmp_path, monkeypatch):
    """`pha edit` re-indexes the documents it re-edited, so search covers the
    new edited text without a separate archive-wide `pha reindex`."""
    from personal_historical_archive import db as _db
    from personal_historical_archive import ingest

    cfg = _tiny_ingest_cfg(tmp_path)
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True)
    src = col / "d.pdf"
    src.write_bytes(b"%PDF-1.4 d")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="d.pdf", path=str(src), sha256="a",
                              size_bytes=1, mtime=1, kind="pdf",
                              dir_path="collections/COLX", now="2026-01-01")
    _db.set_document_status(conn, doc_id, "done")
    conn.commit()
    conn.close()

    calls: list[int] = []
    monkeypatch.setattr(ingest, "edit_document",
                        lambda cfg_, conn_, doc_id_, **k: {"action": "edited", "filename": "d.pdf",
                                                           "editor": "null", "pages": 4})
    monkeypatch.setattr(ingest, "index_document",
                        lambda cfg_, conn_, doc_id_, **k: calls.append(doc_id_) or 9)

    res = ingest.edit_all(cfg, verbose=False)
    assert calls == [doc_id]
    assert res["indexed"] == 1
    assert res["index_failed"] == []

