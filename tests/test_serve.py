from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from personal_historical_archive import db as _db
from personal_historical_archive.serve import _is_loopback, make_server


class _Server:
    def __init__(self, cfg):
        self.srv = make_server(cfg, "127.0.0.1", 0, quiet=True)
        self.port = self.srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type"), e.read()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def server(cfg):
    s = _Server(cfg)
    try:
        yield s
    finally:
        s.close()


def test_health(cfg, add_document, server):
    add_document()
    status, ctype, body = server.get("/health")
    assert status == 200
    assert ctype.startswith("application/json")
    data = json.loads(body)
    assert data["ok"] is True
    assert data["docs"] == 1
    assert data["archive"] == str(cfg.archive_dir)


def test_meta_json(cfg, add_document, write_variant, server):
    doc_id = add_document(pages=(1, 2))
    # the bare directory is the same variant as the @model one — listed once
    write_variant(doc_id, "edited-french-ocr", 1, None)
    write_variant(doc_id, "edited-french-ocr@deepseek-v4-flash", 1, "text")
    status, _, body = server.get("/doc/colx-d/meta.json")
    assert status == 200
    data = json.loads(body)
    assert data["slug"] == "colx-d"
    assert data["rel_path"] == "collections/COLX/d.pdf"
    assert data["page_count"] == 2
    assert data["variants"] == ["edited-french-ocr@deepseek-v4-flash"]
    assert data["render_url"] == "/doc/colx-d/p{page}.jpg"


def test_serves_the_page_render(cfg, add_document, write_render, server):
    doc_id = add_document()
    write_render(doc_id, 1, b"JPEGBYTES")
    status, ctype, body = server.get("/doc/colx-d/p001.jpg")
    assert status == 200
    assert ctype == "image/jpeg"
    assert body == b"JPEGBYTES"


def test_named_image_fallback(cfg, add_document, write_render, server):
    """A directory-of-images document renders as <source_name>.jpg, not p001.jpg."""
    doc_id = add_document(source_names=["502V"])
    write_render(doc_id, 1, b"NAMED", source_name="502V")
    status, _, body = server.get("/doc/colx-d/p001.jpg")
    assert status == 200
    assert body == b"NAMED"


def test_unknown_slug_is_404(cfg, add_document, server):
    add_document()
    status, _, body = server.get("/doc/nope/meta.json")
    assert status == 404
    assert "unknown slug" in json.loads(body)["error"]


def test_page_beyond_page_count_is_404(cfg, add_document, write_render, server):
    doc_id = add_document(pages=(1,))
    write_render(doc_id, 1)
    status, _, body = server.get("/doc/colx-d/p099.jpg")
    assert status == 404
    assert "out of range" in json.loads(body)["error"]


def test_resolves_the_new_sha_after_reprocess_without_restart(cfg, add_document, write_render, server):
    """The whole point of the endpoint: the URL never changes, the bytes behind
    it do. The slug->sha map must be rebuilt from the live DB."""
    doc_id = add_document()
    write_render(doc_id, 1, b"OLD")
    assert server.get("/doc/colx-d/p001.jpg")[2] == b"OLD"

    conn = _db.connect(cfg.db_path)
    try:
        conn.execute("UPDATE documents SET sha256=? WHERE id=?", ("b" * 64, doc_id))
        conn.commit()
    finally:
        conn.close()
    write_render(doc_id, 1, b"NEW")

    # Touch the DB so a same-second write cannot hide behind mtime granularity.
    later = time.time() + 5
    for p in (Path(cfg.db_path), Path(str(cfg.db_path) + "-wal")):
        if p.exists():
            os.utime(p, (later, later))

    status, _, body = server.get("/doc/colx-d/p001.jpg")
    assert status == 200
    assert body == b"NEW"
    assert json.loads(server.get("/doc/colx-d/meta.json")[2])["sha256"] == "b" * 64


def test_loopback_detection():
    assert _is_loopback("127.0.0.1")
    assert _is_loopback("::1")
    assert _is_loopback("localhost")
    assert not _is_loopback("0.0.0.0")
    assert not _is_loopback("192.168.1.10")


def test_cmd_serve_passes_its_options(cfg, monkeypatch):
    from types import SimpleNamespace

    from personal_historical_archive import cli

    seen = {}
    monkeypatch.setattr("personal_historical_archive.serve.run_server",
                        lambda c, host, port, quiet: seen.update(
                            host=host, port=port, quiet=quiet, archive=c.archive_dir))
    cli.cmd_serve(cfg, SimpleNamespace(host="0.0.0.0", port=9999, quiet=True))
    assert seen == {"host": "0.0.0.0", "port": 9999, "quiet": True,
                    "archive": cfg.archive_dir}


