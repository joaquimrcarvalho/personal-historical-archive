"""``pha serve`` — a read-only HTTP endpoint with stable page-render URLs.

The URL for a page is permanent::

    http://127.0.0.1:8765/doc/<slug>/p<NNN>.jpg

because it names the *slug* (derived from the dropbox-relative path), not the
document id, the dated library folder or the content-addressed render directory.
The server resolves ``slug -> current sha256`` per request (cached on the DB's
mtime), so a re-scan/re-edit is picked up without a restart and without the
consumer rewriting its markdown.

Read-only by construction: the DB is opened read-only, no route mutates
anything, and mutations stay behind the existing scan/edit lock.
"""

from __future__ import annotations

import ipaddress
import json
import re
import sqlite3
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import addresses
from .config import Config

_PAGE_RE = re.compile(r"^(?P<slug>.+)/p(?P<page>\d+)\.(?:jpg|jpeg)$")


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    """Open the archive DB read-only.

    ``mode=ro`` reads the live WAL (so the newest committed pages are visible);
    ``immutable=1`` is the fallback for a filesystem where ``-shm``/``-wal``
    cannot be opened, and is what ``dsh-pha`` already uses on this database.
    """
    base = Path(db_path).as_posix()
    for query in ("mode=ro", "immutable=1"):
        try:
            conn = sqlite3.connect(f"file:{base}?{query}", uri=True, timeout=5)
        except sqlite3.OperationalError:
            continue
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            return conn
        except sqlite3.OperationalError:
            conn.close()
    raise sqlite3.OperationalError(f"cannot open {db_path} read-only")


class _Index:
    """slug -> document map, rebuilt whenever the DB (or its WAL) changes."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._key: tuple[float, ...] | None = None
        self._by_slug: dict[str, dict] = {}
        self._mtime = 0.0

    def _db_key(self) -> tuple[float, ...]:
        db = Path(self.cfg.db_path)
        key = []
        for p in (db, Path(str(db) + "-wal")):
            try:
                key.append(p.stat().st_mtime)
            except OSError:
                key.append(0.0)
        return tuple(key)

    def reload(self) -> None:
        rows = []
        try:
            conn = _connect_ro(self.cfg.db_path)
        except sqlite3.Error:
            conn = None
        if conn is not None:
            try:
                rows = conn.execute(
                    "SELECT id, filename, path, dir_path, sha256, page_count, status, "
                    "created_at FROM documents"
                ).fetchall()
            except sqlite3.Error:
                rows = []  # archive not initialised yet: serve an empty index
            finally:
                conn.close()
        by_slug: dict[str, dict] = {}
        for r in rows:
            d = dict(r)
            rel = addresses.document_rel_path(self.cfg, d)
            d["rel_path"] = rel
            by_slug[addresses.doc_slug(rel)] = d
        self._by_slug = by_slug
        self._mtime = max(self._db_key())
        self._key = self._db_key()

    def get(self, slug: str) -> dict | None:
        with self._lock:
            if self._key != self._db_key():
                self.reload()
            return self._by_slug.get(slug)

    def count(self) -> int:
        with self._lock:
            if self._key != self._db_key():
                self.reload()
            return len(self._by_slug)

    def last_db_mtime(self) -> float:
        with self._lock:
            return self._mtime


def _document_variants(cfg: Config, doc: dict) -> list[str]:
    """The document's variant directory names (cheap: one directory listing)."""
    from .ingest import _library_doc_dir

    doc_dir = _library_doc_dir(cfg, doc)
    if doc_dir is None or not doc_dir.is_dir():
        return []
    return sorted(d.name for d in doc_dir.iterdir() if d.is_dir())


