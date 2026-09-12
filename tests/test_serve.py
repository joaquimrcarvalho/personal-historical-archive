from __future__ import annotations

import json
import os
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
