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


_PAGE_KEYS = ("page_start", "page", "start_page", "source_page")
_TITLE_WORDS = re.compile(r"[\w']+", re.UNICODE)


def _record_start_page(rec: dict) -> int | None:
    """Canonical starting-page resolver for records.

    Current encoders emit `page_start`; older/sample encoders emit `page`.
    New encoders should use `page_start`, but the aliases stay accepted.
    """
    for key in _PAGE_KEYS:
        value = rec.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _page_spans(records: list[dict]) -> list[tuple[dict, int, int]]:
    """Resolve each record's starting page and inferred end page.

    Records are sorted by (page_start, line_start). A record with no usable
    page anchor is skipped with a warning; it is never silently assigned page 1.
    """
    anchored: list[tuple[int, dict]] = []
    missing = 0
    for rec in records:
        start = _record_start_page(rec)
        if start is None:
            missing += 1
            continue
        anchored.append((start, rec))
    if missing:
        import sys
        print(f"markdown-from-records: skipped {missing} record(s) with no "
              "usable page anchor (page_start/page/start_page/source_page)",
              file=sys.stderr)
    if not anchored:
        return []
    anchored.sort(key=lambda item: (item[0], int(item[1].get("line_start") or 0)))
    last = max(start for start, _rec in anchored)
    spans: list[tuple[dict, int, int]] = []
    for i, (start, rec) in enumerate(anchored):
        explicit = rec.get("page_end")
        end = start
        if explicit is not None:
            try:
                end = max(start, int(explicit))
            except (TypeError, ValueError):
                end = start
        elif i + 1 < len(anchored):
            nxt = anchored[i + 1][0]
            end = max(start, nxt - 1 if nxt > start else start)
        else:
            end = max(start, last)
        spans.append((rec, start, end))
    return spans


def _page_chunks(records: list[dict]) -> list[tuple[dict, int, int]]:
    """Backward-compatible alias for callers that only need page spans."""
    return _page_spans(records)


def _find_boundary(lines: list[str], rec: dict) -> int | None:
    """1-based line where rec's header begins inside a page, or None.

    `line_start` is authoritative; otherwise a record number line or the first
    significant title word is used.
    """
    ls = rec.get("line_start")
    if ls is not None:
        try:
            return max(1, int(ls))
        except (TypeError, ValueError):
            pass
    number = str(rec.get("number") or "").strip().strip(".")
    title = str(rec.get("text") or rec.get("label") or "").upper()
    words = [w for w in _TITLE_WORDS.findall(title) if len(w) > 3]
    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if number and re.fullmatch(r"\d{1,3}[a-z]?(?:-[a-z]{1,3})?\.?", line):
            return i
    if words:
        first = words[0]
        for i, raw in enumerate(lines, 1):
            if first in raw.upper():
                return i
    return None


def _page_slices(records: list[dict], page_lines: dict[int, list[str]]
                 ) -> list[tuple[dict, int, int, list[tuple[int, list[str]]]]]:
    """Attach the exact page slices each record covers.

    A page shared by two records is split at the next record's `line_start`
    when possible, otherwise at its header line. When no boundary can be
    found, the page goes to the previous record (the same documented fallback
    as the reference implementation).
    """
    spans = _page_spans(records)
    if not spans:
        return []
    out: list[tuple[dict, int, int, list[tuple[int, list[str]]]]] = []
    for i, (rec, start, end) in enumerate(spans):
        pieces: list[tuple[int, list[str]]] = []
        for p in range(start, end + 1):
            lines = list(page_lines.get(p, []))
            if i + 1 < len(spans) and spans[i + 1][1] == p:
                boundary = _find_boundary(lines, spans[i + 1][0])
                if boundary is not None:
                    lines = lines[:boundary - 1]
            if i > 0 and spans[i - 1][2] >= start and p == start:
                if spans[i - 1][2] == start:
                    own = _find_boundary(page_lines.get(p, []), rec)
                    if own is not None and own > 1:
                        lines = lines[own - 1:]
                    else:
                        lines = []
            pieces.append((p, lines))
        out.append((rec, start, end, pieces))
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
    page_lines: dict[int, list[str]] = {}
    for pno, f in pages.items():
        body_text = _read_page_body(f) if strips_notes else f.read_text(encoding="utf-8")
        page_lines[pno] = body_text.splitlines()
    slices = _page_slices(flat, page_lines)
    written = 0
    for rec, start, end, pieces in slices:
        kind = str(rec.get("kind") or "record")
        head = _record_text(rec)
        body_parts = ["\n".join(lines) for _pno, lines in pieces if lines]
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
        skip = {"kind", "class", "text", "page", "page_start", "page_end",
                "start_page", "source_page", "line_start"}
        extra = {k: v for k, v in rec.items()
                 if k not in skip and isinstance(v, (str, int, float))}
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
        for rec, start, end, _pieces in _page_slices(flat, page_lines):
            kind = str(rec.get("kind") or "record")
            head = _record_text(rec) or f"{kind} {start}"
            name = f"{kind}-{start:04d}-{_slug(head)}.md"
            span = f"{start}-{end}" if end != start else str(start)
            lines.append(f"- [{head}]({name}) — {kind}, p. {span}")
        _atomic_write(out_dir / "index.md", "\n".join(lines) + "\n")
    return None
