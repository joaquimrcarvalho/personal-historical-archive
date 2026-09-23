"""G1 — the machine holding a RECEIVED hand-over must not embed it.

Measured on the *Monumenta Brasiliae* hand-over: the worker embedded 2,676 pages
that the owner's `handoff fetch` embedded again minutes later. The return payload
carries rows only (no chunks, no vectors — `handoff.py:build_result`), so the
worker's index can never reach the owner: it is pure duplicated work.

Rule: `import_handoff` records the document set as RECEIVED here (`state: "in"`),
and `scan`/`edit` skip `index_document` for those documents. That marker is NOT
an out-lease — the pipeline must still process them — so `active_leases()` and
`leased_shas()` deliberately ignore it. `--index` (`force_index=True`) overrides,
and `pha handoff cancel <id>` releases the marker.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from personal_historical_archive import db as _db, handoff, ingest
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import sha256_of


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


def _document(cfg: Config, *, pages: int = 3, done: int = 1):
    col = cfg.dropbox / "collections" / "DI"
    col.mkdir(parents=True, exist_ok=True)
    src = col / "vol04.pdf"
    src.write_bytes(b"%PDF-1.4 vol04")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(
        conn, filename="vol04.pdf", path=str(src), sha256=sha256_of(src),
        size_bytes=src.stat().st_size, mtime=src.stat().st_mtime, kind="pdf",
        now=time.time(), dir_path="collections/DI", palaeographer="default",
    )
    for pno in range(1, pages + 1):
        pid = _db.add_page(conn, doc_id, pno)
        if pno <= done:
            _db.set_page_result(conn, pid, raw_text=f"PAGE {pno} machine text")
    _db.update_document(conn, doc_id, page_count=pages)
    _db.set_document_status(conn, doc_id, "done" if done >= pages else "processing")
    conn.commit()
    conn.close()
    return doc_id, src


def _received(tmp_path: Path):
    """The real two-machine path: owner hands out, worker imports."""
    owner = _cfg(tmp_path, "owner")
    _document(owner, pages=2, done=1)
    out = tmp_path / "ho"
    handoff.export_handoff(owner, ["collections/DI"], out, verbose=False)

    worker = _cfg(tmp_path, "worker")
    handoff.import_handoff(worker, out, verbose=False)
    return owner, worker, out


def _worker_doc(cfg: Config):
    conn = _db.connect(cfg.db_path)
    try:
        return conn.execute("SELECT * FROM documents").fetchone()
    finally:
        conn.close()


# --------------------------------------------------------------------------- marker

def test_import_records_the_document_as_received(tmp_path):
    owner, worker, out = _received(tmp_path)
    hid = handoff.read_manifest(out)["handoff_id"]

    marker = handoff.read_lease(worker, hid)
    assert marker is not None and marker.state == handoff.STATE_IN
    doc = _worker_doc(worker)
    assert marker.shas() == {doc["sha256"]}

    # it is NOT a lease: the worker may still scan and edit the document
    assert handoff.leased_shas(worker) == {}
    assert handoff.active_leases(worker) == []
    assert handoff.incoming_shas(worker) == {doc["sha256"]: marker}


def test_import_into_the_owning_archive_keeps_its_out_lease(tmp_path):
    """Importing a payload into the archive that created it must not overwrite
    the out-lease with a received-marker (same handoff id, same file)."""
    owner = _cfg(tmp_path, "owner")
    _document(owner, pages=2, done=1)
    out = tmp_path / "ho"
    handoff.export_handoff(owner, ["collections/DI"], out, verbose=False)
    hid = handoff.read_manifest(out)["handoff_id"]

    handoff.import_handoff(owner, out, verbose=False)

    lease = handoff.read_lease(owner, hid)
    assert lease is not None and lease.state == handoff.STATE_OUT
    assert handoff.incoming_shas(owner) == {}


# --------------------------------------------------------------------------- indexing

def _stub_pass(monkeypatch, calls: dict):
    monkeypatch.setattr(ingest, "page_count", lambda p: 2)

    def fake_render(path, out_dir, dpi, max_px, q, prefix=None, pages=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        wanted = sorted(pages) if pages is not None else [1, 2]
        out = []
        for n in wanted:
            f = out_dir / f"p{n:03d}.jpg"
            f.write_bytes(b"jpeg")
            out.append(f)
        return out

    def fake_transcribe(client, pal, prompt_txt, img, **kw):
        return f"WORKER PAGE {kw.get('page_no')}"

    def fake_index(cfg, conn, doc_id, **kw):
        calls["index"].append(doc_id)
        return 3

    monkeypatch.setattr(ingest, "render_document", fake_render)
    monkeypatch.setattr(ingest, "transcribe_page", fake_transcribe)
    monkeypatch.setattr(ingest, "index_document", fake_index)


def _scan_worker(worker: Config, **kw) -> dict:
    """The real entry point: `scan_once` is what computes the defer set."""
    pal = worker.get_palaeographer("default")
    return ingest.scan_once(worker, object(), pal, path="collections/DI",
                            verbose=False, **kw)


def test_a_scan_on_the_worker_builds_no_chunks(tmp_path, monkeypatch, capsys):
    _owner, worker, _out = _received(tmp_path)
    calls = {"index": []}
    _stub_pass(monkeypatch, calls)

    res = _scan_worker(worker)
    doc = res["results"][0]

    assert doc["action"] == "ingested"
    assert "hand-over" in (doc["index_deferred"] or "")
    assert calls["index"] == [], "the worker must not embed hand-over material"
    # ...but the work itself was done, and the document is complete
    conn = _db.connect(worker.db_path)
    try:
        row = _db.get_document(conn, _worker_doc(worker)["id"])
        texts = [r["raw_text"] for r in _db.get_pages(conn, row["id"])]
    finally:
        conn.close()
    assert row["status"] == "done"
    assert all(t and "WORKER PAGE" in t for t in texts)
    capsys.readouterr()


def test_index_flag_forces_the_embed(tmp_path, monkeypatch):
    _owner, worker, _out = _received(tmp_path)
    calls = {"index": []}
    _stub_pass(monkeypatch, calls)

    res = _scan_worker(worker, force_index=True)

    assert res["results"][0]["index_deferred"] is None
    assert calls["index"], "`--index` must build the chunks here"


def test_releasing_the_marker_makes_the_document_indexable(tmp_path, monkeypatch):
    _owner, worker, out = _received(tmp_path)
    calls = {"index": []}
    _stub_pass(monkeypatch, calls)
    hid = handoff.read_manifest(out)["handoff_id"]

    handoff.release(worker, hid, handoff.STATE_CANCELLED)   # `pha handoff cancel`

    assert handoff.incoming_shas(worker) == {}
    res = _scan_worker(worker)
    assert res["results"][0]["index_deferred"] is None
    assert calls["index"]


def test_an_edit_on_the_worker_builds_no_chunks(tmp_path, monkeypatch, capsys):
    _owner, worker, _out = _received(tmp_path)
    calls = {"index": []}
    _stub_pass(monkeypatch, calls)
    conn = _db.connect(worker.db_path)
    try:
        # the worker finishes the page, then runs the editor pass over the doc
        doc = conn.execute("SELECT * FROM documents").fetchone()
        res = ingest.edit_documents_under(worker, doc["path"], verbose=False)
    finally:
        conn.close()
    assert calls["index"] == [], "an edit on the worker must not embed either"
    assert res["indexed"] == 0
    capsys.readouterr()


def test_the_return_payload_reports_the_omission(tmp_path):
    _owner, worker, out = _received(tmp_path)
    back = tmp_path / "back"

    res = handoff.build_result(worker, out, back, verbose=False)

    assert res["counts"]["index_omitted"] == 1
    assert res["counts"]["documents"] == 1
    import json
    payload = json.loads((back / handoff.RESULT_NAME).read_text(encoding="utf-8"))
    assert payload["documents"][0]["chunks"] == 0        # explicit, not absent


def test_status_marks_the_document_index_deferred(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from personal_historical_archive import cli

    _owner, worker, _out = _received(tmp_path)
    calls = {"index": []}
    _stub_pass(monkeypatch, calls)      # a completed worker scan: done, 0 chunks
    _scan_worker(worker)
    cli.cmd_status(worker, SimpleNamespace())
    out = capsys.readouterr().out
    assert "index deferred — received for hand-over" in out
    assert "NOT INDEXED" not in out
