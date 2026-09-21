from __future__ import annotations

"""The single-page rescan: `pha scan --path <doc> --page N` re-reads one page with
a chosen palaeographer/model, records its provenance, pins it, and re-runs the
editor + indexer for that page only.

See enhancements/pha-single-page-rescan-enhancement-request.md.
"""

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli, db as _db, ingest
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import sha256_of

PAGES = 3


def _cfg(tmp_path) -> Config:
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  models: models\n  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


def _seed(cfg: Config, *, pages: int = PAGES, extra_doc: bool = False):
    """One ingested document with `pages` done pages (plus an optional second)."""
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    src = col / "d.pdf"
    src.write_bytes(b"%PDF-1.4 d")
    conn = _db.connect(cfg.db_path)
    doc = _db.add_document(conn, filename="d.pdf", path=str(src), sha256=sha256_of(src),
                           size_bytes=src.stat().st_size, mtime=src.stat().st_mtime,
                           kind="pdf", dir_path="collections/COLX", now=time.time(),
                           palaeographer="default", palaeographer_model="default")
    for n in range(1, pages + 1):
        pid = _db.add_page(conn, doc, n)
        _db.set_page_result(conn, pid, raw_text=f"machine reading {n}")
    other = None
    if extra_doc:
        src2 = col / "e.pdf"
        src2.write_bytes(b"%PDF-1.4 e")
        other = _db.add_document(conn, filename="e.pdf", path=str(src2), sha256=sha256_of(src2),
                                 size_bytes=1, mtime=1, kind="pdf",
                                 dir_path="collections/COLX", now=time.time(),
                                 palaeographer="default", palaeographer_model="default")
        _db.set_page_result(conn, _db.add_page(conn, other, 1), raw_text="e1")
    _db.set_document_status(conn, doc, "done")
    if other:
        _db.set_document_status(conn, other, "done")
    conn.commit()
    conn.close()
    return src, doc


