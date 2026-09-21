"""Batch behaviour of a model stall inside a multi-page pass.

The per-request wall-clock deadline is `pha-request-stall-timeout-bug-report.md`
(F1); the batch rule is the hand-over workflow request's **G4**: when the
deadline expires inside a multi-page pass, the page must be **abandoned for
that pass**, named in the output, and retried by a later pass — rather than
failing the page or the run.
"""
from __future__ import annotations

import time

from personal_historical_archive import db as _db
from personal_historical_archive import ingest
from personal_historical_archive.config import Config, Palaeographer
from personal_historical_archive.model_client import ModelError, ModelStall


def _cfg(tmp_path) -> Config:
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


def _pal() -> Palaeographer:
    return Palaeographer(id="default", description="", base_url="", api_key="", model="",
                         temperature=0.1, max_tokens=16, timeout_s=10, prompt_text="Transcribe.")


def _stub_scan(monkeypatch, pages: int, stall_on: set[int]):
    """Render `pages` pages and transcribe them, raising ModelStall on some."""
    page_total = pages

    def fake_render(path, out_dir, dpi, max_px, q, prefix=None, pages=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        return [out_dir / f"p{i:03d}.jpg" for i in range(1, page_total + 1)]

    monkeypatch.setattr(ingest, "page_count", lambda path: page_total)
    monkeypatch.setattr(ingest, "render_document", fake_render)
    transcribed: list[int] = []

    def fake_transcribe(client, pal, prompt, img, **kw):
        page = kw.get("page_no")
        transcribed.append(page)
        if page in stall_on:
            raise ModelStall("no complete response within 1800s "
                             "(pha's wall-clock deadline; the connection was still open)")
        return f"TEXT page {page}"

    monkeypatch.setattr(ingest, "transcribe_page", fake_transcribe)
    monkeypatch.setattr(ingest, "edit_document", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "index_document", lambda *a, **k: 0)
    return transcribed


def _src(cfg) -> "object":
    src = cfg.dropbox / "documents" / "doc.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF-1.4 fake")
    return src


def test_scan_abandons_a_stalled_page_and_leaves_it_to_retry(tmp_path, monkeypatch):
    """One stalled page: the pass continues, names it, records NO error, leaves
    the document 'processing' so the next scan resumes exactly that page."""
    cfg = _cfg(tmp_path)
    src = _src(cfg)
    transcribed = _stub_scan(monkeypatch, pages=3, stall_on={2})
    conn = _db.connect(cfg.db_path)

    res = ingest.ingest_file(cfg, conn, object(), src, _pal(), verbose=False)

    assert res["action"] == "stalled"
    assert res["stalled"] == [2]
    assert transcribed == [1, 2, 3], "the pass continues past the stalled page"
    doc = _db.get_document_by_path(conn, str(src))
    assert doc["status"] == "processing", "an incomplete document is never 'done'"
    pages = {p["page_no"]: p for p in _db.get_pages(conn, doc["id"])}
    assert pages[2]["error"] is None and not (pages[2]["raw_text"] or "").strip()
    assert pages[2]["status"] != "done"
    assert "TEXT page 1" in pages[1]["raw_text"] and "TEXT page 3" in pages[3]["raw_text"]
    conn.close()

    # the later pass retries the abandoned page and only it is re-transcribed
    transcribed.clear()
    conn = _db.connect(cfg.db_path)
    monkeypatch.setattr(ingest, "transcribe_page", lambda *a, **kw: "TEXT page 2 retried")
    res2 = ingest.ingest_file(cfg, conn, object(), src, _pal(), verbose=False)
    assert res2["action"] == "ingested"
    doc2 = _db.get_document_by_path(conn, str(src))
    assert doc2["status"] == "done"
    assert {p["page_no"] for p in _db.get_pages(conn, doc2["id"])} == {1, 2, 3}
    conn.close()


def test_scan_stalls_never_trip_the_consecutive_failure_abort(tmp_path, monkeypatch):
    """A page that stalls is the provider's fault, not a broken config: six in a
    row must NOT abort the document as a run-level error."""
    cfg = _cfg(tmp_path)
    src = _src(cfg)
    _stub_scan(monkeypatch, pages=6, stall_on={1, 2, 3, 4, 5, 6})
    conn = _db.connect(cfg.db_path)

    res = ingest.ingest_file(cfg, conn, object(), src, _pal(), verbose=False)

    assert res["action"] == "stalled"
    assert res["stalled"] == [1, 2, 3, 4, 5, 6]
    doc = _db.get_document_by_path(conn, str(src))
    assert doc["status"] == "processing", "a stall must not become an error row"
    assert not (doc["error"] or "").strip()
    conn.close()


def test_scan_genuine_error_still_fails_the_page(tmp_path, monkeypatch):
    """A non-stall ModelError keeps the recorded failure (the stall carve-out is
    narrow)."""
    cfg = _cfg(tmp_path)
    src = _src(cfg)
    _stub_scan(monkeypatch, pages=2, stall_on=set())

    def boom(*a, **kw):
        raise ModelError("connection refused")

    monkeypatch.setattr(ingest, "transcribe_page", boom)
    conn = _db.connect(cfg.db_path)
    res = ingest.ingest_file(cfg, conn, object(), src, _pal(), verbose=False)
    assert res["action"] == "error"
    doc = _db.get_document_by_path(conn, str(src))
    assert doc["status"] == "error"
    assert "connection refused" in (doc["error"] or "")
    conn.close()


def test_edit_abandons_a_stalled_page_and_keeps_the_others(tmp_path, monkeypatch):
    """The same batch rule in `pha edit`: the stalled page has no edit row and
    no error, the other pages are edited, and the result names the page."""
    import threading as _threading

    src = None
    cfg = _cfg(tmp_path)
    src = _src(cfg)
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="doc.pdf", path=str(src), sha256="a",
                              size_bytes=10, mtime=1, kind="pdf",
                              dir_path="documents", now=time.time(), editor="default")
    for n in (1, 2, 3):
        pid = _db.add_page(conn, doc_id, n)
        _db.set_page_result(conn, pid, raw_text=f"raw page {n} " + "text " * 40)
    _db.update_document(conn, doc_id, page_count=3, status="done")
    conn.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

        def chat_text(self, model, prompt, temperature, max_tokens, thinking):
            if "Page: 2 of 3" in prompt:
                raise ModelStall("no complete response within 1800s")
            return "edited"

    monkeypatch.setattr(ingest, "ModelClient", FakeClient)
    res = ingest.edit_document(cfg, conn, doc_id, editor_id="default", verbose=False)
    assert res["action"] == "edited"
    assert res["stalled"] == [2]
    assert res["pages"] == 2
    edits = {r["page_id"]: r for r in conn.execute(
        "SELECT * FROM page_edits WHERE editor='default'").fetchall()}
    pages = _db.get_pages(conn, doc_id)
    assert pages[1]["id"] not in edits, "the stalled page must have no edit row at all"
    assert edits[pages[0]["id"]]["text"] == "edited"
    assert edits[pages[2]["id"]]["text"] == "edited"
    conn.close()


def test_model_stall_is_a_model_error():
    """Batch guards that only catch ModelError must still catch a stall."""
    assert issubclass(ModelStall, ModelError)
