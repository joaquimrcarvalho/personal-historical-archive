"""``pha bench export`` — a self-contained static site from a corpus (P5).

The export is the boundary between the working corpus and anything published:
plain HTML + one stylesheet, images copied beside it, scores as data, no
JavaScript, no pha on the reading side. It never writes into the corpus and
never publishes by itself — it writes a directory the owner can inspect and
then move or host.

Honesty is rendered, not documented: the gold's status and staleness, the
number of pages scored, the caveats, and the rights line all appear on the
page.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from . import bench
from .config import Config
from .viewer import CSS, esc, html_page

EXPORT_CSS = """\
.export-main{display:block;max-width:1200px;margin:0 auto;padding:1rem}
.export-main .cols{display:grid;grid-template-columns:minmax(280px,42%) 1fr;gap:1rem;align-items:start}
.export-main img.page{width:100%;height:auto;border:1px solid #ccc;background:#fff}
.export-main pre{white-space:pre-wrap;background:#fff;border:1px solid #e3e1db;border-radius:4px;
  padding:.6rem;font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;overflow:auto}
.export-main table{border-collapse:collapse;width:100%;font-size:13px}
.export-main th,.export-main td{border-bottom:1px solid #e3e1db;text-align:left;padding:.25rem .4rem}
.export-main .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:.5rem}
.export-main .grid a{color:inherit;text-decoration:none;border:1px solid #ddd;border-radius:6px;
  padding:.3rem;text-align:center;font-size:12px;background:#fff}
.export-main .grid img{width:100%;height:110px;object-fit:cover;border-radius:4px;background:#eee}
.badge2{font-size:11px;border:1px solid #ccc;border-radius:999px;padding:0 .45rem;background:#f7f6f3}
.badge2.gold{background:#e8f5e9;border-color:#a5d6a7}
.badge2.warn{background:#fff4e5;border-color:#ffcc80}
.badge2.bad{background:#fdecea;border-color:#f5c6c2}
@media print{
  .export-main .cols{display:block}
  .export-main img.page{max-width:48%;float:left;margin:0 1rem 1rem 0}
  .noprint{display:none}
  a{color:inherit;text-decoration:none}
}
"""


def _rel(corpus: bench.Corpus, cfg: Config) -> str:
    try:
        return str(corpus.root.relative_to(bench.bench_root(cfg)))
    except ValueError:
        return corpus.root.name


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")
    return s or "corpus"


def default_out(cfg: Config, corpus: bench.Corpus) -> Path:
    rel = corpus.root
    try:
        rel = corpus.root.relative_to(bench.bench_root(cfg))
    except ValueError:
        pass
    return cfg.data / "exports" / "bench" / _slug(str(rel).replace("/", "-"))


def _page_name(page: str) -> str:
    return page if re.match(r"^[A-Za-z0-9._-]+$", page) else _slug(page)


def _gold_badge(corpus: bench.Corpus, page: str) -> str:
    st = bench.page_status(corpus, page)
    out = []
    if st.gold:
        out.append('<span class="badge2 gold">human-corrected</span>')
    elif st.gold_status:
        out.append(f'<span class="badge2">{esc(st.gold_status)}</span>')
    else:
        out.append('<span class="badge2">no gold</span>')
    if st.stale:
        out.append('<span class="badge2 warn">stale — readings changed since review</span>')
    if st.missing:
        out.append(f'<span class="badge2 bad">{len(st.missing)} reading(s) missing</span>')
    return "".join(out)


def _score_table(results: dict) -> str:
    rows = []
    for key, a in (results.get("aggregates") or {}).items():
        def fmt(v, pct=False):
            if v is None:
                return "—"
            return f"{v:.1%}" if pct else f"{v:.3f}"
        rows.append(
            "<tr><td>" + esc(key) + "</td>"
            f"<td>{a.get('pages')}</td><td>{fmt(a.get('cer_diplomatic'))}</td>"
            f"<td>{fmt(a.get('cer_modern'))}</td>"
            f"<td>{fmt(a.get('entry_exact'), True)}</td>"
            f"<td>{fmt(a.get('names'), True)}</td>"
            f"<td>{'partial' if a.get('partial') else ''}</td></tr>")
    caveats = "".join(f"<li>{esc(c)}</li>" for c in results.get("caveats") or [])
    return ('<h2>Scores</h2><table><thead><tr><th>reader</th><th>pages</th>'
            '<th>CER dipl.</th><th>CER mod.</th><th>entry</th><th>names</th>'
            '<th></th></tr></thead><tbody>' + "".join(rows)
            + "</tbody></table>"
            f'<ul class="small">{caveats}</ul>')


def _provenance(meta: dict) -> str:
    if not meta:
        return '<p class="small">No review provenance: this page is not gold.</p>'
    bits = []
    for key in ("reviewed_by", "reviewed_at", "readings_hash", "status"):
        if meta.get(key):
            bits.append(f"{key}: <code>{esc(str(meta[key]))}</code>")
    if meta.get("editing_rules"):
        rules = "".join(f"<li>{esc(r)}</li>" for r in meta["editing_rules"])
        bits.append(f"editing rules:<ul>{rules}</ul>")
    return '<p class="small">' + " · ".join(bits) + "</p>"


def _page_html(corpus: bench.Corpus, page: str, image_rel: str | None,
               ids: list[str], results: dict | None) -> bytes:
    i = ids.index(page) if page in ids else 0
    nav = ['<p class="noprint"><a href="../index.html">index</a>']
    if i > 0:
        nav.append(f'<a href="{esc(_page_name(ids[i - 1]))}.html">‹ {esc(ids[i - 1])}</a>')
    if i + 1 < len(ids):
        nav.append(f'<a href="{esc(_page_name(ids[i + 1]))}.html">{esc(ids[i + 1])} ›</a>')
    nav.append("</p>")
    gold_text = ""
    gold = corpus.gold_path(page)
    if gold.is_file():
        gold_text = bench.extract_transcription(gold.read_text(encoding="utf-8"))
    ref_text = ""
    ref = corpus.reference_path(page)
    if ref.is_file():
        body_text = bench.extract_transcription(ref.read_text(encoding="utf-8"))
        if bench.is_valid_reading(body_text):
            ref_text = body_text
    readings = []
    for r in corpus.readers:
        text = corpus.reading(r, page)
        readings.append(
            f'<h3>{esc(r.name)} <span class="small">({esc(r.dir)}'
            + (f' · model {esc(r.model)}' if r.model else "") + ")</span></h3>"
            + (f"<pre>{esc(text)}</pre>" if text
               else '<p class="small">no reading</p>'))
    comp = corpus.comparison_path(page)
    comp_html = ""
    if comp.is_file():
        body = comp.read_text(encoding="utf-8")
        comp_html = ('<details class="noprint"><summary>stacked comparison</summary>'
                     f"<pre>{esc(body)}</pre></details>")
    img_html = (f'<img class="page" src="../{esc(image_rel)}" alt="page {esc(page)}">'
                if image_rel else '<p class="small">No image in this export.</p>')
    meta = corpus.gold_meta(page)
    page_scores = ""
    if results:
        row = next((p for p in results.get("pages") or [] if p.get("page") == page), None)
        if row and row.get("readers"):
            lines = []
            for key, r in row["readers"].items():
                lines.append(
                    f"<tr><td>{esc(key)}</td><td>{r.get('cer_diplomatic')}</td>"
                    f"<td>{r.get('cer_modern')}</td>"
                    f"<td>{r['entry_exact'][0]}/{r['entry_exact'][1]}</td>"
                    f"<td>{r['names'][0]}/{r['names'][1]}</td></tr>")
            page_scores = ('<h2>This page</h2><table><thead><tr><th>reader</th>'
                           '<th>CER dipl.</th><th>CER mod.</th><th>entry</th>'
                           '<th>names</th></tr></thead><tbody>'
                           + "".join(lines) + "</tbody></table>")
    body = (f'<header><strong>{esc(corpus.title)}</strong>'
            f'<span class="spacer"></span><span class="small">{esc(page)}</span></header>'
            f'<main class="export-main">{"".join(nav)}'
            f'<div class="cols"><div>{img_html}</div><div>'
            f'<h1>{esc(page)} {_gold_badge(corpus, page)}</h1>'
            + (f'<h2>Gold</h2><pre>{esc(gold_text)}</pre>' if gold_text
               else ('<h2>Gold</h2><p class="small">No human-corrected reading yet. '
                     'The machine merge is shown below, unreviewed.</p>'
                     + (f'<h2>Machine merge (not reviewed)</h2><pre>{esc(ref_text)}</pre>'
                        if ref_text else "")))
            + _provenance(meta)
            + page_scores
            + '</div></div>'
            '<h2>Readings</h2>' + "".join(readings)
            + comp_html
            + '<footer class="small noprint">Exported by <code>pha bench export</code>'
              f" · {esc(corpus.rights or 'Rights: verify before publishing.')}</footer>"
            "</main>")
    return html_page(f"{corpus.title} — {page}", body,
                     head_extra=f"<style>{EXPORT_CSS}</style>")


def _index_html(corpus: bench.Corpus, ids: list[str], image_map: dict[str, str],
                results: dict | None) -> bytes:
    st = bench.corpus_status(corpus)
    cards = []
    for page in ids:
        img = image_map.get(page)
        cards.append(
            f'<a href="pages/{esc(_page_name(page))}.html">'
            + (f'<img loading="lazy" src="{esc(img)}" alt="">' if img else "")
            + f"<div><strong>{esc(page)}</strong></div>{_gold_badge(corpus, page)}</a>")
    score_html = _score_table(results) if results else (
        '<h2>Scores</h2><p class="small">No scores: no page has a valid gold '
        "yet, or scoring was skipped.</p>")
    rights = esc(corpus.rights or "Rights: verify before publishing.")
    body = (f'<header><strong>{esc(corpus.title)}</strong></header>'
            '<main class="export-main">'
            f'<h1>{esc(corpus.title)}</h1>'
            f'<p class="small">{esc(corpus.notes or "")}</p>'
            f'<p>{st["pages"]} pages · {len(corpus.readers)} readings · '
            f'{st["gold"]} human-corrected · {st["stale"]} stale</p>'
            f'<p class="small">{esc(", ".join(r.name for r in corpus.readers))}</p>'
            f'<div class="grid">{ "".join(cards) }</div>'
            + score_html
            + ('<p class="small"><a href="data/results.json">results.json</a>'
               ' · <a href="data/results.csv">results.csv</a>'
               ' · <a href="manifest.json">manifest.json</a></p>'
               if results and results.get("pages") else "")
            + f'<footer class="small">{rights} · Exported '
              f'{esc(time.strftime("%Y-%m-%d"))} by <code>pha bench export</code>.</footer>'
            "</main>")
    return html_page(corpus.title, body,
                     head_extra=f"<style>{EXPORT_CSS}</style>")


def _export_image(src: Path, dest: Path, max_px: int) -> tuple[str, bool]:
    """Copy a page image, downscaling to ``max_px`` with pymupdf when possible.

    Returns ``(extension, downscaled)``. The original is copied unchanged when
    the downscale is impossible (unsupported format, unreadable file) — the
    export never silently drops a page image.
    """
    if max_px:
        try:
            import pymupdf as fitz

            with fitz.open(str(src)) as doc:
                page = doc[0]
                rect = page.rect
                if max(rect.width, rect.height) > max_px:
                    zoom = max_px / max(rect.width, rect.height)
                    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                else:
                    pix = page.get_pixmap(alpha=False)
                dest.write_bytes(pix.tobytes("jpeg", jpg_quality=82))
            return ".jpg", True
        except Exception:  # noqa: BLE001 - fall back to a faithful copy
            pass
    ext = src.suffix.lower() or ".jpg"
    shutil.copyfile(src, dest.with_suffix(ext))
    return ext, False


def export_site(corpus: bench.Corpus, cfg: Config, out: str | Path | None = None,
                images: bool = True, score: bool = True,
                force: bool = False, max_image_px: int = 1600) -> dict:
    """Write the static site; returns a summary (and warnings worth showing)."""
    if not corpus.page_ids():
        raise SystemExit(f"no page files under {corpus.root}")
    dest = Path(out).expanduser() if out else default_out(cfg, corpus)
    dest = dest.resolve()
    if dest.exists() and any(dest.iterdir()):
        if not force:
            raise SystemExit(f"{dest} is not empty; pass --force to overwrite it")
        for child in dest.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    (dest / "pages").mkdir(parents=True, exist_ok=True)
    (dest / "images").mkdir(exist_ok=True)

    from .benchui import Workbench, image_path

    wb = Workbench(cfg)
    results = (bench.score_corpus(corpus, write=False, corpus_id=_rel(corpus, cfg))
               if score else None)
    warnings: list[str] = []
    if results is not None:
        bench.write_scores(dest / "data", results)
        if not results["pages"]:
            warnings.append("no page has a valid gold: the site shows scores as empty")

    ids = corpus.page_ids()
    image_map: dict[str, str] = {}
    missing = 0
    for page in ids:
        src = image_path(wb, corpus, page) if images else None
        if src is None:
            missing += 1 if images else 0
            continue
        base = dest / "images" / f"{_page_name(page)}.jpg"
        ext, downscaled = _export_image(src, base, max_image_px)
        name = base.name if downscaled else f"{_page_name(page)}{ext}"
        image_map[page] = f"images/{name}"
    if images and missing:
        warnings.append(f"{missing} page(s) have no image: the export shows them "
                        "text-only (set document.path or add images/)")
    if not images:
        warnings.append("images were not copied (--no-images): the export is text-only")

    (dest / "style.css").write_text(CSS + EXPORT_CSS, encoding="utf-8")
    for page in ids:
        html_text = _page_html(corpus, page, image_map.get(page), ids, results)
        (dest / "pages" / f"{_page_name(page)}.html").write_bytes(html_text)
    (dest / "index.html").write_bytes(_index_html(corpus, ids, image_map, results))

    manifest = {
        "schema": 1,
        "generator": "pha bench export",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "corpus": _rel(corpus, cfg),
        "title": corpus.title,
        "pages": ids,
        "readers": [{"dir": r.dir, "label": r.name, "model": r.model}
                    for r in corpus.readers],
        "images": len(image_map),
        "scores": bool(results and results["pages"]),
        "rights": corpus.rights or "Rights: verify before publishing.",
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    # self-check: a static export must not leak absolute host paths
    leaked = []
    for html_file in list(dest.glob("*.html")) + list((dest / "pages").glob("*.html")):
        text = html_file.read_text(encoding="utf-8", errors="replace")
        if str(cfg.data) in text:
            leaked.append(html_file.name)
    if leaked:
        warnings.append("absolute archive paths leaked into: " + ", ".join(leaked))
    return {"out": str(dest), "pages": len(ids), "images": len(image_map),
            "scores": bool(results and results["pages"]), "warnings": warnings}
