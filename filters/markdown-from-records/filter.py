"""Materialise one Markdown file per record, from the edited pages. See filter.md."""

import json
import re
from pathlib import Path

_FM_RE = re.compile(r"^---\s*\n.*?\n---\s*\n?", re.DOTALL)


def _read_page_body(path: Path) -> str:
    """A library page file -> its body (front matter and `## Notes` stripped)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    text = _FM_RE.sub("", text, count=1)
    # the editor's reading notes are not part of the record's text
    text = re.split(r"\n#{1,3}\s*Notes\s*\n", text, maxsplit=1)[0]
    return text.strip()


def _page_index(pages_dir: Path | None) -> dict[int, Path]:
    """{page_no: file path} for a variant folder (page-NNN.md or <source>.md)."""
    out: dict[int, Path] = {}
    if not pages_dir or not pages_dir.is_dir():
        return out
    for f in sorted(pages_dir.glob("*.md")):
        m = re.fullmatch(r"page-(\d+)", f.stem)
        if m:
            out[int(m.group(1))] = f
    return out


def _slug(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", (text or "").lower(), flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return (s[:limit].rstrip("-")) or "record"


def _page_chunks(records: list[dict]) -> list[tuple[dict, int, int]]:
    """Attach the page span each record covers.

    Records SPAN pages and a record may START mid-page: `page_end` is inferred
    as the page before the next record's page (or the last page), so a shared
    page's top belongs to the previous record.
    """
    pages = [r.get("page") for r in records if isinstance(r.get("page"), int)]
    last = max(pages) if pages else 0
    out: list[tuple[dict, int, int]] = []
    for i, rec in enumerate(records):
        start = rec.get("page") if isinstance(rec.get("page"), int) else None
        if start is None:
            start = out[-1][2] if out else 1
        end = start
        for nxt in records[i + 1:]:
            p = nxt.get("page")
            if isinstance(p, int):
                end = max(start, p - 1 if p > start else start)
                break
        else:
            end = max(start, last)
        out.append((rec, start, end))
    return out


def _record_text(rec: dict) -> str:
    for key in ("text", "title", "name"):
        v = rec.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def run(value, ctx):
    """Write `library_dir/<out_dir>/<kind>-<page>-<slug>.md` per record.

    Returns None: this is an artifact filter — it consumes the records, writes
    files, and leaves the pipeline's value untouched.
    """
    params = ctx.get("params") or {}
    library_dir = ctx.get("library_dir")
    records_file = ctx.get("records_file")
    if not library_dir or not records_file:
        return None
    records_path = Path(records_file)
    if not records_path.exists():
        return None
    try:
        payload = json.loads(records_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    flat: list[dict] = []
    by_kind = payload.get("records") or {}
    if isinstance(by_kind, dict):
        for kind, items in by_kind.items():
            for rec in items or []:
                if isinstance(rec, dict):
                    rec.setdefault("kind", kind)
                    flat.append(rec)
    elif isinstance(by_kind, list):
        flat = [r for r in by_kind if isinstance(r, dict)]
    if not flat:
        return None

    out_dir = Path(library_dir) / str(params.get("out_dir", "segments"))
    out_dir.mkdir(parents=True, exist_ok=True)

    pages = _page_index(Path(ctx["pages_dir_edited"]) if ctx.get("pages_dir_edited") else None)
    if not pages:
        pages = _page_index(Path(ctx["pages_dir_raw"]) if ctx.get("pages_dir_raw") else None)

    encoder = ctx.get("encoder") or ""
    strips_notes = str(params.get("strip_notes", "true")).lower() in ("1", "true", "yes")
    written = 0
    for rec, start, end in _page_chunks(flat):
        kind = str(rec.get("kind") or "record")
        head = _record_text(rec)
        body_parts: list[str] = []
        for pno in range(start, end + 1):
            f = pages.get(pno)
            if f is None:
                continue
            body = _read_page_body(f) if strips_notes else f.read_text(encoding="utf-8")
            if body:
                body_parts.append(body)
        body = "\n\n".join(body_parts).strip()
        if not body:
            continue
        title = _record_text(rec) or f"{kind} {start}"
        doc = payload.get("document") or ""
        fm = {
            "record_kind": kind,
            "pages": f"{start}-{end}" if end != start else str(start),
            "document": doc,
            "document_id": ctx.get("document_id"),
            "filename": ctx.get("filename"),
            "encoder": encoder or payload.get("encoder"),
            "source": str(records_path),
        }
        extra = {k: v for k, v in rec.items()
                 if k not in ("kind", "class", "text", "page") and isinstance(v, (str, int, float))}
        fm.update(extra)
        text = "---\n"
        for k, v in fm.items():
            if v is None or v == "":
                continue
            sval = str(v).replace("\\", "\\\\").replace('"', '\\"')
            text += f'{k}: "{sval}"\n'
        text += f"---\n\n# {title}\n\n{body}\n"
        name = f"{kind}-{start:04d}-{_slug(head or title)}.md"
        _atomic_write(out_dir / name, text)
        written += 1

    if str(params.get("write_index", "false")).lower() in ("1", "true", "yes") and written:
        lines = ["# Records index", "", f"Document: {payload.get('document') or ''}", ""]
        for rec, start, end in _page_chunks(flat):
            kind = str(rec.get("kind") or "record")
            head = _record_text(rec) or f"{kind} {start}"
            name = f"{kind}-{start:04d}-{_slug(head)}.md"
            span = f"{start}-{end}" if end != start else str(start)
            lines.append(f"- [{head}]({name}) — {kind}, p. {span}")
        _atomic_write(out_dir / "index.md", "\n".join(lines) + "\n")
    return None
