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