def test_degraded_read_keeps_the_last_good_index(cfg, add_document, monkeypatch):
    """Regression: a failed/degraded DB read must not wipe a good index.

    The `immutable=1` fallback ignores a live WAL, so it can come back with no
    documents while the archive is being written. Publishing that would turn
    every stable URL into a 404 — the index must survive and retry instead.
    """
    add_document()
    srv = _Server(cfg)
    try:
        assert json.loads(srv.get("/health")[2])["docs"] == 1

        def boom(path):
            raise sqlite3.OperationalError("unable to open database file")

        monkeypatch.setattr("personal_historical_archive.serve._connect_ro", boom)
        later = time.time() + 5  # force the index to consider itself stale
        os.utime(cfg.db_path, (later, later))

        status, _, body = srv.get("/doc/colx-d/meta.json")
        assert status == 200, body
        assert json.loads(body)["slug"] == "colx-d"

        health = json.loads(srv.get("/health")[2])
        assert health["docs"] == 1
        assert health["degraded"] is True
        assert "unable to open" in (health["last_error"] or "")
    finally:
        srv.close()


def test_empty_archive_serves_an_empty_index(cfg):
    """An empty result is fine when there is nothing to lose: 404, not 500."""
    _db.connect(cfg.db_path).close()
    srv = _Server(cfg)
    try:
        health = json.loads(srv.get("/health")[2])
        assert health["docs"] == 0
        status, ctype, body = srv.get("/doc/nope/meta.json")
        assert status == 404
        assert ctype.startswith("application/json")
        assert "unknown slug" in json.loads(body)["error"]
    finally:
        srv.close()


