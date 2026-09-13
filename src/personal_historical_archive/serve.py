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

import html
import ipaddress
import json
import re
import sqlite3
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import addresses
from .config import Config

_PAGE_RE = re.compile(r"^(?P<slug>.+)/p(?P<page>\d+)\.(?:jpg|jpeg)$")
_VIEW_RE = re.compile(r"^(?P<slug>.+)/p(?P<page>\d+)$")

# One tiny stylesheet, inlined: the viewer must work offline and with no build
# step or external asset (CSP below allows only inline style/script).
_CSS = """\
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;flex-direction:column;
  font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:#faf9f7;color:#1b1b1b}
header,footer{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;
  padding:.55rem .9rem;background:#f1efe9;border-bottom:1px solid #ddd}
footer{border-bottom:0;border-top:1px solid #ddd;margin-top:auto;font-size:13px}
.spacer{flex:1}
main{flex:1;display:flex;align-items:center;justify-content:center;padding:1rem}
main.plain{display:block}
img.page{max-width:100%;max-height:calc(100vh - 8.5rem);box-shadow:0 1px 8px rgba(0,0,0,.28)}
a.btn,span.btn{padding:.22rem .55rem;border:1px solid #bbb;border-radius:6px;
  text-decoration:none;color:inherit;white-space:nowrap}
span.btn{border-color:#e6e4df;color:#b3b0aa}
a{color:#0b5fff}
.muted{color:#777;font-size:13px}
h1{font-size:1.15rem;margin:.2rem 0}
h2{font-size:1rem;margin:1.4rem 0 .4rem}
form{display:inline-flex;gap:.3rem;align-items:center}
input[type=number]{width:5.5rem;padding:.2rem .35rem;font:inherit}
button{font:inherit;padding:.22rem .55rem}
.ranges{display:flex;flex-wrap:wrap;gap:.35rem}
.wrap{max-width:64rem;width:100%;margin:0 auto}
.notice{border:1px solid #e0d6c0;background:#fdf6e3;border-radius:8px;padding:1rem 1.2rem;max-width:32rem}
@media (prefers-color-scheme:dark){
  body{background:#17181a;color:#e9e7e4}
  header,footer{background:#202226;border-color:#333}
  span.btn{border-color:#2b2b2b;color:#6a6a6a}
  a{color:#7aa7ff}
  .muted{color:#9a9a9a}
  .notice{border-color:#4a4335;background:#2a2620}
}
"""


def _overview_url(slug: str) -> str:
    return f"/doc/{slug}/"


def _viewer_url(slug: str, page: int) -> str:
    return f"/doc/{slug}/p{page:03d}"


def _jpg_url(slug: str, page: int) -> str:
    return f"/doc/{slug}/p{page:03d}.jpg"


def _meta_url(slug: str) -> str:
    return f"/doc/{slug}/meta.json"


def _go_url(slug: str) -> str:
    return f"/doc/{slug}/go"


def _range_links(slug: str, total: int, step: int = 50) -> str:
    """A compact page-range list — works for a 1,383-page volume, unlike a grid."""
    if total <= 0:
        return ""
    out = []
    for start in range(1, total + 1, step):
        end = min(start + step - 1, total)
        label = str(start) if start == end else f"{start}–{end}"
        out.append(f'<a class="btn" href="{_viewer_url(slug, start)}">{label}</a>')
    return " ".join(out)


def _html_page(title: str, body: str, head_extra: str = "") -> bytes:
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{html.escape(title)}</title>\n"
        f"<style>{_CSS}</style>\n"
        f"{head_extra}</head>\n<body>\n{body}\n</body>\n</html>\n"
    ).encode("utf-8")


def _out_of_range(page: int, total: int) -> str:
    return f"page {page} out of range" + (f" (1–{total})" if total else "")



