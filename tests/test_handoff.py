"""`pha handoff` — the two-machine round trip, exercised on one machine.

The transport is a directory, so both "machines" are two archives in one
`tmp_path`; that is the same code path a LAN pair uses. The properties under
test are the ones the design argues for: content identity, resume-not-skip,
human work wins, and a lease the pipeline honours.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from personal_historical_archive import db as _db
from personal_historical_archive import handoff
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import (
    _raw_sha,
    sha256_of,
    write_document_pages,
)


def _cfg(tmp_path: Path, name: str) -> Config:
    root = tmp_path / name
    root.mkdir()
    (root / "config.yaml").write_text(
        f"paths:\n  archive_dir: {root / 'arc'}\n"
        "  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


def _document(cfg: Config, *, pages: int = 3, done: int = 1, reviewed: int = 0):
    """A partially processed document: `done` real pages, the rest stubs."""
    collection = "collections/DI"
    col = cfg.dropbox / collection
    col.mkdir(parents=True, exist_ok=True)
    src = col / "vol04.pdf"
    src.write_bytes(b"%PDF-1.4 vol04")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(
        conn, filename="vol04.pdf", path=str(src), sha256=sha256_of(src),
        size_bytes=src.stat().st_size, mtime=src.stat().st_mtime, kind="pdf",
        now=time.time(), dir_path=collection, palaeographer="ocr", editor="e1",
    )
    pids = []
    for pno in range(1, pages + 1):
        pid = _db.add_page(conn, doc_id, pno)
        pids.append(pid)
        if pno <= done:
            _db.set_page_result(conn, pid, raw_text=f"PAGE {pno} machine text")
    for pid in pids[:reviewed]:
        _db.mark_page_reviewed(conn, pid, "PAGE 1 human text")
    _db.update_document(conn, doc_id, page_count=pages)
    _db.set_document_status(conn, doc_id, "done" if done >= pages else "processing")
    conn.commit()
    write_document_pages(cfg, conn, doc_id)
    conn.close()
    return doc_id, pids


def _pages(cfg: Config, doc_id: int):
    conn = _db.connect(cfg.db_path)
    try:
        return {
            r["page_no"]: r
            for r in conn.execute(
                "SELECT page_no, raw_text, status, reviewed_at FROM pages "
                "WHERE document_id = ? ORDER BY page_no", (doc_id,),
            )
        }
    finally:
        conn.close()


def _worker_finish(cfg: Config, handoff_dir: Path, pages: list[int]):
    """Finish `pages` in the worker archive, as a real run would."""
    hid = handoff.read_manifest(handoff_dir)["handoff_id"]
    conn = _db.connect(cfg.db_path)
    try:
        doc_id = int(conn.execute("SELECT id FROM documents").fetchone()["id"])
        for pno in pages:
            pid = conn.execute(
                "SELECT id FROM pages WHERE document_id=? AND page_no=?", (doc_id, pno)
            ).fetchone()["id"]
            text = f"PAGE {pno} from the worker"
            _db.set_page_result(conn, pid, raw_text=text)
            _db.set_page_edit(conn, pid, "e1", text=text.upper(), raw_sha=_raw_sha(text))
        _db.set_document_status(conn, doc_id, "done")
        conn.commit()
    finally:
        conn.close()
    return hid


# ------------------------------------------------------------------ stub safety

def test_payload_carries_no_stub_and_import_resumes(tmp_path, monkeypatch):
    """A hand-out must not hand over placeholders, and must leave the worker
    able to finish the document (R2/R3)."""
    monkeypatch.chdir(tmp_path)
    a = _cfg(tmp_path, "projA")
    _document(a, pages=3, done=1)

    out = tmp_path / "ho"
    res = handoff.export_handoff(a, ["collections/DI"], out, verbose=False)
    assert res["stubs_dropped"] == 2, "the two un-extracted pages must be left behind"
    carried = list((out / "library").rglob("*.md"))
    assert len(carried) == 1, f"only the real page travels, got {carried}"
    assert "*waiting*" not in carried[0].read_text(encoding="utf-8")

    b = _cfg(tmp_path, "projB")
    handoff.import_handoff(b, out, verbose=False)
    pages = _pages(b, 1)
    assert pages[1]["raw_text"] == "PAGE 1 machine text"
    assert pages[2]["raw_text"] in (None, "")
    assert pages[3]["raw_text"] in (None, "")
    conn = _db.connect(b.db_path)
    try:
        doc = conn.execute("SELECT status, page_count FROM documents").fetchone()
    finally:
        conn.close()
    assert doc["status"] == "processing", "the worker must resume, not treat it as done"
    assert doc["page_count"] == 3


# ------------------------------------------------------------------- round trip

def test_round_trip_applies_in_place_and_keeps_human_work(tmp_path, monkeypatch):
    """Same id, the worker's text applied, a local human correction untouched
    (R1/R4/R5/R6)."""
    monkeypatch.chdir(tmp_path)
    a = _cfg(tmp_path, "projA")
    doc_id, pids = _document(a, pages=3, done=1, reviewed=1)

    out = tmp_path / "ho"
    res = handoff.export_handoff(a, ["collections/DI"], out, worker="studio", verbose=False)
    hid = res["handoff_id"]

    b = _cfg(tmp_path, "projB")
    handoff.import_handoff(b, out, verbose=False)
    _worker_finish(b, out, [2, 3])

    back = tmp_path / "ho-back"
    handoff.build_result(b, out, back, verbose=False)
    applied = handoff.apply_result(a, back, verbose=False)

    pages = _pages(a, doc_id)
    assert pages[1]["raw_text"] == "PAGE 1 human text"
    assert pages[1]["reviewed_at"] is not None, "local human work survives"
    assert pages[2]["raw_text"] == "PAGE 2 from the worker"
    assert pages[3]["raw_text"] == "PAGE 3 from the worker"
    assert pages[2]["reviewed_at"] is None, "machine work is not 'reviewed' (R6)"

    conn = _db.connect(a.db_path)
    try:
        docs = conn.execute("SELECT id, status FROM documents").fetchall()
        assert len(docs) == 1, "the result updates in place, never adds a document"
        assert int(docs[0]["id"]) == doc_id
        assert docs[0]["status"] == "done"
        edit = conn.execute(
            "SELECT text FROM page_edits WHERE page_id=?", (pids[1],)
        ).fetchone()
        assert edit["text"] == "PAGE 2 FROM THE WORKER"
    finally:
        conn.close()
    assert applied["counts"]["conflict"] == 0
    assert handoff.read_lease(a, hid).state == handoff.STATE_APPLIED
    assert handoff.active_leases(a) == []


# ------------------------------------------------------------------- conflicts

def test_conflicting_human_readings_are_reported_not_resolved(tmp_path, monkeypatch):
    """Reviewed on both sides with different text: local wins, and it is
    reported (R5)."""
    monkeypatch.chdir(tmp_path)
    a = _cfg(tmp_path, "projA")
    doc_id, _ = _document(a, pages=2, done=2, reviewed=1)

    out = tmp_path / "ho"
    handoff.export_handoff(a, ["collections/DI"], out, verbose=False)
    b = _cfg(tmp_path, "projB")
    handoff.import_handoff(b, out, verbose=False)

    conn = _db.connect(b.db_path)
    try:
        bdoc = int(conn.execute("SELECT id FROM documents").fetchone()["id"])
        pid = conn.execute(
            "SELECT id FROM pages WHERE document_id=? AND page_no=1", (bdoc,)
        ).fetchone()["id"]
        _db.mark_page_reviewed(conn, pid, "PAGE 1 the worker's reading")
        conn.commit()
    finally:
        conn.close()

    back = tmp_path / "ho-back"
    handoff.build_result(b, out, back, verbose=False)
    handoff.apply_result(a, back, verbose=False)
    page = _pages(a, doc_id)[1]
    assert page["raw_text"] == "PAGE 1 human text", "the local human reading wins"
    assert page["reviewed_at"] is not None


# -------------------------------------------------------------- edit provenance

def test_edit_from_a_different_reading_is_dropped(tmp_path, monkeypatch):
    """An edit whose `raw_sha` does not match the applied raw is meaningless."""
    monkeypatch.chdir(tmp_path)
    a = _cfg(tmp_path, "projA")
    _document(a, pages=1, done=1)

    out = tmp_path / "ho"
    handoff.export_handoff(a, ["collections/DI"], out, verbose=False)
    b = _cfg(tmp_path, "projB")
    handoff.import_handoff(b, out, verbose=False)

    conn = _db.connect(b.db_path)
    try:
        bdoc = int(conn.execute("SELECT id FROM documents").fetchone()["id"])
        pid = conn.execute("SELECT id FROM pages WHERE document_id=?", (bdoc,)).fetchone()["id"]
        _db.set_page_edit(conn, pid, "e1", text="EDIT OF A DIFFERENT READING",
                          raw_sha=_raw_sha("something else entirely"))
        conn.commit()
    finally:
        conn.close()

    back = tmp_path / "ho-back"
    handoff.build_result(b, out, back, verbose=False)
    handoff.apply_result(a, back, verbose=False)
    conn = _db.connect(a.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) n FROM page_edits").fetchone()["n"] == 0
    finally:
        conn.close()


# --------------------------------------------------------------------- the lease

def test_lease_blocks_a_second_handout_and_can_be_released(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = _cfg(tmp_path, "projA")
    _document(a, pages=2, done=1)

    out = tmp_path / "ho"
    res = handoff.export_handoff(a, ["collections/DI"], out, verbose=False)
    hid = res["handoff_id"]

    with pytest.raises(handoff.HandoffError) as e:
        handoff.export_handoff(a, ["collections/DI"], tmp_path / "ho2", verbose=False)
    assert "already out" in str(e.value)

    assert [l.handoff_id for l in handoff.active_leases(a)] == [hid]
    assert "collections/DI/vol04.pdf" in "\n".join(handoff.status_lines(a))

    handoff.release(a, hid, handoff.STATE_CANCELLED)
    assert handoff.active_leases(a) == []
    res2 = handoff.export_handoff(a, ["collections/DI"], tmp_path / "ho3", verbose=False)
    assert res2["handoff_id"] != hid


def test_fetch_refuses_a_result_with_no_lease_here(tmp_path, monkeypatch):
    """A stray result must not be applied to the wrong document set."""
    monkeypatch.chdir(tmp_path)
    a = _cfg(tmp_path, "projA")
    _document(a, pages=1, done=1)
    out = tmp_path / "ho"
    handoff.export_handoff(a, ["collections/DI"], out, verbose=False)
    b = _cfg(tmp_path, "projB")
    handoff.import_handoff(b, out, verbose=False)
    back = tmp_path / "ho-back"
    handoff.build_result(b, out, back, verbose=False)

    for lease in handoff.active_leases(a):
        handoff.release(a, lease.handoff_id, handoff.STATE_CANCELLED)
    with pytest.raises(handoff.HandoffError) as e:
        handoff.apply_result(a, back, verbose=False)
    # the lease exists but is no longer open, so the result must still be refused
    assert "already cancelled" in str(e.value)


# -------------------------------------------------------------------- dry runs

def test_import_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = _cfg(tmp_path, "projA")
    _document(a, pages=2, done=1)
    out = tmp_path / "ho"
    handoff.export_handoff(a, ["collections/DI"], out, verbose=False)

    b = _cfg(tmp_path, "projB")
    res = handoff.import_handoff(b, out, verbose=False, dry_run=True)
    assert res["dry_run"] is True
    conn = _db.connect(b.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) n FROM documents").fetchone()["n"] == 0
        assert not list(b.library.rglob("*.md"))
    finally:
        conn.close()


def test_back_dry_run_reports_counts_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = _cfg(tmp_path, "projA")
    _document(a, pages=3, done=1)
    out = tmp_path / "ho"
    handoff.export_handoff(a, ["collections/DI"], out, verbose=False)

    b = _cfg(tmp_path, "projB")
    handoff.import_handoff(b, out, verbose=False)
    doc_id, pids = None, None
    conn = _db.connect(b.db_path)
    try:
        row = conn.execute("SELECT id FROM documents").fetchone()
        doc_id = int(row["id"])
        # the worker finishes the two pages A had not done
        for pno in (2, 3):
            pid = conn.execute(
                "SELECT id FROM pages WHERE document_id = ? AND page_no = ?",
                (doc_id, pno),
            ).fetchone()["id"]
            _db.set_page_result(conn, int(pid), raw_text=f"PAGE {pno} worker text")
        conn.commit()
    finally:
        conn.close()

    back = tmp_path / "ho-back"
    res = handoff.build_result(b, out, back, verbose=False, dry_run=True)
    assert res["dry_run"] is True
    assert res["counts"]["pages_done"] == 3
    assert not back.exists(), "a dry run must not create the result directory"
    assert not (back / handoff.RESULT_NAME).exists()

    # …and the real run produces exactly what the dry run predicted
    handoff.build_result(b, out, back, verbose=False)
    real = handoff.read_result(back)
    assert real["counts"] == res["counts"]
    assert real["handoff_id"] == res["handoff_id"]


def test_fetch_dry_run_leaves_the_owner_untouched(tmp_path, monkeypatch):
    """`fetch --dry-run` must report the merge without performing it."""
    monkeypatch.chdir(tmp_path)
    a = _cfg(tmp_path, "projA")
    a_doc, _ = _document(a, pages=3, done=1)

    out = tmp_path / "ho"
    handoff.export_handoff(a, ["collections/DI"], out, verbose=False)

    b = _cfg(tmp_path, "projB")
    handoff.import_handoff(b, out, verbose=False)
    conn = _db.connect(b.db_path)
    try:
        wid = int(conn.execute("SELECT id FROM documents").fetchone()["id"])
        for pno in (2, 3):
            pid = conn.execute(
                "SELECT id FROM pages WHERE document_id = ? AND page_no = ?",
                (wid, pno),
            ).fetchone()["id"]
            _db.set_page_result(conn, int(pid), raw_text=f"PAGE {pno} worker text")
        conn.commit()
    finally:
        conn.close()
    back = tmp_path / "ho-back"
    handoff.build_result(b, out, back, verbose=False)

    before = _pages(a, a_doc)
    res = handoff.apply_result(a, back, verbose=False, dry_run=True)
    assert res["dry_run"] is True
    # pages 2 and 3 carry new text; page 1 comes back identical (the worker was
    # handed the finished page) and still counts as adopting the worker's row
    assert res["counts"][handoff.TOOK_WORKER] == 3
    assert res["counts"][handoff.KEPT_LOCAL] == 0
    assert res["counts"][handoff.CONFLICT] == 0

    after = _pages(a, a_doc)
    assert {p: (r["raw_text"], r["status"]) for p, r in after.items()} == \
           {p: (r["raw_text"], r["status"]) for p, r in before.items()}, \
        "dry run changed the archive"
    # the lease is still open, so the real apply is still possible
    assert [l.handoff_id for l in handoff.active_leases(a)] == [res["handoff_id"]]

    real = handoff.apply_result(a, back, verbose=False)
    assert _pages(a, a_doc)[2]["raw_text"] == "PAGE 2 worker text"
    assert handoff.active_leases(a) == []
    # a dry run is only useful if it predicts the real run
    assert real["counts"] == res["counts"]
    assert real["conflicts"] == res["conflicts"] == []


def test_conflict_is_named_not_just_counted(tmp_path, monkeypatch):
    """Both sides corrected the same page differently: local wins, and the
    report says WHICH page — a bare count cannot be acted on."""
    monkeypatch.chdir(tmp_path)
    a = _cfg(tmp_path, "projA")
    a_doc, pids = _document(a, pages=2, done=2)

    out = tmp_path / "ho"
    handoff.export_handoff(a, ["collections/DI"], out, verbose=False)

    b = _cfg(tmp_path, "projB")
    handoff.import_handoff(b, out, verbose=False)
    # the worker's human corrects page 1 (a different reading)
    conn = _db.connect(b.db_path)
    try:
        wid = int(conn.execute("SELECT id FROM documents").fetchone()["id"])
        wpid = int(conn.execute(
            "SELECT id FROM pages WHERE document_id = ? AND page_no = 1", (wid,)
        ).fetchone()["id"])
        _db.mark_page_reviewed(conn, wpid, "PAGE 1 WORKER human reading")
        conn.commit()
    finally:
        conn.close()
    back = tmp_path / "ho-back"
    handoff.build_result(b, out, back, verbose=False)

    # …and so does the owner's, differently
    conn = _db.connect(a.db_path)
    try:
        _db.mark_page_reviewed(conn, pids[0], "PAGE 1 OWNER human reading")
        conn.commit()
    finally:
        conn.close()

    # the dry run predicts it, naming the page
    dry = handoff.apply_result(a, back, verbose=False, dry_run=True)
    assert dry["counts"][handoff.CONFLICT] == 1
    assert dry["conflicts"] == [{"relpath": "collections/DI/vol04.pdf", "page": 1}]

    res = handoff.apply_result(a, back, verbose=False)
    assert res["conflicts"] == [{"relpath": "collections/DI/vol04.pdf", "page": 1}]
    assert res["counts"][handoff.CONFLICT] == 1
    # the owner's own reading survives, and is still marked reviewed
    p1 = _pages(a, a_doc)[1]
    assert p1["raw_text"] == "PAGE 1 OWNER human reading"
    assert p1["reviewed_at"]
