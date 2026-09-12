"""Remove stray superscript footnote-marker residue. See filter.md."""

import re

# a line that is ONLY marker residue (the footnote's own text is elsewhere)
_STRAY_LINE = re.compile(r"^\s*[*\u00b0\u00ba\u02da\u2217]{1,3}\s*$")
# a marker run glued to the preceding word, e.g. "palavra**" / "palavra°"
_STRAY_TAIL = re.compile(r"(?<=[^\s*])([*\u00b0\u00ba]{2,})\s*$")

# Never touch a digit reference: those are real footnote numbers.
_DIGIT_REF = re.compile(r"\d")


def run(value, ctx):
    if not isinstance(value, str) or not value:
        return value
    params = ctx.get("params") or {}
    strip_tail = str(params.get("strip_trailing_markers", "true")).lower() in ("1", "true", "yes")

    out: list[str] = []
    for line in value.split("\n"):
        # A line that is only marker characters is residue: the footnote text it
        # points at is a separate line block, which we leave untouched.
        if _STRAY_LINE.match(line):
            continue
        if strip_tail:
            line = _STRAY_TAIL.sub("", line)
        out.append(line.rstrip())
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text)
