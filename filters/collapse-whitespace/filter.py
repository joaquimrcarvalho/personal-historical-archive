"""Collapse justified-print space runs and index dotted leaders. See filter.md."""

import re

# a run of 2+ spaces/tabs: justified print, OCR padding
_SPACE_RUN = re.compile(r"[ \t]{2,}")
# index/table dotted leaders: 3+ dot groups separated by spaces (". . . . ."),
# also with middle dots/bullets
_LEADER = re.compile(r"(?:[.\u00b7\u2022]\s+){3,}")
# a real ellipsis typed with no spaces: must survive
_ELLIPSIS = re.compile(r"\.{3,}")


def _collapse_line(line: str, collapse_leaders: bool) -> str:
    """Collapse one line, protecting a genuine `...` ellipsis from the leader rule."""
    out = line
    if collapse_leaders:
        # shield ellipses, collapse leaders, restore the shields
        out = _ELLIPSIS.sub(lambda m: "\x00" * len(m.group(0)), out)
        out = _LEADER.sub(" ", out)
        out = out.replace("\x00", ".")
    return _SPACE_RUN.sub(" ", out).rstrip()


def run(value, ctx):
    if not isinstance(value, str) or not value:
        return value
    params = ctx.get("params") or {}
    collapse_leaders = str(params.get("collapse_leaders", "true")).lower() in ("1", "true", "yes")
    keep_blank = str(params.get("keep_blank_lines", "true")).lower() in ("1", "true", "yes")

    out: list[str] = []
    for line in value.split("\n"):
        if not line.strip():
            out.append("")
            continue
        out.append(_collapse_line(line, collapse_leaders))

    text = "\n".join(out)
    if not keep_blank:
        text = re.sub(r"\n\s*\n+", "\n", text)
    # remove only blank lines at the very start/end, never the body's structure
    return text.strip("\n")
