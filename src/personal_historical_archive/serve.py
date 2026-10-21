"""``pha serve`` — a read-only HTTP endpoint with stable page addresses.

The URL for a page is permanent::

    http://127.0.0.1:8765/doc/<slug>/p<NNN>

because it names the *slug* (derived from the dropbox-relative path), not the
document id, the dated library folder or the content-addressed render directory.
The HTML viewer shows the page render together with the stored raw
transcription and, when one is available, the effective edited reading.  The
bare JPEG stays at ``/doc/<slug>/p<NNN>.jpg`` for inline embeds.
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
from . import db
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
main.reader{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(22rem,.8fr);
  align-items:start;justify-content:stretch;gap:1rem;width:100%;max-width:96rem;
  margin:0 auto}
.reader-media{display:flex;justify-content:center;align-items:flex-start;min-width:0}
.reader-text{min-width:0}
.reading{background:#fff;border:1px solid #e4e1da;border-radius:10px;
  padding:.85rem 1rem;margin-bottom:1rem}
.reading:last-child{margin-bottom:0}
.reading h2{margin:.1rem 0 .15rem}
.provenance{margin:.15rem 0 .6rem;color:#777;font-size:13px}
.badge{display:inline-block;margin-left:.35rem;padding:.02rem .35rem;
  border:1px solid #d8d2c5;border-radius:999px;font-size:11px;
  color:#6d6658;vertical-align:.08em}
pre.transcript{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;
  font:13.5px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  max-height:calc(100vh - 12rem);overflow:auto;margin:.4rem 0 0;
  padding:.65rem .75rem;background:#fbfaf7;border:1px solid #eee;
  border-radius:7px}
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
  .reading{background:#1d1f22;border-color:#333}
  .provenance{color:#9a9a9a}
  .badge{border-color:#444;color:#b8b1a4}
  pre.transcript{background:#17181a;border-color:#333}
  .notice{border-color:#4a4335;background:#2a2620}
}
@media (max-width:900px){
  main.reader{display:block;padding:.75rem}
  .reader-media{margin-bottom:.9rem}
  img.page{max-height:70vh}
  pre.transcript{max-height:none}
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


def _provenance(label: str | None, *, pinned: bool = False,
                reviewed: bool = False) -> str:
    """A safe, compact provenance line for one reading."""
    if not label:
        return ""
    badges = ""
    if reviewed:
        badges += '<span class="badge">reviewed</span>'
    if pinned:
        badges += '<span class="badge">pinned</span>'
    return f'<p class="provenance">{html.escape(label)}{badges}</p>'


def _reading_panel(title: str, text: str | None, provenance: str,
                   empty_message: str) -> str:
    """One escaped, plain-text reading panel.

    Transcriptions are displayed verbatim: no Markdown, HTML or front matter is
    interpreted.  ``empty_message`` is shown instead of a blank code block.
    """
    if text and text.strip():
        body = (f'<pre class="transcript" tabindex="0">'
                f'{html.escape(text)}</pre>')
    else:
        body = f'<p class="muted">{html.escape(empty_message)}</p>'
    return (f'<section class="reading"><h2>{html.escape(title)}</h2>'
            f'{provenance}{body}</section>')


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
                try:
                    rows = conn.execute(
                        "SELECT d.id, d.filename, d.path, d.dir_path, d.sha256, d.page_count, "
                        "d.status, d.created_at, d.palaeographer, d.palaeographer_model, "
                        "d.editor, d.editor_model, "
                        "b.citation AS reference, "
                        "b.parsed_json AS reference_json, b.source_format AS reference_format, "
                        "b.record_origin AS reference_origin "
                        "FROM documents d LEFT JOIN document_bibliography b "
                        "ON b.document_id = d.id"
                    ).fetchall()
                except sqlite3.Error:
                    # A database written before bibliographic sidecars existed:
                    # serve everything else, just without references.
                    rows = conn.execute(
                        "SELECT id, filename, path, dir_path, sha256, page_count, status, "
                        "created_at, palaeographer, palaeographer_model, editor, editor_model "
                        "FROM documents"
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
    """The document's variant directory names (cheap: one directory listing).

    ``edited-X`` is never listed beside ``edited-X@Y``: they are one variant, so
    the overview must not show a reading twice (see
    ``enhancements/pha-duplicate-edited-variants-bug-report.md``).
    """
    from . import addresses
    from .ingest import _library_doc_dir

    doc_dir = _library_doc_dir(cfg, doc)
    if doc_dir is None or not doc_dir.is_dir():
        return []
    names = [d.name for d in doc_dir.iterdir() if d.is_dir()]
    current = {
        "edited": (doc.get("editor"), doc.get("editor_model")),
        "transcription": (doc.get("palaeographer"), doc.get("palaeographer_model")),
    }
    return sorted(addresses.collapse_variant_aliases(names, current=current))


def _reference_of(doc: dict) -> str | None:
    """The stored bibliographic reference for a document, if it has one."""
    return doc.get("reference") or None


def _reference_verified(doc: dict) -> bool | None:
    """False when the reference was machine-drafted rather than human-supplied.

    Serve shows the marker next to a reference instead of presenting a
    machine-guessed imprint as fact. The rule itself lives in `bibliography`.
    """
    from .bibliography import verified_from_origin

    if not _reference_of(doc):
        return None
    return verified_from_origin(doc.get("reference_origin"))


def _reference_agent_drafted(doc: dict) -> bool:
    """Whether this reference must be badged in the page itself.

    Only a model-drafted reference is badged; one imported from the owner's
    library is reported by `pha bib` instead of warning on every page.
    """
    from .bibliography import agent_drafted_from_origin

    return agent_drafted_from_origin(doc.get("reference_origin"))


def _reference_record(doc: dict) -> dict | None:
    """The normalised record behind the reference, for API consumers."""
    raw = doc.get("reference_json")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _reference_html(doc: dict) -> str:
    """The reference as a small HTML block, with the model-drafted badge."""
    ref = _reference_of(doc)
    if not ref:
        return ""
    marker = ""
    if _reference_agent_drafted(doc):
        marker = ' <span class="muted">[unverified reference]</span>'
    return f'<p class="muted">{html.escape(ref)}{marker}</p>'


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

        def _page_reading(self, doc: dict, page: int) -> tuple[dict | None, str | None]:
            """Load one page's raw transcription and effective edited reading.

            Returns ``(data, error)``.  ``data`` is a plain dict so the
            connection can be closed before rendering; a text-read failure is
            recorded as ``text_error`` and must not take the image/navigation
            down with it.  The DB is the same authority as `pha page`: library
            files that a human edited but did not yet import are deliberately
            not consulted here.
            """
            try:
                conn, _ = _connect_ro(cfg.db_path)
                try:
                    row = conn.execute(
                        "SELECT * FROM pages WHERE document_id=? AND page_no=?",
                        (doc.get("id"), page)).fetchone()
                    if row is None:
                        return {
                            "exists": False,
                            "source_name": None,
                            "raw_text": None,
                            "status": None,
                            "raw_label": None,
                            "raw_pinned": False,
                            "raw_reviewed": False,
                            "edited": None,
                            "text_error": None,
                        }, None

                    raw = db.row_get(row, "raw_text")
                    pal = (db.row_get(row, "palaeographer")
                           or doc.get("palaeographer"))
                    pal_model = (db.row_get(row, "palaeographer_model")
                                 or doc.get("palaeographer_model"))
                    raw_label = None
                    if pal:
                        variant = f"transcription-{pal}"
                        if pal_model:
                            variant += f"@{pal_model}"
                        raw_label = addresses.variant_label(variant)

                    data = {
                        "exists": True,
                        "source_name": db.row_get(row, "source_name"),
                        "raw_text": raw,
                        "status": db.row_get(row, "status"),
                        "page_error": db.row_get(row, "error"),
                        "raw_label": raw_label,
                        "raw_pinned": bool(db.row_get(row, "pinned_at")),
                        "raw_reviewed": bool(db.row_get(row, "reviewed_at")),
                        "edited": None,
                        "text_error": None,
                    }

                    # The effective edit is the same reading `pha page --edited`,
                    # `pha cite --edited` and the MCP tools expose.  A waiting
                    # row is not a reading: never present a `*waiting*` stub.
                    try:
                        edit = db.effective_edit_for_page(
                            conn, row["id"], doc.get("editor"))
                        if edit is not None and db.row_get(edit, "status") == "done":
                            edit_text = edit["text"] or ""
                            if edit_text.strip():
                                editor = edit["editor"]
                                edit_model = db.row_get(edit, "editor_model")
                                if not edit_model and editor == doc.get("editor"):
                                    edit_model = doc.get("editor_model")
                                variant = f"edited-{editor}"
                                if edit_model:
                                    variant += f"@{edit_model}"
                                data["edited"] = {
                                    "editor": editor,
                                    "model": edit_model,
                                    "text": edit_text,
                                    "label": addresses.variant_label(variant),
                                    "pinned": bool(db.row_get(edit, "pinned_at")),
                                    "reviewed": bool(db.row_get(edit, "reviewed_at")),
                                }
                    except sqlite3.Error as exc:
                        # A legacy or transient page_edits read must not hide
                        # the raw transcription or break navigation.
                        data["text_error"] = str(exc)
                    return data, None
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                return None, str(exc)

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
                # The document's bibliographic reference, when it has a sidecar.
                "reference": _reference_of(doc),
                "reference_verified": _reference_verified(doc),
                "bibliography": _reference_record(doc),
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
            """The reading surface for one page: image + text + navigation.

            Shows the render, the stored raw transcription and the effective
            edited reading when one is available.  Works with JavaScript
            disabled (plain links and a GET form); JS only adds arrow keys and
            neighbour-image prefetch.  The DB is the authority for the text,
            exactly as for `pha page` and the MCP tools.
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

            reading, read_error = self._page_reading(doc, page)
            if read_error and not quiet:
                sys.stderr.write(
                    f"pha serve: warning: page text read failed for {slug} "
                    f"p{page}: {read_error}\n")
            source_name = reading.get("source_name") if reading else None
            render = addresses.render_path(cfg, doc, page, source_name)
            if render is None or not render.is_file():
                # Navigation must never dead-end: say what is missing and keep
                # the controls (contrast /p{N}.jpg, which 404s).
                media = ('<div class="notice">'
                         f'<p><strong>No render for page {page}.</strong></p>'
                         '<p class="muted">This page has no image in the archive. '
                         'The prev/next controls still work.</p></div>')
            else:
                media = (f'<img class="page" src="{_jpg_url(slug, page)}" '
                         f'alt="{html.escape(name)} — page {page}">')

            if read_error:
                text_panels = (
                    '<section class="reading"><h2>Page text</h2>'
                    '<p class="muted">Page text is temporarily unavailable.</p>'
                    '</section>')
            else:
                status = reading.get("status") if reading else None
                if not reading or not reading.get("exists"):
                    empty = f"No page record is stored for page {page}."
                elif status and status != "done":
                    empty = (f"No transcription text is stored for this page "
                             f"(status: {status}).")
                else:
                    empty = "This page is marked done but has no transcription text."
                panels = [_reading_panel(
                    "Transcription (raw)", reading.get("raw_text") if reading else None,
                    _provenance(reading.get("raw_label") if reading else None,
                                pinned=bool(reading.get("raw_pinned")) if reading else False,
                                reviewed=bool(reading.get("raw_reviewed")) if reading else False),
                    empty)]
                edited = reading.get("edited") if reading else None
                if edited:
                    panels.append(_reading_panel(
                        "Edited reading", edited["text"],
                        _provenance(edited["label"], pinned=edited["pinned"],
                                    reviewed=edited["reviewed"]),
                        ""))
                if reading and reading.get("text_error"):
                    panels.append('<p class="muted">The edited reading could not '
                                  'be loaded.</p>')
                text_panels = "".join(panels)

            main = (f'<main class="reader"><section class="reader-media">{media}</section>'
                    f'<section class="reader-text">{text_panels}</section></main>')

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
                       "if(t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA'||"
                       "(t.closest&&t.closest('pre,[data-keys-off]'))))return;"
                       "if(e.key==='ArrowLeft'||e.key==='k'){if(P)location.href=P}"
                       "else if(e.key==='ArrowRight'||e.key==='j'){if(N)location.href=N}"
                       "});</script>")
            self._html(HTTPStatus.OK, _html_page(
                f"{name} — p. {page}", header + main + _reference_html(doc) + footer,
                head_extra="".join(pre)))

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
                _reference_html(doc),
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
    print(f"pha serve: read-only page viewer on {base}")
    print(f"  health:  {base}/health")
    print(f"  example: {base}/doc/<slug>/p001   (image + transcription + edited reading)")
    print(f"  image:   {base}/doc/<slug>/p001.jpg")
    print("  Ctrl-C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\npha serve: stopped")
    finally:
        srv.server_close()
