"""Remove the OCR/parse engine's synthetic page-separator line. See filter.md."""

import re

DEFAULT_MARKER = (
    r"^\s*[-–—=*_]{0,4}\s*\[?\s*(page|pág|pag|p|pp)\b[^\n]{0,12}$"
)


def run(value, ctx):
    if not isinstance(value, str) or not value:
        return value
    params = ctx.get("params") or {}
    pattern = params.get("marker") or DEFAULT_MARKER
    only_first = str(params.get("only_first_line", "false")).lower() in ("1", "true", "yes")
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return value  # a bad custom pattern must not destroy the text
    lines = value.split("\n")
    out = []
    for i, line in enumerate(lines):
        if rx.match(line) and (i == 0 or not only_first):
            continue
        out.append(line)
    # drop the blank line left behind when the marker was line 0
    while out and not out[0].strip():
        out.pop(0)
    return "\n".join(out)