# ---- page navigation: viewer, overview and the jump box ----------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _get_nofollow(srv, path):
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(srv.base + path, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _head_headers(srv, path):
    req = urllib.request.Request(srv.base + path, method="HEAD")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, dict(r.headers)


def test_viewer_navigation_and_position(cfg, add_document, write_render, server):
    doc_id = add_document(pages=(1, 2, 3))
    write_render(doc_id, 2)
    status, ctype, body = server.get("/doc/colx-d/p002")
    text = body.decode()
    assert status == 200
    assert ctype.startswith("text/html")
    assert "p. 2 of 3" in text
    assert 'href="/doc/colx-d/p001"' in text      # prev / first
    assert 'href="/doc/colx-d/p003"' in text      # next / last
    assert 'src="/doc/colx-d/p002.jpg"' in text   # the raw render


def test_viewer_boundaries_disable_the_controls(cfg, add_document, write_render, server):
    doc_id = add_document(pages=(1, 2, 3))
    write_render(doc_id, 1)
    write_render(doc_id, 3)
    first = server.get("/doc/colx-d/p001")[2].decode()
    assert '<span class="btn">◀ prev</span>' in first
    assert '<span class="btn">⤒ first</span>' in first
    assert 'href="/doc/colx-d/p002"' in first
    last = server.get("/doc/colx-d/p003")[2].decode()
    assert '<span class="btn">next ▶</span>' in last
    assert '<span class="btn">last ⤓</span>' in last
    assert 'href="/doc/colx-d/p002"' in last


def test_viewer_without_a_render_still_navigates(cfg, add_document, write_render, server):
    doc_id = add_document(pages=(1, 2, 3))
    write_render(doc_id, 1)
    write_render(doc_id, 3)          # page 2 deliberately has no render
    status, _, body = server.get("/doc/colx-d/p002")
    text = body.decode()
    assert status == 200
    assert "No render for page 2" in text
    assert "raw text page 2" in text
    assert 'href="/doc/colx-d/p001"' in text
    assert 'href="/doc/colx-d/p003"' in text


def test_viewer_prefetches_neighbours_and_adds_keys(cfg, add_document, write_render, server):
    doc_id = add_document(pages=(1, 2, 3))
    write_render(doc_id, 2)
    text = server.get("/doc/colx-d/p002")[2].decode()
    assert text.count('rel="prefetch"') == 2
    assert "ArrowLeft" in text and "ArrowRight" in text


def test_viewer_unknown_slug_and_out_of_range(cfg, add_document, server):
    add_document(pages=(1, 2, 3))
    status, ctype, body = server.get("/doc/nope/p001")
    assert status == 404 and ctype.startswith("text/html")
    assert "unknown document" in body.decode()
    status, _, body = server.get("/doc/colx-d/p099")
    assert status == 404
    assert "out of range" in body.decode()


def test_viewer_escapes_document_metadata(cfg, add_document, server):
    add_document(filename="a&b<c.pdf")
    status, _, body = server.get("/doc/colx-a-b-c/p001")
    text = body.decode()
    assert status == 200
    assert "a&amp;b&lt;c.pdf" in text
    assert "<c.pdf" not in text


def test_viewer_sets_csp_and_cache_headers(cfg, add_document, server):
    add_document(pages=(1,))
    status, headers = _head_headers(server, "/doc/colx-d/p001")
    assert status == 200
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-cache"


def test_overview_lists_ranges_and_a_jump_box(cfg, add_document, server):
    add_document(pages=tuple(range(1, 121)))
    status, _, body = server.get("/doc/colx-d/")
    text = body.decode()
    assert status == 200
    assert "120 pages" in text
    for target in ("/doc/colx-d/p001", "/doc/colx-d/p051", "/doc/colx-d/p101"):
        assert f'href="{target}"' in text
    assert 'action="/doc/colx-d/go"' in text


def test_jump_redirects_and_rejects_bad_input(cfg, add_document, server):
    add_document(pages=(1, 2, 3))
    status, headers, _ = _get_nofollow(server, "/doc/colx-d/go?page=2")
    assert status == 302
    assert headers.get("Location") == "/doc/colx-d/p002"
    assert _get_nofollow(server, "/doc/colx-d/go?page=99")[0] == 404
    assert _get_nofollow(server, "/doc/colx-d/go?page=x")[0] == 404


def test_meta_reports_the_navigation_urls(cfg, add_document, server):
    add_document(pages=(1, 2, 3))
    data = json.loads(server.get("/doc/colx-d/meta.json")[2])
    assert data["pages"] == {"first": 1, "last": 3}
    assert data["viewer_url"] == "/doc/colx-d/p{page}"
    assert data["overview_url"] == "/doc/colx-d/"
    assert data["render_url"] == "/doc/colx-d/p{page}.jpg"      # unchanged


# ---- page text in the viewer ------------------------------------------------

def _set_page_text(cfg, doc_id, page_no, *, raw=None, editor=None, edit=None,
                   reviewed=False):
    """Set raw text and/or one page edit directly in the DB for viewer tests."""
    conn = _db.connect(cfg.db_path)
    try:
        row = conn.execute(
            "SELECT id FROM pages WHERE document_id=? AND page_no=?",
            (doc_id, page_no)).fetchone()
        assert row is not None
        page_id = row["id"]
        if raw is not None:
            conn.execute("UPDATE pages SET raw_text=? WHERE id=?", (raw, page_id))
        if editor and edit is not None:
            _db.set_page_edit(conn, page_id, editor, text=edit)
            if reviewed:
                _db.mark_edit_reviewed(conn, page_id, editor, edit)
        conn.commit()
    finally:
        conn.close()


def test_viewer_shows_raw_and_effective_edited_text(cfg, add_document,
                                                   write_render, server):
    doc_id = add_document(pages=(1,), editor="french-ocr",
                          editor_model="deepseek-v4-flash")
    write_render(doc_id, 1)
    _set_page_text(cfg, doc_id, 1, raw="RAW TRANSCRIPTION",
                   editor="french-ocr", edit="EDITED READING")

    status, _, body = server.get("/doc/colx-d/p001")
    text = body.decode()
    assert status == 200
    assert "Transcription" in text
    assert "RAW TRANSCRIPTION" in text
    assert "transcription: ocr@liteparse-fra" in text
    assert "Edited reading" in text
    assert "EDITED READING" in text
    assert "edited: french-ocr@deepseek-v4-flash" in text


def test_viewer_omits_the_edited_panel_when_no_filled_edit_exists(
        cfg, add_document, write_render, server):
    doc_id = add_document(pages=(1,))
    write_render(doc_id, 1)
    text = server.get("/doc/colx-d/p001")[2].decode()
    assert "raw text page 1" in text
    assert "Edited reading" not in text


def test_viewer_shows_a_filled_edit_but_not_a_waiting_stub(
        cfg, add_document, write_render, server):
    doc_id = add_document(pages=(1,))
    write_render(doc_id, 1)
    conn = _db.connect(cfg.db_path)
    try:
        page_id = conn.execute(
            "SELECT id FROM pages WHERE document_id=? AND page_no=1",
            (doc_id,)).fetchone()["id"]
        _db.set_page_edit(conn, page_id, "french-ocr", error="not run yet")
        conn.commit()
    finally:
        conn.close()

    text = server.get("/doc/colx-d/p001")[2].decode()
    assert "raw text page 1" in text
    assert "Edited reading" not in text
    assert "not run yet" not in text


def test_viewer_escapes_raw_and_edited_text(cfg, add_document, write_render,
                                            server):
    doc_id = add_document(pages=(1,))
    write_render(doc_id, 1)
    raw = '<script>alert("raw")</script> & <b>not markup</b>'
    edit = '<script>alert("edited")</script> & "quoted"'
    _set_page_text(cfg, doc_id, 1, raw=raw, editor="french-ocr", edit=edit)

    text = server.get("/doc/colx-d/p001")[2].decode()
    assert "&lt;script&gt;alert(&quot;raw&quot;)&lt;/script&gt; &amp; " in text
    assert "&lt;b&gt;not markup&lt;/b&gt;" in text
    assert "&lt;script&gt;alert(&quot;edited&quot;)&lt;/script&gt; &amp; " in text
    assert '<script>alert("raw")</script>' not in text
    assert '<script>alert("edited")</script>' not in text


def test_viewer_serves_the_reviewed_edit_over_the_document_editor(
        cfg, add_document, write_render, server):
    doc_id = add_document(pages=(1,), editor="french-ocr")
    write_render(doc_id, 1)
    conn = _db.connect(cfg.db_path)
    try:
        page_id = conn.execute(
            "SELECT id FROM pages WHERE document_id=? AND page_no=1",
            (doc_id,)).fetchone()["id"]
        _db.set_page_edit(conn, page_id, "french-ocr", text="DOCUMENT EDIT")
        _db.set_page_edit(conn, page_id, "human-editor", text="HUMAN CORRECTION")
        _db.mark_edit_reviewed(conn, page_id, "human-editor", "HUMAN CORRECTION")
        conn.commit()
    finally:
        conn.close()

    text = server.get("/doc/colx-d/p001")[2].decode()
    assert "HUMAN CORRECTION" in text
    assert "DOCUMENT EDIT" not in text
    assert "edited: human-editor" in text


# ------------------------------------------------------- bibliographic reference

SIDECAR = """<?xml version="1.0"?>
<mods xmlns="http://www.loc.gov/mods/v3" version="3.8">
  <titleInfo><title>Documentos históricos do Padroado do Oriente</title><partNumber>IV</partNumber></titleInfo>
  <name type="personal"><namePart type="family">Silva</namePart><namePart type="given">António</namePart></name>
  <originInfo><place><placeTerm type="text">Lisboa</placeTerm></place>
    <publisher>Imprensa Nacional</publisher><dateIssued>1947</dateIssued></originInfo>
  <location><shelfLocator>BNP RES. 1234 V.</shelfLocator></location>
  <recordInfo><recordOrigin>human-supplied</recordOrigin></recordInfo>
</mods>
"""


def _sync(cfg):
    from personal_historical_archive.ingest import sync_bibliography

    conn = _db.connect(cfg.db_path)
    try:
        sync_bibliography(cfg, conn)
    finally:
        conn.close()


def test_meta_and_viewer_carry_the_bibliographic_reference(cfg, add_document, write_sidecar):
    doc_id = add_document()
    write_sidecar(doc_id, SIDECAR)
    _sync(cfg)
    s = _Server(cfg)
    try:
        status, _ctype, body = s.get("/doc/colx-d/meta.json")
        assert status == 200
        data = json.loads(body)
        assert data["reference"].startswith("Silva, António")
        assert data["reference_verified"] is True
        assert data["bibliography"]["shelfmark"] == "BNP RES. 1234 V."

        status, _ctype, body = s.get("/doc/colx-d/p001")
        assert status == 200
        assert "BNP RES. 1234 V." in body.decode()

        status, _ctype, body = s.get("/doc/colx-d/")
        assert status == 200
        assert "BNP RES. 1234 V." in body.decode()
    finally:
        s.close()


def test_meta_without_a_sidecar_has_no_reference(cfg, add_document):
    add_document()
    s = _Server(cfg)
    try:
        status, _ctype, body = s.get("/doc/colx-d/meta.json")
        assert status == 200
        data = json.loads(body)
        assert data["reference"] is None and data["bibliography"] is None
    finally:
        s.close()


def test_serve_still_works_without_the_bibliography_table(cfg, add_document):
    """An archive written before sidecars existed must still serve."""
    add_document()
    conn = _db.connect(cfg.db_path)
    conn.execute("DROP TABLE document_bibliography")
    conn.commit()
    conn.close()
    s = _Server(cfg)
    try:
        status, _ctype, body = s.get("/doc/colx-d/meta.json")
        assert status == 200
        assert json.loads(body)["reference"] is None
    finally:
        s.close()

# ---- encoder records (JSON) ---------------------------------------------------

def _add_record(cfg, doc_id, *, encoder="letters", kind="letter",
                data=None, source=None):
    conn = _db.connect(cfg.db_path)
    _db.add_record(conn, doc_id, encoder, kind,
                   json.dumps(data or {"kind": kind, "text": "record text",
                                       "page_start": 1}), source)
    conn.commit()
    conn.close()


def _seed_records(cfg, doc_id, *, page_start=1, encoder="letters", kind="letter",
                  text="record text", **fields):
    data = {"kind": kind, "text": text, "page_start": page_start, **fields}
    _add_record(cfg, doc_id, encoder=encoder, kind=kind, data=data)


def test_records_archive_json_carries_identity_not_absolute_path(cfg, add_document,
                                                                server):
    doc_id = add_document()
    _seed_records(cfg, doc_id, page_start=1, place="Malaca")

    status, ctype, body = server.get("/records.json")
    assert status == 200
    assert ctype.startswith("application/json")
    data = json.loads(body)
    assert data["ok"] is True
    assert data["total"] == 1
    assert data["limit"] == 200 and data["offset"] == 0
    assert data["degraded"] is False
    rec = data["records"][0]
    assert rec["id"] == 1 and rec["document_id"] == doc_id
    assert rec["slug"] == "colx-d"
    assert rec["rel_path"] == "collections/COLX/d.pdf"
    assert rec["filename"] == "d.pdf"
    assert rec["collection"] == "collections/COLX"
    assert rec["page_no"] == 1
    assert rec["viewer_url"] == "/doc/colx-d/p001"
    assert rec["encoder"] == "letters" and rec["kind"] == "letter"
    assert rec["data"]["place"] == "Malaca"
    assert "path" not in rec          # never leak machine paths from serve

    status, _ctype, body = server.get("/records")     # extensionless alias
    assert status == 200
    assert json.loads(body)["total"] == 1


def test_records_document_and_page_routes(cfg, add_document, server):
    doc_id = add_document(pages=(1, 2))
    _seed_records(cfg, doc_id, page_start=1, text="carta")
    _seed_records(cfg, doc_id, kind="report", text="relatorio", page_start=2)

    status, _ctype, body = server.get("/doc/colx-d/records.json")
    assert status == 200
    data = json.loads(body)
    assert data["total"] == 2
    assert data["slug"] == "colx-d" and data["document_id"] == doc_id
    assert data["filename"] == "d.pdf" and data["page"] is None
    assert [r["kind"] for r in data["records"]] == ["letter", "report"]

    one = json.loads(server.get("/doc/colx-d/records.json?page=1")[2])
    assert one["total"] == 1
    assert one["page"] == 1
    assert [r["kind"] for r in one["records"]] == ["letter"]

    two = json.loads(server.get("/doc/colx-d/records?page=2")[2])
    assert [r["kind"] for r in two["records"]] == ["report"]


def test_records_unknown_slug_is_json_404(cfg, add_document, server):
    add_document()
    status, ctype, body = server.get("/doc/nope/records.json")
    assert status == 404
    assert ctype.startswith("application/json")
    assert json.loads(body)["ok"] is False


def test_records_filters_and_pagination(cfg, add_document, server):
    a = add_document(filename="a.pdf")
    b = add_document(filename="b.pdf")
    _seed_records(cfg, a, encoder="letters", kind="letter", place="Malaca",
                  text="carta de Malaca")
    _seed_records(cfg, a, encoder="letters", kind="report", place="Goa",
                  text="relatorio")
    _seed_records(cfg, b, encoder="person", kind="person", text="Xavier")

    assert json.loads(server.get("/records.json?encoder=letters")[2])["total"] == 2
    assert json.loads(server.get("/records.json?kind=report")[2])["total"] == 1
    assert json.loads(server.get("/records.json?record_kind=person")[2])["total"] == 1
    assert json.loads(server.get("/records.json?field=place%3DMalaca")[2])["total"] == 1
    assert json.loads(server.get("/records.json?field=place%3Dmalaca")[2])["total"] == 0
    assert json.loads(server.get(
        "/records.json?field=place%3Dmalaca&ignore_case=1")[2])["total"] == 1
    assert json.loads(server.get(
        "/records.json?field=place%3Dmalaca&ignore_case=0")[2])["total"] == 0

    page = json.loads(server.get("/records.json?limit=1&offset=1")[2])
    assert page["total"] == 3 and page["limit"] == 1 and page["offset"] == 1
    assert len(page["records"]) == 1
    assert json.loads(server.get("/records.json?limit=5000")[2])["limit"] == 1000

    # page filter is start-page based, archive-wide too
    assert json.loads(server.get("/records.json?page=1")[2])["total"] == 3


def test_records_bad_parameters_are_400(cfg, add_document, server):
    add_document()
    for path in (
        "/records.json?limit=x",
        "/records.json?offset=x",
        "/records.json?page=0",
        "/records.json?page=x",
        "/records.json?field=place",
        "/records.json?field=bad%20name%3Dx",
    ):
        status, ctype, body = server.get(path)
        assert status == 400, path
        assert ctype.startswith("application/json")
        payload = json.loads(body)
        assert payload["ok"] is False and payload["error"], path


def test_records_without_records_table_returns_zeros(cfg, add_document, server):
    """An archive whose DB predates encoder records must serve empty JSON, not 500."""
    add_document()
    assert json.loads(server.get("/records.json")[2])["total"] == 0

    conn = _db.connect(cfg.db_path)
    conn.execute("DROP TABLE record_embeddings")
    conn.execute("DROP TABLE records_fts")
    conn.execute("DROP TABLE records")
    conn.commit()
    conn.close()

    status, _ctype, body = server.get("/records.json")
    assert status == 200
    assert json.loads(body) == {"ok": True, "total": 0, "limit": 200,
                                "offset": 0, "degraded": False, "records": []}
    status, _ctype, body = server.get("/doc/colx-d/records.json")
    assert status == 200
    assert json.loads(body)["total"] == 0


def test_records_links_appear_in_meta_viewer_and_overview(cfg, add_document, server):
    add_document()
    meta = json.loads(server.get("/doc/colx-d/meta.json")[2])
    assert meta["records_url"] == "/doc/colx-d/records.json"
    assert meta["page_records_url"] == "/doc/colx-d/records.json?page={page}"

    status, _ctype, body = server.get("/doc/colx-d/p001")
    assert status == 200
    assert b"/doc/colx-d/records.json?page=1" in body

    status, _ctype, body = server.get("/doc/colx-d/")
    assert status == 200
    assert b"/doc/colx-d/records.json" in body


def test_records_head_and_cache_headers(cfg, add_document, server):
    add_document()
    status, _ctype, body = server.get("/records.json")
    assert status == 200
    # `_Server.get` does not expose headers; request HEAD directly to check both.
    req = urllib.request.Request(server.base + "/records.json", method="HEAD")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200
        assert r.headers.get("Cache-Control") == "no-cache"
        assert r.read() == b""

def test_connect_ro_never_creates_a_missing_archive(tmp_path):
    """Both read modes are read-only: a missing archive.db must raise, not be
    auto-created (a bare immutable=1 URI is READWRITE|CREATE)."""
    from personal_historical_archive.serve import _connect_ro

    missing = tmp_path / "not-an-archive.db"
    with pytest.raises(sqlite3.OperationalError):
        _connect_ro(missing)
    assert not missing.exists()


def test_records_missing_db_is_generic_503(cfg, add_document, server):
    """A DB failure is a 503 whose message never leaks the absolute DB path."""
    add_document()
    for suffix in ("", "-wal", "-shm"):
        Path(str(cfg.db_path) + suffix).unlink(missing_ok=True)

    status, ctype, body = server.get("/records.json")
    assert status == 503
    assert ctype.startswith("application/json")
    payload = json.loads(body)
    assert payload["ok"] is False
    assert "archive" in payload["error"]
    assert str(cfg.archive_dir) not in payload["error"]
    assert not cfg.db_path.exists()       # the failed read must not create it
