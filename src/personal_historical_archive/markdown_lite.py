"""A tiny, dependency-free Markdown renderer for the served viewer.

The edited reading is Markdown (tables, headings, lists, emphasis, links), so
``pha serve`` renders that panel through this module instead of showing the
source.  The RAW transcription is never passed through here: a palaeographer's
text is displayed verbatim, because rendering it would reinterpret its
whitespace and incidental markup.

Safety rules, in order of importance:

* every text segment is HTML-escaped before it reaches the page, so a
  transcription can never inject markup;
* only a fixed set of block/inline elements is produced (no raw HTML pass-
  through), and link targets are restricted to http(s)/mailto/relative URLs.

The supported subset deliberately mirrors the PHA view's own renderer
(``dsh-pha/src/client/index.js``): headings, paragraphs, ordered/unordered
lists, blockquotes, fenced code, horizontal rules, pipe tables, emphasis,
inline code, strikethrough, images, links and footnote references.
"""
from __future__ import annotations

import html
import re

__all__ = ["render_markdown"]

# One alternation, tried left to right at each position.  Order matters:
# images before links, `**`/`__` before `*`/`_`, code last among the markers so
# that a code span can contain literal emphasis characters.
_INLINE_RE = re.compile(
    r"!\[([^\]]*)\]\(([^)\s]+)\)"
    r"|\*\*(?=\S)(.+?)(?<=\S)\*\*"
    r"|__(?=\S)(.+?)(?<=\S)__"
    r"|~~(?=\S)(.+?)(?<=\S)~~"
    r"|`([^`]+)`"
    r"|\[([^\]]+)\]\(([^)\s]+)\)"
    r"|\[\^(\d+)\]"
    r"|\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*"
    r"|(?<![\w])_(?=[^\s_])([^_\n]+?)(?<=[^\s_])(?![\w])_"
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^```")
_HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_UL_RE = re.compile(r"^[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^\d+[.)]\s+(.*)$")
_FN_DEF_RE = re.compile(r"^\[\^(\d+)\]\s*:\s*(.*)$")
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
_TABLE_SEP_CELL_RE = re.compile(r"^:?-{3,}:?$")

_SAFE_SCHEMES = {"http", "https", "mailto"}


def _safe_url(url: str) -> str:
    """Return an escaped href, neutralising scripts and data: URLs."""
    raw = url.strip()
    match = _SCHEME_RE.match(raw)
    if match and match.group(1).lower() not in _SAFE_SCHEMES:
        return "#"
    return html.escape(raw, quote=True)


def _inline(text: str) -> str:
    """Render inline Markdown, escaping every plain-text segment."""
    out: list[str] = []
    last = 0
    for match in _INLINE_RE.finditer(text):
        if match.start() > last:
            out.append(html.escape(text[last:match.start()], quote=True))
        if match.group(1) is not None:                      # image
            out.append(
                '<img class="md-img" src="' + _safe_url(match.group(2)) + '" alt="'
                + html.escape(match.group(1), quote=True) + '" loading="lazy">')
        elif match.group(3) is not None:                    # **strong**
            out.append("<strong>" + _inline(match.group(3)) + "</strong>")
        elif match.group(4) is not None:                    # __strong__
            out.append("<strong>" + _inline(match.group(4)) + "</strong>")
        elif match.group(5) is not None:                    # ~~strike~~
            out.append("<del>" + _inline(match.group(5)) + "</del>")
        elif match.group(6) is not None:                    # `code`
            out.append("<code>" + html.escape(match.group(6), quote=True) + "</code>")
        elif match.group(7) is not None:                    # [label](url)
            out.append(
                '<a href="' + _safe_url(match.group(8)) + '">' + _inline(match.group(7))
                + "</a>")
        elif match.group(9) is not None:                    # [^n]
            n = match.group(9)
            out.append('<sup class="md-fn-ref"><a href="#fn-' + n + '">' + n + "</a></sup>")
        elif match.group(10) is not None:                   # *em*
            out.append("<em>" + _inline(match.group(10)) + "</em>")
        elif match.group(11) is not None:                   # _em_
            out.append("<em>" + _inline(match.group(11)) + "</em>")
        last = match.end()
    if last < len(text):
        out.append(html.escape(text[last:], quote=True))
    return "".join(out)


def _split_row(line: str) -> list[str]:
    """Split a pipe-table row, dropping the optional outer pipes."""
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]


def _is_table_sep(line: str) -> bool:
    """True for ``|---|:---:|`` (the line under a pipe table's header)."""
    if "|" not in line:
        return False
    cells = _split_row(line)
    return bool(cells) and all(_TABLE_SEP_CELL_RE.match(c) for c in cells)


def render_markdown(text: str | None) -> str:
    """Render the supported Markdown subset to safe HTML."""
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    footnotes: dict[str, str] = {}
    para: list[str] = []

    def flush_para() -> None:
        if para:
            # The edited readings use a single newline between related lines
            # (Language: / Script: / Page: ...).  Keep those line breaks visible:
            # collapsing them into spaces turned a readable block into one long
            # paragraph, losing structure that the <pre> view used to show.
            out.append("<p>" + "<br>".join(_inline(line) for line in para) + "</p>")
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_para()
            i += 1
            continue

        fn = _FN_DEF_RE.match(stripped)
        if fn and not stripped.startswith("|"):
            footnotes[fn.group(1)] = fn.group(2)
            i += 1
            continue

        if _FENCE_RE.match(stripped):
            flush_para()
            code: list[str] = []
            i += 1
            while i < len(lines) and not _FENCE_RE.match(lines[i].strip()):
                code.append(lines[i])
                i += 1
            i += 1  # closing fence (or EOF)
            out.append('<pre class="md-pre"><code>'
                       + html.escape("\n".join(code), quote=True) + "</code></pre>")
            continue

        if _HR_RE.match(stripped):
            flush_para()
            out.append("<hr>")
            i += 1
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            flush_para()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            i += 1
            continue

        if stripped.startswith(">"):
            flush_para()
            quoted: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quoted.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            out.append("<blockquote>"
                       + "".join("<p>" + _inline(q) + "</p>" for q in quoted)
                       + "</blockquote>")
            continue

        if (stripped.startswith("|") and i + 1 < len(lines)
                and _is_table_sep(lines[i + 1])):
            flush_para()
            rows: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            header = _split_row(rows[0])
            width = len(header)
            body = [_split_row(r) for r in rows[1:] if not _is_table_sep(r)]
            head_cells = "".join("<th>" + _inline(c) + "</th>" for c in header)
            body_rows = "".join(
                "<tr>" + "".join(
                    "<td>" + _inline(c) + "</td>"
                    for c in (row + [""] * width)[:width]) + "</tr>"
                for row in body)
            out.append('<div class="md-table-wrap"><table class="md-table"><thead><tr>'
                       + head_cells + "</tr></thead><tbody>" + body_rows
                       + "</tbody></table></div>")
            continue

        unordered = _UL_RE.match(stripped)
        ordered = _OL_RE.match(stripped)
        if unordered or ordered:
            flush_para()
            is_ordered = bool(ordered and not unordered)
            items: list[str] = []
            while i < len(lines):
                item = lines[i].strip()
                match = _OL_RE.match(item) if is_ordered else _UL_RE.match(item)
                if not match:
                    break
                items.append(match.group(1))
                i += 1
            tag = "ol" if is_ordered else "ul"
            out.append("<" + tag + ">"
                       + "".join("<li>" + _inline(item) + "</li>" for item in items)
                       + "</" + tag + ">")
            continue

        para.append(stripped)
        i += 1

    flush_para()
    if footnotes:
        entries = "".join(
            '<p id="fn-' + key + '"><span class="md-fn-num">[' + key + "]</span> "
            + _inline(value) + "</p>"
            for key, value in sorted(footnotes.items(), key=lambda kv: int(kv[0])))
        out.append('<section class="md-footnotes"><h3>Footnotes</h3>'
                   + entries + "</section>")
    return "".join(out)