def make_handler(cfg: Config, index: _Index, quiet: bool = False):
    class Handler(BaseHTTPRequestHandler):
        server_version = "pha-serve"

        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            if not quiet:
                sys.stderr.write("pha serve: " + (fmt % args) + "\n")

        # ---- helpers -----------------------------------------------------
        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"ok": False, "error": message})

        # ---- routes ------------------------------------------------------
        def do_GET(self):  # noqa: N802 - stdlib signature
            self.route()

        def do_HEAD(self):  # noqa: N802 - stdlib signature
            self.route()

        def route(self) -> None:
            path = unquote(urlparse(self.path).path)
            if path in ("/health", "/health.json"):
                self._json(HTTPStatus.OK, {
                    "ok": True,
                    "archive": str(cfg.archive_dir),
                    "docs": index.count(),
                    "last_db_mtime": index.last_db_mtime(),
                })
                return
            if not path.startswith("/doc/"):
                self._error(HTTPStatus.NOT_FOUND, "not found")
                return
            rest = path[len("/doc/"):]
            if rest.endswith("/meta.json"):
                self._meta(rest[: -len("/meta.json")])
                return
            m = _PAGE_RE.match(rest)
            if m:
                self._image(m.group("slug"), int(m.group("page")))
                return
            self._error(HTTPStatus.NOT_FOUND, "not found")

        def _meta(self, slug: str) -> None:
            doc = index.get(slug)
            if doc is None:
                self._error(HTTPStatus.NOT_FOUND, f"unknown slug {slug!r}")
                return
            self._json(HTTPStatus.OK, {
                "ok": True,
                "slug": slug,
                "rel_path": doc.get("rel_path"),
                "filename": doc.get("filename"),
                "page_count": doc.get("page_count"),
                "sha256": doc.get("sha256"),
                "status": doc.get("status"),
                "variants": _document_variants(cfg, doc),
                "render_url": f"/doc/{slug}/p{{page}}.jpg",
            })

        def _image(self, slug: str, page: int) -> None:
            doc = index.get(slug)
            if doc is None:
                self._error(HTTPStatus.NOT_FOUND, f"unknown slug {slug!r}")
                return
            total = doc.get("page_count") or 0
            if page < 1 or (total and page > total):
                self._error(HTTPStatus.NOT_FOUND,
                            f"page {page} out of range (1-{total or '?'})")
                return
            source_name = None
            try:
                conn = _connect_ro(cfg.db_path)
                try:
                    row = conn.execute(
                        "SELECT source_name FROM pages WHERE document_id=? AND page_no=?",
                        (doc.get("id"), page)).fetchone()
                    source_name = row["source_name"] if row else None
                finally:
                    conn.close()
            except sqlite3.Error:
                source_name = None
            render = addresses.render_path(cfg, doc, page, source_name)
            if render is None or not render.is_file():
                self._error(HTTPStatus.NOT_FOUND, "no render for this page")
                return
            try:
                blob = render.read_bytes()
            except OSError as e:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"cannot read render: {e}")
                return
            # The URL is stable but the bytes behind it may change after a
            # re-process, so tell clients to revalidate rather than pin a copy.
            self._send(HTTPStatus.OK, blob, "image/jpeg",
                       {"Cache-Control": "no-cache"})

    return Handler


def make_server(cfg: Config, host: str = "127.0.0.1", port: int = 8765,
                quiet: bool = False) -> ThreadingHTTPServer:
    """Build (but do not start) the server. Tests pass port 0 for an ephemeral port."""
    index = _Index(cfg)
    index.reload()
    srv = ThreadingHTTPServer((host, port), make_handler(cfg, index, quiet=quiet))
    srv.daemon_threads = True
    return srv


def _is_loopback(host: str) -> bool:
    if host in ("", "localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def run_server(cfg: Config, host: str = "127.0.0.1", port: int = 8765,
               quiet: bool = False) -> None:
    if not _is_loopback(host):
        print(f"warning: binding to {host} exposes the archive to your network; "
              "use 127.0.0.1 unless you deliberately need LAN/mobile access",
              file=sys.stderr)
    srv = make_server(cfg, host=host, port=port, quiet=quiet)
    shown = host if host not in ("", "0.0.0.0", "::") else "127.0.0.1"
    base = f"http://{shown}:{srv.server_address[1]}"
    print(f"pha serve: read-only page renders on {base}")
    print(f"  health:  {base}/health")
    print(f"  example: {base}/doc/<slug>/p001.jpg   (slug: pha page <doc> <page> --json)")
    print("  Ctrl-C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\npha serve: stopped")
    finally:
        srv.server_close()
