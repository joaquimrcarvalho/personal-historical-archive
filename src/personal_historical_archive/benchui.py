"""``pha bench serve`` — the benchmark workbench (P2 read-only + P3 authoring).

A local, loopback-only app for the one job the CLI cannot do well: reading the
page image beside the stacked readings and writing the human-corrected gold
entry by entry.

It is **not** `pha serve` and **not** PHA View. `pha serve` is read-only by
contract and shares its viewer code with the future public site; PHA View is a
chat surface. This app owns the one writable surface of the benchmark pipeline
(BENCHMARK_TOOL_PLAN.md §3, §6), and its write allow-list is exactly:

    human/<page>.md                  the gold, with front matter + readings_hash
    reference/<page>.md              optional, generated (never hand-edited)
    comparison/<page>.md             optional, generated

It never writes readings, library files or the database. Every save is guarded:
the file must not have changed since it was loaded, the entries must be
contiguous and non-empty, a reviewer must be identified, and the structural
checks that `pha bench verify` applies run in-process first.

Served with the standard library (`http.server`) and the shared `viewer.py`
shell: no Node, no build step, no new dependency, no network.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import bench
from .config import Config
from .viewer import CSS, esc, html_page

MAX_FORM_BYTES = 8 * 1024 * 1024
ENTRY_RE = re.compile(r"^\[(\d+)\]\s*(.*)$")
PAGE_QS = re.compile(r"^[A-Za-z0-9._-]+$")

BENCH_CSS = """\
/* The workbench hardcodes light surfaces (white cards, paper-white panels), so
   it forces a light scheme: on a dark-mode desktop the shared viewer CSS would
   otherwise put light text on those white cards. It also overrides the shared
   inline form rule — the save form is a block container, not a toolbar. */
