"""`pha encode --page N`: the record-stage twin of `pha scan --page N`.

Page mode exists for the case bulk entry detection misses (a document number
that survives only in the running head): it runs ONE encoder over ONE page, with
no detection, and PINS the result so a later bulk encode keeps it. A page that
yields no record pins nothing (the pin certifies an extraction that happened).
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli, db, ingest, locks
from personal_historical_archive.config import Config


def _cfg(tmp_path) -> Config:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        f"paths:\n  archive_dir: {tmp_path / 'arc'}\n", encoding="utf-8")
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


def _doc(cfg: Config, texts=("page one", "DOC, 22 - GOA 1566 115", "page three"),
         rel="collections/COLX/a.pdf", editor: str | None = None) -> int:
    src = cfg.dropbox / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF fake")
    conn = db.connect(cfg.db_path)
    try:
        doc_id = db.add_document(
            conn, filename=src.name, path=str(src), sha256=rel, size_bytes=1,
            mtime=1, kind="pdf", now=time.time(),
            dir_path=str(src.parent.relative_to(cfg.dropbox)),
        )
        for n, text in enumerate(texts, start=1):
            pid = db.add_page(conn, doc_id, n)
            db.set_page_result(conn, pid, raw_text=text)
        if editor:
            db.update_document(conn, doc_id, editor=editor)
        conn.commit()
        return doc_id
    finally:
        conn.close()


def _encoder(cfg: Config, name="documents", pages="1-3",
             rel_dir="collections/COLX") -> None:
    d = cfg.dropbox / rel_dir / "encoders"
    d.mkdir(parents=True, exist_ok=True)
    body = "Extract every entry. Return a JSON array."
    (d / f"{name}.md").write_text(
        f'---\npages: "{pages}"\n---\n{body}\n', encoding="utf-8")


class _FakeClient:
    def close(self) -> None:
        pass


@pytest.fixture
def model(monkeypatch):
    """Replace the model call: `state['records']` is what the encoder returns."""
    state = {"records": [], "calls": 0}

    def _window(*a, **k):
        state["calls"] += 1
        return list(state["records"]), []

    monkeypatch.setattr(
        ingest, "make_encoder_client",
        lambda cfg, encoder_id=None, encoder=None: (_FakeClient(), encoder))
    monkeypatch.setattr(ingest, "_extract_encoder_window", _window)
    return state


def _record(number="22", page=2, title="Carta de Goa"):
    return {"kind": "document", "number": number, "series": "main",
            "title": title, "doc_kind": "letter", "page_start": page}


def _records(cfg: Config) -> list[dict]:
    conn = db.connect(cfg.db_path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, encoder, source, pinned_at, encoder_model, data "
            "FROM records ORDER BY id")]
    finally:
        conn.close()


def _encode(cfg, doc_id, pages=(2,), **kw):
    return ingest.encode_pages(cfg, doc_id=doc_id, pages=set(pages), **kw)


# --------------------------------------------------------------- storing/pins

def test_page_encode_stores_and_pins_one_record(tmp_path, model):
    cfg = _cfg(tmp_path)
    doc_id = _doc(cfg)
    _encoder(cfg)
    model["records"] = [_record()]

    res = _encode(cfg, doc_id, encoder_id="documents")["results"][0]

    assert res["action"] == "encoded" and res["records"] == 1
    assert res["pinned"] is True
    rows = _records(cfg)
    assert len(rows) == 1
    row = rows[0]
    assert row["encoder"] == "documents"
    assert row["pinned_at"] is not None
    assert row["encoder_model"]          # provenance parity with pages/edits
    data = json.loads(row["data"])
    assert data["number"] == "22" and data["page_start"] == 2


def test_page_with_no_record_stores_and_pins_nothing(tmp_path, model):
    cfg = _cfg(tmp_path)
    doc_id = _doc(cfg)
    _encoder(cfg)
    model["records"] = []

    res = _encode(cfg, doc_id, encoder_id="documents")["results"][0]

    assert res["action"] == "encoded"
    assert res["no_record"] is True and res["records"] == 0
    assert res["pinned"] is False
    assert _records(cfg) == []


def test_no_record_does_not_erase_an_existing_pinned_record(tmp_path, model):
    cfg = _cfg(tmp_path)
    doc_id = _doc(cfg)
    _encoder(cfg)
    model["records"] = [_record()]
    _encode(cfg, doc_id, encoder_id="documents")

    model["records"] = []          # a later model miss on the same page
    res = _encode(cfg, doc_id, encoder_id="documents")["results"][0]

    assert res["no_record"] is True
    rows = _records(cfg)
    assert len(rows) == 1 and rows[0]["pinned_at"] is not None


def test_no_pin_stores_without_protection(tmp_path, model):
    cfg = _cfg(tmp_path)
    doc_id = _doc(cfg)
    _encoder(cfg)
    model["records"] = [_record()]

    _encode(cfg, doc_id, encoder_id="documents", pin=False)

    rows = _records(cfg)
    assert len(rows) == 1 and rows[0]["pinned_at"] is None


def test_re_encoding_a_page_replaces_its_record(tmp_path, model):
    cfg = _cfg(tmp_path)
    doc_id = _doc(cfg)
    _encoder(cfg)
    model["records"] = [_record()]
    _encode(cfg, doc_id, encoder_id="documents")

    model["records"] = [_record(title="Carta de Goa (2nd reading)")]
    _encode(cfg, doc_id, encoder_id="documents")

    rows = _records(cfg)
    assert len(rows) == 1, "one row, not a second copy"
    assert json.loads(rows[0]["data"])["title"] == "Carta de Goa (2nd reading)"
    assert rows[0]["pinned_at"] is not None


def test_unpin_keeps_the_record_and_its_search_row(tmp_path, model):
    cfg = _cfg(tmp_path)
    doc_id = _doc(cfg)
    _encoder(cfg)
    model["records"] = [_record()]
    _encode(cfg, doc_id, encoder_id="documents")

    res = ingest.encode_pages(cfg, doc_id=doc_id, pages={2}, unpin=True)["results"][0]

    assert res["action"] == "unpinned" and res["unpinned"] == 1
    conn = db.connect(cfg.db_path)
    try:
        row = conn.execute("SELECT id, pinned_at FROM records").fetchone()
        assert row["pinned_at"] is None
        fts = conn.execute("SELECT COUNT(*) n FROM records_fts WHERE rowid = ?",
                           (row["id"],)).fetchone()["n"]
        assert fts == 1, "the record stays findable (only the protection is lifted)"
    finally:
        conn.close()


def test_bulk_reprocess_keeps_a_pinned_record_and_does_not_duplicate_it(tmp_path, model):
    cfg = _cfg(tmp_path)
    doc_id = _doc(cfg)
    _encoder(cfg)
    model["records"] = [_record()]
    _encode(cfg, doc_id, encoder_id="documents")

    # A bulk pass re-derives the same entry (and one more): the pinned row wins
    # and the duplicate is dropped, the other record is stored.
    model["records"] = [_record(), _record(number="99", page=1, title="Outra")]
    conn = db.connect(cfg.db_path)
    try:
        res = ingest.encode_document(cfg, conn, doc_id,
                                     enc_file=cfg.dropbox / "collections/COLX/encoders/documents.md",
                                     reprocess=True)
        conn.commit()
    finally:
        conn.close()

    assert res["pinned_kept"] == 1
    assert res["pinned_duplicates_dropped"] == 1
    rows = _records(cfg)
    numbers = sorted(json.loads(r["data"])["number"] for r in rows)
    assert numbers == ["22", "99"]
    pinned = [json.loads(r["data"])["number"] for r in rows if r["pinned_at"] is not None]
    assert pinned == ["22"]


# --------------------------------------------------------------- guards

def test_a_record_already_stored_on_another_page_is_refused_then_replaced(tmp_path, model):
    cfg = _cfg(tmp_path)
    doc_id = _doc(cfg)
    _encoder(cfg)
    # Bulk record 22 on page 1 (the mis-detected case this feature repairs).
    conn = db.connect(cfg.db_path)
    try:
        db.add_record(conn, doc_id, "documents", "document",
                      json.dumps(_record(page=1)), source="1")
        conn.commit()
    finally:
        conn.close()
    model["records"] = [_record(page=2)]

    res = _encode(cfg, doc_id, encoder_id="documents")["results"][0]
    assert res["action"] == "refused" and res["refused"] == 1
    assert res["clashes"][0]["page"] == 1
    assert len(_records(cfg)) == 1, "nothing stored while it clashes"

    res = _encode(cfg, doc_id, encoder_id="documents", replace=True)["results"][0]
    assert res["action"] == "encoded" and res["records"] == 1
    rows = _records(cfg)
    assert len(rows) == 1
    assert rows[0]["source"] == "2" and rows[0]["pinned_at"] is not None


def test_the_stored_record_is_grounded_to_the_named_page(tmp_path, model):
    """The model may echo another page (a running head shows the printed page);
    page mode knows where the entry starts, so the record is stored there."""
    cfg = _cfg(tmp_path)
    doc_id = _doc(cfg)
    _encoder(cfg)
    model["records"] = [_record(page=1)]      # model echoes page 1

    _encode(cfg, doc_id, encoder_id="documents")

    rows = _records(cfg)
    assert len(rows) == 1 and rows[0]["source"] == "2"
    assert json.loads(rows[0]["data"])["page_start"] == 2


def test_page_outside_the_declared_range_runs_with_a_warning(tmp_path, model):
    cfg = _cfg(tmp_path)
    doc_id = _doc(cfg)
    _encoder(cfg, pages="3")       # only page 3
    model["records"] = [_record(page=2)]

    res = _encode(cfg, doc_id, encoder_id="documents")["results"][0]

    assert res["action"] == "encoded" and res["outside_range"] is True
    assert len(_records(cfg)) == 1


def test_no_encoder_covers_the_page_without_an_override(tmp_path, model):
    cfg = _cfg(tmp_path)
    doc_id = _doc(cfg)
    _encoder(cfg, pages="3")

    res = _encode(cfg, doc_id)["results"][0]

    assert res["action"] == "error"
    assert "no encoder covers page 2" in res["reason"]


def test_several_encoders_covering_the_page_is_refused(tmp_path, model):
    cfg = _cfg(tmp_path)
    doc_id = _doc(cfg)
    _encoder(cfg, name="documents", pages="1-3")
    _encoder(cfg, name="letters", pages="2-2")

    res = _encode(cfg, doc_id)["results"][0]

    assert res["action"] == "error"
    assert "several encoders cover page 2" in res["reason"]


def test_a_page_without_text_is_refused_with_the_command_that_fills_it(tmp_path, model):
    cfg = _cfg(tmp_path)
    doc_id = _doc(cfg, texts=("page one", None, "page three"))
    _encoder(cfg)

    res = _encode(cfg, doc_id, encoder_id="documents")["results"][0]

    assert res["action"] == "refused"
    assert "pha scan --path" in res["reason"] and "--page 2" in res["reason"]
    assert model["calls"] == 0


def test_the_edited_text_is_what_reaches_the_model(tmp_path, model):
    cfg = _cfg(tmp_path)
    doc_id = _doc(cfg, editor="modernise")
    conn = db.connect(cfg.db_path)
    try:
        page = conn.execute("SELECT id FROM pages WHERE document_id=? AND page_no=2",
                            (doc_id,)).fetchone()
        db.set_page_edit(conn, page["id"], "modernise", text="EDITED page two")
        conn.commit()
    finally:
        conn.close()
    _encoder(cfg)
    seen = {}

    def _window(client, encoder, base_prompt, doc, chunk, block, start_page, verbose, mode="page"):
        seen["block"] = block
        return [_record()], []

    import personal_historical_archive.ingest as ingest_mod
    orig = ingest_mod._extract_encoder_window
    ingest_mod._extract_encoder_window = _window
    try:
        _encode(cfg, doc_id, encoder_id="documents")
    finally:
        ingest_mod._extract_encoder_window = orig

    assert "EDITED page two" in seen["block"]
    assert "DOC, 22" not in seen["block"]


# --------------------------------------------------------------- plan/lock/surface

def test_dry_run_makes_no_call_and_no_write(tmp_path, model):
    cfg = _cfg(tmp_path)
    doc_id = _doc(cfg)
    _encoder(cfg)

    res = _encode(cfg, doc_id, encoder_id="documents", dry_run=True)["results"][0]

    assert res["action"] == "planned"
    assert res["encoder"] == "documents" and res["model"]
    assert res["pages"] == "1-3" and res["pin"] is True
    assert model["calls"] == 0
    assert _records(cfg) == []


def test_a_running_model_job_makes_page_encode_refuse(tmp_path, model, monkeypatch):
    cfg = _cfg(tmp_path)
    doc_id = _doc(cfg)
    _encoder(cfg)
    from personal_historical_archive.extract import encoder_file_named
    enc_file = encoder_file_named("documents", cfg.dropbox / "collections/COLX", cfg.dropbox)
    encoder = cfg.resolve_model(cfg.encoder_from_file(enc_file))
    # Another LIVE process holds the encoder's model server: locks ignore a
    # holder with our own pid, so plant a foreign one like tests/test_locks.py.
    key = locks.stage_key(encoder)
    path = locks._slot_path(key, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("999999 pha scan", encoding="utf-8")
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
    try:
        res = _encode(cfg, doc_id, encoder_id="documents")["results"][0]
    finally:
        path.unlink(missing_ok=True)

    assert res["action"] == "skipped"
    assert "pha scan" in res["reason"]
    assert model["calls"] == 0


def test_page_mode_needs_exactly_one_document(tmp_path, model):
    cfg = _cfg(tmp_path)
    _doc(cfg, rel="collections/COLX/a.pdf")
    _doc(cfg, rel="collections/COLX/b.pdf")
    _encoder(cfg)

    res = ingest.encode_pages(cfg, path="collections/COLX", pages={2})["results"][0]

    assert res["action"] == "error"
    assert "exactly one ingested document" in res["reason"]


def test_cli_prints_the_no_record_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "encode_pages", lambda *a, **k: {"results": [{
        "action": "encoded", "filename": "a.pdf", "page": 195,
        "encoder": "documents", "model": "m", "records": 0,
        "no_record": True, "pinned": False,
    }]})
    args = SimpleNamespace(path=None, doc="78", page=["195"], encoder="documents",
                           model=None, no_pin=False, unpin=False, replace=False,
                           reprocess=False, dry_run=False, include_leased=False,
                           wait=None, no_wait=False)

    cli.cmd_encode(None, args)

    out = capsys.readouterr().out
    assert "no record detected on a.pdf page 195 — nothing pinned" in out