def _stub(monkeypatch, *, text="better reading", total: int = PAGES):
    """Offline render/transcribe/edit/index; records what was asked for."""
    calls: dict = {"render": [], "transcribe": [], "edit": [], "index": []}
    monkeypatch.setattr(ingest, "page_count", lambda p: total)

    def fake_render(path, out_dir, dpi, max_px, q, prefix=None, pages=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        wanted = sorted(pages) if pages is not None else list(range(1, total + 1))
        out = []
        for n in wanted:
            f = out_dir / f"p{n:03d}.jpg"
            f.write_bytes(b"jpeg")
            out.append(f)
        calls["render"].append(wanted)
        return out

    def fake_transcribe(client, pal, prompt_txt, img, **kw):
        calls["transcribe"].append((pal.id, pal.model_ref or pal.model, kw.get("page_no")))
        return f"{text} p{kw.get('page_no')}"

    def fake_edit(cfg_, conn_, doc_id_, **kw):
        calls["edit"].append(kw.get("page_no"))
        return {"action": "edited", "filename": "d.pdf", "editor": "null",
                "pages": 1 if kw.get("page_no") else 0}

    def fake_index(cfg_, conn_, doc_id_, **kw):
        calls["index"].append(kw.get("pages"))
        return 5

    monkeypatch.setattr(ingest, "render_document", fake_render)
    monkeypatch.setattr(ingest, "transcribe_page", fake_transcribe)
    monkeypatch.setattr(ingest, "edit_document", fake_edit)
    monkeypatch.setattr(ingest, "index_document", fake_index)
    return calls


def _run_pages(cfg: Config, src: Path, pages, *, pin: bool = True):
    conn = _db.connect(cfg.db_path)
    try:
        pal = cfg.get_palaeographer("default")
        pal = cfg.resolve_model(pal, "default")
        return ingest.ingest_file(cfg, conn, object(), src, pal, verbose=False,
                                  pages=set(pages), pin=pin)
    finally:
        conn.close()


def _page(cfg: Config, doc: int, n: int):
    conn = _db.connect(cfg.db_path)
    try:
        return conn.execute("SELECT * FROM pages WHERE document_id=? AND page_no=?",
                            (doc, n)).fetchone()
    finally:
        conn.close()


# --------------------------------------------------------------------------- provenance + pin

def test_provenance_columns_default_to_the_document_pair(tmp_path):
    """A bulk-read page carries no per-page provenance (NULL = the document's
    pair), so nothing is backfilled and every existing archive behaves as before."""
    cfg = _cfg(tmp_path)
    _src, doc = _seed(cfg)
    page = _page(cfg, doc, 1)
    assert page["palaeographer"] is None
    assert page["palaeographer_model"] is None
    assert page["pinned_at"] is None


def test_page_reread_touches_only_that_page(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    src, doc = _seed(cfg)
    calls = _stub(monkeypatch)

    res = _run_pages(cfg, src, {2})

    assert res["action"] == "rescanned"
    assert res["pages"] == [2]
    # only page 2 was rendered and transcribed
    assert calls["render"] == [[2]]
    assert [t[2] for t in calls["transcribe"]] == [2]
    # only page 2 changed; the others keep their text and stay unpinned
    assert _page(cfg, doc, 2)["raw_text"] == "better reading p2"
    assert _page(cfg, doc, 1)["raw_text"] == "machine reading 1"
    assert _page(cfg, doc, 3)["raw_text"] == "machine reading 3"
    assert _page(cfg, doc, 1)["pinned_at"] is None
    assert _page(cfg, doc, 3)["palaeographer"] is None
    # page 2 records who read it and is pinned
    p2 = _page(cfg, doc, 2)
    assert (p2["palaeographer"], p2["palaeographer_model"]) == ("default", "default")
    assert p2["pinned_at"] is not None
    # the DOCUMENT's configured pair is untouched (the other pages still use it)
    conn = _db.connect(cfg.db_path)
    try:
        d = _db.get_document(conn, doc)
    finally:
        conn.close()
    assert (d["palaeographer"], d["palaeographer_model"]) == ("default", "default")
    assert d["page_count"] == PAGES          # a page re-read never shrinks it


def test_page_reread_runs_the_editor_and_indexer_for_that_page_only(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    src, doc = _seed(cfg)
    calls = _stub(monkeypatch)

    res = _run_pages(cfg, src, {2})

    assert calls["edit"] == [2]              # the editor for exactly that page
    assert calls["index"] == [{2}]           # incremental index scoped to it
    assert res["edited_pages"] == 1
    assert res["details"][0]["page"] == 2
    assert res["details"][0]["to"] == {"palaeographer": "default", "model": "default"}
    assert res["details"][0]["pinned"] is True


def test_front_matter_carries_page_provenance(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    src, doc = _seed(cfg)
    _stub(monkeypatch)

    _run_pages(cfg, src, {2})

    lib = sorted((cfg.library / "collections" / "COLX").glob("d_*"))[-1]
    tdir = next(p for p in lib.iterdir() if p.name.startswith("transcription-"))
    p2 = (tdir / "page-002.md").read_text(encoding="utf-8")
    p1 = (tdir / "page-001.md").read_text(encoding="utf-8")
    assert "pinned: true" in p2
    # the page's own pair is written on the page that was re-read, and the
    # untouched page keeps only the document-level pair
    assert p2.count("palaeographer:") >= 1
    assert "pinned" not in p1


def test_no_pin_records_provenance_without_protecting(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    src, doc = _seed(cfg)
    _stub(monkeypatch)

    _run_pages(cfg, src, {2}, pin=False)

    p2 = _page(cfg, doc, 2)
    assert p2["palaeographer"] == "default"   # provenance recorded
    assert p2["pinned_at"] is None            # but not protected


# --------------------------------------------------------------------------- the pin gates BULK only

def test_bulk_scan_keeps_a_pinned_page_and_reports_it(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    src, doc = _seed(cfg)
    _stub(monkeypatch, text="chosen-model reading")
    _run_pages(cfg, src, {2})

    # now a BULK pass (--reprocess) with the document's configured model
    monkeypatch.setattr(ingest, "transcribe_page",
                        lambda client, pal, prompt_txt, img, **kw: "bulk reading")
    conn = _db.connect(cfg.db_path)
    try:
        pal = cfg.resolve_model(cfg.get_palaeographer("default"), "default")
        res = ingest.ingest_file(cfg, conn, object(), src, pal, reprocess=True, verbose=False)
    finally:
        conn.close()

    assert res["action"] == "ingested"
    assert res["kept_pinned"] == [2]
    assert _page(cfg, doc, 2)["raw_text"] == "chosen-model reading p2"  # kept
    assert _page(cfg, doc, 1)["raw_text"] == "bulk reading"             # re-read
    assert _page(cfg, doc, 3)["raw_text"] == "bulk reading"


def test_explicit_page_replaces_the_pin(tmp_path, monkeypatch):
    """Naming the page IS the intent: a second `--page N` re-reads it, pin and
    all (the design's 'already-pinned page' edge case)."""
    cfg = _cfg(tmp_path)
    src, doc = _seed(cfg)
    _stub(monkeypatch, text="first")
    _run_pages(cfg, src, {2})
    assert _page(cfg, doc, 2)["raw_text"] == "first p2"

    monkeypatch.setattr(ingest, "transcribe_page",
                        lambda client, pal, prompt_txt, img, **kw: "second")
    _run_pages(cfg, src, {2})
    p2 = _page(cfg, doc, 2)
    assert p2["raw_text"] == "second"
    assert p2["pinned_at"] is not None        # still pinned, now as the new reading


def test_unpin_keeps_the_text_and_releases_the_protection(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    src, doc = _seed(cfg)
    _stub(monkeypatch, text="chosen")
    _run_pages(cfg, src, {2})

    conn = _db.connect(cfg.db_path)
    try:
        res = ingest.unpin_pages(cfg, conn, src, {2})
    finally:
        conn.close()

    assert res == {"action": "unpinned", "filename": "d.pdf", "unpinned": 1}
    p2 = _page(cfg, doc, 2)
    assert p2["raw_text"] == "chosen p2"      # text kept
    assert p2["palaeographer"] == "default"   # provenance kept
    assert p2["pinned_at"] is None            # protection released


# --------------------------------------------------------------------------- human text wins

def test_human_reviewed_transcription_is_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    src, doc = _seed(cfg)
    conn = _db.connect(cfg.db_path)
    try:
        pid = conn.execute("SELECT id FROM pages WHERE document_id=? AND page_no=2",
                           (doc,)).fetchone()["id"]
        _db.mark_page_reviewed(conn, pid, "HUMAN TEXT")
        conn.commit()
    finally:
        conn.close()
    calls = _stub(monkeypatch)

    res = _run_pages(cfg, src, {2})

    assert res["refused_reviewed"] == [2]
    assert calls["transcribe"] == []
    assert _page(cfg, doc, 2)["raw_text"] == "HUMAN TEXT"


def test_out_of_range_page_errors_without_mutating_the_document(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    src, doc = _seed(cfg)
    _stub(monkeypatch)

    res = _run_pages(cfg, src, {9})

    assert res["action"] == "error"
    assert "out of range (1-3)" in res["error"]
    conn = _db.connect(cfg.db_path)
    try:
        assert _db.get_document(conn, doc)["status"] == "done"   # not flipped to error
    finally:
        conn.close()


def test_page_of_an_unscanned_document_errors(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    src = col / "new.pdf"
    src.write_bytes(b"%PDF-1.4 new")
    _stub(monkeypatch)

    res = _run_pages(cfg, src, {1})

    assert res["action"] == "error"
    assert "not scanned yet" in res["error"]


# --------------------------------------------------------------------------- scan_once surface

def test_scan_once_page_requires_exactly_one_document(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _seed(cfg, extra_doc=True)          # two documents match the dropbox
    _stub(monkeypatch)
    conn = _db.connect(cfg.db_path)
    conn.close()

    pal = cfg.resolve_model(cfg.get_palaeographer("default"), "default")
    res = ingest.scan_once(cfg, object(), pal, path="collections/COLX",
                           pages={1}, verbose=False)

    assert res["results"][0]["action"] == "error"
    assert "exactly one document" in res["results"][0]["error"]


def test_scan_once_dry_run_plans_without_a_model_call(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    src, doc = _seed(cfg)
    _stub(monkeypatch, text="chosen")
    _run_pages(cfg, src, {2})           # page 2 now pinned

    def explode(*a, **k):
        raise AssertionError("dry run called the model")
    monkeypatch.setattr(ingest, "transcribe_page", explode)
    monkeypatch.setattr(ingest, "render_document", explode)

    pal = cfg.resolve_model(cfg.get_palaeographer("default"), "default")
    res = ingest.scan_once(cfg, object(), pal, path="collections/COLX", pages={2, 1},
                           dry_run=True, verbose=False)

    planned = res["results"][0]
    assert planned["action"] == "planned"
    by_page = {e["page"]: e for e in planned["plan"]}
    assert by_page[2]["pinned"] is True
    assert by_page[2]["action"] == "re-read"
    assert by_page[1]["action"] == "re-read"
    assert by_page[1]["pinned"] is False


def test_scan_once_dry_run_refuses_to_plan_a_reviewed_page(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    src, doc = _seed(cfg)
    conn = _db.connect(cfg.db_path)
    try:
        pid = conn.execute("SELECT id FROM pages WHERE document_id=? AND page_no=1",
                           (doc,)).fetchone()["id"]
        _db.mark_page_reviewed(conn, pid, "HUMAN")
        conn.commit()
    finally:
        conn.close()

    pal = cfg.resolve_model(cfg.get_palaeographer("default"), "default")
    res = ingest.scan_once(cfg, object(), pal, path="collections/COLX", pages={1},
                           dry_run=True, verbose=False)

    entry = res["results"][0]["plan"][0]
    assert entry["action"] == "refused"
    assert "review --unset" in entry["reason"]


# --------------------------------------------------------------------------- the override is authoritative

def test_override_palaeographer_beats_the_collection_sidecar(tmp_path, monkeypatch):
    """`--palaeographer` is authoritative (README's promise; scan_once used to
    drop it and let pha.yaml win)."""
    cfg = _cfg(tmp_path)
    src, doc = _seed(cfg)
    (cfg.palaeographers_dir / "vlm.md").write_text("VLM rules\n")
    (cfg.dropbox / "collections" / "COLX" / "pha.yaml").write_text(
        "palaeographer:\n  rules: default\n  model: default\n")
    cfg = Config.load(cfg.root)          # reload to pick up vlm.md
    _stub(monkeypatch)

    pal = cfg.resolve_model(cfg.get_palaeographer("vlm"), "default")
    res = ingest.scan_once(cfg, object(), pal, path="collections/COLX", pages={1},
                           pal_override="vlm", verbose=False)

    assert res["results"][0]["action"] == "rescanned"
    assert _page(cfg, doc, 1)["palaeographer"] == "vlm"   # the flag won


def test_override_model_keeps_the_document_rules(tmp_path, monkeypatch):
    """The two halves resolve independently: `--model X` alone keeps the rules
    the document configured (and the lock covers X's server)."""
    cfg = _cfg(tmp_path)
    _src, doc = _seed(cfg)
    (cfg.models_dir / "m3.md").write_text(
        "---\ndescription: M3\nbase_url: http://127.0.0.1:9999/v1\nmodel: m3\n"
        "server: remote-box\n---\n")
    cfg = Config.load(cfg.root)
    _stub(monkeypatch)

    pal, _sidecar = ingest._effective_palaeographer(
        cfg, cfg.dropbox / "collections" / "COLX" / "d.pdf",
        cfg.get_palaeographer("default"), None, "m3")
    assert pal.model_ref == "m3"
    assert pal.model == "m3"
    keys = ingest._job_keys(cfg, [cfg.dropbox / "collections" / "COLX" / "d.pdf"],
                            default_pal=cfg.get_palaeographer("default"),
                            model_override="m3", warn=False)
    assert "remote-box" in keys          # the lock covers the override's server


def test_engine_model_with_overridden_rules_warns(tmp_path, monkeypatch, capsys):
    """`--palaeographer <vlm rules>` without `--model` keeps the document's
    liteparse model, which ignores prompts — say so instead of silently
    producing an OCR reading."""
    cfg = _cfg(tmp_path)
    _src, doc = _seed(cfg)
    (cfg.palaeographers_dir / "vlm.md").write_text("VLM rules\n")
    (cfg.models_dir / "liteparse.md").write_text(
        "---\ndescription: lp\nengine: liteparse\nliteparse_lang: por\n---\n")
    (cfg.dropbox / "collections" / "COLX" / "pha.yaml").write_text(
        "palaeographer:\n  rules: default\n  model: liteparse\n")
    cfg = Config.load(cfg.root)

    ingest._effective_palaeographer(
        cfg, cfg.dropbox / "collections" / "COLX" / "d.pdf",
        cfg.get_palaeographer("default"), "vlm", None)
    out = capsys.readouterr().out
    assert "liteparse" in out and "warning" in out


# --------------------------------------------------------------------------- read surfaces

def test_page_json_reports_the_page_provenance(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    src, doc = _seed(cfg)
    _stub(monkeypatch, text="chosen")
    _run_pages(cfg, src, {2})

    import json
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.cmd_page(cfg, SimpleNamespace(doc=str(doc), page=2, edited=False,
                                          editor=None, json=True))
    meta = json.loads(buf.getvalue())
    assert meta["page_palaeographer"] == "default"
    assert meta["page_model"] == "default"
    assert meta["pinned"] is True
    assert meta["reviewed"] is False


def test_status_shows_pinned_pages(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    src, doc = _seed(cfg)
    _stub(monkeypatch, text="chosen")
    _run_pages(cfg, src, {2})

    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.cmd_status(cfg, SimpleNamespace())
    out = buf.getvalue()
    assert "1 pinned" in out
    assert "pinned page(s)" in out
    assert "pha scan --unpin" in out


def test_edit_lock_does_not_claim_the_palaeographer_server(tmp_path, monkeypatch):
    """The editor reads no page, so its lock must cover the editor + embed
    servers, not the palaeographer's (an edit should not refuse because a scan
    is running on a vision model it never touches)."""
    cfg = _cfg(tmp_path)
    src, _doc = _seed(cfg)
    from personal_historical_archive import locks
    keys = ingest._job_keys(cfg, [src], include_pal=False)
    assert locks.embed_key(cfg) in keys
    assert locks.stage_key(cfg.get_palaeographer("default")) not in keys
    # ...while a scan DOES claim it
    assert locks.stage_key(cfg.get_palaeographer("default")) in ingest._job_keys(cfg, [src])


def test_dry_run_without_page_never_scans(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    src, _doc = _seed(cfg)

    def explode(*a, **k):
        raise AssertionError("--dry-run without --page ran the pipeline")
    monkeypatch.setattr(ingest, "transcribe_page", explode)
    monkeypatch.setattr(ingest, "render_document", explode)

    pal = cfg.resolve_model(cfg.get_palaeographer("default"), "default")
    res = ingest.scan_once(cfg, object(), pal, path="collections/COLX",
                           dry_run=True, verbose=False)
    assert res["results"][0]["action"] == "error"
    assert "--dry-run needs --page" in res["results"][0]["error"]


def test_test_page_selects_exactly_that_page(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    src, doc = _seed(cfg)
    import personal_historical_archive.testrun as tr
    monkeypatch.setattr(tr, "page_count", lambda p: PAGES)

    def fake_render(path, out_dir, dpi, max_px, q, prefix=None, pages=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        out = []
        for n in sorted(pages or []):
            f = out_dir / f"p{n:03d}.jpg"
            f.write_bytes(b"jpeg")
            out.append(f)
        return out
    monkeypatch.setattr(tr, "render_document", fake_render)
    monkeypatch.setattr(tr, "transcribe_page",
                        lambda client, pal, prompt, img, **kw: "T")

    res = tr.run_test(cfg, "collections/COLX/d.pdf", page=2, verbose=False)
    dr = res["documents"][0]
    assert dr["selected"] == [2]
    assert dr["total_pages"] == PAGES