:root{color-scheme:light}
body{background:#faf9f7;color:#1b1b1b}
a{color:#0b57d0}
.bench-main form{display:block;gap:0}
button{background:#f7f6f3;color:#1b1b1b;border:1px solid #bbb;border-radius:4px;
  padding:.18rem .5rem;cursor:pointer;font:inherit;font-size:12px}
.entry textarea,.savebar input[type=text]{background:#fff;color:#1b1b1b}
.reading-line .txt{color:#1b1b1b}
.entry,.card,.pagecard,.savebar,.badge2{color:#1b1b1b}
.bench-main{display:block;padding:.75rem 1rem;max-width:none;margin:0;width:100%}
.bench-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:.8rem}
.card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:.7rem .8rem;color:inherit;
  text-decoration:none;display:block}
.card:hover{border-color:#999}
.card h3{margin:.1rem 0 .3rem;font-size:15px}
.muted{color:#666;font-size:13px}
.badges{display:flex;gap:.3rem;flex-wrap:wrap;margin:.25rem 0}
.badge2{font-size:11px;border:1px solid #ccc;border-radius:999px;padding:0 .45rem;background:#f7f6f3}
.badge2.gold{background:#e8f5e9;border-color:#a5d6a7}
.badge2.warn{background:#fff4e5;border-color:#ffcc80}
.badge2.draft{background:#e8eefc;border-color:#a9c1f5}
.badge2.bad{background:#fdecea;border-color:#f5c6c2}
.bench-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:.5rem}
.pagecard{background:#fff;border:1px solid #ddd;border-radius:6px;padding:.35rem;text-align:center;
  color:inherit;text-decoration:none;font-size:12px}
.pagecard img{width:100%;height:110px;object-fit:cover;border-radius:4px;background:#eee}
.workbench{display:grid;grid-template-columns:minmax(280px,var(--media-w,44%)) 12px minmax(320px,1fr);
  gap:0 1rem;align-items:start;--media-w:44%}
.workbench .media{position:sticky;top:.5rem;min-width:0;align-self:start;
  max-height:calc(100vh - 1.5rem);overflow:auto;overscroll-behavior:contain;
  scrollbar-gutter:stable}
.workbench .media img{width:100%;height:auto;display:block;border:1px solid #ccc;
  border-radius:4px;background:#fff}
.workbench .editpane{min-width:0}
.divider{grid-column:2;align-self:stretch;min-height:70vh;position:relative;cursor:col-resize;
  touch-action:none;border:0;background:transparent;padding:0}
.divider::before{content:"";position:absolute;left:5px;top:0;bottom:0;width:2px;
  background:#c9c5bc;border-radius:2px}
.divider:hover::before,.divider:focus-visible::before{background:#5f5c55;width:4px;left:4px}
.divider::after{content:"⋮";position:absolute;left:-1px;top:50%;transform:translateY(-50%);
  color:#8a857b;font-size:14px;line-height:1;padding:.35rem .12rem;background:#faf9f7;
  border:1px solid #d8d4cb;border-radius:6px}
.divider:focus-visible{outline:2px solid #0b57d0;outline-offset:2px;border-radius:4px}
.readings{margin:.2rem 0 .4rem}
.reading-line{display:flex;gap:.5rem;align-items:flex-start;padding:.2rem 0;border-top:1px dashed #eee}
.reading-line .n{color:#888;min-width:1.1rem}
.reading-line .txt{white-space:pre-wrap;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:12.5px;flex:1}
.reading-line button{font-size:11px;padding:.05rem .4rem;cursor:pointer}
.entry{background:#fff;border:1px solid #ddd;border-radius:6px;padding:.5rem .6rem;margin:.5rem 0}
.entry h4{margin:.1rem 0 .2rem;font-size:13px;color:#555}
.entry .donebox{float:right;font-weight:normal;font-size:12px;color:#666;cursor:pointer}
.entry.done{border-color:#a5d6a7;background:#fbfdf9}
.entry textarea{width:100%;min-height:2.6rem;font:12.5px/1.35 ui-monospace,SFMono-Regular,Menlo,monospace;
  padding:.35rem;border:1px solid #ccc;border-radius:4px;resize:vertical}
.entry textarea.dirty{border-color:#e0a800;background:#fffdf5}
.savebar{position:sticky;bottom:0;background:#f1efe9;border-top:1px solid #ddd;padding:.5rem .6rem;
  display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin-top:1rem}
.savebar input[type=text]{padding:.25rem .4rem}
.ok{background:#e8f5e9;border:1px solid #a5d6a7;padding:.4rem .6rem;border-radius:4px}
.err{background:#fdecea;border:1px solid #f5c6c2;padding:.4rem .6rem;border-radius:4px}
.warnbox{background:#fff4e5;border:1px solid #ffcc80;padding:.4rem .6rem;border-radius:4px}
.entry .badge2.warn{margin-left:.3rem}
.rules{margin:.3rem 0 .6rem;background:#fff;border:1px solid #e3e1db;border-radius:6px;padding:.45rem .7rem}
.rules summary{cursor:pointer;color:#555;font-size:13px}
.rules ol{margin:.4rem 0 0;padding-left:1.3rem;columns:2;column-gap:2.2rem}
.rules li{break-inside:avoid}
.rules li{margin:.4rem 0;font-size:13px}
.rules .explain{color:#333;margin-top:.1rem}
.rules .example{font-family:ui-monospace,Menlo,monospace;font-size:12px;background:#f4f2ec;
  padding:.12rem .35rem;border-radius:3px;display:inline-block;margin-top:.2rem}
@media (max-width:1100px){.rules ol{columns:1}}
.verify pre{white-space:pre-wrap;font-size:12px}
.kbd{font-family:ui-monospace,Menlo,monospace;font-size:11px;background:#eee;padding:0 .25rem;border-radius:3px}
@media (max-width:850px){.workbench{grid-template-columns:1fr}
  .workbench .media{position:static;max-height:none;overflow:visible}
  .divider{display:none}}
"""


class SaveError(Exception):
    """A refused save; carries the HTTP status to show."""

    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- helpers


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _corpus_by_rel(wb: "Workbench", rel: str) -> bench.Corpus:
    root = wb.root
    p = (root / rel)
    if not str(p.resolve()).startswith(str(root.resolve()) + os.sep):
        raise SaveError("corpus path escapes the bench root", HTTPStatus.BAD_REQUEST)
    if not p.is_dir():
        raise SaveError(f"no such corpus: {rel}", HTTPStatus.NOT_FOUND)
    corpus = bench.load_corpus(p)
    if not corpus.readers:
        raise SaveError(f"no readers in {rel}", HTTPStatus.NOT_FOUND)
    return corpus


def _rel(wb: "Workbench", corpus: bench.Corpus) -> str:
    return str(corpus.root.relative_to(wb.root))


REVIEW_DIR = ".review"


def review_path(corpus: bench.Corpus, page: str) -> Path:
    """The sidecar that records which entries were ticked as reviewed."""
    return corpus.root / REVIEW_DIR / f"{page}.json"


def read_review(corpus: bench.Corpus, page: str) -> list[int]:
    """Stored ticks, valid only for the current readings.

    A sidecar whose readings_hash no longer matches is ignored: a re-read may
    have changed the entries, so old ticks are not trustworthy.
    """
    path = review_path(corpus, page)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    if str(data.get("readings_hash") or "") != corpus.readings_hash(page):
        return []
    out: set[int] = set()
    for item in data.get("reviewed") or []:
        try:
            n = int(item)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out.add(n)
    return sorted(out)


def write_review(corpus: bench.Corpus, page: str, reviewed: list[int]) -> Path:
    path = review_path(corpus, page)
    path.parent.mkdir(parents=True, exist_ok=True)
    nums = sorted({int(n) for n in reviewed if int(n) > 0})
    data = {
        "page": page,
        "readings_hash": corpus.readings_hash(page),
        "reviewed": nums,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "updated_by": corpus.gold_reviewer or "",
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def clear_review(corpus: bench.Corpus, page: str) -> None:
    try:
        review_path(corpus, page).unlink()
    except FileNotFoundError:
        pass


def _link(path: str, **params) -> str:
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    return f"{path}?{q}" if q else path


# --------------------------------------------------------------------------- images


def image_path(wb: "Workbench", corpus: bench.Corpus, page: str) -> Path | None:
    """The page image: corpus images/, then the declared source document.

    A directory of images is read in place; a PDF page is rendered on demand
    into ``<archive>/.pha-test/bench-cache/`` (so the first view pays once), never into
    the corpus itself — publishing materialises ``images/`` at export time.
    """
    images = corpus.images_dir
    if images is not None:
        for ext in bench.IMAGE_EXTS:
            p = images / f"{page}{ext}"
            if p.is_file():
                return p
    doc = corpus.document
    if not doc:
        return None
    p = Path(doc)
    if not p.is_absolute():
        p = wb.cfg.data / p
    if p.is_file() and p.suffix.lower() in bench.IMAGE_EXTS:
        return p if p.stem == page else None
    if p.is_dir():
        for f in sorted(p.rglob("*")):
            if f.is_file() and f.stem == page and f.suffix.lower() in bench.IMAGE_EXTS:
                return f
        return None
    if not p.is_file():
        return None
    page_no = bench.pha_page_for(corpus, page, wb.cfg.data)
    if page_no is None:
        return None
    key = hashlib.sha1(str(p).encode("utf-8")).hexdigest()[:12]
    cache = wb.cfg.data / ".pha-test" / "bench-cache" / key
    out = cache / f"p{page_no:03d}.jpg"
    try:
        fresh = out.is_file() and out.stat().st_mtime >= p.stat().st_mtime
    except OSError:
        fresh = False
    if fresh:
        return out
    from .extract import render_document

    try:
        made = render_document(p, cache, dpi=wb.cfg.render_dpi,
                               max_px=wb.cfg.max_image_px,
                               jpeg_quality=wb.cfg.jpeg_quality,
                               pages={page_no})
    except Exception:  # noqa: BLE001 - a render failure must not kill the page
        return None
    return made[0] if made else None


def _content_type(path: Path) -> str:
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".tif": "image/tiff", ".tiff": "image/tiff",
            ".webp": "image/webp"}.get(path.suffix.lower(), "application/octet-stream")


# --------------------------------------------------------------------------- context


class Workbench:
    """Per-process state: the bench root, the corpus list, the form token."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.root = bench.bench_root(cfg)
        self.token = secrets.token_urlsafe(18)

    def corpora(self) -> list[bench.Corpus]:
        return bench.discover(self.root)

    def corpus(self, rel: str) -> bench.Corpus:
        return _corpus_by_rel(self, rel)

    def page(self, rel: str, page: str) -> bench.Corpus:
        if not PAGE_QS.match(page or ""):
            raise SaveError("bad page id", HTTPStatus.BAD_REQUEST)
        corpus = self.corpus(rel)
        if page not in corpus.page_ids():
            raise SaveError(f"no page {page!r} in this corpus", HTTPStatus.NOT_FOUND)
        return corpus

    # -- page model -------------------------------------------------------
    def page_data(self, corpus: bench.Corpus, page: str) -> dict:
        comp = corpus.comparison_path(page)
        comp_text = comp.read_text(encoding="utf-8") if comp.is_file() else ""
        entries = bench.parse_comparison_entries(comp_text) if comp_text else []
        gold_path = corpus.gold_path(page)
        gold_text = gold_path.read_text(encoding="utf-8") if gold_path.is_file() else ""
        ref_path = corpus.reference_path(page)
        ref_text = ref_path.read_text(encoding="utf-8") if ref_path.is_file() else ""
        meta = corpus.gold_meta(page)
        status = str(meta.get("status") or "")
        is_gold = status == bench.STATUS_GOLD
        is_draft = status == bench.STATUS_DRAFT
        pending_copy = bool(meta) and not is_gold and not is_draft
        gold_entries = (bench.entry_texts(gold_text)
                        if (gold_text and (is_gold or is_draft)) else {})
        base_source = "gold" if is_gold else "draft"
        ref_entries = bench.entry_texts(ref_text) if ref_text else {}
        rows = []
        seen: set[int] = set()
        for e in entries:
            seen.add(e.num)
            if e.kind == "all":
                readings = [(0, e.text)]
            else:
                readings = list(enumerate(e.readings, 1))
            text = gold_entries.get(e.num)
            source = base_source
            if text is None:
                text = ref_entries.get(e.num)
                source = "reference" if text is not None else ""
            if text is None:
                text = bench.merge_readings(e.readings) if e.kind == "diff" else e.text
                source = "merge"
            rows.append({"num": e.num, "readings": readings, "text": text,
                         "source": source,
                         "flags": bench.entry_flags([t for _, t in readings])})
        for num in sorted(set(gold_entries) - seen):
            rows.append({"num": num, "readings": [], "text": gold_entries[num],
                         "source": base_source, "flags": []})
        return {
            "entries": entries,
            "rows": rows,
            "gold_text": gold_text,
            "gold_entries": gold_entries,
            "ref_text": ref_text,
            "comp_text": comp_text,
            "base_sha": _sha256(gold_path.read_bytes()) if gold_path.is_file() else "",
            "meta": meta,
            "is_gold": is_gold,
            "is_draft": is_draft,
            "pending_copy": pending_copy,
            "flagged": sum(1 for r in rows if r["flags"]),
            "editing_rules": corpus.editing_rules,
            "editing_rule_notes": corpus.editing_rule_notes,
            "status": bench.page_status(corpus, page),
            "readers": [r.dir for r in corpus.readers],
        }


# --------------------------------------------------------------------------- render


def _badges(status: bench.PageStatus) -> str:
    out = []
    if status.gold:
        out.append('<span class="badge2 gold">gold</span>')
    elif status.draft:
        out.append('<span class="badge2 draft">draft</span>')
    elif status.gold_status:
        out.append(f'<span class="badge2">{esc(status.gold_status)}</span>')
    if status.stale:
        out.append('<span class="badge2 warn">stale</span>')
    if status.missing:
        out.append(f'<span class="badge2 bad">{len(status.missing)} missing</span>')
    if not status.compared and status.entries:
        out.append('<span class="badge2 warn" title="the comparison file predates at '
                   'least one reading; rebuild it only if the readings were re-run '
                   'after the comparison was made">comparison older</span>')
    return '<span class="badges">' + "".join(out) + "</span>" if out else ""


def _head(title: str, crumbs: str = "") -> str:
    return ("<header><strong>pha bench</strong>"
            + (f'<span class="muted">{crumbs}</span>' if crumbs else "")
            + '<span class="spacer"></span>'
            + '<span class="muted">local workbench — writes only the gold</span>'
            "</header>")


def render_index(wb: Workbench) -> bytes:
    corpora = wb.corpora()
    cards = []
    for c in corpora:
        st = bench.corpus_status(c)
        rel = _rel(wb, c)
        cards.append(
            f'<a class="card" href="{_link("/corpus", c=rel)}">'
            f'<h3>{esc(c.title)}</h3>'
            f'<div class="muted">{esc(rel)}</div>'
            '<div class="badges">'
            f'<span class="badge2">{len(c.readers)} readers</span>'
            f'<span class="badge2">{st["pages"]} pages</span>'
            f'<span class="badge2 gold">{st["gold"]} gold</span>'
            + (f'<span class="badge2 warn">{st["stale"]} stale</span>' if st["stale"] else "")
            + "</div></a>")
    pending = ""
    try:
        cands = bench.candidates(wb.root)
    except OSError:
        cands = []
    if cands:
        items = "".join(
            f'<li><code>{esc(str(p.relative_to(wb.root)))}</code> — '
            f'<code>pha bench init {esc(str(p))}</code></li>' for p in cands)
        pending = (f'<h2>Unconfigured</h2><p class="muted">Corpus-shaped folders '
                   f'without <code>bench.yaml</code>:</p><ul>{items}</ul>')
    body = (f'{_head("corpora")}<main class="bench-main">'
            f'<h1>Benchmark corpora</h1>'
            f'<div class="muted">bench root: <code>{esc(str(wb.root))}</code></div>'
            f'<div class="bench-cards" style="margin-top:1rem">{"".join(cards)}</div>'
            f'{pending}</main>')
    return html_page("pha bench", body, f"<style>{BENCH_CSS}</style>")


def render_corpus(wb: Workbench, corpus: bench.Corpus, verify: bool = False,
                  notice: str = "") -> bytes:
    rel = _rel(wb, corpus)
    pages = corpus.page_ids()
    cards = []
    for page in pages:
        st = bench.page_status(corpus, page)
        badges = _badges(st)
        cards.append(
            f'<a class="pagecard" href="{_link("/page", c=rel, p=page)}">'
            f'<img loading="lazy" src="{_link("/img", c=rel, p=page)}" alt="">'
            f'<div><strong>{esc(page)}</strong></div>{badges}</a>')
    verify_html = ""
    if verify:
        reps = bench.verify_corpus(corpus, readings=len(corpus.readers) or None,
                                   include_gold=True)
        lines = []
        for r in reps:
            name = (str(r.path.relative_to(corpus.root))
                    if r.path.is_relative_to(corpus.root) else str(r.path))
            mark = "ok" if r.ok else "FAIL"
            lines.append(f"{mark:4s} {name}")
            lines += [f"     - {p}" for p in r.problems]
            lines += [f"     warn: {w}" for w in r.warnings]
        verify_html = ('<h2>Verification</h2><div class="verify"><pre>'
                       + esc("\n".join(lines)) + "</pre></div>")
    st = bench.corpus_status(corpus)
    body = (f'{_head(corpus.title, " · " + esc(rel))}'
            f'<main class="bench-main">'
            f'<p><a href="/">← all corpora</a></p>'
            f'<h1>{esc(corpus.title)}</h1>'
            f'<div class="muted">{esc(str(corpus.root))}</div>'
            + (f'<p class="ok">{esc(notice)}</p>' if notice else "")
            + f'<p>readers: {esc(", ".join(r.dir for r in corpus.readers)) or "(none)"} · '
              f'pages: {st["pages"]} · compared: {st["compared"]} · '
              f'gold: {st["gold"]} · drafts: {st["drafts"]} · '
              f'stale: {st["stale"]}</p>'
            f'<div class="savebar" style="position:static;margin:.5rem 0">'
            f'<a class="badge2" href="{_link("/corpus", c=rel, verify=1)}">run verification</a>'
            f'<form method="post" action="/build" style="display:inline">'
            f'<input type="hidden" name="token" value="{wb.token}">'
            f'<input type="hidden" name="c" value="{esc(rel)}">'
            f'<button name="what" value="comparison">rebuild comparison</button>'
            f'<button name="what" value="reference">rebuild reference</button>'
            f'<button name="what" value="score">score now</button>'
            f'</form>'
            f'<span class="muted">Writes <code>comparison/</code> and '
            f'<code>reference/</code>, and recomputes <code>scores/</code> — '
            f'never the gold.</span></div>'
            f'<div class="bench-grid">{"".join(cards)}</div>'
            f'{_scores_html(corpus)}'
            f'{verify_html}</main>')
    return html_page(f"pha bench — {corpus.title}", body,
                     f"<style>{BENCH_CSS}</style>")


def _fmt(x, pct: bool = False) -> str:
    if x is None:
        return "—"
    return f"{x:.1%}" if pct else f"{x:.3f}"


def _scores_html(corpus: bench.Corpus) -> str:
    path = corpus.root / "scores" / "results.json"
    if not path.is_file():
        return ('<h2>Scores</h2><p class="muted">No scores yet — run '
                '<code>pha bench score</code> (or the button above) once a page '
                'has a valid gold.</p>')
    try:
        res = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return '<h2>Scores</h2><p class="err">scores/results.json is unreadable.</p>'
    rows = []
    for key, a in (res.get("aggregates") or {}).items():
        rows.append(
            "<tr><td>" + esc(key) + "</td>"
            f"<td>{a.get('pages')}</td>"
            f"<td>{_fmt(a.get('cer_diplomatic'))}</td>"
            f"<td>{_fmt(a.get('cer_modern'))}</td>"
            f"<td>{_fmt(a.get('entry_exact'), True)}</td>"
            f"<td>{_fmt(a.get('names'), True)}</td>"
            f"<td>{'partial' if a.get('partial') else ''}</td></tr>")
    caveats = "".join(f"<li>{esc(c)}</li>" for c in res.get("caveats") or [])
    return ('<h2>Scores <span class="muted">(' + esc(str(res.get("generated_at", "")))
            + ')</span></h2>'
            '<div class="verify"><table><thead><tr><th>reader</th><th>pages</th>'
            '<th>CER dipl.</th><th>CER mod.</th><th>entry</th><th>names</th>'
            '<th></th></tr></thead><tbody>' + "".join(rows)
            + '</tbody></table></div>'
            f'<ul class="muted">{caveats}</ul>')


def _readings_html(row: dict) -> str:
    if not row["readings"]:
        return '<p class="muted">no comparison entry for this number</p>'
    out = []
    for k, text in row["readings"]:
        label = "all" if k == 0 else str(k)
        out.append(
            f'<div class="reading-line"><span class="n">{label}</span>'
            f'<span class="txt">{esc(text)}</span>'
            f'<button type="button" data-put="{row["num"]}" data-k="{k}" '
            f'title="use this reading for the gold entry">use</button></div>')
    return "".join(out)


def render_page(wb: Workbench, corpus: bench.Corpus, page: str,
                error: str = "", saved: bool = False,
                saved_status: str = "", reviewed: list[int] | None = None) -> bytes:
    data = wb.page_data(corpus, page)
    rel = _rel(wb, corpus)
    ids = corpus.page_ids()
    i = ids.index(page) if page in ids else 0
    prev_p = ids[i - 1] if i > 0 else None
    next_p = ids[i + 1] if i + 1 < len(ids) else None
    if not data["comp_text"]:
        body = (f'{_head(corpus.title, " · " + esc(page))}'
                f'<main class="bench-main"><p><a href="{_link("/corpus", c=rel)}">'
                f'← {esc(corpus.title)}</a></p>'
                f'<h1>{esc(page)}</h1>'
                '<p class="err">No comparison for this page yet. The workbench '
                'edits entry-aligned text, so build the comparison first '
                '(it is mechanical and can be rebuilt at any time).</p>'
                f'<form method="post" action="/build">'
                f'<input type="hidden" name="token" value="{wb.token}">'
                f'<input type="hidden" name="c" value="{esc(rel)}">'
                f'<input type="hidden" name="page" value="{esc(page)}">'
                f'<button name="what" value="comparison">build comparison</button> '
                f'<button name="what" value="reference">build reference</button>'
                f'</form></main>')
        return html_page(f"pha bench — {page}", body,
                         f"<style>{BENCH_CSS}</style>")

    image = image_path(wb, corpus, page)
    img_html = (f'<img src="{_link("/img", c=rel, p=page)}" alt="page {esc(page)}">'
                f'<div class="muted"><a href="{_link("/img", c=rel, p=page, full=1)}" '
                f'target="_blank">open full size</a></div>') if image else (
        '<p class="muted">No image: set <code>document.path</code> in '
        '<code>bench.yaml</code> or add <code>images/</code>.</p>')
    entries_html = []
    nums = []
    reading_data = {}
    draft_click = ""
    if data["is_gold"]:
        draft_click = (' onclick="return confirm(\'This page is already validated '
                       'as gold. Saving a draft marks it draft and excludes it from '
                       'scoring until you Save gold again. Continue?\')"')
    draft_button = ('<button type="submit" name="status" value="draft"'
                    + draft_click
                    + ' title="save part-way through; excluded from scoring">'
                      'Save draft</button>')
    rules_html = ""
    notes = data.get("editing_rule_notes") or []
    if notes:
        items = []
        for n in notes:
            extra = ""
            if n.get("explain"):
                extra += f'<div class="explain">{esc(n["explain"])}</div>'
            if n.get("example"):
                extra += f'<div class="example">{esc(n["example"])}</div>'
            items.append(f'<li><strong>{esc(n["rule"])}</strong>{extra}</li>')
        rules_html = ('<details open class="rules"><summary>editing rules — how to '
                      'apply them</summary><ol>' + "".join(items) + "</ol></details>")
    elif data["editing_rules"]:
        rules_html = ("<details><summary>editing rules</summary><ul>"
                      + "".join(f"<li>{esc(r)}</li>" for r in data["editing_rules"])
                      + "</ul></details>")
    for row in data["rows"]:
        nums.append(row["num"])
        for k, text in row["readings"]:
            reading_data[f'{row["num"]}:{k}'] = text
        badge = f'<span class="badge2">{esc(row["source"])}</span>' if row["source"] else ""
        flags = row["flags"]
        if flags:
            badge += ('<span class="badge2 warn">check image: '
                      + esc(", ".join(flags)) + "</span>")
        done = (f'<label class="donebox" title="mark this entry as reviewed">'
                f'<input type="checkbox" data-done="{row["num"]}"> reviewed</label>')
        entries_html.append(
            f'<div class="entry"><h4>[{row["num"]}] {badge}{done}</h4>'
            f'<div class="readings">{_readings_html(row)}</div>'
            f'<textarea name="e{row["num"]}" spellcheck="false">{esc(row["text"])}</textarea>'
            "</div>")
    reviewer = data["meta"].get("reviewed_by") or corpus.gold_reviewer or ""
    status = data["status"]
    body = (f'{_head(corpus.title, " · " + esc(page))}'
            f'<main class="bench-main"><p>'
            f'<a href="{_link("/corpus", c=rel)}">← {esc(corpus.title)}</a>'
            + (f' · <a href="{_link("/page", c=rel, p=prev_p)}">‹ {esc(prev_p)}</a>'
               if prev_p else "")
            + (f' · <a href="{_link("/page", c=rel, p=next_p)}">{esc(next_p)} ›</a>'
               if next_p else "")
            + "</p>"
            f'<h1>{esc(page)} {_badges(status)}</h1>'
            + ('<p class="warnbox">This page has a <strong>pending copy</strong> of an '
               'older reference — it is not gold. Editing starts from '
               '<code>reference/</code>; saving replaces the copy with the reviewed '
               'gold.</p>' if data["pending_copy"] else "")
            + ('<p class="warnbox">This is a <strong>draft</strong> saved part-way '
               'through the review: it is excluded from scoring. Keep going and use '
               '<strong>Save gold</strong> when the page is complete.</p>'
               if data["is_draft"] else "")
            + f'<p class="muted">{data["flagged"]} of {len(data["rows"])} entries are '
              'flagged for the image: a name with no majority, a differing number, '
              f'or an illegible mark. · <strong id="donecount">0 / {len(data["rows"])}'
              '</strong> reviewed</p>'
            + rules_html
            + (f'<p class="ok">Draft saved — your ticks are kept for when you '
               'come back.</p>' if saved and saved_status == bench.STATUS_DRAFT else
               (f'<p class="ok">Saved. The gold page is written and verified.</p>'
                if saved else ""))
            + (f'<p class="err">{esc(error)}</p>' if error else "")
            + f'<div class="workbench"><div class="media">{img_html}</div>'
              '<div class="divider" role="separator" aria-orientation="vertical" '
              'aria-label="resize the image and the text" tabindex="0" '
              'title="drag to resize · double-click to reset"></div>'
            + '<div class="editpane">'
            + f'<form method="post" action="/save" id="saveform" autocomplete="off" '
              f'data-review-key="{esc(f"{rel}|{page}|{corpus.readings_hash(page)}")}" '
              f'data-saved="{esc(saved_status)}" data-corpus="{esc(rel)}" '
              f'data-page="{esc(page)}" '
              f'data-reviewed="{",".join(str(n) for n in (reviewed or []))}">'
              f'<input type="hidden" name="token" value="{wb.token}">'
              f'<input type="hidden" name="c" value="{esc(rel)}">'
              f'<input type="hidden" name="p" value="{esc(page)}">'
              f'<input type="hidden" name="base" value="{data["base_sha"]}">'
              f'<input type="hidden" name="nums" value="{",".join(str(n) for n in nums)}">'
            + "".join(entries_html)
            + '<div class="savebar">'
              f'<label class="muted">reviewer <input type="text" name="reviewer" '
              f'value="{esc(reviewer)}" required></label>'
              '<button type="submit" name="status" value="human-corrected" data-primary>Save gold</button> '
              + draft_button
              + f'<span class="muted"><span class="kbd">⌘S</span> saves · '
              f'"use" copies a reading into its entry · readings_hash '
              f'<code>{esc(corpus.readings_hash(page))[:19]}…</code></span>'
              '</div></form>'
              f'<p class="muted">Gold data: {esc(_yaml(data["meta"])) if data["meta"] else "(none yet)"}</p>'
            "</div></div></main>"
            '<script type="application/json" id="readings-data">'
            + json.dumps(reading_data, ensure_ascii=False).replace("</", "<\\/")
            + "</script>"
            "<script>(function(){const T=JSON.parse(document.getElementById('readings-data').textContent);"
            "document.querySelectorAll('button[data-put]').forEach(b=>{"
            "b.addEventListener('click',()=>{"
            "const ta=document.querySelector('textarea[name=\"e'+b.dataset.put+'\"]');"
            "if(!ta)return;ta.value=T[b.dataset.put+':'+b.dataset.k]||'';"
            "ta.dispatchEvent(new Event('input',{bubbles:true}));"
            "ta.classList.add('dirty');ta.focus();});});})();"
            "document.addEventListener('keydown',e=>{"
            "if((e.metaKey||e.ctrlKey)&&e.key==='s'){e.preventDefault();"
            "const gb=document.querySelector('button[data-primary]');"
            "document.getElementById('saveform').requestSubmit(gb);}});"
            "</script>"
            "<script>(function(){"
            "const wb=document.querySelector('.workbench');"
            "const div=wb&&wb.querySelector('.divider');if(!div)return;"
            "const KEY='pha-bench-media';const clamp=p=>Math.min(78,Math.max(24,p));"
            "const saved=localStorage.getItem(KEY);if(saved)wb.style.setProperty('--media-w',saved);"
            "let drag=false;"
            "const pct=ev=>{const r=wb.getBoundingClientRect();"
            "const x=(ev.clientX!==undefined?ev.clientX:ev.touches[0].clientX)-r.left;"
            "return clamp(x/r.width*100);};"
            "const apply=p=>wb.style.setProperty('--media-w',p.toFixed(1)+'%');"
            "div.addEventListener('pointerdown',e=>{drag=true;div.setPointerCapture(e.pointerId);e.preventDefault();});"
            "div.addEventListener('pointermove',e=>{if(drag)apply(pct(e));});"
            "const end=()=>{if(!drag)return;drag=false;"
            "localStorage.setItem(KEY,wb.style.getPropertyValue('--media-w'));};"
            "div.addEventListener('pointerup',end);div.addEventListener('pointercancel',end);"
            "div.addEventListener('dblclick',()=>{wb.style.removeProperty('--media-w');localStorage.removeItem(KEY);});"
            "div.addEventListener('keydown',e=>{const cur=parseFloat(wb.style.getPropertyValue('--media-w'))||44;"
            "let p=null;if(e.key==='ArrowLeft')p=clamp(cur-2);if(e.key==='ArrowRight')p=clamp(cur+2);"
            "if(p!==null){e.preventDefault();apply(p);localStorage.setItem(KEY,p.toFixed(1)+'%');}});"
            "})();</script>"
            "<script>(function(){"
            "const boxes=Array.from(document.querySelectorAll('input[data-done]'));"
            "const out=document.getElementById('donecount');"
            "const form=document.getElementById('saveform');"
            "const KEY='pha-bench-review:'+(form?form.dataset.reviewKey:'');"
            "const saved=form?form.dataset.saved:'';"
            "const token=form?form.querySelector('input[name=token]').value:'';"
            "const corpus=form?form.dataset.corpus:'';"
            "const page=form?form.dataset.page:'';"
            "const ticks=(form&&form.dataset.reviewed)||'';"
            "const serverTicks=new Set(ticks?ticks.split(',').map(Number):[]);"
            "const upd=()=>{if(out)out.textContent=boxes.filter(b=>b.checked).length"
            "+' / '+boxes.length;};"
            "const read=()=>{try{return new Set((JSON.parse(localStorage.getItem(KEY)"
            "||'[]')||[]).map(Number));}catch(e){return new Set();}};"
            "let timer=null;"
            "const push=()=>{if(!form||!token)return;const body=new URLSearchParams({"
            "token:token,c:corpus,p:page,reviewed:JSON.stringify("
            "boxes.filter(b=>b.checked).map(b=>Number(b.dataset.done)))});"
            "fetch('/review',{method:'POST',body:body,keepalive:true}).catch(()=>{});};"
            "const pushSoon=()=>{clearTimeout(timer);timer=setTimeout(push,250);};"
            "const write=()=>{try{localStorage.setItem(KEY,JSON.stringify("
            "boxes.filter(b=>b.checked).map(b=>Number(b.dataset.done))));}catch(e){}"
            "pushSoon();};"
            "const apply=marks=>{boxes.forEach(b=>{const on=marks.has(Number(b.dataset.done));"
            "b.checked=on;b.closest('.entry').classList.toggle('done',on);});upd();};"
            "let marks=serverTicks.size?serverTicks:read();"
            "if(saved==='human-corrected'){try{localStorage.removeItem(KEY);}catch(e){}"
            "marks=new Set();}"
            "apply(marks);"
            "let ready=false;"
            "const refresh=()=>{ready=true;const m=serverTicks.size?serverTicks:read();"
            "apply(m);if(!serverTicks.size&&m.size)push();};"
            "setTimeout(refresh,250);"
            "window.addEventListener('load',()=>{if(!ready)refresh();});"
            "window.addEventListener('pageshow',()=>{if(!ready)refresh();else apply(read());});"
            "boxes.forEach(b=>{"
            "const ta=document.querySelector('textarea[name=\"e'+b.dataset.done+'\"]');"
            "if(ta)ta.addEventListener('input',()=>{"
            "if(!b.checked){b.checked=true;"
            "b.closest('.entry').classList.add('done');upd();write();}});"
            "b.addEventListener('change',()=>{if(!ready){apply(read());return;}"
            "b.closest('.entry')"
            ".classList.toggle('done',b.checked);upd();write();});});"
            "})();</script>")
    return html_page(f"pha bench — {page}", body,
                     f"<style>{BENCH_CSS}</style>")


# --------------------------------------------------------------------------- save


def _gold_from_existing(text: str, block: str, page: str) -> str:
    """Replace the corrected block inside an existing gold/reference page."""
    m = re.search(r"^(## (?:Corrected text|Reference text[^\n]*))\s*$", text, re.M)
    if m:
        head = text[:m.start()]
        rest = text[m.end():]
        notes = re.search(r"^## Notes\s*$", rest, re.M)
        tail = ("\n## Notes\n" + rest[notes.end():].lstrip("\n")) if notes else ""
        return f"{head}## Corrected text\n\n```\n{block}\n```\n{tail}"
    return f"{text.rstrip()}\n\n## Corrected text\n\n```\n{block}\n```\n"


def _synthesize(corpus: bench.Corpus, page: str, block: str) -> str:
    comp = corpus.comparison_path(page)
    title = f"# {page} — Corrected text"
    context = "Context: (see the comparison)"
    if comp.is_file():
        text = comp.read_text(encoding="utf-8")
        m = re.search(r"^# (.+)$", text, re.M)
        if m:
            title = "# " + m.group(1).replace("Comparative readings", "Corrected text")
        c = re.search(r"^Context: .*$", text, re.M)
        if c:
            context = c.group(0)
    return f"{title}\n\n{context}\n\n## Corrected text\n\n```\n{block}\n```\n"


def save_gold(wb: Workbench, corpus: bench.Corpus, page: str,
              fields: dict, token: str,
              status: str = bench.STATUS_GOLD) -> str:
    """Write a page file (gold or draft). Returns the reviewer name on success.

    Raises SaveError (with an HTTP status) on any refused save. Every rule here
    exists to protect human work: no token, no save; a file that changed on
    disk is not overwritten; a renumbered entry is refused; the structural
    verifier runs before the write.

    ``status`` is ``human-corrected`` (the gold) or ``draft``. A draft may have
    empty entries — that is what saving part-way means — and is excluded from
    scoring; it is still the page the reviewer resumes from.
    """
    status = {"gold": bench.STATUS_GOLD, "draft": bench.STATUS_DRAFT}.get(status, status)
    if status not in (bench.STATUS_GOLD, bench.STATUS_DRAFT):
        raise SaveError(f"unknown save status {status!r}", HTTPStatus.BAD_REQUEST)
    if not secrets.compare_digest(str(token or ""), wb.token):
        raise SaveError("stale form token — reload the page and try again",
                        HTTPStatus.CONFLICT)
    reviewer = str(fields.get("reviewer") or "").strip() or corpus.gold_reviewer
    if not reviewer:
        raise SaveError("a reviewer name is required: the gold must say who "
                        "validated it", HTTPStatus.BAD_REQUEST)
    try:
        nums = [int(x) for x in str(fields.get("nums") or "").split(",") if x.strip()]
    except ValueError:
        raise SaveError("bad entry list", HTTPStatus.BAD_REQUEST) from None
    if not nums:
        raise SaveError("this page has no entries to save", HTTPStatus.BAD_REQUEST)
    if nums != list(range(1, len(nums) + 1)):
        raise SaveError("entries are not contiguous from [1]", HTTPStatus.BAD_REQUEST)

    texts: dict[int, str] = {}
    for n in nums:
        value = str(fields.get(f"e{n}") or "").strip()
        if not value and status == bench.STATUS_GOLD:
            raise SaveError(f"entry [{n}] is empty — the gold cannot lose an "
                            "entry; write the reading, use Save draft, or rebuild "
                            "the comparison", HTTPStatus.BAD_REQUEST)
        texts[n] = value
    block = "\n".join(f"[{n}] {texts[n]}".rstrip() for n in nums)

    gold_path = corpus.gold_path(page)
    existing = gold_path.read_text(encoding="utf-8") if gold_path.is_file() else ""
    existing_meta, existing_body = (bench.split_front_matter(existing) if existing
                                    else ({}, ""))
    if str(fields.get("base") or "") != (
            _sha256(gold_path.read_bytes()) if gold_path.is_file() else ""):
        raise SaveError("the gold file changed on disk since it was loaded — "
                        "reload before saving", HTTPStatus.CONFLICT)

    ref = corpus.reference_path(page)
    ref_body = (bench.split_front_matter(ref.read_text(encoding="utf-8"))[1]
                if ref.is_file() else "")
    base = existing_body or ref_body
    content = (_gold_from_existing(base, block, page) if base
               else _synthesize(corpus, page, block))
    notes = re.search(r"^## Notes\s*$", content, re.M)
    if notes:
        content = content[:notes.start()].rstrip() + "\n\n## Notes\n\n"
    else:
        content = content.rstrip() + "\n\n## Notes\n\n"
    today = time.strftime("%Y-%m-%d")
    if status == bench.STATUS_GOLD:
        content += f"Reviewed {reviewer} {today}.\n"
    else:
        content += (f"Draft saved by {reviewer} {today} — not validated and "
                    "excluded from scoring.\n")
    meta = {
        "page": page,
        "status": status,
        "reviewed_by": reviewer,
        "reviewed_at": time.strftime("%Y-%m-%d"),
        "readings": [r.dir for r in corpus.readers],
        "readings_hash": corpus.readings_hash(page),
        "source": str(corpus.root.relative_to(wb.root)),
    }
    rules = corpus.editing_rules or existing_meta.get("editing_rules") or []
    if rules:
        meta["editing_rules"] = list(rules)
    # structural checks run on the exact bytes that would be written
    problem = bench.verify_gold_text(
        "---\n" + _yaml(meta) + "---\n\n" + content,
        corpus.readings_hash(page))
    probs = problem[0]
    if probs:
        raise SaveError("refused: " + "; ".join(probs), HTTPStatus.BAD_REQUEST)

    gold_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = gold_path.with_suffix(".md.tmp")
    tmp.write_text("---\n" + _yaml(meta) + "---\n\n" + content, encoding="utf-8")
    os.replace(tmp, gold_path)
    if status == bench.STATUS_GOLD:
        clear_review(corpus, page)
    with (corpus.root / "review-log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "page": page, "reviewer": reviewer, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "entries": len(nums),
            "filled": sum(1 for t in texts.values() if t),
            "status": status,
            "readings_hash": meta["readings_hash"],
        }, ensure_ascii=False) + "\n")
    return reviewer


def _yaml(meta: dict) -> str:
    import yaml
    return yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)


# --------------------------------------------------------------------------- http


class Handler(BaseHTTPRequestHandler):
    wb: Workbench
    quiet: bool = False
    server_version = "pha-bench/1"

    # -- plumbing ---------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str,
              headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _html(self, status: int, body: bytes) -> None:
        self._send(status, body, "text/html; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        body = (f'{_head("error")}<main class="bench-main"><h1>{status}</h1>'
                f'<p class="err">{esc(message)}</p>'
                f'<p><a href="/">← all corpora</a></p></main>')
        self._html(status, html_page("pha bench — error", body,
                                     f"<style>{BENCH_CSS}</style>"))

    def _params(self) -> dict:
        u = urllib.parse.urlparse(self.path)
        return {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib API
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        try:
            q = self._params()
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._html(200, render_index(self.wb))
            elif path == "/healthz":
                self._send(200, b'{"ok":true}', "application/json")
            elif path == "/corpus":
                c = self.wb.corpus(q.get("c", ""))
                self._html(200, render_corpus(self.wb, c, verify=bool(q.get("verify")),
                                              notice=q.get("notice", "")))
            elif path == "/page":
                c = self.wb.page(q.get("c", ""), q.get("p", ""))
                saved_status = q.get("saved", "")
                self._html(200, render_page(
                    self.wb, c, q.get("p", ""), saved=bool(saved_status),
                    saved_status=saved_status,
                    reviewed=read_review(c, q.get("p", ""))))
            elif path == "/img":
                self._image(q)
            else:
                self._error(404, "not found")
        except SaveError as e:
            self._error(int(e.status), str(e))
        except Exception as e:  # noqa: BLE001 - a local tool must show the error
            self._error(500, f"{type(e).__name__}: {e}")

    def _image(self, q: dict) -> None:
        c = self.wb.page(q.get("c", ""), q.get("p", ""))
        p = image_path(self.wb, c, q.get("p", ""))
        if p is None:
            self._send(404, b"no image for this page", "text/plain; charset=utf-8")
            return
        self._send(200, p.read_bytes(), _content_type(p))

    def do_POST(self) -> None:  # noqa: N802 - stdlib API
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_FORM_BYTES:
                raise SaveError("bad form size", HTTPStatus.BAD_REQUEST)
            form = urllib.parse.parse_qs(
                self.rfile.read(length).decode("utf-8", "replace"),
                keep_blank_values=True)
            fields = {k: v[0] for k, v in form.items()}
            path = urllib.parse.urlparse(self.path).path
            if path == "/save":
                c = self.wb.page(fields.get("c", ""), fields.get("p", ""))
                status = fields.get("status") or bench.STATUS_GOLD
                status = {"gold": bench.STATUS_GOLD,
                          "draft": bench.STATUS_DRAFT}.get(status, status)
                if status not in (bench.STATUS_GOLD, bench.STATUS_DRAFT):
                    raise SaveError(f"unknown save status {status!r}",
                                    HTTPStatus.BAD_REQUEST)
                save_gold(self.wb, c, fields.get("p", ""), fields,
                          fields.get("token", ""), status=status)
                rel = _rel(self.wb, c)
                self._redirect(_link("/page", c=rel, p=fields.get("p", ""),
                                     saved=status))
            elif path == "/review":
                if not secrets.compare_digest(str(fields.get("token") or ""),
                                              self.wb.token):
                    raise SaveError("stale form token", HTTPStatus.CONFLICT)
                c = self.wb.corpus(fields.get("c", ""))
                page = fields.get("p", "")
                if page not in c.page_ids():
                    raise SaveError(f"no page {page!r} in this corpus",
                                    HTTPStatus.NOT_FOUND)
                raw = fields.get("reviewed") or "[]"
                try:
                    nums = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    nums = [x for x in raw.split(",") if x.strip()]
                try:
                    write_review(c, page, [int(x) for x in nums])
                except (TypeError, ValueError):
                    raise SaveError("bad reviewed list", HTTPStatus.BAD_REQUEST) from None
                self._send(200, b'{"ok":true}', "application/json")

            elif path == "/build":
                c = self.wb.corpus(fields.get("c", ""))
                if not secrets.compare_digest(str(fields.get("token") or ""), self.wb.token):
                    raise SaveError("stale form token", HTTPStatus.CONFLICT)
                what = fields.get("what", "")
                page = fields.get("page") or ""
                if what == "comparison":
                    bench.build_comparison(c, profile="auto")
                elif what == "reference":
                    bench.build_reference(c, readings=len(c.readers) or None)
                elif what == "score":
                    bench.score_corpus(c, write=True,
                                       corpus_id=_rel(self.wb, c))
                    what = "scores"
                else:
                    raise SaveError("unknown build target", HTTPStatus.BAD_REQUEST)
                rel = _rel(self.wb, c)
                if page:
                    self._redirect(_link("/page", c=rel, p=page))
                else:
                    self._redirect(_link("/corpus", c=rel,
                                         notice=f"rebuilt {what}"))
            else:
                self._error(404, "not found")
        except SaveError as e:
            self._error(int(e.status), str(e))
        except SystemExit as e:  # bench commands raise SystemExit on bad input
            self._error(400, str(e))
        except Exception as e:  # noqa: BLE001
            self._error(500, f"{type(e).__name__}: {e}")

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        if not self.quiet:
            sys.stderr.write("  %s\n" % (fmt % args))


def make_server(cfg: Config, host: str = "127.0.0.1", port: int = 8766,
                quiet: bool = False) -> ThreadingHTTPServer:
    wb = Workbench(cfg)
    handler = type("BoundHandler", (Handler,), {"wb": wb, "quiet": quiet})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    server.wb = wb  # exposed for tests and for a future `--open` helper
    return server


def run_server(cfg: Config, host: str = "127.0.0.1", port: int = 8766,
               quiet: bool = False) -> None:
    server = make_server(cfg, host, port, quiet)
    actual_host, actual_port = server.server_address[:2]
    shown = "127.0.0.1" if actual_host in ("0.0.0.0", "::") else actual_host
    print(f"pha bench serve: http://{shown}:{actual_port}/")
    print(f"  corpora under {bench.bench_root(cfg)}; writes only human/, "
          f"reference/ and comparison/")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("  warning: bound beyond loopback — this workbench has no "
              "authentication; use only on a trusted network", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