def _connect_ro(db_path: Path) -> tuple[sqlite3.Connection, bool]:
    """Open the archive DB read-only, returning ``(conn, degraded)``.

    ``mode=ro`` reads the live WAL, so the newest committed rows are visible.
    ``immutable=1`` is the fallback for a filesystem where ``-shm``/``-wal``
    cannot be opened (e.g. a sandboxed launch) — but it reads ONLY the main DB
    file and IGNORES a live ``-wal``, so an empty or stale result from it is not
    trustworthy while the archive is being written. ``degraded`` reports that
    fallback to the caller, which must not treat its answer as authoritative.
    """
    base = Path(db_path).as_posix()
    for query, degraded in (("mode=ro", False), ("immutable=1", True)):
        try:
            conn = sqlite3.connect(f"file:{base}?{query}", uri=True, timeout=5)
        except sqlite3.OperationalError:
            continue
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            return conn, degraded
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
        self._degraded = False
        self._last_error: str | None = None

    def _db_key(self) -> tuple[float, ...]:
        db = Path(self.cfg.db_path)
        key = []
        for p in (db, Path(str(db) + "-wal")):
            try:
                key.append(p.stat().st_mtime)
            except OSError:
                key.append(0.0)
        return tuple(key)

    def _set_state(self, degraded: bool, error: str | None, kept: int | None = None) -> None:
        """Record read health; log only on a change so requests don't spam."""
        self._last_error = error
        if degraded != self._degraded:
            if degraded:
                extra = f"; serving the last good index of {kept} document(s)" if kept else ""
                sys.stderr.write(f"pha serve: warning: DB read degraded ({error}){extra}\n")
            else:
                sys.stderr.write("pha serve: DB read recovered\n")
        self._degraded = degraded

    def reload(self) -> None:
        """Rebuild the slug -> document map, never trusting a bad read.

        A failed or degraded read must not REPLACE a good index with an empty
        one. ``immutable=1`` ignores a live WAL, so while the archive is being
        written it can legitimately come back with nothing even though documents
        exist — publishing that would turn every stable URL into a 404. When the
        read is untrustworthy and we already hold an index, keep serving it and
        retry on the next request (``_key`` is left stale on purpose). An empty
        result is only accepted when we have nothing to lose.
        """
        rows: list = []
        degraded = False
        error: str | None = None
        try:
            conn, degraded = _connect_ro(self.cfg.db_path)
        except sqlite3.Error as exc:
            conn, degraded, error = None, True, str(exc)
        if conn is not None:
            try:
                rows = conn.execute(
                    "SELECT id, filename, path, dir_path, sha256, page_count, status, "
                    "created_at FROM documents"
                ).fetchall()
            except sqlite3.Error as exc:
                rows, degraded, error = [], True, str(exc)
            finally:
                conn.close()
        if degraded and not rows and self._by_slug:
            self._set_state(
                True,
                error or "read back no documents (immutable fallback ignores a live WAL)",
                kept=len(self._by_slug),
            )
            return
        by_slug: dict[str, dict] = {}
        for r in rows:
            d = dict(r)
            rel = addresses.document_rel_path(self.cfg, d)
            d["rel_path"] = rel
            by_slug[addresses.doc_slug(rel)] = d
        self._by_slug = by_slug
        self._mtime = max(self._db_key())
        self._key = self._db_key()
        self._set_state(degraded, error)

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

    def degraded(self) -> bool:
        with self._lock:
            return self._degraded

    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error


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

        def _html(self, status: int, body: bytes) -> None:
            self._send(status, body, "text/html; charset=utf-8", {
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
                # No external asset, no CDN: the viewer works offline.
                "Content-Security-Policy": (
                    "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                    "script-src 'unsafe-inline'"),
            })

        def _redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _not_found_html(self, message: str) -> None:
            body = ('<main class="plain"><div class="wrap">'
                    f'<h1>{html.escape(message)}</h1>'
                    '<p class="muted">No such page on this server.</p>'
                    '<p><a class="btn" href="/health">/health</a></p>'
                    '</div></main>')
            self._html(HTTPStatus.NOT_FOUND, _html_page("not found", body))

        def _source_name(self, doc: dict, page: int) -> str | None:
            """The page's source image stem (directory-of-images documents)."""
            try:
                conn, _ = _connect_ro(cfg.db_path)
                try:
                    row = conn.execute(
                        "SELECT source_name FROM pages WHERE document_id=? AND page_no=?",
                        (doc.get("id"), page)).fetchone()
                    return row["source_name"] if row else None
                finally:
                    conn.close()
            except sqlite3.Error:
                return None

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
                    # A degraded read means the DB could only be opened with the
                    # immutable fallback (no live WAL): the index may be stale.
                    "degraded": index.degraded(),
                    "last_error": index.last_error(),
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
            m = _VIEW_RE.match(rest)
            if m:
                self._viewer(m.group("slug"), int(m.group("page")))
                return
            if rest.endswith("/go"):
                self._go(rest[: -len("/go")], urlparse(self.path).query)
                return
            slug = rest.rstrip("/")
            if slug and "/" not in slug:
                self._overview(slug)
                return
            self._error(HTTPStatus.NOT_FOUND, "not found")

        def _meta(self, slug: str) -> None:
            doc = index.get(slug)
            if doc is None:
                self._error(HTTPStatus.NOT_FOUND, f"unknown slug {slug!r}")
                return
            total = doc.get("page_count") or 0
            self._json(HTTPStatus.OK, {
                "ok": True,
                "slug": slug,
                "rel_path": doc.get("rel_path"),
                "filename": doc.get("filename"),
                "page_count": doc.get("page_count"),
                "sha256": doc.get("sha256"),
                "status": doc.get("status"),
                "variants": _document_variants(cfg, doc),
                # Navigable page addresses. `render_url` is the raw image; the
                # viewer wraps it with prev/next and a position. `{page}` is
                # substituted by the caller (zero-padding is optional).
                "render_url": f"/doc/{slug}/p{{page}}.jpg",
                "viewer_url": f"/doc/{slug}/p{{page}}",
                "overview_url": _overview_url(slug),
                "pages": {"first": 1, "last": total},
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
            source_name = self._source_name(doc, page)
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

        def _viewer(self, slug: str, page: int) -> None:
            """The reading surface for one page: image + prev/next/first/last.

            Works with JavaScript disabled (plain links and a GET form); JS only
            adds the arrow keys, and the two neighbouring images are prefetched so
            flipping feels instant. Deliberately shows the render ONLY — the PHA
            view remains the place to read the transcription text.
            """
            doc = index.get(slug)
            if doc is None:
                self._not_found_html(f"unknown document {slug!r}")
                return
            total = doc.get("page_count") or 0
            if page < 1 or (total and page > total):
                self._not_found_html(_out_of_range(page, total))
                return
            name = doc.get("filename") or slug
            has_prev = page > 1
            has_next = page < total if total else True

            def control(label: str, url: str, enabled: bool) -> str:
                if enabled:
                    return f'<a class="btn" href="{url}">{label}</a>'
                return f'<span class="btn">{label}</span>'

            nav = " ".join((
                control("⤒ first", _viewer_url(slug, 1), has_prev),
                control("◀ prev", _viewer_url(slug, page - 1), has_prev),
                control("next ▶", _viewer_url(slug, page + 1), has_next),
                control("last ⤓", _viewer_url(slug, total), bool(total) and has_next),
            ))

            source_name = self._source_name(doc, page)
            render = addresses.render_path(cfg, doc, page, source_name)
            if render is None or not render.is_file():
                # Navigation must never dead-end: say what is missing and keep
                # the controls (contrast /p{N}.jpg, which 404s).
                main = ('<main><div class="notice">'
                        f'<p><strong>No render for page {page}.</strong></p>'
                        '<p class="muted">This page has no image in the archive. '
                        'The prev/next controls still work.</p></div></main>')
            else:
                main = (f'<main><img class="page" src="{_jpg_url(slug, page)}" '
                        f'alt="{html.escape(name)} — page {page}"></main>')

            of_total = f" of {total}" if total else ""
            max_attr = f' max="{total}"' if total else ''
            header = "".join((
                f'<header><a class="btn" href="{_overview_url(slug)}" '
                'title="document overview">▤</a>',
                f'<strong>{html.escape(name)}</strong>',
                f'<span class="muted">p. {page}{of_total}</span>',
                '<span class="spacer"></span>',
                nav,
                f'<form action="{_go_url(slug)}" method="get">'
                f'<input type="number" name="page" min="1"{max_attr} value="{page}" '
                'aria-label="page number"><button>go</button></form>',
                '</header>',
            ))
            footer = "".join((
                f'<footer><span class="muted">{html.escape(doc.get("rel_path") or "")}</span>',
                '<span class="spacer"></span>',
                f'<a href="{_jpg_url(slug, page)}">jpg</a>',
                f'<a href="{_meta_url(slug)}">meta.json</a>',
                f'<code>pha cite {doc.get("id")} {page}</code></footer>',
            ))

            pre = []
            if has_prev:
                pre.append(f'<link rel="prefetch" href="{_jpg_url(slug, page - 1)}" as="image">')
            if has_next:
                pre.append(f'<link rel="prefetch" href="{_jpg_url(slug, page + 1)}" as="image">')
            js_prev = json.dumps(_viewer_url(slug, page - 1)) if has_prev else "null"
            js_next = json.dumps(_viewer_url(slug, page + 1)) if has_next else "null"
            pre.append("<script>const P=" + js_prev + ",N=" + js_next + ";"
                       "addEventListener('keydown',e=>{const t=e.target;"
                       "if(t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA'))return;"
                       "if(e.key==='ArrowLeft'||e.key==='k'){if(P)location.href=P}"
                       "else if(e.key==='ArrowRight'||e.key==='j'){if(N)location.href=N}"
                       "});</script>")
            self._html(HTTPStatus.OK, _html_page(
                f"{name} — p. {page}", header + main + footer, head_extra="".join(pre)))

        def _overview(self, slug: str) -> None:
            """`/doc/{slug}/` — metadata, a page-range list and a jump box."""
            doc = index.get(slug)
            if doc is None:
                self._not_found_html(f"unknown document {slug!r}")
                return
            total = doc.get("page_count") or 0
            name = doc.get("filename") or slug
            variants = _document_variants(cfg, doc)
            max_attr = f' max="{total}"' if total else ''
            bits = [
                f'<header><strong>{html.escape(name)}</strong>'
                f'<span class="muted">{total or "?"} pages</span></header>',
                '<main class="plain"><div class="wrap">',
                f'<p><a class="btn" href="{_viewer_url(slug, 1)}">▶ Start reading at p. 1</a></p>',
                f'<p class="muted">{html.escape(doc.get("rel_path") or "")}<br>'
                f'sha {html.escape((doc.get("sha256") or "")[:12])} · '
                f'{html.escape(doc.get("status") or "?")}</p>',
                '<h2>Pages</h2>',
                f'<p class="ranges">{_range_links(slug, total)}</p>',
                f'<form action="{_go_url(slug)}" method="get"><label>Go to page '
                f'<input type="number" name="page" min="1"{max_attr} required> '
                '<button>go</button></label></form>',
            ]
            if variants:
                bits.append('<h2>Variants</h2>'
                            f'<p class="muted">{html.escape(", ".join(variants))}</p>')
            bits.append('</div></main>')
            bits.append('<footer><span class="muted">pha serve</span>'
                        '<span class="spacer"></span>'
                        f'<a href="{_meta_url(slug)}">meta.json</a></footer>')
            self._html(HTTPStatus.OK, _html_page(name, "".join(bits)))

        def _go(self, slug: str, query: str) -> None:
            """`/doc/{slug}/go?page=N` — the JS-free jump box, redirects to the viewer."""
            doc = index.get(slug)
            if doc is None:
                self._not_found_html(f"unknown document {slug!r}")
                return
            values = parse_qs(query).get("page") or []
            try:
                page = int(values[0])
            except (IndexError, ValueError):
                self._not_found_html("page must be a number")
                return
            total = doc.get("page_count") or 0
            if page < 1 or (total and page > total):
                self._not_found_html(_out_of_range(page, total))
                return
            self._redirect(_viewer_url(slug, page))

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
