from __future__ import annotations

from types import SimpleNamespace

from personal_historical_archive.ingest import (
    _expand_records,
    _page_filter,
    _parse_json_array,
    _record_key,
    _record_similar,
    _regex_candidates,
    chunk_text,
)


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
